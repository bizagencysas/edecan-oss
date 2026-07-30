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
- `llm_router`: `edecan_llm.router.LLMRouter` global, compartido entre
  jobs (reutiliza el cliente HTTP del proveedor en vez de abrir uno nuevo por
  job).
- `vault`: factory `(session) -> TokenVault` (`edecan_db.vault.TokenVault`,
  ARCHITECTURE.md §10.4) — se construye un `TokenVault` por transacción porque
  el contrato lo ata a una `AsyncSession` concreta (`TokenVault(session,
  key_provider)`).
- `llm_router_for(tenant_id)`: conserva el contrato de los handlers, pero la
  inferencia es global y automática. El tenant nunca selecciona proveedor ni
  modelo; Workers AI y `TaskRouter` resuelven chat, voz y trabajo no-IDE.

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

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_worker.config import Settings

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

    async def llm_router_for(self, tenant_id: UUID | None) -> Any:
        """Devuelve el router global administrado para cualquier tenant.

        El argumento se conserva por compatibilidad con los handlers. No se
        consulta el vault ni existe una selección de proveedor por usuario.
        """
        del tenant_id
        return self.llm_router

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
                "No se pudieron construir las tools MCP del tenant_id=%s; el job sigue sin ellas.",
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
        # completa (`{nombre, transporte, url, comando, headers, env}`) vive TODA
        # cifrada en el vault, ver el `for` de abajo.
        rows = (
            (
                await session.execute(
                    sql_text(
                        "SELECT id, external_account_id FROM connector_accounts "
                        "WHERE tenant_id = :tenant_id AND connector_key = :connector_key "
                        "ORDER BY created_at ASC"
                    ),
                    {"tenant_id": tenant_id, "connector_key": MCP_CONNECTOR_KEY},
                )
            )
            .mappings()
            .all()
        )
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
        llm_router = _build_llm_router(settings)
        stack.push_async_callback(llm_router.aclose)
        logger.info("clientes aioboto3 (s3, sqs) listos endpoint_url=%s", settings.AWS_ENDPOINT_URL)
        yield Deps(
            settings=settings,
            session_factory=get_session,
            s3=s3,
            sqs=sqs,
            embedder=_build_embedder(settings),
            llm_router=llm_router,
            vault=_build_vault_factory(settings),
        )
