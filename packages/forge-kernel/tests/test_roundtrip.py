from __future__ import annotations

import pytest
from edecan_forge_kernel.contracts import (
    Actor,
    AuthzFacts,
    Budget,
    CasRef,
    Effect,
    EffectClass,
    EventDraft,
    Guard,
    Rejection,
    RenderHint,
    SelectionReason,
    ToolResult,
)


@pytest.mark.parametrize(
    "modelo",
    [
        CasRef.from_bytes(b"hola"),
        Budget(usd_micros=500_000, tokens=1200, wall_ms=10, cpu_ms=5, bytes_out=0),
        RenderHint(layout="diff", fields=("path", "hunks"), summary_template=None),
        SelectionReason(strategy="recency", score=0.8, rank=1, rule_id=None),
        Rejection(code="NOT_FOUND", message="no existe"),
        Guard(projection_name="tool_calls", key="call-1", expected_state_hash="b2b:" + "0" * 64),
        ToolResult(
            status="ok",
            score=0,
            plan_step_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
            effect_class=EffectClass.SAFE,
        ),
    ],
)
def test_round_trip_json(modelo) -> None:
    tipo = type(modelo)
    volcado = modelo.model_dump_json()
    reconstruido = tipo.model_validate_json(volcado)
    assert reconstruido == modelo


def test_authz_facts_round_trip_y_presupuesto() -> None:
    hechos = AuthzFacts(
        paths_read=("src/a.py",),
        paths_write=(),
        hosts=(("api.github.com", 443, "https"),),
        effect_targets=("deploy:prod:api",),
        secret_refs=(),
        exec_binaries=("git",),
        overflow=False,
    )
    reconstruido = AuthzFacts.model_validate_json(hechos.model_dump_json())
    assert reconstruido == hechos
    assert hechos.fits_budget()


def test_authz_facts_rechaza_effect_target_con_namespace_invalido() -> None:
    with pytest.raises(ValueError, match="namespace inválido"):
        AuthzFacts(effect_targets=("no tiene ni dos puntos",))


def test_effect_con_cas_ref_round_trip(actor_agente: Actor) -> None:
    del actor_agente
    efecto = Effect(
        id="eff-1",
        kind="cas.put",
        capability_id="cap-1",
        args={"tamano": 128},
        payload_ref=CasRef.from_bytes(b"payload"),
        deadline_us=1_000,
        idempotency_key=None,
    )
    reconstruido = Effect.model_validate_json(efecto.model_dump_json())
    assert reconstruido == efecto


def test_event_draft_round_trip(actor_agente: Actor) -> None:
    from edecan_forge_kernel.contracts import SessionCreatedPayload, emit

    draft = emit(
        "session.created",
        actor=actor_agente,
        stream_id="s1",
        correlation_id="corr-1",
        causation_id=None,
        payload=SessionCreatedPayload(session_id="s1"),
    )
    reconstruido = EventDraft.model_validate_json(draft.model_dump_json())
    assert reconstruido == draft
    assert reconstruido.canonical_bytes() == draft.canonical_bytes()
