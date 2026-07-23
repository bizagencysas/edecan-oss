"""Tests de `LLMProviderConfig` (`edecan_llm.config`, WP-V3-03)."""

from __future__ import annotations

import dataclasses

import pytest
from edecan_llm.config import LLMProviderConfig


def test_defaults() -> None:
    config = LLMProviderConfig(kind="ollama")
    assert config.kind == "ollama"
    assert config.api_key is None
    assert config.base_url is None
    assert config.model_principal is None
    assert config.model_rapido is None
    assert config.model_profundo is None
    assert config.reasoning_effort_profundo is None
    assert config.extra == {}


def test_es_frozen() -> None:
    config = LLMProviderConfig(kind="anthropic")
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.kind = "openai_compat"  # type: ignore[misc]


def test_from_dict_minimo() -> None:
    config = LLMProviderConfig.from_dict({"kind": "claude_cli"})
    assert config.kind == "claude_cli"
    assert config.extra == {}


def test_from_dict_completo() -> None:
    d = {
        "kind": "vertex",
        "api_key": "TU_API_KEY_AQUI",
        "base_url": None,
        "model_principal": "gemini-2.5-pro",
        "model_rapido": "gemini-2.5-flash",
        "model_profundo": "gemini-2.5-pro-deep",
        "reasoning_effort_profundo": "high",
        "extra": {"mode": "api_key"},
    }
    config = LLMProviderConfig.from_dict(d)
    assert config.kind == "vertex"
    assert config.api_key == "TU_API_KEY_AQUI"
    assert config.model_principal == "gemini-2.5-pro"
    assert config.model_profundo == "gemini-2.5-pro-deep"
    assert config.extra == {"mode": "api_key"}


def test_from_dict_tolera_campos_extra_desconocidos() -> None:
    # `id`/`label` son el tipo de campo que agregaría la pantalla de
    # Configuración (o un fixture de test) y que este contrato no conoce.
    config = LLMProviderConfig.from_dict(
        {"kind": "ollama", "id": "abc123", "label": "Mi Ollama local"}
    )
    assert config.kind == "ollama"
    assert not hasattr(config, "id")
    assert not hasattr(config, "label")


def test_from_dict_sin_kind_lanza_value_error() -> None:
    with pytest.raises(ValueError, match="kind"):
        LLMProviderConfig.from_dict({"api_key": "x"})


def test_to_dict_devuelve_todos_los_campos() -> None:
    config = LLMProviderConfig(
        kind="codex_cli",
        extra={"binary_path": "/usr/local/bin/codex"},
    )
    assert config.to_dict() == {
        "kind": "codex_cli",
        "api_key": None,
        "base_url": None,
        "model_principal": None,
        "model_rapido": None,
        "model_profundo": None,
        "reasoning_effort_profundo": None,
        "extra": {"binary_path": "/usr/local/bin/codex"},
    }


def test_to_dict_extra_es_una_copia() -> None:
    config = LLMProviderConfig(kind="ollama", extra={"a": 1})
    data = config.to_dict()
    data["extra"]["a"] = 999
    assert config.extra == {"a": 1}


def test_round_trip_from_dict_to_dict() -> None:
    original = LLMProviderConfig(
        kind="openai_compat",
        api_key="TU_API_KEY_AQUI",
        base_url="https://api.openai.com/v1",
        model_principal="gpt-4o",
    )
    reconstruido = LLMProviderConfig.from_dict(original.to_dict())
    assert reconstruido == original
