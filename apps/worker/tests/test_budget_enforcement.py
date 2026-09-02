"""Unit tests for `edecan_worker.budget` (deterministic budget enforcement)."""

from __future__ import annotations

from edecan_worker.budget import (
    BUDGET_KEYS,
    cap_presupuesto,
    motivo_excedido,
    presupuesto_excedido,
    uso_desde_detalle,
)


def test_budget_keys_schema() -> None:
    assert BUDGET_KEYS == ("money", "compute", "time", "tools")


def test_cap_ausente_o_cero_o_negativo_significa_sin_tope() -> None:
    assert cap_presupuesto({}, "time") is None
    assert cap_presupuesto({"time": 0}, "time") is None
    assert cap_presupuesto({"time": -5}, "time") is None
    assert cap_presupuesto({"time": True}, "time") is None
    assert cap_presupuesto({"time": "nunca"}, "time") is None


def test_cap_positivo_se_respeta() -> None:
    assert cap_presupuesto({"time": 60}, "time") == 60.0


def test_sin_tope_o_sin_uso_no_excede() -> None:
    assert presupuesto_excedido({}, {"time": 999, "tools": 999}) == ()
    assert presupuesto_excedido({"time": 60}, {}) == ()


def test_excedido_detecta_cada_clave_en_orden() -> None:
    budget = {"money": 1.0, "compute": 1000, "time": 30, "tools": 5}
    uso = {"money": 2.0, "compute": 1500, "time": 31, "tools": 6}
    assert presupuesto_excedido(budget, uso) == ("money", "compute", "time", "tools")


def test_excedido_ignora_dimensiones_sin_tope() -> None:
    budget = {"compute": 1000}
    uso = {"time": 10_000, "tools": 10_000}
    assert presupuesto_excedido(budget, uso) == ()


def test_motivo_excedido_legible() -> None:
    assert motivo_excedido(("time", "tools")) == "Presupuesto excedido: time, tools."
    assert motivo_excedido(()) == ""


def test_uso_desde_detalle_suma_tokens_y_cuenta_tool_start() -> None:
    detalle = {
        "usage": {"input_tokens": 120, "output_tokens": 80},
        "tool_log": [
            {"type": "tool_start", "name": "buscar"},
            {"type": "tool_end", "name": "buscar"},
            {"type": "tool_start", "name": "enviar"},
            {"type": "text_delta"},
        ],
    }
    uso = uso_desde_detalle(detalle, elapsed_seconds=12.5)
    assert uso["compute"] == 200.0
    assert uso["tools"] == 2.0
    assert uso["time"] == 12.5
    assert "money" not in uso


def test_uso_desde_detalle_total_tokens_fallback() -> None:
    detalle = {"usage": {"total_tokens": 500}, "tool_log": []}
    uso = uso_desde_detalle(detalle, elapsed_seconds=0.0)
    assert uso["compute"] == 500.0


def test_uso_desde_detalle_money_solo_si_medido() -> None:
    uso = uso_desde_detalle({"cost_usd": 0.42}, elapsed_seconds=0.0)
    assert uso["money"] == 0.42
    uso_sin = uso_desde_detalle({}, elapsed_seconds=0.0)
    assert "money" not in uso_sin


def test_excedido_end_to_end_con_detalle() -> None:
    budget = {"compute": 100, "tools": 1, "time": 5}
    detalle = {
        "usage": {"input_tokens": 90, "output_tokens": 20},
        "tool_log": [{"type": "tool_start"}, {"type": "tool_start"}],
    }
    uso = uso_desde_detalle(detalle, elapsed_seconds=6.0)
    assert presupuesto_excedido(budget, uso) == ("compute", "time", "tools")