"""Tests del chat de bots y su ciclo de vida (bug + delete + ack de identidad).

Cubre la familia de fallos que dejó los chats de bot mudos («Se perdió la
conexión con Edecán» con HTTP 200 y stream vacío):

1. `persist_chat_message` NO debe sombrear `sqlalchemy.text` con un parámetro
   `text` — el TypeError exacto que mató cada turno de bot antes del primer
   evento SSE.
2. `DELETE /v1/agents/workers/{id}` borra conversación + mensajes + worker.
3. Un PATCH que cambia identidad agenda el ack de identidad (turno real del
   bot), con el resumen de qué cambió.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from conftest import auth_headers
from httpx import ASGITransport, AsyncClient

from edecan_api.bot_turn_service import persist_chat_message


class _Filas:
    """Doble mínima del resultado de SQLAlchemy: `.mappings().first()`."""

    def __init__(self, fila: dict[str, Any] | None) -> None:
        self._fila = fila

    def mappings(self) -> _Filas:
        return self

    def first(self) -> dict[str, Any] | None:
        return self._fila


class _FakeSesion:
    """Sesión que graba cada statement; la primera consulta devuelve `fila`."""

    def __init__(self, fila: dict[str, Any] | None = None) -> None:
        self.statements: list[tuple[str, dict | None]] = []
        self._fila = fila

    async def execute(self, clause: Any, params: dict | None = None) -> Any:
        self.statements.append((str(clause), params))
        return _Filas(self._fila)

    @property
    def sqls(self) -> list[str]:
        return [sql for sql, _ in self.statements]


async def test_persist_chat_message_no_sombrea_sqlalchemy_text() -> None:
    """Regresión del bug del chat de bots: el parámetro se llama `texto` y el
    insert ejecuta. Con el shadowing (`text: str`) esto reventaba con
    `TypeError: 'str' object is not callable` ANTES de tocar la base."""
    sesion = _FakeSesion()
    await persist_chat_message(
        sesion,
        tenant_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        role="user",
        texto="Hola Fronti",
        sender_id="user",
        sender_name="Tú",
    )
    assert "INSERT INTO messages" in sesion.sqls[0]


def _fila_worker(worker_id: uuid.UUID, conversation_id: uuid.UUID | None) -> dict[str, Any]:
    return {
        "id": str(worker_id),
        "tenant_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "name": "Fronti",
        "display_name": "Fronti",
        "conversation_id": str(conversation_id) if conversation_id else None,
        "enabled": True,
        "status": "idle",
    }


async def test_delete_worker_borra_mensajes_conversacion_y_bot(
    app, fake_repo: Any, fake_redis: Any, test_settings: Any
) -> None:
    sesion = _FakeSesion(fila=_fila_worker(worker_id := uuid.uuid4(), conversation_id=uuid.uuid4()))
    import edecan_api.deps as edecan_deps

    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: sesion
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())

    resp = await client.delete(f"/v1/agents/workers/{worker_id}", headers=headers)
    assert resp.status_code == 204
    borras = [sql for sql in sesion.sqls if "DELETE FROM" in sql]
    assert len(borras) == 3
    assert "DELETE FROM messages" in borras[0]
    assert "DELETE FROM conversations" in borras[1]
    assert "DELETE FROM persistent_agents" in borras[2]


async def test_delete_worker_404_si_no_existe(app) -> None:
    sesion = _FakeSesion(fila=None)
    import edecan_api.deps as edecan_deps

    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: sesion
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())

    resp = await client.delete(f"/v1/agents/workers/{uuid.uuid4()}", headers=headers)
    assert resp.status_code == 404
    assert not any("DELETE FROM" in sql for sql in sesion.sqls)


async def test_patch_identidad_agenda_ack_con_resumen(
    app, fake_repo: Any, fake_redis: Any, test_settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker_id = uuid.uuid4()
    sesion = _FakeSesion(fila=_fila_worker(worker_id, uuid.uuid4()))
    import edecan_api.deps as edecan_deps

    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: sesion
    acks: list[dict[str, Any]] = []

    async def _ack_falso(request: Any, **kwargs: Any) -> None:
        acks.append(kwargs)

    monkeypatch.setattr("edecan_api.bot_turn_service.ack_cambio_identidad", _ack_falso)
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())

    resp = await client.patch(
        f"/v1/agents/workers/{worker_id}",
        headers=headers,
        json={"name": "Fronti Pro", "purpose": "Experto en UI y diseño de producto"},
    )
    assert resp.status_code == 200
    update_sql = next(sql for sql in sesion.sqls if "UPDATE persistent_agents" in sql)
    assert "name = :name" in update_sql
    assert "purpose = :purpose" in update_sql
    assert len(acks) == 1
    resumen = acks[0]["resumen"]
    assert "Fronti Pro" in resumen
    assert "Experto en UI" in resumen


async def test_patch_sin_cambio_de_identidad_no_agenda_ack(
    app, fake_repo: Any, fake_redis: Any, test_settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker_id = uuid.uuid4()
    fila = _fila_worker(worker_id, uuid.uuid4())
    fila["name"] = "Fronti"
    sesion = _FakeSesion(fila=fila)
    import edecan_api.deps as edecan_deps

    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: sesion
    acks: list[dict[str, Any]] = []

    async def _ack_falso(request: Any, **kwargs: Any) -> None:
        acks.append(kwargs)

    monkeypatch.setattr("edecan_api.bot_turn_service.ack_cambio_identidad", _ack_falso)
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())

    resp = await client.patch(
        f"/v1/agents/workers/{worker_id}",
        headers=headers,
        json={"status": "paused"},
    )
    assert resp.status_code == 200
    assert acks == []
