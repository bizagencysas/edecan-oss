"""Tests de `run_command` y del portapapeles (siempre con allowlist explícita; sin red)."""

from __future__ import annotations

import subprocess
import sys

import pytest
from edecan_companion import actions


def test_run_command_rejects_executable_not_in_allowlist(companion_config):
    assert companion_config.allowed_commands == []  # nada permitido por defecto

    with pytest.raises(actions.ActionError, match="no permitido"):
        actions._run_command({"command": "ls -la"}, companion_config)


def test_run_command_runs_allowed_executable(companion_config):
    companion_config.allowed_commands.append(sys.executable)

    result = actions._run_command(
        {"command": f"{sys.executable} -c \"print('hola')\""}, companion_config
    )

    assert result["returncode"] == 0
    assert "hola" in result["stdout"]
    assert result["truncated"] is False


def test_open_url_abre_http_en_macos(companion_config, monkeypatch):
    """En macOS la URL se abre REUTILIZANDO la pestaña del mismo sitio
    (AppleScript a Chrome): sin esto, cada visita del scan de vida digital
    dejaba UNA PESTAÑA NUEVA de LinkedIn (20+ tabs acumuladas, 30-ago)."""
    seen: list[list[str]] = []

    def fake_run(argv, **kwargs):
        seen.append(list(argv))
        if argv[0] == "osascript":
            return subprocess.CompletedProcess(argv, 0, "reused\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(actions.sys, "platform", "darwin")
    monkeypatch.setattr(actions.subprocess, "run", fake_run)
    result = actions._open_url({"url": "https://www.booking.com/hotel/x"}, companion_config)
    assert result == {
        "url": "https://www.booking.com/hotel/x",
        "launched": True,
        "modo": "reused",
    }
    assert seen == [
        ["osascript", "-", "https://www.booking.com/hotel/x", "booking.com"],
    ]


def test_open_url_cae_a_open_si_osascript_falla(companion_config, monkeypatch):
    """AppleScript falla (sin Chrome, permiso negado): el `open` clásico
    sigue siendo el fallback — nunca peor que antes."""
    seen: list[list[str]] = []

    def fake_run(argv, **kwargs):
        seen.append(list(argv))
        if argv[0] == "osascript":
            return subprocess.CompletedProcess(argv, 1, "", "not allowed")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(actions.sys, "platform", "darwin")
    monkeypatch.setattr(actions.subprocess, "run", fake_run)
    result = actions._open_url({"url": "https://www.booking.com/hotel/x"}, companion_config)
    assert result == {"url": "https://www.booking.com/hotel/x", "launched": True}
    assert seen == [
        ["osascript", "-", "https://www.booking.com/hotel/x", "booking.com"],
        ["open", "https://www.booking.com/hotel/x"],
    ]


def test_open_url_rechaza_esquemas_locales(companion_config):
    with pytest.raises(actions.ActionError, match="solo abro"):
        actions._open_url({"url": "file:///etc/passwd"}, companion_config)


def test_run_command_never_interprets_shell_metacharacters(companion_config, tmp_path):
    """Un ';' en el comando no debe encadenar un segundo proceso: siempre shell=False.

    Antes usaba ``echo``/``touch`` -- funcionan en macOS/Linux, pero NINGUNO
    de los dos existe como ejecutable independiente en Windows real (``echo``
    es un builtin de ``cmd.exe`` sin archivo propio; no hay ``touch.exe`` de
    serie), así que este test ni llegaba a probar lo que dice probar ahí:
    fallaba antes, con ``FileNotFoundError`` al lanzar ``echo``, confirmado
    contra la VM de este proyecto (ver ``docs/opencode-windows.md``).
    ``sys.executable`` sí es portable (mismo criterio que ya usan los tests
    vecinos de este archivo) y sirve igual de bien como vehículo: la
    comprobación real -- que ``;``/``touch`` nunca llegan a una shell que
    los interprete, solo como argv extra e inofensivo de UN SOLO proceso --
    queda intacta y ahora sí corre en las tres plataformas."""

    marker = tmp_path / "should_not_exist.txt"
    companion_config.allowed_commands.append(sys.executable)

    result = actions._run_command(
        {"command": f"{sys.executable} -c \"print('hola;')\" ; touch {marker}"},
        companion_config,
    )

    assert not marker.exists()
    assert "hola;" in result["stdout"]  # el ";" llegó como texto literal, no como separador


# --------------------------------------------------------------------------- #
# Cierre de Windows -- mismo bug ya medido en edecan_mcp.transport y en
# ide_opencode_binario: CreateProcess (lo que subprocess.run con shell=False
# usa por debajo en Windows) no sabe lanzar un guion por lotes (.cmd/.bat,
# el shim típico de npm) aunque shutil.which sí lo encuentre. Estos tests
# prueban _argv_para_windows como lógica pura (monkeypatch de sys.platform y
# de shutil.which) -- no hay Windows real en esta Mac para confirmarlo en
# vivo, ver docs/opencode-windows.md.
# --------------------------------------------------------------------------- #


def test_argv_para_windows_en_posix_no_toca_nada(monkeypatch):
    monkeypatch.setattr(actions.sys, "platform", "darwin")
    assert actions._argv_para_windows(["npm", "install"]) == ["npm", "install"]


def test_argv_para_windows_envuelve_un_shim_cmd_en_cmd_exe(monkeypatch):
    monkeypatch.setattr(actions.sys, "platform", "win32")
    monkeypatch.setattr(actions.shutil, "which", lambda nombre: r"C:\nodejs\npm.cmd")
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")

    assert actions._argv_para_windows(["npm", "install"]) == [
        r"C:\Windows\System32\cmd.exe",
        "/c",
        r"C:\nodejs\npm.cmd",
        "install",
    ]


def test_argv_para_windows_con_exe_nativo_usa_la_ruta_resuelta_sin_envolver(monkeypatch):
    monkeypatch.setattr(actions.sys, "platform", "win32")
    monkeypatch.setattr(actions.shutil, "which", lambda nombre: r"C:\Python312\python.exe")

    assert actions._argv_para_windows(["python", "-c", "1"]) == [
        r"C:\Python312\python.exe",
        "-c",
        "1",
    ]


def test_argv_para_windows_comando_sin_resolver_se_deja_tal_cual(monkeypatch):
    """El caso ``echo``: un builtin de la shell de Windows que no tiene
    archivo propio -- ``shutil.which`` no lo encuentra, y sin ``shell=True``
    no hay forma segura de correrlo, así que se deja igual (fallará más
    abajo con el mismo ``FileNotFoundError`` claro de siempre)."""

    monkeypatch.setattr(actions.sys, "platform", "win32")
    monkeypatch.setattr(actions.shutil, "which", lambda nombre: None)

    assert actions._argv_para_windows(["echo", "hola"]) == ["echo", "hola"]


def test_run_command_en_windows_pasa_por_argv_para_windows(companion_config, monkeypatch):
    """Confirma que ``_run_command`` de verdad usa ``_argv_para_windows`` --
    y que la comprobación de ``allowed_commands`` sigue siendo contra el
    nombre literal que puso el dueño (``npm``), no contra la ruta resuelta
    (``npm.cmd``)."""

    companion_config.allowed_commands.append("npm")
    monkeypatch.setattr(actions.sys, "platform", "win32")
    monkeypatch.setattr(actions.shutil, "which", lambda nombre: r"C:\nodejs\npm.cmd")
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")

    llamadas: list[list[str]] = []

    def _run_falso(argv, **kwargs):
        llamadas.append(argv)
        return subprocess.CompletedProcess(argv, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(actions.subprocess, "run", _run_falso)

    resultado = actions._run_command({"command": "npm --version"}, companion_config)

    assert resultado["returncode"] == 0
    assert llamadas == [[r"C:\Windows\System32\cmd.exe", "/c", r"C:\nodejs\npm.cmd", "--version"]]


def test_run_command_truncates_long_output(companion_config, monkeypatch):
    monkeypatch.setattr(actions, "MAX_COMMAND_OUTPUT_BYTES", 10)
    companion_config.allowed_commands.append(sys.executable)

    result = actions._run_command(
        {"command": f"{sys.executable} -c \"print('x' * 1000)\""}, companion_config
    )

    assert result["truncated"] is True
    assert len(result["stdout"].encode("utf-8")) <= 10


def test_run_command_empty_command_raises(companion_config):
    with pytest.raises(actions.ActionError):
        actions._run_command({"command": "   "}, companion_config)


def test_run_command_missing_param_raises(companion_config):
    with pytest.raises(actions.ActionError, match="command"):
        actions._run_command({}, companion_config)


def test_run_command_runs_with_cwd_pinned_to_sandbox(companion_config):
    companion_config.allowed_commands.append(sys.executable)
    (companion_config.sandbox_dir / "marker.txt").write_text("x")

    result = actions._run_command(
        {"command": f'{sys.executable} -c "import os; print(os.getcwd())"'}, companion_config
    )

    assert result["stdout"].strip() == str(companion_config.sandbox_dir)


def test_clipboard_actions_reject_unsupported_platform(companion_config, monkeypatch):
    monkeypatch.setattr(actions.sys, "platform", "win32")

    with pytest.raises(actions.ActionError, match="no soportado"):
        actions._clipboard_get({}, companion_config)

    with pytest.raises(actions.ActionError, match="no soportado"):
        actions._clipboard_set({"text": "hola"}, companion_config)


def test_clipboard_set_requires_text_param(companion_config):
    with pytest.raises(actions.ActionError, match="text"):
        actions._clipboard_set({}, companion_config)


# ---------------------------------------------------------------------------
# Portapapeles en Linux -- X11 (xclip) vs Wayland (wl-clipboard), y el
# "hallazgo nuevo" de la medición en vivo: `xclip`/`wl-copy` se demonizan y
# heredan las tuberías de stdout/stderr de `capture_output=True`, así que
# `subprocess.run` se queda esperando EOF hasta el timeout aunque la copia
# ya haya funcionado. Estas pruebas fijan que `_clipboard_set` en Linux
# NUNCA usa PIPE para stdout/stderr (la causa raíz del cuelgue).
# ---------------------------------------------------------------------------


def test_clipboard_argv_uses_xclip_on_x11(monkeypatch):
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert actions._linux_clipboard_argv(leer=True) == ["xclip", "-selection", "clipboard", "-o"]
    assert actions._linux_clipboard_argv(leer=False) == ["xclip", "-selection", "clipboard"]


def test_clipboard_argv_uses_wl_clipboard_on_wayland(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert actions._linux_clipboard_argv(leer=True) == ["wl-paste", "--no-newline"]
    assert actions._linux_clipboard_argv(leer=False) == ["wl-copy"]


def test_clipboard_set_on_linux_never_pipes_stdout_or_stderr(companion_config, monkeypatch):
    """La causa raíz del cuelgue medido en vivo: `capture_output=True` (o
    cualquier PIPE de stdout/stderr) hereda hacia el demonio de xclip/wl-copy
    y nunca ve EOF. Confirma que la llamada real nunca pasa PIPE."""
    monkeypatch.setattr(actions.sys, "platform", "linux")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    llamadas = []

    def _run_falso(argv, **kwargs):
        llamadas.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, returncode=0)

    monkeypatch.setattr(actions.subprocess, "run", _run_falso)

    resultado = actions._clipboard_set({"text": "hola mundo"}, companion_config)

    assert resultado == {"written_chars": len("hola mundo")}
    assert len(llamadas) == 1
    argv, kwargs = llamadas[0]
    assert argv == ["xclip", "-selection", "clipboard"]
    assert kwargs["stdout"] is actions.subprocess.DEVNULL
    assert kwargs["stderr"] != actions.subprocess.PIPE
    assert not kwargs.get("capture_output")


def test_clipboard_set_on_linux_surfaces_stderr_on_failure(companion_config, monkeypatch):
    monkeypatch.setattr(actions.sys, "platform", "linux")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    def _run_falso(argv, **kwargs):
        stderr_file = kwargs["stderr"]
        stderr_file.write(b"Error: no hay selecci\xc3\xb3n X11 disponible")
        stderr_file.flush()
        raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(actions.subprocess, "run", _run_falso)

    with pytest.raises(actions.ActionError, match="selecci"):
        actions._clipboard_set({"text": "hola"}, companion_config)


def test_clipboard_set_on_linux_reports_missing_tool_with_the_right_package(
    companion_config, monkeypatch
):
    monkeypatch.setattr(actions.sys, "platform", "linux")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")

    def _run_falso(argv, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", argv[0])

    monkeypatch.setattr(actions.subprocess, "run", _run_falso)

    with pytest.raises(actions.ActionError, match="wl-clipboard"):
        actions._clipboard_set({"text": "hola"}, companion_config)


def test_open_app_rejects_app_not_in_allowlist(companion_config):
    with pytest.raises(actions.ActionError, match="no permitida"):
        actions._open_app({"app": "Safari"}, companion_config)


def test_open_app_allow_all_apps_opt_in_usa_open_sin_shell(companion_config, monkeypatch):
    llamadas = []
    companion_config.allow_all_apps = True
    monkeypatch.setattr(actions.sys, "platform", "darwin")

    def _run_falso(argv, **kwargs):
        llamadas.append((argv, kwargs))

    monkeypatch.setattr(actions.subprocess, "run", _run_falso)

    resultado = actions._open_app({"app": "TeamViewer"}, companion_config)

    assert resultado == {"app": "TeamViewer", "launched": True}
    assert llamadas[0][0] == ["open", "-a", "TeamViewer"]
    assert llamadas[0][1]["check"] is True


def test_open_app_requires_app_param(companion_config):
    with pytest.raises(actions.ActionError, match="app"):
        actions._open_app({}, companion_config)


def test_open_app_rejects_unsupported_platform(companion_config, monkeypatch):
    companion_config.allowed_apps.append("Safari")
    monkeypatch.setattr(actions.sys, "platform", "win32")

    with pytest.raises(actions.ActionError, match="no está soportado"):
        actions._open_app({"app": "Safari"}, companion_config)


# ---------------------------------------------------------------------------
# open_app en Linux -- resolución de .desktop y lanzamiento sin `xdg-open`.
#
# Antes usaba `xdg-open <nombre>`, que espera un archivo/URL, no un nombre de
# app: medido en vivo (edecan-prod, 1-ago-2026) se quedaba colgado el timeout
# completo Y dejaba un diálogo `exo-open` huérfano en pantalla en cada
# intento. Estas pruebas cubren el reemplazo: resolución del `.desktop` (por
# nombre de archivo o por `Name=`) y lanzamiento vía `Popen` sin esperar.
#
# `_FakeProc` imita el `Popen` real lo justo para que `_open_app` pueda
# `.poll()` lo -- la comprobación corta que atrapa el hallazgo medido en vivo
# de "murió al instante y de todos modos dijo launched: True" (ver
# `LINUX_OPEN_APP_POLL_SECONDS`). `monkeypatch` sobre `actions.time.sleep`
# quita la espera real de 300ms en cada prueba sin cambiar el camino que se
# ejecuta.
# ---------------------------------------------------------------------------


class _FakeProc:
    def __init__(self, returncode: int | None = None) -> None:
        self.returncode = returncode

    def poll(self) -> int | None:
        return self.returncode


def _escribir_desktop_entry(carpeta, nombre_archivo: str, *, name: str, exec_line: str) -> None:
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / nombre_archivo).write_text(
        f"[Desktop Entry]\nType=Application\nName={name}\nExec={exec_line}\n",
        encoding="utf-8",
    )


def test_open_app_on_linux_finds_desktop_entry_by_filename_stem(
    companion_config, monkeypatch, tmp_path
):
    companion_config.allowed_apps.append("xfce4-terminal")
    monkeypatch.setattr(actions.sys, "platform", "linux")
    monkeypatch.setattr(actions.time, "sleep", lambda _segundos: None)
    carpeta = tmp_path / "applications"
    _escribir_desktop_entry(
        carpeta, "xfce4-terminal.desktop", name="Xfce Terminal", exec_line="xfce4-terminal"
    )
    monkeypatch.setattr(actions, "_linux_desktop_search_dirs", lambda: [carpeta])
    llamadas = []

    def _popen_falso(argv, **kwargs):
        llamadas.append((argv, kwargs))
        return _FakeProc(returncode=None)  # sigue vivo tras la ventana de poll

    monkeypatch.setattr(actions.subprocess, "Popen", _popen_falso)

    resultado = actions._open_app({"app": "xfce4-terminal"}, companion_config)

    assert resultado == {"app": "xfce4-terminal", "launched": True}
    assert len(llamadas) == 1
    argv, kwargs = llamadas[0]
    assert argv == ["xfce4-terminal"]
    assert kwargs["stdout"] is actions.subprocess.DEVNULL
    assert kwargs["start_new_session"] is True


def test_open_app_on_linux_finds_desktop_entry_by_display_name(
    companion_config, monkeypatch, tmp_path
):
    """El dueño pide 'Visual Studio Code' (nombre visible del menú), no
    'code' (el id técnico del .desktop) -- debe encontrarlo por `Name=`."""
    companion_config.allowed_apps.append("Visual Studio Code")
    monkeypatch.setattr(actions.sys, "platform", "linux")
    monkeypatch.setattr(actions.time, "sleep", lambda _segundos: None)
    carpeta = tmp_path / "applications"
    _escribir_desktop_entry(
        carpeta, "code.desktop", name="Visual Studio Code", exec_line="/usr/bin/code %F"
    )
    monkeypatch.setattr(actions, "_linux_desktop_search_dirs", lambda: [carpeta])
    llamadas = []

    def _popen_falso(argv, **kwargs):
        llamadas.append((argv, kwargs))
        return _FakeProc(returncode=None)

    monkeypatch.setattr(actions.subprocess, "Popen", _popen_falso)

    actions._open_app({"app": "Visual Studio Code"}, companion_config)

    assert llamadas[0][0] == ["/usr/bin/code"]  # el %F (código de campo) se quita


def test_open_app_on_linux_reports_which_dirs_it_searched_when_not_found(
    companion_config, monkeypatch, tmp_path
):
    companion_config.allowed_apps.append("no-existe")
    monkeypatch.setattr(actions.sys, "platform", "linux")
    monkeypatch.setattr(actions, "_linux_desktop_search_dirs", lambda: [tmp_path / "vacia"])

    with pytest.raises(actions.ActionError, match="no encontré un lanzador"):
        actions._open_app({"app": "no-existe"}, companion_config)


def test_open_app_on_linux_does_not_wait_for_the_launched_process(
    companion_config, monkeypatch, tmp_path
):
    """Antes (xdg-open) esperaba hasta el timeout completo. Ahora usa
    `Popen` sin esperar -- confirma que `subprocess.run`/`.wait()` no se
    llaman en ningún momento del camino feliz."""
    companion_config.allowed_apps.append("firefox")
    monkeypatch.setattr(actions.sys, "platform", "linux")
    monkeypatch.setattr(actions.time, "sleep", lambda _segundos: None)
    carpeta = tmp_path / "applications"
    _escribir_desktop_entry(carpeta, "firefox.desktop", name="Firefox", exec_line="firefox")
    monkeypatch.setattr(actions, "_linux_desktop_search_dirs", lambda: [carpeta])

    def _run_no_deberia_llamarse(*_args, **_kwargs):
        raise AssertionError("open_app en Linux no debe esperar al proceso lanzado")

    monkeypatch.setattr(actions.subprocess, "run", _run_no_deberia_llamarse)
    monkeypatch.setattr(actions.subprocess, "Popen", lambda argv, **kwargs: _FakeProc(None))

    actions._open_app({"app": "firefox"}, companion_config)


def test_open_app_on_linux_reports_process_that_dies_instantly(
    companion_config, monkeypatch, tmp_path
):
    """El hallazgo medido en vivo: `Exec=` que muere al instante (código != 0)
    -- p. ej. un binario ausente o un `$DISPLAY` inválido -- ya NO reporta
    `launched: True` a ciegas."""
    companion_config.allowed_apps.append("appquefalla")
    monkeypatch.setattr(actions.sys, "platform", "linux")
    monkeypatch.setattr(actions.time, "sleep", lambda _segundos: None)
    carpeta = tmp_path / "applications"
    _escribir_desktop_entry(carpeta, "appquefalla.desktop", name="Rota", exec_line="/bin/false")
    monkeypatch.setattr(actions, "_linux_desktop_search_dirs", lambda: [carpeta])

    def _popen_falso(argv, stderr=None, **kwargs):
        if stderr is not None:
            stderr.write(b"no puedo abrir la ventana")
            stderr.flush()
        return _FakeProc(returncode=1)

    monkeypatch.setattr(actions.subprocess, "Popen", _popen_falso)

    with pytest.raises(actions.ActionError, match="terminó de inmediato"):
        actions._open_app({"app": "appquefalla"}, companion_config)


def test_open_app_on_linux_still_reports_success_when_process_stays_alive(
    companion_config, monkeypatch, tmp_path
):
    """Un proceso que sigue vivo tras la ventana corta de poll se reporta
    como lanzado -- la comprobación no reintroduce el cuelgue viejo."""
    companion_config.allowed_apps.append("firefox")
    monkeypatch.setattr(actions.sys, "platform", "linux")
    monkeypatch.setattr(actions.time, "sleep", lambda _segundos: None)
    carpeta = tmp_path / "applications"
    _escribir_desktop_entry(carpeta, "firefox.desktop", name="Firefox", exec_line="firefox")
    monkeypatch.setattr(actions, "_linux_desktop_search_dirs", lambda: [carpeta])
    monkeypatch.setattr(actions.subprocess, "Popen", lambda argv, **kwargs: _FakeProc(None))

    resultado = actions._open_app({"app": "firefox"}, companion_config)

    assert resultado == {"app": "firefox", "launched": True}
