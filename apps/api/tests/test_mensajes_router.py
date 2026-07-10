"""`edecan_api.routers.mensajes` — `/v1/mensajes/*` (bandeja de mensajería unificada en la
web, `ARCHITECTURE.md` §13, WP-V4-11; ver el docstring del propio router para el contrato
completo y `docs/mensajeria.md` "Bandeja unificada (web)").

Mismo patrón que `test_smarthome_router.py`/`test_devices_router.py`/`test_erp_router.py`:
`client`/`app`/`fake_repo`/`auth_headers` de `conftest.py`, un `FakeSession` LOCAL
(`FakeSession`/`FakeResult`, duplicado a propósito — `ARCHITECTURE.md` §10.1, "los tests no
importan paquetes hermanos") que responde a la ÚNICA consulta SQL que
`edecan_messaging._creds.resolver_credenciales` emite (buscar la `connector_account` más
reciente para `(tenant_id, connector_key)` — una entrada en `respuestas` por llamada, en el
ORDEN exacto en que el router la pide), y un `FakeVault` LOCAL (`put`/`get` en memoria, mismo
patrón que `test_smarthome_router.py`) para las credenciales resueltas. Los clientes HTTP
reales de cada plataforma (`edecan_messaging.clients`/`.whatsapp`) SÍ hablan por `httpx` de
verdad — se interceptan con `@respx.mock` en TODOS los tests (incluso los que no esperan
tráfico real), mismo criterio que `test_smarthome_router.py`: con `assert_all_mocked=True` de
fábrica, cualquier llamada HTTP no interceptada explícitamente hace fallar el test en vez de
pegarle a la red real.

`edecan_api.main.create_app()` YA monta `mensajes.router` de forma defensiva
(`V4_ROUTER_NAMES`, ver `test_v4_mounting.py`) — este archivo, igual que
`test_devices_router.py`/`test_erp_router.py`, lo monta manualmente sobre la `app` de
`conftest.py` para no depender de ese mecanismo.

`connectors.messaging` (`FLAG_CONNECTORS_MESSAGING`) YA está en `True` en los 4 planes de
`edecan_schemas.plans.PLANES` hoy (a diferencia de `erp.inventory` cuando se escribió
`test_erp_router.py`) — así que el camino feliz usa `auth_headers` normal con cualquier
`plan_key` real, y SOLO los tests de "flag apagado" necesitan un `CurrentUser` fabricado a
mano vía `client_flag_apagado` (mismo truco que `test_erp_router.py`, en dirección opuesta:
aquí se fuerza el flag a `False`, no a `True`).

Nunca se toca `edecan_api/repo.py`, `conftest.py` ni `api_fakes.py` (regla dura del paquete de
trabajo) — ni tampoco `packages/messaging/` (paquete estable de otro dueño, se consume tal cual).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
import respx
from conftest import auth_headers
from edecan_schemas import TokenBundle
from edecan_schemas.plans import FLAG_CONNECTORS_MESSAGING
from httpx import ASGITransport, AsyncClient

from edecan_api import deps as edecan_deps
from edecan_api.routers import mensajes

TELEGRAM_TOKEN = "123:ABC-telegram-token-de-prueba"
SLACK_TOKEN = "xoxb-slack-token-de-prueba"


# ---------------------------------------------------------------------------
# Dobles locales
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
    """Responde, en orden, a cada `SELECT id FROM connector_accounts ...` que emite
    `resolver_credenciales`: `[]` = plataforma sin conectar, `[{"id": ...}]` = conectada."""

    respuestas: list[list[dict[str, Any]]] = field(default_factory=list)
    llamadas: list[dict[str, Any]] = field(default_factory=list)

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> FakeResult:
        self.llamadas.append(dict(params or {}))
        filas = self.respuestas.pop(0) if self.respuestas else []
        return FakeResult(filas)


class FakeVault:
    """`put`/`get` en memoria (mismo patrón que `test_smarthome_router.py::FakeVault`)."""

    def __init__(self) -> None:
        self._store: dict[tuple[uuid.UUID, uuid.UUID], TokenBundle] = {}

    def seed(self, tenant_id: uuid.UUID, account_id: uuid.UUID, bundle: TokenBundle) -> None:
        self._store[(tenant_id, account_id)] = bundle

    async def put(self, tenant_id: uuid.UUID, account_id: uuid.UUID, bundle: TokenBundle) -> None:
        self._store[(tenant_id, account_id)] = bundle

    async def get(self, tenant_id: uuid.UUID, account_id: uuid.UUID) -> TokenBundle | None:
        return self._store.get((tenant_id, account_id))


def _queue_conectado(
    fake_session: FakeSession,
    fake_vault: FakeVault,
    *,
    tenant_id: uuid.UUID,
    access_token: str,
    scopes: tuple[str, ...] = (),
) -> uuid.UUID:
    """Encola LA SIGUIENTE llamada a `resolver_credenciales` como "conectada" — llamar
    justo antes de disparar la request que la consume (el orden importa, ver docstring)."""
    account_id = uuid.uuid4()
    fake_session.respuestas.append([{"id": account_id}])
    bundle = TokenBundle(access_token=access_token, scopes=list(scopes))
    fake_vault.seed(tenant_id, account_id, bundle)
    return account_id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def fake_vault() -> FakeVault:
    return FakeVault()


@pytest.fixture
def _mounted_app(app, fake_session: FakeSession, fake_vault: FakeVault):
    app.include_router(mensajes.router)
    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: fake_session
    app.dependency_overrides[edecan_deps.get_vault] = lambda: fake_vault
    return app


@pytest.fixture
async def client(_mounted_app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=_mounted_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def client_flag_apagado(_mounted_app) -> AsyncIterator[AsyncClient]:
    """Como `client`, pero con `get_current_user` reemplazado por un `CurrentUser` con
    `connectors.messaging=False` — ningún `plan_key` real de `PLANES` produce eso hoy (el
    flag está en `True` en los 4 planes), así que hace falta fabricarlo a mano (mismo truco
    que `test_erp_router.py::client_flag`, en dirección opuesta)."""
    usuario = edecan_deps.CurrentUser(
        user_id=uuid.uuid4(),
        tenant=edecan_deps.TenantCtx(
            tenant_id=uuid.uuid4(),
            plan_key="hosted_pro",
            flags={FLAG_CONNECTORS_MESSAGING: False},
        ),
    )
    _mounted_app.dependency_overrides[edecan_deps.get_current_user] = lambda: usuario
    transport = ASGITransport(app=_mounted_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# GET /canales
# ---------------------------------------------------------------------------


@respx.mock
async def test_canales_requires_authentication(client) -> None:
    response = await client.get("/v1/mensajes/canales")
    assert response.status_code == 401


@respx.mock
async def test_canales_flag_apagado_403(client_flag_apagado) -> None:
    response = await client_flag_apagado.get("/v1/mensajes/canales")
    assert response.status_code == 403


@respx.mock
async def test_canales_conectados_y_desconectados(client, fake_session, fake_vault) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_pro")

    telegram_id = uuid.uuid4()
    slack_id = uuid.uuid4()
    # Orden EXACTO de `edecan_messaging._creds.CONNECTOR_KEYS`: telegram, discord, slack,
    # whatsapp — una entrada de `respuestas` por plataforma, en ese orden.
    fake_session.respuestas = [
        [{"id": telegram_id}],  # telegram: conectado
        [],  # discord: sin conectar
        [{"id": slack_id}],  # slack: conectado
        [],  # whatsapp: sin conectar
    ]
    fake_vault.seed(tenant_id, telegram_id, TokenBundle(access_token=TELEGRAM_TOKEN))
    fake_vault.seed(tenant_id, slack_id, TokenBundle(access_token=SLACK_TOKEN))

    response = await client.get("/v1/mensajes/canales", headers=headers)

    assert response.status_code == 200
    body = {fila["canal"]: fila for fila in response.json()}
    assert body["telegram"] == {"canal": "telegram", "conectado": True, "puede_leer": True}
    assert body["discord"] == {"canal": "discord", "conectado": False, "puede_leer": True}
    assert body["slack"] == {"canal": "slack", "conectado": True, "puede_leer": True}
    # WhatsApp: nunca puede leer, esté o no conectado (ver docstring del router).
    assert body["whatsapp"] == {"canal": "whatsapp", "conectado": False, "puede_leer": False}


# ---------------------------------------------------------------------------
# GET "" — leer mensajes
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_requires_authentication(client) -> None:
    response = await client.get("/v1/mensajes", params={"canal": "telegram"})
    assert response.status_code == 401


@respx.mock
async def test_get_canal_no_soportado_400(client, fake_session) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    response = await client.get("/v1/mensajes", params={"canal": "signal"}, headers=headers)
    assert response.status_code == 400
    assert fake_session.llamadas == []  # nunca llega a resolver credenciales


@respx.mock
async def test_get_whatsapp_no_soporta_lectura_400(client, fake_session) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    response = await client.get("/v1/mensajes", params={"canal": "whatsapp"}, headers=headers)
    assert response.status_code == 400
    assert "webhook" in response.json()["detail"].lower()
    assert fake_session.llamadas == []


@respx.mock
async def test_get_credencial_faltante_400_mensaje_accionable(client, fake_session) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    fake_session.respuestas.append([])  # sin connector_account para telegram

    response = await client.get("/v1/mensajes", params={"canal": "telegram"}, headers=headers)

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "telegram" in detail.lower()
    assert "/app/conectores" in detail


@respx.mock
async def test_get_slack_requiere_origen(client, fake_session, fake_vault) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id)
    _queue_conectado(fake_session, fake_vault, tenant_id=tenant_id, access_token=SLACK_TOKEN)

    response = await client.get("/v1/mensajes", params={"canal": "slack"}, headers=headers)

    assert response.status_code == 400
    assert "origen" in response.json()["detail"].lower()


@respx.mock
async def test_get_lectura_feliz_telegram_sin_origen(client, fake_session, fake_vault) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id)
    _queue_conectado(fake_session, fake_vault, tenant_id=tenant_id, access_token=TELEGRAM_TOKEN)
    respx.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "result": [
                    {
                        "update_id": 1,
                        "message": {
                            "from": {"first_name": "Ana"},
                            "chat": {"id": 555},
                            "date": 1735689600,
                            "text": "hola equipo",
                        },
                    }
                ],
            },
        )
    )

    response = await client.get("/v1/mensajes", params={"canal": "telegram"}, headers=headers)

    assert response.status_code == 200
    cuerpo = response.json()
    assert cuerpo == [
        {
            "canal": "telegram",
            "remitente": "Ana",
            "texto": "hola equipo",
            "fecha": "1735689600",
            "chat_id": "555",
        }
    ]


@respx.mock
async def test_get_lectura_feliz_slack_con_origen(client, fake_session, fake_vault) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id)
    _queue_conectado(fake_session, fake_vault, tenant_id=tenant_id, access_token=SLACK_TOKEN)
    respx.get("https://slack.com/api/conversations.history").mock(
        return_value=httpx.Response(
            200, json={"ok": True, "messages": [{"user": "U1", "text": "hola desde slack"}]}
        )
    )

    response = await client.get(
        "/v1/mensajes", params={"canal": "slack", "origen": "C123"}, headers=headers
    )

    assert response.status_code == 200
    cuerpo = response.json()
    assert cuerpo == [
        {
            "canal": "slack",
            "remitente": "U1",
            "texto": "hola desde slack",
            "fecha": "",
            "chat_id": "C123",
        }
    ]


@respx.mock
async def test_get_cliente_falla_400(client, fake_session, fake_vault) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id)
    _queue_conectado(fake_session, fake_vault, tenant_id=tenant_id, access_token=TELEGRAM_TOKEN)
    respx.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates").mock(
        return_value=httpx.Response(200, json={"ok": False, "description": "Unauthorized"})
    )

    response = await client.get("/v1/mensajes", params={"canal": "telegram"}, headers=headers)

    assert response.status_code == 400


@respx.mock
async def test_get_flag_apagado_403(client_flag_apagado, fake_session) -> None:
    response = await client_flag_apagado.get("/v1/mensajes", params={"canal": "telegram"})
    assert response.status_code == 403
    assert fake_session.llamadas == []


# ---------------------------------------------------------------------------
# POST /enviar
# ---------------------------------------------------------------------------


@respx.mock
async def test_enviar_requires_authentication(client) -> None:
    response = await client.post(
        "/v1/mensajes/enviar", json={"canal": "telegram", "destinatario": "555", "texto": "hola"}
    )
    assert response.status_code == 401


@respx.mock
async def test_enviar_canal_no_soportado_400(client, fake_session) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    response = await client.post(
        "/v1/mensajes/enviar",
        json={"canal": "signal", "destinatario": "555", "texto": "hola"},
        headers=headers,
    )
    assert response.status_code == 400
    assert fake_session.llamadas == []


@respx.mock
async def test_enviar_credencial_faltante_400_mensaje_accionable(client, fake_session) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    fake_session.respuestas.append([])

    response = await client.post(
        "/v1/mensajes/enviar",
        json={"canal": "telegram", "destinatario": "555", "texto": "hola"},
        headers=headers,
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "telegram" in detail.lower()
    assert "/app/conectores" in detail


@respx.mock
async def test_enviar_texto_vacio_422(client, fake_session) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    response = await client.post(
        "/v1/mensajes/enviar",
        json={"canal": "telegram", "destinatario": "555", "texto": "   "},
        headers=headers,
    )
    assert response.status_code == 422
    assert fake_session.llamadas == []


@respx.mock
async def test_enviar_feliz_registra_audit(client, fake_repo, fake_session, fake_vault) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key="hosted_pro")
    _queue_conectado(fake_session, fake_vault, tenant_id=tenant_id, access_token=TELEGRAM_TOKEN)
    ruta = respx.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})
    )

    response = await client.post(
        "/v1/mensajes/enviar",
        json={"canal": "telegram", "destinatario": "555", "texto": "hola equipo"},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "canal": "telegram",
        "destinatario": "555",
        "resultado": {"message_id": 7},
    }
    assert ruta.called

    entrada = next(e for e in fake_repo.audit_log if e["action"] == "mensajes.enviado")
    assert entrada["tenant_id"] == tenant_id
    assert entrada["actor_user_id"] == user_id
    assert entrada["target"] == "telegram:555"
    assert entrada["meta"] == {"preview": "hola equipo"}


@respx.mock
async def test_enviar_whatsapp_fuera_de_ventana_400_sugiere_plantilla(
    client, fake_session, fake_vault
) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id)
    phone_number_id = "109876543210987"
    _queue_conectado(
        fake_session,
        fake_vault,
        tenant_id=tenant_id,
        access_token="token-whatsapp-de-prueba",
        scopes=(phone_number_id,),
    )
    respx.post(f"https://graph.facebook.com/v21.0/{phone_number_id}/messages").mock(
        return_value=httpx.Response(
            400,
            json={"error": {"code": 131047, "message": "Re-engagement message"}},
        )
    )

    response = await client.post(
        "/v1/mensajes/enviar",
        json={"canal": "whatsapp", "destinatario": "+525512345678", "texto": "hola"},
        headers=headers,
    )

    assert response.status_code == 400
    assert "plantilla" in response.json()["detail"].lower()


@respx.mock
async def test_enviar_flag_apagado_403_no_toca_la_sesion(client_flag_apagado, fake_session) -> None:
    response = await client_flag_apagado.post(
        "/v1/mensajes/enviar",
        json={"canal": "telegram", "destinatario": "555", "texto": "hola"},
    )
    assert response.status_code == 403
    assert fake_session.llamadas == []
