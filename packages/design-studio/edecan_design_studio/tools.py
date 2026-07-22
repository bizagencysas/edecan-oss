"""Tools conversacionales del Design Studio; el LLM aporta HTML, no este paquete."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from uuid import UUID, uuid4

from edecan_core import Tool, ToolContext, ToolResult
from edecan_creative import subir_archivo

from .models import DesignVersion, RenderBundle
from .render import BrowserFirstRenderer, DesignRenderer, validate_dimensions
from .sanitize import HtmlValidationError, sanitize_html
from .storage import DesignNotFoundError, DesignStore, S3DesignStore

Uploader = Callable[..., Awaitable[tuple[UUID, str]]]
StoreFactory = Callable[[ToolContext], DesignStore]

_MAX_TITLE_CHARS = 200
_MAX_SUMMARY_CHARS = 500
_DEFAULT_WIDTH = 1200
_DEFAULT_HEIGHT = 800
_EXPORT_FORMATS = frozenset({"html", "png", "pdf"})


class _RenderableTool(Protocol):
    _uploader: Uploader
    _renderer: DesignRenderer
    _store_factory: StoreFactory


def _default_store(ctx: ToolContext) -> DesignStore:
    return S3DesignStore(ctx.settings)


def _title(value: Any) -> str:
    return " ".join(str(value or "").split())[:_MAX_TITLE_CHARS]


def _summary(value: Any, fallback: str) -> str:
    return " ".join(str(value or fallback).split())[:_MAX_SUMMARY_CHARS]


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")[:64] or "diseno"


def _uuid(value: Any, label: str) -> UUID:
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"{label} no es un UUID válido.") from exc


def _artifact(filename: str, file_id: UUID, mime: str) -> dict[str, str]:
    return {"file_id": str(file_id), "filename": filename, "mime": mime}


def _preview_block(preview: dict[str, str], title: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "type": "media",
        "media_kind": "image",
        "artifact": preview,
        "alt": f"Vista previa segura de {title}",
        "caption": "Vista previa rasterizada; el HTML descargable no ejecuta scripts ni red.",
        "fallback_text": f"Vista previa de {title}",
    }


async def _upload_version_preview(
    tool: _RenderableTool,
    ctx: ToolContext,
    version: DesignVersion,
) -> tuple[list[dict[str, str]], dict[str, str], str]:
    rendered = await tool._renderer.render(
        version.html,
        width=version.width,
        height=version.height,
        include_png=True,
        include_pdf=False,
    )
    if not rendered.png:
        raise RuntimeError("El renderer no produjo la vista previa PNG.")
    stem = f"{_slug(version.title)}-{str(version.version_id)[:8]}"
    html_name = f"{stem}.html"
    png_name = f"{stem}-preview.png"
    html_id, html_name = await tool._uploader(
        ctx,
        data=version.html.encode("utf-8"),
        filename=html_name,
        mime="text/html; charset=utf-8",
    )
    png_id, png_name = await tool._uploader(
        ctx,
        data=rendered.png,
        filename=png_name,
        mime="image/png",
    )
    preview = _artifact(png_name, png_id, "image/png")
    return (
        [
            _artifact(html_name, html_id, "text/html; charset=utf-8"),
            preview,
        ],
        preview,
        rendered.engine,
    )


class CrearDisenoVisualTool(Tool):
    name = "crear_diseno_visual"
    description = (
        "Crea y versiona un artefacto visual HTML privado. Debes enviar un documento HTML "
        "completo generado para la solicitud; Edecán lo sanea, guarda y muestra como PNG seguro."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "titulo": {"type": "string"},
            "html": {"type": "string", "description": "Documento HTML completo con CSS inline."},
            "ancho": {"type": "integer", "minimum": 240, "maximum": 2400, "default": 1200},
            "alto": {"type": "integer", "minimum": 240, "maximum": 2400, "default": 800},
            "resumen": {"type": "string", "description": "Qué contiene esta primera versión."},
        },
        "required": ["titulo", "html"],
    }

    def __init__(
        self,
        *,
        store_factory: StoreFactory | None = None,
        uploader: Uploader | None = None,
        renderer: DesignRenderer | None = None,
    ) -> None:
        self._store_factory = store_factory or _default_store
        self._uploader = uploader or subir_archivo
        self._renderer = renderer or BrowserFirstRenderer()

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        title = _title(args.get("titulo"))
        if not title:
            return ToolResult(content="Necesito un título para crear el diseño.")
        try:
            width, height = validate_dimensions(
                int(args.get("ancho") or _DEFAULT_WIDTH),
                int(args.get("alto") or _DEFAULT_HEIGHT),
            )
            safe_html = sanitize_html(str(args.get("html") or ""))
        except (ValueError, TypeError, HtmlValidationError) as exc:
            return ToolResult(content=f"No pude crear el diseño: {exc}")

        version = DesignVersion.create(
            artifact_id=uuid4(),
            title=title,
            html=safe_html,
            width=width,
            height=height,
            summary=_summary(args.get("resumen"), "Versión inicial"),
        )
        await self._store_factory(ctx).save(ctx.tenant_id, version)
        artifacts, preview, engine = await _upload_version_preview(self, ctx, version)
        return ToolResult(
            content=(
                f"Creé «{title}» y guardé su primera versión. Te muestro una vista previa segura; "
                "también puedes descargar el HTML privado."
            ),
            data={
                "artifact_id": str(version.artifact_id),
                "version_id": str(version.version_id),
                "renderer": engine,
                "artifacts": artifacts,
            },
            presentation=[_preview_block(preview, title)],
        )


class ObtenerDisenoVisualTool(Tool):
    name = "obtener_diseno_visual"
    description = (
        "Recupera el HTML saneado de la versión actual o de una versión concreta. Úsala antes "
        "de refinar un diseño para editar el documento completo sin perder contenido."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "artifact_id": {"type": "string", "format": "uuid"},
            "version_id": {"type": "string", "format": "uuid"},
        },
        "required": ["artifact_id"],
    }

    def __init__(self, *, store_factory: StoreFactory | None = None) -> None:
        self._store_factory = store_factory or _default_store

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        try:
            artifact_id = _uuid(args.get("artifact_id"), "artifact_id")
            version_id = _uuid(args["version_id"], "version_id") if args.get("version_id") else None
            version = await self._store_factory(ctx).get(ctx.tenant_id, artifact_id, version_id)
        except (ValueError, DesignNotFoundError) as exc:
            return ToolResult(content=str(exc))
        return ToolResult(
            content=(
                f"Versión {version.version_id} de «{version.title}»:\n```html\n{version.html}\n```"
            ),
            data={
                "artifact_id": str(version.artifact_id),
                "version_id": str(version.version_id),
                "sha256": version.sha256,
            },
        )


class RefinarDisenoVisualTool(CrearDisenoVisualTool):
    name = "refinar_diseno_visual"
    description = (
        "Guarda una nueva versión completa de un diseño existente. Primero recupera el HTML con "
        "obtener_diseno_visual, aplica la petición de la persona y envía el HTML completo nuevo."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "artifact_id": {"type": "string", "format": "uuid"},
            "base_version_id": {
                "type": "string",
                "format": "uuid",
                "description": "Versión que se refinó; evita pisar cambios posteriores.",
            },
            "html": {"type": "string"},
            "resumen": {"type": "string"},
            "ancho": {"type": "integer", "minimum": 240, "maximum": 2400},
            "alto": {"type": "integer", "minimum": 240, "maximum": 2400},
        },
        "required": ["artifact_id", "base_version_id", "html", "resumen"],
    }

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        try:
            artifact_id = _uuid(args.get("artifact_id"), "artifact_id")
            base_version_id = _uuid(args.get("base_version_id"), "base_version_id")
            store = self._store_factory(ctx)
            current = await store.get(ctx.tenant_id, artifact_id)
            if current.version_id != base_version_id:
                return ToolResult(
                    content=(
                        "Ese diseño cambió después de la versión que refinaste. "
                        f"Recupera la versión actual ({current.version_id}) y vuelve a aplicar "
                        "el cambio; no sobrescribí nada."
                    )
                )
            width, height = validate_dimensions(
                int(args.get("ancho") or current.width),
                int(args.get("alto") or current.height),
            )
            safe_html = sanitize_html(str(args.get("html") or ""))
        except (ValueError, TypeError, HtmlValidationError, DesignNotFoundError) as exc:
            return ToolResult(content=f"No pude refinar el diseño: {exc}")

        version = DesignVersion.create(
            artifact_id=artifact_id,
            parent_version_id=current.version_id,
            title=current.title,
            html=safe_html,
            width=width,
            height=height,
            summary=_summary(args.get("resumen"), "Refinamiento"),
        )
        await store.save(ctx.tenant_id, version)
        artifacts, preview, engine = await _upload_version_preview(self, ctx, version)
        return ToolResult(
            content=(
                f"Apliqué el cambio a «{version.title}» como una versión nueva y reversible. "
                "La versión anterior sigue intacta."
            ),
            data={
                "artifact_id": str(version.artifact_id),
                "version_id": str(version.version_id),
                "parent_version_id": str(current.version_id),
                "renderer": engine,
                "artifacts": artifacts,
            },
            presentation=[_preview_block(preview, version.title)],
        )


class HistorialDisenoVisualTool(Tool):
    name = "historial_diseno_visual"
    description = "Lista el historial inmutable de versiones de un diseño privado del usuario."
    input_schema = {
        "type": "object",
        "properties": {"artifact_id": {"type": "string", "format": "uuid"}},
        "required": ["artifact_id"],
    }

    def __init__(self, *, store_factory: StoreFactory | None = None) -> None:
        self._store_factory = store_factory or _default_store

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        try:
            artifact_id = _uuid(args.get("artifact_id"), "artifact_id")
            versions = await self._store_factory(ctx).history(ctx.tenant_id, artifact_id)
        except ValueError as exc:
            return ToolResult(content=str(exc))
        if not versions:
            return ToolResult(content="No encontré ese diseño ni versiones para mostrar.")
        lines = [
            f"{index}. {item.created_at:%Y-%m-%d %H:%M UTC} — {item.summary} "
            f"(version_id={item.version_id})"
            for index, item in enumerate(reversed(versions), start=1)
        ]
        return ToolResult(
            content=f"Historial de «{versions[-1].title}»:\n" + "\n".join(lines),
            data={
                "artifact_id": str(artifact_id),
                "versions": [item.public_metadata() for item in versions],
            },
        )


class ExportarDisenoVisualTool(Tool):
    name = "exportar_diseno_visual"
    description = (
        "Exporta una versión privada del Design Studio como HTML seguro, PNG y/o PDF y devuelve "
        "archivos descargables dentro del chat."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "artifact_id": {"type": "string", "format": "uuid"},
            "version_id": {"type": "string", "format": "uuid"},
            "formatos": {
                "type": "array",
                "items": {"type": "string", "enum": ["html", "png", "pdf"]},
                "minItems": 1,
                "maxItems": 3,
                "default": ["html", "png", "pdf"],
            },
        },
        "required": ["artifact_id"],
    }

    def __init__(
        self,
        *,
        store_factory: StoreFactory | None = None,
        uploader: Uploader | None = None,
        renderer: DesignRenderer | None = None,
    ) -> None:
        self._store_factory = store_factory or _default_store
        self._uploader = uploader or subir_archivo
        self._renderer = renderer or BrowserFirstRenderer()

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        try:
            artifact_id = _uuid(args.get("artifact_id"), "artifact_id")
            version_id = _uuid(args["version_id"], "version_id") if args.get("version_id") else None
            version = await self._store_factory(ctx).get(ctx.tenant_id, artifact_id, version_id)
        except (ValueError, DesignNotFoundError) as exc:
            return ToolResult(content=str(exc))

        raw_formats = args.get("formatos") or ["html", "png", "pdf"]
        if not isinstance(raw_formats, list):
            return ToolResult(content="formatos debe ser una lista con html, png y/o pdf.")
        formats = {str(item).lower() for item in raw_formats}
        if not formats or not formats <= _EXPORT_FORMATS:
            return ToolResult(content="Formatos válidos: html, png y pdf.")

        rendered: RenderBundle = await self._renderer.render(
            version.html,
            width=version.width,
            height=version.height,
            include_png="png" in formats,
            include_pdf="pdf" in formats,
        )
        stem = f"{_slug(version.title)}-{str(version.version_id)[:8]}"
        payloads: list[tuple[str, bytes, str]] = []
        if "html" in formats:
            payloads.append(
                (f"{stem}.html", version.html.encode("utf-8"), "text/html; charset=utf-8")
            )
        if "png" in formats and rendered.png:
            payloads.append((f"{stem}.png", rendered.png, "image/png"))
        if "pdf" in formats and rendered.pdf:
            payloads.append((f"{stem}.pdf", rendered.pdf, "application/pdf"))

        artifacts: list[dict[str, str]] = []
        for filename, data, mime in payloads:
            file_id, final_name = await self._uploader(ctx, data=data, filename=filename, mime=mime)
            artifacts.append(_artifact(final_name, file_id, mime))

        preview = next((item for item in artifacts if item["mime"] == "image/png"), None)
        return ToolResult(
            content=(
                f"Exporté «{version.title}» en {', '.join(sorted(formats))}. "
                "Los archivos siguen siendo privados en Edecán."
            ),
            data={
                "artifact_id": str(version.artifact_id),
                "version_id": str(version.version_id),
                "renderer": rendered.engine,
                "artifacts": artifacts,
            },
            presentation=[_preview_block(preview, version.title)] if preview else None,
        )
