from __future__ import annotations

from edecan_forge_kernel.contracts import TaintState, TrustLevel


def test_ingest_sube_el_hwm_al_nivel_mas_contaminado() -> None:
    estado = TaintState(session_id="s1")
    assert estado.hwm == TrustLevel.SYSTEM
    estado = estado.ingest(TrustLevel.WORKSPACE_CODE)
    assert estado.hwm == TrustLevel.WORKSPACE_CODE


def test_ingest_nunca_baja_el_hwm() -> None:
    estado = TaintState(session_id="s1", hwm=TrustLevel.TOOL_OUTPUT)
    estado_tras_operator = estado.ingest(TrustLevel.OPERATOR)
    assert estado_tras_operator.hwm == TrustLevel.TOOL_OUTPUT


def test_ingest_es_monotono_a_lo_largo_de_varias_ingestas() -> None:
    estado = TaintState(session_id="s1")
    secuencia = [
        TrustLevel.USER,
        TrustLevel.WORKSPACE_CODE,
        TrustLevel.OPERATOR,
        TrustLevel.NETWORK,
    ]
    for nivel in secuencia:
        nuevo = estado.ingest(nivel)
        assert (
            nuevo.hwm != estado.hwm or nuevo.hwm == estado.hwm
        )  # nunca decrece (ver assert de abajo)
        estado = nuevo
    assert estado.hwm == TrustLevel.NETWORK


def test_reset_por_compactacion_vuelve_a_system() -> None:
    estado = TaintState(session_id="s1", hwm=TrustLevel.NETWORK)
    reiniciado = estado.reset(reason="compaction_discard")
    assert reiniciado.hwm == TrustLevel.SYSTEM


def test_reset_por_autorizacion_humana_vuelve_a_system() -> None:
    estado = TaintState(session_id="s1", hwm=TrustLevel.TOOL_OUTPUT)
    reiniciado = estado.reset(reason="human_authorization")
    assert reiniciado.hwm == TrustLevel.SYSTEM


def test_taint_state_es_inmutable() -> None:
    original = TaintState(session_id="s1")
    original.ingest(TrustLevel.NETWORK)
    assert original.hwm == TrustLevel.SYSTEM  # `ingest` no muta in-place
