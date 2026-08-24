"""Tests de `edecan_advisory._texto`: extracción PDF/DOCX/TXT/MD (con
archivos generados en el propio test, sin fixtures binarios versionados) y
el helper de S3 (`descargar_archivo`/`subir_resultado`, fakeando `aioboto3`
— mismo criterio que `packages/docanalysis/tests/test_s3.py`)."""

from __future__ import annotations

import io
import sys
import types
from typing import Any
from uuid import uuid4

import pytest
from edecan_advisory import _texto
from edecan_advisory._texto import ArchivoDescargado, FormatoNoSoportado


def _build_pdf_bytes(texto: str) -> bytes:
    """Construye un PDF real y mínimo con `texto` como único contenido de una
    página, usando solo `pypdf` (ya es una dependencia dura del paquete) —
    sin pulling ninguna librería extra de autoría de PDFs solo para pruebas.
    El contenido stream es PDF estándar (`BT ... Tj ET`), así que
    `pypdf.PdfReader(...).pages[0].extract_text()` lo recupera igual que
    haría con un PDF "de verdad" generado por una herramienta de oficina."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)

    escapado = texto.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    stream_obj = DecodedStreamObject()
    stream_obj.set_data(f"BT /F1 12 Tf 72 712 Td ({escapado}) Tj ET".encode("latin-1"))
    stream_ref = writer._add_object(stream_obj)

    font_dict = DictionaryObject()
    font_dict[NameObject("/Type")] = NameObject("/Font")
    font_dict[NameObject("/Subtype")] = NameObject("/Type1")
    font_dict[NameObject("/BaseFont")] = NameObject("/Helvetica")
    font_ref = writer._add_object(font_dict)

    resources = DictionaryObject()
    fonts = DictionaryObject()
    fonts[NameObject("/F1")] = font_ref
    resources[NameObject("/Font")] = fonts

    page[NameObject("/Contents")] = stream_ref
    page[NameObject("/Resources")] = resources

    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _build_docx_bytes(parrafos: list[str]) -> bytes:
    import docx

    documento = docx.Document()
    for parrafo in parrafos:
        documento.add_paragraph(parrafo)
    buffer = io.BytesIO()
    documento.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# extraer_texto: PDF / DOCX / TXT / MD / no soportado / capado
# ---------------------------------------------------------------------------


def test_extraer_texto_pdf_real_generado_en_el_test():
    contenido = _build_pdf_bytes("Contrato de prueba entre Acme y Beta")
    archivo = ArchivoDescargado(
        contenido=contenido,
        filename="contrato.pdf",
        mime="application/pdf",
        size_bytes=len(contenido),
    )

    texto = _texto.extraer_texto(archivo)

    assert "Contrato de prueba entre Acme y Beta" in texto


def test_extraer_texto_docx_real_generado_en_el_test():
    parrafos = ["Primer párrafo del contrato.", "Segundo párrafo con la cláusula 2."]
    contenido = _build_docx_bytes(parrafos)
    archivo = ArchivoDescargado(
        contenido=contenido,
        filename="contrato.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size_bytes=len(contenido),
    )

    texto = _texto.extraer_texto(archivo)

    assert "Primer párrafo del contrato." in texto
    assert "Segundo párrafo con la cláusula 2." in texto


def test_extraer_texto_docx_detectado_por_extension_aunque_el_mime_sea_generico():
    contenido = _build_docx_bytes(["Texto detectado por extensión."])
    archivo = ArchivoDescargado(
        contenido=contenido,
        filename="contrato.docx",
        mime="application/octet-stream",
        size_bytes=len(contenido),
    )

    texto = _texto.extraer_texto(archivo)

    assert "Texto detectado por extensión." in texto


@pytest.mark.parametrize("filename", ["notas.txt", "notas.md"])
def test_extraer_texto_txt_y_md_decodifica_utf8(filename: str):
    contenido = "Notas en español con ñ y acentos áéíóú.".encode()
    archivo = ArchivoDescargado(
        contenido=contenido, filename=filename, mime="text/plain", size_bytes=len(contenido)
    )

    texto = _texto.extraer_texto(archivo)

    assert texto == "Notas en español con ñ y acentos áéíóú."


def test_extraer_texto_formato_no_soportado_lanza_excepcion_de_negocio():
    archivo = ArchivoDescargado(
        contenido=b"\x89PNG...", filename="foto.png", mime="image/png", size_bytes=8
    )

    with pytest.raises(FormatoNoSoportado, match="foto.png"):
        _texto.extraer_texto(archivo)


def test_extraer_texto_capa_a_max_chars():
    contenido = ("a" * (_texto.MAX_CHARS + 500)).encode("utf-8")
    archivo = ArchivoDescargado(
        contenido=contenido, filename="grande.txt", mime="text/plain", size_bytes=len(contenido)
    )

    texto = _texto.extraer_texto(archivo)

    assert len(texto) == _texto.MAX_CHARS


# ---------------------------------------------------------------------------
# descargar_archivo / subir_resultado: fakea `aioboto3` (nunca red real)
# ---------------------------------------------------------------------------


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def read(self) -> bytes:
        return self._data


class _FakeS3Client:
    def __init__(self, almacen: dict[tuple[str, str], bytes]) -> None:
        self._almacen = almacen
        self.puestos: list[tuple[str, str, bytes, str]] = []

    async def __aenter__(self) -> _FakeS3Client:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        return {"Body": _FakeBody(self._almacen[(Bucket, Key)])}

    async def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> None:
        self._almacen[(Bucket, Key)] = Body
        self.puestos.append((Bucket, Key, Body, ContentType))


class _FakeBotoSession:
    def __init__(self, almacen: dict[tuple[str, str], bytes]) -> None:
        self._almacen = almacen
        self.clientes: list[_FakeS3Client] = []

    def client(self, servicio: str, **kwargs: Any) -> _FakeS3Client:
        assert servicio == "s3"
        cliente = _FakeS3Client(self._almacen)
        self.clientes.append(cliente)
        return cliente


@pytest.fixture
def fake_aioboto3(monkeypatch):
    """Registra un `aioboto3` falso en `sys.modules` — `_texto.py` hace
    `import aioboto3` perezoso DENTRO de cada función, así que basta con
    pre-registrar el módulo falso antes de invocar `descargar_archivo`/
    `subir_resultado` (mismo criterio que `edecan_docanalysis/tests/test_s3.py`)."""
    almacen: dict[tuple[str, str], bytes] = {}

    fake_modulo = types.ModuleType("aioboto3")
    fake_modulo.Session = lambda: _FakeBotoSession(almacen)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "aioboto3", fake_modulo)
    return types.SimpleNamespace(almacen=almacen)


async def test_descargar_archivo_lee_fila_y_baja_bytes(make_ctx, make_session, fake_aioboto3):
    tenant_id = uuid4()
    file_id = uuid4()
    s3_key = f"tenants/{tenant_id}/files/{file_id}/laboratorio.txt"
    fila = {
        "id": file_id,
        "s3_key": s3_key,
        "filename": "laboratorio.txt",
        "mime": "text/plain",
        "size_bytes": 9,
    }
    session = make_session([[fila]])
    ctx = make_ctx(session=session, tenant_id=tenant_id)
    fake_aioboto3.almacen[("edecan-files-test", s3_key)] = b"Glucosa 9"

    resultado = await _texto.descargar_archivo(ctx, file_id)

    assert resultado is not None
    assert resultado.contenido == b"Glucosa 9"
    assert resultado.filename == "laboratorio.txt"


async def test_descargar_archivo_devuelve_none_si_no_existe(make_ctx, make_session, fake_aioboto3):
    session = make_session([[]])
    resultado = await _texto.descargar_archivo(make_ctx(session=session), uuid4())
    assert resultado is None


async def test_subir_resultado_sube_a_s3_e_inserta_fila_files(
    make_ctx, make_session, fake_aioboto3
):
    session = make_session([])
    tenant_id = uuid4()
    ctx = make_ctx(session=session, tenant_id=tenant_id)

    file_id = await _texto.subir_resultado(
        ctx, filename="borrador-nda-ana.md", mime="text/markdown", contenido=b"# NDA"
    )

    clave = ("edecan-files-test", f"tenants/{tenant_id}/files/{file_id}/borrador-nda-ana.md")
    assert fake_aioboto3.almacen[clave] == b"# NDA"

    sql, params = session.llamadas[0]
    assert "INSERT INTO files" in sql
    assert "'ready'" in sql
    assert params["filename"] == "borrador-nda-ana.md"
    assert params["mime"] == "text/markdown"


async def test_extraer_texto_de_file_id_compone_descarga_y_extraccion(
    make_ctx, make_session, fake_aioboto3
):
    tenant_id = uuid4()
    file_id = uuid4()
    s3_key = f"tenants/{tenant_id}/files/{file_id}/notas.txt"
    fila = {
        "id": file_id,
        "s3_key": s3_key,
        "filename": "notas.txt",
        "mime": "text/plain",
        "size_bytes": 5,
    }
    session = make_session([[fila]])
    ctx = make_ctx(session=session, tenant_id=tenant_id)
    fake_aioboto3.almacen[("edecan-files-test", s3_key)] = b"hola\n"

    extraido = await _texto.extraer_texto_de_file_id(ctx, file_id)

    assert extraido is not None
    assert extraido.texto == "hola\n"
    assert extraido.archivo.filename == "notas.txt"


async def test_extraer_texto_de_file_id_none_si_no_existe(make_ctx, make_session, fake_aioboto3):
    session = make_session([[]])
    extraido = await _texto.extraer_texto_de_file_id(make_ctx(session=session), uuid4())
    assert extraido is None
