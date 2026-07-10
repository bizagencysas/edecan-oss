"""`POST /v1/companion/pair-code` y `WS /v1/companion/ws` (ARCHITECTURE.md §10.12).

`companion_ws` llama `get_redis(get_settings())` directo en vez de recibirlos
por `Depends(...)` (ver el comentario del propio router: "son funciones
normales, así que se llaman directo en vez de pasar por Depends(...)"), así
que `app.dependency_overrides` no lo alcanza. Para que estos tests corran
offline y deterministas (sin Redis real), se sustituyen esos dos símbolos ya
importados en `edecan_api.routers.companion` con `monkeypatch.setattr(...)`
-- mismo patrón que usa `test_conversations.py` para `Agent`.

El WebSocket en sí se prueba con `starlette.testclient.TestClient` (soporta
`websocket_connect`, a diferencia del `httpx.AsyncClient`+`ASGITransport` que
usa el resto de la suite vía el fixture `client`).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from conftest import auth_headers
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from edecan_api.companion_manager import CompanionError, ConnectionManager


async def test_create_pair_code_stores_tenant_in_redis(client, fake_redis) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")

    response = await client.post("/v1/companion/pair-code", headers=headers)

    assert response.status_code == 200
    code = response.json()["code"]
    assert len(code) == 8
    stored = await fake_redis.get(f"pair:{code}")
    assert stored == str(tenant_id)


async def test_create_pair_code_requires_authentication(client) -> None:
    response = await client.post("/v1/companion/pair-code")
    assert response.status_code == 401


def test_ws_connect_with_invalid_pair_code_closes_with_4401(
    app, fake_redis, test_settings, monkeypatch
) -> None:
    import edecan_api.routers.companion as companion_module

    monkeypatch.setattr(companion_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(companion_module, "get_redis", lambda settings: fake_redis)

    with TestClient(app) as test_client:
        try:
            with test_client.websocket_connect("/v1/companion/ws?code=NOEXISTE"):
                raise AssertionError("no debería aceptar la conexión con un pair-code inválido")
        except WebSocketDisconnect as exc:
            assert exc.code == 4401


def test_ws_connect_with_valid_pair_code_registers_in_manager_and_consumes_code(
    app, fake_redis, test_settings, monkeypatch
) -> None:
    import edecan_api.routers.companion as companion_module

    monkeypatch.setattr(companion_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(companion_module, "get_redis", lambda settings: fake_redis)

    tenant_id = uuid.uuid4()
    code = "ABCD2345"
    asyncio.run(fake_redis.set(f"pair:{code}", str(tenant_id), ex=600))

    manager = app.state.companion_manager
    assert manager.is_connected(tenant_id) is False

    with TestClient(app) as test_client:
        with test_client.websocket_connect(f"/v1/companion/ws?code={code}"):
            assert manager.is_connected(tenant_id) is True
            # El pair-code es de un solo uso: se borra de Redis al conectar.
            assert asyncio.run(fake_redis.get(f"pair:{code}")) is None

    assert manager.is_connected(tenant_id) is False


def test_ws_reused_pair_code_is_rejected_the_second_time(
    app, fake_redis, test_settings, monkeypatch
) -> None:
    import edecan_api.routers.companion as companion_module

    monkeypatch.setattr(companion_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(companion_module, "get_redis", lambda settings: fake_redis)

    tenant_id = uuid.uuid4()
    code = "WXYZ6789"
    asyncio.run(fake_redis.set(f"pair:{code}", str(tenant_id), ex=600))

    with TestClient(app) as test_client:
        with test_client.websocket_connect(f"/v1/companion/ws?code={code}"):
            pass

        try:
            with test_client.websocket_connect(f"/v1/companion/ws?code={code}"):
                raise AssertionError("un pair-code ya consumido no debería volver a servir")
        except WebSocketDisconnect as exc:
            assert exc.code == 4401


def test_ws_pairing_attempts_are_rate_limited_per_ip(
    app, fake_redis, test_settings, monkeypatch
) -> None:
    """Adivinar el pair-code a fuerza bruta contra el handshake WS debe quedar
    acotado en velocidad, igual que cualquier otro endpoint con credenciales
    de este código base (`deps.rate_limit`) -- aunque acá no hay JWT todavía
    para indexar por `tenant_id`, así que se indexa por IP de origen."""
    import edecan_api.routers.companion as companion_module

    monkeypatch.setattr(companion_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(companion_module, "get_redis", lambda settings: fake_redis)

    with TestClient(app) as test_client:
        # Agota el cupo de la ventana con intentos con código inválido: cada
        # uno cierra con 4401 (código inválido), no todavía por rate limit.
        for _ in range(companion_module.PAIR_WS_RATE_LIMIT_MAX_ATTEMPTS):
            try:
                with test_client.websocket_connect("/v1/companion/ws?code=NOEXISTE"):
                    raise AssertionError("no debería aceptar la conexión con código inválido")
            except WebSocketDisconnect as exc:
                assert exc.code == 4401

        # El siguiente intento desde la misma IP, dentro de la misma ventana,
        # se corta por rate limit -- ya ni siquiera llega a mirar Redis por el
        # código (que además ahora sí sería válido, para probar que el límite
        # de intentos corta antes que la validación del código).
        tenant_id = uuid.uuid4()
        code = "LIMITE99"
        asyncio.run(fake_redis.set(f"pair:{code}", str(tenant_id), ex=600))

        try:
            with test_client.websocket_connect(f"/v1/companion/ws?code={code}"):
                raise AssertionError("debería cortar por rate limit antes de validar el código")
        except WebSocketDisconnect as exc:
            assert exc.code == companion_module.WS_CLOSE_RATE_LIMITED

        # El pair-code válido sigue intacto en Redis: el rechazo fue por rate
        # limit, no se llegó a consumir.
        assert asyncio.run(fake_redis.get(f"pair:{code}")) == str(tenant_id)


# -- ConnectionManager.send_command / .handle_incoming -----------------------
#
# Los tests de arriba cubren el router (`companion.py`): pairing + connect/
# disconnect vía un WebSocket real de `TestClient`. Lo que sigue prueba, a
# nivel unitario y sin pasar por HTTP/WS, el protocolo request/response que
# `send_command` y `handle_incoming` implementan sobre `ConnectionManager`
# (companion_manager.py) -- el mismo que `_companion_caller`
# (routers/conversations.py) inyecta en `ToolContext.extras["companion"]`
# para que la tool `usar_computadora` pueda pedirle acciones al companion
# (docs/api.md, sección "Companion de escritorio").


class _FakeCompanionWebSocket:
    """Doble mínimo de `starlette.websockets.WebSocket`: implementa sólo los
    dos métodos que `ConnectionManager` le toca (`accept` desde `.connect`,
    `send_json` desde `.send_command`) -- no hace falta un WebSocket real
    para ejercitar el protocolo request/response en memoria."""

    def __init__(self) -> None:
        self.accepted = False
        self.sent: list[dict] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)


async def test_send_command_raises_companion_error_when_tenant_has_no_companion() -> None:
    manager = ConnectionManager()

    with pytest.raises(CompanionError):
        await manager.send_command(uuid.uuid4(), "mover_mouse", {"x": 1, "y": 2})


async def test_send_command_sends_request_and_resolves_with_the_companion_response() -> None:
    manager = ConnectionManager()
    tenant_id = uuid.uuid4()
    fake_ws = _FakeCompanionWebSocket()
    await manager.connect(tenant_id, fake_ws)

    task = asyncio.create_task(
        manager.send_command(tenant_id, "mover_mouse", {"x": 10, "y": 20}, timeout=5)
    )
    # `send_command` arma el `Future` pendiente, manda el JSON por el
    # WebSocket y recién ahí se suspende esperando la respuesta -- un solo
    # tick del loop alcanza para que llegue hasta ahí, y sólo entonces se
    # conoce el `request_id` (uuid4 generado adentro) para poder simular la
    # respuesta del companion.
    await asyncio.sleep(0)

    assert len(fake_ws.sent) == 1
    request_id = fake_ws.sent[0]["request_id"]
    assert fake_ws.sent[0] == {
        "request_id": request_id,
        "action": "mover_mouse",
        "params": {"x": 10, "y": 20},
    }

    response = {"request_id": request_id, "ok": True, "result": "listo"}
    await manager.handle_incoming(tenant_id, response)

    assert await task == response


async def test_send_command_raises_companion_error_on_timeout() -> None:
    manager = ConnectionManager()
    tenant_id = uuid.uuid4()
    await manager.connect(tenant_id, _FakeCompanionWebSocket())

    # El companion nunca contesta (no se llama `handle_incoming`): debe
    # cortar por timeout en vez de colgarse.
    with pytest.raises(CompanionError):
        await manager.send_command(tenant_id, "mover_mouse", {}, timeout=0.01)


async def test_handle_incoming_ignores_messages_without_a_matching_pending_request() -> None:
    """Un heartbeat del companion (sin `request_id`) o una respuesta cuyo
    `request_id` ya no está pendiente (nunca existió, o ya se resolvió) no
    debe explotar: `handle_incoming` la ignora en silencio."""
    manager = ConnectionManager()
    tenant_id = uuid.uuid4()

    await manager.handle_incoming(tenant_id, {"type": "heartbeat"})
    await manager.handle_incoming(tenant_id, {"request_id": str(uuid.uuid4()), "ok": True})


async def test_handle_incoming_rejects_response_tagged_with_a_different_tenant() -> None:
    """Aislamiento entre tenants de `handle_incoming` (companion_manager.py:76):
    un companion no debe poder resolver -- ni con datos falsos -- una
    petición pendiente de OTRO tenant."""
    manager = ConnectionManager()
    owner_tenant_id = uuid.uuid4()
    other_tenant_id = uuid.uuid4()
    fake_ws = _FakeCompanionWebSocket()
    await manager.connect(owner_tenant_id, fake_ws)

    task = asyncio.create_task(
        manager.send_command(owner_tenant_id, "leer_pantalla", {}, timeout=5)
    )
    await asyncio.sleep(0)
    request_id = fake_ws.sent[0]["request_id"]

    # Un companion conectado como OTRO tenant intenta resolver la petición
    # ajena (p. ej. adivinando o reutilizando su `request_id`).
    await manager.handle_incoming(
        other_tenant_id, {"request_id": request_id, "ok": True, "result": "dato ajeno"}
    )
    assert task.done() is False

    # La respuesta legítima del dueño sigue pudiendo resolverla: el intento
    # ajeno no la consumió ni la corrompió.
    legit_response = {"request_id": request_id, "ok": True, "result": "dato real"}
    await manager.handle_incoming(owner_tenant_id, legit_response)
    assert await task == legit_response
