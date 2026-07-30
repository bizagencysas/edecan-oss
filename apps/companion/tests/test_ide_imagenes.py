"""Pruebas de ``ide_imagenes``: imágenes como contexto del compositor (T2.1).

Cubre lo que pide el encargo -- tipo válido, tipo rechazado, tope de tamaño
superado, y modelo sin capacidad de visión -- más los casos que hacen que
esas cuatro reglas sean confiables: tipo declarado que no coincide con el
contenido real, modelo no declarado en absoluto, y que encadenar
``preparar_imagen_para_turno`` con un modelo ciego se detiene ANTES de tocar
los bytes de la imagen (el error debe ser "no ve", nunca "tipo no
reconocido").
"""

from __future__ import annotations

import base64
import io

import pytest
from edecan_companion.ide_imagenes import (
    MAX_IMAGEN_BYTES,
    IDEImagenError,
    modelo_soporta_vision,
    preparar_imagen_para_turno,
    validar_y_normalizar_imagen,
    verificar_modelo_ve,
)
from PIL import Image

_MODELOS_TEST = [
    {
        "id": "modelo-con-vision",
        "nombre": "Con Visión",
        "capacidades": ["vision", "herramientas"],
    },
    {
        "id": "modelo-sin-vision",
        "nombre": "Sin Visión",
        "capacidades": ["codigo", "herramientas"],
    },
]


def _png_1x1() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (1, 1), color=(200, 40, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


def _jpeg_2x3() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 3), color=(10, 20, 30)).save(buffer, format="JPEG")
    return buffer.getvalue()


# --- 1. tipo válido ---------------------------------------------------------


def test_imagen_png_valida_se_normaliza_y_arma_el_bloque():
    resultado = validar_y_normalizar_imagen(_png_1x1(), content_type_declarado="image/png")

    assert resultado.preparada.mime == "image/png"
    assert resultado.preparada.recodificada is True
    assert (resultado.preparada.ancho, resultado.preparada.alto) == (1, 1)

    bloque = resultado.bloque
    assert bloque["type"] == "image"
    assert bloque["source"]["type"] == "base64"
    assert bloque["source"]["media_type"] == "image/png"
    assert base64.b64decode(bloque["source"]["data"]).startswith(b"\x89PNG")

    # el resumen para la UI no repite el peso completo de la imagen
    assert set(resultado.preparada.public().keys()) == {
        "mime",
        "tamano_bytes",
        "ancho",
        "alto",
        "recodificada",
    }


def test_imagen_jpeg_valida_reporta_dimensiones_reales():
    resultado = validar_y_normalizar_imagen(_jpeg_2x3())

    assert resultado.preparada.mime == "image/jpeg"
    assert (resultado.preparada.ancho, resultado.preparada.alto) == (2, 3)


# --- 2. tipo rechazado -------------------------------------------------------


def test_tipo_no_reconocido_se_rechaza():
    with pytest.raises(IDEImagenError, match="no reconocido"):
        validar_y_normalizar_imagen(b"esto no es una imagen, es texto plano cualquiera")


def test_content_type_declarado_que_no_coincide_se_rechaza():
    with pytest.raises(IDEImagenError, match="no coincide"):
        validar_y_normalizar_imagen(_png_1x1(), content_type_declarado="image/jpeg")


def test_content_type_generico_no_bloquea_una_imagen_valida():
    # un content-type genérico (no una afirmación concreta de tipo de
    # imagen) no debe bloquear una imagen que sí es válida por firma real.
    resultado = validar_y_normalizar_imagen(
        _png_1x1(), content_type_declarado="application/octet-stream"
    )
    assert resultado.preparada.mime == "image/png"


def test_imagen_vacia_se_rechaza():
    with pytest.raises(IDEImagenError, match="vacía"):
        validar_y_normalizar_imagen(b"")


# --- 3. tope de tamaño superado ----------------------------------------------


def test_imagen_que_supera_el_tope_se_rechaza():
    demasiado_grande = b"\x89PNG\r\n\x1a\n" + b"0" * (MAX_IMAGEN_BYTES + 1)

    with pytest.raises(IDEImagenError, match="pesa"):
        validar_y_normalizar_imagen(demasiado_grande)


def test_tope_de_tamano_es_configurable_mas_estricto():
    with pytest.raises(IDEImagenError, match="pesa"):
        validar_y_normalizar_imagen(_png_1x1(), max_bytes=10)


# --- 4. modelo sin visión -----------------------------------------------------


def test_modelo_sin_vision_se_rechaza_con_mensaje_claro():
    with pytest.raises(IDEImagenError, match="no tiene capacidad de visión"):
        verificar_modelo_ve("modelo-sin-vision", modelos=_MODELOS_TEST)


def test_modelo_con_vision_pasa_y_devuelve_su_modelcard():
    fila = verificar_modelo_ve("modelo-con-vision", modelos=_MODELOS_TEST)
    assert fila["id"] == "modelo-con-vision"


def test_modelo_no_declarado_se_rechaza_como_sin_vision():
    assert modelo_soporta_vision("modelo-fantasma", modelos=_MODELOS_TEST) is False
    with pytest.raises(IDEImagenError, match="no está en la lista"):
        verificar_modelo_ve("modelo-fantasma", modelos=_MODELOS_TEST)


def test_modelo_soporta_vision_solo_con_la_capacidad_declarada():
    assert modelo_soporta_vision("modelo-con-vision", modelos=_MODELOS_TEST) is True
    assert modelo_soporta_vision("modelo-sin-vision", modelos=_MODELOS_TEST) is False


# --- 5. las dos mitades encadenadas -------------------------------------------


def test_preparar_imagen_para_turno_con_modelo_ciego_no_llega_a_decodificar():
    # Bytes claramente inválidos como imagen: si el error fuera "tipo no
    # reconocido" significaría que se intentó decodificar la imagen ANTES
    # de revisar el modelo. Debe fallar por el modelo, no por la imagen.
    with pytest.raises(IDEImagenError, match="no tiene capacidad de visión"):
        preparar_imagen_para_turno(
            b"esto ni siquiera es una imagen",
            "modelo-sin-vision",
            modelos=_MODELOS_TEST,
        )


def test_preparar_imagen_para_turno_con_modelo_vidente_normaliza_y_arma_bloque():
    resultado = preparar_imagen_para_turno(
        _png_1x1(), "modelo-con-vision", modelos=_MODELOS_TEST
    )
    assert resultado.bloque["source"]["media_type"] == "image/png"
    assert resultado.preparada.ancho == 1
