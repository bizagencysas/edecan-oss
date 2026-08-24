"""Tests de `edecan_companion.linux_session` -- descubrimiento de DISPLAY/DBUS.

Construye un árbol de directorios real bajo `tmp_path` que imita `/proc` y
apunta `linux_session._PROC_ROOT` ahí (ver el comentario de ese nombre en el
módulo): así se ejercita la lógica de lectura real de archivos sin tocar el
`/proc` de la máquina que corre la suite ni monkeypatchear métodos de `Path`
a nivel de clase (que afectaría a cualquier otro uso de `Path` durante el
test, incluido el propio pytest).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from edecan_companion import linux_session


def _crear_proceso(
    proc_root: Path, pid: str, *, comm: str, uid: int, environ: dict[str, str]
) -> None:
    carpeta = proc_root / pid
    carpeta.mkdir(parents=True)
    (carpeta / "comm").write_text(comm + "\n", encoding="utf-8")
    (carpeta / "environ").write_bytes(
        b"\x00".join(f"{clave}={valor}".encode() for clave, valor in environ.items()) + b"\x00"
    )
    # `st_uid` no se puede fijar escribiendo un archivo normal (el dueño real
    # es quien corre la suite) -- se guarda aparte y `_stat_falso` lo lee.
    (carpeta / ".uid_de_prueba").write_text(str(uid), encoding="utf-8")


def _instalar_proc_falso(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, uid_propio: int
) -> Path:
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    monkeypatch.setattr(linux_session, "_PROC_ROOT", proc_root)
    monkeypatch.setattr(linux_session.os, "getuid", lambda: uid_propio, raising=False)

    stat_real = Path.stat

    def _stat_falso(self: Path, *args, **kwargs):
        marcador = self / ".uid_de_prueba"
        if self.parent == proc_root and marcador.exists():
            uid_de_prueba = int(marcador.read_text(encoding="utf-8"))

            class _StatFalso:
                st_uid = uid_de_prueba

            return _StatFalso()
        return stat_real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", _stat_falso)
    return proc_root


def test_descubrir_devuelve_vacio_si_ya_hay_display(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DISPLAY", ":0")
    assert linux_session.descubrir_variables_de_sesion() == {}


def test_descubrir_devuelve_vacio_si_ya_hay_wayland_display(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert linux_session.descubrir_variables_de_sesion() == {}


def test_descubrir_encuentra_display_del_gestor_de_sesion_del_mismo_usuario(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    proc_root = _instalar_proc_falso(monkeypatch, tmp_path, uid_propio=1000)
    _crear_proceso(
        proc_root,
        "4242",
        comm="xfce4-session",
        uid=1000,
        environ={
            "DISPLAY": ":10.0",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/dbus-abc",
            "SHELL": "/bin/bash",  # variable irrelevante -- no debe colarse
        },
    )

    encontradas = linux_session.descubrir_variables_de_sesion()

    assert encontradas == {
        "DISPLAY": ":10.0",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/dbus-abc",
    }


def test_descubrir_ignora_procesos_de_otro_usuario(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    proc_root = _instalar_proc_falso(monkeypatch, tmp_path, uid_propio=1000)
    _crear_proceso(proc_root, "10", comm="Xorg", uid=0, environ={"DISPLAY": ":0"})

    assert linux_session.descubrir_variables_de_sesion() == {}


def test_descubrir_ignora_procesos_con_nombre_no_reconocido(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    proc_root = _instalar_proc_falso(monkeypatch, tmp_path, uid_propio=1000)
    _crear_proceso(proc_root, "99", comm="bash", uid=1000, environ={"DISPLAY": ":0"})

    assert linux_session.descubrir_variables_de_sesion() == {}


def test_descubrir_devuelve_vacio_sin_proc(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    # `_PROC_ROOT` apunta a una carpeta que no existe -- simula una
    # plataforma sin `/proc` (o un chroot sin montarlo).
    monkeypatch.setattr(linux_session, "_PROC_ROOT", tmp_path / "no-existe")
    monkeypatch.setattr(linux_session.os, "getuid", lambda: 1000, raising=False)

    assert linux_session.descubrir_variables_de_sesion() == {}


def test_entorno_fusionado_no_pisa_lo_que_ya_esta_puesto(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        linux_session, "descubrir_variables_de_sesion", lambda: {"DISPLAY": ":99", "FOO": "bar"}
    )

    resultado = linux_session.entorno_fusionado({"DISPLAY": ":0", "OTRO": "x"})

    assert resultado["DISPLAY"] == ":0"  # lo explícito manda
    assert resultado["FOO"] == "bar"  # lo descubierto rellena lo que faltaba
    assert resultado["OTRO"] == "x"


def test_xauthority_se_completa_desde_el_home_del_dueno_de_la_sesion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Sin `XAUTHORITY`, X11 rechaza la conexión aunque `DISPLAY` sea correcto.

    xrdp (y varios gestores más) NO publican `XAUTHORITY`: los clientes de X
    caen por convención a `$HOME/.Xauthority`. Eso deja de funcionar en cuanto
    Edecán corre como servicio con otro `HOME` -- el caso real que motivó esto.
    Por eso el home se resuelve del UID de la sesión, nunca de `$HOME`.
    """
    proc_root = _instalar_proc_falso(monkeypatch, tmp_path, uid_propio=1000)
    _crear_proceso(
        proc_root,
        "410",
        comm="xfce4-session",
        uid=1000,
        environ={"DISPLAY": ":10.0"},  # como xrdp: sin XAUTHORITY
    )
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    home = tmp_path / "home-del-dueno"
    home.mkdir()
    (home / ".Xauthority").write_bytes(b"cookie-falsa")

    class _Passwd:
        pw_dir = str(home)

    import pwd

    monkeypatch.setattr(pwd, "getpwuid", lambda uid: _Passwd())

    encontradas = linux_session.descubrir_variables_de_sesion()
    assert encontradas["DISPLAY"] == ":10.0"
    assert encontradas["XAUTHORITY"] == str(home / ".Xauthority")


def test_xauthority_no_se_inventa_si_el_archivo_no_existe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Apuntar a un `.Xauthority` inexistente sería peor que no ponerla:
    X11 fallaría igual pero con un error que despista."""
    proc_root = _instalar_proc_falso(monkeypatch, tmp_path, uid_propio=1000)
    _crear_proceso(
        proc_root, "411", comm="xfce4-session", uid=1000, environ={"DISPLAY": ":10.0"}
    )
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    class _Passwd:
        pw_dir = str(tmp_path / "home-sin-xauthority")

    import pwd

    monkeypatch.setattr(pwd, "getpwuid", lambda uid: _Passwd())

    assert "XAUTHORITY" not in linux_session.descubrir_variables_de_sesion()
