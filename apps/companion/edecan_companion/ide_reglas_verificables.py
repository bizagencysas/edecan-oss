"""Reglas del proyecto que se comprueban solas, con evidencia concreta.

``ide_reglas.py`` resuelve la mitad barata del problema: encuentra el
``AGENTS.md`` del repo y lo mete al prompt. Cumplirlo depende de que el modelo
lo lea y lo interprete igual cada vez -- y con diez sub-agentes en paralelo,
"igual cada vez" no es una garantía, es una esperanza. Un chequeo automático se
cumple idéntico en los diez.

La distinción que decide este archivo
-------------------------------------
Hay reglas COMPROBABLES y reglas de CRITERIO:

- "no hay credenciales versionadas" -> comprobable (hay código que lo dice).
- "no queda un marcador de conflicto sin resolver" -> comprobable.
- "piensa como quien ataca" -> NO comprobable. Es criterio, y está bien que lo
  sea: se queda en el prompt, con ``ide_reglas.as_prompt_block()``.

Agregarle metadatos (prioridad, condiciones, excepciones) a una regla de
criterio no la vuelve ejecutable: la vuelve más prosa que el modelo tiene que
interpretar. Por eso el catálogo de este archivo es SOLO de las comprobables, y
cada una trae su chequeo de verdad -- código que corre y devuelve
cumple/no cumple/no aplica con archivo y línea. Empieza con seis reales en vez
de cincuenta con metadatos que nadie ejecuta.

Decisiones que valen la pena explicar
-------------------------------------
1. **``no_aplica`` no es opcional.** Una regla de Python en un repo de Swift no
   es un incumplimiento. Reportarla como tal entrena a ignorar el reporte
   entero, que es la única forma real de que esta pieza falle.
2. **Hay un cuarto estado, ``error``, y existe por lo mismo.** Cuando un
   chequeo no pudo correr (git ausente, permisos, se pasó del tope de tiempo),
   decir ``cumple`` es mentir y decir ``no_cumple`` es un falso positivo.
   ``error`` dice la verdad: no se sabe. Un chequeo que revienta NO tumba a los
   demás -- cada uno corre aislado.
3. **Un chequeo incompleto que no encontró nada dice ``cumple`` con
   ``parcial=True``**, no ``error``: en un repo enorme el recorte por topes es
   lo normal, y marcarlo como error convertiría cada corrida en ruido. Al revés
   no aplica: encontrar evidencia SÍ es definitivo aunque el recorrido se haya
   quedado a medias.
4. **La evidencia nunca incluye el contenido del archivo, solo
   ``archivo:línea``.** Mismo principio que ``edecan_toolkit.seguridad``: un
   reporte que cita la línea encontrada terminaría copiando la credencial (o la
   ruta con el usuario real de la máquina) al prompt, a los logs y al historial.
   Con la ubicación alcanza para que el agente la abra y la arregle.
5. **Los patrones están escritos para no encontrarse a sí mismos.** Este
   archivo vive dentro de un repo que el propio Edecán puede tener abierto como
   workspace, así que los marcadores de conflicto se buscan con ``<{7}`` en vez
   del literal y las demás expresiones se comprobaron contra este mismo fuente.
   Un falso positivo en la primera corrida es exactamente lo que entrena a
   ignorar el reporte.
6. **El reporte es de lo que el agente TOCÓ, no del repo entero.** Es la
   decisión que decide si esta pieza sirve. Un chequeo del workspace completo
   sobre un monorepo de 2.500 archivos le manda al agente los problemas de
   gente que no conoce, en código que no abrió, y le dice "arregla esto": lo
   pone a pagar deuda ajena en vez de revisar su propio trabajo, y de paso le
   enseña que este bloque casi nunca habla de él -- que es la forma en que un
   reporte se vuelve invisible. Por eso toda corrida lleva un ``Alcance``
   (``alcance_de_la_sesion`` compara contra el commit donde arrancó la sesión;
   ``alcance_de_archivos`` recibe la lista exacta si quien llama ya la tiene).
   Cuando NO se puede acotar -- no hay git, o la sesión no guardó su punto de
   partida -- se revisa el workspace completo, porque callarse es peor, pero el
   bloque del prompt lo DICE: avisa que lo de abajo puede ser preexistente y
   que no salió de este turno. Acotar además hace que el chequeo salga gratis:
   revisar los archivos del diff es leer cinco archivos, no recorrer el repo.

Este módulo es autónomo, como ``ide_reglas.py`` y ``ide_verificacion.py``: no
importa ``ide_sessions.py`` ni ``routers/ide.py``. La integración la hace quien
cablee esto: guarda ``punto_de_partida(raiz)`` al abrir la sesión, llama a
``verificar_todas(raiz, alcance=alcance_de_la_sesion(raiz, base))`` al cerrarla
y pasa ``ReporteVerificacion.as_prompt_block()`` al prompt (o el ``resumen()``
a la UI).

Lo que este módulo NO hace es hablar con ``ide_git.py``, y no por descuido:
``GitService`` trabaja sobre ``workspace_id`` contra un ``WorkspaceStore``, y
acá lo único que hay es una carpeta. Importarlo obligaría a este módulo a
depender del registro de workspaces del IDE para responder una pregunta que se
contesta con dos ``git diff``. Lo que sí se le copia es la disciplina: argv
fijo, ``shell=False`` y tope de tiempo, que es lo que ya hacía ``_correr_git``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as _TiempoAgotado
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from edecan_companion.ide_verificacion import detectar_comando_de_verificacion

# --------------------------------------------------------------------- #
# Topes. Un chequeo lento bloquea el turno del agente, así que todos los
# límites son explícitos y ninguno depende de la red.
# --------------------------------------------------------------------- #

# Por regla. Cinco segundos alcanzan de sobra para recorrer un repo normal y
# son poco comparados con una llamada al modelo, que es lo que viene después.
TOPE_SEGUNDOS_POR_REGLA = 5.0
# Para la corrida completa. Con seis reglas el caso normal está muy por debajo;
# esto existe para el repo patológico, no para el común.
TOPE_SEGUNDOS_TOTAL = 20.0
# Tope de subprocesos (git). Más corto: git responde en milisegundos o algo
# anda mal (un lock, un repo corrupto) y no vale la pena esperarlo.
_TOPE_SEGUNDOS_GIT = 3.0

# Topes del recorrido de archivos. Que el recorrido esté acotado por cantidad y
# por bytes -- no solo por reloj -- hace que el resultado sea el mismo en una
# máquina lenta y en una rápida, que es lo que uno quiere de un chequeo.
#
# Los números salen de una medición, no del aire: este monorepo son 2.592
# archivos de texto y 27,7 MB, que se recorren y leen en 0,36 s. Los topes
# quedan holgados por encima de eso para que un repo grande de verdad no diga
# "revisión parcial" en cada corrida, y siguen siendo un techo para que el
# chequeo no se coma la memoria de la máquina del usuario en un repo que
# versiona datos.
_MAX_ARCHIVOS = 12_000
_MAX_BYTES_POR_ARCHIVO = 512_000
_MAX_BYTES_TOTALES = 48_000_000

# Cuántas ubicaciones se reportan por regla. Veinte archivos con el mismo
# problema no necesitan veinte líneas en el prompt: con una docena y el conteo
# total el agente ya sabe qué hacer.
MAX_EVIDENCIAS_POR_REGLA = 12

# Carpetas que nunca se recorren: son dependencias o salidas de build. Se podan
# antes de descender (por eso `os.walk` y no `Path.rglob`): en un repo con
# `node_modules`, descender y luego descartar es la diferencia entre un chequeo
# de medio segundo y uno de un minuto.
_CARPETAS_IGNORADAS = frozenset(
    {
        ".build",
        ".git",
        ".gradle",
        ".idea",
        ".mypy_cache",
        ".next",
        ".pytest_cache",
        ".ruff_cache",
        ".turbo",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "target",
        "vendor",
        "venv",
    }
)

# Extensiones que se leen al recorrer. Es un superconjunto: cada regla filtra
# después las que le sirven, pero el archivo se lee UNA sola vez por corrida.
_EXTENSIONES_TEXTO = frozenset(
    {
        ".c",
        ".cc",
        ".cfg",
        ".conf",
        ".cjs",
        ".cpp",
        ".cs",
        ".css",
        ".go",
        ".gradle",
        ".h",
        ".hpp",
        ".html",
        ".ini",
        ".java",
        ".js",
        ".json",
        ".jsx",
        ".kt",
        ".kts",
        ".md",
        ".mjs",
        ".php",
        ".py",
        ".rb",
        ".rs",
        ".scss",
        ".sh",
        ".sql",
        ".svelte",
        ".swift",
        ".toml",
        ".ts",
        ".tsx",
        ".vue",
        ".yaml",
        ".yml",
    }
)

# Subconjunto "esto es código fuente". Las reglas de marcadores de conflicto,
# depuración olvidada y rutas absolutas se limitan a estos: en un `.md` o un
# `.yml` los mismos textos son documentación o configuración legítima (un
# README que explica cómo se ve un conflicto, un workflow de CI con la ruta
# absoluta de su runner), y reportarlos sería el falso positivo clásico.
_EXTENSIONES_CODIGO = frozenset(
    {
        ".c",
        ".cc",
        ".cjs",
        ".cpp",
        ".cs",
        ".go",
        ".h",
        ".hpp",
        ".java",
        ".js",
        ".jsx",
        ".kt",
        ".kts",
        ".mjs",
        ".php",
        ".py",
        ".rb",
        ".rs",
        ".sh",
        ".sql",
        ".svelte",
        ".swift",
        ".ts",
        ".tsx",
        ".vue",
    }
)


class IDEReglasVerificablesError(ValueError):
    """Raíz de workspace inválida al verificar las reglas del proyecto."""


class EstadoRegla(StrEnum):
    """Los cuatro desenlaces posibles de un chequeo. Ver puntos 1 y 2 del
    docstring del módulo: ``no_aplica`` y ``error`` existen para no inventar
    incumplimientos donde no los hay."""

    CUMPLE = "cumple"
    NO_CUMPLE = "no_cumple"
    NO_APLICA = "no_aplica"
    ERROR = "error"


class AmbitoDeRegla(StrEnum):
    """Sobre qué mira una regla, que decide si el alcance la recorta o no.

    Casi todas son ``POR_ARCHIVO`` y se acotan a lo que cambió. Las
    ``DEL_PROYECTO`` miran una propiedad del repo entero -- "existe un comando
    de tests" no es de ningún archivo en particular -- y no tiene sentido
    recortarlas. Pero tampoco se pueden mezclar con las otras en el prompt: si
    el agente no tocó el manifiesto, eso NO lo rompió él, y el bloque lo dice
    aparte para que no se ponga a arreglar algo que ya estaba así.
    """

    POR_ARCHIVO = "por_archivo"
    DEL_PROYECTO = "del_proyecto"


@dataclass(frozen=True)
class Evidencia:
    """Dónde está el problema. Nunca QUÉ dice la línea -- ver punto 4."""

    ruta: str
    linea: int | None
    detalle: str

    def como_texto(self) -> str:
        ubicacion = f"{self.ruta}:{self.linea}" if self.linea is not None else self.ruta
        return f"{ubicacion} — {self.detalle}"

    def resumen(self) -> dict[str, Any]:
        return {"ruta": self.ruta, "linea": self.linea, "detalle": self.detalle}


@dataclass(frozen=True)
class Veredicto:
    """Lo que devuelve la función de chequeo de una regla.

    No trae el id ni la descripción a propósito: esos viven en
    ``ReglaVerificable`` y repetirlos aquí sería una segunda fuente de verdad
    que tarde o temprano se desincroniza. ``ReglaVerificable.verificar`` los
    pega para armar el ``ResultadoRegla`` público.
    """

    estado: EstadoRegla
    mensaje: str
    evidencia: tuple[Evidencia, ...] = ()
    parcial: bool = False
    total_hallazgos: int | None = None


@dataclass(frozen=True)
class ResultadoRegla:
    """El veredicto de una regla, ya identificado y con su costo en tiempo."""

    regla_id: str
    descripcion: str
    estado: EstadoRegla
    mensaje: str
    evidencia: tuple[Evidencia, ...]
    parcial: bool
    total_hallazgos: int | None
    duracion_segundos: float
    ambito: AmbitoDeRegla = AmbitoDeRegla.POR_ARCHIVO

    def resumen(self) -> dict[str, Any]:
        return {
            "regla_id": self.regla_id,
            "descripcion": self.descripcion,
            "ambito": str(self.ambito),
            "estado": str(self.estado),
            "mensaje": self.mensaje,
            "evidencia": [item.resumen() for item in self.evidencia],
            "parcial": self.parcial,
            "total_hallazgos": self.total_hallazgos,
            "duracion_segundos": round(self.duracion_segundos, 3),
        }


# --------------------------------------------------------------------- #
# El alcance: qué archivos mira una corrida. Ver punto 6 del docstring.
# --------------------------------------------------------------------- #


class OrigenDelAlcance(StrEnum):
    """De dónde salió la lista de archivos a revisar.

    Importa que viaje en el reporte y no solo la lista: el prompt tiene que
    poder decir "esto es tuyo" o "esto puede ser preexistente", y esa frase
    depende de si el recorte se pudo hacer o no.
    """

    DIFF_DE_LA_SESION = "diff_de_la_sesion"
    LISTA_EXPLICITA = "lista_explicita"
    WORKSPACE_COMPLETO = "workspace_completo"


# Misma forma de referencia que acepta ``ide_git``: empieza con alfanumérico
# (así ningún "base" puede colarse como una opción de git), sin `..` y acotada.
_REFERENCIA_GIT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")


@dataclass(frozen=True)
class Alcance:
    """Qué archivos mira esta corrida, y por qué esos.

    ``archivos`` en ``None`` significa el workspace entero. No es lo mismo que
    un conjunto vacío: vacío quiere decir "se comparó y no cambió nada", que es
    un resultado legítimo y el más barato de todos.
    """

    origen: OrigenDelAlcance
    archivos: frozenset[str] | None
    base: str | None = None
    # Por qué no se pudo acotar. Va tal cual al prompt, así que se escribe para
    # que lo lea el agente y no para un log.
    motivo: str = ""

    @property
    def acotado(self) -> bool:
        return self.archivos is not None

    def contiene(self, ruta: str) -> bool:
        return self.archivos is None or ruta in self.archivos

    def limitar(self, rutas: Sequence[str]) -> tuple[str, ...]:
        return tuple(ruta for ruta in rutas if self.contiene(ruta))

    def resumen(self) -> dict[str, Any]:
        # La lista de archivos NO va en el resumen: en una corrida sin acotar
        # son miles de rutas, y quien lea esto (la UI, un log) necesita saber
        # el alcance, no repetir el árbol del repo.
        return {
            "origen": str(self.origen),
            "acotado": self.acotado,
            "cantidad_archivos": None if self.archivos is None else len(self.archivos),
            "base": self.base,
            "motivo": self.motivo,
        }


def alcance_completo(motivo: str) -> Alcance:
    """Revisar todo el workspace, diciendo por qué no se pudo acotar."""

    return Alcance(origen=OrigenDelAlcance.WORKSPACE_COMPLETO, archivos=None, motivo=motivo)


def alcance_de_archivos(rutas: Sequence[str]) -> Alcance:
    """Alcance explícito: quien llama ya sabe qué archivos se tocaron.

    Es la opción más honesta cuando existe -- el IDE ya lleva la cuenta de lo
    que el agente escribió (``ide_checkpoints`` registra cada archivo que
    toca), y esa lista no incluye lo que ya estaba sucio antes de empezar, cosa
    que un diff contra el punto de partida sí incluye.

    Las rutas son relativas a la raíz y en formato posix, igual que las que
    imprime git. Una ruta absoluta o con ``..`` es un error de quien llama, no
    algo que este módulo deba adivinar: se rechaza en vez de recortarse en
    silencio, porque un alcance que calla lo que descartó miente sobre lo que
    revisó.
    """

    limpias: set[str] = set()
    for ruta in rutas:
        if not isinstance(ruta, str) or not ruta.strip() or "\0" in ruta:
            raise IDEReglasVerificablesError("El alcance trae una ruta vacía o inválida.")
        normalizada = ruta.replace("\\", "/").removeprefix("./")
        if normalizada.startswith("/") or ".." in normalizada.split("/"):
            raise IDEReglasVerificablesError(
                "El alcance solo acepta rutas relativas a la raíz del workspace."
            )
        limpias.add(normalizada)
    return Alcance(origen=OrigenDelAlcance.LISTA_EXPLICITA, archivos=frozenset(limpias))


def punto_de_partida(workspace_root: Path) -> str | None:
    """El commit contra el que se comparará después. Se guarda al ABRIR la sesión.

    Devuelve ``None`` cuando no hay contra qué comparar (no es un repo git, o
    es uno recién creado sin commits). En ese caso ``alcance_de_la_sesion`` cae
    al workspace completo y lo dice, que es lo único honesto que queda.

    Aviso para quien lo cablee: lo que estuviera sucio ANTES de arrancar
    también aparece en ese diff. Si el IDE ya sabe qué archivos escribió el
    agente, ``alcance_de_archivos`` es más preciso que esto.
    """

    salida = _correr_git(_raiz_valida(workspace_root), ["rev-parse", "HEAD"])
    if salida is None:
        return None
    commit = salida.strip()
    return commit or None


def alcance_de_la_sesion(workspace_root: Path, base: str | None) -> Alcance:
    """Los archivos que cambiaron desde ``base``, incluidos los sin versionar.

    Se piden dos cosas a git y no una: ``diff`` contra el punto de partida (lo
    que se modificó, esté commiteado o no) y ``ls-files --others`` (lo que el
    agente creó y todavía no versiona). Sin lo segundo, un archivo nuevo con
    una credencial adentro no lo vería nadie, que es justo el caso que más
    importa. ``--exclude-standard`` deja fuera lo que el ``.gitignore`` ya
    cubre.

    Cualquier tropiezo -- sin base, sin git, una referencia que git no conoce
    -- devuelve ``alcance_completo`` con el motivo escrito. Inventar un alcance
    vacío sería peor que revisar de más: diría "no hay nada que revisar" sin
    haber revisado nada.
    """

    raiz = _raiz_valida(workspace_root)
    if not base:
        return alcance_completo("esta sesión no guardó su punto de partida")
    if not _REFERENCIA_GIT.fullmatch(base) or ".." in base:
        return alcance_completo("el punto de partida no es una referencia de git válida")

    dentro_de_git = _correr_git(raiz, ["rev-parse", "--is-inside-work-tree"])
    if dentro_de_git is None or dentro_de_git.strip() != "true":
        return alcance_completo("el workspace no es un repositorio git")

    cambiados = _correr_git(raiz, ["diff", "--name-only", "-z", base, "--"])
    if cambiados is None:
        return alcance_completo(f"git no pudo comparar contra {base}")
    nuevos = _correr_git(raiz, ["ls-files", "--others", "--exclude-standard", "-z"]) or ""

    rutas = {ruta for ruta in (cambiados + "\0" + nuevos).split("\0") if ruta.strip()}
    return Alcance(
        origen=OrigenDelAlcance.DIFF_DE_LA_SESION,
        archivos=frozenset(rutas),
        base=base,
    )


# --------------------------------------------------------------------- #
# Contexto: el recorrido del workspace, hecho una vez y compartido.
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class _ArchivoLeido:
    ruta: str  # relativa a la raíz, en formato posix (igual que las de git)
    sufijo: str
    texto: str


@dataclass(frozen=True)
class _Escaneo:
    archivos: tuple[_ArchivoLeido, ...]
    truncado: bool


class ContextoDeChequeo:
    """Estado compartido por los chequeos de UNA corrida sobre un workspace.

    Existe por una razón concreta: sin él, cinco reglas que miran el contenido
    de los archivos recorrerían el repo cinco veces y lo leerían cinco veces.
    Se construye con la raíz del workspace y cachea, perezosamente, el
    recorrido y las respuestas de git.

    Los candados no son decorativos: ``verificar_todas`` corre cada regla en un
    hilo con tope de tiempo, y una regla que se pasó del tope sigue viva
    mientras la siguiente ya arrancó. Sin candado, dos hilos construirían el
    caché a la vez.

    Y son DOS candados, no uno, porque los dos cachés no cuestan lo mismo: el
    recorrido de archivos tarda segundos en un repo grande y git responde en
    milisegundos. Con un candado compartido, una regla que solo necesita git
    (``sin-generados-versionados``) se quedaba esperando el recorrido de otra
    que ya se había pasado de su tope, y se pasaba del suyo sin haber hecho
    nada. Medido con el recorrido a 3 s y el tope a 1 s: tres reglas en
    ``error`` cuando solo una tenía por qué estarlo.
    """

    def __init__(self, raiz: Path, alcance: Alcance | None = None) -> None:
        self.raiz = raiz
        self.alcance = (
            alcance
            if alcance is not None
            else alcance_completo("quien llamó al chequeo no indicó qué archivos cambiaron")
        )
        self._candado_escaneo = threading.Lock()
        self._candado_git = threading.Lock()
        self._escaneo: _Escaneo | None = None
        self._es_git: bool | None = None
        self._archivos_de_git: tuple[str, ...] | None = None
        self._git_ya_intentado = False

    # -- archivos ------------------------------------------------------ #

    def escaneo(self) -> _Escaneo:
        """Los archivos de texto del alcance, leídos una sola vez.

        Con alcance acotado no se recorre el repo: se abren las rutas y ya. La
        diferencia es todo el costo de la pieza, medida sobre este monorepo:
        la corrida completa del catálogo son 2,07 s (2.568 archivos, 26,6 MB),
        la misma corrida acotada a los 5 archivos de un turno normal son
        0,060 s, y acotada a un diff gordo de 92 archivos, 0,29 s. El chequeo
        no es lento: era lento porque miraba lo que no le tocaba.
        """

        with self._candado_escaneo:
            if self._escaneo is None:
                rutas = self.alcance.archivos
                if rutas is None:
                    self._escaneo = _recorrer_archivos_de_texto(self.raiz)
                else:
                    self._escaneo = _leer_archivos_del_alcance(self.raiz, rutas)
            return self._escaneo

    def archivos(self, extensiones: frozenset[str]) -> tuple[_ArchivoLeido, ...]:
        return tuple(item for item in self.escaneo().archivos if item.sufijo in extensiones)

    def sin_archivos_en_alcance(self) -> bool:
        """El alcance se pudo calcular y salió vacío: no hay nada que revisar."""

        return self.alcance.acotado and not self.alcance.archivos

    # -- git ----------------------------------------------------------- #

    def es_repo_git(self) -> bool:
        with self._candado_git:
            if self._es_git is None:
                salida = _correr_git(self.raiz, ["rev-parse", "--is-inside-work-tree"])
                self._es_git = salida is not None and salida.strip() == "true"
            return self._es_git

    def archivos_versionados(self) -> tuple[str, ...] | None:
        """Rutas que git tiene en el índice, o ``None`` si no se pudieron leer.

        El fallo también se cachea. Si no, las dos reglas que consultan el
        índice reintentarían un git que ya se sabe colgado, y dos esperas de
        ``_TOPE_SEGUNDOS_GIT`` seguidas se comen solas el tope de la regla.
        """

        with self._candado_git:
            if not self._git_ya_intentado:
                self._git_ya_intentado = True
                salida = _correr_git(self.raiz, ["ls-files", "-z"])
                if salida is not None:
                    self._archivos_de_git = tuple(
                        ruta for ruta in salida.split("\0") if ruta.strip()
                    )
            return self._archivos_de_git

    def versionados_en_alcance(self) -> tuple[str, ...] | None:
        """Lo mismo, recortado a lo que cambió en esta sesión.

        Las dos reglas que miran el índice también se acotan. Si no, la primera
        corrida sobre un repo que arrastra ``node_modules`` versionado desde
        hace dos años lo reporta como si el agente lo acabara de meter.
        """

        versionados = self.archivos_versionados()
        if versionados is None:
            return None
        return self.alcance.limitar(versionados)

    def ignoradas_por_git(self, rutas: Sequence[str]) -> frozenset[str]:
        """Cuáles de ``rutas`` están cubiertas por ``.gitignore``.

        Sin esto, un ``.env`` correctamente ignorado se reportaría como
        credencial versionada -- es decir, se le gritaría al usuario justo por
        haber hecho lo correcto.
        """

        if not rutas or not self.es_repo_git():
            return frozenset()
        return _rutas_ignoradas_por_git(self.raiz, rutas)


def _recorrer_archivos_de_texto(raiz: Path) -> _Escaneo:
    archivos: list[_ArchivoLeido] = []
    bytes_totales = 0
    # `truncado` significa UNA sola cosa: se dejó de mirar por llegar al tope
    # global. Saltarse un archivo suelto de medio mega no cuenta -- se midió
    # sobre este mismo repo y bastaba un bundle generado para que las cuatro
    # reglas de contenido dijeran "revisión parcial" en todas las corridas, que
    # es la clase de aviso que se aprende a ignorar en dos días.
    truncado = False

    for carpeta, subcarpetas, nombres in os.walk(raiz, followlinks=False):
        subcarpetas[:] = [nombre for nombre in subcarpetas if nombre not in _CARPETAS_IGNORADAS]
        if truncado:
            break
        for nombre in nombres:
            if len(archivos) >= _MAX_ARCHIVOS or bytes_totales >= _MAX_BYTES_TOTALES:
                truncado = True
                break
            ruta = Path(carpeta) / nombre
            if ruta.suffix.lower() not in _EXTENSIONES_TEXTO:
                continue
            try:
                if ruta.is_symlink():
                    continue
                tamano = ruta.stat().st_size
                if tamano > _MAX_BYTES_POR_ARCHIVO:
                    # Un archivo de medio mega de código no existe; lo que sí
                    # existe es un `.json` de datos o un bundle generado, y
                    # ninguna de estas reglas tiene nada que decir sobre eso.
                    continue
                # errors="replace" en vez de exigir UTF-8: un archivo con
                # codificación rara no debe tumbar el chequeo entero.
                texto = ruta.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            bytes_totales += tamano
            archivos.append(
                _ArchivoLeido(
                    ruta=ruta.relative_to(raiz).as_posix(),
                    sufijo=ruta.suffix.lower(),
                    texto=texto,
                )
            )

    return _Escaneo(archivos=tuple(archivos), truncado=truncado)


def _leer_archivos_del_alcance(raiz: Path, rutas: frozenset[str]) -> _Escaneo:
    """Lee solo las rutas del alcance, sin recorrer el repo.

    Se ordenan para que dos corridas con el mismo alcance den la evidencia en
    el mismo orden: un reporte que baraja sus líneas parece cambiar cuando no
    cambió nada.
    """

    archivos: list[_ArchivoLeido] = []
    bytes_totales = 0
    truncado = False
    limite = raiz.resolve()

    for ruta in sorted(rutas):
        if len(archivos) >= _MAX_ARCHIVOS or bytes_totales >= _MAX_BYTES_TOTALES:
            truncado = True
            break
        partes = ruta.split("/")
        # Mismos descartes que el recorrido completo, para que el veredicto no
        # dependa de si la corrida fue acotada: un archivo de `node_modules`
        # tampoco es código del agente cuando aparece en el diff.
        if any(parte in _CARPETAS_IGNORADAS for parte in partes[:-1]):
            continue
        destino = raiz / ruta
        sufijo = destino.suffix.lower()
        if sufijo not in _EXTENSIONES_TEXTO:
            continue
        try:
            # Un alcance puede traer rutas borradas (el diff las lista) y
            # enlaces; ninguna de las dos se lee. `resolve` además cierra el
            # paso a un enlace que apunte fuera del workspace.
            if destino.is_symlink() or not destino.is_file():
                continue
            if not destino.resolve().is_relative_to(limite):
                continue
            tamano = destino.stat().st_size
            if tamano > _MAX_BYTES_POR_ARCHIVO:
                continue
            texto = destino.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        bytes_totales += tamano
        archivos.append(_ArchivoLeido(ruta=ruta, sufijo=sufijo, texto=texto))

    return _Escaneo(archivos=tuple(archivos), truncado=truncado)


def _correr_git(raiz: Path, argumentos: Sequence[str]) -> str | None:
    """Corre git en ``raiz`` y devuelve su stdout, o ``None`` si no se pudo.

    No se usa ``ide_verificacion.ejecutar_intento`` a propósito: ese descarta
    la salida cuando el comando sale bien, porque le interesa el error y no el
    dato. Aquí el dato ES lo que hace falta (la lista de archivos que git
    imprime). Sí se copia su disciplina: nada de shell, y tope de tiempo.
    """

    ejecutable = shutil.which("git")
    if ejecutable is None:
        return None
    try:
        proceso = subprocess.run(
            [ejecutable, "-C", str(raiz), *argumentos],
            capture_output=True,
            text=True,
            timeout=_TOPE_SEGUNDOS_GIT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proceso.stdout if proceso.returncode == 0 else None


def _rutas_ignoradas_por_git(raiz: Path, rutas: Sequence[str]) -> frozenset[str]:
    ejecutable = shutil.which("git")
    if ejecutable is None:
        return frozenset()
    try:
        proceso = subprocess.run(
            [ejecutable, "-C", str(raiz), "check-ignore", "--stdin", "-z"],
            input="\0".join(rutas),
            capture_output=True,
            text=True,
            timeout=_TOPE_SEGUNDOS_GIT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    # check-ignore devuelve 1 cuando NINGUNA ruta está ignorada; eso no es un
    # error, es la respuesta "ninguna". Cualquier otro código sí es un fallo y
    # se trata como "no sé", que es lo conservador: no filtrar nada.
    if proceso.returncode not in (0, 1):
        return frozenset()
    return frozenset(ruta for ruta in proceso.stdout.split("\0") if ruta.strip())


# --------------------------------------------------------------------- #
# Utilidades compartidas por los chequeos.
# --------------------------------------------------------------------- #


def _linea_de(texto: str, posicion: int) -> int:
    return texto.count("\n", 0, posicion) + 1


def _buscar_patron(
    archivos: Sequence[_ArchivoLeido], patron: re.Pattern[str], detalle: str
) -> list[Evidencia]:
    hallazgos: list[Evidencia] = []
    for archivo in archivos:
        for coincidencia in patron.finditer(archivo.texto):
            hallazgos.append(
                Evidencia(
                    ruta=archivo.ruta,
                    linea=_linea_de(archivo.texto, coincidencia.start()),
                    detalle=detalle,
                )
            )
    return hallazgos


def _no_aplica_por_falta_de(contexto: ContextoDeChequeo, que: str) -> Veredicto:
    """``no_aplica`` con el "dónde" correcto según el alcance.

    "No hay archivos de código en este workspace" es falso cuando el workspace
    tiene cientos y lo que pasa es que el agente no tocó ninguno. La regla no
    aplica igual, pero el motivo es otro y el reporte tiene que decir cuál.
    """

    donde = (
        "entre los archivos que cambiaron en esta sesión"
        if contexto.alcance.acotado
        else "en este workspace"
    )
    return Veredicto(estado=EstadoRegla.NO_APLICA, mensaje=f"No hay {que} {donde}.")


def _veredicto_por_hallazgos(
    hallazgos: Sequence[Evidencia],
    *,
    mensaje_limpio: str,
    mensaje_sucio: str,
    parcial: bool,
) -> Veredicto:
    """Arma el veredicto de una regla de "buscar y no encontrar".

    El detalle que importa: encontrar evidencia es definitivo aunque el
    recorrido haya sido parcial, pero NO encontrarla con el recorrido a medias
    solo permite decir "cumple, hasta donde se miró" -- de ahí que ``parcial``
    viaje en el resultado en vez de callarse.
    """

    if hallazgos:
        return Veredicto(
            estado=EstadoRegla.NO_CUMPLE,
            mensaje=mensaje_sucio.format(cantidad=len(hallazgos)),
            evidencia=tuple(hallazgos[:MAX_EVIDENCIAS_POR_REGLA]),
            parcial=parcial,
            total_hallazgos=len(hallazgos),
        )
    aviso = " (revisión parcial: se llegó al tope del recorrido)" if parcial else ""
    return Veredicto(
        estado=EstadoRegla.CUMPLE,
        mensaje=f"{mensaje_limpio}{aviso}",
        parcial=parcial,
        total_hallazgos=0,
    )


# --------------------------------------------------------------------- #
# El catálogo. Seis reglas, cada una con su chequeo de verdad.
# --------------------------------------------------------------------- #

# Reglas de `edecan_toolkit.seguridad` que hablan de credenciales. Las otras
# que ese módulo detecta (`unsafe-eval`, `cors-wildcard`, `verify=False`...)
# quedan deliberadamente fuera: son heurísticas con falsos positivos legítimos
# (un `eval` en un intérprete, `verify=False` en un test contra un servidor
# local) y una regla que se equivoca entrena a ignorar todas las demás.
_REGLAS_DE_CREDENCIAL_EN_CONTENIDO = frozenset({"private-key", "hardcoded-secret"})

# Nombres y extensiones de archivo que son un secreto en sí mismos. Son los
# mismos que `seguridad._scan_project` marca como `sensitive-file`, pero la
# pregunta acá es otra y por eso se comprueban contra el índice de git y no
# contra el disco: que exista un `.env` es lo NORMAL (es donde van los
# secretos); que git lo esté versionando es lo grave.
_SUFIJOS_SIEMPRE_SECRETOS = frozenset({".p12", ".pfx"})
# `.pem` y `.key` no bastan por sí solos: un bundle de certificados públicos es
# un `.pem` que se versiona a conciencia. Para estos se confirma leyendo la
# cabecera con el mismo patrón de clave privada de `seguridad`.
_SUFIJOS_A_CONFIRMAR = frozenset({".key", ".pem"})
_NOMBRES_DE_ARCHIVO_SECRETO = frozenset({"id_dsa", "id_ecdsa", "id_ed25519", "id_rsa"})
# `.env.example`, `.env.plantilla` y compañía se versionan a propósito: son la
# plantilla que le dice al siguiente qué variables hace falta llenar.
_MARCAS_DE_PLANTILLA = ("default", "ejemplo", "example", "plantilla", "sample", "template")
_BYTES_DE_CABECERA = 4_096

# Extensiones donde un secreto escrito a mano sí es un hallazgo. Fuera quedan
# `.md` y demás documentación: un bloque de clave privada en un README es un
# ejemplo, y medido sobre este mismo repo era el único hallazgo de ese tipo.
_EXTENSIONES_CONFIGURACION = frozenset({".cfg", ".conf", ".ini", ".json", ".toml", ".yaml", ".yml"})

# Las dos formas de escribir un secreto que el patrón heredado NO ve, y son las
# dos más comunes en configuración:
#
#   {"api_key": "ab12..."}    <- clave entre comillas (todo JSON, y los objetos
#                                literales de JavaScript)
#   api_key: ab12...          <- valor sin comillas (YAML, .ini, .conf)
#
# `hardcoded-secret` de `edecan_toolkit.seguridad` exige exactamente lo
# contrario -- clave SIN comillas y valor CON comillas -- así que en un `.json`
# no encuentra nada. Un `credentials.json` con la llave de servicio adentro es
# de los accidentes más frecuentes que hay, y era invisible para esta regla.
#
# Van acá y no en `seguridad._CONTENT_RULES` a pesar de que allá viven los
# patrones, y la razón es concreta: `seguridad._scan_project` no tiene ninguno
# de los filtros de este módulo (ni salta fixtures, ni exige que el valor tenga
# dígitos, ni consulta el `.gitignore`). El valor sin comillas es la forma que
# más falsos positivos produce -- `password = settings.SECRET_KEY` calza igual
# de bien que un token -- y soltarla en el escáner general lo llenaría de
# ruido. Acá pasa por los tres filtros antes de llegar al reporte. Lo que sí se
# hereda intacto es la lista de palabras clave, y un test la mantiene atada a
# la de `seguridad` para que no se separen sin que nadie se entere.
_CLAVES_DE_SECRETO = r"(?:api[_-]?key|access[_-]?token|client[_-]?secret|password|secret)"
_CUERPO_DE_SECRETO = r"[A-Za-z0-9_./+\-=]{16,}"
_PATRONES_DE_SECRETO_PROPIOS: tuple[tuple[frozenset[str], re.Pattern[str], str], ...] = (
    (
        _EXTENSIONES_CODIGO | _EXTENSIONES_CONFIGURACION,
        re.compile(rf"(?i)[\"']{_CLAVES_DE_SECRETO}[\"']\s*:\s*[\"']{_CUERPO_DE_SECRETO}[\"']"),
        "Parece contener una credencial fija. Muévela al vault y rótala.",
    ),
    (
        # Solo configuración: en un `.py` o un `.ts`, `secret = algo_muy_largo`
        # sin comillas es una variable, no una credencial.
        _EXTENSIONES_CONFIGURACION,
        re.compile(
            rf"(?im)^[ \t]*(?:-[ \t]+)?{_CLAVES_DE_SECRETO}"
            rf"[ \t]*[:=][ \t]*{_CUERPO_DE_SECRETO}[ \t]*$"
        ),
        "Parece contener una credencial fija. Muévela al vault y rótala.",
    ),
)

# Marcas de "esto es un fixture, no una credencial". Sin este filtro la regla
# era inservible: medida sobre el repo de Edecán daba 52 hallazgos y los 52
# eran tokens falsos de tests (`api_key="fake-elevenlabs-key"`). Una regla que
# grita 52 veces sin razón es peor que no tener la regla.
_MARCAS_DE_PRUEBA_EN_RUTA = frozenset({"test", "spec", "fixture", "mock"})
_MARCAS_DE_PRUEBA_EN_NOMBRE = frozenset(
    {"test", "spec", "conftest", "mock", "fixture", "demo", "ejemplo"}
)
# Palabras de un nombre, partiendo también camelCase: `LlaveroTests` -> Llavero,
# Tests. Compararlas enteras y no por subcadena importa en un repo en español,
# donde `especificaciones.py` contiene "spec" y `demografia.py` contiene "demo":
# con subcadenas los dos quedaban clasificados como archivos de prueba y sus
# credenciales dejaban de revisarse. Un falso negativo en la regla de
# credenciales es el peor de los errores posibles de esta pieza.
_PALABRAS_DE_NOMBRE = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[a-z]+|[0-9]+")
_MARCAS_DE_VALOR_FALSO = (
    "aqui",
    "changeme",
    "dummy",
    "ejemplo",
    "example",
    "fake",
    "mock",
    "placeholder",
    "prueba",
    "redact",
    "reemplaza",
    "sample",
    "test",
    "tu-",
    "tu_",
    "xxxx",
    "your",
)


# Confirmaciones que separan "el patrón calzó" de "esto es una credencial".
# Las dos salieron de medir la regla contra el repo de Edecán, donde el patrón
# de `seguridad` calzaba con `_APNS_PEM_HEADER = "-----BEGIN PRIVATE KEY-----"`
# (una constante para validar lo que sube el usuario) y con
# `accessToken = "cc.edecan.app.accessToken"` (el NOMBRE de una llave del
# llavero, no su valor). Ninguna de las dos es un secreto, y las dos hundirían
# la credibilidad del reporte entero.
_CUERPO_BASE64 = re.compile(r"[A-Za-z0-9+/=]{40,}")
_VALOR_ENTRE_COMILLAS = re.compile(r"[\"']([^\"']{8,})[\"']")
_VENTANA_CUERPO_CLAVE = 2_000


def _hay_cuerpo_de_clave(texto: str, fin_de_cabecera: int) -> bool:
    """Una cabecera PEM sin cuerpo base64 detrás es una constante, no una clave."""

    return bool(
        _CUERPO_BASE64.search(texto[fin_de_cabecera : fin_de_cabecera + _VENTANA_CUERPO_CLAVE])
    )


def _valor_parece_secreto(coincidencia: str) -> bool:
    """¿El valor asignado se parece a un secreto de verdad?

    Se exige al menos un dígito. Casi todo token, clave de API o secreto
    generado los tiene, y casi ningún identificador escrito a mano
    (`cc.edecan.app.accessToken`, `client-secret-tenant-A`) los tiene. El
    precio es no detectar una contraseña de puras palabras, y se paga: vale
    más una regla en la que se confía que una que atrapa todo y por eso nadie
    lee.

    El mismo filtro corre sobre el valor SIN comillas de un YAML, que es donde
    más falta hace: ahí `password: settings.SECRET_KEY` calza con el patrón
    igual que un token, y sin este filtro la forma nueva habría llegado al
    reporte llena de referencias a configuración.

    Se corta por el separador ANTES de buscar el valor, y eso no es un detalle:
    en `"client_secret": "ab12..."` la primera cosa entre comillas es la clave,
    no el secreto. Mirándola a ella, ninguna credencial de un JSON tiene
    dígitos y todas se descartaban en silencio.
    """

    partes = re.split(r"[:=]", coincidencia, maxsplit=1)
    valor = partes[1] if len(partes) == 2 else coincidencia
    entre_comillas = _VALOR_ENTRE_COMILLAS.search(valor)
    texto = entre_comillas.group(1) if entre_comillas is not None else valor
    return any(caracter.isdigit() for caracter in texto)


def _palabras_de(texto: str) -> set[str]:
    palabras: set[str] = set()
    for token in _PALABRAS_DE_NOMBRE.findall(texto):
        palabra = token.casefold()
        palabras.add(palabra)
        # El plural cuenta como la misma palabra: `tests`, `__mocks__`,
        # `fixtures` son las carpetas que de verdad se usan.
        if palabra.endswith("s"):
            palabras.add(palabra[:-1])
    return palabras


def _parece_de_prueba(ruta: str) -> bool:
    partes = ruta.split("/")
    if any(_palabras_de(parte) & _MARCAS_DE_PRUEBA_EN_RUTA for parte in partes[:-1]):
        return True
    return bool(_palabras_de(partes[-1]) & _MARCAS_DE_PRUEBA_EN_NOMBRE)


def _archivo_secreto_versionado(
    raiz: Path, ruta: str, patron_clave: re.Pattern[str] | None
) -> bool:
    """¿Esta ruta del índice de git es un archivo de secretos de verdad?"""

    nombre = ruta.split("/")[-1].casefold()
    if any(marca in nombre for marca in _MARCAS_DE_PLANTILLA):
        return False
    sufijo = Path(nombre).suffix
    if nombre in _NOMBRES_DE_ARCHIVO_SECRETO or sufijo in _SUFIJOS_SIEMPRE_SECRETOS:
        return True
    if nombre.startswith(".env"):
        return True
    if sufijo not in _SUFIJOS_A_CONFIRMAR or patron_clave is None:
        return False
    try:
        with (raiz / ruta).open("rb") as archivo:
            cabecera = archivo.read(_BYTES_DE_CABECERA)
    except OSError:
        # Sin poder leerlo no se puede afirmar nada, y afirmar de más es
        # justo lo que vuelve inservible a un reporte.
        return False
    return bool(patron_clave.search(cabecera.decode("utf-8", errors="replace")))


def _chequear_credenciales(contexto: ContextoDeChequeo) -> Veredicto:
    # Import perezoso de los PATRONES, no del escáner completo. Dos razones:
    # `seguridad._scan_project` recorre y lee el repo por su cuenta (6,5 s
    # medidos sobre este monorepo, contra el recorrido compartido que ya está
    # en memoria), y `edecan_toolkit` arrastra el resto del paquete, así que
    # este módulo tiene que poder importarse y correr las otras cinco reglas
    # sin él. Lo que NO se reimplementa es la detección: los patrones salen de
    # ahí, y si alguien agrega uno nuevo esta regla lo hereda sola.
    try:
        from edecan_toolkit.seguridad import _CONTENT_RULES
    except Exception as exc:  # noqa: BLE001 - cualquier fallo de import es "no sé"
        return Veredicto(
            estado=EstadoRegla.ERROR,
            mensaje=f"No se pudo cargar el detector de credenciales de Edecán: {exc}",
        )

    es_git = contexto.es_repo_git()
    revisables = contexto.archivos(_EXTENSIONES_CODIGO | _EXTENSIONES_CONFIGURACION)
    versionados = contexto.versionados_en_alcance() or ()
    if not revisables and not versionados:
        return _no_aplica_por_falta_de(contexto, "código ni archivos versionados que revisar")

    heredados = [
        (regla, patron, mensaje)
        for _, regla, patron, mensaje in _CONTENT_RULES
        if regla in _REGLAS_DE_CREDENCIAL_EN_CONTENIDO
    ]
    patron_clave = next((patron for regla, patron, _ in heredados if regla == "private-key"), None)
    # Los heredados no traen extensiones propias: corren sobre todo lo revisable.
    patrones: list[tuple[str, frozenset[str], re.Pattern[str], str]] = [
        (regla, _EXTENSIONES_CODIGO | _EXTENSIONES_CONFIGURACION, patron, mensaje)
        for regla, patron, mensaje in heredados
    ]
    patrones.extend(
        ("hardcoded-secret", extensiones, patron, mensaje)
        for extensiones, patron, mensaje in _PATRONES_DE_SECRETO_PROPIOS
    )
    hallazgos: list[Evidencia] = []

    # 1. Archivos que SON un secreto y que git está versionando. Acá no hay
    #    heurística que valga: si git lo sigue, está versionado.
    for ruta in versionados:
        if _archivo_secreto_versionado(contexto.raiz, ruta, patron_clave):
            hallazgos.append(
                Evidencia(
                    ruta=ruta,
                    linea=None,
                    detalle="Archivo de secretos versionado: sácalo del índice y rota lo suyo.",
                )
            )

    # 2. Secretos escritos dentro del código y de la configuración.
    ignoradas = contexto.ignoradas_por_git([archivo.ruta for archivo in revisables])
    # Dos patrones distintos pueden calzar en la misma línea (uno heredado y
    # uno propio con otra forma de escribir lo mismo). Una credencial repetida
    # en el reporte se lee como dos problemas donde hay uno.
    vistos: set[tuple[str, int]] = set()
    for archivo in revisables:
        if archivo.ruta in ignoradas or _parece_de_prueba(archivo.ruta):
            continue
        for regla, extensiones, patron, mensaje in patrones:
            if archivo.sufijo not in extensiones:
                continue
            for coincidencia in patron.finditer(archivo.texto):
                encontrado = coincidencia.group(0)
                if any(marca in encontrado.casefold() for marca in _MARCAS_DE_VALOR_FALSO):
                    continue
                if regla == "private-key" and not _hay_cuerpo_de_clave(
                    archivo.texto, coincidencia.end()
                ):
                    continue
                if regla != "private-key" and not _valor_parece_secreto(encontrado):
                    continue
                ubicacion = (archivo.ruta, _linea_de(archivo.texto, coincidencia.start()))
                if ubicacion in vistos:
                    continue
                vistos.add(ubicacion)
                hallazgos.append(Evidencia(ruta=ubicacion[0], linea=ubicacion[1], detalle=mensaje))

    nota_git = "" if es_git else " (no es un repo git: solo se revisó el contenido del código)"
    return _veredicto_por_hallazgos(
        hallazgos,
        mensaje_limpio=(
            f"Sin credenciales a la vista en {len(revisables)} archivos revisados{nota_git}"
        ),
        mensaje_sucio=(
            "Hay {cantidad} credencial(es) dentro del repositorio. Sácalas y rótalas: una "
            "credencial ya versionada se considera quemada."
        ),
        parcial=contexto.escaneo().truncado,
    )


# Salidas de build y dependencias que git no debería estar siguiendo. La lista
# es corta a propósito: `dist/` y `build/` quedan fuera porque hay proyectos
# que versionan su bundle a conciencia, y una regla que discute con una
# decisión legítima del repo es ruido.
_CARPETAS_GENERADAS = frozenset(
    {
        ".mypy_cache",
        ".next",
        ".pytest_cache",
        ".ruff_cache",
        ".turbo",
        ".venv",
        "__pycache__",
        "node_modules",
        "venv",
    }
)
_NOMBRES_GENERADOS = frozenset({".DS_Store", "Thumbs.db"})
_SUFIJOS_GENERADOS = frozenset({".pyc", ".pyo"})


def _chequear_generados_versionados(contexto: ContextoDeChequeo) -> Veredicto:
    if not contexto.es_repo_git():
        return Veredicto(
            estado=EstadoRegla.NO_APLICA,
            mensaje="El workspace no es un repo git, así que no hay nada versionado que revisar.",
        )
    versionados = contexto.versionados_en_alcance()
    if versionados is None:
        return Veredicto(
            estado=EstadoRegla.ERROR,
            mensaje="git no pudo listar los archivos versionados; el chequeo no concluyó.",
        )
    if not versionados:
        return _no_aplica_por_falta_de(contexto, "archivos versionados")

    hallazgos: list[Evidencia] = []
    for ruta in versionados:
        partes = ruta.split("/")
        nombre = partes[-1]
        if any(parte in _CARPETAS_GENERADAS for parte in partes[:-1]):
            motivo = "Está dentro de una carpeta generada o de dependencias."
        elif nombre in _NOMBRES_GENERADOS:
            motivo = "Archivo generado por el sistema operativo."
        elif Path(nombre).suffix.lower() in _SUFIJOS_GENERADOS:
            motivo = "Archivo compilado, se regenera solo."
        else:
            continue
        hallazgos.append(Evidencia(ruta=ruta, linea=None, detalle=motivo))

    cuantos = (
        f"{len(versionados)} archivo(s) del cambio en el índice"
        if contexto.alcance.acotado
        else f"{len(versionados)} archivos en el índice"
    )
    return _veredicto_por_hallazgos(
        hallazgos,
        mensaje_limpio=f"git no versiona nada generado ({cuantos})",
        mensaje_sucio=(
            "git está versionando {cantidad} archivo(s) generado(s). Sácalos del índice "
            "(`git rm --cached`) y agrégalos al .gitignore."
        ),
        parcial=False,
    )


# Se buscan con cuantificador (`<{7}`) y no con el literal para que este mismo
# archivo no se reporte a sí mismo cuando el workspace es el repo de Edecán.
_MARCA_CONFLICTO_INICIO = re.compile(r"^<{7} ", re.MULTILINE)
_MARCA_CONFLICTO_MEDIO = re.compile(r"^={7}\s*$", re.MULTILINE)
_MARCA_CONFLICTO_FIN = re.compile(r"^>{7} ", re.MULTILINE)


def _chequear_marcadores_de_conflicto(contexto: ContextoDeChequeo) -> Veredicto:
    archivos = contexto.archivos(_EXTENSIONES_CODIGO)
    if not archivos:
        return _no_aplica_por_falta_de(contexto, "archivos de código")

    hallazgos: list[Evidencia] = []
    for archivo in archivos:
        inicio = _MARCA_CONFLICTO_INICIO.search(archivo.texto)
        # Se exigen las TRES marcas: con una sola, un `<<<<<<<` de arte ASCII o
        # una línea de `=======` separando secciones bastarían para inventar un
        # conflicto que no existe.
        if (
            inicio is None
            or not _MARCA_CONFLICTO_MEDIO.search(archivo.texto)
            or not _MARCA_CONFLICTO_FIN.search(archivo.texto)
        ):
            continue
        hallazgos.append(
            Evidencia(
                ruta=archivo.ruta,
                linea=_linea_de(archivo.texto, inicio.start()),
                detalle="Quedó un conflicto de merge sin resolver.",
            )
        )

    return _veredicto_por_hallazgos(
        hallazgos,
        mensaje_limpio="Ningún archivo de código tiene marcadores de conflicto",
        mensaje_sucio=(
            "Quedaron marcadores de conflicto sin resolver en {cantidad} archivo(s). "
            "Ese código no compila ni corre: resuélvelo antes de seguir."
        ),
        parcial=contexto.escaneo().truncado,
    )


# Mismo cuidado que con los marcadores: escritas de forma que la expresión no
# calce contra su propio texto fuente (`\s*` nunca acepta la barra invertida
# que sigue a `debugger` en esta misma línea).
_PATRONES_DEPURACION: tuple[tuple[frozenset[str], re.Pattern[str], str], ...] = (
    (
        frozenset({".py"}),
        re.compile(r"(?<![\w.])breakpoint\s*\(\s*\)|\b(?:i?pdb|pudb)\.set_trace\s*\("),
        "Quedó un punto de depuración: cuelga el proceso esperando una consola.",
    ),
    (
        frozenset({".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue", ".svelte"}),
        re.compile(r"(?<![\w.])debugger\s*;"),
        "Quedó una sentencia debugger: frena el navegador de quien abra la página.",
    ),
)


def _chequear_puntos_de_depuracion(contexto: ContextoDeChequeo) -> Veredicto:
    aplicables = frozenset().union(*(extensiones for extensiones, _, _ in _PATRONES_DEPURACION))
    if not contexto.archivos(aplicables):
        return _no_aplica_por_falta_de(contexto, "código de Python ni de JavaScript/TypeScript")

    hallazgos: list[Evidencia] = []
    for extensiones, patron, detalle in _PATRONES_DEPURACION:
        hallazgos.extend(_buscar_patron(contexto.archivos(extensiones), patron, detalle))

    return _veredicto_por_hallazgos(
        hallazgos,
        mensaje_limpio="No quedó ningún punto de depuración en el código",
        mensaje_sucio=(
            "Quedaron {cantidad} punto(s) de depuración en el código. Sácalos: en producción "
            "cuelgan el proceso o frenan el navegador."
        ),
        parcial=contexto.escaneo().truncado,
    )


# El grupo 1 captura el home completo (`/Users/alguien`) para poder preguntarle
# al disco si esa cuenta existe. La expresión no se encuentra a sí misma porque
# acá el literal está partido por la alternancia (`/(?:Users|home)/`).
_PATRON_RUTA_ABSOLUTA_UNIX = re.compile(r"(?<![\w./~-])(/(?:Users|home)/[A-Za-z0-9._-]+)/")
_PATRON_RUTA_ABSOLUTA_WINDOWS = re.compile(
    r"(?<![\w])([A-Za-z]:\\{1,2}Users\\{1,2}[A-Za-z0-9._-]+)\\"
)


def _es_home_de_esta_maquina(prefijo: str, memoria: dict[str, bool]) -> bool:
    """¿``/Users/alguien`` es una cuenta que existe en ESTA máquina?

    Este filtro es la diferencia entre una regla útil y una inservible, y no es
    teoría: sin él, medida sobre el repo de Edecán, la regla daba 7 hallazgos y
    los 7 eran falsos -- placeholders de la interfaz (`/Users/tu-usuario/...`)
    y fixtures de tests con nombres inventados. Exigir que la cuenta exista de
    verdad deja pasar solo lo que importa: código atado al disco de quien lo
    escribió, que en un repo público además filtra su nombre de usuario.

    El precio es un falso negativo posible (una ruta al home de OTRA máquina no
    se detecta acá), y se paga con gusto: el encargo dice preferir no reportar
    antes que reportar mal.
    """

    if prefijo not in memoria:
        try:
            memoria[prefijo] = Path(prefijo.replace("\\\\", "\\")).is_dir()
        except OSError:
            memoria[prefijo] = False
    return memoria[prefijo]


def _chequear_rutas_absolutas(contexto: ContextoDeChequeo) -> Veredicto:
    archivos = contexto.archivos(_EXTENSIONES_CODIGO)
    if not archivos:
        return _no_aplica_por_falta_de(contexto, "archivos de código")

    memoria: dict[str, bool] = {}
    hallazgos: list[Evidencia] = []
    for archivo in archivos:
        for patron in (_PATRON_RUTA_ABSOLUTA_UNIX, _PATRON_RUTA_ABSOLUTA_WINDOWS):
            for coincidencia in patron.finditer(archivo.texto):
                if not _es_home_de_esta_maquina(coincidencia.group(1), memoria):
                    continue
                hallazgos.append(
                    Evidencia(
                        ruta=archivo.ruta,
                        linea=_linea_de(archivo.texto, coincidencia.start()),
                        # La ruta encontrada NO va en la evidencia: lleva dentro
                        # el nombre de usuario real, que es justo lo que esta
                        # regla existe para sacar del repo público.
                        detalle="Ruta absoluta al home de una cuenta real de esta máquina.",
                    )
                )

    return _veredicto_por_hallazgos(
        hallazgos,
        mensaje_limpio="El código no apunta al home de ninguna cuenta de esta máquina",
        mensaje_sucio=(
            "Hay {cantidad} ruta(s) absoluta(s) al home de esta máquina en el código. Usa rutas "
            "relativas o configuración: en otra máquina esto revienta, y en un repo público "
            "además filtra el nombre de usuario."
        ),
        parcial=contexto.escaneo().truncado,
    )


# Solo los manifiestos que `detectar_comando_de_verificacion` sabe leer. Si el
# workspace es de Rust o de Swift, esta regla NO APLICA -- reportar "no hay
# comando de verificación" en un repo con `cargo test` sería exactamente el
# falso positivo que el punto 1 del docstring describe.
_MANIFIESTOS_CONOCIDOS = (
    "package.json",
    "pyproject.toml",
    "pytest.ini",
    "setup.cfg",
    "tox.ini",
    "Makefile",
    "tsconfig.json",
)


def _chequear_verificacion_detectable(contexto: ContextoDeChequeo) -> Veredicto:
    presentes = [nombre for nombre in _MANIFIESTOS_CONOCIDOS if (contexto.raiz / nombre).is_file()]
    if not presentes:
        return Veredicto(
            estado=EstadoRegla.NO_APLICA,
            mensaje=(
                "Este workspace no usa ninguno de los ecosistemas que Edecán sabe verificar "
                "(Node, Python, Make, TypeScript)."
            ),
        )

    comando = detectar_comando_de_verificacion(contexto.raiz)
    if comando is None:
        return Veredicto(
            estado=EstadoRegla.NO_CUMPLE,
            mensaje=(
                "El proyecto no declara cómo se verifica a sí mismo, así que un agente no "
                "tiene forma de saber si lo que escribió funciona. Agrega un script `test`, "
                "pytest o un target `test:` en el Makefile."
            ),
            evidencia=tuple(
                Evidencia(
                    ruta=nombre,
                    linea=None,
                    detalle="Manifiesto sin comando de verificación declarado.",
                )
                for nombre in presentes
            ),
            total_hallazgos=len(presentes),
        )

    return Veredicto(
        estado=EstadoRegla.CUMPLE,
        mensaje=f"El proyecto se verifica con `{' '.join(comando)}`.",
        total_hallazgos=0,
    )


@dataclass(frozen=True)
class ReglaVerificable:
    """Una regla del proyecto que NO depende de que el modelo la interprete.

    ``comprobar`` recibe el contexto de la corrida -- que se construye con la
    raíz del workspace y nada más -- y devuelve un ``Veredicto``. Para correr
    una sola regla desde afuera está ``verificar``, que recibe la raíz
    directamente.
    """

    id: str
    descripcion: str
    comprobar: Callable[[ContextoDeChequeo], Veredicto] = field(repr=False)
    ambito: AmbitoDeRegla = AmbitoDeRegla.POR_ARCHIVO

    def verificar(
        self,
        workspace_root: Path,
        *,
        alcance: Alcance | None = None,
        tope_segundos: float = TOPE_SEGUNDOS_POR_REGLA,
    ) -> ResultadoRegla:
        contexto = ContextoDeChequeo(_raiz_valida(workspace_root), alcance)
        return _correr_regla(self, contexto, tope_segundos)


CATALOGO: tuple[ReglaVerificable, ...] = (
    ReglaVerificable(
        id="sin-credenciales-versionadas",
        descripcion="Ninguna credencial ni clave privada viaja dentro del repositorio.",
        comprobar=_chequear_credenciales,
    ),
    ReglaVerificable(
        id="sin-generados-versionados",
        descripcion="git no versiona dependencias, cachés ni archivos que se regeneran solos.",
        comprobar=_chequear_generados_versionados,
    ),
    ReglaVerificable(
        id="sin-marcadores-de-conflicto",
        descripcion="No quedan conflictos de merge sin resolver en el código.",
        comprobar=_chequear_marcadores_de_conflicto,
    ),
    ReglaVerificable(
        id="sin-puntos-de-depuracion",
        descripcion="No quedan breakpoints ni sentencias debugger olvidadas en el código.",
        comprobar=_chequear_puntos_de_depuracion,
    ),
    ReglaVerificable(
        id="sin-rutas-absolutas-de-esta-maquina",
        descripcion="El código no trae rutas absolutas al home de una cuenta de esta máquina.",
        comprobar=_chequear_rutas_absolutas,
    ),
    ReglaVerificable(
        id="verificacion-detectable",
        descripcion="El proyecto declara un comando con el que se verifica a sí mismo.",
        comprobar=_chequear_verificacion_detectable,
        # La única del catálogo que no es de un archivo: pregunta por el repo.
        # Se sigue reportando aunque el agente no haya tocado el manifiesto,
        # porque sin comando de verificación no puede comprobar lo que escribió
        # -- pero el prompt la marca como condición del proyecto, no como algo
        # que él rompió.
        ambito=AmbitoDeRegla.DEL_PROYECTO,
    ),
)


# --------------------------------------------------------------------- #
# La corrida completa.
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class ReporteVerificacion:
    """El resultado de correr el catálogo entero sobre un workspace."""

    raiz: str
    resultados: tuple[ResultadoRegla, ...]
    duracion_segundos: float
    alcance: Alcance = field(
        default_factory=lambda: alcance_completo("no se indicó el alcance de la corrida")
    )

    @property
    def incumplimientos(self) -> tuple[ResultadoRegla, ...]:
        return tuple(item for item in self.resultados if item.estado is EstadoRegla.NO_CUMPLE)

    @property
    def no_concluyentes(self) -> tuple[ResultadoRegla, ...]:
        return tuple(item for item in self.resultados if item.estado is EstadoRegla.ERROR)

    @property
    def todo_en_orden(self) -> bool:
        """Sin incumplimientos Y sin chequeos que no concluyeron.

        Un chequeo en ``error`` no cuenta como aprobado: no se sabe. Meterlo en
        la bolsa de "todo bien" sería el mismo autoengaño que reportarlo como
        incumplimiento, con el signo cambiado.
        """

        return not self.incumplimientos and not self.no_concluyentes

    def resumen(self) -> dict[str, Any]:
        conteo: dict[str, int] = {estado.value: 0 for estado in EstadoRegla}
        for item in self.resultados:
            conteo[str(item.estado)] += 1
        return {
            "raiz": self.raiz,
            "alcance": self.alcance.resumen(),
            "todo_en_orden": self.todo_en_orden,
            "conteo": conteo,
            "resultados": [item.resumen() for item in self.resultados],
            "duracion_segundos": round(self.duracion_segundos, 3),
        }

    def _encabezado(self) -> str:
        """La primera línea del bloque, que es la que decide cómo se lee el resto.

        Ver punto 6 del docstring del módulo. "Arregla lo que aparezca acá" es
        una orden correcta cuando lo de abajo salió de los archivos que el
        agente escribió, y es una trampa cuando salió de un repo de miles de
        archivos que no abrió: lo pone a arreglar deuda ajena, y a la tercera
        vez que el bloque le habla de código que no es suyo deja de leerlo. Por
        eso el encabezado cambia con el alcance en vez de ser una constante.
        """

        if self.alcance.origen is OrigenDelAlcance.WORKSPACE_COMPLETO:
            return (
                "Chequeo automático de reglas del proyecto, sobre el workspace COMPLETO: no se "
                f"pudo acotar a lo que cambiaste en esta sesión ({self.alcance.motivo}). Eso "
                "quiere decir que lo de abajo puede ser deuda que ya estaba en el repo antes de "
                "que llegaras. Arregla lo que hayas tocado tú; el resto es información, no "
                "tarea, y no lo cambies sin que te lo pidan."
            )
        cuantos = len(self.alcance.archivos or ())
        cuenta = "el único archivo" if cuantos == 1 else f"los {cuantos} archivos"
        if self.alcance.origen is OrigenDelAlcance.DIFF_DE_LA_SESION:
            verbo = "cambió" if cuantos == 1 else "cambiaron"
            # El hash corto alcanza para ubicarse y no gasta media línea del
            # prompt en cuarenta caracteres que nadie va a leer enteros.
            donde = f"{cuenta} que {verbo} desde `{(self.alcance.base or '')[:12]}`"
        else:
            donde = f"{cuenta} que tocaste"
        return (
            f"Chequeo automático de reglas del proyecto, sobre {donde} en esta sesión. Esto NO "
            "es una opinión del modelo: cada punto se comprobó ejecutando código sobre esos "
            "archivos. Es tu propio trabajo: arregla lo que aparezca acá antes de dar la tarea "
            "por terminada."
        )

    def as_prompt_block(self) -> str | None:
        """Bloque listo para el prompt del agente, o ``None`` si no hay nada que decir.

        Solo se reporta lo que hace falta arreglar y lo que no se pudo
        comprobar. Las reglas que cumplen y las que no aplican no van al prompt:
        gastar contexto en "todo bien" es la forma más segura de que el agente
        deje de leer este bloque.
        """

        if not self.incumplimientos and not self.no_concluyentes:
            return None

        lineas = [self._encabezado()]
        for item in self.incumplimientos:
            if item.ambito is AmbitoDeRegla.DEL_PROYECTO:
                # No se cuela entre los incumplimientos del cambio: es una
                # condición del repo, y presentarla como algo que el agente
                # rompió es la misma mentira que el encabezado evita.
                etiqueta = "DEL PROYECTO (ya estaba así, no lo rompiste tú)"
            else:
                etiqueta = "INCUMPLE"
            lineas.append(f"- {etiqueta} [{item.regla_id}] {item.descripcion} {item.mensaje}")
            for evidencia in item.evidencia:
                lineas.append(f"    · {evidencia.como_texto()}")
            faltan = (item.total_hallazgos or 0) - len(item.evidencia)
            if faltan > 0:
                lineas.append(f"    · (y {faltan} más)")
        for item in self.no_concluyentes:
            lineas.append(f"- SIN COMPROBAR [{item.regla_id}] {item.descripcion} {item.mensaje}")
        return "\n".join(lineas)


def _raiz_valida(workspace_root: Path) -> Path:
    if not isinstance(workspace_root, Path):
        raise IDEReglasVerificablesError("workspace_root debe ser un Path.")
    if not workspace_root.is_dir():
        raise IDEReglasVerificablesError("La raíz del workspace no existe o no es una carpeta.")
    return workspace_root


def _correr_regla(
    regla: ReglaVerificable, contexto: ContextoDeChequeo, tope_segundos: float
) -> ResultadoRegla:
    """Corre UNA regla aislada: ni su excepción ni su lentitud tocan a las demás."""

    inicio = time.monotonic()

    def _armar(veredicto: Veredicto) -> ResultadoRegla:
        return ResultadoRegla(
            regla_id=regla.id,
            descripcion=regla.descripcion,
            estado=veredicto.estado,
            mensaje=veredicto.mensaje,
            evidencia=veredicto.evidencia,
            parcial=veredicto.parcial,
            total_hallazgos=veredicto.total_hallazgos,
            duracion_segundos=time.monotonic() - inicio,
            ambito=regla.ambito,
        )

    # El alcance se calculó y no cambió nada. Las reglas de archivo no tienen
    # dónde mirar; correrlas para que cada una repita la misma frase es gastar
    # hilos y tiempo en un turno donde el agente no escribió código.
    if regla.ambito is AmbitoDeRegla.POR_ARCHIVO and contexto.sin_archivos_en_alcance():
        return _armar(
            Veredicto(
                estado=EstadoRegla.NO_APLICA,
                mensaje="No cambió ningún archivo en esta sesión.",
            )
        )

    if tope_segundos <= 0:
        return _armar(
            Veredicto(
                estado=EstadoRegla.ERROR,
                mensaje="No se ejecutó: se agotó el tope de tiempo de la corrida.",
            )
        )

    # El hilo existe solo para poder RENDIRSE a tiempo. Un chequeo mal portado
    # (un repo en un disco de red, git colgado) no puede quedarse con el turno
    # del agente; se lo deja terminar solo y se sigue sin él.
    ejecutor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="edecan-regla")
    try:
        futuro = ejecutor.submit(regla.comprobar, contexto)
        try:
            return _armar(futuro.result(timeout=tope_segundos))
        except _TiempoAgotado:
            return _armar(
                Veredicto(
                    estado=EstadoRegla.ERROR,
                    mensaje=(
                        f"El chequeo no terminó dentro de {tope_segundos:g}s; no se sabe si "
                        "la regla se cumple."
                    ),
                )
            )
        except Exception as exc:  # noqa: BLE001 - el aislamiento es el punto
            return _armar(
                Veredicto(
                    estado=EstadoRegla.ERROR,
                    mensaje=f"El chequeo falló: {type(exc).__name__}: {exc}",
                )
            )
    finally:
        # wait=False a propósito: esperar a un chequeo que ya se pasó del tope
        # sería justo lo que el tope existe para evitar.
        ejecutor.shutdown(wait=False)


def verificar_todas(
    workspace_root: Path,
    *,
    alcance: Alcance | None = None,
    reglas: Sequence[ReglaVerificable] | None = None,
    tope_segundos_por_regla: float = TOPE_SEGUNDOS_POR_REGLA,
    tope_segundos_total: float = TOPE_SEGUNDOS_TOTAL,
) -> ReporteVerificacion:
    """Corre el catálogo sobre ``workspace_root`` y devuelve el reporte.

    ``alcance`` es lo que hace que el reporte hable del trabajo del agente y no
    del repo entero -- ver punto 6 del docstring del módulo. Se arma con
    ``alcance_de_la_sesion(raiz, base)`` (lo normal: el diff contra el commit
    donde arrancó la sesión) o con ``alcance_de_archivos(rutas)`` cuando quien
    llama ya sabe exactamente qué se tocó. Omitirlo revisa el workspace
    completo, y entonces el bloque del prompt avisa que lo que trae puede ser
    preexistente.

    Todas las reglas comparten un mismo ``ContextoDeChequeo``, así que los
    archivos se leen una sola vez por corrida. Ninguna regla puede tumbar a
    otra: cada una corre aislada y con su propio tope, y lo que sobra del tope
    total limita a las que faltan.
    """

    raiz = _raiz_valida(workspace_root)
    contexto = ContextoDeChequeo(raiz, alcance)
    catalogo = tuple(reglas) if reglas is not None else CATALOGO

    inicio = time.monotonic()
    resultados: list[ResultadoRegla] = []
    for regla in catalogo:
        restante = tope_segundos_total - (time.monotonic() - inicio)
        resultados.append(_correr_regla(regla, contexto, min(tope_segundos_por_regla, restante)))

    return ReporteVerificacion(
        raiz=str(raiz),
        resultados=tuple(resultados),
        duracion_segundos=time.monotonic() - inicio,
        alcance=contexto.alcance,
    )
