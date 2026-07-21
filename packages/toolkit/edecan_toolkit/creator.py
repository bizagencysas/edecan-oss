"""Creación universal de artefactos privados desde una sola solicitud.

El planner es determinista; la redacción base puede usar el LLM ya conectado.
Cada formato se materializa como bytes reales, se valida, se guarda en un
workspace aislado y se sube como archivo privado. Publicar o desplegar queda en
tools peligrosas separadas y nunca ocurre desde este módulo.
"""

from __future__ import annotations

import hashlib
import html
import io
import json
import os
import re
import unicodedata
import uuid
import zipfile
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from edecan_core import Tool, ToolContext, ToolResult, plan_creation, redact
from edecan_creative import (
    CrearDocumentoTool,
    CrearPdfTool,
    CrearPresentacionTool,
    subir_archivo,
)
from edecan_schemas import ArtifactEvidence, ArtifactKind, CreationManifest, CreationPlan

from .contenido import GenerarContenidoTool

Uploader = Callable[..., Awaitable[tuple[uuid.UUID, str]]]

_MAX_REQUEST_CHARS = 20_000
_MAX_CONTENT_CHARS = 40_000
_MIME_BY_KIND: dict[ArtifactKind, str] = {
    "post": "text/markdown; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "website": "application/zip",
    "app": "application/zip",
}
_EXTENSION_BY_KIND: dict[ArtifactKind, str] = {
    "post": ".md",
    "docx": ".docx",
    "pdf": ".pdf",
    "pptx": ".pptx",
    "website": "-website.zip",
    "app": "-app.zip",
}
_ZIP_TIMESTAMP = (2024, 1, 1, 0, 0, 0)


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    return slug[:64] or "creacion-edecan"


def _creator_root(settings: Any) -> Path:
    configured = str(getattr(settings, "CREATOR_WORKSPACE_DIR", "") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    data_dir = Path(str(getattr(settings, "DATA_DIR", "~/.edecan/data"))).expanduser()
    return (data_dir / "creator").resolve()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError(f"Ruta inválida dentro del proyecto: {name!r}")
            info = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, files[name])
    return buffer.getvalue()


def _write_project_tree(root: Path, files: dict[str, bytes]) -> None:
    for relative, data in files.items():
        destination = (root / relative).resolve()
        if not destination.is_relative_to(root.resolve()):
            raise ValueError(f"Ruta de proyecto fuera del sandbox: {relative!r}")
        _atomic_write(destination, data)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_blob(kind: ArtifactKind, data: bytes) -> str:
    if not data:
        raise ValueError("El renderizador produjo un archivo vacío.")
    if kind == "post":
        text = data.decode("utf-8")
        if not text.strip():
            raise ValueError("El post no contiene texto.")
        return "markdown_utf8_nonempty"
    if kind == "pdf":
        if not data.startswith(b"%PDF-") or b"%%EOF" not in data[-1024:]:
            raise ValueError("El archivo generado no tiene una estructura PDF válida.")
        return "pdf_header_and_eof_verified"

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = set(archive.namelist())
        bad_paths = [name for name in names if name.startswith("/") or ".." in Path(name).parts]
        if bad_paths:
            raise ValueError("El ZIP generado contiene rutas inseguras.")
        if kind == "docx":
            required = {"[Content_Types].xml", "word/document.xml"}
        elif kind == "pptx":
            required = {"[Content_Types].xml", "ppt/presentation.xml"}
        elif kind == "website":
            required = {"index.html", "styles.css", "README.md", "edecan-project.json"}
        else:
            required = {
                "package.json",
                "server.mjs",
                "public/index.html",
                "public/app.js",
                "public/styles.css",
                "test/server.test.mjs",
                "README.md",
                "edecan-project.json",
            }
        missing = sorted(required - names)
        if missing:
            raise ValueError(f"El ZIP generado está incompleto; faltan: {', '.join(missing)}")
        for name in required:
            if not archive.read(name):
                raise ValueError(f"El archivo interno {name!r} está vacío.")
    return f"{kind}_zip_structure_verified"


def _paragraphs(content: str) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", content) if part.strip()]
    return paragraphs[:100] or [content.strip()]


def _bullets(content: str) -> list[str]:
    lines = [
        re.sub(r"^(?:[-*•]|\d+[.)])\s*", "", line).strip()
        for line in content.splitlines()
        if line.strip()
    ]
    if len(lines) < 2:
        lines = [part.strip() for part in re.split(r"(?<=[.!?])\s+", content) if part.strip()]
    return lines[:20] or [content.strip()]


def _website_files(plan: CreationPlan, content: str) -> dict[str, bytes]:
    title = html.escape(plan.title)
    body = "\n".join(f"<p>{html.escape(paragraph)}</p>" for paragraph in _paragraphs(content))
    index = f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{title}">
  <title>{title}</title>
  <link rel="stylesheet" href="styles.css">
</head>
<body>
  <main>
    <p class="eyebrow">Creado con Edecán</p>
    <h1>{title}</h1>
    <section>{body}</section>
  </main>
</body>
</html>
"""
    styles = """*{box-sizing:border-box}body{margin:0;background:#f4f1ea;color:#18201d;
font-family:Inter,ui-sans-serif,system-ui,sans-serif}main{width:min(760px,calc(100% - 2rem));
margin:12vh auto;padding:clamp(2rem,6vw,5rem);background:#fff;border:1px solid #d9d4c8;
border-radius:24px;box-shadow:0 24px 80px #1d2a2418}h1{
font-size:clamp(2.4rem,7vw,5rem);line-height:.95;letter-spacing:-.055em;margin:.35em 0}
.eyebrow{color:#507264;text-transform:uppercase;
letter-spacing:.16em;font-size:.75rem;font-weight:700}section{font-size:1.08rem;line-height:1.75}
"""
    project = {
        "schema": "edecan.project/v1",
        "kind": "static-website",
        "title": plan.title,
        "entrypoint": "index.html",
        "external_effects": False,
    }
    readme = f"# {plan.title}\n\nPágina web estática. Abre `index.html` en un navegador.\n"
    return {
        "README.md": readme.encode(),
        "edecan-project.json": json.dumps(project, ensure_ascii=False, indent=2).encode(),
        "index.html": index.encode(),
        "styles.css": styles.encode(),
    }


def _app_files(plan: CreationPlan, content: str) -> dict[str, bytes]:
    title_json = json.dumps(plan.title, ensure_ascii=False)
    content_json = json.dumps(content, ensure_ascii=False)
    title_html = html.escape(plan.title)
    package = {
        "name": _slug(plan.title),
        "private": True,
        "version": "0.1.0",
        "type": "module",
        "scripts": {"start": "node server.mjs", "test": "node --test"},
        "engines": {"node": ">=20"},
    }
    server = """import {createServer as createHttpServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {extname, join} from 'node:path';
import {fileURLToPath} from 'node:url';

const root = fileURLToPath(new URL('./public/', import.meta.url));
const mime = {'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8',
  '.css':'text/css; charset=utf-8','.json':'application/json; charset=utf-8'};

export function createServer() {
  return createHttpServer(async (request, response) => {
    if (request.url === '/api/health') {
      response.writeHead(200, {'content-type':'application/json; charset=utf-8'});
      response.end(JSON.stringify({ok:true})); return;
    }
    const requested = request.url === '/' ? 'index.html' : String(request.url).slice(1);
    if (!/^[a-zA-Z0-9._/-]+$/.test(requested) || requested.includes('..')) {
      response.writeHead(400); response.end('Bad request'); return;
    }
    try {
      const data = await readFile(join(root, requested));
      const type = mime[extname(requested)] ?? 'application/octet-stream';
      response.writeHead(200, {'content-type': type});
      response.end(data);
    } catch {
      response.writeHead(404); response.end('Not found');
    }
  });
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  createServer().listen(Number(process.env.PORT || 3000), '127.0.0.1', () => {
    console.log(`App lista en http://127.0.0.1:${process.env.PORT || 3000}`);
  });
}
"""
    index = f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title_html}</title>
<link rel="stylesheet" href="/styles.css"></head><body><main><span>Scaffold full-stack</span>
<h1>{title_html}</h1><div id="content"></div><button id="health">Verificar API</button>
<output id="status" aria-live="polite"></output></main><script type="module" src="/app.js"></script>
</body></html>"""
    app_js = f"""const title = {title_json};
const content = {content_json};
document.querySelector('#content').textContent = content;
document.querySelector('#health').addEventListener('click', async () => {{
  const response = await fetch('/api/health');
  const data = await response.json();
  document.querySelector('#status').textContent = data.ok ? `${{title}}: API operativa` : 'Error';
}});
"""
    styles = """*{box-sizing:border-box}body{margin:0;min-height:100vh;
display:grid;place-items:center;
background:#101915;color:#edf5ef;font-family:ui-sans-serif,system-ui}main{width:min(760px,92vw);
padding:clamp(2rem,6vw,5rem);border:1px solid #486154;border-radius:28px;background:#17241e}
h1{font-size:clamp(2.4rem,7vw,5rem);letter-spacing:-.055em;margin:.3em 0}
#content{white-space:pre-wrap;line-height:1.7;color:#bed0c4}
button{margin-top:2rem;border:0;border-radius:999px;padding:.9rem 1.2rem;
background:#b8f2ce;color:#102018;font-weight:800}output{display:block;margin-top:1rem}
"""
    test = """import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from '../server.mjs';

test('health endpoint responds with real JSON', async () => {
  const server = createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    const {port} = server.address();
    const response = await fetch(`http://127.0.0.1:${port}/api/health`);
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), {ok:true});
  } finally { await new Promise(resolve => server.close(resolve)); }
});
"""
    project = {
        "schema": "edecan.project/v1",
        "kind": "full-stack-node-scaffold",
        "title": plan.title,
        "entrypoint": "server.mjs",
        "test_command": "npm test",
        "external_effects": False,
    }
    readme = f"""# {plan.title}

Scaffold full-stack ejecutable, sin dependencias de terceros.

```bash
npm test
npm start
```

Abre http://127.0.0.1:3000. Incluye frontend, servidor HTTP, `/api/health` y prueba real.
No es un despliegue ni promete lógica de negocio que no esté en estos archivos.
"""
    return {
        "README.md": readme.encode(),
        "edecan-project.json": json.dumps(project, ensure_ascii=False, indent=2).encode(),
        "package.json": json.dumps(package, ensure_ascii=False, indent=2).encode(),
        "public/app.js": app_js.encode(),
        "public/index.html": index.encode(),
        "public/styles.css": styles.encode(),
        "server.mjs": server.encode(),
        "test/server.test.mjs": test.encode(),
    }


class CrearArtefactosTool(Tool):
    """Planea y materializa uno o varios entregables privados."""

    name = "crear_artefactos"
    description = (
        "Crea entregables reales y privados desde una sola solicitud: posts Markdown, "
        "Word, PDF, PowerPoint, páginas web estáticas y scaffolds full-stack de apps. "
        "Acepta solicitudes compuestas, devuelve file_id por artefacto, SHA-256 y un "
        "manifest verificable. No publica, despliega ni actúa sobre cuentas externas."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "solicitud": {
                "type": "string",
                "description": "Qué crear, incluyendo objetivo, audiencia y requisitos.",
            },
            "formatos": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["post", "docx", "pdf", "pptx", "website", "app"],
                },
                "description": (
                    "Formatos explícitos. Si se omite, el planner detecta todos los "
                    "mencionados en la solicitud."
                ),
                "maxItems": 6,
            },
            "titulo": {"type": "string", "description": "Título común opcional."},
            "contenido": {
                "type": "string",
                "description": (
                    "Contenido base opcional. Si se omite, Edecán lo redacta con el LLM "
                    "conectado y usa un fallback explícito si no está disponible."
                ),
            },
            "tono": {"type": "string"},
            "longitud": {"type": "string"},
        },
        "required": ["solicitud"],
    }

    def __init__(
        self,
        *,
        uploader: Uploader | None = None,
        content_tool: GenerarContenidoTool | None = None,
        id_factory: Callable[[], uuid.UUID] = uuid.uuid4,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._uploader = uploader or subir_archivo
        self._content_tool = content_tool or GenerarContenidoTool()
        self._id_factory = id_factory
        self._now = now or (lambda: datetime.now(UTC))

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        request = str(args.get("solicitud") or "").strip()[:_MAX_REQUEST_CHARS]
        requested_formats = args.get("formatos")
        if requested_formats is not None and not isinstance(requested_formats, list):
            return ToolResult(content="'formatos' debe ser una lista de formatos soportados.")
        try:
            plan = plan_creation(
                request,
                requested_formats=requested_formats,
                title=str(args.get("titulo") or "").strip() or None,
            )
        except ValueError as exc:
            return ToolResult(content=str(exc))

        creation_id = str(self._id_factory())
        root = _creator_root(ctx.settings)
        creation_dir = (root / str(ctx.tenant_id) / creation_id).resolve()
        if not creation_dir.is_relative_to(root):
            return ToolResult(content="No pude crear un workspace seguro para los artefactos.")
        creation_dir.mkdir(parents=True, exist_ok=False)

        content, content_source = await self._content(ctx, args, plan)
        evidence: list[ArtifactEvidence] = []
        for deliverable in plan.deliverables:
            artifact = await self._create_one(
                ctx,
                creation_dir=creation_dir,
                plan=plan,
                kind=deliverable.kind,
                content=content,
            )
            evidence.append(artifact)

        created_count = sum(item.status == "created" for item in evidence)
        if created_count == len(evidence):
            status = "completed"
        elif created_count:
            status = "partial"
        else:
            status = "failed"
        manifest = CreationManifest(
            creation_id=creation_id,
            tenant_id=str(ctx.tenant_id),
            user_id=str(ctx.user_id),
            created_at=self._now().isoformat(),
            status=status,
            request=plan.request,
            title=plan.title,
            content_source=content_source,
            plan=plan,
            artifacts=evidence,
        )
        manifest_bytes = manifest.model_dump_json(indent=2).encode()
        manifest_path = creation_dir / "manifest.json"
        _atomic_write(manifest_path, manifest_bytes)
        manifest_evidence = await self._upload_manifest(ctx, creation_dir, manifest_bytes)

        failed = [item.filename for item in evidence if item.status == "failed"]
        created = [item.filename for item in evidence if item.status == "created"]
        summary = (
            f"Creé {len(created)} de {len(evidence)} artefacto(s) real(es): "
            f"{', '.join(f'«{name}»' for name in created) or 'ninguno'}."
        )
        if manifest_evidence.file_id:
            summary += f" Manifest verificable: «{manifest_evidence.filename}»."
        else:
            summary += " El manifest quedó únicamente en el workspace local."
        if failed:
            summary += f" Fallaron: {', '.join(failed)}; no afirmo que estén creados."
        summary += " No publiqué ni desplegué nada."
        post_created = any(
            item.kind == "post" and item.status == "created" for item in evidence
        )
        if post_created:
            summary += f"\n\nContenido del post:\n{content[:4000]}"

        local_mode = bool(getattr(ctx.settings, "EDECAN_LOCAL_MODE", False))
        primary = next((item for item in evidence if item.status == "created"), None)
        return ToolResult(
            content=summary,
            data={
                "creation_id": creation_id,
                "status": status,
                "plan": plan.model_dump(),
                "artifacts": [item.model_dump() for item in evidence],
                "manifest": manifest_evidence.model_dump(),
                "file_id": primary.file_id if len(created) == 1 and primary else None,
                "filename": primary.filename if len(created) == 1 and primary else None,
                "manifest_file_id": manifest_evidence.file_id,
                "post_text": content if post_created else None,
                "workspace_path": str(creation_dir) if local_mode else None,
            },
        )

    async def _content(
        self, ctx: ToolContext, args: dict[str, Any], plan: CreationPlan
    ) -> tuple[str, str]:
        provided = str(args.get("contenido") or "").strip()[:_MAX_CONTENT_CHARS]
        if provided:
            return provided, "provided"
        brief = (
            f"{plan.request}\n\nRedacta contenido base consistente para estos formatos: "
            + ", ".join(item.kind for item in plan.deliverables)
            + "."
        )
        try:
            result = await self._content_tool.run(
                ctx,
                {
                    "brief": brief,
                    "tipo": "post",
                    "tono": args.get("tono"),
                    "longitud": args.get("longitud") or "medio",
                },
            )
            if result.data and result.content.strip():
                return result.content.strip()[:_MAX_CONTENT_CHARS], "llm"
        except Exception:  # noqa: BLE001 - creación sigue con fallback honesto
            pass
        fallback = (
            f"Contenido base generado de forma determinista para «{plan.title}».\n\n"
            f"Solicitud original: {plan.request}\n\n"
            "Este borrador conserva la intención original y debe revisarse antes de publicarse."
        )
        return fallback, "deterministic_fallback"

    async def _create_one(
        self,
        ctx: ToolContext,
        *,
        creation_dir: Path,
        plan: CreationPlan,
        kind: ArtifactKind,
        content: str,
    ) -> ArtifactEvidence:
        base = _slug(plan.title)
        expected_filename = f"{base}{_EXTENSION_BY_KIND[kind]}"
        try:
            if kind in {"docx", "pdf", "pptx"}:
                return await self._create_office(
                    ctx,
                    creation_dir=creation_dir,
                    plan=plan,
                    kind=kind,
                    content=content,
                )
            if kind == "post":
                data = f"# {plan.title}\n\n{content.strip()}\n".encode()
                return await self._persist_blob(
                    ctx,
                    creation_dir=creation_dir,
                    kind=kind,
                    filename=expected_filename,
                    data=data,
                )
            files = (
                _website_files(plan, content) if kind == "website" else _app_files(plan, content)
            )
            project_dir = creation_dir / ("website" if kind == "website" else "app")
            _write_project_tree(project_dir, files)
            return await self._persist_blob(
                ctx,
                creation_dir=creation_dir,
                kind=kind,
                filename=expected_filename,
                data=_zip_bytes(files),
                metadata={"project_files": sorted(files)},
            )
        except Exception as exc:  # noqa: BLE001 - un formato no cancela los demás
            return ArtifactEvidence(
                kind=kind,
                status="failed",
                filename=expected_filename,
                mime=_MIME_BY_KIND[kind],
                relative_path=expected_filename,
                size_bytes=0,
                validation="failed",
                error=redact(str(exc))[:500],
            )

    async def _create_office(
        self,
        ctx: ToolContext,
        *,
        creation_dir: Path,
        plan: CreationPlan,
        kind: ArtifactKind,
        content: str,
    ) -> ArtifactEvidence:
        captured: dict[str, Any] = {}

        async def capture_uploader(
            tool_ctx: Any, *, data: bytes, filename: str, mime: str
        ) -> tuple[uuid.UUID, str]:
            captured.update(data=data, filename=filename, mime=mime)
            _validate_blob(kind, data)
            _atomic_write(creation_dir / filename, data)
            return await self._uploader(tool_ctx, data=data, filename=filename, mime=mime)

        if kind == "docx":
            tool = CrearDocumentoTool(uploader=capture_uploader)
            result = await tool.run(
                ctx,
                {
                    "titulo": plan.title,
                    "secciones": [{"encabezado": "Contenido", "parrafos": _paragraphs(content)}],
                },
            )
        elif kind == "pdf":
            tool = CrearPdfTool(uploader=capture_uploader)
            result = await tool.run(ctx, {"titulo": plan.title, "parrafos": _paragraphs(content)})
        else:
            bullets = _bullets(content)
            slides = [
                {"titulo": f"Parte {index + 1}", "bullets": bullets[index : index + 5]}
                for index in range(0, len(bullets), 5)
            ]
            tool = CrearPresentacionTool(uploader=capture_uploader)
            result = await tool.run(ctx, {"titulo": plan.title, "diapositivas": slides})

        data = captured.get("data")
        if not isinstance(data, bytes) or not result.data:
            raise ValueError(f"El renderizador {kind} no produjo un artefacto verificable.")
        filename = str(captured["filename"])
        return ArtifactEvidence(
            kind=kind,
            status="created",
            filename=filename,
            mime=str(captured["mime"]),
            relative_path=filename,
            size_bytes=len(data),
            sha256=_sha256(data),
            validation=_validate_blob(kind, data),
            file_id=str(result.data["file_id"]),
        )

    async def _persist_blob(
        self,
        ctx: ToolContext,
        *,
        creation_dir: Path,
        kind: ArtifactKind,
        filename: str,
        data: bytes,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactEvidence:
        validation = _validate_blob(kind, data)
        _atomic_write(creation_dir / filename, data)
        file_id, stored_filename = await self._uploader(
            ctx, data=data, filename=filename, mime=_MIME_BY_KIND[kind]
        )
        return ArtifactEvidence(
            kind=kind,
            status="created",
            filename=stored_filename,
            mime=_MIME_BY_KIND[kind],
            relative_path=filename,
            size_bytes=len(data),
            sha256=_sha256(data),
            validation=validation,
            file_id=str(file_id),
            metadata=metadata or {},
        )

    async def _upload_manifest(
        self, ctx: ToolContext, creation_dir: Path, data: bytes
    ) -> ArtifactEvidence:
        filename = "manifest.json"
        try:
            parsed = json.loads(data)
            if parsed.get("version") != 1 or not parsed.get("creation_id"):
                raise ValueError("Manifest inválido.")
            file_id, stored_filename = await self._uploader(
                ctx, data=data, filename=filename, mime="application/json"
            )
            return ArtifactEvidence(
                kind="manifest",
                status="created",
                filename=stored_filename,
                mime="application/json",
                relative_path=str((creation_dir / filename).relative_to(creation_dir)),
                size_bytes=len(data),
                sha256=_sha256(data),
                validation="json_schema_v1_verified",
                file_id=str(file_id),
            )
        except Exception as exc:  # noqa: BLE001 - artefactos siguen siendo válidos
            return ArtifactEvidence(
                kind="manifest",
                status="failed",
                filename=filename,
                mime="application/json",
                relative_path=filename,
                size_bytes=len(data),
                sha256=_sha256(data),
                validation="local_json_only",
                error=redact(str(exc))[:500],
            )
