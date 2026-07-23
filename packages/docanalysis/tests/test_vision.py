"""Tests de `edecan_docanalysis.vision` (`analizar_imagen`)."""

from __future__ import annotations

from uuid import uuid4

from edecan_docanalysis.vision import AnalizarImagenTool

_PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
    "de0000000c4944415478da6360000002000155bfabd3000000004945"
    "4e44ae426082"
)


async def test_file_id_invalido(make_ctx, fake_s3):
    resultado = await AnalizarImagenTool().run(make_ctx(), {"file_id": "no-es-uuid"})
    assert "identificador válido" in resultado.content


async def test_archivo_no_encontrado(make_ctx, fake_s3):
    fake_s3.archivo = None
    resultado = await AnalizarImagenTool().run(make_ctx(), {"file_id": str(uuid4())})
    assert "No encontré ese archivo" in resultado.content


async def test_rechaza_archivo_demasiado_grande(make_ctx, fake_s3, make_archivo):
    contenido = b"\x00" * (5 * 1024 * 1024 + 1)
    fake_s3.archivo = make_archivo(contenido=contenido, filename="grande.png", mime="image/png")

    resultado = await AnalizarImagenTool().run(make_ctx(), {"file_id": str(uuid4())})

    assert "5 MB" in resultado.content


async def test_rechaza_formato_no_soportado(make_ctx, fake_s3, make_archivo):
    fake_s3.archivo = make_archivo(contenido=b"RIFF....", filename="video.mp4", mime="video/mp4")

    resultado = await AnalizarImagenTool().run(make_ctx(), {"file_id": str(uuid4())})

    assert "no es una imagen soportada" in resultado.content


async def test_proveedor_openai_compatible_recibe_la_imagen_por_contrato_comun(
    make_ctx, fake_s3, make_archivo, make_llm
):
    fake_s3.archivo = make_archivo(contenido=_PNG_1PX, filename="captura.png", mime="image/png")
    llm = make_llm(proveedor_nombre="openai_compat")
    ctx = make_ctx(llm=llm)

    resultado = await AnalizarImagenTool().run(ctx, {"file_id": str(uuid4())})

    assert resultado.content == "respuesta de prueba"
    assert len(llm.llamadas) == 1
    assert llm.llamadas[0][2].messages[0].content[0]["type"] == "image"


async def test_analiza_imagen_con_proveedor_anthropic_y_pregunta_por_defecto(
    make_ctx, fake_s3, make_archivo, make_llm
):
    fake_s3.archivo = make_archivo(contenido=_PNG_1PX, filename="captura.png", mime="image/png")
    llm = make_llm(texto="Es un píxel transparente.")
    ctx = make_ctx(llm=llm, extras={"flags": {"models.premium": True}})

    resultado = await AnalizarImagenTool().run(ctx, {"file_id": str(uuid4())})

    assert resultado.content == "Es un píxel transparente."
    assert resultado.data["mime"] == "image/png"
    assert resultado.data["pregunta"] == "Describe y transcribe (OCR) esta imagen."

    assert len(llm.llamadas) == 1
    alias, flags, req = llm.llamadas[0]
    assert alias == "rapido"
    assert flags == {"models.premium": True}

    bloques = req.messages[0].content
    assert isinstance(bloques, list)
    assert bloques[0]["type"] == "image"
    assert bloques[0]["source"]["type"] == "base64"
    assert bloques[0]["source"]["media_type"] == "image/png"
    import base64

    assert base64.b64decode(bloques[0]["source"]["data"]) == _PNG_1PX
    assert bloques[1] == {"type": "text", "text": "Describe y transcribe (OCR) esta imagen."}


async def test_analiza_imagen_con_pregunta_explicita(make_ctx, fake_s3, make_archivo, make_llm):
    fake_s3.archivo = make_archivo(contenido=_PNG_1PX, filename="captura.png", mime="image/png")
    llm = make_llm(texto="Sí, hay un logo.")
    ctx = make_ctx(llm=llm)

    resultado = await AnalizarImagenTool().run(
        ctx, {"file_id": str(uuid4()), "pregunta": "¿Hay un logo?"}
    )

    assert resultado.content == "Sí, hay un logo."
    assert resultado.data["pregunta"] == "¿Hay un logo?"
    _alias, _flags, req = llm.llamadas[0]
    assert req.messages[0].content[1] == {"type": "text", "text": "¿Hay un logo?"}


async def test_resuelve_mime_por_extension_si_el_declarado_es_generico(
    make_ctx, fake_s3, make_archivo, make_llm
):
    fake_s3.archivo = make_archivo(
        contenido=_PNG_1PX, filename="foto.jpg", mime="application/octet-stream"
    )
    llm = make_llm(texto="ok")
    ctx = make_ctx(llm=llm)

    resultado = await AnalizarImagenTool().run(ctx, {"file_id": str(uuid4())})

    assert resultado.data["mime"] == "image/jpeg"


async def test_respuesta_vacia_del_llm_cae_a_mensaje_claro(
    make_ctx, fake_s3, make_archivo, make_llm
):
    fake_s3.archivo = make_archivo(contenido=_PNG_1PX, filename="captura.png", mime="image/png")
    llm = make_llm(texto="   ")
    ctx = make_ctx(llm=llm)

    resultado = await AnalizarImagenTool().run(ctx, {"file_id": str(uuid4())})

    assert "No logré analizar" in resultado.content
