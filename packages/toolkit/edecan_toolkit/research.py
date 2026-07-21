"""Investigación web (`ARCHITECTURE.md` §10.14, §10.2).

`SearchProvider` es el protocolo que implementan `BraveSearch`, `TavilySearch`
y `StubSearch`; `get_search_provider(settings)` elige la implementación según
`settings.SEARCH_PROVIDER` (`stub|brave|tavily`) — SIEMPRE la config de
PLATAFORMA (`.env`/`Settings` de quien opera el servidor), nunca por tenant.
Se conserva por back-compat (self-host de un solo tenant que configura
`SEARCH_PROVIDER`/`BRAVE_API_KEY`/`TAVILY_API_KEY` en su propio `.env`,
scripts/tests propios de este paquete), pero desde la corrección de diseño de
`DIRECCION_ACTUAL.md` ("nunca una llave compartida de plataforma") NINGÚN
flujo de tenant la invoca — ver `get_tenant_search_provider` abajo. La tool
`buscar_web` resume los resultados con título + url + snippet.

`get_tenant_search_provider(ctx)` es la variante bring-your-own real (mismo
criterio que `docs/credenciales.md` para LLM/voz, `DIRECCION_ACTUAL.md` "Modelo
de credenciales: TODO lo trae el cliente, siempre"): si el tenant conectó su
propia key de búsqueda (`PUT /v1/credentials/search`,
`apps/api/edecan_api/routers/credentials.py`, `TokenVault` connector_key
`SEARCH_CONNECTOR_KEY`), la usa; si no —o si falla cualquier paso de esa
resolución— cae DIRECTO a `StubSearch()`, nunca a `get_search_provider(ctx.settings)`
ni a `BRAVE_API_KEY`/`TAVILY_API_KEY` de plataforma: "tenant → stub", el mismo
criterio de dos niveles (sin paso intermedio de plataforma) que ya sigue
`apps/api/edecan_api/routers/voice.py` (`_stt_para_tenant`/`_tts_para_tenant`)
para voz web. `BuscarWebTool`/`CompararPreciosTool` (`edecan_browser.tools`)
usan esta variante; `get_search_provider(settings)` en sí queda igual que
antes (back-compat total, pero ya NO es lo que usa este fallback).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from urllib.parse import quote_plus, urlsplit

import httpx
from edecan_core import Tool, ToolContext, ToolResult
from sqlalchemy import text as sql_text

from ._util import clamp_int

logger = logging.getLogger(__name__)

_TIMEOUT = 15.0
_K_DEFECTO = 5
_K_MAXIMO = 10

# `connector_key` del `TokenVault` para la credencial de búsqueda bring-your-own
# del tenant (ver docstring del módulo). Definido acá y, por separado, en
# `apps/api/edecan_api/routers/credentials.py` (el mismo string literal
# duplicado a propósito: `edecan_toolkit` no puede importar `edecan_api` —
# dependencia en sentido contrario — igual criterio que `LLM_CONNECTOR_KEY`,
# duplicado entre `apps/api/edecan_api/deps.py` y `apps/worker/edecan_worker/deps.py`).
SEARCH_CONNECTOR_KEY = "search"


@dataclass(frozen=True)
class SearchHit:
    """Un resultado de búsqueda web normalizado."""

    title: str
    url: str
    snippet: str


@runtime_checkable
class SearchProvider(Protocol):
    """Protocolo común de proveedor de búsqueda web."""

    async def search(self, query: str, k: int = 5) -> list[SearchHit]: ...


class BraveSearch:
    """Brave Search API — `GET https://api.search.brave.com/res/v1/web/search`
    con el header `X-Subscription-Token`.
    """

    _URL = "https://api.search.brave.com/res/v1/web/search"
    name = "brave"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def search(self, query: str, k: int = 5) -> list[SearchHit]:
        limite = max(1, min(k, _K_MAXIMO))
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            respuesta = await http.get(
                self._URL,
                params={"q": query, "count": limite},
                headers={"Accept": "application/json", "X-Subscription-Token": self._api_key},
            )
        respuesta.raise_for_status()
        resultados = respuesta.json().get("web", {}).get("results", [])
        return [
            SearchHit(
                title=r.get("title", ""), url=r.get("url", ""), snippet=r.get("description", "")
            )
            for r in resultados[:limite]
        ]


class TavilySearch:
    """Tavily Search API — `POST https://api.tavily.com/search`."""

    _URL = "https://api.tavily.com/search"
    name = "tavily"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def search(self, query: str, k: int = 5) -> list[SearchHit]:
        limite = max(1, min(k, _K_MAXIMO))
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            respuesta = await http.post(
                self._URL,
                json={"api_key": self._api_key, "query": query, "max_results": limite},
            )
        respuesta.raise_for_status()
        resultados = respuesta.json().get("results", [])
        return [
            SearchHit(title=r.get("title", ""), url=r.get("url", ""), snippet=r.get("content", ""))
            for r in resultados[:limite]
        ]


class StubSearch:
    """Proveedor determinista y offline — usado por defecto (`SEARCH_PROVIDER=stub`)
    en dev/self-host cuando no hay ninguna API key de búsqueda configurada.
    """

    name = "stub"

    async def search(self, query: str, k: int = 5) -> list[SearchHit]:
        limite = max(1, min(k, _K_MAXIMO))
        return [
            SearchHit(
                title=f"Resultado de ejemplo {i} para «{query}»",
                url=f"https://example.com/search?q={quote_plus(query)}&r={i}",
                snippet=(
                    "SEARCH_PROVIDER=stub no hace llamadas de red reales. Configura "
                    "BRAVE_API_KEY o TAVILY_API_KEY (ver .env.example) para búsquedas reales."
                ),
            )
            for i in range(1, limite + 1)
        ]


def get_search_provider(settings: Any) -> SearchProvider:
    """Construye el `SearchProvider` configurado en `settings.SEARCH_PROVIDER`
    (lectura defensiva vía `getattr`, igual que `edecan_llm.router.LLMRouter`,
    para no acoplar `edecan_toolkit` a una clase `Settings` concreta).
    """
    proveedor = str(getattr(settings, "SEARCH_PROVIDER", None) or "stub").strip().lower()
    if proveedor == "brave":
        api_key = getattr(settings, "BRAVE_API_KEY", None)
        if not api_key:
            raise RuntimeError("SEARCH_PROVIDER=brave pero falta BRAVE_API_KEY (ver .env.example).")
        return BraveSearch(api_key)
    if proveedor == "tavily":
        api_key = getattr(settings, "TAVILY_API_KEY", None)
        if not api_key:
            raise RuntimeError(
                "SEARCH_PROVIDER=tavily pero falta TAVILY_API_KEY (ver .env.example)."
            )
        return TavilySearch(api_key)
    if proveedor != "stub":
        logger.warning("SEARCH_PROVIDER=%r desconocido; usando 'stub'.", proveedor)
    return StubSearch()


def _stub_con_aviso(tenant_id: Any) -> StubSearch:
    """`StubSearch` + `logger.warning` accionable — se llama desde CADA rama
    de `get_tenant_search_provider` que no encontró una credencial de
    búsqueda del tenant utilizable (nunca conectó nada, cuenta a medio
    escribir, JSON corrupto, `provider` desconocido). JAMÁS consulta
    `BRAVE_API_KEY`/`TAVILY_API_KEY`/`SEARCH_PROVIDER` de plataforma."""
    logger.warning(
        "tenant_id=%s no tiene una credencial de búsqueda propia conectada (o no es "
        "utilizable); usando StubSearch. Conecta tu propia credencial en "
        "Configuración -> PUT /v1/credentials/search.",
        tenant_id,
    )
    return StubSearch()


async def get_tenant_search_provider(ctx: Any) -> SearchProvider:
    """`SearchProvider` bring-your-own del tenant — "tenant → stub", SIN paso
    intermedio de plataforma (ver docstring del módulo).

    Lee `ctx.tenant_id`/`ctx.session`/`ctx.vault` de forma defensiva (`ctx` es
    `edecan_core.tools.ToolContext` en producción, pero un `Any` a propósito
    — mismo criterio que `edecan_api.deps.load_tenant_llm_config`/
    `edecan_worker.deps.Deps._resolve_tenant_llm_router`, que resuelven la
    misma pregunta para LLM): si falta cualquiera de los tres, o el tenant
    nunca conectó `PUT /v1/credentials/search`, o CUALQUIER paso falla (vault
    caído, JSON corrupto, `provider` desconocido), se devuelve DIRECTO
    `StubSearch()` — nunca `get_search_provider(ctx.settings)` ni
    `BRAVE_API_KEY`/`TAVILY_API_KEY` de plataforma — con `logger.warning`
    indicando al tenant cómo conectar su propia credencial. Nunca revienta
    `buscar_web`/`comparar_precios` por esto.
    """
    tenant_id = getattr(ctx, "tenant_id", None)
    session = getattr(ctx, "session", None)
    vault = getattr(ctx, "vault", None)
    if tenant_id is None or session is None or vault is None:
        return _stub_con_aviso(tenant_id)

    try:
        row = (
            (
                await session.execute(
                    sql_text(
                        "SELECT id FROM connector_accounts WHERE tenant_id = :tenant_id "
                        "AND connector_key = :connector_key ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"tenant_id": tenant_id, "connector_key": SEARCH_CONNECTOR_KEY},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return _stub_con_aviso(tenant_id)

        bundle = await vault.get(tenant_id=tenant_id, connector_account_id=row["id"])
        if bundle is None:
            return _stub_con_aviso(tenant_id)

        data = json.loads(bundle.access_token)
        api_key = data.get("api_key")
        proveedor = str(data.get("provider") or "").strip().lower()
        if not api_key or proveedor not in ("brave", "tavily"):
            return _stub_con_aviso(tenant_id)
        return BraveSearch(api_key) if proveedor == "brave" else TavilySearch(api_key)
    except Exception:
        logger.warning(
            "No se pudo resolver el SearchProvider bring-your-own del tenant_id=%s (fallo "
            "leyendo su credencial); usando StubSearch, NUNCA la config de plataforma. "
            "Conecta tu propia credencial en Configuración -> PUT /v1/credentials/search.",
            tenant_id,
            exc_info=True,
        )
        return StubSearch()


class BuscarWebTool(Tool):
    name = "buscar_web"
    description = "Busca en la web y devuelve los resultados más relevantes (título, url, resumen)."
    input_schema = {
        "type": "object",
        "properties": {
            "consulta": {"type": "string", "description": "Qué buscar en la web."},
            "k": {
                "type": "integer",
                "description": "Cuántos resultados devolver (1-10).",
                "default": _K_DEFECTO,
            },
        },
        "required": ["consulta"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        consulta = str(args.get("consulta", "")).strip()
        if not consulta:
            return ToolResult(content="Dime qué quieres que busque en la web.")
        k = clamp_int(args.get("k"), default=_K_DEFECTO, minimo=1, maximo=_K_MAXIMO)

        proveedor = await get_tenant_search_provider(ctx)
        hits = await proveedor.search(consulta, k=k)

        if not hits:
            return ToolResult(
                content=f"No encontré resultados para «{consulta}».", data={"resultados": []}
            )

        lineas = [f"{i}. {h.title} — {h.url}\n   {h.snippet}" for i, h in enumerate(hits, start=1)]
        resultados = [{"title": h.title, "url": h.url, "snippet": h.snippet} for h in hits]
        provider_name = str(getattr(proveedor, "name", "") or "").lower()
        source_mode = (
            "demo"
            if provider_name == "stub"
            else "live"
            if provider_name in {"brave", "tavily"}
            else "unknown"
        )
        presentation = [
            {
                "type": "link_preview",
                "fallback_text": h.title or h.url,
                "url": h.url,
                "title": h.title or urlsplit(h.url).hostname or "Resultado web",
                "description": h.snippet or None,
                "site_name": urlsplit(h.url).hostname,
                "source_mode": source_mode,
                "actions": [
                    {
                        "id": f"search.open.{index}",
                        "label": "Abrir fuente",
                        "action": "open_url",
                        "url": h.url,
                    }
                ],
            }
            for index, h in enumerate(hits[:10])
        ]
        return ToolResult(
            content="\n".join(lineas),
            data={"resultados": resultados},
            presentation=presentation,
        )
