"""Tests de `edecan_docanalysis.pdf` (`extraer_tablas_pdf`).

Genera PDFs mínimos con `pypdf.PdfWriter` a mano (un content stream con
operadores `BT/Tj/ET` y una fuente base14 `/Helvetica`, sin depender de
ningún archivo externo) — verificado en desarrollo que `pypdf` recupera el
texto con las mismas separaciones de espacios con las que se escribió.
"""

from __future__ import annotations

import csv
import io
from uuid import uuid4

from edecan_docanalysis.pdf import ExtraerTablasPdfTool


def _construir_pdf(paginas: list[list[str]]) -> bytes:
    """Un PDF con una página por elemento de `paginas`, cada línea de texto
    dibujada con `Td` relativos de -14pt entre líneas."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    for lineas in paginas:
        page = writer.add_blank_page(width=612, height=792)
        cuerpo = ["BT", "/F1 10 Tf", "72 750 Td"]
        for i, linea in enumerate(lineas):
            escapado = linea.replace("\\", "\\\\").replace("(", r"\(").replace(")", r"\)")
            if i > 0:
                cuerpo.append("0 -14 Td")
            cuerpo.append(f"({escapado}) Tj")
        cuerpo.append("ET")
        contenido = "\n".join(cuerpo).encode("latin-1")

        stream_obj = DecodedStreamObject()
        stream_obj.set_data(contenido)
        content_ref = writer._add_object(stream_obj)

        font_dict = DictionaryObject()
        font_dict[NameObject("/Type")] = NameObject("/Font")
        font_dict[NameObject("/Subtype")] = NameObject("/Type1")
        font_dict[NameObject("/BaseFont")] = NameObject("/Helvetica")
        font_ref = writer._add_object(font_dict)

        resources = DictionaryObject()
        fonts = DictionaryObject()
        fonts[NameObject("/F1")] = font_ref
        resources[NameObject("/Font")] = fonts

        page[NameObject("/Contents")] = content_ref
        page[NameObject("/Resources")] = resources

    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


_PAGINA_CON_TABLA = [
    "Nombre    Edad    Ciudad",
    "Ana    30    Bogota",
    "Luis    25    Medellin",
    "Carlos    40    Cali",
]
_PAGINA_SIN_TABLA = ["Solo texto normal en esta pagina", "sin ninguna tabla alineada aqui"]


async def test_file_id_invalido(make_ctx, fake_s3):
    resultado = await ExtraerTablasPdfTool().run(make_ctx(), {"file_id": "no-es-uuid"})
    assert "identificador válido" in resultado.content


async def test_archivo_no_encontrado(make_ctx, fake_s3):
    fake_s3.archivo = None
    resultado = await ExtraerTablasPdfTool().run(make_ctx(), {"file_id": str(uuid4())})
    assert "No encontré ese archivo" in resultado.content


async def test_no_es_pdf(make_ctx, fake_s3, make_archivo):
    fake_s3.archivo = make_archivo(contenido=b"a,b\n1,2\n", filename="datos.csv", mime="text/csv")
    resultado = await ExtraerTablasPdfTool().run(make_ctx(), {"file_id": str(uuid4())})
    assert "no es un PDF" in resultado.content


async def test_pdf_corrupto_da_error_claro_sin_lanzar(make_ctx, fake_s3, make_archivo):
    fake_s3.archivo = make_archivo(
        contenido=b"esto no es un pdf de verdad", filename="malo.pdf", mime="application/pdf"
    )
    resultado = await ExtraerTablasPdfTool().run(make_ctx(), {"file_id": str(uuid4())})
    assert "No pude leer" in resultado.content


async def test_detecta_tabla_en_una_pagina_y_no_en_la_otra(make_ctx, fake_s3, make_archivo):
    pdf_bytes = _construir_pdf([_PAGINA_CON_TABLA, _PAGINA_SIN_TABLA])
    fake_s3.archivo = make_archivo(
        contenido=pdf_bytes, filename="reporte.pdf", mime="application/pdf"
    )

    resultado = await ExtraerTablasPdfTool().run(make_ctx(), {"file_id": str(uuid4())})

    assert resultado.data["paginas"] == [1, 2]
    tablas = resultado.data["tablas_csv"]
    assert len(tablas) == 1
    assert tablas[0]["pagina"] == 1
    assert tablas[0]["indice"] == 0
    assert tablas[0]["filas"] == 4  # encabezado + 3 filas de datos

    filas_csv = list(csv.reader(io.StringIO(tablas[0]["csv"])))
    assert filas_csv == [
        ["Nombre", "Edad", "Ciudad"],
        ["Ana", "30", "Bogota"],
        ["Luis", "25", "Medellin"],
        ["Carlos", "40", "Cali"],
    ]
    assert "Detecté 1 tabla" in resultado.content


async def test_sin_tablas_detectadas_lo_dice_explicitamente(make_ctx, fake_s3, make_archivo):
    pdf_bytes = _construir_pdf([_PAGINA_SIN_TABLA])
    fake_s3.archivo = make_archivo(
        contenido=pdf_bytes, filename="notas.pdf", mime="application/pdf"
    )

    resultado = await ExtraerTablasPdfTool().run(make_ctx(), {"file_id": str(uuid4())})

    assert resultado.data["tablas_csv"] == []
    assert "No detecté tablas" in resultado.content


async def test_paginas_restringe_el_procesamiento(make_ctx, fake_s3, make_archivo):
    pdf_bytes = _construir_pdf([_PAGINA_CON_TABLA, _PAGINA_SIN_TABLA, _PAGINA_CON_TABLA])
    fake_s3.archivo = make_archivo(
        contenido=pdf_bytes, filename="reporte.pdf", mime="application/pdf"
    )

    resultado = await ExtraerTablasPdfTool().run(
        make_ctx(), {"file_id": str(uuid4()), "paginas": [2]}
    )

    assert resultado.data["paginas"] == [2]
    assert resultado.data["tablas_csv"] == []  # la página 2 no tiene tabla


async def test_paginas_fuera_de_rango_no_procesa_nada(make_ctx, fake_s3, make_archivo):
    pdf_bytes = _construir_pdf([_PAGINA_CON_TABLA])
    fake_s3.archivo = make_archivo(
        contenido=pdf_bytes, filename="reporte.pdf", mime="application/pdf"
    )

    resultado = await ExtraerTablasPdfTool().run(
        make_ctx(), {"file_id": str(uuid4()), "paginas": [99]}
    )

    assert "no tiene páginas para procesar" in resultado.content
