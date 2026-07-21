from edecan_llm.model_selection import choose_discovered_models


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
