from __future__ import annotations

from uuid import uuid4

from edecan_design_studio import get_all_tools
from edecan_design_studio.models import RenderBundle
from edecan_design_studio.tools import (
    CrearColeccionVisualTool,
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


async def test_preset_humano_define_dimensiones_sin_pedir_pixeles(
    studio_deps, make_ctx
) -> None:
    store, uploader, renderer = studio_deps
    ctx = make_ctx()
    result = await CrearDisenoVisualTool(
        store_factory=lambda _ctx: store,
        uploader=uploader,
        renderer=renderer,
    ).run(
        ctx,
        {"titulo": "Historia", "html": HTML_V1, "formato": "historia_vertical"},
    )

    version = await store.get(ctx.tenant_id, __import__("uuid").UUID(result.data["artifact_id"]))
    assert (version.width, version.height) == (1080, 1920)
    assert result.data["format"] == "historia_vertical"


async def test_coleccion_visual_crea_lienzos_reales_y_reporta_parcial(
    studio_deps, make_ctx
) -> None:
    store, uploader, renderer = studio_deps
    ctx = make_ctx()
    result = await CrearColeccionVisualTool(
        store_factory=lambda _ctx: store,
        uploader=uploader,
        renderer=renderer,
    ).run(
        ctx,
        {
            "titulo": "Carrusel educativo",
            "lienzos": [
                {"titulo": "Portada", "html": HTML_V1, "formato": "post_cuadrado"},
                {"titulo": "Cierre", "html": "no es HTML visual"},
            ],
        },
    )

    assert result.data["status"] == "partial"
    assert len(result.data["designs"]) == 1
    assert len(result.data["artifacts"]) == 2
    assert result.presentation and len(result.presentation) == 1
    assert "1 de 2" in result.content


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


async def test_create_no_persiste_si_no_puede_entregar_preview(studio_deps, make_ctx) -> None:
    store, uploader, _renderer = studio_deps
    ctx = make_ctx()

    class MissingPreviewRenderer:
        async def render(self, html, *, width, height, include_png, include_pdf):
            return RenderBundle(png=None, pdf=None, engine="broken")

    result = await CrearDisenoVisualTool(
        store_factory=lambda _ctx: store,
        uploader=uploader,
        renderer=MissingPreviewRenderer(),
    ).run(ctx, {"titulo": "Demo", "html": HTML_V1})

    assert result.data is None
    assert "No añadí ninguna versión" in result.content
    assert store.versions == {}


async def test_refine_no_persiste_si_falla_la_entrega(studio_deps, make_ctx) -> None:
    store, uploader, renderer = studio_deps
    ctx = make_ctx()

    def factory(_ctx):
        return store

    created = await CrearDisenoVisualTool(
        store_factory=factory, uploader=uploader, renderer=renderer
    ).run(ctx, {"titulo": "Demo", "html": HTML_V1})

    class MissingPreviewRenderer:
        async def render(self, html, *, width, height, include_png, include_pdf):
            return RenderBundle(png=None, pdf=None, engine="broken")

    result = await RefinarDisenoVisualTool(
        store_factory=factory,
        uploader=uploader,
        renderer=MissingPreviewRenderer(),
    ).run(
        ctx,
        {
            "artifact_id": created.data["artifact_id"],
            "base_version_id": created.data["version_id"],
            "html": HTML_V2,
            "resumen": "Cambio que no pudo renderizarse",
        },
    )

    assert result.data is None
    assert "versión actual sigue intacta" in result.content
    artifact_id = __import__("uuid").UUID(created.data["artifact_id"])
    history = await store.history(ctx.tenant_id, artifact_id)
    assert [str(item.version_id) for item in history] == [created.data["version_id"]]


async def test_export_no_afirma_formatos_que_renderer_no_produjo(
    studio_deps, make_ctx
) -> None:
    store, uploader, renderer = studio_deps
    ctx = make_ctx()

    def factory(_ctx):
        return store

    created = await CrearDisenoVisualTool(
        store_factory=factory, uploader=uploader, renderer=renderer
    ).run(ctx, {"titulo": "Demo", "html": HTML_V1})
    uploads_before = len(uploader.calls)

    class MissingPdfRenderer:
        async def render(self, html, *, width, height, include_png, include_pdf):
            return RenderBundle(
                png=b"\x89PNG\r\n\x1a\npreview" if include_png else None,
                pdf=None,
                engine="broken",
            )

    result = await ExportarDisenoVisualTool(
        store_factory=factory,
        uploader=uploader,
        renderer=MissingPdfRenderer(),
    ).run(ctx, {"artifact_id": created.data["artifact_id"], "formatos": ["png", "pdf"]})

    assert result.data is None
    assert "PDF" in result.content
    assert "No guardé una exportación incompleta" in result.content
    assert len(uploader.calls) == uploads_before


def test_entry_point_is_provider_neutral_and_complete() -> None:
    tools = get_all_tools()
    assert [tool.name for tool in tools] == [
        "crear_diseno_visual",
        "crear_coleccion_visual",
        "obtener_diseno_visual",
        "refinar_diseno_visual",
        "historial_diseno_visual",
        "exportar_diseno_visual",
        "ver_estudio_creativo",
        "usar_estudio_creativo",
        "usar_estudio_creativo_premium",
        "ver_proyectos_creativos",
        "crear_editar_proyecto_creativo",
        "administrar_proyecto_creativo",
    ]
    combined = " ".join(
        [tool.name + " " + tool.description + " " + str(tool.input_schema) for tool in tools]
    ).lower()
    assert "api_key" not in combined
    assert "modelo_id" not in combined
