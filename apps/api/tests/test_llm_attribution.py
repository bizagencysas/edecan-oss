from edecan_api.llm_attribution import build_llm_usage_meta


def test_costo_conocido_se_calcula_y_conserva_atribucion() -> None:
    meta = build_llm_usage_meta(
        attribution={"provider": "fake", "model": "gpt-4o-mini", "model_alias": "rapido"},
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )

    assert meta["cost_status"] == "known"
    assert meta["estimated_cost_usd"] == 0.75
    assert meta["provider"] == "fake"
    assert meta["model_alias"] == "rapido"


def test_modelo_desconocido_no_se_presenta_como_costo_cero() -> None:
    meta = build_llm_usage_meta(
        attribution={"provider": "workers-ai", "model": "modelo-sin-tarifa"},
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )

    assert meta["cost_status"] == "unknown"
    assert meta["estimated_cost_usd"] is None


def test_atribucion_descarta_payloads_no_allowlisted() -> None:
    meta = build_llm_usage_meta(
        attribution={"model": "gpt-4o", "prompt": "secreto", "args": {"token": "x"}},
        input_tokens=0,
        output_tokens=0,
    )

    assert "prompt" not in meta
    assert "args" not in meta
