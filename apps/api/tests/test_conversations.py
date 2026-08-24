"""`POST /v1/conversations/{id}/messages` — smoke SSE con `Agent` monkeypatched,
y cuota diaria de mensajes -> 429 (ARCHITECTURE.md §10.12, §10.7, §10.13)."""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from datetime import UTC, datetime

import pytest
from conftest import auth_headers
from edecan_schemas import ArtifactRef, ToolEndEvent


class _VisionBody:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    async def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            return self.payload
        return self.payload[:size]


class _VisionS3Client:
    def __init__(self, objects: dict[str, bytes | Exception]) -> None:
        self.objects = objects

    async def __aenter__(self) -> _VisionS3Client:
        return self

    async def __aexit__(self, *exc_info) -> None:
        return None

    async def get_object(self, *, Bucket: str, Key: str) -> dict:  # noqa: N803
        assert Bucket == "edecan-files"
        payload = self.objects[Key]
        if isinstance(payload, Exception):
            raise payload
        return {"Body": _VisionBody(payload)}


class _VisionS3Session:
    def __init__(self, objects: dict[str, bytes | Exception]) -> None:
        self.objects = objects

    def client(self, service_name: str, **kwargs) -> _VisionS3Client:
        assert service_name == "s3"
        return _VisionS3Client(self.objects)


def test_automatic_title_summarizes_api_setup_without_copying_the_message() -> None:
    import edecan_api.routers.conversations as conversations_module

    title = conversations_module._automatic_conversation_title(
        "Configura la API key de X es [credencial protegida]. Luego pruébala."
    )

    assert title == "Configurar API Key - X"
    assert "credencial" not in title.lower()


def test_automatic_title_is_a_short_intent_instead_of_the_opening_sentence() -> None:
    import edecan_api.routers.conversations as conversations_module

    assert (
        conversations_module._automatic_conversation_title(
            "Que pasaría si yo empezara a hacer 15 flexiones diarias con 15"
        )
        == "Hacer 15 flexiones diarias"
    )
    assert (
        conversations_module._automatic_conversation_title(
            "How do I explain a friend in one paragraph who you are and what you make"
        )
        == "Explicar quién es Edecán"
    )
    assert (
        conversations_module._automatic_conversation_title(
            "Envíame una notificación push de prueba a mi iOS."
        )
        == "Prueba de notificación push"
    )
    assert (
        conversations_module._automatic_conversation_title(
            "Búscame vuelos de Medellín a Bogotá el 31 de julio para dos personas."
        )
        == "Vuelos Medellín a Bogotá"
    )


async def test_semantic_title_uses_fast_model_when_fallback_is_still_a_copy() -> None:
    import edecan_api.routers.conversations as conversations_module

    class Response:
        text = "Organizar lanzamiento del producto"

    class Router:
        async def complete(self, alias, flags, request):
            assert alias == "rapido"
            assert flags == {}
            assert request.metadata["task"] == "conversation_title"
            assert request.max_tokens == 256
            return Response()

    message = "Ayúdame con todo lo necesario para el nuevo lanzamiento que tengo en mente"
    fallback = conversations_module._automatic_conversation_title(message)

    title = await conversations_module._semantic_conversation_title(
        Router(),
        message,
        fallback=fallback,
    )

    assert title == "Organizar lanzamiento del producto"


def test_workers_ai_settings_without_retired_openai_fields_keep_memory_available() -> None:
    from edecan_core.memory.embedders import HashEmbedder

    import edecan_api.routers.conversations as conversations_module
    from edecan_api.config import Settings

    settings = Settings(
        CLOUDFLARE_ACCOUNT_ID="cloudflare-account",
        CLOUDFLARE_API_TOKEN="cloudflare-token",
        EMBEDDINGS_MODEL=None,
        EMBEDDINGS_DIM=32,
    )

    assert conversations_module._has_real_embeddings_provider(settings) is False
    assert isinstance(conversations_module._build_embedder(settings), HashEmbedder)


def test_tool_end_with_artifact_is_json_serializable_for_history() -> None:
    import edecan_api.routers.conversations as conversations_module

    file_id = uuid.uuid4()
    mission_id = uuid.uuid4()
    event = ToolEndEvent(
        name="crear_artefactos",
        result_preview="Creado",
        artifacts=[ArtifactRef(file_id=file_id, filename="reporte.pdf", mime="application/pdf")],
        mission_id=mission_id,
    )

    serialized = conversations_module._event_to_dict(event)

    assert serialized["artifacts"][0]["file_id"] == str(file_id)
    assert serialized["mission_id"] == str(mission_id)
    json.dumps(serialized)  # regresión: antes lanzaba UUID is not JSON serializable


async def test_design_tool_end_enqueues_uuid_only_notification(monkeypatch) -> None:
    import edecan_api.routers.conversations as conversations_module

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    artifact_id = uuid.uuid4()
    queued = []

    async def fake_enqueue(settings, job_type, payload, queued_tenant_id):  # noqa: ANN001
        queued.append((job_type, payload, queued_tenant_id))
        return uuid.uuid4()

    monkeypatch.setattr(conversations_module, "enqueue", fake_enqueue)
    await conversations_module._enqueue_tool_notification(
        settings=object(),
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation_id,
        event={
            "type": "tool_end",
            "tool_call_id": "provider-free-text-id",
            "name": "crear_diseno_visual",
            "result_preview": "Diseño listo",
            "artifacts": [{"file_id": str(artifact_id), "filename": "privado.html"}],
        },
    )

    assert queued == [
        (
            "notify_important_event",
            {
                "user_id": str(user_id),
                "kind": "design_ready",
                "event_id": str(artifact_id),
                "artifact_id": str(artifact_id),
            },
            tenant_id,
        )
    ]


async def test_failed_tool_end_never_enqueues_notification(monkeypatch) -> None:
    import edecan_api.routers.conversations as conversations_module

    async def forbidden(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("un fallo no se anuncia como terminado")

    monkeypatch.setattr(conversations_module, "enqueue", forbidden)
    await conversations_module._enqueue_tool_notification(
        settings=object(),
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        event={
            "type": "tool_end",
            "tool_call_id": "call-1",
            "name": "gestionar_autorreparacion_local",
            "result_preview": "Error: no se pudo aplicar",
        },
    )


async def test_fydesign_completion_notifies_mobile_without_listing_every_tool(monkeypatch) -> None:
    import edecan_api.routers.conversations as conversations_module

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    queued = []

    async def fake_enqueue(settings, job_type, payload, queued_tenant_id):  # noqa: ANN001
        queued.append((job_type, payload, queued_tenant_id))
        return uuid.uuid4()

    monkeypatch.setattr(conversations_module, "enqueue", fake_enqueue)
    await conversations_module._enqueue_tool_notification(
        settings=object(),
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation_id,
        event={
            "type": "tool_end",
            "tool_call_id": "studio-call-1",
            "name": "fydesign_video_ad",
            "result_preview": "Video listo",
            "artifacts": [],
        },
    )

    assert queued[0][0] == "notify_important_event"
    assert queued[0][1]["kind"] == "design_ready"
    assert queued[0][1]["chat_id"] == str(conversation_id)
    assert queued[0][2] == tenant_id


def test_fydesign_health_does_not_emit_completion_notification() -> None:
    import edecan_api.routers.conversations as conversations_module

    assert conversations_module._notification_kind_for_tool("fydesign_health") is None


async def _create_conversation(client, headers: dict[str, str]) -> str:
    response = await client.post("/v1/conversations", json={"channel": "web"}, headers=headers)
    assert response.status_code == 201
    return response.json()["id"]


async def test_image_attachment_reaches_same_agent_turn_as_private_multimodal_block(
    client, fake_repo, monkeypatch
) -> None:
    import edecan_api.routers.conversations as conversations_module

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    file_id = uuid.uuid4()
    image_bytes = b"\x89PNG\r\n\x1a\nprivate-image"
    s3_key = f"tenants/{tenant_id}/files/{file_id}/captura.png"
    await fake_repo.create_file(
        tenant_id=tenant_id,
        user_id=user_id,
        file_id=file_id,
        s3_key=s3_key,
        filename="captura.png",
        mime="image/png",
        size_bytes=len(image_bytes),
        status="uploaded",
    )
    monkeypatch.setattr(
        conversations_module.aioboto3,
        "Session",
        lambda: _VisionS3Session({s3_key: image_bytes}),
    )

    class VisionInspectingAgent:
        def __init__(self, llm_router, registry) -> None:
            pass

        async def run_turn(self, *, ctx, user_text, **kwargs):
            content = ctx.extras["direct_user_content"]
            assert isinstance(content, list)
            # Cuando la imagen va inline, el bloque de texto se limpia del
            # contexto de adjuntos ("Archivos adjuntos privados:...") para no
            # confundir al modelo con instrucciones de fallback.
            assert content[0] == {"type": "text", "text": "¿Qué ves aquí?"}
            assert content[1]["type"] == "image"
            assert content[1]["source"]["media_type"] == "image/png"
            assert base64.b64decode(content[1]["source"]["data"]) == image_bytes
            assert "respóndela directamente sin llamar una herramienta" in user_text
            yield {"type": "text_delta", "text": "Veo la captura directamente."}
            yield {"type": "done", "usage": {}}

    monkeypatch.setattr(conversations_module, "Agent", VisionInspectingAgent)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key="hosted_basic")
    conversation_id = await _create_conversation(client, headers)

    response = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": "¿Qué ves aquí?", "attachments": [str(file_id)]},
        headers=headers,
    )

    assert response.status_code == 200
    assert "Veo la captura directamente." in response.text
    stored = fake_repo.messages[uuid.UUID(conversation_id)][0]["content"]
    assert stored["text"] == "¿Qué ves aquí?"
    assert stored["attachments"] == [
        {"file_id": str(file_id), "filename": "captura.png", "mime": "image/png"}
    ]
    assert "private-image" not in json.dumps(stored)
    assert s3_key not in json.dumps(stored)


async def test_direct_vision_keeps_healthy_images_when_one_private_object_fails(
    fake_repo, test_settings, monkeypatch
) -> None:
    import edecan_api.routers.conversations as conversations_module

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    good_id = uuid.uuid4()
    broken_id = uuid.uuid4()
    good_key = f"tenants/{tenant_id}/files/{good_id}/buena.jpg"
    broken_key = f"tenants/{tenant_id}/files/{broken_id}/rota.jpg"
    for file_id, key in ((good_id, good_key), (broken_id, broken_key)):
        await fake_repo.create_file(
            tenant_id=tenant_id,
            user_id=user_id,
            file_id=file_id,
            s3_key=key,
            filename=key.rsplit("/", 1)[-1],
            mime="image/jpeg",
            size_bytes=32,
            status="uploaded",
        )
    monkeypatch.setattr(
        conversations_module.aioboto3,
        "Session",
        lambda: _VisionS3Session(
            {
                broken_key: RuntimeError("objeto temporalmente no disponible"),
                good_key: b"healthy-private-image",
            }
        ),
    )

    content = await conversations_module._direct_multimodal_content(
        settings=test_settings,
        user_text="Analiza las dos imágenes.",
        attachments=[
            {"file_id": str(broken_id), "filename": "rota.jpg", "mime": "image/jpeg"},
            {"file_id": str(good_id), "filename": "buena.jpg", "mime": "image/jpeg"},
        ],
        repo=fake_repo,
        tenant_id=tenant_id,
    )

    assert isinstance(content, list)
    assert [block["type"] for block in content] == ["text", "image"]
    assert base64.b64decode(content[1]["source"]["data"]) == b"healthy-private-image"


async def test_direct_vision_caps_total_private_payload(
    fake_repo, test_settings, monkeypatch
) -> None:
    import edecan_api.routers.conversations as conversations_module

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    attachments = []
    objects: dict[str, bytes | Exception] = {}
    for index, payload in enumerate((b"12345678", b"abcdefgh", b"z")):
        file_id = uuid.uuid4()
        key = f"tenants/{tenant_id}/files/{file_id}/imagen-{index}.png"
        await fake_repo.create_file(
            tenant_id=tenant_id,
            user_id=user_id,
            file_id=file_id,
            s3_key=key,
            filename=f"imagen-{index}.png",
            mime="image/png",
            size_bytes=len(payload),
            status="uploaded",
        )
        attachments.append(
            {"file_id": str(file_id), "filename": f"imagen-{index}.png", "mime": "image/png"}
        )
        objects[key] = payload

    monkeypatch.setattr(conversations_module, "_DIRECT_VISION_MAX_TOTAL_BYTES", 16)
    monkeypatch.setattr(
        conversations_module.aioboto3,
        "Session",
        lambda: _VisionS3Session(objects),
    )

    content = await conversations_module._direct_multimodal_content(
        settings=test_settings,
        user_text="Analiza estas imágenes.",
        attachments=attachments,
        repo=fake_repo,
        tenant_id=tenant_id,
    )

    assert isinstance(content, list)
    assert [block["type"] for block in content] == ["text", "image", "image"]
    assert (
        sum(
            len(base64.b64decode(block["source"]["data"]))
            for block in content
            if block["type"] == "image"
        )
        == 16
    )


async def test_post_message_streams_sse_and_persists_assistant_turn(
    client, fake_repo, monkeypatch
) -> None:
    import edecan_api.routers.conversations as conversations_module

    class ScriptedAgent:
        """Agente falso: emite `text_delta` x2 y `done`, tal como pide el WP."""

        def __init__(self, llm_router, registry) -> None:
            self.llm_router = llm_router
            self.registry = registry

        async def run_turn(
            self, *, ctx, persona, history, user_text, flags, extra_tools=None, seleccion=None
        ):
            assert user_text == "Hola, ¿cómo estás?"
            yield {"type": "text_delta", "text": "Hola "}
            yield {"type": "text_delta", "text": "mundo"}
            yield {
                "type": "done",
                "usage": {"input_tokens": 12, "output_tokens": 7},
                "explanation": "Evidence: perfil local\nTools: memoria",
            }

    monkeypatch.setattr(conversations_module, "Agent", ScriptedAgent)

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key="hosted_basic")

    conversation_id = await _create_conversation(client, headers)

    response = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": "Hola, ¿cómo estás?"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    body = response.text
    assert "event: message.delta" in body
    assert "event: message.done" in body
    assert '"text": "Hola "' in body

    conversation_uuid = uuid.UUID(conversation_id)
    messages = fake_repo.messages[conversation_uuid]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == {"text": "Hola, ¿cómo estás?"}
    assert messages[-1]["content"] == {
        "text": "Hola mundo",
        "explanation": "Evidence: perfil local\nTools: memoria",
    }
    assert messages[-1]["tokens_in"] == 12
    assert messages[-1]["tokens_out"] == 7

    kinds = [event["kind"] for event in fake_repo.usage_events]
    assert kinds.count("messages") == 1
    llm_events = [e for e in fake_repo.usage_events if e["kind"] == "llm_tokens"]
    assert len(llm_events) == 1
    assert llm_events[0]["quantity"] == 19  # 12 + 7


async def test_bare_fix_runs_bounded_preflight_and_always_finishes(
    client, fake_repo, monkeypatch
) -> None:
    import edecan_api.routers.conversations as conversations_module

    class AgentMustNotRun:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def run_turn(self, **kwargs):
            raise AssertionError("/fix desnudo no debe esperar al LLM")
            yield  # pragma: no cover

    monkeypatch.setattr(conversations_module, "Agent", AgentMustNotRun)
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    conversation_id = await _create_conversation(client, headers)

    response = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": " /FIX "},
        headers=headers,
    )

    assert response.status_code == 200
    assert "event: tool.start" in response.text
    assert "event: tool.end" in response.text
    assert "event: message.done" in response.text
    assert "escribe el fallo junto al comando" in response.text
    messages = fake_repo.messages[uuid.UUID(conversation_id)]
    assert [message["role"] for message in messages] == ["user", "assistant"]


async def test_first_message_names_conversation_without_waiting_for_second_llm(
    client, fake_repo, monkeypatch
) -> None:
    import edecan_api.routers.conversations as conversations_module

    class ScriptedAgent:
        def __init__(self, llm_router, registry) -> None:
            pass

        async def run_turn(self, **kwargs):
            yield {"type": "text_delta", "text": "Claro."}
            yield {"type": "done", "usage": {}}

    monkeypatch.setattr(conversations_module, "Agent", ScriptedAgent)
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    conversation_id = await _create_conversation(client, headers)

    response = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": "Planifica mi viaje familiar a Madrid. Incluye hoteles."},
        headers=headers,
    )

    assert response.status_code == 200
    row = fake_repo.conversations[uuid.UUID(conversation_id)]
    assert row["title"] == "Planificar viaje familiar a Madrid"


async def test_inline_credential_never_reaches_llm_history_or_sse(
    client, fake_repo, monkeypatch
) -> None:
    import edecan_api.routers.conversations as conversations_module

    llm_runs = 0

    class ForbiddenAgent:
        def __init__(self, llm_router, registry) -> None:
            pass

        async def run_turn(self, **kwargs):
            nonlocal llm_runs
            llm_runs += 1
            raise AssertionError("La credencial no debe llegar al LLM")
            yield  # pragma: no cover

    monkeypatch.setattr(conversations_module, "Agent", ForbiddenAgent)
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key="hosted_basic")
    conversation_id = await _create_conversation(client, headers)
    secret = "sk_proj_super_secret_1234567890"

    response = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": f"Mira, mi API key de ElevenLabs es {secret}. Configúrala."},
        headers=headers,
    )

    assert response.status_code == 200
    assert llm_runs == 0
    assert secret not in response.text
    assert "[credencial protegida]" in response.text
    stored = fake_repo.messages[uuid.UUID(conversation_id)]
    assert [message["role"] for message in stored] == ["user", "assistant"]
    assert secret not in json.dumps(stored, default=str)
    assert secret not in fake_repo.conversations[uuid.UUID(conversation_id)]["title"]
    assert (
        fake_repo.conversations[uuid.UUID(conversation_id)]["title"]
        == "Configurar API Key - ElevenLabs"
    )
    assert fake_repo.audit_log[-1]["action"] == "credentials.chat.failed"


async def test_secret_without_configuration_intent_is_redacted_before_llm_and_storage(
    client, fake_repo, monkeypatch
) -> None:
    import edecan_api.routers.conversations as conversations_module

    seen_user_text: list[str] = []

    class InspectingAgent:
        def __init__(self, llm_router, registry) -> None:
            pass

        async def run_turn(self, **kwargs):
            seen_user_text.append(kwargs["user_text"])
            yield {"type": "text_delta", "text": "Entendido."}
            yield {"type": "done", "usage": {}}

    monkeypatch.setattr(conversations_module, "Agent", InspectingAgent)
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    conversation_id = await _create_conversation(client, headers)
    secret = "sk-proj-example-secret-1234567890"

    response = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": f"Seguro la pusiste mal. Esta es {secret}."},
        headers=headers,
    )

    assert response.status_code == 200
    assert seen_user_text and secret not in seen_user_text[0]
    stored = fake_repo.messages[uuid.UUID(conversation_id)]
    assert secret not in json.dumps(stored, default=str)
    assert "[REDACTED]" in json.dumps(stored, default=str)


def test_historical_secret_is_redacted_when_serialized_or_sent_back_to_llm() -> None:
    import edecan_api.routers.conversations as conversations_module

    secret = "sk-proj-historical-secret-1234567890"
    row = {
        "id": uuid.uuid4(),
        "role": "user",
        "content": {"text": f"Clave antigua: {secret}"},
        "tool_calls": [{"result": {"debug": f"Bearer {secret}"}}],
        "created_at": datetime.now(UTC),
    }

    outgoing = conversations_module._message_out(row)
    history = conversations_module._rows_to_chat_messages([row])

    assert secret not in json.dumps(outgoing, default=str)
    assert secret not in str(history[0].content)
    assert "[REDACTED]" in json.dumps(outgoing, default=str)


async def test_conversation_can_be_renamed_and_is_user_and_tenant_scoped(client) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    conversation_id = await _create_conversation(client, headers)

    renamed = await client.patch(
        f"/v1/conversations/{conversation_id}",
        json={"title": "  Lanzamiento   de Edecán  "},
        headers=headers,
    )

    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Lanzamiento de Edecán"
    other_user_headers = auth_headers(
        user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic"
    )
    denied_user = await client.patch(
        f"/v1/conversations/{conversation_id}",
        json={"title": "No permitido"},
        headers=other_user_headers,
    )
    other_tenant_headers = auth_headers(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic"
    )
    denied_tenant = await client.patch(
        f"/v1/conversations/{conversation_id}",
        json={"title": "Tampoco permitido"},
        headers=other_tenant_headers,
    )
    assert denied_user.status_code == 404
    assert denied_tenant.status_code == 404


async def test_post_message_idempotency_replays_exact_sse_without_duplicate_side_effects(
    client, fake_repo, monkeypatch
) -> None:
    import edecan_api.routers.conversations as conversations_module

    runs = 0

    class ScriptedAgent:
        def __init__(self, llm_router, registry) -> None:
            pass

        async def run_turn(self, **kwargs):
            nonlocal runs
            runs += 1
            yield {"type": "text_delta", "text": "Hecho una sola vez."}
            yield {"type": "done", "usage": {"input_tokens": 2, "output_tokens": 3}}

    monkeypatch.setattr(conversations_module, "Agent", ScriptedAgent)
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    key = str(uuid.uuid4())
    headers["Idempotency-Key"] = key
    conversation_id = await _create_conversation(client, headers)

    first = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": "Hazlo"},
        headers=headers,
    )
    replay = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": "Hazlo"},
        headers=headers,
    )

    assert first.status_code == replay.status_code == 200
    assert first.text == replay.text
    assert first.headers["idempotency-replayed"] == "false"
    assert replay.headers["idempotency-replayed"] == "true"
    assert replay.headers["idempotency-key"] == key
    assert runs == 1
    stored = fake_repo.messages[uuid.UUID(conversation_id)]
    assert [message["role"] for message in stored] == ["user", "assistant"]
    assert [event["kind"] for event in fake_repo.usage_events].count("messages") == 1


async def test_completed_message_attempt_can_resume_without_resending_body(
    client, fake_repo, monkeypatch
) -> None:
    import edecan_api.routers.conversations as conversations_module

    runs = 0

    class ScriptedAgent:
        def __init__(self, llm_router, registry) -> None:
            pass

        async def run_turn(self, **kwargs):
            nonlocal runs
            runs += 1
            yield {"type": "text_delta", "text": "Resultado recuperable."}
            yield {"type": "done", "usage": {"input_tokens": 1, "output_tokens": 2}}

    monkeypatch.setattr(conversations_module, "Agent", ScriptedAgent)
    tenant_id = uuid.uuid4()
    headers = auth_headers(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        plan_key="hosted_basic",
    )
    attempt_id = str(uuid.uuid4())
    conversation_id = await _create_conversation(client, headers)

    first = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": "Continúa aunque cierre el teléfono"},
        headers={**headers, "Idempotency-Key": attempt_id},
    )
    resumed = await client.get(
        f"/v1/conversations/{conversation_id}/message-attempts/{attempt_id}",
        headers=headers,
    )

    assert first.status_code == resumed.status_code == 200
    assert resumed.headers["content-type"].startswith("text/event-stream")
    assert resumed.headers["idempotency-replayed"] == "true"
    assert resumed.headers["cache-control"] == "no-store"
    assert resumed.text == first.text
    assert runs == 1
    assert [message["role"] for message in fake_repo.messages[uuid.UUID(conversation_id)]] == [
        "user",
        "assistant",
    ]


async def test_in_flight_message_attempt_returns_202_without_false_failure(
    client, fake_redis
) -> None:
    import edecan_api.routers.conversations as conversations_module

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    headers = auth_headers(
        user_id=user_id,
        tenant_id=tenant_id,
        plan_key="hosted_basic",
    )
    conversation_id = await _create_conversation(client, headers)
    attempt_id = uuid.uuid4()
    redis_key = conversations_module._message_idempotency_key(
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=uuid.UUID(conversation_id),
        idempotency_key=attempt_id,
    )
    owner, existing = await conversations_module._claim_message_idempotency(
        fake_redis,
        redis_key=redis_key,
        request_hash="hash",
        ttl_seconds=3600,
    )
    assert owner is not None and existing is None

    response = await client.get(
        f"/v1/conversations/{conversation_id}/message-attempts/{attempt_id}",
        headers=headers,
    )

    assert response.status_code == 202
    assert response.json() == {"status": "in_flight"}
    assert response.headers["retry-after"] == "1"
    assert response.headers["cache-control"] == "no-store"


async def test_message_attempt_resume_is_user_and_tenant_scoped_and_expiry_safe(
    client, fake_redis
) -> None:
    import edecan_api.routers.conversations as conversations_module

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    headers = auth_headers(
        user_id=user_id,
        tenant_id=tenant_id,
        plan_key="hosted_basic",
    )
    conversation_id = await _create_conversation(client, headers)
    attempt_id = uuid.uuid4()
    redis_key = conversations_module._message_idempotency_key(
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=uuid.UUID(conversation_id),
        idempotency_key=attempt_id,
    )
    await fake_redis.set(
        redis_key,
        json.dumps(
            {
                "status": "completed",
                "request_hash": "hash",
                "events": ['event: message.done\ndata: {"type":"done","usage":{}}\n\n'],
            }
        ),
        ex=3600,
    )

    other_user_headers = auth_headers(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        plan_key="hosted_basic",
    )
    other_tenant_headers = auth_headers(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        plan_key="hosted_basic",
    )
    denied_user = await client.get(
        f"/v1/conversations/{conversation_id}/message-attempts/{attempt_id}",
        headers=other_user_headers,
    )
    denied_tenant = await client.get(
        f"/v1/conversations/{conversation_id}/message-attempts/{attempt_id}",
        headers=other_tenant_headers,
    )
    missing = await client.get(
        f"/v1/conversations/{conversation_id}/message-attempts/{uuid.uuid4()}",
        headers=headers,
    )

    assert denied_user.status_code == 404
    assert denied_tenant.status_code == 404
    assert missing.status_code == 404
    assert "reanudación" in missing.json()["detail"]


async def test_idempotent_stream_is_live_and_finishes_replay_after_consumer_disconnect(
    fake_redis,
) -> None:
    import edecan_api.routers.conversations as conversations_module

    release = asyncio.Event()
    redis_key = "chat_idempotency:tenant:conversation:attempt"
    request_hash = "request-hash"
    owner_token, existing = await conversations_module._claim_message_idempotency(
        fake_redis,
        redis_key=redis_key,
        request_hash=request_hash,
        ttl_seconds=3600,
    )
    assert owner_token is not None and existing is None
    notifications: list[str] = []

    async def notify() -> None:
        notifications.append("ready")

    async def source():
        yield "event: message.delta\ndata: first\n\n"
        await release.wait()
        yield "event: message.done\ndata: done\n\n"

    stream = conversations_module._stream_and_complete_idempotency(
        stream=source(),
        redis_client=fake_redis,
        redis_key=redis_key,
        request_hash=request_hash,
        owner_token=owner_token,
        ttl_seconds=3600,
        on_disconnected_complete=notify,
    )
    first = await anext(stream)
    assert first == "event: message.delta\ndata: first\n\n"
    in_flight = await conversations_module._load_idempotency_record(fake_redis, redis_key=redis_key)
    assert in_flight is not None and in_flight["status"] == "in_flight"

    # Simula que el transporte se desconecta después del primer token. Cerrar
    # el consumidor debe esperar al productor y dejar el replay completo.
    asyncio.get_running_loop().call_soon(release.set)
    await stream.aclose()
    completed = await conversations_module._load_idempotency_record(fake_redis, redis_key=redis_key)
    assert completed is not None
    assert completed == {
        "status": "completed",
        "request_hash": request_hash,
        "events": [
            "event: message.delta\ndata: first\n\n",
            "event: message.done\ndata: done\n\n",
        ],
        "completed_at": completed["completed_at"],
    }
    assert notifications == ["ready"]


async def test_idempotent_stream_does_not_notify_when_client_reads_to_done(fake_redis) -> None:
    import edecan_api.routers.conversations as conversations_module

    redis_key = "chat_idempotency:tenant:conversation:connected"
    owner_token, _ = await conversations_module._claim_message_idempotency(
        fake_redis,
        redis_key=redis_key,
        request_hash="hash",
        ttl_seconds=3600,
    )
    assert owner_token is not None
    notifications: list[str] = []

    async def source():
        yield "event: message.done\ndata: done\n\n"

    async def notify() -> None:
        notifications.append("unexpected")

    stream = conversations_module._stream_and_complete_idempotency(
        stream=source(),
        redis_client=fake_redis,
        redis_key=redis_key,
        request_hash="hash",
        owner_token=owner_token,
        ttl_seconds=3600,
        on_disconnected_complete=notify,
    )

    assert [chunk async for chunk in stream] == ["event: message.done\ndata: done\n\n"]
    assert notifications == []


async def test_post_message_idempotency_rejects_same_key_with_different_body(
    client, monkeypatch
) -> None:
    import edecan_api.routers.conversations as conversations_module

    class ScriptedAgent:
        def __init__(self, llm_router, registry) -> None:
            pass

        async def run_turn(self, **kwargs):
            yield {"type": "done", "usage": {}}

    monkeypatch.setattr(conversations_module, "Agent", ScriptedAgent)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    headers["Idempotency-Key"] = str(uuid.uuid4())
    conversation_id = await _create_conversation(client, headers)

    first = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": "primero"},
        headers=headers,
    )
    conflict = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": "distinto"},
        headers=headers,
    )

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert "mensaje diferente" in conflict.json()["detail"]


async def test_post_message_idempotency_rejects_concurrent_in_flight_retry(
    client, fake_repo, monkeypatch
) -> None:
    import edecan_api.routers.conversations as conversations_module

    started = asyncio.Event()
    release = asyncio.Event()
    runs = 0

    class SlowAgent:
        def __init__(self, llm_router, registry) -> None:
            pass

        async def run_turn(self, **kwargs):
            nonlocal runs
            runs += 1
            started.set()
            await release.wait()
            yield {"type": "done", "usage": {}}

    monkeypatch.setattr(conversations_module, "Agent", SlowAgent)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    headers["Idempotency-Key"] = str(uuid.uuid4())
    conversation_id = await _create_conversation(client, headers)
    url = f"/v1/conversations/{conversation_id}/messages"

    first_task = asyncio.create_task(client.post(url, json={"text": "una vez"}, headers=headers))
    await asyncio.wait_for(started.wait(), timeout=1)
    concurrent = await client.post(url, json={"text": "una vez"}, headers=headers)

    assert concurrent.status_code == 409
    assert concurrent.headers["retry-after"] == "1"
    assert "procesando" in concurrent.json()["detail"]
    release.set()
    first = await asyncio.wait_for(first_task, timeout=1)
    assert first.status_code == 200
    assert runs == 1
    assert [m["role"] for m in fake_repo.messages[uuid.UUID(conversation_id)]] == [
        "user",
        "assistant",
    ]


async def test_post_message_idempotency_is_scoped_per_conversation(client, monkeypatch) -> None:
    import edecan_api.routers.conversations as conversations_module

    runs = 0

    class ScriptedAgent:
        def __init__(self, llm_router, registry) -> None:
            pass

        async def run_turn(self, **kwargs):
            nonlocal runs
            runs += 1
            yield {"type": "done", "usage": {}}

    monkeypatch.setattr(conversations_module, "Agent", ScriptedAgent)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    headers["Idempotency-Key"] = str(uuid.uuid4())
    first_conversation = await _create_conversation(client, headers)
    second_conversation = await _create_conversation(client, headers)

    first = await client.post(
        f"/v1/conversations/{first_conversation}/messages",
        json={"text": "mismo payload"},
        headers=headers,
    )
    second = await client.post(
        f"/v1/conversations/{second_conversation}/messages",
        json={"text": "mismo payload"},
        headers=headers,
    )

    assert first.status_code == second.status_code == 200
    assert runs == 2


async def test_post_message_rejects_malformed_idempotency_uuid(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    headers["Idempotency-Key"] = "no-es-uuid"
    conversation_id = await _create_conversation(client, headers)

    response = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": "Hola"},
        headers=headers,
    )

    assert response.status_code == 422


async def test_post_message_ctx_lleva_los_flags_del_plan_del_tenant(client, monkeypatch) -> None:
    """Regresión: `_build_ctx` debe meter `tenant.flags` en `ctx.extras["flags"]`
    -no solo pasárselo a `Agent.run_turn(flags=...)`- porque una `Tool` (p. ej.
    `GenerarContenidoTool` en `edecan_toolkit.contenido`) solo recibe `ctx`, y
    sin esta clave `_tenant_flags(ctx)` siempre ve `{}`. Modelo de precio de
    pago único (`edecan_schemas.plans` docstring): `models.premium` ya está en
    `True` en las 4 entradas de `PLANES` por igual, así que esto ahora
    verifica simplemente que el flag real del plan (`True`) se propague a esas
    tools en vez de quedarse en `{}`."""
    import edecan_api.routers.conversations as conversations_module

    seen_flags: list[dict] = []

    class ScriptedAgent:
        def __init__(self, llm_router, registry) -> None:
            pass

        async def run_turn(
            self, *, ctx, persona, history, user_text, flags, extra_tools=None, seleccion=None
        ):
            seen_flags.append(ctx.extras.get("flags"))
            yield {"type": "done", "usage": {}}

    monkeypatch.setattr(conversations_module, "Agent", ScriptedAgent)

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key="hosted_basic")
    conversation_id = await _create_conversation(client, headers)

    response = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": "Hola"},
        headers=headers,
    )

    assert response.status_code == 200
    assert len(seen_flags) == 1
    assert seen_flags[0] is not None
    assert seen_flags[0].get("models.premium") is True


async def test_post_message_adjunta_archivo_privado_al_mismo_hilo(
    client, fake_repo, monkeypatch
) -> None:
    import edecan_api.routers.conversations as conversations_module

    seen_user_text: list[str] = []

    class ScriptedAgent:
        def __init__(self, llm_router, registry) -> None:
            pass

        async def run_turn(self, *, user_text, **kwargs):
            seen_user_text.append(user_text)
            yield {"type": "text_delta", "text": "Lo revisaré."}
            yield {"type": "done", "usage": {}}

    monkeypatch.setattr(conversations_module, "Agent", ScriptedAgent)
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    file_id = uuid.uuid4()
    await fake_repo.create_file(
        tenant_id=tenant_id,
        user_id=user_id,
        s3_key=f"tenants/{tenant_id}/files/{file_id}/contrato.pdf",
        filename="contrato.pdf",
        mime="application/pdf",
        size_bytes=100,
        status="ready",
        file_id=file_id,
    )
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key="hosted_basic")
    conversation_id = await _create_conversation(client, headers)

    response = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": "Resume esto", "attachments": [str(file_id)]},
        headers=headers,
    )

    assert response.status_code == 200
    assert str(file_id) in seen_user_text[0]
    assert "contrato.pdf" in seen_user_text[0]
    stored = fake_repo.messages[uuid.UUID(conversation_id)][0]["content"]
    assert stored["attachments"][0] == {
        "file_id": str(file_id),
        "filename": "contrato.pdf",
        "mime": "application/pdf",
    }


async def test_post_message_with_orphan_plan_returns_429_not_unlimited(
    client, fake_repo, monkeypatch
) -> None:
    """Regresión (barrido v7, WP-V7-08 encontró y corrigió el mismo patrón en
    `files.py`/`voice.py`; `conversations.py` quedó fuera del alcance de ese WP y
    se cierra acá, WP-V7-12): `_check_message_quota` defaulteaba a `UNLIMITED`
    cuando `tenant.flags` no trae `LIMIT_MESSAGES_PER_DAY` -- exactamente lo que
    pasa con un `plan_key` huérfano (`edecan_api.deps.flags_for_plan` devuelve
    `{}` para un plan que no existe en `edecan_schemas.plans.PLANES`; el JWT no
    valida `plan` contra el catálogo al firmarlo). Con el fix (default `0`,
    fail-closed), ese tenant es rechazado con 429 en vez de mandar mensajes sin
    ningún límite en el endpoint más usado de toda la API."""
    import edecan_api.routers.conversations as conversations_module

    class NeverCalledAgent:
        def __init__(self, llm_router, registry) -> None:
            raise AssertionError("El agente no debería invocarse con la cuota agotada (0).")

    monkeypatch.setattr(conversations_module, "Agent", NeverCalledAgent)

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key="plan_no_existe")

    conversation_id = await _create_conversation(client, headers)

    response = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": "¿Hay límite?"},
        headers=headers,
    )

    assert response.status_code == 429
    assert "límite" in response.json()["detail"].lower()


async def test_post_message_to_unknown_conversation_returns_404(client) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key="hosted_basic")

    response = await client.post(
        f"/v1/conversations/{uuid.uuid4()}/messages", json={"text": "Hola"}, headers=headers
    )
    assert response.status_code == 404


async def test_list_conversations_is_scoped_per_tenant(client) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    headers_a = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_a, plan_key="hosted_basic")
    headers_b = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_b, plan_key="hosted_basic")

    await _create_conversation(client, headers_a)

    response_a = await client.get("/v1/conversations", headers=headers_a)
    response_b = await client.get("/v1/conversations", headers=headers_b)

    assert len(response_a.json()) == 1
    assert len(response_b.json()) == 0


async def test_list_conversations_replaces_legacy_long_message_copy(client, fake_repo) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key="hosted_basic")
    conversation_id = uuid.UUID(await _create_conversation(client, headers))
    first_message = (
        "Configura la API key de X es [credencial protegida]. "
        "Luego comprueba que la integración responda correctamente."
    )
    legacy_title = first_message[:62]
    fake_repo.conversations[conversation_id]["title"] = legacy_title
    fake_repo.conversations[conversation_id]["title_source"] = "legacy"
    await fake_repo.add_message(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        role="user",
        content={"text": first_message},
    )

    response = await client.get("/v1/conversations", headers=headers)

    assert response.status_code == 200
    assert response.json()[0]["title"] == "Configurar API Key - X"
    assert fake_repo.conversations[conversation_id]["title_source"] == "auto"


async def test_list_conversations_preserves_legacy_manual_title(client, fake_repo) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key="hosted_basic")
    conversation_id = uuid.UUID(await _create_conversation(client, headers))
    fake_repo.conversations[conversation_id]["title"] = "Viaje familiar"
    fake_repo.conversations[conversation_id]["title_source"] = "legacy"
    await fake_repo.add_message(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        role="user",
        content={"text": "Quiero comparar vuelos y hoteles para viajar con mi familia."},
    )

    response = await client.get("/v1/conversations", headers=headers)

    assert response.status_code == 200
    assert response.json()[0]["title"] == "Viaje familiar"
    assert fake_repo.conversations[conversation_id]["title_source"] == "manual"


async def test_list_conversations_improves_existing_automatic_title(client, fake_repo) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key="hosted_basic")
    conversation_id = uuid.UUID(await _create_conversation(client, headers))
    first_message = "How do I explain a friend in one paragraph who you are and what you make"
    fake_repo.conversations[conversation_id]["title"] = first_message[:72]
    fake_repo.conversations[conversation_id]["title_source"] = "auto"
    await fake_repo.add_message(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        role="user",
        content={"text": first_message},
    )

    response = await client.get("/v1/conversations", headers=headers)

    assert response.status_code == 200
    assert response.json()[0]["title"] == "Explicar quién es Edecán"
    assert fake_repo.conversations[conversation_id]["title_source"] == "auto"


# --------------------------------------------------------------------------
# GET /main -- conversación "principal" (frente 5, paridad REFERENCIA)
# --------------------------------------------------------------------------


async def test_get_main_conversation_creates_it_lazily_and_is_idempotent(client) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")

    first = await client.get("/v1/conversations/main", headers=headers)
    second = await client.get("/v1/conversations/main", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["is_main"] is True
    assert first.json()["title"] == "Actividad"


async def test_get_main_conversation_is_scoped_per_tenant_and_user(client) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    headers_a = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_a, plan_key="hosted_basic")
    headers_b = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_b, plan_key="hosted_basic")

    response_a = await client.get("/v1/conversations/main", headers=headers_a)
    response_b = await client.get("/v1/conversations/main", headers=headers_b)

    assert response_a.json()["id"] != response_b.json()["id"]


async def test_main_conversation_appears_pinned_with_flag_in_list_conversations(client) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key="hosted_basic")
    regular_id = await _create_conversation(client, headers)

    main_response = await client.get("/v1/conversations/main", headers=headers)
    main_id = main_response.json()["id"]

    listado = await client.get("/v1/conversations", headers=headers)
    by_id = {row["id"]: row for row in listado.json()}

    assert by_id[main_id]["is_main"] is True
    assert by_id[regular_id]["is_main"] is False


async def test_main_conversation_survives_concurrent_first_resolution(client, fake_repo) -> None:
    """Dos requests concurrentes (dos eventos automáticos a la vez) nunca
    deben terminar con dos conversaciones `is_main = true` distintas -- el
    mismo contrato que garantiza el índice único parcial en Postgres
    (`ON CONFLICT ... WHERE is_main`, ver `SqlRepo.resolve_main_conversation`).
    `FakeRepo` no es concurrente de verdad, pero esta prueba fija el
    contrato observable: llamar `resolve_main_conversation` más de una vez
    siempre devuelve la misma fila.
    """
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()

    resultados = [
        await fake_repo.resolve_main_conversation(tenant_id=tenant_id, user_id=user_id)
        for _ in range(3)
    ]

    ids = {row["id"] for row in resultados}
    assert len(ids) == 1


# --------------------------------------------------------------------------
# GET /{id} y DELETE /{id}
# --------------------------------------------------------------------------


async def test_get_conversation_by_id_includes_message_history(client, fake_repo) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    conversation_id = await _create_conversation(client, headers)

    await fake_repo.add_message(
        tenant_id=tenant_id,
        conversation_id=uuid.UUID(conversation_id),
        role="user",
        content={"text": "Hola"},
    )

    response = await client.get(f"/v1/conversations/{conversation_id}", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == conversation_id
    assert body["channel"] == "web"
    assert len(body["messages"]) == 1
    assert body["messages"][0]["role"] == "user"
    assert body["messages"][0]["content"] == {"text": "Hola"}
    assert body["pending_confirmation"] is None


# ---------------------------------------------------------------------------
# Comando local /clear: reinicia el contexto SIN borrar el historial
# (revisión `0031_conv_context_cleared`). El cliente intercepta el
# texto /clear y llama a este endpoint directo -- nunca pasa por el modelo.
# ---------------------------------------------------------------------------


class _HistoryCapturingAgent:
    """Agente que solo anota el `history` (`list[ChatMessage]`) con el que se
    corrió el turno -- lo que de verdad ve el modelo, no lo que hay en la
    tabla `messages`."""

    capturadas: list[object] = []

    def __init__(self, llm_router, registry) -> None:
        pass

    async def run_turn(self, **kwargs):
        _HistoryCapturingAgent.capturadas.append(kwargs.get("history"))
        yield {"type": "text_delta", "text": "listo"}
        yield {"type": "done", "usage": {}}


async def test_clear_context_no_borra_mensajes_pero_deja_de_listarlos_por_defecto(
    client, fake_repo
) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    conversation_id = await _create_conversation(client, headers)
    conversation_uuid = uuid.UUID(conversation_id)

    await fake_repo.add_message(
        tenant_id=tenant_id,
        conversation_id=conversation_uuid,
        role="user",
        content={"text": "Hola"},
    )
    await fake_repo.add_message(
        tenant_id=tenant_id,
        conversation_id=conversation_uuid,
        role="assistant",
        content={"text": "Hola, ¿en qué te ayudo?"},
    )

    limpiado = await client.post(f"/v1/conversations/{conversation_id}/clear", headers=headers)
    assert limpiado.status_code == 200
    assert limpiado.json()["id"] == conversation_id
    assert limpiado.json()["context_cleared_at"] is not None

    despues = await client.get(f"/v1/conversations/{conversation_id}", headers=headers)
    assert despues.status_code == 200
    # La pantalla queda limpia...
    assert despues.json()["messages"] == []
    # ...pero nada se borró de verdad: sigue completo si se consulta sin el
    # límite de `/clear` (la garantía "no destructivo" del comando).
    intacto = await fake_repo.list_messages(tenant_id=tenant_id, conversation_id=conversation_uuid)
    assert len(intacto) == 2


async def test_clear_context_saca_el_historial_previo_del_turno_siguiente(
    client, fake_repo, monkeypatch
) -> None:
    import edecan_api.routers.conversations as conversations_module

    _HistoryCapturingAgent.capturadas = []
    monkeypatch.setattr(conversations_module, "Agent", _HistoryCapturingAgent)

    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    conversation_id = await _create_conversation(client, headers)
    conversation_uuid = uuid.UUID(conversation_id)

    await fake_repo.add_message(
        tenant_id=tenant_id,
        conversation_id=conversation_uuid,
        role="user",
        content={"text": "Un secreto de un turno viejo"},
    )

    await client.post(f"/v1/conversations/{conversation_id}/clear", headers=headers)

    respuesta = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": "hola de nuevo"},
        headers=headers,
    )

    assert respuesta.status_code == 200
    history = _HistoryCapturingAgent.capturadas[-1]
    assert history == []


async def test_clear_context_es_idempotente_y_user_y_tenant_scoped(client) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    conversation_id = await _create_conversation(client, headers)

    primero = await client.post(f"/v1/conversations/{conversation_id}/clear", headers=headers)
    segundo = await client.post(f"/v1/conversations/{conversation_id}/clear", headers=headers)
    assert primero.status_code == 200
    assert segundo.status_code == 200
    # Repetirlo no revive nada ni rompe nada: sigue habiendo, como mucho, el
    # mismo límite movido un poco más adelante.
    assert segundo.json()["context_cleared_at"] >= primero.json()["context_cleared_at"]

    otro_usuario = await client.post(
        f"/v1/conversations/{conversation_id}/clear",
        headers=auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic"),
    )
    otro_tenant = await client.post(
        f"/v1/conversations/{conversation_id}/clear",
        headers=auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic"),
    )
    assert otro_usuario.status_code == 404
    assert otro_tenant.status_code == 404


async def test_clear_context_de_conversacion_desconocida_devuelve_404(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.post(f"/v1/conversations/{uuid.uuid4()}/clear", headers=headers)
    assert response.status_code == 404


async def test_branch_copia_mensajes_sin_borrar_el_original(client, fake_repo) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    conversation_id = await _create_conversation(client, headers)
    conversation_uuid = uuid.UUID(conversation_id)
    first = await fake_repo.add_message(
        tenant_id=tenant_id,
        conversation_id=conversation_uuid,
        role="user",
        content={"text": "Hola"},
    )
    await fake_repo.add_message(
        tenant_id=tenant_id,
        conversation_id=conversation_uuid,
        role="assistant",
        content={"text": "¿Qué hacemos?"},
    )
    branched = await client.post(
        f"/v1/conversations/{conversation_id}/branch",
        json={"until_message_id": str(first["id"])},
        headers=headers,
    )
    assert branched.status_code == 200
    new_id = uuid.UUID(branched.json()["id"])
    assert new_id != conversation_uuid
    copied = await fake_repo.list_messages(tenant_id=tenant_id, conversation_id=new_id)
    assert [row["content"]["text"] for row in copied] == ["Hola"]
    original = await fake_repo.list_messages(tenant_id=tenant_id, conversation_id=conversation_uuid)
    assert len(original) == 2


async def test_rewind_crea_rama_sin_el_ultimo_turno(client, fake_repo) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    conversation_id = await _create_conversation(client, headers)
    conversation_uuid = uuid.UUID(conversation_id)
    await fake_repo.add_message(
        tenant_id=tenant_id,
        conversation_id=conversation_uuid,
        role="user",
        content={"text": "uno"},
    )
    await fake_repo.add_message(
        tenant_id=tenant_id,
        conversation_id=conversation_uuid,
        role="assistant",
        content={"text": "dos"},
    )
    await fake_repo.add_message(
        tenant_id=tenant_id,
        conversation_id=conversation_uuid,
        role="user",
        content={"text": "tres"},
    )
    await fake_repo.add_message(
        tenant_id=tenant_id,
        conversation_id=conversation_uuid,
        role="assistant",
        content={"text": "cuatro"},
    )
    rewound = await client.post(f"/v1/conversations/{conversation_id}/rewind", headers=headers)
    assert rewound.status_code == 200
    copied = await fake_repo.list_messages(
        tenant_id=tenant_id, conversation_id=uuid.UUID(rewound.json()["id"])
    )
    assert [row["content"]["text"] for row in copied] == ["uno", "dos"]


async def test_message_flags_guarda_pin_y_bookmark(client, fake_repo) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    conversation_id = await _create_conversation(client, headers)
    message = await fake_repo.add_message(
        tenant_id=tenant_id,
        conversation_id=uuid.UUID(conversation_id),
        role="assistant",
        content={"text": "respuesta"},
    )
    flagged = await client.post(
        f"/v1/conversations/{conversation_id}/messages/{message['id']}/flags",
        json={"pinned": True, "bookmark": True},
        headers=headers,
    )
    assert flagged.status_code == 200
    assert flagged.json()["content"]["pinned"] is True
    assert flagged.json()["content"]["bookmark"] is True


async def test_get_conversation_recovers_only_public_pending_confirmation(
    client, fake_redis
) -> None:
    import edecan_api.routers.conversations as conversations_module

    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    conversation_id = await _create_conversation(client, headers)
    conversation_uuid = uuid.UUID(conversation_id)
    await conversations_module._store_pending_confirmation(
        fake_redis,
        tenant_id=tenant_id,
        conversation_id=conversation_uuid,
        tool_call_id="mail_recoverable_1",
        name="enviar_correo",
        args={"to": "ana@example.com", "subject": "Hola"},
    )
    # Simula el estado operativo real guardado junto a la acción. Nunca debe
    # atravesar el contrato GET aunque el cliente recargue la aplicación.
    private_key = conversations_module._pending_confirmation_key(
        tenant_id=tenant_id,
        conversation_id=conversation_uuid,
        tool_call_id="mail_recoverable_1",
    )
    private_payload = json.loads(await fake_redis.get(private_key))
    private_payload["pending_turn"] = {"system_prompt": "SECRETO_INTERNO"}
    await fake_redis.set(
        private_key,
        json.dumps(private_payload),
        ex=conversations_module.PENDING_CONFIRMATION_TTL_SECONDS,
    )

    response = await client.get(f"/v1/conversations/{conversation_id}", headers=headers)

    assert response.status_code == 200
    assert response.json()["pending_confirmation"] == {
        "tool_call_id": "mail_recoverable_1",
        "name": "enviar_correo",
        "args": {"to": "ana@example.com", "subject": "Hola"},
    }
    assert "SECRETO_INTERNO" not in response.text

    other_tenant_headers = auth_headers(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic"
    )
    isolated = await client.get(
        f"/v1/conversations/{conversation_id}", headers=other_tenant_headers
    )
    assert isolated.status_code == 404

    declined = await client.post(
        f"/v1/conversations/{conversation_id}/confirm",
        json={"tool_call_id": "mail_recoverable_1", "approved": False},
        headers=headers,
    )
    assert declined.status_code == 200
    after_consumption = await client.get(f"/v1/conversations/{conversation_id}", headers=headers)
    assert after_consumption.json()["pending_confirmation"] is None


async def test_get_unknown_conversation_returns_404(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.get(f"/v1/conversations/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404


async def test_get_conversation_from_another_tenant_returns_404(client) -> None:
    headers_a = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    headers_b = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    conversation_id = await _create_conversation(client, headers_a)

    response = await client.get(f"/v1/conversations/{conversation_id}", headers=headers_b)
    assert response.status_code == 404


async def test_get_and_delete_conversation_from_another_user_returns_404(client) -> None:
    tenant_id = uuid.uuid4()
    owner_headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    other_user_headers = auth_headers(
        user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic"
    )
    conversation_id = await _create_conversation(client, owner_headers)

    fetched = await client.get(f"/v1/conversations/{conversation_id}", headers=other_user_headers)
    deleted = await client.delete(
        f"/v1/conversations/{conversation_id}", headers=other_user_headers
    )
    owner_still_has_it = await client.get(
        f"/v1/conversations/{conversation_id}", headers=owner_headers
    )

    assert fetched.status_code == 404
    assert deleted.status_code == 404
    assert owner_still_has_it.status_code == 200


async def test_delete_conversation(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    conversation_id = await _create_conversation(client, headers)

    response = await client.delete(f"/v1/conversations/{conversation_id}", headers=headers)
    assert response.status_code == 204
    assert response.content == b""

    listed = await client.get("/v1/conversations", headers=headers)
    assert listed.json() == []


async def test_delete_unknown_conversation_returns_404(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    response = await client.delete(f"/v1/conversations/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404


# --------------------------------------------------------------------------
# POST /{id}/confirm — gate de confirmación para tools `dangerous`.
# --------------------------------------------------------------------------


async def test_confirm_approved_executes_the_pending_dangerous_tool(
    client, fake_repo, monkeypatch
) -> None:
    """El `tool_call_id` que el usuario aprueba es el de la respuesta LLM
    ORIGINAL (la que disparó `confirmation_required`). `POST /confirm` nunca
    vuelve a invocar al LLM para ejecutarla -si lo hiciera, la respuesta
    nueva acuñaría un `tool_call_id` distinto que no coincidiría con el
    aprobado y la tool jamás se ejecutaría-: la ejecuta directo con la
    tool/args que quedaron guardados en Redis."""
    from edecan_core.tools import ToolResult

    import edecan_api.routers.conversations as conversations_module

    tool_calls: list[dict] = []

    class FakeDangerousTool:
        name = "enviar_correo"
        dangerous = True

        async def run(self, ctx, args):
            tool_calls.append(args)
            return ToolResult(content="Correo enviado a ana@example.com")

    class FakeRegistry:
        def get(self, name: str):
            return FakeDangerousTool() if name == "enviar_correo" else None

    monkeypatch.setattr(conversations_module, "get_tool_registry", lambda request: FakeRegistry())

    class ScriptedAgent:
        """Simula el turno original: el LLM pide `enviar_correo` y el agente
        detiene el turno porque es `dangerous` y no está pre-aprobada."""

        def __init__(self, llm_router, registry) -> None:
            pass

        async def run_turn(
            self, *, ctx, persona, history, user_text, flags, extra_tools=None, seleccion=None
        ):
            yield {
                "type": "confirmation_required",
                "tool_call_id": "toolu_original_001",
                "name": "enviar_correo",
                "args": {"to": "ana@example.com", "body": "hola"},
            }

    monkeypatch.setattr(conversations_module, "Agent", ScriptedAgent)

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key="hosted_basic")
    conversation_id = await _create_conversation(client, headers)

    first = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": "Mándale un correo a Ana"},
        headers=headers,
    )
    assert first.status_code == 200
    assert "event: confirmation.required" in first.text
    assert tool_calls == []  # el turno se detuvo: todavía no se ejecutó nada

    approve = await client.post(
        f"/v1/conversations/{conversation_id}/confirm",
        json={"tool_call_id": "toolu_original_001", "approved": True},
        headers=headers,
    )

    assert approve.status_code == 200
    body = approve.text
    assert "event: tool.start" in body
    assert "event: tool.end" in body
    assert "event: message.done" in body
    # La tool SÍ se ejecutó, con los args que el modelo había propuesto.
    assert tool_calls == [{"to": "ana@example.com", "body": "hola"}]

    conversation_uuid = uuid.UUID(conversation_id)
    messages = fake_repo.messages[conversation_uuid]
    assert messages[-1]["role"] == "assistant"
    assert "Correo enviado" in messages[-1]["content"]["text"]

    # De un solo uso: repetir la confirmación con el mismo `tool_call_id` no
    # encuentra nada pendiente y NO vuelve a ejecutar la tool.
    replay = await client.post(
        f"/v1/conversations/{conversation_id}/confirm",
        json={"tool_call_id": "toolu_original_001", "approved": True},
        headers=headers,
    )
    assert replay.status_code == 409
    assert len(tool_calls) == 1


async def test_confirm_continua_lote_compuesto_sin_perder_ni_duplicar_acciones(
    client, fake_repo, monkeypatch
) -> None:
    from edecan_core.tools import ToolResult
    from edecan_schemas import PendingAgentTurn

    import edecan_api.routers.conversations as conversations_module

    executions: dict[str, list[dict]] = {
        "enviar_correo": [],
        "revisar_documento": [],
        "crear_recordatorio": [],
    }

    class FakeTool:
        dangerous = False
        requires_flags = frozenset()

        def __init__(self, name: str, *, dangerous: bool = False) -> None:
            self.name = name
            self.dangerous = dangerous

        async def run(self, ctx, args):
            executions[self.name].append(args)
            return ToolResult(content=f"{self.name}: ok")

    tools = {
        "enviar_correo": FakeTool("enviar_correo", dangerous=True),
        "revisar_documento": FakeTool("revisar_documento"),
        "crear_recordatorio": FakeTool("crear_recordatorio"),
    }

    class FakeRegistry:
        def get(self, name: str):
            return tools.get(name)

    monkeypatch.setattr(conversations_module, "get_tool_registry", lambda request: FakeRegistry())

    pending = PendingAgentTurn(
        messages=[
            {"role": "user", "content": "Envía, revisa y recuérdame."},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "mail_1",
                        "name": "enviar_correo",
                        "input": {"to": "ana@example.com"},
                    },
                    {
                        "type": "tool_use",
                        "id": "doc_1",
                        "name": "revisar_documento",
                        "input": {"id": "doc-7"},
                    },
                    {
                        "type": "tool_use",
                        "id": "rem_1",
                        "name": "crear_recordatorio",
                        "input": {"texto": "Pagar mañana"},
                    },
                ],
            },
        ],
        tool_calls=[
            {"id": "mail_1", "name": "enviar_correo", "arguments": {"to": "ana@example.com"}},
            {"id": "doc_1", "name": "revisar_documento", "arguments": {"id": "doc-7"}},
            {
                "id": "rem_1",
                "name": "crear_recordatorio",
                "arguments": {"texto": "Pagar mañana"},
            },
        ],
        operational_tool_names=list(tools),
        usage={"input_tokens": 5, "output_tokens": 2},
        iteration=0,
        accumulated_text="Voy a hacerlo. ",
        system_prompt="Sistema original",
    )

    class ScriptedAgent:
        def __init__(self, llm_router, registry) -> None:
            self.registry = registry

        async def run_turn(self, **kwargs):
            yield {
                "type": "confirmation_required",
                "tool_call_id": "mail_1",
                "name": "enviar_correo",
                "args": {"to": "ana@example.com"},
                "pending_turn": pending.model_dump(),
            }

        async def resume_turn(
            self,
            *,
            ctx,
            pending,
            approved_tool_call_id,
            flags,
            extra_tools=None,
            seleccion=None,
        ):
            assert approved_tool_call_id == "mail_1"
            assert pending.system_prompt == "Sistema original"
            for call in pending.tool_calls:
                tool = self.registry.get(call.name)
                yield {"type": "tool_start", "name": call.name, "args": call.arguments}
                result = await tool.run(ctx, call.arguments)
                yield {
                    "type": "tool_end",
                    "name": call.name,
                    "result_preview": result.content,
                }
            yield {"type": "text_delta", "text": "Todo listo."}
            yield {"type": "done", "usage": {"input_tokens": 7, "output_tokens": 4}}

    monkeypatch.setattr(conversations_module, "Agent", ScriptedAgent)

    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    conversation_id = await _create_conversation(client, headers)
    first = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": "Envía, revisa y recuérdame."},
        headers=headers,
    )
    assert first.status_code == 200
    assert "pending_turn" not in first.text
    assert all(not calls for calls in executions.values())

    approve = await client.post(
        f"/v1/conversations/{conversation_id}/confirm",
        json={"tool_call_id": "mail_1", "approved": True},
        headers=headers,
    )
    assert approve.status_code == 200
    assert executions == {
        "enviar_correo": [{"to": "ana@example.com"}],
        "revisar_documento": [{"id": "doc-7"}],
        "crear_recordatorio": [{"texto": "Pagar mañana"}],
    }
    saved = fake_repo.messages[uuid.UUID(conversation_id)][-1]
    assert saved["content"] == {"text": "Voy a hacerlo. Todo listo."}
    assert len(saved["tool_calls"]) == 6

    replay = await client.post(
        f"/v1/conversations/{conversation_id}/confirm",
        json={"tool_call_id": "mail_1", "approved": True},
        headers=headers,
    )
    assert replay.status_code == 409
    assert sum(len(calls) for calls in executions.values()) == 3


async def test_pending_confirmation_uses_atomic_getdel(fake_redis, monkeypatch) -> None:
    import edecan_api.routers.conversations as conversations_module

    tenant_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    await conversations_module._store_pending_confirmation(
        fake_redis,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        tool_call_id="atomic_1",
        name="enviar_correo",
        args={},
    )

    async def forbidden(*args, **kwargs):
        raise AssertionError("La confirmación debe consumirse con GETDEL, no GET + DELETE.")

    monkeypatch.setattr(fake_redis, "get", forbidden)
    monkeypatch.setattr(fake_redis, "delete", forbidden)
    popped = await conversations_module._pop_pending_confirmation(
        fake_redis,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        tool_call_id="atomic_1",
    )
    assert popped == {"name": "enviar_correo", "args": {}}
    assert (
        await conversations_module._pop_pending_confirmation(
            fake_redis,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            tool_call_id="atomic_1",
        )
        is None
    )


async def test_continuation_can_request_a_future_dangerous_confirmation(
    client, fake_redis, monkeypatch
) -> None:
    from edecan_core.tools import ToolResult
    from edecan_schemas import PendingAgentTurn

    import edecan_api.routers.conversations as conversations_module

    executions = {"enviar_correo": 0, "preparar_pago": 0}

    class FakeDangerousTool:
        dangerous = True
        requires_flags = frozenset()

        def __init__(self, name: str) -> None:
            self.name = name

        async def run(self, ctx, args):
            executions[self.name] += 1
            return ToolResult(content=f"{self.name}: ok")

    tools = {
        "enviar_correo": FakeDangerousTool("enviar_correo"),
        "preparar_pago": FakeDangerousTool("preparar_pago"),
    }

    class FakeRegistry:
        def get(self, name: str):
            return tools.get(name)

    class ScriptedAgent:
        def __init__(self, llm_router, registry) -> None:
            self.registry = registry

        async def resume_turn(
            self,
            *,
            ctx,
            pending,
            approved_tool_call_id,
            flags,
            extra_tools=None,
            seleccion=None,
        ):
            call = next(call for call in pending.tool_calls if call.id == approved_tool_call_id)
            tool = self.registry.get(call.name)
            yield {"type": "tool_start", "name": call.name, "args": call.arguments}
            result = await tool.run(ctx, call.arguments)
            yield {
                "type": "tool_end",
                "name": call.name,
                "result_preview": result.content,
            }
            if approved_tool_call_id == "mail_first":
                next_pending = PendingAgentTurn(
                    messages=[
                        *pending.messages,
                        {
                            "role": "tool",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "mail_first",
                                    "content": result.content,
                                }
                            ],
                        },
                        {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "pay_future",
                                    "name": "preparar_pago",
                                    "input": {"monto": 20},
                                }
                            ],
                        },
                    ],
                    tool_calls=[
                        {
                            "id": "pay_future",
                            "name": "preparar_pago",
                            "arguments": {"monto": 20},
                        }
                    ],
                    operational_tool_names=list(tools),
                    iteration=1,
                    tool_log=[
                        {"type": "tool_start", "name": "enviar_correo", "args": {}},
                        {
                            "type": "tool_end",
                            "name": "enviar_correo",
                            "result_preview": result.content,
                        },
                    ],
                )
                yield {
                    "type": "confirmation_required",
                    "tool_call_id": "pay_future",
                    "name": "preparar_pago",
                    "args": {"monto": 20},
                    "pending_turn": next_pending.model_dump(),
                }
                return
            yield {"type": "text_delta", "text": "Ambas acciones completadas."}
            yield {"type": "done", "usage": {}}

    monkeypatch.setattr(conversations_module, "get_tool_registry", lambda request: FakeRegistry())
    monkeypatch.setattr(conversations_module, "Agent", ScriptedAgent)
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    conversation_id = await _create_conversation(client, headers)
    first_pending = PendingAgentTurn(
        messages=[
            {"role": "user", "content": "Envía el correo y luego prepara el pago."},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "mail_first",
                        "name": "enviar_correo",
                        "input": {},
                    }
                ],
            },
        ],
        tool_calls=[{"id": "mail_first", "name": "enviar_correo", "arguments": {}}],
        operational_tool_names=list(tools),
        iteration=0,
    )
    await conversations_module._store_pending_confirmation(
        fake_redis,
        tenant_id=tenant_id,
        conversation_id=uuid.UUID(conversation_id),
        tool_call_id="mail_first",
        name="enviar_correo",
        args={},
        pending_turn=first_pending,
    )

    first_approval = await client.post(
        f"/v1/conversations/{conversation_id}/confirm",
        json={"tool_call_id": "mail_first", "approved": True},
        headers=headers,
    )
    assert first_approval.status_code == 200
    assert "pay_future" in first_approval.text
    assert executions == {"enviar_correo": 1, "preparar_pago": 0}

    second_approval = await client.post(
        f"/v1/conversations/{conversation_id}/confirm",
        json={"tool_call_id": "pay_future", "approved": True},
        headers=headers,
    )
    assert second_approval.status_code == 200
    assert "event: message.done" in second_approval.text
    assert executions == {"enviar_correo": 1, "preparar_pago": 1}


async def test_confirm_continuation_fails_closed_on_flag_downgrade(
    client, fake_redis, monkeypatch
) -> None:
    from edecan_schemas import PendingAgentTurn

    import edecan_api.routers.conversations as conversations_module

    executions: list[dict] = []

    class FlaggedTool:
        name = "enviar_correo"
        dangerous = True
        requires_flags = frozenset({"capability.disabled_after_request"})

        async def run(self, ctx, args):
            executions.append(args)
            raise AssertionError("No debe ejecutarse tras perder su flag.")

    class FakeRegistry:
        def get(self, name: str):
            return FlaggedTool() if name == "enviar_correo" else None

    monkeypatch.setattr(conversations_module, "get_tool_registry", lambda request: FakeRegistry())
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    conversation_id = await _create_conversation(client, headers)
    pending = PendingAgentTurn(
        messages=[{"role": "user", "content": "envíalo"}],
        tool_calls=[{"id": "mail_flag", "name": "enviar_correo", "arguments": {}}],
        operational_tool_names=["enviar_correo"],
        iteration=0,
    )
    await conversations_module._store_pending_confirmation(
        fake_redis,
        tenant_id=tenant_id,
        conversation_id=uuid.UUID(conversation_id),
        tool_call_id="mail_flag",
        name="enviar_correo",
        args={},
        pending_turn=pending,
    )

    response = await client.post(
        f"/v1/conversations/{conversation_id}/confirm",
        json={"tool_call_id": "mail_flag", "approved": True},
        headers=headers,
    )
    assert response.status_code == 403
    assert executions == []


async def test_confirm_continuation_fails_closed_if_tool_was_removed(
    client, fake_redis, monkeypatch
) -> None:
    from edecan_schemas import PendingAgentTurn

    import edecan_api.routers.conversations as conversations_module

    class EmptyRegistry:
        def get(self, name: str):
            return None

    monkeypatch.setattr(conversations_module, "get_tool_registry", lambda request: EmptyRegistry())
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    conversation_id = await _create_conversation(client, headers)
    pending = PendingAgentTurn(
        messages=[{"role": "user", "content": "envíalo"}],
        tool_calls=[{"id": "mail_removed", "name": "enviar_correo", "arguments": {}}],
        operational_tool_names=["enviar_correo"],
        iteration=0,
    )
    await conversations_module._store_pending_confirmation(
        fake_redis,
        tenant_id=tenant_id,
        conversation_id=uuid.UUID(conversation_id),
        tool_call_id="mail_removed",
        name="enviar_correo",
        args={},
        pending_turn=pending,
    )

    response = await client.post(
        f"/v1/conversations/{conversation_id}/confirm",
        json={"tool_call_id": "mail_removed", "approved": True},
        headers=headers,
    )
    assert response.status_code == 409


async def test_confirm_ctx_lleva_los_flags_del_plan_del_tenant(client, monkeypatch) -> None:
    """Mismo `_build_ctx` que arma `POST /messages` arma también el `ctx` de
    `POST /confirm` -ver regresión en `test_post_message_ctx_lleva_los_flags_del_plan_del_tenant`-,
    así que la tool `dangerous` que se ejecuta tras aprobar también debe ver
    `ctx.extras["flags"]` con los flags reales del plan del tenant."""
    from edecan_core.tools import ToolResult

    import edecan_api.routers.conversations as conversations_module

    seen_flags: list[dict] = []

    class FakeDangerousTool:
        name = "enviar_correo"
        dangerous = True

        async def run(self, ctx, args):
            seen_flags.append(ctx.extras.get("flags"))
            return ToolResult(content="Correo enviado")

    class FakeRegistry:
        def get(self, name: str):
            return FakeDangerousTool() if name == "enviar_correo" else None

    monkeypatch.setattr(conversations_module, "get_tool_registry", lambda request: FakeRegistry())

    class ScriptedAgent:
        def __init__(self, llm_router, registry) -> None:
            pass

        async def run_turn(
            self, *, ctx, persona, history, user_text, flags, extra_tools=None, seleccion=None
        ):
            yield {
                "type": "confirmation_required",
                "tool_call_id": "toolu_original_003",
                "name": "enviar_correo",
                "args": {"to": "ana@example.com"},
            }

    monkeypatch.setattr(conversations_module, "Agent", ScriptedAgent)

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key="hosted_basic")
    conversation_id = await _create_conversation(client, headers)

    await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": "Mándale un correo a Ana"},
        headers=headers,
    )
    approve = await client.post(
        f"/v1/conversations/{conversation_id}/confirm",
        json={"tool_call_id": "toolu_original_003", "approved": True},
        headers=headers,
    )

    assert approve.status_code == 200
    assert len(seen_flags) == 1
    assert seen_flags[0] is not None
    assert seen_flags[0].get("models.premium") is True


async def test_confirm_permite_tool_dangerous_cuyo_flag_de_plan_si_esta_satisfecho(
    client, monkeypatch
) -> None:
    """Contraparte de la prueba anterior -mismo par tool/flag, pero un plan
    que SÍ incluye `commerce.orders` (`hosted_pro`)-: confirma que el chequeo
    nuevo no bloquea el camino legítimo."""
    from edecan_core.tools import ToolResult
    from edecan_schemas.plans import FLAG_COMMERCE_ORDERS

    import edecan_api.routers.conversations as conversations_module

    tool_calls: list[dict] = []

    class FakePrepararPagoTool:
        name = "preparar_pago"
        dangerous = True
        requires_flags = frozenset({FLAG_COMMERCE_ORDERS})

        async def run(self, ctx, args):
            tool_calls.append(args)
            return ToolResult(content="Pago preparado")

    class FakeRegistry:
        def get(self, name: str):
            return FakePrepararPagoTool() if name == "preparar_pago" else None

    monkeypatch.setattr(conversations_module, "get_tool_registry", lambda request: FakeRegistry())

    class ScriptedAgent:
        def __init__(self, llm_router, registry) -> None:
            pass

        async def run_turn(
            self, *, ctx, persona, history, user_text, flags, extra_tools=None, seleccion=None
        ):
            yield {
                "type": "confirmation_required",
                "tool_call_id": "toolu_pago_permitido_001",
                "name": "preparar_pago",
                "args": {"monto": 100, "moneda": "USD"},
            }

    monkeypatch.setattr(conversations_module, "Agent", ScriptedAgent)

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key="hosted_pro")
    conversation_id = await _create_conversation(client, headers)

    await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": "Prepara un pago de 100 USD"},
        headers=headers,
    )

    approve = await client.post(
        f"/v1/conversations/{conversation_id}/confirm",
        json={"tool_call_id": "toolu_pago_permitido_001", "approved": True},
        headers=headers,
    )

    assert approve.status_code == 200
    assert tool_calls == [{"monto": 100, "moneda": "USD"}]


async def test_confirm_without_pending_confirmation_returns_409(client) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key="hosted_basic")
    conversation_id = await _create_conversation(client, headers)

    response = await client.post(
        f"/v1/conversations/{conversation_id}/confirm",
        json={"tool_call_id": "nunca-existió", "approved": True},
        headers=headers,
    )
    assert response.status_code == 409


async def test_confirm_declined_does_not_execute_tool(client, fake_repo, monkeypatch) -> None:
    import edecan_api.routers.conversations as conversations_module

    tool_calls: list[dict] = []

    class FakeDangerousTool:
        name = "enviar_correo"
        dangerous = True

        async def run(self, ctx, args):
            tool_calls.append(args)
            raise AssertionError("No debería ejecutarse: el usuario rechazó la acción.")

    class FakeRegistry:
        def get(self, name: str):
            return FakeDangerousTool() if name == "enviar_correo" else None

    monkeypatch.setattr(conversations_module, "get_tool_registry", lambda request: FakeRegistry())

    class ScriptedAgent:
        def __init__(self, llm_router, registry) -> None:
            pass

        async def run_turn(
            self, *, ctx, persona, history, user_text, flags, extra_tools=None, seleccion=None
        ):
            yield {
                "type": "confirmation_required",
                "tool_call_id": "toolu_original_002",
                "name": "enviar_correo",
                "args": {"to": "ana@example.com"},
            }

    monkeypatch.setattr(conversations_module, "Agent", ScriptedAgent)

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key="hosted_basic")
    conversation_id = await _create_conversation(client, headers)

    await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": "Mándale un correo a Ana"},
        headers=headers,
    )

    decline = await client.post(
        f"/v1/conversations/{conversation_id}/confirm",
        json={"tool_call_id": "toolu_original_002", "approved": False},
        headers=headers,
    )

    assert decline.status_code == 200
    assert "no realizo esa acción" in decline.text
    assert tool_calls == []

    approve_after_rejection = await client.post(
        f"/v1/conversations/{conversation_id}/confirm",
        json={"tool_call_id": "toolu_original_002", "approved": True},
        headers=headers,
    )
    assert approve_after_rejection.status_code == 409
    assert tool_calls == []


# ---------------------------------------------------------------------------
# Selector de modelos del chat: PUT /model, override por turno y gate de ceguera
# ---------------------------------------------------------------------------


def _un_principal_con_esfuerzo() -> str:
    """Un id real del catálogo que razone (para la fila Esfuerzo)."""

    from edecan_llm.task_router import modelos_chat_disponibles

    return next(
        row["id"]
        for row in modelos_chat_disponibles()
        if row["principal"] and row["soporta_esfuerzo"]
    )


def _un_modelo_ciego() -> str | None:
    from edecan_llm.task_router import modelos_chat_disponibles

    return next(
        (row["id"] for row in modelos_chat_disponibles() if not row["ve_imagenes"]),
        None,
    )


class _SelectionCapturingAgent:
    """Agente que solo anota la `SeleccionDeModelo` con la que se corrió el turno."""

    capturadas: list[object] = []

    def __init__(self, llm_router, registry) -> None:
        pass

    async def run_turn(self, **kwargs):
        _SelectionCapturingAgent.capturadas.append(kwargs.get("seleccion"))
        yield {"type": "text_delta", "text": "listo"}
        yield {"type": "done", "usage": {}}


async def test_conversacion_nueva_nace_en_automatico(client) -> None:
    """Estrenar el selector no cambia nada: sin selección la cadena de siempre
    (`WORKERS_AI_CHAT_MODEL` -> `MODELO_POR_DEFECTO`) sigue decidiendo."""

    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    conversation_id = await _create_conversation(client, headers)

    detalle = await client.get(f"/v1/conversations/{conversation_id}", headers=headers)
    listado = await client.get("/v1/conversations", headers=headers)

    assert detalle.json()["model"] is None
    assert detalle.json()["effort"] is None
    assert listado.json()[0]["model"] is None
    assert listado.json()[0]["effort"] is None


async def test_put_model_persiste_y_se_restaura_al_reabrir(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    conversation_id = await _create_conversation(client, headers)
    elegido = _un_principal_con_esfuerzo()

    puesto = await client.put(
        f"/v1/conversations/{conversation_id}/model",
        json={"model": elegido, "effort": "alto"},
        headers=headers,
    )

    assert puesto.status_code == 200
    assert puesto.json() == {"model": elegido, "effort": "alto"}
    # La pastilla del composer se reconstruye desde el GET, en cualquier equipo.
    detalle = await client.get(f"/v1/conversations/{conversation_id}", headers=headers)
    assert detalle.json()["model"] == elegido
    assert detalle.json()["effort"] == "alto"


async def test_put_model_con_null_vuelve_a_automatico(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    conversation_id = await _create_conversation(client, headers)
    await client.put(
        f"/v1/conversations/{conversation_id}/model",
        json={"model": _un_principal_con_esfuerzo(), "effort": "bajo"},
        headers=headers,
    )

    limpiado = await client.put(
        f"/v1/conversations/{conversation_id}/model",
        json={"model": None, "effort": None},
        headers=headers,
    )

    assert limpiado.json() == {"model": None, "effort": None}


async def test_put_model_guarda_el_esfuerzo_aunque_el_modelo_activo_no_lo_soporte(
    client,
) -> None:
    """Cambiar de Copla a Oda tiene que recordar el nivel previo: el gate del
    Esfuerzo es al APLICAR el turno, no al guardar."""

    from edecan_llm.task_router import modelo_chat_por_defecto

    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    conversation_id = await _create_conversation(client, headers)

    puesto = await client.put(
        f"/v1/conversations/{conversation_id}/model",
        json={"model": modelo_chat_por_defecto(), "effort": "alto"},
        headers=headers,
    )

    assert puesto.json() == {"model": modelo_chat_por_defecto(), "effort": "alto"}


async def test_put_model_rechaza_un_modelo_fuera_del_catalogo(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    conversation_id = await _create_conversation(client, headers)

    # glm-5.2 está descartado con evidencia (42 s por vuelta del ciclo
    # agente-herramientas): que esté en el catálogo del IDE no lo habilita acá.
    rechazado = await client.put(
        f"/v1/conversations/{conversation_id}/model",
        json={"model": "@cf/zai-org/glm-5.2", "effort": None},
        headers=headers,
    )
    esfuerzo_invalido = await client.put(
        f"/v1/conversations/{conversation_id}/model",
        json={"model": None, "effort": "turbo"},
        headers=headers,
    )

    assert rechazado.status_code == 422
    assert esfuerzo_invalido.status_code == 422


async def test_put_model_es_user_y_tenant_scoped(client) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id)
    conversation_id = await _create_conversation(client, headers)
    elegido = _un_principal_con_esfuerzo()

    otro_usuario = await client.put(
        f"/v1/conversations/{conversation_id}/model",
        json={"model": elegido, "effort": None},
        headers=auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id),
    )
    otro_tenant = await client.put(
        f"/v1/conversations/{conversation_id}/model",
        json={"model": elegido, "effort": None},
        headers=auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4()),
    )

    assert otro_usuario.status_code == 404
    assert otro_tenant.status_code == 404


async def test_el_turno_corre_con_el_modelo_persistido_por_el_selector(client, monkeypatch) -> None:
    import edecan_api.routers.conversations as conversations_module

    _SelectionCapturingAgent.capturadas = []
    monkeypatch.setattr(conversations_module, "Agent", _SelectionCapturingAgent)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    conversation_id = await _create_conversation(client, headers)
    elegido = _un_principal_con_esfuerzo()
    await client.put(
        f"/v1/conversations/{conversation_id}/model",
        json={"model": elegido, "effort": "bajo"},
        headers=headers,
    )

    respuesta = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": "hola"},
        headers=headers,
    )

    assert respuesta.status_code == 200
    seleccion = _SelectionCapturingAgent.capturadas[-1]
    assert seleccion.modelo == elegido
    assert seleccion.esfuerzo == "bajo"


async def test_sin_seleccion_el_turno_corre_en_automatico(client, monkeypatch) -> None:
    import edecan_api.routers.conversations as conversations_module

    _SelectionCapturingAgent.capturadas = []
    monkeypatch.setattr(conversations_module, "Agent", _SelectionCapturingAgent)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    conversation_id = await _create_conversation(client, headers)

    await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": "hola"},
        headers=headers,
    )

    seleccion = _SelectionCapturingAgent.capturadas[-1]
    assert seleccion.modelo is None
    assert seleccion.esfuerzo is None


async def test_el_body_del_turno_gana_y_tambien_persiste(client, fake_repo, monkeypatch) -> None:
    """Elegir-y-enviar en un solo gesto, sin carrera entre el PUT y el POST."""

    import edecan_api.routers.conversations as conversations_module

    _SelectionCapturingAgent.capturadas = []
    monkeypatch.setattr(conversations_module, "Agent", _SelectionCapturingAgent)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    conversation_id = await _create_conversation(client, headers)
    elegido = _un_principal_con_esfuerzo()

    await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": "hola", "model": elegido, "effort": "alto"},
        headers=headers,
    )

    assert _SelectionCapturingAgent.capturadas[-1].modelo == elegido
    assert _SelectionCapturingAgent.capturadas[-1].esfuerzo == "alto"
    fila = fake_repo.conversations[uuid.UUID(conversation_id)]
    assert fila["chat_model"] == elegido
    assert fila["chat_effort"] == "alto"


async def test_el_body_solo_con_esfuerzo_conserva_el_modelo_ya_elegido(
    client, fake_repo, monkeypatch
) -> None:
    import edecan_api.routers.conversations as conversations_module

    _SelectionCapturingAgent.capturadas = []
    monkeypatch.setattr(conversations_module, "Agent", _SelectionCapturingAgent)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    conversation_id = await _create_conversation(client, headers)
    elegido = _un_principal_con_esfuerzo()
    await client.put(
        f"/v1/conversations/{conversation_id}/model",
        json={"model": elegido, "effort": "bajo"},
        headers=headers,
    )

    await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": "hola", "effort": "alto"},
        headers=headers,
    )

    assert _SelectionCapturingAgent.capturadas[-1].modelo == elegido
    assert fake_repo.conversations[uuid.UUID(conversation_id)]["chat_effort"] == "alto"


async def test_el_esfuerzo_no_se_aplica_a_un_modelo_que_no_razona(
    client, fake_repo, monkeypatch
) -> None:
    """Se guarda, pero no se aplica: un control decorativo es exactamente lo
    que hace sentir que el asistente es incapaz."""

    from edecan_llm.task_router import modelo_chat_por_defecto

    import edecan_api.routers.conversations as conversations_module

    _SelectionCapturingAgent.capturadas = []
    monkeypatch.setattr(conversations_module, "Agent", _SelectionCapturingAgent)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    conversation_id = await _create_conversation(client, headers)
    copla = modelo_chat_por_defecto()

    await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": "hola", "model": copla, "effort": "alto"},
        headers=headers,
    )

    assert _SelectionCapturingAgent.capturadas[-1].modelo == copla
    assert _SelectionCapturingAgent.capturadas[-1].esfuerzo is None
    assert fake_repo.conversations[uuid.UUID(conversation_id)]["chat_effort"] == "alto"


async def test_post_message_rechaza_un_modelo_fuera_del_catalogo(client, monkeypatch) -> None:
    import edecan_api.routers.conversations as conversations_module

    class AgentMustNotRun:
        def __init__(self, llm_router, registry) -> None:
            raise AssertionError("Un modelo inválido no debe llegar a correr el turno.")

    monkeypatch.setattr(conversations_module, "Agent", AgentMustNotRun)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    conversation_id = await _create_conversation(client, headers)

    rechazado = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": "hola", "model": "@cf/zai-org/glm-5.2"},
        headers=headers,
    )

    assert rechazado.status_code == 422


async def test_una_imagen_con_modelo_ciego_degrada_el_turno_sin_tocar_la_seleccion(
    client, fake_repo, monkeypatch
) -> None:
    """Degradación con gracia y determinista: ese turno corre con el default con
    visión del catálogo y la selección persistida queda intacta."""

    from edecan_llm.task_router import modelo_chat_con_vision_por_defecto

    import edecan_api.routers.conversations as conversations_module

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    file_id = uuid.uuid4()
    image_bytes = b"\x89PNG\r\n\x1a\ncaptura"
    s3_key = f"tenants/{tenant_id}/files/{file_id}/captura.png"
    await fake_repo.create_file(
        tenant_id=tenant_id,
        user_id=user_id,
        file_id=file_id,
        s3_key=s3_key,
        filename="captura.png",
        mime="image/png",
        size_bytes=len(image_bytes),
        status="uploaded",
    )
    monkeypatch.setattr(
        conversations_module.aioboto3,
        "Session",
        lambda: _VisionS3Session({s3_key: image_bytes}),
    )
    _SelectionCapturingAgent.capturadas = []
    monkeypatch.setattr(conversations_module, "Agent", _SelectionCapturingAgent)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key="hosted_basic")
    conversation_id = await _create_conversation(client, headers)
    ciego = _un_modelo_ciego()
    if ciego is None:
        pytest.skip("el catálogo del chat ya no incluye modelos ciegos")
    await client.put(
        f"/v1/conversations/{conversation_id}/model",
        json={"model": ciego, "effort": None},
        headers=headers,
    )

    con_imagen = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": "¿Qué ves aquí?", "attachments": [str(file_id)]},
        headers=headers,
    )

    assert con_imagen.status_code == 200
    assert _SelectionCapturingAgent.capturadas[-1].modelo == modelo_chat_con_vision_por_defecto()
    # La selección NO cambia: el próximo turno sin imagen vuelve al elegido.
    assert fake_repo.conversations[uuid.UUID(conversation_id)]["chat_model"] == ciego
    await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": "y ahora sin imagen"},
        headers=headers,
    )
    assert _SelectionCapturingAgent.capturadas[-1].modelo == ciego


async def test_una_imagen_con_modelo_que_ve_no_degrada_nada(client, fake_repo, monkeypatch) -> None:
    import edecan_api.routers.conversations as conversations_module

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    file_id = uuid.uuid4()
    image_bytes = b"\x89PNG\r\n\x1a\ncaptura"
    s3_key = f"tenants/{tenant_id}/files/{file_id}/captura.png"
    await fake_repo.create_file(
        tenant_id=tenant_id,
        user_id=user_id,
        file_id=file_id,
        s3_key=s3_key,
        filename="captura.png",
        mime="image/png",
        size_bytes=len(image_bytes),
        status="uploaded",
    )
    monkeypatch.setattr(
        conversations_module.aioboto3,
        "Session",
        lambda: _VisionS3Session({s3_key: image_bytes}),
    )
    _SelectionCapturingAgent.capturadas = []
    monkeypatch.setattr(conversations_module, "Agent", _SelectionCapturingAgent)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key="hosted_basic")
    conversation_id = await _create_conversation(client, headers)
    vidente = _un_principal_con_esfuerzo()

    await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={
            "text": "¿Qué ves aquí?",
            "attachments": [str(file_id)],
            "model": vidente,
            "effort": "medio",
        },
        headers=headers,
    )

    assert _SelectionCapturingAgent.capturadas[-1].modelo == vidente
    assert _SelectionCapturingAgent.capturadas[-1].esfuerzo == "medio"


async def test_la_confirmacion_relee_la_seleccion_de_la_conversacion(client, monkeypatch) -> None:
    """El `/confirm` corre en otro request HTTP: sin releer las columnas el lote
    confirmado correría con el modelo automático en silencio."""

    from edecan_schemas import PendingAgentTurn

    import edecan_api.routers.conversations as conversations_module

    capturadas: list[object] = []
    pending = PendingAgentTurn(
        messages=[{"role": "user", "content": "Manda el correo."}],
        tool_calls=[{"id": "mail_1", "name": "enviar_correo", "arguments": {}}],
        operational_tool_names=["enviar_correo"],
        usage={"input_tokens": 1, "output_tokens": 1},
        iteration=0,
        accumulated_text="",
        system_prompt="Sistema original",
    )

    class ScriptedAgent:
        def __init__(self, llm_router, registry) -> None:
            pass

        async def run_turn(self, **kwargs):
            yield {
                "type": "confirmation_required",
                "tool_call_id": "mail_1",
                "name": "enviar_correo",
                "args": {},
                "pending_turn": pending.model_dump(),
            }

        async def resume_turn(self, **kwargs):
            capturadas.append(kwargs.get("seleccion"))
            yield {"type": "text_delta", "text": "Listo."}
            yield {"type": "done", "usage": {}}

    monkeypatch.setattr(conversations_module, "Agent", ScriptedAgent)
    monkeypatch.setattr(conversations_module, "_preflight_pending_turn", lambda **kwargs: None)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    conversation_id = await _create_conversation(client, headers)
    elegido = _un_principal_con_esfuerzo()
    await client.put(
        f"/v1/conversations/{conversation_id}/model",
        json={"model": elegido, "effort": "alto"},
        headers=headers,
    )
    await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": "Manda el correo."},
        headers=headers,
    )

    aprobado = await client.post(
        f"/v1/conversations/{conversation_id}/confirm",
        json={"tool_call_id": "mail_1", "approved": True},
        headers=headers,
    )

    assert aprobado.status_code == 200
    assert capturadas[-1].modelo == elegido
    assert capturadas[-1].esfuerzo == "alto"


async def test_card_generica_del_piloto_sdui_llega_por_sse_y_sobrevive_el_contrato(
    client, monkeypatch
) -> None:
    """E2E del paso 5 del plan de SDUI (piloto backend-only): una tool
    first-party arma una `GenericCardBlock` con el helper reutilizable
    `construir_card_generica` (`edecan_core.cards`), pasa por el mismo
    embudo de seguridad que ya protege a `social_draft`
    (`rich_blocks_from_tool_data`, `edecan_core.agent`), y el SSE de
    `tool.end` la trae en `blocks` junto con `blocks_version` -- sin haber
    tocado una sola línea de Swift. Cierra el ciclo decodificándola de
    vuelta con `ChatBlockAdapter`, el mismo contrato que usan iOS/Android/web."""
    from edecan_core.agent import rich_blocks_from_tool_data
    from edecan_core.cards import BotonCard, construir_card_generica
    from edecan_schemas import (
        ChatBlockAdapter,
        CopyTextAction,
        GenericCardBlock,
        SocialDraftBlock,
    )

    import edecan_api.routers.conversations as conversations_module

    image_id = uuid.uuid4()
    tarjeta = construir_card_generica(
        card_id="resumen-piloto",
        fallback_text="Borrador de LinkedIn: El crédito construye confianza.",
        kicker="LinkedIn",
        titulo="El crédito construye confianza",
        imagen=ArtifactRef(file_id=image_id, filename="post.png", mime="image/png"),
        cuerpo=["Un texto verdadero sobre crédito y confianza."],
        botones=[
            BotonCard(
                accion=CopyTextAction(
                    id="copiar-texto", label="Copiar texto", text="El copy completo."
                )
            )
        ],
    )
    # Presentación real de `empaquetar_borrador_social` con
    # `EDECAN_SDUI_CARD_PILOTO` encendida (ver `edecan_creative.social`): la
    # card NUEVA viaja ADEMÁS del `social_draft` de siempre, nunca en su
    # lugar. Se incluye aquí el `social_draft` real (no solo la card sola)
    # para que el conteo de `blocks` refleje exactamente ese camino --
    # `social_draft.artifacts` ya cubre el `file_id` de la imagen, así que el
    # enriquecimiento automático de `rich_blocks_from_tool_data` no vuelve a
    # envolverla en un `MediaBlock` suelto y duplicado.
    social_draft_dict = {
        "type": "social_draft",
        "fallback_text": "Borrador de LinkedIn: El crédito construye confianza."[:1000],
        "status": "ready",
        "platform": "linkedin",
        "target": None,
        "copy": "El copy completo.",
        "parts": ["El copy completo."],
        "alt_text": "",
        "offline_visual": False,
        "visual_warning": "",
        "artifacts": [{"file_id": str(image_id), "filename": "post.png", "mime": "image/png"}],
        "requires_human_confirmation": True,
    }
    # Mismo embudo que ya protege `social_draft` (paso 2 del plan): la card
    # solo se acuña porque su imagen apunta a un `file_id` que esta MISMA
    # tool call devolvió, y `crear_contenido_social` ya está en
    # `TOOLS_FIRST_PARTY_CON_ACCIONES_PRIVILEGIADAS`.
    bloques_acunados = rich_blocks_from_tool_data(
        {"artifacts": [{"file_id": str(image_id), "filename": "post.png", "mime": "image/png"}]},
        presentation=[social_draft_dict, tarjeta.model_dump(mode="json")],
        tool_name="crear_contenido_social",
    )
    assert len(bloques_acunados) == 2
    assert isinstance(bloques_acunados[0], SocialDraftBlock)
    assert isinstance(bloques_acunados[1], GenericCardBlock)

    class ScriptedAgent:
        def __init__(self, llm_router, registry) -> None:
            pass

        async def run_turn(
            self, *, ctx, persona, history, user_text, flags, extra_tools=None, seleccion=None
        ):
            yield {
                "type": "tool_start",
                "tool_call_id": "call-1",
                "name": "crear_contenido_social",
                "args": {},
            }
            yield {
                "type": "tool_end",
                "tool_call_id": "call-1",
                "name": "crear_contenido_social",
                "result_preview": "Card lista.",
                "artifacts": [
                    {"file_id": str(image_id), "filename": "post.png", "mime": "image/png"}
                ],
                "blocks_version": 1,
                "blocks": [bloque.model_dump(mode="json") for bloque in bloques_acunados],
            }
            yield {"type": "text_delta", "text": "Card lista."}
            yield {"type": "done", "usage": {}}

    monkeypatch.setattr(conversations_module, "Agent", ScriptedAgent)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    conversation_id = await _create_conversation(client, headers)

    response = await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        # A propósito NO usa palabras que disparen el atajo directo de
        # LinkedIn (`_es_pedido_directo_de_post_linkedin`): este test quiere
        # ejercitar el camino NORMAL por `Agent.run_turn` (`ScriptedAgent`),
        # no el atajo que llama a la tool real.
        json={"text": "Ayúdame a preparar la campaña de crédito de este mes."},
        headers=headers,
    )

    assert response.status_code == 200
    lines = response.text.splitlines()
    tool_end_payload = None
    for index, line in enumerate(lines):
        if line == "event: tool.end":
            data_line = lines[index + 1]
            assert data_line.startswith("data: ")
            tool_end_payload = json.loads(data_line[len("data: ") :])
            break
    assert tool_end_payload is not None, "el SSE no trajo un evento tool.end"

    assert tool_end_payload["blocks_version"] == 1
    assert [block["type"] for block in tool_end_payload["blocks"]] == ["social_draft", "card"]
    bloque_crudo = tool_end_payload["blocks"][1]

    # Sobrevive el viaje por el contrato: lo que llegó por SSE decodifica
    # limpio del lado del schema, el mismo `ChatBlockAdapter` que usan
    # iOS/Android/web.
    decodificado = ChatBlockAdapter.validate_python(bloque_crudo)
    assert isinstance(decodificado, GenericCardBlock)
    assert decodificado.card_id == "resumen-piloto"
    assert decodificado.fallback_text == "Borrador de LinkedIn: El crédito construye confianza."
    textos = [nodo for nodo in decodificado.raiz.hijos if getattr(nodo, "nodo", None) == "texto"]
    assert textos[0].contenido == "LinkedIn"
    assert textos[1].contenido == "El crédito construye confianza"
