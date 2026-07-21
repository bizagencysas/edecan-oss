from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from edecan_core import Agent, Tool, ToolContext, ToolRegistry, ToolResult
from edecan_schemas import ConfirmationRequiredEvent, DoneEvent, PersonaConfig, ToolEndEvent
from edecan_toolkit.creator import CrearArtefactosTool


@dataclass
class FakeToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeChunk:
    type: str
    text: str | None = None
    tool_call: FakeToolCall | None = None
    usage: Any = None


class ScriptedProvider:
    def __init__(self, responses: list[list[FakeChunk]]) -> None:
        self.responses = list(responses)
        self.requests: list[Any] = []

    async def stream(self, request):
        self.requests.append(request)
        for chunk in self.responses.pop(0):
            yield chunk


class FakeRouter:
    def __init__(self, provider: ScriptedProvider) -> None:
        self.provider = provider

    def resolve(self, alias: str, flags: dict[str, Any]):
        return self.provider, "fake-model"


class DummyTool(Tool):
    description = "Capacidad irrelevante para ampliar el catálogo."
    input_schema = {"type": "object", "properties": {}}

    def __init__(self, index: int) -> None:
        self.name = f"dummy_{index}"

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        return ToolResult(content="dummy")


class FakePublishTool(Tool):
    name = "publicar_social"
    description = "Publica el post en una cuenta externa conectada."
    input_schema = {"type": "object", "properties": {"texto": {"type": "string"}}}
    dangerous = True

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        self.calls.append(args)
        return ToolResult(content="Publicado")


class RecordingUploader:
    def __init__(self) -> None:
        self.names: list[str] = []

    async def __call__(self, ctx, *, data: bytes, filename: str, mime: str):
        self.names.append(filename)
        return uuid5(NAMESPACE_URL, filename), filename


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        session=None,
        settings=SimpleNamespace(
            DATA_DIR=str(tmp_path), CREATOR_WORKSPACE_DIR=None, EDECAN_LOCAL_MODE=True
        ),
        llm=None,
        vault=None,
        extras={},
    )


def _registry(*tools: Tool) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    for index in range(13):
        registry.register(DummyTool(index))
    return registry


async def test_frase_de_chat_selecciona_y_ejecuta_el_creator_real(tmp_path: Path) -> None:
    uploader = RecordingUploader()
    creator = CrearArtefactosTool(uploader=uploader)
    provider = ScriptedProvider(
        [
            [
                FakeChunk(
                    type="tool_call",
                    tool_call=FakeToolCall(
                        "create_1",
                        "crear_artefactos",
                        {
                            "solicitud": "Crea una página web y una app para Café Norte",
                            "titulo": "Café Norte",
                            "contenido": "Café de origen y atención cercana.",
                        },
                    ),
                )
            ],
            [FakeChunk(type="text", text="Entregables listos con manifest.")],
        ]
    )
    agent = Agent(FakeRouter(provider), _registry(creator))

    events = [
        event
        async for event in agent.run_turn(
            ctx=_ctx(tmp_path),
            persona=PersonaConfig(),
            history=[],
            user_text="Crea una página web y una app para Café Norte.",
            flags={},
        )
    ]

    assert "crear_artefactos" in {spec.name for spec in provider.requests[0].tools}
    assert [name for name in uploader.names if name.endswith(".zip")] == [
        "cafe-norte-website.zip",
        "cafe-norte-app.zip",
    ]
    assert uploader.names[-1] == "manifest.json"
    assert any(isinstance(event, ToolEndEvent) for event in events)
    assert isinstance(events[-1], DoneEvent)


async def test_publicacion_externa_confirma_antes_de_crear_o_publicar(tmp_path: Path) -> None:
    uploader = RecordingUploader()
    creator = CrearArtefactosTool(uploader=uploader)
    publish = FakePublishTool()
    provider = ScriptedProvider(
        [
            [
                FakeChunk(
                    type="tool_call",
                    tool_call=FakeToolCall(
                        "create_post",
                        "crear_artefactos",
                        {
                            "solicitud": "Crea un post y publícalo",
                            "contenido": "Post verificable.",
                        },
                    ),
                ),
                FakeChunk(
                    type="tool_call",
                    tool_call=FakeToolCall(
                        "publish_post", "publicar_social", {"texto": "Post verificable."}
                    ),
                ),
            ],
            [FakeChunk(type="text", text="Creado y publicado tras aprobación.")],
        ]
    )
    agent = Agent(FakeRouter(provider), _registry(creator, publish))
    ctx = _ctx(tmp_path)

    first = [
        event
        async for event in agent.run_turn(
            ctx=ctx,
            persona=PersonaConfig(),
            history=[],
            user_text="Crea un post y publícalo en X.",
            flags={},
        )
    ]
    confirmation = next(
        event for event in first if isinstance(event, ConfirmationRequiredEvent)
    )
    assert confirmation.tool_call_id == "publish_post"
    assert confirmation.pending_turn is not None
    assert uploader.names == []
    assert publish.calls == []

    resumed = [
        event
        async for event in agent.resume_turn(
            ctx=ctx,
            pending=confirmation.pending_turn,
            approved_tool_call_id="publish_post",
            flags={},
        )
    ]
    assert any(name.endswith(".md") for name in uploader.names)
    assert uploader.names[-1] == "manifest.json"
    assert publish.calls == [{"texto": "Post verificable."}]
    assert isinstance(resumed[-1], DoneEvent)
