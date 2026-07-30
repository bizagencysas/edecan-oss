"""`fuente_local` — Fuente #2 de Agent Skills: una carpeta del propio Mac del dueño, sin
subir nada a ningún lado (`ARCHITECTURE.md` §10.7, junto a `sources.py` que agrega
OpenClaw/Hermes como fuentes remotas).

Este módulo NO hace ninguna petición de red — lee directo del disco local. Por eso el
riesgo que le importa NO es la procedencia del contenido (la ruta la da el propio dueño
desde su máquina, ya "confiable" en ese sentido — a diferencia de un `owner/repo` de
GitHub o de un índice de terceros), sino el ALCANCE de la lectura: nunca seguir un enlace
simbólico que escape de la carpeta que el dueño indicó, y nunca leer un archivo más allá
del mismo tope de tamaño que ya se aplica a un `SKILL.md` remoto
(`edecan_skills.installer._MAX_BYTES`, reusado acá tal cual, no reinventado).

Dos funciones públicas, deliberadamente simétricas a lo que ya existe:

- `leer_skill_local(ruta)` -> `edecan_skills.installer.SkillFile` — MISMA forma que
  devuelve `installer.fetch_skill()` (un `.texto` + un `.url` de trazabilidad), para que
  todo lo que viene después en el pipeline (`installer.parse_skill_md`,
  `installer.parse_capabilities`) siga funcionando sin ningún cambio, sin que le importe
  si el `SkillFile` vino de `raw.githubusercontent.com` o del disco.
- `listar_skills_locales(ruta)` -> `list[edecan_skills.client.SkillHit]` — MISMA forma
  que devuelve `sources.OpenClawSource.search()`/`HermesSource.search()`: cuando la ruta
  dada apunta a una carpeta con VARIAS skills (una subcarpeta con `SKILL.md` por cada
  una), se listan todas en vez de adivinar cuál quiso decir el dueño; si la ruta ya
  apunta a una única skill (una carpeta con `SKILL.md` directo, o un `.md` suelto), la
  lista tiene un solo elemento cuyo `source` es esa misma ruta.

Instalar de verdad una skill encontrada acá (persistirla, escanearla en busca de
inyección, etc.) sigue siendo trabajo de `edecan_skills.store.insert_skill`, exactamente
igual que con cualquier otra fuente — este módulo solo resuelve "¿qué `SKILL.md` es este
y qué dice?", nunca toca la base de datos.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .client import SkillHit
from .installer import _MAX_BYTES as _MAX_SKILL_BYTES
from .installer import SkillDemasiadoGrandeError, SkillFile, parse_skill_md

logger = logging.getLogger(__name__)

_SKILL_MD_NAME = "SKILL.md"
_K_DEFECTO = 10


class RutaSkillInvalidaError(ValueError):
    """La ruta dada no resuelve a ninguna skill legible: no existe, no hay permisos para
    leerla, no contiene ningún `SKILL.md` (ni directo ni en una subcarpeta), o algún paso
    de la resolución intenta seguir un enlace simbólico fuera de la carpeta indicada (ver
    docstring del módulo)."""


def _ruta_no_vacia(ruta_texto: str) -> Path:
    texto = (ruta_texto or "").strip()
    if not texto:
        raise RutaSkillInvalidaError(
            "Falta la ruta de la skill local (ej. '/Users/tu/mis-skills/algo')."
        )
    return Path(texto).expanduser()


def _base_de_contencion(ruta: Path) -> Path:
    """Real path de la carpeta que actúa de frontera anti-symlink-fuera: la propia `ruta`
    si ya es un directorio, o su carpeta contenedora si `ruta` apunta directo a un `.md`
    (el "carpeta indicada" del paquete de trabajo, en ambos casos)."""
    try:
        objetivo = ruta if ruta.is_dir() else ruta.parent
        return objetivo.resolve(strict=True)
    except PermissionError as exc:
        raise RutaSkillInvalidaError(f"Sin permisos para acceder a '{ruta}': {exc}") from exc
    except OSError as exc:
        raise RutaSkillInvalidaError(
            f"La ruta '{ruta}' no existe o no es accesible: {exc}"
        ) from exc


def _dentro_de_base(candidato: Path, base: Path) -> Path:
    """Resuelve `candidato` siguiendo symlinks y exige que el resultado quede dentro de
    `base` — si el candidato (o cualquier symlink en su camino) escapa de esa carpeta,
    `RutaSkillInvalidaError` en vez de leerlo (ver docstring del módulo)."""
    try:
        real = candidato.resolve(strict=True)
    except PermissionError as exc:
        raise RutaSkillInvalidaError(f"Sin permisos para leer '{candidato}': {exc}") from exc
    except OSError as exc:
        raise RutaSkillInvalidaError(f"No se pudo resolver '{candidato}': {exc}") from exc
    try:
        real.relative_to(base)
    except ValueError:
        raise RutaSkillInvalidaError(
            f"'{candidato}' es (o pasa por) un enlace simbólico que apunta fuera de la "
            f"carpeta indicada ('{base}') — no se sigue, por seguridad."
        ) from None
    return real


def _localizar_skill_md(ruta: Path) -> Path:
    """`Path` real del `SKILL.md` a leer, dada la ruta que pasó el dueño: la propia `ruta`
    si ya es un `.md`, o `ruta/SKILL.md` si `ruta` es una carpeta con esa skill directo en
    la raíz. Nunca adivina entre varias subcarpetas — para eso está
    `listar_skills_locales`."""
    if not ruta.exists():
        raise RutaSkillInvalidaError(f"La ruta '{ruta}' no existe.")

    base = _base_de_contencion(ruta)

    if ruta.is_file():
        if ruta.suffix.lower() != ".md":
            raise RutaSkillInvalidaError(
                f"'{ruta}' no es un archivo de skill (.md) — pasa un `SKILL.md` o la carpeta "
                "que lo contiene."
            )
        return _dentro_de_base(ruta, base)

    if ruta.is_dir():
        candidato = ruta / _SKILL_MD_NAME
        if not candidato.exists():
            raise RutaSkillInvalidaError(
                f"La carpeta '{ruta}' no tiene ningún {_SKILL_MD_NAME} directo. Si guarda "
                "varias skills en subcarpetas, usa listar_skills_locales para verlas."
            )
        return _dentro_de_base(candidato, base)

    raise RutaSkillInvalidaError(f"'{ruta}' no es ni un archivo ni una carpeta.")


def leer_skill_local(ruta_texto: str) -> SkillFile:
    """Localiza y lee el `SKILL.md` en `ruta_texto` (una carpeta con `SKILL.md` directo, o
    la ruta a un `.md` suelto) y lo devuelve como `SkillFile` — MISMA forma que
    `installer.fetch_skill()` (ver docstring del módulo), para que `parse_skill_md`/
    `parse_capabilities` lo procesen sin ningún cambio.

    Mismo tope de tamaño que un `SKILL.md` remoto (`installer._MAX_BYTES`, 200_000 bytes):
    `SkillDemasiadoGrandeError` si se supera. `RutaSkillInvalidaError` ante ruta
    inexistente, sin permisos, sin ningún `SKILL.md`, o un enlace simbólico que escape de
    la carpeta indicada.
    """
    ruta = _ruta_no_vacia(ruta_texto)
    skill_md = _localizar_skill_md(ruta)

    try:
        tam = skill_md.stat().st_size
    except OSError as exc:
        raise RutaSkillInvalidaError(f"No se pudo leer '{skill_md}': {exc}") from exc
    if tam > _MAX_SKILL_BYTES:
        raise SkillDemasiadoGrandeError(
            f"El SKILL.md de '{skill_md}' supera el límite de {_MAX_SKILL_BYTES} bytes."
        )

    try:
        crudo = skill_md.read_bytes()
    except PermissionError as exc:
        raise RutaSkillInvalidaError(f"Sin permisos para leer '{skill_md}': {exc}") from exc
    # Defensivo ante TOCTOU (el archivo creció entre el `stat()` de arriba y este `read`):
    # nunca confiar solo en el tamaño reportado antes de leer.
    if len(crudo) > _MAX_SKILL_BYTES:
        raise SkillDemasiadoGrandeError(
            f"El SKILL.md de '{skill_md}' supera el límite de {_MAX_SKILL_BYTES} bytes."
        )

    texto = crudo.decode("utf-8", errors="replace")
    return SkillFile(texto=texto, url=f"file://{skill_md}")


def _nombre_por_defecto(ruta: Path) -> str:
    """Nombre a usar si el `SKILL.md` no trae `name` en su frontmatter: el nombre del
    `.md` sin extensión, o el de la carpeta que lo contiene."""
    return ruta.stem if ruta.is_file() else ruta.name


def _hit_de_una_skill(ruta: Path) -> SkillHit:
    archivo = leer_skill_local(str(ruta))
    nombre, descripcion, _version, _cuerpo = parse_skill_md(archivo.texto)
    return SkillHit(
        nombre=nombre or _nombre_por_defecto(ruta),
        source=str(ruta),
        descripcion=descripcion,
        installs=None,
    )


def listar_skills_locales(ruta_texto: str, k: int = _K_DEFECTO) -> list[SkillHit]:
    """Skills encontradas en `ruta_texto`, en la MISMA forma que devuelven las fuentes
    remotas indexadas (`sources.OpenClawSource.search()`/`HermesSource.search()`): una
    lista de `SkillHit` cuyo `source` es la ruta exacta a pasarle después a
    `leer_skill_local` para instalar esa skill en particular.

    Tres casos:
    - `ruta_texto` apunta a un `.md` suelto, o a una carpeta con `SKILL.md` directo en su
      raíz: una única skill, lista de un elemento.
    - `ruta_texto` apunta a una carpeta SIN `SKILL.md` directo pero con subcarpetas que sí
      lo tienen (una skill por subcarpeta): se listan todas (hasta `k`), en vez de adivinar
      cuál quiso decir el dueño. Una subcarpeta cuyo `SKILL.md` no se puede leer (demasiado
      grande, o un enlace simbólico que escapa de `ruta_texto`) se ignora con un
      `logger.warning` en vez de tumbar el resto del listado — mismo criterio best-effort
      que ya usan las fuentes remotas ante un ítem individual defectuoso.
    - Ningún caso anterior aplica: `RutaSkillInvalidaError` con un mensaje accionable.
    """
    ruta = _ruta_no_vacia(ruta_texto)
    if not ruta.exists():
        raise RutaSkillInvalidaError(f"La ruta '{ruta}' no existe.")

    if ruta.is_file():
        return [_hit_de_una_skill(ruta)]

    if not ruta.is_dir():
        raise RutaSkillInvalidaError(f"'{ruta}' no es ni un archivo ni una carpeta.")

    if (ruta / _SKILL_MD_NAME).exists():
        return [_hit_de_una_skill(ruta)]

    base = _base_de_contencion(ruta)
    try:
        subcarpetas = sorted(p for p in ruta.iterdir() if p.is_dir())
    except PermissionError as exc:
        raise RutaSkillInvalidaError(f"Sin permisos para listar '{ruta}': {exc}") from exc

    resultados: list[SkillHit] = []
    limite = max(1, k)
    for subcarpeta in subcarpetas:
        candidato = subcarpeta / _SKILL_MD_NAME
        if not candidato.exists():
            continue
        try:
            _dentro_de_base(candidato, base)
            hit = _hit_de_una_skill(subcarpeta)
        except (RutaSkillInvalidaError, SkillDemasiadoGrandeError) as exc:
            logger.warning("Se ignora la skill en '%s': %s", subcarpeta, exc)
            continue
        resultados.append(hit)
        if len(resultados) >= limite:
            break

    if not resultados:
        raise RutaSkillInvalidaError(
            f"La carpeta '{ruta}' no contiene ningún {_SKILL_MD_NAME} — ni directo ni en "
            "sus subcarpetas."
        )
    return resultados


__all__ = ["RutaSkillInvalidaError", "leer_skill_local", "listar_skills_locales"]
