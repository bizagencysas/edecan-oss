"""Tests de `edecan_worker.push` — envío de push nativo (APNs/FCM)
bring-your-own, 100% offline (`respx` + claves generadas EN el test con
`cryptography`, nunca un push real ni una credencial real, ver docstring del
propio módulo bajo prueba).

`_FakeSession`/`_FakeVault`/`_FakeBundle` son fakes PROPIOS de este archivo
(no se toca `apps/worker/tests/fakes.py` compartido) — mismo criterio que
`test_llm_por_tenant.py`: `fakes.FakeSession` es un placeholder sin
`execute()`, y `edecan_worker.push` necesita una `AsyncSession` que entienda
el SELECT/UPDATE crudo sobre `connector_accounts`/`devices`
(`sqlalchemy.text`), que ninguno de los fakes compartidos modela.
"""

from __future__ import annotations

import json
import sys
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

import edecan_worker.push as push
import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fakes import make_deps

# ---------------------------------------------------------------------------
# Fakes locales — ver docstring del módulo.
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> _FakeResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)


@dataclass
class _FakeSession:
    """`get_tenant_session`-like: cada `execute()` consume la siguiente
    respuesta programada, EN EL ORDEN EXACTO en que `push.py` las pide
    (mismo patrón que `apps/api/tests/test_ads_router.py::FakeSession`)."""

    respuestas: list[list[dict[str, Any]]] = field(default_factory=list)
    llamadas: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        self.llamadas.append((str(stmt), dict(params or {})))
        filas = self.respuestas.pop(0) if self.respuestas else []
        return _FakeResult(filas)


class _RaisingSession:
    """Simula una sesión/DB caída: cualquier `execute` lanza."""

    async def execute(self, *args: Any, **kwargs: Any) -> _FakeResult:
        raise RuntimeError("la base de datos no respondió")


def _session_factory_de(session: Any):
    """`SessionFactory` (`edecan_worker.deps.SessionFactory`) que siempre
    entrega `session` sin importar el `tenant_id` pedido (mismo patrón que
    `test_llm_por_tenant.py::_session_factory_de`)."""

    @asynccontextmanager
    async def _factory(tenant_id: uuid.UUID | None):
        yield session

    return _factory


@dataclass
class _FakeBundle:
    access_token: str


class _FakeVault:
    """Doble de `edecan_db.vault.TokenVault`: solo implementa `get(...)` (lo
    único que usa `edecan_worker.push`), con la MISMA firma de kwargs
    (`tenant_id=`, `connector_account_id=`) que `push.py` invoca (mismo
    patrón que `test_llm_por_tenant.py::_FakeVault`)."""

    def __init__(
        self, *, bundle: _FakeBundle | None = None, raise_exc: Exception | None = None
    ) -> None:
        self._bundle = bundle
        self._raise_exc = raise_exc
        self.get_calls: list[tuple[uuid.UUID, uuid.UUID]] = []

    async def get(
        self, *, tenant_id: uuid.UUID, connector_account_id: uuid.UUID
    ) -> _FakeBundle | None:
        self.get_calls.append((tenant_id, connector_account_id))
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._bundle


# ---------------------------------------------------------------------------
# Credenciales de prueba — claves reales generadas EN el test, nunca reales.
# ---------------------------------------------------------------------------


def _par_ec_p8() -> tuple[ec.EllipticCurvePrivateKey, str]:
    clave = ec.generate_private_key(ec.SECP256R1())
    pem = clave.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    return clave, pem


def _service_account_fcm_json(
    *,
    client_email: str = "svc@mi-proyecto.iam.gserviceaccount.com",
    project_id: str = "mi-proyecto",
) -> str:
    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = clave.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    data = {
        "type": "service_account",
        "project_id": project_id,
        "private_key": pem,
        "client_email": client_email,
    }
    return json.dumps(data)


def _cred_apns(p8_key: str, **overrides: Any) -> dict[str, Any]:
    base = {
        "team_id": "TEAMID1234",
        "key_id": "KEYID5678",
        "bundle_id": "com.acme.app",
        "p8_key": p8_key,
        "environment": "production",
    }
    base.update(overrides)
    return base


def _cred_fcm(
    *, project_id: str = "mi-proyecto", client_email: str | None = None, **overrides: Any
) -> dict[str, Any]:
    """`client_email`, si viene, se hornea DENTRO del `service_account_json`
    generado (no es un campo de `cred_fcm` en sí — `push.py` lo lee del JSON
    parseado, nunca del dict externo)."""
    kwargs_json: dict[str, Any] = {"project_id": project_id}
    if client_email is not None:
        kwargs_json["client_email"] = client_email
    base = {
        "service_account_json": _service_account_fcm_json(**kwargs_json),
        "project_id": project_id,
    }
    base.update(overrides)
    return base


def _device_row(**overrides: Any) -> dict[str, Any]:
    base = {"id": uuid.uuid4(), "push_token": "tok-abc", "push_platform": "apns"}
    base.update(overrides)
    return base


def _mock_fcm_token_exchange(*, access_token: str = "t") -> respx.Route:
    """Registra el mock de `oauth2.googleapis.com/token` (canje del JWT-bearer
    por un access_token) — helper compartido para no repetir la misma línea
    larga en cada test de `enviar_fcm`."""
    return respx.post(push._FCM_TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": access_token})
    )


# ---------------------------------------------------------------------------
# `enviar_apns`
# ---------------------------------------------------------------------------


@respx.mock
async def test_enviar_apns_jwt_decodificable_con_kid_e_iss_correctos() -> None:
    clave, p8_pem = _par_ec_p8()
    cred = _cred_apns(p8_pem, team_id="TEAM-XYZ", key_id="KEY-123")
    respx.post(f"https://{push._APNS_HOST_PRODUCTION}/3/device/tok-1").mock(
        return_value=httpx.Response(200)
    )

    await push.enviar_apns(cred, "tok-1", "Hola", "Mundo")

    request = respx.calls.last.request
    auth = request.headers["authorization"]
    assert auth.startswith("bearer ")
    token_jwt = auth.removeprefix("bearer ")

    header = jwt.get_unverified_header(token_jwt)
    assert header["kid"] == "KEY-123"
    assert header["alg"] == "ES256"

    payload = jwt.decode(token_jwt, clave.public_key(), algorithms=["ES256"])
    assert payload["iss"] == "TEAM-XYZ"
    assert isinstance(payload["iat"], int)


@respx.mock
async def test_enviar_apns_request_correcta_topic_push_type_y_body() -> None:
    _, p8_pem = _par_ec_p8()
    cred = _cred_apns(p8_pem, bundle_id="com.midominio.app")
    respx.post(f"https://{push._APNS_HOST_PRODUCTION}/3/device/tok-2").mock(
        return_value=httpx.Response(200)
    )

    response = await push.enviar_apns(cred, "tok-2", "Recordatorio", "Hola mundo")

    assert response.status_code == 200
    request = respx.calls.last.request
    assert request.headers["apns-topic"] == "com.midominio.app"
    assert request.headers["apns-push-type"] == "alert"
    assert json.loads(request.content) == {
        "aps": {"alert": {"title": "Recordatorio", "body": "Hola mundo"}, "sound": "default"}
    }


@respx.mock
async def test_enviar_apns_incluye_deeplink_opaco_fuera_de_aps() -> None:
    _, p8_pem = _par_ec_p8()
    cred = _cred_apns(p8_pem)
    respx.post(f"https://{push._APNS_HOST_PRODUCTION}/3/device/tok-data").mock(
        return_value=httpx.Response(200)
    )

    await push.enviar_apns(
        cred,
        "tok-data",
        "Trabajo terminado",
        "Abre Edecán.",
        data={"route": "activity", "kind": "mission", "resource_id": "abc-123"},
    )

    payload = json.loads(respx.calls.last.request.content)
    assert payload["aps"]["alert"]["title"] == "Trabajo terminado"
    assert payload["route"] == "activity"
    assert payload["kind"] == "mission"
    assert payload["resource_id"] == "abc-123"


@respx.mock
async def test_enviar_apns_sandbox_usa_host_sandbox() -> None:
    _, p8_pem = _par_ec_p8()
    cred = _cred_apns(p8_pem, environment="sandbox")
    ruta = respx.post(f"https://{push._APNS_HOST_SANDBOX}/3/device/tok-sb").mock(
        return_value=httpx.Response(200)
    )

    response = await push.enviar_apns(cred, "tok-sb", "T", "C")

    assert response.status_code == 200
    assert ruta.called


async def test_enviar_apns_sin_pyjwt_lanza_push_no_disponible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`sys.modules["jwt"] = None` es el truco estándar de Python para
    simular "paquete no instalado" (el import system lanza `ImportError` si
    encuentra `None` en `sys.modules`) — prueba que el `try/except
    ImportError` de `_construir_jwt_apns` (import perezoso, ver docstring del
    módulo) funciona de verdad, no solo que está documentado."""
    monkeypatch.setitem(sys.modules, "jwt", None)
    _, p8_pem = _par_ec_p8()
    cred = _cred_apns(p8_pem)

    with pytest.raises(push.PushNoDisponibleError):
        await push.enviar_apns(cred, "tok-x", "T", "C")


# ---------------------------------------------------------------------------
# `enviar_fcm`
# ---------------------------------------------------------------------------


@respx.mock
async def test_enviar_fcm_canjea_token_y_postea_al_project_id_correcto() -> None:
    cred = _cred_fcm(project_id="proyecto-real-123")
    ruta_token = _mock_fcm_token_exchange(access_token="ya29.fake-token")
    ruta_send = respx.post(f"{push._FCM_SEND_BASE_URL}/proyecto-real-123/messages:send").mock(
        return_value=httpx.Response(200, json={"name": "projects/proyecto-real-123/messages/1"})
    )

    response = await push.enviar_fcm(cred, "device-token-fcm", "Título", "Cuerpo")

    assert response.status_code == 200
    assert ruta_token.called
    assert ruta_send.called

    send_request = ruta_send.calls.last.request
    assert send_request.headers["authorization"] == "Bearer ya29.fake-token"
    assert json.loads(send_request.content) == {
        "message": {
            "token": "device-token-fcm",
            "notification": {"title": "Título", "body": "Cuerpo"},
        }
    }


@respx.mock
async def test_enviar_fcm_incluye_data_de_navegacion_como_strings() -> None:
    cred = _cred_fcm(project_id="proyecto-data")
    _mock_fcm_token_exchange()
    route = respx.post(f"{push._FCM_SEND_BASE_URL}/proyecto-data/messages:send").mock(
        return_value=httpx.Response(200, json={})
    )

    await push.enviar_fcm(
        cred,
        "tok",
        "Archivo listo",
        "Abre Edecán.",
        data={"route": "assistant", "kind": "files", "resource_id": "file-1"},
    )

    message = json.loads(route.calls.last.request.content)["message"]
    assert message["data"] == {
        "route": "assistant",
        "kind": "files",
        "resource_id": "file-1",
    }


def test_data_push_rechaza_claves_arbitrarias_y_valores_no_textuales() -> None:
    with pytest.raises(ValueError):
        push._normalizar_data_push({"document_text": "secreto"})
    with pytest.raises(ValueError):
        push._normalizar_data_push({"artifact_id": 123})  # type: ignore[dict-item]


@respx.mock
async def test_enviar_fcm_jwt_bearer_assertion_tiene_iss_scope_y_aud_correctos() -> None:
    cred = _cred_fcm(
        project_id="proyecto-1", client_email="cuenta@proyecto-1.iam.gserviceaccount.com"
    )
    data = json.loads(cred["service_account_json"])
    _mock_fcm_token_exchange()
    respx.post(f"{push._FCM_SEND_BASE_URL}/proyecto-1/messages:send").mock(
        return_value=httpx.Response(200, json={})
    )

    await push.enviar_fcm(cred, "tok", "T", "C")

    token_request = respx.calls[0].request
    form = parse_qs(token_request.content.decode("utf-8"))
    assertion = form["assertion"][0]
    assert form["grant_type"][0] == "urn:ietf:params:oauth:grant-type:jwt-bearer"

    clave_publica = serialization.load_pem_private_key(
        data["private_key"].encode("utf-8"), password=None
    ).public_key()
    payload = jwt.decode(
        assertion, clave_publica, algorithms=["RS256"], audience=push._FCM_TOKEN_URL
    )
    assert payload["iss"] == "cuenta@proyecto-1.iam.gserviceaccount.com"
    assert payload["scope"] == "https://www.googleapis.com/auth/firebase.messaging"
    assert payload["aud"] == push._FCM_TOKEN_URL


@respx.mock
async def test_enviar_fcm_deriva_project_id_del_json_si_no_viene_en_cred() -> None:
    """`cred_fcm` sin `project_id` propio (el router lo resuelve normalmente
    al guardar, pero `enviar_fcm` en sí también sabe derivarlo del JSON como
    red de seguridad)."""
    service_account_json = _service_account_fcm_json(project_id="derivado-del-json")
    cred = {"service_account_json": service_account_json}  # sin "project_id"
    _mock_fcm_token_exchange()
    ruta_send = respx.post(f"{push._FCM_SEND_BASE_URL}/derivado-del-json/messages:send").mock(
        return_value=httpx.Response(200, json={})
    )

    response = await push.enviar_fcm(cred, "tok", "T", "C")

    assert response.status_code == 200
    assert ruta_send.called


# ---------------------------------------------------------------------------
# `cargar_credenciales_push` — fail-closed, nunca lanza.
# ---------------------------------------------------------------------------


async def test_cargar_credenciales_push_sin_connector_account_devuelve_none() -> None:
    session = _FakeSession(respuestas=[[]])
    vault = _FakeVault()

    resultado = await push.cargar_credenciales_push(session, vault, uuid.uuid4())

    assert resultado is None
    assert vault.get_calls == []


async def test_cargar_credenciales_push_bundle_ausente_devuelve_none() -> None:
    account_id = uuid.uuid4()
    session = _FakeSession(respuestas=[[{"id": account_id}]])
    vault = _FakeVault(bundle=None)

    resultado = await push.cargar_credenciales_push(session, vault, uuid.uuid4())

    assert resultado is None
    assert len(vault.get_calls) == 1


async def test_cargar_credenciales_push_json_corrupto_devuelve_none() -> None:
    account_id = uuid.uuid4()
    session = _FakeSession(respuestas=[[{"id": account_id}]])
    vault = _FakeVault(bundle=_FakeBundle(access_token="esto no es json"))

    resultado = await push.cargar_credenciales_push(session, vault, uuid.uuid4())

    assert resultado is None


async def test_cargar_credenciales_push_sesion_lanza_devuelve_none_nunca_revienta() -> None:
    resultado = await push.cargar_credenciales_push(_RaisingSession(), _FakeVault(), uuid.uuid4())
    assert resultado is None


async def test_cargar_credenciales_push_vault_lanza_devuelve_none_nunca_revienta() -> None:
    account_id = uuid.uuid4()
    session = _FakeSession(respuestas=[[{"id": account_id}]])
    vault = _FakeVault(raise_exc=RuntimeError("vault caído"))

    resultado = await push.cargar_credenciales_push(session, vault, uuid.uuid4())

    assert resultado is None


# ---------------------------------------------------------------------------
# `enviar_push_a_usuario` — despacho multi-dispositivo, fail-closed.
# ---------------------------------------------------------------------------


@respx.mock
async def test_enviar_push_a_usuario_sin_credenciales_cero_enviados_sin_ningun_request() -> None:
    """Sin `connector_account` de push -> `cargar_credenciales_push` devuelve
    `None` de inmediato -> nunca se llega a mirar `devices` ni a hacer NINGÚN
    request HTTP. `@respx.mock` sin ninguna ruta registrada ya bloquearía
    cualquier request real que se intentara (respx solo permite tráfico
    explícitamente mockeado); `len(respx.calls) == 0` lo confirma
    explícitamente en vez de confiar solo en que nada reviente."""
    tenant_id = uuid.uuid4()
    session = _FakeSession(respuestas=[[]])  # SELECT connector_accounts -> vacío
    vault = _FakeVault()
    deps = make_deps(session_factory=_session_factory_de(session), vault=lambda s: vault)

    resultado = await push.enviar_push_a_usuario(
        deps, tenant_id=tenant_id, user_id=uuid.uuid4(), titulo="T", cuerpo="C"
    )

    assert resultado == push.ResultadoEnvioPush(0, 0)
    assert len(respx.calls) == 0


@respx.mock
async def test_enviar_push_a_usuario_sin_dispositivos_cero_enviados() -> None:
    account_id = uuid.uuid4()
    config = {"apns": _cred_apns(_par_ec_p8()[1])}
    session = _FakeSession(
        respuestas=[
            [{"id": account_id}],  # SELECT connector_accounts
            [],  # SELECT devices -> ninguno con push_token
        ]
    )
    vault = _FakeVault(bundle=_FakeBundle(access_token=json.dumps(config)))
    deps = make_deps(session_factory=_session_factory_de(session), vault=lambda s: vault)

    resultado = await push.enviar_push_a_usuario(
        deps, tenant_id=uuid.uuid4(), user_id=uuid.uuid4(), titulo="T", cuerpo="C"
    )

    assert resultado == push.ResultadoEnvioPush(0, 0)
    assert len(respx.calls) == 0


@respx.mock
async def test_enviar_push_a_usuario_multi_device_mixto_apns_y_fcm() -> None:
    account_id = uuid.uuid4()
    _, p8_pem = _par_ec_p8()
    config = {
        "apns": _cred_apns(p8_pem, bundle_id="com.acme.app"),
        "fcm": _cred_fcm(project_id="proyecto-mixto"),
    }
    device_apns = _device_row(push_token="tok-apns", push_platform="apns")
    device_fcm = _device_row(push_token="tok-fcm", push_platform="fcm")
    session = _FakeSession(
        respuestas=[
            [{"id": account_id}],
            [device_apns, device_fcm],
        ]
    )
    vault = _FakeVault(bundle=_FakeBundle(access_token=json.dumps(config)))
    deps = make_deps(session_factory=_session_factory_de(session), vault=lambda s: vault)

    respx.post(f"https://{push._APNS_HOST_PRODUCTION}/3/device/tok-apns").mock(
        return_value=httpx.Response(200)
    )
    _mock_fcm_token_exchange()
    respx.post(f"{push._FCM_SEND_BASE_URL}/proyecto-mixto/messages:send").mock(
        return_value=httpx.Response(200, json={})
    )

    resultado = await push.enviar_push_a_usuario(
        deps, tenant_id=uuid.uuid4(), user_id=uuid.uuid4(), titulo="T", cuerpo="C"
    )

    assert resultado == push.ResultadoEnvioPush(enviados=2, fallidos=0)


@respx.mock
async def test_enviar_push_a_usuario_fallo_de_un_device_no_frena_el_otro() -> None:
    account_id = uuid.uuid4()
    _, p8_pem = _par_ec_p8()
    config = {"apns": _cred_apns(p8_pem)}
    device_ok = _device_row(push_token="tok-ok", push_platform="apns")
    device_mal = _device_row(push_token="tok-mal", push_platform="apns")
    session = _FakeSession(respuestas=[[{"id": account_id}], [device_ok, device_mal]])
    vault = _FakeVault(bundle=_FakeBundle(access_token=json.dumps(config)))
    deps = make_deps(session_factory=_session_factory_de(session), vault=lambda s: vault)

    respx.post(f"https://{push._APNS_HOST_PRODUCTION}/3/device/tok-ok").mock(
        return_value=httpx.Response(200)
    )
    respx.post(f"https://{push._APNS_HOST_PRODUCTION}/3/device/tok-mal").mock(
        side_effect=httpx.ConnectError("red caída")
    )

    resultado = await push.enviar_push_a_usuario(
        deps, tenant_id=uuid.uuid4(), user_id=uuid.uuid4(), titulo="T", cuerpo="C"
    )

    assert resultado == push.ResultadoEnvioPush(enviados=1, fallidos=1)


@respx.mock
async def test_enviar_push_a_usuario_device_sin_credencial_conectada_cuenta_como_fallo() -> None:
    """El dispositivo pide `fcm` pero el tenant solo conectó `apns` — no debe
    intentar ningún request, solo contarlo como fallido."""
    account_id = uuid.uuid4()
    _, p8_pem = _par_ec_p8()
    config = {"apns": _cred_apns(p8_pem)}  # sin "fcm"
    device_fcm = _device_row(push_token="tok-fcm", push_platform="fcm")
    session = _FakeSession(respuestas=[[{"id": account_id}], [device_fcm]])
    vault = _FakeVault(bundle=_FakeBundle(access_token=json.dumps(config)))
    deps = make_deps(session_factory=_session_factory_de(session), vault=lambda s: vault)

    resultado = await push.enviar_push_a_usuario(
        deps, tenant_id=uuid.uuid4(), user_id=uuid.uuid4(), titulo="T", cuerpo="C"
    )

    assert resultado == push.ResultadoEnvioPush(enviados=0, fallidos=1)
    assert len(respx.calls) == 0


# ---------------------------------------------------------------------------
# Limpieza de `push_token` ante un status "token inválido" del proveedor —
# ver `_APNS_TOKEN_INVALIDO_STATUSES`/`_FCM_TOKEN_INVALIDO_STATUSES` en push.py.
# ---------------------------------------------------------------------------


@respx.mock
async def test_apns_410_unregistered_limpia_push_token_y_platform_del_device() -> None:
    account_id = uuid.uuid4()
    device_id = uuid.uuid4()
    _, p8_pem = _par_ec_p8()
    config = {"apns": _cred_apns(p8_pem)}
    device = _device_row(id=device_id, push_token="tok-viejo", push_platform="apns")
    session = _FakeSession(
        respuestas=[
            [{"id": account_id}],  # SELECT connector_accounts
            [device],  # SELECT devices
            [],  # UPDATE devices SET push_token = NULL ...
        ]
    )
    vault = _FakeVault(bundle=_FakeBundle(access_token=json.dumps(config)))
    deps = make_deps(session_factory=_session_factory_de(session), vault=lambda s: vault)

    respx.post(f"https://{push._APNS_HOST_PRODUCTION}/3/device/tok-viejo").mock(
        return_value=httpx.Response(410, json={"reason": "Unregistered"})
    )

    resultado = await push.enviar_push_a_usuario(
        deps, tenant_id=uuid.uuid4(), user_id=uuid.uuid4(), titulo="T", cuerpo="C"
    )

    assert resultado == push.ResultadoEnvioPush(enviados=0, fallidos=1)
    sql_update, params_update = session.llamadas[-1]
    assert "UPDATE devices" in sql_update
    assert "push_token = NULL" in sql_update
    assert "push_platform = NULL" in sql_update
    assert params_update["id"] == device_id


@respx.mock
async def test_fcm_404_unregistered_limpia_push_token_del_device() -> None:
    account_id = uuid.uuid4()
    device_id = uuid.uuid4()
    config = {"fcm": _cred_fcm(project_id="proyecto-1")}
    device = _device_row(id=device_id, push_token="tok-fcm-viejo", push_platform="fcm")
    session = _FakeSession(
        respuestas=[[{"id": account_id}], [device], []]
    )
    vault = _FakeVault(bundle=_FakeBundle(access_token=json.dumps(config)))
    deps = make_deps(session_factory=_session_factory_de(session), vault=lambda s: vault)

    _mock_fcm_token_exchange()
    respx.post(f"{push._FCM_SEND_BASE_URL}/proyecto-1/messages:send").mock(
        return_value=httpx.Response(404, json={"error": {"status": "NOT_FOUND"}})
    )

    resultado = await push.enviar_push_a_usuario(
        deps, tenant_id=uuid.uuid4(), user_id=uuid.uuid4(), titulo="T", cuerpo="C"
    )

    assert resultado == push.ResultadoEnvioPush(enviados=0, fallidos=1)
    sql_update, params_update = session.llamadas[-1]
    assert "UPDATE devices" in sql_update
    assert params_update["id"] == device_id


@respx.mock
async def test_apns_500_no_limpia_el_token_solo_cuenta_fallo() -> None:
    """Un error transitorio (500) NO debe limpiar el token — solo los status
    en `_APNS_TOKEN_INVALIDO_STATUSES` lo hacen."""
    account_id = uuid.uuid4()
    _, p8_pem = _par_ec_p8()
    config = {"apns": _cred_apns(p8_pem)}
    device = _device_row(push_token="tok-500", push_platform="apns")
    session = _FakeSession(respuestas=[[{"id": account_id}], [device]])
    vault = _FakeVault(bundle=_FakeBundle(access_token=json.dumps(config)))
    deps = make_deps(session_factory=_session_factory_de(session), vault=lambda s: vault)

    respx.post(f"https://{push._APNS_HOST_PRODUCTION}/3/device/tok-500").mock(
        return_value=httpx.Response(500)
    )

    resultado = await push.enviar_push_a_usuario(
        deps, tenant_id=uuid.uuid4(), user_id=uuid.uuid4(), titulo="T", cuerpo="C"
    )

    assert resultado == push.ResultadoEnvioPush(enviados=0, fallidos=1)
    # Solo 2 llamadas de sesión (SELECT connector_accounts, SELECT devices) —
    # ningún UPDATE de limpieza.
    assert len(session.llamadas) == 2
