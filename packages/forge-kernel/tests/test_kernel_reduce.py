from __future__ import annotations

import pytest
from edecan_forge_kernel.contracts import (
    Actor,
    AdmissionSubstate,
    CasRef,
    Command,
    EffectClass,
    KernelState,
    Stamp,
    ToolCallState,
    reduce,
)


def _cmd(kind: str, args: dict, *, actor: Actor, stamp: Stamp, corr: str = "corr-1") -> Command:
    return Command(
        kind=kind, stream_id="agent-1", actor=actor, stamp=stamp, correlation_id=corr, args=args
    )


def test_comando_desconocido_produce_rejection(
    estado_vacio: KernelState, actor_agente: Actor, stamp: Stamp
) -> None:
    decision = reduce(estado_vacio, _cmd("no.existe", {}, actor=actor_agente, stamp=stamp))
    assert decision.rejection is not None
    assert decision.rejection.code == "UNKNOWN_COMMAND"
    assert decision.events == ()
    assert decision.effects == ()


def test_session_create_produce_evento_y_actualiza_estado(
    estado_vacio: KernelState, actor_agente: Actor, stamp: Stamp
) -> None:
    decision = reduce(
        estado_vacio, _cmd("session.create", {"session_id": "s1"}, actor=actor_agente, stamp=stamp)
    )
    assert decision.rejection is None
    assert len(decision.events) == 1
    assert decision.events[0].type == "session.created"
    assert "s1" in decision.state.sessions
    assert estado_vacio.sessions == {}  # el estado original no se muta


def test_session_create_duplicada_es_rechazada(
    estado_vacio: KernelState, actor_agente: Actor, stamp: Stamp
) -> None:
    d1 = reduce(
        estado_vacio, _cmd("session.create", {"session_id": "s1"}, actor=actor_agente, stamp=stamp)
    )
    d2 = reduce(
        d1.state, _cmd("session.create", {"session_id": "s1"}, actor=actor_agente, stamp=stamp)
    )
    assert d2.rejection is not None
    assert d2.rejection.code == "ALREADY_EXISTS"


def _ciclo_hasta_dispatched(
    estado: KernelState, actor: Actor, stamp: Stamp, call_id: str
) -> KernelState:
    estado = reduce(
        estado,
        _cmd(
            "tool.call_request",
            {"call_id": call_id, "tool_id": "fs.read_file", "effect_class": EffectClass.SAFE},
            actor=actor,
            stamp=stamp,
        ),
    ).state
    estado = reduce(
        estado, _cmd("tool.call_admit", {"call_id": call_id}, actor=actor, stamp=stamp)
    ).state
    for destino in ("authorized", "queued", "dispatched"):
        estado = reduce(
            estado,
            _cmd(
                "tool.call_advance_admission",
                {"call_id": call_id, "to": destino},
                actor=actor,
                stamp=stamp,
            ),
        ).state
    return estado


def test_ciclo_de_vida_feliz_completo(
    estado_vacio: KernelState, actor_agente: Actor, stamp: Stamp
) -> None:
    call_id = "call-1"
    estado = _ciclo_hasta_dispatched(estado_vacio, actor_agente, stamp, call_id)
    registro = estado.tool_calls[call_id]
    assert registro.state is ToolCallState.ADMITTED
    assert registro.admission is AdmissionSubstate.DISPATCHED

    decision_start = reduce(
        estado, _cmd("tool.call_start", {"call_id": call_id}, actor=actor_agente, stamp=stamp)
    )
    assert decision_start.events[0].type == "tool.call_started"
    assert decision_start.state.tool_calls[call_id].state is ToolCallState.STARTED
    assert decision_start.state.tool_calls[call_id].admission is None

    decision_complete = reduce(
        decision_start.state,
        _cmd(
            "tool.call_complete", {"call_id": call_id, "score": 0}, actor=actor_agente, stamp=stamp
        ),
    )
    assert decision_complete.events[0].type == "tool.call_completed"
    assert decision_complete.state.tool_calls[call_id].state is ToolCallState.COMPLETED


def test_start_sin_pasar_por_dispatched_es_rechazado(
    estado_vacio: KernelState, actor_agente: Actor, stamp: Stamp
) -> None:
    call_id = "call-2"
    decision = reduce(
        estado_vacio,
        _cmd(
            "tool.call_request",
            {"call_id": call_id, "tool_id": "fs.grep", "effect_class": EffectClass.SAFE},
            actor=actor_agente,
            stamp=stamp,
        ),
    )
    estado = reduce(
        decision.state,
        _cmd("tool.call_admit", {"call_id": call_id}, actor=actor_agente, stamp=stamp),
    ).state
    # todavía en 'validated', no en 'dispatched'
    resultado = reduce(
        estado, _cmd("tool.call_start", {"call_id": call_id}, actor=actor_agente, stamp=stamp)
    )
    assert resultado.rejection is not None
    assert resultado.rejection.code == "INVALID_STATE"


def test_suspender_y_reanudar(estado_vacio: KernelState, actor_agente: Actor, stamp: Stamp) -> None:
    call_id = "call-3"
    estado = _ciclo_hasta_dispatched(estado_vacio, actor_agente, stamp, call_id)
    estado = reduce(
        estado, _cmd("tool.call_start", {"call_id": call_id}, actor=actor_agente, stamp=stamp)
    ).state

    checkpoint = str(CasRef.from_bytes(b"checkpoint-1"))
    decision_suspend = reduce(
        estado,
        _cmd(
            "tool.call_suspend",
            {"call_id": call_id, "checkpoint_ref": checkpoint},
            actor=actor_agente,
            stamp=stamp,
        ),
    )
    assert decision_suspend.state.tool_calls[call_id].state is ToolCallState.SUSPENDED

    decision_resume = reduce(
        decision_suspend.state,
        _cmd("tool.call_resume", {"call_id": call_id}, actor=actor_agente, stamp=stamp),
    )
    reanudada = decision_resume.state.tool_calls[call_id]
    assert reanudada.state is ToolCallState.STARTED
    assert reanudada.attempt == 2


def test_suspender_sin_checkpoint_es_rechazado(
    estado_vacio: KernelState, actor_agente: Actor, stamp: Stamp
) -> None:
    call_id = "call-4"
    estado = _ciclo_hasta_dispatched(estado_vacio, actor_agente, stamp, call_id)
    estado = reduce(
        estado, _cmd("tool.call_start", {"call_id": call_id}, actor=actor_agente, stamp=stamp)
    ).state
    resultado = reduce(
        estado, _cmd("tool.call_suspend", {"call_id": call_id}, actor=actor_agente, stamp=stamp)
    )
    assert resultado.rejection is not None
    assert resultado.rejection.code == "INVALID_ARGS"


def test_cancelar_antes_de_despachar(
    estado_vacio: KernelState, actor_agente: Actor, stamp: Stamp
) -> None:
    call_id = "call-5"
    decision = reduce(
        estado_vacio,
        _cmd(
            "tool.call_request",
            {"call_id": call_id, "tool_id": "vcs.git", "effect_class": EffectClass.REVERSIBLE},
            actor=actor_agente,
            stamp=stamp,
        ),
    )
    cancelada = reduce(
        decision.state,
        _cmd(
            "tool.call_cancel",
            {"call_id": call_id, "reason_code": "user_abort"},
            actor=actor_agente,
            stamp=stamp,
        ),
    )
    assert cancelada.state.tool_calls[call_id].state is ToolCallState.CANCELLED


def test_rechazar_durante_admision(
    estado_vacio: KernelState, actor_agente: Actor, stamp: Stamp
) -> None:
    call_id = "call-6"
    estado = reduce(
        estado_vacio,
        _cmd(
            "tool.call_request",
            {
                "call_id": call_id,
                "tool_id": "proc.terminal",
                "effect_class": EffectClass.DESTRUCTIVE,
            },
            actor=actor_agente,
            stamp=stamp,
        ),
    ).state
    estado = reduce(
        estado, _cmd("tool.call_admit", {"call_id": call_id}, actor=actor_agente, stamp=stamp)
    ).state
    rechazada = reduce(
        estado,
        _cmd(
            "tool.call_reject",
            {"call_id": call_id, "reason_code": "schema_invalid"},
            actor=actor_agente,
            stamp=stamp,
        ),
    )
    assert rechazada.state.tool_calls[call_id].state is ToolCallState.REJECTED
    assert rechazada.events[0].type == "tool.call_rejected"


@pytest.mark.parametrize("outcome", ["completed", "failed", "unknown"])
def test_orfanato_y_resolucion(
    estado_vacio: KernelState, actor_agente: Actor, stamp: Stamp, outcome: str
) -> None:
    call_id = f"call-orphan-{outcome}"
    estado = _ciclo_hasta_dispatched(estado_vacio, actor_agente, stamp, call_id)
    estado = reduce(
        estado, _cmd("tool.call_start", {"call_id": call_id}, actor=actor_agente, stamp=stamp)
    ).state
    huerfana = reduce(
        estado, _cmd("tool.call_orphan", {"call_id": call_id}, actor=actor_agente, stamp=stamp)
    )
    assert huerfana.state.tool_calls[call_id].state is ToolCallState.ORPHANED

    resuelta = reduce(
        huerfana.state,
        _cmd(
            "tool.call_resolve_orphan",
            {"call_id": call_id, "outcome": outcome},
            actor=actor_agente,
            stamp=stamp,
        ),
    )
    assert resuelta.rejection is None
    esperado = {
        "completed": ToolCallState.COMPLETED,
        "failed": ToolCallState.FAILED,
        "unknown": ToolCallState.UNKNOWN,
    }
    assert resuelta.state.tool_calls[call_id].state is esperado[outcome]


def test_transicion_ilegal_dentro_de_reduce_lanza_assertion_error(
    estado_vacio: KernelState, actor_agente: Actor, stamp: Stamp
) -> None:
    """`reduce` no envuelve `AssertionError` en un `Rejection`: es un bug de programa, tal como
    fija §1.6, línea 1588, y así debe propagarse. Se dispara al intentar cancelar una llamada ya
    terminal (`COMPLETED`): el handler de cancelación no re-chequea "¿ya es terminal?" con una
    regla de negocio propia porque esa es exactamente la responsabilidad de la máquina de
    estados pinneada, no de cada comando por separado."""
    call_id = "call-ilegal"
    estado = _ciclo_hasta_dispatched(estado_vacio, actor_agente, stamp, call_id)
    estado = reduce(
        estado, _cmd("tool.call_start", {"call_id": call_id}, actor=actor_agente, stamp=stamp)
    ).state
    estado = reduce(
        estado, _cmd("tool.call_complete", {"call_id": call_id}, actor=actor_agente, stamp=stamp)
    ).state
    with pytest.raises(AssertionError, match="transición ilegal"):
        reduce(
            estado,
            _cmd(
                "tool.call_cancel",
                {"call_id": call_id, "reason_code": "too_late"},
                actor=actor_agente,
                stamp=stamp,
            ),
        )


def test_llamada_inexistente_es_rechazada_con_not_found(
    estado_vacio: KernelState, actor_agente: Actor, stamp: Stamp
) -> None:
    resultado = reduce(
        estado_vacio,
        _cmd("tool.call_admit", {"call_id": "no-existe"}, actor=actor_agente, stamp=stamp),
    )
    assert resultado.rejection is not None
    assert resultado.rejection.code == "NOT_FOUND"


# --------------------------------------------------------------------------------------- #
# Determinismo: mismo estado + mismo comando + mismo Stamp => mismos eventos, byte a byte
# --------------------------------------------------------------------------------------- #


def test_reduce_es_determinista_byte_a_byte(
    estado_vacio: KernelState, actor_agente: Actor, stamp: Stamp
) -> None:
    cmd = _cmd(
        "tool.call_request",
        {"call_id": "call-det", "tool_id": "fs.read_file", "effect_class": EffectClass.SAFE},
        actor=actor_agente,
        stamp=stamp,
    )
    decision_1 = reduce(estado_vacio, cmd)
    decision_2 = reduce(estado_vacio, cmd)

    assert len(decision_1.events) == len(decision_2.events) == 1
    assert decision_1.events[0].canonical_bytes() == decision_2.events[0].canonical_bytes()
    assert decision_1.state.model_dump(mode="json") == decision_2.state.model_dump(mode="json")


def test_reduce_es_determinista_a_lo_largo_de_un_ciclo_completo(
    estado_vacio: KernelState, actor_agente: Actor, stamp: Stamp
) -> None:
    def correr() -> list[bytes]:
        estado = estado_vacio
        rastro: list[bytes] = []
        pasos = [
            (
                "tool.call_request",
                {"call_id": "c", "tool_id": "fs.grep", "effect_class": EffectClass.SAFE},
            ),
            ("tool.call_admit", {"call_id": "c"}),
            ("tool.call_advance_admission", {"call_id": "c", "to": "authorized"}),
            ("tool.call_advance_admission", {"call_id": "c", "to": "queued"}),
            ("tool.call_advance_admission", {"call_id": "c", "to": "dispatched"}),
            ("tool.call_start", {"call_id": "c"}),
            ("tool.call_complete", {"call_id": "c"}),
        ]
        for kind, args in pasos:
            decision = reduce(estado, _cmd(kind, args, actor=actor_agente, stamp=stamp))
            rastro.extend(d.canonical_bytes() for d in decision.events)
            estado = decision.state
        return rastro

    assert correr() == correr()


def test_dos_stamps_distintos_producen_eventos_distintos(
    estado_vacio: KernelState, actor_agente: Actor
) -> None:
    """El determinismo es respecto al `Stamp`, no absoluto: dos `id_seed` distintos deben poder
    producir ULIDs/ids distintos aguas abajo (aquí se verifica indirectamente vía el estado del
    evento, que sí es igual, y se deja constancia de que el `Stamp` es la única fuente de
    variación permitida)."""
    stamp_a = Stamp(ts_physical=1, id_seed=b"a" * 16, lease_epoch=1, observed_lamport=0)
    stamp_b = Stamp(ts_physical=2, id_seed=b"b" * 16, lease_epoch=1, observed_lamport=0)
    cmd_a = _cmd("session.create", {"session_id": "s1"}, actor=actor_agente, stamp=stamp_a)
    cmd_b = _cmd("session.create", {"session_id": "s1"}, actor=actor_agente, stamp=stamp_b)
    decision_a = reduce(estado_vacio, cmd_a)
    decision_b = reduce(estado_vacio, cmd_b)
    # El contenido de dominio (created_at_us) sí varía con el Stamp, así que los estados
    # resultantes difieren de forma predecible y no accidental.
    assert decision_a.state.sessions["s1"].created_at_us == 1
    assert decision_b.state.sessions["s1"].created_at_us == 2
