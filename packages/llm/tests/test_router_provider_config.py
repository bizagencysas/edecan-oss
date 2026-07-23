"""Tests de `LLMRouter` con `provider_config` explícito (`ARCHITECTURE.md`
§12.c, WP-V3-03) — selección de proveedor y resolución de modelo elegidos
por el tenant. Los tests del comportamiento legado (`provider_config=None`,
sin cambios desde v1/v2) viven en `test_llm_router.py` y NO se tocan acá.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from edecan_llm.anthropic import AnthropicProvider
from edecan_llm.claude_cli import DEFAULT_TIMEOUT_SECONDS as CLAUDE_CLI_DEFAULT_TIMEOUT_SECONDS
from edecan_llm.claude_cli import ClaudeCLIProvider
from edecan_llm.codex_cli import DEFAULT_TIMEOUT_SECONDS as CODEX_CLI_DEFAULT_TIMEOUT_SECONDS
from edecan_llm.codex_cli import CodexCLIProvider
from edecan_llm.config import LLMProviderConfig
from edecan_llm.errors import LLMError
from edecan_llm.ollama import OllamaProvider
from edecan_llm.openai_compat import OpenAICompatProvider
from edecan_llm.router import LLMRouter
from edecan_llm.vertex import VertexAIProvider


def _settings(**overrides: object) -> SimpleNamespace:
    base: dict[str, object] = dict(
        ANTHROPIC_API_KEY="TU_ANTHROPIC_API_KEY_AQUI",
        ANTHROPIC_MODEL_PRINCIPAL="claude-sonnet-4-5",
        ANTHROPIC_MODEL_RAPIDO="claude-haiku-4-5",
        OPENAI_COMPAT_BASE_URL=None,
        OPENAI_COMPAT_API_KEY=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# --- anthropic ---------------------------------------------------------------


def test_anthropic_usa_api_key_de_la_config() -> None:
    config = LLMProviderConfig(kind="anthropic", api_key="TU_KEY_DEL_TENANT_AQUI")
    router = LLMRouter(_settings(ANTHROPIC_API_KEY=None), provider_config=config)

    provider, _ = router.resolve("principal", {})

    assert isinstance(provider, AnthropicProvider)


def test_anthropic_sin_key_en_config_no_cae_a_settings_de_plataforma() -> None:
    """Aislamiento multi-tenant: aunque la plataforma tenga su propia
    `ANTHROPIC_API_KEY` configurada (legítima para el modo legacy / jobs de
    sistema sin tenant), un `provider_config` de tenant sin `api_key` propia
    NUNCA debe heredarla en silencio — debe fallar igual que si tampoco
    hubiera nada en `settings` (ver comentario de
    `_build_provider_from_config`)."""
    config = LLMProviderConfig(kind="anthropic")
    router = LLMRouter(
        _settings(ANTHROPIC_API_KEY="TU_KEY_PLATAFORMA_AQUI"), provider_config=config
    )

    with pytest.raises(LLMError):
        router.resolve("principal", {})


def test_anthropic_sin_key_en_ningun_lado_lanza_llm_error() -> None:
    config = LLMProviderConfig(kind="anthropic")
    router = LLMRouter(_settings(ANTHROPIC_API_KEY=None), provider_config=config)

    with pytest.raises(LLMError):
        router.resolve("principal", {})


def test_anthropic_modelos_de_la_config_tienen_prioridad_sobre_settings() -> None:
    config = LLMProviderConfig(
        kind="anthropic",
        api_key="TU_KEY_AQUI",
        model_principal="claude-opus-4-5",
        model_rapido="claude-haiku-4-5-tenant",
        model_profundo="claude-opus-4-6",
    )
    router = LLMRouter(_settings(), provider_config=config)

    _, principal = router.resolve("principal", {"models.premium": True})
    _, rapido = router.resolve("rapido", {})
    _, profundo = router.resolve("profundo", {"models.premium": True})

    assert principal == "claude-opus-4-5"
    assert rapido == "claude-haiku-4-5-tenant"
    assert profundo == "claude-opus-4-6"


def test_modelo_profundo_legacy_cae_a_principal() -> None:
    config = LLMProviderConfig(
        kind="anthropic",
        api_key="TU_KEY_AQUI",
        model_principal="claude-sonnet-4-5",
    )
    router = LLMRouter(_settings(), provider_config=config)

    _, profundo = router.resolve("profundo", {"models.premium": True})

    assert profundo == "claude-sonnet-4-5"


def test_modelo_profundo_degrada_a_rapido_sin_premium() -> None:
    config = LLMProviderConfig(
        kind="anthropic",
        api_key="TU_KEY_AQUI",
        model_principal="claude-sonnet-4-5",
        model_rapido="claude-haiku-4-5",
        model_profundo="claude-opus-4-6",
    )
    router = LLMRouter(_settings(), provider_config=config)

    _, profundo = router.resolve("profundo", {"models.premium": False})

    assert profundo == "claude-haiku-4-5"


def test_anthropic_sin_modelos_en_config_cae_a_settings() -> None:
    config = LLMProviderConfig(kind="anthropic", api_key="TU_KEY_AQUI")
    router = LLMRouter(_settings(), provider_config=config)

    _, principal = router.resolve("principal", {"models.premium": True})

    assert principal == "claude-sonnet-4-5"


def test_anthropic_sin_modelos_en_config_ni_settings_usa_default_hardcodeado() -> None:
    config = LLMProviderConfig(kind="anthropic", api_key="TU_KEY_AQUI")
    settings = SimpleNamespace(ANTHROPIC_API_KEY="TU_KEY_AQUI")  # sin *_MODEL_* declarados
    router = LLMRouter(settings, provider_config=config)

    _, principal = router.resolve("principal", {"models.premium": True})
    _, rapido = router.resolve("rapido", {})

    assert principal == "claude-sonnet-4-5"
    assert rapido == "claude-haiku-4-5"


def test_anthropic_degrada_a_rapido_sin_flag_premium() -> None:
    config = LLMProviderConfig(kind="anthropic", api_key="TU_KEY_AQUI")
    router = LLMRouter(_settings(), provider_config=config)

    _, model = router.resolve("principal", {"models.premium": False})

    assert model == "claude-haiku-4-5"


# --- openai_compat -------------------------------------------------------------


def test_openai_compat_usa_base_url_y_key_de_la_config() -> None:
    config = LLMProviderConfig(
        kind="openai_compat", base_url="https://api.groq.com/openai/v1", api_key="TU_KEY_AQUI"
    )
    router = LLMRouter(_settings(), provider_config=config)

    provider, _ = router.resolve("principal", {})

    assert isinstance(provider, OpenAICompatProvider)


def test_openai_compat_sin_base_url_en_config_no_cae_a_settings_de_plataforma() -> None:
    """Mismo criterio que el test equivalente de `anthropic` arriba: un
    `OPENAI_COMPAT_BASE_URL` de plataforma NUNCA debe sustituir un `base_url`
    de tenant ausente."""
    config = LLMProviderConfig(kind="openai_compat")
    router = LLMRouter(
        _settings(OPENAI_COMPAT_BASE_URL="https://api.openai.com/v1"), provider_config=config
    )

    with pytest.raises(LLMError):
        router.resolve("principal", {})


def test_openai_compat_sin_base_url_en_ningun_lado_lanza_llm_error() -> None:
    config = LLMProviderConfig(kind="openai_compat")
    router = LLMRouter(_settings(), provider_config=config)

    with pytest.raises(LLMError):
        router.resolve("principal", {})


def test_openai_compat_sin_api_key_en_config_no_filtra_la_de_plataforma() -> None:
    """Regresión directa del hueco de seguridad corregido: un tenant que trae
    su PROPIO `base_url` pero deja `api_key` vacío (válido — `api_key` es
    opcional para `kind="openai_compat"`, ver
    `credentials.py::_LLM_KINDS_REQUIEREN_API_KEY`) jamás debe terminar
    adjuntando el `OPENAI_COMPAT_API_KEY` REAL de la plataforma como Bearer
    token hacia ese `base_url` elegido por el tenant — eso permitiría a un
    tenant malicioso robar la credencial del operador apuntando `base_url` a
    un servidor propio que loguee el header `Authorization`. El proveedor
    debe construirse sin credencial (`api_key == ""`) en vez de heredar la de
    plataforma."""
    config = LLMProviderConfig(
        kind="openai_compat", base_url="https://servidor-del-tenant.example/v1"
    )
    router = LLMRouter(
        _settings(OPENAI_COMPAT_API_KEY="SECRETO_REAL_DE_PLATAFORMA"), provider_config=config
    )

    provider, _ = router.resolve("principal", {})

    assert isinstance(provider, OpenAICompatProvider)
    assert provider._api_key == ""  # type: ignore[attr-defined]
    assert provider._api_key != "SECRETO_REAL_DE_PLATAFORMA"  # type: ignore[attr-defined]


def test_openai_compat_modelo_rapido_falta_usa_principal() -> None:
    config = LLMProviderConfig(
        kind="openai_compat",
        base_url="https://api.openai.com/v1",
        api_key="TU_KEY_AQUI",
        model_principal="gpt-4o",
    )
    router = LLMRouter(_settings(), provider_config=config)

    _, principal = router.resolve("principal", {"models.premium": True})
    _, rapido = router.resolve("rapido", {})

    assert principal == "gpt-4o"
    assert rapido == "gpt-4o"


# --- vertex ----------------------------------------------------------------------


def test_vertex_construye_provider_modo_api_key() -> None:
    config = LLMProviderConfig(
        kind="vertex", api_key="TU_GEMINI_KEY_AQUI", extra={"mode": "api_key"}
    )
    router = LLMRouter(_settings(), provider_config=config)

    provider, _ = router.resolve("principal", {})

    assert isinstance(provider, VertexAIProvider)


def test_vertex_modelos_de_la_config_tienen_prioridad() -> None:
    config = LLMProviderConfig(
        kind="vertex",
        api_key="TU_GEMINI_KEY_AQUI",
        model_principal="gemini-2.5-pro-tenant",
        model_rapido="gemini-2.5-flash-tenant",
        extra={"mode": "api_key"},
    )
    router = LLMRouter(_settings(), provider_config=config)

    _, principal = router.resolve("principal", {"models.premium": True})
    _, rapido = router.resolve("rapido", {})

    assert principal == "gemini-2.5-pro-tenant"
    assert rapido == "gemini-2.5-flash-tenant"


def test_vertex_sin_modelos_en_config_cae_a_settings_vertex() -> None:
    config = LLMProviderConfig(
        kind="vertex", api_key="TU_GEMINI_KEY_AQUI", extra={"mode": "api_key"}
    )
    settings = _settings(
        VERTEX_MODEL_PRINCIPAL="gemini-2.5-pro", VERTEX_MODEL_RAPIDO="gemini-2.5-flash"
    )
    router = LLMRouter(settings, provider_config=config)

    _, principal = router.resolve("principal", {"models.premium": True})
    _, rapido = router.resolve("rapido", {})

    assert principal == "gemini-2.5-pro"
    assert rapido == "gemini-2.5-flash"


def test_vertex_sin_modelos_en_config_ni_settings_usa_default_hardcodeado() -> None:
    config = LLMProviderConfig(
        kind="vertex", api_key="TU_GEMINI_KEY_AQUI", extra={"mode": "api_key"}
    )
    router = LLMRouter(_settings(), provider_config=config)

    _, principal = router.resolve("principal", {"models.premium": True})
    _, rapido = router.resolve("rapido", {})

    assert principal == "gemini-3.5-flash"
    assert rapido == "gemini-3.1-flash-lite"


def test_vertex_degrada_a_rapido_sin_flag_premium() -> None:
    config = LLMProviderConfig(
        kind="vertex", api_key="TU_GEMINI_KEY_AQUI", extra={"mode": "api_key"}
    )
    router = LLMRouter(_settings(), provider_config=config)

    _, model = router.resolve("principal", {"models.premium": False})

    assert model == "gemini-3.1-flash-lite"


# --- claude_cli / codex_cli ------------------------------------------------------


def test_claude_cli_construye_provider_con_binary_path_de_extra() -> None:
    config = LLMProviderConfig(kind="claude_cli", extra={"binary_path": "/usr/local/bin/claude"})
    router = LLMRouter(_settings(EDECAN_LOCAL_MODE=True), provider_config=config)

    provider, _ = router.resolve("principal", {})

    assert isinstance(provider, ClaudeCLIProvider)
    assert provider._binary_path == "/usr/local/bin/claude"  # type: ignore[attr-defined]


def test_codex_cli_construye_provider_con_binary_path_de_extra() -> None:
    config = LLMProviderConfig(kind="codex_cli", extra={"binary_path": "/usr/local/bin/codex"})
    router = LLMRouter(_settings(EDECAN_LOCAL_MODE=True), provider_config=config)

    provider, _ = router.resolve("principal", {})

    assert isinstance(provider, CodexCLIProvider)
    assert provider._binary_path == "/usr/local/bin/codex"  # type: ignore[attr-defined]


def test_claude_cli_cae_a_settings_claude_cli_path_si_falta_binary_path_en_extra() -> None:
    config = LLMProviderConfig(kind="claude_cli")
    router = LLMRouter(
        _settings(CLAUDE_CLI_PATH="/opt/claude/bin/claude", EDECAN_LOCAL_MODE=True),
        provider_config=config,
    )

    provider, _ = router.resolve("principal", {})

    assert isinstance(provider, ClaudeCLIProvider)
    assert provider._binary_path == "/opt/claude/bin/claude"  # type: ignore[attr-defined]


def test_codex_cli_cae_a_settings_codex_cli_path_si_falta_binary_path_en_extra() -> None:
    config = LLMProviderConfig(kind="codex_cli")
    router = LLMRouter(
        _settings(CODEX_CLI_PATH="/opt/codex/bin/codex", EDECAN_LOCAL_MODE=True),
        provider_config=config,
    )

    provider, _ = router.resolve("principal", {})

    assert isinstance(provider, CodexCLIProvider)
    assert provider._binary_path == "/opt/codex/bin/codex"  # type: ignore[attr-defined]


def test_claude_cli_extra_binary_path_tiene_prioridad_sobre_settings() -> None:
    config = LLMProviderConfig(kind="claude_cli", extra={"binary_path": "/from/extra/claude"})
    router = LLMRouter(
        _settings(CLAUDE_CLI_PATH="/from/settings/claude", EDECAN_LOCAL_MODE=True),
        provider_config=config,
    )

    provider, _ = router.resolve("principal", {})

    assert provider._binary_path == "/from/extra/claude"  # type: ignore[attr-defined]


def test_claude_cli_usa_timeout_de_extra() -> None:
    config = LLMProviderConfig(
        kind="claude_cli",
        extra={"binary_path": "/usr/local/bin/claude", "timeout_seconds": 45},
    )
    router = LLMRouter(
        _settings(LLM_CLI_TIMEOUT_SECONDS=999, EDECAN_LOCAL_MODE=True), provider_config=config
    )

    provider, _ = router.resolve("principal", {})

    assert provider._timeout_seconds == 45  # type: ignore[attr-defined]


def test_claude_cli_cae_a_settings_llm_cli_timeout_si_falta_en_extra() -> None:
    config = LLMProviderConfig(kind="claude_cli", extra={"binary_path": "/usr/local/bin/claude"})
    router = LLMRouter(
        _settings(LLM_CLI_TIMEOUT_SECONDS=90, EDECAN_LOCAL_MODE=True), provider_config=config
    )

    provider, _ = router.resolve("principal", {})

    assert provider._timeout_seconds == 90  # type: ignore[attr-defined]


def test_codex_cli_cae_a_settings_llm_cli_timeout_si_falta_en_extra() -> None:
    config = LLMProviderConfig(kind="codex_cli", extra={"binary_path": "/usr/local/bin/codex"})
    router = LLMRouter(
        _settings(LLM_CLI_TIMEOUT_SECONDS=120, EDECAN_LOCAL_MODE=True), provider_config=config
    )

    provider, _ = router.resolve("principal", {})

    assert provider._timeout_seconds == 120  # type: ignore[attr-defined]


def test_claude_cli_sin_timeout_en_extra_ni_settings_usa_default_del_provider() -> None:
    """Nunca debe pasarse `timeout_seconds=None` explícito: eso desactivaría
    el timeout del subproceso (`asyncio.wait_for(..., timeout=None)` espera
    para siempre) en vez de caer al `DEFAULT_TIMEOUT_SECONDS` del provider.
    """
    config = LLMProviderConfig(kind="claude_cli", extra={"binary_path": "/usr/local/bin/claude"})
    router = LLMRouter(
        _settings(EDECAN_LOCAL_MODE=True), provider_config=config
    )  # sin LLM_CLI_TIMEOUT_SECONDS

    provider, _ = router.resolve("principal", {})
    timeout = provider._timeout_seconds  # type: ignore[attr-defined]

    assert timeout == CLAUDE_CLI_DEFAULT_TIMEOUT_SECONDS
    assert timeout is not None


def test_codex_cli_sin_timeout_en_extra_ni_settings_usa_default_del_provider() -> None:
    config = LLMProviderConfig(kind="codex_cli", extra={"binary_path": "/usr/local/bin/codex"})
    router = LLMRouter(
        _settings(EDECAN_LOCAL_MODE=True), provider_config=config
    )  # sin LLM_CLI_TIMEOUT_SECONDS

    provider, _ = router.resolve("principal", {})
    timeout = provider._timeout_seconds  # type: ignore[attr-defined]

    assert timeout == CODEX_CLI_DEFAULT_TIMEOUT_SECONDS
    assert timeout is not None


def test_codex_cli_configura_reasoning_solo_para_modelo_profundo() -> None:
    config = LLMProviderConfig(
        kind="codex_cli",
        model_principal="gpt-5.6-terra",
        model_rapido="gpt-5.6-luna",
        model_profundo="gpt-5.6-sol",
        reasoning_effort_profundo="xhigh",
        extra={"binary_path": "/usr/local/bin/codex"},
    )
    router = LLMRouter(_settings(EDECAN_LOCAL_MODE=True), provider_config=config)

    provider, profundo = router.resolve("profundo", {"models.premium": True})

    assert profundo == "gpt-5.6-sol"
    assert provider._reasoning_effort_by_model == {"gpt-5.6-sol": "xhigh"}  # type: ignore[attr-defined]


def test_cli_modelo_vacio_es_valido_usa_default_del_binario() -> None:
    config = LLMProviderConfig(kind="claude_cli", extra={"binary_path": "/usr/local/bin/claude"})
    router = LLMRouter(_settings(EDECAN_LOCAL_MODE=True), provider_config=config)

    _, principal = router.resolve("principal", {"models.premium": True})
    _, rapido = router.resolve("rapido", {})

    assert principal == ""
    assert rapido == ""


def test_cli_modelo_rapido_falta_usa_principal() -> None:
    config = LLMProviderConfig(
        kind="codex_cli", model_principal="o3", extra={"binary_path": "/usr/local/bin/codex"}
    )
    router = LLMRouter(_settings(EDECAN_LOCAL_MODE=True), provider_config=config)

    _, rapido = router.resolve("rapido", {})

    assert rapido == "o3"


# --- ollama ------------------------------------------------------------------------


def test_ollama_construye_provider_con_base_url_de_la_config() -> None:
    config = LLMProviderConfig(kind="ollama", base_url="http://otronodo:11434")
    router = LLMRouter(_settings(EDECAN_LOCAL_MODE=True), provider_config=config)

    provider, _ = router.resolve("principal", {})

    assert isinstance(provider, OllamaProvider)
    assert provider._base_url == "http://otronodo:11434"  # type: ignore[attr-defined]


def test_ollama_sin_base_url_usa_default_localhost() -> None:
    config = LLMProviderConfig(kind="ollama")
    router = LLMRouter(_settings(EDECAN_LOCAL_MODE=True), provider_config=config)

    provider, _ = router.resolve("principal", {})

    assert isinstance(provider, OllamaProvider)
    assert provider._base_url == "http://localhost:11434"  # type: ignore[attr-defined]


def test_ollama_usa_model_principal_de_la_config_como_default() -> None:
    config = LLMProviderConfig(kind="ollama", model_principal="llama3.1:8b")
    router = LLMRouter(_settings(EDECAN_LOCAL_MODE=True), provider_config=config)

    _, principal = router.resolve("principal", {"models.premium": True})

    assert principal == "llama3.1:8b"


# --- kinds local-only (claude_cli/codex_cli/ollama) exigen EDECAN_LOCAL_MODE -------
#
# Segunda capa de aislamiento multi-tenant (ver el comentario de
# `_LOCAL_ONLY_KINDS` en `router.py`): el gate de ESCRITURA vive en
# `edecan_api.routers.credentials.put_llm_credentials`, pero
# `_build_provider_from_config` es quien de verdad ejecuta el binario/puerto
# local, así que vuelve a exigir `EDECAN_LOCAL_MODE` acá — cubre tanto una
# fila `connector_accounts` ya guardada que sobrevive a un `EDECAN_LOCAL_MODE`
# apagado después, como una base de datos de una instalación local copiada a
# un servidor hospedado compartido.


def test_claude_cli_sin_edecan_local_mode_lanza_llm_error() -> None:
    config = LLMProviderConfig(kind="claude_cli", extra={"binary_path": "/usr/local/bin/claude"})
    router = LLMRouter(_settings(), provider_config=config)  # sin EDECAN_LOCAL_MODE

    with pytest.raises(LLMError, match="EDECAN_LOCAL_MODE"):
        router.resolve("principal", {})


def test_codex_cli_con_edecan_local_mode_false_lanza_llm_error() -> None:
    config = LLMProviderConfig(kind="codex_cli", extra={"binary_path": "/usr/local/bin/codex"})
    router = LLMRouter(_settings(EDECAN_LOCAL_MODE=False), provider_config=config)

    with pytest.raises(LLMError, match="EDECAN_LOCAL_MODE"):
        router.resolve("principal", {})


def test_ollama_sin_edecan_local_mode_lanza_llm_error() -> None:
    config = LLMProviderConfig(kind="ollama")
    router = LLMRouter(_settings(), provider_config=config)  # sin EDECAN_LOCAL_MODE

    with pytest.raises(LLMError, match="EDECAN_LOCAL_MODE"):
        router.resolve("principal", {})


# --- genérico ------------------------------------------------------------------------


def test_kind_desconocido_lanza_llm_error() -> None:
    config = LLMProviderConfig(kind="groq_directo")
    router = LLMRouter(_settings(), provider_config=config)

    with pytest.raises(LLMError, match="groq_directo"):
        router.resolve("principal", {})


def test_provider_se_construye_una_sola_vez_con_provider_config() -> None:
    config = LLMProviderConfig(kind="ollama")
    router = LLMRouter(_settings(EDECAN_LOCAL_MODE=True), provider_config=config)

    provider_1, _ = router.resolve("principal", {})
    provider_2, _ = router.resolve("rapido", {})

    assert provider_1 is provider_2
