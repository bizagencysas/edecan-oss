"""Recorrido contractual de un usuario nuevo a través de las superficies
web/API críticas, sin infraestructura externa ni credenciales reales."""

from __future__ import annotations


class _FakeS3Client:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info) -> None:
        return None

    async def put_object(self, **kwargs) -> None:
        assert kwargs["Bucket"] == "edecan-files"


class _FakeS3Session:
    def client(self, service_name: str, **kwargs):
        assert service_name == "s3"
        return _FakeS3Client()


async def test_new_user_can_auth_chat_upload_refresh_and_logout(
    app, client, fake_redis, monkeypatch
) -> None:
    import edecan_api.routers.conversations as conversations_module
    import edecan_api.routers.files as files_module
    from edecan_api.deps import get_platform_session, get_redis

    class ReadySession:
        async def execute(self, statement):
            assert str(statement) == "SELECT 1"

    app.dependency_overrides[get_platform_session] = lambda: ReadySession()
    app.dependency_overrides[get_redis] = lambda: fake_redis

    assert (await client.get("/healthz")).json() == {"status": "ok"}
    ready = await client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ok"}

    registered = await client.post(
        "/v1/auth/register",
        json={
            "email": "journey@example.com",
            "password": "journey-password-123",
            "tenant_name": "Journey Tenant",
        },
    )
    assert registered.status_code == 201
    registration_refresh = registered.json()["refresh_token"]

    # El recorrido no se limita al atajo de sesión que devuelve register:
    # revoca ese refresh y prueba también el ingreso normal de un usuario que
    # vuelve a la aplicación.
    registration_logout = await client.post(
        "/v1/auth/logout", json={"refresh_token": registration_refresh}
    )
    assert registration_logout.status_code == 200
    logged_in = await client.post(
        "/v1/auth/login",
        json={
            "email": "journey@example.com",
            "password": "journey-password-123",
        },
    )
    assert logged_in.status_code == 200
    access_token = logged_in.json()["access_token"]
    refresh_token = logged_in.json()["refresh_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    me = await client.get("/v1/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "journey@example.com"

    setup = await client.get("/v1/setup/status", headers=headers)
    assert setup.status_code == 200
    assert setup.json()["onboarding_completed"] is False

    class ScriptedAgent:
        def __init__(self, llm_router, registry) -> None:
            pass

        async def run_turn(self, *, user_text, **kwargs):
            assert user_text == "Hola desde cero"
            yield {"type": "text_delta", "text": "Hola, usuario nuevo"}
            yield {"type": "done", "usage": {"input_tokens": 3, "output_tokens": 4}}

    monkeypatch.setattr(conversations_module, "Agent", ScriptedAgent)

    conversation = await client.post("/v1/conversations", json={}, headers=headers)
    assert conversation.status_code == 201
    conversation_id = conversation.json()["id"]
    turn = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": "Hola desde cero"},
        headers=headers,
    )
    assert turn.status_code == 200
    assert "event: message.delta" in turn.text
    assert "event: message.done" in turn.text

    monkeypatch.setattr(files_module.aioboto3, "Session", _FakeS3Session)

    async def fake_enqueue(*args, **kwargs):
        return None

    monkeypatch.setattr(files_module, "enqueue", fake_enqueue)
    uploaded = await client.post(
        "/v1/files",
        files={"file": ("journey.txt", b"contenido", "text/plain")},
        headers=headers,
    )
    assert uploaded.status_code == 201
    assert uploaded.json()["status"] == "uploaded"

    rotated = await client.post(
        "/v1/auth/refresh", json={"refresh_token": refresh_token}
    )
    assert rotated.status_code == 200
    rotated_headers = {"Authorization": f"Bearer {rotated.json()['access_token']}"}
    listed = await client.get("/v1/files", headers=rotated_headers)
    assert [item["filename"] for item in listed.json()] == ["journey.txt"]

    rotated_refresh = rotated.json()["refresh_token"]
    logged_out = await client.post(
        "/v1/auth/logout", json={"refresh_token": rotated_refresh}
    )
    assert logged_out.status_code == 200
    assert logged_out.json() == {"revoked": True}
    denied = await client.post(
        "/v1/auth/refresh", json={"refresh_token": rotated_refresh}
    )
    assert denied.status_code == 401
