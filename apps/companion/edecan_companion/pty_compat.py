"""Capa de abstracción de terminal — la única frontera entre ``ide_sessions.py``
y el pseudo-terminal real de cada plataforma.

Contrato fijado en ``docs/edecan-windows.md`` §1.1: seis operaciones
(``abrir``/``escribir``/``leer``/``redimensionar``/``cerrar``/``matar_arbol``) y una
sola excepción (:class:`PTYError`) — el llamador no distingue plataformas ni en el
camino de error. Este módulo trae las DOS ramas:

- POSIX (macOS/Linux): ``pty.openpty()`` + ``Popen`` — la única que se puede
  ejecutar y depurar desde esta máquina, y la que de verdad corre en cada
  ``uv run pytest`` de este repo.
- Windows: ConPTY vía ``pywinpty`` (decisión tomada en el plan §1: se
  descartó ``winpty`` clásico -- proyecto muerto pre-ConPTY -- y un ``ctypes``
  propio sobre ``CreatePseudoConsole``/``ResizePseudoConsole``/
  ``ClosePseudoConsole`` -- indepurable sin un Windows delante). Escrita con
  cada llamada documentada contra la fuente pública de ``pywinpty``
  (github.com/andfoy/pywinpty, módulo ``winpty.ptyprocess``) y la
  documentación oficial de Win32/ConPTY, pero **sin poder ejecutarse ni
  depurarse desde esta máquina** (macOS no tiene ConPTY). Ver
  ``tests/test_pty_compat.py`` para los tests marcados
  ``skipif(sys.platform != "win32")`` que la ejercitan.

Regla de evidencia de este repo: nada de lo que hay aquí para Windows puede
declararse "funciona" hasta que corra en Windows x64 (CI o la PC del dueño).
La rama Windows de este archivo es diseño razonado y citado contra
documentación pública, no un hecho verificado -- ver la lista de supuestos
sin confirmar al final de la clase ``_WindowsPTY``.

Corrección real encontrada al escribir esta capa (no algo copiado sin más del
código actual): el patrón que hoy vive en ``ide_sessions.py``
(``pty.openpty()`` + ``Popen(start_new_session=True)``, sin más) deja al hijo
como líder de sesión pero SIN terminal controladora -- un experimento con
``ps`` lo confirmó (``TTY ??`` / ``TPGID 0``): Ctrl+C solo se veía como el eco
"^C", el kernel nunca generaba ``SIGINT`` hacia el hijo. Esta implementación
agrega el ``ioctl(TIOCSCTTY)`` que falta (ver ``_PosixPTY.abrir``); con él,
``ps`` muestra la terminal real y Ctrl+C sí interrumpe -- se volvió a probar y
quedó confirmado con el mismo experimento.

Nota de integración (fuera del alcance de este módulo): el plan describe
``PTYError`` como subclase de ``IDESessionError`` (definida en ``ide_sessions.py``)
para que ese archivo no tenga que importar dos jerarquías de excepciones. Esta
capa se mantiene deliberadamente independiente de ``ide_sessions`` — es la pieza
de más abajo del árbol de imports y no debe depender de un módulo que la vaya a
importar a ella — así que expone ``PTYError`` como excepción propia; quien migre
``ide_sessions.py`` a usar esta capa (fase F1 del plan) decide si la re-lanza como
``IDESessionError`` o si la deja subir tal cual.
"""

from __future__ import annotations

import codecs
import contextlib
import os
import signal
import struct
import subprocess
import time
from abc import ABC, abstractmethod
from typing import Any

# Las tres bibliotecas de la rama POSIX no existen en Windows, y ``winpty``
# (paquete ``pywinpty`` -- ver ``apps/companion/pyproject.toml``, dependencia
# con marcador ``sys_platform == "win32"``) no existe fuera de Windows: no
# publica wheels para macOS/Linux y no tendría con qué hablar si los
# publicara (ConPTY es una API de Win32). El import queda condicionado
# exactamente como ya lo hacía ``ide_sessions.py`` antes de migrar a esta
# capa, para que el módulo se pueda importar en cualquier plataforma sin que
# la rama ausente rompa nada.
if os.name != "nt":
    import fcntl
    import pty
    import termios

    winpty = None  # type: ignore[assignment]
else:  # pragma: no cover - ejercitado solo en Windows real.
    fcntl = None  # type: ignore[assignment]
    pty = None  # type: ignore[assignment]
    termios = None  # type: ignore[assignment]
    import winpty

# Tamaño por defecto del pseudo-terminal cuando el llamador no pide uno propio.
# Coincide con lo que hoy usa el frontend del IDE como área visible inicial.
DEFAULT_COLS = 120
DEFAULT_ROWS = 32

# Cierre cooperativo: cuánto se espera entre la señal "por favor termina" (SIGTERM
# en POSIX / ``taskkill /T`` sin ``/F`` en Windows) y el martillo. Ver §1.2 del plan.
CLOSE_GRACE_PERIOD_S = 3.0


class PTYError(Exception):
    """Error único de la capa de terminal.

    Cubre tanto fallos al abrir el pseudo-terminal o arrancar el proceso como
    fallos de E/S sobre uno ya abierto. El llamador (``ide_sessions.py``) no
    necesita — ni debe — distinguir POSIX de Windows a partir de esta excepción.
    """


class TerminalPTY(ABC):
    """Una terminal interactiva viva. Una implementación por plataforma.

    Nadie construye una implementación directamente: se obtiene con
    :func:`abrir_pty`, que elige la clase correcta según ``os.name``.
    """

    @abstractmethod
    def escribir(self, data: bytes) -> None:
        """Entrada cruda del usuario, UTF-8 ya codificado a bytes, incluyendo
        secuencias de control (``\\x03``, ``\\x1b[A``...). No traduce nada: el
        emulador del frontend manda tal cual lo que la persona tecleó.
        """

    @abstractmethod
    def leer(self, max_bytes: int = 4096) -> bytes:
        """Bloqueante. Devuelve ``b""`` en EOF (el proceso terminó y el buffer
        del pseudo-terminal ya se drenó). El hilo lector de ``ide_sessions``
        pasa el resultado al mismo decoder incremental UTF-8
        ``errors="replace"`` de siempre — esto no cambia por plataforma.
        """

    @abstractmethod
    def redimensionar(self, cols: int, rows: int) -> None:
        """Avisa al proceso hijo el nuevo tamaño de su terminal (``SIGWINCH``
        en POSIX; ConPTY expone su propio ``set_size`` en Windows)."""

    @abstractmethod
    def cerrar(self) -> None:
        """Cierre cooperativo: pide que el árbol termine solo (SIGTERM /
        ``taskkill /T``), espera :data:`CLOSE_GRACE_PERIOD_S`, y si sigue vivo
        aplica el martillo (SIGKILL / ``taskkill /T /F``). Siempre libera el
        pseudo-terminal al final, incluso si el proceso ya había terminado."""

    @abstractmethod
    def matar_arbol(self) -> None:
        """El martillo directo, sin fase cooperativa. Idempotente: llamarlo
        sobre un árbol ya muerto no lanza."""

    @abstractmethod
    def esperar(self, timeout: float | None = None) -> int | None:
        """Espera a que el proceso YA en camino de terminar reporte su
        código de salida -- sin mandar ninguna señal. La usa el hilo lector
        de ``ide_sessions.py`` justo después de observar EOF en ``leer()``:
        para ese momento el hijo casi siempre ya terminó por su cuenta (o el
        pseudo-terminal se cerró), así que esto es un ``reap`` de zombi, no
        una cancelación. Libera los recursos del pseudo-terminal al volver
        -- reemplaza exactamente al ``process.wait()`` + ``os.close(master_fd)``
        que hacía ``SessionManager._finish`` antes de esta migración.
        """

    @property
    @abstractmethod
    def pid(self) -> int:
        """PID del proceso raíz (el que se lanzó con ``abrir_pty``)."""

    @property
    @abstractmethod
    def codigo_salida(self) -> int | None:
        """``None`` mientras el proceso raíz sigue vivo; su código de salida
        una vez terminó."""


def abrir_pty(
    argv: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    cols: int = DEFAULT_COLS,
    rows: int = DEFAULT_ROWS,
) -> TerminalPTY:
    """Fábrica: arranca ``argv`` conectado a un pseudo-terminal del tamaño dado
    y devuelve la implementación correcta para ``os.name``.

    ``argv`` debe llegar ya validado (ejecutable resuelto y existente) — esta
    capa no repite esa validación, es responsabilidad del llamador (hoy,
    ``IDESessionManager._validate_argv`` en ``ide_sessions.py``).
    """

    if os.name == "nt":
        return _WindowsPTY.abrir(argv, cwd=cwd, env=env, cols=cols, rows=rows)
    return _PosixPTY.abrir(argv, cwd=cwd, env=env, cols=cols, rows=rows)


def _set_winsize(fd: int, cols: int, rows: int) -> None:
    """``TIOCSWINSZ`` empaqueta (rows, cols, xpixel, ypixel) — ``man tty_ioctl``.
    Los píxeles no aplican a una terminal de texto: van en cero, como hace
    cualquier emulador de terminal real.
    """

    packed = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, packed)


def _comando_taskkill(pid: int, *, force: bool) -> list[str]:
    """Arma el argv de ``taskkill`` para matar el árbol de ``pid``.

    Función pura (ninguna syscall): separada de :meth:`_WindowsPTY._taskkill`
    para poder probar la construcción del comando desde cualquier
    plataforma sin tocar ConPTY -- la regla de este repo prohíbe mockear
    ConPTY para "pasar" en macOS, pero construir una lista de argumentos no
    es ConPTY, es Python puro.

    ``/T``: "Ends the specified process and any child processes started by
    it" -- el árbol completo, no solo la raíz (ver
    learn.microsoft.com/windows-server/administration/windows-commands/taskkill).
    ``/F`` cuando ``force`` es verdadero: salta la fase cooperativa e mata
    directo (usado por ``matar_arbol()`` y por el segundo intento de
    ``cerrar()`` tras agotar :data:`CLOSE_GRACE_PERIOD_S`).
    """

    comando = ["taskkill", "/T", "/PID", str(pid)]
    if force:
        comando.insert(1, "/F")
    return comando


class _PosixPTY(TerminalPTY):
    """Implementación POSIX: ``pty.openpty()`` + ``Popen`` en su propio grupo
    de procesos (``start_new_session=True``), que es lo que hace posible
    ``os.killpg`` — la base de ``cerrar()``/``matar_arbol()`` de todo el
    árbol y no solo del proceso raíz.
    """

    def __init__(self, process: subprocess.Popen[Any], master_fd: int) -> None:
        self._process = process
        self._master_fd = master_fd
        self._master_closed = False

    @classmethod
    def abrir(
        cls,
        argv: list[str],
        *,
        cwd: str,
        env: dict[str, str],
        cols: int,
        rows: int,
    ) -> _PosixPTY:
        try:
            master_fd, slave_fd = pty.openpty()
        except OSError as exc:
            raise PTYError(f"No se pudo abrir un pseudo-terminal: {exc}") from exc

        def _volverse_terminal_controladora() -> None:
            # Corre en el hijo, ya bifurcado, DESPUÉS de que ``start_new_session``
            # hizo ``setsid()`` y ANTES del ``exec``. Sin este ``ioctl`` el hijo
            # queda con sesión propia pero SIN terminal controladora -- se
            # comprobó con un experimento real (ver docs/edecan-windows.md, no
            # asumido): ``ps`` mostraba ``TTY ??`` / ``TPGID 0`` y ``\x03``
            # solo se veía como el eco visual "^C", sin que el kernel generara
            # jamás SIGINT hacia el hijo. Con este ioctl, ``ps`` muestra la
            # terminal real y el TPGID del grupo del hijo, y Ctrl+C sí lo
            # interrumpe -- que es exactamente el paso 5 del checklist del
            # dueño (docs/edecan-windows.md §10) y el equivalente POSIX de
            # "ConPTY traduce \\x03 a CTRL_C_EVENT" en Windows (§1.2).
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)

        try:
            _set_winsize(master_fd, cols, rows)
            try:
                process = subprocess.Popen(
                    argv,
                    cwd=cwd,
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    start_new_session=True,
                    close_fds=True,
                    env=env,
                    preexec_fn=_volverse_terminal_controladora,
                )
            finally:
                # El hijo ya tiene su propia copia (heredada al fork); el
                # padre no necesita la suya y debe cerrarla para que el EOF
                # del lado master llegue cuando el hijo (y solo el hijo)
                # cierre la suya.
                os.close(slave_fd)
        except OSError as exc:
            os.close(master_fd)
            raise PTYError(f"No se pudo arrancar {argv[0]!r}: {exc}") from exc
        return cls(process, master_fd)

    def escribir(self, data: bytes) -> None:
        if not isinstance(data, bytes):
            raise TypeError("escribir() espera bytes ya codificados, no str.")
        try:
            os.write(self._master_fd, data)
        except OSError as exc:
            raise PTYError(f"No se pudo escribir en la terminal: {exc}") from exc

    def leer(self, max_bytes: int = 4096) -> bytes:
        try:
            return os.read(self._master_fd, max_bytes)
        except OSError:
            # Master ya cerrado, o el hijo terminó y el kernel liberó el
            # slave: para el llamador esto es EOF, no un error.
            return b""

    def redimensionar(self, cols: int, rows: int) -> None:
        try:
            _set_winsize(self._master_fd, cols, rows)
        except OSError as exc:
            raise PTYError(f"No se pudo redimensionar la terminal: {exc}") from exc

    def cerrar(self) -> None:
        if self._process.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self._process.pid, signal.SIGTERM)
            try:
                self._process.wait(timeout=CLOSE_GRACE_PERIOD_S)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self._process.pid, signal.SIGKILL)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    self._process.wait(timeout=CLOSE_GRACE_PERIOD_S)
        self._release_master()

    def matar_arbol(self) -> None:
        if self._process.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self._process.pid, signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                self._process.wait(timeout=CLOSE_GRACE_PERIOD_S)
        self._release_master()

    def esperar(self, timeout: float | None = None) -> int | None:
        # Se llama tras observar EOF en ``leer()``: el hijo casi siempre ya
        # terminó por su cuenta (o el kernel liberó el lado slave), así que
        # esto no manda ninguna señal -- solo recoge el código de salida
        # (equivalente exacto al ``process.wait()`` que hacía ``_finish`` en
        # ``ide_sessions.py`` antes de esta migración) y libera el master_fd.
        try:
            codigo = self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
        self._release_master()
        return codigo

    def _release_master(self) -> None:
        if not self._master_closed:
            self._master_closed = True
            with contextlib.suppress(OSError):
                os.close(self._master_fd)

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def codigo_salida(self) -> int | None:
        return self._process.poll()


class _WindowsPTY(TerminalPTY):
    """Implementación ConPTY vía ``pywinpty`` — decisión tomada en
    ``docs/edecan-windows.md`` §1 (no ``winpty`` clásico pre-ConPTY, no
    ``ctypes`` propio sobre ``CreatePseudoConsole``/``ResizePseudoConsole``/
    ``ClosePseudoConsole``: eso es reimplementar en Python lo que ``pywinpty``
    ya hace en Rust, y el ``STARTUPINFOEX`` + ``PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE``
    a mano es exactamente el código que no se puede depurar sin un Windows
    delante). ``pywinpty`` usa ConPTY como backend por defecto en Windows 10
    1809+ (nuestro piso de soporte) -- ``winpty.enums.Backend.ConPTY`` es el
    valor 0 y es lo que la librería intenta primero cuando no se fuerza nada
    (variable de entorno ``PYWINPTY_BACKEND``, que esta capa no toca).

    Cada llamada de aquí abajo cita su fuente porque esta rama **no se puede
    ejecutar ni depurar desde macOS**: la evidencia de que compila y de que la
    suite de contrato pasa solo existe una vez que corra en el job de CI de
    Windows o en la PC del dueño (ver ``docs/edecan-windows.md`` §1.3 y §6).
    Fuentes citadas:

    - API pública de ``pywinpty``: github.com/andfoy/pywinpty,
      ``winpty/ptyprocess.py`` (``PtyProcess.spawn``/``read``/``write``/
      ``setwinsize``/``isalive``/``exitstatus``) y ``winpty/enums.py``
      (``Backend``, ``Encoding``).
    - ConPTY (lo que ``pywinpty`` envuelve):
      learn.microsoft.com/windows/console/creating-a-pseudoconsole-session,
      .../createpseudoconsole, .../resizepseudoconsole,
      .../closepseudoconsole, .../console-process-groups.
    - Señales: la traducción de ``\\x03`` a ``CTRL_C_EVENT`` depende de que el
      proceso hijo NO se cree con ``CREATE_NEW_PROCESS_GROUP`` (si se crea con
      esa bandera, queda en un grupo de consola distinto al del host de
      ConPTY y el evento nunca llega -- confirmado en
      learn.microsoft.com/answers/questions/5832200 y
      learn.microsoft.com/windows/console/console-process-groups). Esta capa
      NO ve ni controla esa bandera: la decide el ``CreateProcess`` interno de
      ``pywinpty`` (crate Rust ``winptyrs``, fuera del código Python
      inspeccionable desde aquí) -- por eso "Ctrl+C interrumpe de verdad" está
      en la lista de supuestos sin confirmar, no en la de hechos.
    - ``taskkill``: learn.microsoft.com/windows-server/administration/windows-commands/taskkill.
    - ``subprocess.CREATE_NO_WINDOW``:
      docs.python.org/3/library/subprocess.html#subprocess.CREATE_NO_WINDOW.

    Supuestos SIN confirmar (van a la lista de "pendiente de comprobar en
    Windows" del encargo, no se afirman como hechos):

    1. Que el ``CreateProcess`` interno de ``pywinpty`` no use
       ``CREATE_NEW_PROCESS_GROUP`` -- si lo usara, Ctrl+C dejaría de llegar
       al hijo aunque el resto (E/S, resize) siguiera funcionando.
    2. Que ``PtyProcess.spawn(..., dimensions=(rows, cols))`` interprete ese
       orden tal como documenta su docstring (rows, cols) y no al revés.
    3. Que ``winpty.WinptyError`` sea efectivamente el tipo que se lanza ante
       un fallo de ``CreatePseudoConsole`` (HRESULT de error) -- se cita del
       ``__init__.py`` público del paquete, no de haberlo disparado.
    4. Que ``taskkill /T`` sin ``/F`` le dé a la mayoría de apps de consola
       una vía de cierre limpio antes de que llegue el ``/F`` a los 3s -- es
       la lectura razonable de la documentación, no algo medido con reloj en
       una PC real.
    """

    def __init__(self, proc: winpty.PtyProcess) -> None:
        self._proc = proc
        # Entrada: bytes -> str para PtyProcess.write() (ver winpty/ptyprocess.py,
        # ``write(self, s)`` opera sobre ``str``). Incremental para no partir
        # una secuencia UTF-8 multibyte que llegue repartida entre dos
        # llamadas a ``escribir()`` -- ``errors="replace"`` porque un byte
        # realmente inválido no debe tumbar el hilo que atiende la terminal,
        # igual que la política ya aceptada para la salida (ver docstring del
        # módulo, "Codificación").
        self._decoder_entrada = codecs.getincrementaldecoder("utf-8")(errors="replace")

    @classmethod
    def abrir(
        cls,
        argv: list[str],
        *,
        cwd: str,
        env: dict[str, str],
        cols: int,
        rows: int,
    ) -> _WindowsPTY:
        entorno = dict(env)
        # PYTHONUTF8=1 / PYTHONIOENCODING=utf-8: pywinpty conversa en UTF-8
        # (``PtyProcess.read``/``write``, ver winpty/ptyprocess.py); sin esto
        # un hijo Python en Windows podría escribir con la página de códigos
        # nativa del sistema (CP-850/CP-1252) y el decoder incremental
        # ``errors="replace"`` de ide_sessions tendría más basura que
        # absorber de la necesaria. No se toca ``chcp`` global (decisión del
        # plan, docs/edecan-windows.md §1.2 "Codificación").
        entorno.setdefault("PYTHONUTF8", "1")
        entorno.setdefault("PYTHONIOENCODING", "utf-8")
        try:
            # PtyProcess.spawn(argv, cwd=, env=, dimensions=(rows, cols)):
            # firma pública en winpty/ptyprocess.py. ``argv`` ya llega resuelto
            # (ver el docstring de ``abrir_pty``): ``spawn`` re-resuelve el
            # comando con su propio ``which()`` interno, lo que aquí es
            # redundante pero inofensivo -- si el ejecutable no aparece por
            # ese segundo lookup, lanza ``FileNotFoundError`` (capturado abajo).
            proc = winpty.PtyProcess.spawn(
                list(argv),
                cwd=cwd,
                env=entorno,
                dimensions=(rows, cols),
            )
        except FileNotFoundError as exc:
            raise PTYError(f"No se encontró el ejecutable: {argv[0]!r}: {exc}") from exc
        except winpty.WinptyError as exc:
            # Excepción pública de pywinpty (``winpty.WinptyError``, ver
            # winpty/__init__.py) para fallos del backend ConPTY/winpty-agent
            # al crear el pseudo-terminal -- p. ej. CreatePseudoConsole
            # devolviendo un HRESULT de error:
            # learn.microsoft.com/windows/console/createpseudoconsole.
            raise PTYError(f"No se pudo iniciar la terminal de Windows: {exc}") from exc
        except OSError as exc:
            raise PTYError(f"No se pudo iniciar la terminal de Windows: {exc}") from exc
        return cls(proc)

    def escribir(self, data: bytes) -> None:
        if not isinstance(data, bytes):
            raise TypeError("escribir() espera bytes ya codificados, no str.")
        # El \x03 de Ctrl+C es un solo byte ASCII: siempre pasa entero por el
        # decoder incremental en la misma llamada, nunca queda a medias. Es
        # ConPTY quien lo traduce a CTRL_C_EVENT para el grupo de consola del
        # hijo (ver el docstring de la clase, "Señales") -- esta capa no lo
        # traduce ni llama a GenerateConsoleCtrlEvent (rechazado en el plan
        # por su semántica de grupos, ver docs/edecan-windows.md §1.2).
        texto = self._decoder_entrada.decode(data)
        if not texto:
            return
        try:
            self._proc.write(texto)
        except EOFError as exc:
            # PtyProcess.write lanza EOFError si el proceso ya no está vivo
            # (ver winpty/ptyprocess.py, ``write``) -- se uniforma con la
            # excepción única de esta capa.
            raise PTYError("La terminal ya no está activa.") from exc

    def leer(self, max_bytes: int = 4096) -> bytes:
        try:
            # PtyProcess.read(size) ya decodifica UTF-8 internamente
            # (reintenta leyendo bytes de más hasta completar una secuencia
            # válida -- ver el cuerpo de ``read`` en winpty/ptyprocess.py) y
            # devuelve ``str``; ``size`` cuenta caracteres, no bytes exactos
            # -- un detalle de la librería, no de ConPTY, que no cambia el
            # contrato (``max_bytes`` sigue siendo solo el tamaño del trozo
            # que se pide, igual que ``os.read(fd, 4096)`` en la rama POSIX).
            texto = self._proc.read(max_bytes)
        except EOFError:
            # Pseudo-terminal cerrado y buffer ya drenado -- el EOF de este
            # contrato, igual que la rama POSIX devuelve ``b""`` en ese caso.
            return b""
        # Se re-codifica a UTF-8 para que el llamador (el hilo lector de
        # ide_sessions) siga hablando bytes con el MISMO decoder incremental
        # ``errors="replace"`` que usa hoy para la rama POSIX -- el contrato
        # de esta capa es bytes en las dos direcciones (ver docstring del
        # módulo, "Codificación").
        return texto.encode("utf-8")

    def redimensionar(self, cols: int, rows: int) -> None:
        try:
            # PtyProcess.setwinsize(rows, cols) -- mismo orden (filas,
            # columnas) que ``dimensions`` en ``spawn()``; internamente llama
            # a ``PTY.set_size(cols, rows)``, que en el backend ConPTY es
            # ResizePseudoConsole:
            # learn.microsoft.com/windows/console/resizepseudoconsole.
            self._proc.setwinsize(rows, cols)
        except (OSError, winpty.WinptyError) as exc:
            raise PTYError(f"No se pudo redimensionar la terminal: {exc}") from exc

    def cerrar(self) -> None:
        if self._proc.isalive():
            self._taskkill(force=False)
            limite = time.monotonic() + CLOSE_GRACE_PERIOD_S
            while time.monotonic() < limite and self._proc.isalive():
                time.sleep(0.05)
            if self._proc.isalive():
                self._taskkill(force=True)
        # No hay un master_fd que cerrar como en POSIX: cuando la última
        # referencia a ``PtyProcess``/``PTY`` se libera, su ``__del__`` (en la
        # extensión Rust) cierra el pseudo-terminal -- el equivalente de
        # ClosePseudoConsole: learn.microsoft.com/windows/console/closepseudoconsole.
        # Nada que hacer aquí explícitamente más allá de dejar de referenciarlo.

    def matar_arbol(self) -> None:
        if self._proc.isalive():
            self._taskkill(force=True)

    def esperar(self, timeout: float | None = None) -> int | None:
        # Se llama tras observar EOF en ``leer()``: el hijo casi siempre ya
        # terminó (o el pseudo-terminal se cerró) -- no manda ninguna señal,
        # solo recoge el código de salida. ``PtyProcess.wait()`` (ver
        # winpty/ptyprocess.py) sondea ``isalive()`` en un bucle SIN límite
        # de tiempo, así que el límite de ``timeout`` se sondea aquí en vez
        # de delegarlo -- mismo costo (poll de 0.1s), con corte real.
        limite = None if timeout is None else time.monotonic() + timeout
        while self._proc.isalive():
            if limite is not None and time.monotonic() >= limite:
                return None
            time.sleep(0.05)
        return self._proc.exitstatus

    def _taskkill(self, *, force: bool) -> None:
        """``taskkill /T [/F] /PID <pid>`` -- mata el ÁRBOL completo, no solo
        la raíz (``/T``: "Ends the specified process and any child processes
        started by it", ver
        learn.microsoft.com/windows-server/administration/windows-commands/taskkill).
        Sin ``/F`` primero: le da a la app la chance de un cierre limpio vía
        ``WM_CLOSE``/``CTRL_CLOSE_EVENT`` antes del martillo a los
        :data:`CLOSE_GRACE_PERIOD_S` segundos (docs/edecan-windows.md §1.2).

        Idempotente a propósito (contrato de :meth:`matar_arbol`): si el PID
        ya no existe, ``taskkill`` devuelve un código de salida distinto de 0
        y esta función lo ignora en vez de propagarlo -- "ya está muerto" es
        éxito para ``cerrar()``/``matar_arbol()``, no un error.
        """
        with contextlib.suppress(Exception):
            subprocess.run(
                _comando_taskkill(self.pid, force=force),
                # CREATE_NO_WINDOW: sin esto, cada taskkill parpadea una
                # consola nueva en la pantalla del usuario.
                # docs.python.org/3/library/subprocess.html#subprocess.CREATE_NO_WINDOW
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                capture_output=True,
                check=False,
            )

    @property
    def pid(self) -> int:
        return self._proc.pid

    @property
    def codigo_salida(self) -> int | None:
        if self._proc.isalive():
            return None
        return self._proc.exitstatus
