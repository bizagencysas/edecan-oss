"""`POST /v1/conversations/{id}/messages` — smoke SSE con `Agent` monkeypatched,
y cuota diaria de mensajes -> 429 (ARCHITECTURE.md §10.12, §10.7, §10.13)."""

from __future__ import annotations

import uuid

from conftest import auth_headers


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
