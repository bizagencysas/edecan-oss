"""Tests de `edecan_vehicles.tools`: `VehiculoEstadoTool`, `VehiculoControlarTool`,
`get_all_tools` (`ARCHITECTURE.md` §10.15). Sin red real (`respx`), sin
importar `edecan_db`/`edecan_schemas` — fakes locales de `conftest.py`.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import respx
from edecan_vehicles.providers import SMARTCAR_API_BASE, STUB_VEHICLE_ID
from edecan_vehicles.tools import (
    VehiculoControlarTool,
    VehiculoEstadoTool,
    get_all_tools,
)

CLIENT_ID = "client-id"
CLIENT_SECRET = "client-secret"
REFRESH_TOKEN = "refresh-token"
VEHICLE_ID = "vehiculo-real-1"


def _bundle_valido() -> SimpleNamespace:
    return SimpleNamespace(
        access_token=json.dumps(
            {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "refresh_token": REFRESH_TOKEN}
        )
    )


def _mock_refresh() -> None:
    respx.post("https://auth.smartcar.com/oauth/token").mock(
        return_value=httpx.Response(
            200, json={"access_token": "tok", "token_type": "Bearer", "expires_in": 7200}
        )
    )


# ---------------------------------------------------------------------------
# Metadatos de las tools
# ---------------------------------------------------------------------------


def test_vehiculo_estado_no_es_dangerous_y_exige_flag():
    tool = VehiculoEstadoTool()
    assert tool.dangerous is False
    assert tool.requires_flags == frozenset({"tools.vehicles"})
    assert tool.name == "vehiculo_estado"


def test_vehiculo_controlar_es_dangerous_y_exige_flag():
    tool = VehiculoControlarTool()
    assert tool.dangerous is True
    assert tool.requires_flags == frozenset({"tools.vehicles"})
    assert tool.name == "vehiculo_controlar"


def test_get_all_tools_devuelve_las_dos():
    tools = get_all_tools()
    nombres = {t.name for t in tools}
    assert nombres == {"vehiculo_estado", "vehiculo_controlar"}


# ---------------------------------------------------------------------------
# vehiculo_estado — modo demo por defecto (ctx sin credencial conectada)
# ---------------------------------------------------------------------------


async def test_vehiculo_estado_sin_vehicle_id_lista_en_modo_demo(make_ctx):
    resultado = await VehiculoEstadoTool().run(make_ctx(), {})
    assert STUB_VEHICLE_ID in resultado.content
    assert "modo demo" in resultado.content
    assert resultado.data["vehiculos"][0]["id"] == STUB_VEHICLE_ID


async def test_vehiculo_estado_con_vehicle_id_demo_da_detalle(make_ctx):
    resultado = await VehiculoEstadoTool().run(make_ctx(), {"vehicle_id": STUB_VEHICLE_ID})
    assert "Combustible" in resultado.content
    assert "modo demo" in resultado.content
    assert resultado.data["estado"]["combustible"]["porcentaje"] == 72.0


async def test_vehiculo_estado_vehicle_id_desconocido_en_demo_da_error_claro(make_ctx):
    resultado = await VehiculoEstadoTool().run(make_ctx(), {"vehicle_id": "no-existe"})
    assert "no-existe" in resultado.content


# ---------------------------------------------------------------------------
# vehiculo_controlar — validación de argumentos (nunca llega al proveedor)
# ---------------------------------------------------------------------------


async def test_vehiculo_controlar_sin_vehicle_id(make_ctx):
    resultado = await VehiculoControlarTool().run(make_ctx(), {"accion": "bloquear"})
    assert "id del vehículo" in resultado.content


async def test_vehiculo_controlar_sin_accion(make_ctx):
    resultado = await VehiculoControlarTool().run(make_ctx(), {"vehicle_id": STUB_VEHICLE_ID})
    assert "bloquear" in resultado.content or "acci" in resultado.content.lower()


async def test_vehiculo_controlar_accion_invalida_no_resuelve_proveedor(make_ctx):
    ctx = make_ctx()
    resultado = await VehiculoControlarTool().run(
        ctx, {"vehicle_id": STUB_VEHICLE_ID, "accion": "arrancar"}
    )
    assert "arrancar" in resultado.content
    assert ctx.session.llamadas == []  # nunca llegó a resolver credenciales


# ---------------------------------------------------------------------------
# vehiculo_controlar — modo demo
# ---------------------------------------------------------------------------


async def test_vehiculo_controlar_bloquear_modo_demo(make_ctx):
    resultado = await VehiculoControlarTool().run(
        make_ctx(), {"vehicle_id": STUB_VEHICLE_ID, "accion": "bloquear"}
    )
    assert "Bloqueé" in resultado.content
    assert "modo demo" in resultado.content
    assert resultado.data["status"] == "ok"


async def test_vehiculo_controlar_desbloquear_modo_demo(make_ctx):
    resultado = await VehiculoControlarTool().run(
        make_ctx(), {"vehicle_id": STUB_VEHICLE_ID, "accion": "DESBLOQUEAR"}
    )
    assert "Desbloqueé" in resultado.content


# ---------------------------------------------------------------------------
# Con credencial de Smartcar real conectada (respx) — sin aviso de demo
# ---------------------------------------------------------------------------


def _fila_cuenta() -> dict:
    return {"id": "acc-1"}


def _ctx_con_smartcar_conectado(make_ctx, make_session, make_vault):
    return make_ctx(
        session=make_session([[_fila_cuenta()]]), vault=make_vault(bundle=_bundle_valido())
    )


@respx.mock
async def test_vehiculo_estado_lista_con_smartcar_real_sin_aviso_demo(
    make_ctx, make_session, make_vault
):
    _mock_refresh()
    respx.get(f"{SMARTCAR_API_BASE}/vehicles").mock(
        return_value=httpx.Response(200, json={"vehicles": [VEHICLE_ID]})
    )
    respx.get(f"{SMARTCAR_API_BASE}/vehicles/{VEHICLE_ID}").mock(
        return_value=httpx.Response(
            200, json={"id": VEHICLE_ID, "make": "TESLA", "model": "Model Y", "year": 2024}
        )
    )
    ctx = _ctx_con_smartcar_conectado(make_ctx, make_session, make_vault)

    resultado = await VehiculoEstadoTool().run(ctx, {})

    assert "modo demo" not in resultado.content
    assert VEHICLE_ID in resultado.content
    assert resultado.data["vehiculos"] == [
        {"id": VEHICLE_ID, "marca": "TESLA", "modelo": "Model Y", "anio": 2024}
    ]


@respx.mock
async def test_vehiculo_estado_detalle_con_smartcar_real(make_ctx, make_session, make_vault):
    _mock_refresh()
    respx.get(f"{SMARTCAR_API_BASE}/vehicles/{VEHICLE_ID}/battery").mock(
        return_value=httpx.Response(200, json={"percentRemaining": 0.9, "range": 300.0})
    )
    respx.get(f"{SMARTCAR_API_BASE}/vehicles/{VEHICLE_ID}/fuel").mock(
        return_value=httpx.Response(501)
    )
    respx.get(f"{SMARTCAR_API_BASE}/vehicles/{VEHICLE_ID}/odometer").mock(
        return_value=httpx.Response(200, json={"distance": 5000.0})
    )
    respx.get(f"{SMARTCAR_API_BASE}/vehicles/{VEHICLE_ID}/location").mock(
        return_value=httpx.Response(403)
    )
    ctx = _ctx_con_smartcar_conectado(make_ctx, make_session, make_vault)

    resultado = await VehiculoEstadoTool().run(ctx, {"vehicle_id": VEHICLE_ID})

    assert "Batería 90%" in resultado.content
    assert "autonomía ~300 km" in resultado.content
    assert "modo demo" not in resultado.content
    assert resultado.data["estado"]["combustible"] is None


@respx.mock
async def test_vehiculo_controlar_bloquear_con_smartcar_real(make_ctx, make_session, make_vault):
    _mock_refresh()
    ruta = respx.post(f"{SMARTCAR_API_BASE}/vehicles/{VEHICLE_ID}/security").mock(
        return_value=httpx.Response(200, json={"status": "success"})
    )
    ctx = _ctx_con_smartcar_conectado(make_ctx, make_session, make_vault)

    resultado = await VehiculoControlarTool().run(
        ctx, {"vehicle_id": VEHICLE_ID, "accion": "bloquear"}
    )

    assert "Bloqueé" in resultado.content
    assert "modo demo" not in resultado.content
    enviado = json.loads(ruta.calls.last.request.content)
    assert enviado == {"action": "LOCK"}


@respx.mock
async def test_vehiculo_controlar_error_de_smartcar_se_devuelve_como_toolresult(
    make_ctx, make_session, make_vault
):
    _mock_refresh()
    respx.post(f"{SMARTCAR_API_BASE}/vehicles/{VEHICLE_ID}/security").mock(
        return_value=httpx.Response(409, text="vehicle is asleep")
    )
    ctx = _ctx_con_smartcar_conectado(make_ctx, make_session, make_vault)

    resultado = await VehiculoControlarTool().run(
        ctx, {"vehicle_id": VEHICLE_ID, "accion": "desbloquear"}
    )

    assert "409" in resultado.content
