"""Dependencias FastAPI compartidas de `edecan_api`.

Contiene (ARCHITECTURE.md §10.12, §2, §10.4, §10.6):

- `CurrentUser` / `TenantCtx`: se construyen ÚNICAMENTE a partir de los claims
  del JWT `Authorization: Bearer <token>`
  (`{sub, ten, plan, typ, iat, exp, jti, sid}`). Los
  *flags* del plan se recalculan siempre con `edecan_schemas.plans.PLANES[plan]`
  — **nunca** se confía en un flag que viniera embebido en el token.
- Sesión/])Repo: `get_platform_repo` (sesión sin `tenant_id`, rol dueño, para
  operaciones sin contexto de tenant todavía: registro, login, refresh, admin,
  webhook de Stripe) y `get_repo` (sesión con `SET LOCAL app.tenant_id`, para
  todo lo demás — aísla por Row-Level Security, ver ARCHITECTURE.md §2).
- `get_redis`, `get_llm_router`, `get_vault`: construyen los colaboradores que
  entran en `ToolContext` (§10.7) al correr un turno del agente. `get_llm_router`
  resuelve BRING-YOUR-OWN por tenant (WP-V3-02, `DIRECCION_ACTUAL.md` "Modelo
  de credenciales": ver `load_tenant_llm_config` más abajo) — SIN fallback a
  ninguna credencial de plataforma: mismo patrón que ya usa Twilio
  (`TokenVault`, `premium/edecan_premium/telephony.py::for_tenant`, que lanza
  `TwilioNotConnectedError` y nunca cae a una cuenta compartida). Si el
  tenant no conectó su propio proveedor, `get_llm_router` corta la request
  con `HTTPException(400)` en vez de servir el turno con la API key del
  operador de la plataforma.
- `rate_limit`: límite simple de 60 solicitudes/min por tenant (Redis INCR+EXPIRE).
- `require_superadmin`: gate para `edecan_api.routers.admin`.
- `_redis_client`: además del `REDIS_URL` real de siempre, soporta
  `REDIS_URL="memory://..."` (modo escritorio Tauri, un solo proceso, ver
  `DIRECCION_ACTUAL.md` "Stack de la app de escritorio") con `fakeredis`.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import redis.asyncio as redis_asyncio
from edecan_db.session import get_session
from edecan_db.vault import KmsKeyProvider, LocalKeyProvider, TokenVault
from edecan_llm.router import LLMRouter
from edecan_schemas.plans import PLANES
from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.config import Settings, get_settings
from edecan_api.repo import Repo, SqlRepo
from edecan_api.security import DecodedToken, TokenError, decode_token

# `edecan_llm.config.LLMProviderConfig` es el contrato pinned de WP-V3-03 (se
# construye EN PARALELO a este WP, ver ARCHITECTURE.md §12) — import con
# guardia: si todavía no existe, `LLMProviderConfig` queda en `None` y
# `load_tenant_llm_config` no puede construir ninguna config de tenant (ver
# más abajo), así que `get_llm_router` corta la request exactamente igual que
# si el tenant no hubiera conectado nada — NUNCA degrada al `LLMRouter` de
# plataforma (el `try/except ImportError` en sí es el mismo criterio que
# `edecan_connectors.registry` con `SOCIAL_CONNECTORS`/`MESSAGING_CONNECTORS`,
# pero solo para tolerar el orden de aterrizaje de los WP en paralelo, no
# para justificar un fallback de credenciales). Esto hace que este WP
# funcione igual sin importar si aterriza antes o después que WP-V3-03.
try:
    from edecan_llm.config import LLMProviderConfig
except ImportError:  # pragma: no cover - WP-V3-03 todavía no aterrizó
    LLMProviderConfig = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

RATE_LIMIT_MAX_REQUESTS = 60
RATE_LIMIT_WINDOW_SECONDS = 60

# Connector keys del TokenVault para credenciales bring-your-own resueltas por
# tenant (WP-V3-02, `docs/credenciales.md`) — centralizadas acá porque tanto
# `routers/credentials.py` (las escribe) como `routers/voice.py` (lee las de
# voz) y este módulo (lee la de LLM) las necesitan; evita triplicar el string
# mágico entre los tres archivos.
LLM_CONNECTOR_KEY = "llm"
VOICE_STT_CONNECTOR_KEY = "voice_stt"
VOICE_TTS_CONNECTOR_KEY = "voice_tts"


# ---------------------------------------------------------------------------
# Identidad: usuario/tenant actuales (desde el JWT, nunca desde la DB)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TenantCtx:
    """Contexto de tenant recalculado server-side en cada request."""

    tenant_id: uuid.UUID
    plan_key: str
    flags: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CurrentUser:
    user_id: uuid.UUID
    tenant: TenantCtx

    @property
    def tenant_id(self) -> uuid.UUID:
        return self.tenant.tenant_id


def flags_for_plan(plan_key: str) -> dict[str, Any]:
    """`edecan_schemas.plans.PLANES[plan_key].flags`, o `{}` si el plan no existe
    (p. ej. quedó huérfano tras un cambio de catálogo de planes) — nunca lanza."""
    plan = PLANES.get(plan_key)
    return dict(plan.flags) if plan is not None else {}


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.strip():
        raise _unauthorized("Falta el header Authorization: Bearer <token>.")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauthorized("Header Authorization mal formado: se espera 'Bearer <token>'.")
    return token.strip()


async def get_current_user(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> CurrentUser:
    """Decodifica el access token y arma `CurrentUser` con flags recalculados."""
    token = _extract_bearer_token(authorization)
    try:
        decoded: DecodedToken = decode_token(
            token, secret=settings.JWT_SECRET, expected_typ="access"
        )
    except TokenError as exc:
        raise _unauthorized(str(exc)) from exc

    tenant = TenantCtx(
        tenant_id=decoded.ten, plan_key=decoded.plan, flags=flags_for_plan(decoded.plan)
    )
    return CurrentUser(user_id=decoded.sub, tenant=tenant)


async def get_tenant_ctx(current_user: CurrentUser = Depends(get_current_user)) -> TenantCtx:
    return current_user.tenant


# ---------------------------------------------------------------------------
# Sesión / Repo (aislamiento multi-tenant vía RLS — ARCHITECTURE.md §2, §10.3)
# ---------------------------------------------------------------------------


async def get_platform_session() -> AsyncIterator[AsyncSession]:
    """Sesión "plataforma": rol dueño, sin `tenant_id` (bypassa RLS).

    Para operaciones que todavía no tienen contexto de tenant (registro,
    login, refresh, TOTP) o que son intrínsecamente cross-tenant (admin,
    webhook de Stripe) — igual que "el worker" en ARCHITECTURE.md §2, el
    código que la usa debe filtrar `tenant_id` explícitamente donde aplique.
    """
    async with get_session(None) as session:
        yield session


async def get_platform_repo(
    session: AsyncSession = Depends(get_platform_session, scope="function"),
) -> Repo:
    """Repo global cuya transacción termina antes de enviar la respuesta.

    Es especialmente importante en registro: el access token nunca debe
    llegar al cliente antes de que tenant, usuario y membresía sean visibles
    para la siguiente petición.
    """
    return SqlRepo(session)


async def get_tenant_session(
    current_user: CurrentUser = Depends(get_current_user),
) -> AsyncIterator[AsyncSession]:
    """Sesión con `SET LOCAL ROLE app_user` + `app.tenant_id` del usuario actual."""
    async with get_session(current_user.tenant_id) as session:
        yield session


async def get_repo(
    session: AsyncSession = Depends(get_tenant_session, scope="function"),
) -> Repo:
    """Repo del tenant con commit/rollback antes de responder al cliente.

    Así un 2xx confirma una escritura ya persistida y cualquier fallo del
    commit se convierte en error HTTP en lugar de aparecer después de haber
    entregado una respuesta exitosa.
    """
    return SqlRepo(session)


# ---------------------------------------------------------------------------
# Redis (rate limit, pair-codes, confirmaciones pendientes)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=8)
def _redis_client(url: str) -> redis_asyncio.Redis:
    """Cliente Redis cacheado por `url` (`lru_cache` ya garantiza una única
    instancia por proceso para cada valor distinto de `url`).

    Modo escritorio (Tauri, un solo proceso, sin infraestructura separada —
    `DIRECCION_ACTUAL.md` "Stack de la app de escritorio"): si `url` empieza
    con `"memory://"`, en vez de abrir una conexión TCP a un Redis real (que
    el cliente de escritorio no tiene por qué tener instalado) se usa
    `fakeredis` — misma API async que `redis.asyncio.Redis`, en memoria del
    propio proceso. El `lru_cache` de arriba es lo que hace que sea SIEMPRE
    la MISMA instancia mientras el proceso viva (rate-limit/pair-codes/
    confirmaciones pendientes necesitan estado compartido entre requests, no
    un Redis nuevo y vacío en cada llamada). Requiere el paquete opcional
    `fakeredis` instalado; si no lo está, falla con un `RuntimeError` claro
    en vez de un `ImportError` críptico en medio de una request.
    """
    if url.startswith("memory://"):
        try:
            from fakeredis import aioredis as fakeredis_aioredis
        except ImportError as exc:
            raise RuntimeError(
                "REDIS_URL='memory://...' (modo escritorio sin Redis real) requiere "
                "el paquete 'fakeredis' instalado, o configura un REDIS_URL real "
                "(p. ej. redis://localhost:6379/0)."
            ) from exc
        return fakeredis_aioredis.FakeRedis(decode_responses=True)
    return redis_asyncio.from_url(url, decode_responses=True)


def get_redis(settings: Settings = Depends(get_settings)) -> redis_asyncio.Redis:
    return _redis_client(settings.REDIS_URL)


async def rate_limit(
    current_user: CurrentUser = Depends(get_current_user),
    redis_client: redis_asyncio.Redis = Depends(get_redis),
) -> None:
    """Límite simple por tenant: 60 solicitudes/min, ventana fija (INCR+EXPIRE)."""
    window = int(time.time()) // RATE_LIMIT_WINDOW_SECONDS
    key = f"ratelimit:{current_user.tenant_id}:{window}"
    count = await redis_client.incr(key)
    if count == 1:
        await redis_client.expire(key, RATE_LIMIT_WINDOW_SECONDS)
    if count > RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Demasiadas solicitudes: límite de {RATE_LIMIT_MAX_REQUESTS} "
                "por minuto por tenant."
            ),
        )


# ---------------------------------------------------------------------------
# LLM router — sin callback de uso: la persistencia de `usage_events` de
# tokens ocurre al recibir el evento `done` del turno (ver `routers/conversations.py`)
# para no contar dos veces.
#
# Bring-your-own por tenant, SIN fallback a plataforma (WP-V3-02;
# `DIRECCION_ACTUAL.md` "Modelo de credenciales: TODO lo trae el cliente,
# siempre" marca explícitamente esto como corrección pendiente: "deben
# resolver por tenant igual que Twilio"): `load_tenant_llm_config` lee del
# `TokenVault` (connector_key `LLM_CONNECTOR_KEY`, guardado por
# `PUT /v1/credentials/llm` en `routers/credentials.py`) la config que el
# tenant conectó. Si existe, `get_llm_router` arma un `LLMRouter` propio para
# ESE tenant; si no (o si algo falla leyéndola), NUNCA cae a
# `ANTHROPIC_API_KEY`/`.env` de plataforma — corta la request con
# `HTTPException(400)`, mismo criterio que `TwilioNotConnectedError`
# (`premium/edecan_premium/telephony.py::for_tenant`). Sin este corte, un
# tenant que no trajo su propia credencial terminaría facturando en silencio
# contra la cuenta del operador de la plataforma — el hueco de ToS/legal que
# este WP existe para cerrar, no un "back-compat" aceptable.
# ---------------------------------------------------------------------------


async def load_tenant_llm_config(
    session: AsyncSession, settings: Settings, tenant_id: uuid.UUID
) -> LLMProviderConfig | None:
    """Config LLM bring-your-own del tenant, o `None` si no tiene ninguna
    utilizable (`DIRECCION_ACTUAL.md` "Modelo de credenciales: TODO lo trae
    el cliente"; `docs/credenciales.md`).

    `None` en TODOS estos casos — nunca lanza; quien la llama (`get_llm_router`)
    decide qué hacer con `None`, que HOY es cortar la request con
    `HTTPException(400)`, nunca degradar a una credencial de plataforma:
    - `edecan_llm.config.LLMProviderConfig` todavía no existe (WP-V3-03 se
      construye en paralelo — ver el `try/except ImportError` al tope de este
      módulo).
    - El tenant nunca conectó un proveedor propio (`GET /v1/credentials` con
      `"llm": null`) — no hay `connector_accounts` con `connector_key="llm"`
      para este tenant.
    - Cualquier error leyendo/descifrando/parseando lo guardado: vault roto,
      JSON corrupto, o `session` no utilizable (p. ej. en tests que
      sobreescriben `get_tenant_session`/`get_llm_router` con `None` — ver
      `apps/api/tests/conftest.py`). Se registra con `logger.warning` y se
      trata como "el tenant no conectó nada".
    """
    if LLMProviderConfig is None:
        logger.debug(
            "edecan_llm.config.LLMProviderConfig no disponible todavía "
            "(WP-V3-03 en paralelo); get_llm_router tratará esto como "
            "'tenant sin proveedor LLM conectado'."
        )
        return None
    try:
        repo = SqlRepo(session)
        accounts = await repo.list_connector_accounts(tenant_id=tenant_id)
        account = next(
            (a for a in accounts if a["connector_key"] == LLM_CONNECTOR_KEY), None
        )
        if account is None:
            return None

        vault = TokenVault(session, build_key_provider(settings))
        bundle = await vault.get(tenant_id, account["id"])
        if bundle is None:
            return None

        data = json.loads(bundle.access_token)
        return LLMProviderConfig(
            kind=data["kind"],
            api_key=data.get("api_key"),
            base_url=data.get("base_url"),
            model_principal=data.get("model_principal"),
            model_rapido=data.get("model_rapido"),
            extra=data.get("extra") or {},
        )
    except Exception:
        logger.warning(
            "No se pudo cargar la config LLM bring-your-own del tenant_id=%s; "
            "se trata como 'sin proveedor conectado' (get_llm_router corta la "
            "request, nunca degrada a una credencial de plataforma).",
            tenant_id,
            exc_info=True,
        )
        return None


async def get_llm_router(
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_session),
    settings: Settings = Depends(get_settings),
) -> LLMRouter:
    """`LLMRouter` bring-your-own del tenant actual — NUNCA el de plataforma.

    Corta la request con `HTTPException(400)` si el tenant no conectó su
    propio proveedor LLM, en vez de degradar en silencio a
    `ANTHROPIC_API_KEY`/`.env` de plataforma (ver el bloque de comentarios
    arriba y `DIRECCION_ACTUAL.md` "Modelo de credenciales"): sin este corte,
    cualquier tenant sin credencial propia terminaría facturando en silencio
    contra la cuenta del operador de la plataforma.
    """
    provider_config = await load_tenant_llm_config(session, settings, current_user.tenant_id)
    if provider_config is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Este tenant no tiene un proveedor de LLM conectado. Conéctalo en "
                "Configuración (PUT /v1/credentials/llm) antes de chatear — Edecán "
                "nunca usa una credencial de LLM compartida de la plataforma."
            ),
        )
    return LLMRouter(settings, on_usage=None, provider_config=provider_config)


# ---------------------------------------------------------------------------
# TokenVault (§10.4) — cifrado envolvente de credenciales de conectores/Twilio
# ---------------------------------------------------------------------------


def build_key_provider(settings: Settings) -> Any:
    """`KmsKeyProvider` si hay `KMS_KEY_ID` (prod); si no, `LocalKeyProvider` (dev/self-host)."""
    if settings.KMS_KEY_ID:
        return KmsKeyProvider(
            settings.KMS_KEY_ID,
            region_name=settings.AWS_REGION,
            endpoint_url=settings.AWS_ENDPOINT_URL,
        )
    return LocalKeyProvider(settings.LOCAL_MASTER_KEY)


async def get_vault(
    session: AsyncSession = Depends(get_tenant_session),
    settings: Settings = Depends(get_settings),
) -> TokenVault:
    return TokenVault(session, build_key_provider(settings))


# ---------------------------------------------------------------------------
# MCP bring-your-own (ARCHITECTURE.md §15, WP-V6-07): tools dinámicas de los
# servidores MCP que el propio tenant conectó (`PUT /v1/mcp/servers`,
# `edecan_api.routers.mcp`) — se fusionan SOLO para el turno actual vía
# `Agent.run_turn(..., extra_tools=...)` (`edecan_core.agent`); NUNCA se
# registran en el `ToolRegistry` compartido de `app.state` (ver el docstring
# de `Agent.run_turn` para por qué eso importa: un servidor MCP mal
# comportado o que el tenant desconecta no debe poder "contaminar" lo que
# ven los demás tenants ni turnos futuros de este mismo tenant sin volver a
# pasar por este mismo camino).
#
# `edecan_mcp` se importa con guardia DENTRO de `_build_mcp_tools_for_tenant`
# (mismo criterio que `edecan_llm.config.LLMProviderConfig` más arriba: un
# paquete hermano que puede todavía no estar instalado/declarado como
# dependencia de `apps/api` mientras el linchpin de v6 lo registra en el
# workspace uv, ver la nota al final de `packages/mcp/pyproject.toml`) — sin
# él, `get_mcp_tools_for_tenant` simplemente devuelve `[]`, nunca rompe el
# import de este módulo ni el chat.
# ---------------------------------------------------------------------------

MCP_CONNECTOR_KEY = "mcp"
_MCP_TOOLS_FLAG = "tools.mcp"
_MCP_TOOLS_CACHE_TTL_SECONDS = 60.0


async def get_mcp_tools_for_tenant(request: Request, current_user: CurrentUser) -> list[Any]:
    """Tools MCP bring-your-own del tenant actual, listas para pasar como
    `extra_tools` a `Agent.run_turn` (`edecan_api.routers.conversations`,
    los dos caminos: turno normal y `POST .../confirm`).

    Caché débil por tenant en `app.state.mcp_tools_cache`, TTL
    `_MCP_TOOLS_CACHE_TTL_SECONDS` (60s): `construir_tools_mcp`
    (`edecan_mcp.tool_adapter`) hace una ronda de red/proceso POR SERVIDOR
    configurado (handshake MCP completo + `tools/list`) en cada llamada —
    sin esta caché, cada mensaje de chat pagaría ese costo de nuevo. Un
    `dict` simple `{tenant_id: (expira_monotonic, tools)}` alcanza: no hace
    falta invalidación activa al conectar/editar un servidor, tarda como
    máximo el TTL en reflejarse.

    Fail-open ante CUALQUIER error (flag `tools.mcp` apagado, `edecan_mcp`
    no instalado todavía, vault/sesión que fallan, un servidor MCP caído):
    devuelve `[]` con un `logger.warning` — un servidor MCP mal configurado
    o momentáneamente inalcanzable NUNCA debe romper el chat del tenant.
    """
    if not current_user.tenant.flags.get(_MCP_TOOLS_FLAG, False):
        return []

    cache: dict[uuid.UUID, tuple[float, list[Any]]] | None = getattr(
        request.app.state, "mcp_tools_cache", None
    )
    if cache is None:
        cache = {}
        request.app.state.mcp_tools_cache = cache

    ahora = time.monotonic()
    cacheado = cache.get(current_user.tenant_id)
    if cacheado is not None and cacheado[0] > ahora:
        return cacheado[1]

    try:
        tools = await _build_mcp_tools_for_tenant(current_user.tenant_id)
    except Exception:
        logger.warning(
            "No se pudieron construir las tools MCP del tenant_id=%s; el chat sigue sin ellas.",
            current_user.tenant_id,
            exc_info=True,
        )
        tools = []

    cache[current_user.tenant_id] = (ahora + _MCP_TOOLS_CACHE_TTL_SECONDS, tools)
    return tools


async def _build_mcp_tools_for_tenant(tenant_id: uuid.UUID) -> list[Any]:
    # Import perezoso CON GUARDIA — ver el comentario de arriba.
    try:
        from edecan_mcp.provider_config import deserializar_config_mcp
        from edecan_mcp.tool_adapter import construir_tools_mcp, sanear_slug
    except ImportError:
        logger.debug("edecan_mcp no disponible todavía; get_mcp_tools_for_tenant devuelve [].")
        return []

    settings = get_settings()
    local_mode = bool(getattr(settings, "EDECAN_LOCAL_MODE", False))

    configs = []
    headers_por_slug: dict[str, dict[str, str]] = {}
    async with get_session(tenant_id) as session:
        repo = SqlRepo(session)
        cuentas = await repo.list_connector_accounts(tenant_id=tenant_id)
        cuentas_mcp = [a for a in cuentas if a["connector_key"] == MCP_CONNECTOR_KEY]
        if not cuentas_mcp:
            return []

        # `ARCHITECTURE.md` §15.g (pinned): la config completa (`{nombre,
        # transporte, url, comando, headers}`) vive TODA junta, cifrada, en
        # el vault — `connector_accounts` es identidad pura, sin config.
        vault = TokenVault(session, build_key_provider(settings))
        for cuenta in cuentas_mcp:
            nombre = cuenta["external_account_id"]
            bundle = await vault.get(tenant_id, cuenta["id"])
            raw = bundle.access_token if bundle is not None else None
            config, headers = deserializar_config_mcp(raw, nombre_fallback=nombre)
            configs.append(config)
            headers_por_slug[sanear_slug(nombre)] = headers

    return await construir_tools_mcp(configs, headers_por_slug, local_mode=local_mode)


# ---------------------------------------------------------------------------
# ToolRegistry compartido (construido en el `lifespan` de `main.py`; si por
# algún motivo una request llega antes de que el lifespan haya corrido —p. ej.
# un test que no dispara el ciclo de vida ASGI— se construye perezosamente
# una única vez y se cachea en `app.state`).
# ---------------------------------------------------------------------------


def get_tool_registry(request: Request) -> Any:
    registry = getattr(request.app.state, "tool_registry", None)
    if registry is None:
        from edecan_core.tools import ToolRegistry

        registry = ToolRegistry()
        registry.load_entry_points(group="edecan.tools")
        request.app.state.tool_registry = registry
    return registry


# ---------------------------------------------------------------------------
# Superadmin (edecan_api.routers.admin)
# ---------------------------------------------------------------------------


async def require_superadmin(
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_platform_repo),
) -> CurrentUser:
    user = await repo.get_user(current_user.user_id)
    if user is None or not user.get("is_superadmin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Requiere privilegios de superadmin."
        )
    return current_user
