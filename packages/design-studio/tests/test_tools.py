from __future__ import annotations

from uuid import uuid4

from edecan_design_studio import get_all_tools
from edecan_design_studio.tools import (
    CrearDisenoVisualTool,
    ExportarDisenoVisualTool,
    HistorialDisenoVisualTool,
    ObtenerDisenoVisualTool,
    RefinarDisenoVisualTool,
)

HTML_V1 = """<!doctype html><html><head><title>Demo</title>
<style>body{margin:0;background:#faf7f0}h1{color:#123}</style></head>
<body><main><h1>Primera versión</h1><p>Contenido.</p></main></body></html>"""

HTML_V2 = HTML_V1.replace("Primera versión", "Segunda versión").replace("#123", "#456")


async def test_full_create_refine_history_export_slice(studio_deps, make_ctx) -> None:
    store, uploader, renderer = studio_deps
    ctx = make_ctx()

    def factory(_ctx):
        return store

    created = await CrearDisenoVisualTool(
        store_factory=factory, uploader=uploader, renderer=renderer
    ).run(ctx, {"titulo": "Landing del taller", "html": HTML_V1})
    assert created.data is not None
    artifact_id = created.data["artifact_id"]
    version_1 = created.data["version_id"]
    assert [item["mime"] for item in created.data["artifacts"]] == [
        "text/html; charset=utf-8",
        "image/png",
    ]
    assert created.presentation and created.presentation[0]["media_kind"] == "image"

    fetched = await ObtenerDisenoVisualTool(store_factory=factory).run(
        ctx, {"artifact_id": artifact_id}
    )
    assert "Primera versión" in fetched.content
    assert "<script" not in fetched.content

    refined = await RefinarDisenoVisualTool(
        store_factory=factory, uploader=uploader, renderer=renderer
    ).run(
        ctx,
        {
            "artifact_id": artifact_id,
            "base_version_id": version_1,
            "html": HTML_V2,
            "resumen": "Título y color actualizados",
        },
    )
    assert refined.data is not None
    version_2 = refined.data["version_id"]
    assert refined.data["parent_version_id"] == version_1

    history = await HistorialDisenoVisualTool(store_factory=factory).run(
        ctx, {"artifact_id": artifact_id}
    )
    assert history.data is not None
    assert [item["version_id"] for item in history.data["versions"]] == [version_1, version_2]

    exported = await ExportarDisenoVisualTool(
        store_factory=factory, uploader=uploader, renderer=renderer
    ).run(ctx, {"artifact_id": artifact_id, "formatos": ["html", "png", "pdf"]})
    assert exported.data is not None
    assert {item["mime"] for item in exported.data["artifacts"]} == {
        "text/html; charset=utf-8",
        "image/png",
        "application/pdf",
    }
    assert any(call["data"].startswith(b"%PDF-") for call in uploader.calls)


async def test_refine_conflict_never_overwrites(studio_deps, make_ctx) -> None:
    store, uploader, renderer = studio_deps
    ctx = make_ctx()

    def factory(_ctx):
        return store

    tool = CrearDisenoVisualTool(store_factory=factory, uploader=uploader, renderer=renderer)
    created = await tool.run(ctx, {"titulo": "Demo", "html": HTML_V1})
    artifact_id = created.data["artifact_id"]

    result = await RefinarDisenoVisualTool(
        store_factory=factory, uploader=uploader, renderer=renderer
    ).run(
        ctx,
        {
            "artifact_id": artifact_id,
            "base_version_id": str(uuid4()),
            "html": HTML_V2,
            "resumen": "Cambio obsoleto",
        },
    )
    assert "no sobrescribí nada" in result.content
    assert len(await store.history(ctx.tenant_id, __import__("uuid").UUID(artifact_id))) == 1


async def test_history_is_tenant_isolated(studio_deps, make_ctx) -> None:
    store, uploader, renderer = studio_deps
    first = make_ctx()
    second = make_ctx()

    def factory(_ctx):
        return store

    created = await CrearDisenoVisualTool(
        store_factory=factory, uploader=uploader, renderer=renderer
    ).run(first, {"titulo": "Privado", "html": HTML_V1})
    result = await HistorialDisenoVisualTool(store_factory=factory).run(
        second, {"artifact_id": created.data["artifact_id"]}
    )
    assert "No encontré" in result.content


def test_entry_point_is_provider_neutral_and_complete() -> None:
    tools = get_all_tools()
    assert [tool.name for tool in tools] == [
        "crear_diseno_visual",
        "obtener_diseno_visual",
        "refinar_diseno_visual",
        "historial_diseno_visual",
        "exportar_diseno_visual",
    ]
    combined = " ".join(
        [tool.name + " " + tool.description + " " + str(tool.input_schema) for tool in tools]
    ).lower()
    assert "api_key" not in combined
    assert "modelo_id" not in combined
