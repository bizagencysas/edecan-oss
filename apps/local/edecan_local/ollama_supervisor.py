"""`edecan_local.ollama_supervisor` — arranque/apagado OPCIONAL de un
`ollama serve` embebido (`DIRECCION_ACTUAL.md` "Confirmado: agregar Ollama",
WP-V4-09, patrón de auto-provisioning de open-jarvis/OpenJarvis, ver NOTICE).

Todo el CICLO DE VIDA (arrancar, esperar que responda, apagar) vive ACÁ, en
Python -- no en `apps/desktop/src-tauri` (Rust) -- porque este paquete SÍ se
testea en este entorno (no hay `cargo`/`rustc`, ver `docs/desktop-local.md`
§8/§9). El sidecar de Tauri (`backend.rs::build_command`) solo le pasa a
este proceso, cuando lanza `edecan-local`, dos env vars OPCIONALES:

- ``EDECAN_OLLAMA_BIN``: ruta absoluta al binario `ollama` empaquetado como
  sidecar de Tauri (`scripts/download-ollama.sh` + `tauri.conf.json` ->
  `bundle.externalBin`), SI quien armó la app lo incluyó. Ausente si no.
- ``EDECAN_OLLAMA_AUTOSTART``: `"true"`/`"1"` si el usuario activó "usar
  Ollama" (o la app la fija sola tras un clic en Configuración -- ver
  `docs/desktop.md` "Ollama embebido (opcional)"), o se exportó a mano en
  modo dev/self-host.

`maybe_start_ollama(settings)` es deliberadamente SÍNCRONA -- igual que
`pgserver.get_server(...)` en `edecan_local.pg` -- porque
`edecan_local.runtime.run()` la corre dentro de `asyncio.to_thread(...)`,
el mismo patrón que ya usa para el Postgres embebido. Ollama es SIEMPRE
opcional y de "mejor esfuerzo": esta función NUNCA lanza -- cualquier fallo
(binario roto, puerto ocupado, timeout arrancando) se resuelve devolviendo
`None` con un log claro, sin tumbar el arranque del resto del asistente.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

_AUTOSTART_ENV = "EDECAN_OLLAMA_AUTOSTART"
_BIN_ENV = "EDECAN_OLLAMA_BIN"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})

_DEFAULT_BASE_URL = "http://localhost:11434"
_PING_TIMEOUT_SECONDS = 2.0
_READY_TIMEOUT_SECONDS = 20.0
_READY_POLL_INTERVAL_SECONDS = 0.5
_STOP_WAIT_SECONDS = 3.0


class OllamaHandle:
    """Envuelve el `subprocess.Popen` de `ollama serve`.

    `.stop()` es idempotente (llamarla más de una vez es un no-op la segunda
    vez) y sigue el MISMO criterio que
    `apps/desktop/src-tauri/src/backend.rs::kill_backend` (documentado en
    `docs/desktop-local.md` §8): primero un apagado prolijo (`terminate` =
    SIGTERM en Unix, `TerminateProcess` en Windows vía la API estándar de
    `subprocess`) con un margen corto para que el proceso salga solo, y solo
    si no salió a tiempo, un `kill` (SIGKILL) como red de seguridad -- nunca
    deja el proceso colgado indefinidamente ni revienta el apagado del
    runner completo si algo sale mal matándolo.
    """

    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self._process = process
        self._stopped = False

    @property
    def pid(self) -> int:
        return self._process.pid

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True

        if self._process.poll() is not None:
            return  # Ya había terminado solo (crash, o el usuario lo cerró).

        try:
            self._process.terminate()
            self._process.wait(timeout=_STOP_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            logger.warning(
                "ollama serve (pid %s) no salió tras la señal de apagado en %ss -- forzando kill.",
                self.pid,
                _STOP_WAIT_SECONDS,
            )
            self._process.kill()
            try:
                self._process.wait(timeout=_STOP_WAIT_SECONDS)
            except subprocess.TimeoutExpired:
                logger.warning("ollama serve (pid %s) sigue vivo tras el kill.", self.pid)
        except Exception:  # noqa: BLE001 - apagar nunca debe reventar el shutdown del runner
            logger.warning("Error deteniendo ollama serve (pid %s).", self.pid, exc_info=True)
        else:
            logger.info("ollama serve (pid %s) detenido.", self.pid)


def maybe_start_ollama(settings: Any = None) -> OllamaHandle | None:
    """Arranca `ollama serve` embebido si corresponde, o devuelve `None`.

    Cuatro caminos, en orden (todos terminan en un log claro, nunca en una
    excepción):

    1. ``EDECAN_OLLAMA_AUTOSTART`` no está activada -> `None` de inmediato,
       sin tocar el disco ni la red (comportamiento hoy-por-defecto: nadie
       nota nada distinto si no opta por esto).
    2. No se encuentra ningún binario `ollama` (ni `EDECAN_OLLAMA_BIN`, ni
       el `PATH`) -> `None` con un log explicando por qué.
    3. Ya hay un Ollama respondiendo en `settings.OLLAMA_BASE_URL` (el
       usuario lo tenía corriendo aparte, o quedó de un arranque anterior)
       -> `None`: nunca se lanza un segundo proceso compitiendo por el mismo
       puerto.
    4. Arranque feliz: se lanza el proceso y se espera (con reintentos
       cortos) a que responda -> `OllamaHandle`. Si nunca llega a responder
       dentro de `_READY_TIMEOUT_SECONDS`, se lo detiene y se devuelve
       `None` igual -- Ollama es de "mejor esfuerzo", nunca bloquea el resto
       del arranque.
    """
    if not _autostart_enabled():
        return None

    binary = _resolve_binary()
    if binary is None:
        logger.info(
            "%s=true pero no se encontró ningún binario de ollama (ni %s, ni el PATH) "
            "-- se omite el arranque automático. La app sigue funcionando igual; el "
            "usuario puede instalar Ollama aparte y usarlo desde Configuración.",
            _AUTOSTART_ENV,
            _BIN_ENV,
        )
        return None

    base_url = getattr(settings, "OLLAMA_BASE_URL", None) or _DEFAULT_BASE_URL

    if _ping(base_url):
        logger.info("Ollama ya está corriendo en %s -- no se lanza un proceso nuevo.", base_url)
        return None

    logger.info("Arrancando ollama serve embebido (%s) en %s...", binary, base_url)
    process = _spawn(binary, base_url)
    if process is None:
        return None

    handle = OllamaHandle(process)
    if not _wait_until_ready(base_url, process):
        logger.warning(
            "ollama serve (pid %s) no respondió en %ss -- se detiene y se sigue sin él.",
            handle.pid,
            _READY_TIMEOUT_SECONDS,
        )
        handle.stop()
        return None

    logger.info("Ollama embebido listo en %s (pid %s).", base_url, handle.pid)
    return handle


# ---------------------------------------------------------------------------
# Helpers privados
# ---------------------------------------------------------------------------


def _autostart_enabled() -> bool:
    raw = os.environ.get(_AUTOSTART_ENV, "")
    return raw.strip().lower() in _TRUE_VALUES


def _resolve_binary() -> str | None:
    """`EDECAN_OLLAMA_BIN` (sidecar empaquetado por Tauri, ver docstring del
    módulo) primero; si no está fijada, `shutil.which("ollama")` (el usuario
    ya lo tenía instalado aparte, con o sin empaquetado)."""
    from_env = os.environ.get(_BIN_ENV)
    if from_env:
        return from_env
    return shutil.which("ollama")


def _ping(base_url: str) -> bool:
    """`GET {base_url}/api/tags` con timeout corto -- `True` si Ollama
    responde ahí. Import local de `httpx` (no al tope del archivo) a
    propósito: mismo patrón que `edecan_local.runtime._wait_until_healthy`,
    para que los tests puedan fakear el módulo entero vía `sys.modules` sin
    depender de que este archivo lo haya importado antes."""
    import httpx

    try:
        response = httpx.get(f"{base_url}/api/tags", timeout=_PING_TIMEOUT_SECONDS)
        response.raise_for_status()
    except Exception:  # noqa: BLE001 - "todavía no está corriendo" es el caso normal
        return False
    return True


def _spawn(binary: str, base_url: str) -> subprocess.Popen[bytes] | None:
    """Lanza `<binary> serve` con `OLLAMA_HOST` fijado a partir de
    `base_url` (para que si `OLLAMA_BASE_URL` fue configurado a un puerto no
    estándar, el proceso levantado escuche justo ahí). stdout/stderr van a
    `DEVNULL` a propósito: nadie lee esas tuberías en este proceso, y no
    drenarlas podría bloquear a `ollama` si llegara a escribir mucho."""
    env = dict(os.environ)
    host = urlsplit(base_url).netloc
    if host:
        env["OLLAMA_HOST"] = host

    try:
        return subprocess.Popen(  # noqa: S603 - binario resuelto por nosotros, nunca desde input de usuario/red
            [binary, "serve"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        logger.warning("No se pudo lanzar '%s serve'.", binary, exc_info=True)
        return None


def _wait_until_ready(base_url: str, process: subprocess.Popen[bytes]) -> bool:
    """Sondea `base_url` hasta que responda o se agote `_READY_TIMEOUT_SECONDS`
    -- corta de inmediato (sin agotar el timeout) si el proceso ya terminó
    solo (crash temprano, típicamente puerto ocupado por otra cosa)."""
    deadline = time.monotonic() + _READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            logger.warning(
                "ollama serve terminó solo (código %s) antes de avisar que estaba listo.",
                process.returncode,
            )
            return False
        if _ping(base_url):
            return True
        time.sleep(_READY_POLL_INTERVAL_SECONDS)
    return False
