from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from edecan_core import ToolResult
from edecan_toolkit.creator import CrearArtefactosTool


class RecordingUploader:
    def __init__(self, *, fail_filename_suffix: str | None = None) -> None:
        self.uploads: list[dict[str, Any]] = []
        self.fail_filename_suffix = fail_filename_suffix

    async def __call__(self, ctx, *, data: bytes, filename: str, mime: str):
        self.uploads.append({"data": data, "filename": filename, "mime": mime})
        if self.fail_filename_suffix and filename.endswith(self.fail_filename_suffix):
            raise RuntimeError("fallo de upload simulado")
        return uuid.uuid5(uuid.NAMESPACE_URL, filename), filename


def _settings(tmp_path: Path, *, local: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        DATA_DIR=str(tmp_path / "data"),
        CREATOR_WORKSPACE_DIR=None,
        EDECAN_LOCAL_MODE=local,
    )


async def test_crea_los_seis_formatos_reales_manifest_y_evidencia(
    tmp_path: Path, make_ctx
) -> None:
    uploader = RecordingUploader()
    fixed_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    tool = CrearArtefactosTool(
        uploader=uploader,
        id_factory=lambda: fixed_id,
        now=lambda: datetime(2026, 7, 20, 12, 0, tzinfo=UTC),
    )
    ctx = make_ctx(settings=_settings(tmp_path))

    result = await tool.run(
        ctx,
        {
            "solicitud": (
                "Crea un post, Word, PDF, PowerPoint, página web y app para Café Norte."
            ),
            "titulo": "Café Norte",
            "contenido": "Una experiencia cercana.\n\nCafé de origen y atención humana.",
        },
    )

    assert result.data is not None
    assert result.data["status"] == "completed"
    assert result.data["creation_id"] == str(fixed_id)
    artifacts = result.data["artifacts"]
    assert [item["kind"] for item in artifacts] == [
        "post",
        "docx",
        "pdf",
        "pptx",
        "website",
        "app",
    ]
    assert all(item["status"] == "created" for item in artifacts)
    assert all(item["file_id"] and item["filename"] for item in artifacts)
    assert all(len(item["sha256"]) == 64 for item in artifacts)
    assert all(item["size_bytes"] > 0 for item in artifacts)
    assert len(uploader.uploads) == 7  # seis entregables + manifest
    assert "No publiqué ni desplegué nada" in result.content
    assert "Contenido del post" in result.content
    assert result.data["post_text"].startswith("Una experiencia cercana")

    workspace = Path(result.data["workspace_path"])
    manifest = json.loads((workspace / "manifest.json").read_text())
    assert manifest["status"] == "completed"
    assert manifest["content_source"] == "provided"
    assert len(manifest["artifacts"]) == 6
    uploaded_by_name = {item["filename"]: item["data"] for item in uploader.uploads}
    for item in artifacts:
        assert hashlib.sha256(uploaded_by_name[item["filename"]]).hexdigest() == item["sha256"]
    assert result.data["manifest"]["file_id"]

    by_kind = {item["kind"]: item for item in artifacts}
    assert (workspace / by_kind["post"]["relative_path"]).read_text().startswith("# Café Norte")
    assert (workspace / by_kind["pdf"]["relative_path"]).read_bytes().startswith(b"%PDF-")
    with zipfile.ZipFile(workspace / by_kind["docx"]["relative_path"]) as archive:
        assert "word/document.xml" in archive.namelist()
    with zipfile.ZipFile(workspace / by_kind["pptx"]["relative_path"]) as archive:
        assert "ppt/presentation.xml" in archive.namelist()
    with zipfile.ZipFile(workspace / by_kind["website"]["relative_path"]) as archive:
        assert {"index.html", "styles.css", "edecan-project.json"} <= set(
            archive.namelist()
        )
    with zipfile.ZipFile(workspace / by_kind["app"]["relative_path"]) as archive:
        assert {"server.mjs", "package.json", "test/server.test.mjs"} <= set(
            archive.namelist()
        )


async def test_solicitud_compuesta_con_fallo_parcial_no_afirma_el_formato_fallido(
    tmp_path: Path, make_ctx
) -> None:
    uploader = RecordingUploader(fail_filename_suffix=".pptx")
    tool = CrearArtefactosTool(uploader=uploader)
    ctx = make_ctx(settings=_settings(tmp_path))

    result = await tool.run(
        ctx,
        {
            "solicitud": "Crea un PDF y una presentación",
            "titulo": "Plan",
            "contenido": "Primero validar.\nLuego lanzar.",
        },
    )

    assert result.data is not None
    assert result.data["status"] == "partial"
    by_kind = {item["kind"]: item for item in result.data["artifacts"]}
    assert by_kind["pdf"]["status"] == "created"
    assert by_kind["pptx"]["status"] == "failed"
    assert by_kind["pptx"]["file_id"] is None
    assert "Fallaron" in result.content


async def test_formato_desconocido_no_crea_workspace_ni_sube_archivos(
    tmp_path: Path, make_ctx
) -> None:
    uploader = RecordingUploader()
    tool = CrearArtefactosTool(uploader=uploader)
    ctx = make_ctx(settings=_settings(tmp_path))

    result = await tool.run(
        ctx, {"solicitud": "Crea algo", "formatos": ["holograma-cuántico"]}
    )

    assert result.data is None
    assert "Formato no soportado" in result.content
    assert uploader.uploads == []
    assert not (tmp_path / "data" / "creator").exists()


async def test_fallo_del_llm_genera_fallback_honesto_y_artefacto_real(
    tmp_path: Path, make_ctx
) -> None:
    class BrokenContentTool:
        async def run(self, ctx, args) -> ToolResult:
            raise RuntimeError("LLM no disponible")

    uploader = RecordingUploader()
    tool = CrearArtefactosTool(uploader=uploader, content_tool=BrokenContentTool())
    ctx = make_ctx(settings=_settings(tmp_path))

    result = await tool.run(ctx, {"solicitud": "Crea un post sobre privacidad"})

    assert result.data is not None
    assert result.data["status"] == "completed"
    assert result.data["file_id"] == result.data["artifacts"][0]["file_id"]
    assert result.data["filename"] == result.data["artifacts"][0]["filename"]
    workspace = Path(result.data["workspace_path"])
    manifest = json.loads((workspace / "manifest.json").read_text())
    assert manifest["content_source"] == "deterministic_fallback"
    post = next(item for item in result.data["artifacts"] if item["kind"] == "post")
    assert "Solicitud original" in (workspace / post["relative_path"]).read_text()


async def test_workspace_no_se_expone_en_hosted(tmp_path: Path, make_ctx) -> None:
    tool = CrearArtefactosTool(uploader=RecordingUploader())
    ctx = make_ctx(settings=_settings(tmp_path, local=False))
    result = await tool.run(
        ctx,
        {"solicitud": "Crea una página web", "contenido": "Contenido real."},
    )
    assert result.data is not None
    assert result.data["workspace_path"] is None


async def test_pagina_escapa_html_del_usuario_y_sus_rutas_son_seguras(
    tmp_path: Path, make_ctx
) -> None:
    tool = CrearArtefactosTool(uploader=RecordingUploader())
    ctx = make_ctx(settings=_settings(tmp_path))
    result = await tool.run(
        ctx,
        {
            "solicitud": "Crea una página web",
            "titulo": "<script>alert(1)</script>",
            "contenido": "<img src=x onerror=alert(1)>",
        },
    )
    website = next(item for item in result.data["artifacts"] if item["kind"] == "website")
    archive_path = Path(result.data["workspace_path"]) / website["relative_path"]
    with zipfile.ZipFile(archive_path) as archive:
        index = archive.read("index.html").decode()
        assert "<script>alert(1)</script>" not in index
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in index
        assert "&lt;img src=x onerror=alert(1)&gt;" in index
        assert all(
            not name.startswith("/") and ".." not in Path(name).parts
            for name in archive.namelist()
        )


@pytest.mark.skipif(shutil.which("node") is None, reason="Node no está instalado")
async def test_scaffold_de_app_incluye_y_pasa_su_test_real(tmp_path: Path, make_ctx) -> None:
    uploader = RecordingUploader()
    tool = CrearArtefactosTool(uploader=uploader)
    ctx = make_ctx(settings=_settings(tmp_path))
    result = await tool.run(
        ctx,
        {
            "solicitud": "Crea una app completa",
            "titulo": "Operador Local",
            "contenido": "Panel simple y verificable.",
        },
    )
    app = next(item for item in result.data["artifacts"] if item["kind"] == "app")
    extract_dir = tmp_path / "extracted-app"
    with zipfile.ZipFile(Path(result.data["workspace_path"]) / app["relative_path"]) as archive:
        archive.extractall(extract_dir)

    completed = subprocess.run(
        [shutil.which("node") or "node", "--test"],
        cwd=extract_dir,
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_creator_es_privado_y_no_dangerous() -> None:
    tool = CrearArtefactosTool(uploader=RecordingUploader())
    assert tool.dangerous is False
    assert tool.requires_flags == frozenset()
    assert "No publica" in tool.description
