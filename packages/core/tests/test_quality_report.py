from __future__ import annotations

from edecan_core.quality_report import (
    QualitySignals,
    aggregate_signals,
    negative_feedback_to_case,
    render_weekly_report,
    satisfaction_signals,
)


def test_quality_signals_por_defecto_queda_vacio() -> None:
    signals = QualitySignals()

    assert signals.top_failures == []
    assert signals.slowest_tools == []
    assert signals.expensive_routes == []
    assert signals.common_user_corrections == []
    assert signals.agent_loops == 0
    assert signals.retrieval_failures == 0


def test_aggregate_signals_eventos_vacios() -> None:
    signals = aggregate_signals([])

    assert signals.top_failures == []
    assert signals.slowest_tools == []
    assert signals.expensive_routes == []
    assert signals.common_user_corrections == []
    assert signals.agent_loops == 0
    assert signals.retrieval_failures == 0


def test_aggregate_signals_cuenta_fallos_por_frecuencia() -> None:
    events = [
        {"kind": "tool_failure", "detail": "buscar_web"},
        {"kind": "tool_failure", "detail": "buscar_web"},
        {"kind": "tool_failure", "detail": "leer_archivo"},
    ]

    signals = aggregate_signals(events)

    assert signals.top_failures == ["buscar_web", "leer_archivo"]


def test_aggregate_signals_promedia_y_ordena_latencias() -> None:
    events = [
        {"kind": "tool_latency", "detail": {"tool": "a", "seconds": 2.0}},
        {"kind": "tool_latency", "detail": {"tool": "a", "seconds": 4.0}},
        {"kind": "tool_latency", "detail": {"tool": "b", "seconds": 1.0}},
    ]

    signals = aggregate_signals(events)

    assert signals.slowest_tools[0] == ("a", 3.0)
    assert signals.slowest_tools[1] == ("b", 1.0)


def test_aggregate_signals_acepta_latencia_como_tupla_y_ms() -> None:
    events = [
        {"kind": "tool_latency", "detail": ("buscar_web", 0.5)},
        {"kind": "tool_latency", "detail": {"tool": "leer", "ms": 2000}},
    ]

    signals = aggregate_signals(events)

    assert ("buscar_web", 0.5) in signals.slowest_tools
    assert ("leer", 2.0) in signals.slowest_tools


def test_aggregate_signals_deduplica_rutas_caras_en_orden() -> None:
    events = [
        {"kind": "expensive_route", "detail": "ruta_a"},
        {"kind": "expensive_route", "detail": "ruta_b"},
        {"kind": "expensive_route", "detail": "ruta_a"},
    ]

    signals = aggregate_signals(events)

    assert signals.expensive_routes == ["ruta_a", "ruta_b"]


def test_aggregate_signals_cuenta_correcciones_y_contadores() -> None:
    events = [
        {"kind": "user_correction", "detail": "la respuesta estaba mal"},
        {"kind": "user_correction", "detail": "la respuesta estaba mal"},
        {"kind": "user_correction", "detail": "otra corrección"},
        {"kind": "agent_loop", "detail": None},
        {"kind": "agent_loop", "detail": None},
        {"kind": "retrieval_failure", "detail": None},
    ]

    signals = aggregate_signals(events)

    assert signals.common_user_corrections == [
        "la respuesta estaba mal",
        "otra corrección",
    ]
    assert signals.agent_loops == 2
    assert signals.retrieval_failures == 1


def test_aggregate_signals_ignora_kinds_desconocidos() -> None:
    events = [
        {"kind": "algo_raro", "detail": "x"},
        {"kind": "tool_failure", "detail": "buscar_web"},
    ]

    signals = aggregate_signals(events)

    assert signals.top_failures == ["buscar_web"]


def test_aggregate_signals_tolera_eventos_malformados() -> None:
    events = [
        "no soy un dict",
        {"sin_kind": "x"},
        {"kind": "tool_failure", "detail": None},
        {"kind": "tool_latency", "detail": {"tool": "a"}},
    ]

    signals = aggregate_signals(events)

    assert signals.top_failures == []
    assert signals.slowest_tools == []


def test_render_weekly_report_es_escaneable() -> None:
    signals = QualitySignals(
        top_failures=["buscar_web", "leer_archivo"],
        slowest_tools=[("buscar_web", 1.234)],
        expensive_routes=["ruta_a"],
        common_user_corrections=["respuesta incorrecta"],
        agent_loops=3,
        retrieval_failures=2,
    )

    report = render_weekly_report(signals)

    assert "Weekly Quality Report" in report
    assert "buscar_web" in report
    assert "leer_archivo" in report
    assert "1.234s" in report
    assert "ruta_a" in report
    assert "respuesta incorrecta" in report
    assert "Agent loops: 3" in report
    assert "Retrieval failures: 2" in report


def test_render_weekly_report_vacio_muestra_none() -> None:
    report = render_weekly_report(QualitySignals())

    assert "(none)" in report
    assert "Agent loops: 0" in report
    assert "Retrieval failures: 0" in report


def test_satisfaction_signals_mensajes_vacios() -> None:
    assert satisfaction_signals([]) == {
        "user_corrections": 0,
        "regenerations": 0,
        "abandonments": 0,
        "repeated_questions": 0,
        "successful_completions": 0,
    }


def test_satisfaction_signals_detecta_correccion_por_meta() -> None:
    messages = [{"role": "user", "text": "eso no es lo que pedí", "meta": {"correction": True}}]

    counts = satisfaction_signals(messages)

    assert counts["user_corrections"] == 1


def test_satisfaction_signals_detecta_correccion_por_texto() -> None:
    messages = [{"role": "user", "text": "eso está mal, corrígelo", "meta": {}}]

    counts = satisfaction_signals(messages)

    assert counts["user_corrections"] == 1


def test_satisfaction_signals_detecta_regeneracion_abandono_y_completado() -> None:
    messages = [
        {"role": "assistant", "text": "r1", "meta": {"regenerated": True}},
        {"role": "assistant", "text": "r2", "meta": {"abandoned": True}},
        {"role": "assistant", "text": "r3", "meta": {"completed": True}},
    ]

    counts = satisfaction_signals(messages)

    assert counts["regenerations"] == 1
    assert counts["abandonments"] == 1
    assert counts["successful_completions"] == 1


def test_satisfaction_signals_detecta_pregunta_repetida_por_duplicado() -> None:
    messages = [
        {"role": "user", "text": "¿Cuánto cuesta el producto?", "meta": {}},
        {"role": "assistant", "text": "respuesta", "meta": {}},
        {"role": "user", "text": "¿Cuánto cuesta el producto?", "meta": {}},
    ]

    counts = satisfaction_signals(messages)

    assert counts["repeated_questions"] == 1


def test_satisfaction_signals_lee_lista_de_signals_en_meta() -> None:
    messages = [
        {"role": "user", "text": "x", "meta": {"signals": ["user_correction", "abandonment"]}}
    ]

    counts = satisfaction_signals(messages)

    assert counts["user_corrections"] == 1
    assert counts["abandonments"] == 1


def test_satisfaction_signals_no_doble_cuenta_pregunta_repetida() -> None:
    messages = [
        {"role": "user", "text": "pregunta A", "meta": {}},
        {"role": "user", "text": "pregunta A", "meta": {"repeated_question": True}},
    ]

    counts = satisfaction_signals(messages)

    assert counts["repeated_questions"] == 1


def test_negative_feedback_to_case_devuelve_caso_anonimizado() -> None:
    feedback = {
        "text": "Eso está mal, debiste usar la fecha correcta del reporte fiscal",
        "category": "factual_error",
        "turn_id": "t-123",
    }

    case = negative_feedback_to_case(feedback)

    assert set(case.keys()) == {"name", "input", "category", "expected"}
    assert case["category"] == "factual_error"
    assert case["name"].startswith("negative_feedback_")


def test_negative_feedback_to_case_no_filtra_contenido_crudo() -> None:
    feedback = {"text": "datos personales del usuario 123456", "category": "wrong"}

    case = negative_feedback_to_case(feedback)

    assert "datos personales del usuario 123456" not in case["input"]
    assert "datos personales del usuario 123456" not in case["expected"]
    assert "datos personales del usuario 123456" not in case["name"]


def test_negative_feedback_to_case_es_determinista() -> None:
    feedback = {"text": "contenido", "category": "x", "turn_id": "1"}

    assert negative_feedback_to_case(feedback) == negative_feedback_to_case(feedback)


def test_negative_feedback_to_case_feedback_vacio() -> None:
    case = negative_feedback_to_case({})

    assert case["category"] == "negative_feedback"
    assert case["name"].startswith("negative_feedback_")
    assert set(case.keys()) == {"name", "input", "category", "expected"}