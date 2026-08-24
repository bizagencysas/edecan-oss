"""Mapa del proyecto: un ÍNDICE barato de un repo grande, no un volcado.

El problema real que resuelve este módulo -- medido, no supuesto
------------------------------------------------------------------
Acme (el repo de referencia que el dueño quiere poder reconstruir con el
IDE de Edecán) tiene 1.099 archivos y 271.316 líneas. Ninguna ventana de
contexto aguanta eso, y el agente del IDE (``ide_workers_agent.WorkersIDEAgent``)
no puede leerse el repo entero antes de decidir qué hacer con una tarea. Lo
que sí puede tener siempre a mano es un mapa chico: "esto existe, esto hace
cada parte, así se prueba y se corre" -- suficiente para decidir DÓNDE mirar
antes de gastar una sola llamada de ``leer_archivo``.

Este módulo construye exactamente ese mapa:

- **Estructura**: qué "unidades" tiene el repo (una carpeta con un manifiesto
  propio: ``pyproject.toml``, ``package.json``, ``Package.swift``/``.xcodeproj``,
  ``build.gradle[.kts]``/``pom.xml``, ``go.mod``, ``Cargo.toml``, ``Gemfile``,
  ``composer.json`` o ``*.csproj``/``*.sln``), qué hace cada una (se lee su
  propio ``description``, no se inventa), sus comandos de test/build, y sus
  puntos de entrada. La lista de ecosistemas no es decorativa: un mapa que
  solo entiende Python y Node deja el repo de una app iOS, de un servicio en
  Go o de un backend en Kotlin sin una sola unidad -- y todo lo que se apoya
  en el mapa (empezando por ``ide_semilla_proyecto``) queda inerte ahí.
- **Lenguajes y gestores de paquetes**: conteo de líneas por extensión, y
  qué gestor coordina el repo (``uv`` si hay ``uv.lock``/``[tool.uv.workspace]``
  en la raíz, ``npm``/``pnpm``/``yarn`` por paquete Node según su lockfile).
- **Presupuesto explícito de tokens**: ``render_prompt`` NO es "el JSON
  completo" -- es un texto pensado para vivir dentro de un prompt, con un
  límite de tokens duro (``DEFAULT_PROMPT_TOKEN_BUDGET``) que se respeta
  recortando por prioridad (unidades grandes primero, descripciones cortas
  antes que unidades completas) en vez de cortar a la mitad de una palabra.
  Un mapa de 50.000 tokens no es un mapa, es otro problema de contexto.
- **Cacheado e incremental**: el chequeo de "¿cambió el repo?" (``firma``,
  un hash de ruta+tamaño+mtime de cada archivo rastreado) es TODO lo que se
  paga en el camino feliz -- ni un archivo de código se abre para contar
  líneas si la firma no cambió desde el último mapa. Reconstruir de cero
  (cuando sí cambió) solo lee contenido de archivos de código conocidos
  (nunca binarios, nunca ``node_modules``/``.venv``/``dist``/``build``), así
  que en un repo de 270k líneas tarda segundos, no minutos.
- **Respeta ``.gitignore``**: el listado de archivos sale de
  ``git ls-files -z --cached --others --exclude-standard`` -- mismo comando,
  mismo criterio, que ya usan ``ide_referencias.ReferenceService`` y
  ``ide_busqueda_semantica.SemanticSearchService`` (no se importa ninguno de
  esos dos módulos: son propiedad de otras corridas trabajando en paralelo
  sobre ellos; acá se repite el mismo comando pequeño en vez de acoplarse).

Se complementa con la búsqueda semántica (``ide_busqueda_semantica.py``, obra
de otra corrida): este módulo da la ESTRUCTURA ("existe un paquete
``packages/browser`` que hace scraping"), la búsqueda semántica da el
DETALLE ("¿dónde se valida la contraseña?"). Ninguno reemplaza al otro.

Deliberadamente NO importa ``ide_workers_agent.py``, ``ide_sessions.py`` ni
``apps/api/edecan_api/routers/ide.py`` (los tres bajo edición de otra
corrida en paralelo): el cableado real -- qué herramienta nueva del agente
llama a ``ProjectMapService`` y cuándo -- queda para que lo arme una persona
después, con la API pública de este archivo.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from edecan_companion.ide_files import _IGNORED as _IGNORED_DIRS
from edecan_companion.ide_workspaces import WorkspaceStore

__all__ = [
    "CHARS_PER_TOKEN",
    "DEFAULT_PROMPT_TOKEN_BUDGET",
    "IDEProjectMapError",
    "LenguajeStat",
    "MAX_BYTES_FOR_LINE_COUNT",
    "MAX_TRACKED_FILES",
    "MapaProyecto",
    "ProjectMapService",
    "UnidadProyecto",
]


class IDEProjectMapError(ValueError):
    """Workspace o parámetro inválido al pedir/renderizar el mapa."""


# --------------------------------------------------------------------- #
# Presupuesto de tamaño -- el corazón del pedido: "barato, cacheado, y que
# quepa en un prompt". Ver ``render_prompt`` para cómo se respeta.
# --------------------------------------------------------------------- #

CHARS_PER_TOKEN = 4
"""Misma heurística de ~4 caracteres por token que ya usa
``ide_referencias.py``/``ide_costos.py`` -- no se reinventa un contador de
tokens propio para un texto que, de todas formas, nunca se manda solo: viaja
como parte de un prompt más grande que ya lo estima igual."""

DEFAULT_PROMPT_TOKEN_BUDGET = 1_500
"""~6.000 caracteres. Deliberadamente chico: este mapa compite por espacio
con el resto del prompt (system, historial, la tarea en curso) -- 1.500
tokens es "una página", no "medio contexto"."""

MAX_TRACKED_FILES = 20_000
"""Techo duro sobre cuántos archivos rastrea el listado, incluso en un
monorepo mucho más grande que Acme. Pasado esto se marca
``archivos_truncados=True`` en vez de seguir creciendo sin límite."""

MAX_BYTES_FOR_LINE_COUNT = 1_500_000
"""Un archivo de código más grande que esto NO se abre para contar líneas
línea por línea (evita que un solo archivo generado/mínimamente-comprimido
haga lento todo el escaneo) -- se estima con ``tamaño // _BYTES_POR_LINEA_ESTIMADA``
y el mapa completo queda marcado ``lineas_son_aproximadas=True``."""

_BYTES_POR_LINEA_ESTIMADA = 40

_GIT_LS_FILES_TIMEOUT_SECONDS = 5
_GIT_HEAD_TIMEOUT_SECONDS = 3

_MANIFEST_PYTHON = "pyproject.toml"
_MANIFEST_NODE = "package.json"
_MANIFEST_RUST = "Cargo.toml"
_MANIFEST_GO = "go.mod"
_MANIFEST_SWIFT_SPM = "Package.swift"
_MANIFEST_SWIFT_PODS = "Podfile"
_MANIFEST_GRADLE = "build.gradle"
_MANIFEST_GRADLE_KTS = "build.gradle.kts"
_MANIFEST_MAVEN = "pom.xml"
_MANIFEST_RUBY = "Gemfile"
_MANIFEST_PHP = "composer.json"

_ECOSISTEMA_POR_MANIFIESTO: dict[str, str] = {
    _MANIFEST_PYTHON: "python",
    _MANIFEST_NODE: "node",
    _MANIFEST_RUST: "rust",
    _MANIFEST_GO: "go",
    _MANIFEST_SWIFT_SPM: "swift",
    _MANIFEST_SWIFT_PODS: "swift",
    _MANIFEST_GRADLE: "jvm",
    _MANIFEST_GRADLE_KTS: "jvm",
    _MANIFEST_MAVEN: "jvm",
    _MANIFEST_RUBY: "ruby",
    _MANIFEST_PHP: "php",
}
"""Nombre exacto de archivo → ecosistema. ``jvm`` y ``swift`` son claves de
ecosistema, no el ``tipo`` final de la unidad: el constructor de cada uno
decide después si eso es ``kotlin`` o ``java`` mirando el código que hay al
lado (ver ``_unidad_jvm``)."""

_SUFIJOS_MANIFIESTO: tuple[tuple[str, str], ...] = (
    (".csproj", "dotnet"),
    (".fsproj", "dotnet"),
    (".vbproj", "dotnet"),
    (".sln", "dotnet"),
)
"""Manifiestos que .NET nombra con el proyecto (``Pagos.Api.csproj``), así que
se reconocen por sufijo y no por nombre exacto."""

_SUFIJOS_BUNDLE: tuple[str, ...] = (".xcodeproj", ".xcworkspace")
"""Xcode guarda su "manifiesto" en una CARPETA, no en un archivo: git lista lo
que hay adentro (``MiApp.xcodeproj/project.pbxproj``), así que la unidad es la
carpeta PADRE del bundle. Solo cuenta el bundle más externo -- un
``.xcodeproj`` trae adentro su propio ``project.xcworkspace``, y tomarlo en
serio inventaría una unidad dentro de otra."""

_PRECEDENCIA_ECOSISTEMAS: tuple[str, ...] = (
    "python",
    "node",
    "rust",
    "go",
    "jvm",
    "swift",
    "dotnet",
    "ruby",
    "php",
)
"""Desempate cuando UNA carpeta trae manifiestos de dos ecosistemas.

Una carpeta produce exactamente una unidad: dos unidades con la misma ``ruta``
se repartirían los mismos archivos y cada línea se contaría dos veces. El
orden no pretende ser una verdad universal -- lo que importa es que sea
DETERMINISTA (antes dependía de en qué orden listó git los archivos) y que
``Gemfile``/``composer.json`` queden al final, porque son los que más seguido
acompañan a una app escrita en otra cosa (los assets de un Rails, las
herramientas de un tema) en vez de describirla."""

_CODE_LANGUAGES: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".php": "php",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cs": "c#",
    ".sql": "sql",
    ".sh": "shell",
    ".bash": "shell",
    ".vue": "vue",
}
"""Extensiones que cuentan como "líneas de código" -- deliberadamente NO
incluye ``.json``/``.yml``/``.md``/``.html``/``.css`` sueltos: son datos o
documentación, no lo que alguien mide cuando dice "271.316 líneas"."""

_ENTRY_CANDIDATES_PYTHON = ("main.py", "__main__.py", "app.py", "server.py")
_ENTRY_CANDIDATES_NODE = ("index.ts", "index.js", "server.ts", "server.js", "main.ts")
_ENTRY_CANDIDATES_SWIFT = ("main.swift", "App.swift", "AppDelegate.swift", "ContentView.swift")
_ENTRY_CANDIDATES_JVM = ("Main.kt", "Application.kt", "Main.java", "Application.java")
_ENTRY_CANDIDATES_GO = ("main.go",)
_ENTRY_CANDIDATES_RUST = ("src/main.rs", "src/lib.rs")
_ENTRY_CANDIDATES_RUBY = ("config.ru", "bin/rails", "Rakefile")
_ENTRY_CANDIDATES_PHP = ("artisan", "public/index.php", "index.php")
_ENTRY_CANDIDATES_DOTNET = ("Program.cs", "Startup.cs", "Program.fs")

_MAX_BYTES_MANIFIESTO = 200_000
"""Un manifiesto se lee acotado igual que cualquier otro archivo del repo: es
contenido que escribió quien mantiene ese repositorio, no un archivo de
confianza, y acá solo se le sacan unos pocos campos."""

_MAP_CACHE_SUBDIR = "project-map"
_MAP_FORMAT_VERSION = 1


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


# --------------------------------------------------------------------- #
# Listado de archivos rastreados -- respeta .gitignore, mismo comando que
# ``ide_referencias.py``/``ide_busqueda_semantica.py`` usan cada uno por su
# cuenta (no se importa ninguno de los dos: ver docstring del módulo).
# --------------------------------------------------------------------- #


def _list_tracked_files(root: Path) -> tuple[list[str], bool]:
    via_git = _list_files_via_git(root)
    files = via_git if via_git is not None else _list_files_via_walk(root)
    truncated = len(files) > MAX_TRACKED_FILES
    return sorted(files[:MAX_TRACKED_FILES]), truncated


def _list_files_via_git(root: Path) -> list[str] | None:
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True,
            timeout=_GIT_LS_FILES_TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    files: list[str] = []
    for entry in result.stdout.decode("utf-8", errors="replace").split("\x00"):
        if not entry:
            continue
        posix_path = PurePosixPath(entry)
        if ".." in posix_path.parts or posix_path.is_absolute():
            continue
        if any(part in _IGNORED_DIRS for part in posix_path.parts):
            continue
        files.append(entry)
    return files


def _list_files_via_walk(root: Path) -> list[str]:
    files: list[str] = []
    collection_cap = MAX_TRACKED_FILES + 1
    for current_dir, subdirs, filenames in os.walk(root):
        subdirs[:] = sorted(
            name for name in subdirs if name not in _IGNORED_DIRS and not name.startswith(".")
        )
        for filename in sorted(filenames):
            absolute = Path(current_dir) / filename
            try:
                relative = absolute.relative_to(root)
            except ValueError:
                continue
            files.append(relative.as_posix())
            if len(files) >= collection_cap:
                return files
    return files


@dataclass(frozen=True)
class _ArchivoRastreado:
    ruta: str
    tamano: int
    mtime_ns: int


def _stat_tracked_files(root: Path, relative_paths: list[str]) -> list[_ArchivoRastreado]:
    """Un ``stat`` por archivo -- nunca abre ni lee contenido. Es la parte
    "barata" que corre SIEMPRE (incluso cuando el mapa termina saliendo del
    caché) porque es, a la vez, el listado que hace falta para reconstruir
    Y la base de la firma de invalidación (ver ``_signature``)."""
    rows: list[_ArchivoRastreado] = []
    for relative_path in relative_paths:
        absolute = root / relative_path
        try:
            info = absolute.stat()
        except OSError:
            continue
        if not absolute.is_file():
            continue
        rows.append(
            _ArchivoRastreado(ruta=relative_path, tamano=info.st_size, mtime_ns=info.st_mtime_ns)
        )
    return rows


def _signature(archivos: list[_ArchivoRastreado]) -> str:
    """Hash barato de ruta+tamaño+mtime de cada archivo rastreado. Si nada
    cambió (ni un archivo se creó/borró/tocó) esta firma es idéntica a la del
    último mapa cacheado, y ``ProjectMapService.get_map`` no lee ni una línea
    de código de nuevo."""
    hasher = hashlib.sha256()
    for fila in archivos:
        hasher.update(f"{fila.ruta}|{fila.tamano}|{fila.mtime_ns}\n".encode())
    return hasher.hexdigest()


# --------------------------------------------------------------------- #
# Conteo de líneas por lenguaje -- solo para extensiones de "código real"
# (``_CODE_LANGUAGES``), con estimación para archivos grandes en vez de
# leerlos completos.
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class LenguajeStat:
    lenguaje: str
    archivos: int
    lineas: int

    def resumen(self) -> dict[str, Any]:
        return {"lenguaje": self.lenguaje, "archivos": self.archivos, "lineas": self.lineas}


@dataclass
class _ConteoArchivo:
    ruta: str
    lenguaje: str | None
    lineas: int
    es_aproximado: bool


def _contar_lineas(
    root: Path, archivos: list[_ArchivoRastreado]
) -> tuple[list[_ConteoArchivo], bool]:
    resultado: list[_ConteoArchivo] = []
    alguna_aproximada = False
    for fila in archivos:
        extension = PurePosixPath(fila.ruta).suffix.lower()
        lenguaje = _CODE_LANGUAGES.get(extension)
        if lenguaje is None:
            resultado.append(
                _ConteoArchivo(ruta=fila.ruta, lenguaje=None, lineas=0, es_aproximado=False)
            )
            continue
        if fila.tamano > MAX_BYTES_FOR_LINE_COUNT:
            estimado = max(fila.tamano // _BYTES_POR_LINEA_ESTIMADA, 1)
            alguna_aproximada = True
            resultado.append(
                _ConteoArchivo(
                    ruta=fila.ruta, lenguaje=lenguaje, lineas=estimado, es_aproximado=True
                )
            )
            continue
        try:
            texto = (root / fila.ruta).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            resultado.append(
                _ConteoArchivo(ruta=fila.ruta, lenguaje=lenguaje, lineas=0, es_aproximado=False)
            )
            continue
        lineas = texto.count("\n") + (1 if texto and not texto.endswith("\n") else 0)
        resultado.append(
            _ConteoArchivo(ruta=fila.ruta, lenguaje=lenguaje, lineas=lineas, es_aproximado=False)
        )
    return resultado, alguna_aproximada


def _agregar_lenguajes(conteos: list[_ConteoArchivo]) -> list[LenguajeStat]:
    por_lenguaje: dict[str, list[int]] = {}
    for fila in conteos:
        if fila.lenguaje is None:
            continue
        acumulado = por_lenguaje.setdefault(fila.lenguaje, [0, 0])
        acumulado[0] += 1
        acumulado[1] += fila.lineas
    stats = [
        LenguajeStat(lenguaje=lenguaje, archivos=valores[0], lineas=valores[1])
        for lenguaje, valores in por_lenguaje.items()
    ]
    return sorted(stats, key=lambda s: s.lineas, reverse=True)


# --------------------------------------------------------------------- #
# Unidades del proyecto (paquetes Python/Node): una por carpeta con
# ``pyproject.toml`` o ``package.json``.
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class UnidadProyecto:
    ruta: str
    # "python" | "node" | "swift" | "kotlin" | "java" | "go" | "rust" | "ruby"
    # | "php" | "dotnet"
    tipo: str
    nombre: str | None
    descripcion: str | None
    version: str | None
    comandos_test: tuple[str, ...]
    comandos_build: tuple[str, ...]
    entradas: tuple[str, ...]
    archivos: int
    lineas: int

    def resumen(self) -> dict[str, Any]:
        return {
            "ruta": self.ruta,
            "tipo": self.tipo,
            "nombre": self.nombre,
            "descripcion": self.descripcion,
            "version": self.version,
            "comandos_test": list(self.comandos_test),
            "comandos_build": list(self.comandos_build),
            "entradas": list(self.entradas),
            "archivos": self.archivos,
            "lineas": self.lineas,
        }


def _leer_toml(absolute: Path) -> dict[str, Any] | None:
    """``pyproject.toml`` y ``Cargo.toml`` son el mismo formato; el lector es
    uno solo."""
    try:
        with absolute.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None


def _leer_json_dict(absolute: Path) -> dict[str, Any] | None:
    """Sirve para ``package.json`` y para ``composer.json``: mismo formato,
    mismos campos ``name``/``description``/``version``/``scripts``."""
    try:
        data = json.loads(absolute.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _leer_texto_manifiesto(absolute: Path) -> str:
    """Texto acotado de un manifiesto que NO es TOML ni JSON.

    ``pom.xml``, ``*.csproj``, ``build.gradle`` y ``Package.swift`` se leen con
    expresiones regulares sobre este texto, a propósito: de un XML solo hacen
    falta cuatro campos, y meter un parser de XML de la biblioteca estándar
    contra un archivo que escribió quien mantiene el repo abre la puerta a la
    expansión de entidades (el "billion laughs") a cambio de nada. ``.gradle``
    y ``Package.swift`` directamente no son datos: son programas, y ejecutarlos
    para leerles el nombre está fuera de discusión.
    """

    try:
        crudo = absolute.read_bytes()[:_MAX_BYTES_MANIFIESTO]
    except OSError:
        return ""
    return crudo.decode("utf-8", errors="replace")


_RE_SWIFT_NOMBRE = re.compile(r"\bname\s*:\s*\"([^\"\n]{1,80})\"")
_RE_GO_MODULE = re.compile(r"^\s*module\s+(\S{1,200})", re.MULTILINE)
_RE_GRADLE_VERSION = re.compile(r"^\s*version\s*=\s*[\"']([^\"'\n]{1,40})[\"']", re.MULTILINE)
_RE_GRADLE_DESCRIPCION = re.compile(
    r"^\s*description\s*=\s*[\"']([^\"'\n]{1,300})[\"']", re.MULTILINE
)
_RE_GRADLE_ROOT_PROJECT = re.compile(
    r"rootProject\.name\s*=\s*[\"']([^\"'\n]{1,80})[\"']", re.MULTILINE
)
_RE_DOTNET_ES_TEST = re.compile(r"Microsoft\.NET\.Test\.Sdk|xunit|nunit|MSTest", re.IGNORECASE)


def _sin_bloque_parent(pom: str) -> str:
    """El ``pom.xml`` sin su ``<parent>``.

    Se hace con índices y no con ``<parent\\b.*?</parent>``: ese patrón, sobre
    un archivo del repo con muchos ``<parent`` sin cerrar, reintenta desde cada
    posición y se vuelve cuadrático. Acá el archivo lo escribe quien mantenga
    el repositorio que se está abriendo, así que el costo tiene que depender de
    su tamaño y no de su forma.

    Hace falta sacarlo porque el ``<parent>`` trae su propio
    ``artifactId``/``version``, y son los primeros que aparecen: sin esto, un
    módulo se queda con el nombre y la versión de su POM padre.
    """

    minusculas = pom.lower()
    inicio = minusculas.find("<parent")
    if inicio == -1:
        return pom
    fin = minusculas.find("</parent>", inicio)
    return pom if fin == -1 else pom[:inicio] + pom[fin + len("</parent>") :]


def _etiqueta_xml(texto: str, etiqueta: str) -> str | None:
    hallazgo = re.search(rf"<{etiqueta}>([^<]{{1,300}})</{etiqueta}>", texto, re.IGNORECASE)
    return hallazgo.group(1).strip() or None if hallazgo else None


def _nombres_hijos(directorio: str, archivos_del_dir: set[str]) -> set[str]:
    """Nombres de los hijos DIRECTOS de ``directorio`` -- archivos sueltos y el
    primer tramo de todo lo que cuelga más abajo (así ``MiApp.xcodeproj``
    aparece aunque git solo liste lo que tiene adentro)."""

    prefijo = f"{directorio}/" if directorio else ""
    nombres: set[str] = set()
    for ruta in archivos_del_dir:
        if not ruta.startswith(prefijo):
            continue
        resto = ruta[len(prefijo) :]
        if resto:
            nombres.add(resto.split("/", 1)[0])
    return nombres


def _primera_entrada(archivos_del_dir: set[str], candidatos: tuple[str, ...]) -> tuple[str, ...]:
    """El primer punto de entrada convencional que de verdad existe bajo la
    unidad (a cualquier profundidad: ``edecan_core/main.py`` cuenta para
    ``packages/core``)."""

    for candidato in candidatos:
        if any(ruta == candidato or ruta.endswith("/" + candidato) for ruta in archivos_del_dir):
            return (candidato,)
    return ()


def _hay_algo_bajo(directorio: str, archivos_del_dir: set[str], subcarpeta: str) -> bool:
    prefijo = f"{directorio}/{subcarpeta}/" if directorio else f"{subcarpeta}/"
    return any(ruta.startswith(prefijo) for ruta in archivos_del_dir)


def _base_de(root: Path, directorio: str) -> Path:
    return (root / directorio) if directorio else root


def _nueva_unidad(
    directorio: str,
    tipo: str,
    *,
    nombre: str | None = None,
    descripcion: str | None = None,
    version: str | None = None,
    comandos_test: tuple[str, ...] = (),
    comandos_build: tuple[str, ...] = (),
    entradas: tuple[str, ...] = (),
) -> UnidadProyecto:
    """Unidad recién detectada. ``archivos``/``lineas`` van en cero porque los
    llena ``_detectar_unidades`` después, cuando ya sabe qué archivo cae en qué
    unidad; ningún constructor los puede saber solo."""

    return UnidadProyecto(
        ruta=directorio or ".",
        tipo=tipo,
        nombre=nombre,
        descripcion=descripcion,
        version=version,
        comandos_test=comandos_test,
        comandos_build=comandos_build,
        entradas=entradas,
        archivos=0,
        lineas=0,
    )


def _unidad_python(
    directorio: str, root: Path, archivos_del_dir: set[str]
) -> UnidadProyecto | None:
    manifest_path = _base_de(root, directorio) / _MANIFEST_PYTHON
    datos = _leer_toml(manifest_path)
    if datos is None:
        return None
    proyecto = datos.get("project") if isinstance(datos.get("project"), dict) else {}
    dependencias = " ".join(
        str(d) for d in (proyecto.get("dependencies") or []) if isinstance(d, str)
    )
    grupos_dev = datos.get("dependency-groups", {})
    dev_deps = ""
    if isinstance(grupos_dev, dict):
        dev_deps = " ".join(
            str(d)
            for grupo in grupos_dev.values()
            if isinstance(grupo, list)
            for d in grupo
            if isinstance(d, str)
        )
    tiene_pytest = "pytest" in dependencias.lower() or "pytest" in dev_deps.lower()
    scripts = proyecto.get("scripts") if isinstance(proyecto.get("scripts"), dict) else {}
    entradas = tuple(f"{nombre} → {objetivo}" for nombre, objetivo in scripts.items())
    if not entradas:
        entradas = _primera_entrada(archivos_del_dir, _ENTRY_CANDIDATES_PYTHON)
    version = proyecto.get("version")
    return _nueva_unidad(
        directorio,
        "python",
        nombre=proyecto.get("name") if isinstance(proyecto.get("name"), str) else None,
        descripcion=(
            proyecto.get("description") if isinstance(proyecto.get("description"), str) else None
        ),
        version=version if isinstance(version, str) else None,
        comandos_test=("pytest -q",) if tiene_pytest else (),
        entradas=entradas,
    )


def _gestor_node(root: Path, directorio: str) -> str:
    base = root / directorio if directorio else root
    if (base / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (base / "yarn.lock").is_file():
        return "yarn"
    return "npm"


def _unidad_node(directorio: str, root: Path, archivos_del_dir: set[str]) -> UnidadProyecto | None:
    manifest_path = _base_de(root, directorio) / _MANIFEST_NODE
    datos = _leer_json_dict(manifest_path)
    if datos is None:
        return None
    scripts = datos.get("scripts") if isinstance(datos.get("scripts"), dict) else {}
    gestor = _gestor_node(root, directorio)
    comandos_test = (f"{gestor} run test",) if "test" in scripts else ()
    comandos_build = (f"{gestor} run build",) if "build" in scripts else ()
    entradas: list[str] = []
    for clave in ("dev", "start"):
        if clave in scripts:
            entradas.append(f"{gestor} run {clave}")
    main = datos.get("main")
    if isinstance(main, str) and main:
        entradas.append(f"main: {main}")
    bin_field = datos.get("bin")
    if isinstance(bin_field, str) and bin_field:
        entradas.append(f"bin: {bin_field}")
    elif isinstance(bin_field, dict):
        entradas.extend(f"bin {nombre}: {objetivo}" for nombre, objetivo in bin_field.items())
    if not entradas:
        entradas.extend(_primera_entrada(archivos_del_dir, _ENTRY_CANDIDATES_NODE))
    version = datos.get("version")
    descripcion = datos.get("description")
    return _nueva_unidad(
        directorio,
        "node",
        nombre=datos.get("name") if isinstance(datos.get("name"), str) else None,
        descripcion=descripcion if isinstance(descripcion, str) else None,
        version=version if isinstance(version, str) else None,
        comandos_test=comandos_test,
        comandos_build=comandos_build,
        entradas=tuple(entradas),
    )


# --------------------------------------------------------------------- #
# Los ecosistemas que no son Python ni Node. Cada constructor tiene la misma
# firma ``(directorio, root, archivos_del_dir)`` para que ``_detectar_unidades``
# los despache por tabla, y todos siguen la misma regla: se leen los campos que
# el manifiesto DECLARA y se emiten solo los comandos que ese manifiesto
# vuelve ciertos. Un comando inventado en el mapa es peor que ninguno: el
# agente lo va a correr.
# --------------------------------------------------------------------- #


def _unidad_swift(directorio: str, root: Path, archivos_del_dir: set[str]) -> UnidadProyecto | None:
    base = _base_de(root, directorio)
    hijos = _nombres_hijos(directorio, archivos_del_dir)
    proyectos = sorted(n for n in hijos if n.endswith(".xcodeproj"))
    espacios = sorted(n for n in hijos if n.endswith(".xcworkspace"))
    tiene_spm = (base / _MANIFEST_SWIFT_SPM).is_file()
    tiene_pods = (base / _MANIFEST_SWIFT_PODS).is_file()
    if not (tiene_spm or proyectos or espacios or tiene_pods):
        return None

    nombre: str | None = None
    if tiene_spm:
        hallazgo = _RE_SWIFT_NOMBRE.search(_leer_texto_manifiesto(base / _MANIFEST_SWIFT_SPM))
        nombre = hallazgo.group(1) if hallazgo else None
    if nombre is None and proyectos:
        nombre = proyectos[0].removesuffix(".xcodeproj")
    if nombre is None and espacios:
        nombre = espacios[0].removesuffix(".xcworkspace")

    comandos_test: tuple[str, ...] = ()
    comandos_build: list[str] = []
    if tiene_pods:
        # Sin ``pod install`` no hay ``.xcworkspace`` utilizable: es el primer
        # comando de verdad de este repo, no un detalle de instalación.
        comandos_build.append("pod install")
    if tiene_spm:
        comandos_build.append("swift build")
        comandos_test = ("swift test",)
    elif proyectos:
        # ``-workspace`` exigiría además un ``-scheme``, y el esquema no está
        # en ningún archivo que este mapa lea: se emite solo la forma que sí
        # funciona tal cual está escrita.
        comandos_build.append(f"xcodebuild -project {proyectos[0]} build")
        comandos_test = (f"xcodebuild -project {proyectos[0]} test",)

    return _nueva_unidad(
        directorio,
        "swift",
        nombre=nombre,
        comandos_test=comandos_test,
        comandos_build=tuple(comandos_build),
        entradas=_primera_entrada(archivos_del_dir, _ENTRY_CANDIDATES_SWIFT),
    )


def _tipo_jvm(archivos_del_dir: set[str], hijos: set[str]) -> str:
    """¿Kotlin o Java? El manifiesto de Gradle/Maven no lo dice, así que lo
    decide el código que hay bajo la unidad; el DSL del build solo desempata
    cuando todavía no hay ni una fuente."""

    kotlin = sum(1 for ruta in archivos_del_dir if ruta.endswith(".kt"))
    java = sum(1 for ruta in archivos_del_dir if ruta.endswith(".java"))
    if kotlin and kotlin >= java:
        return "kotlin"
    if java:
        return "java"
    return "kotlin" if _MANIFEST_GRADLE_KTS in hijos else "java"


def _unidad_jvm(directorio: str, root: Path, archivos_del_dir: set[str]) -> UnidadProyecto | None:
    base = _base_de(root, directorio)
    hijos = _nombres_hijos(directorio, archivos_del_dir)
    tipo = _tipo_jvm(archivos_del_dir, hijos)
    entradas = _primera_entrada(archivos_del_dir, _ENTRY_CANDIDATES_JVM)

    if (base / _MANIFEST_MAVEN).is_file():
        propio = _sin_bloque_parent(_leer_texto_manifiesto(base / _MANIFEST_MAVEN))
        return _nueva_unidad(
            directorio,
            tipo,
            nombre=_etiqueta_xml(propio, "name") or _etiqueta_xml(propio, "artifactId"),
            descripcion=_etiqueta_xml(propio, "description"),
            version=_etiqueta_xml(propio, "version"),
            comandos_test=("mvn test",),
            comandos_build=("mvn package",),
            entradas=entradas,
        )

    gradle = next(
        (n for n in (_MANIFEST_GRADLE_KTS, _MANIFEST_GRADLE) if (base / n).is_file()), None
    )
    if gradle is None:
        return None
    texto = _leer_texto_manifiesto(base / gradle)
    nombre = None
    for ajustes in ("settings.gradle.kts", "settings.gradle"):
        if (base / ajustes).is_file():
            raiz = _RE_GRADLE_ROOT_PROJECT.search(_leer_texto_manifiesto(base / ajustes))
            nombre = raiz.group(1) if raiz else None
            break
    descripcion = _RE_GRADLE_DESCRIPCION.search(texto)
    version = _RE_GRADLE_VERSION.search(texto)
    prefijo = _prefijo_gradle(root, directorio)
    return _nueva_unidad(
        directorio,
        tipo,
        nombre=nombre,
        descripcion=descripcion.group(1) if descripcion else None,
        version=version.group(1) if version else None,
        comandos_test=(f"{prefijo}test",),
        comandos_build=(f"{prefijo}build",),
        entradas=entradas,
    )


def _prefijo_gradle(root: Path, directorio: str) -> str:
    """Con qué se invoca Gradle para ESTA unidad.

    El wrapper (``./gradlew``) vive una sola vez, en la raíz del build, y desde
    ahí los subproyectos se llaman por su ruta con dos puntos (``:app:test``).
    Emitir ``./gradlew`` dentro de ``app/`` sería nombrar un archivo que no
    existe ahí.
    """

    if (_base_de(root, directorio) / "gradlew").is_file():
        return "./gradlew "
    if (root / "gradlew").is_file():
        return f"./gradlew :{directorio.replace('/', ':')}:" if directorio else "./gradlew "
    return f"gradle :{directorio.replace('/', ':')}:" if directorio else "gradle "


def _unidad_go(directorio: str, root: Path, archivos_del_dir: set[str]) -> UnidadProyecto | None:
    base = _base_de(root, directorio)
    if not (base / _MANIFEST_GO).is_file():
        return None
    hallazgo = _RE_GO_MODULE.search(_leer_texto_manifiesto(base / _MANIFEST_GO))
    return _nueva_unidad(
        directorio,
        "go",
        # La ruta del módulo ES su nombre en Go; la línea ``go 1.23`` no va en
        # ``version`` porque es la versión del lenguaje, no la del módulo.
        nombre=hallazgo.group(1) if hallazgo else None,
        comandos_test=("go test ./...",),
        comandos_build=("go build ./...",),
        entradas=_primera_entrada(archivos_del_dir, _ENTRY_CANDIDATES_GO),
    )


def _unidad_rust(directorio: str, root: Path, archivos_del_dir: set[str]) -> UnidadProyecto | None:
    datos = _leer_toml(_base_de(root, directorio) / _MANIFEST_RUST)
    if datos is None:
        return None
    paquete = datos.get("package") if isinstance(datos.get("package"), dict) else {}
    if not paquete and not isinstance(datos.get("workspace"), dict):
        return None
    nombre = paquete.get("name")
    descripcion = paquete.get("description")
    version = paquete.get("version")
    return _nueva_unidad(
        directorio,
        "rust",
        # Un ``Cargo.toml`` de solo ``[workspace]`` no tiene ``[package]`` y
        # aun así es una unidad: es el límite del build.
        nombre=nombre if isinstance(nombre, str) else None,
        descripcion=descripcion if isinstance(descripcion, str) else None,
        version=version if isinstance(version, str) else None,
        comandos_test=("cargo test",),
        comandos_build=("cargo build",),
        entradas=_primera_entrada(archivos_del_dir, _ENTRY_CANDIDATES_RUST),
    )


def _unidad_ruby(directorio: str, root: Path, archivos_del_dir: set[str]) -> UnidadProyecto | None:
    base = _base_de(root, directorio)
    if not (base / _MANIFEST_RUBY).is_file():
        return None
    hijos = _nombres_hijos(directorio, archivos_del_dir)
    gemspecs = sorted(n for n in hijos if n.endswith(".gemspec"))
    if _hay_algo_bajo(directorio, archivos_del_dir, "spec"):
        comandos_test: tuple[str, ...] = ("bundle exec rspec",)
    elif _hay_algo_bajo(directorio, archivos_del_dir, "test"):
        comandos_test = ("bundle exec rake test",)
    else:
        comandos_test = ()
    return _nueva_unidad(
        directorio,
        "ruby",
        # El ``Gemfile`` no declara nombre (solo dependencias); el ``.gemspec``
        # de al lado sí, y su nombre de archivo ES el nombre de la gema.
        nombre=gemspecs[0].removesuffix(".gemspec") if gemspecs else None,
        comandos_test=comandos_test,
        entradas=_primera_entrada(archivos_del_dir, _ENTRY_CANDIDATES_RUBY),
    )


def _unidad_php(directorio: str, root: Path, archivos_del_dir: set[str]) -> UnidadProyecto | None:
    datos = _leer_json_dict(_base_de(root, directorio) / _MANIFEST_PHP)
    if datos is None:
        return None
    scripts = datos.get("scripts") if isinstance(datos.get("scripts"), dict) else {}
    hijos = _nombres_hijos(directorio, archivos_del_dir)
    if "test" in scripts:
        comandos_test: tuple[str, ...] = ("composer run test",)
    elif {"phpunit.xml", "phpunit.xml.dist"} & hijos:
        comandos_test = ("vendor/bin/phpunit",)
    else:
        comandos_test = ()
    nombre = datos.get("name")
    descripcion = datos.get("description")
    version = datos.get("version")
    return _nueva_unidad(
        directorio,
        "php",
        nombre=nombre if isinstance(nombre, str) else None,
        descripcion=descripcion if isinstance(descripcion, str) else None,
        version=version if isinstance(version, str) else None,
        comandos_test=comandos_test,
        comandos_build=("composer run build",) if "build" in scripts else (),
        entradas=_primera_entrada(archivos_del_dir, _ENTRY_CANDIDATES_PHP),
    )


def _unidad_dotnet(
    directorio: str, root: Path, archivos_del_dir: set[str]
) -> UnidadProyecto | None:
    base = _base_de(root, directorio)
    hijos = _nombres_hijos(directorio, archivos_del_dir)
    proyectos = sorted(n for n in hijos if n.endswith((".csproj", ".fsproj", ".vbproj")))
    soluciones = sorted(n for n in hijos if n.endswith(".sln"))
    if not proyectos and not soluciones:
        return None

    if proyectos:
        # El nombre del ``.csproj`` es, salvo que se diga lo contrario, el
        # nombre del ensamblado que produce.
        manifiesto = proyectos[0]
        texto = _leer_texto_manifiesto(base / manifiesto)
        nombre = _etiqueta_xml(texto, "AssemblyName") or manifiesto.rsplit(".", 1)[0]
        descripcion = _etiqueta_xml(texto, "Description")
        version = _etiqueta_xml(texto, "Version")
        es_test = bool(_RE_DOTNET_ES_TEST.search(texto))
    else:
        manifiesto = soluciones[0]
        nombre = manifiesto.removesuffix(".sln")
        descripcion = None
        version = None
        # ``dotnet test`` sobre una solución corre los proyectos de prueba que
        # tenga; sobre un proyecto que no lo es, falla.
        es_test = True

    return _nueva_unidad(
        directorio,
        "dotnet",
        nombre=nombre,
        descripcion=descripcion,
        version=version,
        comandos_test=(f"dotnet test {manifiesto}",) if es_test else (),
        comandos_build=(f"dotnet build {manifiesto}",),
        entradas=_primera_entrada(archivos_del_dir, _ENTRY_CANDIDATES_DOTNET),
    )


_CONSTRUCTORES: dict[str, Callable[[str, Path, set[str]], UnidadProyecto | None]] = {
    "python": _unidad_python,
    "node": _unidad_node,
    "swift": _unidad_swift,
    "jvm": _unidad_jvm,
    "go": _unidad_go,
    "rust": _unidad_rust,
    "ruby": _unidad_ruby,
    "php": _unidad_php,
    "dotnet": _unidad_dotnet,
}


def _directorio_de(ruta: str) -> str:
    partes = ruta.rsplit("/", 1)
    return partes[0] if len(partes) == 2 else ""


def _detectar_unidades(
    root: Path, relative_paths: list[str], conteos: list[_ConteoArchivo]
) -> list[UnidadProyecto]:
    def _archivos_bajo(directorio: str) -> set[str]:
        """Todos los archivos rastreados dentro de ``directorio``, a
        cualquier profundidad -- hace falta recursivo (no solo hijos
        directos) para detectar un punto de entrada como
        ``edecan_core/main.py`` dentro de ``packages/core``."""
        if not directorio:
            return set(relative_paths)
        prefijo = directorio + "/"
        return {ruta for ruta in relative_paths if ruta == directorio or ruta.startswith(prefijo)}

    candidatos: dict[str, str] = {}  # directorio -> ecosistema

    def _proponer(directorio: str, ecosistema: str) -> None:
        actual = candidatos.get(directorio)
        if actual is None or _PRECEDENCIA_ECOSISTEMAS.index(
            ecosistema
        ) < _PRECEDENCIA_ECOSISTEMAS.index(actual):
            candidatos[directorio] = ecosistema

    for ruta in relative_paths:
        partes = PurePosixPath(ruta).parts
        if not partes:
            continue
        nombre = partes[-1]
        ecosistema = _ECOSISTEMA_POR_MANIFIESTO.get(nombre)
        if ecosistema is None:
            ecosistema = next(
                (eco for sufijo, eco in _SUFIJOS_MANIFIESTO if nombre.endswith(sufijo)), None
            )
        if ecosistema is not None:
            _proponer(_directorio_de(ruta), ecosistema)
        # Solo el bundle de Xcode MÁS EXTERNO: adentro de un ``.xcodeproj``
        # hay otro ``.xcworkspace``, y tomarlo en serio inventaría una unidad
        # colgando de la unidad real.
        for indice, parte in enumerate(partes[:-1]):
            if parte.endswith(_SUFIJOS_BUNDLE):
                _proponer("/".join(partes[:indice]), "swift")
                break

    unidades: list[UnidadProyecto] = []
    for directorio, ecosistema in candidatos.items():
        unidad = _CONSTRUCTORES[ecosistema](directorio, root, _archivos_bajo(directorio))
        if unidad is not None:
            unidades.append(unidad)

    # Asigna cada archivo (con líneas ya contadas) a la unidad cuyo directorio
    # sea el prefijo más largo que calce -- así un archivo de
    # ``apps/api/edecan_api/routers/ide.py`` cuenta para ``apps/api``, no para
    # la raíz, aunque la raíz también tenga ``pyproject.toml``.
    prefijos = sorted((u.ruta for u in unidades if u.ruta != "."), key=len, reverse=True)
    acumulado: dict[str, list[int]] = {u.ruta: [0, 0] for u in unidades}
    for fila in conteos:
        destino = "."
        for prefijo in prefijos:
            if fila.ruta == prefijo or fila.ruta.startswith(prefijo + "/"):
                destino = prefijo
                break
        if destino in acumulado:
            acumulado[destino][0] += 1
            acumulado[destino][1] += fila.lineas

    resultado: list[UnidadProyecto] = []
    for unidad in unidades:
        archivos_count, lineas_count = acumulado.get(unidad.ruta, [0, 0])
        resultado.append(
            UnidadProyecto(
                ruta=unidad.ruta,
                tipo=unidad.tipo,
                nombre=unidad.nombre,
                descripcion=unidad.descripcion,
                version=unidad.version,
                comandos_test=unidad.comandos_test,
                comandos_build=unidad.comandos_build,
                entradas=unidad.entradas,
                archivos=archivos_count,
                lineas=lineas_count,
            )
        )
    return sorted(resultado, key=lambda u: u.lineas, reverse=True)


def _gestor_de_unidad(root: Path, unidad: UnidadProyecto) -> str | None:
    """Con qué se instalan las dependencias de esta unidad.

    No es adorno: los gestores son de los pocos hechos ESTRUCTURALES que el
    mapa expone, y ``ide_semilla_proyecto`` los usa como anclas para decidir si
    un texto habla de este repo o de cualquier repo. Sin esto, un repo Swift o
    Go llegaba a la semilla sin una sola ancla de herramienta.
    """

    base = _base_de(root, unidad.ruta if unidad.ruta != "." else "")
    if unidad.tipo == "rust":
        return "cargo"
    if unidad.tipo == "go":
        return "go modules"
    if unidad.tipo in ("kotlin", "java"):
        return "maven" if (base / _MANIFEST_MAVEN).is_file() else "gradle"
    if unidad.tipo == "swift":
        if (base / _MANIFEST_SWIFT_SPM).is_file():
            return "swiftpm"
        return "cocoapods" if (base / _MANIFEST_SWIFT_PODS).is_file() else "xcodebuild"
    if unidad.tipo == "ruby":
        return "bundler"
    if unidad.tipo == "php":
        return "composer"
    if unidad.tipo == "dotnet":
        return "dotnet"
    return None


def _gestores_paquetes(root: Path, unidades: list[UnidadProyecto]) -> tuple[str, ...]:
    gestores: list[str] = []
    raiz = _leer_toml(root / _MANIFEST_PYTHON)
    tiene_workspace_uv = isinstance(raiz, dict) and isinstance(
        (raiz.get("tool") or {}).get("uv"), dict
    )
    if tiene_workspace_uv or (root / "uv.lock").is_file():
        gestores.append("uv (workspace Python)")
    elif any(u.tipo == "python" for u in unidades):
        gestores.append("pip (Python, sin workspace uv detectado)")
    gestores_node = sorted(
        {_gestor_node(root, u.ruta if u.ruta != "." else "") for u in unidades if u.tipo == "node"}
    )
    gestores.extend(gestores_node)
    otros = {gestor for u in unidades if (gestor := _gestor_de_unidad(root, u)) is not None}
    gestores.extend(sorted(otros))
    return tuple(gestores)


# --------------------------------------------------------------------- #
# El mapa completo.
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class MapaProyecto:
    workspace_id: str
    generado_en: str
    firma: str
    total_archivos: int
    total_lineas: int
    lineas_son_aproximadas: bool
    archivos_truncados: bool
    lenguajes: tuple[LenguajeStat, ...]
    unidades: tuple[UnidadProyecto, ...]
    gestores_paquetes: tuple[str, ...]

    def resumen(self) -> dict[str, Any]:
        return {
            "version": _MAP_FORMAT_VERSION,
            "workspace_id": self.workspace_id,
            "generado_en": self.generado_en,
            "firma": self.firma,
            "total_archivos": self.total_archivos,
            "total_lineas": self.total_lineas,
            "lineas_son_aproximadas": self.lineas_son_aproximadas,
            "archivos_truncados": self.archivos_truncados,
            "lenguajes": [stat.resumen() for stat in self.lenguajes],
            "unidades": [u.resumen() for u in self.unidades],
            "gestores_paquetes": list(self.gestores_paquetes),
        }

    @staticmethod
    def desde_resumen(data: dict[str, Any]) -> MapaProyecto:
        return MapaProyecto(
            workspace_id=str(data.get("workspace_id") or ""),
            generado_en=str(data.get("generado_en") or ""),
            firma=str(data.get("firma") or ""),
            total_archivos=int(data.get("total_archivos") or 0),
            total_lineas=int(data.get("total_lineas") or 0),
            lineas_son_aproximadas=bool(data.get("lineas_son_aproximadas")),
            archivos_truncados=bool(data.get("archivos_truncados")),
            lenguajes=tuple(
                LenguajeStat(
                    lenguaje=str(row.get("lenguaje")),
                    archivos=int(row.get("archivos") or 0),
                    lineas=int(row.get("lineas") or 0),
                )
                for row in data.get("lenguajes") or []
                if isinstance(row, dict)
            ),
            unidades=tuple(
                UnidadProyecto(
                    ruta=str(row.get("ruta")),
                    tipo=str(row.get("tipo")),
                    nombre=row.get("nombre"),
                    descripcion=row.get("descripcion"),
                    version=row.get("version"),
                    comandos_test=tuple(row.get("comandos_test") or ()),
                    comandos_build=tuple(row.get("comandos_build") or ()),
                    entradas=tuple(row.get("entradas") or ()),
                    archivos=int(row.get("archivos") or 0),
                    lineas=int(row.get("lineas") or 0),
                )
                for row in data.get("unidades") or []
                if isinstance(row, dict)
            ),
            gestores_paquetes=tuple(data.get("gestores_paquetes") or ()),
        )


# --------------------------------------------------------------------- #
# Servicio: cacheado por workspace, con invalidación por firma.
# --------------------------------------------------------------------- #


class ProjectMapService:
    """Construye y cachea el ``MapaProyecto`` de cada workspace autorizado.

    El camino feliz (``get_map`` sin cambios en el repo) solo hace ``stat``
    de cada archivo rastreado -- nunca abre uno para contar líneas ni
    reparsea un manifiesto. Solo cuando la firma cambia se paga el costo
    completo de escaneo (ver docstring del módulo).
    """

    def __init__(self, workspaces: WorkspaceStore, state_dir: Path) -> None:
        self.workspaces = workspaces
        self._cache_dir = Path(state_dir) / _MAP_CACHE_SUBDIR

    # -- API pública ------------------------------------------------------

    def get_map(self, workspace_id: str, *, force: bool = False) -> dict[str, Any]:
        """Devuelve el mapa como ``dict`` (mismo formato que
        ``MapaProyecto.resumen()``), del caché si la firma no cambió."""
        root = self.workspaces.root(workspace_id)
        relative_paths, files_truncated = _list_tracked_files(root)
        archivos_stat = _stat_tracked_files(root, relative_paths)
        firma_actual = _signature(archivos_stat)

        if not force:
            en_cache = self._read_cache(workspace_id)
            if en_cache is not None and en_cache.get("firma") == firma_actual:
                return en_cache

        mapa = self._construir(workspace_id, root, archivos_stat, firma_actual, files_truncated)
        resumen = mapa.resumen()
        self._write_cache(workspace_id, resumen)
        return resumen

    def invalidate(self, workspace_id: str | None = None) -> None:
        """Borra el/los mapa(s) cacheado(s) -- fuerza reconstrucción completa
        en el próximo ``get_map``."""
        if workspace_id is None:
            if self._cache_dir.is_dir():
                for entry in self._cache_dir.glob("*.json"):
                    entry.unlink(missing_ok=True)
            return
        self._cache_path(workspace_id).unlink(missing_ok=True)

    def render_prompt(
        self, mapa: dict[str, Any], *, budget_tokens: int = DEFAULT_PROMPT_TOKEN_BUDGET
    ) -> str:
        """Renderiza ``mapa`` (el ``dict`` que devuelve ``get_map``) como
        texto listo para meter en un prompt, respetando ``budget_tokens``.

        No es estático: es el punto de esta función. Prioriza en este orden
        cuando el texto completo no entra --
        1. descripciones completas de cada unidad → recortadas a una línea;
        2. la lista completa de unidades → solo las N con más líneas, con una
           nota de cuántas quedaron afuera;
        3. si ni así entra, corte duro del texto con marca explícita.
        """
        if not isinstance(budget_tokens, int) or budget_tokens < 50:
            raise IDEProjectMapError("budget_tokens debe ser un entero >= 50.")
        presupuesto_chars = budget_tokens * CHARS_PER_TOKEN

        objeto = mapa if isinstance(mapa, MapaProyecto) else MapaProyecto.desde_resumen(mapa)

        texto_completo = _renderizar(objeto, descripcion_corta=False, max_unidades=None)
        if len(texto_completo) <= presupuesto_chars:
            return texto_completo

        texto_recortado = _renderizar(objeto, descripcion_corta=True, max_unidades=None)
        if len(texto_recortado) <= presupuesto_chars:
            return texto_recortado

        for max_unidades in (40, 25, 15, 8, 4, 1):
            candidato = _renderizar(objeto, descripcion_corta=True, max_unidades=max_unidades)
            if len(candidato) <= presupuesto_chars:
                return candidato

        # Último recurso: corte duro con marca explícita (nunca a mitad de
        # palabra multibyte: se corta en el límite de caracteres, Python ya
        # opera sobre code points).
        corte = texto_recortado[: max(presupuesto_chars - 20, 0)]
        return corte + "\n…[mapa recortado por presupuesto]"

    # -- construcción -------------------------------------------------------

    def _construir(
        self,
        workspace_id: str,
        root: Path,
        archivos_stat: list[_ArchivoRastreado],
        firma: str,
        files_truncated: bool,
    ) -> MapaProyecto:
        relative_paths = [fila.ruta for fila in archivos_stat]
        conteos, alguna_aproximada = _contar_lineas(root, archivos_stat)
        unidades = _detectar_unidades(root, relative_paths, conteos)
        lenguajes = _agregar_lenguajes(conteos)
        gestores = _gestores_paquetes(root, unidades)
        total_lineas = sum(c.lineas for c in conteos)
        return MapaProyecto(
            workspace_id=workspace_id,
            generado_en=_utc_now_iso(),
            firma=firma,
            total_archivos=len(archivos_stat),
            total_lineas=total_lineas,
            lineas_son_aproximadas=alguna_aproximada,
            archivos_truncados=files_truncated,
            lenguajes=tuple(lenguajes),
            unidades=tuple(unidades),
            gestores_paquetes=gestores,
        )

    # -- caché en disco -- mismo patrón atómico que ``WorkspaceStore._save`` /
    # ``SemanticSearchService._write_index_file``: archivo temporal + rename.

    def _cache_path(self, workspace_id: str) -> Path:
        return self._cache_dir / f"{workspace_id}.json"

    def _read_cache(self, workspace_id: str) -> dict[str, Any] | None:
        path = self._cache_path(workspace_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or data.get("version") != _MAP_FORMAT_VERSION:
            return None
        return data

    def _write_cache(self, workspace_id: str, resumen: dict[str, Any]) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        target = self._cache_path(workspace_id)
        temp = target.with_name(f".{target.name}.tmp-{os.getpid()}")
        temp.write_text(json.dumps(resumen, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, target)


# --------------------------------------------------------------------- #
# Render a texto -- separado del servicio para poder probarlo sin
# workspace/disco de por medio.
# --------------------------------------------------------------------- #


def _renderizar(mapa: MapaProyecto, *, descripcion_corta: bool, max_unidades: int | None) -> str:
    lineas: list[str] = []
    lineas.append("# Mapa del proyecto")
    lineas.append(
        f"Generado: {mapa.generado_en} | archivos: {mapa.total_archivos}"
        + (" (truncado)" if mapa.archivos_truncados else "")
        + f" | líneas de código: {mapa.total_lineas:,}"
        + (" (aprox.)" if mapa.lineas_son_aproximadas else "")
    )
    if mapa.gestores_paquetes:
        lineas.append(f"Gestores de paquetes: {', '.join(mapa.gestores_paquetes)}")

    if mapa.lenguajes:
        lineas.append("")
        lineas.append("## Lenguajes")
        for stat in mapa.lenguajes:
            lineas.append(f"- {stat.lenguaje}: {stat.archivos} archivos, {stat.lineas:,} líneas")

    unidades = list(mapa.unidades)
    omitidas = 0
    if max_unidades is not None and len(unidades) > max_unidades:
        omitidas = len(unidades) - max_unidades
        unidades = unidades[:max_unidades]

    if unidades:
        lineas.append("")
        lineas.append("## Unidades (apps/paquetes)")
        for unidad in unidades:
            sufijo_nombre = f" — {unidad.nombre}" if unidad.nombre else ""
            lineas.append(f"- `{unidad.ruta}` ({unidad.tipo}){sufijo_nombre}")
            if unidad.descripcion:
                descripcion = unidad.descripcion
                if descripcion_corta and len(descripcion) > 90:
                    descripcion = descripcion[:87] + "…"
                lineas.append(f"  {descripcion}")
            lineas.append(f"  archivos: {unidad.archivos}, líneas: {unidad.lineas:,}")
            if unidad.comandos_test:
                lineas.append(f"  test: {', '.join(unidad.comandos_test)}")
            if unidad.comandos_build:
                lineas.append(f"  build: {', '.join(unidad.comandos_build)}")
            if unidad.entradas:
                lineas.append(f"  entrada: {', '.join(unidad.entradas)}")
        if omitidas:
            lineas.append(f"- … +{omitidas} unidades más (ver caché completo o búsqueda semántica)")

    return "\n".join(lineas)
