"""`POST /v1/conversations/{id}/messages` — smoke SSE con `Agent` monkeypatched,
y cuota diaria de mensajes -> 429 (ARCHITECTURE.md §10.12, §10.7, §10.13)."""

from __future__ import annotations

import json
import uuid

from conftest import auth_headers
from edecan_schemas import ArtifactRef, ToolEndEvent


def test_tool_end_with_artifact_is_json_serializable_for_history() -> None:
    import edecan_api.routers.conversations as conversations_module

    file_id = uuid.uuid4()
    event = ToolEndEvent(
        name="crear_artefactos",
        result_preview="Creado",
        artifacts=[ArtifactRef(file_id=file_id, filename="reporte.pdf", mime="application/pdf")],
    )

    serialized = conversations_module._event_to_dict(event)

    assert serialized["artifacts"][0]["file_id"] == str(file_id)
    json.dumps(serialized)  # regresión: antes lanzaba UUID is not JSON serializable


async def _create_conversation(client, headers: dict[str, str]) -> str:
    response = await client.post("/v1/conversations", json={"channel": "web"}, headers=headers)
    assert response.status_code == 201
    return response.json()["id"]


async def test_post_message_streams_sse_and_persists_assistant_turn(
    client, fake_repo, monkeypatch
) -> None:
    import edecan_api.routers.conversations as conversations_module

    class ScriptedAgent:
        """Agente falso: emite `text_delta` x2 y `done`, tal como pide el WP."""

        def __init__(self, llm_router, registry) -> None:
            self.llm_router = llm_router
            self.registry = registry

        async def run_turn(self, *, ctx, persona, history, user_text, flags, extra_tools=None):
            assert user_text == "Hola, ¿cómo estás?"
            yield {"type": "text_delta", "text": "Hola "}
            yield {"type": "text_delta", "text": "mundo"}
            yield {"type": "done", "usage": {"input_tokens": 12, "output_tokens": 7}}

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
    assert messages[-1]["content"] == {"text": "Hola mundo"}
    assert messages[-1]["tokens_in"] == 12
    assert messages[-1]["tokens_out"] == 7

    kinds = [event["kind"] for event in fake_repo.usage_events]
    assert kinds.count("messages") == 1
    llm_events = [e for e in fake_repo.usage_events if e["kind"] == "llm_tokens"]
    assert len(llm_events) == 1
    assert llm_events[0]["quantity"] == 19  # 12 + 7


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

        async def run_turn(self, *, ctx, persona, history, user_text, flags, extra_tools=None):
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

        async def run_turn(self, *, ctx, persona, history, user_text, flags, extra_tools=None):
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
            self, *, ctx, pending, approved_tool_call_id, flags, extra_tools=None
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
    headers = auth_headers(
        user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic"
    )
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
            self, *, ctx, pending, approved_tool_call_id, flags, extra_tools=None
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
    headers = auth_headers(
        user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic"
    )
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
    headers = auth_headers(
        user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic"
    )
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
    headers = auth_headers(
        user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic"
    )
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

        async def run_turn(self, *, ctx, persona, history, user_text, flags, extra_tools=None):
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

        async def run_turn(self, *, ctx, persona, history, user_text, flags, extra_tools=None):
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

        async def run_turn(self, *, ctx, persona, history, user_text, flags, extra_tools=None):
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
