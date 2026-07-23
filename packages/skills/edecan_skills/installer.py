"""Instalación de una Agent Skill: parseo de la fuente, descarga del `SKILL.md` y parseo de
su frontmatter (`ARCHITECTURE.md` §10.7, `DIRECCION_ACTUAL.md`).

Un "Agent Skill" es un repo/carpeta con `SKILL.md` (frontmatter YAML: `name`,
`description`, opcionalmente `version`/`license`/metadata) cuyo cuerpo markdown son
instrucciones para el agente — mismo estándar abierto que indexa skills.sh e instala
`npx skills add <owner/repo>`. Este módulo replica ESE mecanismo de instalación leyendo el
`SKILL.md` directo desde `raw.githubusercontent.com` (API pública oficial de GitHub para
contenido raw), sin depender de la API de skills.sh para nada — `edecan_skills.client` es
solo descubrimiento (búsqueda), este módulo es el camino que SIEMPRE funciona.

**Anti path-traversal / anti-SSRF, a propósito**: `parse_source()` NUNCA devuelve una URL
lista para pedir — solo `(owner, repo, subpath)`, cada segmento validado contra una regex
estricta. `fetch_skill()` arma las URLs a pedir ÍNTEGRAMENTE a mano, siempre contra el host
fijo `raw.githubusercontent.com` — el host que el usuario haya pegado en una URL (github.com
o skills.sh, los únicos aceptados) nunca se usa para armar la petición real, solo para
extraer `owner`/`repo`/`subpath` de ahí. Así una fuente maliciosa no puede hacer que Edecán
pida una URL arbitraria.

**Límites de nombre/descripción y capacidades declaradas (WP-V5-04)**: `MAX_NAME_LENGTH`/
`MAX_DESCRIPTION_LENGTH` y el regex de slug están portados de
`openjarvis.skills.parser.SkillParser` (Apache-2.0) — con una diferencia deliberada: donde
OpenJarvis RECHAZA un `name` que no cumple el regex (su spec exige que `name` ya sea el
identificador), Edecán tolera nombres humanos libres (`"PDF Helper"`, con espacios/
mayúsculas) porque ya los normaliza a slug en `edecan_skills.store.slugify` — acá solo se
valida que esa normalización no colapse a nada (`FuenteInvalidaError` si un `name` compuesto
solo de emoji/símbolos/espacios produciría un slug vacío, en vez de dejar que dos fuentes
distintas terminen compartiendo en silencio el slug de reserva `"skill"`). `parse_capabilities`
lee el campo `allowed-tools` del frontmatter (mismo estándar que Claude Code y otros agentes
que ya consumen Agent Skills) — ver `edecan_skills.security` para qué significa que una
capacidad declarada sea "peligrosa".
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml

logger = logging.getLogger(__name__)

# Solo caracteres seguros de nombre de repo/owner de GitHub, y NUNCA "." ni ".." a secas
# (anti path-traversal: un segmento ".." no puede colarse en la ruta que se arma a mano en
# `fetch_skill`). GitHub en sí ya prohíbe nombres de owner/repo con estas formas, pero se
# valida igual acá porque `parse_source` es la única puerta de entrada de datos del usuario.
_SEGMENT_RE = re.compile(r"^(?!\.{1,2}$)[A-Za-z0-9._-]+$")

# Únicos hosts que `parse_source` acepta en una URL completa — cualquier otro host se
# rechaza (anti-SSRF: nunca se arma una petición hacia un host arbitrario pegado por el
# usuario; ver docstring del módulo).
_ALLOWED_HOSTS = {"github.com", "www.github.com", "skills.sh", "www.skills.sh"}

_RAW_BASE = "https://raw.githubusercontent.com"
_MAX_BYTES = 200_000
_FRONTMATTER_DELIM = "---"

# Caracteres de control a eliminar de cualquier texto que venga de un SKILL.md de
# terceros, salvo '\n' (0x0A) y '\t' (0x09) — incluye '\r', NUL y el resto de C0/DEL.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

# Límites estrictos portados de `openjarvis.skills.parser` (Apache-2.0) — ver docstring del
# módulo para la diferencia deliberada frente al original (acá se trunca, no se rechaza,
# salvo el caso "el nombre no produce ningún slug").
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024

# Regex de slug EXACTA de `openjarvis.skills.parser._NAME_PATTERN`: minúsculas/dígitos con
# guiones simples, sin guion inicial/final ni dobles. Se aplica al `name` del frontmatter
# tal cual viene (para preservarlo sin re-normalizar si ya es un slug válido) y, si no
# cumple, al resultado de colapsarlo con el mismo algoritmo que `store.slugify` (ver
# `_validar_nombre_produce_slug`).
_NOMBRE_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?!-))*[a-z0-9]$|^[a-z0-9]$")

# Réplica local del colapso de `edecan_skills.store.slugify`, a propósito SIN su fallback
# `"skill"` — acá el resultado vacío es justamente la señal que dispara el rechazo (ver
# `_validar_nombre_produce_slug`); `store.slugify` sigue siendo la única fuente de verdad
# para el `slug` que de verdad se persiste.
_SLUG_COLAPSA_RE = re.compile(r"[^a-z0-9]+")

# `allowed-tools` del frontmatter: separadores válidos al normalizar cada capacidad a
# snake_case (espacios y guiones colapsan a '_').
_CAPACIDAD_SEPARADOR_RE = re.compile(r"[\s-]+")
_CAPACIDAD_GUIONES_BAJOS_RE = re.compile(r"_+")


class FuenteInvalidaError(ValueError):
    """`source` no tiene una forma reconocida, o algún segmento (owner/repo/subpath) no
    pasa la validación anti path-traversal/SSRF (ver docstring del módulo)."""


class SkillNoEncontradaError(ValueError):
    """Ningún candidato de ruta de `fetch_skill` devolvió un `SKILL.md` (404 en los tres)."""


class SkillDemasiadoGrandeError(ValueError):
    """El `SKILL.md` encontrado supera `_MAX_BYTES` — el llamador HTTP lo mapea a 413."""


@dataclass(frozen=True)
class SkillFile:
    """`SKILL.md` crudo, tal cual se descargó."""

    texto: str
    url: str  # candidato exacto que respondió 200 — trazabilidad/logs, nunca se re-pide.


@dataclass(frozen=True)
class InstalledSkill:
    """Resultado completo de `install_from_source`: fuente ya resuelta + `SKILL.md` ya
    parseado, lista para persistir (`edecan_skills.store.insert_skill`)."""

    owner: str
    repo: str
    subpath: str | None
    source: str  # normalizado: "owner/repo" o "owner/repo/subpath"
    nombre: str
    descripcion: str
    version: str | None
    contenido: str
    capabilities: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# parse_source
# ---------------------------------------------------------------------------


def _validar_segmento(segmento: str, *, campo: str) -> str:
    if not _SEGMENT_RE.match(segmento):
        raise FuenteInvalidaError(
            f"'{segmento}' no es un {campo} válido (solo letras/números/'.'/'_'/'-', sin '..')."
        )
    return segmento


def _validar_subpath(partes: list[str]) -> str | None:
    limpio = [p for p in partes if p]  # descarta segmentos vacíos ('//' o slash final)
    if not limpio:
        return None
    for parte in limpio:
        _validar_segmento(parte, campo="segmento de ruta")
    return "/".join(limpio)


def _parse_url(texto: str) -> tuple[str, str, str | None]:
    parsed = urlparse(texto)
    if parsed.scheme not in ("https", "http"):
        raise FuenteInvalidaError(f"Esquema no soportado en '{texto}' (usa https://).")
    host = (parsed.hostname or "").lower()
    if host not in _ALLOWED_HOSTS:
        raise FuenteInvalidaError(
            f"Host no permitido: '{host or texto}'. Solo se aceptan github.com o skills.sh."
        )

    segmentos = [p for p in parsed.path.split("/") if p != ""]
    if len(segmentos) < 2:
        raise FuenteInvalidaError(f"'{texto}' no incluye 'owner/repo'.")
    owner = _validar_segmento(segmentos[0], campo="owner")
    repo = _validar_segmento(segmentos[1], campo="repo")

    resto = segmentos[2:]
    if resto and resto[0] == "tree" and "github.com" in host:
        # .../tree/<branch>/<path...> — el branch se descarta a propósito:
        # `fetch_skill` siempre resuelve contra "HEAD", nunca contra un branch
        # específico (ver su docstring). Lo que sigue al branch es el subpath.
        resto = resto[2:] if len(resto) > 1 else []
    subpath = _validar_subpath(resto)
    return owner, repo, subpath


def parse_source(source: str) -> tuple[str, str, str | None]:
    """`(owner, repo, subpath|None)` a partir de cualquiera de las 4 formas soportadas:
    `'owner/repo'`, `'owner/repo/sub/path'`, una URL de GitHub
    (`https://github.com/owner/repo` o `.../tree/<branch>/<path>`), o una URL de skills.sh
    (`https://skills.sh/owner/repo`).

    Lanza `FuenteInvalidaError` (subclase de `ValueError`) si `source` no tiene una forma
    reconocida, si algún segmento no pasa `_SEGMENT_RE`, o si una URL apunta a un host que
    no sea github.com/skills.sh.
    """
    texto = (source or "").strip()
    if not texto:
        raise FuenteInvalidaError("Falta la fuente de la skill (ej. 'owner/repo').")

    if "://" in texto:
        return _parse_url(texto)

    partes = [p for p in texto.split("/") if p != ""]
    if len(partes) < 2:
        raise FuenteInvalidaError(
            f"'{source}' no tiene la forma 'owner/repo' (ni 'owner/repo/sub/path')."
        )
    owner = _validar_segmento(partes[0], campo="owner")
    repo = _validar_segmento(partes[1], campo="repo")
    subpath = _validar_subpath(partes[2:])
    return owner, repo, subpath


# ---------------------------------------------------------------------------
# fetch_skill
# ---------------------------------------------------------------------------


def _candidatos(owner: str, repo: str, subpath: str | None) -> list[str]:
    rutas: list[str] = []

    def agregar(ruta: str) -> None:
        ruta = ruta.strip("/")
        if ruta and ruta not in rutas:
            rutas.append(ruta)

    if subpath:
        # El `id` de skills.sh suele ser owner/repo/<skill-id>, mientras el
        # repositorio guarda la skill en skills/<skill-id>/SKILL.md. Otros
        # índices ya entregan un subpath completo. Se soportan ambos sin
        # adivinar hosts ni hacer peticiones fuera de GitHub raw.
        agregar(f"{subpath}/SKILL.md")
        agregar(f"skills/{subpath}/SKILL.md")
        agregar(f".claude/skills/{subpath}/SKILL.md")
        agregar(f".agents/skills/{subpath}/SKILL.md")
        # OpenAI publica las skills indexadas bajo skills/.curated y
        # skills/.system; el índice expone solo el slug humano.
        agregar(f"skills/.curated/{subpath}/SKILL.md")
        agregar(f"skills/.system/{subpath}/SKILL.md")
    else:
        agregar("SKILL.md")

    agregar(f"skills/{repo}/SKILL.md")
    agregar("skill/SKILL.md")
    return [f"{_RAW_BASE}/{owner}/{repo}/HEAD/{ruta}" for ruta in rutas]


async def fetch_skill(
    owner: str, repo: str, subpath: str | None, http: httpx.AsyncClient
) -> SkillFile:
    """Descarga el `SKILL.md` de `owner/repo` (y opcionalmente `subpath`), probando en
    rutas candidatas seguras contra `raw.githubusercontent.com` — siempre en la rama
    `HEAD` (la rama por defecto del repo, cualquiera sea su nombre real, sin tener que
    resolverlo aparte): primero `{subpath}/SKILL.md` (o `SKILL.md` en la raíz si no hay
    `subpath`), las estructuras habituales de monorepos (`skills/`, `.claude/skills/`,
    `.agents/skills/` y las colecciones curadas de OpenAI), y los fallbacks históricos.
    La primera que
    responda 200 gana — las demás ni se intentan.

    La descarga es en streaming y se corta apenas se superan `_MAX_BYTES` (200_000, sin
    esperar a bajar el archivo completo — importante ante un `SKILL.md` enorme o
    malicioso), lanzando `SkillDemasiadoGrandeError`. Si todas responden 404 (o
    fallan de red), lanza `SkillNoEncontradaError`.
    """
    fuente = f"{owner}/{repo}" + (f"/{subpath}" if subpath else "")

    for url in _candidatos(owner, repo, subpath):
        try:
            async with http.stream("GET", url) as respuesta:
                if respuesta.status_code == 404:
                    continue
                if respuesta.status_code != 200:
                    logger.warning("GET %s devolvió %d (inesperado)", url, respuesta.status_code)
                    continue

                crudo = bytearray()
                async for trozo in respuesta.aiter_bytes():
                    crudo.extend(trozo)
                    if len(crudo) > _MAX_BYTES:
                        raise SkillDemasiadoGrandeError(
                            f"El SKILL.md de «{fuente}» supera el límite de {_MAX_BYTES} bytes."
                        )
                encoding = respuesta.encoding or "utf-8"
                texto = bytes(crudo).decode(encoding, errors="replace")
            return SkillFile(texto=texto, url=url)
        except httpx.HTTPError as exc:
            logger.warning("Fallo de red obteniendo %s: %s", url, exc)
            continue

    raise SkillNoEncontradaError(f"No se encontró SKILL.md en {fuente}.")


# ---------------------------------------------------------------------------
# parse_skill_md
# ---------------------------------------------------------------------------


def _sanitizar(texto: str) -> str:
    """Strip de null bytes y caracteres de control, salvo `\\n`/`\\t` (ver `_CONTROL_CHARS_RE`)."""
    return _CONTROL_CHARS_RE.sub("", texto or "")


def _split_frontmatter(texto: str) -> tuple[dict | None, str]:
    """`(frontmatter, cuerpo)`. `frontmatter` es `None` si no hay bloque `---`…`---` al
    inicio, si el YAML es inválido, o si no evalúa a un `dict` (p. ej. una lista o un
    escalar sueltos) — en los tres casos se trata todo `texto` como cuerpo plano, nunca
    se lanza (`yaml.safe_load` SIEMPRE, nunca `yaml.load` sin `Loader`: evita ejecutar
    tags arbitrarios de un `SKILL.md` de un tercero)."""
    lineas = texto.splitlines()
    if not lineas or lineas[0].strip() != _FRONTMATTER_DELIM:
        return None, texto.strip()

    for i in range(1, len(lineas)):
        if lineas[i].strip() == _FRONTMATTER_DELIM:
            bloque = "\n".join(lineas[1:i])
            cuerpo = "\n".join(lineas[i + 1 :]).strip()
            try:
                datos = yaml.safe_load(bloque)
            except yaml.YAMLError:
                logger.warning(
                    "Frontmatter YAML inválido en SKILL.md; se ignora, se usa como cuerpo plano."
                )
                return None, texto.strip()
            return (datos if isinstance(datos, dict) else None), cuerpo

    return None, texto.strip()  # nunca se cerró el '---': no es frontmatter válido


def _primera_linea_no_vacia(texto: str) -> str:
    for linea in texto.splitlines():
        limpio = linea.strip()
        if limpio:
            return limpio
    return ""


def parse_skill_md(texto: str) -> tuple[str, str, str | None, str]:
    """`(nombre, descripcion, version|None, cuerpo)` de un `SKILL.md`.

    Con frontmatter YAML válido (delimitado por `---`): `nombre`/`descripcion`/`version`
    vienen de las claves `name`/`description`/`version` (`.get` defensivo, `""`/`None` si
    faltan); si el frontmatter no trae `description`, se completa con la primera línea no
    vacía del cuerpo (mismo fallback que el caso sin frontmatter). Sin frontmatter (o
    inválido): `nombre=""` (el llamador, que sí conoce el `repo`, lo completa — ver
    `install_from_source`) y `descripcion`=primera línea no vacía del cuerpo.
    """
    frontmatter, cuerpo = _split_frontmatter(_sanitizar(texto))

    if frontmatter is not None:
        nombre = str(frontmatter.get("name") or "").strip()
        descripcion = str(frontmatter.get("description") or "").strip() or _primera_linea_no_vacia(
            cuerpo
        )
        version_raw = frontmatter.get("version")
        version = str(version_raw).strip() if version_raw not in (None, "") else None
        return nombre, descripcion, version, cuerpo

    return "", _primera_linea_no_vacia(cuerpo), None, cuerpo


# ---------------------------------------------------------------------------
# parse_capabilities — campo `allowed-tools` del frontmatter
# ---------------------------------------------------------------------------


def _normalizar_capacidad(valor: str) -> str:
    """`"Enviar Correo"`/`"enviar-correo"` -> `"enviar_correo"`: minúsculas, espacios y
    guiones colapsan a `_` (colapsando repetidos), sin `_` al borde."""
    minusculas = (valor or "").strip().lower()
    con_guion_bajo = _CAPACIDAD_SEPARADOR_RE.sub("_", minusculas)
    return _CAPACIDAD_GUIONES_BAJOS_RE.sub("_", con_guion_bajo).strip("_")


def parse_capabilities(texto: str) -> list[str]:
    """Capacidades declaradas por el campo estándar `allowed-tools` del frontmatter de un
    `SKILL.md` (mismo campo que usan Claude Code y otros agentes que ya consumen Agent
    Skills), normalizadas a snake_case en minúsculas, sin duplicados, en el orden en que
    aparecen. Tolera tanto una lista YAML (`allowed-tools: [enviar_correo,
    usar_computadora]`) como un string separado por comas (`allowed-tools: "enviar_correo,
    usar_computadora"`) — `[]` si el campo falta, no tiene ninguna de esas dos formas, o el
    frontmatter no es válido (mismo criterio permisivo que `parse_skill_md`: un frontmatter
    inválido nunca lanza, degrada a "sin capacidades declaradas").

    Estas capacidades son SOLO la declaración de la propia skill sobre lo que espera poder
    usar — Edecán nunca ejecuta código de una skill (ver `docs/skills.md`), así que
    declarar `usar_computadora` acá no le da a la skill ningún poder real; es información
    de riesgo que `edecan_skills.security`/la UI muestran antes de activarla.
    """
    frontmatter, _ = _split_frontmatter(_sanitizar(texto))
    if not frontmatter:
        return []

    crudo = frontmatter.get("allowed-tools")
    if isinstance(crudo, str):
        items: list[Any] = crudo.split(",")
    elif isinstance(crudo, list):
        items = crudo
    else:
        return []

    vistas: set[str] = set()
    resultado: list[str] = []
    for item in items:
        normalizada = _normalizar_capacidad(str(item))
        if normalizada and normalizada not in vistas:
            vistas.add(normalizada)
            resultado.append(normalizada)
    return resultado


# ---------------------------------------------------------------------------
# Validación de nombre -> slug (ver docstring del módulo)
# ---------------------------------------------------------------------------


def _validar_nombre_produce_slug(nombre: str) -> None:
    """Exige que `nombre` produzca un slug no vacío. Si `nombre` YA es un slug válido (la
    regex `_NOMBRE_SLUG_RE`, portada de OpenJarvis) se preserva tal cual — no se
    re-normaliza un `name` que un `SKILL.md` bien formado ya trae como identificador. Si
    no, se colapsa con el mismo algoritmo que `edecan_skills.store.slugify` (sin su
    fallback `"skill"`, ver `_SLUG_COLAPSA_RE`) y se rechaza con `FuenteInvalidaError` si
    el resultado queda vacío (un `nombre` compuesto solo de emoji/símbolos/espacios) — sin
    este chequeo, esa fuente terminaría persistida bajo el slug de reserva `"skill"` de
    `store.slugify`, chocando en silencio con cualquier otra fuente igual de vacía."""
    if _NOMBRE_SLUG_RE.match(nombre):
        return
    colapsado = _SLUG_COLAPSA_RE.sub("-", nombre.strip().lower()).strip("-")
    if not colapsado:
        raise FuenteInvalidaError(
            f"El nombre «{nombre}» no produce un identificador válido tras normalizar "
            "(solo tiene caracteres no alfanuméricos)."
        )


# ---------------------------------------------------------------------------
# Pipeline completo: parse_source -> fetch_skill -> parse_skill_md
# ---------------------------------------------------------------------------


async def install_from_source(source: str, *, http: httpx.AsyncClient) -> InstalledSkill:
    """Pipeline completo de "instalar una skill desde su fuente": `parse_source` →
    `fetch_skill` → `parse_skill_md` (+ `parse_capabilities`), con el fallback `nombre=repo`
    cuando el `SKILL.md` no trae `name` en su frontmatter, truncado a `MAX_NAME_LENGTH`/
    `MAX_DESCRIPTION_LENGTH` y validado con `_validar_nombre_produce_slug` (ver docstring
    del módulo — lanza `FuenteInvalidaError` si el nombre resultante no produce ningún slug).

    Única fuente de verdad para este pipeline — la usan tanto
    `edecan_skills.tools.InstalarSkillTool` como `POST /v1/skills/install`
    (`apps/api/edecan_api/routers/skills.py`), para no duplicarlo en dos lugares (mismo
    criterio que `edecan_business.invoices.crear_factura`, ver su docstring). Deliberadamente
    NO toca la base de datos — eso es trabajo exclusivo de `edecan_skills.store.insert_skill`,
    que el llamador invoca después con el resultado de esta función.
    """
    owner, repo, subpath = parse_source(source)
    archivo = await fetch_skill(owner, repo, subpath, http)
    nombre, descripcion, version, cuerpo = parse_skill_md(archivo.texto)
    capabilities = parse_capabilities(archivo.texto)
    source_normalizado = f"{owner}/{repo}" + (f"/{subpath}" if subpath else "")

    nombre_final = (nombre or repo)[:MAX_NAME_LENGTH]
    _validar_nombre_produce_slug(nombre_final)

    return InstalledSkill(
        owner=owner,
        repo=repo,
        subpath=subpath,
        source=source_normalizado,
        nombre=nombre_final,
        descripcion=descripcion[:MAX_DESCRIPTION_LENGTH],
        version=version,
        contenido=cuerpo,
        capabilities=capabilities,
    )
