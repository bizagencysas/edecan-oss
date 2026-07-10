"""`edecan_local.ollama_supervisor` (WP-V4-09) -- offline por defecto: ningún
test toca la red ni lanza un binario `ollama` real. `httpx` se fakea vía
`sys.modules` (mismo truco que `test_runtime.py::_install_fake_httpx`) para
los tests de `_ping`, y `subprocess.Popen`/los procesos que devuelve se
fakean con objetos de mano (mismo espíritu que `test_pg.py` fakea
`pgserver`) para `_spawn`/`OllamaHandle.stop`. Los tests de `maybe_start_ollama`
fakean directamente sus colaboradores (`_resolve_binary`/`_ping`/`_spawn`)
en vez de bajar hasta httpx/subprocess -- cubren la lógica de branching, no
la mecánica de red/proceso (ya cubierta por los tests de más abajo)."""

from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import edecan_local.ollama_supervisor as mod
import pytest
from edecan_local.ollama_supervisor import OllamaHandle, maybe_start_ollama


@pytest.fixture(autouse=True)
def _sin_env_de_ollama_heredado(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ningún test de este módulo debe depender de lo que haya (o no) en el
    entorno real de quien corre la suite -- cada test que necesita
    `EDECAN_OLLAMA_AUTOSTART`/`EDECAN_OLLAMA_BIN` los fija explícito."""
    monkeypatch.delenv("EDECAN_OLLAMA_AUTOSTART", raising=False)
    monkeypatch.delenv("EDECAN_OLLAMA_BIN", raising=False)


def _no_deberia_llamarse(*_args: Any, **_kwargs: Any) -> Any:
    pytest.fail("no debería llamarse")


# ---------------------------------------------------------------------------
# _autostart_enabled
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("valor", ["1", "true", "True", "TRUE", "yes", "on"])
def test_autostart_enabled_valores_verdaderos(monkeypatch: pytest.MonkeyPatch, valor: str) -> None:
    monkeypatch.setenv("EDECAN_OLLAMA_AUTOSTART", valor)
    assert mod._autostart_enabled() is True


@pytest.mark.parametrize("valor", ["0", "false", "no", "", "algo-random"])
def test_autostart_enabled_valores_falsos(monkeypatch: pytest.MonkeyPatch, valor: str) -> None:
    monkeypatch.setenv("EDECAN_OLLAMA_AUTOSTART", valor)
    assert mod._autostart_enabled() is False


def test_autostart_enabled_sin_fijar_es_falso() -> None:
    assert mod._autostart_enabled() is False


# ---------------------------------------------------------------------------
# _resolve_binary
# ---------------------------------------------------------------------------


def test_resolve_binary_usa_edecan_ollama_bin_si_esta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDECAN_OLLAMA_BIN", "/ruta/al/sidecar/ollama")
    monkeypatch.setattr(mod.shutil, "which", _no_deberia_llamarse)
    assert mod._resolve_binary() == "/ruta/al/sidecar/ollama"


def test_resolve_binary_cae_a_which_sin_env(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_which(name: str) -> str | None:
        return "/usr/local/bin/ollama" if name == "ollama" else None

    monkeypatch.setattr(mod.shutil, "which", fake_which)
    assert mod._resolve_binary() == "/usr/local/bin/ollama"


def test_resolve_binary_none_si_no_hay_nada(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)
    assert mod._resolve_binary() is None


# ---------------------------------------------------------------------------
# _ping -- httpx fakeado vía sys.modules
# ---------------------------------------------------------------------------


class _FakeHttpxResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _install_fake_httpx_get(
    monkeypatch: pytest.MonkeyPatch, response_fn: Any
) -> list[tuple[str, float]]:
    calls: list[tuple[str, float]] = []

    def fake_get(url: str, timeout: float | None = None) -> _FakeHttpxResponse:
        calls.append((url, timeout))
        item = response_fn(len(calls))
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(get=fake_get))
    return calls


def test_ping_true_si_responde_200(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_httpx_get(monkeypatch, lambda n: _FakeHttpxResponse(200))
    assert mod._ping("http://localhost:11434") is True
    assert calls == [("http://localhost:11434/api/tags", mod._PING_TIMEOUT_SECONDS)]


def test_ping_false_si_error_de_conexion(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_httpx_get(monkeypatch, lambda n: ConnectionError("rechazado"))
    assert mod._ping("http://localhost:11434") is False


def test_ping_false_si_status_de_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_httpx_get(monkeypatch, lambda n: _FakeHttpxResponse(500))
    assert mod._ping("http://localhost:11434") is False


# ---------------------------------------------------------------------------
# _spawn -- subprocess.Popen fakeado
# ---------------------------------------------------------------------------


class _FakePopenHandle:
    """Doble mínimo de `subprocess.Popen`: solo lo que `OllamaHandle`/
    `_wait_until_ready` tocan (`pid`, `poll`, `terminate`, `kill`, `wait`)."""

    def __init__(self, *, pid: int = 4242) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.terminate_called = False
        self.kill_called = False
        self.wait_calls: list[float | None] = []
        self._alive = True
        # Si se fija, `wait()` no lanza TimeoutExpired -- simula que el
        # proceso sale solo apenas se lo manda a terminar/matar.
        self.exits_on_terminate = True
        self.exits_on_kill = True

    def poll(self) -> int | None:
        return None if self._alive else (self.returncode if self.returncode is not None else 0)

    def terminate(self) -> None:
        self.terminate_called = True
        if self.exits_on_terminate:
            self._alive = False
            self.returncode = 0

    def kill(self) -> None:
        self.kill_called = True
        if self.exits_on_kill:
            self._alive = False
            self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls.append(timeout)
        if self._alive:
            raise subprocess.TimeoutExpired(cmd="ollama", timeout=timeout or 0)
        assert self.returncode is not None
        return self.returncode


def test_spawn_pasa_binario_serve_y_ollama_host(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    fake_process = _FakePopenHandle()

    def fake_popen(
        args: list[str], *, env: dict[str, str], stdout: Any, stderr: Any
    ) -> _FakePopenHandle:
        captured["args"] = args
        captured["env"] = env
        captured["stdout"] = stdout
        captured["stderr"] = stderr
        return fake_process

    monkeypatch.setattr(mod.subprocess, "Popen", fake_popen)

    result = mod._spawn("/usr/local/bin/ollama", "http://127.0.0.1:9999")

    assert result is fake_process
    assert captured["args"] == ["/usr/local/bin/ollama", "serve"]
    assert captured["env"]["OLLAMA_HOST"] == "127.0.0.1:9999"
    assert captured["stdout"] == subprocess.DEVNULL
    assert captured["stderr"] == subprocess.DEVNULL


def test_spawn_devuelve_none_si_popen_lanza_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_popen(*args: Any, **kwargs: Any) -> None:
        raise OSError("binario roto")

    monkeypatch.setattr(mod.subprocess, "Popen", fake_popen)

    assert mod._spawn("/no/existe/ollama", "http://localhost:11434") is None


# ---------------------------------------------------------------------------
# _wait_until_ready
# ---------------------------------------------------------------------------


def test_wait_until_ready_exito_tras_reintentos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_READY_POLL_INTERVAL_SECONDS", 0.001)
    pings: list[str] = []

    def fake_ping(base_url: str) -> bool:
        pings.append(base_url)
        return len(pings) >= 3

    monkeypatch.setattr(mod, "_ping", fake_ping)
    process = _FakePopenHandle()

    assert mod._wait_until_ready("http://localhost:11434", process) is True
    assert len(pings) == 3


def test_wait_until_ready_corta_si_el_proceso_ya_termino(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_ping", lambda base_url: pytest.fail("no debería llegar a pinguear"))
    process = _FakePopenHandle()
    process._alive = False
    process.returncode = 1

    assert mod._wait_until_ready("http://localhost:11434", process) is False


def test_wait_until_ready_agota_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_READY_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(mod, "_READY_POLL_INTERVAL_SECONDS", 0.005)
    monkeypatch.setattr(mod, "_ping", lambda base_url: False)
    process = _FakePopenHandle()

    assert mod._wait_until_ready("http://localhost:11434", process) is False


# ---------------------------------------------------------------------------
# OllamaHandle.stop()
# ---------------------------------------------------------------------------


def test_handle_stop_termina_prolijo() -> None:
    process = _FakePopenHandle()
    handle = OllamaHandle(process)

    handle.stop()

    assert process.terminate_called is True
    assert process.kill_called is False


def test_handle_stop_es_idempotente() -> None:
    process = _FakePopenHandle()
    handle = OllamaHandle(process)

    handle.stop()
    handle.stop()

    assert process.wait_calls.count(mod._STOP_WAIT_SECONDS) == 1


def test_handle_stop_no_hace_nada_si_ya_habia_terminado() -> None:
    process = _FakePopenHandle()
    process._alive = False
    process.returncode = 0
    handle = OllamaHandle(process)

    handle.stop()

    assert process.terminate_called is False
    assert process.kill_called is False


def test_handle_stop_escala_a_kill_si_terminate_no_alcanza() -> None:
    process = _FakePopenHandle()
    process.exits_on_terminate = False  # terminate() no lo mata -> escala a kill()
    handle = OllamaHandle(process)

    handle.stop()

    assert process.terminate_called is True
    assert process.kill_called is True


def test_handle_stop_nunca_lanza_aunque_siga_vivo_tras_kill() -> None:
    process = _FakePopenHandle()
    process.exits_on_terminate = False
    process.exits_on_kill = False  # ni terminate ni kill lo bajan -> no debe reventar igual
    handle = OllamaHandle(process)

    handle.stop()  # no debe propagar ninguna excepción

    assert process.terminate_called is True
    assert process.kill_called is True


def test_handle_stop_nunca_lanza_si_terminate_explota(monkeypatch: pytest.MonkeyPatch) -> None:
    process = _FakePopenHandle()

    def terminate_roto() -> None:
        raise RuntimeError("el sistema operativo dijo que no")

    monkeypatch.setattr(process, "terminate", terminate_roto)
    handle = OllamaHandle(process)

    handle.stop()  # no debe propagar la excepción


def test_handle_pid_expone_el_pid_del_proceso() -> None:
    process = _FakePopenHandle(pid=9876)
    assert OllamaHandle(process).pid == 9876


# ---------------------------------------------------------------------------
# maybe_start_ollama -- los cinco escenarios pedidos por el work package
# ---------------------------------------------------------------------------


def test_maybe_start_ollama_autostart_apagado_no_hace_nada(monkeypatch: pytest.MonkeyPatch) -> None:
    # Sin EDECAN_OLLAMA_AUTOSTART fijada (default de la fixture autouse):
    # ningún colaborador debería siquiera llamarse.
    monkeypatch.setattr(mod, "_resolve_binary", _no_deberia_llamarse)
    monkeypatch.setattr(mod, "_ping", _no_deberia_llamarse)
    monkeypatch.setattr(mod, "_spawn", _no_deberia_llamarse)

    assert maybe_start_ollama(SimpleNamespace(OLLAMA_BASE_URL="http://localhost:11434")) is None


def test_maybe_start_ollama_binario_ausente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDECAN_OLLAMA_AUTOSTART", "true")
    monkeypatch.setattr(mod, "_resolve_binary", lambda: None)
    monkeypatch.setattr(mod, "_ping", _no_deberia_llamarse)

    assert maybe_start_ollama(SimpleNamespace(OLLAMA_BASE_URL="http://localhost:11434")) is None


def test_maybe_start_ollama_ya_corriendo_no_lanza_otro(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDECAN_OLLAMA_AUTOSTART", "1")
    monkeypatch.setattr(mod, "_resolve_binary", lambda: "/usr/local/bin/ollama")
    monkeypatch.setattr(mod, "_ping", lambda base_url: True)
    monkeypatch.setattr(mod, "_spawn", _no_deberia_llamarse)

    assert maybe_start_ollama(SimpleNamespace(OLLAMA_BASE_URL="http://localhost:11434")) is None


def test_maybe_start_ollama_arranque_feliz(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDECAN_OLLAMA_AUTOSTART", "true")
    monkeypatch.setattr(mod, "_resolve_binary", lambda: "/usr/local/bin/ollama")

    ping_calls: list[str] = []

    def fake_ping(base_url: str) -> bool:
        ping_calls.append(base_url)
        # 1er llamado ("¿ya está corriendo?") -> False; los siguientes
        # (dentro de _wait_until_ready) -> True, ya "arrancó".
        return len(ping_calls) > 1

    monkeypatch.setattr(mod, "_ping", fake_ping)

    fake_process = _FakePopenHandle(pid=5150)
    spawn_calls: list[tuple[str, str]] = []

    def fake_spawn(binary: str, base_url: str) -> _FakePopenHandle:
        spawn_calls.append((binary, base_url))
        return fake_process

    monkeypatch.setattr(mod, "_spawn", fake_spawn)

    handle = maybe_start_ollama(SimpleNamespace(OLLAMA_BASE_URL="http://localhost:11434"))

    assert isinstance(handle, OllamaHandle)
    assert handle.pid == 5150
    assert spawn_calls == [("/usr/local/bin/ollama", "http://localhost:11434")]
    assert len(ping_calls) >= 2


def test_maybe_start_ollama_nunca_queda_listo_se_detiene_solo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EDECAN_OLLAMA_AUTOSTART", "true")
    monkeypatch.setattr(mod, "_READY_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(mod, "_READY_POLL_INTERVAL_SECONDS", 0.005)
    monkeypatch.setattr(mod, "_resolve_binary", lambda: "/usr/local/bin/ollama")
    # Nunca responde, ni antes (ya-corriendo) ni después (arranque).
    monkeypatch.setattr(mod, "_ping", lambda base_url: False)

    fake_process = _FakePopenHandle()
    monkeypatch.setattr(mod, "_spawn", lambda binary, base_url: fake_process)

    handle = maybe_start_ollama(SimpleNamespace(OLLAMA_BASE_URL="http://localhost:11434"))

    assert handle is None
    assert fake_process.terminate_called is True  # se detuvo solo, no queda huérfano


def test_maybe_start_ollama_spawn_fallido_devuelve_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDECAN_OLLAMA_AUTOSTART", "true")
    monkeypatch.setattr(mod, "_resolve_binary", lambda: "/usr/local/bin/ollama")
    monkeypatch.setattr(mod, "_ping", lambda base_url: False)
    monkeypatch.setattr(mod, "_spawn", lambda binary, base_url: None)

    assert maybe_start_ollama(SimpleNamespace(OLLAMA_BASE_URL="http://localhost:11434")) is None


def test_maybe_start_ollama_sin_settings_usa_default_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EDECAN_OLLAMA_AUTOSTART", "true")
    # Corta temprano ("binario ausente") -- alcanza para probar que
    # `maybe_start_ollama()` sin `settings` no revienta (usa el default).
    monkeypatch.setattr(mod, "_resolve_binary", lambda: None)

    assert maybe_start_ollama() is None
