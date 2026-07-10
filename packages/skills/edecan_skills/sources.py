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

import io
import logging
import tarfile

import httpx
import yaml

from .client import SkillHit
from .installer import SkillDemasiadoGrandeError

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


__all__ = ["HermesSource", "OpenClawSource"]
