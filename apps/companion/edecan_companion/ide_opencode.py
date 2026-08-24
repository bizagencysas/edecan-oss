"""Adaptador Python contra el servidor HTTP de ``opencode`` (motor de agentes).

# POR QUÉ existe este archivo

El dueño decidió meter opencode (github.com/anomalyco/opencode, MIT, 191k
estrellas) como MOTOR del IDE de Edecán. La interfaz que ya tiene el dueño
NO se toca -- se sustituye el motor de agentes, no la pantalla. Este módulo
es el cimiento de esa sustitución: el adaptador Python que arranca
``opencode serve``, habla su API HTTP y expone eventos en vivo. Todo lo
demás (el cableado a ``ide_sessions.py``/``ide_workers_agent.py``, la UI) lo
conecta el humano después -- ver la nota del encargo.

La razón concreta, verificada a mano antes de escribir una sola línea de
este archivo: se creó una sesión real, se le pidió reescribir un README, y
el archivo CAMBIÓ EN DISCO (hash distinto) en menos de tres segundos. Eso es
justo lo que el motor de agentes viejo de Edecán NO hace -- dice "listo,
escrito" y el archivo se queda igual. Por eso la regla 6 de este archivo es
tan tajante: cualquier error que devuelva opencode se propaga con su
mensaje real, nunca se traga. Tragarse un error y fingir éxito es
exactamente el bug que esta migración existe para matar.

# Cómo se sacaron las formas de la API

Ninguna forma de request/response de este archivo se inventó: se arrancó un
``opencode serve`` real en esta máquina y se leyó ``GET /doc`` (OpenAPI
3.1, ~480 KB) para cada endpoint que se usa aquí. Dos hallazgos que vale la
pena dejar escritos porque no son obvios desde afuera:

1. **Los endpoints reales viven bajo el prefijo ``/api/`` -- las rutas
   ``/session/...`` sin ese prefijo también existen en el ``/doc`` pero son
   la superficie que usa el propio TUI de opencode, con otra forma de
   sesión (``SessionInfo`` viejo, no ``SessionV2Info``) y sin la mitad de
   estos endpoints (no hay ``/session/{id}/prompt`` ni ``/session/{id}/event``
   fuera de ``/api``). Este adaptador habla EXCLUSIVAMENTE con el prefijo
   ``/api/`` -- es la superficie v2, la documentada como "la API que expone
   opencode" en el encargo.
2. **El proveedor Workers AI se registra leyendo ``opencode.json`` del
   directorio de trabajo con el que arranca el proceso ``opencode serve``**
   -- no hay una bandera ``--cwd`` (se comprobó con ``--help``): el
   directorio de trabajo real del proceso ES la carpeta desde la que se
   lanza. Por eso ``ServidorOpencode.iniciar`` recibe un ``directorio_trabajo``
   y arranca el subproceso con ``cwd=`` ese directorio -- si quien llama
   quiere el proveedor ``workersai`` disponible, el ``opencode.json`` con
   sus credenciales tiene que vivir en la raíz de ESE directorio. Este
   módulo no escribe ese archivo por cuenta propia: la configuración del
   proveedor es responsabilidad de quien arma el workspace, no de este
   cimiento (ver ``test_ide_opencode.py`` para un ejemplo end-to-end).

# Puerto: nunca uno fijo

``opencode serve --port 0`` (el valor por defecto del propio binario, según
``--help``) le pide al sistema operativo un puerto libre de verdad -- ese es
el único punto en el que se decide el puerto, así se evita la carrera
"pido un puerto libre en Python, lo cierro, se lo paso a otro proceso" que
puede perder la carrera contra CUALQUIER otro proceso de la máquina del
dueño. El puerto real se lee de la primera línea de log que imprime el
propio binario (``opencode server listening on http://127.0.0.1:PUERTO``),
no se adivina.

# Qué eventos llegan tipados y cuáles quedan como ``dict``

``GET /api/session/{id}/event`` transmite objetos ``SessionDurableEvent``:
una unión de **27** variantes (``session.next.tool.called``,
``session.next.text.started``, ... -- la lista completa está en
``/doc#/components/schemas/SessionDurableEvent``). Modelar cada variante acá
infla este cimiento sin necesidad real: los campos comunes (``id``, ``type``,
``data``, ``durable``, ``location``) SÍ llegan tipados en ``EventoSesion``;
``data`` -- que es donde cada variante difiere -- se deja como ``dict``
porque quien consuma el iterador ya sabe qué campos esperar mirando
``EventoSesion.type`` y, si hace falta la forma exacta, ``/doc`` bajo el
nombre ``SessionNext<Variante>``. Mismo criterio para ``MensajeSesion``
(8 variantes bajo ``SessionMessage``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import signal
import subprocess
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict

from .ide_opencode_binario import (
    BinarioOpencodeNoEncontrado as _BinarioNoEncontradoResolucion,
)
from .ide_opencode_binario import _argv_para_ejecutar, resolver_binario_opencode
from .pty_compat import _comando_taskkill

# --------------------------------------------------------------------------- #
# Errores -- honestos a propósito (regla 6 del encargo: nada se traga)
# --------------------------------------------------------------------------- #


class ErrorOpencode(RuntimeError):
    """Un fallo REAL de opencode (HTTP, transporte, o parseo de su respuesta).

    Nunca se lanza con un mensaje inventado: ``mensaje`` siempre incluye lo
    que opencode dijo (su ``message``/``_tag`` si respondió JSON, o el
    cuerpo crudo si no). Es la contraparte directa de "dice que escribió y
    no escribió" -- si algo falla, quien llame a este adaptador se entera
    con las palabras reales del servidor, no con un "no se pudo" genérico.
    """

    def __init__(
        self,
        mensaje: str,
        *,
        tag: str | None = None,
        status: int | None = None,
        cuerpo: Any = None,
    ) -> None:
        super().__init__(mensaje)
        self.tag = tag
        self.status = status
        self.cuerpo = cuerpo


class BinarioOpencodeNoEncontrado(RuntimeError):
    """No se encontró un ``opencode`` ejecutable en ninguna ruta esperada."""


class ServidorOpencodeNoArrancoError(RuntimeError):
    """El subproceso ``opencode serve`` no llegó a quedar listo a tiempo."""


#: Ver el docstring de ``ServidorOpencode._solicitar`` -- reintentos SOLO
#: para ``ConnectError``/``ConnectTimeout`` (el TCP nunca se estableció,
#: reintentar es siempre seguro), nunca para un fallo a mitad de respuesta.
_MAX_REINTENTOS_CONEXION = 10
_ESPERA_ENTRE_REINTENTOS_CONEXION = 0.3


# --------------------------------------------------------------------------- #
# Modelos Pydantic v2 -- formas leídas de /doc, ver docstring del módulo
# --------------------------------------------------------------------------- #


class ModeloRef(BaseModel):
    """``#/components/schemas/ModelRef`` -- qué proveedor y qué modelo."""

    model_config = ConfigDict(extra="forbid")

    id: str
    providerID: str
    variant: str | None = None


class UbicacionRef(BaseModel):
    """``#/components/schemas/LocationRef`` -- dónde vive una sesión."""

    model_config = ConfigDict(extra="forbid")

    directory: str
    workspaceID: str | None = None


class InfoSesion(BaseModel):
    """``#/components/schemas/SessionV2Info`` -- lo que devuelve crear/leer/listar."""

    model_config = ConfigDict(extra="allow")

    id: str
    projectID: str
    title: str
    location: UbicacionRef
    cost: float
    tokens: dict[str, Any]
    time: dict[str, Any]
    agent: str | None = None
    model: ModeloRef | None = None
    parentID: str | None = None
    subpath: str | None = None


class EntradaAdmitida(BaseModel):
    """``#/components/schemas/SessionInputAdmitted`` -- respuesta de ``/prompt``.

    OJO: que esto vuelva no significa que el modelo ya contestó ni que ya
    tocó ningún archivo -- solo que opencode ADMITIÓ el mensaje y programó
    la vuelta del agente. Lo que de verdad pasó se sigue por
    :meth:`ServidorOpencode.eventos`.
    """

    model_config = ConfigDict(extra="allow")

    admittedSeq: int
    id: str
    sessionID: str
    prompt: dict[str, Any]
    delivery: Literal["steer", "queue"]
    timeCreated: float
    promotedSeq: int | None = None


class EventoSesion(BaseModel):
    """Envoltura tipada de un evento SSE de ``SessionDurableEvent``.

    Ver "Qué eventos llegan tipados" en el docstring del módulo para el
    porqué de dejar ``data`` como ``dict``.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    type: str
    data: dict[str, Any]
    durable: dict[str, Any] | None = None
    location: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class MensajeSesion(BaseModel):
    """Envoltura tipada de un elemento de ``SessionMessage`` (historial/contexto)."""

    model_config = ConfigDict(extra="allow")

    id: str
    type: str


@dataclass(slots=True, frozen=True)
class PaginaSesiones:
    items: list[InfoSesion]
    cursor_siguiente: str | None
    cursor_previo: str | None


@dataclass(slots=True, frozen=True)
class PaginaMensajes:
    items: list[MensajeSesion]
    cursor_siguiente: str | None
    cursor_previo: str | None


# --------------------------------------------------------------------------- #
# Ubicar el binario
# --------------------------------------------------------------------------- #

_PATRON_LISTENING = re.compile(r"listening on https?://[^:\s]+:(?P<puerto>\d+)")


def _ubicar_binario(ruta_explicita: str | Path | None = None) -> Path:
    """Encuentra el ejecutable ``opencode``. Error claro si no está -- regla 1.

    CORRECCIÓN (encargo del motor, punto 3): antes, sin ``ruta_explicita``,
    esto solo miraba ``~/.opencode/bin/opencode`` (hardcodeado, la ruta de
    esta Mac de desarrollo) y ``PATH`` -- dentro de la app empaquetada de
    Tauri eso falla siempre, porque ni ``sys._MEIPASS`` (bundle) ni
    ``EDECAN_OPENCODE_BIN`` (variable del sidecar) se consultaban nunca.
    Ahora la búsqueda automática delega por completo en
    ``ide_opencode_binario.resolver_binario_opencode()`` (bundle ->
    ``EDECAN_OPENCODE_BIN`` -> ``PATH``, ver el docstring de ese módulo para
    el porqué de cada escalón) -- una sola fuente de verdad para "dónde vive
    opencode", en vez de dos búsquedas que se puedan desincronizar.

    ``ruta_explicita`` (quien llama pasa ``ruta_binario=`` a mano, p. ej. un
    test) NO pasa por ese resolver: es una orden directa, se valida tal cual
    y nada más.

    El error de ``ide_opencode_binario`` (su propia clase
    ``BinarioOpencodeNoEncontrado``, sin relación de herencia con la de ESTE
    módulo pese al mismo nombre) se re-envuelve en la excepción de este
    archivo -- para no romper a quien ya captura
    ``ide_opencode.BinarioOpencodeNoEncontrado`` específicamente (ver
    ``test_ide_opencode.py::test_binario_inexistente_da_error_claro_no_cuelga``,
    que sigue pasando sin tocarse porque ese test usa ``ruta_explicita``, la
    rama que ni siquiera toca este resolver).
    """

    if ruta_explicita is not None:
        ruta = Path(ruta_explicita)
        if ruta.is_file() and os.access(ruta, os.X_OK):
            return ruta
        raise BinarioOpencodeNoEncontrado(
            f"La ruta de opencode indicada no es un ejecutable válido: {ruta}"
        )
    try:
        resuelto = resolver_binario_opencode()
    except _BinarioNoEncontradoResolucion as exc:
        raise BinarioOpencodeNoEncontrado(str(exc)) from exc
    return resuelto.ruta


def _argv_y_flags_lanzamiento(binario: Path) -> tuple[list[str], dict[str, Any]]:
    """Arma el argv y los ``kwargs`` de plataforma para lanzar
    ``opencode serve --port 0`` -- extraído de :meth:`ServidorOpencode.iniciar`
    a una función pura a propósito, por dos razones:

    1. Un solo lugar que sepa "cómo se lanza opencode en esta plataforma",
       en vez de tenerlo disperso inline dentro de un ``classmethod`` largo.
    2. Se puede probar aislada con ``monkeypatch.setattr(os, "name", "nt")``
       SIN arrastrar el resto de ``iniciar()`` -- que sí hace
       ``Path(...)``/``.resolve()`` de verdad (``_ubicar_binario``,
       ``directorio.resolve()``). Mezclar ambas cosas bajo un ``os.name``
       fingido revienta: ``pathlib.Path`` decide su propia subclase
       (``PosixPath``/``WindowsPath``) mirando ``os.name`` en CADA
       ``Path(...)`` nuevo, así que una ruta POSIX real construida mientras
       se finge Windows deja de saber leer el disco real de este Mac. Esta
       función no reconstruye ningún ``Path`` -- solo lee ``binario.suffix``
       (ver ``_argv_para_ejecutar``) y arma listas/dicts -- por eso es segura
       de probar bajo ``os.name`` fingido.

    ``_argv_para_ejecutar`` (ver su docstring en ``ide_opencode_binario.py``)
    es lo que evita el bug medido en ``packages/mcp/edecan_mcp/transport.py``:
    si opencode se resolvió como un shim ``.cmd``/``.bat`` (instalación
    estándar por ``npm install -g opencode-ai`` en Windows), lanzarlo tal
    cual con ``create_subprocess_exec`` falla con
    ``FileNotFoundError [WinError 2]`` -- ``CreateProcess`` no interpreta un
    guion por lotes. En POSIX, o si ``binario`` ya es un ejecutable nativo
    (``.exe`` incluido), el argv sale sin envolver.

    ``start_new_session``: en POSIX pone al subproceso en su propio grupo
    -- lo que permite matar el árbol completo con ``os.killpg`` en
    :meth:`ServidorOpencode.detener`. En Windows ese parámetro no existe
    (``subprocess``/``asyncio`` lo ignoran ahí de todas formas), así que
    siempre va ``False`` -- el árbol se mata por otra vía en Windows, ver
    el docstring de :meth:`ServidorOpencode.detener`.

    ``creationflags``: ``CREATE_NO_WINDOW`` evita que cada arranque de
    ``opencode serve`` (o del ``cmd.exe`` que lo envuelve si el argv quedó
    reescrito arriba) le parpadee una consola negra al dueño -- mismo
    motivo que ya evita ``pty_compat._WindowsPTY._taskkill`` en cada
    llamada a ``taskkill``. ``getattr`` con default ``0`` porque la
    constante no existe en POSIX y ``creationflags`` distinto de cero ahí
    sí revienta (comprobado: ``creationflags=0`` es un no-op seguro en
    cualquier plataforma, un valor no-cero no lo es fuera de Windows).
    """

    argv = _argv_para_ejecutar(binario, "serve", "--port", "0")
    flags: dict[str, Any] = {
        "start_new_session": os.name != "nt",
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }
    return argv, flags


def _error_desde_cuerpo(status: int, cuerpo_bytes: bytes, metodo: str, ruta: str) -> ErrorOpencode:
    """Arma un ``ErrorOpencode`` con el mensaje REAL de opencode.

    opencode responde errores como ``{"_tag": "...", "message": "..."}``
    (``InvalidRequestError``, ``SessionNotFoundError``, ``ConflictError``,
    etc. -- ver ``/doc#/components/schemas``). Si el cuerpo no es ese JSON
    (p. ej. un 502 de un proxy, o el proceso murió a medio camino), se cae
    al texto crudo -- nunca a un mensaje inventado.
    """

    mensaje = f"opencode devolvió {status} en {metodo} {ruta}"
    tag: str | None = None
    cuerpo: Any = None
    texto = cuerpo_bytes.decode("utf-8", errors="replace")
    try:
        cuerpo = json.loads(texto) if texto else None
    except json.JSONDecodeError:
        cuerpo = texto
    if isinstance(cuerpo, dict):
        tag = cuerpo.get("_tag")
        mensaje_real = cuerpo.get("message")
        if mensaje_real:
            mensaje = f"{mensaje} ({tag}): {mensaje_real}" if tag else f"{mensaje}: {mensaje_real}"
    elif isinstance(cuerpo, str) and cuerpo:
        mensaje = f"{mensaje}: {cuerpo[:500]}"
    return ErrorOpencode(mensaje, tag=tag, status=status, cuerpo=cuerpo)


# --------------------------------------------------------------------------- #
# El adaptador
# --------------------------------------------------------------------------- #


class ServidorOpencode:
    """Ciclo de vida de ``opencode serve`` + cliente contra su API ``/api``.

    Se construye con :meth:`iniciar` (nunca ``ServidorOpencode(...)``
    directo -- necesita el subproceso vivo y el puerto real). Es un
    gestor de contexto asíncrono::

        async with await ServidorOpencode.iniciar(directorio) as servidor:
            sesion = await servidor.crear_sesion(proveedor="workersai", modelo="...")
            await servidor.enviar_prompt(sesion.id, "...")
            async for evento in servidor.eventos(sesion.id):
                ...
    """

    def __init__(
        self,
        *,
        proceso: asyncio.subprocess.Process,
        puerto: int,
        cliente: httpx.AsyncClient,
        directorio_trabajo: Path,
        ruta_binario: Path,
        buffer_salida: deque[str],
        tareas_lectura: list[asyncio.Task[None]],
    ) -> None:
        self._proceso = proceso
        self.puerto = puerto
        self._cliente = cliente
        self.directorio_trabajo = directorio_trabajo
        self.ruta_binario = ruta_binario
        self._buffer_salida = buffer_salida
        self._tareas_lectura = tareas_lectura
        self._detenido = False

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.puerto}/api"

    def ultimas_lineas_log(self, n: int = 50) -> list[str]:
        """Últimas líneas de stdout/stderr del subproceso -- para diagnóstico."""

        return list(self._buffer_salida)[-n:]

    # ----------------------------------------------------------------- #
    # Arranque / apagado
    # ----------------------------------------------------------------- #

    @classmethod
    async def iniciar(
        cls,
        directorio_trabajo: str | Path,
        *,
        ruta_binario: str | Path | None = None,
        timeout_arranque: float = 20.0,
        timeout_lectura_http: float = 180.0,
    ) -> ServidorOpencode:
        """Arranca ``opencode serve --port 0`` en ``directorio_trabajo`` y espera
        a que responda de verdad (no solo a que imprima el log de arranque).

        ``timeout_lectura_http`` es el timeout de LECTURA por defecto del
        cliente HTTP para las llamadas normales (crear sesión, mandar
        prompt, etc.) -- generoso a propósito porque un modelo pensando
        puede tardar; ``eventos()`` usa su propia política de timeout, ver
        ese método.
        """

        binario = _ubicar_binario(ruta_binario)
        directorio = Path(directorio_trabajo).resolve()
        if not directorio.is_dir():
            raise ValueError(
                f"El directorio de trabajo no existe o no es una carpeta: {directorio}"
            )

        # Ver el docstring de _argv_y_flags_lanzamiento -- ahí vive el
        # porqué completo (precedente del .cmd, CREATE_NO_WINDOW,
        # start_new_session) y por qué esto es una función aparte en vez de
        # quedar inline acá.
        argv, flags_lanzamiento = _argv_y_flags_lanzamiento(binario)
        proceso = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(directorio),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **flags_lanzamiento,
        )

        buffer_salida: deque[str] = deque(maxlen=300)
        cola_puerto: asyncio.Queue[int] = asyncio.Queue(maxsize=1)
        puerto_anunciado = False

        async def _leer(stream: asyncio.StreamReader | None, prefijo: str) -> None:
            nonlocal puerto_anunciado
            if stream is None:
                return
            while True:
                linea = await stream.readline()
                if not linea:
                    break
                texto = linea.decode("utf-8", errors="replace").rstrip("\n")
                buffer_salida.append(f"[{prefijo}] {texto}")
                if not puerto_anunciado:
                    coincidencia = _PATRON_LISTENING.search(texto)
                    if coincidencia:
                        puerto_anunciado = True
                        await cola_puerto.put(int(coincidencia.group("puerto")))

        tarea_stdout = asyncio.create_task(_leer(proceso.stdout, "stdout"))
        tarea_stderr = asyncio.create_task(_leer(proceso.stderr, "stderr"))
        tareas_lectura = [tarea_stdout, tarea_stderr]

        async def _abortar_arranque(motivo: str) -> None:
            if proceso.returncode is None:
                proceso.kill()
                with contextlib.suppress(TimeoutError, ProcessLookupError):
                    await asyncio.wait_for(proceso.wait(), timeout=5.0)
            for tarea in tareas_lectura:
                tarea.cancel()
            for tarea in tareas_lectura:
                with contextlib.suppress(asyncio.CancelledError):
                    await tarea
            salida = "\n".join(buffer_salida) or "(sin salida)"
            raise ServidorOpencodeNoArrancoError(f"{motivo}\nSalida del proceso:\n{salida}")

        try:
            puerto = await asyncio.wait_for(cola_puerto.get(), timeout=timeout_arranque)
        except TimeoutError:
            await _abortar_arranque(
                f"opencode no anunció su puerto en {timeout_arranque}s (binario: {binario})."
            )
            raise  # inalcanzable: _abortar_arranque siempre lanza

        if proceso.returncode is not None:
            await _abortar_arranque(f"opencode terminó al arrancar (código {proceso.returncode}).")
            raise  # inalcanzable

        cliente = httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{puerto}/api",
            limits=httpx.Limits(max_keepalive_connections=0, max_connections=20),
            timeout=httpx.Timeout(connect=10.0, read=timeout_lectura_http, write=30.0, pool=10.0),
        )

        # El log de "listening" sale en cuanto el socket empieza a escuchar,
        # que no es lo mismo que "ya puede atender una petición real" --
        # se comprobó que hay una ventana corta entre ambos momentos. Por
        # eso se hace polling a /health hasta que responda 200 de verdad,
        # distinguiendo "tarda un poco en levantar" (se reintenta) de
        # "el proceso murió" (returncode deja de ser None -> se aborta ya).
        limite = time.monotonic() + timeout_arranque
        listo = False
        while time.monotonic() < limite:
            if proceso.returncode is not None:
                break
            try:
                resp = await cliente.get("/health", timeout=httpx.Timeout(2.0))
                if resp.status_code == 200:
                    await asyncio.sleep(0.3)
                    listo = True
                    break
            except httpx.TransportError:
                pass
            await asyncio.sleep(0.1)

        if not listo:
            await cliente.aclose()
            await _abortar_arranque(
                f"opencode abrió el puerto {puerto} pero /api/health nunca respondió 200 "
                f"en {timeout_arranque}s."
            )
            raise  # inalcanzable

        return cls(
            proceso=proceso,
            puerto=puerto,
            cliente=cliente,
            directorio_trabajo=directorio,
            ruta_binario=binario,
            buffer_salida=buffer_salida,
            tareas_lectura=tareas_lectura,
        )

    async def detener(self, *, timeout: float = 5.0) -> None:
        """Para el subproceso limpio: señal cooperativa primero, martillo si
        no obedece -- SIGTERM/SIGKILL en POSIX, ``taskkill /T`` en Windows.

        # Por qué Windows NO puede usar ``self._proceso.terminate()``/``.kill()``

        POSIX tiene ``os.killpg`` sobre el grupo de proceso completo (el
        subproceso arrancó con ``start_new_session=True``, ver
        :meth:`iniciar`). Windows no tiene ni señales POSIX ni grupos de
        proceso equivalentes: ``Process.terminate()``/``.kill()`` ahí
        llaman a ``TerminateProcess`` sobre el ÚNICO PID que asyncio
        rastrea -- y ``TerminateProcess`` no conoce hijos.

        Esto no es una preocupación teórica: se midió en vivo contra la VM
        de validación (Windows Server 2022) que, cuando opencode se
        resuelve como el shim ``opencode.cmd`` que deja
        ``npm install -g opencode-ai`` (la vía de instalación estándar en
        Windows), el PID que asyncio rastrea es el de ``cmd.exe`` -- pero
        el ``opencode.exe`` real que hace el trabajo es su NIETO. Matar
        solo la raíz deja ese nieto huérfano y vivo, con el puerto TCP
        todavía ocupado: confirmado tres veces en vivo (27 procesos
        ``opencode.exe`` contados ANTES de ``detener()`` y los mismos 27
        SEGUÍAN vivos 2 segundos DESPUÉS). Es exactamente el tipo de
        servidor huérfano que ya le rompió la app al dueño dos veces en
        macOS -- no puede repetirse en Windows.

        # Por qué ``taskkill /T`` y no ``CREATE_NEW_PROCESS_GROUP`` + ``CTRL_BREAK_EVENT``

        La alternativa "elegante" de Windows para parar un árbol de
        procesos es crear el grupo con ``CREATE_NEW_PROCESS_GROUP`` y
        mandarle ``CTRL_BREAK_EVENT`` (``os.kill(pid,
        signal.CTRL_BREAK_EVENT)``). Se descartó a propósito:
        ``GenerateConsoleCtrlEvent`` solo entrega la señal a los procesos
        que están en el MISMO grupo de consola que el proceso al que se le
        creó el grupo -- y un nieto lanzado por un wrapper de Node/
        ``cmd.exe`` (que es justo el escenario medido arriba) puede acabar
        en un grupo de consola distinto, con lo que la señal nunca llegaría
        al ``opencode.exe`` real. ``taskkill /T`` no depende de grupos de
        consola en absoluto: recorre el árbol real de procesos
        (padre -> hijo -> nieto -> ...) tal como Windows lo registra, sea
        cual sea cómo se creó cada eslabón -- es la opción pragmática que
        el dueño pidió ("usa lo que sea para que funcione") y la única que
        la medición en vivo confirmó que limpia el árbol completo.

        Se reutiliza ``pty_compat._comando_taskkill`` -- el mismo primitivo
        de dos fases (``/T`` cooperativo, luego ``/T /F`` como martillo) que
        ya usa ``ide_workers_agent.py::_terminate_process`` para el mismo
        problema -- en vez de reinventar el comando: un solo lugar que sepa
        armar el argv de ``taskkill`` de este repo.
        """

        if self._detenido:
            return
        self._detenido = True
        await self._cliente.aclose()
        if self._proceso.returncode is None:
            try:
                if os.name != "nt":
                    os.killpg(self._proceso.pid, signal.SIGTERM)
                else:
                    await self._taskkill(force=False)
                await asyncio.wait_for(self._proceso.wait(), timeout=timeout)
            except (TimeoutError, ProcessLookupError):
                with contextlib.suppress(TimeoutError, ProcessLookupError):
                    if os.name != "nt":
                        os.killpg(self._proceso.pid, signal.SIGKILL)
                    else:
                        await self._taskkill(force=True)
                    await asyncio.wait_for(self._proceso.wait(), timeout=2.0)
        for tarea in self._tareas_lectura:
            tarea.cancel()
        for tarea in self._tareas_lectura:
            with contextlib.suppress(asyncio.CancelledError):
                await tarea

    async def _taskkill(self, *, force: bool) -> None:
        """Corre ``taskkill`` de forma asíncrona sobre el árbol de
        :attr:`_proceso` -- ver el docstring de :meth:`detener` para el
        porqué completo.

        Deliberadamente ``asyncio.create_subprocess_exec`` y no
        ``subprocess.run`` bloqueante: este método corre dentro de
        ``detener()``, que es ``async`` -- bloquear el hilo aquí congelaría
        el event loop entero mientras Windows mata el árbol.

        No se comprueba el ``returncode`` de ``taskkill`` (equivalente a
        ``check=False``): si el proceso ya murió por su cuenta entre que se
        decidió llamar a esto y que ``taskkill`` corrió de verdad,
        ``taskkill`` simplemente devuelve un código de error -- no es un
        fallo real que ``detener()`` deba propagar, lo único que importa es
        que el proceso YA no esté vivo, y eso lo confirma
        ``self._proceso.wait()`` justo después de esta llamada.
        """

        proceso_taskkill = await asyncio.create_subprocess_exec(
            *_comando_taskkill(self._proceso.pid, force=force),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            # Mismo motivo que en iniciar(): sin esto, cada llamada a
            # taskkill parpadea su propia consola negra.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        await proceso_taskkill.wait()

    async def __aenter__(self) -> ServidorOpencode:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.detener()

    # ----------------------------------------------------------------- #
    # HTTP interno
    # ----------------------------------------------------------------- #

    async def _solicitar(
        self,
        metodo: str,
        ruta: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> httpx.Response:
        """HALLAZGO REAL de un verificador (integración real, no supuesto):
        contra opencode corriendo de verdad, un POST puede fallar con
        ``httpx.ConnectError`` ("All connection attempts failed") en un
        instante puntual -- el servidor estaba momentáneamente sin aceptar
        conexiones nuevas (arrancando/bajo carga), no caído de verdad (el
        siguiente intento, milisegundos después, ya conecta). Se reintenta
        ``_MAX_REINTENTOS_CONEXION`` veces SOLO ``ConnectError``/
        ``ConnectTimeout`` -- el TCP nunca llegó a establecerse, así que
        reintentar es SIEMPRE seguro (nada se procesó del lado del
        servidor). Deliberadamente NO se reintenta ningún otro
        ``httpx.TransportError`` (``ReadError``, conexión cortada A MITAD de
        respuesta, etc.): ahí la petición sí pudo haber llegado y
        procesarse -- reintentar un ``POST /session/{id}/prompt`` en ese
        caso arriesgaría mandar el mismo prompt dos veces, exactamente el
        tipo de efecto secundario duplicado que este adaptador no se puede
        permitir inventar."""

        intentos_conexion = 0
        while True:
            if self._proceso.returncode is not None:
                ultimas = list(self._buffer_salida)[-100:]
                logs = "\n".join(ultimas) if ultimas else "(sin logs de opencode)"
                raise ErrorOpencode(
                    f"El proceso de opencode terminó mientras se ejecutaba {metodo} {ruta} "
                    f"con código {self._proceso.returncode}.\n"
                    f"Últimas líneas de opencode:\n{logs}"
                )

            try:
                resp = await self._cliente.request(
                    metodo, ruta, json=json_body, params=params, timeout=timeout
                )
            except httpx.TimeoutException as exc:
                raise ErrorOpencode(
                    f"opencode no respondió a tiempo en {metodo} {ruta}: {exc}"
                ) from exc
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                intentos_conexion += 1
                if intentos_conexion <= _MAX_REINTENTOS_CONEXION and (
                    metodo == "GET" or ruta.startswith("/session")
                ):
                    await asyncio.sleep(_ESPERA_ENTRE_REINTENTOS_CONEXION)
                    continue
                raise ErrorOpencode(
                    f"No se pudo conectar con opencode en {metodo} {ruta}: {exc}"
                ) from exc
            except httpx.TransportError as exc:
                raise ErrorOpencode(f"Fallo de transporte en {metodo} {ruta}: {exc}") from exc

            if resp.status_code >= 400:
                raise _error_desde_cuerpo(resp.status_code, resp.content, metodo, ruta)
            return resp

    # ----------------------------------------------------------------- #
    # Sesiones
    # ----------------------------------------------------------------- #

    async def crear_sesion(
        self,
        *,
        directorio: str | Path | None = None,
        id_sesion: str | None = None,
        agente: str | None = None,
        proveedor: str | None = None,
        modelo: str | None = None,
        variante: str | None = None,
    ) -> InfoSesion:
        """Crea una sesión. ``proveedor``/``modelo`` van juntos (``ModelRef``
        exige ``providerID`` + ``id``); si se omiten, opencode usa su
        modelo por defecto configurado."""

        if (proveedor is None) != (modelo is None):
            raise ValueError(
                "proveedor y modelo van juntos: opencode exige providerID + id "
                "(ver ModelRef en /doc) -- pasa los dos o ninguno."
            )
        directorio_efectivo = (
            str(directorio) if directorio is not None else str(self.directorio_trabajo)
        )
        cuerpo: dict[str, Any] = {"location": {"directory": directorio_efectivo}}
        if id_sesion is not None:
            cuerpo["id"] = id_sesion
        if agente is not None:
            cuerpo["agent"] = agente
        if proveedor is not None and modelo is not None:
            ref: dict[str, Any] = {"providerID": proveedor, "id": modelo}
            if variante is not None:
                ref["variant"] = variante
            cuerpo["model"] = ref
        resp = await self._solicitar("POST", "/session", json_body=cuerpo)
        return InfoSesion.model_validate(resp.json()["data"])

    async def obtener_sesion(self, id_sesion: str) -> InfoSesion:
        resp = await self._solicitar("GET", f"/session/{id_sesion}")
        return InfoSesion.model_validate(resp.json()["data"])

    async def listar_sesiones(
        self,
        *,
        directorio: str | Path | None = None,
        limite: int | None = None,
        orden: Literal["asc", "desc"] | None = None,
        cursor: str | None = None,
        busqueda: str | None = None,
    ) -> PaginaSesiones:
        params: dict[str, Any] = {}
        if directorio is not None:
            params["directory"] = str(directorio)
        if limite is not None:
            params["limit"] = limite
        if orden is not None:
            params["order"] = orden
        if cursor is not None:
            params["cursor"] = cursor
        if busqueda is not None:
            params["search"] = busqueda
        resp = await self._solicitar("GET", "/session", params=params)
        cuerpo = resp.json()
        items = [InfoSesion.model_validate(x) for x in cuerpo["data"]]
        cur = cuerpo.get("cursor") or {}
        return PaginaSesiones(
            items=items, cursor_siguiente=cur.get("next"), cursor_previo=cur.get("previous")
        )

    async def historial_sesion(
        self,
        id_sesion: str,
        *,
        limite: int | None = None,
        orden: Literal["asc", "desc"] | None = None,
        cursor: str | None = None,
    ) -> PaginaMensajes:
        params: dict[str, Any] = {}
        if limite is not None:
            params["limit"] = limite
        if orden is not None:
            params["order"] = orden
        if cursor is not None:
            params["cursor"] = cursor
        resp = await self._solicitar("GET", f"/session/{id_sesion}/message", params=params)
        cuerpo = resp.json()
        items = [MensajeSesion.model_validate(x) for x in cuerpo["data"]]
        cur = cuerpo.get("cursor") or {}
        return PaginaMensajes(
            items=items, cursor_siguiente=cur.get("next"), cursor_previo=cur.get("previous")
        )

    async def contexto_sesion(self, id_sesion: str) -> list[MensajeSesion]:
        """Mensajes activos de contexto (todo lo posterior a la última compactación)."""

        resp = await self._solicitar("GET", f"/session/{id_sesion}/context")
        return [MensajeSesion.model_validate(x) for x in resp.json()["data"]]

    # ----------------------------------------------------------------- #
    # Prompt
    # ----------------------------------------------------------------- #

    async def enviar_prompt(
        self,
        id_sesion: str,
        texto: str,
        *,
        id_mensaje: str | None = None,
        entrega: Literal["steer", "queue"] | None = None,
        resumir: bool | None = None,
    ) -> EntradaAdmitida:
        """Manda un mensaje. Esto solo ADMITE el mensaje y programa la vuelta
        del agente -- no espera a que el modelo termine (se comprobó: la
        llamada vuelve casi al instante incluso cuando el agente tarda
        segundos en tocar el archivo). Seguir el progreso real es cosa de
        :meth:`eventos`."""

        cuerpo: dict[str, Any] = {"prompt": {"text": texto}}
        if id_mensaje is not None:
            cuerpo["id"] = id_mensaje
        if entrega is not None:
            cuerpo["delivery"] = entrega
        if resumir is not None:
            cuerpo["resume"] = resumir
        resp = await self._solicitar("POST", f"/session/{id_sesion}/prompt", json_body=cuerpo)
        return EntradaAdmitida.model_validate(resp.json()["data"])

    # ----------------------------------------------------------------- #
    # Eventos en vivo (SSE)
    # ----------------------------------------------------------------- #

    async def eventos(
        self,
        id_sesion: str,
        *,
        desde: str | None = None,
        tiempo_maximo_sin_eventos: float | None = None,
    ) -> AsyncIterator[EventoSesion]:
        """Itera los eventos SSE de una sesión, ya tipados en ``EventoSesion``.

        ``desde``: id de evento (``evt_...``) a partir del cual reanudar --
        es el parámetro ``after`` de la API. Guardar el ``id`` del último
        evento recibido y volver a llamar con ``desde=ese_id`` tras una
        caída es la manera de "no perder eventos" que da opencode; este
        método NO reconecta solo -- reintentar en silencio escondería la
        caída del servidor, que es justo el tipo de bug que esta migración
        busca eliminar. Quien consuma el iterador decide si reconecta y con
        qué backoff.

        ``tiempo_maximo_sin_eventos``: si se pasa y no llega NINGÚN evento
        en ese lapso, se lanza ``TimeoutError`` -- la manera de distinguir
        "el modelo está pensando" (en la prueba de integración llegaron
        eventos de razonamiento cada 1-2s) de "el proceso murió a media
        transmisión" (no llega nada, nunca). Sin este parámetro (por
        defecto) el iterador espera indefinidamente por el próximo evento,
        que sigue siendo seguro: si la conexión TCP de verdad se cae,
        httpx lanza de inmediato -- no se queda colgado para siempre salvo
        en una partición de red silenciosa, el único caso que este
        parámetro cubre.
        """

        params = {"after": desde} if desde else None
        try:
            async with self._cliente.stream(
                "GET",
                f"/session/{id_sesion}/event",
                params=params,
                timeout=httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0),
            ) as resp:
                if resp.status_code >= 400:
                    cuerpo = await resp.aread()
                    raise _error_desde_cuerpo(
                        resp.status_code, cuerpo, "GET", f"/session/{id_sesion}/event"
                    )
                lector = resp.aiter_lines()
                while True:
                    try:
                        if tiempo_maximo_sin_eventos is not None:
                            linea = await asyncio.wait_for(
                                lector.__anext__(), timeout=tiempo_maximo_sin_eventos
                            )
                        else:
                            linea = await lector.__anext__()
                    except StopAsyncIteration:
                        break
                    except TimeoutError as exc:
                        raise TimeoutError(
                            f"Sin eventos de la sesión {id_sesion} en "
                            f"{tiempo_maximo_sin_eventos}s -- puede que opencode se haya "
                            "caído a media transmisión."
                        ) from exc
                    if not linea or linea.startswith(":"):
                        continue  # línea vacía o comentario SSE: separador de evento
                    if not linea.startswith("data:"):
                        continue  # se comprobó en integración: opencode solo manda "data:"
                    crudo = linea[len("data:") :].strip()
                    if not crudo:
                        continue
                    try:
                        payload = json.loads(crudo)
                    except json.JSONDecodeError as exc:
                        raise ErrorOpencode(
                            f"Evento SSE de opencode no es JSON válido: {crudo[:300]!r}"
                        ) from exc
                    yield EventoSesion.model_validate(payload)
        except httpx.TransportError as exc:
            raise ErrorOpencode(
                f"Se perdió la conexión SSE con la sesión {id_sesion}: {exc}"
            ) from exc

    # ----------------------------------------------------------------- #
    # Control: interrumpir, cambiar modelo/agente, contexto, compactar
    # ----------------------------------------------------------------- #

    async def interrumpir(self, id_sesion: str) -> None:
        """Interrumpe la ejecución activa. Sobre una sesión ociosa es un no-op."""

        await self._solicitar("POST", f"/session/{id_sesion}/interrupt")

    async def cambiar_modelo(
        self, id_sesion: str, *, proveedor: str, modelo: str, variante: str | None = None
    ) -> None:
        ref: dict[str, Any] = {"providerID": proveedor, "id": modelo}
        if variante is not None:
            ref["variant"] = variante
        await self._solicitar("POST", f"/session/{id_sesion}/model", json_body={"model": ref})

    async def cambiar_agente(self, id_sesion: str, agente: str) -> None:
        await self._solicitar("POST", f"/session/{id_sesion}/agent", json_body={"agent": agente})

    async def compactar(self, id_sesion: str) -> None:
        await self._solicitar("POST", f"/session/{id_sesion}/compact")
