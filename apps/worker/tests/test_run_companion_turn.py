"""Tests de `run_companion_turn`: turno proactivo REAL del companion.

El scheduler solo despierta; el modelo decide si escribe. Verifica silencio
válido, mensaje persistido + push `agent_message`, quiet hours, idempotencia.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import edecan_worker.handlers.run_companion_turn as handler
import pytest
from edecan_core.companion_wake import SILENCE_SENTINEL
from edecan_core.tools import ToolRegistry
from edecan_schemas import JobEnvelope, PersonaConfig
from fakes import FakeRepo, install_fake_edecan_core_queue, make_deps


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> _FakeResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return dict(self._rows[0]) if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._rows]


class FakeSession:
    def __init__(self) -> None:
        self.wake_claims: set[str] = set()
        self.companion_24_7 = False

    async def execute(self, clause: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        sql = str(clause)
        params = dict(params or {})

        if "pg_advisory_xact_lock" in sql:
            return _FakeResult()
        if "notifications.preferences.updated" in sql:
            if self.companion_24_7:
                return _FakeResult([{"meta": {"companion_24_7": True}}])
            return _FakeResult()
        if "FROM audit_log" in sql and "companion_wake" in params.get("action", ""):
            target = params.get("target", "")
            exists = target in self.wake_claims
            return _FakeResult([{"id": uuid.uuid4()}] if exists else [])
        if "INSERT INTO audit_log" in sql:
            self.wake_claims.add(params["target"])
            return _FakeResult()
        return _FakeResult()


@asynccontextmanager
async def _session_factory(_tenant_id: uuid.UUID | None):
    yield FakeSession()


def _env(*, tenant_id: uuid.UUID, user_id: uuid.UUID, wake_key: str = "wake:test") -> JobEnvelope:
    return JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="run_companion_turn",
        payload={"user_id": str(user_id), "wake_key": wake_key},
    )


def _seed_repo(fake_repo: FakeRepo, tenant_id: uuid.UUID, user_id: uuid.UUID) -> uuid.UUID:
    fake_repo.tenants[tenant_id] = {"id": tenant_id, "plan_key": "free_selfhost"}
    fake_repo.personas[(tenant_id, user_id)] = {
        "nombre_asistente": "Edecán",
        "idioma": "es",
        "tono": "cálido",
        "formalidad": 1,
        "emojis": False,
        "instrucciones": "",
        "rasgos": [],
        "memoria_activada": True,
        "voice_id": None,
    }
    conversation = {
        "id": uuid.uuid4(),
        "tenant_id": tenant_id,
        "user_id": user_id,
        "title": "Actividad",
        "is_main": True,
    }
    fake_repo.conversations[conversation["id"]] = conversation
    return conversation["id"]


def _patch_agent_pipeline(monkeypatch: pytest.MonkeyPatch, fake_turn) -> None:
    persona = PersonaConfig(
        nombre_asistente="Edecán",
        idioma="es",
        tono="cálido",
        formalidad=1,
        emojis=False,
        instrucciones="",
        rasgos=[],
    )

    monkeypatch.setattr(handler, "_build_registry", lambda: ToolRegistry())
    monkeypatch.setattr(
        handler,
        "_apply_agent_profile",
        lambda registry, _persona, _profile: (registry, persona),
    )
    monkeypatch.setattr(handler, "run_companion_agent_turn", fake_turn)


async def test_wake_with_silence_produces_no_message_nor_push(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    fake_repo = FakeRepo()
    _seed_repo(fake_repo, tenant_id, user_id)
    session_holder: list[FakeSession] = []

    @asynccontextmanager
    async def session_factory(_tenant_id: uuid.UUID | None):
        session = FakeSession()
        session_holder.append(session)
        yield session

    async def fake_turn(**_kwargs: Any) -> tuple[str, list, dict, str | None]:
        return SILENCE_SENTINEL, [], {}, None

    pushes: list[Any] = []
    enqueued: list[tuple[str, dict, uuid.UUID]] = []

    async def fake_enqueue(_settings, job_type, payload, tenant):
        enqueued.append((job_type, payload, tenant))
        return uuid.uuid4()

    async def fake_notify(_deps, event):
        pushes.append(event)

    monkeypatch.setattr(handler, "SqlRepo", lambda _session: fake_repo)
    _patch_agent_pipeline(monkeypatch, fake_turn)
    monkeypatch.setattr(handler, "notify_important_event", fake_notify)
    install_fake_edecan_core_queue(monkeypatch, fake_enqueue)

    await handler.handle(_env(tenant_id=tenant_id, user_id=user_id), make_deps(session_factory=session_factory))

    assert fake_repo.messages == []
    assert pushes == []
    assert enqueued == []


async def test_wake_with_substantive_text_persists_message_and_pushes_with_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    fake_repo = FakeRepo()
    conversation_id = _seed_repo(fake_repo, tenant_id, user_id)
    body = "Tienes una aprobación pendiente del deploy en staging."

    async def fake_turn(**_kwargs: Any) -> tuple[str, list, dict, str | None]:
        return body, [{"type": "tool_end", "name": "listar_aprobaciones"}], {"completion_tokens": 42}, None

    pushes: list[Any] = []
    enqueued: list[str] = []

    async def fake_enqueue(_settings, job_type, _payload, _tenant):
        enqueued.append(job_type)
        return uuid.uuid4()

    async def fake_notify(_deps, event):
        pushes.append(event)

    monkeypatch.setattr(handler, "SqlRepo", lambda _session: fake_repo)
    _patch_agent_pipeline(monkeypatch, fake_turn)
    monkeypatch.setattr(handler, "should_run_wake", lambda **_: True)
    monkeypatch.setattr(handler, "notify_important_event", fake_notify)
    install_fake_edecan_core_queue(monkeypatch, fake_enqueue)

    await handler.handle(_env(tenant_id=tenant_id, user_id=user_id, wake_key="approval:abc"), make_deps(session_factory=_session_factory))

    assert len(fake_repo.messages) == 1
    msg = fake_repo.messages[0]
    assert msg["role"] == "assistant"
    assert msg["content"]["text"] == body
    assert msg["conversation_id"] == conversation_id
    assert len(pushes) == 1
    assert pushes[0].kind == "agent_message"
    assert pushes[0].chat_id == conversation_id
    assert pushes[0].apns_title == "Edecán"
    assert pushes[0].apns_body == body
    assert pushes[0].title == "Mensaje de Edecán"
    assert pushes[0].body != pushes[0].apns_body
    assert pushes[0].push_data()["deeplink"] == f"edecan://chat/{conversation_id}"
    assert enqueued == ["memory_consolidate"]


async def test_phone_call_wake_push_uses_llamada_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    fake_repo = FakeRepo()
    _seed_repo(fake_repo, tenant_id, user_id)
    body = "Colgamos con Daniel: confirmó que envía la dirección mañana."

    async def fake_turn(**_kwargs: Any) -> tuple[str, list, dict, str | None]:
        return body, [], {}, None

    pushes: list[Any] = []

    async def fake_notify(_deps, event):
        pushes.append(event)

    monkeypatch.setattr(handler, "SqlRepo", lambda _session: fake_repo)
    _patch_agent_pipeline(monkeypatch, fake_turn)
    monkeypatch.setattr(handler, "notify_important_event", fake_notify)
    install_fake_edecan_core_queue(monkeypatch, lambda *_a, **_k: uuid.uuid4())

    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="run_companion_turn",
        payload={
            "user_id": str(user_id),
            "wake_key": "phone_call:abc",
            "source": "phone_call_finished",
            "urgent": True,
            "require_message": True,
            "push": {"title": "Llamada"},
        },
    )
    await handler.handle(env, make_deps(session_factory=_session_factory))

    assert len(pushes) == 1
    assert pushes[0].apns_title == "Llamada"
    assert pushes[0].apns_body == body
    assert "Edecán tiene algo que decirte" not in pushes[0].apns_body


async def test_quiet_hours_defer_without_consuming_wake_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    fake_repo = FakeRepo()
    _seed_repo(fake_repo, tenant_id, user_id)
    session = FakeSession()
    turn_calls = 0

    async def fake_turn(**_kwargs: Any) -> tuple[str, list, dict, str | None]:
        nonlocal turn_calls
        turn_calls += 1
        return "no debería correr", [], {}, None

    monkeypatch.setattr(handler, "SqlRepo", lambda _session: fake_repo)
    _patch_agent_pipeline(monkeypatch, fake_turn)
    monkeypatch.setattr(handler, "should_run_wake", lambda **_: False)

    @asynccontextmanager
    async def session_factory(_tenant_id: uuid.UUID | None):
        yield session

    await handler.handle(_env(tenant_id=tenant_id, user_id=user_id), make_deps(session_factory=session_factory))

    assert turn_calls == 0
    assert fake_repo.messages == []
    assert session.wake_claims == set()


async def test_duplicate_wake_key_skips_second_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    fake_repo = FakeRepo()
    _seed_repo(fake_repo, tenant_id, user_id)
    wake_key = "approval:dup"
    turn_calls = 0
    shared_session = FakeSession()

    async def fake_turn(**_kwargs: Any) -> tuple[str, list, dict, str | None]:
        nonlocal turn_calls
        turn_calls += 1
        return "Solo una vez.", [], {}, None

    pushes: list[Any] = []

    async def fake_notify(_deps, event):
        pushes.append(event)

    @asynccontextmanager
    async def session_factory(_tenant_id: uuid.UUID | None):
        yield shared_session

    monkeypatch.setattr(handler, "SqlRepo", lambda _session: fake_repo)
    _patch_agent_pipeline(monkeypatch, fake_turn)
    monkeypatch.setattr(handler, "should_run_wake", lambda **_: True)
    monkeypatch.setattr(handler, "notify_important_event", fake_notify)
    async def fake_enqueue(*_args, **_kwargs):
        return uuid.uuid4()

    install_fake_edecan_core_queue(monkeypatch, fake_enqueue)

    deps = make_deps(session_factory=session_factory)
    env = _env(tenant_id=tenant_id, user_id=user_id, wake_key=wake_key)

    await handler.handle(env, deps)
    await handler.handle(env, deps)

    assert turn_calls == 1
    assert len(fake_repo.messages) == 1
    assert len(pushes) == 1


async def test_turno_con_companion_inyecta_puente_y_aprueba_usar_computadora(
    monkeypatch, tmp_path
):
    """Con fábrica de companion registrada, el turno del dueño inyecta el puente
    y aprueba `usar_computadora` (vida digital real). Sin fábrica: nada."""
    import uuid

    from edecan_core.companion_access import register_companion_factory
    from edecan_core.tools import ToolContext, ToolRegistry
    from edecan_worker.handlers.run_companion_turn import run_companion_agent_turn

    async def fake_bridge_call(_action, _params):
        return {}

    tenant_id = uuid.uuid4()
    seen: dict = {}

    def fake_factory(tid):
        seen["tid"] = tid
        return fake_bridge_call

    register_companion_factory(fake_factory)
    try:
        ctx = ToolContext(
            tenant_id=tenant_id,
            user_id=None,
            session=None,
            settings=None,
            llm=None,
            vault=None,
            extras={},
        )
        registry = ToolRegistry()
        await run_companion_agent_turn(
            ctx=ctx,
            llm_router=None,
            registry=registry,
            persona=None,
            flags={},
            history=[],
            instruction="explora",
            provider_health=None,
        )
        assert seen.get("tid") == tenant_id
        assert ctx.extras["companion"] is fake_bridge_call
        assert ctx.extras["approved_tool_calls"] == {"usar_computadora"}

        register_companion_factory(None)
        ctx2 = ToolContext(
            tenant_id=tenant_id,
            user_id=None,
            session=None,
            settings=None,
            llm=None,
            vault=None,
            extras={},
        )
        await run_companion_agent_turn(
            ctx=ctx2,
            llm_router=None,
            registry=registry,
            persona=None,
            flags={},
            history=[],
            instruction="explora",
            provider_health=None,
        )
        assert ctx2.extras["approved_tool_calls"] == set()
        assert "companion" not in ctx2.extras
    finally:
        register_companion_factory(None)
