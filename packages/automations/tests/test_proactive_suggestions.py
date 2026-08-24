from edecan_automations.proactive import suggest_automation_from_event


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
