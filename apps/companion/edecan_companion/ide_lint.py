"""Autorreparación de lint: primero el linter, y al modelo solo si hace falta.

El error de lint es el único error de programación que casi siempre trae su
propia solución adentro. ``ruff check --fix`` y ``eslint --fix`` arreglan la
mayoría de lo que marcan en milisegundos, gratis y sin equivocarse. Pedirle a
un modelo que borre un import sin usar es pagar segundos y tokens por algo que
el propio linter ya sabe hacer mejor. Por eso el orden de este módulo no es
negociable:

1. detectar qué linter usa el proyecto (mirando el repo, no preguntando);
2. correrlo para saber QUÉ marcó -- esa lista es, además, el contrato de
   alcance del arreglo;
3. aplicar el arreglo automático del propio linter;
4. volver a revisar; si ya quedó limpio, **el modelo nunca se entera**;
5. solo con lo que sobrevive a ``--fix`` se entra al bucle de
   ``ide_verificacion.ejecutar_hasta_que_pase``, que es quien ya sabe
   ejecutar -> leer el error -> arreglar -> repetir, y quien ya sabe frenar
   cuando el mismo error se repite sin avanzar.

Este módulo NO reimplementa ese bucle ni el detector de "estoy atascado":
importa ``ide_verificacion`` y le pasa el comando de revisión del linter.

Alcance: lo que esta sesión tocó, y de eso solo lo que el linter marcó
----------------------------------------------------------------------
Un arreglo de lint que "de paso" reordena imports, renombra variables o
reformatea archivos que nadie tocó es exactamente lo que hace que nadie
confíe en la reparación automática. Correr ``ruff check --fix`` sobre un
monorepo de miles de archivos porque el agente editó tres es esa misma
traición, solo que a escala. Cuatro decisiones protegen esto:

- **Sin alcance no hay arreglo.** ``rutas`` es lo que la sesión editó. Si no
  viene, se deriva de lo que git ve cambiado en el working tree, que es la
  misma pregunta escrita en otro lado. Si tampoco se puede (no es un repo,
  git no está, hay demasiados archivos cambiados para que eso sea "una
  sesión"), esto se **niega** y lo dice: estado ``"sin_alcance"``, cero
  bytes tocados. Arreglar el repositorio entero sigue siendo posible, pero
  hay que pedirlo con todas las letras (``todo_el_proyecto=True``).
- Se invocan solo los sub-comandos de LINT, nunca los de formato:
  ``ruff check --fix`` (jamás ``ruff format``), ``biome lint`` (jamás
  ``biome check``, que además corre el formateador). Y ``ruff check --fix``
  aplica únicamente los arreglos que ruff considera seguros -- no se pasa
  ``--unsafe-fixes``.
- Se saca una foto (hash por archivo) **de todo el proyecto**, no solo del
  alcance, antes de tocar nada, y se compara al final. Fotografiar solo el
  alcance haría invisible justo lo que hay que cazar: la edición de al lado.
- Un archivo modificado está en regla si estaba dentro del alcance declarado
  Y el linter lo había señalado. Cualquier otro sale en
  ``archivos_fuera_de_alcance``. No se revierte solo -- eso lo decide quien
  integra, con el diff a la vista -- pero deja de ser invisible. Y
  ``alcance_respetado`` es True solo cuando de verdad se pudo mirar: si la
  foto no se pudo sacar, la respuesta honesta es "no lo sé", no "todo bien".

Un linter que no está instalado no es culpa de nadie
----------------------------------------------------
Si el proyecto configura eslint pero ``node_modules`` no existe, esto no es
un error del usuario ni una excepción: es un estado con nombre
(``"linter_no_instalado"``) y un mensaje que dice qué instalar. A propósito
NO se cae a ``npx``: ``npx`` descargaría el linter de la red sin permiso, y
descargar cosas en la máquina de alguien no es algo que este módulo pueda
decidir solo.

Integración
-----------
Autónomo, como ``ide_verificacion.py`` y ``ide_costos.py``: no importa
``ide_sessions.py`` ni ``ide_workers_agent.py``. Quien lo cablee expone
``reparar_lint`` (o ``reparar_todo_el_lint``) como herramienta del agente y
pasa un ``arreglar`` que le dé ``SolicitudDeArreglo.instrucciones`` al modelo.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Literal

from edecan_companion.ide_verificacion import (
    ResultadoIntento,
    ejecutar_hasta_que_pase,
    extraer_resumen,
)

# Un linter es rápido por definición; si pasa de esto, o el proyecto es
# gigante o algo se colgó. Más corto que el timeout de verificación (300s)
# porque acá no se está compilando ni corriendo pruebas.
TIMEOUT_LINTER_SEGUNDOS = 180

# Cuántas veces se le puede pedir al modelo que arregle lo que `--fix` no
# pudo. Tres es generoso: si en tres vueltas no resolvió un problema de lint
# (que es mecánico por naturaleza), el problema no era mecánico y lo tiene
# que ver una persona.
MAX_INTENTOS_DEL_MODELO = 3

# Tope de archivos de la foto previa. Hashear medio millón de archivos para
# verificar el alcance costaría más que el arreglo entero; pasado el tope se
# informa con honestidad que el alcance no se verificó, en vez de mentir.
MAX_ARCHIVOS_FOTOGRAFIA = 8000

# Cuántos archivos cambiados puede tener el working tree para que derivar el
# alcance de ahí siga siendo razonable. Pasado el tope no estamos ante "lo que
# el agente acaba de editar" sino ante un merge, un formateo masivo o un
# `git checkout` de otra rama; meter todo eso en la línea de comando del
# linter, además, la haría estallar. Se pide el alcance explícito.
MAX_ARCHIVOS_DEL_ALCANCE = 500

# git status sobre un repo grande tarda, pero no minutos. Si tarda más que
# esto, algo está bloqueado (un index.lock, un filesystem de red) y es mejor
# quedarse sin alcance derivado que colgar la reparación entera.
TIMEOUT_GIT_SEGUNDOS = 30

# Hasta cuántos niveles hacia arriba se busca `node_modules/.bin` o `.venv/bin`.
# En un monorepo el binario vive en la raíz, no junto al paquete que se lintea.
NIVELES_BUSQUEDA_HACIA_ARRIBA = 5

# Carpetas que nunca entran en la foto de alcance: caché, dependencias y
# metadatos de VCS. Los directorios ocultos en general SÍ entran a propósito
# (`.storybook/`, `.config/` tienen fuentes reales que eslint sí lintea): una
# omisión ahí volvería invisible justo la edición fuera de alcance que este
# módulo existe para delatar.
_CARPETAS_IGNORADAS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".gradle",
        ".next",
        ".turbo",
        "dist",
        "build",
        "target",
        "vendor",
        "Pods",
        "DerivedData",
    }
)

Severidad = Literal["error", "aviso"]

Estado = Literal[
    "sin_linter",
    "linter_no_instalado",
    "sin_alcance",
    "el_linter_fallo",
    "sin_problemas",
    "arreglado_por_el_linter",
    "arreglado_con_ayuda_del_modelo",
    "quedan_problemas",
]

# De dónde salió el permiso para tocar archivos: lo pidió el llamador
# (``rutas``), lo dijo git (working tree), lo autorizó explícitamente para
# todo el repo (``todo_el_proyecto``), o no hubo forma de saberlo.
OrigenDelAlcance = Literal["rutas", "git", "proyecto", "ninguno"]


class IDELintError(ValueError):
    """Uso inválido de este módulo (no un problema de lint del proyecto)."""


# --------------------------------------------------------------------- #
# Problemas y su lectura desde la salida del linter.
# --------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ProblemaLint:
    """Una marca del linter, normalizada sin importar de qué linter vino."""

    archivo: str
    linea: int | None
    columna: int | None
    codigo: str | None
    mensaje: str
    severidad: Severidad

    def como_linea(self) -> str:
        posicion = self.archivo
        if self.linea is not None:
            posicion += f":{self.linea}"
            if self.columna is not None:
                posicion += f":{self.columna}"
        codigo = f" {self.codigo}" if self.codigo else ""
        return f"{posicion}:{codigo} {self.mensaje}".strip()

    def resumen(self) -> dict[str, Any]:
        return {
            "archivo": self.archivo,
            "linea": self.linea,
            "columna": self.columna,
            "codigo": self.codigo,
            "mensaje": self.mensaje,
            "severidad": self.severidad,
        }


# "archivo.ext:12:5: resto" -- el formato que comparten ruff (concise), flake8,
# swiftlint, ktlint y golangci-lint. Se exige extensión en el nombre del
# archivo (igual que hace `ide_verificacion`) porque sin eso cualquier línea
# con dos puntos y un número se colaría como si fuera un problema.
_PATRON_LINEA_LINT = re.compile(
    r"^(?P<archivo>[^\s:][^:\n]*\.[A-Za-z0-9]{1,6}):(?P<linea>\d+)"
    r"(?::(?P<columna>\d+))?:?\s+(?P<resto>\S.*)$",
    re.MULTILINE,
)
# Código de regla al principio ("F401", "E501", "no-unused-vars") o al final:
# entre paréntesis como lo imprimen swiftlint y golangci-lint, o entre
# corchetes con la severidad delante como lo imprime `eslint --format unix`.
_PATRON_CODIGO_AL_INICIO = re.compile(r"^(?P<codigo>[A-Z]{1,5}\d{2,4}|[a-z][\w/-]*[\w])\s+(?=\S)")
_PATRON_CODIGO_AL_FINAL = re.compile(
    r"(?:\((?P<parentesis>[\w./-]+)\)|\[(?:Error|Warning)/(?P<corchete>[\w./@-]+)\])\s*$"
)
_PATRON_SEVERIDAD = re.compile(r"\b(?P<sev>error|warning|warn|aviso)\b\s*:?", re.IGNORECASE)


def _normalizar_ruta(valor: str, raiz: Path) -> str:
    """Toda ruta se guarda relativa a la raíz del proyecto y en formato posix.

    Los linters no se ponen de acuerdo: eslint imprime rutas absolutas, ruff
    las imprime relativas al cwd. Sin normalizar, comparar "lo que el linter
    marcó" contra "lo que se modificó" daría falsos positivos de alcance.
    """
    try:
        ruta = Path(valor)
        absoluta = ruta if ruta.is_absolute() else (raiz / ruta)
        return absoluta.resolve().relative_to(raiz.resolve()).as_posix()
    except (ValueError, OSError):
        return os.path.normpath(valor)


def _severidad_de(texto: str) -> Severidad:
    match = _PATRON_SEVERIDAD.search(texto)
    if match and match.group("sev").casefold() in {"warning", "warn", "aviso"}:
        return "aviso"
    return "error"


def _parsear_texto(salida: str, raiz: Path) -> tuple[ProblemaLint, ...]:
    """Lee el formato "archivo:línea:columna: mensaje" común a casi todo linter."""
    problemas: list[ProblemaLint] = []
    for match in _PATRON_LINEA_LINT.finditer(salida):
        resto = match.group("resto").strip()
        codigo = None
        # El código del FINAL se prueba primero: cuando está, es inequívoco
        # ("(line_length)", "(ineffassign)", "[Warning/jsx-a11y/alt-text]").
        # El del principio es una heurística sobre la primera palabra, y con
        # esos formatos se quedaba con "ineffectual" o "img" en vez de la regla.
        match_final = _PATRON_CODIGO_AL_FINAL.search(resto)
        if match_final:
            codigo = match_final.group("parentesis") or match_final.group("corchete")
        else:
            match_codigo = _PATRON_CODIGO_AL_INICIO.match(resto)
            if match_codigo:
                codigo = match_codigo.group("codigo")
                resto = resto[match_codigo.end() :].strip()
        problemas.append(
            ProblemaLint(
                archivo=_normalizar_ruta(match.group("archivo"), raiz),
                linea=int(match.group("linea")),
                columna=int(match.group("columna")) if match.group("columna") else None,
                codigo=codigo,
                mensaje=resto,
                severidad=_severidad_de(resto),
            )
        )
    return tuple(problemas)


def _parsear_ruff_json(salida: str, raiz: Path) -> tuple[ProblemaLint, ...] | None:
    """``None`` significa "esto no era JSON de ruff", no "no hay problemas"."""
    try:
        datos = json.loads(salida or "[]")
    except json.JSONDecodeError:
        return None
    if not isinstance(datos, list):
        return None
    problemas: list[ProblemaLint] = []
    for item in datos:
        if not isinstance(item, dict) or "filename" not in item:
            return None
        ubicacion = item.get("location") if isinstance(item.get("location"), dict) else {}
        problemas.append(
            ProblemaLint(
                archivo=_normalizar_ruta(str(item["filename"]), raiz),
                linea=_entero(ubicacion.get("row")),
                columna=_entero(ubicacion.get("column")),
                codigo=str(item["code"]) if item.get("code") else None,
                mensaje=str(item.get("message") or "").strip(),
                # ruff no gradúa severidad: todo lo que reporta es una
                # violación de una regla que el proyecto activó.
                severidad="error",
            )
        )
    return tuple(problemas)


def _parsear_eslint_json(salida: str, raiz: Path) -> tuple[ProblemaLint, ...] | None:
    try:
        datos = json.loads(salida or "[]")
    except json.JSONDecodeError:
        return None
    if not isinstance(datos, list):
        return None
    problemas: list[ProblemaLint] = []
    for archivo_reportado in datos:
        if not isinstance(archivo_reportado, dict) or "filePath" not in archivo_reportado:
            return None
        archivo = _normalizar_ruta(str(archivo_reportado["filePath"]), raiz)
        mensajes = archivo_reportado.get("messages")
        for mensaje in mensajes if isinstance(mensajes, list) else []:
            if not isinstance(mensaje, dict):
                continue
            problemas.append(
                ProblemaLint(
                    archivo=archivo,
                    linea=_entero(mensaje.get("line")),
                    columna=_entero(mensaje.get("column")),
                    codigo=str(mensaje["ruleId"]) if mensaje.get("ruleId") else None,
                    mensaje=str(mensaje.get("message") or "").strip(),
                    severidad="error" if mensaje.get("severity") == 2 else "aviso",
                )
            )
    return tuple(problemas)


def _entero(valor: Any) -> int | None:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


_PARSERS: dict[str, Callable[[str, Path], tuple[ProblemaLint, ...] | None]] = {
    "ruff_json": _parsear_ruff_json,
    "eslint_json": _parsear_eslint_json,
    "texto": _parsear_texto,
}


# --------------------------------------------------------------------- #
# Catálogo de linters: cómo se detecta cada uno y cómo se lo invoca.
# --------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Invocacion:
    """Una forma de pedirle a un linter que revise, y cómo leer su salida."""

    argv: tuple[str, ...]
    formato: str


def _leer(ruta: Path) -> str:
    try:
        return ruta.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _package_json(raiz: Path) -> dict[str, Any]:
    try:
        datos = json.loads((raiz / "package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return datos if isinstance(datos, dict) else {}


def _dependencias_declaradas(raiz: Path) -> set[str]:
    datos = _package_json(raiz)
    nombres: set[str] = set()
    for clave in ("dependencies", "devDependencies", "peerDependencies"):
        bloque = datos.get(clave)
        if isinstance(bloque, dict):
            nombres.update(bloque)
    return nombres


def _primer_archivo(raiz: Path, nombres: Sequence[str]) -> str | None:
    for nombre in nombres:
        if (raiz / nombre).is_file():
            return nombre
    return None


def _detectar_ruff(raiz: Path) -> str | None:
    encontrado = _primer_archivo(raiz, ("ruff.toml", ".ruff.toml"))
    if encontrado:
        return encontrado
    pyproject = raiz / "pyproject.toml"
    if pyproject.is_file() and "ruff" in _leer(pyproject):
        return "pyproject.toml"
    return None


def _detectar_flake8(raiz: Path) -> str | None:
    if (raiz / ".flake8").is_file():
        return ".flake8"
    for nombre in ("setup.cfg", "tox.ini"):
        ruta = raiz / nombre
        if ruta.is_file() and "[flake8]" in _leer(ruta):
            return nombre
    return None


_CONFIGS_ESLINT = (
    "eslint.config.js",
    "eslint.config.mjs",
    "eslint.config.cjs",
    "eslint.config.ts",
    "eslint.config.mts",
    ".eslintrc",
    ".eslintrc.js",
    ".eslintrc.cjs",
    ".eslintrc.mjs",
    ".eslintrc.json",
    ".eslintrc.yml",
    ".eslintrc.yaml",
)


def _detectar_eslint(raiz: Path) -> str | None:
    encontrado = _primer_archivo(raiz, _CONFIGS_ESLINT)
    if encontrado:
        return encontrado
    datos = _package_json(raiz)
    if isinstance(datos.get("eslintConfig"), dict):
        return "package.json (eslintConfig)"
    if "eslint" in _dependencias_declaradas(raiz):
        return "package.json (dependencia eslint)"
    return None


def _detectar_biome(raiz: Path) -> str | None:
    encontrado = _primer_archivo(raiz, ("biome.json", "biome.jsonc"))
    if encontrado:
        return encontrado
    if "@biomejs/biome" in _dependencias_declaradas(raiz):
        return "package.json (dependencia @biomejs/biome)"
    return None


def _detectar_swiftlint(raiz: Path) -> str | None:
    return _primer_archivo(raiz, (".swiftlint.yml", ".swiftlint.yaml"))


def _detectar_ktlint(raiz: Path) -> str | None:
    for nombre in ("build.gradle.kts", "build.gradle", ".editorconfig"):
        ruta = raiz / nombre
        if ruta.is_file() and "ktlint" in _leer(ruta):
            return nombre
    return None


def _detectar_golangci(raiz: Path) -> str | None:
    return _primer_archivo(
        raiz, (".golangci.yml", ".golangci.yaml", ".golangci.toml", ".golangci.json")
    )


_EXTENSIONES_PYTHON = frozenset({".py", ".pyi"})
_EXTENSIONES_JS = frozenset(
    {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".vue", ".svelte"}
)


@dataclass(frozen=True, slots=True)
class _DefinicionLinter:
    nombre: str
    binario: str
    # Rutas relativas donde suele vivir el binario dentro del propio proyecto,
    # probadas antes que el PATH: el linter del proyecto manda sobre el que
    # alguien tenga instalado globalmente, que puede ser de otra versión.
    busqueda_local: tuple[str, ...]
    extensiones: frozenset[str]
    variantes_revisar: tuple[_Invocacion, ...]
    # Invocación sin banderas de formato, la que se usa dentro del bucle del
    # modelo: cero riesgo de versión y su salida "para humanos" es justamente
    # el mejor texto posible para un prompt.
    argv_texto: tuple[str, ...]
    variantes_arreglar: tuple[tuple[str, ...], ...]
    como_instalar: str
    # Qué revisar cuando el llamador no pasa `rutas`. La mayoría de los
    # linters ya asume el directorio actual, pero eslint < 9 NO: sin patrón de
    # archivos no lintea nada y sale con código 0, o sea un "todo limpio"
    # falso, que es la peor forma posible de fallar para esta pieza.
    objetivo_si_no_hay_rutas: tuple[str, ...] = ()


_CATALOGO: tuple[_DefinicionLinter, ...] = (
    _DefinicionLinter(
        nombre="ruff",
        binario="ruff",
        busqueda_local=(".venv/bin/ruff", "venv/bin/ruff", ".venv/Scripts/ruff.exe"),
        extensiones=_EXTENSIONES_PYTHON,
        variantes_revisar=(
            _Invocacion(("check", "--output-format", "json"), "ruff_json"),
            # Ruff viejo usaba `--format`; si la bandera moderna no existe,
            # esta variante todavía da JSON en vez de forzar el modo texto.
            _Invocacion(("check", "--format", "json"), "ruff_json"),
            _Invocacion(("check",), "texto"),
        ),
        argv_texto=("check",),
        # `--fix` sin `--unsafe-fixes` a propósito: los arreglos inseguros de
        # ruff pueden cambiar el comportamiento del código, y este módulo
        # promete un cambio mecánico. `ruff format` queda fuera por lo mismo.
        variantes_arreglar=(("check", "--fix"),),
        como_instalar="Instálalo con `uv pip install ruff` (o `pipx install ruff`).",
    ),
    _DefinicionLinter(
        nombre="flake8",
        binario="flake8",
        busqueda_local=(".venv/bin/flake8", "venv/bin/flake8"),
        extensiones=_EXTENSIONES_PYTHON,
        variantes_revisar=(_Invocacion((), "texto"),),
        argv_texto=(),
        # flake8 no arregla nada: acá el modelo no es un desperdicio, es la
        # única opción.
        variantes_arreglar=(),
        como_instalar="Instálalo con `uv pip install flake8`.",
    ),
    _DefinicionLinter(
        nombre="eslint",
        binario="eslint",
        busqueda_local=("node_modules/.bin/eslint",),
        extensiones=_EXTENSIONES_JS,
        variantes_revisar=(_Invocacion(("--format", "json"), "eslint_json"),),
        # `unix` en vez del formato bonito por defecto porque imprime
        # "archivo:línea:columna: mensaje", que es lo único que el parser de
        # texto sabe leer; con el formato `stylish` el bucle le entregaba al
        # modelo un bloque del que no salía ni un archivo ni una línea.
        # `--max-warnings 0` porque este módulo ya cuenta los avisos como
        # problemas (bloquean `InformeLint.limpio`): sin la bandera, eslint
        # sale con código 0, el bucle da la vuelta por buena y el modelo NUNCA
        # llega a ver un aviso -- justo el caso de `next/core-web-vitals`,
        # donde casi todas las reglas son avisos.
        argv_texto=("--format", "unix", "--max-warnings", "0"),
        variantes_arreglar=(("--fix",),),
        como_instalar="Instálalo con `npm install` en la raíz del proyecto.",
        objetivo_si_no_hay_rutas=(".",),
    ),
    _DefinicionLinter(
        nombre="biome",
        binario="biome",
        busqueda_local=("node_modules/.bin/biome",),
        extensiones=_EXTENSIONES_JS,
        # `biome lint`, NUNCA `biome check`: `check` corre también el
        # formateador y devolvería un diff enorme donde se pidió un arreglo
        # puntual de lint. Se lee su salida de texto y no su reporter JSON
        # porque la forma de ese JSON cambió entre versiones mayores, mientras
        # que las líneas "archivo:línea:columna regla" siguen igual.
        variantes_revisar=(_Invocacion(("lint",), "texto"),),
        argv_texto=("lint",),
        # Biome 2 usa `--write`; Biome 1 usaba `--apply`. Se prueban en ese
        # orden y la segunda solo entra si la primera no existe.
        variantes_arreglar=(("lint", "--write"), ("lint", "--apply")),
        como_instalar="Instálalo con `npm install` en la raíz del proyecto.",
        # Biome pide un objetivo explícito: `biome lint` a secas no revisa nada.
        objetivo_si_no_hay_rutas=(".",),
    ),
    _DefinicionLinter(
        nombre="swiftlint",
        binario="swiftlint",
        busqueda_local=(),
        extensiones=frozenset({".swift"}),
        variantes_revisar=(_Invocacion(("lint", "--quiet"), "texto"),),
        argv_texto=("lint", "--quiet"),
        variantes_arreglar=(("--fix", "--quiet"), ("autocorrect", "--quiet")),
        como_instalar="Instálalo con `brew install swiftlint`.",
    ),
    _DefinicionLinter(
        nombre="ktlint",
        binario="ktlint",
        busqueda_local=(),
        extensiones=frozenset({".kt", ".kts"}),
        variantes_revisar=(_Invocacion((), "texto"),),
        argv_texto=(),
        variantes_arreglar=(("--format",),),
        como_instalar="Instálalo con `brew install ktlint`.",
    ),
    _DefinicionLinter(
        nombre="golangci-lint",
        binario="golangci-lint",
        busqueda_local=(),
        extensiones=frozenset({".go"}),
        variantes_revisar=(_Invocacion(("run",), "texto"),),
        argv_texto=("run",),
        variantes_arreglar=(("run", "--fix"),),
        como_instalar="Instálalo con `brew install golangci-lint`.",
    ),
)


@dataclass(frozen=True, slots=True)
class LinterDetectado:
    """Un linter que este proyecto sí usa, y si además se puede ejecutar."""

    nombre: str
    evidencia: str
    extensiones: frozenset[str]
    ejecutable: str | None
    motivo_no_disponible: str | None

    @property
    def disponible(self) -> bool:
        return self.ejecutable is not None

    @property
    def arregla_solo(self) -> bool:
        return bool(_definicion(self.nombre).variantes_arreglar)

    def resumen(self) -> dict[str, Any]:
        return {
            "nombre": self.nombre,
            "evidencia": self.evidencia,
            "extensiones": sorted(self.extensiones),
            "disponible": self.disponible,
            "ejecutable": self.ejecutable,
            "motivo_no_disponible": self.motivo_no_disponible,
            "arregla_solo": self.arregla_solo,
        }


_DETECTORES: dict[str, Callable[[Path], str | None]] = {
    "ruff": _detectar_ruff,
    "flake8": _detectar_flake8,
    "eslint": _detectar_eslint,
    "biome": _detectar_biome,
    "swiftlint": _detectar_swiftlint,
    "ktlint": _detectar_ktlint,
    "golangci-lint": _detectar_golangci,
}


def _definicion(nombre: str) -> _DefinicionLinter:
    for definicion in _CATALOGO:
        if definicion.nombre == nombre:
            return definicion
    raise IDELintError(f"Linter desconocido: {nombre!r}.")


def _detectar_subiendo(nombre: str, raiz: Path) -> str | None:
    """Busca la configuración del linter también en los directorios de arriba.

    En un monorepo la config vive en la raíz y el paquete que se está
    editando no repite nada: ``apps/companion`` de este mismo repo no
    menciona ruff en su ``pyproject.toml``, pero el workspace entero lo usa.
    Mirar solo el directorio pedido daba "sin linter" justo en los
    directorios donde más se trabaja. Los linters resuelven su configuración
    subiendo igual que esto, y ``_resolver_ejecutable`` ya subía para el
    binario; faltaba la otra mitad.

    La evidencia devuelta dice desde dónde se subió (``../pyproject.toml``)
    para que quien lea el informe sepa qué archivo manda de verdad.
    """
    detector = _DETECTORES[nombre]
    directorio = raiz.resolve()
    for salto in range(NIVELES_BUSQUEDA_HACIA_ARRIBA):
        evidencia = detector(directorio)
        if evidencia:
            return evidencia if salto == 0 else f"{'../' * salto}{evidencia}"
        if directorio.parent == directorio:
            break
        directorio = directorio.parent
    return None


def _resolver_ejecutable(definicion: _DefinicionLinter, raiz: Path) -> str | None:
    """Binario del proyecto primero, PATH después.

    Se sube por los directorios padre porque en un monorepo el
    ``node_modules/.bin`` está en la raíz del repo, no junto al paquete que se
    está linteando.
    """
    directorio = raiz.resolve()
    for _ in range(NIVELES_BUSQUEDA_HACIA_ARRIBA):
        for relativa in definicion.busqueda_local:
            candidato = directorio / relativa
            if candidato.is_file() and os.access(candidato, os.X_OK):
                return str(candidato)
        if directorio.parent == directorio:
            break
        directorio = directorio.parent
    return shutil.which(definicion.binario)


def detectar_linters(
    directorio: str | Path, *, rutas: Sequence[str] = ()
) -> tuple[LinterDetectado, ...]:
    """Qué linters usa este proyecto, según lo que hay escrito en el repo.

    Nunca inventa un linter "por defecto": si no hay configuración ni
    dependencia declarada, no hay linter, y eso se dice tal cual. Cuando
    ``rutas`` viene con archivos concretos, se descartan los linters cuyo
    lenguaje no aparece ahí -- correr eslint porque se editó un ``.py`` es
    ruido, no ayuda.
    """
    raiz = Path(directorio)
    extensiones_pedidas = {Path(ruta).suffix for ruta in rutas if Path(ruta).suffix}
    detectados: list[LinterDetectado] = []
    for definicion in _CATALOGO:
        evidencia = _detectar_subiendo(definicion.nombre, raiz)
        if not evidencia:
            continue
        if extensiones_pedidas and not (extensiones_pedidas & definicion.extensiones):
            continue
        ejecutable = _resolver_ejecutable(definicion, raiz)
        motivo = None
        if ejecutable is None:
            motivo = (
                f"El proyecto usa {definicion.nombre} (lo declara {evidencia}), pero el "
                f"binario no está: no aparece en el proyecto ni en el PATH. "
                f"{definicion.como_instalar} No se descarga nada solo."
            )
        detectados.append(
            LinterDetectado(
                nombre=definicion.nombre,
                evidencia=evidencia,
                extensiones=definicion.extensiones,
                ejecutable=ejecutable,
                motivo_no_disponible=motivo,
            )
        )
    return tuple(detectados)


# --------------------------------------------------------------------- #
# Ejecución del linter.
# --------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Salida:
    exit_code: int | None
    stdout: str
    stderr: str
    error: str | None


def _objetivos(definicion: _DefinicionLinter, rutas: Sequence[str]) -> tuple[str, ...]:
    """Qué se le pone al final de la línea de comando: las rutas, o el respaldo.

    Nunca se deja la invocación sin objetivo cuando el linter necesita uno:
    "no revisé nada" y "no hay nada que revisar" se ven idénticos desde afuera
    (código 0, salida vacía) y confundirlos convierte a este módulo en una
    máquina de dar vistos buenos falsos.
    """
    return tuple(rutas) if rutas else definicion.objetivo_si_no_hay_rutas


def _correr(argv: Sequence[str], *, cwd: Path, timeout_segundos: int) -> _Salida:
    """Sin shell y con el ejecutable ya resuelto, igual que ``ide_verificacion``."""
    try:
        proceso = subprocess.run(
            list(argv),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_segundos,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _Salida(None, "", "", f"El linter superó el límite de {timeout_segundos}s.")
    except OSError as exc:
        return _Salida(None, "", "", f"No se pudo lanzar el linter: {exc}")
    return _Salida(proceso.returncode, proceso.stdout or "", proceso.stderr or "", None)


@dataclass(frozen=True, slots=True)
class InformeLint:
    """Lo que el linter marcó en una pasada, ya normalizado."""

    linter: str
    argv: tuple[str, ...]
    exit_code: int | None
    problemas: tuple[ProblemaLint, ...]
    salida_recortada: str
    error_de_ejecucion: str | None

    @property
    def limpio(self) -> bool:
        return self.error_de_ejecucion is None and self.exit_code == 0 and not self.problemas

    @property
    def archivos(self) -> tuple[str, ...]:
        vistos: list[str] = []
        for problema in self.problemas:
            if problema.archivo not in vistos:
                vistos.append(problema.archivo)
        return tuple(vistos)

    def resumen(self) -> dict[str, Any]:
        return {
            "linter": self.linter,
            "argv": list(self.argv),
            "exit_code": self.exit_code,
            "limpio": self.limpio,
            "total_problemas": len(self.problemas),
            "problemas": [problema.resumen() for problema in self.problemas],
            "archivos": list(self.archivos),
            "salida_recortada": self.salida_recortada,
            "error_de_ejecucion": self.error_de_ejecucion,
        }


def _revisar_con(
    linter: LinterDetectado,
    *,
    raiz: Path,
    rutas: Sequence[str],
    timeout_segundos: int,
) -> InformeLint:
    definicion = _definicion(linter.nombre)
    assert linter.ejecutable is not None  # el llamador ya verificó disponibilidad
    objetivos = _objetivos(definicion, rutas)
    ultima: _Salida | None = None
    argv_usado: tuple[str, ...] = ()
    for indice, variante in enumerate(definicion.variantes_revisar):
        argv_usado = (linter.ejecutable, *variante.argv, *objetivos)
        ultima = _correr(argv_usado, cwd=raiz, timeout_segundos=timeout_segundos)
        if ultima.error is not None:
            break
        quedan_variantes = indice + 1 < len(definicion.variantes_revisar)
        # Dos formas de descubrir que esta versión del linter no entiende la
        # bandera que se le pasó: que lo diga (mensaje de uso) o que se haya
        # pedido JSON y no haya llegado JSON. En ambas se prueba la variante
        # siguiente en vez de dar por perdida la revisión entera.
        if quedan_variantes and _parece_bandera_no_soportada(ultima):
            continue
        problemas = _PARSERS[variante.formato](ultima.stdout, raiz)
        if problemas is None:
            if quedan_variantes:
                continue
            problemas = ()
        if not problemas and variante.formato == "texto":
            # El formato texto también vive en stderr en varios linters.
            problemas = _parsear_texto(ultima.stderr, raiz)
        return _armar_informe(
            linter.nombre,
            argv_usado,
            ultima,
            problemas,
            estructurado=variante.formato != "texto",
        )

    if ultima is None:  # pragma: no cover - el catálogo nunca trae 0 variantes
        raise IDELintError(f"{linter.nombre} no tiene ninguna forma de revisión definida.")
    return _armar_informe(linter.nombre, argv_usado, ultima, ())


def _armar_informe(
    nombre: str,
    argv: tuple[str, ...],
    salida: _Salida,
    problemas: tuple[ProblemaLint, ...],
    *,
    estructurado: bool = False,
) -> InformeLint:
    resumen = extraer_resumen(salida.stdout, salida.stderr)
    error = salida.error
    if error is None and not problemas and salida.exit_code not in (0, None):
        # Salió mal y no se pudo leer ni un problema: no es "el código está
        # sucio", es "el linter no pudo trabajar" (configuración rota, versión
        # incompatible). Confundir los dos casos manda al modelo a arreglar
        # código que no tiene nada malo.
        error = resumen.texto or f"El linter terminó con código {salida.exit_code}."
    return InformeLint(
        linter=nombre,
        argv=argv,
        exit_code=salida.exit_code,
        problemas=problemas,
        # Con los problemas ya estructurados (JSON), arrastrar además el
        # volcado crudo solo mete kilobytes de ruido en el payload que viaja
        # a la UI y al prompt. Se guarda cuando es lo único que hay.
        salida_recortada="" if (estructurado and problemas) else resumen.texto,
        error_de_ejecucion=error,
    )


def revisar_lint(
    directorio: str | Path,
    *,
    rutas: Sequence[str] = (),
    linter: str | None = None,
    timeout_segundos: int = TIMEOUT_LINTER_SEGUNDOS,
) -> InformeLint | None:
    """Corre el linter en modo lectura: nunca modifica un archivo.

    Devuelve ``None`` si el proyecto no tiene linter detectable o si el que
    tiene no está instalado -- para saber cuál de los dos casos es, mira
    ``detectar_linters``.
    """
    raiz = Path(directorio)
    elegido = _elegir(raiz, rutas=rutas, nombre=linter)
    if elegido is None or not elegido.disponible:
        return None
    return _revisar_con(elegido, raiz=raiz, rutas=rutas, timeout_segundos=timeout_segundos)


def _elegir(
    raiz: Path, *, rutas: Sequence[str], nombre: str | None
) -> LinterDetectado | None:
    detectados = detectar_linters(raiz, rutas=rutas)
    if nombre is not None:
        for candidato in detectados:
            if candidato.nombre == nombre:
                return candidato
        return None
    for candidato in detectados:
        if candidato.disponible:
            return candidato
    return detectados[0] if detectados else None


# --------------------------------------------------------------------- #
# Alcance de la sesión: qué archivos tiene permiso de tocar este arreglo.
# --------------------------------------------------------------------- #


def _archivos_cambiados_en_git(raiz: Path, *, timeout_segundos: int) -> tuple[str, ...] | None:
    """Lo que el working tree tiene distinto: staged, sin stagear y sin trackear.

    Es la mejor respuesta disponible a "qué tocó esta sesión" cuando el
    llamador no la dio: son literalmente los archivos que todavía no están en
    ningún commit tal como están en disco. No se miran commits: lo ya
    commiteado es trabajo cerrado, no algo que este arreglo deba reabrir.

    ``None`` significa "no se pudo saber" (no hay git, no es un repo, git
    falló), nunca "no cambió nada" -- confundir los dos casos es justo lo que
    convertiría esto en un arreglo del proyecto entero disfrazado.

    No se reusa ``ide_git.GitService`` a propósito: esa clase trabaja contra un
    ``WorkspaceStore`` y un ``workspace_id`` autorizado, y este módulo recibe
    un directorio suelto (es autónomo, como ``ide_verificacion``). Traerse el
    store para leer un ``status`` acoplaría la pieza entera a la sesión.
    """
    git = shutil.which("git")
    if git is None:
        return None
    raiz_resuelta = raiz.resolve()
    # El porcelain imprime rutas relativas a la RAÍZ DEL REPO, no al cwd: sin
    # el toplevel, un subdirectorio de un monorepo derivaría rutas que no
    # existen desde donde se va a correr el linter.
    cabeza = _correr(
        (git, "-C", str(raiz_resuelta), "rev-parse", "--show-toplevel"),
        cwd=raiz_resuelta,
        timeout_segundos=timeout_segundos,
    )
    if cabeza.error is not None or cabeza.exit_code != 0 or not cabeza.stdout.strip():
        return None
    toplevel = Path(cabeza.stdout.strip())
    salida = _correr(
        (
            git,
            "-C",
            str(raiz_resuelta),
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            # Limita el recorrido al subárbol pedido: en un monorepo, lo que
            # se editó en otro paquete no es alcance de este arreglo.
            "--",
            ".",
        ),
        cwd=raiz_resuelta,
        timeout_segundos=timeout_segundos,
    )
    if salida.error is not None or salida.exit_code != 0:
        return None
    cambiados: list[str] = []
    for ruta in _rutas_del_porcelain(salida.stdout):
        absoluta = (toplevel / ruta).resolve()
        try:
            relativa = absoluta.relative_to(raiz_resuelta).as_posix()
        except ValueError:
            continue
        # Un archivo borrado sigue apareciendo en `status` pero pasarlo al
        # linter lo haría fallar por ruta inexistente.
        if absoluta.is_file() and relativa not in cambiados:
            cambiados.append(relativa)
    return tuple(cambiados)


def _rutas_del_porcelain(salida: str) -> list[str]:
    """Lee ``git status --porcelain=v1 -z``: registros ``XY ruta`` separados por NUL."""
    registros = salida.split("\0")
    rutas: list[str] = []
    indice = 0
    while indice < len(registros):
        registro = registros[indice]
        indice += 1
        if len(registro) < 4:
            continue
        if registro[0] in {"R", "C"}:
            # Un renombrado/copiado gasta un registro extra con la ruta
            # ORIGINAL, que ya no existe en disco: se salta.
            indice += 1
        rutas.append(registro[3:])
    return rutas


@dataclass(frozen=True, slots=True)
class _Alcance:
    """Qué puede tocar este arreglo, y de dónde salió ese permiso.

    ``rutas`` vacío se lee según el origen: con ``"proyecto"`` es "todo vale"
    (alguien lo pidió), con ``"git"`` es "esta sesión no dejó nada de este
    lenguaje" y con ``"ninguno"`` es "no hay permiso para tocar nada".
    """

    origen: OrigenDelAlcance
    rutas: tuple[str, ...]


def _dentro_del_alcance(archivo: str, declarado: Sequence[str]) -> bool:
    """``declarado`` vacío es el proyecto entero, y eso solo pasa si lo pidieron."""
    if not declarado:
        return True
    return any(archivo == ruta or archivo.startswith(f"{ruta.rstrip('/')}/") for ruta in declarado)


def _cambios_de_la_sesion(raiz: Path) -> tuple[tuple[str, ...] | None, str]:
    """``(archivos_cambiados, motivo_si_no_se_pudo)``; nunca las dos cosas."""
    cambiados = _archivos_cambiados_en_git(raiz, timeout_segundos=TIMEOUT_GIT_SEGUNDOS)
    if cambiados is None:
        return None, (
            "tampoco se pudo derivar de git (o no hay repositorio acá, o git no está "
            "instalado, o el `status` falló)"
        )
    if len(cambiados) > MAX_ARCHIVOS_DEL_ALCANCE:
        return None, (
            f"git ve {len(cambiados)} archivos cambiados, más de los "
            f"{MAX_ARCHIVOS_DEL_ALCANCE} que pueden pasar por 'lo que editó una sesión' "
            "(eso ya parece un merge o un cambio de rama)"
        )
    return cambiados, ""


def _decidir_alcance(
    definicion: _DefinicionLinter,
    *,
    rutas: Sequence[str],
    cambiados: tuple[str, ...] | None,
    todo_el_proyecto: bool,
) -> _Alcance:
    """Lo que pidió el llamador manda; después git; el proyecto entero solo si se pide."""
    if rutas:
        return _Alcance("rutas", tuple(rutas))
    if todo_el_proyecto:
        return _Alcance("proyecto", ())
    if cambiados is None:
        return _Alcance("ninguno", ())
    # De lo que cambió, solo lo que este linter sabe leer: pasarle un `.ts` a
    # ruff no es alcance, es un error de invocación.
    return _Alcance(
        "git", tuple(ruta for ruta in cambiados if Path(ruta).suffix in definicion.extensiones)
    )


# --------------------------------------------------------------------- #
# Foto del árbol, para verificar que el arreglo no se fue de tema.
# --------------------------------------------------------------------- #


def _fotografiar(raiz: Path, extensiones: frozenset[str]) -> dict[str, str] | None:
    """Hash por archivo de lo que el linter podría llegar a reescribir.

    Fotografía el proyecto ENTERO, no solo el alcance: lo que hay que cazar es
    precisamente la edición que se salió del alcance, y una foto acotada al
    alcance la volvería invisible por construcción.

    ``None`` cuando hay demasiados archivos: es preferible decir "no verifiqué
    el alcance" que gastar más en la verificación que en el arreglo.
    """
    mapa: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(raiz):
        dirnames[:] = [d for d in dirnames if d not in _CARPETAS_IGNORADAS]
        for nombre in filenames:
            if Path(nombre).suffix not in extensiones:
                continue
            if len(mapa) >= MAX_ARCHIVOS_FOTOGRAFIA:
                return None
            _anotar(mapa, Path(dirpath) / nombre, raiz)
    return mapa


def _anotar(mapa: dict[str, str], archivo: Path, raiz: Path) -> None:
    try:
        digest = hashlib.sha256(archivo.read_bytes()).hexdigest()
    except OSError:
        return
    mapa[_normalizar_ruta(str(archivo), raiz)] = digest


def _diferencias(antes: dict[str, str], despues: dict[str, str]) -> tuple[str, ...]:
    tocados = {ruta for ruta, hash_ in despues.items() if antes.get(ruta) != hash_}
    tocados |= {ruta for ruta in antes if ruta not in despues}
    return tuple(sorted(tocados))


# --------------------------------------------------------------------- #
# Reparación.
# --------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SolicitudDeArreglo:
    """Lo que se le pasa al modelo cuando el linter ya hizo todo lo que podía."""

    linter: str
    intento: int
    problemas: tuple[ProblemaLint, ...]
    archivos: tuple[str, ...]
    instrucciones: str

    def resumen(self) -> dict[str, Any]:
        return {
            "linter": self.linter,
            "intento": self.intento,
            "problemas": [problema.resumen() for problema in self.problemas],
            "archivos": list(self.archivos),
            "instrucciones": self.instrucciones,
        }


ArregloDelModelo = Callable[[SolicitudDeArreglo], None]


def _instrucciones(
    linter: str, problemas: Sequence[ProblemaLint], archivos: Sequence[str], salida: str
) -> str:
    lineas = [
        f"El linter `{linter}` dejó {len(problemas)} problema(s) que su arreglo "
        "automático no puede resolver.",
        "",
        "Corrige ÚNICAMENTE lo que está en esta lista. No reordenes imports que el "
        "linter no señaló, no renombres nada, no reformatees archivos completos y no "
        "aproveches el viaje para mejorar otra cosa: este cambio tiene que poder "
        "leerse en diez segundos.",
    ]
    if archivos:
        lineas += ["", "Toca solo estos archivos:", *(f"- {archivo}" for archivo in archivos)]
    if problemas:
        lineas += ["", "Problemas:", *(f"- {problema.como_linea()}" for problema in problemas)]
    elif salida:
        # Sin problemas parseados (linter con formato propio) el texto crudo
        # ya recortado es lo mejor que se le puede dar al modelo.
        lineas += ["", "Salida del linter:", salida]
    return "\n".join(lineas)


@dataclass(frozen=True, slots=True)
class ResultadoReparacion:
    """Qué se arregló, quién lo arregló y qué archivos se tocaron de verdad."""

    linter: str | None
    estado: Estado
    mensaje: str
    problemas_iniciales: tuple[ProblemaLint, ...]
    problemas_restantes: tuple[ProblemaLint, ...]
    resueltos_por_el_linter: int
    intentos_del_modelo: int
    archivos_modificados: tuple[str, ...]
    archivos_fuera_de_alcance: tuple[str, ...]
    alcance_verificado: bool
    detenido_por: str | None
    alcance_origen: OrigenDelAlcance
    # Lo que se declaró tocable. Vacío significa "todo el proyecto", y eso
    # solo ocurre con ``todo_el_proyecto=True``.
    archivos_del_alcance: tuple[str, ...] = ()

    @property
    def alcance_respetado(self) -> bool:
        """Se miró el disco Y nada se salió de lo declarado.

        No alcanza con que la lista de archivos fuera de alcance esté vacía:
        también está vacía cuando no se pudo sacar la foto, y ahí la respuesta
        honesta es "no lo sé", que para quien va a aceptar un diff vale lo
        mismo que un "no".
        """
        return self.alcance_verificado and not self.archivos_fuera_de_alcance

    @property
    def limpio(self) -> bool:
        return self.estado in (
            "sin_problemas",
            "arreglado_por_el_linter",
            "arreglado_con_ayuda_del_modelo",
        )

    def resumen(self) -> dict[str, Any]:
        return {
            "linter": self.linter,
            "estado": self.estado,
            "limpio": self.limpio,
            "mensaje": self.mensaje,
            "problemas_iniciales": [p.resumen() for p in self.problemas_iniciales],
            "problemas_restantes": [p.resumen() for p in self.problemas_restantes],
            "resueltos_por_el_linter": self.resueltos_por_el_linter,
            "intentos_del_modelo": self.intentos_del_modelo,
            "archivos_modificados": list(self.archivos_modificados),
            "archivos_fuera_de_alcance": list(self.archivos_fuera_de_alcance),
            "alcance_verificado": self.alcance_verificado,
            "alcance_respetado": self.alcance_respetado,
            "alcance_origen": self.alcance_origen,
            "archivos_del_alcance": list(self.archivos_del_alcance),
            "detenido_por": self.detenido_por,
        }


def _sin_cambios(
    linter: str | None,
    estado: Estado,
    mensaje: str,
    *,
    problemas: tuple[ProblemaLint, ...] = (),
    alcance: _Alcance | None = None,
) -> ResultadoReparacion:
    return ResultadoReparacion(
        linter=linter,
        estado=estado,
        mensaje=mensaje,
        problemas_iniciales=problemas,
        problemas_restantes=problemas,
        resueltos_por_el_linter=0,
        intentos_del_modelo=0,
        archivos_modificados=(),
        archivos_fuera_de_alcance=(),
        # No se tocó nada, así que el alcance está respetado por construcción.
        alcance_verificado=True,
        detenido_por=None,
        alcance_origen=alcance.origen if alcance else "ninguno",
        archivos_del_alcance=alcance.rutas if alcance else (),
    )


def _arreglo_automatico(
    linter: LinterDetectado, *, raiz: Path, rutas: Sequence[str], timeout_segundos: int
) -> None:
    """Le pide al linter que se arregle solo. Errores acá no son fatales.

    Si ``--fix`` falla, el estado del código no empeoró: simplemente sigue
    sucio y la revisión siguiente lo va a decir. Por eso no se levanta nada.
    """
    definicion = _definicion(linter.nombre)
    assert linter.ejecutable is not None
    objetivos = _objetivos(definicion, rutas)
    for variante in definicion.variantes_arreglar:
        salida = _correr(
            (linter.ejecutable, *variante, *objetivos), cwd=raiz, timeout_segundos=timeout_segundos
        )
        if salida.error is not None:
            return
        if not _parece_bandera_no_soportada(salida):
            return


_PATRONES_BANDERA_NO_SOPORTADA = (
    re.compile(r"unexpected argument", re.IGNORECASE),
    re.compile(r"unrecognized (?:option|argument)", re.IGNORECASE),
    re.compile(r"unknown (?:option|argument|flag|command)", re.IGNORECASE),
    re.compile(r"no such (?:option|subcommand)", re.IGNORECASE),
)


def _parece_bandera_no_soportada(salida: _Salida) -> bool:
    """¿La invocación falló por una bandera que esta versión no conoce?

    Se exige un código de salida "raro" además del texto: los linters usan 0
    (limpio) y 1 (encontró problemas) para su trabajo normal, así que un
    mensaje de uso con código 1 casi siempre es contenido del propio reporte,
    no un error de invocación.
    """
    if salida.exit_code in (0, 1):
        return False
    texto = f"{salida.stdout}\n{salida.stderr}"
    return any(patron.search(texto) for patron in _PATRONES_BANDERA_NO_SOPORTADA)


def reparar_lint(
    directorio: str | Path,
    *,
    rutas: Sequence[str] = (),
    linter: str | None = None,
    arreglar: ArregloDelModelo | None = None,
    todo_el_proyecto: bool = False,
    max_intentos_del_modelo: int = MAX_INTENTOS_DEL_MODELO,
    timeout_segundos: int = TIMEOUT_LINTER_SEGUNDOS,
) -> ResultadoReparacion:
    """Detecta el linter, deja que se arregle solo y solo entonces llama al modelo.

    Parámetros
    ----------
    rutas:
        Archivos o carpetas a revisar, relativos a ``directorio``. Pasa acá lo
        que el agente acaba de editar: es el límite del cambio. Si no viene, se
        deriva de lo que git ve cambiado en el working tree; y si eso tampoco
        se puede, la reparación se niega (``estado="sin_alcance"``) en vez de
        arreglar el proyecto entero.
    linter:
        Fuerza uno del catálogo (``"ruff"``, ``"eslint"``, ...). Por defecto se
        usa el primer linter detectado que además esté instalado.
    arreglar:
        Callback que el INTEGRADOR conecta al modelo. Recibe una
        ``SolicitudDeArreglo`` con los problemas que ``--fix`` no pudo
        resolver y edita los archivos. Si es ``None``, este módulo hace todo
        lo gratuito y reporta lo que quedó, sin gastar un solo token.
    todo_el_proyecto:
        Autoriza explícitamente a arreglar todo lo que el linter marque en el
        repositorio, sin alcance de sesión. Es una decisión de quien llama --
        el diff resultante puede ser enorme -- y por eso hay que escribirla.
    max_intentos_del_modelo:
        Tope de llamadas a ``arreglar``. El bucle también corta antes por su
        cuenta si el mismo error se repite sin cambiar (esa parte la resuelve
        ``ide_verificacion``).
    """
    if max_intentos_del_modelo < 0:
        raise IDELintError("max_intentos_del_modelo no puede ser negativo.")
    raiz = Path(directorio)
    if not raiz.is_dir():
        raise IDELintError(f"No es un directorio: {directorio}")

    cambiados: tuple[str, ...] | None = None
    motivo_sin_alcance = ""
    if not rutas and not todo_el_proyecto:
        cambiados, motivo_sin_alcance = _cambios_de_la_sesion(raiz)

    # Los archivos cambiados también dicen qué linter tiene sentido correr:
    # una sesión que solo tocó `.ts` no necesita que se despierte ruff.
    elegido = _elegir(raiz, rutas=cambiados if cambiados is not None else rutas, nombre=linter)
    if elegido is None and cambiados is not None:
        # La sesión no tocó nada de ningún lenguaje con linter. El proyecto sí
        # tiene linters, así que decir "acá no hay linter" sería falso: se elige
        # igual para poder nombrarlo, y el alcance vacío corta más abajo.
        elegido = _elegir(raiz, rutas=(), nombre=linter)
    if elegido is None:
        detalle = (
            f"No se encontró configuración de {linter} en el proyecto."
            if linter
            else "No se encontró ningún linter configurado en el proyecto "
            "(se buscó ruff, flake8, eslint, biome, swiftlint, ktlint y golangci-lint)."
        )
        return _sin_cambios(linter, "sin_linter", f"{detalle} No hay nada que reparar.")
    if not elegido.disponible:
        return _sin_cambios(
            elegido.nombre,
            "linter_no_instalado",
            elegido.motivo_no_disponible or f"{elegido.nombre} no está instalado.",
        )

    definicion = _definicion(elegido.nombre)
    alcance = _decidir_alcance(
        definicion,
        rutas=rutas,
        cambiados=cambiados,
        todo_el_proyecto=todo_el_proyecto,
    )
    if motivo_sin_alcance:
        return _sin_cambios(
            elegido.nombre,
            "sin_alcance",
            "No se tocó ni un archivo. Sin `rutas` no se sabe qué editó esta sesión, y "
            f"{motivo_sin_alcance}. Correr `{elegido.nombre} --fix` sobre todo el "
            "proyecto dejaría un diff enorme que nadie pidió, mezclado con el trabajo "
            "real. Pasa `rutas=[...]` con lo que tocaste, o `todo_el_proyecto=True` si "
            "de verdad quieres arreglar el repositorio entero.",
            alcance=alcance,
        )
    if alcance.origen == "git" and not alcance.rutas:
        extensiones = ", ".join(sorted(definicion.extensiones))
        return _sin_cambios(
            elegido.nombre,
            "sin_problemas",
            f"Esta sesión no dejó cambios en archivos que {elegido.nombre} revise "
            f"({extensiones}), así que no había nada dentro del alcance. El resto del "
            "proyecto no se revisó a propósito.",
            alcance=alcance,
        )

    informe_inicial = _revisar_con(
        elegido, raiz=raiz, rutas=alcance.rutas, timeout_segundos=timeout_segundos
    )
    if informe_inicial.error_de_ejecucion:
        return _sin_cambios(
            elegido.nombre,
            "el_linter_fallo",
            f"{elegido.nombre} no pudo completar la revisión, así que no se tocó "
            f"nada: {informe_inicial.error_de_ejecucion}",
            alcance=alcance,
        )
    if informe_inicial.limpio:
        return _sin_cambios(
            elegido.nombre,
            "sin_problemas",
            f"{elegido.nombre} no marcó nada. Nada que arreglar.",
            alcance=alcance,
        )

    senalados: set[str] = set(informe_inicial.archivos)
    foto_inicial = _fotografiar(raiz, definicion.extensiones)

    informe_actual = informe_inicial
    if definicion.variantes_arreglar:
        _arreglo_automatico(
            elegido, raiz=raiz, rutas=alcance.rutas, timeout_segundos=timeout_segundos
        )
        informe_actual = _revisar_con(
            elegido, raiz=raiz, rutas=alcance.rutas, timeout_segundos=timeout_segundos
        )
        senalados |= set(informe_actual.archivos)

    resueltos = max(0, len(informe_inicial.problemas) - len(informe_actual.problemas))
    # De acá en adelante hay tres salidas posibles y todas cierran igual:
    # comparando la foto previa contra el disco. `senalados` viaja por
    # referencia a propósito -- el puente al modelo le sigue agregando archivos
    # mientras el bucle corre, y esos también son alcance legítimo.
    cerrar = partial(
        _finalizar,
        elegido.nombre,
        informe_inicial=informe_inicial,
        resueltos=resueltos,
        raiz=raiz,
        definicion=definicion,
        alcance=alcance,
        foto_inicial=foto_inicial,
        senalados=senalados,
    )

    if informe_actual.error_de_ejecucion:
        # El `--fix` corrió y la revisión de después ya no pudo leer nada. Sin
        # esta comprobación el informe se veía como "quedan 0 problemas" y el
        # modelo entraba a arreglar una lista vacía. Se cierra por `cerrar` y
        # no por `_sin_cambios` porque el disco YA se movió.
        return cerrar(
            estado="el_linter_fallo",
            mensaje=(
                f"`{elegido.nombre} --fix` corrió, pero la revisión de después no pudo "
                f"completarse: {informe_actual.error_de_ejecucion} No se sabe qué quedó "
                "sucio, así que no se llamó al modelo; lo que se lista como pendiente es "
                "lo que había ANTES del arreglo."
            ),
            problemas_restantes=informe_inicial.problemas,
            intentos_del_modelo=0,
            detenido_por="el_linter_fallo",
        )

    if informe_actual.limpio:
        return cerrar(
            estado="arreglado_por_el_linter",
            mensaje=(
                f"`{elegido.nombre} --fix` resolvió los {resueltos} problema(s) por su "
                "cuenta. No hizo falta el modelo."
            ),
            problemas_restantes=(),
            intentos_del_modelo=0,
            detenido_por=None,
        )

    if arreglar is None or max_intentos_del_modelo == 0:
        motivo = (
            "no se recibió una función `arreglar`"
            if arreglar is None
            else "el tope de intentos del modelo es 0"
        )
        return cerrar(
            estado="quedan_problemas",
            mensaje=(
                f"Quedan {len(informe_actual.problemas)} problema(s) que "
                f"{elegido.nombre} no arregla solo, y {motivo}: se reportan sin tocarlos."
            ),
            problemas_restantes=informe_actual.problemas,
            intentos_del_modelo=0,
            detenido_por="sin_funcion_de_arreglo",
        )

    llamadas: list[int] = []
    # La primera vuelta ya tiene los problemas estructurados de la revisión
    # recién hecha; de la segunda en adelante se leen de la salida del propio
    # bucle, que corre el linter en su formato de texto.
    problemas_de_la_primera_vuelta = list(informe_actual.problemas)

    def _puente(intento: ResultadoIntento) -> None:
        """Traduce un intento fallido del bucle a una petición para el modelo."""
        llamadas.append(intento.intento)
        salida = intento.resumen_error.texto if intento.resumen_error else ""
        problemas = tuple(problemas_de_la_primera_vuelta) or _parsear_texto(salida, raiz)
        archivos = tuple(dict.fromkeys(problema.archivo for problema in problemas))
        senalados.update(archivos)
        arreglar(
            SolicitudDeArreglo(
                linter=elegido.nombre,
                intento=len(llamadas),
                problemas=problemas,
                archivos=archivos,
                instrucciones=_instrucciones(elegido.nombre, problemas, archivos, salida),
            )
        )
        # El modelo pudo introducir algo que el linter SÍ arregla solo (un
        # import que quedó sin usar, por ejemplo). Volver a correr `--fix`
        # acá es gratis y ahorra una vuelta completa del modelo.
        if definicion.variantes_arreglar:
            _arreglo_automatico(
                elegido, raiz=raiz, rutas=alcance.rutas, timeout_segundos=timeout_segundos
            )
        problemas_de_la_primera_vuelta.clear()

    # El bucle vive en `ide_verificacion`: sabe reintentar, sabe cortar cuando
    # el mismo error se repite sin avanzar y sabe distinguir "no se pudo
    # ejecutar" de "falló". Se le pasa la invocación sin banderas de formato
    # porque su salida para humanos es el mejor texto de error disponible.
    argv_bucle = (
        elegido.ejecutable,
        *definicion.argv_texto,
        *_objetivos(definicion, alcance.rutas),
    )
    bucle = ejecutar_hasta_que_pase(
        argv_bucle,
        cwd=raiz,
        arreglar=_puente,
        # +1 porque el primer intento del bucle es una revisión, no un arreglo:
        # con N arreglos hacen falta N+1 corridas del linter.
        max_intentos=max_intentos_del_modelo + 1,
        timeout_segundos=timeout_segundos,
    )

    informe_final = _revisar_con(
        elegido, raiz=raiz, rutas=alcance.rutas, timeout_segundos=timeout_segundos
    )
    senalados |= set(informe_final.archivos)
    if informe_final.error_de_ejecucion:
        # Misma trampa que después del `--fix`, un paso más adelante: sin esto
        # el informe anunciaba "quedan 0 problema(s)", que se lee como "casi
        # limpio", cuando lo cierto es que ya no se puede saber.
        return cerrar(
            estado="el_linter_fallo",
            mensaje=(
                f"Después de {len(llamadas)} vuelta(s) del modelo, {elegido.nombre} ya no "
                f"pudo completar la revisión: {informe_final.error_de_ejecucion} Revisa el "
                "diff a mano: lo que se lista como pendiente es lo que había al empezar."
            ),
            problemas_restantes=informe_inicial.problemas,
            intentos_del_modelo=len(llamadas),
            detenido_por="el_linter_fallo",
        )
    if informe_final.limpio:
        estado: Estado = "arreglado_con_ayuda_del_modelo"
        mensaje = (
            f"{elegido.nombre} quedó limpio: {resueltos} problema(s) los resolvió "
            f"`--fix` y el resto se arregló en {len(llamadas)} vuelta(s) del modelo."
        )
    else:
        estado = "quedan_problemas"
        mensaje = (
            f"Quedan {len(informe_final.problemas)} problema(s) de {elegido.nombre} "
            f"después de {len(llamadas)} vuelta(s) del modelo. {bucle.mensaje}"
        )
        if bucle.detenido_por == "aprobado":
            # Hay linters (swiftlint, por ejemplo) que salen con código 0
            # aunque hayan reportado violaciones. El bucle solo mira el código
            # de salida, así que lo dio por bueno; decirlo es mejor que dejar
            # un "pasó" que no coincide con la lista de problemas de al lado.
            mensaje += (
                f" Cuidado: {elegido.nombre} terminó con código 0 aunque siguió "
                "reportando problemas, así que el bucle lo dio por bueno. Revísalos tú."
            )

    return cerrar(
        estado=estado,
        mensaje=mensaje,
        problemas_restantes=informe_final.problemas,
        intentos_del_modelo=len(llamadas),
        detenido_por=bucle.detenido_por,
    )


def _finalizar(
    nombre: str,
    *,
    estado: Estado,
    mensaje: str,
    informe_inicial: InformeLint,
    problemas_restantes: tuple[ProblemaLint, ...],
    resueltos: int,
    intentos_del_modelo: int,
    detenido_por: str | None,
    raiz: Path,
    definicion: _DefinicionLinter,
    alcance: _Alcance,
    foto_inicial: dict[str, str] | None,
    senalados: set[str],
) -> ResultadoReparacion:
    """Cierra el resultado comparando la foto previa contra el árbol de ahora.

    Un archivo modificado está en regla solo si cumple las DOS condiciones:
    estaba dentro del alcance que declaró la sesión y además el linter lo había
    señalado. Medir contra una sola de las dos es lo que hacía que
    ``alcance_respetado`` dijera que sí después de reescribir medio repositorio.
    """
    modificados: tuple[str, ...] = ()
    fuera_de_alcance: tuple[str, ...] = ()
    alcance_verificado = foto_inicial is not None
    if foto_inicial is not None:
        foto_final = _fotografiar(raiz, definicion.extensiones)
        if foto_final is None:
            alcance_verificado = False
        else:
            modificados = _diferencias(foto_inicial, foto_final)
            fuera_de_alcance = tuple(
                archivo
                for archivo in modificados
                if archivo not in senalados
                or not _dentro_del_alcance(archivo, alcance.rutas)
            )
    if fuera_de_alcance:
        mensaje += (
            " OJO: se modificaron archivos que estaban fuera del alcance de este "
            "arreglo ("
            + ", ".join(fuera_de_alcance)
            + "). Revisa el diff antes de aceptarlo: esta reparación tenía que "
            "limitarse a lo que el linter marcó dentro de lo que tocó la sesión."
        )
    elif not alcance_verificado:
        mensaje += (
            " No se pudo verificar el alcance del cambio: el proyecto tiene más de "
            f"{MAX_ARCHIVOS_FOTOGRAFIA} archivos de este lenguaje. Revisa el diff a mano."
        )
    return ResultadoReparacion(
        linter=nombre,
        estado=estado,
        mensaje=mensaje,
        problemas_iniciales=informe_inicial.problemas,
        problemas_restantes=problemas_restantes,
        resueltos_por_el_linter=resueltos,
        intentos_del_modelo=intentos_del_modelo,
        archivos_modificados=modificados,
        archivos_fuera_de_alcance=fuera_de_alcance,
        alcance_verificado=alcance_verificado,
        detenido_por=detenido_por,
        alcance_origen=alcance.origen,
        archivos_del_alcance=alcance.rutas,
    )


def reparar_todo_el_lint(
    directorio: str | Path,
    *,
    rutas: Sequence[str] = (),
    arreglar: ArregloDelModelo | None = None,
    todo_el_proyecto: bool = False,
    max_intentos_del_modelo: int = MAX_INTENTOS_DEL_MODELO,
    timeout_segundos: int = TIMEOUT_LINTER_SEGUNDOS,
) -> tuple[ResultadoReparacion, ...]:
    """``reparar_lint`` una vez por cada linter detectado.

    Un monorepo con Python y TypeScript tiene dos linters y los dos importan;
    quedarse con el primero dejaría la mitad del cambio sin revisar. Devuelve
    una tupla vacía si el proyecto no tiene ningún linter configurado.

    El alcance se resuelve linter por linter, igual que en ``reparar_lint``: sin
    ``rutas`` cada uno mira qué archivos de SU lenguaje cambiaron en el working
    tree, y el que no tenga ninguno devuelve ``"sin_problemas"`` sin revisar el
    resto del proyecto.
    """
    raiz = Path(directorio)
    resultados = []
    for detectado in detectar_linters(raiz, rutas=rutas):
        rutas_del_linter = [
            ruta
            for ruta in rutas
            if not Path(ruta).suffix or Path(ruta).suffix in detectado.extensiones
        ]
        if rutas and not rutas_del_linter:
            continue
        resultados.append(
            reparar_lint(
                raiz,
                rutas=rutas_del_linter,
                linter=detectado.nombre,
                arreglar=arreglar,
                todo_el_proyecto=todo_el_proyecto,
                max_intentos_del_modelo=max_intentos_del_modelo,
                timeout_segundos=timeout_segundos,
            )
        )
    return tuple(resultados)
