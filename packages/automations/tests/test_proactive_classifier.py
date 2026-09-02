"""`clasificar_proactividad` — ladder Observation→Suggestion→Draft→Action
(product design)."""

from edecan_automations.proactive import clasificar_proactividad


def test_stage_explicito_gana_sobre_el_resto() -> None:
    assert (
        clasificar_proactividad({"kind": "automation_failed", "stage": "action"}) == "action"
    )
    assert clasificar_proactividad({"kind": "x", "etapa": "draft"}) == "draft"


def test_stage_explicito_invalido_se_ignora_y_cae_al_tipo() -> None:
    assert clasificar_proactividad({"kind": "routine_due", "stage": "bogus"}) == "action"


def test_requires_user_confirmation_es_suggestion() -> None:
    assert (
        clasificar_proactividad(
            {"kind": "automation_suggestion", "requires_user_confirmation": True}
        )
        == "suggestion"
    )
    assert (
        clasificar_proactividad({"kind": "routine_suggestion", "requires_user_confirmation": True})
        == "suggestion"
    )


def test_draft_ready_es_draft_aunque_pida_confirmacion() -> None:
    assert (
        clasificar_proactividad(
            {"kind": "design_ready", "draft_ready": True, "requires_user_confirmation": True}
        )
        == "draft"
    )
    assert clasificar_proactividad({"kind": "design_ready", "is_draft": True}) == "draft"


def test_may_act_es_action_dentro_de_politica() -> None:
    assert clasificar_proactividad({"kind": "routine_due", "may_act": True}) == "action"


def test_confirmacion_le_gana_a_may_act() -> None:
    # Fail-closed: una señal que pide confirmación jamás actúa sola.
    assert (
        clasificar_proactividad(
            {"kind": "routine_due", "may_act": True, "requires_user_confirmation": True}
        )
        == "suggestion"
    )


def test_tipo_conocido_sin_claves_explicitas() -> None:
    assert clasificar_proactividad({"kind": "automation_failed"}) == "suggestion"
    assert clasificar_proactividad({"kind": "automation_due"}) == "action"
    assert clasificar_proactividad({"kind": "content_published"}) == "observation"


def test_tipo_desconocido_cae_a_observation() -> None:
    assert clasificar_proactividad({"kind": "evento_nunca_visto"}) == "observation"
    assert clasificar_proactividad({}) == "observation"


def test_es_determinista() -> None:
    signal = {"kind": "automation_failed", "failure_count": 3}
    assert clasificar_proactividad(signal) == clasificar_proactividad(signal)
