from __future__ import annotations

import io
from uuid import uuid4

from edecan_docanalysis.archivos import EditarPdfTool, LeerArchivoTool, _render_text_pdf
from pypdf import PdfReader


async def test_leer_archivo_texto_entrega_contenido(make_ctx, fake_s3, make_archivo):
    fake_s3.archivo = make_archivo(
        contenido=b"Hola, Venezuela", filename="nota.md", mime="text/markdown"
    )

    result = await LeerArchivoTool().run(make_ctx(), {"file_id": str(uuid4())})

    assert result.content == "Hola, Venezuela"
    assert result.data["filename"] == "nota.md"
    assert result.data["truncated"] is False


async def test_leer_archivo_pdf_extrae_paginas(make_ctx, fake_s3, make_archivo):
    data = _render_text_pdf("Informe", ["Contenido importante"])
    fake_s3.archivo = make_archivo(
        contenido=data, filename="informe.pdf", mime="application/pdf"
    )

    result = await LeerArchivoTool().run(make_ctx(), {"file_id": str(uuid4())})

    assert "[Página 1]" in result.content
    assert "Contenido importante" in result.content


async def test_editar_pdf_reconstruye_sin_sobrescribir_original(
    make_ctx, fake_s3, make_archivo
):
    original = _render_text_pdf("Original", ["Texto anterior"])
    fake_s3.archivo = make_archivo(
        contenido=original, filename="original.pdf", mime="application/pdf"
    )
    output_id = uuid4()
    fake_s3.siguiente_file_id = output_id

    result = await EditarPdfTool().run(
        make_ctx(),
        {
            "file_id": str(uuid4()),
            "modo": "reconstruir",
            "titulo": "Corregido",
            "parrafos": ["Texto nuevo y verificado."],
            "nombre_salida": "corregido.pdf",
        },
    )

    assert result.data["file_id"] == str(output_id)
    assert result.data["filename"] == "corregido.pdf"
    assert fake_s3.archivo.contenido == original
    generated = fake_s3.subidas[0]["contenido"]
    assert generated.startswith(b"%PDF-")
    extracted = "".join(
        page.extract_text() or "" for page in PdfReader(io.BytesIO(generated)).pages
    )
    assert "Texto nuevo y verificado" in extracted


async def test_editar_pdf_anexa_paginas(make_ctx, fake_s3, make_archivo):
    original = _render_text_pdf("Original", ["Primera parte"])
    fake_s3.archivo = make_archivo(
        contenido=original, filename="original.pdf", mime="application/pdf"
    )

    result = await EditarPdfTool().run(
        make_ctx(),
        {
            "file_id": str(uuid4()),
            "modo": "anexar",
            "titulo": "Anexo",
            "parrafos": ["Segunda parte"],
        },
    )

    assert result.data["mime"] == "application/pdf"
    assert len(PdfReader(io.BytesIO(fake_s3.subidas[0]["contenido"])).pages) == 2
