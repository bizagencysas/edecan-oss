"""`edecan_api.routers.devices` — push nativo (APNs/FCM), v5,
`ARCHITECTURE.md` §14, WP-V5-13 (ver el docstring del propio router, sección
"Push nativo (APNs/FCM)", para el contrato completo).

Mismo patrón que `test_devices_router.py` (para `get_tenant_session`, vía
`FakeSession`/`FakeResult` locales) + `test_ads_router.py` (para `get_vault`,
vía `FakeVault` local) — ambos duplicados a propósito en este archivo en vez
de importados (`ARCHITECTURE.md` §10.1: "los tests no importan paquetes
hermanos... usan stubs/fakes"; cada archivo de test de este estilo mantiene
sus propios dobles). `get_repo` NO se sobreescribe: sigue apuntando al
`fake_repo` que `conftest.py::app` ya inyecta (`FakeRepo`, en memoria) — así
`connector_accounts`/`audit_log` persisten entre llamadas sucesivas dentro
del MISMO test (necesario para probar el merge parcial de `PUT
/push/credentials`).

No existe ningún plan real con `notifications.push` en `False` hoy (`True`
en los 4 planes de `edecan_schemas.plans.PLANES`) — los tests de "flag
apagado" usan un `plan_key` inventado que no está en `PLANES`
(`flags_for_plan` devuelve `{}` para un plan desconocido, mismo truco que
`test_ide_router.py::test_...plan_fantasma...`), no una plataforma con el
flag apagado de verdad.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from conftest import auth_headers
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from edecan_schemas import TokenBundle
from httpx import ASGITransport, AsyncClient

import edecan_api.deps as edecan_deps
from edecan_api.routers import devices

PLAN_SIN_PUSH = "plan_fantasma_sin_push"


# ---------------------------------------------------------------------------
# Fakes locales — ver docstring del módulo.
# ---------------------------------------------------------------------------


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> FakeResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)


@dataclass
class FakeSession:
    respuestas: list[list[dict[str, Any]]] = field(default_factory=list)
    llamadas: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> FakeResult:
        self.llamadas.append((str(stmt), dict(params or {})))
        filas = self.respuestas.pop(0) if self.respuestas else []
        return FakeResult(filas)


@dataclass
class FakeVault:
    """`get_vault` falso: `bundle` es lo que devuelve `get()`; `puts` registra
    cada `put()` (mismo patrón que `test_ads_router.py::FakeVault`)."""

    bundle: TokenBundle | None = None
    puts: list[tuple[uuid.UUID, uuid.UUID, TokenBundle]] = field(default_factory=list)

    async def put(self, tenant_id: uuid.UUID, account_id: uuid.UUID, bundle: TokenBundle) -> None:
        self.puts.append((tenant_id, account_id, bundle))
        self.bundle = bundle

    async def get(self, tenant_id: uuid.UUID, account_id: uuid.UUID) -> TokenBundle | None:
        return self.bundle


@pytest.fixture
def fake_session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def fake_vault() -> FakeVault:
    return FakeVault()


@pytest.fixture
def _mounted_app(app, fake_session: FakeSession, fake_vault: FakeVault):
    app.include_router(devices.router)
    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: fake_session
    app.dependency_overrides[edecan_deps.get_vault] = lambda: fake_vault
    return app


@pytest.fixture
async def client(_mounted_app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=_mounted_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _headers(**kw: Any) -> dict[str, str]:
    kw.setdefault("user_id", uuid.uuid4())
    kw.setdefault("tenant_id", uuid.uuid4())
    return auth_headers(**kw)


# ---------------------------------------------------------------------------
# Credenciales de prueba — claves reales generadas EN el test, nunca reales.
# ---------------------------------------------------------------------------


def _p8_ec_valido() -> str:
    clave = ec.generate_private_key(ec.SECP256R1())
    return clave.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


def _p8_rsa_no_ec() -> str:
    """Una clave privada PEM válida... pero RSA, no EC — `_validar_apns` debe
    rechazarla por tipo, no por forma PEM."""
    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return clave.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


def _apns_body(**overrides: Any) -> dict[str, Any]:
    base = {
        "team_id": "TEAMID1234",
        "key_id": "KEYID5678",
        "bundle_id": "com.acme.app",
        "p8_key": _p8_ec_valido(),
    }
    base.update(overrides)
    return base


def _service_account_json(
    *,
    project_id: str = "mi-proyecto",
    client_email: str = "svc@mi-proyecto.iam.gserviceaccount.com",
) -> str:
    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = clave.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    return json.dumps(
        {
            "type": "service_account",
            "project_id": project_id,
            "private_key": pem,
            "client_email": client_email,
        }
    )


def _fcm_body(**overrides: Any) -> dict[str, Any]:
    base = {"service_account_json": _service_account_json()}
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Autenticación / flag gate — en TODAS las rutas nuevas.
# ---------------------------------------------------------------------------


async def test_set_push_token_requires_authentication(client) -> None:
    response = await client.post(
        f"/v1/devices/{uuid.uuid4()}/push-token",
        json={"push_token": "tok", "push_platform": "apns"},
    )
    assert response.status_code == 401


async def test_delete_push_token_requires_authentication(client) -> None:
    response = await client.delete(f"/v1/devices/{uuid.uuid4()}/push-token")
    assert response.status_code == 401


async def test_put_push_credentials_requires_authentication(client) -> None:
    response = await client.put("/v1/devices/push/credentials", json={"apns": _apns_body()})
    assert response.status_code == 401


async def test_delete_push_credentials_requires_authentication(client) -> None:
    response = await client.delete("/v1/devices/push/credentials")
    assert response.status_code == 401


async def test_push_status_requires_authentication(client) -> None:
    response = await client.get("/v1/devices/push/status")
    assert response.status_code == 401


async def test_push_preferences_requires_authentication(client) -> None:
    assert (await client.get("/v1/devices/push/preferences")).status_code == 401
    assert (
        await client.put("/v1/devices/push/preferences", json={"content": False})
    ).status_code == 401


_DEVICE_ID_CUALQUIERA = uuid.uuid4()
_PUSH_TOKEN_BODY_CUALQUIERA = {"push_token": "t", "push_platform": "apns"}


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", f"/v1/devices/{_DEVICE_ID_CUALQUIERA}/push-token", _PUSH_TOKEN_BODY_CUALQUIERA),
        ("DELETE", f"/v1/devices/{_DEVICE_ID_CUALQUIERA}/push-token", None),
        ("PUT", "/v1/devices/push/credentials", {"apns": _apns_body()}),
        ("DELETE", "/v1/devices/push/credentials", None),
        ("GET", "/v1/devices/push/status", None),
        ("GET", "/v1/devices/push/preferences", None),
        ("PUT", "/v1/devices/push/preferences", {"content": False}),
    ],
)
async def test_rechaza_plan_sin_flag_notifications_push(
    client, method: str, path: str, body: dict[str, Any] | None
) -> None:
    headers = _headers(plan_key=PLAN_SIN_PUSH)
    response = await client.request(method, path, json=body, headers=headers)
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# GET/PUT /push/preferences
# ---------------------------------------------------------------------------


async def test_push_preferences_defaults_are_human_friendly(
    client, fake_session: FakeSession
) -> None:
    fake_session.respuestas = [[]]

    response = await client.get("/v1/devices/push/preferences", headers=_headers())

    assert response.status_code == 200
    assert response.json() == {
        "work": True,
        "content": True,
        "design": True,
        "files": True,
        "self_repair": True,
    }


async def test_put_push_preferences_writes_only_safe_category_state(
    client, fake_session: FakeSession
) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    fake_session.respuestas = [[], []]

    response = await client.put(
        "/v1/devices/push/preferences",
        json={"content": False, "files": False},
        headers=_headers(tenant_id=tenant_id, user_id=user_id),
    )

    assert response.status_code == 200
    assert response.json()["content"] is False
    assert response.json()["files"] is False
    assert response.json()["work"] is True
    insert_sql, params = fake_session.llamadas[1]
    assert "INSERT INTO audit_log" in insert_sql
    assert params["tenant_id"] == tenant_id
    assert params["user_id"] == user_id
    assert params["action"] == "notifications.preferences.updated"
    serialized = params["meta"]
    assert "content" in serialized and "files" in serialized
    assert "prompt" not in serialized and "filename" not in serialized


async def test_put_push_preferences_rejects_unknown_category(client) -> None:
    response = await client.put(
        "/v1/devices/push/preferences",
        json={"marketing_secrets": True},
        headers=_headers(),
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /{id}/push-token
# ---------------------------------------------------------------------------


async def test_set_push_token_ok_filtra_por_tenant_usuario_y_activo(
    client, fake_session: FakeSession
) -> None:
    device_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    fake_session.respuestas = [[{"id": device_id}]]

    response = await client.post(
        f"/v1/devices/{device_id}/push-token",
        json={"push_token": "tok-abc123", "push_platform": "apns"},
        headers=_headers(tenant_id=tenant_id, user_id=user_id),
    )

    assert response.status_code == 204
    sql, params = fake_session.llamadas[0]
    assert "UPDATE devices" in sql
    assert "push_token = :push_token" in sql
    assert "push_platform = :push_platform" in sql
    assert "tenant_id = :tenant_id" in sql
    assert "user_id = :user_id" in sql
    assert "status = 'active'" in sql
    assert params["id"] == str(device_id)
    assert params["tenant_id"] == str(tenant_id)
    assert params["user_id"] == str(user_id)
    assert params["push_token"] == "tok-abc123"
    assert params["push_platform"] == "apns"


async def test_set_push_token_device_ajeno_o_inexistente_404(
    client, fake_session: FakeSession
) -> None:
    fake_session.respuestas = [[]]  # UPDATE no matchea ninguna fila
    response = await client.post(
        f"/v1/devices/{uuid.uuid4()}/push-token",
        json={"push_token": "tok", "push_platform": "fcm"},
        headers=_headers(),
    )
    assert response.status_code == 404


async def test_set_push_token_rechaza_plataforma_invalida(client) -> None:
    response = await client.post(
        f"/v1/devices/{uuid.uuid4()}/push-token",
        json={"push_token": "tok", "push_platform": "windows_notification_center"},
        headers=_headers(),
    )
    assert response.status_code == 422


async def test_set_push_token_rechaza_token_vacio(client) -> None:
    response = await client.post(
        f"/v1/devices/{uuid.uuid4()}/push-token",
        json={"push_token": "", "push_platform": "apns"},
        headers=_headers(),
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /{id}/push-token
# ---------------------------------------------------------------------------


async def test_delete_push_token_ok_limpia_ambas_columnas(
    client, fake_session: FakeSession
) -> None:
    device_id = uuid.uuid4()
    fake_session.respuestas = [[{"id": device_id}]]

    response = await client.delete(
        f"/v1/devices/{device_id}/push-token", headers=_headers()
    )

    assert response.status_code == 204
    sql, params = fake_session.llamadas[0]
    assert "push_token = NULL" in sql
    assert "push_platform = NULL" in sql
    assert params["id"] == str(device_id)


async def test_delete_push_token_device_ajeno_404(client, fake_session: FakeSession) -> None:
    fake_session.respuestas = [[]]
    response = await client.delete(f"/v1/devices/{uuid.uuid4()}/push-token", headers=_headers())
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# PUT /push/credentials — forma inválida (400, sin red, nada se guarda)
# ---------------------------------------------------------------------------


async def test_put_credentials_sin_apns_ni_fcm_400(client, fake_vault: FakeVault) -> None:
    response = await client.put(
        "/v1/devices/push/credentials", json={}, headers=_headers()
    )
    assert response.status_code == 400
    assert fake_vault.puts == []


async def test_put_credentials_apns_p8_basura_400(client, fake_vault: FakeVault) -> None:
    response = await client.put(
        "/v1/devices/push/credentials",
        json={"apns": _apns_body(p8_key="esto no es una clave p8, es basura")},
        headers=_headers(),
    )
    assert response.status_code == 400
    assert "PEM" in response.json()["detail"]
    assert fake_vault.puts == []


async def test_put_credentials_apns_pem_con_header_pero_cuerpo_corrupto_400(
    client, fake_vault: FakeVault
) -> None:
    pem_corrupto = (
        "-----BEGIN PRIVATE KEY-----\nZZZZ_no_es_base64_valido\n-----END PRIVATE KEY-----"
    )
    response = await client.put(
        "/v1/devices/push/credentials",
        json={"apns": _apns_body(p8_key=pem_corrupto)},
        headers=_headers(),
    )
    assert response.status_code == 400
    assert fake_vault.puts == []


async def test_put_credentials_apns_clave_rsa_en_vez_de_ec_400(
    client, fake_vault: FakeVault
) -> None:
    response = await client.put(
        "/v1/devices/push/credentials",
        json={"apns": _apns_body(p8_key=_p8_rsa_no_ec())},
        headers=_headers(),
    )
    assert response.status_code == 400
    detalle = response.json()["detail"].lower()
    assert "curva elíptica" in detalle or "ec" in detalle
    assert fake_vault.puts == []


async def test_put_credentials_fcm_json_invalido_400(client, fake_vault: FakeVault) -> None:
    response = await client.put(
        "/v1/devices/push/credentials",
        json={"fcm": _fcm_body(service_account_json="{esto no es json valido")},
        headers=_headers(),
    )
    assert response.status_code == 400
    assert fake_vault.puts == []


async def test_put_credentials_fcm_json_sin_type_service_account_400(
    client, fake_vault: FakeVault
) -> None:
    response = await client.put(
        "/v1/devices/push/credentials",
        json={"fcm": _fcm_body(service_account_json=json.dumps({"type": "otra_cosa"}))},
        headers=_headers(),
    )
    assert response.status_code == 400
    assert "service_account" in response.json()["detail"]
    assert fake_vault.puts == []


async def test_put_credentials_fcm_json_sin_client_email_ni_private_key_400(
    client, fake_vault: FakeVault
) -> None:
    response = await client.put(
        "/v1/devices/push/credentials",
        json={
            "fcm": _fcm_body(
                service_account_json=json.dumps({"type": "service_account", "project_id": "x"})
            )
        },
        headers=_headers(),
    )
    assert response.status_code == 400
    assert fake_vault.puts == []


async def test_put_credentials_fcm_sin_project_id_derivable_400(
    client, fake_vault: FakeVault
) -> None:
    sin_project_id = json.dumps(
        {
            "type": "service_account",
            "client_email": "svc@x.iam.gserviceaccount.com",
            "private_key": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----",
        }
    )
    response = await client.put(
        "/v1/devices/push/credentials",
        json={"fcm": {"service_account_json": sin_project_id}},
        headers=_headers(),
    )
    assert response.status_code == 400
    assert "project_id" in response.json()["detail"]
    assert fake_vault.puts == []


# ---------------------------------------------------------------------------
# PUT /push/credentials — feliz + merge parcial.
# ---------------------------------------------------------------------------


async def test_put_credentials_solo_apns_guarda_y_audita(
    client, fake_vault: FakeVault, fake_repo
) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id, user_id=user_id)

    response = await client.put(
        "/v1/devices/push/credentials",
        json={"apns": _apns_body(team_id="EQUIPO1")},
        headers=headers,
    )

    assert response.status_code == 204
    assert len(fake_vault.puts) == 1
    stored_tenant_id, _account_id, bundle = fake_vault.puts[0]
    assert stored_tenant_id == tenant_id
    assert bundle.token_type == "config"
    config = json.loads(bundle.access_token)
    assert set(config.keys()) == {"apns"}
    assert config["apns"]["team_id"] == "EQUIPO1"
    assert config["apns"]["environment"] == "production"

    assert len(fake_repo.audit_log) == 1
    entry = fake_repo.audit_log[0]
    assert entry["action"] == "devices.push_credentials.connected"
    assert entry["tenant_id"] == tenant_id
    assert entry["actor_user_id"] == user_id
    assert entry["meta"] == {"apns": True, "fcm": False}


async def test_put_credentials_solo_fcm_deriva_project_id_del_json(
    client, fake_vault: FakeVault
) -> None:
    sin_project_id_en_body = _service_account_json(project_id="derivado-del-json")
    response = await client.put(
        "/v1/devices/push/credentials",
        json={"fcm": {"service_account_json": sin_project_id_en_body}},
        headers=_headers(),
    )

    assert response.status_code == 204
    _tenant, _account, bundle = fake_vault.puts[0]
    config = json.loads(bundle.access_token)
    assert config["fcm"]["project_id"] == "derivado-del-json"


async def test_put_credentials_segunda_llamada_solo_fcm_no_borra_apns_ya_guardado(
    client, fake_vault: FakeVault
) -> None:
    """Un `PUT` con solo `fcm` NUNCA debe borrar un `apns` guardado antes en
    una llamada previa — ver docstring del router (`_cargar_config_push_
    existente`)."""
    headers = _headers()

    await client.put(
        "/v1/devices/push/credentials", json={"apns": _apns_body()}, headers=headers
    )
    response = await client.put(
        "/v1/devices/push/credentials",
        json={"fcm": _fcm_body(project_id="proyecto-2")},
        headers=headers,
    )

    assert response.status_code == 204
    assert len(fake_vault.puts) == 2
    _tenant, _account, bundle_final = fake_vault.puts[-1]
    config = json.loads(bundle_final.access_token)
    assert set(config.keys()) == {"apns", "fcm"}
    assert config["fcm"]["project_id"] == "proyecto-2"


# ---------------------------------------------------------------------------
# DELETE /push/credentials
# ---------------------------------------------------------------------------


async def test_delete_credentials_es_idempotente_sin_nada_conectado(
    client, fake_repo
) -> None:
    response = await client.delete("/v1/devices/push/credentials", headers=_headers())
    assert response.status_code == 204
    assert fake_repo.audit_log == []


async def test_delete_credentials_borra_lo_conectado_y_audita(
    client, fake_vault: FakeVault, fake_repo
) -> None:
    headers = _headers()
    await client.put("/v1/devices/push/credentials", json={"apns": _apns_body()}, headers=headers)

    response = await client.delete("/v1/devices/push/credentials", headers=headers)

    assert response.status_code == 204
    acciones = [entry["action"] for entry in fake_repo.audit_log]
    assert "devices.push_credentials.disconnected" in acciones


# ---------------------------------------------------------------------------
# GET /push/status
# ---------------------------------------------------------------------------


async def test_push_status_no_configurado(client, fake_session: FakeSession) -> None:
    fake_session.respuestas = [[{"n": 0}]]
    response = await client.get("/v1/devices/push/status", headers=_headers())
    assert response.status_code == 200
    assert response.json() == {"apns": False, "fcm": False, "devices_con_token": 0}


async def test_push_status_configurado_con_ambos_y_cuenta_dispositivos(
    client, fake_vault: FakeVault, fake_session: FakeSession
) -> None:
    headers = _headers()
    await client.put(
        "/v1/devices/push/credentials",
        json={"apns": _apns_body(), "fcm": _fcm_body()},
        headers=headers,
    )

    fake_session.respuestas = [[{"n": 3}]]
    response = await client.get("/v1/devices/push/status", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"apns": True, "fcm": True, "devices_con_token": 3}
    sql, params = fake_session.llamadas[0]
    assert "COUNT(*)" in sql
    assert "push_token IS NOT NULL" in sql
    assert "tenant_id" in params
