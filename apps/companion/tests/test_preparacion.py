"""Tests de `edecan_companion.preparacion` (pantalla de preparación de Windows).

Corren en macOS/Linux (donde vive este repo) igual que el resto de la
suite; la rama "detección real en una PC Windows" no se puede ejecutar aquí
-- ver `docs/edecan-windows.md`, "Regla de evidencia". Lo que SÍ se puede
fijar desde acá es el CONTRATO: con `_plataforma_soportada`/`shutil.which`/
los lectores de registro mockeados (mismo espíritu que ya usa
`test_ide_runtime.py::test_default_terminal_windows_prefiere_pwsh`, pero sin
tocar el `os.name` real del proceso -- ver el docstring de
`preparacion._plataforma_soportada` sobre por qué: mutarlo de verdad se
filtraría a `pty_compat.abrir_pty`, que también decide por `os.name`), una
regresión futura revienta este test antes de llegar a Windows.

Tres grupos:
- `detectar()`: dobles por requisito, nunca lanza, lista vacía fuera de
  Windows.
- El manifiesto en sí: ids únicos, `instalar` fijo y auditable.
- `EjecutorPreparacion`: rechaza un `id` desconocido (la regla de seguridad
  central del módulo), reporta "requiere admin" con claridad y ANTES de
  tocar `pty_compat`, y corre un `instalar` real de principio a fin sobre un
  requisito de prueba (POSIX, con `sys.executable` en vez de `winget`).
"""

from __future__ import annotations

import sys
import time

import pytest
from edecan_companion import preparacion


def _which_mock(disponibles: dict[str, str]):
    """`shutil.which` falso -- mismo patrón que
    `test_ide_runtime._which_windows_mock`."""

    def _which(cmd: str) -> str | None:
        return disponibles.get(cmd)

    return _which


# ---------------------------------------------------------------------------
# detectar()
# ---------------------------------------------------------------------------


def test_detectar_returns_empty_list_outside_windows(monkeypatch: pytest.MonkeyPatch):
    assert preparacion.detectar() == []


def test_detectar_reports_every_requisito_with_dobles(monkeypatch: pytest.MonkeyPatch):
    """Con cada comprobación mockeada a "todo falta", `detectar()` en Windows
    debe reportar las cinco filas del manifiesto, en orden, con la forma
    exacta que consume el router y el frontend."""
    monkeypatch.setattr(preparacion, "_plataforma_soportada", lambda: True)
    monkeypatch.setattr(preparacion, "_build_de_windows", lambda: 10240)  # anterior a 1809
    monkeypatch.setattr(preparacion.shutil, "which", _which_mock({}))
    monkeypatch.setattr(preparacion, "_leer_long_paths_enabled", lambda: 0)
    monkeypatch.setattr(preparacion, "_version_webview2_instalada", lambda: None)

    filas = preparacion.detectar()

    assert [fila["id"] for fila in filas] == [
        "windows_version",
        "powershell7",
        "rutas_largas",
        "webview2",
        "git",
    ]
    assert all(fila["estado"] == "falta" for fila in filas)
    por_id = {fila["id"]: fila for fila in filas}
    assert por_id["windows_version"]["instalable"] is False
    assert por_id["windows_version"]["obligatorio"] is True
    assert por_id["powershell7"]["instalable"] is True
    assert por_id["powershell7"]["requiere_admin"] is True
    assert por_id["powershell7"]["obligatorio"] is False
    assert por_id["rutas_largas"]["requiere_admin"] is True
    assert por_id["rutas_largas"]["obligatorio"] is False
    assert por_id["webview2"]["obligatorio"] is True
    assert por_id["git"]["requiere_admin"] is False
    assert por_id["git"]["obligatorio"] is True
    assert all("por_que" in fila and fila["por_que"] for fila in filas)


def test_detectar_reports_cumplido_when_everything_is_satisfied(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(preparacion, "_plataforma_soportada", lambda: True)
    monkeypatch.setattr(preparacion, "_build_de_windows", lambda: 22631)
    monkeypatch.setattr(
        preparacion.shutil,
        "which",
        _which_mock({"pwsh.exe": "C:\\pwsh.exe", "git.exe": "C:\\git.exe"}),
    )
    monkeypatch.setattr(preparacion, "_leer_long_paths_enabled", lambda: 1)
    monkeypatch.setattr(preparacion, "_version_webview2_instalada", lambda: "120.0.1")

    filas = preparacion.detectar()

    assert all(fila["estado"] == "cumplido" for fila in filas)


def test_detectar_reports_desconocido_when_a_check_raises_never_bubbles_up(
    monkeypatch: pytest.MonkeyPatch,
):
    """Regla explícita del encargo: un requisito que no se puede comprobar se
    reporta como "desconocido", `detectar()` no revienta el arranque."""
    monkeypatch.setattr(preparacion, "_plataforma_soportada", lambda: True)
    monkeypatch.setattr(preparacion, "_build_de_windows", lambda: 22631)
    monkeypatch.setattr(preparacion.shutil, "which", _which_mock({}))

    def _explota() -> int | None:
        raise RuntimeError("registro inaccesible de una forma inesperada")

    monkeypatch.setattr(preparacion, "_leer_long_paths_enabled", _explota)
    monkeypatch.setattr(preparacion, "_version_webview2_instalada", lambda: None)

    filas = preparacion.detectar()

    por_id = {fila["id"]: fila for fila in filas}
    assert por_id["rutas_largas"]["estado"] == "desconocido"
    # El resto del manifiesto se sigue reportando con normalidad -- un
    # requisito roto no debe tumbar a los demás.
    assert por_id["windows_version"]["estado"] == "cumplido"


@pytest.mark.parametrize(
    "build,esperado",
    [
        (17762, "falta"),
        (17763, "cumplido"),
        (22631, "cumplido"),
    ],
)
def test_comprobar_version_windows_umbral_exacto(
    monkeypatch: pytest.MonkeyPatch, build: int, esperado: str
):
    monkeypatch.setattr(preparacion, "_build_de_windows", lambda: build)

    assert preparacion._comprobar_version_windows().value == esperado


def test_comprobar_version_windows_desconocido_sin_getwindowsversion(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(preparacion, "_build_de_windows", lambda: None)

    assert preparacion._comprobar_version_windows() == preparacion.EstadoRequisito.DESCONOCIDO


# ---------------------------------------------------------------------------
# El manifiesto en sí -- ids únicos y comandos auditables.
# ---------------------------------------------------------------------------


def test_manifiesto_ids_are_unique():
    ids = [requisito.id for requisito in preparacion.REQUISITOS]
    assert len(ids) == len(set(ids))


def test_manifiesto_windows_version_has_no_automatic_install():
    """Documenta la decisión: no hay comando de CLI que actualice el SO con
    seguridad, así que este requisito nunca se ofrece con instalación
    automática."""
    requisito = preparacion._requisito_por_id("windows_version")
    assert requisito is not None
    assert requisito.instalar is None
    assert requisito.obligatorio is True


def test_manifiesto_every_installable_argv_is_a_fixed_list_of_nonempty_strings():
    for requisito in preparacion.REQUISITOS:
        if requisito.instalar is None:
            continue
        assert isinstance(requisito.instalar, list)
        assert requisito.instalar, f"{requisito.id}: argv vacío"
        assert all(isinstance(item, str) and item for item in requisito.instalar)
        # Regla de seguridad del módulo: el argv sale de winget o de
        # powershell fijo, nunca de un intérprete de comandos con
        # metacaracteres (docs/edecan-windows.md §3, "shell=True").
        assert requisito.instalar[0] in {"winget", "powershell"}


# ---------------------------------------------------------------------------
# EjecutorPreparacion
# ---------------------------------------------------------------------------


def test_ejecutor_listar_outside_windows_is_empty_and_not_elevated():
    # Esta suite corre en macOS/Linux -- la plataforma real ya no es "nt",
    # así que no hace falta mockear nada para ejercitar esta rama.
    resultado = preparacion.EjecutorPreparacion().listar()

    assert resultado == {"requisitos": [], "elevado": False}


def test_ejecutor_instalar_rejects_unknown_id_before_touching_the_system(
    monkeypatch: pytest.MonkeyPatch,
):
    """La regla de seguridad central del módulo: un `id` que no está en
    `REQUISITOS` nunca llega a `pty_compat.abrir_pty` -- se prueba
    envenenando esa función para que reviente si alguien llega a llamarla."""
    monkeypatch.setattr(preparacion, "_plataforma_soportada", lambda: True)

    def _no_deberia_llamarse(*_args, **_kwargs):
        raise AssertionError("abrir_pty no debía llamarse para un id desconocido")

    monkeypatch.setattr(preparacion.pty_compat, "abrir_pty", _no_deberia_llamarse)

    ejecutor = preparacion.EjecutorPreparacion()

    with pytest.raises(preparacion.PreparacionError, match="desconocido"):
        ejecutor.instalar("no-existe-en-el-manifiesto")


def test_ejecutor_leer_rejects_unknown_id(monkeypatch: pytest.MonkeyPatch):
    ejecutor = preparacion.EjecutorPreparacion()

    with pytest.raises(preparacion.PreparacionError, match="desconocido"):
        ejecutor.leer("no-existe-en-el-manifiesto", 0)


def test_ejecutor_instalar_outside_windows_refuses_even_a_known_id():
    ejecutor = preparacion.EjecutorPreparacion()

    with pytest.raises(preparacion.PreparacionError, match="Windows"):
        ejecutor.instalar("git")


def test_ejecutor_instalar_requisito_sin_automatizacion_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(preparacion, "_plataforma_soportada", lambda: True)
    ejecutor = preparacion.EjecutorPreparacion()

    with pytest.raises(preparacion.PreparacionError, match="instalación automática"):
        ejecutor.instalar("windows_version")


def test_ejecutor_instalar_requires_admin_reports_clearly_without_spawning_a_process(
    monkeypatch: pytest.MonkeyPatch,
):
    """"Lo que necesita administrador se marca antes de pulsar play, no
    después de fallar": si ya se pulsó play sin ser administrador, el
    resultado tiene que ser un mensaje claro y sincrónico, nunca un intento
    de instalación que cuelgue a medias."""
    monkeypatch.setattr(preparacion, "_plataforma_soportada", lambda: True)
    monkeypatch.setattr(preparacion, "_es_administrador", lambda: False)

    def _no_deberia_llamarse(*_args, **_kwargs):
        raise AssertionError("abrir_pty no debía llamarse sin permisos de administrador")

    monkeypatch.setattr(preparacion.pty_compat, "abrir_pty", _no_deberia_llamarse)

    ejecutor = preparacion.EjecutorPreparacion()
    resultado = ejecutor.instalar("powershell7")  # requiere_admin=True

    assert resultado["estado"] == "error"
    assert "administrador" in (resultado["error"] or "").lower()

    leido = ejecutor.leer("powershell7", 0)
    assert leido["estado"] == "error"
    assert any("administrador" in evento["text"].lower() for evento in leido["events"])


def _requisito_de_prueba(*, comprobar) -> preparacion.Requisito:
    """Un requisito con un `instalar` real y seguro de ejecutar en esta
    máquina (POSIX): el propio intérprete de Python, nunca `winget`."""
    return preparacion.Requisito(
        id="fake",
        nombre="Requisito de prueba",
        por_que="Ejercita EjecutorPreparacion de punta a punta sin depender de winget real.",
        comprobar=comprobar,
        instalar=[sys.executable, "-c", "print('hola desde el instalador de prueba')"],
        requiere_admin=False,
        obligatorio=True,
    )


def _esperar_estado_final(ejecutor: preparacion.EjecutorPreparacion, requisito_id: str) -> dict:
    limite = time.monotonic() + 5.0
    leido = ejecutor.leer(requisito_id, 0)
    while leido["estado"] == "ejecutando" and time.monotonic() < limite:
        time.sleep(0.02)
        leido = ejecutor.leer(requisito_id, 0)
    return leido


def test_ejecutor_instalar_runs_the_fixed_argv_and_streams_output_to_completion(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(preparacion, "_plataforma_soportada", lambda: True)
    fake = _requisito_de_prueba(comprobar=lambda: preparacion.EstadoRequisito.CUMPLIDO)
    monkeypatch.setattr(preparacion, "REQUISITOS", (fake,))
    ejecutor = preparacion.EjecutorPreparacion()

    snapshot = ejecutor.instalar("fake")
    assert snapshot["estado"] in {"ejecutando", "completado"}

    leido = _esperar_estado_final(ejecutor, "fake")

    assert leido["estado"] == "completado"
    assert any("hola desde el instalador de prueba" in evento["text"] for evento in leido["events"])


def test_ejecutor_instalar_reports_error_when_the_check_still_fails_after_running(
    monkeypatch: pytest.MonkeyPatch,
):
    """El código de salida 0 no basta: se vuelve a comprobar el requisito de
    verdad antes de anunciar éxito (un instalador puede "terminar bien" sin
    haber cambiado nada)."""
    monkeypatch.setattr(preparacion, "_plataforma_soportada", lambda: True)
    fake = _requisito_de_prueba(comprobar=lambda: preparacion.EstadoRequisito.FALTA)
    monkeypatch.setattr(preparacion, "REQUISITOS", (fake,))
    ejecutor = preparacion.EjecutorPreparacion()

    ejecutor.instalar("fake")
    leido = _esperar_estado_final(ejecutor, "fake")

    assert leido["estado"] == "error"
    assert "sigue sin cumplirse" in (leido["error"] or "")


def test_ejecutor_instalar_is_idempotent_while_already_running(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(preparacion, "_plataforma_soportada", lambda: True)
    lento = preparacion.Requisito(
        id="fake-lento",
        nombre="Requisito lento de prueba",
        por_que="Ejercita que una segunda llamada no relanza el proceso.",
        comprobar=lambda: preparacion.EstadoRequisito.CUMPLIDO,
        instalar=[sys.executable, "-c", "import time; time.sleep(0.5)"],
        requiere_admin=False,
        obligatorio=True,
    )
    monkeypatch.setattr(preparacion, "REQUISITOS", (lento,))
    ejecutor = preparacion.EjecutorPreparacion()

    ejecutor.instalar("fake-lento")
    primera_instalacion = ejecutor._instalaciones["fake-lento"]
    segunda_respuesta = ejecutor.instalar("fake-lento")

    assert segunda_respuesta["estado"] == "ejecutando"
    assert ejecutor._instalaciones["fake-lento"] is primera_instalacion

    _esperar_estado_final(ejecutor, "fake-lento")


def test_ejecutor_leer_before_ever_installing_reports_no_iniciado():
    ejecutor = preparacion.EjecutorPreparacion()

    leido = ejecutor.leer("git", 0)

    assert leido == {
        "id": "git",
        "estado": "no_iniciado",
        "error": None,
        "events": [],
        "next_cursor": 0,
        "has_more": False,
    }
