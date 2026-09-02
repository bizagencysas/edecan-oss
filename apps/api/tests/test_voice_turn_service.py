from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from edecan_core.session import UnifiedSessionState
from edecan_schemas import DoneEvent, PersonaConfig, TextDeltaEvent

from edecan_api.deps import CurrentUser, TenantCtx
from edecan_api.voice_orchestration import (
    VoiceDelegation,
    VoiceOrchestration,
    resolve_worker_id,
    route_voice_intent,
)
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


# ---------------------------------------------------------------------------
# Router de intención determinista (Wave I: voice orchestration)
# ---------------------------------------------------------------------------


def test_route_dile_a_target_delega():
    result = route_voice_intent("dile al Developer que compruebe el deployment")
    assert result is not None
    assert len(result.delegated) == 1
    item = result.delegated[0]
    assert item.target == "Developer"
    assert item.instruction == "compruebe el deployment"
    assert item.kind == "agent"
    assert item.requires_approval is False
    assert result.reply_text == ""


def test_route_multiple_clauses_divide_delegado_y_keep_talking():
    result = route_voice_intent(
        "Revisa quién me escribió hoy, dile al Developer que compruebe el deployment "
        "y busca un restaurante para esta noche"
    )
    assert result is not None
    assert len(result.delegated) == 1
    assert result.delegated[0].target == "Developer"
    assert result.delegated[0].instruction == "compruebe el deployment"
    assert "Revisa quién me escribió hoy" in result.reply_text
    assert "busca un restaurante para esta noche" in result.reply_text


def test_route_pon_a_target_a_verbo():
    result = route_voice_intent("pon a María a revisar el correo")
    assert result is not None
    item = result.delegated[0]
    assert item.target == "María"
    assert item.instruction == "revisar el correo"
    assert item.kind == "agent"


def test_route_encarga_a_target_que():
    result = route_voice_intent("encárgale al contador que cuadre las cuentas")
    assert result is not None
    item = result.delegated[0]
    assert item.target == "contador"
    assert item.instruction == "cuadre las cuentas"


def test_route_mission_generica_sin_target():
    result = route_voice_intent("encarga una misión que investigue a la competencia")
    assert result is not None
    item = result.delegated[0]
    assert item.kind == "mission"
    assert item.target == ""
    assert item.instruction == "investigue a la competencia"


def test_route_sin_delegacion_devuelve_none():
    assert route_voice_intent("hola, ¿cómo estás?") is None
    # Imperativo suelto SIN destino nombrado = keep-talking, no delegación.
    assert route_voice_intent("Revisa quién me escribió hoy") is None


def test_route_seguridad_compra_requiere_aprobacion():
    result = route_voice_intent("dile al contador que pague la factura")
    assert result is not None
    assert result.delegated[0].requires_approval is True


def test_route_seguridad_borrado_requiere_aprobacion():
    result = route_voice_intent("dile al Developer que borre todos los registros")
    assert result is not None
    assert result.delegated[0].requires_approval is True


def test_route_llm_fallback_solo_cuando_determinista_no_detecta():
    def clasificador(_text):
        return VoiceOrchestration(
            delegated=[VoiceDelegation(target="Analista", instruction="revise el reporte")]
        )

    # Sin detección determinista: el fallback sí aporta.
    fallback = route_voice_intent("no entiendo esta frase rara", llm_classifier=clasificador)
    assert fallback is not None
    assert fallback.delegated[0].target == "Analista"

    # Con detección determinista: el fallback NUNCA se consulta (determinista gana).
    def clasificador_que_miente(_text):
        raise AssertionError("el fallback no debe consultarse si el parser detectó")

    determinista = route_voice_intent(
        "dile al Developer que compruebe el deployment",
        llm_classifier=clasificador_que_miente,
    )
    assert determinista is not None
    assert determinista.delegated[0].target == "Developer"


# ---------------------------------------------------------------------------
# Resolución de nombre → worker id (product design)
# ---------------------------------------------------------------------------


def test_resolve_worker_id_por_name_exacto_sin_acentos():
    developer_id = str(uuid4())
    workers = [
        {"id": developer_id, "name": "Developer", "display_name": "Elena Dev", "role_title": "Dev"},
        {"id": str(uuid4()), "name": "Research", "display_name": None, "role_title": "Analista"},
    ]

    assert resolve_worker_id(workers, "Developer") == developer_id
    assert resolve_worker_id(workers, "developer") == developer_id
    assert resolve_worker_id(workers, "DEVELOPER") == developer_id


def test_resolve_worker_id_por_display_name_y_role_title():
    research_id = str(uuid4())
    analyst_id = str(uuid4())
    workers = [
        {"id": str(uuid4()), "name": "dev", "display_name": "Elena", "role_title": "Developer"},
        {"id": research_id, "name": "r", "display_name": "Research", "role_title": "Investigación"},
        {"id": analyst_id, "name": "a", "display_name": None, "role_title": "Analista"},
    ]

    assert resolve_worker_id(workers, "Research") == research_id
    assert resolve_worker_id(workers, "Analista") == analyst_id


def test_resolve_worker_id_subcadena_como_fallback():
    developer_id = str(uuid4())
    workers = [
        {"id": developer_id, "name": "Developer Engineer", "display_name": None, "role_title": None}
    ]

    assert resolve_worker_id(workers, "Develop") == developer_id


def test_resolve_worker_id_sin_match_devuelve_none():
    workers = [{"id": str(uuid4()), "name": "Research", "display_name": None, "role_title": None}]

    assert resolve_worker_id(workers, "Contador") is None
    assert resolve_worker_id([], "Developer") is None
    assert resolve_worker_id(workers, "") is None


def test_resolve_worker_id_worker_sin_id_devuelve_none():
    workers = [{"name": "Developer", "display_name": None, "role_title": None}]

    assert resolve_worker_id(workers, "Developer") is None


# ---------------------------------------------------------------------------
# Wiring: execute_voice_text_turn con delegación detectada
# ---------------------------------------------------------------------------


async def test_voice_service_routea_delegacion_y_sigue_hablando(monkeypatch):
    import edecan_api.routers.conversations as conversations
    import edecan_api.routers.perfil as perfil
    import edecan_api.routers.persona as persona_router
    import edecan_api.voice_turn_service as service

    capturado: dict = {}

    class _Agent:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run_turn(self, **_kwargs):
            capturado["user_text"] = _kwargs.get("user_text")
            yield TextDeltaEvent(text="Busco el restaurante para esta noche.")
            yield DoneEvent(usage={"input_tokens": 2, "output_tokens": 2})

    async def _fake_ejecutar(_ctx, orchestration):
        assert orchestration.delegated[0].target == "Developer"
        return (["Le encargué a Developer que compruebe el deployment"], [])

    monkeypatch.setattr(service, "Agent", _Agent)
    monkeypatch.setattr(service, "ejecutar_delegaciones", _fake_ejecutar)
    monkeypatch.setattr(conversations, "_build_ctx", lambda **_kwargs: SimpleNamespace(extras={}))
    monkeypatch.setattr(conversations, "_extra_conversation_tools", lambda *_args: _empty())
    monkeypatch.setattr(conversations, "_tools_con_pregunta_pendiente", lambda *_args: [])
    monkeypatch.setattr(perfil, "profile_context_for", lambda *_args: _empty_text())
    monkeypatch.setattr(persona_router, "persona_from_row", lambda *_args: PersonaConfig())
    monkeypatch.setattr(service, "enqueue", _empty_enqueue)
    monkeypatch.setattr(service, "load_unified_session", lambda *_args, **_kwargs: _session_state())
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
        user_text="dile al Developer que compruebe el deployment y busca un restaurante",
    )

    # El agente recibió SOLO el resto keep-talking, no la cláusula delegada.
    assert capturado["user_text"] == "busca un restaurante"
    assert result.text.startswith("Le encargué a Developer que compruebe el deployment")
    assert "Busco el restaurante" in result.text
    assert [row["role"] for row in repo.messages] == ["user", "assistant"]
    assert repo.messages[0]["content"] == {
        "text": "dile al Developer que compruebe el deployment y busca un restaurante"
    }


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
