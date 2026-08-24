"""Tests de `edecan_smarthome.tools`: `CasaDispositivosTool`, `CasaEstadoTool`,
`CasaControlarTool`, `get_all_tools` (`ARCHITECTURE.md` §10.15). Sin red real
(`respx`), sin importar `edecan_db` — fakes locales de `conftest.py`.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import respx
from edecan_smarthome.tools import (
    CONNECTOR_KEY,
    DOMINIOS_BLOQUEADOS,
    CasaControlarTool,
    CasaDispositivosTool,
    CasaEstadoTool,
    get_all_tools,
)

BASE_URL = "http://homeassistant.local:8123"
TOKEN = "token-de-prueba"


def _fila_cuenta(cuenta_id: str = "acc-1") -> dict:
    return {"id": cuenta_id}


def _bundle(base_url: str = BASE_URL, token: str = TOKEN) -> SimpleNamespace:
    return SimpleNamespace(access_token=token, scopes=[base_url])


# ---------------------------------------------------------------------------
# Metadatos de las tools
# ---------------------------------------------------------------------------


def test_connector_key_pinned():
    assert CONNECTOR_KEY == "homeassistant"


def test_dominios_bloqueados_solo_lock():
    assert DOMINIOS_BLOQUEADOS == frozenset({"lock"})


def test_casa_dispositivos_no_es_dangerous_ni_requiere_flags():
    tool = CasaDispositivosTool()
    assert tool.dangerous is False
    assert tool.requires_flags == frozenset()
    assert tool.name == "casa_dispositivos"


def test_casa_estado_no_es_dangerous_ni_requiere_flags():
    tool = CasaEstadoTool()
    assert tool.dangerous is False
    assert tool.requires_flags == frozenset()
    assert tool.name == "casa_estado"


def test_casa_controlar_es_dangerous_y_sin_flags():
    """`dangerous=True` es lo único que gatea esta tool — el work package
    pide explícitamente NO agregar un flag de plan nuevo."""
    tool = CasaControlarTool()
    assert tool.dangerous is True
    assert tool.requires_flags == frozenset()
    assert tool.name == "casa_controlar"


def test_get_all_tools_devuelve_las_tres():
    tools = get_all_tools()
    nombres = {t.name for t in tools}
    assert nombres == {"casa_dispositivos", "casa_estado", "casa_controlar"}


# ---------------------------------------------------------------------------
# Sin credenciales configuradas — mensaje de configuración, nunca un error crudo
# ---------------------------------------------------------------------------


async def test_casa_dispositivos_sin_cuenta_conectada(make_ctx, make_session):
    ctx = make_ctx(session=make_session([[]]))
    resultado = await CasaDispositivosTool().run(ctx, {})
    assert "conectaste tu Home Assistant" in resultado.content


async def test_casa_estado_sin_cuenta_conectada(make_ctx, make_session):
    ctx = make_ctx(session=make_session([[]]))
    resultado = await CasaEstadoTool().run(ctx, {"entity_id": "light.sala"})
    assert "conectaste tu Home Assistant" in resultado.content


async def test_casa_controlar_sin_cuenta_conectada(make_ctx, make_session):
    ctx = make_ctx(session=make_session([[]]))
    resultado = await CasaControlarTool().run(
        ctx, {"entity_id": "light.sala", "accion": "encender"}
    )
    assert "conectaste tu Home Assistant" in resultado.content


async def test_cuenta_conectada_pero_sin_bundle_en_vault(make_ctx, make_session, make_vault):
    ctx = make_ctx(session=make_session([[_fila_cuenta()]]), vault=make_vault(bundle=None))
    resultado = await CasaDispositivosTool().run(ctx, {})
    assert "conectaste tu Home Assistant" in resultado.content


async def test_bundle_sin_scopes_se_trata_como_no_configurado(make_ctx, make_session, make_vault):
    bundle_sin_base_url = SimpleNamespace(access_token=TOKEN, scopes=[])
    ctx = make_ctx(
        session=make_session([[_fila_cuenta()]]), vault=make_vault(bundle=bundle_sin_base_url)
    )
    resultado = await CasaEstadoTool().run(ctx, {"entity_id": "light.sala"})
    assert "conectaste tu Home Assistant" in resultado.content


async def test_bundle_con_base_url_invalida_da_error_claro(make_ctx, make_session, make_vault):
    bundle_url_invalida = SimpleNamespace(access_token=TOKEN, scopes=["no-es-una-url"])
    ctx = make_ctx(
        session=make_session([[_fila_cuenta()]]), vault=make_vault(bundle=bundle_url_invalida)
    )
    resultado = await CasaDispositivosTool().run(ctx, {})
    assert "no es válida" in resultado.content or "no válida" in resultado.content


# ---------------------------------------------------------------------------
# Validación de argumentos (sin llegar a la red)
# ---------------------------------------------------------------------------


async def test_casa_estado_sin_entity_id(make_ctx):
    resultado = await CasaEstadoTool().run(make_ctx(), {})
    assert "entity_id" in resultado.content


async def test_casa_controlar_sin_entity_id(make_ctx):
    resultado = await CasaControlarTool().run(make_ctx(), {"accion": "encender"})
    assert "entity_id" in resultado.content


async def test_casa_controlar_sin_accion(make_ctx):
    resultado = await CasaControlarTool().run(make_ctx(), {"entity_id": "light.sala"})
    assert "acci" in resultado.content.lower()


async def test_casa_controlar_accion_no_entendida(make_ctx):
    resultado = await CasaControlarTool().run(
        make_ctx(), {"entity_id": "light.sala", "accion": "hacer-magia"}
    )
    assert "No entendí la acción" in resultado.content


# ---------------------------------------------------------------------------
# Guardrail de cerraduras — NUNCA, verificado en varias formas (bloquea ANTES
# de tocar vault/red: ver aserción sobre `ctx.session.llamadas`)
# ---------------------------------------------------------------------------


async def test_casa_controlar_bloquea_lock_unlock_explicito(make_ctx, make_session):
    ctx = make_ctx(session=make_session([[]]))
    resultado = await CasaControlarTool().run(
        ctx, {"entity_id": "lock.puerta_principal", "accion": "lock.unlock"}
    )
    assert "cerraduras" in resultado.content
    assert ctx.session.llamadas == []  # nunca llegó a resolver credenciales


async def test_casa_controlar_bloquea_lock_open_explicito(make_ctx):
    resultado = await CasaControlarTool().run(
        make_ctx(), {"entity_id": "lock.puerta_principal", "accion": "lock.open"}
    )
    assert "cerraduras" in resultado.content


async def test_casa_controlar_bloquea_lock_lock_explicito_tambien(make_ctx):
    """Bloquea el DOMINIO completo (`DOMINIOS_BLOQUEADOS`), no solo
    unlock/open — ni siquiera 'lock.lock' (bloquear, en sí inofensivo) pasa,
    por simplicidad y seguridad (ver docstring de `DOMINIOS_BLOQUEADOS`)."""
    resultado = await CasaControlarTool().run(
        make_ctx(), {"entity_id": "lock.puerta_principal", "accion": "lock.lock"}
    )
    assert "cerraduras" in resultado.content


async def test_casa_controlar_bloquea_apagar_mapeado_sobre_entidad_lock(make_ctx):
    """'apagar' mapea a homeassistant.turn_off, que Home Assistant traduce a
    un unlock real para una entidad del dominio 'lock' — debe bloquearse
    igual que el 'domain.service' explícito."""
    resultado = await CasaControlarTool().run(
        make_ctx(), {"entity_id": "lock.puerta_principal", "accion": "apagar"}
    )
    assert "cerraduras" in resultado.content


async def test_casa_controlar_bloquea_encender_mapeado_sobre_entidad_lock(make_ctx):
    resultado = await CasaControlarTool().run(
        make_ctx(), {"entity_id": "lock.puerta_principal", "accion": "encender"}
    )
    assert "cerraduras" in resultado.content


async def test_casa_controlar_bloquea_alternar_mapeado_sobre_entidad_lock(make_ctx):
    resultado = await CasaControlarTool().run(
        make_ctx(), {"entity_id": "lock.puerta_principal", "accion": "alternar"}
    )
    assert "cerraduras" in resultado.content


def test_dominio_light_no_esta_bloqueado():
    assert "light" not in DOMINIOS_BLOQUEADOS


# ---------------------------------------------------------------------------
# Happy paths (respx)
# ---------------------------------------------------------------------------


@respx.mock
async def test_casa_dispositivos_feliz(make_ctx, make_session, make_vault):
    respx.get(f"{BASE_URL}/api/states").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "entity_id": "light.sala",
                    "state": "on",
                    "attributes": {"friendly_name": "Sala"},
                },
            ],
        )
    )
    ctx = make_ctx(session=make_session([[_fila_cuenta()]]), vault=make_vault(bundle=_bundle()))

    resultado = await CasaDispositivosTool().run(ctx, {})

    assert "Sala" in resultado.content
    assert resultado.data["dispositivos"][0]["entity_id"] == "light.sala"


@respx.mock
async def test_casa_dispositivos_filtra_por_dominio(make_ctx, make_session, make_vault):
    ruta = respx.get(f"{BASE_URL}/api/states").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"entity_id": "light.sala", "state": "on", "attributes": {}},
                {"entity_id": "switch.enchufe", "state": "off", "attributes": {}},
            ],
        )
    )
    ctx = make_ctx(session=make_session([[_fila_cuenta()]]), vault=make_vault(bundle=_bundle()))

    resultado = await CasaDispositivosTool().run(ctx, {"dominio": "switch"})

    assert ruta.called
    assert [d["entity_id"] for d in resultado.data["dispositivos"]] == ["switch.enchufe"]


@respx.mock
async def test_casa_dispositivos_sin_resultados_para_el_dominio(make_ctx, make_session, make_vault):
    respx.get(f"{BASE_URL}/api/states").mock(return_value=httpx.Response(200, json=[]))
    ctx = make_ctx(session=make_session([[_fila_cuenta()]]), vault=make_vault(bundle=_bundle()))

    resultado = await CasaDispositivosTool().run(ctx, {"dominio": "climate"})

    assert "No encontré dispositivos" in resultado.content
    assert resultado.data == {"dispositivos": []}


@respx.mock
async def test_casa_estado_feliz(make_ctx, make_session, make_vault):
    respx.get(f"{BASE_URL}/api/states/light.sala").mock(
        return_value=httpx.Response(
            200,
            json={
                "entity_id": "light.sala",
                "state": "on",
                "attributes": {"friendly_name": "Sala", "brightness": 180},
            },
        )
    )
    ctx = make_ctx(session=make_session([[_fila_cuenta()]]), vault=make_vault(bundle=_bundle()))

    resultado = await CasaEstadoTool().run(ctx, {"entity_id": "light.sala"})

    assert "Sala" in resultado.content
    assert "brightness" in resultado.content
    assert resultado.data["estado"]["state"] == "on"


@respx.mock
async def test_casa_estado_entidad_inexistente(make_ctx, make_session, make_vault):
    respx.get(f"{BASE_URL}/api/states/light.no_existe").mock(return_value=httpx.Response(404))
    ctx = make_ctx(session=make_session([[_fila_cuenta()]]), vault=make_vault(bundle=_bundle()))

    resultado = await CasaEstadoTool().run(ctx, {"entity_id": "light.no_existe"})

    assert "No existe la entidad" in resultado.content


@respx.mock
async def test_casa_controlar_encender_luz_feliz(make_ctx, make_session, make_vault):
    ruta = respx.post(f"{BASE_URL}/api/services/homeassistant/turn_on").mock(
        return_value=httpx.Response(200, json=[{"entity_id": "light.sala", "state": "on"}])
    )
    ctx = make_ctx(session=make_session([[_fila_cuenta()]]), vault=make_vault(bundle=_bundle()))

    resultado = await CasaControlarTool().run(
        ctx, {"entity_id": "light.sala", "accion": "encender"}
    )

    assert "light.sala" in resultado.content
    assert resultado.data == {
        "entity_id": "light.sala",
        "domain": "homeassistant",
        "service": "turn_on",
        "parametros": {"entity_id": "light.sala"},
    }
    enviado = json.loads(ruta.calls.last.request.content)
    assert enviado == {"entity_id": "light.sala"}


@respx.mock
async def test_casa_controlar_apagar_mapea_a_turn_off(make_ctx, make_session, make_vault):
    respx.post(f"{BASE_URL}/api/services/homeassistant/turn_off").mock(
        return_value=httpx.Response(200, json=[])
    )
    ctx = make_ctx(session=make_session([[_fila_cuenta()]]), vault=make_vault(bundle=_bundle()))

    resultado = await CasaControlarTool().run(
        ctx, {"entity_id": "switch.enchufe", "accion": "apagar"}
    )

    assert resultado.data["service"] == "turn_off"


@respx.mock
async def test_casa_controlar_alternar_mapea_a_toggle(make_ctx, make_session, make_vault):
    respx.post(f"{BASE_URL}/api/services/homeassistant/toggle").mock(
        return_value=httpx.Response(200, json=[])
    )
    ctx = make_ctx(session=make_session([[_fila_cuenta()]]), vault=make_vault(bundle=_bundle()))

    resultado = await CasaControlarTool().run(
        ctx, {"entity_id": "switch.enchufe", "accion": "alternar"}
    )

    assert resultado.data["service"] == "toggle"


@respx.mock
async def test_casa_controlar_accion_explicita_con_parametros(make_ctx, make_session, make_vault):
    ruta = respx.post(f"{BASE_URL}/api/services/climate/set_temperature").mock(
        return_value=httpx.Response(200, json=[])
    )
    ctx = make_ctx(session=make_session([[_fila_cuenta()]]), vault=make_vault(bundle=_bundle()))

    resultado = await CasaControlarTool().run(
        ctx,
        {
            "entity_id": "climate.termostato",
            "accion": "climate.set_temperature",
            "parametros": {"temperature": 22},
        },
    )

    assert "climate.set_temperature" in resultado.content
    enviado = json.loads(ruta.calls.last.request.content)
    assert enviado == {"temperature": 22, "entity_id": "climate.termostato"}


@respx.mock
async def test_casa_controlar_error_de_home_assistant_se_devuelve_como_toolresult(
    make_ctx, make_session, make_vault
):
    respx.post(f"{BASE_URL}/api/services/homeassistant/turn_on").mock(
        return_value=httpx.Response(500, text="boom")
    )
    ctx = make_ctx(session=make_session([[_fila_cuenta()]]), vault=make_vault(bundle=_bundle()))

    resultado = await CasaControlarTool().run(
        ctx, {"entity_id": "light.sala", "accion": "encender"}
    )

    assert "500" in resultado.content


@respx.mock
async def test_casa_controlar_usa_timeout_de_settings(make_ctx, make_session, make_vault):
    """`HOMEASSISTANT_TIMEOUT_SECONDS` de `ctx.settings` se respeta (leído
    con `getattr`, nunca revienta si falta — ver docstring de `tools.py`)."""
    respx.post(f"{BASE_URL}/api/services/homeassistant/turn_on").mock(
        return_value=httpx.Response(200, json=[])
    )
    ctx = make_ctx(
        session=make_session([[_fila_cuenta()]]),
        vault=make_vault(bundle=_bundle()),
        settings=SimpleNamespace(HOMEASSISTANT_TIMEOUT_SECONDS=5),
    )

    resultado = await CasaControlarTool().run(
        ctx, {"entity_id": "light.sala", "accion": "encender"}
    )

    assert resultado.data["service"] == "turn_on"
