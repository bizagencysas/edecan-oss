"""Tests de `edecan_vehicles.providers`: `StubVehiclesProvider`, `SmartcarProvider`
(refresh de token, rotación de `refresh_token` persistida, `estado()` con
capabilities parciales, LOCK/UNLOCK) y `get_tenant_vehicle_provider`
("tenant → stub"). Sin red real (`respx`), sin importar `edecan_db`/
`edecan_schemas` — fakes locales de `conftest.py` (`ARCHITECTURE.md` §10.15).
"""

from __future__ import annotations

import json

import httpx
import respx
from edecan_vehicles.providers import (
    SMARTCAR_API_BASE,
    SMARTCAR_AUTH_URL,
    STUB_VEHICLE_ID,
    VEHICLES_CONNECTOR_KEY,
    SmartcarProvider,
    StubVehiclesProvider,
    VehicleProviderError,
    get_tenant_vehicle_provider,
)

CLIENT_ID = "client-id-de-prueba"
CLIENT_SECRET = "client-secret-de-prueba"
REFRESH_TOKEN = "refresh-token-inicial"
VEHICLE_ID = "11111111-2222-3333-4444-555555555555"


def _mock_refresh(
    *,
    access_token: str = "access-token-1",
    nuevo_refresh_token: str | None = None,
    expires_in: int = 7200,
) -> respx.Route:
    payload = {"access_token": access_token, "token_type": "Bearer", "expires_in": expires_in}
    if nuevo_refresh_token is not None:
        payload["refresh_token"] = nuevo_refresh_token
    return respx.post(SMARTCAR_AUTH_URL).mock(return_value=httpx.Response(200, json=payload))


# ---------------------------------------------------------------------------
# StubVehiclesProvider
# ---------------------------------------------------------------------------


async def test_stub_list_vehicles_devuelve_un_vehiculo_demo():
    vehiculos = await StubVehiclesProvider().list_vehicles()
    assert len(vehiculos) == 1
    assert vehiculos[0]["id"] == STUB_VEHICLE_ID
    assert vehiculos[0]["marca"] and vehiculos[0]["modelo"] and vehiculos[0]["anio"]


async def test_stub_estado_del_vehiculo_demo():
    estado = await StubVehiclesProvider().estado(STUB_VEHICLE_ID)
    assert estado["combustible"]["porcentaje"] == 72.0
    assert estado["ubicacion"] == {"lat": 19.4326, "lon": -99.1332}


async def test_stub_estado_vehiculo_desconocido_lanza():
    try:
        await StubVehiclesProvider().estado("no-existe")
        raise AssertionError("debía lanzar VehicleProviderError")
    except VehicleProviderError as exc:
        assert "no-existe" in str(exc)


async def test_stub_controlar_puertas_bloquear():
    resultado = await StubVehiclesProvider().controlar_puertas(STUB_VEHICLE_ID, "bloquear")
    assert resultado == {
        "vehicle_id": STUB_VEHICLE_ID,
        "accion": "bloquear",
        "status": "ok",
        "demo": True,
    }


async def test_stub_controlar_puertas_accion_invalida_lanza():
    try:
        await StubVehiclesProvider().controlar_puertas(STUB_VEHICLE_ID, "arrancar")
        raise AssertionError("debía lanzar VehicleProviderError")
    except VehicleProviderError as exc:
        assert "arrancar" in str(exc)


async def test_stub_controlar_puertas_vehiculo_desconocido_lanza():
    try:
        await StubVehiclesProvider().controlar_puertas("otro-id", "bloquear")
        raise AssertionError("debía lanzar VehicleProviderError")
    except VehicleProviderError:
        pass


# ---------------------------------------------------------------------------
# SmartcarProvider — refresh de token
# ---------------------------------------------------------------------------


@respx.mock
async def test_smartcar_refresca_token_y_manda_bearer_en_llamadas():
    _mock_refresh(access_token="token-fresco")
    ruta_vehicles = respx.get(f"{SMARTCAR_API_BASE}/vehicles").mock(
        return_value=httpx.Response(200, json={"vehicles": []})
    )
    provider = SmartcarProvider(
        client_id=CLIENT_ID, client_secret=CLIENT_SECRET, refresh_token=REFRESH_TOKEN
    )

    resultado = await provider.list_vehicles()

    assert resultado == []
    assert ruta_vehicles.calls.last.request.headers["Authorization"] == "Bearer token-fresco"


@respx.mock
async def test_smartcar_refresh_usa_basic_auth_con_client_id_secret():
    ruta_refresh = _mock_refresh()
    respx.get(f"{SMARTCAR_API_BASE}/vehicles").mock(
        return_value=httpx.Response(200, json={"vehicles": []})
    )
    provider = SmartcarProvider(
        client_id=CLIENT_ID, client_secret=CLIENT_SECRET, refresh_token=REFRESH_TOKEN
    )

    await provider.list_vehicles()

    enviado = ruta_refresh.calls.last.request
    assert enviado.headers["Authorization"].startswith("Basic ")
    cuerpo = enviado.content.decode()
    assert "grant_type=refresh_token" in cuerpo
    assert f"refresh_token={REFRESH_TOKEN}" in cuerpo


@respx.mock
async def test_smartcar_token_se_cachea_no_refresca_dos_veces():
    ruta_refresh = _mock_refresh()
    respx.get(f"{SMARTCAR_API_BASE}/vehicles").mock(
        return_value=httpx.Response(200, json={"vehicles": []})
    )
    provider = SmartcarProvider(
        client_id=CLIENT_ID, client_secret=CLIENT_SECRET, refresh_token=REFRESH_TOKEN
    )

    await provider.list_vehicles()
    await provider.list_vehicles()

    assert ruta_refresh.call_count == 1


@respx.mock
async def test_smartcar_token_expirado_refresca_de_nuevo(monkeypatch):
    import edecan_vehicles.providers as providers_module

    ruta_refresh = _mock_refresh()
    respx.get(f"{SMARTCAR_API_BASE}/vehicles").mock(
        return_value=httpx.Response(200, json={"vehicles": []})
    )
    provider = SmartcarProvider(
        client_id=CLIENT_ID, client_secret=CLIENT_SECRET, refresh_token=REFRESH_TOKEN
    )
    await provider.list_vehicles()
    assert ruta_refresh.call_count == 1

    reloj = {"t": 0.0}
    monkeypatch.setattr(providers_module.time, "monotonic", lambda: reloj["t"])
    provider._access_token_expires_at = 0.0  # forzar expiración sin esperar de verdad

    await provider.list_vehicles()
    assert ruta_refresh.call_count == 2


@respx.mock
async def test_smartcar_refresh_rechazado_401_lanza_error_claro():
    respx.post(SMARTCAR_AUTH_URL).mock(return_value=httpx.Response(401, text="invalid_grant"))
    provider = SmartcarProvider(
        client_id=CLIENT_ID, client_secret=CLIENT_SECRET, refresh_token=REFRESH_TOKEN
    )
    try:
        await provider.list_vehicles()
        raise AssertionError("debía lanzar VehicleProviderError")
    except VehicleProviderError as exc:
        assert "rechazó" in str(exc).lower() or "credenciales" in str(exc).lower()


@respx.mock
async def test_smartcar_refresh_red_caida_lanza_error_claro():
    respx.post(SMARTCAR_AUTH_URL).mock(side_effect=httpx.ConnectError("connection refused"))
    provider = SmartcarProvider(
        client_id=CLIENT_ID, client_secret=CLIENT_SECRET, refresh_token=REFRESH_TOKEN
    )
    try:
        await provider.list_vehicles()
        raise AssertionError("debía lanzar VehicleProviderError")
    except VehicleProviderError as exc:
        assert "conectar" in str(exc).lower()


def test_smartcar_provider_exige_las_tres_credenciales():
    for kwargs in (
        {"client_id": "", "client_secret": CLIENT_SECRET, "refresh_token": REFRESH_TOKEN},
        {"client_id": CLIENT_ID, "client_secret": "", "refresh_token": REFRESH_TOKEN},
        {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "refresh_token": ""},
    ):
        try:
            SmartcarProvider(**kwargs)
            raise AssertionError("debía lanzar VehicleProviderError")
        except VehicleProviderError:
            pass


# ---------------------------------------------------------------------------
# SmartcarProvider — rotación de refresh_token
# ---------------------------------------------------------------------------


@respx.mock
async def test_smartcar_rotacion_de_refresh_token_llama_al_callback():
    _mock_refresh(nuevo_refresh_token="refresh-token-NUEVO")
    respx.get(f"{SMARTCAR_API_BASE}/vehicles").mock(
        return_value=httpx.Response(200, json={"vehicles": []})
    )
    llamadas: list[str] = []

    async def on_refresh_token(nuevo: str) -> None:
        llamadas.append(nuevo)

    provider = SmartcarProvider(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        refresh_token=REFRESH_TOKEN,
        on_refresh_token=on_refresh_token,
    )

    await provider.list_vehicles()

    assert llamadas == ["refresh-token-NUEVO"]
    assert provider.refresh_token == "refresh-token-NUEVO"


@respx.mock
async def test_smartcar_sin_rotacion_no_llama_al_callback():
    """Si Smartcar no manda `refresh_token` en la respuesta (o manda el mismo),
    el callback NO se invoca — solo importa persistir cuando de verdad cambió."""
    _mock_refresh()  # sin `nuevo_refresh_token` -> la respuesta no trae ese campo
    respx.get(f"{SMARTCAR_API_BASE}/vehicles").mock(
        return_value=httpx.Response(200, json={"vehicles": []})
    )
    llamadas: list[str] = []

    async def on_refresh_token(nuevo: str) -> None:
        llamadas.append(nuevo)

    provider = SmartcarProvider(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        refresh_token=REFRESH_TOKEN,
        on_refresh_token=on_refresh_token,
    )

    await provider.list_vehicles()

    assert llamadas == []
    assert provider.refresh_token == REFRESH_TOKEN


# ---------------------------------------------------------------------------
# SmartcarProvider — list_vehicles
# ---------------------------------------------------------------------------


@respx.mock
async def test_smartcar_list_vehicles_feliz_con_dos_autos():
    _mock_refresh()
    respx.get(f"{SMARTCAR_API_BASE}/vehicles").mock(
        return_value=httpx.Response(200, json={"vehicles": ["id-1", "id-2"]})
    )
    respx.get(f"{SMARTCAR_API_BASE}/vehicles/id-1").mock(
        return_value=httpx.Response(
            200, json={"id": "id-1", "make": "TESLA", "model": "Model 3", "year": 2023}
        )
    )
    respx.get(f"{SMARTCAR_API_BASE}/vehicles/id-2").mock(
        return_value=httpx.Response(
            200, json={"id": "id-2", "make": "FORD", "model": "F-150", "year": 2021}
        )
    )
    provider = SmartcarProvider(
        client_id=CLIENT_ID, client_secret=CLIENT_SECRET, refresh_token=REFRESH_TOKEN
    )

    vehiculos = await provider.list_vehicles()

    assert vehiculos == [
        {"id": "id-1", "marca": "TESLA", "modelo": "Model 3", "anio": 2023},
        {"id": "id-2", "marca": "FORD", "modelo": "F-150", "anio": 2021},
    ]


@respx.mock
async def test_smartcar_list_vehicles_sin_vehiculos():
    _mock_refresh()
    respx.get(f"{SMARTCAR_API_BASE}/vehicles").mock(
        return_value=httpx.Response(200, json={"vehicles": []})
    )
    provider = SmartcarProvider(
        client_id=CLIENT_ID, client_secret=CLIENT_SECRET, refresh_token=REFRESH_TOKEN
    )
    assert await provider.list_vehicles() == []


@respx.mock
async def test_smartcar_list_vehicles_info_no_disponible_deja_campos_none():
    """`GET /vehicles/{id}` cae en un status "capability no disponible" (poco
    usual para este endpoint, pero el código lo tolera igual) — el vehículo
    SIGUE apareciendo en la lista, solo con marca/modelo/año en `None`."""
    _mock_refresh()
    respx.get(f"{SMARTCAR_API_BASE}/vehicles").mock(
        return_value=httpx.Response(200, json={"vehicles": ["id-1"]})
    )
    respx.get(f"{SMARTCAR_API_BASE}/vehicles/id-1").mock(return_value=httpx.Response(403))
    provider = SmartcarProvider(
        client_id=CLIENT_ID, client_secret=CLIENT_SECRET, refresh_token=REFRESH_TOKEN
    )

    vehiculos = await provider.list_vehicles()

    assert vehiculos == [{"id": "id-1", "marca": None, "modelo": None, "anio": None}]


# ---------------------------------------------------------------------------
# SmartcarProvider — estado() con capabilities parciales
# ---------------------------------------------------------------------------


@respx.mock
async def test_smartcar_estado_todas_las_capabilities_disponibles():
    _mock_refresh()
    respx.get(f"{SMARTCAR_API_BASE}/vehicles/{VEHICLE_ID}/battery").mock(
        return_value=httpx.Response(200, json={"percentRemaining": 0.82, "range": 320.5})
    )
    respx.get(f"{SMARTCAR_API_BASE}/vehicles/{VEHICLE_ID}/fuel").mock(
        return_value=httpx.Response(200, json={"percentRemaining": 0.4, "range": 180.0})
    )
    respx.get(f"{SMARTCAR_API_BASE}/vehicles/{VEHICLE_ID}/odometer").mock(
        return_value=httpx.Response(200, json={"distance": 12345.6})
    )
    respx.get(f"{SMARTCAR_API_BASE}/vehicles/{VEHICLE_ID}/location").mock(
        return_value=httpx.Response(200, json={"latitude": 37.4292, "longitude": -122.1381})
    )
    provider = SmartcarProvider(
        client_id=CLIENT_ID, client_secret=CLIENT_SECRET, refresh_token=REFRESH_TOKEN
    )

    estado = await provider.estado(VEHICLE_ID)

    assert estado["bateria"] == {"porcentaje": 82.0, "autonomia_km": 320.5}
    assert estado["combustible"] == {"porcentaje": 40.0, "autonomia_km": 180.0}
    assert estado["odometro"] == 12345.6
    assert estado["ubicacion"] == {"lat": 37.4292, "lon": -122.1381}


@respx.mock
async def test_smartcar_estado_capabilities_parciales_501_403_409_no_disponible():
    """Ejemplo realista: auto eléctrico sin `fuel` (501, "no soporta esta
    capability"), sin permiso de `location` en el scope autorizado (403), y
    dormido para `odometer` (409 VEHICLE_STATE) — ninguno de los tres tumba
    la llamada; cada campo cae a `None` y `battery` sigue funcionando."""
    _mock_refresh()
    respx.get(f"{SMARTCAR_API_BASE}/vehicles/{VEHICLE_ID}/battery").mock(
        return_value=httpx.Response(200, json={"percentRemaining": 0.55})
    )
    respx.get(f"{SMARTCAR_API_BASE}/vehicles/{VEHICLE_ID}/fuel").mock(
        return_value=httpx.Response(501, json={"error": "not_capable"})
    )
    respx.get(f"{SMARTCAR_API_BASE}/vehicles/{VEHICLE_ID}/odometer").mock(
        return_value=httpx.Response(409, json={"error": "vehicle_state_error"})
    )
    respx.get(f"{SMARTCAR_API_BASE}/vehicles/{VEHICLE_ID}/location").mock(
        return_value=httpx.Response(403, json={"error": "permission_denied"})
    )
    provider = SmartcarProvider(
        client_id=CLIENT_ID, client_secret=CLIENT_SECRET, refresh_token=REFRESH_TOKEN
    )

    estado = await provider.estado(VEHICLE_ID)

    assert estado == {
        "bateria": {"porcentaje": 55.0},
        "combustible": None,
        "odometro": None,
        "ubicacion": None,
    }


@respx.mock
async def test_smartcar_estado_401_en_una_capability_lanza_error_claro():
    """401 SÍ es un problema real (token inválido) — a diferencia de
    403/404/409/501, no se trata como "capability no disponible"."""
    _mock_refresh()
    respx.get(f"{SMARTCAR_API_BASE}/vehicles/{VEHICLE_ID}/battery").mock(
        return_value=httpx.Response(401)
    )
    provider = SmartcarProvider(
        client_id=CLIENT_ID, client_secret=CLIENT_SECRET, refresh_token=REFRESH_TOKEN
    )
    try:
        await provider.estado(VEHICLE_ID)
        raise AssertionError("debía lanzar VehicleProviderError")
    except VehicleProviderError as exc:
        assert "401" in str(exc) or "rechazó" in str(exc).lower()


# ---------------------------------------------------------------------------
# SmartcarProvider — controlar_puertas (LOCK/UNLOCK)
# ---------------------------------------------------------------------------


@respx.mock
async def test_smartcar_controlar_puertas_bloquear_manda_lock():
    _mock_refresh()
    ruta = respx.post(f"{SMARTCAR_API_BASE}/vehicles/{VEHICLE_ID}/security").mock(
        return_value=httpx.Response(200, json={"status": "success"})
    )
    provider = SmartcarProvider(
        client_id=CLIENT_ID, client_secret=CLIENT_SECRET, refresh_token=REFRESH_TOKEN
    )

    resultado = await provider.controlar_puertas(VEHICLE_ID, "bloquear")

    assert resultado == {"vehicle_id": VEHICLE_ID, "accion": "bloquear", "status": "ok"}
    enviado = json.loads(ruta.calls.last.request.content)
    assert enviado == {"action": "LOCK"}


@respx.mock
async def test_smartcar_controlar_puertas_desbloquear_manda_unlock():
    _mock_refresh()
    ruta = respx.post(f"{SMARTCAR_API_BASE}/vehicles/{VEHICLE_ID}/security").mock(
        return_value=httpx.Response(200, json={"status": "success"})
    )
    provider = SmartcarProvider(
        client_id=CLIENT_ID, client_secret=CLIENT_SECRET, refresh_token=REFRESH_TOKEN
    )

    resultado = await provider.controlar_puertas(VEHICLE_ID, "desbloquear")

    assert resultado["accion"] == "desbloquear"
    enviado = json.loads(ruta.calls.last.request.content)
    assert enviado == {"action": "UNLOCK"}


@respx.mock
async def test_smartcar_controlar_puertas_accion_invalida_no_llega_a_la_red():
    provider = SmartcarProvider(
        client_id=CLIENT_ID, client_secret=CLIENT_SECRET, refresh_token=REFRESH_TOKEN
    )
    try:
        await provider.controlar_puertas(VEHICLE_ID, "arrancar")
        raise AssertionError("debía lanzar VehicleProviderError")
    except VehicleProviderError as exc:
        assert "arrancar" in str(exc)


@respx.mock
async def test_smartcar_controlar_puertas_error_de_smartcar_se_propaga():
    _mock_refresh()
    respx.post(f"{SMARTCAR_API_BASE}/vehicles/{VEHICLE_ID}/security").mock(
        return_value=httpx.Response(409, text="vehicle is asleep")
    )
    provider = SmartcarProvider(
        client_id=CLIENT_ID, client_secret=CLIENT_SECRET, refresh_token=REFRESH_TOKEN
    )
    try:
        await provider.controlar_puertas(VEHICLE_ID, "bloquear")
        raise AssertionError("debía lanzar VehicleProviderError")
    except VehicleProviderError as exc:
        assert "409" in str(exc)


# ---------------------------------------------------------------------------
# get_tenant_vehicle_provider — "tenant → stub"
# ---------------------------------------------------------------------------


def _fila_cuenta(cuenta_id: str = "acc-1") -> dict:
    return {"id": cuenta_id}


def _bundle_valido() -> object:
    from types import SimpleNamespace

    return SimpleNamespace(
        access_token=json.dumps(
            {
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "refresh_token": REFRESH_TOKEN,
            }
        )
    )


async def test_get_tenant_vehicle_provider_sin_vault_cae_a_stub(make_ctx):
    ctx = make_ctx(vault=None)
    # `make_ctx` por defecto YA rellena `vault` con un `FakeVault()` vacío —
    # forzamos explícitamente `None` para probar la rama "falta ctx.vault".
    ctx.vault = None
    provider = await get_tenant_vehicle_provider(ctx)
    assert isinstance(provider, StubVehiclesProvider)


async def test_get_tenant_vehicle_provider_sin_cuenta_conectada_cae_a_stub(
    make_ctx, make_session, make_vault
):
    ctx = make_ctx(session=make_session([[]]), vault=make_vault())
    provider = await get_tenant_vehicle_provider(ctx)
    assert isinstance(provider, StubVehiclesProvider)
    # La consulta sí filtró por el connector_key correcto.
    assert ctx.session.llamadas[0][1]["connector_key"] == VEHICLES_CONNECTOR_KEY


async def test_get_tenant_vehicle_provider_bundle_vacio_cae_a_stub(
    make_ctx, make_session, make_vault
):
    ctx = make_ctx(session=make_session([[_fila_cuenta()]]), vault=make_vault(bundle=None))
    provider = await get_tenant_vehicle_provider(ctx)
    assert isinstance(provider, StubVehiclesProvider)


async def test_get_tenant_vehicle_provider_json_corrupto_cae_a_stub(
    make_ctx, make_session, make_vault
):
    from types import SimpleNamespace

    bundle_corrupto = SimpleNamespace(access_token="no es json{{{")
    ctx = make_ctx(
        session=make_session([[_fila_cuenta()]]), vault=make_vault(bundle=bundle_corrupto)
    )
    provider = await get_tenant_vehicle_provider(ctx)
    assert isinstance(provider, StubVehiclesProvider)


async def test_get_tenant_vehicle_provider_faltan_campos_cae_a_stub(
    make_ctx, make_session, make_vault
):
    from types import SimpleNamespace

    bundle_incompleto = SimpleNamespace(access_token=json.dumps({"client_id": CLIENT_ID}))
    ctx = make_ctx(
        session=make_session([[_fila_cuenta()]]), vault=make_vault(bundle=bundle_incompleto)
    )
    provider = await get_tenant_vehicle_provider(ctx)
    assert isinstance(provider, StubVehiclesProvider)


async def test_get_tenant_vehicle_provider_vault_revienta_cae_a_stub(
    make_ctx, make_session, caplog
):
    class _VaultQueRevienta:
        async def get(self, tenant_id, connector_account_id):
            raise RuntimeError("vault caído")

    ctx = make_ctx(session=make_session([[_fila_cuenta()]]), vault=_VaultQueRevienta())
    with caplog.at_level("WARNING"):
        provider = await get_tenant_vehicle_provider(ctx)
    assert isinstance(provider, StubVehiclesProvider)
    texto = caplog.text.lower()
    assert "vehiculos" in texto or "smartcar" in texto or "vehicle" in texto


async def test_get_tenant_vehicle_provider_usa_credencial_del_tenant(
    make_ctx, make_session, make_vault
):
    cuenta_id = "acc-42"
    ctx = make_ctx(
        session=make_session([[_fila_cuenta(cuenta_id)]]), vault=make_vault(bundle=_bundle_valido())
    )

    provider = await get_tenant_vehicle_provider(ctx)

    assert isinstance(provider, SmartcarProvider)
    assert ctx.vault.llamadas_get == [(ctx.tenant_id, cuenta_id)]


@respx.mock
async def test_get_tenant_vehicle_provider_persiste_rotacion_en_el_vault(
    make_ctx, make_session, make_vault
):
    """Integración end-to-end: el proveedor que arma `get_tenant_vehicle_provider`
    debe persistir el `refresh_token` rotado de vuelta en `ctx.vault`, con el
    MISMO `client_id`/`client_secret` y el `connector_account_id` correcto."""
    _mock_refresh(nuevo_refresh_token="refresh-ROTADO")
    respx.get(f"{SMARTCAR_API_BASE}/vehicles").mock(
        return_value=httpx.Response(200, json={"vehicles": []})
    )
    cuenta_id = "acc-77"
    vault = make_vault(bundle=_bundle_valido())
    ctx = make_ctx(session=make_session([[_fila_cuenta(cuenta_id)]]), vault=vault)

    provider = await get_tenant_vehicle_provider(ctx)
    assert isinstance(provider, SmartcarProvider)
    await provider.list_vehicles()

    assert len(vault.puts) == 1
    tenant_id_guardado, account_id_guardado, bundle_guardado = vault.puts[0]
    assert tenant_id_guardado == ctx.tenant_id
    assert account_id_guardado == cuenta_id
    data_guardada = json.loads(bundle_guardado.access_token)
    assert data_guardada == {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": "refresh-ROTADO",
    }
    assert bundle_guardado.scopes == ["smartcar"]
