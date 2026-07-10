"""`/v1/vehiculos/*` — conector Smartcar, bring-your-own (WP-V4-08,
`ARCHITECTURE.md` §13, `apps/api/edecan_api/routers/vehiculos.py`,
`docs/vehiculos.md`).

Mismas convenciones que `test_smarthome_router.py`/`test_credentials_router.py`:
`client`/`app`/`fake_repo`/`auth_headers` de `conftest.py`, `FakeVault` local
con `put`/`get` en memoria (no se toca `conftest.py`). Cada test lleva
`@respx.mock` (incluso los que no esperan tráfico real, p. ej.
`validate=false`): con `assert_all_mocked=True` de fábrica, cualquier llamada
HTTP no interceptada explícitamente hace fallar el test en vez de pegarle a
la red real.

Modelo de precio de pago único (2026-07-09, `edecan_schemas.plans`
docstring): `edecan_schemas.plans.FLAG_TOOLS_VEHICLES` ("tools.vehicles") ya
está en `True` en las 4 entradas de `PLANES` por igual — no hay más "flag
apagado" que probar. Los tests usan el default de `_headers()`
(`plan_key="hosted_pro"`), sin necesitar `monkeypatch`.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest
import respx
from conftest import auth_headers
from edecan_schemas import TokenBundle

import edecan_api.deps as edecan_deps
from edecan_api.routers import vehiculos

_PLAN_CON_FLAG = "hosted_pro"

CLIENT_ID = "smartcar-client-id"
CLIENT_SECRET = "smartcar-client-secret"
REFRESH_TOKEN = "refresh-token-de-prueba"
SMARTCAR_AUTH_URL = "https://auth.smartcar.com/oauth/token"
SMARTCAR_API_BASE = "https://api.smartcar.com/v2.0"
VEHICLE_ID = "veh-123"


class FakeVault:
    """Doble de `edecan_db.vault.TokenVault` con `put`/`get` en memoria
    (mismo patrón que `test_smarthome_router.py`)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._store: dict[tuple[uuid.UUID, uuid.UUID], TokenBundle] = {}
        self.puts: list[tuple[uuid.UUID, uuid.UUID, TokenBundle]] = []

    async def put(self, tenant_id: uuid.UUID, account_id: uuid.UUID, bundle: TokenBundle) -> None:
        self.puts.append((tenant_id, account_id, bundle))
        self._store[(tenant_id, account_id)] = bundle

    async def get(self, tenant_id: uuid.UUID, account_id: uuid.UUID) -> TokenBundle | None:
        return self._store.get((tenant_id, account_id))


class _FakeDbSession:
    """Doble mínimo de `AsyncSession`: solo cuenta llamadas a `commit()`
    (`HOTFIXES_PENDIENTES.md` punto 8) — mismo patrón que
    `test_remote_router.py::_FakeDbSession`. `conftest.py` deja
    `get_tenant_session` en `lambda: None` por defecto, que no sirve para el
    caso puntual de `POST /{id}/puertas` cuando Smartcar falla."""

    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


def _headers(**overrides: Any) -> dict[str, str]:
    return auth_headers(
        user_id=overrides.pop("user_id", uuid.uuid4()),
        tenant_id=overrides.pop("tenant_id", uuid.uuid4()),
        plan_key=overrides.pop("plan_key", _PLAN_CON_FLAG),
    )


def _install_vault(app: Any) -> FakeVault:
    fake_vault = FakeVault()
    app.dependency_overrides[edecan_deps.get_vault] = lambda: fake_vault
    return fake_vault


def _credentials_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
    }
    body.update(overrides)
    return body


def _mock_refresh(
    *, access_token: str = "access-token-1", nuevo_refresh_token: str | None = None
) -> respx.Route:
    payload: dict[str, Any] = {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": 7200,
    }
    if nuevo_refresh_token is not None:
        payload["refresh_token"] = nuevo_refresh_token
    return respx.post(SMARTCAR_AUTH_URL).mock(return_value=httpx.Response(200, json=payload))


async def _conectar(client, headers: dict[str, str], *, refresh_token: str = REFRESH_TOKEN) -> None:
    """Conecta credenciales con `validate=false` (sin red) — helper para
    tests que solo necesitan una cuenta ya conectada antes de ejercitar otro
    endpoint."""
    response = await client.put(
        "/v1/vehiculos/credentials",
        json=_credentials_body(refresh_token=refresh_token, validate=False),
        headers=headers,
    )
    assert response.status_code == 204


@pytest.fixture
def mounted_app(app):
    """`edecan_api.main.create_app()` puede o no traer ya `vehiculos.router`
    montado (montaje defensivo v4, dueño WP-V4-01): solo se incluye a mano si
    todavía no está, para no registrar las mismas rutas dos veces (mismo
    patrón que `test_missions_router.py::_mounted_app`)."""
    ya_montado = any(getattr(route, "path", "") == "/v1/vehiculos" for route in app.routes)
    if not ya_montado:
        app.include_router(vehiculos.router)
    return app


# ---------------------------------------------------------------------------
# PUT /v1/vehiculos/credentials — requiere autenticación
# ---------------------------------------------------------------------------


@respx.mock
async def test_put_requires_authentication(client, mounted_app) -> None:
    response = await client.put(
        "/v1/vehiculos/credentials",
        json=_credentials_body(),
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# PUT /v1/vehiculos/credentials — validación de payload
# ---------------------------------------------------------------------------


@respx.mock
async def test_put_rechaza_campos_vacios(client, mounted_app) -> None:
    _install_vault(mounted_app)
    response = await client.put(
        "/v1/vehiculos/credentials",
        json={"client_id": "  ", "client_secret": CLIENT_SECRET, "refresh_token": REFRESH_TOKEN},
        headers=_headers(),
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# PUT /v1/vehiculos/credentials — validate=false (sin red)
# ---------------------------------------------------------------------------


@respx.mock
async def test_put_validate_false_no_pega_a_la_red_y_guarda(client, mounted_app, fake_repo) -> None:
    fake_vault = _install_vault(mounted_app)
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)

    await _conectar(client, headers)

    accounts = await fake_repo.list_connector_accounts(tenant_id=tenant_id)
    assert len(accounts) == 1
    assert accounts[0]["connector_key"] == "vehicles"
    assert accounts[0]["display_name"] == "Smartcar"

    assert len(fake_vault.puts) == 1
    stored_tenant_id, stored_account_id, bundle = fake_vault.puts[0]
    assert stored_tenant_id == tenant_id
    assert stored_account_id == accounts[0]["id"]
    assert json.loads(bundle.access_token) == {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
    }
    assert bundle.token_type == "config"

    actions = [entry["action"] for entry in fake_repo.audit_log]
    assert "vehiculos.connected" in actions


# ---------------------------------------------------------------------------
# PUT /v1/vehiculos/credentials — "pegar y validar" (validate=true, respx)
# ---------------------------------------------------------------------------


@respx.mock
async def test_put_valida_contra_smartcar_real_y_guarda(client, mounted_app) -> None:
    ruta_refresh = _mock_refresh(access_token="tok-valido")
    ruta_vehicles = respx.get(f"{SMARTCAR_API_BASE}/vehicles").mock(
        return_value=httpx.Response(200, json={"vehicles": []})
    )
    fake_vault = _install_vault(mounted_app)

    response = await client.put(
        "/v1/vehiculos/credentials",
        json=_credentials_body(),
        headers=_headers(),
    )

    assert response.status_code == 204
    assert len(fake_vault.puts) == 1
    assert ruta_refresh.calls.last.request.headers["Authorization"].startswith("Basic ")
    assert ruta_vehicles.calls.last.request.headers["Authorization"] == "Bearer tok-valido"


@respx.mock
async def test_put_credenciales_invalidas_400_no_guarda_nada(client, mounted_app) -> None:
    respx.post(SMARTCAR_AUTH_URL).mock(return_value=httpx.Response(401, text="invalid_grant"))
    fake_vault = _install_vault(mounted_app)

    response = await client.put(
        "/v1/vehiculos/credentials",
        json=_credentials_body(),
        headers=_headers(),
    )

    assert response.status_code == 400
    assert "rechaz" in response.json()["detail"].lower()
    assert fake_vault.puts == []


@respx.mock
async def test_put_smartcar_inalcanzable_400_no_guarda_nada(client, mounted_app) -> None:
    respx.post(SMARTCAR_AUTH_URL).mock(side_effect=httpx.ConnectError("connection refused"))
    fake_vault = _install_vault(mounted_app)

    response = await client.put(
        "/v1/vehiculos/credentials",
        json=_credentials_body(),
        headers=_headers(),
    )

    assert response.status_code == 400
    assert fake_vault.puts == []


@respx.mock
async def test_put_guarda_refresh_token_rotado_durante_la_validacion(client, mounted_app) -> None:
    """Si Smartcar rota el `refresh_token` YA en el ping de validación del
    propio `PUT`, lo que se guarda es el NUEVO — nunca el que mandó el
    usuario (ver docstring del router, "Rotación del refresh_token")."""
    _mock_refresh(nuevo_refresh_token="refresh-ROTADO-en-validacion")
    respx.get(f"{SMARTCAR_API_BASE}/vehicles").mock(
        return_value=httpx.Response(200, json={"vehicles": []})
    )
    fake_vault = _install_vault(mounted_app)

    response = await client.put(
        "/v1/vehiculos/credentials",
        json=_credentials_body(),
        headers=_headers(),
    )

    assert response.status_code == 204
    guardado = json.loads(fake_vault.puts[0][2].access_token)
    assert guardado["refresh_token"] == "refresh-ROTADO-en-validacion"


@respx.mock
async def test_put_reconecta_reusa_la_misma_cuenta(client, mounted_app, fake_repo) -> None:
    """Singleton por tenant: un segundo PUT actualiza la MISMA
    `connector_account`, no crea una segunda."""
    fake_vault = _install_vault(mounted_app)
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)

    await _conectar(client, headers, refresh_token="primer-refresh")
    await _conectar(client, headers, refresh_token="segundo-refresh")

    accounts = await fake_repo.list_connector_accounts(tenant_id=tenant_id)
    assert len(accounts) == 1
    assert len(fake_vault.puts) == 2
    assert json.loads(fake_vault.puts[-1][2].access_token)["refresh_token"] == "segundo-refresh"


# ---------------------------------------------------------------------------
# DELETE /v1/vehiculos/credentials
# ---------------------------------------------------------------------------


@respx.mock
async def test_delete_vehiculos_credentials(client, mounted_app, fake_repo) -> None:
    _install_vault(mounted_app)
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)
    await _conectar(client, headers)

    response = await client.delete("/v1/vehiculos/credentials", headers=headers)

    assert response.status_code == 204
    assert await fake_repo.list_connector_accounts(tenant_id=tenant_id) == []
    actions = [entry["action"] for entry in fake_repo.audit_log]
    assert "vehiculos.disconnected" in actions


@respx.mock
async def test_delete_es_idempotente(client, mounted_app) -> None:
    _install_vault(mounted_app)
    response = await client.delete("/v1/vehiculos/credentials", headers=_headers())
    assert response.status_code == 204


# ---------------------------------------------------------------------------
# GET /v1/vehiculos/status
# ---------------------------------------------------------------------------


@respx.mock
async def test_status_no_configurado(client, mounted_app) -> None:
    _install_vault(mounted_app)
    response = await client.get("/v1/vehiculos/status", headers=_headers())
    assert response.status_code == 200
    assert response.json() == {"configured": False, "reachable": None}


@respx.mock
async def test_status_configurado_y_reachable_true(client, mounted_app) -> None:
    _mock_refresh()
    _install_vault(mounted_app)
    headers = _headers()
    await _conectar(client, headers)

    response = await client.get("/v1/vehiculos/status", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"configured": True, "reachable": True}


@respx.mock
async def test_status_configurado_pero_credenciales_revocadas_reachable_false(
    client, mounted_app
) -> None:
    respx.post(SMARTCAR_AUTH_URL).mock(return_value=httpx.Response(401))
    _install_vault(mounted_app)
    headers = _headers()
    await _conectar(client, headers)

    response = await client.get("/v1/vehiculos/status", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["reachable"] is False


@respx.mock
async def test_status_red_falla_reachable_null_nunca_500(client, mounted_app) -> None:
    respx.post(SMARTCAR_AUTH_URL).mock(side_effect=httpx.ConnectTimeout("timed out"))
    _install_vault(mounted_app)
    headers = _headers()
    await _conectar(client, headers)

    response = await client.get("/v1/vehiculos/status", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["reachable"] is None


# ---------------------------------------------------------------------------
# GET /v1/vehiculos — lista
# ---------------------------------------------------------------------------


@respx.mock
async def test_listar_sin_conectar_400(client, mounted_app) -> None:
    _install_vault(mounted_app)
    response = await client.get("/v1/vehiculos", headers=_headers())
    assert response.status_code == 400


@respx.mock
async def test_listar_feliz_dos_vehiculos(client, mounted_app) -> None:
    _mock_refresh()
    respx.get(f"{SMARTCAR_API_BASE}/vehicles").mock(
        return_value=httpx.Response(200, json={"vehicles": ["id-1", "id-2"]})
    )
    respx.get(f"{SMARTCAR_API_BASE}/vehicles/id-1").mock(
        return_value=httpx.Response(
            200, json={"id": "id-1", "make": "TESLA", "model": "Model 3", "year": 2023}
        )
    )
    respx.get(f"{SMARTCAR_API_BASE}/vehicles/id-2").mock(return_value=httpx.Response(403))
    _install_vault(mounted_app)
    headers = _headers()
    await _conectar(client, headers)

    response = await client.get("/v1/vehiculos", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body[0] == {"id": "id-1", "marca": "TESLA", "modelo": "Model 3", "anio": 2023}
    assert body[1] == {"id": "id-2", "marca": None, "modelo": None, "anio": None}


@respx.mock
async def test_listar_persiste_rotacion_de_refresh_token(client, mounted_app) -> None:
    _mock_refresh(nuevo_refresh_token="refresh-ROTADO-en-lista")
    respx.get(f"{SMARTCAR_API_BASE}/vehicles").mock(
        return_value=httpx.Response(200, json={"vehicles": []})
    )
    fake_vault = _install_vault(mounted_app)
    headers = _headers()
    await _conectar(client, headers)
    assert len(fake_vault.puts) == 1  # solo el PUT inicial hasta ahora

    response = await client.get("/v1/vehiculos", headers=headers)

    assert response.status_code == 200
    assert len(fake_vault.puts) == 2  # el GET persistió la rotación
    guardado = json.loads(fake_vault.puts[-1][2].access_token)
    assert guardado["refresh_token"] == "refresh-ROTADO-en-lista"
    assert guardado["client_id"] == CLIENT_ID


# ---------------------------------------------------------------------------
# GET /v1/vehiculos/{vehicle_id}/estado
# ---------------------------------------------------------------------------


@respx.mock
async def test_estado_sin_conectar_400(client, mounted_app) -> None:
    _install_vault(mounted_app)
    response = await client.get(f"/v1/vehiculos/{VEHICLE_ID}/estado", headers=_headers())
    assert response.status_code == 400


@respx.mock
async def test_estado_con_capabilities_parciales(client, mounted_app) -> None:
    _mock_refresh()
    respx.get(f"{SMARTCAR_API_BASE}/vehicles/{VEHICLE_ID}/battery").mock(
        return_value=httpx.Response(200, json={"percentRemaining": 0.65, "range": 250.0})
    )
    respx.get(f"{SMARTCAR_API_BASE}/vehicles/{VEHICLE_ID}/fuel").mock(
        return_value=httpx.Response(501)
    )
    respx.get(f"{SMARTCAR_API_BASE}/vehicles/{VEHICLE_ID}/odometer").mock(
        return_value=httpx.Response(200, json={"distance": 4321.0})
    )
    respx.get(f"{SMARTCAR_API_BASE}/vehicles/{VEHICLE_ID}/location").mock(
        return_value=httpx.Response(409)
    )
    _install_vault(mounted_app)
    headers = _headers()
    await _conectar(client, headers)

    response = await client.get(f"/v1/vehiculos/{VEHICLE_ID}/estado", headers=headers)

    assert response.status_code == 200
    assert response.json() == {
        "vehicle_id": VEHICLE_ID,
        "bateria": {"porcentaje": 65.0, "autonomia_km": 250.0},
        "combustible": None,
        "odometro": 4321.0,
        "ubicacion": None,
    }


@respx.mock
async def test_estado_401_reconecta_reporta_400(client, mounted_app) -> None:
    _mock_refresh()
    respx.get(f"{SMARTCAR_API_BASE}/vehicles/{VEHICLE_ID}/battery").mock(
        return_value=httpx.Response(401)
    )
    _install_vault(mounted_app)
    headers = _headers()
    await _conectar(client, headers)

    response = await client.get(f"/v1/vehiculos/{VEHICLE_ID}/estado", headers=headers)
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# POST /v1/vehiculos/{vehicle_id}/puertas — validación
# ---------------------------------------------------------------------------


@respx.mock
async def test_puertas_accion_invalida_400(client, mounted_app) -> None:
    _install_vault(mounted_app)
    response = await client.post(
        f"/v1/vehiculos/{VEHICLE_ID}/puertas", json={"accion": "arrancar"}, headers=_headers()
    )
    assert response.status_code == 400


@respx.mock
async def test_puertas_sin_conectar_400(client, mounted_app) -> None:
    _install_vault(mounted_app)
    response = await client.post(
        f"/v1/vehiculos/{VEHICLE_ID}/puertas", json={"accion": "bloquear"}, headers=_headers()
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# POST /v1/vehiculos/{vehicle_id}/puertas — camino feliz, con auditoría
# ---------------------------------------------------------------------------


@respx.mock
async def test_puertas_bloquear_feliz_con_audit(client, mounted_app, fake_repo) -> None:
    _mock_refresh()
    ruta_security = respx.post(f"{SMARTCAR_API_BASE}/vehicles/{VEHICLE_ID}/security").mock(
        return_value=httpx.Response(200, json={"status": "success"})
    )
    _install_vault(mounted_app)
    headers = _headers()
    await _conectar(client, headers)

    response = await client.post(
        f"/v1/vehiculos/{VEHICLE_ID}/puertas", json={"accion": "bloquear"}, headers=headers
    )

    assert response.status_code == 200
    assert response.json() == {"vehicle_id": VEHICLE_ID, "accion": "bloquear", "status": "ok"}
    enviado = json.loads(ruta_security.calls.last.request.content)
    assert enviado == {"action": "LOCK"}

    actions = [entry["action"] for entry in fake_repo.audit_log]
    assert "vehiculos.puertas.bloquear" in actions


@respx.mock
async def test_puertas_desbloquear_manda_unlock(client, mounted_app) -> None:
    _mock_refresh()
    ruta_security = respx.post(f"{SMARTCAR_API_BASE}/vehicles/{VEHICLE_ID}/security").mock(
        return_value=httpx.Response(200, json={"status": "success"})
    )
    _install_vault(mounted_app)
    headers = _headers()
    await _conectar(client, headers)

    response = await client.post(
        f"/v1/vehiculos/{VEHICLE_ID}/puertas", json={"accion": "DESBLOQUEAR"}, headers=headers
    )

    assert response.status_code == 200
    enviado = json.loads(ruta_security.calls.last.request.content)
    assert enviado == {"action": "UNLOCK"}


# ---------------------------------------------------------------------------
# POST /v1/vehiculos/{vehicle_id}/puertas — error de Smartcar: audit_log
# SIEMPRE, comiteado ANTES de relanzar (HOTFIXES_PENDIENTES.md punto 8).
# ---------------------------------------------------------------------------


@respx.mock
async def test_puertas_error_de_smartcar_commitea_auditoria_antes_de_relanzar(
    client, mounted_app, fake_repo
) -> None:
    """Verifica el commit EN SÍ (`fake_db_session.commits`), no solo el
    resultado en `fake_repo` (un fake en memoria sin semántica transaccional
    real — "persiste" el audit log aunque el código nunca llamara a
    `commit()`, así que por sí solo no detectaría una regresión de
    `HOTFIXES_PENDIENTES.md` punto 8) — mismo criterio que
    `test_remote_router.py::test_frame_denied_commits_audit_evidence_before_raising_403`.
    """
    _mock_refresh()
    respx.post(f"{SMARTCAR_API_BASE}/vehicles/{VEHICLE_ID}/security").mock(
        return_value=httpx.Response(409, text="vehicle is asleep")
    )
    _install_vault(mounted_app)
    headers = _headers()
    await _conectar(client, headers)

    fake_db_session = _FakeDbSession()
    mounted_app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: fake_db_session

    response = await client.post(
        f"/v1/vehiculos/{VEHICLE_ID}/puertas", json={"accion": "bloquear"}, headers=headers
    )

    assert response.status_code == 502
    assert fake_db_session.commits == 1

    actions = [entry["action"] for entry in fake_repo.audit_log]
    assert "vehiculos.puertas.error" in actions
    fallo = next(e for e in fake_repo.audit_log if e["action"] == "vehiculos.puertas.error")
    assert fallo["meta"]["accion"] == "bloquear"
    assert fallo["target"] == VEHICLE_ID


@respx.mock
async def test_puertas_credenciales_rechazadas_tambien_audita_y_commitea(
    client, mounted_app, fake_repo
) -> None:
    """Mismo commit-antes-de-relanzar, pero cuando el que falla es el
    refresh (credenciales revocadas) en vez de la llamada de seguridad. Aquí
    se siembra la cuenta/vault directo (en vez de vía `PUT`) para poder
    mockear un ÚNICO comportamiento de `SMARTCAR_AUTH_URL` (401) sin
    reconfigurar `respx` a mitad de test."""
    fake_vault = _install_vault(mounted_app)
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)

    account = await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key="vehicles",
        external_account_id="vehicles",
        display_name="Smartcar",
        scopes=[],
    )
    await fake_vault.put(
        tenant_id,
        account["id"],
        TokenBundle(
            access_token=json.dumps(
                {
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                    "refresh_token": REFRESH_TOKEN,
                }
            ),
            token_type="config",
            scopes=["smartcar"],
        ),
    )
    respx.post(SMARTCAR_AUTH_URL).mock(return_value=httpx.Response(401, text="revoked"))

    fake_db_session = _FakeDbSession()
    mounted_app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: fake_db_session

    response = await client.post(
        f"/v1/vehiculos/{VEHICLE_ID}/puertas", json={"accion": "desbloquear"}, headers=headers
    )

    assert response.status_code == 400
    assert fake_db_session.commits == 1
    actions = [entry["action"] for entry in fake_repo.audit_log]
    assert "vehiculos.puertas.error" in actions
