from __future__ import annotations

import pytest
from edecan_schemas.tools import ToolCallData, ToolResultData, ToolSpec
from pydantic import ValidationError


def test_tool_spec_requiere_campos():
    spec = ToolSpec(name="hora_actual", description="Devuelve la hora actual", input_schema={})
    assert spec.name == "hora_actual"
    with pytest.raises(ValidationError):
        ToolSpec(name="x")


def test_tool_call_data_defaults():
    call = ToolCallData(id="call_1", name="calculadora")
    assert call.arguments == {}


def test_tool_result_data_defaults():
    result = ToolResultData(content="42")
    assert result.data is None
    assert result.requires_confirmation is False
