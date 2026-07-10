"""Tests offline (respx) de `edecan_smarthome.client.HomeAssistantClient`.

Sin red real (`ARCHITECTURE.md` §10.15). `asyncio_mode = "auto"`
(`pyproject.toml` de este paquete) — sin `@pytest.mark.asyncio` en cada test,
mismo estilo que `packages/browser/tests/test_fetch.py`.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from edecan_smarthome.client import MAX_ENTIDADES, HomeAssistantClient, HomeAssistantError

BASE_URL = "http://homeassistant.local:8123"
TOKEN = "token-largo-de-prueba"


def _cliente(base_url: str = BASE_URL, token: str = TOKEN) -> HomeAssistantClient:
    return HomeAssistantClient(base_url, token)


def _entidad(entity_id: str, state: str = "on", friendly_name: str | None = None) -> dict:
    return {
        "entity_id": entity_id,
        "state": state,
        "attributes": {"friendly_name": friendly_name or entity_id},
    }


# ---------------------------------------------------------------------------
# Construcción / validación de base_url y token
# ---------------------------------------------------------------------------


def test_exige_token():
    with pytest.raises(HomeAssistantError):
        HomeAssistantClient(BASE_URL, "")


def test_exige_token_no_solo_espacios():
    with pytest.raises(HomeAssistantError):
        HomeAssistantClient(BASE_URL, "   ")


@pytest.mark.parametrize(
    "url", ["ftp://homeassistant.local:8123", "no-es-una-url", "javascript:alert(1)", ""]
)
def test_rechaza_esquema_no_http(url):
    with pytest.raises(HomeAssistantError):
        HomeAssistantClient(url, TOKEN)


def test_rechaza_credenciales_embebidas():
    with pytest.raises(HomeAssistantError):
        HomeAssistantClient("http://user:pass@homeassistant.local:8123", TOKEN)


@pytest.mark.parametrize(
    "url",
    [
        "http://homeassistant.local:8123",
        "http://192.168.1.50:8123",
        "https://10.0.0.5:8123",
        "http://127.0.0.1:8123",
    ],
)
def test_acepta_ips_privadas_y_mdns_local_a_proposito(url):
    """Protección SSRF INVERTIDA respecto a `edecan_browser.policy`: una IP
    privada o un hostname `.local` es el caso NORMAL para Home Assistant (ver
    docstring del módulo bajo prueba) — nunca debe rechazarse por eso."""
    cliente = HomeAssistantClient(url, TOKEN)
    assert cliente.base_url == url


def test_normaliza_quitando_slash_final():
    cliente = HomeAssistantClient(f"{BASE_URL}/", TOKEN)
    assert cliente.base_url == BASE_URL


# ---------------------------------------------------------------------------
# ping()
# ---------------------------------------------------------------------------


@respx.mock
async def test_ping_ok():
    respx.get(f"{BASE_URL}/api/").mock(
        return_value=httpx.Response(200, json={"message": "API running."})
    )
    assert await _cliente().ping() is True


@respx.mock
async def test_ping_401_token_invalido():
    respx.get(f"{BASE_URL}/api/").mock(return_value=httpx.Response(401))
    with pytest.raises(HomeAssistantError, match="token"):
        await _cliente().ping()


@respx.mock
async def test_ping_error_5xx():
    respx.get(f"{BASE_URL}/api/").mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(HomeAssistantError, match="500"):
        await _cliente().ping()


@respx.mock
async def test_ping_timeout_mensaje_accionable():
    respx.get(f"{BASE_URL}/api/").mock(side_effect=httpx.ConnectTimeout("timed out"))
    with pytest.raises(HomeAssistantError) as exc_info:
        await _cliente().ping()
    assert "encendido" in str(exc_info.value)


@respx.mock
async def test_ping_envia_bearer_del_token():
    ruta = respx.get(f"{BASE_URL}/api/").mock(return_value=httpx.Response(200))
    await _cliente(token="mi-token-secreto").ping()
    assert ruta.calls.last.request.headers["Authorization"] == "Bearer mi-token-secreto"


# ---------------------------------------------------------------------------
# estados()
# ---------------------------------------------------------------------------


@respx.mock
async def test_estados_sin_filtro():
    payload = [_entidad("light.sala"), _entidad("switch.enchufe")]
    respx.get(f"{BASE_URL}/api/states").mock(return_value=httpx.Response(200, json=payload))
    entidades = await _cliente().estados()
    assert [e["entity_id"] for e in entidades] == ["light.sala", "switch.enchufe"]
    assert entidades[0]["friendly_name"] == "light.sala"


@respx.mock
async def test_estados_filtra_por_dominio():
    payload = [_entidad("light.sala"), _entidad("switch.enchufe"), _entidad("light.cocina")]
    respx.get(f"{BASE_URL}/api/states").mock(return_value=httpx.Response(200, json=payload))
    entidades = await _cliente().estados("light")
    assert {e["entity_id"] for e in entidades} == {"light.sala", "light.cocina"}


@respx.mock
async def test_estados_filtro_no_hace_match_parcial_de_dominio():
    """'light' no debe matchear 'lighting.algo' — requiere el separador '.'."""
    payload = [_entidad("light.sala"), _entidad("lighting.algo")]
    respx.get(f"{BASE_URL}/api/states").mock(return_value=httpx.Response(200, json=payload))
    entidades = await _cliente().estados("light")
    assert [e["entity_id"] for e in entidades] == ["light.sala"]


@respx.mock
async def test_estados_cap_200_entidades():
    payload = [_entidad(f"light.luz_{i}") for i in range(250)]
    respx.get(f"{BASE_URL}/api/states").mock(return_value=httpx.Response(200, json=payload))
    entidades = await _cliente().estados()
    assert len(entidades) == MAX_ENTIDADES == 200


@respx.mock
async def test_estados_filtro_se_aplica_antes_del_cap():
    """250 luces + 1 switch: filtrar por 'switch' debe encontrar la única
    coincidencia, no perderla por haber topado el cap en las 200 primeras
    entidades SIN filtrar (que serían solo luces)."""
    payload = [_entidad(f"light.luz_{i}") for i in range(250)] + [_entidad("switch.unico")]
    respx.get(f"{BASE_URL}/api/states").mock(return_value=httpx.Response(200, json=payload))
    entidades = await _cliente().estados("switch")
    assert [e["entity_id"] for e in entidades] == ["switch.unico"]


@respx.mock
async def test_estados_respuesta_no_json():
    respx.get(f"{BASE_URL}/api/states").mock(return_value=httpx.Response(200, text="no soy json"))
    with pytest.raises(HomeAssistantError, match="no-JSON"):
        await _cliente().estados()


@respx.mock
async def test_estados_401():
    respx.get(f"{BASE_URL}/api/states").mock(return_value=httpx.Response(401))
    with pytest.raises(HomeAssistantError, match="token"):
        await _cliente().estados()


# ---------------------------------------------------------------------------
# estado()
# ---------------------------------------------------------------------------


@respx.mock
async def test_estado_ok():
    respx.get(f"{BASE_URL}/api/states/light.sala").mock(
        return_value=httpx.Response(
            200,
            json={
                "entity_id": "light.sala",
                "state": "on",
                "attributes": {"friendly_name": "Sala", "brightness": 200},
            },
        )
    )
    estado = await _cliente().estado("light.sala")
    assert estado["state"] == "on"
    assert estado["attributes"]["brightness"] == 200


@respx.mock
async def test_estado_404_mensaje_claro():
    respx.get(f"{BASE_URL}/api/states/light.no_existe").mock(return_value=httpx.Response(404))
    with pytest.raises(HomeAssistantError, match="No existe la entidad"):
        await _cliente().estado("light.no_existe")


@respx.mock
async def test_estado_401():
    respx.get(f"{BASE_URL}/api/states/light.sala").mock(return_value=httpx.Response(401))
    with pytest.raises(HomeAssistantError, match="token"):
        await _cliente().estado("light.sala")


# ---------------------------------------------------------------------------
# llamar_servicio()
# ---------------------------------------------------------------------------


@respx.mock
async def test_llamar_servicio_ok_envia_service_data():
    ruta = respx.post(f"{BASE_URL}/api/services/light/turn_on").mock(
        return_value=httpx.Response(200, json=[{"entity_id": "light.sala", "state": "on"}])
    )
    resultado = await _cliente().llamar_servicio(
        "light", "turn_on", {"entity_id": "light.sala", "brightness": 200}
    )
    assert resultado == [{"entity_id": "light.sala", "state": "on"}]
    enviado = json.loads(ruta.calls.last.request.content)
    assert enviado == {"entity_id": "light.sala", "brightness": 200}


@respx.mock
async def test_llamar_servicio_sin_service_data_envia_objeto_vacio():
    ruta = respx.post(f"{BASE_URL}/api/services/homeassistant/turn_off").mock(
        return_value=httpx.Response(200, json=[])
    )
    await _cliente().llamar_servicio("homeassistant", "turn_off", None)
    assert json.loads(ruta.calls.last.request.content) == {}


@respx.mock
async def test_llamar_servicio_respuesta_vacia_devuelve_none():
    respx.post(f"{BASE_URL}/api/services/light/turn_off").mock(return_value=httpx.Response(200))
    resultado = await _cliente().llamar_servicio("light", "turn_off", {"entity_id": "light.sala"})
    assert resultado is None


@respx.mock
async def test_llamar_servicio_error_5xx():
    respx.post(f"{BASE_URL}/api/services/light/turn_on").mock(
        return_value=httpx.Response(500, text="boom")
    )
    with pytest.raises(HomeAssistantError, match="500"):
        await _cliente().llamar_servicio("light", "turn_on", {"entity_id": "light.sala"})


@respx.mock
async def test_llamar_servicio_401():
    respx.post(f"{BASE_URL}/api/services/light/turn_on").mock(return_value=httpx.Response(401))
    with pytest.raises(HomeAssistantError, match="token"):
        await _cliente().llamar_servicio("light", "turn_on", {"entity_id": "light.sala"})
