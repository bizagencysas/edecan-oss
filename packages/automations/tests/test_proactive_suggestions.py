from edecan_automations.proactive import detect_routine_suggestions, suggest_automation_from_event


def test_suggestion_requires_three_failures_and_no_mutation() -> None:
    event = {"kind": "automation_failed", "automation_id": "a-1", "failure_count": 3}

    suggestion = suggest_automation_from_event(event)

    assert suggestion == {
        "kind": "automation_suggestion",
        "action": "review_automation",
        "automation_id": "a-1",
        "failure_count": 3,
        "requires_user_confirmation": True,
        "reason": (
            "La automatización falló varias veces consecutivas; revisa su conexión o condición."
        ),
    }
    assert event == {"kind": "automation_failed", "automation_id": "a-1", "failure_count": 3}


def test_suggestion_falla_cerrado_para_ruido_o_silencio() -> None:
    assert suggest_automation_from_event({"kind": "automation_failed", "failure_count": 2}) is None
    assert (
        suggest_automation_from_event(
            {
                "kind": "automation_failed",
                "automation_id": "a-1",
                "failure_count": 3,
                "silenciado": True,
            }
        )
        is None
    )
    assert (
        suggest_automation_from_event(
            {"kind": "automation_completed", "automation_id": "a-1", "failure_count": 9}
        )
        is None
    )


def test_detect_routine_suggestions_agrupa_tareas_repetidas() -> None:
    entries = [
        {"label": "Generar reporte de ventas", "occurred_at": None},
        {"label": "  Generar reporte de ventas ", "occurred_at": None},
        {"label": "generar reporte de ventas", "occurred_at": None},
        {"label": "Enviar resumen al equipo", "occurred_at": None},
    ]

    sugerencias = detect_routine_suggestions(entries)

    assert len(sugerencias) == 1
    assert sugerencias[0]["kind"] == "routine_suggestion"
    assert sugerencias[0]["action"] == "create_routine"
    assert sugerencias[0]["repetitions"] == 3
    assert sugerencias[0]["requires_user_confirmation"] is True


def test_detect_routine_suggestions_bajo_el_umbral_no_sugiere_nada() -> None:
    entries = [
        {"label": "Tarea una vez", "occurred_at": None},
        {"label": "Tarea una vez", "occurred_at": None},
    ]

    assert detect_routine_suggestions(entries) == []


def test_detect_routine_suggestions_ignora_labels_vacios_y_limita_el_tope() -> None:
    entries = [
        {"label": "Tarea A", "occurred_at": None},
        {"label": "Tarea A", "occurred_at": None},
        {"label": "Tarea A", "occurred_at": None},
        {"label": "Tarea B", "occurred_at": None},
        {"label": "Tarea B", "occurred_at": None},
        {"label": "Tarea B", "occurred_at": None},
        {"label": "  ", "occurred_at": None},
    ]

    sugerencias = detect_routine_suggestions(entries, max_suggestions=1)

    assert len(sugerencias) == 1
    assert sugerencias[0]["repetitions"] == 3
