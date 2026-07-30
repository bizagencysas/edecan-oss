from __future__ import annotations

import pytest
from edecan_forge_kernel.contracts import (
    TERMINAL_TOOL_CALL_STATES,
    AdmissionSubstate,
    ToolCallState,
    validate_admission_substate_transition,
    validate_tool_call_transition,
)

LEGALES = [
    (ToolCallState.REQUESTED, ToolCallState.ADMITTED),
    (ToolCallState.REQUESTED, ToolCallState.REJECTED),
    (ToolCallState.REQUESTED, ToolCallState.CANCELLED),
    (ToolCallState.ADMITTED, ToolCallState.STARTED),
    (ToolCallState.ADMITTED, ToolCallState.REJECTED),
    (ToolCallState.ADMITTED, ToolCallState.CANCELLED),
    (ToolCallState.STARTED, ToolCallState.COMPLETED),
    (ToolCallState.STARTED, ToolCallState.FAILED),
    (ToolCallState.STARTED, ToolCallState.CANCELLED),
    (ToolCallState.STARTED, ToolCallState.SUSPENDED),
    (ToolCallState.STARTED, ToolCallState.ORPHANED),
    (ToolCallState.SUSPENDED, ToolCallState.STARTED),
    (ToolCallState.SUSPENDED, ToolCallState.CANCELLED),
    (ToolCallState.ORPHANED, ToolCallState.COMPLETED),
    (ToolCallState.ORPHANED, ToolCallState.FAILED),
    (ToolCallState.ORPHANED, ToolCallState.UNKNOWN),
]


@pytest.mark.parametrize(("actual", "siguiente"), LEGALES)
def test_transiciones_legales_no_lanzan(actual: ToolCallState, siguiente: ToolCallState) -> None:
    validate_tool_call_transition(actual, siguiente)  # no debe lanzar


def test_todos_los_estados_terminales_no_tienen_salida() -> None:
    for terminal in TERMINAL_TOOL_CALL_STATES:
        for destino in ToolCallState:
            assert (terminal, destino) not in LEGALES, f"{terminal} no debería poder ir a {destino}"


@pytest.mark.parametrize(
    ("actual", "siguiente"),
    [
        (ToolCallState.REQUESTED, ToolCallState.STARTED),  # salta 'admitted'
        (ToolCallState.REQUESTED, ToolCallState.COMPLETED),
        (ToolCallState.ADMITTED, ToolCallState.COMPLETED),  # salta 'started'
        (
            ToolCallState.ADMITTED,
            ToolCallState.ORPHANED,
        ),  # orphaned cuelga de 'started', no de 'admitted'
        (ToolCallState.STARTED, ToolCallState.REJECTED),  # rejected es de admisión, no de ejecución
        (ToolCallState.COMPLETED, ToolCallState.STARTED),  # terminal no reabre
        (ToolCallState.FAILED, ToolCallState.COMPLETED),
        (ToolCallState.CANCELLED, ToolCallState.STARTED),
        (ToolCallState.REJECTED, ToolCallState.ADMITTED),
        (ToolCallState.UNKNOWN, ToolCallState.COMPLETED),
        (
            ToolCallState.SUSPENDED,
            ToolCallState.COMPLETED,
        ),  # una suspendida vuelve a 'started' primero
        (ToolCallState.ORPHANED, ToolCallState.ADMITTED),
    ],
)
def test_transiciones_ilegales_producen_assertion_error(
    actual: ToolCallState, siguiente: ToolCallState
) -> None:
    with pytest.raises(AssertionError, match="transición ilegal"):
        validate_tool_call_transition(actual, siguiente)


def test_subestados_de_admision_deben_entrar_por_validated() -> None:
    validate_admission_substate_transition(None, AdmissionSubstate.VALIDATED)
    with pytest.raises(AssertionError, match="validated"):
        validate_admission_substate_transition(None, AdmissionSubstate.AUTHORIZED)


def test_subestados_de_admision_avanzan_en_orden() -> None:
    validate_admission_substate_transition(
        AdmissionSubstate.VALIDATED, AdmissionSubstate.AUTHORIZED
    )
    validate_admission_substate_transition(
        AdmissionSubstate.AUTHORIZED, AdmissionSubstate.APPROVAL_PENDING
    )
    validate_admission_substate_transition(
        AdmissionSubstate.APPROVAL_PENDING, AdmissionSubstate.QUEUED
    )
    validate_admission_substate_transition(AdmissionSubstate.QUEUED, AdmissionSubstate.DISPATCHED)


def test_approval_pending_es_opcional() -> None:
    """§5.2, línea 3500: `[approval_pending]` entre corchetes es opcional."""
    validate_admission_substate_transition(AdmissionSubstate.AUTHORIZED, AdmissionSubstate.QUEUED)


def test_subestados_de_admision_no_retroceden_ni_saltan() -> None:
    with pytest.raises(AssertionError, match="transición ilegal de subestado"):
        validate_admission_substate_transition(
            AdmissionSubstate.VALIDATED, AdmissionSubstate.DISPATCHED
        )
    with pytest.raises(AssertionError, match="transición ilegal de subestado"):
        validate_admission_substate_transition(
            AdmissionSubstate.QUEUED, AdmissionSubstate.VALIDATED
        )
