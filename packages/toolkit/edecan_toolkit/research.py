"""Investigación web (`ARCHITECTURE.md` §10.14, §10.2).

`SearchProvider` es el protocolo que implementan `DuckDuckGoSearch`,
`BraveSearch`, `TavilySearch` y `StubSearch`; `get_search_provider(settings)`
elige la implementación según `settings.SEARCH_PROVIDER`
(`stub|duckduckgo|brave|tavily`) — SIEMPRE la config de
PLATAFORMA (`.env`/`Settings` de quien opera el servidor), nunca por tenant.
Se conserva por back-compat (self-host de un solo tenant que configura
`SEARCH_PROVIDER`/`BRAVE_API_KEY`/`TAVILY_API_KEY` en su propio `.env`,
scripts/tests propios de este paquete), pero desde la corrección de diseño de
`DIRECCION_ACTUAL.md` ("nunca una llave compartida de plataforma") NINGÚN
flujo de tenant la invoca — ver `get_tenant_search_provider` abajo. La tool
`buscar_web` resume los resultados con título + url + snippet.

`get_tenant_search_provider(ctx)` es la variante de producción: si el tenant
conectó su propia key de búsqueda (`PUT /v1/credentials/search`,
`apps/api/edecan_api/routers/credentials.py`, `TokenVault` connector_key
`SEARCH_CONNECTOR_KEY`), la usa; si no —o si falla esa resolución— usa
`DuckDuckGoSearch`, un buscador web real sin API key. Nunca cae a una clave
de plataforma. Así internet es una capacidad de Edecán y no depende de que
el modelo sea Claude, Codex, Ollama, Qwen, Kimi u otro.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import socket
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any, Protocol, runtime_checkable
from urllib.parse import parse_qs, quote_plus, urlsplit

import httpx
from edecan_core import Tool, ToolContext, ToolResult
from edecan_core.deep_research import is_local_search_question
from edecan_core.web_security import scan_for_injection
from sqlalchemy import text as sql_text

from ._util import clamp_int

logger = logging.getLogger(__name__)

_TIMEOUT = 15.0
_RESEARCH_CACHE_TTL_SECONDS = 15 * 60
_research_session_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_K_DEFECTO = 5
_K_MAXIMO = 10
_MAX_PRESENTATION_CARDS = 3

_DIRECT_LINK_WORDS = {
    "link",
    "enlace",
    "url",
}
_DIRECT_LINK_PHRASES = (
    "sitio oficial",
    "web oficial",
    "pagina oficial",
    "página oficial",
    "google maps",
)
_GENERIC_CARD_TITLES = {
    "google maps",
    "maps",
    "resultado web",
    "sin titulo",
    "sin título",
    "untitled",
}


async def _check_source_availability(url: str) -> dict[str, Any]:
    """Comprueba una fuente pública sin seguir redirects ni tocar redes privadas."""
    partes = urlsplit(url)
    host = partes.hostname or ""
    if partes.scheme not in {"http", "https"} or not host:
        return {"status": "blocked", "reason": "invalid_url"}
    try:
        literal = ipaddress.ip_address(host)
        addresses = [literal]
    except ValueError:
        if host.casefold() in {"localhost", "localhost.localdomain"}:
            return {"status": "blocked", "reason": "private_host"}
        try:
            infos = await asyncio.to_thread(socket.getaddrinfo, host, 443, type=socket.SOCK_STREAM)
            addresses = [ipaddress.ip_address(info[4][0]) for info in infos]
        except (OSError, ValueError):
            return {"status": "unavailable", "reason": "dns_failed"}
    if not addresses or any(not address.is_global for address in addresses):
        return {"status": "blocked", "reason": "private_or_non_global_host"}
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as http:
            response = await http.head(url, headers={"User-Agent": "Edecan/0.7"})
        return {
            "status": "reachable" if response.status_code < 400 else "unavailable",
            "http_status": response.status_code,
        }
    except httpx.HTTPError:
        return {"status": "unavailable", "reason": "request_failed"}


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


def _consulta_pide_enlace_directo(consulta: str) -> bool:
    """Los pedidos navegacionales necesitan la URL, no una parrilla de fuentes.

    El contenido completo de la búsqueda sigue disponible para que el modelo
    responda con el enlace correcto. Solo se evita convertir cada resultado en
    una tarjeta visual redundante.
    """

    normalizada = " ".join(consulta.casefold().split())
    palabras = set(re.findall(r"[\wáéíóúüñ]+", normalizada))
    return bool(palabras & _DIRECT_LINK_WORDS) or any(
        frase in normalizada for frase in _DIRECT_LINK_PHRASES
    )


def _hits_para_presentacion(consulta: str, hits: list[SearchHit]) -> list[SearchHit]:
    """Selecciona pocas fuentes visuales, útiles, completas y no duplicadas."""

    if _consulta_pide_enlace_directo(consulta):
        return []

    seleccionados: list[SearchHit] = []
    vistos: set[str] = set()
    for hit in hits:
        titulo = " ".join(hit.title.split())
        resumen = " ".join(hit.snippet.split())
        partes = urlsplit(hit.url)
        host = (partes.hostname or "").casefold()
        if partes.scheme not in {"http", "https"} or not host:
            continue
        if not titulo or not resumen:
            continue
        if titulo.casefold() in _GENERIC_CARD_TITLES or titulo.casefold() == host:
            continue

        clave = f"{host}{partes.path.rstrip('/').casefold()}"
        if clave in vistos:
            continue
        vistos.add(clave)
        seleccionados.append(SearchHit(title=titulo, url=hit.url, snippet=resumen))
        if len(seleccionados) >= _MAX_PRESENTATION_CARDS:
            break
    return seleccionados


@runtime_checkable
class SearchProvider(Protocol):
    """Protocolo común de proveedor de búsqueda web."""

    async def search(self, query: str, k: int = 5) -> list[SearchHit]: ...


class _DuckDuckGoHTMLParser(HTMLParser):
    """Extrae enlaces y resúmenes del frontend HTML accesible."""

    def __init__(self, limite: int) -> None:
        super().__init__(convert_charrefs=True)
        self._limite = limite
        self._capturando: str | None = None
        self._href_actual = ""
        self._texto_actual: list[str] = []
        self._pendiente: SearchHit | None = None
        self.resultados: list[SearchHit] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in {"a", "div"}:
            return
        atributos = dict(attrs)
        clases = set(str(atributos.get("class") or "").split())
        if tag == "a" and "result__a" in clases:
            self._guardar_pendiente()
            self._capturando = "title"
            self._href_actual = str(atributos.get("href") or "")
            self._texto_actual = []
        elif "result__snippet" in clases and self._pendiente is not None:
            self._capturando = "snippet"
            self._texto_actual = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capturando == "title":
            titulo = " ".join("".join(self._texto_actual).split())
            url = _normalizar_url_duckduckgo(self._href_actual)
            self._pendiente = SearchHit(title=titulo, url=url, snippet="") if url else None
            self._capturando = None
            self._texto_actual = []
        elif tag in {"a", "div"} and self._capturando == "snippet":
            snippet = " ".join("".join(self._texto_actual).split())
            if self._pendiente is not None:
                self._pendiente = SearchHit(
                    title=self._pendiente.title,
                    url=self._pendiente.url,
                    snippet=snippet,
                )
            self._capturando = None
            self._texto_actual = []

    def handle_data(self, data: str) -> None:
        if self._capturando is not None:
            self._texto_actual.append(data)

    def close(self) -> None:
        super().close()
        self._guardar_pendiente()

    def _guardar_pendiente(self) -> None:
        if self._pendiente is not None and len(self.resultados) < self._limite:
            self.resultados.append(self._pendiente)
        self._pendiente = None


def _normalizar_url_duckduckgo(raw: str) -> str:
    url = raw.strip()
    if url.startswith("//"):
        url = f"https:{url}"
    partes = urlsplit(url)
    if partes.hostname in {"duckduckgo.com", "www.duckduckgo.com"} and partes.path == "/l/":
        destino = (parse_qs(partes.query).get("uddg") or [""])[0]
        if destino:
            url = destino
            partes = urlsplit(url)
    return url if partes.scheme in {"http", "https"} and partes.hostname else ""


class DuckDuckGoSearch:
    """Búsqueda web real, sin credenciales y sin depender del proveedor LLM."""

    _URL = "https://html.duckduckgo.com/html/"
    name = "duckduckgo"

    async def search(self, query: str, k: int = 5) -> list[SearchHit]:
        consulta = str(query or "").strip()
        if not consulta:
            return []
        limite = max(1, min(k, _K_MAXIMO))
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as http:
            respuesta = await http.get(
                self._URL,
                params={"q": consulta},
                headers={
                    "Accept": "text/html",
                    "User-Agent": "Edecan/0.7 (+https://github.com/your-org/edecan-oss)",
                },
            )
        respuesta.raise_for_status()
        parser = _DuckDuckGoHTMLParser(limite)
        parser.feed(respuesta.text)
        parser.close()
        return parser.resultados[:limite]


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
    if proveedor == "duckduckgo":
        return DuckDuckGoSearch()
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


def _busqueda_sin_clave(tenant_id: Any) -> DuckDuckGoSearch:
    """Fallback real sin secretos compartidos y sin dependencia del LLM."""
    logger.info(
        "tenant_id=%s no tiene búsqueda dedicada utilizable; usando DuckDuckGoSearch sin API key.",
        tenant_id,
    )
    return DuckDuckGoSearch()


async def get_tenant_search_provider(ctx: Any) -> SearchProvider:
    """Proveedor dedicado del tenant o búsqueda real sin clave.

    Lee `ctx.tenant_id`/`ctx.session`/`ctx.vault` de forma defensiva (`ctx` es
    `edecan_core.tools.ToolContext` en producción, pero un `Any` a propósito
    — mismo criterio que `edecan_api.deps.load_tenant_llm_config`/
    `edecan_worker.deps.Deps._resolve_tenant_llm_router`, que resuelven la
    misma pregunta para LLM): si falta cualquiera de los tres, no hay cuenta
    dedicada, o el vault falla, devuelve `DuckDuckGoSearch()`. Nunca consulta
    claves de plataforma y nunca inspecciona qué modelo de IA está conectado.
    """
    tenant_id = getattr(ctx, "tenant_id", None)
    session = getattr(ctx, "session", None)
    vault = getattr(ctx, "vault", None)
    if tenant_id is None or session is None or vault is None:
        return _busqueda_sin_clave(tenant_id)

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
            return _busqueda_sin_clave(tenant_id)

        bundle = await vault.get(tenant_id=tenant_id, connector_account_id=row["id"])
        if bundle is None:
            return _busqueda_sin_clave(tenant_id)

        data = json.loads(bundle.access_token)
        api_key = data.get("api_key")
        proveedor = str(data.get("provider") or "").strip().lower()
        if not api_key or proveedor not in ("brave", "tavily"):
            return _busqueda_sin_clave(tenant_id)
        return BraveSearch(api_key) if proveedor == "brave" else TavilySearch(api_key)
    except Exception:
        logger.warning(
            "No se pudo resolver la búsqueda dedicada del tenant_id=%s; usando "
            "DuckDuckGoSearch sin API key, nunca la config de plataforma.",
            tenant_id,
            exc_info=True,
        )
        return DuckDuckGoSearch()


class BuscarWebTool(Tool):
    name = "buscar_web"
    description = "Busca en la web y devuelve los resultados más relevantes (título, url, resumen)."
    category = "research"
    risk_level = "none"
    latency_class = "interactive"
    cost_class = "free"
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
            # Antes esto devolvía "Dime qué quieres que busque en la web." como
            # `ToolResult` normal (sin `is_error`): un texto con forma de
            # respuesta que un modelo puede leer como "ya contesté" en vez de
            # "la llamada falló", y terminar reintentando `buscar_web` con
            # `consulta` vacía varias veces en el mismo turno en vez de
            # pedirle al dueño qué buscar. `is_error=True` + un mensaje que
            # nombra el argumento faltante y qué hacer es lo que un modelo
            # puede leer como error y corregir, con o sin soporte de
            # `is_error` en el proveedor de LLM (ver `ToolResult.is_error`).
            return ToolResult(
                content=(
                    "Error: falta el argumento obligatorio 'consulta' (vino vacío). No "
                    "vuelvas a llamar a 'buscar_web' sin especificar qué buscar — pregúntale "
                    "al dueño qué quiere que busques, o usa una consulta concreta si ya la "
                    "conoces por el contexto de la conversación."
                ),
                is_error=True,
            )
        k = clamp_int(args.get("k"), default=_K_DEFECTO, minimo=1, maximo=_K_MAXIMO)
        cache_key = f"{consulta.casefold()}::{k}"
        cached = _research_session_cache.get(cache_key)
        if cached is not None:
            stored_at, payload = cached
            if time.monotonic() - stored_at < _RESEARCH_CACHE_TTL_SECONDS:
                return ToolResult(
                    content=str(payload.get("content") or ""),
                    data={**(payload.get("data") or {}), "cache_hit": True},
                    presentation=payload.get("presentation") or [],
                    citations=payload.get("citations") or [],
                )

        proveedor = await get_tenant_search_provider(ctx)
        local_queries = (
            [
                consulta,
                f"{consulta} horarios precio disponibilidad",
                f"{consulta} reseñas alternativas ubicación",
                f"{consulta} cómo llegar mapa direcciones",
            ]
            if is_local_search_question(consulta)
            else [consulta]
        )
        search_results = await asyncio.gather(
            *(proveedor.search(query, k=k) for query in local_queries),
            return_exceptions=True,
        )
        hits: list[SearchHit] = []
        seen_urls: set[str] = set()
        for result in search_results:
            if isinstance(result, Exception):
                logger.warning("buscar_web: falló una consulta complementaria: %s", result)
                continue
            for hit in result:
                if hit.url not in seen_urls:
                    seen_urls.add(hit.url)
                    hits.append(hit)
                if len(hits) >= _K_MAXIMO:
                    break
            if len(hits) >= _K_MAXIMO:
                break

        if not hits:
            return ToolResult(
                content=f"No encontré resultados para «{consulta}».", data={"resultados": []}
            )

        lineas = [f"{i}. {h.title} — {h.url}\n   {h.snippet}" for i, h in enumerate(hits, start=1)]
        resultados = [{"title": h.title, "url": h.url, "snippet": h.snippet} for h in hits]
        for h in hits:
            scan = scan_for_injection(h.snippet)
            if scan.is_suspicious:
                logger.warning(
                    "buscar_web: snippet from %s triggered injection scan: %s",
                    h.url,
                    scan.patterns_found,
                )
        provider_name = str(getattr(proveedor, "name", "") or "").lower()
        source_mode = (
            "demo"
            if provider_name == "stub"
            else "live"
            if provider_name in {"brave", "tavily", "duckduckgo"}
            else "unknown"
        )
        retrieved_at = datetime.now(UTC).isoformat()
        presentation_hits = _hits_para_presentacion(consulta, hits)
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
            for index, h in enumerate(presentation_hits)
        ]
        citations = [
            {
                "id": f"src-{i}",
                "title": h.title or urlsplit(h.url).hostname or "Resultado web",
                "url": h.url,
                "source": urlsplit(h.url).hostname or "",
                "excerpt": h.snippet or None,
                "retrieved_at": retrieved_at,
            }
            for i, h in enumerate(hits[:10])
        ]
        data = {
            "resultados": resultados,
            "search_provenance": {
                "provider": provider_name or "unknown",
                "mode": source_mode,
                "retrieved_at": retrieved_at,
                "queries": local_queries,
            },
        }
        if len(local_queries) > 1:
            source_domains = sorted(
                {
                    hostname
                    for h in hits
                    if h.url
                    for hostname in [urlsplit(h.url).hostname]
                    if hostname
                }
            )
            data["local_search"] = {
                "compared_queries": local_queries,
                "distinct_sources": len(source_domains),
                "source_domains": source_domains,
                "freshness_checks": [
                    "horarios",
                    "precio",
                    "disponibilidad",
                    "reseñas",
                    "alternativas",
                    "cómo_llegar",
                ],
                "comparison_complete": len(local_queries) == 4,
            }
            if source_mode == "live":
                checks = await asyncio.gather(
                    *(_check_source_availability(hit.url) for hit in hits[:5])
                )
                data["local_search"]["availability_checks"] = [
                    {"url": hit.url, **check} for hit, check in zip(hits[:5], checks, strict=True)
                ]
                data["local_search"]["availability_checks_complete"] = True
        result = ToolResult(
            content="\n".join(lineas),
            data=data,
            presentation=presentation,
            citations=citations,
        )
        _research_session_cache[cache_key] = (
            time.monotonic(),
            {
                "content": result.content,
                "data": data,
                "presentation": presentation,
                "citations": citations,
            },
        )
        if len(_research_session_cache) > 64:
            oldest = min(_research_session_cache, key=lambda key: _research_session_cache[key][0])
            _research_session_cache.pop(oldest, None)
        return result
