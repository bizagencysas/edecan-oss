"""`edecan_api.event_log`: el módulo fail-open y su cableado en los pushes del bot.

El módulo escribe en `event_log` (migración 0067) y NUNCA lanza; la sesión del
log es propiedad del caller (el módulo no commitea). Acá se verifica el
contrato con una sesión falsa y el wiring de los dos puntos de
`routers/persistent_agents.py` (el notify temprano al primer delta y el de fin
de turno), con `log_event_con_factory` sustituido por un captor.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from conftest import auth_headers

import edecan_api.bot_turn_service as bot_turn_service
import edecan_api.deps as edecan_deps
import edecan_api.event_log as event_log
import edecan_api.routers.persistent_agents as persistent_agents
from edecan_api.routers.conversations import _format_sse

# ---------------------------------------------------------------------------
# El módulo: escribe, no commitea, fail-open
# ---------------------------------------------------------------------------


class _FakeSessionCaptura:
    """Sesión falsa: captura los INSERT y cuenta los commits."""

    def __init__(self, *, revienta: bool = False) -> None:
        self.revienta = revienta
        self.ejecutados: list[tuple[str, dict[str, Any]]] = []
        self.commits = 0

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
        if self.revienta:
            raise RuntimeError("Postgres caído")
        self.ejecutados.append((" ".join(str(stmt).split()), dict(params or {})))
        return None


async def test_log_event_escribe_con_el_contrato_y_no_commitea() -> None:
    """`log_event` inserta (categoria, accion, detalle jsonb) y deja el commit
    al dueño de la sesión — la sesión del log es dedicada, jamás la de la
    petición."""
    session = _FakeSessionCaptura()
    tenant_id = uuid.uuid4()

    escrito = await event_log.log_event(
        session,
        tenant_id=tenant_id,
        categoria="push",
        accion="encolado_temprano",
        detalle={"event_id": "e-1", "texto": "el post completo ñ 🎉"},
    )

    assert escrito is True
    (sql, params) = session.ejecutados[0]
    assert "INSERT INTO event_log" in sql
    assert params["tenant_id"] == tenant_id
    assert params["categoria"] == "push"
    assert params["accion"] == "encolado_temprano"
    detalle = json.loads(params["detalle"])
    assert detalle == {"event_id": "e-1", "texto": "el post completo ñ 🎉"}
    assert params["created_at"] is not None
    assert session.commits == 0  # el commit es del dueño de la sesión


async def test_log_event_acepta_detalle_largo_y_no_json_nativo() -> None:
    """jsonb acepta texto largo (un post completo); los valores no-JSON (UUID,
    NaN) no pueden tumbar el INSERT."""
    session = _FakeSessionCaptura()

    escrito = await event_log.log_event(
        session,
        tenant_id=None,
        categoria="post",
        accion="post_creado",
        detalle={"texto": "x" * 20_000, "id": uuid.uuid4(), "nan": float("nan")},
    )

    assert escrito is True
    detalle = json.loads(session.ejecutados[0][1]["detalle"])
    assert len(detalle["texto"]) == 20_000
    assert isinstance(detalle["id"], str)  # UUID serializado por default=str
    assert detalle["nan"] is None


async def test_log_event_fail_open_no_lanza() -> None:
    """BD caída o tabla sin migrar: `False`, jamás una excepción — el flujo del
    negocio no depende del log."""
    session = _FakeSessionCaptura(revienta=True)

    escrito = await event_log.log_event(
        session, tenant_id=uuid.uuid4(), categoria="push", accion="x"
    )

    assert escrito is False


async def test_log_event_con_factory_abre_sesion_y_es_fail_open() -> None:
    """El punto de entrada de los call-sites abre una sesión propia; si ni eso
    se puede (BD caída), traga y devuelve False."""
    session = _FakeSessionCaptura()
    llamadas: list[uuid.UUID | None] = []

    class _Factory:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *exc_info: Any) -> bool:
            return False

    def factory(tenant_id: uuid.UUID | None):
        llamadas.append(tenant_id)
        return _Factory()

    tenant_id = uuid.uuid4()
    escrito = await event_log.log_event_con_factory(
        factory, tenant_id=tenant_id, categoria="push", accion="encolado_final"
    )

    assert escrito is True
    assert llamadas == [tenant_id]
    assert len(session.ejecutados) == 1

    def factory_rota(_tenant_id: uuid.UUID | None):
        raise RuntimeError("no hay conexión")

    roto = await event_log.log_event_con_factory(
        factory_rota, tenant_id=tenant_id, categoria="push", accion="encolado_final"
    )
    assert roto is False


# ---------------------------------------------------------------------------
# Wiring: los dos notify del turno de un bot quedan en event_log
# ---------------------------------------------------------------------------


class _CaptorLog:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, session_factory, *, tenant_id, categoria, accion, detalle=None):  # noqa: ANN001, ANN204
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "categoria": categoria,
                "accion": accion,
                "detalle": dict(detalle or {}),
            }
        )


class _FakeFreshSession:
    """Doble de `edecan_db.session.get_session`: nunca lee fila alguna."""

    async def __aenter__(self) -> _FakeFreshSession:
        return self

    async def __aexit__(self, *exc_info: Any) -> bool:
        return False

    async def execute(self, _clause: Any, _params: dict | None = None) -> Any:
        return _FakeResult()


class _FakeResult:
    def mappings(self) -> _FakeMappings:
        return _FakeMappings()


class _FakeMappings:
    def first(self) -> None:
        return None


@pytest.fixture
def bot_turn_env(monkeypatch: pytest.MonkeyPatch, fake_redis) -> dict[str, Any]:
    """Fakea el turno completo, captura los jobs encolados y el event_log."""
    worker_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    enqueued: list[tuple[str, dict[str, Any], Any]] = []
    captor = _CaptorLog()

    async def fake_load_worker(_session, _user, _worker_id):
        return {"id": str(worker_id), "name": "BotX", "display_name": "BotX"}

    async def fake_ensure_conversation(_session, _user, _worker):
        return conversation_id

    def fake_display_name(_worker) -> str:
        return "BotX"

    async def fake_enqueue(_settings, job_type, payload, tenant):
        enqueued.append((job_type, payload, tenant))

    async def fake_enqueue_outbox(session, *, tenant_id, job_type, payload):
        enqueued.append((job_type, payload, tenant_id))

    monkeypatch.setattr(bot_turn_service, "load_worker", fake_load_worker)
    monkeypatch.setattr(bot_turn_service, "ensure_worker_conversation", fake_ensure_conversation)
    monkeypatch.setattr(bot_turn_service, "worker_display_name", fake_display_name)
    monkeypatch.setattr(persistent_agents, "enqueue", fake_enqueue)
    monkeypatch.setattr("edecan_core.queue.enqueue_outbox", fake_enqueue_outbox)
    monkeypatch.setattr(event_log, "log_event_con_factory", captor)
    monkeypatch.setattr(edecan_deps, "get_redis", lambda settings: fake_redis)
    monkeypatch.setattr("edecan_db.session.get_session", lambda tenant_id: _FakeFreshSession())
    return {
        "worker_id": worker_id,
        "conversation_id": conversation_id,
        "enqueued": enqueued,
        "captor": captor,
    }


async def test_event_log_wiring_del_push_temprano_y_final(
    client, bot_turn_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """El notify temprano (primer delta) y el de fin de turno quedan cada uno
    en event_log con su event_id compartido y el tenant del actor."""
    deltas = ["Voy a revisar eso…"]

    async def fake_turn(**_kwargs):
        yield _format_sse("message.started", {"type": "started"})
        yield _format_sse("message.delta", {"type": "text_delta", "text": deltas[0]})
        yield _format_sse("message.done", {"type": "done", "usage": {}, "message_id": str(uuid.uuid4())})

    monkeypatch.setattr(bot_turn_service, "stream_worker_turn", fake_turn)
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()

    resp = await client.post(
        f"/v1/agents/workers/{bot_turn_env['worker_id']}/message",
        json={"text": "hola"},
        headers=auth_headers(user_id=user_id, tenant_id=tenant_id),
    )

    assert resp.status_code == 200
    captor: _CaptorLog = bot_turn_env["captor"]
    assert [c["accion"] for c in captor.calls] == ["encolado_temprano", "encolado_final"]
    temprano, final = captor.calls
    assert temprano["categoria"] == "push"
    assert final["categoria"] == "push"
    assert temprano["tenant_id"] == tenant_id
    assert final["tenant_id"] == tenant_id
    assert temprano["detalle"]["event_id"] == final["detalle"]["event_id"]  # dedup durable
    assert temprano["detalle"]["chat_id"] == str(bot_turn_env["conversation_id"])
    assert "Voy a revisar eso" in temprano["detalle"]["apns_body"]
    # B-16: el push final lleva el texto REAL del turno (no "Terminé" genérico).
    assert "Voy a revisar eso" in final["detalle"]["apns_body"]


async def test_event_log_wiring_sin_delta_solo_loguea_el_final(
    client, bot_turn_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sin delta de texto no hay notify temprano: solo el evento de fin queda
    en event_log — el log refleja exactamente los pushes encolados."""
    async def fake_turn(**_kwargs):
        yield _format_sse("message.started", {"type": "started"})
        yield _format_sse("message.done", {"type": "done", "usage": {}, "message_id": str(uuid.uuid4())})

    monkeypatch.setattr(bot_turn_service, "stream_worker_turn", fake_turn)

    resp = await client.post(
        f"/v1/agents/workers/{bot_turn_env['worker_id']}/message",
        json={"text": "hola"},
        headers=auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4()),
    )

    assert resp.status_code == 200
    captor: _CaptorLog = bot_turn_env["captor"]
    assert [c["accion"] for c in captor.calls] == ["encolado_final"]