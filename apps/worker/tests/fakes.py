"""Fakes en memoria para testear `edecan_worker` sin Postgres/SQS/S3 reales ni
paquetes hermanos (`ARCHITECTURE.md` §10.1: "los tests no importan paquetes
hermanos, usan fakes/stubs").

`FakeRepo` implementa el `Protocol Repo` de `edecan_worker.repo` en memoria;
los tests de handlers monkeypatchean el nombre `SqlRepo` importado en el
módulo del handler bajo prueba (p. ej. `monkeypatch.setattr(ingest_file,
"SqlRepo", lambda session: fake_repo)`) para que use `FakeRepo` en su lugar,
sin tocar Postgres. `FakeSession` es un placeholder: los handlers reciben una
instancia vía `deps.session_factory(None)`, pero nunca la usan directamente
—hablan con `SqlRepo(session)`, que es justo lo que el monkeypatch sustituye—
así que solo necesita existir para que el `async with` tenga algo real.
"""

from __future__ import annotations

import sys
import types
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from edecan_worker.config import Settings
from edecan_worker.deps import Deps


def utcnow() -> datetime:
    return datetime.now(UTC)


def install_fake_edecan_core_queue(
    monkeypatch: pytest.MonkeyPatch, enqueue_fn: Callable[..., Awaitable[uuid.UUID]]
) -> None:
    """Registra un `edecan_core.queue` falso en `sys.modules`.

    `edecan_core` es un paquete hermano que puede aún no existir en este
    workspace mientras se construye en paralelo (`ARCHITECTURE.md` §10.1).
    Los handlers que lo necesitan (`send_reminder_scan`, `scheduler`) lo
    importan de forma perezosa con `from edecan_core.queue import enqueue`
    DENTRO de la función, así que basta con pre-registrar un módulo falso en
    `sys.modules` antes de invocar el handler: el `import` de Python lo
    encuentra ahí y nunca toca el disco — no hace falta que el paquete real
    exista. `monkeypatch.setitem` deshace el registro al terminar el test.
    """
    fake_queue_module = types.ModuleType("edecan_core.queue")
    fake_queue_module.enqueue = enqueue_fn  # type: ignore[attr-defined]
    fake_core_module = types.ModuleType("edecan_core")
    fake_core_module.queue = fake_queue_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "edecan_core", fake_core_module)
    monkeypatch.setitem(sys.modules, "edecan_core.queue", fake_queue_module)


# ---------------------------------------------------------------------------
# Sesión / repo falsos
# ---------------------------------------------------------------------------


class FakeSession:
    """Placeholder de `AsyncSession` — ver docstring del módulo."""


@asynccontextmanager
async def fake_session_factory(tenant_id: uuid.UUID | None) -> AsyncIterator[FakeSession]:
    yield FakeSession()


@dataclass
class FakeRepo:
    """Sustituto en memoria de `edecan_worker.repo.SqlRepo` para tests."""

    files: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    file_chunks: list[dict[str, Any]] = field(default_factory=list)
    usage_events: list[dict[str, Any]] = field(default_factory=list)
    reminders: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    conversations: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    messages: list[dict[str, Any]] = field(default_factory=list)
    oauth_tokens: list[dict[str, Any]] = field(default_factory=list)
    connector_accounts: list[dict[str, Any]] = field(default_factory=list)
    tenants: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    memory_items: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    memory_edges: list[dict[str, Any]] = field(default_factory=list)
    personas: dict[tuple[uuid.UUID, uuid.UUID], dict[str, Any]] = field(default_factory=dict)

    # -- archivos / ingesta ---------------------------------------------------

    async def get_file(self, *, tenant_id: uuid.UUID, file_id: uuid.UUID) -> dict[str, Any] | None:
        row = self.files.get(file_id)
        if row is None or row["tenant_id"] != tenant_id:
            return None
        return dict(row)

    async def update_file_status(
        self, *, tenant_id: uuid.UUID, file_id: uuid.UUID, status: str
    ) -> None:
        if file_id in self.files and self.files[file_id]["tenant_id"] == tenant_id:
            self.files[file_id]["status"] = status

    async def add_file_chunks(
        self,
        *,
        tenant_id: uuid.UUID,
        file_id: uuid.UUID,
        chunks: list[tuple[int, str, list[float]]],
    ) -> None:
        for seq, text_piece, embedding in chunks:
            self.file_chunks.append(
                {
                    "id": uuid.uuid4(),
                    "tenant_id": tenant_id,
                    "file_id": file_id,
                    "seq": seq,
                    "text": text_piece,
                    "embedding": embedding,
                }
            )

    # -- uso / cuotas -----------------------------------------------------------

    async def add_usage_event(
        self,
        *,
        tenant_id: uuid.UUID,
        kind: str,
        quantity: float,
        meta: dict[str, Any] | None = None,
    ) -> None:
        self.usage_events.append(
            {"tenant_id": tenant_id, "kind": kind, "quantity": quantity, "meta": meta or {}}
        )

    # -- recordatorios --------------------------------------------------------------

    async def list_due_reminders(self, *, now: datetime) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.reminders.values()
            if row["status"] == "pending" and row["due_at"] <= now
        ]

    async def get_reminder(
        self, *, tenant_id: uuid.UUID, reminder_id: uuid.UUID
    ) -> dict[str, Any] | None:
        row = self.reminders.get(reminder_id)
        if row is None or row["tenant_id"] != tenant_id:
            return None
        return dict(row)

    async def mark_reminder_sent(self, *, tenant_id: uuid.UUID, reminder_id: uuid.UUID) -> None:
        if reminder_id in self.reminders and self.reminders[reminder_id]["tenant_id"] == tenant_id:
            self.reminders[reminder_id]["status"] = "sent"

    # -- conversaciones / mensajes ---------------------------------------------------

    async def get_conversation(
        self, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> dict[str, Any] | None:
        row = self.conversations.get(conversation_id)
        if row is None or row["tenant_id"] != tenant_id:
            return None
        return dict(row)

    async def get_conversation_by_title(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, title: str
    ) -> dict[str, Any] | None:
        for row in self.conversations.values():
            same_tenant_and_user = row["tenant_id"] == tenant_id and row["user_id"] == user_id
            if same_tenant_and_user and row["title"] == title:
                return dict(row)
        return None

    async def create_conversation(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, title: str | None, channel: str
    ) -> dict[str, Any]:
        row = {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "user_id": user_id,
            "title": title,
            "channel": channel,
            "updated_at": utcnow(),
        }
        self.conversations[row["id"]] = row
        return dict(row)

    async def add_message(
        self,
        *,
        tenant_id: uuid.UUID,
        conversation_id: uuid.UUID,
        role: str,
        content: Any,
        tool_calls: Any = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> dict[str, Any]:
        row = {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "tool_calls": tool_calls,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
        }
        self.messages.append(row)
        if conversation_id in self.conversations:
            self.conversations[conversation_id]["updated_at"] = utcnow()
        return dict(row)

    # -- conectores / oauth -------------------------------------------------------------

    async def list_expiring_oauth_tokens(
        self, *, tenant_id: uuid.UUID | None, before: datetime
    ) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.oauth_tokens
            if row["expires_at"] is not None
            and row["expires_at"] < before
            and (tenant_id is None or row["tenant_id"] == tenant_id)
        ]

    async def get_connector_account_by_key(
        self, *, tenant_id: uuid.UUID, connector_key: str
    ) -> dict[str, Any] | None:
        matches = [
            row
            for row in self.connector_accounts
            if row["tenant_id"] == tenant_id and row["connector_key"] == connector_key
        ]
        if not matches:
            return None
        return dict(min(matches, key=lambda r: r["created_at"]))

    # -- tenants ----------------------------------------------------------------------------

    async def get_tenant(self, *, tenant_id: uuid.UUID) -> dict[str, Any] | None:
        row = self.tenants.get(tenant_id)
        return dict(row) if row else None

    # -- memoria ------------------------------------------------------------------------------

    async def list_memory_items_with_embedding(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.memory_items.values()
            if row["tenant_id"] == tenant_id and row["user_id"] == user_id and row.get("embedding")
        ]

    async def update_memory_item_importance(
        self, *, tenant_id: uuid.UUID, memory_id: uuid.UUID, importance: float
    ) -> None:
        row = self.memory_items.get(memory_id)
        if row is not None and row["tenant_id"] == tenant_id:
            row["importance"] = importance

    async def delete_memory_items(
        self, *, tenant_id: uuid.UUID, memory_ids: list[uuid.UUID]
    ) -> int:
        deleted = 0
        for memory_id in memory_ids:
            row = self.memory_items.get(memory_id)
            if row is not None and row["tenant_id"] == tenant_id:
                del self.memory_items[memory_id]
                deleted += 1
        return deleted

    # -- memoria: extracción por LLM ---------------------------------------------------

    async def get_persona(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID
    ) -> dict[str, Any] | None:
        row = self.personas.get((tenant_id, user_id))
        return dict(row) if row is not None else None

    async def list_recent_messages_for_user(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, limit: int
    ) -> list[dict[str, Any]]:
        conversation_ids = {
            conv_id
            for conv_id, conv in self.conversations.items()
            if conv["tenant_id"] == tenant_id and conv["user_id"] == user_id
        }
        rows = [
            dict(row)
            for row in self.messages
            if row["tenant_id"] == tenant_id and row["conversation_id"] in conversation_ids
        ]
        return rows[-limit:]

    async def list_memory_contents(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, limit: int
    ) -> list[dict[str, Any]]:
        rows = [
            dict(row)
            for row in self.memory_items.values()
            if row["tenant_id"] == tenant_id and row["user_id"] == user_id
        ]
        rows.sort(key=lambda row: row.get("importance", 0.0), reverse=True)
        return rows[:limit]

    async def add_memory_item(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        kind: str,
        content: str,
        importance: float,
        source: str,
        embedding: list[float] | None,
    ) -> dict[str, Any]:
        row = {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "user_id": user_id,
            "kind": kind,
            "content": content,
            "importance": importance,
            "source": source,
            "embedding": embedding,
            "created_at": utcnow(),
        }
        self.memory_items[row["id"]] = row
        return dict(row)

    # -- memoria: grafo -----------------------------------------------------------------

    async def add_edge(
        self, *, tenant_id: uuid.UUID, src_id: uuid.UUID, dst_id: uuid.UUID, relation: str
    ) -> uuid.UUID:
        edge_id = uuid.uuid4()
        self.memory_edges.append(
            {
                "id": edge_id,
                "tenant_id": tenant_id,
                "src_id": src_id,
                "dst_id": dst_id,
                "relation": relation,
            }
        )
        return edge_id


# ---------------------------------------------------------------------------
# S3 / SQS falsos en memoria
# ---------------------------------------------------------------------------


class FakeS3Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def read(self) -> bytes:
        return self._data


class FakeS3:
    """S3 falso en memoria: `{(bucket, key): bytes}`."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put(self, bucket: str, key: str, data: bytes) -> None:
        self.objects[(bucket, key)] = data

    async def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        data = self.objects.get((Bucket, Key))
        if data is None:
            raise KeyError(f"objeto S3 no encontrado: s3://{Bucket}/{Key}")
        return {"Body": FakeS3Body(data)}


class FakeSQS:
    """SQS falso en memoria: guarda los mensajes enviados/borrados para asserts."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self.to_receive: list[dict[str, Any]] = []

    async def send_message(
        self, *, QueueUrl: str, MessageBody: str, DelaySeconds: int = 0
    ) -> dict[str, Any]:
        self.sent.append(
            {"QueueUrl": QueueUrl, "MessageBody": MessageBody, "DelaySeconds": DelaySeconds}
        )
        return {"MessageId": str(uuid.uuid4())}

    async def delete_message(self, *, QueueUrl: str, ReceiptHandle: str) -> dict[str, Any]:
        self.deleted.append(ReceiptHandle)
        return {}

    async def receive_message(
        self, *, QueueUrl: str, WaitTimeSeconds: int, MaxNumberOfMessages: int
    ) -> dict[str, Any]:
        batch = self.to_receive[:MaxNumberOfMessages]
        self.to_receive = self.to_receive[MaxNumberOfMessages:]
        return {"Messages": batch}


# ---------------------------------------------------------------------------
# Embedder / LLM falsos
# ---------------------------------------------------------------------------


class FakeEmbedder:
    """Determinista: cada texto -> vector de longitud fija `dim` según su hash."""

    def __init__(self, dim: int = 8) -> None:
        self.dim = dim
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        vectors = []
        for text_piece in texts:
            seed = sum(text_piece.encode("utf-8")) or 1
            vectors.append([((seed * (i + 1)) % 97) / 97 for i in range(self.dim)])
        return vectors


@dataclass
class FakeUsage:
    input_tokens: int = 10
    output_tokens: int = 20


@dataclass
class FakeCompletionResponse:
    text: str
    usage: FakeUsage = field(default_factory=FakeUsage)
    tool_calls: list[Any] = field(default_factory=list)
    stop_reason: str = "end"


class FakeProvider:
    def __init__(self, reply: str = "contenido generado") -> None:
        self.reply = reply
        self.requests: list[Any] = []

    async def complete(self, req: Any) -> FakeCompletionResponse:
        self.requests.append(req)
        return FakeCompletionResponse(text=self.reply)


class FakeLLMRouter:
    def __init__(self, model: str = "modelo-fake") -> None:
        self.provider = FakeProvider()
        self.model = model
        self.resolved: list[tuple[str, dict[str, Any]]] = []

    def resolve(self, alias: str, tenant_flags: dict[str, Any]) -> tuple[FakeProvider, str]:
        self.resolved.append((alias, tenant_flags))
        return self.provider, self.model


# ---------------------------------------------------------------------------
# Vault falso
# ---------------------------------------------------------------------------


@dataclass
class FakeTokenBundle:
    access_token: str
    refresh_token: str | None = None
    expires_at: Any = None
    scopes: list[str] = field(default_factory=list)
    token_type: str = "bearer"


class FakeVault:
    def __init__(self) -> None:
        self.store: dict[tuple[uuid.UUID, uuid.UUID], FakeTokenBundle] = {}
        self.puts: list[tuple[uuid.UUID, uuid.UUID, FakeTokenBundle]] = []

    async def get(
        self, *, tenant_id: uuid.UUID, connector_account_id: uuid.UUID
    ) -> FakeTokenBundle | None:
        return self.store.get((tenant_id, connector_account_id))

    async def put(
        self, *, tenant_id: uuid.UUID, connector_account_id: uuid.UUID, bundle: FakeTokenBundle
    ) -> None:
        self.store[(tenant_id, connector_account_id)] = bundle
        self.puts.append((tenant_id, connector_account_id, bundle))


# ---------------------------------------------------------------------------
# Deps de prueba
# ---------------------------------------------------------------------------


def make_deps(**overrides: Any) -> Deps:
    settings = overrides.pop("settings", None) or Settings(
        SQS_QUEUE_URL="http://localhost:4566/000000000000/edecan-jobs",
        S3_BUCKET="edecan-files-test",
    )
    defaults: dict[str, Any] = dict(
        settings=settings,
        session_factory=fake_session_factory,
        s3=FakeS3(),
        sqs=FakeSQS(),
        embedder=FakeEmbedder(),
        llm_router=FakeLLMRouter(),
        vault=lambda session: FakeVault(),
    )
    defaults.update(overrides)
    return Deps(**defaults)
