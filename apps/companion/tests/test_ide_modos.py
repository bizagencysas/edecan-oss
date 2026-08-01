"""Pruebas de ``ide_modos``: objetivo persistente (``/goal``), nivel de
esfuerzo (``/effort``) y modo planificación (``/plan`` como modo).

Cubren, en orden, lo que pide el trabajo pendiente:
- un objetivo que se cumple cuando se cierran todos sus criterios;
- un objetivo que se estanca y se detiene, tanto por bucle detectado por
  ``ide_costos`` en un solo turno como por varios turnos seguidos sin
  cerrar ningún criterio;
- que cambiar el nivel de esfuerzo sube el presupuesto de tokens (nunca lo
  baja), y que se recorta contra el techo real de ``config/modelos.yml``;
- que el modo planificación no permite escribir archivos mientras está
  activo, y sí los permite en cuanto se sale de él.
"""

from __future__ import annotations

from typing import Any

import pytest
from edecan_companion.ide_costos import analizar_tarea
from edecan_companion.ide_modos import (
    MAX_TURNOS_SIN_AVANCE,
    NIVEL_POR_DEFECTO,
    NIVELES_ESFUERZO,
    EsfuerzoStore,
    GoalStore,
    IDEModosError,
    ModoPlanificacionStore,
    max_tokens_para_esfuerzo,
    resolver_nivel_esfuerzo,
)

# --- helpers compartidos con test_ide_costos.py (mismo patrón de eventos) --


def _ev(cursor: int, tipo: str, texto: str, ts: str) -> dict[str, Any]:
    return {"cursor": cursor, "type": tipo, "text": texto, "timestamp": ts}


def _eventos_con_bucle_sin_cambios() -> list[dict[str, Any]]:
    """La misma herramienta repetida 4 veces, sin ningún evento "file" en
    medio -- el caso literal que ``ide_costos`` detecta como bucle SIN
    avance."""
    from datetime import UTC, datetime, timedelta

    inicio = datetime(2026, 7, 28, 10, 0, 0, tzinfo=UTC)
    eventos = []
    cursor = 0
    for i in range(4):
        cursor += 1
        ts_tool = (inicio + timedelta(seconds=i)).isoformat()
        eventos.append(_ev(cursor, "tool", "Usando leer_archivo.", ts_tool))
        cursor += 1
        ts_output = (inicio + timedelta(seconds=i + 0.5)).isoformat()
        eventos.append(_ev(cursor, "output", "el mismo contenido de siempre", ts_output))
    return eventos


def _eventos_normales_con_cambio() -> list[dict[str, Any]]:
    from datetime import UTC, datetime

    ts = datetime(2026, 7, 28, 10, 0, 0, tzinfo=UTC).isoformat()
    return [
        _ev(1, "tool", "Usando editar_archivo.", ts),
        _ev(2, "file", "Archivo actualizado: README.md", ts),
    ]


# --- GoalStore: set_goal ----------------------------------------------------


def test_set_goal_crea_objetivo_activo_con_criterios():
    store = GoalStore()
    goal = store.set_goal("sesion-1", "Migrar el endpoint a async", ["pytest -q pasa en verde"])
    assert goal.status == "activo"
    assert goal.session_id == "sesion-1"
    assert goal.descripcion == "Migrar el endpoint a async"
    assert [c.descripcion for c in goal.criterios] == ["pytest -q pasa en verde"]
    assert all(not c.cumplido for c in goal.criterios)
    assert goal.progreso == (0, 1)


def test_set_goal_rechaza_sin_criterios():
    store = GoalStore()
    with pytest.raises(IDEModosError):
        store.set_goal("sesion-1", "meta", [])


def test_set_goal_rechaza_descripcion_vacia():
    store = GoalStore()
    with pytest.raises(IDEModosError):
        store.set_goal("sesion-1", "   ", ["criterio"])


def test_set_goal_rechaza_segundo_objetivo_activo_en_la_misma_sesion():
    store = GoalStore()
    store.set_goal("sesion-1", "primero", ["criterio"])
    with pytest.raises(IDEModosError):
        store.set_goal("sesion-1", "segundo", ["criterio"])


def test_set_goal_permite_otra_sesion_en_paralelo():
    store = GoalStore()
    store.set_goal("sesion-1", "primero", ["criterio"])
    goal_b = store.set_goal("sesion-2", "segundo", ["criterio"])
    assert goal_b.session_id == "sesion-2"


# --- GoalStore: se cumple ----------------------------------------------------


def test_objetivo_se_cumple_cuando_se_cierran_todos_los_criterios():
    store = GoalStore()
    goal = store.set_goal(
        "sesion-1", "Arreglar el bug del login", ["tests pasan", "revisado por el usuario"]
    )
    primero, segundo = goal.criterios

    after_first = store.cumplir_criterio(goal.id, primero.id)
    assert after_first.status == "activo"
    assert after_first.progreso == (1, 2)

    after_second = store.cumplir_criterio(goal.id, segundo.id)
    assert after_second.status == "cumplido"
    assert after_second.progreso == (2, 2)
    assert after_second.motivo_fin == "Todos los criterios de éxito se cumplieron."


def test_cumplir_criterio_resetea_el_contador_de_turnos_sin_avance():
    store = GoalStore()
    goal = store.set_goal("sesion-1", "meta", ["criterio único", "segundo criterio"])
    store.registrar_turno(goal.id)
    store.registrar_turno(goal.id)
    assert store.get(goal.id).turnos_sin_avance == 2

    store.cumplir_criterio(goal.id, goal.criterios[0].id)
    assert store.get(goal.id).turnos_sin_avance == 0


def test_cumplir_criterio_con_id_ajeno_lanza():
    store = GoalStore()
    goal = store.set_goal("sesion-1", "meta", ["criterio"])
    with pytest.raises(IDEModosError):
        store.cumplir_criterio(goal.id, "no-existe")


def test_cumplir_criterio_en_objetivo_no_activo_lanza():
    store = GoalStore()
    goal = store.set_goal("sesion-1", "meta", ["criterio"])
    store.cancel(goal.id)
    with pytest.raises(IDEModosError):
        store.cumplir_criterio(goal.id, goal.criterios[0].id)


# --- GoalStore: se estanca y se detiene --------------------------------------


def test_objetivo_se_estanca_por_bucle_detectado_en_un_solo_turno():
    store = GoalStore()
    goal = store.set_goal("sesion-1", "Refactorizar el módulo de auth", ["build limpio"])

    costo = analizar_tarea(_eventos_con_bucle_sin_cambios())
    assert costo.bucles, "el fixture debe producir un bucle detectable"

    after = store.registrar_turno(goal.id, task_cost=costo)
    assert after.status == "estancado"
    assert "ide_costos" in (after.motivo_fin or "")


def test_objetivo_se_estanca_por_bucle_usando_el_resumen_serializado():
    """``registrar_turno`` acepta también el dict de ``.resumen()``, no solo
    el ``TaskCost`` en vivo."""
    store = GoalStore()
    goal = store.set_goal("sesion-1", "meta", ["criterio"])
    resumen = analizar_tarea(_eventos_con_bucle_sin_cambios()).resumen()
    after = store.registrar_turno(goal.id, task_cost=resumen)
    assert after.status == "estancado"


def test_objetivo_no_se_estanca_con_tarea_normal_y_criterio_pendiente():
    store = GoalStore()
    goal = store.set_goal("sesion-1", "meta", ["criterio 1", "criterio 2"])
    costo = analizar_tarea(_eventos_normales_con_cambio())
    after = store.registrar_turno(goal.id, task_cost=costo)
    assert after.status == "activo"
    assert after.turnos_sin_avance == 1


def test_objetivo_se_estanca_tras_varios_turnos_seguidos_sin_avance():
    store = GoalStore()
    goal = store.set_goal("sesion-1", "meta", ["criterio 1", "criterio 2"])
    for _ in range(MAX_TURNOS_SIN_AVANCE - 1):
        after = store.registrar_turno(goal.id)
        assert after.status == "activo"
    after_final = store.registrar_turno(goal.id)
    assert after_final.status == "estancado"
    assert after_final.turnos_sin_avance == MAX_TURNOS_SIN_AVANCE


def test_objetivo_no_se_estanca_si_algun_turno_intermedio_avanza():
    store = GoalStore()
    goal = store.set_goal("sesion-1", "meta", ["criterio 1", "criterio 2"])
    store.registrar_turno(goal.id)
    store.registrar_turno(goal.id, criterios_cumplidos_este_turno=1)
    after = store.registrar_turno(goal.id)
    assert after.status == "activo"
    assert after.turnos_sin_avance == 1


def test_registrar_turno_en_objetivo_terminal_lanza():
    store = GoalStore()
    goal = store.set_goal("sesion-1", "meta", ["criterio"])
    store.cumplir_criterio(goal.id, goal.criterios[0].id)
    with pytest.raises(IDEModosError):
        store.registrar_turno(goal.id)


# --- GoalStore: cancelar / lectura -------------------------------------------


def test_cancel_libera_el_slot_de_la_sesion():
    store = GoalStore()
    goal = store.set_goal("sesion-1", "meta", ["criterio"])
    cancelled = store.cancel(goal.id, "ya no hace falta")
    assert cancelled.status == "cancelado"
    assert cancelled.motivo_fin == "ya no hace falta"
    assert store.get_active_for_session("sesion-1") is None


def test_get_objetivo_inexistente_lanza():
    store = GoalStore()
    with pytest.raises(IDEModosError):
        store.get("no-existe")


def test_public_devuelve_estructura_serializable():
    store = GoalStore()
    goal = store.set_goal("sesion-1", "meta", ["criterio"])
    payload = goal.public()
    assert payload["status"] == "activo"
    assert payload["progreso"] == {"cumplidos": 0, "total": 1}
    assert payload["criterios"][0]["descripcion"] == "criterio"


# --- /effort: nivel de esfuerzo -----------------------------------------------


def test_resolver_nivel_esfuerzo_valida_nombres():
    assert resolver_nivel_esfuerzo("bajo").nombre == "bajo"
    assert resolver_nivel_esfuerzo("ALTO").nombre == "alto"
    assert resolver_nivel_esfuerzo(None).nombre == NIVEL_POR_DEFECTO
    with pytest.raises(IDEModosError):
        resolver_nivel_esfuerzo("turbo")


def test_subir_el_esfuerzo_sube_el_presupuesto_de_tokens():
    base = 1000
    bajo = max_tokens_para_esfuerzo(base, "bajo")
    medio = max_tokens_para_esfuerzo(base, "medio")
    alto = max_tokens_para_esfuerzo(base, "alto")
    assert base < bajo < medio < alto


def test_presupuesto_nunca_baja_del_max_tokens_pedido():
    for nombre in NIVELES_ESFUERZO:
        resultado = max_tokens_para_esfuerzo(1000, nombre)
        assert resultado >= 1000 + NIVELES_ESFUERZO[nombre].reserva_tokens - 1


def test_presupuesto_se_recorta_contra_el_techo_del_perfil(tmp_path):
    ruta_yaml = tmp_path / "modelos.yml"
    ruta_yaml.write_text(
        "perfiles:\n"
        "  ingenieria_software:\n"
        "    contexto_max_tokens: 1200\n",
        encoding="utf-8",
    )
    resultado = max_tokens_para_esfuerzo(1000, "alto", ruta_yaml=ruta_yaml)
    assert resultado == 1200


def test_presupuesto_sin_config_no_recorta():
    resultado = max_tokens_para_esfuerzo(1000, "alto", ruta_yaml="/no/existe.yml")
    assert resultado == 1000 + NIVELES_ESFUERZO["alto"].reserva_tokens


def test_max_tokens_actual_invalido_lanza():
    with pytest.raises(IDEModosError):
        max_tokens_para_esfuerzo(0, "medio")
    with pytest.raises(IDEModosError):
        max_tokens_para_esfuerzo(-5, "medio")


def test_esfuerzo_store_por_defecto_y_fijar():
    store = EsfuerzoStore()
    assert store.obtener("sesion-1").nombre == NIVEL_POR_DEFECTO

    fijado = store.fijar("sesion-1", "alto")
    assert fijado.nombre == "alto"
    assert store.obtener("sesion-1").nombre == "alto"
    # otra sesión no se ve afectada
    assert store.obtener("sesion-2").nombre == NIVEL_POR_DEFECTO


def test_esfuerzo_store_presupuesto_usa_el_nivel_vigente():
    store = EsfuerzoStore()
    store.fijar("sesion-1", "bajo")
    presupuesto_bajo = store.presupuesto("sesion-1", 1000, ruta_yaml="/no/existe.yml")
    store.fijar("sesion-1", "alto")
    presupuesto_alto = store.presupuesto("sesion-1", 1000, ruta_yaml="/no/existe.yml")
    assert presupuesto_bajo < presupuesto_alto


# --- /plan como modo -----------------------------------------------------


def test_modo_planificacion_inactivo_por_defecto_permite_escribir():
    store = ModoPlanificacionStore()
    assert store.esta_activo("sesion-1") is False
    store.verificar_accion_permitida("sesion-1", "escribir_archivo")  # no lanza


def test_modo_planificacion_activo_no_permite_escribir_archivos():
    store = ModoPlanificacionStore()
    store.activar("sesion-1", motivo="explorando antes de tocar nada")
    assert store.esta_activo("sesion-1") is True
    for herramienta in ("escribir_archivo", "editar_archivo", "ejecutar_comando"):
        with pytest.raises(IDEModosError):
            store.verificar_accion_permitida("sesion-1", herramienta)


def test_modo_planificacion_activo_permite_herramientas_de_solo_lectura():
    store = ModoPlanificacionStore()
    store.activar("sesion-1")
    for herramienta in ("leer_archivo", "listar_archivos", "buscar_en_archivos", "buscar_web"):
        store.verificar_accion_permitida("sesion-1", herramienta)  # no lanza


def test_salir_del_modo_planificacion_vuelve_a_permitir_escribir():
    store = ModoPlanificacionStore()
    store.activar("sesion-1")
    store.salir("sesion-1")
    assert store.esta_activo("sesion-1") is False
    store.verificar_accion_permitida("sesion-1", "escribir_archivo")  # no lanza


def test_modo_planificacion_no_afecta_otra_sesion():
    store = ModoPlanificacionStore()
    store.activar("sesion-1")
    assert store.esta_activo("sesion-2") is False
    store.verificar_accion_permitida("sesion-2", "escribir_archivo")  # no lanza


def test_salir_sin_haber_activado_no_lanza():
    store = ModoPlanificacionStore()
    modo = store.salir("sesion-nueva")
    assert modo.activo is False


def test_sincronizar_con_plan_propuesto_activa_el_modo():
    from edecan_companion.ide_plan import PlanStore

    modos = ModoPlanificacionStore()
    planes = PlanStore()
    plan = planes.propose("sesion-1", "meta", ["paso 1", "paso 2"])

    modo = modos.sincronizar_con_plan(plan, "sesion-1")
    assert modo.activo is True
    with pytest.raises(IDEModosError):
        modos.verificar_accion_permitida("sesion-1", "escribir_archivo")


def test_sincronizar_con_plan_aprobado_apaga_el_modo():
    from edecan_companion.ide_plan import PlanStore

    modos = ModoPlanificacionStore()
    planes = PlanStore()
    plan = planes.propose("sesion-1", "meta", ["paso 1", "paso 2"])
    modos.sincronizar_con_plan(plan, "sesion-1")

    aprobado = planes.approve(plan.id)
    modo = modos.sincronizar_con_plan(aprobado, "sesion-1")
    assert modo.activo is False
    modos.verificar_accion_permitida("sesion-1", "escribir_archivo")  # no lanza


def test_sincronizar_sin_plan_activo_apaga_el_modo():
    modos = ModoPlanificacionStore()
    modos.activar("sesion-1")
    modo = modos.sincronizar_con_plan(None, "sesion-1")
    assert modo.activo is False
