"""Tests de `edecan_voice.cloning` contra respuestas HTTP simuladas con respx
(WP-V5-10) — cero red real, mismo patrón que `test_voice_elevenlabs.py`.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from edecan_voice.cloning import (
    MuestraVoz,
    VoiceCloningError,
    VozDisponible,
    borrar_clon,
    crear_clon,
    generar_efecto,
    listar_voces,
)

FAKE_API_KEY = "fake-elevenlabs-key"

# ---------------------------------------------------------------------------
# listar_voces
# ---------------------------------------------------------------------------


@respx.mock
async def test_listar_voces_parsea_el_catalogo():
    route = respx.get("https://api.elevenlabs.io/v1/voices").mock(
        return_value=httpx.Response(
            200,
            json={
                "voices": [
                    {
                        "voice_id": "voz-1",
                        "name": "Voz Uno",
                        "category": "premade",
                        "preview_url": "https://cdn.example.com/voz-1.mp3",
                    },
                    {
                        "voice_id": "voz-2",
                        "name": "Mi Clon",
                        "category": "cloned",
                        "preview_url": None,
                    },
                ]
            },
        )
    )

    voces = await listar_voces(FAKE_API_KEY)

    assert route.called
    assert route.calls.last.request.headers["xi-api-key"] == FAKE_API_KEY
    assert voces == [
        VozDisponible(
            voice_id="voz-1",
            nombre="Voz Uno",
            categoria="premade",
            preview_url="https://cdn.example.com/voz-1.mp3",
        ),
        VozDisponible(voice_id="voz-2", nombre="Mi Clon", categoria="cloned", preview_url=None),
    ]


@respx.mock
async def test_listar_voces_ignora_entradas_sin_id_o_nombre():
    respx.get("https://api.elevenlabs.io/v1/voices").mock(
        return_value=httpx.Response(
            200,
            json={
                "voices": [
                    {"voice_id": "voz-1", "name": "Voz Uno", "category": "premade"},
                    {"voice_id": "", "name": "Sin id"},
                    {"voice_id": "voz-3", "name": None},
                ]
            },
        )
    )

    voces = await listar_voces(FAKE_API_KEY)

    assert len(voces) == 1
    assert voces[0].voice_id == "voz-1"


@respx.mock
async def test_listar_voces_categoria_por_defecto_premade():
    respx.get("https://api.elevenlabs.io/v1/voices").mock(
        return_value=httpx.Response(
            200, json={"voices": [{"voice_id": "voz-1", "name": "Voz Uno"}]}
        )
    )

    voces = await listar_voces(FAKE_API_KEY)

    assert voces[0].categoria == "premade"


@respx.mock
async def test_listar_voces_error_http_no_filtra_la_api_key():
    respx.get("https://api.elevenlabs.io/v1/voices").mock(
        return_value=httpx.Response(401, text="invalid_api_key")
    )

    with pytest.raises(VoiceCloningError) as exc_info:
        await listar_voces(FAKE_API_KEY)

    mensaje = str(exc_info.value)
    assert "401" in mensaje
    assert "invalid_api_key" in mensaje
    assert FAKE_API_KEY not in mensaje


@respx.mock
async def test_listar_voces_respuesta_no_json_lanza_error_claro():
    respx.get("https://api.elevenlabs.io/v1/voices").mock(
        return_value=httpx.Response(200, text="<html>no soy json</html>")
    )

    with pytest.raises(VoiceCloningError):
        await listar_voces(FAKE_API_KEY)


async def test_listar_voces_error_de_red_no_filtra_la_api_key():
    # Sin ninguna ruta `respx` registrada y sin `@respx.mock`: httpx intentará
    # una conexión real que fallará rápido contra un puerto/host inválido —
    # en vez de depender de eso, mockeamos el transporte para simular un
    # error de red determinista.
    class _TransportQueFalla(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom", request=request)

    import edecan_voice.cloning as cloning_module

    original_client = httpx.AsyncClient

    def _client_con_transporte_roto(*args, **kwargs):
        kwargs["transport"] = _TransportQueFalla()
        return original_client(*args, **kwargs)

    cloning_module.httpx.AsyncClient = _client_con_transporte_roto
    try:
        with pytest.raises(VoiceCloningError) as exc_info:
            await listar_voces(FAKE_API_KEY)
    finally:
        cloning_module.httpx.AsyncClient = original_client

    assert FAKE_API_KEY not in str(exc_info.value)


# ---------------------------------------------------------------------------
# crear_clon
# ---------------------------------------------------------------------------


@respx.mock
async def test_crear_clon_envia_multipart_y_devuelve_voice_id():
    route = respx.post("https://api.elevenlabs.io/v1/voices/add").mock(
        return_value=httpx.Response(200, json={"voice_id": "nuevo-voice-id"})
    )

    voice_id = await crear_clon(
        FAKE_API_KEY,
        "Mi Voz Clonada",
        [MuestraVoz(data=b"audio-1", filename="muestra1.mp3")],
        "Una descripción",
    )

    assert voice_id == "nuevo-voice-id"
    assert route.called
    request = route.calls.last.request
    assert request.headers["xi-api-key"] == FAKE_API_KEY
    # Multipart: el cuerpo crudo debe traer el nombre del campo, el nombre de
    # archivo de la muestra y la descripción — sin parsear el multipart a
    # mano, basta con confirmar que viajaron como substrings del cuerpo.
    body = request.content
    assert b"Mi Voz Clonada" in body
    assert b"muestra1.mp3" in body
    assert b"Una descripcion" in body or "Una descripción".encode() in body


@respx.mock
async def test_crear_clon_con_varias_muestras():
    route = respx.post("https://api.elevenlabs.io/v1/voices/add").mock(
        return_value=httpx.Response(201, json={"voice_id": "otro-id"})
    )

    voice_id = await crear_clon(
        FAKE_API_KEY,
        "Nombre",
        [
            MuestraVoz(data=b"audio-1", filename="m1.mp3"),
            MuestraVoz(data=b"audio-2", filename="m2.mp3"),
            MuestraVoz(data=b"audio-3", filename="m3.wav", content_type="audio/wav"),
        ],
    )

    assert voice_id == "otro-id"
    assert route.called


async def test_crear_clon_sin_muestras_lanza_sin_llamar_a_la_red():
    with pytest.raises(VoiceCloningError, match="al menos una muestra"):
        await crear_clon(FAKE_API_KEY, "Nombre", [])


@respx.mock
async def test_crear_clon_error_http_expone_mensaje_del_proveedor_sin_la_key():
    respx.post("https://api.elevenlabs.io/v1/voices/add").mock(
        return_value=httpx.Response(422, text="voice_limit_reached")
    )

    with pytest.raises(VoiceCloningError) as exc_info:
        await crear_clon(FAKE_API_KEY, "Nombre", [MuestraVoz(data=b"x", filename="m.mp3")])

    mensaje = str(exc_info.value)
    assert "422" in mensaje
    assert "voice_limit_reached" in mensaje
    assert FAKE_API_KEY not in mensaje


@respx.mock
async def test_crear_clon_sin_voice_id_en_la_respuesta_lanza_error_claro():
    respx.post("https://api.elevenlabs.io/v1/voices/add").mock(
        return_value=httpx.Response(200, json={})
    )

    with pytest.raises(VoiceCloningError, match="voice_id"):
        await crear_clon(FAKE_API_KEY, "Nombre", [MuestraVoz(data=b"x", filename="m.mp3")])


# ---------------------------------------------------------------------------
# borrar_clon
# ---------------------------------------------------------------------------


@respx.mock
async def test_borrar_clon_ok():
    route = respx.delete("https://api.elevenlabs.io/v1/voices/voz-a-borrar").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )

    await borrar_clon(FAKE_API_KEY, "voz-a-borrar")

    assert route.called
    assert route.calls.last.request.headers["xi-api-key"] == FAKE_API_KEY


@respx.mock
async def test_borrar_clon_204_tambien_es_exito():
    respx.delete("https://api.elevenlabs.io/v1/voices/voz-a-borrar").mock(
        return_value=httpx.Response(204)
    )

    await borrar_clon(FAKE_API_KEY, "voz-a-borrar")  # no lanza


@respx.mock
async def test_borrar_clon_error_no_filtra_la_api_key():
    respx.delete("https://api.elevenlabs.io/v1/voices/voz-x").mock(
        return_value=httpx.Response(404, text="voice_not_found")
    )

    with pytest.raises(VoiceCloningError) as exc_info:
        await borrar_clon(FAKE_API_KEY, "voz-x")

    mensaje = str(exc_info.value)
    assert "voice_not_found" in mensaje
    assert FAKE_API_KEY not in mensaje


# ---------------------------------------------------------------------------
# generar_efecto
# ---------------------------------------------------------------------------


@respx.mock
async def test_generar_efecto_devuelve_bytes_mp3():
    route = respx.post("https://api.elevenlabs.io/v1/sound-generation").mock(
        return_value=httpx.Response(200, content=b"FAKE-MP3-EFECTO")
    )

    audio = await generar_efecto(FAKE_API_KEY, "una puerta cerrandose")

    assert audio == b"FAKE-MP3-EFECTO"
    assert route.called
    import json as json_module

    assert json_module.loads(route.calls.last.request.content) == {
        "text": "una puerta cerrandose"
    }


@respx.mock
async def test_generar_efecto_error_no_filtra_la_api_key():
    respx.post("https://api.elevenlabs.io/v1/sound-generation").mock(
        return_value=httpx.Response(400, text="invalid_text")
    )

    with pytest.raises(VoiceCloningError) as exc_info:
        await generar_efecto(FAKE_API_KEY, "texto raro")

    assert FAKE_API_KEY not in str(exc_info.value)
