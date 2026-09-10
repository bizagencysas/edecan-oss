from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import edecan_automations.runner as runner_module
from edecan_automations.runner import RunnerDeps, run_automation
from edecan_core.agent import Agent
from edecan_core.tools import Tool, ToolContext, ToolRegistry, ToolResult
from edecan_schemas import DoneEvent, ErrorEvent, PersonaConfig


class _ExtraSkillTool(Tool):
    name = "skill_helper"
    description = "A helper supplied by a loaded skill."
    input_schema = {"type": "object", "properties": {}}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        return ToolResult(content="ok")


def _ctx(**extras: Any) -> ToolContext:
    return ToolContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        session=None,
        settings=None,
        llm=None,
        vault=None,
        extras=extras,
    )


async def test_headless_bootstrap_uses_fast_alias_context_skills_and_effective_policy(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}
    budget_gate = object()
    extra_tool = _ExtraSkillTool()

    class ScriptedAgent:
        def __init__(self, llm_router: Any, registry: ToolRegistry, **kwargs: Any) -> None:
            captured["agent_kwargs"] = kwargs

        async def run_turn(self, **kwargs: Any):
            captured["turn_kwargs"] = kwargs
            yield {"type": "text_delta", "text": "done"}
            yield {
                "type": "done",
                "usage": {"input_tokens": 7, "output_tokens": 3},
                "attribution": {
                    "provider": "catalog-provider",
                    "model": "catalog-model",
                    "model_alias": "chat_rapido",
                    "reasoning_effort": "xhigh",
                },
            }

    monkeypatch.setattr(runner_module, "Agent", ScriptedAgent)
    saved: list[tuple[str, dict[str, Any]]] = []

    async def save_run(status: str, detail: dict[str, Any]) -> None:
        saved.append((status, detail))

    persona = SimpleNamespace(instrucciones="Base instructions")
    deps = RunnerDeps(
        ctx=_ctx(),
        llm_router=SimpleNamespace(),
        registry=ToolRegistry(),
        persona=persona,
        flags={},
        save_run=save_run,
        reasoning_effort="xhigh",
        model_policy={"model": "catalog-model"},
        profile_context="Owner profile",
        skills_context="Loaded skill instructions",
        extra_tools=[extra_tool],
        budget_gate=budget_gate,  # type: ignore[arg-type]
    )

    await run_automation(
        {"accion": {"kind": "agent_instruction", "instruccion": "Do the work."}}, deps
    )

    assert captured["agent_kwargs"] == {
        "model_alias": "chat_rapido",
        "reasoning_effort": "xhigh",
        "budget_gate": budget_gate,
    }
    turn = captured["turn_kwargs"]
    assert turn["ctx"].extras["profile_context"] == "Owner profile"
    assert turn["persona"] is not persona
    assert turn["persona"].instrucciones == "Base instructions\n\nLoaded skill instructions"
    assert turn["extra_tools"] == [extra_tool]
    assert turn["seleccion"].modelo == "catalog-model"

    assert saved[0][0] == "done"
    execution = saved[0][1]["execution"]
    assert execution == {
        "model_alias": "chat_rapido",
        "model_policy": {
            "requested_model": "catalog-model",
            "effective_model": "catalog-model",
            "applied": True,
        },
        "provider": "catalog-provider",
        "model": "catalog-model",
        "reasoning_effort": "xhigh",
    }


async def test_headless_explicit_alias_overrides_fast_default(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class ScriptedAgent:
        def __init__(self, llm_router: Any, registry: ToolRegistry, **kwargs: Any) -> None:
            captured.update(kwargs)

        async def run_turn(self, **kwargs: Any):
            yield {"type": "done", "usage": {}, "attribution": {}}

    monkeypatch.setattr(runner_module, "Agent", ScriptedAgent)

    async def save_run(status: str, detail: dict[str, Any]) -> None:
        pass

    deps = RunnerDeps(
        ctx=_ctx(),
        llm_router=SimpleNamespace(),
        registry=ToolRegistry(),
        persona=SimpleNamespace(instrucciones=""),
        flags={},
        save_run=save_run,
        model_alias="worker",
    )

    await run_automation({"accion": {"instruccion": "Do it."}}, deps)

    assert captured["model_alias"] == "worker"


def test_intrinsic_danger_is_immutable_when_effective_authorization_changes(monkeypatch) -> None:
    class SensitiveTool(Tool):
        name = "sensitive"
        description = "Has a sensitive side effect."
        input_schema = {"type": "object", "properties": {}}
        dangerous = True

        async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
            return ToolResult(content="ok")

    tool = SensitiveTool()
    assert tool.intrinsically_dangerous is True

    monkeypatch.setenv("EDECAN_SIN_CONFIRMACIONES", "1")
    registry = ToolRegistry()
    registry.register(tool)

    assert tool.dangerous is False
    assert tool.intrinsically_dangerous is True
    assert runner_module._build_safe_registry(registry, flags={}).get("sensitive") is None
    try:
        tool.intrinsically_dangerous = False  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        raise AssertionError("intrinsically_dangerous must be read-only")


@dataclass
class _ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Chunk:
    type: str
    text: str | None = None
    tool_call: _ToolCall | None = None
    usage: Any | None = None


class _Provider:
    name = "fake-provider"

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: Any):
        self.calls += 1
        if self.calls == 1:
            yield _Chunk(type="tool_call", tool_call=_ToolCall("call-1", "skill_helper"))
        else:
            yield _Chunk(type="text", text="must not be reached")


class _Router:
    def __init__(self, provider: _Provider, model: str = "fake-model") -> None:
        self.provider = provider
        self.model = model

    def resolve(self, alias: str, flags: dict[str, Any]) -> tuple[_Provider, str]:
        return self.provider, self.model


class _CatalogRouter:
    def __init__(self, provider: _Provider) -> None:
        self.provider = provider

    def resolve_with_attribution(
        self,
        alias: str,
        flags: dict[str, Any],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[_Provider, str, dict[str, str]]:
        requested = str((metadata or {}).get("modelo_elegido") or "")
        model = requested if requested == "catalog-model" else "catalog-default"
        return self.provider, model, {"router": "test_catalog", "router_alias": alias}


async def test_valid_worker_model_policy_reaches_catalog_router_and_is_reported() -> None:
    provider = _Provider()
    provider._call = 1
    saved: list[tuple[str, dict[str, Any]]] = []

    async def save_run(status: str, detail: dict[str, Any]) -> None:
        saved.append((status, detail))

    deps = RunnerDeps(
        ctx=_ctx(),
        llm_router=_CatalogRouter(provider),
        registry=ToolRegistry(),
        persona=PersonaConfig(),
        flags={},
        save_run=save_run,
        model_policy={"model": "catalog-model"},
    )

    await run_automation({"accion": {"instruccion": "Answer directly."}}, deps)

    assert saved[0][0] == "done"
    assert saved[0][1]["execution"]["model"] == "catalog-model"
    assert saved[0][1]["execution"]["model_policy"] == {
        "requested_model": "catalog-model",
        "effective_model": "catalog-model",
        "applied": True,
    }


async def test_budget_gate_rejects_next_llm_call_before_provider_is_invoked() -> None:
    provider = _Provider()
    gate_calls = 0

    async def budget_gate(request: Any) -> None:
        nonlocal gate_calls
        gate_calls += 1
        if gate_calls > 1:
            raise RuntimeError("run budget exhausted")

    registry = ToolRegistry()
    registry.register(_ExtraSkillTool())
    events = [
        event
        async for event in Agent(
            _Router(provider), registry, budget_gate=budget_gate
        ).run_turn(
            ctx=_ctx(),
            persona=PersonaConfig(),
            history=[],
            user_text="Use the skill helper.",
            flags={},
        )
    ]

    assert gate_calls == 2
    assert provider.calls == 1
    assert isinstance(events[-1], ErrorEvent)


async def test_done_records_effective_provider_model_alias_and_effort() -> None:
    provider = _Provider()
    provider._call = 1  # the next scripted response is final text, without a tool call
    events = [
        event
        async for event in Agent(
            _Router(provider, model="gpt-5.6-test"),
            ToolRegistry(),
            model_alias="chat_rapido",
            reasoning_effort="xhigh",
        ).run_turn(
            ctx=_ctx(),
            persona=PersonaConfig(),
            history=[],
            user_text="Answer directly.",
            flags={},
        )
    ]

    done = next(event for event in events if isinstance(event, DoneEvent))
    assert done.attribution == {
        "router": "unknown",
        "router_alias": "chat_rapido",
        "provider": "fake-provider",
        "model": "gpt-5.6-test",
        "model_alias": "chat_rapido",
        "reasoning_effort": "xhigh",
    }
