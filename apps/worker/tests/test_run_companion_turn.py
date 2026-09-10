"""Tests de `run_companion_turn`: turno proactivo REAL del companion.

El scheduler solo despierta; el modelo decide si escribe. Verifica silencio
válido, mensaje persistido + push `agent_message`, quiet hours, idempotencia.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
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

    monkeypatch.setattr(handler, "_build_registry", lambda _tenant_id=None: ToolRegistry())
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
        assert ctx.extras["approved_tool_calls"] == {
                "usar_computadora",
                "navegar_web_interactivo",
            }

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


async def test_require_message_empty_turn_retries_once_then_posts_honest_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Despertar con `require_message` + dos intentos vacíos = aviso honesto.

    El corte silencioso está prohibido en este camino: si el motor falla dos
    veces, el dueño recibe un aviso de fallo (veracidad), nunca silencio.
    """
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    fake_repo = FakeRepo()
    _seed_repo(fake_repo, tenant_id, user_id)
    turn_calls = 0

    async def fake_turn(**_kwargs: Any) -> tuple[str, list, dict, str | None]:
        nonlocal turn_calls
        turn_calls += 1
        return SILENCE_SENTINEL, [], {}, None

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
            "wake_key": "phone_call:fallo",
            "source": "phone_call_finished",
            "urgent": True,
            "require_message": True,
            "push": {"title": "Llamada"},
        },
    )
    await handler.handle(env, make_deps(session_factory=_session_factory))

    assert turn_calls == 2  # un reintento único, no un loop
    assert len(fake_repo.messages) == 1
    fallback = fake_repo.messages[0]["content"]["text"]
    assert "No pude generar el mensaje" in fallback
    assert len(pushes) == 1
    assert pushes[0].apns_body == fallback


async def test_content_wake_empty_turn_stays_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wake de contenido sin novedad y sin fallo de proveedor = silencio.

    Antes publicaba el aviso honesto en cada slot (spam de push para el
    dueño). El aviso queda reservado a fallos reales del motor.
    """
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    fake_repo = FakeRepo()
    _seed_repo(fake_repo, tenant_id, user_id)
    turn_calls = 0

    async def fake_turn(**_kwargs: Any) -> tuple[str, list, dict, str | None]:
        nonlocal turn_calls
        turn_calls += 1
        return SILENCE_SENTINEL, [], {}, None

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
            "wake_key": "vida_digital:202609031920",
            "source": "vida_digital",
            "urgent": True,
            "require_message": True,
            "push": {"title": "Vida digital"},
        },
    )
    await handler.handle(env, make_deps(session_factory=_session_factory))

    assert turn_calls >= 1
    assert len(fake_repo.messages) == 0
    assert len(pushes) == 0


async def test_require_message_empty_then_substantive_posts_the_substantive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reintento único: si el segundo intento produce mensaje, gana ese."""
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    fake_repo = FakeRepo()
    conversation_id = _seed_repo(fake_repo, tenant_id, user_id)
    turn_calls = 0
    body = "Te llamó +573042449497: agendó una demo para el jueves."

    async def fake_turn(**_kwargs: Any) -> tuple[str, list, dict, str | None]:
        nonlocal turn_calls
        turn_calls += 1
        if turn_calls == 1:
            return SILENCE_SENTINEL, [], {}, None
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
            "wake_key": "phone_call:retry",
            "source": "phone_call_finished",
            "urgent": True,
            "require_message": True,
            "push": {"title": "Llamada"},
        },
    )
    await handler.handle(env, make_deps(session_factory=_session_factory))

    assert turn_calls == 2
    assert len(fake_repo.messages) == 1
    assert fake_repo.messages[0]["content"]["text"] == body
    assert fake_repo.messages[0]["conversation_id"] == conversation_id
    assert len(pushes) == 1


async def test_wake_de_llamada_acepta_reporte_sin_pregunta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Portón de voz humana relajado para wakes de llamada: el resumen de una
    llamada es un reporte de hechos — sin pregunta ni postura igual se publica
    (el dueño debe recibir el resumen). Anti-volcado queda intacto."""
    import uuid as _uuid

    from edecan_core.tools import ToolContext, ToolRegistry
    from edecan_schemas import PersonaConfig
    from edecan_worker.handlers.run_companion_turn import run_companion_agent_turn

    REPORTE = "Te llamó +573042449497: agendó una demo para el jueves, 45 segundos."

    class _FakeAgent:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def run_turn(self, **_kwargs: Any):
            async def _gen():
                yield {"type": "text_delta", "text": REPORTE}
                yield {"type": "done", "usage": {"completion_tokens": 12}}

            return _gen()

    monkeypatch.setattr(handler, "Agent", _FakeAgent)
    persona = PersonaConfig(
        nombre_asistente="Edecán",
        idioma="es",
        tono="cálido",
        formalidad=1,
        emojis=False,
        instrucciones="",
        rasgos=[],
    )
    ctx = ToolContext(
        tenant_id=_uuid.uuid4(),
        user_id=None,
        session=None,
        settings=None,
        llm=None,
        vault=None,
        extras={},
    )

    # Relajado (wake de llamada): el reporte pasa.
    texto, _log, _usage, error = await run_companion_agent_turn(
        ctx=ctx,
        llm_router=None,
        registry=ToolRegistry(),
        persona=persona,
        flags={},
        history=[],
        instruction="resumen de llamada",
        provider_health=None,
        exigir_pregunta_opinion=False,
    )
    assert error is None
    assert REPORTE in texto

    # Estricto (demás wakes): el mismo reporte se descarta (comportamiento previo).
    texto2, _log2, _usage2, error2 = await run_companion_agent_turn(
        ctx=ctx,
        llm_router=None,
        registry=ToolRegistry(),
        persona=persona,
        flags={},
        history=[],
        instruction="vida digital",
        provider_health=None,
        exigir_pregunta_opinion=True,
    )
    assert texto2 == ""
    assert error2 is None


# ---------------------------------------------------------------------------
# Continuidad tras reinicio auto-gestionado (restart_pending)
# ---------------------------------------------------------------------------


async def test_wake_con_restart_pending_y_flag_reencola_y_no_escribe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    fake_repo = FakeRepo()
    _seed_repo(fake_repo, tenant_id, user_id)
    enqueued: list[tuple[str, dict, uuid.UUID]] = []

    async def fake_enqueue(settings, job_type, payload, tid, *, delay_seconds=None):
        enqueued.append((job_type, payload, tid, delay_seconds))
        return uuid.uuid4()

    install_fake_edecan_core_queue(monkeypatch, fake_enqueue)
    monkeypatch.setattr(handler, "SqlRepo", lambda _session: fake_repo)
    monkeypatch.setattr(handler, "_RESTART_FLAG", "/tmp/flag-restart-existe")
    import pathlib

    pathlib.Path(handler._RESTART_FLAG).write_text("motivo")

    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="run_companion_turn",
        payload={
            "user_id": str(user_id),
            "wake_key": "post_restart:123",
            "source": "post_restart",
            "instruction": "Retoma: construía X",
            "require_message": True,
            "restart_pending": True,
        },
    )
    await handler.handle(env, make_deps(session_factory=_session_factory))

    assert fake_repo.messages == []
    assert len(enqueued) == 1
    job_type, payload, tid, delay = enqueued[0]
    assert job_type == "run_companion_turn"
    assert payload["restart_pending"] is True
    assert delay == 30
    assert tid == tenant_id
    pathlib.Path(handler._RESTART_FLAG).unlink(missing_ok=True)


async def test_wake_con_restart_pending_sin_flag_prosigue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    fake_repo = FakeRepo()
    _seed_repo(fake_repo, tenant_id, user_id)
    enqueued: list[tuple] = []

    async def fake_enqueue(settings, job_type, payload, tid):
        enqueued.append((job_type, payload, tid))
        return uuid.uuid4()

    async def fake_turn(**_kwargs):
        return "Volví del reinicio y retomé la construcción de X.", [], {}, None

    install_fake_edecan_core_queue(monkeypatch, fake_enqueue)
    monkeypatch.setattr(handler, "SqlRepo", lambda _session: fake_repo)
    _patch_agent_pipeline(monkeypatch, fake_turn)
    async def fake_notify(_d, _e):
        return None

    monkeypatch.setattr(handler, "notify_important_event", fake_notify)
    monkeypatch.setattr(handler, "_RESTART_FLAG", "/tmp/flag-restart-inexistente")
    import pathlib

    pathlib.Path(handler._RESTART_FLAG).unlink(missing_ok=True)
    # Determinista: el wake de reinicio no puede quedar en quiet hours.
    from edecan_core import companion_wake

    monkeypatch.setattr(companion_wake, "is_quiet_hours", lambda now=None: False)

    env = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="run_companion_turn",
        payload={
            "user_id": str(user_id),
            "wake_key": "post_restart:123",
            "source": "post_restart",
            "instruction": "Retoma: construía X",
            "require_message": True,
            "restart_pending": True,
        },
    )
    await handler.handle(env, make_deps(session_factory=_session_factory))

    # Sin flag = el reinicio ya pasó: el turno SÍ publica su mensaje y NO
    # se re-encola a sí mismo (memory_consolidate es el encolado normal).
    assert len(fake_repo.messages) == 1
    assert "Volví del reinicio" in fake_repo.messages[0]["content"]["text"]
    assert [jt for jt, _p, _t in enqueued if jt == "run_companion_turn"] == []
