from __future__ import annotations

import pytest
from edecan_forge_kernel.contracts import (
    SCHEMA_REGISTRY,
    Actor,
    EventDraft,
    SchemaRegistry,
    SchemaRegistryError,
    SessionCreatedPayload,
    TypeDescriptor,
    emit,
)


def test_el_namespace_completo_de_13_dominios_esta_declarado() -> None:
    dominios = {t.split(".", 1)[0] for t in SCHEMA_REGISTRY.all_types()}
    esperados = {
        "session",
        "agent",
        "turn",
        "tool",
        "fs",
        "proc",
        "ctx",
        "plugin",
        "provider",
        "approval",
        "budget",
        "workspace",
        "kernel",
    }
    assert dominios == esperados


def test_los_diez_tipos_tool_call_estan_activos() -> None:
    activos_tool = {t for t in SCHEMA_REGISTRY.active_types() if t.startswith("tool.")}
    assert activos_tool == {
        "tool.call_requested",
        "tool.call_admitted",
        "tool.call_rejected",
        "tool.call_started",
        "tool.call_suspended",
        "tool.call_completed",
        "tool.call_failed",
        "tool.call_cancelled",
        "tool.call_orphaned",
        "tool.call_unknown",
    }


def test_tipo_no_declarado_revienta_al_construir_un_event_draft(actor_agente: Actor) -> None:
    """Es el 'test que falla si alguien añade un tipo sin registrarlo': construir un
    `EventDraft` con un `type` que nadie registró en `SCHEMA_REGISTRY` falla en el constructor,
    no en tiempo de escritura al journal."""
    with pytest.raises(Exception, match="no declarado"):
        EventDraft(
            stream_id="s1",
            v=1,
            type="tool.call_teleported",
            cls="fact",
            actor=actor_agente,
            correlation_id="corr-1",
            payload_inline={"call_id": "abc"},
        )


def test_tipo_reservado_no_activo_revienta_al_emitir(actor_agente: Actor) -> None:
    with pytest.raises(Exception, match="no está activo"):
        emit(
            "agent.spawned",
            actor=actor_agente,
            stream_id="s1",
            correlation_id="corr-1",
            causation_id=None,
            payload=SessionCreatedPayload(session_id="s1"),
        )


def test_registro_sellado_rechaza_altas_nuevas() -> None:
    registro = SchemaRegistry()
    registro.register(
        TypeDescriptor(
            type="demo.hecho",
            v=1,
            cls="fact",
            status="active",
            requires_strict=False,
            payload_model=SessionCreatedPayload,
        )
    )
    registro.seal()
    with pytest.raises(SchemaRegistryError, match="sellado"):
        registro.register(
            TypeDescriptor(
                type="demo.otro",
                v=1,
                cls="fact",
                status="reserved",
                requires_strict=False,
                payload_model=SessionCreatedPayload,
            )
        )


def test_descriptor_de_tipo_no_declarado_lanza() -> None:
    registro = SchemaRegistry()
    with pytest.raises(SchemaRegistryError, match="no declarado"):
        registro.descriptor("nada.aqui")


def test_is_active_falso_para_reservado_y_para_inexistente() -> None:
    assert SCHEMA_REGISTRY.is_active("agent.spawned") is False
    assert SCHEMA_REGISTRY.is_active("no.existe") is False
    assert SCHEMA_REGISTRY.is_active("tool.call_requested") is True
