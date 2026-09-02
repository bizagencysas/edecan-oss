"""Tests de `edecan_browser._driver_macos` (re-firma JIT del driver en frozen).

`asegurar_driver_playwright_macos` es best-effort por contrato: estos tests
verifican el GUARD (solo actúa en macOS frozen), la precisión del diagnóstico
(solo re-firma el estado letal: hardened runtime SIN `allow-jit`; no toca un
binario ad-hoc sin runtime — el del build actual — ni uno ya apto), que la
re-firma invoque `codesign` con los argumentos correctos y que NINGÚN fallo
del proceso de firma escape (el launch de Playwright degrada por su propio
camino). Nunca se invoca `codesign` real: todo se simula con `monkeypatch`,
igual que el resto del paquete evita red y Chromium reales.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from edecan_browser import _driver_macos
from edecan_browser._driver_macos import (
    _driver_node_path,
    _necesita_refirma,
    asegurar_driver_playwright_macos,
)


def _congelado(monkeypatch, *, darwin: bool = True) -> None:
    monkeypatch.setattr(sys, "platform", "darwin" if darwin else "linux")
    monkeypatch.setattr(sys, "frozen", True, raising=False)


def test_no_hace_nada_fuera_de_frozen(monkeypatch):
    # En dev (sin `sys.frozen`) jamás se toca el driver, aunque el paquete esté.
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    corridas = []

    def _falso_run(cmd, **kwargs):
        corridas.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(_driver_macos.subprocess, "run", _falso_run)
    asegurar_driver_playwright_macos()
    assert corridas == []


def test_no_hace_nada_fuera_de_macos(monkeypatch):
    _congelado(monkeypatch, darwin=False)
    corridas = []

    def _falso_run(cmd, **kwargs):
        corridas.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(_driver_macos.subprocess, "run", _falso_run)
    asegurar_driver_playwright_macos()
    assert corridas == []


def test_no_toca_driver_adhoc_sin_runtime(monkeypatch):
    """El estado del build actual (ad-hoc, SIN hardened runtime) corre bien:
    el helper NO debe re-firmarlo."""
    _congelado(monkeypatch)
    monkeypatch.setattr(_driver_macos, "_driver_node_path", lambda: Path("/falso/node"))
    monkeypatch.setattr(
        _driver_macos,
        "_necesita_refirma",
        lambda _nodo: False,
    )
    corridas = []

    def _falso_run(cmd, **kwargs):
        corridas.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(_driver_macos.subprocess, "run", _falso_run)
    asegurar_driver_playwright_macos()
    assert corridas == []


def test_re_firma_estado_letal_runtime_sin_jit(monkeypatch):
    _congelado(monkeypatch)
    nodo = Path("/falso/_MEI1234/playwright/driver/node")
    monkeypatch.setattr(_driver_macos, "_driver_node_path", lambda: nodo)
    monkeypatch.setattr(_driver_macos, "_necesita_refirma", lambda _nodo: True)
    capturadas: list[tuple[list[str], str]] = []

    def _falso_run(cmd, **kwargs):
        # La plist se lee AHORA: el helper la borra al salir (finally), así
        # que fuera del mock ya no existiría.
        plist = Path(cmd[cmd.index("--entitlements") + 1])
        capturadas.append((list(cmd), plist.read_text()))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(_driver_macos.subprocess, "run", _falso_run)
    asegurar_driver_playwright_macos()

    assert len(capturadas) == 1
    cmd, contenido_plist = capturadas[0]
    assert cmd[0] == "codesign"
    # La re-firma es ad-hoc (--sign -), mantiene hardened runtime y una plist
    # de entitlements que concede JIT.
    assert "--force" in cmd and "-" in cmd
    assert cmd[cmd.index("--options") + 1] == "runtime"
    assert "allow-jit" in contenido_plist


def test_fallo_de_codesign_no_se_propaga(monkeypatch):
    _congelado(monkeypatch)
    monkeypatch.setattr(_driver_macos, "_driver_node_path", lambda: Path("/falso/node"))
    monkeypatch.setattr(_driver_macos, "_necesita_refirma", lambda _nodo: True)
    monkeypatch.setattr(
        _driver_macos.subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 1, "", "firma rechazada"),
    )
    asegurar_driver_playwright_macos()  # no lanza


def test_codesign_ausente_no_se_propaga(monkeypatch):
    _congelado(monkeypatch)
    monkeypatch.setattr(_driver_macos, "_driver_node_path", lambda: Path("/falso/node"))
    monkeypatch.setattr(_driver_macos, "_necesita_refirma", lambda _nodo: True)
    monkeypatch.setattr(
        _driver_macos.subprocess,
        "run",
        lambda cmd, **kwargs: (_ for _ in ()).throw(FileNotFoundError("no codesign")),
    )
    asegurar_driver_playwright_macos()  # no lanza


def test_necesita_refirma_lee_flags_y_entitlements(monkeypatch):
    nodo = Path("/falso/node")

    def _salida(returncode: int, stdout: str, stderr: str):
        def _run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)

        return _run

    # Estado letal: hardened runtime (bit 0x10000) sin allow-jit.
    monkeypatch.setattr(
        _driver_macos.subprocess,
        "run",
        _salida(0, "flags=0x10000(runtime) hashes=7325+2\n", ""),
    )
    assert _necesita_refirma(nodo) is True

    # El mismo runtime CON allow-jit está sano.
    monkeypatch.setattr(
        _driver_macos.subprocess,
        "run",
        _salida(0, "flags=0x10002(runtime)\n", "com.apple.security.cs.allow-jit\n"),
    )
    assert _necesita_refirma(nodo) is False

    # Ad-hoc sin runtime (estado del build actual): sano, no se toca.
    monkeypatch.setattr(
        _driver_macos.subprocess,
        "run",
        _salida(0, "flags=0x2(adhoc) hashes=7325+2\n", ""),
    )
    assert _necesita_refirma(nodo) is False

    # Sin firma (arm64 lo mataría al ejecutar): re-firmar solo ayuda.
    monkeypatch.setattr(
        _driver_macos.subprocess,
        "run",
        _salida(1, "", "code object is not signed at all"),
    )
    assert _necesita_refirma(nodo) is True

    # codesign ausente/roto: no se sabe → None (el caller avisa y sigue).
    def _explota(cmd, **kwargs):
        raise OSError("sin codesign")

    monkeypatch.setattr(_driver_macos.subprocess, "run", _explota)
    assert _necesita_refirma(nodo) is None


def test_driver_node_path_sin_paquete_devuelve_none(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def _import_falso(nombre, *args, **kwargs):
        if nombre == "playwright":
            raise ImportError("sin playwright")
        return real_import(nombre, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _import_falso)
    assert _driver_node_path() is None


def test_driver_node_path_con_paquete_real():
    # En el entorno de tests playwright SÍ está instalado (extra dev): la ruta
    # resuelta debe apuntar a un binario que existe.
    ruta = _driver_node_path()
    if ruta is None:
        return
    assert ruta.name == "node" and ruta.is_file()
