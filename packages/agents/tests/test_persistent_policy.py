import pytest
from edecan_agents.persistent_policy import (
    MAX_HANDOFF_DEPTH,
    validate_handoff,
    validate_worker_budget,
    validate_worker_tools,
)


def test_budget_rejects_negativos_y_claves_desconocidas() -> None:
    assert validate_worker_budget({"compute": 2, "money": 0}) == {"compute": 2, "money": 0}
    with pytest.raises(ValueError):
        validate_worker_budget({"money": -1})
    with pytest.raises(ValueError):
        validate_worker_budget({"secrets": 1})


def test_tools_se_deduplican() -> None:
    assert validate_worker_tools([" buscar_web ", "buscar_web", "hora_actual"]) == [
        "buscar_web",
        "hora_actual",
    ]


def test_handoff_es_estructurado_acotado_y_requiere_aprobacion() -> None:
    envelope = validate_handoff(
        source_worker_id="a",
        destination_worker_id="b",
        task_id="task-1",
        visited_worker_ids=["root"],
    )
    assert envelope["protocol"] == "edecan.worker-handoff.v1"
    assert envelope["requires_human_approval"] is True
    with pytest.raises(ValueError):
        validate_handoff(source_worker_id="a", destination_worker_id="a", task_id="task-1")
    with pytest.raises(ValueError):
        validate_handoff(
            source_worker_id="a",
            destination_worker_id="b",
            task_id="task-1",
            depth=MAX_HANDOFF_DEPTH,
        )
    with pytest.raises(ValueError):
        validate_handoff(
            source_worker_id="a",
            destination_worker_id="root",
            task_id="task-1",
            visited_worker_ids=["root"],
        )
