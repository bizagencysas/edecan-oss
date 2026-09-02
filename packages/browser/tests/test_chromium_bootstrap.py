"""Bootstrap de Chromium en primer arranque."""

from __future__ import annotations

from pathlib import Path

import pytest

from edecan_browser import _chromium_bootstrap as bootstrap


def test_chromium_instalado_false_sin_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bootstrap, "_playwright_cache_root", lambda: tmp_path)
    assert bootstrap.chromium_instalado() is False


def test_chromium_instalado_true_con_directorio(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cache = tmp_path / "chromium-1234" / "chrome-mac" / "Chromium.app" / "Contents" / "MacOS"
    cache.mkdir(parents=True)
    (cache / "Chromium").write_text("", encoding="utf-8")
    monkeypatch.setattr(bootstrap, "_playwright_cache_root", lambda: tmp_path)
    assert bootstrap.chromium_instalado() is True


def test_asegurar_chromium_instalado_no_reinstala_si_ya_existe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "chromium-9999" / "chrome-mac" / "Chromium.app" / "Contents" / "MacOS"
    cache.mkdir(parents=True)
    (cache / "Chromium").write_text("", encoding="utf-8")
    monkeypatch.setattr(bootstrap, "_playwright_cache_root", lambda: tmp_path)

    def boom(*_args, **_kwargs):
        raise AssertionError("no debe llamar playwright install")

    monkeypatch.setattr(bootstrap.subprocess, "run", boom)
    assert bootstrap.asegurar_chromium_instalado() is True


def test_asegurar_chromium_instalado_invoca_playwright_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(bootstrap, "_playwright_cache_root", lambda: tmp_path)
    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        cache = tmp_path / "chromium-1234" / "chrome-mac" / "Chromium.app" / "Contents" / "MacOS"
        cache.mkdir(parents=True)
        (cache / "Chromium").write_text("", encoding="utf-8")
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)
    assert bootstrap.asegurar_chromium_instalado(force=True) is True
    assert calls[0][-2:] == ["install", "chromium"]
