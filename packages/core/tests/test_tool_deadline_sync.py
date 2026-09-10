from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import edecan_core.agent as agent_module
from edecan_core.agent import Agent
from edecan_core.tools import Tool, ToolContext, ToolRegistry, ToolResult
from edecan_schemas import PersonaConfig, ToolEndEvent


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
    def __init__(self) -> None:
        self._call = 0

    async def stream(self, request: Any):
        self._call += 1
        if self._call == 1:
            yield _Chunk(type="tool_call", tool_call=_ToolCall("call-1", "blocking_search"))
        else:
            yield _Chunk(type="text", text="The search timed out.")


class _Router:
    def __init__(self, provider: _Provider) -> None:
        self.provider = provider

    def resolve(self, alias: str, flags: dict[str, Any]) -> tuple[_Provider, str]:
        return self.provider, "fake-model"


class _BlockingSearch(Tool):
    name = "blocking_search"
    description = "Runs deliberately blocking synchronous work."
    input_schema = {"type": "object", "properties": {}}
    async_run_in_thread = True
    timeout_seconds = 0.03

    def __init__(self) -> None:
        super().__init__()
        self.thread_id: int | None = None
        self.finished = threading.Event()

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        self.thread_id = threading.get_ident()
        time.sleep(0.15)
        self.finished.set()
        return ToolResult(content="late result")


def _ctx() -> ToolContext:
    return ToolContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        session=None,
        settings=None,
        llm=None,
        vault=None,
        extras={},
    )


async def test_threaded_sync_tool_does_not_block_deadline_and_reports_residual_work(
    monkeypatch,
) -> None:
    monkeypatch.setattr(agent_module, "TOOL_PROGRESS_INTERVAL_SECONDS", 0.005)
    tool = _BlockingSearch()
    registry = ToolRegistry()
    registry.register(tool)
    main_thread = threading.get_ident()
    loop_progressed = asyncio.Event()

    async def prove_loop_progress() -> None:
        await asyncio.sleep(0.01)
        loop_progressed.set()

    probe = asyncio.create_task(prove_loop_progress())
    events = [
        event
        async for event in Agent(_Router(_Provider()), registry).run_turn(
            ctx=_ctx(),
            persona=PersonaConfig(),
            history=[],
            user_text="Run the blocking search.",
            flags={},
        )
    ]
    await probe

    endings = [event for event in events if isinstance(event, ToolEndEvent)]
    assert loop_progressed.is_set()
    assert tool.thread_id is not None and tool.thread_id != main_thread
    assert endings
    assert "pudo quedar sin detenerse" in endings[0].result_preview
    assert not tool.finished.is_set()

    # Let the worker finish so the test does not leave executor work behind.
    await asyncio.to_thread(tool.finished.wait, 0.5)
    assert tool.finished.is_set()
