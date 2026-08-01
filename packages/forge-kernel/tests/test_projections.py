"""`projections.py`: fold determinista, idempotencia ante entrega doble, y reconstrucción desde
cero — incluida la reconstrucción en OTRO PROCESO con `PYTHONHASHSEED` distinto, para que un
`dict`/`set` desordenado en algún punto del fold no pueda colarse sin que un test lo note."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from edecan_forge_kernel.contracts import (
    Actor,
    AdmissionSubstate,
    EffectClass,
    ToolCallAdmittedPayload,
    ToolCallCompletedPayload,
    ToolCallFailedPayload,
    ToolCallRequestedPayload,
    ToolCallStartedPayload,
    emit,
)
from edecan_forge_kernel.projections import (
    BudgetLedgerProjection,
    Projection,
    SessionTimelineProjection,
)

from tests.conftest import FakeJournal

PAQUETE_FORGE_KERNEL = Path(__file__).resolve().parent.parent


async def _construir_journal_de_ejemplo(actor: Actor) -> FakeJournal:
    """Dos llamadas a herramienta en la misma sesión, con desenlaces distintos (una completa,
    otra falla) y `EffectClass` distintas — suficiente para que `session_timeline` tenga varias
    entradas atribuidas y `budget_ledger` tenga más de una fila que tallar."""
    journal = FakeJournal()
    seq = 0

    async def _publicar(draft) -> None:
        nonlocal seq
        resultado = await journal.append([draft], stream_id="s1", expected_seq=seq, lease_epoch=1)
        assert resultado.accepted
        seq = resultado.to_seq

    from edecan_forge_kernel.contracts import SessionCreatedPayload

    await _publicar(
        emit(
            "session.created",
            actor=actor,
            stream_id="s1",
            correlation_id="corr-0",
            causation_id=None,
            payload=SessionCreatedPayload(session_id="s1"),
        )
    )
    await _publicar(
        emit(
            "tool.call_requested",
            actor=actor,
            stream_id="s1",
            correlation_id="corr-1",
            causation_id=None,
            payload=ToolCallRequestedPayload(
                call_id="call-1", tool_id="fs.read", attempt=1, effect_class=EffectClass.SAFE
            ),
        )
    )
    await _publicar(
        emit(
            "tool.call_admitted",
            actor=actor,
            stream_id="s1",
            correlation_id="corr-1",
            causation_id=None,
            payload=ToolCallAdmittedPayload(
                call_id="call-1", admission=AdmissionSubstate.VALIDATED
            ),
        )
    )
    await _publicar(
        emit(
            "tool.call_started",
            actor=actor,
            stream_id="s1",
            correlation_id="corr-1",
            causation_id=None,
            payload=ToolCallStartedPayload(call_id="call-1", attempt=1),
        )
    )
    await _publicar(
        emit(
            "tool.call_completed",
            actor=actor,
            stream_id="s1",
            correlation_id="corr-1",
            causation_id=None,
            payload=ToolCallCompletedPayload(call_id="call-1", score=0),
        )
    )
    await _publicar(
        emit(
            "tool.call_requested",
            actor=actor,
            stream_id="s1",
            correlation_id="corr-2",
            causation_id=None,
            payload=ToolCallRequestedPayload(
                call_id="call-2",
                tool_id="net.deploy",
                attempt=1,
                effect_class=EffectClass.EXTERNAL_EFFECT,
            ),
        )
    )
    await _publicar(
        emit(
            "tool.call_failed",
            actor=actor,
            stream_id="s1",
            correlation_id="corr-2",
            causation_id=None,
            payload=ToolCallFailedPayload(call_id="call-2", error_code="TIMEOUT"),
        )
    )
    return journal


@pytest.mark.asyncio
async def test_session_timeline_atribuye_effect_class_a_eventos_terminales(
    actor_agente: Actor,
) -> None:
    journal = await _construir_journal_de_ejemplo(actor_agente)
    timeline = SessionTimelineProjection.rebuild(journal.appended)

    entradas = timeline.timeline("s1")
    assert [e.type for e in entradas] == [
        "session.created",
        "tool.call_requested",
        "tool.call_admitted",
        "tool.call_started",
        "tool.call_completed",
        "tool.call_requested",
        "tool.call_failed",
    ]
    completado = next(e for e in entradas if e.type == "tool.call_completed")
    assert completado.call_id == "call-1"
    assert completado.effect_class == EffectClass.SAFE  # heredado del `requested`, no repetido

    fallado = next(e for e in entradas if e.type == "tool.call_failed")
    assert fallado.effect_class == EffectClass.EXTERNAL_EFFECT


@pytest.mark.asyncio
async def test_budget_ledger_tally_por_effect_class_y_desenlace(actor_agente: Actor) -> None:
    journal = await _construir_journal_de_ejemplo(actor_agente)
    ledger = BudgetLedgerProjection.rebuild(journal.appended)

    tally_safe = ledger.tally_for("s1", EffectClass.SAFE)
    assert tally_safe["requested"] == 1
    assert tally_safe["completed"] == 1
    assert tally_safe["failed"] == 0

    tally_externo = ledger.tally_for("s1", EffectClass.EXTERNAL_EFFECT)
    assert tally_externo["requested"] == 1
    assert tally_externo["failed"] == 1
    assert tally_externo["completed"] == 0


@pytest.mark.asyncio
async def test_consumidor_idempotente_ante_entrega_doble(actor_agente: Actor) -> None:
    journal = await _construir_journal_de_ejemplo(actor_agente)
    timeline = SessionTimelineProjection()
    for evento in journal.appended:
        timeline.apply(evento)

    hash_tras_primera_pasada = timeline.state_hash()
    cursor_tras_primera_pasada = timeline.last_applied_seq("s1")
    assert cursor_tras_primera_pasada == journal.appended[-1].seq

    # Re-entrega COMPLETA de la misma secuencia (at-least-once, §1.4) — no debe cambiar nada.
    for evento in journal.appended:
        timeline.apply(evento)

    assert timeline.state_hash() == hash_tras_primera_pasada
    assert timeline.last_applied_seq("s1") == cursor_tras_primera_pasada
    assert len(timeline.timeline("s1")) == len(journal.appended)  # ninguna entrada duplicada

    # Re-entrega de UN SOLO evento intermedio también es inofensiva.
    timeline.apply(journal.appended[2])
    assert timeline.state_hash() == hash_tras_primera_pasada


@pytest.mark.asyncio
async def test_rebuild_es_insensible_al_orden_de_entrada(actor_agente: Actor) -> None:
    """`rebuild()` ordena por `(stream_id, seq)` antes de plegar — alimentarlo ya ordenado o al
    revés debe dar el mismo `state_hash`."""
    journal = await _construir_journal_de_ejemplo(actor_agente)
    en_orden = SessionTimelineProjection.rebuild(journal.appended)
    al_reves = SessionTimelineProjection.rebuild(list(reversed(journal.appended)))
    assert en_orden.state_hash() == al_reves.state_hash()


def test_projection_base_es_abstracta() -> None:
    with pytest.raises(TypeError):
        Projection()  # type: ignore[abstract]


@pytest.mark.asyncio
async def test_reconstruccion_determinista_en_otro_proceso(actor_agente: Actor) -> None:
    """Reconstruye `session_timeline` y `budget_ledger` en DOS subprocesos con
    `PYTHONHASHSEED` explícitamente distinto entre sí (y del proceso de test), y compara los
    tres `state_hash()` resultantes. Si algo del fold dependiera de orden de iteración de un
    `dict`/`set` no determinista, los hashes divergirían aquí."""
    journal = await _construir_journal_de_ejemplo(actor_agente)
    eventos_json = json.dumps([e.model_dump(mode="json") for e in journal.appended])

    def _ejecutar_en_subproceso(hash_seed: str) -> dict[str, str]:
        resultado = subprocess.run(
            [sys.executable, "-m", "tests._projection_rebuild_worker"],
            cwd=str(PAQUETE_FORGE_KERNEL),
            input=eventos_json,
            capture_output=True,
            text=True,
            env={**_env_base(), "PYTHONHASHSEED": hash_seed},
            timeout=60,
        )
        assert resultado.returncode == 0, resultado.stderr
        return json.loads(resultado.stdout)

    salida_1 = _ejecutar_en_subproceso("111")
    salida_2 = _ejecutar_en_subproceso("222")

    assert salida_1 == salida_2

    # Y coincide con lo calculado en ESTE proceso (con el PYTHONHASHSEED que sea que tenga).
    timeline_local = SessionTimelineProjection.rebuild(journal.appended)
    ledger_local = BudgetLedgerProjection.rebuild(journal.appended)
    assert salida_1["session_timeline"] == timeline_local.state_hash()
    assert salida_1["budget_ledger"] == ledger_local.state_hash()


def _env_base() -> dict[str, str]:
    import os

    # Copia el entorno real (PATH, VIRTUAL_ENV, etc.) para que `sys.executable` resuelva el
    # mismo intérprete/venv que está corriendo pytest; solo `PYTHONHASHSEED` se sobreescribe.
    return dict(os.environ)
