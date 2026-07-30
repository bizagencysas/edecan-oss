"""El __init__.py debe re-exportar todos los contratos públicos (§10.5)."""

from __future__ import annotations

import edecan_schemas


def test_reexporta_todos_los_simbolos_publicos():
    nombres_esperados = {
        "PLANES",
        "PlanDef",
        "UNLIMITED",
        "PersonaConfig",
        "TenantOut",
        "UserOut",
        "ChatMessageIn",
        "AgentEvent",
        "AgentEventAdapter",
        "ToolSpec",
        "ToolCallData",
        "ToolResultData",
        "TokenBundle",
        "JobEnvelope",
        "JOB_TYPES",
    }
    for nombre in nombres_esperados:
        assert hasattr(edecan_schemas, nombre), f"falta re-exportar {nombre}"


def test_no_hay_mas_dependencias_que_pydantic():
    import tomllib
    from pathlib import Path

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    deps = data["project"]["dependencies"]
    assert len(deps) == 1
    assert deps[0].startswith("pydantic")
