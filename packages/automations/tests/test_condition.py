"""Tests de la condición opcional (PHASE2 §60) y del estado de corrida
(PHASE2 §61) de `edecan_automations.engine` — puro, sin IO.

Cubren las dos garantías no negociables del evaluador:
1. Backwards-compatible: `condition=None` (o ausente) SIEMPRE devuelve `True`,
   así que ninguna automatización existente cambia de comportamiento.
2. Nunca lanza: una condición malformada, un operador desconocido o una
   comparación de tipos incompatibles no puede tumbar el barrido multi-tenant.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from edecan_automations.engine import (
    AutomationState,
    compute_automation_state,
    evaluate_condition,
)
from edecan_schemas.automations import (
    ConditionAdapter,
    ConditionClause,
    ScheduleTrigger,
    TriggerDefAdapter,
)
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# evaluate_condition — backwards compatible (sin condición corre siempre)
# ---------------------------------------------------------------------------


def test_evaluate_condition_none_devuelve_true() -> None:
    # El caso que protege la retrocompatibilidad: ninguna fila existente trae
    # `condition`, así que TODAS deben seguir ejecutándose.
    assert evaluate_condition(None, {}) is True


def test_evaluate_condition_lista_vacia_devuelve_true() -> None:
    # `all([])` es True: una lista sin cláusulas equivale a "sin condición".
    assert evaluate_condition([], {}) is True


def test_evaluate_condition_string_json_valido() -> None:
    # Columna `jsonb` entregada como `str` crudo por el driver.
    assert evaluate_condition('{"field": "hour", "op": "eq", "value": 9}', {"hour": 9}) is True


# ---------------------------------------------------------------------------
# evaluate_condition — operadores
# ---------------------------------------------------------------------------


def test_evaluate_condition_eq() -> None:
    assert evaluate_condition({"field": "x", "op": "eq", "value": 1}, {"x": 1}) is True
    assert evaluate_condition({"field": "x", "op": "eq", "value": 1}, {"x": 2}) is False


def test_evaluate_condition_neq() -> None:
    assert evaluate_condition({"field": "x", "op": "neq", "value": 1}, {"x": 2}) is True
    assert evaluate_condition({"field": "x", "op": "neq", "value": 1}, {"x": 1}) is False


@pytest.mark.parametrize(
    ("op", "actual", "value", "esperado"),
    [
        ("gt", 3, 2, True),
        ("gt", 2, 3, False),
        ("gte", 2, 2, True),
        ("lt", 2, 3, True),
        ("lte", 2, 2, True),
    ],
)
def test_evaluate_condition_orden(op: str, actual: int, value: int, esperado: bool) -> None:
    assert evaluate_condition({"field": "x", "op": op, "value": value}, {"x": actual}) is esperado


def test_evaluate_condition_contains_string() -> None:
    assert evaluate_condition({"field": "s", "op": "contains", "value": "bc"}, {"s": "abc"}) is True
    assert (
        evaluate_condition({"field": "s", "op": "contains", "value": "zz"}, {"s": "abc"}) is False
    )


def test_evaluate_condition_contains_lista() -> None:
    assert (
        evaluate_condition({"field": "s", "op": "contains", "value": 2}, {"s": [1, 2, 3]}) is True
    )


def test_evaluate_condition_exists() -> None:
    assert evaluate_condition({"field": "x", "op": "exists"}, {"x": 0}) is True
    assert evaluate_condition({"field": "x", "op": "exists"}, {"x": None}) is False
    assert evaluate_condition({"field": "x", "op": "exists"}, {}) is False


def test_evaluate_condition_ruta_punteada() -> None:
    assert (
        evaluate_condition(
            {"field": "detalle.error", "op": "eq", "value": "boom"}, {"detalle": {"error": "boom"}}
        )
        is True
    )


# ---------------------------------------------------------------------------
# evaluate_condition — combinación AND
# ---------------------------------------------------------------------------


def test_evaluate_condition_lista_and_todas() -> None:
    condicion = [
        {"field": "hour", "op": "gte", "value": 9},
        {"field": "hour", "op": "lt", "value": 17},
    ]
    assert evaluate_condition(condicion, {"hour": 12}) is True


def test_evaluate_condition_lista_and_una_falla_falla_todo() -> None:
    condicion = [
        {"field": "hour", "op": "gte", "value": 9},
        {"field": "hour", "op": "lt", "value": 17},
    ]
    assert evaluate_condition(condicion, {"hour": 8}) is False


# ---------------------------------------------------------------------------
# evaluate_condition — nunca lanza
# ---------------------------------------------------------------------------


def test_evaluate_condition_operador_desconocido_no_lanza() -> None:
    assert evaluate_condition({"field": "x", "op": "raro", "value": 1}, {"x": 1}) is True


def test_evaluate_condition_clausula_no_dict_no_lanza() -> None:
    assert evaluate_condition("no soy una clausula", {}) is True
    assert evaluate_condition(42, {}) is True  # type: ignore[arg-type]


def test_evaluate_condition_comparacion_de_tipos_incompatibles_no_lanza() -> None:
    # `gt` entre un datetime y un str: vale False en vez de TypeError.
    ahora = datetime(2026, 1, 1, tzinfo=UTC)
    assert (
        evaluate_condition({"field": "last_run", "op": "gt", "value": "ayer"}, {"last_run": ahora})
        is False
    )


def test_evaluate_condition_contains_sobre_none_no_lanza() -> None:
    assert evaluate_condition({"field": "x", "op": "contains", "value": 1}, {"x": None}) is False


def test_evaluate_condition_field_ausente_no_lanza() -> None:
    assert evaluate_condition({"field": "no_existe", "op": "eq", "value": 1}, {}) is False


def test_evaluate_condition_json_roto_no_lanza() -> None:
    assert evaluate_condition("{{{no es json", {}) is True


def test_evaluate_condition_field_no_string_no_lanza() -> None:
    assert evaluate_condition({"field": 1, "op": "eq", "value": 1}, {}) is True


# ---------------------------------------------------------------------------
# evaluate_condition — contexto real de runtime (caso "solo si falló la última")
# ---------------------------------------------------------------------------


def test_evaluate_condition_solo_si_ultima_fallo() -> None:
    condicion = {"field": "failure_count", "op": "gt", "value": 0}
    assert evaluate_condition(condicion, {"failure_count": 2}) is True
    assert evaluate_condition(condicion, {"failure_count": 0}) is False


# ---------------------------------------------------------------------------
# compute_automation_state
# ---------------------------------------------------------------------------


def _run(status: str, started_at: datetime) -> dict:
    return {"status": status, "started_at": started_at}


def test_compute_automation_state_sin_runs() -> None:
    estado = compute_automation_state([])
    assert estado == AutomationState(
        last_run=None, last_result=None, failure_count=0, next_run=None
    )


def test_compute_automation_state_ultima_corrida_desordenada() -> None:
    primera = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    segunda = datetime(2026, 1, 2, 9, 0, tzinfo=UTC)
    estado = compute_automation_state(
        [_run("done", primera), _run("error", segunda)],  # desordenado a propósito
    )
    assert estado.last_run == segunda
    assert estado.last_result == "error"
    assert estado.failure_count == 1


def test_compute_automation_state_failure_count_consecutivas() -> None:
    t = [datetime(2026, 1, d, 9, 0, tzinfo=UTC) for d in (1, 2, 3)]
    estado = compute_automation_state(
        [_run("error", t[0]), _run("error", t[1]), _run("error", t[2])]
    )
    assert estado.failure_count == 3


def test_compute_automation_state_exito_reinicia_failure_count() -> None:
    # Un `done` interrumpe la racha: solo cuentan los errores CONSECUTIVOS desde
    # la más reciente hacia atrás (misma semántica que `consecutive_failures`).
    t = [datetime(2026, 1, d, 9, 0, tzinfo=UTC) for d in (1, 2, 3, 4)]
    estado = compute_automation_state(
        [
            _run("error", t[0]),
            _run("error", t[1]),
            _run("done", t[2]),
            _run("error", t[3]),
        ]
    )
    assert estado.last_result == "error"
    assert estado.failure_count == 1


def test_compute_automation_state_pasa_next_run() -> None:
    proxima = datetime(2026, 2, 1, 9, 0, tzinfo=UTC)
    estado = compute_automation_state([], next_run_at=proxima)
    assert estado.next_run == proxima


def test_compute_automation_state_run_sin_timestamp_no_rompe() -> None:
    # Una fila sin timestamp usable se ordena al final y no debe tumbar el
    # cálculo (nunca lanza).
    buena = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    estado = compute_automation_state([{"status": "error"}, _run("done", buena)])
    assert estado.last_result == "done"
    assert estado.last_run == buena


# ---------------------------------------------------------------------------
# Schema: `Condition` (PHASE2 §60) — validación y retrocompatibilidad
# ---------------------------------------------------------------------------


def test_condition_adapter_acepta_clausula_y_lista() -> None:
    clausula = ConditionAdapter.validate_python({"field": "hour", "op": "eq", "value": 9})
    assert isinstance(clausula, ConditionClause)
    assert clausula.op == "eq"

    lista = ConditionAdapter.validate_python(
        [{"field": "hour", "op": "gte", "value": 9}, {"field": "hour", "op": "lt", "value": 17}]
    )
    assert isinstance(lista, list) and len(lista) == 2


def test_condition_adapter_rechaza_op_desconocido() -> None:
    with pytest.raises(ValidationError):
        ConditionAdapter.validate_python({"field": "hour", "op": "raro", "value": 9})


def test_trigger_con_condition_es_opcional_y_backwards_compatible() -> None:
    # Sin `condition`: valida exactamente como antes.
    trigger = TriggerDefAdapter.validate_python({"kind": "schedule", "rrule": "FREQ=DAILY"})
    assert isinstance(trigger, ScheduleTrigger)
    assert trigger.condition is None

    # Con `condition`: se valida y queda accesible.
    trigger_con = TriggerDefAdapter.validate_python(
        {
            "kind": "schedule",
            "rrule": "FREQ=DAILY",
            "condition": {"field": "hour", "op": "eq", "value": 9},
        }
    )
    assert isinstance(trigger_con, ScheduleTrigger)
    assert trigger_con.condition is not None
