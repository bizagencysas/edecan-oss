"""Política de seguridad de navegación.

Esto implementa, en código, el guardrail «investigar sí, comprar jamás»
(`ROADMAP_V2.md` §6 categoría 6, §8 guardrails 1 y 4): `edecan_browser` SOLO
hace lecturas (`GET`) de páginas públicas para investigar/comparar precios —
jamás inicia un flujo de compra, pago o autenticación, jamás llega a una URL
de red privada o de metadata de nube (SSRF), y jamás navega ni extrae
contenido de LinkedIn (exclusión permanente de cumplimiento, `ARCHITECTURE.md`
§0.2 — ver punto 2 más abajo).

`check_navigation(url, settings)` es el portero: `edecan_browser.tools` lo
llama antes de cualquier fetch real sobre la URL pedida por el usuario/LLM, y
`edecan_browser.fetch.HttpxFetcher` lo vuelve a llamar sobre cada URL de
destino de un redirect antes de seguirlo — un `check_navigation` aprobado
sobre la URL original NO cubre a dónde puede redirigir esa URL (ver docstring
de `HttpxFetcher`), así que ambos puntos de entrada son necesarios. Encadena,
en orden barato→caro (para no gastar una llamada de red en una URL que de
todos modos se iba a rechazar):

1. Esquema permitido (`http`/`https` solamente).
2. Exclusión de cumplimiento por dominio: LinkedIn (`linkedin.com` y
   cualquier subdominio) está excluido permanentemente de Edecán
   (`ARCHITECTURE.md` §0.2, `docs/cumplimiento/tos-redes.md` sección
   "LinkedIn — excluido permanentemente") — a diferencia del resto de esta
   lista, esto no es un guardrail de seguridad general sino una decisión de
   producto/ToS, y por eso se evalúa por nombre de host exacto (nunca por
   substring sobre la URL completa, para no bloquear de más una URL ajena
   que solo mencione "linkedin" en el path/query).
3. Blocklist de rutas transaccionales (regex sobre la URL completa).
4. SSRF: IP literal o resolución DNS del host contra rangos privados,
   loopback, link-local, reservados o de metadata de nube.
5. `robots.txt` del origen (descargado con `httpx` — mockeable con `respx` —
   y parseado con `urllib.robotparser`; cacheado 10 minutos por origen).

Cualquier rechazo devuelve `PolicyResult(allowed=False, reason=...)` con un
mensaje listo para mostrarle al usuario — nunca lanza una excepción por una
URL "de negocio" inválida (mismo criterio que documenta `Tool.run` en
`edecan_core.tools.base`: los problemas de negocio se devuelven como
resultado, no como excepción).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

logger = logging.getLogger(__name__)

_ESQUEMAS_PERMITIDOS = frozenset({"http", "https"})

# Patrón EXACTO pedido por el work package (ROADMAP_V2.md §7.7): cualquiera
# de estas palabras en la URL (path o query) basta para rechazar — es
# deliberadamente amplio (más falsos positivos, cero falsos negativos) porque
# el costo de rechazar de más una URL de investigación es bajo, mientras que
# dejar pasar un checkout/login real violaría el guardrail permanente.
_RUTA_TRANSACCIONAL_RE = re.compile(
    r"checkout|cart|carrito|payment|pago|login|signin|account", re.IGNORECASE
)

_HOSTS_BLOQUEADOS_POR_NOMBRE = frozenset(
    {"localhost", "localhost.", "metadata.google.internal", "metadata.goog"}
)

# Dominios excluidos PERMANENTEMENTE por cumplimiento (no por SSRF/red): hoy
# solo LinkedIn. El *User Agreement* de LinkedIn prohíbe explícitamente bots o
# métodos automatizados para acceder al servicio o extraer ("scrape") perfiles
# e información (`docs/cumplimiento/tos-redes.md`), y la promesa del producto
# ("no puedes... leer nada ahí", `persona.py`) es absoluta — no solo para
# `packages/connectors/`. `edecan_browser` es un navegador de propósito
# general cuyas 3 tools reciben la URL como argumento en tiempo de ejecución
# (`edecan_browser.tools`), así que ni el guardrail estático de
# `ToolRegistry.register()` (que solo mira `tool.name`/`tool.description`) ni
# `test_no_linkedin` (que solo escanea `packages/connectors/`) cubren este
# camino — este es el único punto de enforcement en código para él.
_HOSTS_EXCLUIDOS_CUMPLIMIENTO = frozenset({"linkedin.com"})


def _host_excluido_por_cumplimiento(hostname: str) -> bool:
    """`True` si `hostname` es (o es subdominio de) un dominio de
    `_HOSTS_EXCLUIDOS_CUMPLIMIENTO` — mismo criterio de coincidencia
    (dominio exacto o `.dominio` al final) que `_dominio_permitido` en
    `edecan_connectors/social/tests/test_allowed_domains.py`.
    """
    return any(
        hostname == dominio or hostname.endswith(f".{dominio}")
        for dominio in _HOSTS_EXCLUIDOS_CUMPLIMIENTO
    )


_TTL_ROBOTS_SEGUNDOS = 10 * 60
_TIMEOUT_RESOLUCION_DNS_SEGUNDOS = 5.0
_USER_AGENT_DEFECTO = "EdecanBot/1.0"
_TIMEOUT_DEFECTO_SEGUNDOS = 20.0


@dataclass(frozen=True)
class PolicyResult:
    """`allowed=False` siempre trae `reason` con un mensaje explicable al usuario."""

    allowed: bool
    reason: str | None = None


async def resolve_hostname_ips(hostname: str) -> list[str]:
    """Resuelve `hostname` a direcciones IP vía el resolutor DNS del sistema.

    Aislada en su propia función de módulo (en vez de una llamada inline
    dentro de `check_navigation`) a propósito: los tests de SSRF por dominio
    la reemplazan con `monkeypatch.setattr(policy, "resolve_hostname_ips", ...)`
    para no depender de DNS real (`ARCHITECTURE.md` §10.15 / §0.5: "tests
    offline y deterministas"). Esto funciona porque Python resuelve nombres a
    nivel de módulo dinámicamente en cada llamada — el monkeypatch de una
    función módulo-nivel aplica también a las llamadas que hace
    `check_navigation` (definida en este mismo módulo) por nombre.
    """
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(hostname, None)
    return sorted({info[4][0] for info in infos})


def _ip_bloqueada(ip_str: str) -> bool:
    """`True` si `ip_str` no es una dirección pública/global de internet.

    Se comprueban varias propiedades en vez de confiar solo en `is_private`
    (que en algunas versiones de `ipaddress` no cubre 100% loopback/link-local
    para IPv6) — más conservador, defensa en profundidad para el guardrail SSRF.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # no parseable → por seguridad, se trata como bloqueada
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _es_ip_literal(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        return False


async def _hostname_bloqueado_por_red(hostname: str) -> bool:
    if hostname in _HOSTS_BLOQUEADOS_POR_NOMBRE or hostname.endswith(".localhost"):
        return True
    if _es_ip_literal(hostname):
        return _ip_bloqueada(hostname)
    try:
        ips = await asyncio.wait_for(
            resolve_hostname_ips(hostname), timeout=_TIMEOUT_RESOLUCION_DNS_SEGUNDOS
        )
    except Exception:
        # DNS caído/timeout/dominio inexistente: no podemos garantizar que no
        # apunte a una red privada, así que se bloquea por seguridad (fail
        # closed) — el propio fetch real habría fallado igual más adelante.
        logger.warning("No se pudo resolver '%s' por DNS; se bloquea por seguridad.", hostname)
        return True
    if not ips:
        return True
    return any(_ip_bloqueada(ip) for ip in ips)


class RobotsCache:
    """Cache en memoria de `robots.txt` por origen (`scheme://netloc`).

    TTL configurable (default `_TTL_ROBOTS_SEGUNDOS` = 10 min, pinned en
    ROADMAP_V2.md §7.7 "cacheado por dominio en memoria TTL 10min"). Una
    instancia módulo-nivel (`_CACHE_GLOBAL`, más abajo) sirve para producción;
    los tests construyen su propia instancia (o el fixture `_cache_robots_fresca`
    en `conftest.py` reemplaza `_CACHE_GLOBAL`) para no compartir estado —
    y por lo tanto resultados de `respx` — entre casos de test distintos.
    """

    def __init__(self, ttl_seconds: float = _TTL_ROBOTS_SEGUNDOS) -> None:
        self._ttl = ttl_seconds
        self._entradas: dict[str, tuple[float, RobotFileParser]] = {}

    async def permite(self, *, origin: str, url: str, user_agent: str, timeout: float) -> bool:
        parser = await self._parser_de(origin, user_agent, timeout)
        return parser.can_fetch(user_agent, url)

    async def _parser_de(self, origin: str, user_agent: str, timeout: float) -> RobotFileParser:
        ahora = time.monotonic()
        cacheado = self._entradas.get(origin)
        if cacheado is not None and (ahora - cacheado[0]) < self._ttl:
            return cacheado[1]
        parser = await self._descargar_y_parsear(origin, user_agent, timeout)
        self._entradas[origin] = (ahora, parser)
        return parser

    async def _descargar_y_parsear(
        self, origin: str, user_agent: str, timeout: float
    ) -> RobotFileParser:
        parser = RobotFileParser()
        parser.set_url(f"{origin}/robots.txt")
        try:
            async with httpx.AsyncClient(
                timeout=timeout, headers={"User-Agent": user_agent}
            ) as http:
                respuesta = await http.get(f"{origin}/robots.txt")
        except httpx.HTTPError:
            logger.info(
                "No se pudo descargar robots.txt de %s; se asume sin restricciones.", origin
            )
            parser.parse([])  # sin reglas descargadas = se permite todo
            return parser
        if respuesta.status_code >= 400:
            # Convención estándar de robots.txt: si no existe (404) u otro
            # error de cliente/servidor, se asume que el sitio no restringe nada.
            parser.parse([])
            return parser
        parser.parse(respuesta.text.splitlines())
        return parser


_CACHE_GLOBAL = RobotsCache()


def _valor(settings: Any, campo: str, default: Any) -> Any:
    valor = getattr(settings, campo, None) if settings is not None else None
    return valor if valor is not None else default


async def check_navigation(
    url: str,
    settings: Any = None,
    *,
    robots_cache: RobotsCache | None = None,
) -> PolicyResult:
    """Evalúa si `edecan_browser` puede hacer `GET url`. Ver docstring del módulo."""
    partes = urlsplit(url)
    if partes.scheme.lower() not in _ESQUEMAS_PERMITIDOS or not partes.hostname:
        return PolicyResult(
            False, f"«{url}» no es una URL http/https válida — Edecán solo navega http(s)."
        )

    hostname = partes.hostname.lower()
    if _host_excluido_por_cumplimiento(hostname):
        return PolicyResult(
            False,
            "LinkedIn está excluido permanentemente de Edecán: no navego, extraigo datos ni "
            "comparo precios en linkedin.com bajo ninguna forma (ver "
            "docs/cumplimiento/tos-redes.md).",
        )

    if _RUTA_TRANSACCIONAL_RE.search(url):
        return PolicyResult(
            False,
            "Esa URL parece un flujo de compra, carrito, pago o inicio de sesión. Edecán "
            "investiga y compara precios, pero NUNCA navega checkouts, pagos ni formularios "
            "de login — la decisión y la acción son siempre tuyas.",
        )

    if await _hostname_bloqueado_por_red(hostname):
        return PolicyResult(
            False,
            f"«{hostname}» resuelve a una dirección de red privada, local o de metadata de "
            "nube — bloqueado por seguridad (protección SSRF).",
        )

    user_agent = str(_valor(settings, "BROWSER_USER_AGENT", _USER_AGENT_DEFECTO))
    timeout = float(_valor(settings, "BROWSER_TIMEOUT_SECONDS", _TIMEOUT_DEFECTO_SEGUNDOS))
    origin = f"{partes.scheme}://{partes.netloc}"
    cache = robots_cache if robots_cache is not None else _CACHE_GLOBAL
    permitido = await cache.permite(origin=origin, url=url, user_agent=user_agent, timeout=timeout)
    if not permitido:
        return PolicyResult(False, f"El robots.txt de {partes.netloc} no permite navegar esa ruta.")

    return PolicyResult(True)
