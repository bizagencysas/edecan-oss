"""`OpenClawSource`/`HermesSource` — dos índices adicionales de "Agent Skills", además del
de skills.sh (`edecan_skills.client.SkillsIndexClient`) — `ARCHITECTURE.md` §10.7,
`DIRECCION_ACTUAL.md` ("Confirmado: agregar Ollama... integrar el marketplace de
skills.sh", que menciona OpenClaw ~13,700 skills y Hermes Agent ~150 como las fuentes que
skills.sh también agrega).

Adaptación de `openjarvis.skills.sources.openclaw.OpenClawResolver` y
`openjarvis.skills.sources.hermes.HermesResolver` (Apache-2.0, ver `docs/skills.md`) — con
una diferencia deliberada y obligatoria: el original clona el repo índice completo con
`git clone`/`git pull` a un caché local en disco (`subprocess.run(["git", ...])`). Este
paquete tiene PROHIBIDO acoplarse a `git` (regla dura del entorno) y, de todos modos, un
`git clone` persistente no encaja con el modelo multi-tenant de Edecán (¿de quién sería ese
caché en disco? ¿cómo se invalida?). En su lugar: cada búsqueda descarga el tarball oficial
de GitHub (`codeload.github.com`, streaming + cap de tamaño — mismo patrón que
`edecan_skills.installer.fetch_skill`, cliente `httpx.AsyncClient` inyectable) y lo recorre
EN MEMORIA con `tarfile`, sin tocar el filesystem ni mantener ningún estado entre llamadas.

Este módulo SOLO implementa descubrimiento (`search`) — instalar una skill encontrada acá
sigue pasando por el pipeline ya existente y ya probado
(`edecan_skills.installer.install_from_source`, que resuelve contra
`raw.githubusercontent.com`): cada `SkillHit.source` que devuelve `search()` ya viene
formado como el `"owner/repo/subpath"` correcto para que ese pipeline lo resuelva sin
ningún cambio — ver `_TarballSkillSource._listar_desde_tarball`.
"""

from __future__ import annotations

import asyncio
import io
import ipaddress
import logging
import tarfile
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
import yaml

from .client import SkillHit
from .installer import FuenteInvalidaError, SkillDemasiadoGrandeError, SkillNoEncontradaError

logger = logging.getLogger(__name__)

_CODELOAD_BASE = "https://codeload.github.com"

# Cap propio para el tarball del ÍNDICE completo — deliberadamente mucho más generoso que
# `installer._MAX_BYTES` (200_000, el cap de UN SOLO `SKILL.md`): un índice como OpenClaw
# trae miles de skills en un solo archivo. Misma EXCEPCIÓN (`SkillDemasiadoGrandeError`) que
# `fetch_skill`, para que el llamador HTTP la mapee igual a 413 — se reutiliza el TIPO de
# error, no el número.
_MAX_TARBALL_BYTES = 50_000_000

# Cap de bytes leídos por miembro del tar al extraer su preview (nombre/descripción del
# frontmatter) — nunca hace falta leer un `SKILL.md` completo para eso, y así un miembro
# individual enorme dentro de un tarball por lo demás válido no puede inflar memoria.
_PREVIEW_READ_BYTES = 20_000

_K_DEFECTO = 10


def _preview(texto: str, *, default_name: str) -> tuple[str, str]:
    """`(nombre, descripcion)` del frontmatter de un `SKILL.md` — réplica local minimalista
    de `installer._split_frontmatter` (sin sanitizar caracteres de control: esto es solo
    una vista previa de búsqueda que nunca se persiste tal cual — instalar de verdad pasa
    por `install_from_source`, que sí sanitiza el contenido real antes de guardarlo)."""
    if not texto.startswith("---"):
        return default_name, ""
    resto = texto[3:]
    if resto.startswith("\n"):
        resto = resto[1:]
    fin = resto.find("\n---")
    if fin == -1:
        return default_name, ""
    try:
        frontmatter = yaml.safe_load(resto[:fin])
    except yaml.YAMLError:
        return default_name, ""
    if not isinstance(frontmatter, dict):
        return default_name, ""
    nombre = str(frontmatter.get("name") or "").strip() or default_name
    descripcion = str(frontmatter.get("description") or "").strip()
    return nombre, descripcion


class _TarballSkillSource:
    """Base compartida por `OpenClawSource`/`HermesSource`: descarga el tarball del repo
    índice y camina `skills/<segmento>/<nombre>/SKILL.md` dentro de él — el layout de dos
    niveles bajo `skills/` es idéntico en OpenClaw (`skills/<owner>/<nombre>/SKILL.md`) y
    Hermes (`skills/<categoria>/<nombre>/SKILL.md`, ver
    `openjarvis.skills.sources.{openclaw,hermes}`); la subclase solo fija `name`/`_owner`/
    `_repo`.
    """

    name: str = ""
    _owner: str = ""
    _repo: str = ""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    def _tarball_url(self) -> str:
        return f"{_CODELOAD_BASE}/{self._owner}/{self._repo}/tar.gz/HEAD"

    async def _descargar_tarball(self) -> bytes | None:
        """`None` ante cualquier fallo de red o status inesperado (best-effort, mismo
        criterio que `SkillsIndexClient.search` — un índice adicional caído nunca debe
        tumbar `buscar_skills`, ver su docstring). Lanza `SkillDemasiadoGrandeError` si el
        tarball supera `_MAX_TARBALL_BYTES` — en streaming, corta apenas se pasa, sin
        esperar a bajarlo completo."""
        url = self._tarball_url()
        try:
            async with self._http.stream("GET", url) as respuesta:
                if respuesta.status_code != 200:
                    logger.warning(
                        "GET %s (índice %s) devolvió %d (inesperado)",
                        url,
                        self.name,
                        respuesta.status_code,
                    )
                    return None
                crudo = bytearray()
                async for trozo in respuesta.aiter_bytes():
                    crudo.extend(trozo)
                    if len(crudo) > _MAX_TARBALL_BYTES:
                        raise SkillDemasiadoGrandeError(
                            f"El índice de {self.name} ({self._owner}/{self._repo}) supera "
                            f"el límite de {_MAX_TARBALL_BYTES} bytes."
                        )
                return bytes(crudo)
        except httpx.HTTPError as exc:
            logger.warning("Fallo de red descargando el tarball de %s: %s", self.name, exc)
            return None

    def _listar_desde_tarball(self, crudo: bytes) -> list[SkillHit]:
        """Recorre el tarball ya descargado y construye un `SkillHit` por cada
        `SKILL.md` encontrado en el layout de dos niveles esperado. `source` queda formado
        como `"{owner}/{repo}/skills/{segmento}/{nombre}"` — el `owner/repo/subpath` que
        `edecan_skills.installer.install_from_source` resuelve sin cambios (candidato #1 de
        `fetch_skill`: `{subpath}/SKILL.md`)."""
        resultados: list[SkillHit] = []
        try:
            with tarfile.open(fileobj=io.BytesIO(crudo), mode="r:gz") as tar:
                for miembro in tar.getmembers():
                    if not miembro.isfile():
                        continue
                    # <raíz-del-tar>/skills/<segmento>/<nombre>/SKILL.md — GitHub antepone
                    # siempre "<repo>-<ref>/" como raíz, así que la raíz misma se ignora
                    # (solo importa que haya EXACTAMENTE 5 segmentos con esta forma).
                    partes = miembro.name.split("/")
                    if len(partes) != 5 or partes[1] != "skills" or partes[4] != "SKILL.md":
                        continue
                    segmento, nombre_dir = partes[2], partes[3]

                    extraido = tar.extractfile(miembro)
                    if extraido is None:
                        continue
                    texto = extraido.read(_PREVIEW_READ_BYTES).decode("utf-8", errors="replace")
                    nombre, descripcion = _preview(texto, default_name=nombre_dir)

                    resultados.append(
                        SkillHit(
                            nombre=nombre,
                            source=f"{self._owner}/{self._repo}/skills/{segmento}/{nombre_dir}",
                            descripcion=descripcion,
                            installs=None,
                        )
                    )
        except tarfile.TarError as exc:
            logger.warning("Tarball de %s no es un .tar.gz válido: %s", self.name, exc)
            return []
        return resultados

    async def search(self, query: str, k: int = _K_DEFECTO) -> list[SkillHit]:
        """Descarga el tarball, lista todas las skills del índice y filtra por substring en
        nombre/descripción — mismo espíritu que `SourceResolver.resolve` de OpenJarvis
        (`sources/base.py`), adaptado a HTTP en vez de un caché de `git clone` en disco.
        `query` vacío devuelve el índice completo (capado a `k`). `[]` ante cualquier fallo
        (tarball caído, demasiado grande vía `SkillDemasiadoGrandeError` propagada, o con
        un formato inesperado) — un índice adicional caído nunca debe tumbar
        `buscar_skills`."""
        crudo = await self._descargar_tarball()
        if crudo is None:
            return []

        todas = self._listar_desde_tarball(crudo)
        consulta = (query or "").strip().lower()
        if consulta:
            todas = [
                hit
                for hit in todas
                if consulta in hit.nombre.lower() or consulta in hit.descripcion.lower()
            ]
        return todas[: max(1, k)]


class OpenClawSource(_TarballSkillSource):
    """Índice OpenClaw (`github.com/openclaw/skills`, ~13,700 skills): layout
    `skills/<owner>/<nombre>/SKILL.md` — adaptado de
    `openjarvis.skills.sources.openclaw.OpenClawResolver` (Apache-2.0)."""

    name = "openclaw"
    _owner = "openclaw"
    _repo = "skills"


class HermesSource(_TarballSkillSource):
    """Índice Hermes Agent (`github.com/NousResearch/hermes-agent`, ~150 skills): layout
    `skills/<categoria>/<nombre>/SKILL.md` — adaptado de
    `openjarvis.skills.sources.hermes.HermesResolver` (Apache-2.0)."""

    name = "hermes"
    _owner = "NousResearch"
    _repo = "hermes-agent"


# ---------------------------------------------------------------------------
# UrlSkillSource — instalar pegando la URL http(s) exacta de un SKILL.md
# ---------------------------------------------------------------------------
#
# A diferencia de OpenClawSource/HermesSource (arriba), acá no hay ningún índice ni
# tarball: el usuario pega DIRECTO la URL de un SKILL.md (o un archivo de skill
# equivalente) alojado en cualquier host http(s) — no solo github.com/skills.sh (los
# únicos que acepta `edecan_skills.installer.parse_source`). Esa libertad es justamente
# el riesgo: `installer.fetch_skill` NUNCA arma una petición con un host que haya pegado
# el usuario (ver su docstring, "anti path-traversal / anti-SSRF, a propósito") — acá SÍ
# hace falta pedir exactamente el host que el usuario dio, así que el guardrail anti-SSRF
# tiene que vivir en el propio pedido, no en evitar hacerlo.
#
# Mismo criterio de qué es "red privada/reservada" que `httpURLSegura` en
# `apps/mobile/ios/EdecanKit/Sources/EdecanKit/Models.swift` y `publicHttpUrl` en
# `apps/web/src/lib/chat-blocks.ts` (localhost/.local/.internal, 127.x, 10.x, 192.168.x,
# 172.16-31.x, 169.254.x, IPv6 privadas) — pero con una diferencia deliberada: esos dos
# viven en el cliente y solo deciden si mostrarle al usuario un botón "abrir enlace",
# nunca hacen la petición ellos mismos. Acá SÍ se hace un `GET` real desde el servidor de
# Edecán, así que un hostname público que RESUELVA por DNS a una IP privada (DNS
# rebinding) es un vector real y mirar el string del host no alcanza: además de las
# comparaciones literales, se resuelve DNS y se valida cada IP resultante — mismo
# mecanismo que ya usa `edecan_browser.policy.check_navigation` para el navegador de
# Edecán, reimplementado acá (en vez de importado) para no volver a `edecan_skills`
# dependiente de `edecan_browser`: son paquetes hermanos independientes y
# `packages/skills/pyproject.toml` no declara esa dependencia.


_ESQUEMAS_URL_PERMITIDOS = frozenset({"http", "https"})

# Comparación literal exacta (sin DNS) — atajo barato antes de resolver.
_HOSTS_BLOQUEADOS_LITERAL = frozenset({"localhost"})
_SUFIJOS_HOST_BLOQUEADOS = (".localhost", ".local", ".internal")

_TIMEOUT_RESOLUCION_DNS_SEGUNDOS = 5.0

# Cap del `SKILL.md` (o equivalente) pedido por URL directa — UN solo archivo, así que
# usa el mismo tope que `installer._MAX_BYTES` (200_000), no el de un índice completo
# (`_MAX_TARBALL_BYTES`, arriba, pensado para miles de skills en un solo tarball).
_MAX_URL_SKILL_BYTES = 200_000

# Mismo tope que `edecan_browser.fetch.HttpxFetcher._MAX_REDIRECTS` (5): cada salto se
# revalida a mano ANTES de pedirse (ver `UrlSkillSource._descargar`).
_MAX_REDIRECTS_URL = 5


async def resolve_hostname_ips(hostname: str) -> list[str]:
    """Resuelve `hostname` por el resolutor DNS del sistema.

    Aislada en su propia función de módulo (en vez de una llamada inline) a propósito:
    los tests de SSRF por dominio la reemplazan con
    `monkeypatch.setattr(sources, "resolve_hostname_ips", ...)` para no depender de DNS
    real — mismo patrón, por el mismo motivo, que
    `edecan_browser.policy.resolve_hostname_ips` (ver su docstring)."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(hostname, None)
    return sorted({info[4][0] for info in infos})


def _es_ip_literal(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        return False


def _ip_bloqueada(ip_str: str) -> bool:
    """`True` si `ip_str` no es una dirección pública de internet.

    Se comprueban varias propiedades en vez de confiar solo en `is_private` — más
    conservador, defensa en profundidad para el guardrail SSRF (mismo criterio que
    `edecan_browser.policy._ip_bloqueada`)."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # no parseable -> por seguridad, se trata como bloqueada
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


async def _host_bloqueado_por_red(hostname: str) -> bool:
    """`True` si `hostname` (ya en minúsculas) es, o resuelve por DNS a, una dirección de
    red privada/local/reservada. Si no es una IP literal, SIEMPRE se resuelve por DNS —
    nunca se confía en que "no parece" una IP privada: `except Exception` (DNS caído,
    timeout, dominio inexistente) bloquea por seguridad (fail closed), igual que
    `edecan_browser.policy._hostname_bloqueado_por_red` — el propio `GET` real habría
    fallado igual más adelante si el DNS no respondiera."""
    if hostname in _HOSTS_BLOQUEADOS_LITERAL or any(
        hostname.endswith(sufijo) for sufijo in _SUFIJOS_HOST_BLOQUEADOS
    ):
        return True
    if _es_ip_literal(hostname):
        return _ip_bloqueada(hostname)
    try:
        ips = await asyncio.wait_for(
            resolve_hostname_ips(hostname), timeout=_TIMEOUT_RESOLUCION_DNS_SEGUNDOS
        )
    except Exception:
        logger.warning("No se pudo resolver '%s' por DNS; se bloquea por seguridad.", hostname)
        return True
    if not ips:
        return True
    return any(_ip_bloqueada(ip) for ip in ips)


async def _validar_url_segura(url: str) -> None:
    """Guardrail anti-SSRF completo sobre `url`: esquema http(s), sin usuario/contraseña
    embebidos, host presente y NO privado/local/reservado (literal o resuelto por DNS).
    Lanza `FuenteInvalidaError` (mismo tipo que usa `installer.parse_source` para
    rechazos de fuente) ante cualquier fallo — nunca deja pasar en silencio."""
    partes = urlsplit(url)
    if partes.scheme.lower() not in _ESQUEMAS_URL_PERMITIDOS:
        raise FuenteInvalidaError(
            f"Esquema no soportado en «{url}» (solo se acepta http:// o https://)."
        )
    if partes.username or partes.password:
        raise FuenteInvalidaError(f"«{url}» no puede incluir usuario/contraseña en la URL.")
    hostname = (partes.hostname or "").lower()
    if not hostname:
        raise FuenteInvalidaError(f"«{url}» no tiene un host válido.")
    if await _host_bloqueado_por_red(hostname):
        raise FuenteInvalidaError(
            f"«{hostname}» resuelve a una dirección de red privada, local o reservada — "
            "bloqueado por seguridad (protección SSRF)."
        )


@dataclass(frozen=True)
class SkillFileDesdeUrl:
    """`SKILL.md` (o archivo de skill equivalente) descargado directo de una URL pegada
    por el usuario."""

    texto: str
    url: str  # URL exacta que respondió 200 (tras redirects, cada uno ya revalidado).


def _nombre_por_defecto_de_url(url: str) -> str:
    """Fallback de `nombre` cuando el frontmatter no trae `name`: el último segmento no
    vacío del path de `url`, sin la extensión `.md` — p. ej.
    `https://ejemplo.com/skills/pdf-helper/SKILL.md` -> `"pdf-helper"` (se prefiere el
    segmento ANTERIOR cuando el último es literalmente `SKILL.md`, el caso más común;
    de otro modo el fallback sería siempre el poco útil `"SKILL"`)."""
    segmentos = [p for p in urlsplit(url).path.split("/") if p]
    if not segmentos:
        return "skill"
    ultimo = segmentos[-1]
    if ultimo.lower() == "skill.md" and len(segmentos) > 1:
        return segmentos[-2]
    return ultimo[:-3] if ultimo.lower().endswith(".md") else ultimo


class UrlSkillSource:
    """Instala una skill pegando DIRECTO la URL http(s) de su `SKILL.md` — sin pasar por
    github.com/skills.sh (los únicos hosts que acepta
    `edecan_skills.installer.parse_source`). Ver el bloque de comentarios de arriba para
    el porqué del guardrail anti-SSRF propio.

    Descarga en streaming, capada a `_MAX_URL_SKILL_BYTES` (se corta apenas se supera,
    sin esperar a bajar el archivo completo — mismo criterio que
    `installer.fetch_skill`/`_TarballSkillSource._descargar_tarball`), y sigue redirects
    A MANO (nunca `follow_redirects=True` del cliente): cada destino se revalida con
    `_validar_url_segura` ANTES de pedirse, hasta `_MAX_REDIRECTS_URL` veces — así un host
    público ya validado no puede redirigir hacia una IP privada sin que el guardrail lo
    vea (mismo patrón que `edecan_browser.fetch.HttpxFetcher`)."""

    name = "url"

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def _descargar(self, url: str) -> SkillFileDesdeUrl:
        url_actual = url
        for _ in range(_MAX_REDIRECTS_URL + 1):
            await _validar_url_segura(url_actual)
            try:
                async with self._http.stream("GET", url_actual) as respuesta:
                    if respuesta.has_redirect_location:
                        # `next_request` ya trae la URL de destino resuelta a absoluta
                        # por httpx — con `follow_redirects=False` (default del
                        # cliente) la calcula pero NO la pide sola; se revalida en la
                        # próxima vuelta del for, ANTES de pedirla.
                        url_actual = str(respuesta.next_request.url)  # type: ignore[union-attr]
                        continue
                    if respuesta.status_code != 200:
                        raise SkillNoEncontradaError(
                            f"«{url_actual}» devolvió {respuesta.status_code} (se esperaba 200)."
                        )
                    crudo = bytearray()
                    async for trozo in respuesta.aiter_bytes():
                        crudo.extend(trozo)
                        if len(crudo) > _MAX_URL_SKILL_BYTES:
                            raise SkillDemasiadoGrandeError(
                                f"«{url}» supera el límite de {_MAX_URL_SKILL_BYTES} bytes."
                            )
                    encoding = respuesta.encoding or "utf-8"
                    texto = bytes(crudo).decode(encoding, errors="replace")
                return SkillFileDesdeUrl(texto=texto, url=url_actual)
            except httpx.HTTPError as exc:
                raise SkillNoEncontradaError(
                    f"Fallo de red descargando «{url_actual}»: {exc}"
                ) from exc
        raise FuenteInvalidaError(
            f"«{url}» tiene demasiados redirects (máximo {_MAX_REDIRECTS_URL})."
        )

    async def fetch(self, url: str) -> SkillHit:
        """`SkillHit` con `source=url` — la URL ORIGINAL pegada por el usuario, nunca la
        de un redirect final: así una reinstalación futura vuelve a pasar por el
        guardrail anti-SSRF completo, en vez de confiar en un destino de redirect que
        pudo cambiar desde entonces.

        A diferencia de `_TarballSkillSource.search` (best-effort, `[]` ante cualquier
        fallo: un índice adicional caído nunca debe tumbar `buscar_skills`), acá el
        usuario pidió instalar ESTA URL puntual — un fallo (`FuenteInvalidaError`,
        `SkillNoEncontradaError`, `SkillDemasiadoGrandeError`) debe explicársele, nunca
        silenciarse como si no hubiera resultados."""
        texto_limpio = (url or "").strip()
        if not texto_limpio:
            raise FuenteInvalidaError("Falta la URL de la skill.")
        archivo = await self._descargar(texto_limpio)
        nombre, descripcion = _preview(
            archivo.texto, default_name=_nombre_por_defecto_de_url(texto_limpio)
        )
        return SkillHit(
            nombre=nombre, source=texto_limpio, descripcion=descripcion, installs=None
        )


__all__ = ["HermesSource", "OpenClawSource", "SkillFileDesdeUrl", "UrlSkillSource"]
