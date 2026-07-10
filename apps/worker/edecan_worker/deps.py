"""Dependencias compartidas de `edecan_worker` (ARCHITECTURE.md §10.1, §10.2, §10.11).

`Deps` agrupa los recursos que recibe cada handler
(`Handler = Callable[[JobEnvelope, Deps], Awaitable[None]]`, ver
`edecan_worker.handlers`):

- `session_factory`: `edecan_db.session.get_session` tal cual. Los handlers
  SIEMPRE lo llaman como `session_factory(None)` — conexión "dueño" que
  bypassa Row-Level Security (ARCHITECTURE.md §2) — y por eso deben filtrar
  manualmente por `tenant_id` en cada query (ver `edecan_worker.repo.SqlRepo`).
- `s3` / `sqs`: clientes `aioboto3` ya abiertos, apuntando a `AWS_ENDPOINT_URL`
  si está definido (p. ej. LocalStack en dev).
- `embedder`: `OpenAICompatEmbedder` si hay un proveedor de embeddings
  OpenAI-compatible configurado de verdad (`OPENAI_COMPAT_BASE_URL` +
  `OPENAI_COMPAT_API_KEY` + `EMBEDDINGS_MODEL`, ninguno vacío ni con el valor
  placeholder de `.env.example`), si no `HashEmbedder` — ambos de
  `edecan_core` (ARCHITECTURE.md §10.7). Mismo criterio que
  `edecan_api.routers.conversations._has_real_embeddings_provider`
  (mantener en sync si cambia).
- `llm_router`: `edecan_llm.router.LLMRouter` DE PLATAFORMA, compartido entre
  jobs (reutiliza el cliente HTTP del proveedor en vez de abrir uno nuevo por
  job).
- `vault`: factory `(session) -> TokenVault` (`edecan_db.vault.TokenVault`,
  ARCHITECTURE.md §10.4) — se construye un `TokenVault` por transacción porque
  el contrato lo ata a una `AsyncSession` concreta (`TokenVault(session,
  key_provider)`).
- `llm_router_for(tenant_id)`: bring-your-own por tenant (WP-V3-02,
  `DIRECCION_ACTUAL.md` "Modelo de credenciales") — ver su docstring más abajo.
  Los 5 handlers que llaman al LLM (`generate_content`, `run_automation`,
  `run_mission`, `memory_consolidate`, `ingest_file`) lo usan en vez de
  `deps.llm_router` directo.

`build_deps(settings)` es el context manager async que arma la versión REAL de
`Deps` (lo usan `main.py`/`scheduler.py` para la vida del proceso). Hace
imports perezosos (dentro de las funciones, no al tope del módulo) de
`aioboto3`, `edecan_core`, `edecan_db.session` y `edecan_db.vault` porque
`edecan_core` (y, en este momento del desarrollo, `edecan_db.vault`) son
paquetes hermanos que pueden todavía no existir en este workspace mientras se
construyen en paralelo (ARCHITECTURE.md §10.1: "importar hermanos en código de
producción sí está permitido, por nombre de módulo"). Así, `edecan_worker.deps`
—y por tanto `edecan_worker.handlers`, que depende de este módulo— se puede
seguir importando y testeando con fakes aunque esos paquetes aún no existan.

Los tests NUNCA llaman a `build_deps`: construyen `Deps` directamente con
fakes (ver `tests/fakes.py::make_deps`), tal como exige ARCHITECTURE.md §10.1.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_worker.config import Settings

# Connector key del TokenVault para la credencial LLM bring-your-own del
# tenant (WP-V3-02) — MISMO valor que `edecan_api.deps.LLM_CONNECTOR_KEY`
# (duplicado a propósito: `apps/worker` y `apps/api` son deployables
# independientes, ARCHITECTURE.md §10.1, no se importan entre sí).
LLM_CONNECTOR_KEY = "llm"

# Ídem para servidores MCP bring-your-own (ARCHITECTURE.md §15, WP-V6-07) —
# MISMO valor que `edecan_api.routers.mcp.MCP_CONNECTOR_KEY`/`edecan_api.
# deps.MCP_CONNECTOR_KEY`, duplicado por el mismo motivo de arriba.
MCP_CONNECTOR_KEY = "mcp"
_MCP_TOOLS_FLAG = "tools.mcp"

if TYPE_CHECKING:
    # Solo tipo: el `Embedder` real de `edecan_core` (§10.7), no una copia local.
    # No hace falta en runtime gracias a `from __future__ import annotations`
    # (mismo patrón que `edecan_premium.campaigns` con `JobEnvelope`) — así este
    # módulo sigue sin tomar un import fuerte de `edecan_core` a nivel de módulo.
    from edecan_core.memory import Embedder

logger = logging.getLogger(__name__)

SessionFactory = Callable[[UUID | None], AbstractAsyncContextManager[AsyncSession]]
VaultFactory = Callable[[AsyncSession], Any]


class TenantLLMNotConnectedError(Exception):
    """El tenant no tiene un proveedor de LLM propio conectado (o no se pudo
    resolver de forma confiable) — ver `Deps.llm_router_for`. El mensaje es
    directamente presentable al tenant: termina como `jobs.last_error` en el
    runner local (`edecan_local.worker_loop._mark_failure`) y en los logs del
    despachador SQS (`edecan_worker.main._handle_message`)."""

    def __init__(self, tenant_id: UUID) -> None:
        self.tenant_id = tenant_id
        super().__init__(
            "Este tenant no tiene un proveedor de LLM propio conectado. Conecta uno en "
            "Configuración (PUT /v1/credentials/llm) — Edecán nunca usa una credencial de "
            "LLM compartida de la plataforma."
        )


@dataclass
class Deps:
    """Recursos compartidos que recibe cada `Handler(env, deps)`."""

    settings: Settings
    session_factory: SessionFactory
    s3: Any
    sqs: Any
    embedder: Embedder
    llm_router: Any
    vault: VaultFactory
    # Caché por tenant de `llm_router_for` (ver su docstring) — `Deps` vive
    # todo el proceso del worker (`build_deps`), así que sin esta caché cada
    # job pagaría una consulta a Postgres + al vault por CADA job de ese
    # tenant, no solo la primera vez. `repr=False`/`compare=False`: no es
    # parte de la identidad "de datos" de `Deps` (dos `Deps` con los mismos
    # colaboradores pero distinto estado de caché siguen siendo "iguales" a
    # efectos de test/debug), y no aporta nada útil a un `repr()`.
    _tenant_llm_routers: dict[UUID, Any] = field(
        default_factory=dict, repr=False, compare=False
    )

    async def llm_router_for(self, tenant_id: UUID | None) -> Any:
        """`LLMRouter` bring-your-own del tenant (WP-V3-02, `DIRECCION_ACTUAL.md`
        "Modelo de credenciales: TODO lo trae el cliente"): si el tenant
        conectó su propio proveedor LLM (`PUT /v1/credentials/llm`,
        `TokenVault` connector_key `"llm"`), se usa ESE; si no, o si CUALQUIER
        paso de la resolución falla, lanza `TenantLLMNotConnectedError` — NUNCA
        cae a `self.llm_router` (el de plataforma) para un tenant real (mismo
        criterio fail-closed que `edecan_api.deps.get_llm_router`, que corta
        con `HTTPException(400)` en vez de degradar — ver
        `docs/credenciales.md` "Orden de resolución" y `docs/roadmap.md`).

        Como esto corre en un job asíncrono (no hay a quién devolverle un
        HTTP 400), la excepción se deja propagar tal cual hasta el
        despachador del job (`edecan_worker.main._handle_message` /
        `edecan_local.worker_loop`): ambos ya tratan CUALQUIER excepción de un
        handler como fallo del job (reintento con backoff hasta
        `MAX_ATTEMPTS`, luego DLQ/`status='error'`) — reintentar no arregla
        que el tenant no haya conectado nada, pero tampoco hace daño: cada
        intento vuelve a fallar rápido con este mismo mensaje claro, sin
        tocar jamás `ANTHROPIC_API_KEY` de plataforma. `str(exc)` termina
        como `jobs.last_error` en el runner local
        (`edecan_local.worker_loop._mark_failure`), así que el mensaje de
        `TenantLLMNotConnectedError` debe ser directamente presentable al
        tenant.

        `tenant_id=None` (jobs de sistema sin tenant propio, p. ej.
        `send_reminder_scan`) devuelve `self.llm_router` directo, sin
        siquiera intentar el vault: no hay tenant del que leer nada, y ese
        caso SÍ es legítimamente de plataforma.

        Cachea por tenant en `self._tenant_llm_routers` SOLO en éxito — ver
        el comentario del campo arriba. Un fallo nunca se cachea: el próximo
        intento (reintento del job, u otro job del mismo tenant en el mismo
        proceso) vuelve a consultar el vault, por si el tenant conectó algo
        mientras tanto.
        """
        if tenant_id is None:
            return self.llm_router
        if tenant_id in self._tenant_llm_routers:
            return self._tenant_llm_routers[tenant_id]

        resolved = await self._resolve_tenant_llm_router(tenant_id)
        self._tenant_llm_routers[tenant_id] = resolved
        return resolved

    async def _resolve_tenant_llm_router(self, tenant_id: UUID) -> Any:
        # `edecan_llm.config.LLMProviderConfig` es el contrato pinned de
        # WP-V3-03 (se construía EN PARALELO durante v3, ver ARCHITECTURE.md
        # §12) — import perezoso CON GUARDIA (mismo criterio que el resto de
        # este módulo con `edecan_core`/`edecan_db`, ver docstring del
        # módulo). Mismo criterio fail-closed que
        # `edecan_api.deps.load_tenant_llm_config`: si todavía no existe (no
        # debería pasar ya que v3 terminó, pero un self-host con un checkout
        # parcial es posible), se trata igual que "tenant sin nada
        # conectado" — nunca se degrada al `LLMRouter` de plataforma.
        try:
            from edecan_llm.config import LLMProviderConfig
        except ImportError as exc:
            logger.warning(
                "edecan_llm.config.LLMProviderConfig no disponible; "
                "tenant_id=%s se trata como sin proveedor LLM conectado.",
                tenant_id,
            )
            raise TenantLLMNotConnectedError(tenant_id) from exc

        try:
            from edecan_llm.router import LLMRouter

            async with self.session_factory(None) as session:
                row = (
                    await session.execute(
                        sql_text(
                            "SELECT id FROM connector_accounts WHERE tenant_id = :tenant_id "
                            "AND connector_key = :connector_key "
                            "ORDER BY created_at DESC LIMIT 1"
                        ),
                        {"tenant_id": tenant_id, "connector_key": LLM_CONNECTOR_KEY},
                    )
                ).mappings().first()
                if row is None:
                    raise TenantLLMNotConnectedError(tenant_id)

                vault = self.vault(session)
                bundle = await vault.get(tenant_id=tenant_id, connector_account_id=row["id"])
                if bundle is None:
                    raise TenantLLMNotConnectedError(tenant_id)

                data = json.loads(bundle.access_token)
                provider_config = LLMProviderConfig(
                    kind=data["kind"],
                    api_key=data.get("api_key"),
                    base_url=data.get("base_url"),
                    model_principal=data.get("model_principal"),
                    model_rapido=data.get("model_rapido"),
                    extra=data.get("extra") or {},
                )

            return LLMRouter(self.settings, on_usage=None, provider_config=provider_config)
        except TenantLLMNotConnectedError:
            raise
        except Exception as exc:
            logger.warning(
                "No se pudo resolver el LLMRouter bring-your-own del tenant_id=%s; "
                "se trata como sin proveedor LLM conectado (nunca se degrada a la "
                "credencial de plataforma).",
                tenant_id,
                exc_info=True,
            )
            raise TenantLLMNotConnectedError(tenant_id) from exc

    async def mcp_tools_para(
        self, tenant_id: UUID | None, session: AsyncSession, flags: dict[str, Any]
    ) -> list[Any]:
        """Tools MCP bring-your-own del tenant para un job headless (misión o
        automatización, `ARCHITECTURE.md` §15) — MISMAS reglas que
        `apps/api/edecan_api/deps.py::get_mcp_tools_for_tenant`: flag de plan
        `tools.mcp`, transporte http SIEMPRE permitido, stdio SOLO si
        `self.settings` trae `EDECAN_LOCAL_MODE=True`.

        El llamador (`run_mission.py`/`run_automation.py`) las registra en el
        `ToolRegistry` recién construido para ESE job ANTES de aplicar
        `RestrictedRegistry`/`_build_safe_registry` — así ambos filtros
        (perfil de misión, `dangerous` excluido de automatizaciones headless)
        siguen aplicando sobre las tools MCP exactamente igual que sobre
        cualquier otra (ver el docstring de esos dos módulos).

        Reutiliza la `session` "dueño" que el llamador YA tiene abierta
        (`deps.session_factory(None)`) — nunca abre una propia. A diferencia
        de `apps/api/edecan_api/deps.py`, SIN caché: cada job ya paga su
        propia ronda de handshake/`tools/list` una única vez por ejecución,
        no hay "cada mensaje de chat" que amortizar.

        Fail-open ante CUALQUIER error (flag apagado, `edecan_mcp` no
        instalado todavía, vault/consulta que fallan, un servidor MCP caído):
        devuelve `[]` con un `logger.warning` — un servidor MCP mal
        configurado NUNCA debe tumbar una misión/automatización completa.
        """
        if tenant_id is None or not flags.get(_MCP_TOOLS_FLAG, False):
            return []
        try:
            return await self._build_mcp_tools(tenant_id, session)
        except Exception:
            logger.warning(
                "No se pudieron construir las tools MCP del tenant_id=%s; el job "
                "sigue sin ellas.",
                tenant_id,
                exc_info=True,
            )
            return []

    async def _build_mcp_tools(self, tenant_id: UUID, session: AsyncSession) -> list[Any]:
        # Import perezoso CON GUARDIA, mismo criterio que `edecan_llm.config.
        # LLMProviderConfig` más arriba: `edecan_mcp` puede todavía no estar
        # instalado/declarado como dependencia de `apps/worker` mientras el
        # linchpin de v6 lo registra en el workspace uv (ver la nota al final
        # de `packages/mcp/pyproject.toml`).
        try:
            from edecan_mcp.provider_config import deserializar_config_mcp
            from edecan_mcp.tool_adapter import construir_tools_mcp, sanear_slug
        except ImportError:
            logger.debug("edecan_mcp no disponible todavía; mcp_tools_para devuelve [].")
            return []

        # `ARCHITECTURE.md` §15.g (pinned): `connector_accounts` es identidad
        # pura para MCP — nunca lleva la config (ni siquiera la parte
        # no-secreta). Por eso esta consulta ya NO trae `scopes`: la config
        # completa (`{nombre, transporte, url, comando, headers}`) vive TODA
        # cifrada en el vault, ver el `for` de abajo.
        rows = (
            await session.execute(
                sql_text(
                    "SELECT id, external_account_id FROM connector_accounts "
                    "WHERE tenant_id = :tenant_id AND connector_key = :connector_key "
                    "ORDER BY created_at ASC"
                ),
                {"tenant_id": tenant_id, "connector_key": MCP_CONNECTOR_KEY},
            )
        ).mappings().all()
        if not rows:
            return []

        local_mode = bool(getattr(self.settings, "EDECAN_LOCAL_MODE", False))
        vault = self.vault(session)
        configs = []
        headers_por_slug: dict[str, dict[str, str]] = {}
        for row in rows:
            nombre = row["external_account_id"]
            bundle = await vault.get(tenant_id=tenant_id, connector_account_id=row["id"])
            raw = bundle.access_token if bundle is not None else None
            config, headers = deserializar_config_mcp(raw, nombre_fallback=nombre)
            configs.append(config)
            headers_por_slug[sanear_slug(nombre)] = headers

        return await construir_tools_mcp(configs, headers_por_slug, local_mode=local_mode)


# Placeholders públicos de `.env.example` para el proveedor de embeddings
# OpenAI-compatible (no son secretos: compararlos aquí no filtra nada). Mismo
# criterio que `edecan_api.routers.conversations._has_real_embeddings_provider`
# (mantener en sync si cambia): un `.env` recién copiado de `.env.example` sin
# tocar estas dos variables trae EXACTAMENTE estos valores —strings no vacíos,
# por tanto truthy— y además `OPENAI_COMPAT_BASE_URL` YA trae un valor real de
# fábrica (`https://api.openai.com/v1`), así que revisar solo
# `OPENAI_COMPAT_BASE_URL`, o solo truthiness de las tres variables, no basta
# para detectar que el proveedor sigue sin configurar. Sin este chequeo, el
# setup mínimo de `docs/self-hosting.md` §2.1 dispara una llamada HTTP real a
# `https://api.openai.com/v1/embeddings` con una API key falsa en cada job que
# use `deps.embedder` (p. ej. `ingest_file`), en vez de caer al `HashEmbedder`
# offline que promete `docs/self-hosting.md` §4.
_OPENAI_COMPAT_API_KEY_PLACEHOLDER = "TU_OPENAI_COMPAT_API_KEY_AQUI"
_EMBEDDINGS_MODEL_PLACEHOLDER = "TU_EMBEDDINGS_MODEL_AQUI"


def _has_real_embeddings_provider(settings: Settings) -> bool:
    """`True` solo si hay un proveedor de embeddings OpenAI-compatible
    configurado de verdad: `OPENAI_COMPAT_BASE_URL`/`OPENAI_COMPAT_API_KEY`/
    `EMBEDDINGS_MODEL` no vacíos y, además, `OPENAI_COMPAT_API_KEY`/
    `EMBEDDINGS_MODEL` distintos de los placeholders públicos de
    `.env.example` (ver comentario arriba)."""
    return bool(
        settings.OPENAI_COMPAT_BASE_URL
        and settings.OPENAI_COMPAT_API_KEY
        and settings.OPENAI_COMPAT_API_KEY != _OPENAI_COMPAT_API_KEY_PLACEHOLDER
        and settings.EMBEDDINGS_MODEL
        and settings.EMBEDDINGS_MODEL != _EMBEDDINGS_MODEL_PLACEHOLDER
    )


def _build_embedder(settings: Settings) -> Embedder:
    """`OpenAICompatEmbedder` si hay un proveedor de embeddings real
    configurado, si no `HashEmbedder` (§10.7). Mismo criterio que
    `edecan_api.routers.conversations._build_embedder`."""
    if _has_real_embeddings_provider(settings):
        from edecan_core.memory import (
            OpenAICompatEmbedder,  # import perezoso, ver docstring del módulo
        )

        return OpenAICompatEmbedder(
            base_url=settings.OPENAI_COMPAT_BASE_URL,
            api_key=settings.OPENAI_COMPAT_API_KEY,
            model=settings.EMBEDDINGS_MODEL,
        )

    from edecan_core.memory import HashEmbedder  # import perezoso, ver docstring del módulo

    return HashEmbedder(dim=settings.EMBEDDINGS_DIM)


def _build_llm_router(settings: Settings) -> Any:
    from edecan_llm.router import (
        LLMRouter,  # import perezoso por uniformidad con el resto de builders
    )

    return LLMRouter(settings)


def _build_vault_factory(settings: Settings) -> VaultFactory:
    """Arma el `KeyProvider` una vez (vía `edecan_db.vault.get_key_provider`,
    que ya decide `KmsKeyProvider` vs `LocalKeyProvider` según `KMS_KEY_ID`/
    `LOCAL_MASTER_KEY`) y devuelve un factory `(session) -> TokenVault`.

    Delega en `edecan_db.settings.get_settings()` (en vez de adaptar los
    campos de `edecan_worker.config.Settings` a mano) porque ambas leen las
    MISMAS variables de entorno (`pydantic-settings` no distingue mayúsculas
    de minúsculas en el nombre de campo al mapear env vars por defecto) — así
    esta función no duplica la lógica de selección de `KeyProvider` del
    paquete dueño del contrato (`ARCHITECTURE.md` §10.4).
    """
    # Import perezoso, ver docstring del módulo: edecan_db.vault todavía puede
    # no existir mientras ese paquete hermano se construye en paralelo.
    from edecan_db.settings import get_settings as get_db_settings
    from edecan_db.vault import TokenVault, get_key_provider

    key_provider = get_key_provider(get_db_settings())

    def vault_factory(session: AsyncSession) -> Any:
        return TokenVault(session, key_provider)

    return vault_factory


@asynccontextmanager
async def build_deps(settings: Settings) -> AsyncIterator[Deps]:
    """Arma `Deps` "de verdad" para `main.py`/`scheduler.py`.

    Abre los clientes `aioboto3` de S3/SQS (apuntando a `AWS_ENDPOINT_URL` si
    está definido) y los cierra al salir del bloque `async with`.
    """
    import aioboto3  # import perezoso: solo lo necesita esta función, no los tests con fakes
    from edecan_db.session import get_session  # import perezoso, ver docstring del módulo

    boto_session = aioboto3.Session()
    client_kwargs: dict[str, Any] = {"region_name": settings.AWS_REGION}
    if settings.AWS_ENDPOINT_URL:
        client_kwargs["endpoint_url"] = settings.AWS_ENDPOINT_URL

    async with AsyncExitStack() as stack:
        s3 = await stack.enter_async_context(boto_session.client("s3", **client_kwargs))
        sqs = await stack.enter_async_context(boto_session.client("sqs", **client_kwargs))
        logger.info("clientes aioboto3 (s3, sqs) listos endpoint_url=%s", settings.AWS_ENDPOINT_URL)
        yield Deps(
            settings=settings,
            session_factory=get_session,
            s3=s3,
            sqs=sqs,
            embedder=_build_embedder(settings),
            llm_router=_build_llm_router(settings),
            vault=_build_vault_factory(settings),
        )
