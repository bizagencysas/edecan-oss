from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from edecan_core.session import UnifiedSessionState
from edecan_schemas import DoneEvent, PersonaConfig, TextDeltaEvent

from edecan_api.deps import CurrentUser, TenantCtx
from edecan_api.voice_turn_service import execute_voice_text_turn


class _Repo:
    def __init__(self) -> None:
        self.messages = []
        self.usage = []

    async def get_conversation(self, **_kwargs):
        return {"id": str(_kwargs["conversation_id"]), "context_cleared_at": None}

    async def list_messages(self, **_kwargs):
        return []

    async def get_persona(self, **_kwargs):
        return {}

    async def add_message(self, **kwargs):
        self.messages.append(kwargs)

    async def add_usage_event(self, **kwargs):
        self.usage.append(kwargs)


async def test_voice_service_reusa_agent_y_persiste_turno(monkeypatch):
    import edecan_api.routers.conversations as conversations
    import edecan_api.routers.perfil as perfil
    import edecan_api.routers.persona as persona_router
    import edecan_api.voice_turn_service as service

    class _Agent:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run_turn(self, **_kwargs):
            yield TextDeltaEvent(text="Respuesta por voz")
            yield DoneEvent(usage={"input_tokens": 4, "output_tokens": 3})

    monkeypatch.setattr(service, "Agent", _Agent)
    monkeypatch.setattr(conversations, "_build_ctx", lambda **_kwargs: SimpleNamespace(extras={}))
    monkeypatch.setattr(conversations, "_extra_conversation_tools", lambda *_args: _empty())
    monkeypatch.setattr(conversations, "_tools_con_pregunta_pendiente", lambda *_args: [])
    monkeypatch.setattr(perfil, "profile_context_for", lambda *_args: _empty_text())
    monkeypatch.setattr(persona_router, "persona_from_row", lambda *_args: PersonaConfig())
    monkeypatch.setattr(service, "enqueue", _empty_enqueue)
    monkeypatch.setattr(
        service,
        "load_unified_session",
        lambda *_args, **_kwargs: _session_state(),
    )
    monkeypatch.setattr(service, "save_unified_session", _empty_save)

    tenant_id, user_id, conversation_id = uuid4(), uuid4(), uuid4()
    current_user = CurrentUser(
        user_id=user_id,
        tenant=TenantCtx(tenant_id=tenant_id, plan_key="hosted_basic", flags={}),
    )
    repo = _Repo()
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(tool_registry=object()))
    )
    settings = SimpleNamespace(
        CHAT_CONTEXT_ENABLED=False,
        CHAT_CONTEXT_RECENT_MESSAGES=10,
        CHAT_CONTEXT_MAX_MESSAGES=50,
        CHAT_CONTEXT_MAX_CHARS=20_000,
    )

    result = await execute_voice_text_turn(
        request=request,
        session=object(),
        repo=repo,
        vault=object(),
        current_user=current_user,
        settings=settings,
        llm_router=object(),
        conversation_id=conversation_id,
        user_text="  Hola **mundo**  ",
    )

    assert result.text == "Respuesta por voz"
    assert [row["role"] for row in repo.messages] == ["user", "assistant"]
    assert repo.messages[0]["content"] == {"text": "Hola **mundo**"}
    assert repo.messages[1]["tokens_in"] == 4
    assert {row["kind"] for row in repo.usage} == {"llm_tokens", "messages"}


async def _empty() -> list:
    return []


async def _empty_text() -> str:
    return ""


async def _empty_enqueue(*_args, **_kwargs):
    return None


async def _empty_save(*_args, **_kwargs):
    return None


async def _session_state() -> UnifiedSessionState:
    return UnifiedSessionState("s", "t", "u", "c")
