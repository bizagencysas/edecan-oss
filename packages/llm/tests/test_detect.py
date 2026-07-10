"""Tests de `detect_local_providers` — sin binarios reales ni red real
(monkeypatch de `shutil.which`/`subprocess.run` + respx para Ollama, tal
como pide el paquete WP-V3-03)."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import httpx
import pytest
import respx
from edecan_llm.detect import detect_local_providers

OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"


@respx.mock
def test_nada_instalado_ni_corriendo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    respx.get(OLLAMA_TAGS_URL).mock(side_effect=httpx.ConnectError("refused"))

    result = detect_local_providers()

    assert result == {
        "claude_cli": {"installed": False, "path": None, "version": None},
        "codex_cli": {"installed": False, "path": None, "version": None},
        "ollama": {"running": False, "base_url": "http://localhost:11434", "models": []},
    }


@respx.mock
def test_claude_instalado_via_which_con_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/local/bin/claude" if name == "claude" else None
    )

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        assert args == ["/usr/local/bin/claude", "--version"]
        return subprocess.CompletedProcess(args, 0, stdout="1.2.3\n", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    respx.get(OLLAMA_TAGS_URL).mock(side_effect=httpx.ConnectError("refused"))

    result = detect_local_providers()

    assert result["claude_cli"] == {
        "installed": True,
        "path": "/usr/local/bin/claude",
        "version": "1.2.3",
    }
    assert result["codex_cli"] == {"installed": False, "path": None, "version": None}


@respx.mock
def test_codex_instalado_via_settings_path_tiene_prioridad_sobre_which(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        assert args == ["/opt/codex/bin/codex", "--version"]
        return subprocess.CompletedProcess(args, 0, stdout="codex-cli 0.9.0\n", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    respx.get(OLLAMA_TAGS_URL).mock(side_effect=httpx.ConnectError("refused"))
    settings = SimpleNamespace(CODEX_CLI_PATH="/opt/codex/bin/codex")

    result = detect_local_providers(settings)

    assert result["codex_cli"] == {
        "installed": True,
        "path": "/opt/codex/bin/codex",
        "version": "codex-cli 0.9.0",
    }
    assert result["claude_cli"] == {"installed": False, "path": None, "version": None}


@respx.mock
def test_version_usa_stderr_si_stdout_esta_vacio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/local/bin/claude" if name == "claude" else None
    )

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="1.0.0-beta\n")

    monkeypatch.setattr("subprocess.run", fake_run)
    respx.get(OLLAMA_TAGS_URL).mock(side_effect=httpx.ConnectError("refused"))

    result = detect_local_providers()

    assert result["claude_cli"]["version"] == "1.0.0-beta"


@respx.mock
def test_version_falla_pero_installed_sigue_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/local/bin/claude" if name == "claude" else None
    )

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        raise subprocess.TimeoutExpired(cmd=args, timeout=5)

    monkeypatch.setattr("subprocess.run", fake_run)
    respx.get(OLLAMA_TAGS_URL).mock(side_effect=httpx.ConnectError("refused"))

    result = detect_local_providers()

    assert result["claude_cli"] == {
        "installed": True,
        "path": "/usr/local/bin/claude",
        "version": None,
    }


@respx.mock
def test_which_lanza_excepcion_detect_no_revienta(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_name: str) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr("shutil.which", boom)
    respx.get(OLLAMA_TAGS_URL).mock(side_effect=httpx.ConnectError("refused"))

    result = detect_local_providers()

    assert result["claude_cli"] == {"installed": False, "path": None, "version": None}
    assert result["codex_cli"] == {"installed": False, "path": None, "version": None}


@respx.mock
def test_ollama_corriendo_con_modelos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    respx.get(OLLAMA_TAGS_URL).mock(
        return_value=httpx.Response(
            200, json={"models": [{"name": "llama3.1:8b"}, {"name": "mistral:7b"}]}
        )
    )

    result = detect_local_providers()

    assert result["ollama"] == {
        "running": True,
        "base_url": "http://localhost:11434",
        "models": ["llama3.1:8b", "mistral:7b"],
    }


@respx.mock
def test_ollama_base_url_configurable_por_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    respx.get("http://otronodo:11434/api/tags").mock(
        return_value=httpx.Response(200, json={"models": []})
    )
    settings = SimpleNamespace(OLLAMA_BASE_URL="http://otronodo:11434")

    result = detect_local_providers(settings)

    assert result["ollama"] == {
        "running": True,
        "base_url": "http://otronodo:11434",
        "models": [],
    }


@respx.mock
def test_ollama_status_error_no_revienta_y_marca_apagado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    respx.get(OLLAMA_TAGS_URL).mock(return_value=httpx.Response(500, text="boom"))

    result = detect_local_providers()

    assert result["ollama"] == {
        "running": False,
        "base_url": "http://localhost:11434",
        "models": [],
    }


@respx.mock
def test_ollama_modelo_sin_nombre_se_omite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    respx.get(OLLAMA_TAGS_URL).mock(
        return_value=httpx.Response(
            200, json={"models": [{"name": "llama3.1:8b"}, {"digest": "sinnombre"}]}
        )
    )

    result = detect_local_providers()

    assert result["ollama"]["models"] == ["llama3.1:8b"]


@respx.mock
def test_shape_siempre_completo_con_settings_vacio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    respx.get(OLLAMA_TAGS_URL).mock(side_effect=httpx.ConnectError("refused"))

    result = detect_local_providers(SimpleNamespace())

    assert set(result.keys()) == {"claude_cli", "codex_cli", "ollama"}
    for key in ("claude_cli", "codex_cli"):
        assert set(result[key].keys()) == {"installed", "path", "version"}
    assert set(result["ollama"].keys()) == {"running", "base_url", "models"}
