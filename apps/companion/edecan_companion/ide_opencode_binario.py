"""Resolución del binario ``opencode`` -- para que viaje DENTRO de la app.

# POR QUÉ existe este archivo (separado de ``ide_opencode.py``)

``ide_opencode.py`` ya sabe hablar con ``opencode serve`` una vez que tiene
una ruta a un ejecutable (su propio ``_ubicar_binario`` privado, pensado
para esta Mac de desarrollo: ``~/.opencode/bin/opencode`` o el ``PATH``).
Ese archivo es del OTRO encargo y no se toca acá (regla del encargo: "no lo
reescribas"). Este módulo resuelve un problema distinto y más amplio: la app
de escritorio del dueño se congela con PyInstaller (``apps/desktop/
packaging/edecan_local.spec`` + ``scripts/build-backend.sh``/``.ps1``) para
correr en la máquina de un cliente que **nunca instaló opencode** -- ni en
macOS, ni (sobre todo) en Windows. Si la única ruta que el código conoce es
``~/.opencode/bin/opencode``, la app queda coja fuera de esta máquina. El
cableado real de este módulo dentro de ``ide_opencode.py`` lo hace el humano
después (mismo criterio que la nota final del encargo anterior); este
archivo es el cimiento de resolución, comprobable de forma aislada.

# El orden de búsqueda -- y por qué CADA paso se comporta distinto ante un
# fallo

1. **Empaquetado dentro del bundle** (``sys._MEIPASS``, el directorio donde
   PyInstaller ``onefile`` se autoextrae en cada arranque -- mismo mecanismo
   que ya usan ``edecan_local.migrate``/``edecan_local.pg``/``edecan_local.
   runtime`` de este mismo repo para alembic/Postgres/la web estática, ver
   sus propios ``getattr(sys, "_MEIPASS", None)``). Si el proceso corre
   congelado pero NINGUNO de los candidatos existe ahí, esto NO es un error
   fatal por sí solo -- se sigue probando el siguiente escalón. Puede que la
   build todavía no incluya el sidecar, o que se esté depurando el binario
   congelado sin haber corrido el script de empaquetado completo.

2. **``EDECAN_OPENCODE_BIN``** (variable de entorno) -- mismo patrón exacto
   que ya usa este repo para Ollama: ``apps/local/edecan_local/
   ollama_supervisor.py`` lee ``EDECAN_OLLAMA_BIN``, que ``apps/desktop/
   src-tauri/src/backend.rs::with_ollama_env`` fija al lanzar el sidecar de
   ``edecan-local`` SI ``scripts/download-ollama.sh`` dejó un Ollama
   embebido. La recomendación de ``docs/opencode-empaquetado.md`` es
   exactamente ese mecanismo para opencode (sidecar de Tauri +
   ``EDECAN_OPENCODE_BIN``, no meterlo dentro del ``.spec`` de PyInstaller --
   ver ese documento para el porqué completo). A diferencia del paso 1, si
   esta variable SÍ está fijada pero no apunta a un ejecutable válido, esto
   es un error DURO -- alguien (Tauri, o una persona a mano) declaró
   explícitamente "usa este binario" y ese binario no sirve; seguir
   probando el ``PATH`` en silencio escondería justo el tipo de fallo de
   empaquetado que este módulo existe para hacer diagnosticable (regla 1 del
   encargo).

3. **``PATH`` del sistema** (``shutil.which``) -- cubre el desarrollo local
   (esta Mac, ``~/.opencode/bin`` ya está en ``PATH``, ver ``ide_opencode.py``)
   y cualquier instalación manual del dueño o de un cliente avanzado.

Si los tres escalones se agotan sin éxito, ``BinarioOpencodeNoEncontrado``
lleva un mensaje que dice QUÉ se probó y QUÉ hacer -- nunca un
``FileNotFoundError`` pelado (regla 2 del encargo).

# El problema del bit ejecutable en PyInstaller ``onefile`` + ``datas``

Si opencode SÍ se empaqueta dentro del propio ``.spec`` de PyInstaller (vía
``datas=[...]``, la alternativa que ``docs/opencode-empaquetado.md``
desaconseja pero documenta), hay un gotcha real que vale la pena dejar
escrito porque no es obvio: se verificó leyendo el propio código fuente de
PyInstaller instalado en este repo
(``.venv/lib/python3.12/site-packages/PyInstaller/building/api.py``, clase
``COLLECT``) que, al construir la distribución, un archivo de tipo
``DATA`` SOLO recibe ``chmod 0o755`` si ya tenía el bit ejecutable puesto en
el disco de origen ANTES de empaquetar (``os.access(src_name, os.X_OK)``).
Eso cubre la fase de *build*, pero la fase que importa acá es la de
*runtime*: el bootloader ``onefile`` (C, compilado, no hay fuente Python que
inspeccionar en el wheel) es quien reescribe los ``datas`` en el directorio
temporal ``sys._MEIPASS`` en CADA arranque, y no hay garantía documentada de
que preserve el bit ejecutable de un archivo de tipo ``DATA`` en esa
reescritura -- es un problema reportado repetidas veces contra PyInstaller
para binarios de terceros embebidos como dato plano. Por eso
``_asegurar_bit_ejecutable`` de este módulo vuelve a poner el bit ejecutable
a mano sobre cualquier candidato que encuentre bajo ``_MEIPASS`` antes de
darlo por bueno -- es barato, idempotente (``_MEIPASS`` es un directorio
temporal nuevo en cada arranque, así que esto corre una vez por proceso) y
no depende de confiar en un comportamiento del bootloader que no se pudo
verificar leyendo su código fuente.

# Windows: ``.exe`` y el mismo criterio que ``packages/mcp/edecan_mcp/
# transport.py``

``packages/mcp/edecan_mcp/transport.py::_argv_ejecutable`` documenta un bug
medido en Windows Server 2022 el 30-07-2026:
``asyncio.create_subprocess_exec`` (y, por el mismo motivo, ``subprocess.run``)
fallan con ``FileNotFoundError [WinError 2]`` contra un lanzador que resulta
ser un guion por lotes (``.cmd``/``.bat``, típico de instalaciones vía npm:
``npx``, ``npm``, ``yarn``), aunque ``shutil.which`` sí lo encuentre (porque
``shutil.which`` respeta ``PATHEXT`` y SÍ sabe qué es un ejecutable "para
Windows en general" -- ``CreateProcess`` no). ``edecan_companion`` está
deliberadamente aislado del resto del monorepo (no depende de
``packages/mcp``, ver ``pyproject.toml`` de este paquete) así que este
módulo trae su PROPIA copia mínima del mismo criterio en
``_argv_para_ejecutar``, en vez de importar aquel -- misma solución exacta
(resolver con ``shutil.which``, y si el resultado es un guion por lotes,
delegar en ``cmd.exe /c`` con el argv todavía separado, JAMÁS
``shell=True``), aplicada acá.

El binario real de opencode que el dueño ya tiene instalado en esta Mac
(``~/.opencode/bin/opencode``, verificado con ``file``: ``Mach-O 64-bit
executable``) NO es un guion por lotes -- es un ejecutable nativo
compilado. Pero este módulo no puede asumir que TODA instalación de
opencode en Windows use el mismo empaquetado nativo: algunas herramientas
de este mismo ecosistema (Node/Bun) se distribuyen con un shim ``.cmd`` que
reenvía a un binario real. Aplicar el criterio de todas formas cuesta poco
y evita repetir el mismo bug que ya costó una investigación completa en
``transport.py``.

Aclaración honesta (regla 6, "nada simulado"): esta sesión corre en macOS
-- no hay manera de verificar contra un Windows real el comportamiento de
``cmd.exe /c`` con opencode acá. Lo que SÍ está verificado en esta Mac: la
resolución por ``PATH``, por variable de entorno y por bundle simulado
(``sys._MEIPASS`` de prueba), más ``opencode --version`` contra el binario
real. La rama Windows del armado de argv se prueba a nivel de LÓGICA PURA
(``monkeypatch`` de ``os.name``, sin ejecutar nada) -- ver
``test_ide_opencode_binario.py`` y su docstring para el detalle exacto de
qué quedó probado end-to-end y qué quedó probado solo como lógica.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Contrato de nombres -- ver docs/opencode-empaquetado.md para el porqué de
# cada uno.
# --------------------------------------------------------------------------- #

#: Variable de entorno que declara la ruta explícita al binario -- mismo
#: patrón que ``EDECAN_OLLAMA_BIN`` (``ollama_supervisor.py``) y
#: ``EDECAN_STUDIO_NODE_BINARY`` (``edecan_api/config.py``).
VARIABLE_ENTORNO_BINARIO = "EDECAN_OPENCODE_BIN"

#: Versión mínima esperada -- la que el dueño verificó a mano en esta Mac
#: antes de empezar la migración (ver MEMORY.md de esa investigación:
#: ``opencode --version`` -> ``1.17.18``). No es un mínimo teórico: es la
#: última versión contra la que se comprobó que la API bajo ``/api`` (la que
#: usa ``ide_opencode.py``) tiene la forma documentada acá.
VERSION_MINIMA_ESPERADA: tuple[int, int, int] = (1, 17, 18)

#: Nombre de la carpeta dentro del bundle donde ``docs/opencode-empaquetado.md``
#: recomienda dejar el sidecar, para que este módulo y quien arme el paquete
#: compartan el mismo contrato de ruta.
_SUBCARPETA_BUNDLE = "opencode"

_PATRON_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")

_TIMEOUT_VERSION_SEGUNDOS = 10.0


class BinarioOpencodeNoEncontrado(RuntimeError):
    """No se encontró un ``opencode`` ejecutable válido.

    El mensaje siempre explica QUÉ se probó y QUÉ hacer a continuación --
    nunca es un ``FileNotFoundError`` pelado (regla 2 del encargo).
    """


Origen = Literal["empaquetado", "variable_entorno", "ruta_sistema"]


@dataclass(slots=True, frozen=True)
class BinarioOpencode:
    """Resultado de :func:`resolver_binario_opencode` -- de dónde salió el
    binario y qué tan seguro es usarlo, para que un fallo aguas abajo se
    pueda diagnosticar sin adivinar."""

    ruta: Path
    origen: Origen
    version: str | None
    version_suficiente: bool | None
    """``None`` cuando no se pudo determinar la versión instalada (el
    binario no respondió a ``--version`` a tiempo, o su salida no traía un
    número reconocible) -- distinto de ``False`` (sí se determinó, y es más
    vieja que :data:`VERSION_MINIMA_ESPERADA`)."""
    advertencia: str | None
    """Mensaje humano listo para mostrar/loguear si ``version_suficiente``
    no es ``True``. ``None`` si todo está en orden."""


# --------------------------------------------------------------------------- #
# Windows: mismo criterio que packages/mcp/edecan_mcp/transport.py --
# ver el docstring largo del módulo para el porqué completo.
# --------------------------------------------------------------------------- #


def _argv_para_ejecutar(ruta: Path, *args: str) -> list[str]:
    """Arma el argv que ``subprocess`` puede lanzar de verdad para ``ruta``.

    En POSIX, o si ``ruta`` no es un guion por lotes, es solo ``[ruta,
    *args]``. En Windows, si ``ruta`` termina en ``.cmd``/``.bat``, se
    delega en ``cmd.exe /c`` con el argv todavía separado -- NUNCA
    ``shell=True`` (inyección de shell con cualquier argumento que traiga
    comillas o ``&``). Ver el docstring del módulo, sección Windows, para
    la cita completa del bug que esto evita.
    """
    if os.name != "nt" or ruta.suffix.lower() not in (".cmd", ".bat"):
        return [str(ruta), *args]
    comspec = os.environ.get("COMSPEC") or "cmd.exe"
    return [comspec, "/c", str(ruta), *args]


def _nombre_binario() -> str:
    return "opencode.exe" if os.name == "nt" else "opencode"


# --------------------------------------------------------------------------- #
# Paso 1: empaquetado dentro del bundle.
# --------------------------------------------------------------------------- #


def _candidatos_bundle() -> list[Path]:
    """Rutas donde este módulo busca un opencode empaquetado dentro de
    ``sys._MEIPASS`` -- ver ``docs/opencode-empaquetado.md`` para el
    contrato exacto que debe cumplir quien arma el ``.spec``. Se listan dos
    formas a propósito (con y sin la subcarpeta ``bin/``) porque el mismo
    binario, corriente arriba, se instala en ``~/.opencode/bin/opencode``
    -- conservar esa forma dentro del bundle facilita comparar rutas a
    simple vista al depurar; la forma plana es el respaldo más simple si
    quien arma el paquete prefiere no replicar esa jerarquía.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return []
    base = Path(meipass) / _SUBCARPETA_BUNDLE
    nombre = _nombre_binario()
    return [base / "bin" / nombre, base / nombre]


def _asegurar_bit_ejecutable(ruta: Path) -> None:
    """Fuerza el bit ejecutable sobre un candidato encontrado bajo
    ``_MEIPASS`` -- ver "El problema del bit ejecutable" en el docstring del
    módulo. Mejor esfuerzo: si ``chmod`` falla (permisos del directorio
    temporal, sistema de archivos raro), no revienta la resolución -- el
    siguiente chequeo (``os.access(..., os.X_OK)``) simplemente descarta el
    candidato y se prueba el próximo escalón, con un log que deja el motivo
    real, no silencioso.
    """
    if os.name == "nt":
        return  # Windows no tiene bit ejecutable POSIX; la extensión ya decide.
    try:
        modo_actual = ruta.stat().st_mode
        os.chmod(ruta, modo_actual | 0o111)
    except OSError as exc:
        logger.warning("No se pudo asegurar el bit ejecutable de %s: %s", ruta, exc)


def _es_ejecutable(ruta: Path) -> bool:
    if not ruta.is_file():
        return False
    if os.name == "nt":
        return True  # Sin bit POSIX; ya se llegó acá por una ruta con la extensión correcta.
    return os.access(ruta, os.X_OK)


def _resolver_bundle() -> Path | None:
    for candidato in _candidatos_bundle():
        if not candidato.is_file():
            continue
        _asegurar_bit_ejecutable(candidato)
        if _es_ejecutable(candidato):
            return candidato
        logger.warning(
            "Encontré %s empaquetado pero no quedó ejecutable tras chmod -- se descarta.",
            candidato,
        )
    return None


# --------------------------------------------------------------------------- #
# Paso 2: variable de entorno.
# --------------------------------------------------------------------------- #


def _resolver_variable_entorno() -> Path | None:
    """``None`` si la variable no está fijada (escalón normal, sigue
    probando). Si SÍ está fijada pero no sirve, lanza de inmediato -- ver
    "por qué CADA paso se comporta distinto" en el docstring del módulo:
    una declaración explícita rota debe fallar fuerte, no esconderse
    detrás del siguiente escalón.
    """
    valor = os.environ.get(VARIABLE_ENTORNO_BINARIO)
    if not valor:
        return None
    ruta = Path(valor)
    if _es_ejecutable(ruta):
        return ruta
    razon = "no existe" if not ruta.is_file() else "no tiene permiso de ejecución"
    raise BinarioOpencodeNoEncontrado(
        f"{VARIABLE_ENTORNO_BINARIO}={valor!r} está fijada pero la ruta {razon}. "
        "Esa variable la debería fijar el empaquetado (ver "
        "docs/opencode-empaquetado.md) -- revisa el sidecar que se copió, o "
        "corrige/quita la variable si la fijaste a mano."
    )


# --------------------------------------------------------------------------- #
# Paso 3: PATH del sistema.
# --------------------------------------------------------------------------- #


def _resolver_ruta_sistema() -> Path | None:
    """``shutil.which("opencode")`` respeta ``PATHEXT`` en Windows por su
    cuenta (encuentra ``opencode.exe``/``.cmd``/``.bat`` sin que este módulo
    tenga que probar cada extensión a mano) -- por eso acá se busca siempre
    el nombre sin extensión, a diferencia de los candidatos del bundle (que
    sí son rutas de archivo exactas, ``shutil.which`` no interviene ahí).
    """
    encontrado = shutil.which("opencode")
    return Path(encontrado) if encontrado else None


# --------------------------------------------------------------------------- #
# Versión.
# --------------------------------------------------------------------------- #


def _version_binario(ruta: Path) -> str | None:
    """Corre ``<ruta> --version`` y extrae un ``X.Y.Z`` de la salida.

    Best-effort a propósito: un fallo acá (timeout, el binario no reconoce
    ``--version``, permisos) NO debe tumbar la resolución del binario --
    solo deja ``version=None`` en el resultado, con el motivo real en el
    log (nunca tragado en silencio, regla 6).
    """
    argv = _argv_para_ejecutar(ruta, "--version")
    try:
        resultado = subprocess.run(  # noqa: S603 - argv de lista, jamás shell=True
            argv,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_VERSION_SEGUNDOS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning(
            "No se pudo ejecutar «%s --version» para verificar la versión: %s", ruta, exc
        )
        return None
    salida = f"{resultado.stdout}\n{resultado.stderr}"
    coincidencia = _PATRON_VERSION.search(salida)
    if coincidencia is None:
        logger.warning(
            "«%s --version» no devolvió un número de versión reconocible (salida: %r).",
            ruta,
            salida.strip()[:200],
        )
        return None
    return coincidencia.group(0)


def _version_tupla(version: str) -> tuple[int, int, int] | None:
    coincidencia = _PATRON_VERSION.search(version)
    if coincidencia is None:
        return None
    return (int(coincidencia.group(1)), int(coincidencia.group(2)), int(coincidencia.group(3)))


def _evaluar_version(ruta: Path) -> tuple[str | None, bool | None, str | None]:
    """Devuelve ``(version, version_suficiente, advertencia)`` para
    :class:`BinarioOpencode`."""
    version = _version_binario(ruta)
    if version is None:
        advertencia = (
            f"No pude determinar la versión de opencode en {ruta} -- lo uso igual, "
            "pero si algo falla más adelante contra su API, verifica a mano con "
            f"«{ruta} --version» (se espera al menos "
            f"{'.'.join(map(str, VERSION_MINIMA_ESPERADA))})."
        )
        return None, None, advertencia
    tupla = _version_tupla(version)
    if tupla is None or tupla >= VERSION_MINIMA_ESPERADA:
        return version, True, None
    minima = ".".join(map(str, VERSION_MINIMA_ESPERADA))
    advertencia = (
        f"opencode en {ruta} reporta la versión {version}, más vieja que la mínima "
        f"esperada por este código ({minima}). Puede funcionar igual, pero si algo "
        "falla contra la API documentada en ide_opencode.py, actualiza opencode "
        "primero antes de seguir depurando."
    )
    return version, False, advertencia


# --------------------------------------------------------------------------- #
# Punto de entrada público.
# --------------------------------------------------------------------------- #


def resolver_binario_opencode(*, verificar_version: bool = True) -> BinarioOpencode:
    """Encuentra el binario ``opencode`` a usar, en el orden documentado en
    el docstring del módulo: empaquetado -> variable de entorno -> ``PATH``.

    ``verificar_version=False`` salta la llamada a ``--version`` (útil para
    resolución rápida cuando quien llama ya sabe que va a comprobar la
    versión de otra forma, o en un test que solo quiere la ruta).

    Lanza :class:`BinarioOpencodeNoEncontrado` si:

    - ninguno de los tres escalones encontró un ejecutable válido, o
    - :data:`VARIABLE_ENTORNO_BINARIO` estaba fijada mirando a una ruta
      inválida (fallo fuerte, ver el docstring del módulo).
    """
    ruta = _resolver_bundle()
    origen: Origen = "empaquetado"

    if ruta is None:
        ruta = _resolver_variable_entorno()
        origen = "variable_entorno"

    if ruta is None:
        ruta = _resolver_ruta_sistema()
        origen = "ruta_sistema"

    if ruta is None:
        candidatos_bundle = _candidatos_bundle()
        bundle_txt = (
            ", ".join(str(c) for c in candidatos_bundle)
            if candidatos_bundle
            else "no aplica (el proceso no está corriendo congelado con PyInstaller)"
        )
        raise BinarioOpencodeNoEncontrado(
            "No encontré un binario de opencode utilizable. Probé, en este orden:\n"
            f"  1. Empaquetado en el bundle: {bundle_txt}\n"
            f"  2. Variable de entorno {VARIABLE_ENTORNO_BINARIO}: no está fijada\n"
            '  3. PATH del sistema: shutil.which("opencode") no encontró nada\n'
            "\n"
            "Qué hacer:\n"
            "  - En desarrollo: instala opencode (github.com/anomalyco/opencode) y "
            "deja que quede en tu PATH, o fija "
            f"{VARIABLE_ENTORNO_BINARIO} a la ruta completa del ejecutable.\n"
            "  - En la app empaquetada: falta el sidecar de opencode en esta build "
            "-- ver docs/opencode-empaquetado.md."
        )

    version: str | None = None
    version_suficiente: bool | None = None
    advertencia: str | None = None
    if verificar_version:
        version, version_suficiente, advertencia = _evaluar_version(ruta)
        if advertencia:
            logger.warning(advertencia)

    logger.info(
        "opencode resuelto vía %s: %s (versión: %s)", origen, ruta, version or "desconocida"
    )
    return BinarioOpencode(
        ruta=ruta,
        origen=origen,
        version=version,
        version_suficiente=version_suficiente,
        advertencia=advertencia,
    )
