"""Acceso a datos de `edecan_worker` (ARCHITECTURE.md §10.3).

Igual que `apps/api/edecan_api/repo.py`: `ARCHITECTURE.md` §10.3 pinnea los
nombres EXACTOS de tablas/columnas pero no una API de modelos ORM de
`edecan_db` (ese paquete solo pinnea `edecan_db.session.get_session` y
`edecan_db.vault.TokenVault`, §10.3/§10.4). Para no acoplar esta app a una API
de modelos no especificada en el contrato, `SqlRepo` habla SQL parametrizado
directo contra el esquema pinneado, sobre la `AsyncSession` que entrega
`edecan_db.session.get_session(None)` — conexión "dueño", **bypassa Row-Level
Security** (ARCHITECTURE.md §2). Por eso **cada método recibe `tenant_id`
explícito y lo usa en el `WHERE`**: el worker nunca confía en RLS para aislar
tenants.

`list_due_reminders` y `list_expiring_oauth_tokens(tenant_id=None)` son la
única excepción deliberada a "siempre filtrar por tenant_id": son barridos
globales que ejecutan jobs de sistema sin tenant propio (`send_reminder_scan`,
`sync_connector` en modo barrido) — ver la nota en cada método.

`Repo` es el `Protocol` que consumen los handlers — la construcción
`SqlRepo(session)` es el punto de inyección. `tests/fakes.py` lo sustituye
por `FakeRepo` (en memoria, sin Postgres), tal como exige `ARCHITECTURE.md`
§10.1: "los tests no importan paquetes hermanos, usan fakes/stubs".
"""

from __future__ import annotations

import calendar
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

Row = dict[str, Any]


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_jsonb(value: Any) -> str:
    """Serializa `value` a JSON para columnas `jsonb` (`"null"` si es `None`)."""
    return json.dumps(value if value is not None else None)


def vector_literal(values: list[float]) -> str:
    """Formato de texto de entrada de pgvector (`"[v1,v2,...]"`) para castear `::vector`."""
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


def parse_vector(value: Any) -> list[float] | None:
    """Parsea una columna `vector` leída sin el codec de pgvector registrado.

    Sin `pgvector.asyncpg.register_vector(conn)`, asyncpg devuelve las
    columnas `vector` por su representación de texto (`"[0.1,0.2,...]"`).
    También acepta una lista/tupla ya parseada, por si el codec sí está
    registrado en el engine (`edecan_db` no lo pinnea explícitamente).
    """
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return [float(v) for v in value]
    text_value = str(value).strip().strip("[]")
    if not text_value:
        return []
    return [float(v) for v in text_value.split(",")]


def _coerce_datetime(value: Any) -> datetime:
    """Normaliza un valor de columna `timestamptz` (o de `rrule`/`UNTIL`) a
    `datetime` tz-aware UTC.

    `asyncpg` ya devuelve `datetime` para columnas `timestamptz`; el `except`
    de texto ISO 8601 replica el mismo patrón defensivo que
    `edecan_toolkit.recordatorios._parsear_fecha`.
    """
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


_RRULE_FREQS = ("DAILY", "WEEKLY", "MONTHLY", "YEARLY")


def _add_months(dt: datetime, months: int) -> datetime:
    """Suma `months` meses calendario a `dt`, recortando el día al último
    válido del mes destino (ej. 31 ene + 1 mes -> 28/29 feb)."""
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _next_occurrence(due_at: Any, rrule: str, *, after: datetime) -> datetime | None:
    """Siguiente ocurrencia de un recordatorio recurrente, estrictamente
    posterior a `after`, según `rrule` (subconjunto de RFC 5545 que cubre
    `FREQ=DAILY|WEEKLY|MONTHLY|YEARLY`, `INTERVAL` y `UNTIL`).

    Devuelve `None` si `rrule` no trae un `FREQ` reconocido o si `UNTIL` ya
    pasó: en ambos casos el llamador (`SqlRepo.mark_reminder_sent`) debe
    tratar el recordatorio como no-recurrente (cerrarlo con status='sent').
    """
    params: dict[str, str] = {}
    for part in rrule.upper().replace(" ", "").split(";"):
        key, sep, value = part.partition("=")
        if sep:
            params[key] = value

    freq = params.get("FREQ")
    if freq not in _RRULE_FREQS:
        return None

    try:
        interval = max(1, int(params.get("INTERVAL", "1")))
    except ValueError:
        interval = 1

    until: datetime | None = None
    if params.get("UNTIL"):
        try:
            until = _coerce_datetime(params["UNTIL"])
        except ValueError:
            until = None

    next_due = _coerce_datetime(due_at)
    for _ in range(10_000):  # cota de seguridad: nunca debería iterar tanto
        if freq == "DAILY":
            next_due = next_due + timedelta(days=interval)
        elif freq == "WEEKLY":
            next_due = next_due + timedelta(weeks=interval)
        elif freq == "MONTHLY":
            next_due = _add_months(next_due, interval)
        else:  # YEARLY
            next_due = _add_months(next_due, interval * 12)

        if until is not None and next_due > until:
            return None
        if next_due > after:
            return next_due
    return None


class Repo(Protocol):
    """Contrato de acceso a datos que consumen los handlers de `edecan_worker`."""

    # -- archivos / ingesta (`ingest_file`) ------------------------------------
    async def get_file(self, *, tenant_id: uuid.UUID, file_id: uuid.UUID) -> Row | None: ...
    async def update_file_status(
        self, *, tenant_id: uuid.UUID, file_id: uuid.UUID, status: str
    ) -> None: ...
    async def add_file_chunks(
        self,
        *,
        tenant_id: uuid.UUID,
        file_id: uuid.UUID,
        chunks: list[tuple[int, str, list[float]]],
    ) -> None: ...

    # -- uso / cuotas (`ingest_file`, `generate_content`) ----------------------------
    async def add_usage_event(
        self,
        *,
        tenant_id: uuid.UUID,
        kind: str,
        quantity: float,
        meta: dict[str, Any] | None = None,
    ) -> None: ...

    # -- recordatorios (`send_reminder_scan`, `send_reminder`) --------------------------
    async def list_due_reminders(self, *, now: datetime) -> list[Row]: ...
    async def get_reminder(self, *, tenant_id: uuid.UUID, reminder_id: uuid.UUID) -> Row | None: ...
    async def mark_reminder_sent(self, *, tenant_id: uuid.UUID, reminder_id: uuid.UUID) -> None: ...

    # -- conversaciones / mensajes (`send_reminder`, `generate_content`) -----------------
    async def get_conversation(
        self, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> Row | None: ...
    async def get_conversation_by_title(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, title: str
    ) -> Row | None: ...
    async def create_conversation(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, title: str | None, channel: str
    ) -> Row: ...
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
    ) -> Row: ...

    # -- conectores / oauth (`sync_connector`) ---------------------------------------
    async def list_expiring_oauth_tokens(
        self, *, tenant_id: uuid.UUID | None, before: datetime
    ) -> list[Row]: ...
    async def get_connector_account_by_key(
        self, *, tenant_id: uuid.UUID, connector_key: str
    ) -> Row | None: ...

    # -- tenants (`generate_content`) ------------------------------------------------
    async def get_tenant(self, *, tenant_id: uuid.UUID) -> Row | None: ...

    # -- memoria (`memory_consolidate`) ------------------------------------------------
    async def list_memory_items_with_embedding(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[Row]: ...
    async def update_memory_item_importance(
        self, *, tenant_id: uuid.UUID, memory_id: uuid.UUID, importance: float
    ) -> None: ...
    async def delete_memory_items(
        self, *, tenant_id: uuid.UUID, memory_ids: list[uuid.UUID]
    ) -> int: ...

    # -- memoria: extracción por LLM (`memory_consolidate`, fase 1) -------------------
    async def get_persona(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> Row | None: ...
    async def list_recent_messages_for_user(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, limit: int
    ) -> list[Row]: ...
    async def list_memory_contents(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, limit: int
    ) -> list[Row]: ...
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
    ) -> Row: ...

    # -- memoria: grafo (`memory_consolidate`, fase 1) --------------------------------
    async def add_edge(
        self, *, tenant_id: uuid.UUID, src_id: uuid.UUID, dst_id: uuid.UUID, relation: str
    ) -> uuid.UUID: ...


class SqlRepo:
    """Implementación de `Repo` sobre Postgres (SQL parametrizado, sin ORM)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def _first(self, stmt: str, params: dict[str, Any]) -> Row | None:
        result = await self._s.execute(text(stmt), params)
        row = result.mappings().first()
        return dict(row) if row is not None else None

    async def _all(self, stmt: str, params: dict[str, Any]) -> list[Row]:
        result = await self._s.execute(text(stmt), params)
        return [dict(row) for row in result.mappings().all()]

    async def _exec(self, stmt: str, params: dict[str, Any]) -> int:
        result = await self._s.execute(text(stmt), params)
        return result.rowcount or 0

    # -- archivos / ingesta -----------------------------------------------------

    async def get_file(self, *, tenant_id: uuid.UUID, file_id: uuid.UUID) -> Row | None:
        return await self._first(
            "SELECT * FROM files WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": file_id},
        )

    async def update_file_status(
        self, *, tenant_id: uuid.UUID, file_id: uuid.UUID, status: str
    ) -> None:
        await self._exec(
            "UPDATE files SET status = :status, updated_at = :now "
            "WHERE tenant_id = :tenant_id AND id = :id",
            {"status": status, "now": utcnow(), "tenant_id": tenant_id, "id": file_id},
        )

    async def add_file_chunks(
        self,
        *,
        tenant_id: uuid.UUID,
        file_id: uuid.UUID,
        chunks: list[tuple[int, str, list[float]]],
    ) -> None:
        now = utcnow()
        for seq, chunk_text_value, embedding in chunks:
            await self._exec(
                """
                INSERT INTO file_chunks (
                    id, tenant_id, file_id, seq, text, embedding, created_at, updated_at
                )
                VALUES (:id, :tenant_id, :file_id, :seq, :text, :embedding ::vector, :now, :now)
                """,
                {
                    "id": new_uuid(),
                    "tenant_id": tenant_id,
                    "file_id": file_id,
                    "seq": seq,
                    "text": chunk_text_value,
                    "embedding": vector_literal(embedding),
                    "now": now,
                },
            )

    # -- uso / cuotas ---------------------------------------------------------------

    async def add_usage_event(
        self,
        *,
        tenant_id: uuid.UUID,
        kind: str,
        quantity: float,
        meta: dict[str, Any] | None = None,
    ) -> None:
        await self._exec(
            """
            INSERT INTO usage_events (id, tenant_id, kind, quantity, meta, created_at, updated_at)
            VALUES (:id, :tenant_id, :kind, :quantity, :meta ::jsonb, :now, :now)
            """,
            {
                "id": new_uuid(),
                "tenant_id": tenant_id,
                "kind": kind,
                "quantity": quantity,
                "meta": to_jsonb(meta or {}),
                "now": utcnow(),
            },
        )

    # -- recordatorios ------------------------------------------------------------------

    async def list_due_reminders(self, *, now: datetime) -> list[Row]:
        # Barrido global deliberado (sin filtro de tenant): lo dispara
        # `send_reminder_scan`, un job de sistema (`tenant_id is None`) que
        # por definición recorre TODOS los tenants (ARCHITECTURE.md §2, §10.11).
        return await self._all(
            "SELECT * FROM reminders WHERE status = 'pending' AND due_at <= :now "
            "ORDER BY due_at ASC",
            {"now": now},
        )

    async def get_reminder(self, *, tenant_id: uuid.UUID, reminder_id: uuid.UUID) -> Row | None:
        return await self._first(
            "SELECT * FROM reminders WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": reminder_id},
        )

    async def mark_reminder_sent(self, *, tenant_id: uuid.UUID, reminder_id: uuid.UUID) -> None:
        # Si el recordatorio tiene `rrule`, no lo cerramos: lo reprogramamos a
        # su siguiente ocurrencia y lo dejamos 'pending'. Si lo cerráramos
        # como a uno de una sola vez, `list_due_reminders` (que solo mira
        # status='pending') nunca lo volvería a ver y la recurrencia dejaría
        # de dispararse después del primer envío.
        reminder = await self._first(
            "SELECT due_at, rrule FROM reminders WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": reminder_id},
        )
        next_due_at = None
        if reminder is not None and reminder.get("rrule"):
            next_due_at = _next_occurrence(reminder["due_at"], reminder["rrule"], after=utcnow())

        if next_due_at is not None:
            await self._exec(
                "UPDATE reminders SET due_at = :due_at, status = 'pending', updated_at = :now "
                "WHERE tenant_id = :tenant_id AND id = :id",
                {"due_at": next_due_at, "now": utcnow(), "tenant_id": tenant_id, "id": reminder_id},
            )
        else:
            await self._exec(
                "UPDATE reminders SET status = 'sent', updated_at = :now "
                "WHERE tenant_id = :tenant_id AND id = :id",
                {"now": utcnow(), "tenant_id": tenant_id, "id": reminder_id},
            )

    # -- conversaciones / mensajes -------------------------------------------------------

    async def get_conversation(
        self, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> Row | None:
        return await self._first(
            "SELECT * FROM conversations WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": conversation_id},
        )

    async def get_conversation_by_title(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, title: str
    ) -> Row | None:
        return await self._first(
            """
            SELECT * FROM conversations
            WHERE tenant_id = :tenant_id AND user_id = :user_id AND title = :title
            ORDER BY created_at ASC
            LIMIT 1
            """,
            {"tenant_id": tenant_id, "user_id": user_id, "title": title},
        )

    async def create_conversation(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, title: str | None, channel: str
    ) -> Row:
        row = await self._first(
            """
            INSERT INTO conversations (
                id, tenant_id, user_id, title, channel, created_at, updated_at
            )
            VALUES (:id, :tenant_id, :user_id, :title, :channel, :now, :now)
            RETURNING *
            """,
            {
                "id": new_uuid(),
                "tenant_id": tenant_id,
                "user_id": user_id,
                # `conversations.title` es `NOT NULL, server_default=""` (§10.3):
                # como la columna SÍ va en la lista del INSERT, un `None` se
                # bindea como NULL explícito y el `server_default` no aplica
                # (solo aplica si la columna se omite del INSERT). Mismo criterio
                # que `edecan_api.repo.SqlRepo.create_conversation`.
                "title": title if title is not None else "",
                "channel": channel,
                "now": utcnow(),
            },
        )
        assert row is not None
        return row

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
    ) -> Row:
        row = await self._first(
            """
            INSERT INTO messages (
                id, conversation_id, tenant_id, role, content, tool_calls,
                tokens_in, tokens_out, created_at, updated_at
            ) VALUES (
                :id, :conversation_id, :tenant_id, :role, :content ::jsonb, :tool_calls ::jsonb,
                :tokens_in, :tokens_out, :now, :now
            )
            RETURNING *
            """,
            {
                "id": new_uuid(),
                "conversation_id": conversation_id,
                "tenant_id": tenant_id,
                "role": role,
                "content": to_jsonb(content),
                "tool_calls": to_jsonb(tool_calls),
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "now": utcnow(),
            },
        )
        assert row is not None
        await self._exec(
            "UPDATE conversations SET updated_at = :now WHERE tenant_id = :tenant_id AND id = :id",
            {"id": conversation_id, "tenant_id": tenant_id, "now": utcnow()},
        )
        return row

    # -- conectores / oauth -----------------------------------------------------------------

    async def list_expiring_oauth_tokens(
        self, *, tenant_id: uuid.UUID | None, before: datetime
    ) -> list[Row]:
        where_tenant = "AND ot.tenant_id = :tenant_id" if tenant_id is not None else ""
        params: dict[str, Any] = {"before": before}
        if tenant_id is not None:
            params["tenant_id"] = tenant_id
        return await self._all(
            f"""
            SELECT ot.tenant_id, ot.connector_account_id, ot.expires_at, ca.connector_key
            FROM oauth_tokens ot
            JOIN connector_accounts ca
              ON ca.id = ot.connector_account_id AND ca.tenant_id = ot.tenant_id
            WHERE ot.expires_at IS NOT NULL AND ot.expires_at < :before {where_tenant}
            """,
            params,
        )

    async def get_connector_account_by_key(
        self, *, tenant_id: uuid.UUID, connector_key: str
    ) -> Row | None:
        """La fila MÁS ANTIGUA con este `connector_key` para el tenant -- usado
        para la config de app OAuth propia (`"{key}__app_config"`, ver
        `apps/api/edecan_api/oauth_app_credentials.py`), que es singleton por
        tenant+key, así que "más antigua" y "única" coinciden en la práctica."""
        return await self._first(
            "SELECT * FROM connector_accounts WHERE tenant_id = :tenant_id "
            "AND connector_key = :connector_key ORDER BY created_at ASC LIMIT 1",
            {"tenant_id": tenant_id, "connector_key": connector_key},
        )

    # -- tenants --------------------------------------------------------------------------

    async def get_tenant(self, *, tenant_id: uuid.UUID) -> Row | None:
        return await self._first("SELECT * FROM tenants WHERE id = :id", {"id": tenant_id})

    # -- memoria ----------------------------------------------------------------------------

    async def list_memory_items_with_embedding(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[Row]:
        rows = await self._all(
            """
            SELECT id, importance, created_at, embedding FROM memory_items
            WHERE tenant_id = :tenant_id AND user_id = :user_id AND embedding IS NOT NULL
            ORDER BY created_at ASC
            """,
            {"tenant_id": tenant_id, "user_id": user_id},
        )
        for row in rows:
            row["embedding"] = parse_vector(row.get("embedding"))
        return rows

    async def update_memory_item_importance(
        self, *, tenant_id: uuid.UUID, memory_id: uuid.UUID, importance: float
    ) -> None:
        await self._exec(
            "UPDATE memory_items SET importance = :importance, updated_at = :now "
            "WHERE tenant_id = :tenant_id AND id = :id",
            {"importance": importance, "now": utcnow(), "tenant_id": tenant_id, "id": memory_id},
        )

    async def delete_memory_items(
        self, *, tenant_id: uuid.UUID, memory_ids: list[uuid.UUID]
    ) -> int:
        if not memory_ids:
            return 0
        placeholders = ", ".join(f":id{i}" for i in range(len(memory_ids)))
        params: dict[str, Any] = {"tenant_id": tenant_id}
        for i, memory_id in enumerate(memory_ids):
            params[f"id{i}"] = memory_id
        return await self._exec(
            f"DELETE FROM memory_items WHERE tenant_id = :tenant_id AND id IN ({placeholders})",
            params,
        )

    # -- memoria: extracción por LLM ---------------------------------------------------

    async def get_persona(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> Row | None:
        # Misma query que `edecan_api.repo.SqlRepo.get_persona`: la fila
        # específica del usuario si existe, si no la fila "default" del tenant
        # (`user_id IS NULL`, ARCHITECTURE.md §10.3).
        return await self._first(
            """
            SELECT * FROM personas
            WHERE tenant_id = :tenant_id AND (user_id = :user_id OR user_id IS NULL)
            ORDER BY user_id NULLS LAST
            LIMIT 1
            """,
            {"tenant_id": tenant_id, "user_id": user_id},
        )

    async def list_recent_messages_for_user(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, limit: int
    ) -> list[Row]:
        # El payload de `memory_consolidate` solo trae `user_id` (no
        # `conversation_id`, ARCHITECTURE.md §10.11 no pinnea más claves), así
        # que se toman los `limit` mensajes más recientes de CUALQUIER
        # conversación del usuario -en la práctica, casi siempre la misma que
        # acaba de cerrar turno, porque este job se encola justo después
        # (`edecan_api.routers.conversations._stream_agent_events`). Mismo
        # patrón de subconsulta que `edecan_api.repo.SqlRepo.list_messages`:
        # ordenar desc para el LIMIT y luego asc para devolver cronológico.
        return await self._all(
            """
            SELECT * FROM (
                SELECT m.role, m.content, m.created_at
                FROM messages m
                JOIN conversations c ON c.id = m.conversation_id AND c.tenant_id = m.tenant_id
                WHERE m.tenant_id = :tenant_id AND c.user_id = :user_id
                ORDER BY m.created_at DESC
                LIMIT :limit
            ) recientes
            ORDER BY created_at ASC
            """,
            {"tenant_id": tenant_id, "user_id": user_id, "limit": limit},
        )

    async def list_memory_contents(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, limit: int
    ) -> list[Row]:
        """`kind`+`content` de las memorias existentes del usuario, para dárselas
        de contexto al LLM extractor y que no duplique lo que ya sabe."""
        return await self._all(
            """
            SELECT kind, content FROM memory_items
            WHERE tenant_id = :tenant_id AND user_id = :user_id
            ORDER BY importance DESC, created_at DESC
            LIMIT :limit
            """,
            {"tenant_id": tenant_id, "user_id": user_id, "limit": limit},
        )

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
    ) -> Row:
        """Inserta un `memory_item` nuevo (mismas columnas que
        `edecan_api.repo.SqlRepo.add_memory`), con un `embedding` YA calculado
        por el llamador -a diferencia de `add_memory`, que lo calcula uno por
        uno, `memory_consolidate` embebe todos los ítems extraídos en un solo
        batch (`Embedder.embed`), así que aquí solo se inserta."""
        row = await self._first(
            """
            INSERT INTO memory_items (
                id, tenant_id, user_id, kind, content, embedding, importance, source,
                created_at, updated_at
            ) VALUES (
                :id, :tenant_id, :user_id, :kind, :content, :embedding ::vector, :importance,
                :source, :now, :now
            )
            RETURNING *
            """,
            {
                "id": new_uuid(),
                "tenant_id": tenant_id,
                "user_id": user_id,
                "kind": kind,
                "content": content,
                "embedding": vector_literal(embedding) if embedding is not None else None,
                "importance": importance,
                "source": source,
                "now": utcnow(),
            },
        )
        assert row is not None
        return row

    # -- memoria: grafo -----------------------------------------------------------------

    async def add_edge(
        self, *, tenant_id: uuid.UUID, src_id: uuid.UUID, dst_id: uuid.UUID, relation: str
    ) -> uuid.UUID:
        """Crea una arista en `memory_edges` delegando en
        `edecan_core.memory.graph.add_edge` (ARCHITECTURE.md §10.3/§10.7) sobre
        esta misma `AsyncSession`, en vez de duplicar aquí el INSERT: esa es la
        función que ARCHITECTURE.md pinnea para el grafo de memoria. Import
        perezoso -mismo criterio que `edecan_worker.deps`/`edecan_worker.scheduler`
        con `edecan_core`, un paquete hermano (ARCHITECTURE.md §10.1)."""
        from edecan_core.memory.graph import add_edge as _add_edge

        return await _add_edge(
            self._s, tenant_id=tenant_id, src_id=src_id, dst_id=dst_id, relation=relation
        )
