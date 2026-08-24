"""Tests del filtro proactivo determinista (`edecan_automations.proactive`,
PHASE2.md §54-55).

El filtro no depende de LLM ni de base de datos: son funciones puras sobre
``dict``, así que se prueban con asserts directos sin fakes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from edecan_automations.proactive import (
    es_importante,
    es_nuevo,
    le_importa_al_usuario,
    requiere_accion,
    score_event,
    should_notify,
    suprimir_duplicado,
    ya_reportado,
)


def test_score_event_importancia_explicita_gana_al_tipo() -> None:
    assert score_event({"kind": "automation_failed", "importance": 0.2}) == 0.2


def test_score_event_por_tipo() -> None:
    assert score_event({"kind": "automation_failed"}) == 0.9
    assert score_event({"type": "work_failed"}) == 0.9
    assert score_event({"kind": "push_test"}) == 0.0


def test_score_event_tipo_desconocido_puntua_cero() -> None:
    assert score_event({"kind": "algo_raro"}) == 0.0
    assert score_event({}) == 0.0


def test_score_event_acota_al_rango_cero_uno() -> None:
    assert score_event({"importance": 5.0}) == 1.0
    assert score_event({"importance": -3.0}) == 0.0


def test_score_event_ignora_importancia_booleana() -> None:
    # bool es subclase de int: no debe tratarse como puntuación numérica.
    assert score_event({"kind": "automation_failed", "importance": True}) == 0.9


def test_es_importante_respeta_umbral() -> None:
    assert es_importante({"kind": "automation_failed"}) is True
    assert es_importante({"kind": "automation_completed"}) is False
    assert es_importante({}) is False


def test_es_nuevo_nuevo_falso_descarta() -> None:
    assert es_nuevo({"kind": "automation_failed", "nuevo": False}) is False


def test_es_nuevo_sin_marca_temporal_asume_nuevo() -> None:
    assert es_nuevo({"kind": "automation_failed"}) is True


def test_es_nuevo_descarta_evento_viejo() -> None:
    ahora = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    viejo = {"kind": "automation_failed", "occurred_at": ahora - timedelta(days=2)}
    fresco = {"kind": "automation_failed", "created_at": ahora - timedelta(hours=1)}
    assert es_nuevo(viejo, now=ahora) is False
    assert es_nuevo(fresco, now=ahora) is True


def test_le_importa_al_usuario_respeta_cancelaciones() -> None:
    assert le_importa_al_usuario({"kind": "x"}) is True
    assert le_importa_al_usuario({"kind": "x", "suscrito": False}) is False
    assert le_importa_al_usuario({"kind": "x", "silenciado": True}) is False


def test_requiere_accion_respeta_campo_y_tipo() -> None:
    assert requiere_accion({"kind": "automation_failed"}) is True
    assert requiere_accion({"kind": "automation_failed", "requires_action": False}) is False
    assert requiere_accion({"kind": "content_created", "requires_action": True}) is True


def test_ya_reportado_solo_si_lo_declara() -> None:
    assert ya_reportado({"kind": "x", "reportado": True}) is True
    assert ya_reportado({"kind": "x", "reportado": False}) is False
    assert ya_reportado({"kind": "x"}) is False


def test_should_notify_caso_feliz() -> None:
    assert should_notify({"kind": "automation_failed"}) is True


def test_should_notify_rechaza_poco_importante() -> None:
    assert should_notify({"kind": "automation_completed"}) is False
    assert should_notify({"kind": "content_created"}) is False


def test_should_notify_rechaza_ya_reportado() -> None:
    assert should_notify({"kind": "automation_failed", "reportado": True}) is False


def test_should_notify_rechaza_sin_suscripcion() -> None:
    assert should_notify({"kind": "automation_failed", "suscrito": False}) is False


def test_should_notify_rechaza_sin_accion() -> None:
    assert should_notify({"kind": "automation_failed", "requires_action": False}) is False


def test_should_notify_rechaza_viejo() -> None:
    viejo = {
        "kind": "automation_failed",
        "occurred_at": "2000-01-01T00:00:00+00:00",
    }
    assert should_notify(viejo) is False


def test_should_notify_importancia_explicita_puede_rescatar() -> None:
    assert should_notify({"kind": "algo_raro", "importance": 0.9}) is True


def test_suprimir_duplicado_primera_vez_no_suprime() -> None:
    historial: dict[str, datetime] = {}
    ahora = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    evento = {"kind": "automation_failed", "event_id": "abc"}
    assert suprimir_duplicado(historial, evento, now=ahora) is False


def test_suprimir_duplicado_segunda_vez_dentro_de_ventana() -> None:
    historial: dict[str, datetime] = {}
    ahora = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    evento = {"kind": "automation_failed", "event_id": "abc"}
    suprimir_duplicado(historial, evento, now=ahora)
    assert suprimir_duplicado(historial, evento, now=ahora + timedelta(hours=1)) is True


def test_suprimir_duplicado_fuera_de_ventana_deja_pasar() -> None:
    historial: dict[str, datetime] = {}
    ahora = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    evento = {"kind": "automation_failed", "event_id": "abc"}
    suprimir_duplicado(historial, evento, now=ahora)
    assert (
        suprimir_duplicado(historial, evento, now=ahora + timedelta(hours=7)) is False
    )


def test_suprimir_duplicado_claves_distintas_no_colisionan() -> None:
    historial: dict[str, datetime] = {}
    ahora = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    assert suprimir_duplicado(historial, {"kind": "x", "event_id": "a"}, now=ahora) is False
    assert suprimir_duplicado(historial, {"kind": "x", "event_id": "b"}, now=ahora) is False


def test_suprimir_duplicado_sin_clave_no_suprime() -> None:
    historial: dict[str, datetime] = {}
    ahora = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    assert suprimir_duplicado(historial, {"kind": "x"}, now=ahora) is False
    assert suprimir_duplicado(historial, {"kind": "x"}, now=ahora) is False