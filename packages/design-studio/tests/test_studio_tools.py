from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from edecan_design_studio import get_all_tools
from edecan_design_studio.studio_tools import (
    PREMIUM_STUDIO_CAPABILITIES,
    SAFE_STUDIO_CAPABILITIES,
    AdministrarProyectoCreativoTool,
    CrearEditarProyectoCreativoTool,
    UsarEstudioCreativoPremiumTool,
    UsarEstudioCreativoTool,
    VerEstudioCreativoTool,
    VerProyectosCreativosTool,
    _is_safe_github_repository,
    _is_safe_video_source_url,
)


class FakeClient:
    def __init__(self, output: Path, calls: list[dict]) -> None:
        self.output = output
        self.calls = calls

    async def discover(self):
        return [{"name": f"cap-{index}"} for index in range(37)]

    async def execute(self, capability, arguments, *, credentials=None):
        self.output.mkdir(parents=True, exist_ok=True)
        artifact = self.output / "resultado.png"
        artifact.write_bytes(b"\x89PNG\r\n\x1a\nreal")
        self.calls.append(
            {
                "capability": capability,
                "arguments": arguments,
                "credentials": credentials,
            }
        )
        return {"ok": True, "files": [str(artifact)], "absolute": str(self.output)}


@dataclass
class Downloaded:
    filename: str = "entrada.png"
    contenido: bytes = b"image"


def _local_ctx(make_ctx, tmp_path: Path):
    ctx = make_ctx()
    ctx.settings = SimpleNamespace(EDECAN_LOCAL_MODE=True, DATA_DIR=str(tmp_path))
    return ctx


async def _credentials(_ctx):
    return {"ANTHROPIC_API_KEY": "vault-key"}


async def test_studio_safe_delivers_private_artifact_without_local_path(
    make_ctx, tmp_path: Path
) -> None:
    ctx = _local_ctx(make_ctx, tmp_path)
    calls: list[dict] = []
    uploads: list[dict] = []

    def client_factory(_ctx, output, _state):
        return FakeClient(output, calls)

    async def uploader(_ctx, *, data, filename, mime):
        uploads.append({"data": data, "filename": filename, "mime": mime})
        return uuid4(), filename

    tool = UsarEstudioCreativoTool(
        client_factory=client_factory,
        credentials_resolver=_credentials,
        uploader=uploader,
    )
    result = await tool.run(
        ctx,
        {"capacidad": "fydesign_generate", "argumentos": {"prompt": "hola"}},
    )

    assert calls[0]["capability"] == "fydesign_generate"
    assert calls[0]["arguments"] == {"prompt": "hola"}
    assert calls[0]["credentials"]["ANTHROPIC_API_KEY"] == "vault-key"
    assert calls[0]["credentials"]["FYDESIGN_LLM_BRIDGE_URL"].startswith(
        "http://127.0.0.1:"
    )
    assert calls[0]["credentials"]["FYDESIGN_LLM_BRIDGE_TOKEN"]
    assert uploads[0]["mime"] == "image/png"
    assert result.data["artifacts"][0]["filename"] == "resultado.png"
    assert result.data["result"]["files"] == ["resultado.png"]
    assert str(tmp_path) not in str(result.data)
    assert result.presentation[0]["media_kind"] == "image"


async def test_studio_delivers_at_most_the_shared_limit_of_64_artifacts(
    make_ctx, tmp_path: Path
) -> None:
    ctx = _local_ctx(make_ctx, tmp_path)
    uploads: list[str] = []

    class ManyArtifactsClient(FakeClient):
        async def execute(self, capability, arguments, *, credentials=None):
            self.output.mkdir(parents=True, exist_ok=True)
            files = []
            for index in range(70):
                artifact = self.output / f"resultado-{index:02}.png"
                artifact.write_bytes(b"\x89PNG\r\n\x1a\n")
                files.append(str(artifact))
            return {"ok": True, "files": files}

    async def uploader(_ctx, *, data, filename, mime):
        uploads.append(filename)
        return uuid4(), filename

    tool = UsarEstudioCreativoTool(
        client_factory=lambda _ctx, output, _state: ManyArtifactsClient(output, []),
        credentials_resolver=_credentials,
        uploader=uploader,
    )
    result = await tool.run(
        ctx,
        {"capacidad": "fydesign_generate", "argumentos": {"prompt": "colección"}},
    )

    assert len(uploads) == 64
    assert len(result.data["artifacts"]) == 64


async def test_attachment_is_downloaded_to_tenant_staging(make_ctx, tmp_path: Path) -> None:
    ctx = _local_ctx(make_ctx, tmp_path)
    calls: list[dict] = []

    class InspectingClient(FakeClient):
        async def execute(self, capability, arguments, *, credentials=None):
            staged = Path(arguments["file"])
            assert staged.read_bytes() == b"image"
            return await super().execute(
                capability, arguments, credentials=credentials
            )

    async def downloader(_session, _settings, _tenant_id, _file_id):
        return Downloaded()

    async def uploader(_ctx, *, data, filename, mime):
        return uuid4(), filename

    tool = UsarEstudioCreativoTool(
        client_factory=lambda _ctx, output, _state: InspectingClient(output, calls),
        credentials_resolver=_credentials,
        uploader=uploader,
        downloader=downloader,
    )
    await tool.run(
        ctx,
        {
            "capacidad": "fydesign_analyze_video",
            "argumentos": {},
            "archivos": {"file": str(uuid4())},
        },
    )

    staged = Path(calls[0]["arguments"]["file"])
    assert staged.is_relative_to(tmp_path / "studio" / str(ctx.tenant_id))
    assert not staged.exists()


async def test_rejects_arbitrary_local_file_before_engine(make_ctx, tmp_path: Path) -> None:
    ctx = _local_ctx(make_ctx, tmp_path)
    calls: list[dict] = []
    tool = UsarEstudioCreativoTool(
        client_factory=lambda _ctx, output, _state: FakeClient(output, calls),
        credentials_resolver=_credentials,
    )

    result = await tool.run(
        ctx,
        {
            "capacidad": "fydesign_analyze_video",
            "argumentos": {"file": "/etc/passwd"},
        },
    )

    assert "no es una URL pública segura" in result.content
    assert calls == []


async def test_rejects_private_network_url_before_engine(make_ctx, tmp_path: Path) -> None:
    ctx = _local_ctx(make_ctx, tmp_path)
    calls: list[dict] = []
    tool = UsarEstudioCreativoTool(
        client_factory=lambda _ctx, output, _state: FakeClient(output, calls),
        credentials_resolver=_credentials,
    )

    result = await tool.run(
        ctx,
        {
            "capacidad": "fydesign_generate",
            "argumentos": {"prompt": "hola", "repo": "http://127.0.0.1/private"},
        },
    )

    assert "URL pública de GitHub" in result.content
    assert calls == []


async def test_repository_and_video_urls_reject_redirect_shapes(monkeypatch) -> None:
    async def public_url(_value: str) -> bool:
        return True

    monkeypatch.setattr(
        "edecan_design_studio.studio_tools._is_safe_remote_url", public_url
    )

    assert await _is_safe_github_repository("openai/codex")
    assert await _is_safe_github_repository("https://github.com/openai/codex")
    assert not await _is_safe_github_repository(
        "https://github.com/redirect?target=http://127.0.0.1"
    )
    assert await _is_safe_video_source_url("https://youtu.be/dQw4w9WgXcQ")
    assert await _is_safe_video_source_url(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    )
    assert not await _is_safe_video_source_url(
        "https://www.youtube.com/redirect?q=http://127.0.0.1"
    )
    assert not await _is_safe_video_source_url("https://cdn.example.com/video.mp4")


async def test_rejects_direct_remote_video_before_ytdlp(make_ctx, tmp_path: Path) -> None:
    ctx = _local_ctx(make_ctx, tmp_path)
    calls: list[dict] = []
    tool = UsarEstudioCreativoTool(
        client_factory=lambda _ctx, output, _state: FakeClient(output, calls),
        credentials_resolver=_credentials,
    )

    result = await tool.run(
        ctx,
        {
            "capacidad": "fydesign_analyze_video",
            "argumentos": {"url": "https://example.com/video.mp4"},
        },
    )

    assert "adjunta el archivo" in result.content.lower()
    assert calls == []


async def test_rejects_private_brand_asset_before_engine(make_ctx, tmp_path: Path) -> None:
    ctx = _local_ctx(make_ctx, tmp_path)
    calls: list[dict] = []
    tool = UsarEstudioCreativoPremiumTool(
        client_factory=lambda _ctx, output, _state: FakeClient(output, calls),
        credentials_resolver=_credentials,
    )

    result = await tool.run(
        ctx,
        {
            "capacidad": "fydesign_register_brand",
            "argumentos": {
                "brand": "Acme",
                "assets": [{"name": "logo", "url": "http://[::1]/logo.png"}],
            },
        },
    )

    assert "no es segura" in result.content
    assert calls == []


async def test_catalog_and_local_mode_gate(make_ctx, tmp_path: Path) -> None:
    ctx = _local_ctx(make_ctx, tmp_path)
    tool = VerEstudioCreativoTool(
        client_factory=lambda _ctx, output, _state: FakeClient(output, [])
    )
    ready = await tool.run(ctx, {})
    assert "37 capacidades" in ready.content

    ctx.settings.EDECAN_LOCAL_MODE = False
    gated = await tool.run(ctx, {})
    assert "app local" in gated.content


def test_registry_and_cost_boundary_are_complete() -> None:
    tools = {tool.name: tool for tool in get_all_tools()}
    assert {
        "ver_estudio_creativo",
        "usar_estudio_creativo",
        "usar_estudio_creativo_premium",
        "ver_proyectos_creativos",
        "crear_editar_proyecto_creativo",
        "administrar_proyecto_creativo",
    } <= tools.keys()
    assert tools["usar_estudio_creativo"].dangerous is False
    assert tools["usar_estudio_creativo_premium"].dangerous is True
    assert tools["crear_editar_proyecto_creativo"].dangerous is False
    assert tools["administrar_proyecto_creativo"].dangerous is True
    assert SAFE_STUDIO_CAPABILITIES.isdisjoint(PREMIUM_STUDIO_CAPABILITIES)
    assert len(SAFE_STUDIO_CAPABILITIES | PREMIUM_STUDIO_CAPABILITIES) == 37


def test_premium_schema_contains_paid_generation() -> None:
    schema = UsarEstudioCreativoPremiumTool.input_schema
    natural, expert = schema["oneOf"]
    assert natural["required"] == ["pedido"]
    assert natural["properties"]["archivos"]["type"] == "array"
    assert "fydesign_video_ad" in expert["properties"]["capacidad"]["enum"]
    assert "fydesign_train_face" in expert["properties"]["capacidad"]["enum"]


def test_project_schema_exposes_mockup_recreation_inputs() -> None:
    properties = CrearEditarProyectoCreativoTool.input_schema["properties"]

    assert properties["screenBriefs"]["maxItems"] == 8
    assert "dashboard" in properties["screenBriefs"]["items"]["properties"]["layout"][
        "enum"
    ]
    assert properties["languages"]["items"]["enum"] == ["en", "es", "pt", "fr"]
    assert properties["theme"]["properties"]["hasDarkMode"]["type"] == "boolean"


async def test_natural_request_uses_real_mcp_schema_and_maps_private_file(
    make_ctx, tmp_path: Path
) -> None:
    ctx = _local_ctx(make_ctx, tmp_path)
    ctx.llm.text = (
        '{"capacidad":"fydesign_edit","argumentos":'
        '{"inputImage":"attachment_1","prompt":"limpia el fondo"}}'
    )
    calls: list[dict] = []

    class NaturalClient(FakeClient):
        async def discover(self):
            return [
                {
                    "name": "fydesign_edit",
                    "description": "Edita una imagen existente.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "inputImage": {"type": "string"},
                            "prompt": {"type": "string"},
                        },
                        "required": ["inputImage", "prompt"],
                    },
                }
            ]

        async def execute(self, capability, arguments, *, credentials=None):
            assert Path(arguments["inputImage"]).read_bytes() == b"image"
            return await super().execute(
                capability, arguments, credentials=credentials
            )

    async def downloader(_session, _settings, _tenant_id, _file_id):
        return Downloaded(filename="producto.png")

    async def uploader(_ctx, *, data, filename, mime):
        return uuid4(), filename

    tool = UsarEstudioCreativoPremiumTool(
        client_factory=lambda _ctx, output, _state: NaturalClient(output, calls),
        credentials_resolver=_credentials,
        uploader=uploader,
        downloader=downloader,
    )
    result = await tool.run(
        ctx,
        {"pedido": "Edita esta foto y limpia el fondo", "archivos": [str(uuid4())]},
    )

    assert calls[0]["capability"] == "fydesign_edit"
    staged = Path(calls[0]["arguments"]["inputImage"])
    assert staged.name.endswith("-producto.png")
    assert staged.is_relative_to(tmp_path / "studio" / str(ctx.tenant_id))
    assert not staged.exists()
    assert calls[0]["arguments"]["prompt"] == "limpia el fondo"
    assert "Studio completó edit" in result.content
    planning_request = ctx.llm.llamadas[0][2]
    assert "fydesign_edit" in planning_request.messages[0].content
    assert "attachment_1" in planning_request.messages[0].content


async def test_project_tools_bridge_router_and_deliver_revision(
    make_ctx, tmp_path: Path
) -> None:
    ctx = _local_ctx(make_ctx, tmp_path)
    calls: list[dict] = []

    class FakeProjectClient:
        def __init__(self, output: Path) -> None:
            self.output = output

        async def execute(self, action, arguments, *, credentials=None):
            self.output.mkdir(parents=True, exist_ok=True)
            preview = self.output / "preview.png"
            preview.write_bytes(b"\x89PNG\r\n\x1a\n")
            calls.append(
                {"action": action, "arguments": arguments, "credentials": credentials}
            )
            return {"ok": True, "files": [str(preview)]}

    async def uploader(_ctx, *, data, filename, mime):
        return uuid4(), filename

    tool = CrearEditarProyectoCreativoTool(
        client_factory=lambda _ctx, output, _state: FakeProjectClient(output),
        credentials_resolver=_credentials,
        uploader=uploader,
    )
    result = await tool.run(
        ctx,
        {"accion": "create", "prompt": "Una landing humana", "projectName": "Demo"},
    )

    assert calls[0]["action"] == "create"
    assert calls[0]["arguments"]["prompt"] == "Una landing humana"
    assert calls[0]["credentials"]["FYDESIGN_LLM_BRIDGE_URL"].startswith(
        "http://127.0.0.1:"
    )
    assert result.data["artifacts"][0]["filename"] == "preview.png"
    assert "revisión nueva" in result.content


async def test_project_private_references_exist_only_during_execution(
    make_ctx, tmp_path: Path
) -> None:
    ctx = _local_ctx(make_ctx, tmp_path)
    calls: list[dict] = []

    class InspectingProjectClient:
        def __init__(self, output: Path) -> None:
            self.output = output

        async def execute(self, action, arguments, *, credentials=None):
            staged = Path(arguments["assetPaths"][0])
            assert staged.read_bytes() == b"image"
            self.output.mkdir(parents=True, exist_ok=True)
            preview = self.output / "preview.png"
            preview.write_bytes(b"\x89PNG\r\n\x1a\n")
            calls.append({"staged": staged, "action": action})
            return {"ok": True, "files": [str(preview)]}

    async def downloader(_session, _settings, _tenant_id, _file_id):
        return Downloaded(filename="referencia.png")

    async def uploader(_ctx, *, data, filename, mime):
        return uuid4(), filename

    tool = CrearEditarProyectoCreativoTool(
        client_factory=lambda _ctx, output, _state: InspectingProjectClient(output),
        credentials_resolver=_credentials,
        uploader=uploader,
        downloader=downloader,
    )
    await tool.run(
        ctx,
        {
            "accion": "create",
            "prompt": "Una app inspirada en esta referencia",
            "archivos": [str(uuid4())],
        },
    )

    assert calls[0]["action"] == "create"
    assert calls[0]["staged"].is_relative_to(
        tmp_path / "studio" / str(ctx.tenant_id) / "project-engine"
    )
    assert not calls[0]["staged"].exists()


def test_project_tool_action_boundaries() -> None:
    assert {"health", "history", "brand-health", "corpus-search"} <= (
        VerProyectosCreativosTool.allowed_actions
    )
    assert {"create", "edit", "template-create", "design-system-generate"} <= (
        CrearEditarProyectoCreativoTool.allowed_actions
    )
    assert AdministrarProyectoCreativoTool.allowed_actions == {
        "tidy",
        "archive",
        "restore",
    }


def test_project_tool_exposes_variants_and_quality_without_an_internal_module_choice() -> None:
    properties = CrearEditarProyectoCreativoTool.input_schema["properties"]
    assert properties["count"]["maximum"] == 4
    assert properties["quality"]["enum"] == ["fast", "balanced", "max"]
    assert properties["archivos"]["maxItems"] == 12
    assert properties["exportFormat"]["enum"] == ["html", "png", "pdf"]
