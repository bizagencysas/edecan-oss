from __future__ import annotations

from edecan_core.llm_types import CompletionRequest


def test_completion_request_matches_provider_reasoning_contract() -> None:
    request = CompletionRequest(model="model", reasoning_effort="low")

    assert request.reasoning_effort == "low"
