"""Pruebas de ``ide_plan``: el criterio de "¿esto merece un plan?" y el ciclo
de vida completo del estado (proponer, editar, aprobar, avanzar, terminar).
"""

from __future__ import annotations

import pytest
from edecan_companion.ide_plan import (
    MAX_STEPS,
    IDEPlanError,
    PlanStore,
    requires_plan,
)

# --- requires_plan -----------------------------------------------------


def test_requires_plan_false_sin_pasos_o_un_solo_paso():
    assert requires_plan("haz algo", []) is False
    assert requires_plan("haz algo", ["un único paso"]) is False


def test_requires_plan_true_con_tres_o_mas_pasos_por_defecto():
    steps = ["paso 1", "paso 2", "paso 3"]
    assert requires_plan("agrega un botón nuevo al panel", steps) is True


def test_requires_plan_false_por_debajo_del_umbral():
    steps = ["paso 1", "paso 2"]
    assert requires_plan("agrega un botón nuevo al panel", steps) is False


def test_requires_plan_false_para_intencion_trivial_de_lectura():
    # "Lee el readme" no merece un plan aunque el desglose tenga 3 pasos.
    steps = ["abrir el archivo", "leerlo", "resumir el contenido"]
    assert requires_plan("Lee el README y dime qué dice", steps) is False


def test_requires_plan_true_para_intencion_trivial_pero_desglose_sospechoso():
    # 5+ pasos bajo un verbo "trivial" es señal de que no era tan trivial.
    steps = [f"paso {i}" for i in range(1, 6)]
    assert requires_plan("Muestra el estado del repo", steps) is True


def test_requires_plan_true_por_palabra_de_alto_riesgo_con_pocos_pasos():
    # "refactoriza la autenticación" con solo 2 pasos candidatos igual debe
    # pasar por aprobación: el costo de un rumbo equivocado es alto.
    steps = ["tocar el middleware de auth", "actualizar los tests"]
    assert requires_plan("Refactoriza la autenticación del API", steps) is True


def test_requires_plan_false_alto_riesgo_con_un_solo_paso():
    assert requires_plan("Elimina el archivo temporal", ["borrar el archivo"]) is False


def test_requires_plan_no_lanza_con_entradas_basura():
    assert requires_plan("", []) is False
    assert requires_plan(None, ["a", "", "  ", "b", "c"]) is True  # type: ignore[arg-type]


# --- PlanStore.propose ---------------------------------------------------


def test_propose_crea_plan_en_estado_proposed():
    store = PlanStore()
    plan = store.propose("sesion-1", "Refactorizar el módulo de auth", ["paso 1", "paso 2"])
    assert plan.status == "proposed"
    assert plan.session_id == "sesion-1"
    assert plan.goal == "Refactorizar el módulo de auth"
    assert [step.description for step in plan.steps] == ["paso 1", "paso 2"]
    assert all(step.status == "pending" for step in plan.steps)
    assert plan.current_step_index is None


def test_propose_rechaza_pasos_vacios():
    store = PlanStore()
    with pytest.raises(IDEPlanError):
        store.propose("sesion-1", "meta", [])


def test_propose_rechaza_exceso_de_pasos():
    store = PlanStore()
    with pytest.raises(IDEPlanError):
        store.propose("sesion-1", "meta", [f"paso {i}" for i in range(MAX_STEPS + 1)])


def test_propose_rechaza_paso_vacio_o_solo_espacios():
    store = PlanStore()
    with pytest.raises(IDEPlanError):
        store.propose("sesion-1", "meta", ["paso ok", "   "])


def test_propose_rechaza_meta_vacia():
    store = PlanStore()
    with pytest.raises(IDEPlanError):
        store.propose("sesion-1", "   ", ["paso 1"])


def test_propose_rechaza_session_id_vacio():
    store = PlanStore()
    with pytest.raises(IDEPlanError):
        store.propose("", "meta", ["paso 1"])


def test_propose_rechaza_segundo_plan_activo_en_la_misma_sesion():
    store = PlanStore()
    store.propose("sesion-1", "primer plan", ["paso 1"])
    with pytest.raises(IDEPlanError):
        store.propose("sesion-1", "segundo plan", ["paso 1"])


def test_propose_permite_otra_sesion_en_paralelo():
    store = PlanStore()
    store.propose("sesion-1", "primer plan", ["paso 1"])
    # No debe chocar con el slot de otra sesión.
    plan_b = store.propose("sesion-2", "otro plan", ["paso 1"])
    assert plan_b.session_id == "sesion-2"


def test_propose_permite_uno_nuevo_tras_rechazar_el_anterior():
    store = PlanStore()
    first = store.propose("sesion-1", "primer plan", ["paso 1"])
    store.reject(first.id)
    second = store.propose("sesion-1", "segundo plan", ["paso 1"])
    assert second.status == "proposed"


# --- edit ------------------------------------------------------------------


def test_edit_reemplaza_los_pasos_mientras_esta_proposed():
    store = PlanStore()
    plan = store.propose("sesion-1", "meta", ["viejo paso"])
    edited = store.edit(plan.id, ["paso nuevo 1", "paso nuevo 2"])
    assert [step.description for step in edited.steps] == ["paso nuevo 1", "paso nuevo 2"]
    assert edited.status == "proposed"


def test_edit_falla_una_vez_aprobado():
    store = PlanStore()
    plan = store.propose("sesion-1", "meta", ["paso 1"])
    store.approve(plan.id)
    with pytest.raises(IDEPlanError):
        store.edit(plan.id, ["otro paso"])


def test_edit_plan_inexistente_lanza():
    store = PlanStore()
    with pytest.raises(IDEPlanError):
        store.edit("no-existe", ["paso 1"])


# --- approve / reject --------------------------------------------------


def test_approve_pasa_a_executing_con_primer_paso_activo():
    store = PlanStore()
    plan = store.propose("sesion-1", "meta", ["paso 1", "paso 2"])
    approved = store.approve(plan.id)
    assert approved.status == "executing"
    assert approved.current_step_index == 0
    assert approved.steps[0].status == "active"
    assert approved.steps[1].status == "pending"
    assert approved.current_step is approved.steps[0]


def test_approve_dos_veces_lanza():
    store = PlanStore()
    plan = store.propose("sesion-1", "meta", ["paso 1"])
    store.approve(plan.id)
    with pytest.raises(IDEPlanError):
        store.approve(plan.id)


def test_reject_pasa_a_rejected_y_libera_el_slot_de_la_sesion():
    store = PlanStore()
    plan = store.propose("sesion-1", "meta", ["paso 1"])
    rejected = store.reject(plan.id, reason="no hace falta")
    assert rejected.status == "rejected"
    assert rejected.error == "no hace falta"
    assert store.get_active_for_session("sesion-1") is None


def test_reject_tras_aprobar_lanza():
    store = PlanStore()
    plan = store.propose("sesion-1", "meta", ["paso 1"])
    store.approve(plan.id)
    with pytest.raises(IDEPlanError):
        store.reject(plan.id)


# --- advance / skip_current / fail / cancel ------------------------------


def test_advance_recorre_los_pasos_en_orden_y_completa_al_final():
    store = PlanStore()
    plan = store.propose("sesion-1", "meta", ["paso 1", "paso 2"])
    store.approve(plan.id)

    after_first = store.advance(plan.id, note="hecho")
    assert after_first.steps[0].status == "done"
    assert after_first.steps[0].note == "hecho"
    assert after_first.current_step_index == 1
    assert after_first.steps[1].status == "active"
    assert after_first.status == "executing"

    after_second = store.advance(plan.id)
    assert after_second.status == "completed"
    assert after_second.current_step_index is None
    assert after_second.steps[1].status == "done"


def test_advance_sin_aprobar_lanza():
    store = PlanStore()
    plan = store.propose("sesion-1", "meta", ["paso 1"])
    with pytest.raises(IDEPlanError):
        store.advance(plan.id)


def test_advance_tras_completar_lanza():
    store = PlanStore()
    plan = store.propose("sesion-1", "meta", ["paso 1"])
    store.approve(plan.id)
    store.advance(plan.id)
    with pytest.raises(IDEPlanError):
        store.advance(plan.id)


def test_skip_current_marca_skipped_y_avanza():
    store = PlanStore()
    plan = store.propose("sesion-1", "meta", ["paso 1", "paso 2"])
    store.approve(plan.id)
    after = store.skip_current(plan.id, note="ya no aplicaba")
    assert after.steps[0].status == "skipped"
    assert after.steps[0].note == "ya no aplicaba"
    assert after.current_step_index == 1


def test_fail_marca_failed_y_conserva_el_paso_donde_se_rompio():
    store = PlanStore()
    plan = store.propose("sesion-1", "meta", ["paso 1", "paso 2"])
    store.approve(plan.id)
    store.advance(plan.id)
    failed = store.fail(plan.id, "el comando reventó")
    assert failed.status == "failed"
    assert failed.error == "el comando reventó"
    # El índice del paso donde se rompió se conserva, no se limpia.
    assert failed.current_step_index == 1


def test_fail_sin_estar_executing_lanza():
    store = PlanStore()
    plan = store.propose("sesion-1", "meta", ["paso 1"])
    with pytest.raises(IDEPlanError):
        store.fail(plan.id, "motivo")


def test_cancel_desde_proposed():
    store = PlanStore()
    plan = store.propose("sesion-1", "meta", ["paso 1"])
    cancelled = store.cancel(plan.id, "el usuario se arrepintió")
    assert cancelled.status == "cancelled"
    assert cancelled.error == "el usuario se arrepintió"
    assert store.get_active_for_session("sesion-1") is None


def test_cancel_desde_executing_no_reescribe_el_paso_activo():
    store = PlanStore()
    plan = store.propose("sesion-1", "meta", ["paso 1", "paso 2"])
    store.approve(plan.id)
    cancelled = store.cancel(plan.id)
    assert cancelled.status == "cancelled"
    # Cancelar el plan es distinto de saltar el paso: no se toca su status.
    assert cancelled.steps[0].status == "active"


def test_cancel_plan_terminal_lanza():
    store = PlanStore()
    plan = store.propose("sesion-1", "meta", ["paso 1"])
    store.approve(plan.id)
    store.advance(plan.id)
    with pytest.raises(IDEPlanError):
        store.cancel(plan.id)


# --- lectura / API pública -------------------------------------------------


def test_get_plan_inexistente_lanza():
    store = PlanStore()
    with pytest.raises(IDEPlanError):
        store.get("no-existe")


def test_get_active_for_session_ignora_planes_terminales():
    store = PlanStore()
    plan = store.propose("sesion-1", "meta", ["paso 1"])
    store.reject(plan.id)
    assert store.get_active_for_session("sesion-1") is None


def test_list_for_session_devuelve_todo_en_orden_de_creacion():
    store = PlanStore()
    first = store.propose("sesion-1", "primero", ["paso 1"])
    store.reject(first.id)
    second = store.propose("sesion-1", "segundo", ["paso 1"])
    rows = store.list_for_session("sesion-1")
    assert [row.id for row in rows] == [first.id, second.id]


def test_public_devuelve_estructura_serializable():
    store = PlanStore()
    plan = store.propose("sesion-1", "meta", ["paso 1"])
    payload = plan.public()
    assert payload["status"] == "proposed"
    assert payload["steps"][0]["description"] == "paso 1"
    assert payload["steps"][0]["status"] == "pending"
    assert payload["current_step_index"] is None
    assert payload["error"] is None
    assert "created_at" in payload and "updated_at" in payload
