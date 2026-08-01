import logging

import pytest
from edecan_llm.errors import LLMError
from edecan_llm.model_selection import (
    PLATFORM_CONFIG_OVERRIDE_KEYS,
    WORKERS_AI_TASK_DEFAULTS,
    advertencia_modelo_de_razonamiento,
    choose_discovered_models,
    resolve_task_model,
)


def test_anthropic_elige_calidad_y_rapidez_por_familia() -> None:
    choice = choose_discovered_models(
        "anthropic",
        {
            "data": [
                {"id": "claude-sonnet-4-7-20260701", "created_at": 3},
                {"id": "claude-haiku-4-6-20260601", "created_at": 2},
                {"id": "claude-opus-4-6-20260501", "created_at": 1},
            ]
        },
    )

    assert choice is not None
    assert choice.principal == "claude-opus-4-6-20260501"
    assert choice.rapido == "claude-haiku-4-6-20260601"


def test_openai_prefiere_alias_general_y_mini_para_rapido() -> None:
    choice = choose_discovered_models(
        "openai_compat",
        {
            "data": [
                {"id": "text-embedding-9", "created": 999},
                {"id": "gpt-5.2-2026-04-01", "created": 40},
                {"id": "gpt-5.2", "created": 41},
                {"id": "gpt-5.2-mini", "created": 42},
                {"id": "gpt-5.2-nano", "created": 43},
                {"id": "gpt-5.3-realtime", "created": 100},
            ]
        },
    )

    assert choice is not None
    assert choice.principal == "gpt-5.2"
    assert choice.rapido == "gpt-5.2-mini"


def test_openai_compatible_generico_conserva_modelos_de_texto() -> None:
    choice = choose_discovered_models(
        "openai_compat",
        {
            "data": [
                {"id": "llama-3.1-8b", "created": 1},
                {"id": "llama-3.3-70b", "created": 2},
                {"id": "whisper-large-v3", "created": 9},
            ]
        },
    )

    assert choice is not None
    assert choice.principal == "llama-3.3-70b"
    assert choice.rapido == "llama-3.1-8b"


def test_vertex_prefiere_estable_reciente_y_lite_para_rapido() -> None:
    choice = choose_discovered_models(
        "vertex",
        {
            "models": [
                {
                    "name": "models/gemini-3.1-pro-preview",
                    "supportedGenerationMethods": ["generateContent"],
                },
                {
                    "name": "models/gemini-3.5-flash",
                    "supportedGenerationMethods": ["generateContent"],
                },
                {
                    "name": "models/gemini-3.1-flash-lite",
                    "supportedGenerationMethods": ["generateContent"],
                },
                {
                    "name": "models/gemini-embedding-2",
                    "supportedGenerationMethods": ["embedContent"],
                },
            ]
        },
    )

    assert choice is not None
    assert choice.principal == "gemini-3.5-flash"
    assert choice.rapido == "gemini-3.1-flash-lite"


def test_payload_sin_modelos_utiles_no_inventa_un_id() -> None:
    assert (
        choose_discovered_models(
            "openai_compat", {"data": [{"id": "text-embedding-3-large"}]}
        )
        is None
    )


# --------------------------------------------------------------------------- #
# Un modelo por tarea (Workers AI) — WP "IDEA 4".
# --------------------------------------------------------------------------- #


def test_alias_rapido_resuelve_al_default_medido() -> None:
    # gpt-oss-20b: el rápido documentado para chat/llamada, no el glm actual.
    assert resolve_task_model("rapido") == "@cf/openai/gpt-oss-20b"
    assert resolve_task_model("rapido") == WORKERS_AI_TASK_DEFAULTS["rapido"].model_id


def test_alias_ingenieria_software_sin_override_lanza_error_claro() -> None:
    # Por diseño no hay default hardcodeado para este alias en este módulo
    # (ver el bloque IMPORTANTE de model_selection.py): nombrar el modelo de
    # código completo aquí rompería `sin_nombres_de_modelo_en_el_codigo`.
    with pytest.raises(LLMError, match="ingenieria_software"):
        resolve_task_model("ingenieria_software")


def test_alias_ingenieria_software_con_override_resuelve_al_valor_dado() -> None:
    overrides = {"WORKERS_AI_ENGINEERING_MODEL": "@cf/moonshotai/kimi-k2.7-code"}
    assert resolve_task_model("ingenieria_software", overrides) == "@cf/moonshotai/kimi-k2.7-code"


def test_override_de_platform_config_gana_sobre_el_default() -> None:
    overrides = {"WORKERS_AI_CHAT_MODEL": "@cf/mistralai/mistral-small-3.1-24b"}
    assert resolve_task_model("rapido", overrides) == "@cf/mistralai/mistral-small-3.1-24b"


def test_override_vacio_o_ausente_no_pisa_el_default() -> None:
    assert resolve_task_model("rapido", {}) == WORKERS_AI_TASK_DEFAULTS["rapido"].model_id
    assert (
        resolve_task_model("rapido", {"WORKERS_AI_CHAT_MODEL": "   "})
        == WORKERS_AI_TASK_DEFAULTS["rapido"].model_id
    )


def test_override_de_otro_alias_no_se_confunde_con_rapido() -> None:
    # Una clave que no es la del alias pedido no debe filtrarse por accidente.
    overrides = {"WORKERS_AI_ENGINEERING_MODEL": "@cf/nvidia/nemotron-3-120b-a12b"}
    assert resolve_task_model("rapido", overrides) == WORKERS_AI_TASK_DEFAULTS["rapido"].model_id


def test_claves_de_override_documentadas_para_cada_alias() -> None:
    assert PLATFORM_CONFIG_OVERRIDE_KEYS["rapido"] == "WORKERS_AI_CHAT_MODEL"
    assert PLATFORM_CONFIG_OVERRIDE_KEYS["ingenieria_software"] == "WORKERS_AI_ENGINEERING_MODEL"


def test_defaults_actuales_no_son_modelos_de_razonamiento() -> None:
    # El punto entero del cambio: sacar los modelos de razonamiento actuales
    # (glm-4.7-flash/glm-5.2 de config/modelos.yml) del camino rápido.
    for default in WORKERS_AI_TASK_DEFAULTS.values():
        assert advertencia_modelo_de_razonamiento(default.model_id) is None


def test_advertencia_modelo_de_razonamiento_detecta_los_dos_glm() -> None:
    assert advertencia_modelo_de_razonamiento("@cf/zai-org/glm-4.7-flash") is not None
    assert advertencia_modelo_de_razonamiento("@cf/zai-org/glm-5.2") is not None
    assert advertencia_modelo_de_razonamiento("@cf/openai/gpt-oss-20b") is None


def test_override_a_modelo_de_razonamiento_se_respeta_pero_queda_logueado(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # El dueño puede pisar el default con un modelo de razonamiento si quiere
    # (nunca se lo bloquea) — pero el código debe dejarlo advertido en el log.
    with caplog.at_level(logging.WARNING, logger="edecan_llm.model_selection"):
        model = resolve_task_model(
            "rapido", {"WORKERS_AI_CHAT_MODEL": "@cf/zai-org/glm-4.7-flash"}
        )
    assert model == "@cf/zai-org/glm-4.7-flash"
    assert any("razonamiento" in record.message for record in caplog.records)
