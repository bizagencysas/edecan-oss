"""Mitigaciones de la auditoría 2026-09-07 — ola de chat (BOTS-02/09/11/12/18/20/24).

Cada test cubre UNA tarea, sin Postgres/Redis reales (mismo contrato de dobles
que el resto de `apps/api/tests`):

1. BOTS-02: el chat interactivo aplica la matriz de autonomía al registry.
2. BOTS-11: `POST /confirm` por SSE también decide la fila durable.
3. BOTS-18: la tarjeta en vivo enmascara args sensibles.
4. BOTS-12: cursor de historial pagina hacia atrás y respeta el protocolo legacy.
5. BOTS-09: epoch de conversación incrementa en clear/delete y viaja en header.
6. BOTS-20: aviso explícito de multi-réplica (default single-replica).
7. BOTS-24: cleanup de arranque re-etiqueta in-flight huérfanos como interrupted.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import auth_headers
from edecan_core.tools import Tool, ToolContext, ToolRegistry, ToolResult
from fastapi.responses import StreamingResponse
from httpx import ASGITransport, AsyncClient

import edecan_api.bot_turn_service as bot_service
import edecan_api.routers.conversations as conv
from edecan_api.bot_turn_service import (
    _decode_message_cursor,
    _encode_message_cursor,
    _filter_extra_tools_by_autonomy,
    _filter_registry_by_autonomy,
    get_conversation_epoch,
    increment_conversation_epoch,
    list_normalized_messages,
)
from edecan_api.routers.conversations import (
    _mark_pending_approval_decided,
    _mask_sensitive_args,
    _reopen_pending_approval,
)

# ---------------------------------------------------------------------------
# BOTS-02 — autonomía en el chat interactivo
# ---------------------------------------------------------------------------


class _ReadTool(Tool):
    name = "leer_archivo"
    description = "lee"
    category = "read"
    input_schema = {"type": "object", "properties": {}}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        return ToolResult(content="")


class _WriteTool(Tool):
    name = "borrar_archivo"
    description = "borra"
    category = "write"
    input_schema = {"type": "object", "properties": {}}
    dangerous = True

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        return ToolResult(content="")


def test_filter_registry_por_autonomia_read_only_quita_escritura() -> None:
    full = ToolRegistry()
    full.register(_ReadTool())
    full.register(_WriteTool())

    restringido = _filter_registry_by_autonomy(full, "read_only")
    nombres = {t.name for t in restringido.all()}
    assert "leer_archivo" in nombres
    assert "borrar_archivo" not in nombres
    # `full` no restringe: devuelve el MISMO registry, sin copiar.
    assert _filter_registry_by_autonomy(full, "full") is full


def test_filter_extra_tools_por_autonomia_read_only_rechaza_mcp_write() -> None:
    class _McpLeer:
        name = "mcp_demo_buscar"  # "buscar" -> read

    class _McpBorrar:
        name = "mcp_demo_delete"  # "delete" -> write

    filtradas = _filter_extra_tools_by_autonomy([_McpLeer(), _McpBorrar()], "read_only")
    nombres = {t.name for t in filtradas}
    assert "mcp_demo_buscar" in nombres
    assert "mcp_demo_delete" not in nombres

    completas = _filter_extra_tools_by_autonomy([_McpLeer(), _McpBorrar()], "full")
    assert {t.name for t in completas} == {"mcp_demo_buscar", "mcp_demo_delete"}


async def test_resume_worker_read_only_bloquea_tool_escritura(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F2: al resumir el turno de un WORKER se re-aplica la matriz de autonomía
    con el nivel VIGENTE. Un worker bajado a `read_only` después de aprobarse la
    tarjeta no puede confirmar una tool de escritura (fail-closed)."""
    from edecan_schemas.chat import PendingAgentTurn, PendingToolCall
    from fastapi import HTTPException

    registry = ToolRegistry()
    registry.register(_WriteTool())  # "borrar_archivo", dangerous=True → write

    monkeypatch.setattr(conv, "get_tool_registry", lambda _request: registry)

    async def _no_extras(request: Any, current_user: Any) -> list[Any]:
        return []

    monkeypatch.setattr(conv, "_extra_conversation_tools", _no_extras)

    async def _load_worker_read_only(session: Any, user: Any, worker_id: Any) -> dict[str, Any]:
        return {"id": str(worker_id), "autonomy_level": "read_only"}

    monkeypatch.setattr(bot_service, "load_worker", _load_worker_read_only)

    worker_id = uuid.uuid4()
    pending = {
        "name": "borrar_archivo",
        "args": {},
        "worker_id": str(worker_id),
        "pending_turn": PendingAgentTurn(
            version=1,
            messages=[],
            tool_calls=[PendingToolCall(id="call_1", name="borrar_archivo", arguments={})],
            operational_tool_names=["borrar_archivo"],
            iteration=0,
        ).model_dump(),
    }

    with pytest.raises(HTTPException) as exc_info:
        await conv._resume_approved_turn(
            request=SimpleNamespace(),
            current_user=SimpleNamespace(tenant_id=uuid.uuid4(), user_id=uuid.uuid4()),
            tenant=SimpleNamespace(flags={}, plan_key="hosted_basic"),
            conversation_id=uuid.uuid4(),
            conversation={"chat_model": None, "chat_effort": None},
            tool_call_id="call_1",
            pending=pending,
            repo=SimpleNamespace(),
            session=SimpleNamespace(),
            llm_router=SimpleNamespace(),
            vault=None,
            settings=SimpleNamespace(),
            redis_client=SimpleNamespace(),
        )
    assert exc_info.value.status_code == 409


# ---------------------------------------------------------------------------
# BOTS-11 — confirm por SSE decide la fila durable
# ---------------------------------------------------------------------------


class _NoopNested:
    async def __aenter__(self) -> _NoopNested:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _MarkDecidedSession:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict]] = []

    def begin_nested(self) -> _NoopNested:
        return _NoopNested()

    async def execute(self, clause, params=None):
        self.statements.append((str(clause), dict(params or {})))
        return SimpleNamespace(rowcount=1)


async def test_mark_pending_approval_decided_escribe_update_idempotente() -> None:
    session = _MarkDecidedSession()
    tenant_id, user_id, cid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    ok = await _mark_pending_approval_decided(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=cid,
        tool_call_id="call_1",
        status="approved",
    )

    assert ok is True
    sql, params = session.statements[0]
    assert "UPDATE pending_approvals" in sql
    assert "status = :status" in sql
    assert "tool_call_id = :tool_call_id" in sql
    assert "AND status = 'pending'" in sql  # idempotente: solo transiciona pendientes
    assert params["status"] == "approved"
    assert params["tool_call_id"] == "call_1"
    assert params["conversation_id"] == str(cid)
    assert params["tenant_id"] == str(tenant_id)
    assert params["decided_by"] == str(user_id)


async def test_mark_pending_approval_decided_sin_sesion_devuelve_false() -> None:
    assert (
        await _mark_pending_approval_decided(
            None,
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            tool_call_id="x",
            status="denied",
        )
        is False
    )


async def test_reopen_pending_approval_escribe_rollback_a_pending() -> None:
    session = _MarkDecidedSession()
    await _reopen_pending_approval(
        session,
        tenant_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        tool_call_id="call_1",
    )
    sql, params = session.statements[0]
    assert "status = 'pending'" in sql
    assert "AND status = 'approved'" in sql  # solo revierte lo recién aprobado
    assert params["tool_call_id"] == "call_1"


async def _create_conversation(client: AsyncClient, headers: dict[str, str]) -> str:
    response = await client.post("/v1/conversations", json={"channel": "web"}, headers=headers)
    assert response.status_code == 201
    return response.json()["id"]


async def test_confirm_reabre_fila_durable_ante_excepcion_no_http(
    client, fake_repo, fake_redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Menor: el reopen de la fila durable ante una reanudación fallida ocurre
    con CUALQUIER excepción previa al stream — no solo `HTTPException`."""
    reopen_calls: list[tuple[str, str]] = []

    async def _fake_mark(session: Any, **kwargs: Any) -> bool:
        return True

    async def _fake_resume(**kwargs: Any) -> StreamingResponse:
        raise ValueError("boom inesperado antes del stream")

    async def _fake_reopen(
        session: Any, *, tenant_id: Any, conversation_id: Any, tool_call_id: Any
    ) -> None:
        reopen_calls.append((str(conversation_id), tool_call_id))

    monkeypatch.setattr(conv, "_mark_pending_approval_decided", _fake_mark)
    monkeypatch.setattr(conv, "_resume_approved_turn", _fake_resume)
    monkeypatch.setattr(conv, "_reopen_pending_approval", _fake_reopen)

    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)
    conversation_id = await _create_conversation(client, headers)

    await conv._store_pending_confirmation(
        fake_redis,
        tenant_id=tenant_id,
        conversation_id=uuid.UUID(conversation_id),
        tool_call_id="call_1",
        name="enviar_correo",
        args={},
    )

    try:
        resp = await client.post(
            f"/v1/conversations/{conversation_id}/confirm",
            json={"tool_call_id": "call_1", "approved": True},
            headers=headers,
        )
        assert resp.status_code == 500
    except ValueError:
        pass  # transport re-lanza el error no manejado (raise_app_exceptions=True)

    assert reopen_calls == [(conversation_id, "call_1")]


async def test_confirm_ss_marca_la_fila_durable(
    client, fake_repo, fake_redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`POST /confirm` (aprobando) debe marcar la fila durable como `approved`,
    además de consumir Redis — sin esto la fila quedaba `pending` y reaparecía
    como fantasma en cold open (BOTS-11)."""
    llamadas: dict[str, Any] = {}

    async def fake_mark(session, **kwargs: Any) -> bool:
        llamadas.update(kwargs)
        return True

    async def _empty_events():
        if False:  # pragma: no cover
            yield ""

    async def fake_resume(**kwargs: Any) -> StreamingResponse:
        return StreamingResponse(_empty_events(), media_type="text/event-stream")

    monkeypatch.setattr(conv, "_mark_pending_approval_decided", fake_mark)
    monkeypatch.setattr(conv, "_resume_approved_turn", fake_resume)

    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)
    conversation_id = await _create_conversation(client, headers)

    await conv._store_pending_confirmation(
        fake_redis,
        tenant_id=tenant_id,
        conversation_id=uuid.UUID(conversation_id),
        tool_call_id="call_1",
        name="enviar_correo",
        args={"to": "ana@example.com"},
    )

    resp = await client.post(
        f"/v1/conversations/{conversation_id}/confirm",
        json={"tool_call_id": "call_1", "approved": True},
        headers=headers,
    )

    assert resp.status_code == 200
    assert llamadas["status"] == "approved"
    assert llamadas["tool_call_id"] == "call_1"
    assert llamadas["conversation_id"] == uuid.UUID(conversation_id)


# ---------------------------------------------------------------------------
# BOTS-18 — tarjeta en vivo enmascarada
# ---------------------------------------------------------------------------


async def test_get_pending_confirmation_enmascara_args_sensibles(fake_redis) -> None:
    tenant_id, cid = uuid.uuid4(), uuid.uuid4()
    await conv._store_pending_confirmation(
        fake_redis,
        tenant_id=tenant_id,
        conversation_id=cid,
        tool_call_id="call_1",
        name="enviar_correo",
        args={"to": "ana@example.com", "password": "supersecreto123"},
    )

    pending = await conv._get_pending_confirmation(
        fake_redis, tenant_id=tenant_id, conversation_id=cid
    )

    assert pending is not None
    out = pending.model_dump(mode="json")
    assert out["args"]["to"] == "ana@example.com"
    assert out["args"]["password"] == "supe…"
    # El secreto íntegro NO viaja al cliente.
    assert "supersecreto123" not in json.dumps(out)


def test_mask_sensitive_args_no_muta_el_payload() -> None:
    args = {"token": "sk-abcdef", "headers": {"Authorization": "Bearer tok_1234"}}
    out = _mask_sensitive_args(args)
    assert out["token"] == "sk-a…"
    assert out["headers"]["Authorization"] == "Bear…"
    # El payload original queda intacto para el resume.
    assert args["token"] == "sk-abcdef"


# ---------------------------------------------------------------------------
# BOTS-12 — cursor de historial
# ---------------------------------------------------------------------------


class _ManyRows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _ManyRows:
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)


class _MessagesSession:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.statements: list[tuple[str, dict]] = []

    async def execute(self, clause, params=None):
        self.statements.append((str(clause), dict(params or {})))
        return _ManyRows(self.rows)


def _msg_row(text: str, *, created_at: datetime, message_id: uuid.UUID | None = None) -> dict:
    return {
        "id": message_id or uuid.uuid4(),
        "conversation_id": uuid.uuid4(),
        "role": "user",
        "content": {"text": text},
        "tool_calls": None,
        "created_at": created_at,
    }


def test_cursor_opaco_roundtrip() -> None:
    dt = datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC)
    mid = uuid.uuid4()
    decoded_dt, decoded_id = _decode_message_cursor(_encode_message_cursor(dt, mid))
    assert decoded_dt == dt
    assert decoded_id == mid


def test_cursor_primera_pagina_con_offset_que_viaja_como_espacio() -> None:
    """El cursor de primera página de iOS lleva `+00:00`; en el query string el
    `+` llega como espacio. `_decode_message_cursor` debe restaurarlo y no
    rechazar el cursor (regresión: el chat del bot no cargaba con 422)."""
    sentinel_con_espacio = (
        "9999-12-31T23:59:59.999999 00:00,ffffffff-ffff-ffff-ffff-ffffffffffff"
    )
    dt, mid = _decode_message_cursor(sentinel_con_espacio)
    assert dt == datetime(9999, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)
    assert mid == uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")


async def test_list_normalized_messages_sin_cursor_devuelve_lista_legacy() -> None:
    session = _MessagesSession([])
    result = await list_normalized_messages(
        session,
        tenant_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        limit=50,
    )
    # Legacy intacto: lista plana, LIMIT exacto (sin la fila extra de has_more).
    assert isinstance(result, list)
    assert result == []
    assert session.statements[0][1]["limit"] == 50
    assert "(created_at, id) <" not in session.statements[0][0]


async def test_list_normalized_messages_pagina_con_cursor_y_marca_has_more() -> None:
    base = datetime(2026, 9, 7, tzinfo=UTC)
    rows = [
        _msg_row("m1", created_at=base),
        _msg_row("m2", created_at=base + timedelta(seconds=1)),
    ]
    session = _MessagesSession(rows)

    page = await list_normalized_messages(
        session,
        tenant_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        limit=1,
        before=_encode_message_cursor(base, str(uuid.uuid4())),
    )

    assert isinstance(page, dict)
    assert page["has_more"] is True
    assert len(page["messages"]) == 1
    assert page["next_cursor"] is not None
    sql, params = session.statements[0]
    assert "(created_at, id) < (:before_created_at, :before_id)" in sql
    assert params["limit"] == 2  # capped + 1 para detectar has_more


# ---------------------------------------------------------------------------
# BOTS-09 — epoch de conversación
# ---------------------------------------------------------------------------


async def test_epoch_helpers_roundtrip(fake_redis) -> None:
    tenant_id, cid = uuid.uuid4(), uuid.uuid4()
    assert await get_conversation_epoch(fake_redis, tenant_id=tenant_id, conversation_id=cid) == 0
    assert await increment_conversation_epoch(fake_redis, tenant_id=tenant_id, conversation_id=cid) == 1
    assert await increment_conversation_epoch(fake_redis, tenant_id=tenant_id, conversation_id=cid) == 2
    assert await get_conversation_epoch(fake_redis, tenant_id=tenant_id, conversation_id=cid) == 2


class _Filas:
    def __init__(self, fila: dict[str, Any] | None) -> None:
        self._fila = fila

    def mappings(self) -> _Filas:
        return self

    def all(self) -> list[dict[str, Any]]:
        return [self._fila] if self._fila is not None else []

    def first(self) -> dict[str, Any] | None:
        return self._fila


class _WorkerSession:
    def __init__(self, fila: dict[str, Any]) -> None:
        self._fila = fila
        self.sqls: list[str] = []

    async def execute(self, clause, params=None):
        self.sqls.append(str(clause))
        return _Filas(self._fila)


def _fila_worker(worker_id: uuid.UUID, conversation_id: uuid.UUID | None) -> dict[str, Any]:
    return {
        "id": str(worker_id),
        "tenant_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "name": "BotAlpha",
        "display_name": "BotAlpha",
        "conversation_id": str(conversation_id) if conversation_id else None,
        "enabled": True,
        "status": "idle",
    }


async def test_clear_worker_incrementa_epoch(
    app, fake_repo, fake_redis, test_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    sesion = _WorkerSession(_fila_worker(worker_id, conversation_id))
    import edecan_api.deps as edecan_deps

    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: sesion
    incrementos: list[tuple[uuid.UUID, uuid.UUID]] = []

    async def fake_incr(redis_client, *, tenant_id, conversation_id):
        incrementos.append((tenant_id, conversation_id))
        return 1

    monkeypatch.setattr(bot_service, "increment_conversation_epoch", fake_incr)
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())

    resp = await client.post(f"/v1/agents/workers/{worker_id}/clear", headers=headers)

    assert resp.status_code == 204
    assert any("DELETE FROM messages" in sql for sql in sesion.sqls)
    assert incrementos and incrementos[0][1] == conversation_id


async def test_list_worker_messages_devuelve_epoch_en_header(
    app, fake_repo, fake_redis, test_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    sesion = _WorkerSession(_fila_worker(worker_id, conversation_id))
    import edecan_api.deps as edecan_deps

    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: sesion

    async def fake_epoch(redis_client, *, tenant_id, conversation_id):
        return 7

    async def fake_list(session, *, tenant_id, conversation_id, limit=None, before=None):
        return [{"id": "m1"}]

    monkeypatch.setattr(bot_service, "get_conversation_epoch", fake_epoch)
    monkeypatch.setattr(bot_service, "list_normalized_messages", fake_list)
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())

    resp = await client.get(f"/v1/agents/workers/{worker_id}/messages", headers=headers)

    assert resp.status_code == 200
    assert resp.headers["x-conversation-epoch"] == "7"


# ---------------------------------------------------------------------------
# BOTS-20 — aviso de multi-réplica
# ---------------------------------------------------------------------------


def test_config_single_replica_default_true(test_settings) -> None:
    assert test_settings.EDECAN_API_SINGLE_REPLICA is True


async def test_lifespan_avisa_si_multi_replica(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    import edecan_api.main as main_mod

    class _Registry:
        def load_entry_points(self, group) -> None:
            pass

    class _HealthStore:
        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

    class _Router:
        async def aclose(self) -> None:
            pass

    class _NoScanRedis:
        # sin `scan_iter`: el cleanup de BOTS-24 se salta (devuelve 0).
        async def get(self, key):
            return None

    fake_settings = SimpleNamespace(
        EDECAN_API_SINGLE_REPLICA=False,
        EDECAN_LOCAL_MODE=False,
        CHAT_IDEMPOTENCY_TTL_SECONDS=60,
    )
    fake_app = SimpleNamespace(
        state=SimpleNamespace(
            tool_registry=_Registry(),
            settings=fake_settings,
            provider_health_store=_HealthStore(),
            llm_router=_Router(),
        )
    )
    monkeypatch.setattr(main_mod, "get_redis", lambda settings: _NoScanRedis())
    caplog.set_level(logging.WARNING, logger="edecan_api")

    async with main_mod._lifespan(fake_app):
        pass

    assert "EDECAN_API_SINGLE_REPLICA=False" in caplog.text


# ---------------------------------------------------------------------------
# BOTS-24 — cleanup de arranque
# ---------------------------------------------------------------------------


class _ScanRedis:
    def __init__(self, records: dict[str, str]) -> None:
        self.records = records

    async def get(self, key: str) -> str | None:
        return self.records.get(key)

    async def set(self, key: str, value: str, ex=None) -> None:
        self.records[key] = value

    async def scan_iter(self, match: str | None = None):
        prefix = (match or "*").rstrip("*")
        for key in list(self.records):
            if key.startswith(prefix):
                yield key


async def test_cleanup_marca_inflight_huerfanos_como_interrupted() -> None:
    import edecan_api.main as main_mod

    records = {
        "chat_idempotency:t:u:c:k1": json.dumps(
            {"status": "in_flight", "request_hash": "h1", "owner_token": "o1"}
        ),
        "chat_idempotency:t:u:c:k2": json.dumps({"status": "queued", "request_hash": "h2"}),
        "chat_idempotency:t:u:c:k3": json.dumps({"status": "completed", "events": ["x"]}),
        "otra:cosa": json.dumps({"status": "in_flight"}),
    }
    redis = _ScanRedis(records)

    marcadas = await main_mod._cleanup_orphan_inflight_turns(redis, ttl_seconds=60)

    assert marcadas == 2
    assert json.loads(records["chat_idempotency:t:u:c:k1"])["status"] == "interrupted"
    assert json.loads(records["chat_idempotency:t:u:c:k2"])["status"] == "interrupted"
    assert "interrupted_at" in json.loads(records["chat_idempotency:t:u:c:k1"])
    # `completed` NO se toca: un turno terminado no se re-etiqueta.
    assert json.loads(records["chat_idempotency:t:u:c:k3"])["status"] == "completed"
    # Otra prefijo NO se toca.
    assert json.loads(records["otra:cosa"])["status"] == "in_flight"


async def test_cleanup_sin_scan_iter_devuelve_cero() -> None:
    import edecan_api.main as main_mod

    class _NoScan:
        async def get(self, key):
            return None

    assert await main_mod._cleanup_orphan_inflight_turns(_NoScan(), ttl_seconds=60) == 0