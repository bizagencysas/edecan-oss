"""Compilador de skills personales (PHASE2.md §208, §209): convierte una instrucción
recurrente del usuario ("Cuando diga revisar repo...") en un `SKILL.md` estructurado y
vuelve a leerlo en forma de datos.

El objetivo NO es inventar contenido, sino fijar una ESTRUCTURA determinista y reversible
sobre lo que ya se sabe (el disparo, la descripción, las herramientas, los permisos y el
formato de salida) para que la skill resultante sea exactamente el mismo estándar que ya
sabe leer `edecan_skills.installer`: frontmatter YAML `name`/`description`/`version`
(§209 — las skills llevan versión) seguido de un cuerpo markdown. El cuerpo se organiza en
seis secciones fijas con encabezados `##` canónicos:

```text
trigger / inputs / workflow / tools / permissions / output
```

La elección de mapa es deliberada y se documenta acá para que `parse_compiled_skill`
pueda hacer el camino inverso sin ambigüedad:

- `trigger`      ← la instrucción recurrente tal cual (lo que dispara la skill).
- `inputs`       ← la descripción (el contrato de qué recibe/necesita la skill).
- `workflow`     ← el ciclo de vida genérico de una skill (detectar → reunir → aplicar →
                   producir), NO un texto inventado por skill; es el andamiaje que el
                   humano o el agente refinan después.
- `tools`        ← lista `herramientas` serializada como bullets (`- `).
- `permissions`  ← lista `permisos` serializada como bullets (`- `).
- `output`       ← `output_format` tal cual.

`compile_skill` → `parse_compiled_skill` es un round-trip: recompilar lo parseado (salvo el
contenido que este módulo genera, como `workflow`) devuelve el mismo resultado. Única
limitación asumida y documentada: ninguno de los textos de entrada (`instruccion`,
`descripcion`, `output_format`) debe contener una línea que empiece con `## `, porque eso
sería indistinguible de un encabezado de sección.

El frontmatter se emite con `yaml.safe_dump` (nunca interpolación a mano), de modo que un
`name`/`description` con `:`, comillas o caracteres especiales quede correctamente citado y
`installer.parse_skill_md` (que usa `yaml.safe_load`) lo lea idéntico.
"""

from __future__ import annotations

import re

import yaml

from .installer import parse_skill_md

# Secciones canónicas en su orden de aparición en el cuerpo compilado.
_SECCIONES: tuple[str, ...] = (
    "trigger",
    "inputs",
    "workflow",
    "tools",
    "permissions",
    "output",
)

# Título markdown de cada sección (`## <Título>`). Es el nombre público de la sección.
_TITULOS: dict[str, str] = {
    "trigger": "Trigger",
    "inputs": "Inputs",
    "workflow": "Workflow",
    "tools": "Tools",
    "permissions": "Permissions",
    "output": "Output",
}

# Inverso de `_TITULOS` para reconocer un encabezado al parsear.
_NOMBRE_POR_TITULO: dict[str, str] = {titulo: nombre for nombre, titulo in _TITULOS.items()}

# Versión con la que nace toda skill recién compilada (§209: las skills llevan versión).
_VERSION_INICIAL = "1.0.0"

# Semver estricto `MAYOR.MENOR.PARCHE` — lo único que `bump_version` sabe incrementar.
_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

# Ciclo de vida genérico de una skill. No es relleno: es el andamiaje determinista que
# toda skill compilada comparte y que el humano/agente refina por skill concreta.
_WORKFLOW = (
    "1. Detectar el disparo (Trigger) e identificar las entradas (Inputs).\n"
    "2. Aplicar las herramientas declaradas (Tools) dentro de los permisos (Permissions).\n"
    "3. Producir la salida (Output) en el formato declarado."
)


def _seccion(nombre: str, contenido: str) -> str:
    return f"## {_TITULOS[nombre]}\n\n{contenido}"


def _a_bullets(items: list[str]) -> str:
    """Serializa una lista como bullets markdown (`- item`), una por línea."""
    return "\n".join(f"- {item}" for item in items)


def _parsear_bullets(contenido: str) -> list[str]:
    """Inverso de `_a_bullets`: lee bullets markdown (`- item`) y devuelve los items,
    descartando líneas vacías y tolerando el `-` sin espacio."""
    items: list[str] = []
    for linea in (contenido or "").splitlines():
        limpio = linea.strip()
        if not limpio:
            continue
        if limpio.startswith("- "):
            limpio = limpio[2:]
        elif limpio.startswith("-"):
            limpio = limpio[1:]
        items.append(limpio.strip())
    return items


def _parsear_encabezado(linea: str) -> str | None:
    """`nombre` de la sección si `linea` es un encabezado canónico `## <Título>`, o `None`."""
    limpio = linea.strip()
    if not limpio.startswith("## "):
        return None
    return _NOMBRE_POR_TITULO.get(limpio[3:].strip())


def _split_secciones(cuerpo: str) -> dict[str, str]:
    """Descompone `cuerpo` en `{nombre: contenido}` según los encabezados `##` canónicos.
    El texto previo al primer encabezado (preámbulo) se ignora a propósito; una sección sin
    contenido queda con `""`."""
    secciones: dict[str, str] = {}
    actual: str | None = None
    buffer: list[str] = []
    for linea in (cuerpo or "").splitlines():
        encabezado = _parsear_encabezado(linea)
        if encabezado is not None:
            if actual is not None:
                secciones[actual] = "\n".join(buffer).strip()
            actual = encabezado
            buffer = []
        elif actual is not None:
            buffer.append(linea)
    if actual is not None:
        secciones[actual] = "\n".join(buffer).strip()
    return secciones


def compile_skill(
    instruccion: str,
    *,
    nombre: str,
    descripcion: str,
    herramientas: list[str],
    permisos: list[str],
    output_format: str,
) -> str:
    """Convierte una instrucción recurrente en un `SKILL.md` estructurado y válido.

    Devuelve el documento completo (frontmatter + cuerpo), listo para pasarse a
    `installer.parse_skill_md`/`parse_capabilities` o persistirse — mismo estándar que
    lee el instalador, con la estructura de seis secciones descrita en el docstring del
    módulo. La versión inicial es `1.0.0` (§209); súbela después con `bump_version`.
    """
    frontmatter = yaml.safe_dump(
        {
            "name": (nombre or "").strip(),
            "description": (descripcion or "").strip(),
            "version": _VERSION_INICIAL,
        },
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    ).strip()

    cuerpo = "\n\n".join(
        [
            _seccion("trigger", (instruccion or "").strip()),
            _seccion("inputs", (descripcion or "").strip()),
            _seccion("workflow", _WORKFLOW),
            _seccion("tools", _a_bullets(list(herramientas))),
            _seccion("permissions", _a_bullets(list(permisos))),
            _seccion("output", (output_format or "").strip()),
        ]
    )
    return f"---\n{frontmatter}\n---\n\n{cuerpo}\n"


def parse_compiled_skill(skill_md: str) -> dict:
    """Inverso de `compile_skill`: lee un `SKILL.md` compilado y devuelve sus secciones
    como `dict` con claves `nombre`, `descripcion`, `version`, `trigger`, `inputs`,
    `workflow`, `tools` (lista), `permissions` (lista) y `output`. Las secciones ausentes
    se degradan a `""`/`[]` en vez de lanzar — es un parseo permisivo, igual que
    `installer.parse_skill_md`."""
    nombre, descripcion, version, cuerpo = parse_skill_md(skill_md)
    secciones = _split_secciones(cuerpo)
    return {
        "nombre": nombre,
        "descripcion": descripcion,
        "version": version,
        "trigger": secciones.get("trigger", ""),
        "inputs": secciones.get("inputs", ""),
        "workflow": secciones.get("workflow", ""),
        "tools": _parsear_bullets(secciones.get("tools", "")),
        "permissions": _parsear_bullets(secciones.get("permissions", "")),
        "output": secciones.get("output", ""),
    }


def secciones_faltantes(skill_md: str) -> list[str]:
    """Nombres de sección requerida que FALTAN en el cuerpo del `SKILL.md`. `[]` significa
    que las seis secciones (`trigger`…`output`) están presentes — la verificación de
    estructura mínima del compilador (PHASE2.md §208)."""
    _, _, _, cuerpo = parse_skill_md(skill_md)
    presentes = _split_secciones(cuerpo)
    return [nombre for nombre in _SECCIONES if nombre not in presentes]


def bump_version(version: str | None) -> str:
    """Incrementa el parche de una versión semántica: `"1.0.0"` → `"1.0.1"`. Devuelve
    `"1.0.0"` si `version` es `None` o no es un semver `MAYOR.MENOR.PARCHE` — nunca se
    adivina cómo continuar una versión que no se entiende (mejor reiniciar la numeración
    que inventar un `2.0.0` o un sufijo raro)."""
    if not version:
        return _VERSION_INICIAL
    match = _SEMVER_RE.match(str(version).strip())
    if match is None:
        return _VERSION_INICIAL
    mayor, menor, parche = (int(parte) for parte in match.groups())
    return f"{mayor}.{menor}.{parche + 1}"


__all__ = [
    "bump_version",
    "compile_skill",
    "parse_compiled_skill",
    "secciones_faltantes",
]