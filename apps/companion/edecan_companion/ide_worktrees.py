"""Un worktree de git por sub-agente: pisarse deja de ser *posible*.

``ide_reparto.py`` ya evita que dos sub-agentes editen el mismo archivo: mira
las rutas que cada paso declara y, si duda, los pone en oleadas distintas. Eso
funciona, pero la garantía es de **disciplina** -- depende de que las rutas
declaradas sean honestas y de que el sub-agente no toque nada más. Un agente
que "de paso" arregla un import en un archivo ajeno rompe esa garantía sin que
nadie se entere hasta ver el diff roto.

Con un worktree por sub-agente la garantía pasa a ser **física**: cada uno
trabaja en su propia copia del árbol; escriban lo que escriban, no comparten un
solo byte de working tree. Perder trabajo ajeno deja de ser un riesgo que
administrar y pasa a ser algo que no puede ocurrir.

Lo que este módulo NO hace, a propósito: resolver conflictos. Ver ``fusionar``.

Costo real, medido en este mismo repo (467k líneas, 1788 archivos rastreados,
19 MB de blobs en HEAD):

    crear un worktree ......... 0,24 s     23 MB en disco
    destruirlo ................ 0,13 s

Barato, pero con dos letras chicas que importan:

1. Un worktree solo contiene lo **rastreado**. ``.venv``, ``node_modules`` y
   cualquier artefacto de build quedan fuera (están en ``.gitignore``). Un
   sub-agente que necesite compilar o correr la suite dentro de su worktree no
   encuentra las dependencias instaladas: o se le comparten desde el repo base,
   o se instalan ahí, o esa sub-tarea no va en worktree. Es la razón principal
   por la que este módulo NO se activa solo.
2. Por lo mismo, lo que ``.gitignore`` excluye **no vuelve** en la fusión: si
   el agente escribe en ``dist/``, ese archivo se queda en el worktree y muere
   con él. ``cosechar`` lo reporta en ``ignorados`` para que no sea una sorpresa.

Cambios sin commitear en el repo base
-------------------------------------
Un worktree nuevo se crea a partir de un *commit*, así que por defecto NO vería
el trabajo a medias del dueño -- los sub-agentes editarían una versión vieja del
código y casi todo lo que produjeran chocaría al volver. Por eso ``crear``
arranca tomando un **snapshot** del estado real con ``git stash create``: un
objeto commit con el árbol de trabajo tal cual está (staged + sin stagear), que
NO toca el working tree ni el índice ni la pila de stash del dueño. Todos los
worktrees de la corrida salen de ese snapshot, y la fusión se calcula contra él.

Consecuencias, que conviene tener presentes:

- Los archivos **sin rastrear** (``??``) no entran en el snapshot: ``git stash
  create`` no los incluye. Un sub-agente no ve el archivo nuevo que el dueño
  acaba de crear y todavía no agregó a git.
- Si el dueño sigue editando mientras los agentes trabajan, ``aplicar`` lo
  detecta y **no aplica nada** (``git apply`` es todo-o-nada). Nunca se pisa una
  edición humana en curso.

Una corrida = un solo lote de sub-agentes paralelos
---------------------------------------------------
TODOS los worktrees de una corrida salen del MISMO snapshot, y eso acota dónde
sirve esta pieza: sirve para sub-agentes que corren **a la vez y son
independientes**. NO sirve, tal cual está, para pasos que van uno después de
otro, porque el segundo no vería lo que hizo el primero -- saldría del snapshot
original, trabajaría a ciegas sobre la versión vieja y, al volver, chocaría con
él en ``fusionar`` (y entonces no se integra nada, ni siquiera lo del primero).

Eso importa mucho al conectarlo con ``ide_reparto.PlanificadorReparto``, porque
sus oleadas son exactamente eso: la oleada 2 existe *porque* sus pasos chocan
con los de la oleada 1. Envolver su runner sin más convierte "secuencial a
propósito" en "paralelo por accidente". Mientras este módulo no sepa avanzar su
base entre oleadas (fusionar lo de la oleada N y arrancar la N+1 desde ahí,
guardando el snapshot original para ``aplicar``), el aislamiento se conecta a
UNA oleada, o se usa ``ide_equipo.EquipoDeAgentes`` directamente.

Sin git no se rompe
-------------------
``evaluar_repo`` responde antes de construir nada: si no hay git, si la carpeta
no es un repo, si no hay ni un commit, si el git es viejo o si no alcanza el
disco, devuelve ``usable=False`` con un motivo en español. Quien orqueste usa
ese motivo para caer al reparto de ``ide_reparto`` tal como hoy, y decírselo al
dueño. Este módulo nunca es un requisito: es una mejora que se aplica cuando se
puede.
"""

from __future__ import annotations

import asyncio
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from edecan_companion.ide_equipo import ControlEquipo, Subtarea, SubtareaRunner

__all__ = [
    "MAX_WORKTREES_PERMITIDOS",
    "VERSION_GIT_MINIMA",
    "ConflictoFusion",
    "Cosecha",
    "DiagnosticoWorktrees",
    "GestorWorktrees",
    "ResultadoAplicacion",
    "ResultadoFusion",
    "TopesWorktrees",
    "Worktree",
    "WorktreeError",
    "barrer_huerfanos",
    "envolver_runner",
    "evaluar_repo",
]

# Mismo techo que ``ide_equipo.MAX_CONCURRENCIA_PERMITIDA``: no tiene sentido
# permitir más copias del árbol que sub-agentes simultáneos.
MAX_WORKTREES_PERMITIDOS = 8

# ``git merge-tree --write-tree`` (git 2.38, oct-2022) es lo que permite
# calcular una fusión REAL sin tocar el working tree del dueño ni el índice.
# Sin él habría que hacer el merge en algún checkout de verdad, y un merge a
# medias en el repo de alguien es exactamente lo que este módulo existe para
# evitar. Con git viejo preferimos no ofrecer worktrees antes que ofrecer una
# fusión que no podemos verificar sin riesgo.
VERSION_GIT_MINIMA = (2, 38)

# Identidad fija para los commits internos de cosecha. Son commits de
# andamiaje que viven en ``refs/edecan/`` y se borran al cerrar: no deben
# llevar la identidad del dueño ni depender de que tenga ``user.email``
# configurado globalmente (si no lo tuviera, ``git commit`` fallaría).
_AUTOR_COSECHA = "Edecán"
_CORREO_COSECHA = "edecan@localhost"

_ESPACIO_REF = "refs/edecan/worktrees"
_ARCHIVO_CORRIDA = "edecan-run.json"
_CARACTER_INVALIDO = re.compile(r"[^A-Za-z0-9._-]+")
_VERSION_GIT = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")

CodigoDiagnostico = Literal[
    "ok",
    "sin_git",
    "git_viejo",
    "no_es_repo",
    "sin_commits",
    "repo_enorme",
    "poco_disco",
]


class WorktreeError(ValueError):
    """Operación de worktree inválida o fallida.

    Hereda de ``ValueError`` por consistencia con ``EquipoError`` y
    ``RepartoError``: quien orqueste ya captura ese tipo para degradar a
    secuencial en vez de reventarle el turno al dueño.
    """


# ---------------------------------------------------------------------------
# Topes. "Un worktree en un repo grande cuesta disco y tiempo": estos son los
# límites que impiden que la mejora se convierta en el problema.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TopesWorktrees:
    """Límites duros de la corrida. Los defaults salen de la medición real.

    ``max_segundos_creacion`` es el tope adaptativo y el más útil de todos: si
    el PRIMER worktree tarda más que eso, crear los demás se rechaza. En este
    repo un worktree tarda 0,24 s; si en el repo de alguien tarda un minuto,
    paralelizar cuesta más de lo que ahorra y es mejor enterarse al primero que
    al octavo.
    """

    max_worktrees: int = 4
    max_mb_por_worktree: float = 1024.0
    # Disco libre exigido = (tamaño estimado x worktrees) x este margen. x3
    # deja aire para el propio trabajo de los agentes y para que la máquina no
    # quede al borde: llenar el disco de alguien es peor que no paralelizar.
    margen_disco: float = 3.0
    timeout_git_s: float = 120.0
    max_segundos_creacion: float = 45.0
    # Un worktree huérfano de una corrida muerta se barre en la siguiente. El
    # día de gracia es para no borrarle material a una corrida viva de otro
    # proceso cuyo pid no pudimos verificar.
    max_edad_huerfano_s: float = 24 * 3600.0

    def __post_init__(self) -> None:
        if not 1 <= self.max_worktrees <= MAX_WORKTREES_PERMITIDOS:
            raise WorktreeError(
                "max_worktrees debe estar entre 1 y "
                f"{MAX_WORKTREES_PERMITIDOS}; se recibió {self.max_worktrees}."
            )
        if self.max_mb_por_worktree <= 0 or self.margen_disco <= 0:
            raise WorktreeError("Los topes de disco deben ser positivos.")
        if self.timeout_git_s <= 0 or self.max_segundos_creacion <= 0:
            raise WorktreeError("Los topes de tiempo deben ser positivos.")


# ---------------------------------------------------------------------------
# Ejecución de git. Igual que ``ide_git.py``: argv fijo, ``shell=False``.
# ---------------------------------------------------------------------------


def _entorno_git() -> dict[str, str]:
    """Entorno limpio para cada invocación de git.

    ``GIT_DIR``/``GIT_WORK_TREE``/``GIT_INDEX_FILE`` se borran porque si el
    companion llegara a ejecutarse desde dentro de un hook de git, esas
    variables secuestrarían nuestro ``-C`` y escribiríamos en el repo
    equivocado. ``GIT_TERMINAL_PROMPT=0`` evita que una operación quede colgada
    esperando credenciales que nadie va a escribir.
    """
    entorno = dict(os.environ)
    for variable in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_NAMESPACE"):
        entorno.pop(variable, None)
    entorno["GIT_TERMINAL_PROMPT"] = "0"
    return entorno


def _git(
    binario: str,
    cwd: Path,
    args: list[str],
    *,
    timeout: float,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    # ``gc.auto=0``: nuestras operaciones crean objetos sueltos (snapshots,
    # cosechas) y un ``git gc`` automático disparado a mitad de una corrida en
    # un repo grande es un congelamiento de varios segundos que el dueño no
    # pidió y no puede explicarse.
    argv = [binario, "-C", str(cwd), "-c", "gc.auto=0", *args]
    try:
        completado = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            shell=False,
            timeout=timeout,
            check=False,
            env=_entorno_git(),
        )
    except subprocess.TimeoutExpired as exc:
        raise WorktreeError(f"git {args[0]} superó el tiempo límite de {timeout:.0f} s.") from exc
    except OSError as exc:
        raise WorktreeError(f"No se pudo ejecutar git: {exc}") from exc
    if check and completado.returncode != 0:
        detalle = completado.stderr.decode("utf-8", errors="replace").strip()
        raise WorktreeError(detalle[:1000] or f"git {args[0]} falló ({completado.returncode}).")
    return completado


def _texto(completado: subprocess.CompletedProcess[bytes]) -> str:
    return completado.stdout.decode("utf-8", errors="replace").strip()


def _podar_solo_lo_nuestro(
    binario: str, raiz: Path, nuestras_rutas: Path, *, timeout: float
) -> None:
    """Equivalente de ``git worktree prune``, pero acotado a NUESTROS worktrees.

    ``git worktree prune`` no acepta un filtro y, sin ``--expire``, no tiene
    fecha de gracia: barre toda entrada de ``.git/worktrees`` cuya carpeta no
    exista *en este instante*. Si el dueño tiene un worktree suyo en un volumen
    desmontado, en una carpeta sincronizada que se desalojó, o simplemente
    movida a mano esperando un ``git worktree repair``, un prune nuestro se la
    desconecta del repo: sus archivos siguen ahí pero el worktree queda en
    ``fatal: not a git repository``, y se lleva su índice, su HEAD y sus reflogs
    (verificado). Este módulo limpia lo que ensucia y nada más, así que en vez
    de podar el repo entero borra a mano la carpeta de administración de las
    entradas muertas que apuntan dentro de ``nuestras_rutas``.
    """
    try:
        completado = _git(
            binario,
            raiz,
            ["rev-parse", "--path-format=absolute", "--git-common-dir"],
            timeout=timeout,
            check=False,
        )
    except WorktreeError:
        # Corre dentro de ``destruir``, que promete no lanzar: un git colgado
        # acá taparía el error real del sub-agente.
        return
    if completado.returncode != 0:
        return
    admin_raiz = Path(_texto(completado)) / "worktrees"
    if not admin_raiz.is_dir():
        return
    try:
        entradas = sorted(admin_raiz.iterdir())
    except OSError:  # pragma: no cover - defensivo
        return
    for admin in entradas:
        try:
            destino = Path((admin / "gitdir").read_text(encoding="utf-8").strip())
        except OSError:
            continue
        arbol = destino.parent
        if not arbol.is_relative_to(nuestras_rutas):
            continue  # del dueño: no se toca ni para mirar
        if arbol.exists():
            continue  # viva
        shutil.rmtree(admin, ignore_errors=True)


def _slug(valor: str) -> str:
    """Nombre seguro para carpeta y para ref de git, estable y sin colisiones.

    El sufijo con hash no es decorativo: dos ids distintos ("api/paso 1" y
    "api-paso-1") colapsarían al mismo slug, y dos worktrees compartiendo
    carpeta sería justo el bug que este módulo existe para hacer imposible.
    """
    limpio = _CARACTER_INVALIDO.sub("-", valor.strip())
    limpio = re.sub(r"\.{2,}", ".", limpio).strip("-.")[:32]
    huella = hashlib.sha256(valor.encode("utf-8")).hexdigest()[:8]
    return f"{limpio}-{huella}" if limpio else huella


# ---------------------------------------------------------------------------
# Diagnóstico previo: ¿se puede, aquí y ahora?
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DiagnosticoWorktrees:
    """Respuesta a "¿puedo usar worktrees en este repo?" sin excepciones.

    ``motivo`` está escrito para mostrárselo al dueño tal cual: cuando
    ``usable`` es ``False``, quien orqueste cae al reparto de ``ide_reparto`` y
    le dice por qué, en vez de fallar en silencio o fallar y ya.
    """

    usable: bool
    codigo: CodigoDiagnostico
    motivo: str
    version_git: tuple[int, ...] | None = None
    raiz_repo: Path | None = None
    mb_por_worktree: float | None = None
    mb_libres: float | None = None


def _version_git(binario: str, timeout: float) -> tuple[int, ...] | None:
    completado = subprocess.run(
        [binario, "--version"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        shell=False,
        timeout=timeout,
        check=False,
        env=_entorno_git(),
    )
    if completado.returncode != 0:
        return None
    encontrado = _VERSION_GIT.search(_texto(completado))
    if not encontrado:
        return None
    return tuple(int(parte) for parte in encontrado.groups() if parte is not None)


def _mb_del_arbol(binario: str, raiz: Path, commit: str, timeout: float) -> float:
    """Peso descomprimido de los archivos rastreados en ``commit``, en MB.

    Es el tamaño que va a ocupar cada worktree. Se mide con ``ls-tree`` (0,02 s
    en este repo) en vez de con ``du`` sobre un worktree ya creado, porque el
    punto de medirlo es decidir ANTES de crearlo.
    """
    completado = _git(binario, raiz, ["ls-tree", "-r", "--long", commit], timeout=timeout)
    total = 0
    for linea in completado.stdout.decode("utf-8", errors="replace").splitlines():
        cabecera = linea.split("\t", 1)[0].split()
        if len(cabecera) < 4:
            continue
        try:
            total += int(cabecera[3])
        except ValueError:
            # Submódulos y entradas sin tamaño ("-"): no aportan bytes al
            # checkout de este repo.
            continue
    return total / 1_048_576


def evaluar_repo(
    ruta: Path | str,
    *,
    topes: TopesWorktrees | None = None,
    base_dir: Path | None = None,
) -> DiagnosticoWorktrees:
    """Decide si vale la pena (y se puede) aislar sub-agentes en worktrees.

    No lanza excepciones a propósito: la respuesta "no se puede" es un
    resultado normal y esperado, no un error. El llamador la usa para elegir
    entre este módulo y el reparto de siempre.
    """
    topes = topes or TopesWorktrees()
    binario = shutil.which("git")
    if binario is None:
        return DiagnosticoWorktrees(
            usable=False,
            codigo="sin_git",
            motivo=(
                "Git no está instalado o no está en el PATH. Los sub-agentes "
                "se reparten por archivos, como hasta ahora."
            ),
        )

    version = _version_git(binario, topes.timeout_git_s)
    if version is None or version < VERSION_GIT_MINIMA:
        legible = ".".join(str(n) for n in version) if version else "desconocida"
        minima = ".".join(str(n) for n in VERSION_GIT_MINIMA)
        return DiagnosticoWorktrees(
            usable=False,
            codigo="git_viejo",
            motivo=(
                f"Tu git es {legible} y hace falta {minima} o más nuevo para calcular "
                "la fusión sin tocar tu copia de trabajo. Los sub-agentes se reparten "
                "por archivos, como hasta ahora."
            ),
            version_git=version,
        )

    carpeta = Path(ruta).expanduser()
    if not carpeta.is_dir():
        return DiagnosticoWorktrees(
            usable=False,
            codigo="no_es_repo",
            motivo="La carpeta del proyecto no existe.",
            version_git=version,
        )

    completado = _git(
        binario, carpeta, ["rev-parse", "--show-toplevel"], timeout=topes.timeout_git_s, check=False
    )
    if completado.returncode != 0 or not _texto(completado):
        return DiagnosticoWorktrees(
            usable=False,
            codigo="no_es_repo",
            motivo=(
                "Este proyecto no es un repositorio Git, así que no se pueden aislar "
                "los sub-agentes en copias separadas. Se reparten por archivos, como "
                "hasta ahora."
            ),
            version_git=version,
        )
    raiz = Path(_texto(completado)).resolve()

    completado = _git(
        binario, raiz, ["rev-parse", "--verify", "HEAD"], timeout=topes.timeout_git_s, check=False
    )
    if completado.returncode != 0:
        return DiagnosticoWorktrees(
            usable=False,
            codigo="sin_commits",
            motivo=(
                "El repositorio todavía no tiene ningún commit: no hay un punto de "
                "partida desde el cual crear copias. Haz un primer commit o deja que "
                "los sub-agentes se repartan por archivos."
            ),
            version_git=version,
            raiz_repo=raiz,
        )

    mb = _mb_del_arbol(binario, raiz, _texto(completado), topes.timeout_git_s)
    if mb > topes.max_mb_por_worktree:
        return DiagnosticoWorktrees(
            usable=False,
            codigo="repo_enorme",
            motivo=(
                f"Cada copia del proyecto pesaría {mb:.0f} MB y el tope es "
                f"{topes.max_mb_por_worktree:.0f} MB. Los sub-agentes se reparten por "
                "archivos para no llenarte el disco."
            ),
            version_git=version,
            raiz_repo=raiz,
            mb_por_worktree=mb,
        )

    destino = _resolver_base_dir(base_dir)
    destino.mkdir(parents=True, exist_ok=True)
    mb_libres = shutil.disk_usage(destino).free / 1_048_576
    necesarios = mb * topes.max_worktrees * topes.margen_disco
    if mb_libres < necesarios:
        return DiagnosticoWorktrees(
            usable=False,
            codigo="poco_disco",
            motivo=(
                f"Harían falta ~{necesarios:.0f} MB libres para {topes.max_worktrees} "
                f"copias del proyecto y quedan {mb_libres:.0f} MB. Los sub-agentes se "
                "reparten por archivos."
            ),
            version_git=version,
            raiz_repo=raiz,
            mb_por_worktree=mb,
            mb_libres=mb_libres,
        )

    return DiagnosticoWorktrees(
        usable=True,
        codigo="ok",
        motivo=(
            f"Cada sub-agente trabaja en su propia copia (~{mb:.0f} MB); no pueden "
            "pisarse."
        ),
        version_git=version,
        raiz_repo=raiz,
        mb_por_worktree=mb,
        mb_libres=mb_libres,
    )


def _resolver_base_dir(base_dir: Path | None) -> Path:
    """Dónde viven los worktrees.

    Fuera del repo, siempre: un worktree DENTRO del árbol de trabajo aparece
    como un montón de archivos sin rastrear en el ``git status`` del dueño, y
    hace que buscadores y mapas del proyecto indexen el repo dos veces.
    El directorio temporal del sistema, además, es una de las pocas rutas que
    ``ide_workspaces.validate_workspace_root`` acepta autorizar sin pelear.
    """
    if base_dir is not None:
        return Path(base_dir).expanduser().resolve()
    return Path(tempfile.gettempdir()).resolve() / "edecan-worktrees"


# ---------------------------------------------------------------------------
# Piezas del resultado.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Worktree:
    """Una copia viva del proyecto, dueña exclusiva de su carpeta."""

    id: str
    ruta: Path
    commit_base: str
    creacion_s: float


@dataclass(frozen=True, slots=True)
class Cosecha:
    """Lo que un sub-agente dejó en su worktree, ya congelado en un commit."""

    worktree_id: str
    commit: str | None
    archivos: tuple[str, ...] = ()
    ignorados: tuple[str, ...] = ()

    @property
    def sin_cambios(self) -> bool:
        return self.commit is None


@dataclass(frozen=True, slots=True)
class ConflictoFusion:
    """Un choque real entre lo que hizo un sub-agente y lo ya integrado.

    ``archivos`` son los que git marcó en conflicto. Nadie los resuelve
    automáticamente: se muestran y decide una persona.
    """

    worktree_id: str
    archivos: tuple[str, ...]
    ref: str
    detalle: str = ""

    def __str__(self) -> str:  # pragma: no cover - solo formatea texto
        return f"{self.worktree_id}: {', '.join(self.archivos)}"


@dataclass(frozen=True, slots=True)
class ResultadoFusion:
    """El resultado de juntar lo que hicieron todos los sub-agentes.

    ``ok=False`` significa "hay conflictos, no toqué nada". Deliberadamente NO
    existe una variante "integré lo que pude": si el aporte de A y el de B
    chocan, quedarse con A es elegir por el dueño sin decírselo. Eso es
    exactamente lo que no se hace acá.
    """

    ok: bool
    commit_base: str
    commit_integrado: str | None
    archivos: tuple[str, ...] = ()
    aportaron: tuple[str, ...] = ()
    sin_cambios: tuple[str, ...] = ()
    conflictos: tuple[ConflictoFusion, ...] = ()
    refs_conservadas: dict[str, str] = field(default_factory=dict)

    @property
    def hay_cambios(self) -> bool:
        return self.commit_integrado is not None and bool(self.archivos)

    def resumen(self) -> str:
        if self.conflictos:
            detalle = "; ".join(
                f"{c.worktree_id} en {', '.join(c.archivos[:5])}" for c in self.conflictos
            )
            return (
                f"No se fusionó nada: {len(self.conflictos)} sub-agente(s) chocan con el "
                f"resto ({detalle}). El trabajo de cada uno quedó guardado en su ref "
                "para que decidas tú cuál se queda."
            )
        if not self.hay_cambios:
            return "Ningún sub-agente cambió archivos rastreados."
        partes = [
            f"{len(self.aportaron)} sub-agente(s) fusionados sin conflicto en "
            f"{len(self.archivos)} archivo(s)"
        ]
        if self.sin_cambios:
            partes.append(f"{len(self.sin_cambios)} sin cambios")
        return ". ".join(partes) + "."


@dataclass(frozen=True, slots=True)
class ResultadoAplicacion:
    """Qué pasó al llevar la integración al árbol de trabajo del dueño.

    ``refs`` es la dirección exacta de lo que quedó guardado en ``refs/edecan/``:
    cuando no se aplicó, es dónde sigue vivo el trabajo de los sub-agentes; y
    cuando sí se aplicó, ``refs["base"]`` es el árbol tal como estaba antes, o
    sea el deshacer. En los dos casos la interfaz tiene que poder decirlo: un
    "no se aplicó nada" a secas suena a que no quedó nada.
    """

    aplicado: bool
    motivo: str
    archivos: tuple[str, ...] = ()
    refs: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# El gestor.
# ---------------------------------------------------------------------------


class GestorWorktrees:
    """Crea, cosecha, fusiona y destruye los worktrees de UNA corrida.

    Uso previsto (el ``with`` no es opcional: es la limpieza garantizada)::

        diagnostico = evaluar_repo(raiz)
        if not diagnostico.usable:
            ...  # reparto de ide_reparto, y mostrar diagnostico.motivo
        with GestorWorktrees(raiz) as gestor:
            equipo = EquipoDeAgentes(runner=envolver_runner(gestor, mi_runner))
            await equipo.ejecutar(plan)
            fusion = gestor.fusionar()
            if fusion.ok:
                gestor.aplicar(fusion)

    Una instancia = una corrida. Es seguro llamar a sus métodos desde varios
    hilos (``asyncio.to_thread``): las mutaciones van bajo un lock propio, y
    cada worktree tiene su propio índice de git, así que dos ``cosechar``
    simultáneos no se disputan ``index.lock``.
    """

    def __init__(
        self,
        repo: Path | str,
        *,
        base_dir: Path | None = None,
        topes: TopesWorktrees | None = None,
    ) -> None:
        self.topes = topes or TopesWorktrees()
        diagnostico = evaluar_repo(repo, topes=self.topes, base_dir=base_dir)
        if not diagnostico.usable or diagnostico.raiz_repo is None:
            # Quien no quiera excepciones llama a ``evaluar_repo`` primero;
            # llegar acá con un repo inservible es un error de programación del
            # llamador, no una condición normal.
            raise WorktreeError(diagnostico.motivo)
        binario = shutil.which("git")
        if binario is None:  # pragma: no cover - evaluar_repo ya lo cubrió
            raise WorktreeError("Git no está instalado o no está en PATH.")
        self._git_bin = binario
        self.raiz = diagnostico.raiz_repo
        self.diagnostico = diagnostico
        self.run_id = f"{int(time.time())}-{os.getpid()}-{os.urandom(3).hex()}"
        self._dir_repo = _resolver_base_dir(base_dir) / _slug(str(self.raiz))
        self._dir_corrida = self._dir_repo / self.run_id
        self._lock = threading.RLock()
        self._activos: dict[str, Worktree] = {}
        self._cosechas: dict[str, Cosecha] = {}
        self._commit_base: str | None = None
        self._creacion_mas_lenta = 0.0
        self._cerrado = False
        self._refs_conservadas: dict[str, str] = {}
        # Se enciende en cuanto una cosecha produce un commit y ya no se apaga:
        # a partir de ahí los refs de la corrida son el ÚNICO lugar donde existe
        # ese trabajo (la carpeta del worktree se destruye enseguida) y, si se
        # aplicó, además son el único "antes" del árbol del dueño. Decide si
        # ``cerrar`` puede borrarlos; los recoge ``barrer_huerfanos`` por vejez.
        self._refs_con_trabajo = False

    # -- infraestructura interna -------------------------------------------

    def _run(
        self, args: list[str], *, cwd: Path | None = None, check: bool = True
    ) -> subprocess.CompletedProcess[bytes]:
        return _git(
            self._git_bin,
            cwd or self.raiz,
            args,
            timeout=self.topes.timeout_git_s,
            check=check,
        )

    def _intentar(self, args: list[str], *, cwd: Path | None = None) -> None:
        """Un git de limpieza: ni su fallo ni su timeout suben.

        La limpieza suele correr dentro de un ``finally`` que está atendiendo
        OTRO error (el del sub-agente). Si un git colgado levantara acá,
        sustituiría esa causa real por un "git tardó demasiado" y el dueño
        perdería la única pista de lo que de verdad pasó.
        """
        try:
            self._run(args, cwd=cwd, check=False)
        except WorktreeError:
            pass

    def _ref(self, worktree_id: str) -> str:
        return f"{_ESPACIO_REF}/{self.run_id}/{_slug(worktree_id)}"

    @property
    def commit_base(self) -> str:
        """Snapshot desde el que salen TODOS los worktrees de esta corrida.

        Se calcula una sola vez, al crear el primero: si se recalculara por
        worktree, dos sub-agentes podrían partir de estados distintos del
        árbol y la fusión mezclaría manzanas con peras.
        """
        with self._lock:
            if self._commit_base is None:
                self._commit_base = self._tomar_snapshot()
            return self._commit_base

    def _tomar_snapshot(self) -> str:
        cabeza = _texto(self._run(["rev-parse", "HEAD"]))
        # ``stash create`` NO toca el working tree, ni el índice, ni la pila de
        # stash: solo escribe objetos y devuelve el commit. Es la única forma
        # de que los sub-agentes vean el trabajo a medias del dueño sin
        # arrebatárselo. Devuelve vacío cuando no hay nada que guardar.
        creado = _texto(self._run(["stash", "create", "edecan: base de sub-agentes"], check=False))
        snapshot = creado or cabeza
        self._dir_corrida.mkdir(parents=True, exist_ok=True)
        # Un ref real ancla el snapshot: sin él sería un objeto suelto que un
        # ``git gc`` del propio dueño podría barrer a mitad de la corrida.
        self._run(["update-ref", f"{_ESPACIO_REF}/{self.run_id}/base", snapshot])
        (self._dir_corrida / _ARCHIVO_CORRIDA).write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "creado_en": time.time(),
                    "repo": str(self.raiz),
                    "run_id": self.run_id,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return snapshot

    # -- ciclo de vida de un worktree --------------------------------------

    def crear(self, worktree_id: str) -> Worktree:
        """Crea la copia aislada de un sub-agente.

        Ojo con los topes: cuando uno se alcanza, esto LANZA, y a través de
        ``envolver_runner`` eso significa que esa sub-tarea queda fallida sin
        haberse ejecutado. Aquí no hay degradación automática a "trabaja en el
        repo base" a propósito -- decidirlo por su cuenta sería romper el
        aislamiento en silencio, que es lo único que este módulo promete. Quien
        orqueste tiene que mantener la concurrencia del equipo <=
        ``topes.max_worktrees``, o atrapar ``WorktreeError`` y decidir.
        """
        if not isinstance(worktree_id, str) or not worktree_id.strip():
            raise WorktreeError("Todo worktree necesita un id no vacío.")
        with self._lock:
            if self._cerrado:
                raise WorktreeError("Este gestor ya se cerró.")
            if worktree_id in self._activos:
                raise WorktreeError(f"El worktree {worktree_id!r} ya existe en esta corrida.")
            if len(self._activos) >= self.topes.max_worktrees:
                raise WorktreeError(
                    f"Ya hay {self.topes.max_worktrees} copias abiertas, que es el tope de "
                    "esta corrida; esta sub-tarea no llegó a ejecutarse. Para que no "
                    "vuelva a pasar, la concurrencia del equipo no puede superar "
                    "max_worktrees."
                )
            # Tope adaptativo: si la primera copia salió cara, no se pagan más.
            if self._creacion_mas_lenta > self.topes.max_segundos_creacion:
                raise WorktreeError(
                    f"Crear una copia de este proyecto tardó {self._creacion_mas_lenta:.0f} s "
                    f"(tope: {self.topes.max_segundos_creacion:.0f} s). No compensa aislar "
                    "más sub-agentes en esta corrida; esta sub-tarea no llegó a ejecutarse."
                )
            base = self.commit_base
            destino = self._dir_corrida / _slug(worktree_id)
            if destino.exists():  # pragma: no cover - el hash del slug lo hace improbable
                raise WorktreeError(f"La carpeta {destino} ya está ocupada.")
            inicio = time.monotonic()
            # ``--detach``: sin rama. Una rama por sub-agente ensuciaría el
            # ``git branch`` del dueño con basura de andamiaje, y si la
            # limpieza fallara se la quedaría para siempre.
            self._run(["worktree", "add", "--quiet", "--detach", str(destino), base])
            duracion = time.monotonic() - inicio
            self._creacion_mas_lenta = max(self._creacion_mas_lenta, duracion)
            worktree = Worktree(
                id=worktree_id, ruta=destino, commit_base=base, creacion_s=duracion
            )
            self._activos[worktree_id] = worktree
            return worktree

    def destruir(self, worktree: Worktree) -> None:
        """Borra la copia. Nunca lanza: la limpieza no puede fallar hacia arriba.

        Va en tres pasadas porque cada una cubre una forma distinta de quedar a
        medias: ``worktree remove`` para el caso normal, ``rmtree`` para cuando
        git se negó (permisos, archivos abiertos) y la poda de metadatos para
        dejar ``.git/worktrees`` consistente en cualquiera de los dos.
        """
        with self._lock:
            self._activos.pop(worktree.id, None)
        self._intentar(["worktree", "remove", "--force", str(worktree.ruta)])
        if worktree.ruta.exists():
            shutil.rmtree(worktree.ruta, ignore_errors=True)
        # Acotada a NUESTRA carpeta, nunca ``git worktree prune`` a secas: ver
        # ``_podar_solo_lo_nuestro``.
        _podar_solo_lo_nuestro(
            self._git_bin, self.raiz, self._dir_repo, timeout=self.topes.timeout_git_s
        )

    @contextmanager
    def sesion(self, worktree_id: str) -> Iterator[Worktree]:
        """Un worktree con destrucción garantizada, pase lo que pase adentro."""
        worktree = self.crear(worktree_id)
        try:
            yield worktree
        finally:
            self.destruir(worktree)

    # -- cosecha ------------------------------------------------------------

    def cosechar(self, worktree: Worktree) -> Cosecha:
        """Congela en un commit lo que el sub-agente dejó en su worktree.

        Hay que hacerlo ANTES de destruir la copia: después no hay de dónde
        sacarlo. El commit queda anclado en ``refs/edecan/`` (invisible para
        ``git branch``) para que sobreviva a la destrucción de la carpeta.
        """
        estado = self._run(
            ["status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd=worktree.ruta
        )
        # Se pregunta por los ignorados ANTES de decidir si hubo cambios: el
        # caso peor es justo el sub-agente cuyo trabajo entero cayó en una
        # ruta ignorada (escribió solo en ``dist/``). Ahí ``status`` no ve
        # nada, y sin este dato la cosecha diría "no tocó nada" cuando en
        # realidad tocó bastante y se perdió todo en silencio.
        ignorados = self._ignorados(worktree)
        if not estado.stdout.strip(b"\0"):
            with self._lock:
                cosecha = Cosecha(worktree_id=worktree.id, commit=None, ignorados=ignorados)
                self._cosechas[worktree.id] = cosecha
            return cosecha

        self._run(["add", "--all", "--", "."], cwd=worktree.ruta)
        self._run(
            [
                "-c",
                f"user.name={_AUTOR_COSECHA}",
                "-c",
                f"user.email={_CORREO_COSECHA}",
                # Un hook ``pre-commit`` del dueño (o una firma GPG que pide
                # passphrase) colgaría o rechazaría un commit interno que él
                # nunca pidió y que además va a desaparecer al cerrar.
                "-c",
                "commit.gpgsign=false",
                "commit",
                "--no-verify",
                "--quiet",
                "--message",
                f"edecan: sub-agente {worktree.id}",
            ],
            cwd=worktree.ruta,
        )
        commit = _texto(self._run(["rev-parse", "HEAD"], cwd=worktree.ruta))
        self._run(["update-ref", self._ref(worktree.id), commit])
        archivos = tuple(
            sorted(
                nombre
                for nombre in _texto(
                    self._run(
                        ["diff", "--name-only", worktree.commit_base, commit], cwd=worktree.ruta
                    )
                ).splitlines()
                if nombre
            )
        )
        with self._lock:
            cosecha = Cosecha(
                worktree_id=worktree.id, commit=commit, archivos=archivos, ignorados=ignorados
            )
            self._cosechas[worktree.id] = cosecha
            # Desde este instante hay trabajo que solo existe en un ref: la
            # carpeta del worktree se destruye enseguida.
            self._refs_con_trabajo = True
        return cosecha

    def _ignorados(self, worktree: Worktree) -> tuple[str, ...]:
        """Lo que el sub-agente escribió y ``.gitignore`` excluye.

        No es un error, pero sí una pérdida silenciosa: eso no viaja de vuelta.
        Se usa ``--ignored=traditional`` (no ``matching``) porque colapsa
        carpetas enteras en una línea: un ``npm install`` dentro del worktree
        daría decenas de miles de rutas y volvería carísimo un dato que solo
        sirve para avisar.
        """
        completado = self._run(
            ["status", "--porcelain=v1", "-z", "--ignored=traditional"],
            cwd=worktree.ruta,
            check=False,
        )
        if completado.returncode != 0:  # pragma: no cover - defensivo
            return ()
        rutas = [
            registro[3:]
            for registro in completado.stdout.decode("utf-8", errors="replace").split("\0")
            if registro.startswith("!! ")
        ]
        return tuple(sorted(rutas)[:20])

    # -- fusión: el corazón del asunto --------------------------------------

    def _fusionar_par(self, acumulado: str, candidato: str) -> tuple[str | None, tuple[str, ...]]:
        """Fusiona dos commits en memoria. Devuelve ``(arbol, conflictos)``.

        ``merge-tree --write-tree`` hace la fusión a tres bandas de verdad
        (misma maquinaria que ``git merge``) pero escribe solo objetos: no hay
        índice, no hay checkout, no hay working tree que pueda quedar a medias
        si esto falla. Por eso probar una fusión no tiene riesgo alguno.
        """
        completado = self._run(
            ["merge-tree", "--write-tree", "--name-only", "-z", acumulado, candidato],
            check=False,
        )
        campos = completado.stdout.decode("utf-8", errors="replace").split("\0")
        if completado.returncode == 0:
            return (campos[0].strip() if campos else None), ()
        if completado.returncode != 1:
            detalle = completado.stderr.decode("utf-8", errors="replace").strip()
            raise WorktreeError(detalle[:1000] or "No se pudo calcular la fusión.")
        conflictos: list[str] = []
        for campo in campos[1:]:
            if campo == "":
                break
            conflictos.append(campo)
        return None, tuple(sorted(set(conflictos)))

    def fusionar(self, orden: list[str] | None = None) -> ResultadoFusion:
        """Junta los aportes de todos los sub-agentes cosechados.

        La regla que gobierna esta función, y la razón de que exista este
        módulo: **si algo choca, no se integra nada**. Un merge automático
        equivocado en el repo del dueño es peor que no haber paralelizado,
        porque el error viaja disfrazado de trabajo terminado.

        No toca el working tree ni el índice del dueño: todo ocurre entre
        objetos de git. Cuando ``ok`` es ``False``, el commit de cada
        sub-agente queda vivo en ``refs/edecan/`` (ver
        ``ResultadoFusion.refs_conservadas``) para que una persona pueda
        mirarlos con ``git diff`` y decidir.
        """
        with self._lock:
            ids = list(orden) if orden is not None else list(self._cosechas)
            faltantes = [i for i in ids if i not in self._cosechas]
            if faltantes:
                raise WorktreeError(f"No hay cosecha de: {', '.join(faltantes)}.")
            cosechas = [self._cosechas[i] for i in ids]
            base = self._commit_base

        if base is None:
            # Nadie llegó a crear un worktree: nada que fusionar, y sin
            # snapshot no hay contra qué comparar.
            return ResultadoFusion(ok=True, commit_base="", commit_integrado=None)

        acumulado = base
        aportaron: list[str] = []
        sin_cambios = tuple(c.worktree_id for c in cosechas if c.sin_cambios)
        conflictos: list[ConflictoFusion] = []

        for cosecha in cosechas:
            if cosecha.commit is None:
                continue
            arbol, chocados = self._fusionar_par(acumulado, cosecha.commit)
            if arbol is None:
                conflictos.append(
                    ConflictoFusion(
                        worktree_id=cosecha.worktree_id,
                        archivos=chocados,
                        ref=self._ref(cosecha.worktree_id),
                        detalle=(
                            f"El aporte de {cosecha.worktree_id} y lo ya integrado cambian "
                            "las mismas líneas."
                        ),
                    )
                )
                continue
            acumulado = _texto(
                self._run(
                    [
                        "-c",
                        f"user.name={_AUTOR_COSECHA}",
                        "-c",
                        f"user.email={_CORREO_COSECHA}",
                        "commit-tree",
                        arbol,
                        "-p",
                        acumulado,
                        "-p",
                        cosecha.commit,
                        "-m",
                        f"edecan: integra {cosecha.worktree_id}",
                    ]
                )
            )
            aportaron.append(cosecha.worktree_id)

        if conflictos:
            with self._lock:
                self._refs_conservadas = {
                    c.worktree_id: self._ref(c.worktree_id)
                    for c in cosechas
                    if c.commit is not None
                }
                refs = dict(self._refs_conservadas)
                refs["base"] = f"{_ESPACIO_REF}/{self.run_id}/base"
            return ResultadoFusion(
                ok=False,
                commit_base=base,
                commit_integrado=None,
                aportaron=tuple(aportaron),
                sin_cambios=sin_cambios,
                conflictos=tuple(conflictos),
                refs_conservadas=refs,
            )

        integrado = acumulado if acumulado != base else None
        archivos: tuple[str, ...] = ()
        if integrado is not None:
            archivos = tuple(
                sorted(
                    nombre
                    for nombre in _texto(
                        self._run(["diff", "--name-only", base, integrado])
                    ).splitlines()
                    if nombre
                )
            )
        return ResultadoFusion(
            ok=True,
            commit_base=base,
            commit_integrado=integrado,
            archivos=archivos,
            aportaron=tuple(aportaron),
            sin_cambios=sin_cambios,
        )

    # -- volver al repo del dueño -------------------------------------------

    def aplicar(self, resultado: ResultadoFusion) -> ResultadoAplicacion:
        """Lleva la integración al árbol de trabajo del dueño, sin commitear.

        Paso aparte de ``fusionar`` a propósito: fusionar es cálculo puro y no
        arriesga nada; esto sí escribe en el repo de una persona, así que es
        una decisión explícita de quien orqueste (o del propio dueño desde la
        interfaz).

        Se aplica como un parche contra el snapshot inicial, no como un
        checkout: ``git apply`` es todo-o-nada y verifica que cada archivo esté
        como lo dejamos. Si el dueño editó algo mientras los agentes
        trabajaban, esto NO aplica nada y lo dice. Los cambios quedan sin
        commitear, que es como se pueden revisar en ``DiffReview``.
        """
        if not resultado.ok:
            return ResultadoAplicacion(
                aplicado=False,
                motivo="La fusión tiene conflictos sin resolver; no se aplica nada.",
            )
        if resultado.commit_integrado is None:
            return ResultadoAplicacion(
                aplicado=True,
                motivo="Ningún sub-agente cambió archivos: no había nada que aplicar.",
            )
        parche = self._run(
            [
                "diff",
                "--binary",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
                resultado.commit_base,
                resultado.commit_integrado,
            ]
        ).stdout
        if not parche:  # pragma: no cover - defensivo
            return ResultadoAplicacion(aplicado=True, motivo="No había diferencias que aplicar.")
        verificacion = subprocess.run(
            [self._git_bin, "-C", str(self.raiz), "apply", "--check", "-"],
            input=parche,
            capture_output=True,
            shell=False,
            timeout=self.topes.timeout_git_s,
            check=False,
            env=_entorno_git(),
        )
        if verificacion.returncode != 0:
            detalle = verificacion.stderr.decode("utf-8", errors="replace").strip()
            # No se dice "cambiaste tu copia" a secas: la otra causa, igual de
            # común, es un archivo que el dueño tenía SIN RASTREAR y que por eso
            # no viajó al snapshot -- el sub-agente lo "crea" y git se niega a
            # pisarlo. Ahí él no cambió nada y echarle la culpa lo manda a
            # buscar un problema que no existe. El detalle de git nombra el
            # archivo exacto, así que va entero.
            return ResultadoAplicacion(
                aplicado=False,
                motivo=(
                    "Tu copia de trabajo ya no coincide con la del arranque, así que "
                    "no se aplicó nada para no pisar lo tuyo (o la editaste mientras "
                    "trabajaban, o alguno de estos archivos ya existía sin estar "
                    f"rastreado por git y por eso no viajó): {detalle[:500]}. Lo que "
                    "hicieron los sub-agentes no se perdió: está guardado y puedes "
                    "verlo con git diff."
                ),
                archivos=resultado.archivos,
                refs=self._refs_de_la_corrida(),
            )
        aplicacion = subprocess.run(
            [self._git_bin, "-C", str(self.raiz), "apply", "-"],
            input=parche,
            capture_output=True,
            shell=False,
            timeout=self.topes.timeout_git_s,
            check=False,
            env=_entorno_git(),
        )
        if aplicacion.returncode != 0:  # pragma: no cover - --check ya filtró
            detalle = aplicacion.stderr.decode("utf-8", errors="replace").strip()
            return ResultadoAplicacion(
                aplicado=False,
                motivo=f"Git rechazó el parche: {detalle[:500]}",
                archivos=resultado.archivos,
                refs=self._refs_de_la_corrida(),
            )
        refs = self._refs_de_la_corrida()
        base = refs.get("base", "")
        return ResultadoAplicacion(
            aplicado=True,
            motivo=(
                f"{len(resultado.archivos)} archivo(s) actualizados en tu copia de "
                "trabajo, sin commitear, listos para revisar. Si algo de esto pisó "
                "trabajo tuyo que no habías commiteado, así estaba tu árbol justo "
                f"antes: git checkout {base} -- ."
            ),
            archivos=resultado.archivos,
            refs=refs,
        )

    def _refs_de_la_corrida(self) -> dict[str, str]:
        """Dónde quedó guardado, ref por ref, todo lo de esta corrida.

        Se arma en el momento y no al cerrar porque el llamador la necesita
        para decírselo al dueño cuando algo salió distinto de lo esperado, que
        es justo cuando "no se aplicó nada" suena a "no quedó nada".
        """
        with self._lock:
            refs = {
                worktree_id: self._ref(worktree_id)
                for worktree_id, cosecha in self._cosechas.items()
                if cosecha.commit is not None
            }
        refs["base"] = f"{_ESPACIO_REF}/{self.run_id}/base"
        return refs

    # -- cierre y barrido ---------------------------------------------------

    def cerrar(self, *, conservar_refs: bool | None = None) -> None:
        """Destruye todo lo de esta corrida. Idempotente y sin excepciones.

        Por defecto conserva los refs de toda corrida que llegó a cosechar algo.
        No alcanza con conservarlos "cuando hubo conflicto": los dos caminos que
        más importan terminan con ``fusionar`` diciendo ``ok`` y el trabajo
        igualmente fuera del árbol del dueño --

        - ``aplicar`` se negó a escribir (él editó mientras trabajaban, o un
          archivo suyo sin rastrear no dejó pasar el parche). Ahí borrar los
          refs tira a la basura, sin aviso y sin vuelta atrás, lo que hicieron
          todos los sub-agentes: destruidos los worktrees, no queda copia;
        - ``aplicar`` sí escribió, y entonces el snapshot ``base`` es el único
          registro de cómo estaba el árbol del dueño ANTES -- incluido su
          trabajo sin commitear, que el parche pudo haber reescrito. Borrarlo es
          quitarle el deshacer.

        Los recoge ``barrer_huerfanos`` cuando envejecen. Quien de verdad quiera
        no dejar nada pasa ``conservar_refs=False``.
        """
        with self._lock:
            if self._cerrado:
                return
            self._cerrado = True
            activos = list(self._activos.values())
            if conservar_refs is None:
                conservar = bool(self._refs_conservadas) or self._refs_con_trabajo
            else:
                conservar = conservar_refs
        for worktree in activos:
            self.destruir(worktree)
        if not conservar:
            self._borrar_refs(self.run_id)
            shutil.rmtree(self._dir_corrida, ignore_errors=True)
            return
        self._marcar_refs_pendientes()

    def _marcar_refs_pendientes(self) -> None:
        """Deja escrito que esta corrida guarda trabajo humano por revisar.

        Sin esta marca, ``barrer_huerfanos`` da la corrida por abandonada en
        cuanto el companion se reinicia -- el pid que la creó ya no existe -- y
        borra los refs del conflicto la próxima vez que se abra el proyecto. Es
        decir: el trabajo que se conservó "para que decidas tú" desaparecería
        antes de que nadie lo mirara, que es exactamente el desastre que
        ``fusionar`` intenta evitar. Con la marca, esos refs solo se van por
        vejez (``max_edad_huerfano_s``), que es lo que su docstring promete.
        """
        archivo = self._dir_corrida / _ARCHIVO_CORRIDA
        try:
            datos = json.loads(archivo.read_text(encoding="utf-8"))
        except (OSError, ValueError):  # pragma: no cover - defensivo
            return
        datos["refs_pendientes"] = True
        try:
            archivo.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
        except OSError:  # pragma: no cover - defensivo
            return

    def _borrar_refs(self, run_id: str) -> None:
        try:
            completado = self._run(
                ["for-each-ref", "--format=%(refname)", f"{_ESPACIO_REF}/{run_id}"], check=False
            )
        except WorktreeError:  # pragma: no cover - defensivo
            return
        if completado.returncode != 0:  # pragma: no cover - defensivo
            return
        for ref in _texto(completado).splitlines():
            if ref.strip():
                self._intentar(["update-ref", "-d", ref.strip()])

    def barrer_huerfanos(self) -> list[str]:
        """Barre worktrees de corridas anteriores que murieron sin limpiar."""
        return barrer_huerfanos(
            self.raiz,
            base_dir=self._dir_repo.parent,
            topes=self.topes,
            excluir_run=self.run_id,
        )

    def __enter__(self) -> GestorWorktrees:
        return self

    def __exit__(self, *_excepcion: object) -> Literal[False]:
        self.cerrar()
        return False


# ---------------------------------------------------------------------------
# Barrido de huérfanos: la segunda mitad de la limpieza garantizada.
#
# ``try/finally`` cubre que el agente falle o lo cancelen. NO cubre que el
# proceso entero muera (kill -9, batería, reinicio): ahí no corre ningún
# ``finally``. Sin esto, cada muerte dejaría 23 MB y una entrada en
# ``.git/worktrees`` en el repo de alguien, para siempre.
# ---------------------------------------------------------------------------


def _pid_vivo(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _pid_vivo_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        # Existe pero es de otro usuario, o el sistema no deja preguntar. Se
        # asume vivo: no borrarle los worktrees a una corrida que quizás sigue
        # trabajando. La edad máxima es la red de seguridad de este caso.
        return True
    return True


def _pid_vivo_windows(pid: int) -> bool:
    """Equivalente de ``os.kill(pid, 0)`` en Windows -- pero sin usar
    ``os.kill``, que ahí NO es una comprobación inocua.

    La propia documentación de Python lo advierte para Windows: cualquier
    señal que no sea ``CTRL_C_EVENT``/``CTRL_BREAK_EVENT`` "causará que el
    proceso sea matado incondicionalmente por la API TerminateProcess, y el
    código de salida se pondrá a ``sig``" -- es decir, ``os.kill(pid, 0)``
    en Windows llamaría a ``TerminateProcess(handle, 0)`` y MATARÍA de
    verdad al proceso que se suponía que solo había que consultar (con
    código de salida 0, que además parece una salida limpia y no un
    zombie). Mismo tipo de trampa cross-platform que ya se identificó y
    arregló en ``ServidorOpencode.detener`` (``ide_opencode.py``): un
    primitivo de POSIX que en Windows hace otra cosa completamente distinta.

    Además hay una segunda trampa, más sutil, en el código que esta función
    reemplaza: para un PID que directamente NO EXISTE, ``os.kill`` en
    Windows no siempre levanta ``ProcessLookupError`` (la excepción que la
    rama POSIX usa para decidir "muerto") -- levanta un ``OSError`` genérico
    (``OpenProcess`` falla con ``ERROR_INVALID_PARAMETER``, que no se
    traduce a ``ProcessLookupError``). El ``except OSError: return True``
    de la rama POSIX, pensado para "existe pero no tengo permiso", entonces
    clasifica un PID MUERTO como VIVO en Windows -- exactamente al revés de
    lo que se necesita para barrer huérfanos.

    En vez de nada de eso: se abre un handle de solo consulta
    (``PROCESS_QUERY_LIMITED_INFORMATION``, el privilegio mínimo que no
    exige ser dueño del proceso ni administrador) directamente con la API
    de Windows vía ``ctypes``. Si se puede abrir, el proceso existe. Si
    falla con "acceso denegado" (``ERROR_ACCESS_DENIED``), se asume vivo --
    mismo criterio conservador que la rama POSIX: existe pero es de otro
    usuario, no vale la pena arriesgarse a borrarle el trabajo a una
    corrida que quizás sigue viva. Cualquier otro error (el caso normal de
    un PID que ya no existe, ``ERROR_INVALID_PARAMETER``) se trata como
    muerto.

    PENDIENTE (regla 6, "nada simulado"): la lógica se escribió y se probó
    contra los tests de este archivo en macOS con ``os.name`` forzado a
    ``"nt"`` -- pero ``ctypes.WinDLL`` no existe fuera de Windows, así que
    la propia llamada a ``kernel32.OpenProcess`` está mockeada en esos
    tests (ver ``test_ide_worktrees.py``). El resultado REAL contra
    Windows (que de verdad distinga un PID vivo de uno muerto sin matar
    nada) queda pendiente de una fase con acceso a la VM."""

    process_query_limited_information = 0x1000
    error_access_denied = 5

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() == error_access_denied
    kernel32.CloseHandle(handle)
    return True


def barrer_huerfanos(
    repo: Path | str,
    *,
    base_dir: Path | None = None,
    topes: TopesWorktrees | None = None,
    excluir_run: str | None = None,
) -> list[str]:
    """Borra las corridas abandonadas de ``repo``. Devuelve los run_id barridos.

    Una corrida se considera abandonada si el proceso que la creó ya no existe,
    o si pasó ``max_edad_huerfano_s`` desde que empezó. Pensado para llamarse al
    abrir un proyecto: barato y silencioso.

    Excepción: una corrida marcada ``refs_pendientes`` guarda el trabajo de una
    fusión en conflicto, que una persona todavía tiene que mirar. Esa solo se
    barre por vejez -- que su proceso haya muerto es lo normal (el companion se
    reinicia), no una señal de que el trabajo ya no importe.
    """
    topes = topes or TopesWorktrees()
    binario = shutil.which("git")
    if binario is None:
        return []
    carpeta = Path(repo).expanduser()
    completado = _git(
        binario, carpeta, ["rev-parse", "--show-toplevel"], timeout=topes.timeout_git_s, check=False
    )
    if completado.returncode != 0 or not _texto(completado):
        return []
    raiz = Path(_texto(completado)).resolve()
    dir_repo = _resolver_base_dir(base_dir) / _slug(str(raiz))
    if not dir_repo.is_dir():
        return []

    barridos: list[str] = []
    ahora = time.time()
    for corrida in sorted(dir_repo.iterdir()):
        if not corrida.is_dir() or corrida.name == excluir_run:
            continue
        pid = 0
        creado_en = 0.0
        refs_pendientes = False
        try:
            datos = json.loads((corrida / _ARCHIVO_CORRIDA).read_text(encoding="utf-8"))
            pid = int(datos.get("pid", 0))
            creado_en = float(datos.get("creado_en", 0.0))
            refs_pendientes = bool(datos.get("refs_pendientes", False))
        except (OSError, ValueError, TypeError):
            # Sin metadatos legibles no se puede saber de quién es. Se decide
            # por la fecha de la carpeta, que es lo único confiable que queda.
            try:
                creado_en = corrida.stat().st_mtime
            except OSError:  # pragma: no cover - carrera con otro barrido
                continue
        vieja = (ahora - creado_en) > topes.max_edad_huerfano_s
        # Sin pid no se decide por pid: se decide por la fecha, como dice el
        # comentario de arriba. Preguntarle a ``_pid_vivo(0)`` -- que responde
        # "muerto" -- daría por abandonada una corrida recién creada cuyo
        # archivo de metadatos todavía no se escribió o quedó a medias, y el
        # barrido le arrancaría los worktrees a sub-agentes que están
        # escribiendo en ellos ahora mismo. Con dos ventanas de Edecán sobre el
        # mismo repo eso no es hipotético.
        parece_viva = refs_pendientes or pid <= 0 or _pid_vivo(pid)
        if not vieja and parece_viva:
            continue
        for hijo in sorted(corrida.iterdir()):
            if hijo.is_dir():
                _git(
                    binario,
                    raiz,
                    ["worktree", "remove", "--force", str(hijo)],
                    timeout=topes.timeout_git_s,
                    check=False,
                )
        shutil.rmtree(corrida, ignore_errors=True)
        refs = _git(
            binario,
            raiz,
            ["for-each-ref", "--format=%(refname)", f"{_ESPACIO_REF}/{corrida.name}"],
            timeout=topes.timeout_git_s,
            check=False,
        )
        for ref in _texto(refs).splitlines():
            if ref.strip():
                _git(
                    binario,
                    raiz,
                    ["update-ref", "-d", ref.strip()],
                    timeout=topes.timeout_git_s,
                    check=False,
                )
        barridos.append(corrida.name)
    if barridos:
        _podar_solo_lo_nuestro(binario, raiz, dir_repo, timeout=topes.timeout_git_s)
    return barridos


# ---------------------------------------------------------------------------
# Puente con ide_equipo: el envoltorio que convierte un runner normal en uno
# que corre aislado.
# ---------------------------------------------------------------------------

RunnerEnWorktree = Callable[[Subtarea, ControlEquipo, Worktree], Awaitable[str]]
"""Como ``ide_equipo.SubtareaRunner`` pero recibiendo, además, el worktree
donde esa sub-tarea debe trabajar. Quien lo implemente tiene que apuntar las
herramientas de archivos del sub-agente a ``worktree.ruta`` -- si sigue
escribiendo en el repo base, el aislamiento no existe."""


def envolver_runner(gestor: GestorWorktrees, runner: RunnerEnWorktree) -> SubtareaRunner:
    """Adapta un runner para que cada sub-tarea corra en su propio worktree.

    El resultado encaja tal cual donde ``ide_equipo.EquipoDeAgentes`` espera un
    ``SubtareaRunner``, así que activar el aislamiento no obliga a tocarlo.

    Un gestor por lote paralelo. Encaja *de tipos* también en
    ``ide_reparto.PlanificadorReparto``, pero un solo gestor para todas sus
    oleadas está mal: cada oleada arrancaría del mismo snapshot y la oleada 2
    -- que existe justamente porque toca los mismos archivos que la 1 --
    trabajaría sobre la versión previa y chocaría al fusionar, dejando la
    corrida entera en cero. Ver la nota "Una corrida = un solo lote" arriba.

    Una sub-tarea que falla NO aporta código: su trabajo a medias se descarta
    con el worktree. Integrar el resultado parcial de un agente que reventó es
    meter código que nadie terminó de escribir en el repo de alguien.
    """

    async def runner_aislado(sub: Subtarea, control: ControlEquipo) -> str:
        # Crear bloquea ~0,25 s de I/O; fuera del event loop para no frenar a
        # los sub-agentes que ya están corriendo.
        worktree = await asyncio.to_thread(gestor.crear, sub.id)
        try:
            salida = await runner(sub, control, worktree)
            await asyncio.to_thread(gestor.cosechar, worktree)
            return salida
        finally:
            # Deliberadamente SÍNCRONO. Si esto fuera ``await
            # asyncio.to_thread(...)`` y la tarea estuviera siendo cancelada,
            # el await levantaría ``CancelledError`` de inmediato y la
            # destrucción quedaría a la deriva: worktree huérfano justo en el
            # caso que más importa, que es cuando algo salió mal.
            gestor.destruir(worktree)

    return runner_aislado
