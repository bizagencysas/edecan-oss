"""Acceso a datos de `edecan_api`.

`ARCHITECTURE.md` §10.3 pinnea los nombres EXACTOS de tablas y columnas, pero
no pinnea nombres de clases ORM de `edecan_db` (ese paquete solo pinnea
`edecan_db.session.get_session` y `edecan_db.vault.TokenVault`, §10.3/§10.4).
Para no acoplar esta app a una API de modelos no especificada en el contrato,
`SqlRepo` habla SQL parametrizado directo contra el esquema pinneado, sobre la
`AsyncSession` que entrega `edecan_db.session.get_session(tenant_id)`.

`Repo` es el `Protocol` que consumen los routers — la dependencia `get_repo`
(`deps.py`) es el punto de inyección. `tests/` la sobreescribe con `FakeRepo`
(en memoria, sin Postgres), tal como exige `ARCHITECTURE.md` §10.1: "Los
tests NO importan paquetes hermanos: usa stubs/fakes".
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from edecan_core.memory import HashEmbedder, OpenAICompatEmbedder
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.config import Settings, get_settings

Row = dict[str, Any]


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(UTC)


def _j(value: Any) -> str:
    """Serializa `value` a JSON para columnas `jsonb` (o `"null"` si es `None`)."""
    return json.dumps(value if value is not None else None)


# Placeholders públicos de `.env.example` para el proveedor de embeddings
# OpenAI-compatible (no son secretos: compararlos aquí no filtra nada). Mismo
# criterio que `edecan_api.routers.conversations._has_real_embeddings_provider`
# y `edecan_worker.deps._has_real_embeddings_provider` (mantener las tres en
# sync si cambia): un `.env` recién copiado de `.env.example` sin tocar estas
# dos variables trae EXACTAMENTE estos valores — strings no vacíos, por tanto
# truthy — y además `OPENAI_COMPAT_BASE_URL` YA trae un valor real de fábrica
# (`https://api.openai.com/v1`), así que revisar solo truthiness de las tres
# variables no basta para detectar que el proveedor sigue sin configurar. Sin
# este chequeo extra, el setup mínimo de `docs/self-hosting.md` §2.1 dispara
# una llamada HTTP real a `https://api.openai.com/v1/embeddings` con una API
# key falsa en cada `POST /v1/memory` (`SqlRepo.add_memory`), en vez de caer
# al `HashEmbedder` offline que promete `docs/self-hosting.md` §4.
_OPENAI_COMPAT_API_KEY_PLACEHOLDER = "TU_OPENAI_COMPAT_API_KEY_AQUI"
_EMBEDDINGS_MODEL_PLACEHOLDER = "TU_EMBEDDINGS_MODEL_AQUI"


def _has_real_embeddings_provider(settings: Settings) -> bool:
    """`True` solo si hay un proveedor de embeddings OpenAI-compatible
    configurado de verdad: `OPENAI_COMPAT_BASE_URL`/`OPENAI_COMPAT_API_KEY`/
    `EMBEDDINGS_MODEL` no vacíos y, además, `OPENAI_COMPAT_API_KEY`/
    `EMBEDDINGS_MODEL` distintos de los placeholders públicos de
    `.env.example` (ver comentario arriba)."""
    return bool(
        settings.OPENAI_COMPAT_BASE_URL
        and settings.OPENAI_COMPAT_API_KEY
        and settings.OPENAI_COMPAT_API_KEY != _OPENAI_COMPAT_API_KEY_PLACEHOLDER
        and settings.EMBEDDINGS_MODEL
        and settings.EMBEDDINGS_MODEL != _EMBEDDINGS_MODEL_PLACEHOLDER
    )


def _build_memory_embedder(settings: Settings) -> Any:
    """Mismo criterio que `edecan_api.routers.conversations._build_embedder`
    (mantener ambos en sync si cambia): `OpenAICompatEmbedder` si hay proveedor
    de embeddings configurado de verdad (`_has_real_embeddings_provider`), si
    no `HashEmbedder` (determinista, offline). Nunca `None` — así
    `SqlRepo.add_memory` siempre guarda un `embedding` real, en el mismo
    espacio vectorial que usa `edecan_core.memory.pg.PgMemoryStore.search`
    para la búsqueda semántica.
    """
    if _has_real_embeddings_provider(settings):
        return OpenAICompatEmbedder(
            base_url=settings.OPENAI_COMPAT_BASE_URL,
            api_key=settings.OPENAI_COMPAT_API_KEY,
            model=settings.EMBEDDINGS_MODEL,
        )
    return HashEmbedder(dim=settings.EMBEDDINGS_DIM)


def _vector_literal(values: list[float]) -> str:
    """Literal de texto de pgvector `"[v1,v2,...]"` — misma técnica que
    `edecan_core.memory.pg._vector_literal` (`asyncpg` no trae codec para
    `vector`, así que se castea en SQL con `::vector`)."""
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


class Repo(Protocol):
    """Contrato de acceso a datos que consumen los routers de `edecan_api`.

    Implementaciones: `SqlRepo` (producción, Postgres vía `edecan_db`) y
    `FakeRepo` (tests, diccionarios en memoria — ver `tests/fakes.py`).
    """

    # -- tenants / users / memberships ------------------------------------
    async def create_tenant(self, *, name: str, slug: str, plan_key: str) -> Row: ...
    async def get_tenant(self, tenant_id: uuid.UUID) -> Row | None: ...
    async def get_tenant_by_slug(self, slug: str) -> Row | None: ...
    async def update_tenant_plan(self, tenant_id: uuid.UUID, plan_key: str) -> None: ...
    async def update_tenant_onboarding_completed(self, tenant_id: uuid.UUID) -> None: ...
    async def update_tenant_lifetime_updates(self, tenant_id: uuid.UUID) -> None: ...
    async def list_tenants(self, *, limit: int = 200) -> list[Row]: ...

    async def create_user(self, *, email: str, password_hash: str) -> Row: ...
    async def get_user(self, user_id: uuid.UUID) -> Row | None: ...
    async def get_user_by_email(self, email: str) -> Row | None: ...
    async def set_user_totp_secret(self, user_id: uuid.UUID, secret: str | None) -> None: ...
    async def update_user_password_hash(self, user_id: uuid.UUID, password_hash: str) -> None: ...

    async def create_membership(
        self, *, user_id: uuid.UUID, tenant_id: uuid.UUID, role: str
    ) -> Row: ...
    async def get_membership(self, *, user_id: uuid.UUID, tenant_id: uuid.UUID) -> Row | None: ...
    async def get_first_membership_for_user(self, user_id: uuid.UUID) -> Row | None: ...
    async def get_first_user_id_for_tenant(self, tenant_id: uuid.UUID) -> uuid.UUID | None: ...
    async def get_local_owner(self) -> Row | None: ...
    async def set_local_owner(self, *, user_id: uuid.UUID, tenant_id: uuid.UUID) -> Row: ...
    async def get_first_active_owner(self) -> Row | None: ...

    # -- personas -----------------------------------------------------------
    async def create_persona_default(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> Row: ...
    async def get_persona(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> Row | None: ...
    async def upsert_persona(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, fields: dict[str, Any]
    ) -> Row: ...

    # -- conversaciones / mensajes -------------------------------------------
    async def create_conversation(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, title: str | None, channel: str
    ) -> Row: ...
    async def list_conversations(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[Row]: ...
    async def get_conversation(
        self, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> Row | None: ...
    async def delete_conversation(
        self, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> bool: ...
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
    async def list_messages(
        self, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID, limit: int = 50
    ) -> list[Row]: ...

    # -- llamadas como canal conversacional -----------------------------------
    async def create_phone_call(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        direction: str,
        from_e164: str,
        to_e164: str,
        goal: str,
        status: str = "draft",
        provider_call_sid: str | None = None,
    ) -> Row: ...
    async def list_phone_calls(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, limit: int = 50
    ) -> list[Row]: ...
    async def get_phone_call(self, *, tenant_id: uuid.UUID, call_id: uuid.UUID) -> Row | None: ...
    async def get_phone_call_by_provider_sid(self, *, provider_call_sid: str) -> Row | None: ...
    async def get_phone_call_global(self, *, call_id: uuid.UUID) -> Row | None: ...
    async def update_phone_call(
        self, *, tenant_id: uuid.UUID, call_id: uuid.UUID, fields: dict[str, Any]
    ) -> Row | None: ...
    async def add_phone_call_event(
        self,
        *,
        tenant_id: uuid.UUID,
        call_id: uuid.UUID,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> Row: ...
    async def list_phone_call_events(
        self, *, tenant_id: uuid.UUID, call_id: uuid.UUID
    ) -> list[Row]: ...

    # -- consentimiento de destinatarios --------------------------------------
    async def has_phone_consent(
        self, *, tenant_id: uuid.UUID, phone_e164: str, kind: str
    ) -> bool: ...
    async def grant_phone_consent(
        self, *, tenant_id: uuid.UUID, phone_e164: str, kind: str, source: str
    ) -> Row: ...

    # -- uso / cuotas ---------------------------------------------------------
    async def add_usage_event(
        self,
        *,
        tenant_id: uuid.UUID,
        kind: str,
        quantity: float,
        meta: dict[str, Any] | None = None,
    ) -> None: ...
    async def sum_usage_since(
        self, *, tenant_id: uuid.UUID, kind: str, since: datetime
    ) -> float: ...
    async def sum_usage_by_kind_since(
        self, *, tenant_id: uuid.UUID, since: datetime
    ) -> dict[str, float]: ...
    async def sum_usage_all_tenants_since(self, *, since: datetime) -> list[Row]: ...

    # -- memoria --------------------------------------------------------------
    async def list_memory(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, q: str | None, k: int
    ) -> list[Row]: ...
    async def add_memory(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        kind: str,
        content: str,
        importance: float,
        source: str,
    ) -> Row: ...
    async def delete_memory(self, *, tenant_id: uuid.UUID, memory_id: uuid.UUID) -> bool: ...

    # -- conectores -------------------------------------------------------------
    async def list_connector_accounts(self, *, tenant_id: uuid.UUID) -> list[Row]: ...
    async def get_connector_account(
        self, *, tenant_id: uuid.UUID, account_id: uuid.UUID
    ) -> Row | None: ...
    async def create_connector_account(
        self,
        *,
        tenant_id: uuid.UUID,
        connector_key: str,
        external_account_id: str,
        display_name: str,
        scopes: list[str],
    ) -> Row: ...
    async def get_connector_account_by_external_id(
        self, *, connector_key: str, external_account_id: str
    ) -> Row | None: ...
    async def delete_connector_account(
        self, *, tenant_id: uuid.UUID, account_id: uuid.UUID
    ) -> bool: ...

    # -- archivos -----------------------------------------------------------------
    async def create_file(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        s3_key: str,
        filename: str,
        mime: str,
        size_bytes: int,
        status: str,
        file_id: uuid.UUID | None = None,
    ) -> Row: ...
    async def get_file(self, *, tenant_id: uuid.UUID, file_id: uuid.UUID) -> Row | None: ...
    async def list_files(self, *, tenant_id: uuid.UUID) -> list[Row]: ...

    # -- recordatorios --------------------------------------------------------------
    async def create_reminder(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, fields: dict[str, Any]
    ) -> Row: ...
    async def list_reminders(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> list[Row]: ...
    async def get_reminder(self, *, tenant_id: uuid.UUID, reminder_id: uuid.UUID) -> Row | None: ...
    async def update_reminder(
        self, *, tenant_id: uuid.UUID, reminder_id: uuid.UUID, fields: dict[str, Any]
    ) -> Row | None: ...
    async def delete_reminder(self, *, tenant_id: uuid.UUID, reminder_id: uuid.UUID) -> bool: ...

    # -- contactos --------------------------------------------------------------------
    async def create_contact(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, fields: dict[str, Any]
    ) -> Row: ...
    async def list_contacts(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, q: str | None
    ) -> list[Row]: ...
    async def get_contact(self, *, tenant_id: uuid.UUID, contact_id: uuid.UUID) -> Row | None: ...
    async def update_contact(
        self, *, tenant_id: uuid.UUID, contact_id: uuid.UUID, fields: dict[str, Any]
    ) -> Row | None: ...
    async def delete_contact(self, *, tenant_id: uuid.UUID, contact_id: uuid.UUID) -> bool: ...

    # -- finanzas -----------------------------------------------------------------------
    async def create_transaction(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, fields: dict[str, Any]
    ) -> Row: ...
    async def list_transactions(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, mes: str | None
    ) -> list[Row]: ...
    async def get_transaction(
        self, *, tenant_id: uuid.UUID, transaction_id: uuid.UUID
    ) -> Row | None: ...
    async def update_transaction(
        self, *, tenant_id: uuid.UUID, transaction_id: uuid.UUID, fields: dict[str, Any]
    ) -> Row | None: ...
    async def delete_transaction(
        self, *, tenant_id: uuid.UUID, transaction_id: uuid.UUID
    ) -> bool: ...
    async def finance_summary(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, mes: str
    ) -> Row: ...

    # -- billing ------------------------------------------------------------------------
    async def upsert_subscription(self, *, tenant_id: uuid.UUID, fields: dict[str, Any]) -> Row: ...
    async def get_subscription_by_stripe_customer(self, stripe_customer_id: str) -> Row | None: ...
    async def get_subscription_by_stripe_subscription(
        self, stripe_subscription_id: str
    ) -> Row | None: ...

    # -- auditoría ------------------------------------------------------------------------
    async def add_audit_log(
        self,
        *,
        tenant_id: uuid.UUID | None,
        actor_user_id: uuid.UUID | None,
        action: str,
        target: str,
        meta: dict[str, Any] | None = None,
    ) -> None: ...

    # -- vista remota (control remoto, WP-V2-09) -------------------------------------------
    async def create_remote_session(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> Row: ...
    async def list_remote_sessions(self, *, tenant_id: uuid.UUID) -> list[Row]: ...
    async def get_remote_session(
        self, *, tenant_id: uuid.UUID, session_id: uuid.UUID
    ) -> Row | None: ...
    async def record_remote_session_frame(
        self, *, tenant_id: uuid.UUID, session_id: uuid.UUID
    ) -> Row: ...
    async def mark_remote_session_denied(
        self, *, tenant_id: uuid.UUID, session_id: uuid.UUID
    ) -> Row: ...
    async def mark_remote_session_ended(
        self, *, tenant_id: uuid.UUID, session_id: uuid.UUID
    ) -> Row: ...
    # WP-V4-10 (control remoto fase 2, apps/api/edecan_api/routers/remote.py):
    # promueve una sesión recién creada (siempre nace kind='view', ver
    # `create_remote_session`) a kind='control' -- NUNCA se agregó un parámetro
    # `kind` a `create_remote_session` para no romper su única firma que ya
    # consumen `SqlRepo`/`FakeRepo`/todos los tests de v2; este método nuevo,
    # mínimo y aditivo, es la vía elegida en su lugar. Sin migración nueva:
    # `remote_sessions.kind` ya es vocabulario abierto, sin CHECK constraint
    # (ver `edecan_db.models.RemoteSession`, migración `0003_v2_expansion`).
    async def mark_remote_session_kind(
        self, *, tenant_id: uuid.UUID, session_id: uuid.UUID, kind: str
    ) -> Row: ...


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

    # -- tenants / users / memberships --------------------------------------

    async def create_tenant(self, *, name: str, slug: str, plan_key: str) -> Row:
        row = await self._first(
            """
            INSERT INTO tenants (id, name, slug, plan_key, status, created_at, updated_at)
            VALUES (:id, :name, :slug, :plan_key, 'active', :now, :now)
            RETURNING *
            """,
            {"id": _uuid(), "name": name, "slug": slug, "plan_key": plan_key, "now": _now()},
        )
        assert row is not None
        return row

    async def get_tenant(self, tenant_id: uuid.UUID) -> Row | None:
        return await self._first("SELECT * FROM tenants WHERE id = :id", {"id": tenant_id})

    async def get_tenant_by_slug(self, slug: str) -> Row | None:
        return await self._first("SELECT * FROM tenants WHERE slug = :slug", {"slug": slug})

    async def update_tenant_plan(self, tenant_id: uuid.UUID, plan_key: str) -> None:
        await self._exec(
            "UPDATE tenants SET plan_key = :plan_key, updated_at = :now WHERE id = :id",
            {"id": tenant_id, "plan_key": plan_key, "now": _now()},
        )

    async def update_tenant_onboarding_completed(self, tenant_id: uuid.UUID) -> None:
        await self._exec(
            "UPDATE tenants SET onboarding_completed_at = :now, updated_at = :now WHERE id = :id",
            {"id": tenant_id, "now": _now()},
        )

    async def update_tenant_lifetime_updates(self, tenant_id: uuid.UUID) -> None:
        await self._exec(
            "UPDATE tenants SET lifetime_updates_purchased_at = :now, updated_at = :now "
            "WHERE id = :id",
            {"id": tenant_id, "now": _now()},
        )

    async def list_tenants(self, *, limit: int = 200) -> list[Row]:
        return await self._all(
            "SELECT * FROM tenants ORDER BY created_at DESC LIMIT :limit", {"limit": limit}
        )

    async def create_user(self, *, email: str, password_hash: str) -> Row:
        row = await self._first(
            """
            INSERT INTO users (
                id, email, password_hash, totp_secret, is_superadmin, created_at, updated_at
            )
            VALUES (:id, :email, :password_hash, NULL, false, :now, :now)
            RETURNING *
            """,
            {
                "id": _uuid(),
                "email": email.strip().lower(),
                "password_hash": password_hash,
                "now": _now(),
            },
        )
        assert row is not None
        return row

    async def get_user(self, user_id: uuid.UUID) -> Row | None:
        return await self._first("SELECT * FROM users WHERE id = :id", {"id": user_id})

    async def get_user_by_email(self, email: str) -> Row | None:
        return await self._first(
            "SELECT * FROM users WHERE email = :email", {"email": email.strip().lower()}
        )

    async def set_user_totp_secret(self, user_id: uuid.UUID, secret: str | None) -> None:
        await self._exec(
            "UPDATE users SET totp_secret = :secret, updated_at = :now WHERE id = :id",
            {"id": user_id, "secret": secret, "now": _now()},
        )

    async def update_user_password_hash(self, user_id: uuid.UUID, password_hash: str) -> None:
        await self._exec(
            "UPDATE users SET password_hash = :password_hash, updated_at = :now WHERE id = :id",
            {"id": user_id, "password_hash": password_hash, "now": _now()},
        )

    async def create_membership(
        self, *, user_id: uuid.UUID, tenant_id: uuid.UUID, role: str
    ) -> Row:
        row = await self._first(
            """
            INSERT INTO memberships (id, user_id, tenant_id, role, created_at, updated_at)
            VALUES (:id, :user_id, :tenant_id, :role, :now, :now)
            RETURNING *
            """,
            {
                "id": _uuid(),
                "user_id": user_id,
                "tenant_id": tenant_id,
                "role": role,
                "now": _now(),
            },
        )
        assert row is not None
        return row

    async def get_membership(self, *, user_id: uuid.UUID, tenant_id: uuid.UUID) -> Row | None:
        return await self._first(
            "SELECT * FROM memberships WHERE user_id = :user_id AND tenant_id = :tenant_id",
            {"user_id": user_id, "tenant_id": tenant_id},
        )

    async def get_first_membership_for_user(self, user_id: uuid.UUID) -> Row | None:
        return await self._first(
            "SELECT * FROM memberships WHERE user_id = :user_id ORDER BY created_at ASC LIMIT 1",
            {"user_id": user_id},
        )

    async def get_first_user_id_for_tenant(self, tenant_id: uuid.UUID) -> uuid.UUID | None:
        row = await self._first(
            """
            SELECT user_id FROM memberships
            WHERE tenant_id = :tenant_id
            ORDER BY created_at ASC LIMIT 1
            """,
            {"tenant_id": tenant_id},
        )
        return row["user_id"] if row is not None else None

    async def get_first_active_owner(self) -> Row | None:
        """Candidato legacy para una base anterior al marcador singleton.

        Hosted nunca usa este método para autenticar. `0014` fija el candidato
        más antiguo en `local_installation`; este fallback solo permite reparar
        automáticamente una base sin marcador cuando hay exactamente uno.
        """
        return await self._first(
            """
            SELECT u.id AS user_id, u.email, m.tenant_id, t.plan_key,
                   COUNT(*) OVER () AS owner_count
            FROM memberships m
            JOIN users u ON u.id = m.user_id
            JOIN tenants t ON t.id = m.tenant_id
            WHERE m.role = 'owner' AND t.status = 'active'
            ORDER BY m.created_at ASC, m.id ASC
            LIMIT 1
            """,
            {},
        )

    async def get_local_owner(self) -> Row | None:
        """Dueño fijado de esta instalación autohospedada."""
        return await self._first(
            """
            SELECT u.id AS user_id, u.email, t.id AS tenant_id, t.plan_key
            FROM local_installation li
            JOIN users u ON u.id = li.owner_user_id
            JOIN tenants t ON t.id = li.owner_tenant_id
            JOIN memberships m
              ON m.user_id = li.owner_user_id
             AND m.tenant_id = li.owner_tenant_id
             AND m.role = 'owner'
            WHERE li.installation_key = 'local' AND t.status = 'active'
            """,
            {},
        )

    async def set_local_owner(self, *, user_id: uuid.UUID, tenant_id: uuid.UUID) -> Row:
        """Fija el singleton una vez; nunca cambia un dueño ya seleccionado."""
        await self._exec(
            """
            INSERT INTO local_installation (
                installation_key, owner_user_id, owner_tenant_id, created_at, updated_at
            )
            VALUES ('local', :user_id, :tenant_id, :now, :now)
            ON CONFLICT (installation_key) DO NOTHING
            """,
            {"user_id": user_id, "tenant_id": tenant_id, "now": _now()},
        )
        owner = await self.get_local_owner()
        assert owner is not None
        return owner

    # -- personas -------------------------------------------------------------

    async def create_persona_default(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> Row:
        row = await self._first(
            """
            INSERT INTO personas (
                id, tenant_id, user_id, nombre_asistente, idioma, tono, formalidad,
                emojis, instrucciones, rasgos, memoria_activada, voice_id,
                estilo_relacion, adulto_confirmado, consentimiento_romantico,
                created_at, updated_at
            ) VALUES (
                :id, :tenant_id, :user_id, 'Edecán', 'es', 'cálido y profesional', 1,
                false, '', :rasgos, true, NULL, 'profesional', false, false,
                :now, :now
            )
            RETURNING *
            """,
            {
                "id": _uuid(),
                "tenant_id": tenant_id,
                "user_id": user_id,
                "rasgos": _j([]),
                "now": _now(),
            },
        )
        assert row is not None
        return row

    async def get_persona(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> Row | None:
        row = await self._first(
            """
            SELECT * FROM personas
            WHERE tenant_id = :tenant_id AND (user_id = :user_id OR user_id IS NULL)
            ORDER BY user_id NULLS LAST
            LIMIT 1
            """,
            {"tenant_id": tenant_id, "user_id": user_id},
        )
        return row

    async def upsert_persona(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, fields: dict[str, Any]
    ) -> Row:
        existing = await self.get_persona(tenant_id=tenant_id, user_id=user_id)
        allowed = {
            "nombre_asistente",
            "idioma",
            "tono",
            "formalidad",
            "emojis",
            "instrucciones",
            "rasgos",
            "memoria_activada",
            "voice_id",
            "estilo_relacion",
            "adulto_confirmado",
            "consentimiento_romantico",
        }
        clean = {k: v for k, v in fields.items() if k in allowed}
        if existing is None:
            base = await self.create_persona_default(tenant_id=tenant_id, user_id=user_id)
            existing_id = base["id"]
        else:
            existing_id = existing["id"]
        if not clean:
            refreshed = await self._first(
                "SELECT * FROM personas WHERE id = :id", {"id": existing_id}
            )
            assert refreshed is not None
            return refreshed

        set_parts = []
        params: dict[str, Any] = {"id": existing_id, "tenant_id": tenant_id, "now": _now()}
        for key, value in clean.items():
            if key == "rasgos":
                # El espacio antes de `::jsonb` es obligatorio: el regex de
                # bind params de SQLAlchemy (`sqlalchemy.sql.compiler.BIND_PARAMS`,
                # `(?<![:\w$\\]):([\w$]+)(?![:\w$])`) NO reconoce `:rasgos`
                # como parámetro si lo sigue otro `:` pegado — sin el espacio,
                # `:rasgos::jsonb` queda como texto literal en el SQL
                # compilado y Postgres revienta con "syntax error at or near
                # ':'" en cada UPDATE (nunca se vio porque los tests de
                # `apps/api` corren contra `FakeRepo`, nunca contra Postgres
                # real — ver `test_repo_sql_integration.py`).
                set_parts.append("rasgos = :rasgos ::jsonb")
                params["rasgos"] = _j(value)
            else:
                set_parts.append(f"{key} = :{key}")
                params[key] = value
        set_clause = ", ".join(set_parts)
        row = await self._first(
            f"UPDATE personas SET {set_clause}, updated_at = :now "
            "WHERE id = :id AND tenant_id = :tenant_id RETURNING *",
            params,
        )
        assert row is not None
        return row

    # -- conversaciones / mensajes ---------------------------------------------

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
                "id": _uuid(),
                "tenant_id": tenant_id,
                "user_id": user_id,
                # `conversations.title` es `NOT NULL, server_default=""` (§10.3):
                # como la columna SÍ va en la lista del INSERT, un `None` se
                # bindea como NULL explícito y el `server_default` no aplica
                # (solo aplica si la columna se omite del INSERT). Sin título
                # (el flujo normal de "nueva conversación") hay que mandar el
                # mismo "" que usaría el default, o Postgres rechaza el INSERT.
                "title": title if title is not None else "",
                "channel": channel,
                "now": _now(),
            },
        )
        assert row is not None
        return row

    async def list_conversations(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> list[Row]:
        return await self._all(
            """
            SELECT * FROM conversations
            WHERE tenant_id = :tenant_id AND user_id = :user_id
            ORDER BY created_at DESC
            """,
            {"tenant_id": tenant_id, "user_id": user_id},
        )

    async def get_conversation(
        self, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> Row | None:
        return await self._first(
            "SELECT * FROM conversations WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": conversation_id},
        )

    async def delete_conversation(
        self, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> bool:
        deleted = await self._exec(
            "DELETE FROM conversations WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": conversation_id},
        )
        return deleted > 0

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
                "id": _uuid(),
                "conversation_id": conversation_id,
                "tenant_id": tenant_id,
                "role": role,
                "content": _j(content),
                "tool_calls": _j(tool_calls),
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "now": _now(),
            },
        )
        assert row is not None
        await self._exec(
            "UPDATE conversations SET updated_at = :now WHERE id = :id AND tenant_id = :tenant_id",
            {"id": conversation_id, "tenant_id": tenant_id, "now": _now()},
        )
        return row

    async def list_messages(
        self, *, tenant_id: uuid.UUID, conversation_id: uuid.UUID, limit: int = 50
    ) -> list[Row]:
        rows = await self._all(
            """
            SELECT * FROM (
                SELECT * FROM messages
                WHERE tenant_id = :tenant_id AND conversation_id = :conversation_id
                ORDER BY created_at DESC
                LIMIT :limit
            ) recientes
            ORDER BY created_at ASC
            """,
            {"tenant_id": tenant_id, "conversation_id": conversation_id, "limit": limit},
        )
        return rows

    # -- llamadas como canal conversacional -----------------------------------

    async def create_phone_call(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        direction: str,
        from_e164: str,
        to_e164: str,
        goal: str,
        status: str = "draft",
        provider_call_sid: str | None = None,
    ) -> Row:
        row = await self._first(
            """
            INSERT INTO phone_calls (
                id, tenant_id, user_id, conversation_id, direction, from_e164, to_e164,
                goal, status, provider, provider_call_sid, created_at, updated_at
            ) VALUES (
                :id, :tenant_id, :user_id, :conversation_id, :direction, :from_e164,
                :to_e164, :goal, :status, 'twilio', :provider_call_sid, :now, :now
            ) RETURNING *
            """,
            {
                "id": _uuid(),
                "tenant_id": tenant_id,
                "user_id": user_id,
                "conversation_id": conversation_id,
                "direction": direction,
                "from_e164": from_e164,
                "to_e164": to_e164,
                "goal": goal,
                "status": status,
                "provider_call_sid": provider_call_sid,
                "now": _now(),
            },
        )
        assert row is not None
        return row

    async def list_phone_calls(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, limit: int = 50
    ) -> list[Row]:
        return await self._all(
            """
            SELECT * FROM phone_calls
            WHERE tenant_id = :tenant_id AND user_id = :user_id
            ORDER BY created_at DESC LIMIT :limit
            """,
            {"tenant_id": tenant_id, "user_id": user_id, "limit": limit},
        )

    async def get_phone_call(self, *, tenant_id: uuid.UUID, call_id: uuid.UUID) -> Row | None:
        return await self._first(
            "SELECT * FROM phone_calls WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": call_id},
        )

    async def get_phone_call_by_provider_sid(self, *, provider_call_sid: str) -> Row | None:
        return await self._first(
            "SELECT * FROM phone_calls WHERE provider_call_sid = :provider_call_sid",
            {"provider_call_sid": provider_call_sid},
        )

    async def get_phone_call_global(self, *, call_id: uuid.UUID) -> Row | None:
        """Lookup sin tenant solo para webhooks firmados, usando repo plataforma."""
        return await self._first(
            "SELECT * FROM phone_calls WHERE id = :id", {"id": call_id}
        )

    async def update_phone_call(
        self, *, tenant_id: uuid.UUID, call_id: uuid.UUID, fields: dict[str, Any]
    ) -> Row | None:
        allowed = {
            "status",
            "provider_call_sid",
            "confirmed_at",
            "started_at",
            "ended_at",
            "duration_seconds",
            "error",
        }
        clean = {key: value for key, value in fields.items() if key in allowed}
        if not clean:
            return await self.get_phone_call(tenant_id=tenant_id, call_id=call_id)
        set_clause = ", ".join(f"{key} = :{key}" for key in clean)
        return await self._first(
            f"UPDATE phone_calls SET {set_clause}, updated_at = :now "
            "WHERE tenant_id = :tenant_id AND id = :id RETURNING *",
            {**clean, "tenant_id": tenant_id, "id": call_id, "now": _now()},
        )

    async def add_phone_call_event(
        self,
        *,
        tenant_id: uuid.UUID,
        call_id: uuid.UUID,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> Row:
        row = await self._first(
            """
            INSERT INTO phone_call_events (
                id, tenant_id, call_id, event_type, payload, occurred_at, created_at, updated_at
            ) VALUES (
                :id, :tenant_id, :call_id, :event_type, :payload ::jsonb, :now, :now, :now
            ) RETURNING *
            """,
            {
                "id": _uuid(),
                "tenant_id": tenant_id,
                "call_id": call_id,
                "event_type": event_type,
                "payload": _j(payload or {}),
                "now": _now(),
            },
        )
        assert row is not None
        return row

    async def list_phone_call_events(
        self, *, tenant_id: uuid.UUID, call_id: uuid.UUID
    ) -> list[Row]:
        return await self._all(
            """
            SELECT * FROM phone_call_events
            WHERE tenant_id = :tenant_id AND call_id = :call_id
            ORDER BY occurred_at ASC, created_at ASC
            """,
            {"tenant_id": tenant_id, "call_id": call_id},
        )

    async def has_phone_consent(self, *, tenant_id: uuid.UUID, phone_e164: str, kind: str) -> bool:
        row = await self._first(
            """
            SELECT id FROM consents
            WHERE tenant_id = :tenant_id AND phone_e164 = :phone_e164
              AND kind = :kind AND revoked_at IS NULL
            ORDER BY granted_at DESC LIMIT 1
            """,
            {"tenant_id": tenant_id, "phone_e164": phone_e164, "kind": kind},
        )
        return row is not None

    async def grant_phone_consent(
        self, *, tenant_id: uuid.UUID, phone_e164: str, kind: str, source: str
    ) -> Row:
        row = await self._first(
            """
            INSERT INTO consents (
                id, tenant_id, phone_e164, kind, granted_at, revoked_at, source,
                created_at, updated_at
            ) VALUES (
                :id, :tenant_id, :phone_e164, :kind, :now, NULL, :source, :now, :now
            ) RETURNING *
            """,
            {
                "id": _uuid(),
                "tenant_id": tenant_id,
                "phone_e164": phone_e164,
                "kind": kind,
                "source": source,
                "now": _now(),
            },
        )
        assert row is not None
        return row

    # -- uso / cuotas -----------------------------------------------------------

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
                "id": _uuid(),
                "tenant_id": tenant_id,
                "kind": kind,
                "quantity": quantity,
                "meta": _j(meta or {}),
                "now": _now(),
            },
        )

    async def sum_usage_since(self, *, tenant_id: uuid.UUID, kind: str, since: datetime) -> float:
        row = await self._first(
            """
            SELECT COALESCE(SUM(quantity), 0) AS total FROM usage_events
            WHERE tenant_id = :tenant_id AND kind = :kind AND created_at >= :since
            """,
            {"tenant_id": tenant_id, "kind": kind, "since": since},
        )
        return float(row["total"]) if row else 0.0

    async def sum_usage_by_kind_since(
        self, *, tenant_id: uuid.UUID, since: datetime
    ) -> dict[str, float]:
        rows = await self._all(
            """
            SELECT kind, COALESCE(SUM(quantity), 0) AS total FROM usage_events
            WHERE tenant_id = :tenant_id AND created_at >= :since
            GROUP BY kind
            """,
            {"tenant_id": tenant_id, "since": since},
        )
        return {row["kind"]: float(row["total"]) for row in rows}

    async def sum_usage_all_tenants_since(self, *, since: datetime) -> list[Row]:
        return await self._all(
            """
            SELECT tenant_id, kind, COALESCE(SUM(quantity), 0) AS total FROM usage_events
            WHERE created_at >= :since
            GROUP BY tenant_id, kind
            ORDER BY tenant_id
            """,
            {"since": since},
        )

    # -- memoria ------------------------------------------------------------------

    async def list_memory(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, q: str | None, k: int
    ) -> list[Row]:
        if q:
            return await self._all(
                """
                SELECT * FROM memory_items
                WHERE tenant_id = :tenant_id AND user_id = :user_id AND content ILIKE :q
                ORDER BY importance DESC, created_at DESC
                LIMIT :k
                """,
                {"tenant_id": tenant_id, "user_id": user_id, "q": f"%{q}%", "k": k},
            )
        return await self._all(
            """
            SELECT * FROM memory_items
            WHERE tenant_id = :tenant_id AND user_id = :user_id
            ORDER BY importance DESC, created_at DESC
            LIMIT :k
            """,
            {"tenant_id": tenant_id, "user_id": user_id, "k": k},
        )

    async def add_memory(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        kind: str,
        content: str,
        importance: float,
        source: str,
    ) -> Row:
        # Antes se insertaba con `embedding=NULL`: `PgMemoryStore.search` filtra
        # `embedding IS NOT NULL` en su rama vectorial, así que un recuerdo
        # agregado por este camino (`POST /v1/memory`) jamás aparecía en la
        # búsqueda semántica que usa el agente, aunque el tenant tuviera
        # `EMBEDDINGS_MODEL` configurado. Se calcula el embedding aquí, con el
        # mismo criterio que la conversación, para que quede visible.
        embedder = _build_memory_embedder(get_settings())
        [embedding] = await embedder.embed([content])
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
                "id": _uuid(),
                "tenant_id": tenant_id,
                "user_id": user_id,
                "kind": kind,
                "content": content,
                "embedding": _vector_literal(embedding),
                "importance": importance,
                "source": source,
                "now": _now(),
            },
        )
        assert row is not None
        return row

    async def delete_memory(self, *, tenant_id: uuid.UUID, memory_id: uuid.UUID) -> bool:
        deleted = await self._exec(
            "DELETE FROM memory_items WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": memory_id},
        )
        return deleted > 0

    # -- conectores -----------------------------------------------------------------

    async def list_connector_accounts(self, *, tenant_id: uuid.UUID) -> list[Row]:
        return await self._all(
            """
            SELECT * FROM connector_accounts
            WHERE tenant_id = :tenant_id
            ORDER BY created_at DESC
            """,
            {"tenant_id": tenant_id},
        )

    async def get_connector_account(
        self, *, tenant_id: uuid.UUID, account_id: uuid.UUID
    ) -> Row | None:
        return await self._first(
            "SELECT * FROM connector_accounts WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": account_id},
        )

    async def create_connector_account(
        self,
        *,
        tenant_id: uuid.UUID,
        connector_key: str,
        external_account_id: str,
        display_name: str,
        scopes: list[str],
    ) -> Row:
        row = await self._first(
            """
            INSERT INTO connector_accounts (
                id, tenant_id, connector_key, external_account_id, display_name, status,
                scopes, created_at, updated_at
            ) VALUES (
                :id, :tenant_id, :connector_key, :external_account_id, :display_name, 'active',
                :scopes ::jsonb, :now, :now
            )
            RETURNING *
            """,
            {
                "id": _uuid(),
                "tenant_id": tenant_id,
                "connector_key": connector_key,
                "external_account_id": external_account_id,
                "display_name": display_name,
                "scopes": _j(scopes),
                "now": _now(),
            },
        )
        assert row is not None
        return row

    async def get_connector_account_by_external_id(
        self, *, connector_key: str, external_account_id: str
    ) -> Row | None:
        """Busca por `(connector_key, external_account_id)` SIN acotar por `tenant_id`.

        Única excepción deliberada, en todo este archivo, a la convención de
        siempre filtrar por tenant: la usa `connect_twilio`
        (`edecan_api.routers.connectors`) para detectar, ANTES de reclamar un
        número de Twilio, si ya lo tiene conectado OTRO tenant (hallazgo de
        auditoría aislamiento-multi-tenant — sin este chequeo, `connector_accounts`
        solo tenía `UNIQUE(tenant_id, connector_key, external_account_id)`,
        que no evita que dos tenants distintos registren el mismo número).
        Debe llamarse con un `Repo` construido sobre la sesión "plataforma"
        (`edecan_api.deps.get_platform_repo`, rol dueño): la sesión normal por
        tenant tiene Row-Level Security activa y jamás vería la fila de otro
        tenant, así que este chequeo sería un no-op si se ejecutara ahí.
        """
        return await self._first(
            """
            SELECT * FROM connector_accounts
            WHERE connector_key = :connector_key AND external_account_id = :external_account_id
            ORDER BY created_at ASC
            LIMIT 1
            """,
            {"connector_key": connector_key, "external_account_id": external_account_id},
        )

    async def delete_connector_account(
        self, *, tenant_id: uuid.UUID, account_id: uuid.UUID
    ) -> bool:
        deleted = await self._exec(
            "DELETE FROM connector_accounts WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": account_id},
        )
        return deleted > 0

    # -- archivos -------------------------------------------------------------------

    async def create_file(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        s3_key: str,
        filename: str,
        mime: str,
        size_bytes: int,
        status: str,
        file_id: uuid.UUID | None = None,
    ) -> Row:
        # `file_id` es opcional para el caller: `edecan_api.routers.files` necesita
        # conocer el id ANTES de insertar la fila (forma parte del `s3_key`, ver
        # ARCHITECTURE.md §10.14 `tenants/{tenant_id}/files/{file_id}/{filename}`),
        # así que puede pasarlo explícito en vez de dejar que `SqlRepo` genere uno
        # nuevo que no coincidiría con la ruta ya subida a S3.
        row = await self._first(
            """
            INSERT INTO files (
                id, tenant_id, user_id, s3_key, filename, mime, size_bytes, status,
                created_at, updated_at
            ) VALUES (
                :id, :tenant_id, :user_id, :s3_key, :filename, :mime, :size_bytes, :status,
                :now, :now
            )
            RETURNING *
            """,
            {
                "id": file_id or _uuid(),
                "tenant_id": tenant_id,
                "user_id": user_id,
                "s3_key": s3_key,
                "filename": filename,
                "mime": mime,
                "size_bytes": size_bytes,
                "status": status,
                "now": _now(),
            },
        )
        assert row is not None
        return row

    async def get_file(self, *, tenant_id: uuid.UUID, file_id: uuid.UUID) -> Row | None:
        return await self._first(
            "SELECT * FROM files WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": file_id},
        )

    async def list_files(self, *, tenant_id: uuid.UUID) -> list[Row]:
        return await self._all(
            "SELECT * FROM files WHERE tenant_id = :tenant_id ORDER BY created_at DESC",
            {"tenant_id": tenant_id},
        )

    # -- recordatorios ----------------------------------------------------------------

    _REMINDER_FIELDS = {"due_at", "rrule", "message", "channel", "status"}

    async def create_reminder(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, fields: dict[str, Any]
    ) -> Row:
        row = await self._first(
            """
            INSERT INTO reminders (
                id, tenant_id, user_id, due_at, rrule, message, channel, status,
                created_at, updated_at
            ) VALUES (
                :id, :tenant_id, :user_id, :due_at, :rrule, :message, :channel, :status, :now, :now
            )
            RETURNING *
            """,
            {
                "id": _uuid(),
                "tenant_id": tenant_id,
                "user_id": user_id,
                "due_at": fields["due_at"],
                "rrule": fields.get("rrule"),
                "message": fields["message"],
                "channel": fields.get("channel", "web"),
                "status": fields.get("status", "pending"),
                "now": _now(),
            },
        )
        assert row is not None
        return row

    async def list_reminders(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> list[Row]:
        return await self._all(
            """
            SELECT * FROM reminders WHERE tenant_id = :tenant_id AND user_id = :user_id
            ORDER BY due_at ASC
            """,
            {"tenant_id": tenant_id, "user_id": user_id},
        )

    async def get_reminder(self, *, tenant_id: uuid.UUID, reminder_id: uuid.UUID) -> Row | None:
        return await self._first(
            "SELECT * FROM reminders WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": reminder_id},
        )

    async def update_reminder(
        self, *, tenant_id: uuid.UUID, reminder_id: uuid.UUID, fields: dict[str, Any]
    ) -> Row | None:
        clean = {k: v for k, v in fields.items() if k in self._REMINDER_FIELDS}
        if not clean:
            return await self.get_reminder(tenant_id=tenant_id, reminder_id=reminder_id)
        set_clause = ", ".join(f"{k} = :{k}" for k in clean)
        params = {**clean, "id": reminder_id, "tenant_id": tenant_id, "now": _now()}
        return await self._first(
            f"UPDATE reminders SET {set_clause}, updated_at = :now "
            "WHERE tenant_id = :tenant_id AND id = :id RETURNING *",
            params,
        )

    async def delete_reminder(self, *, tenant_id: uuid.UUID, reminder_id: uuid.UUID) -> bool:
        deleted = await self._exec(
            "DELETE FROM reminders WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": reminder_id},
        )
        return deleted > 0

    # -- contactos --------------------------------------------------------------------

    async def create_contact(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, fields: dict[str, Any]
    ) -> Row:
        row = await self._first(
            """
            INSERT INTO contacts (
                id, tenant_id, user_id, nombre, emails, phones, empresa, notas, tags,
                created_at, updated_at
            ) VALUES (
                :id, :tenant_id, :user_id, :nombre, :emails ::jsonb, :phones ::jsonb, :empresa,
                :notas, :tags ::jsonb, :now, :now
            )
            RETURNING *
            """,
            {
                "id": _uuid(),
                "tenant_id": tenant_id,
                "user_id": user_id,
                "nombre": fields["nombre"],
                "emails": _j(fields.get("emails", [])),
                "phones": _j(fields.get("phones", [])),
                "empresa": fields.get("empresa") or "",
                "notas": fields.get("notas") or "",
                "tags": _j(fields.get("tags", [])),
                "now": _now(),
            },
        )
        assert row is not None
        return row

    async def list_contacts(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, q: str | None
    ) -> list[Row]:
        if q:
            return await self._all(
                """
                SELECT * FROM contacts
                WHERE tenant_id = :tenant_id AND user_id = :user_id AND nombre ILIKE :q
                ORDER BY nombre ASC
                """,
                {"tenant_id": tenant_id, "user_id": user_id, "q": f"%{q}%"},
            )
        return await self._all(
            """
            SELECT * FROM contacts
            WHERE tenant_id = :tenant_id AND user_id = :user_id
            ORDER BY nombre ASC
            """,
            {"tenant_id": tenant_id, "user_id": user_id},
        )

    async def get_contact(self, *, tenant_id: uuid.UUID, contact_id: uuid.UUID) -> Row | None:
        return await self._first(
            "SELECT * FROM contacts WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": contact_id},
        )

    async def update_contact(
        self, *, tenant_id: uuid.UUID, contact_id: uuid.UUID, fields: dict[str, Any]
    ) -> Row | None:
        allowed = {"nombre", "emails", "phones", "empresa", "notas", "tags"}
        clean = {k: v for k, v in fields.items() if k in allowed}
        if not clean:
            return await self.get_contact(tenant_id=tenant_id, contact_id=contact_id)
        set_parts = []
        params: dict[str, Any] = {"id": contact_id, "tenant_id": tenant_id, "now": _now()}
        for key, value in clean.items():
            if key in ("emails", "phones", "tags"):
                # Espacio antes de `::jsonb` obligatorio -- ver el comentario
                # equivalente en `upsert_persona` (mismo gotcha del regex de
                # bind params de SQLAlchemy).
                set_parts.append(f"{key} = :{key} ::jsonb")
                params[key] = _j(value)
            else:
                set_parts.append(f"{key} = :{key}")
                params[key] = value
        return await self._first(
            f"UPDATE contacts SET {', '.join(set_parts)}, updated_at = :now "
            "WHERE tenant_id = :tenant_id AND id = :id RETURNING *",
            params,
        )

    async def delete_contact(self, *, tenant_id: uuid.UUID, contact_id: uuid.UUID) -> bool:
        deleted = await self._exec(
            "DELETE FROM contacts WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": contact_id},
        )
        return deleted > 0

    # -- finanzas ---------------------------------------------------------------------

    async def create_transaction(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, fields: dict[str, Any]
    ) -> Row:
        row = await self._first(
            """
            INSERT INTO transactions (
                id, tenant_id, user_id, fecha, monto, moneda, categoria, descripcion, cuenta,
                created_at, updated_at
            ) VALUES (
                :id, :tenant_id, :user_id, :fecha ::date, :monto ::numeric, :moneda, :categoria,
                :descripcion, :cuenta, :now, :now
            )
            RETURNING *
            """,
            {
                "id": _uuid(),
                "tenant_id": tenant_id,
                "user_id": user_id,
                "fecha": fields["fecha"],
                "monto": fields["monto"],
                "moneda": fields.get("moneda", "USD"),
                "categoria": fields.get("categoria") or "sin_categoria",
                "descripcion": fields.get("descripcion") or "",
                "cuenta": fields.get("cuenta") or "",
                "now": _now(),
            },
        )
        assert row is not None
        return row

    async def list_transactions(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, mes: str | None
    ) -> list[Row]:
        if mes:
            return await self._all(
                """
                SELECT * FROM transactions
                WHERE tenant_id = :tenant_id AND user_id = :user_id
                  AND to_char(fecha, 'YYYY-MM') = :mes
                ORDER BY fecha DESC
                """,
                {"tenant_id": tenant_id, "user_id": user_id, "mes": mes},
            )
        return await self._all(
            """
            SELECT * FROM transactions
            WHERE tenant_id = :tenant_id AND user_id = :user_id
            ORDER BY fecha DESC
            """,
            {"tenant_id": tenant_id, "user_id": user_id},
        )

    async def get_transaction(
        self, *, tenant_id: uuid.UUID, transaction_id: uuid.UUID
    ) -> Row | None:
        return await self._first(
            "SELECT * FROM transactions WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": transaction_id},
        )

    async def update_transaction(
        self, *, tenant_id: uuid.UUID, transaction_id: uuid.UUID, fields: dict[str, Any]
    ) -> Row | None:
        allowed = {"fecha", "monto", "moneda", "categoria", "descripcion", "cuenta"}
        clean = {k: v for k, v in fields.items() if k in allowed}
        if not clean:
            return await self.get_transaction(tenant_id=tenant_id, transaction_id=transaction_id)
        set_parts = []
        params: dict[str, Any] = {"id": transaction_id, "tenant_id": tenant_id, "now": _now()}
        for key, value in clean.items():
            # Espacio delante del cast obligatorio (cuando aplica) -- ver el
            # comentario equivalente en `upsert_persona` (mismo gotcha del
            # regex de bind params de SQLAlchemy: `:fecha::date` pegado deja
            # de reconocerse como parámetro).
            cast = " ::date" if key == "fecha" else " ::numeric" if key == "monto" else ""
            set_parts.append(f"{key} = :{key}{cast}")
            params[key] = value
        return await self._first(
            f"UPDATE transactions SET {', '.join(set_parts)}, updated_at = :now "
            "WHERE tenant_id = :tenant_id AND id = :id RETURNING *",
            params,
        )

    async def delete_transaction(self, *, tenant_id: uuid.UUID, transaction_id: uuid.UUID) -> bool:
        deleted = await self._exec(
            "DELETE FROM transactions WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": transaction_id},
        )
        return deleted > 0

    async def finance_summary(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID, mes: str) -> Row:
        row = await self._first(
            """
            SELECT
                COALESCE(SUM(monto) FILTER (WHERE monto > 0), 0) AS ingresos,
                COALESCE(SUM(monto) FILTER (WHERE monto < 0), 0) AS gastos,
                COALESCE(SUM(monto), 0) AS neto,
                COUNT(*) AS num_transacciones
            FROM transactions
            WHERE tenant_id = :tenant_id AND user_id = :user_id AND to_char(fecha, 'YYYY-MM') = :mes
            """,
            {"tenant_id": tenant_id, "user_id": user_id, "mes": mes},
        )
        por_categoria = await self._all(
            """
            SELECT
                COALESCE(categoria, 'sin_categoria') AS categoria,
                COALESCE(SUM(monto), 0) AS total
            FROM transactions
            WHERE tenant_id = :tenant_id AND user_id = :user_id AND to_char(fecha, 'YYYY-MM') = :mes
            GROUP BY categoria
            ORDER BY total ASC
            """,
            {"tenant_id": tenant_id, "user_id": user_id, "mes": mes},
        )
        assert row is not None
        row["por_categoria"] = por_categoria
        row["mes"] = mes
        return row

    # -- billing -----------------------------------------------------------------------

    async def upsert_subscription(self, *, tenant_id: uuid.UUID, fields: dict[str, Any]) -> Row:
        existing = await self._first(
            "SELECT * FROM subscriptions WHERE tenant_id = :tenant_id", {"tenant_id": tenant_id}
        )
        if existing is None:
            row = await self._first(
                """
                INSERT INTO subscriptions (
                    id, tenant_id, stripe_customer_id, stripe_subscription_id, plan_key,
                    status, current_period_end, created_at, updated_at
                ) VALUES (
                    :id, :tenant_id, :stripe_customer_id, :stripe_subscription_id, :plan_key,
                    :status, :current_period_end, :now, :now
                )
                RETURNING *
                """,
                {
                    "id": _uuid(),
                    "tenant_id": tenant_id,
                    "stripe_customer_id": fields.get("stripe_customer_id"),
                    "stripe_subscription_id": fields.get("stripe_subscription_id"),
                    "plan_key": fields.get("plan_key"),
                    "status": fields.get("status", "active"),
                    "current_period_end": fields.get("current_period_end"),
                    "now": _now(),
                },
            )
            assert row is not None
            return row

        allowed = {
            "stripe_customer_id",
            "stripe_subscription_id",
            "plan_key",
            "status",
            "current_period_end",
        }
        clean = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not clean:
            return existing
        set_clause = ", ".join(f"{k} = :{k}" for k in clean)
        row = await self._first(
            f"UPDATE subscriptions SET {set_clause}, updated_at = :now "
            "WHERE tenant_id = :tenant_id RETURNING *",
            {**clean, "tenant_id": tenant_id, "now": _now()},
        )
        assert row is not None
        return row

    async def get_subscription_by_stripe_customer(self, stripe_customer_id: str) -> Row | None:
        return await self._first(
            "SELECT * FROM subscriptions WHERE stripe_customer_id = :v", {"v": stripe_customer_id}
        )

    async def get_subscription_by_stripe_subscription(
        self, stripe_subscription_id: str
    ) -> Row | None:
        return await self._first(
            "SELECT * FROM subscriptions WHERE stripe_subscription_id = :v",
            {"v": stripe_subscription_id},
        )

    # -- auditoría ---------------------------------------------------------------------

    async def add_audit_log(
        self,
        *,
        tenant_id: uuid.UUID | None,
        actor_user_id: uuid.UUID | None,
        action: str,
        target: str,
        meta: dict[str, Any] | None = None,
    ) -> None:
        await self._exec(
            """
            INSERT INTO audit_log (
                id, tenant_id, actor_user_id, action, target, meta, created_at, updated_at
            )
            VALUES (:id, :tenant_id, :actor_user_id, :action, :target, :meta ::jsonb, :now, :now)
            """,
            {
                "id": _uuid(),
                "tenant_id": tenant_id,
                "actor_user_id": actor_user_id,
                "action": action,
                "target": target,
                "meta": _j(meta or {}),
                "now": _now(),
            },
        )

    # -- vista remota (control remoto, WP-V2-09) -------------------------------------------
    #
    # `remote_sessions` (migración `0003_v2_expansion`, ya aterrizada — ver
    # `packages/db/edecan_db/models.py::RemoteSession`). Mismos nombres de
    # columna/valores que el prototipo original en memoria de
    # `edecan_api.routers.remote._RemoteSessionStore` (ver historial de ese
    # archivo): `device_id` siempre `NULL` (el emparejamiento con `devices` es
    # P2, no lo escribe ningún código todavía), `kind` siempre `'view'`.

    _REMOTE_SESSION_COLUMNS = (
        "id, tenant_id, user_id, device_id, kind, status, started_at, ended_at, "
        "frames_count, created_at, updated_at"
    )

    async def create_remote_session(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> Row:
        row = await self._first(
            f"""
            INSERT INTO remote_sessions (
                id, tenant_id, user_id, device_id, kind, status, frames_count
            )
            VALUES (:id, :tenant_id, :user_id, NULL, 'view', 'pending', 0)
            RETURNING {self._REMOTE_SESSION_COLUMNS}
            """,
            {"id": _uuid(), "tenant_id": tenant_id, "user_id": user_id},
        )
        assert row is not None
        return row

    async def list_remote_sessions(self, *, tenant_id: uuid.UUID) -> list[Row]:
        return await self._all(
            f"""
            SELECT {self._REMOTE_SESSION_COLUMNS} FROM remote_sessions
            WHERE tenant_id = :tenant_id
            ORDER BY created_at DESC
            """,
            {"tenant_id": tenant_id},
        )

    async def get_remote_session(
        self, *, tenant_id: uuid.UUID, session_id: uuid.UUID
    ) -> Row | None:
        return await self._first(
            f"""
            SELECT {self._REMOTE_SESSION_COLUMNS} FROM remote_sessions
            WHERE tenant_id = :tenant_id AND id = :id
            """,
            {"tenant_id": tenant_id, "id": session_id},
        )

    async def record_remote_session_frame(
        self, *, tenant_id: uuid.UUID, session_id: uuid.UUID
    ) -> Row:
        """Incrementa `frames_count`; si la sesión seguía `pending`, la pasa a
        `active` y fija `started_at` (mismo contrato que el prototipo en
        memoria: "primer frame OK marca la sesión active + started_at").
        Se asume que el llamador ya verificó que la fila existe y pertenece a
        `tenant_id` (todas las rutas de `routers.remote` llaman primero a
        `get_remote_session`, que 404-ea si no) — el `WHERE tenant_id = ...`
        de abajo es de todos modos la barrera real entre tenants."""
        row = await self._first(
            f"""
            UPDATE remote_sessions SET
                frames_count = frames_count + 1,
                status = CASE WHEN status = 'pending' THEN 'active' ELSE status END,
                started_at = CASE WHEN status = 'pending' THEN :now ELSE started_at END,
                updated_at = :now
            WHERE tenant_id = :tenant_id AND id = :id
            RETURNING {self._REMOTE_SESSION_COLUMNS}
            """,
            {"tenant_id": tenant_id, "id": session_id, "now": _now()},
        )
        assert row is not None
        return row

    async def mark_remote_session_denied(
        self, *, tenant_id: uuid.UUID, session_id: uuid.UUID
    ) -> Row:
        row = await self._first(
            f"""
            UPDATE remote_sessions SET status = 'denied', updated_at = :now
            WHERE tenant_id = :tenant_id AND id = :id
            RETURNING {self._REMOTE_SESSION_COLUMNS}
            """,
            {"tenant_id": tenant_id, "id": session_id, "now": _now()},
        )
        assert row is not None
        return row

    async def mark_remote_session_ended(
        self, *, tenant_id: uuid.UUID, session_id: uuid.UUID
    ) -> Row:
        """Idempotente: terminar una sesión ya `ended` no la vuelve a tocar
        (mismo contrato que el prototipo en memoria) — el UPDATE solo afecta
        filas con `status != 'ended'`; si no afectó ninguna (ya estaba
        `ended`), se relee la fila sin modificarla."""
        row = await self._first(
            f"""
            UPDATE remote_sessions SET status = 'ended', ended_at = :now, updated_at = :now
            WHERE tenant_id = :tenant_id AND id = :id AND status != 'ended'
            RETURNING {self._REMOTE_SESSION_COLUMNS}
            """,
            {"tenant_id": tenant_id, "id": session_id, "now": _now()},
        )
        if row is not None:
            return row
        existing = await self.get_remote_session(tenant_id=tenant_id, session_id=session_id)
        assert existing is not None
        return existing

    async def mark_remote_session_kind(
        self, *, tenant_id: uuid.UUID, session_id: uuid.UUID, kind: str
    ) -> Row:
        """WP-V4-10: usada por `routers.remote.create_session` justo después de
        `create_remote_session` cuando el cliente pidió `kind='control'` --
        ver el comentario sobre este método en la declaración de `Repo` más
        arriba para el porqué de un método nuevo en vez de un parámetro."""
        row = await self._first(
            f"""
            UPDATE remote_sessions SET kind = :kind, updated_at = :now
            WHERE tenant_id = :tenant_id AND id = :id
            RETURNING {self._REMOTE_SESSION_COLUMNS}
            """,
            {"tenant_id": tenant_id, "id": session_id, "kind": kind, "now": _now()},
        )
        assert row is not None
        return row
