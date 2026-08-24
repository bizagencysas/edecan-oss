from __future__ import annotations

from uuid import uuid4

from edecan_core.agent import _imagen_de_la_mac
from edecan_core.companion_access import companion_para, register_companion_factory


def test_companion_para_devuelve_none_sin_factory() -> None:
    register_companion_factory(None)
    assert companion_para(uuid4()) is None


def test_companion_para_usa_la_factory_registrada() -> None:
    tenant = uuid4()

    async def caller(accion: str, params: dict) -> dict:
        return {"ok": True, "accion": accion, **params}

    register_companion_factory(lambda tid: caller if tid == tenant else None)
    try:
        assert companion_para(tenant) is caller
        assert companion_para(uuid4()) is None
    finally:
        register_companion_factory(None)


def test_imagen_de_la_mac_arma_bloque_multimodal() -> None:
    bloque = _imagen_de_la_mac(
        {"accion": "screenshot", "resultado": {"ok": True, "image_b64": "Zm90bw==", "mime": "image/png"}}
    )
    assert bloque == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "Zm90bw=="},
    }


def test_imagen_de_la_mac_ignora_resultado_sin_foto() -> None:
    assert _imagen_de_la_mac({"accion": "open_app", "resultado": {"ok": True}}) is None
    assert _imagen_de_la_mac(None) is None


def test_relato_de_la_mac_nombra_cursor_no_un_escritorio_generico() -> None:
    from edecan_core.agent import _relato_de_la_mac

    texto = _relato_de_la_mac(
        {
            "accion": "screenshot",
            "resultado": {
                "ok": True,
                "image_b64": "Zm90bw==",
                "ventanas": [
                    {"app": "Cursor", "titulo": "Edecan-Nuevo", "al_frente": True},
                    {"app": "Safari", "titulo": "GitHub"},
                ],
            },
        }
    )
    assert "Cursor" in texto
    assert "Edecan-Nuevo" in texto
    assert "(al frente)" in texto
    assert "- Cursor — Edecan-Nuevo (al frente)" in texto
    assert "- Safari — GitHub" in texto


def test_imagenes_de_la_mac_ponen_recorte_despues_del_escritorio() -> None:
    from edecan_core.agent import _imagenes_de_la_mac

    imagenes = _imagenes_de_la_mac(
        {
            "accion": "screenshot",
            "resultado": {
                "ok": True,
                "image_b64": "AAA",
                "mime": "image/webp",
                "crop_b64": "BBB",
                "crop_mime": "image/webp",
            },
        }
    )
    assert [item["source"]["data"] for item in imagenes] == ["AAA", "BBB"]


def test_relato_de_la_mac_incluye_ocr_y_foco() -> None:
    from edecan_core.agent import _relato_de_la_mac

    texto = _relato_de_la_mac(
        {
            "accion": "screenshot",
            "resultado": {
                "ok": True,
                "image_b64": "Zm90bw==",
                "crop_b64": "cmVjb3J0ZQ==",
                "ventanas": [{"app": "Cursor", "titulo": "Edecan-Nuevo", "al_frente": True}],
                "foco": {"app": "Cursor", "rol": "AXTextArea", "valor": "hola"},
                "texto_visible": ["Eso estoy haciendo justo al momento de escribir este mensaje"],
            },
        }
    )
    assert "segunda foto" in texto
    assert "Texto del campo enfocado: hola" in texto
    assert "Eso estoy haciendo justo al momento de escribir este mensaje" in texto
