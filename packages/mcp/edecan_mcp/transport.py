"""Transportes MCP — ASYNC (adaptado de OpenJarvis `src/openjarvis/mcp/
transport.py`, síncrono en origen, Apache-2.0, ver `NOTICE` en la raíz del
repo). Tres implementaciones:

- `InProcessTransport` — despacha directo a un objeto en memoria con
  `async def handle(request) -> MCPResponse` (tests, ver `tests/
  fake_server.py`).
- `HTTPTransport` — MCP *Streamable HTTP*: `httpx.AsyncClient` persistente
  (una conexión reutilizada entre llamadas mientras el transporte vive),
  header `Mcp-Session-Id` rastreado tras el primer response, acepta tanto
  `application/json` como `text/event-stream` en la respuesta (spec MCP
  2025-03-26).
- `StdioTransport` — subprocess local vía `asyncio.create_subprocess_exec`
  (JAMÁS `shell=True`: `command` siempre es una lista ya separada, nunca un
  string que un shell interprete) hablando JSON delimitado por líneas sobre
  stdin/stdout. Drena `stderr` en una tarea de fondo para no bloquear al
  subprocess si escribe ahí, y esa tarea se ejecuta a través de
  `_run_background` (ver su docstring) para normalizar cualquier
  `SystemExit`/`KeyboardInterrupt` crudo antes de que la máquina de
  `asyncio.Task` lo vea — el mismo riesgo que documenta
  `apps/local/edecan_local/runtime.py::_run_background` (investigación real
  de fuga de tareas asyncio, ver `HOTFIXES_PENDIENTES.md` sección
  correspondiente): una tarea de fondo que deja escapar un `SystemExit`
  puede interrumpir lo que sea que esté bombeando el event loop en ese
  instante, no solo a sí misma. `edecan_mcp` es un paquete (no una app, ver
  `ARCHITECTURE.md` §10.1) así que no puede importar `apps/local` — este
  módulo trae su PROPIA copia mínima del mismo patrón, con esta cita como
  precedente en vez de reimplementar la investigación desde cero.
"""

from __future__ import annotations

import asyncio
import logging
import os
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from .protocol import MCPRequest, MCPResponse

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)

DEFAULT_CONNECT_TIMEOUT_SECONDS = 10.0
DEFAULT_REQUEST_TIMEOUT_SECONDS = 60.0
DEFAULT_STDIO_TIMEOUT_SECONDS = 30.0
DEFAULT_STDIO_CLOSE_TIMEOUT_SECONDS = 5.0

# Variables de entorno que SÍ se heredan del proceso padre al lanzar un
# servidor MCP por stdio — deliberadamente mínimo (ver `seguridad.py` y
# `docs/mcp.md` "Seguridad"): el server MCP corre como subproceso local del
# propio tenant, pero el proceso backend puede tener en su ambiente
# credenciales de PLATAFORMA (`AWS_*`, `ANTHROPIC_API_KEY`, `JWT_SECRET`,
# etc., ver `edecan_api.config.Settings`) que un servidor MCP de terceros
# JAMÁS debe poder leer con un simple `os.environ`. `PATH`/`HOME` son el
# mínimo indispensable para que un binario común (`npx`, `python`, `node`,
# etc.) se resuelva y encuentre su config de usuario — cualquier variable
# adicional que un servidor MCP concreto necesite es responsabilidad del
# propio tenant (pasarla codificada en el `comando`, p. ej. `env X=Y npx
# ...`, no algo que Edecán le regale del ambiente del backend).
#
# Nota (WP-V7-05, verificado empíricamente con un subproceso real que
# reporta su propio `os.environ`, ver `tests/test_transport.py::
# test_stdio_transport_el_subproceso_solo_hereda_path_y_home`): en macOS, el
# proceso HIJO puede recibir un par de variables NO secretas más allá de este
# allowlist — `__CF_USER_TEXT_ENCODING` (CoreFoundation, deriva del UID) y
# `LC_CTYPE` (coerción de locale de CPython, PEP 538) — incluso pasando un
# `env={}` totalmente vacío a `create_subprocess_exec`. Confirmado que NO
# llegan copiando `os.environ` del padre (persisten igual con `env={}`
# explícito): las sintetiza el propio runtime del sistema operativo/CPython
# en el hijo, fuera del control de este módulo, y ninguna de las dos puede
# contener un secreto. La garantía real que sí depende de este código —
# ninguna credencial/config de PLATAFORMA (`ANTHROPIC_API_KEY`, `AWS_*`,
# `DATABASE_URL`, `JWT_SECRET`, ...) cruza al subprocess— se mantiene intacta.
_STDIO_ENV_ALLOWLIST = ("PATH", "HOME")


class MCPTransportError(RuntimeError):
    """Error de transporte (conexión/timeout/proceso) — mensaje ya listo para
    mostrarle al usuario/modelo, nunca expone un traceback interno."""


async def _run_background(coro: Any, *, label: str) -> None:
    """Ejecuta `coro` normalizando cualquier `BaseException` que NO sea
    `asyncio.CancelledError` ni una `Exception` "normal" a una `RuntimeError`
    ANTES de que la máquina de `asyncio.Task` la vea — mismo patrón (y mismo
    motivo) que `apps/local/edecan_local/runtime.py::_run_background`, ver el
    docstring de este módulo para la cita completa. Toda tarea de fondo de
    este paquete (hoy: `StdioTransport._drenar_stderr`) pasa por acá.
    """
    try:
        await coro
    except (asyncio.CancelledError, Exception):
        raise
    except BaseException as exc:  # SystemExit/KeyboardInterrupt/GeneratorExit
        raise RuntimeError(f"{label} terminó con {type(exc).__name__}: {exc}") from exc


class MCPTransport(ABC):
    """Transporte abstracto para comunicación MCP."""

    @abstractmethod
    async def send(self, request: MCPRequest) -> MCPResponse:
        """Envía `request` y espera la `MCPResponse` correspondiente."""
        raise NotImplementedError

    async def send_notification(self, request: MCPRequest) -> None:
        """Envía una notificación JSON-RPC (sin `id`, sin respuesta esperada).

        Implementación por defecto: delega en `send` y descarta la
        respuesta. Los transportes reales (`StdioTransport`/`HTTPTransport`)
        la sobreescriben porque el servidor no responde nada a una
        notificación — usar `send` ahí se quedaría esperando una respuesta
        que nunca llega.
        """
        await self.send(request)

    @abstractmethod
    async def close(self) -> None:
        """Libera los recursos del transporte (conexión HTTP, subprocess…)."""
        raise NotImplementedError


class InProcessTransport(MCPTransport):
    """Transporte directo en memoria — solo para tests.

    `server` es cualquier objeto con `async def handle(request: MCPRequest)
    -> MCPResponse` (ver `tests/fake_server.py` de este mismo paquete):
    nunca serializa nada a JSON, así que sirve para probar `MCPClient` sin
    tocar red ni subprocesos.
    """

    def __init__(self, server: Any) -> None:
        self._server = server

    async def send(self, request: MCPRequest) -> MCPResponse:
        return await self._server.handle(request)

    async def close(self) -> None:
        pass


class StdioTransport(MCPTransport):
    """Servidor MCP local, lanzado como subprocess y hablado por stdin/stdout
    (JSON delimitado por líneas). SOLO tiene sentido con `EDECAN_LOCAL_MODE`
    (ver `seguridad.validar_comando_mcp`, que es quien exige eso — este
    transporte en sí no vuelve a comprobarlo, confía en su llamador).
    """

    def __init__(
        self,
        command: list[str],
        *,
        timeout_seconds: float = DEFAULT_STDIO_TIMEOUT_SECONDS,
        close_timeout_seconds: float = DEFAULT_STDIO_CLOSE_TIMEOUT_SECONDS,
    ) -> None:
        if not command or not command[0].strip():
            raise ValueError("StdioTransport requiere un comando no vacío.")
        self._command = list(command)
        self._timeout = timeout_seconds
        self._close_timeout = close_timeout_seconds
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task[None] | None = None

    async def _ensure_started(self) -> asyncio.subprocess.Process:
        if self._process is not None:
            return self._process
        env = {clave: os.environ[clave] for clave in _STDIO_ENV_ALLOWLIST if clave in os.environ}
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self._command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except (OSError, FileNotFoundError) as exc:
            raise MCPTransportError(
                f"No se pudo iniciar el servidor MCP «{self._command[0]}»: {exc}"
            ) from exc
        self._stderr_task = asyncio.create_task(
            _run_background(self._drenar_stderr(), label=f"mcp-stdio-stderr:{self._command[0]}")
        )
        return self._process

    async def _drenar_stderr(self) -> None:
        """Lee `stderr` del subprocess en segundo plano y lo manda a logs —
        sin esto, un servidor MCP que escriba suficiente en `stderr` podría
        bloquearse si nadie vacía ese pipe (backpressure de subprocess)."""
        process = self._process
        if process is None or process.stderr is None:
            return
        while True:
            linea = await process.stderr.readline()
            if not linea:
                return
            logger.debug(
                "mcp stdio stderr (%s): %s",
                self._command[0],
                linea.decode("utf-8", errors="replace").rstrip(),
            )

    async def send(self, request: MCPRequest) -> MCPResponse:
        process = await self._ensure_started()
        if process.stdin is None or process.stdout is None:
            raise MCPTransportError("El proceso del servidor MCP no expone stdin/stdout.")

        linea = (request.to_json() + "\n").encode("utf-8")
        try:
            process.stdin.write(linea)
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, RuntimeError) as exc:
            raise MCPTransportError(
                f"El servidor MCP «{self._command[0]}» cerró la conexión: {exc}"
            ) from exc

        try:
            respuesta_linea = await asyncio.wait_for(
                process.stdout.readline(), timeout=self._timeout
            )
        except TimeoutError as exc:
            raise MCPTransportError(
                f"El servidor MCP «{self._command[0]}» no respondió en {self._timeout:.0f}s."
            ) from exc
        if not respuesta_linea:
            raise MCPTransportError(
                f"El servidor MCP «{self._command[0]}» cerró la conexión sin responder."
            )
        try:
            return MCPResponse.from_json(respuesta_linea.decode("utf-8"))
        except (ValueError, KeyError, UnicodeDecodeError) as exc:
            raise MCPTransportError(
                f"Respuesta inválida del servidor MCP «{self._command[0]}»: {exc}"
            ) from exc

    async def send_notification(self, request: MCPRequest) -> None:
        """Solo escribe — nunca lee: un servidor stdio no responde nada a una
        notificación, así que `send()` se quedaría esperando para siempre en
        `readline()`."""
        process = await self._ensure_started()
        if process.stdin is None:
            raise MCPTransportError("El proceso del servidor MCP no expone stdin.")
        linea = (request.to_json() + "\n").encode("utf-8")
        try:
            process.stdin.write(linea)
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, RuntimeError) as exc:
            raise MCPTransportError(
                f"El servidor MCP «{self._command[0]}» cerró la conexión: {exc}"
            ) from exc

    async def close(self) -> None:
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 - cerrar nunca debe reventar por esto
                logger.debug("La tarea de stderr terminó con error al cerrar.", exc_info=True)
            self._stderr_task = None

        process = self._process
        self._process = None
        if process is None or process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=self._close_timeout)
        except TimeoutError:
            process.kill()
            await process.wait()


class HTTPTransport(MCPTransport):
    """MCP *Streamable HTTP* — JSON-RPC sobre HTTP con `httpx.AsyncClient`
    persistente (se abre una vez, se reutiliza en cada `send`)."""

    def __init__(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
        request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        import httpx

        self._url = url
        self._headers_extra = dict(headers or {})
        self._session_id: str | None = None
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=connect_timeout_seconds,
                read=request_timeout_seconds,
                write=request_timeout_seconds,
                pool=connect_timeout_seconds,
            ),
        )

    def _build_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        # Los headers del tenant (p. ej. `Authorization: Bearer …` de SU
        # servidor MCP) van encima — nunca al revés, para que un servidor
        # que de verdad necesite pisar `Accept`/`Content-Type` pueda hacerlo.
        headers.update(self._headers_extra)
        if self._session_id is not None:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    async def _post(self, request: MCPRequest) -> httpx.Response:
        import httpx

        try:
            response = await self._client.post(
                self._url, json=request.to_dict(), headers=self._build_headers()
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise MCPTransportError(f"El servidor MCP no respondió a tiempo: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            raise MCPTransportError(
                f"El servidor MCP respondió HTTP {exc.response.status_code}."
            ) from exc
        except httpx.HTTPError as exc:
            raise MCPTransportError(f"No se pudo conectar con el servidor MCP: {exc}") from exc

        nuevo_session_id = response.headers.get("mcp-session-id")
        if nuevo_session_id is not None:
            self._session_id = nuevo_session_id
        return response

    @staticmethod
    def _extraer_json_de_sse(texto: str) -> str:
        """Un servidor *Streamable HTTP* puede responder `text/event-stream`
        en vez de `application/json` — el cuerpo real es la ÚLTIMA línea
        `data:` del stream."""
        ultimo = ""
        for linea in texto.splitlines():
            if linea.startswith("data:"):
                ultimo = linea[len("data:") :].strip()
        if not ultimo:
            raise MCPTransportError("El servidor MCP respondió un event-stream sin datos.")
        return ultimo

    async def send(self, request: MCPRequest) -> MCPResponse:
        response = await self._post(request)
        content_type = response.headers.get("content-type", "")
        cuerpo = response.text
        if "text/event-stream" in content_type or cuerpo.lstrip().startswith("event:"):
            cuerpo = self._extraer_json_de_sse(cuerpo)
        try:
            return MCPResponse.from_json(cuerpo)
        except (ValueError, KeyError) as exc:
            raise MCPTransportError(f"Respuesta inválida del servidor MCP: {exc}") from exc

    async def send_notification(self, request: MCPRequest) -> None:
        """Acepta cualquier 2xx sin intentar parsear el cuerpo — el servidor
        puede responder `202 Accepted` con el cuerpo vacío."""
        await self._post(request)

    async def close(self) -> None:
        await self._client.aclose()


__all__ = [
    "DEFAULT_CONNECT_TIMEOUT_SECONDS",
    "DEFAULT_REQUEST_TIMEOUT_SECONDS",
    "DEFAULT_STDIO_CLOSE_TIMEOUT_SECONDS",
    "DEFAULT_STDIO_TIMEOUT_SECONDS",
    "HTTPTransport",
    "InProcessTransport",
    "MCPTransport",
    "MCPTransportError",
    "StdioTransport",
]
