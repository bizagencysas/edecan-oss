"""Modelos SQLAlchemy 2.0 (estilo `Mapped`) de todas las tablas de Edecán.

Contrato exacto: `ARCHITECTURE.md` §10.3. Todas las tablas tienen
`id UUID PK default gen_random_uuid()` y `created_at`/`updated_at timestamptz`
(`IDMixin`/`TimestampMixin`); las tenant-scoped además `tenant_id UUID NOT NULL`
indexado (`TenantScopedMixin`) y quedan protegidas por Row-Level Security
(política `tenant_isolation`) en la migración `0001_initial` — ver
`edecan_db.session.get_session` para cómo se activa esa política por
transacción.

Solo `tenants`, `users` y `tenant_keys` son globales (sin RLS). `jobs` y
`audit_log` tienen `tenant_id` NULLABLE (jobs/auditoría de plataforma no
atados a un tenant) pero SÍ llevan RLS: bajo una sesión con `app.tenant_id`
fijado, las filas con `tenant_id IS NULL` simplemente no son visibles (el
operador `=` con `NULL` nunca es verdadero) — solo el rol dueño (worker, que
bypassa RLS y filtra a mano, ver §2) puede verlas.

Deliberadamente **no** se declaran `relationship()` de ORM: en un stack async
la carga perezosa (lazy loading) de relaciones es una fuente clásica de bugs
(`MissingGreenlet`) si no se usa `selectinload`/`joinedload` explícito en cada
query. Los paquetes consumidores hacen `select(Modelo).where(...)` explícito;
las FKs ya documentan las relaciones a nivel de esquema.

Los `CheckConstraint` de este módulo solo restringen las columnas cuyo
vocabulario está *pinned* explícitamente en `ARCHITECTURE.md` (notación
`campo: a|b|c` en §10.3, el tuple `JOB_TYPES` de §10.5, o el rango
`0..3` de `PersonaConfig.formalidad` en §10.5). Columnas de estado/rol sin
vocabulario pinned (p. ej. `tenants.status`, `connector_accounts.status`,
`campaigns.status`, `subscriptions.status`) se dejan como texto abierto a
propósito para no inventar un contrato que el documento no fija.

Las 14 tablas al final de este módulo (sección "v2") son de
`ROADMAP_V2.md` §7.4 (dueño WP-V2-01) y siguen la MISMA convención: mismos
mixins, mismo helper `_enum_check`, mismo criterio de nullability. Regla
local para columnas JSONB nuevas sin vocabulario de texto (no aplica a las
de v1, ya congeladas): si el campo está marcado `null` en §7.4 queda
`nullable=True` sin default (p. ej. `agent_steps.usage`); si no, queda
`nullable=False` con `server_default=text("'{}'::jsonb")` (p. ej.
`orders.meta`, mismo criterio que `UsageEvent.meta`/`AuditLog.meta` de v1) —
así ninguna columna JSONB nueva exige que el caller pase un literal vacío a
mano. Para columnas `timestamptz` nuevas sin marca `null`: si semánticamente
representan "el momento en que la fila empezó a existir/ocurrió" (p. ej.
`health_logs.registrado_en`, `automation_runs.started_at`) llevan
`server_default=func.now()` — mismo criterio que `consents.granted_at` en v1
(que tampoco trae la palabra "default" en §10.3 pero sí lleva ese default en
este módulo).

La sección "v3" que sigue a continuación (una sola tabla, `Skill`) es de
`ARCHITECTURE.md` §12e (dueño WP-V3-01) y sigue el mismo criterio.

La sección "v4" (`Product`/`StockMove`/`AdDraft`) es de `ARCHITECTURE.md` §13
(dueño WP-V4-01), mismo criterio: columnas de texto nuevas sin vocabulario
pinned usan `Text` (no `String`, ver `Skill` en v3); `server_default=text(...)`
para defaults numéricos/booleanos, string literal plano para defaults de
texto (mismo criterio documentado arriba).

La sección "v5" al final (`Employee`/`TimeOff`/`PayrollRun`/`PayrollItem`/
`VoiceConsent`) es de `ARCHITECTURE.md` §14 (dueño WP-V5-01), mismo criterio
de tipos/defaults que v4. Sus columnas `status` (`employees.status`,
`voice_consents.status`) se dejan como texto abierto a propósito (mismo
criterio que `tenants.status` citado arriba, sin vocabulario pinned en §14)
salvo `time_off.status`/`payroll_runs.status`, que SÍ llevan `_enum_check`
por ser máquinas de estado explícitas (aprobar/rechazar una ausencia; nunca
pagar nómina sin pasar por `'approved'`, mismo espíritu que `ad_drafts`/
`orders`: "dinero real nunca se mueve solo", `DIRECCION_ACTUAL.md`). Además,
esta sección altera dos tablas existentes (`ADD COLUMN`, migración
`0007_v5_expansion`): `devices` gana `push_token`/`push_platform` (v2,
notificaciones push) y `skills` gana `trust_tier`/`capabilities` (v3, modelo
de confianza adaptado de OpenJarvis) — ver el docstring de `Device`/`Skill`
más arriba, no de esta sección.

La sección "v6" al final (`Meeting`/`Podcast`) es de `ARCHITECTURE.md` §15
(dueño WP-V6-01), migración `0008_v6_expansion`, mismo criterio de
tipos/defaults que v4/v5. `meetings.status`/`podcasts.status` comparten el
MISMO vocabulario (`pending|running|done|error`) y llevan `_enum_check` (a
diferencia de `employees.status`/`tenants.status`: aquí sí hay una máquina de
estados explícita — un job `process_meeting`/`generate_podcast` la mueve de
`pending` a `running` y luego a `done`/`error`, nunca la decide el caller a
mano). `meetings.source_file_id`/`transcript_file_id` y `podcasts.file_id`
NO llevan FK a propósito — mismo criterio que `voice_consents.consent_file_id`
(v5): referencia informativa opcional a un `File`, no forzada a nivel de base
de datos.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

# ---------------------------------------------------------------------------
# Base declarativa + convención de nombres (recomendada por los docs de
# Alembic para que constraints/índices tengan nombres predecibles, útiles al
# escribir `op.drop_constraint(...)` a mano en futuras migraciones).
# ---------------------------------------------------------------------------

NAMING_CONVENTION = {
    # `ix`/`uq`/`fk`/`pk`: SQLAlchemy solo usa esta plantilla para generar un
    # nombre cuando el constraint/índice NO trae ya un `name=` explícito — un
    # `name=` explícito se respeta tal cual (verificado: un `UniqueConstraint`
    # multi-columna con `name=` explícito conserva sus columnas completas en
    # el nombre, no se trunca a `column_0_name`). Este paquete siempre pasa
    # `name=` explícito a sus `UniqueConstraint`, así que esta plantilla de
    # `uq` es en la práctica solo una red de seguridad para el caso (no usado
    # hoy) de un `UniqueConstraint` sin nombre.
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    # OJO: a diferencia de `ix`/`uq`/`fk`/`pk`, la convención `ck` SIEMPRE
    # reprocesa el `name=` ya dado (un `CheckConstraint` no tiene columnas
    # "naturales" de las que derivar un nombre, así que el token
    # `%(constraint_name)s` reutiliza el nombre recibido como ingrediente).
    # Por eso `_enum_check(...)` de este módulo recibe el nombre BASE (p. ej.
    # `"role"`), no el nombre completo (`"ck_memberships_role"`) — si se le
    # pasara el nombre completo, quedaría doblemente prefijado
    # (`ck_memberships_ck_memberships_role`).
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base compartida por todos los modelos de `edecan_db`."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# ---------------------------------------------------------------------------
# Mixins comunes
# ---------------------------------------------------------------------------


class IDMixin:
    """`id UUID PK default gen_random_uuid()` (§10.3, preámbulo)."""

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )


class TimestampMixin:
    """`created_at`/`updated_at timestamptz` (§10.3, preámbulo).

    `updated_at` se refresca vía `onupdate=func.now()` de SQLAlchemy — esto
    cubre updates hechos a través del ORM. Updates SQL directos (fuera del
    ORM) no lo disparan; si en el futuro hace falta esa garantía a nivel de
    base de datos, se puede añadir un trigger en una migración posterior.
    """

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class TenantScopedMixin:
    """`tenant_id UUID NOT NULL` + índice + FK a `tenants.id` (§2, §10.3).

    Las tablas que usan este mixin quedan protegidas por Row-Level Security
    (política `tenant_isolation`) en la migración `0001_initial`. Se usa
    `declared_attr` (en vez de un atributo de clase plano) porque la columna
    lleva `ForeignKey`, y `declared_attr` es la forma recomendada por
    SQLAlchemy para que cada tabla concreta reciba su propia `Column`.
    """

    @declared_attr
    def tenant_id(cls) -> Mapped[uuid.UUID]:  # noqa: N805 - convención de SQLAlchemy
        return mapped_column(
            PG_UUID(as_uuid=True),
            ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )


def _enum_check(column: str, values: tuple[str, ...]) -> CheckConstraint:
    """`CheckConstraint` para una columna cuyo vocabulario está pinned en §10.3/§10.5.

    `name=column` (nombre BASE, sin prefijo de tabla): la convención `ck` de
    `NAMING_CONVENTION` antepone `ck_<tabla>_` automáticamente — ver la nota
    junto a esa convención más arriba.
    """
    quoted = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IN ({quoted})", name=column)


# ---------------------------------------------------------------------------
# Globales (sin RLS): tenants, users, tenant_keys
# ---------------------------------------------------------------------------


class Tenant(IDMixin, TimestampMixin, Base):
    """`tenants(name, slug unique, plan_key, status)` — global, sin RLS."""

    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    # `plan_key` referencia lógicamente a `edecan_schemas.plans.PLANES` (no hay
    # FK de base de datos porque los planes viven en código, no en tabla).
    plan_key: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="active")
    # NULL = todavía no terminó el wizard de primer arranque (migración 0009,
    # `apps/api/edecan_api/routers/setup.py::put_setup_complete`).
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    # NULL = compró el tier base ($99, "código completo"); con fecha = compró
    # el tier de $199 ("código + actualizaciones de por vida", migración
    # 0010) — ver docstring de esa migración para el modelo de precio nuevo.
    lifetime_updates_purchased_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )


class User(IDMixin, TimestampMixin, Base):
    """`users(email unique, password_hash, totp_secret nullable, is_superadmin bool)` — global."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    totp_secret: Mapped[str | None] = mapped_column(String, nullable=True)
    is_superadmin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )


class TenantKey(IDMixin, TimestampMixin, Base):
    """`tenant_keys(tenant_id unique, encrypted_data_key, kms_key_id nullable, version)`.

    Global (sin RLS) — ver `ARCHITECTURE.md` §10.3. Solo `edecan_db.vault`
    accede a esta tabla, y SIEMPRE filtra por `tenant_id` de forma explícita
    en cada query (igual que el worker hace con `jobs`, ver §2), así que la
    ausencia de RLS aquí no abre una fuga entre tenants en la práctica.
    """

    __tablename__ = "tenant_keys"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    encrypted_data_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    kms_key_id: Mapped[str | None] = mapped_column(String, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


# ---------------------------------------------------------------------------
# Tenant-scoped (RLS)
# ---------------------------------------------------------------------------


class Membership(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`memberships(user_id, tenant_id, role: owner|admin|member)`."""

    __tablename__ = "memberships"
    __table_args__ = (
        _enum_check("role", ("owner", "admin", "member")),
        UniqueConstraint("tenant_id", "user_id", name="uq_memberships_tenant_id_user_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String, nullable=False)


class Persona(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`personas(...)` — configuración "nivel Dios" del asistente (§10.3, §10.5).

    Defaults idénticos a `edecan_schemas.models.PersonaConfig` (duplicados a
    propósito: este paquete no depende de `pydantic` para sus columnas, solo
    reusa los mismos valores literales pinned en §10.5).
    """

    __tablename__ = "personas"
    __table_args__ = (
        CheckConstraint("formalidad >= 0 AND formalidad <= 3", name="formalidad_range"),
        CheckConstraint(
            "estilo_relacion IN ('profesional', 'coach', 'amigo', 'romantico')",
            name="persona_estilo_relacion_valid",
        ),
        CheckConstraint(
            "(estilo_relacion = 'romantico' AND adulto_confirmado "
            "AND consentimiento_romantico) OR "
            "(estilo_relacion <> 'romantico' AND NOT adulto_confirmado "
            "AND NOT consentimiento_romantico)",
            name="persona_romantico_requires_consent",
        ),
        # Como máximo una persona "default" (user_id NULL) por tenant. Postgres
        # trata cada NULL como distinto en un UNIQUE normal, por eso hace falta
        # un índice único parcial en vez de un UniqueConstraint(tenant_id, user_id).
        Index(
            "uq_personas_tenant_id_default",
            "tenant_id",
            unique=True,
            postgresql_where=text("user_id IS NULL"),
        ),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    nombre_asistente: Mapped[str] = mapped_column(String, nullable=False, server_default="Edecán")
    idioma: Mapped[str] = mapped_column(String, nullable=False, server_default="es")
    tono: Mapped[str] = mapped_column(String, nullable=False, server_default="cálido y profesional")
    formalidad: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    emojis: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    instrucciones: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    rasgos: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    memoria_activada: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    voice_id: Mapped[str | None] = mapped_column(String, nullable=True)
    estilo_relacion: Mapped[str] = mapped_column(
        String, nullable=False, server_default="profesional"
    )
    adulto_confirmado: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    consentimiento_romantico: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )


class Conversation(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`conversations(tenant_id, user_id, title, channel: web|voice|phone|api)`."""

    __tablename__ = "conversations"
    __table_args__ = (
        _enum_check("channel", ("web", "voice", "phone", "api")),
        _enum_check("title_source", ("auto_pending", "auto", "manual", "legacy")),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String, nullable=False, server_default="")
    title_source: Mapped[str] = mapped_column(String, nullable=False, server_default="legacy")
    channel: Mapped[str] = mapped_column(String, nullable=False, server_default="web")


class Message(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`messages(conversation_id, tenant_id, role, content jsonb, tool_calls jsonb nullable,
    tokens_in int, tokens_out int)`."""

    __tablename__ = "messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    # Formato libre: normalmente `{"text": "..."}` o una lista de bloques de
    # contenido estilo Anthropic (ver `edecan_llm.base.ChatMessage`, §10.6).
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[Any] = mapped_column(JSONB, nullable=False)
    tool_calls: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))


class MemoryItem(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`memory_items(tenant_id, user_id, kind: fact|preference|event|entity, content text,
    embedding vector(1536) nullable, importance float, source text, superseded_at,
    superseded_by)`. Las filas reemplazadas se conservan como historial, pero no
    participan en el contexto activo del asistente."""

    __tablename__ = "memory_items"
    __table_args__ = (_enum_check("kind", ("fact", "preference", "event", "entity")),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Runtime: `pgvector` devuelve `numpy.ndarray` al leer y acepta
    # `list[float]`/`numpy.ndarray` al escribir. Dimensión fija 1536
    # (`EMBEDDINGS_DIM` por defecto, ARCHITECTURE.md §10.2).
    embedding: Mapped[Any | None] = mapped_column(Vector(1536), nullable=True)
    importance: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0"))
    source: Mapped[str] = mapped_column(String, nullable=False)
    superseded_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("memory_items.id", ondelete="SET NULL"),
        nullable=True,
    )


class MemoryEdge(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`memory_edges(tenant_id, src_id, dst_id, relation text)` — grafo de memoria."""

    __tablename__ = "memory_edges"

    src_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("memory_items.id", ondelete="CASCADE"), nullable=False
    )
    dst_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("memory_items.id", ondelete="CASCADE"), nullable=False
    )
    relation: Mapped[str] = mapped_column(String, nullable=False)


class ConnectorAccount(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`connector_accounts(tenant_id, connector_key, external_account_id, display_name,
    status, scopes jsonb)`."""

    __tablename__ = "connector_accounts"
    __table_args__ = (
        # Nombre acortado a propósito (no es solo `uq_<tabla>_<col1>_<col2>_<col3>`
        # literal): Postgres trunca/rechaza identificadores de más de 63 bytes,
        # y el nombre "obvio" completo (`..._tenant_id_connector_key_external_account_id`)
        # los supera.
        UniqueConstraint(
            "tenant_id",
            "connector_key",
            "external_account_id",
            name="uq_connector_accounts_tenant_connector_external",
        ),
        # Solo evita duplicados DENTRO de un mismo tenant. Un número E.164 de
        # Twilio real solo puede pertenecer a UNA cuenta de Twilio (y por
        # tanto a un tenant) a la vez, así que además hace falta un UNIQUE
        # GLOBAL (cruzando tenants) para `connector_key='twilio'` — sin él,
        # cualquier tenant autenticado podía registrar el mismo número que ya
        # tenía conectado OTRO tenant (hallazgo de auditoría
        # aislamiento-multi-tenant): `edecan_premium.twilio_router
        # ._resolve_tenant_by_number` resuelve el dueño de un número por
        # `ORDER BY created_at DESC LIMIT 1`, así que el registro más
        # reciente "robaba" el número, y las llamadas/SMS entrantes reales
        # del dueño legítimo empezaban a fallar la validación de firma.
        # `edecan_api.routers.connectors.connect_twilio` ya verifica
        # propiedad contra la API real de Twilio antes de insertar (defensa
        # primaria) y hace un chequeo aplicativo previo (mensaje 409 claro);
        # este índice es el respaldo atómico ante una carrera entre dos
        # requests concurrentes. Índice único PARCIAL (no un
        # `UniqueConstraint` de la sola columna `external_account_id`) porque
        # solo aplica a Twilio: los demás `connector_key` (OAuth) no tienen
        # esa garantía de unicidad real en `external_account_id` (ver
        # `_bundle_account_hint` en `edecan_api.routers.connectors`, que lo
        # deriva de un hash del access token, no de un id de cuenta estable
        # del proveedor).
        Index(
            "uq_connector_accounts_twilio_external_account_id",
            "external_account_id",
            unique=True,
            postgresql_where=text("connector_key = 'twilio'"),
        ),
    )

    # Claves EXACTAS de `edecan_connectors.registry.CONNECTORS` ("google",
    # "microsoft", "meta", "x", "youtube") más "twilio" para telefonía premium
    # (§10.10). No se restringe con CHECK: el registro de conectores es
    # extensible en tiempo de ejecución (ver docstring del módulo).
    connector_key: Mapped[str] = mapped_column(String, nullable=False)
    external_account_id: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False, server_default="")
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="active")
    scopes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )


class OAuthToken(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`oauth_tokens(tenant_id, connector_account_id, ciphertext bytea, nonce bytea,
    key_version int, expires_at nullable)` — ver `edecan_db.vault.TokenVault`."""

    __tablename__ = "oauth_tokens"
    __table_args__ = (
        UniqueConstraint("connector_account_id", name="uq_oauth_tokens_connector_account_id"),
    )

    connector_account_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("connector_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class File(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`files(tenant_id, user_id, s3_key, filename, mime, size_bytes,
    status: uploaded|processing|ready|error)`."""

    __tablename__ = "files"
    __table_args__ = (_enum_check("status", ("uploaded", "processing", "ready", "error")),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # `s3://$S3_BUCKET/tenants/{tenant_id}/files/{file_id}/{filename}` (§10.14).
    s3_key: Mapped[str] = mapped_column(String, nullable=False)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    mime: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="uploaded")


class FileChunk(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`file_chunks(tenant_id, file_id, seq int, text, embedding vector(1536))`."""

    __tablename__ = "file_chunks"
    __table_args__ = (UniqueConstraint("file_id", "seq", name="uq_file_chunks_file_id_seq"),)

    file_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    # Nombre de columna pinned tal cual en §10.3 ("text"). No colisiona con el
    # `text()` de SQLAlchemy importado a nivel de módulo: el cuerpo de una
    # clase tiene su propio namespace, así que este atributo no lo sombrea.
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(1536), nullable=False)


class Reminder(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`reminders(tenant_id, user_id, due_at timestamptz, rrule nullable, message,
    channel, status: pending|sent|cancelled)`."""

    __tablename__ = "reminders"
    __table_args__ = (_enum_check("status", ("pending", "sent", "cancelled")),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    due_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    rrule: Mapped[str | None] = mapped_column(String, nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="pending")


class Contact(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`contacts(tenant_id, user_id, nombre, emails jsonb, phones jsonb, empresa, notas,
    tags jsonb)`."""

    __tablename__ = "contacts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    nombre: Mapped[str] = mapped_column(String, nullable=False)
    emails: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    phones: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    empresa: Mapped[str] = mapped_column(String, nullable=False, server_default="")
    notas: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    tags: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )


class Transaction(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`transactions(tenant_id, user_id, fecha date, monto numeric(14,2), moneda char(3),
    categoria, descripcion, cuenta)` — finanzas personales estilo CFO."""

    __tablename__ = "transactions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    monto: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    moneda: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    categoria: Mapped[str] = mapped_column(String, nullable=False)
    descripcion: Mapped[str] = mapped_column(String, nullable=False, server_default="")
    cuenta: Mapped[str] = mapped_column(String, nullable=False, server_default="")


class Campaign(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`campaigns(tenant_id, nombre, kind: voice|sms, script text, status, schedule jsonb)`."""

    __tablename__ = "campaigns"
    __table_args__ = (_enum_check("kind", ("voice", "sms")),)

    nombre: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    script: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="draft")
    schedule: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class CampaignTarget(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`campaign_targets(tenant_id, campaign_id, contact_id nullable, phone_e164,
    status: pending|done|optout|error, last_attempt_at nullable)`."""

    __tablename__ = "campaign_targets"
    __table_args__ = (_enum_check("status", ("pending", "done", "optout", "error")),)

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    phone_e164: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="pending")
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )


class Consent(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`consents(tenant_id, phone_e164, kind: sms|voice, granted_at, revoked_at nullable,
    source)` — motor de cumplimiento de telefonía/campañas (§4, §10.10)."""

    __tablename__ = "consents"
    __table_args__ = (_enum_check("kind", ("sms", "voice")),)

    phone_e164: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=False)


class Job(IDMixin, TimestampMixin, Base):
    """`jobs(tenant_id nullable, type, payload jsonb, status: queued|running|done|error,
    attempts int, last_error nullable)` (§10.3, §10.11).

    `tenant_id` es NULLABLE (jobs de plataforma, p. ej. `send_reminder_scan`,
    no están atados a un tenant) pero la tabla SÍ tiene RLS: bajo una sesión
    con `app.tenant_id` fijado, las filas con `tenant_id IS NULL` no son
    visibles. El worker se conecta como dueño (bypassa RLS, §2) y filtra por
    `tenant_id` a mano.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        _enum_check("status", ("queued", "running", "done", "error")),
        # Mismo vocabulario que `edecan_schemas.queue.JOB_TYPES` (§10.5) — se
        # duplica aquí como literales porque `edecan_db` no depende de
        # `edecan_schemas` para tipos, solo para el `TokenBundle` del vault.
        # Los 3 tipos `run_mission`/`run_automation`/`automation_scan` son de
        # v2 (migración `0005_jobs_type_check_v2_types`, que también
        # actualiza el CHECK real en Postgres — este `_enum_check` describe
        # el esquema pero nunca lo crea, ver docstring del módulo). El 11º
        # (`generate_podcast`) es de v5 — `ARCHITECTURE.md` §14, migración
        # `0007_v5_expansion`, que actualiza el CHECK real en Postgres con el
        # MISMO patrón drop+create que usó `0005` (Postgres no soporta
        # modificar la expresión de un CHECK existente in place). El 12º
        # (`process_meeting`) es de v6 — `ARCHITECTURE.md` §15, migración
        # `0008_v6_expansion`. `notify_phone_call_summary` se agrega en
        # `0017_phone_call_summaries` para desacoplar el push del webhook.
        _enum_check(
            "type",
            (
                "ingest_file",
                "sync_connector",
                "send_reminder",
                "send_reminder_scan",
                "run_campaign_step",
                "generate_content",
                "memory_consolidate",
                "run_mission",
                "run_automation",
                "automation_scan",
                "generate_podcast",
                "process_meeting",
                "notify_phone_call_summary",
                "notify_incoming_phone_call",
                "notify_important_event",
            ),
        ),
    )

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class UsageEvent(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`usage_events(tenant_id, kind: llm_tokens|voice_seconds|storage_bytes|messages,
    quantity numeric, meta jsonb)` — medición para cuotas/facturación."""

    __tablename__ = "usage_events"
    __table_args__ = (
        _enum_check(
            "kind",
            ("llm_tokens", "voice_seconds", "storage_bytes", "messages"),
        ),
    )

    kind: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class AuditLog(IDMixin, TimestampMixin, Base):
    """`audit_log(tenant_id nullable, actor_user_id nullable, action, target, meta jsonb)`.

    `tenant_id`/`actor_user_id` nullable + `ondelete="SET NULL"`: el rastro de
    auditoría se conserva aunque se borre el tenant o el usuario actor.
    """

    __tablename__ = "audit_log"

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String, nullable=False)
    target: Mapped[str] = mapped_column(String, nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class Subscription(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`subscriptions(tenant_id, stripe_customer_id, stripe_subscription_id, plan_key,
    status, current_period_end)` — facturación (Stripe)."""

    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("stripe_subscription_id", name="uq_subscriptions_stripe_subscription_id"),
    )

    stripe_customer_id: Mapped[str] = mapped_column(String, nullable=False)
    stripe_subscription_id: Mapped[str] = mapped_column(String, nullable=False)
    # Referencia lógica a `edecan_schemas.plans.PLANES` (sin FK de base de
    # datos, igual que `tenants.plan_key`).
    plan_key: Mapped[str] = mapped_column(String, nullable=False)
    # Vocabulario de Stripe (active/trialing/past_due/canceled/...), no pinned
    # en ARCHITECTURE.md — se deja como texto abierto a propósito.
    status: Mapped[str] = mapped_column(String, nullable=False)
    current_period_end: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


# ---------------------------------------------------------------------------
# v2 (ROADMAP_V2.md §7.4, dueño WP-V2-01) — todas tenant-scoped (RLS), ninguna
# excepción declarada en §7.4 ("salvo indicación" — ninguna de las 14 trae
# una). Ver la nota de nullability/defaults en el docstring del módulo.
# ---------------------------------------------------------------------------


class AgentMission(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`agent_missions(user_id, objetivo, status, plan nullable, resultado
    nullable, presupuesto, error nullable)` — misión por objetivo (WP-V2-06)."""

    __tablename__ = "agent_missions"
    __table_args__ = (
        _enum_check(
            "status",
            ("planning", "running", "waiting_confirmation", "done", "error", "cancelled"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    objetivo: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="planning")
    plan: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    resultado: Mapped[str | None] = mapped_column(Text, nullable=True)
    presupuesto: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AgentStep(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`agent_steps(mission_id, seq, agente, instruccion, status, resultado
    nullable, usage nullable)` — un paso ejecutado por un perfil de agente
    (ROADMAP_V2.md §7.9) dentro de una `AgentMission`."""

    __tablename__ = "agent_steps"
    __table_args__ = (
        _enum_check(
            "status",
            ("pending", "running", "waiting_confirmation", "done", "error", "skipped"),
        ),
    )

    mission_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agent_missions.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    agente: Mapped[str] = mapped_column(String, nullable=False)
    instruccion: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="pending")
    resultado: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage: Mapped[Any | None] = mapped_column(JSONB, nullable=True)


class Automation(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`automations(user_id, nombre, descripcion, trigger, accion, enabled,
    next_run_at nullable, last_run_at nullable)` — regla disparador→acción
    (WP-V2-07). `trigger`/`accion` son la forma pinned en
    `edecan_schemas.automations.TriggerDef`/`AccionDef`."""

    __tablename__ = "automations"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    nombre: Mapped[str] = mapped_column(String, nullable=False)
    descripcion: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    # Nombre de columna pinned tal cual en ROADMAP_V2.md §7.4 ("trigger"): es
    # palabra reservada de SQL, pero SQLAlchemy la detecta y la cita
    # automáticamente en el DDL/DML de Postgres (no requiere `"trigger"` a mano).
    trigger: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    accion: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    next_run_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class AutomationRun(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`automation_runs(automation_id, status, detalle, started_at,
    finished_at nullable)` — una corrida auditada de una `Automation`."""

    __tablename__ = "automation_runs"
    __table_args__ = (_enum_check("status", ("running", "done", "error", "waiting_confirmation")),)

    automation_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("automations.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="running")
    detalle: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class Device(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`devices(user_id, nombre, plataforma, kind, status, last_seen_at
    nullable, fingerprint nullable, push_token nullable, push_platform
    nullable)` — dispositivo emparejado (companion de escritorio hoy;
    `mobile` es el asiento reservado para las apps nativas P2, ROADMAP_V2.md
    §6.1).

    `push_token`/`push_platform` son de v5 (`ARCHITECTURE.md` §14, migración
    `0007_v5_expansion`, dueño real WP-V5-13, flag `notifications.push`):
    token de push nativo (APNs/FCM) del dispositivo y qué plataforma lo emitió
    (`apns`/`fcm`), ambos nullable — un `companion`/`mobile` recién emparejado
    no trae push todavía, lo registra después vía un endpoint dedicado que
    construye ese WP. Sin CHECK en `push_platform`: vocabulario abierto a
    propósito, mismo criterio que `stock_moves.motivo` (ver docstring del
    módulo)."""

    __tablename__ = "devices"
    __table_args__ = (
        _enum_check("kind", ("companion", "mobile")),
        _enum_check("status", ("active", "revoked")),
        Index(
            "uq_devices_pairing_secret_hash",
            "pairing_secret_hash",
            unique=True,
            postgresql_where=text("pairing_secret_hash IS NOT NULL"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    nombre: Mapped[str] = mapped_column(String, nullable=False)
    plataforma: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="active")
    last_seen_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    fingerprint: Mapped[str | None] = mapped_column(String, nullable=True)
    push_token: Mapped[str | None] = mapped_column(String, nullable=True)
    push_platform: Mapped[str | None] = mapped_column(String, nullable=True)
    # Solo el hash del secreto durable emitido por el emparejamiento QR. El
    # secreto original nunca sale del Keystore/Keychain del teléfono.
    pairing_secret_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    paired_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class RemoteSession(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`remote_sessions(user_id, device_id nullable, kind default 'view',
    status, started_at nullable, ended_at nullable, frames_count)` —
    prototipo SOLO-VISTA de WP-V2-09 (guardrail ROADMAP_V2.md §8.2: exige
    aprobación explícita en el companion antes de `status="active"`)."""

    __tablename__ = "remote_sessions"
    __table_args__ = (_enum_check("status", ("pending", "active", "ended", "denied")),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("devices.id", ondelete="SET NULL"), nullable=True
    )
    # Sin CHECK: vocabulario abierto a propósito (ver docstring de
    # `edecan_schemas.devices.RemoteSessionOut.kind`) — hoy solo se usa "view".
    kind: Mapped[str] = mapped_column(String, nullable=False, server_default="view")
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="pending")
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    frames_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))


class Order(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`orders(user_id, kind, status, descripcion, monto nullable, moneda,
    simbolo nullable, lado nullable, cantidad nullable, meta, confirmed_at
    nullable, executed_at nullable)` — WP-V2-10.

    GUARDRAIL NO NEGOCIABLE (ROADMAP_V2.md §8.1, ver también
    `edecan_schemas.commerce`): nace SIEMPRE en `status="draft"`
    (`server_default`, nunca lo decide el caller) y hoy el único estado de
    ejecución posible es `executed_paper` — ninguna fila de esta tabla mueve
    dinero real por sí sola.
    """

    __tablename__ = "orders"
    __table_args__ = (
        _enum_check("kind", ("payment", "purchase", "trade")),
        _enum_check("status", ("draft", "confirmed", "executed_paper", "cancelled", "expired")),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="draft")
    descripcion: Mapped[str] = mapped_column(Text, nullable=False)
    monto: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    moneda: Mapped[str] = mapped_column(CHAR(3), nullable=False, server_default="USD")
    simbolo: Mapped[str | None] = mapped_column(String, nullable=True)
    lado: Mapped[str | None] = mapped_column(String, nullable=True)
    cantidad: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class Holding(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`holdings(user_id, simbolo, cantidad, costo_promedio, moneda, kind
    default 'paper')` — posición resultante de `Order` tipo `trade`
    ejecutadas en modo paper (WP-V2-10). Sin CHECK en `kind`: `"paper"` es
    el único valor operativo hoy (ROADMAP_V2.md §7.5 `COMMERCE_MODE`), pero
    el campo queda abierto para no bloquear un modo `"live"` futuro con una
    migración de esquema."""

    __tablename__ = "holdings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    simbolo: Mapped[str] = mapped_column(String, nullable=False)
    cantidad: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    costo_promedio: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    moneda: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False, server_default="paper")


class Budget(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`budgets(user_id, categoria, monto_mensual, moneda default 'USD')` —
    presupuesto mensual por categoría (WP-V2-10, solo lectura/seguimiento)."""

    __tablename__ = "budgets"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    categoria: Mapped[str] = mapped_column(String, nullable=False)
    monto_mensual: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    moneda: Mapped[str] = mapped_column(CHAR(3), nullable=False, server_default="USD")


class Invoice(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`invoices(user_id, numero, cliente_nombre, cliente_email nullable,
    moneda default 'USD', subtotal, impuestos default 0, total, status,
    due_date nullable, pdf_file_id nullable, notas default '')` — WP-V2-12.
    Nace `status="draft"` (mismo criterio que `Order`, aunque aquí no es un
    guardrail de dinero real: una factura en borrador simplemente no se ha
    enviado). `pdf_file_id` referencia el PDF ya generado y subido como fila
    de `files` (v1, ARCHITECTURE.md §10.3) — `ondelete="SET NULL"` conserva
    la factura si el archivo se borra."""

    __tablename__ = "invoices"
    __table_args__ = (_enum_check("status", ("draft", "sent", "paid", "void")),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    numero: Mapped[str] = mapped_column(String, nullable=False)
    cliente_nombre: Mapped[str] = mapped_column(String, nullable=False)
    cliente_email: Mapped[str | None] = mapped_column(String, nullable=True)
    moneda: Mapped[str] = mapped_column(CHAR(3), nullable=False, server_default="USD")
    subtotal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    impuestos: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0")
    )
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="draft")
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    pdf_file_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL"), nullable=True
    )
    notas: Mapped[str] = mapped_column(Text, nullable=False, server_default="")


class InvoiceItem(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`invoice_items(invoice_id, descripcion, cantidad, precio_unitario,
    total)` — línea de una `Invoice`. Sin `user_id` propio (§7.4: no está en
    la tabla pinned) — se resuelve vía `invoice_id` → `Invoice.user_id`."""

    __tablename__ = "invoice_items"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False
    )
    descripcion: Mapped[str] = mapped_column(Text, nullable=False)
    cantidad: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    precio_unitario: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)


class HealthLog(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`health_logs(user_id, kind, valor, notas nullable, registrado_en)` —
    tracking informativo de hábitos/medicamentos (WP-V2-11). Toda tool que
    escriba aquí debe llevar el disclaimer obligatorio de
    ROADMAP_V2.md §8.3 en su respuesta — ese contrato vive en
    `edecan_advisory`, no en esta tabla."""

    __tablename__ = "health_logs"
    __table_args__ = (
        _enum_check("kind", ("medicamento", "ejercicio", "sueno", "agua", "habito", "medida")),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String, nullable=False)
    valor: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    notas: Mapped[str | None] = mapped_column(Text, nullable=True)
    registrado_en: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class LearningProgress(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`learning_progress(user_id, tema, nivel default 'inicial', leccion
    nullable, resultados nullable)` — progreso del tutor (WP-V2-11)."""

    __tablename__ = "learning_progress"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    tema: Mapped[str] = mapped_column(String, nullable=False)
    nivel: Mapped[str] = mapped_column(String, nullable=False, server_default="inicial")
    leccion: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    resultados: Mapped[Any | None] = mapped_column(JSONB, nullable=True)


class UserProfile(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`user_profiles(user_id, resumen default '', datos, version default 1)`
    + `UNIQUE(tenant_id, user_id)` — perfil vivo (WP-V2-13, espejo de
    `edecan_schemas.profile.LiveProfile`)."""

    __tablename__ = "user_profiles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_user_profiles_tenant_id_user_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    resumen: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    datos: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


# ---------------------------------------------------------------------------
# v3 (ARCHITECTURE.md §12e, dueño WP-V3-01)
# ---------------------------------------------------------------------------


class Skill(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`skills(user_id, nombre, slug, source, descripcion default '', version
    nullable, contenido, recursos default '{}', enabled default true,
    trust_tier default 'sin_revisar', capabilities default '[]')` +
    `UNIQUE(tenant_id, slug)` — un "Agent Skill" instalado (marketplace
    abierto skills.sh u origen manual) que el toolkit de Edecán puede
    activar/desactivar (WP-V3-04, `ARCHITECTURE.md` §12e).

    `source` guarda de dónde vino, p. ej. `"owner/repo"` (mismo formato que
    `npx skills add <owner/repo>` de skills.sh) o `"manual"` para uno cargado
    a mano. `contenido` guarda el `SKILL.md` completo tal cual (texto plano,
    sin parsear) — el paquete dueño decide cómo interpretarlo en runtime.
    `recursos` queda abierto (JSONB) para metadatos adicionales del skill
    (archivos auxiliares, scripts, etc.) sin forzar una migración de esquema
    por cada campo nuevo que el estándar "Agent Skills" defina.

    `trust_tier`/`capabilities` son de v5 (`ARCHITECTURE.md` §14, migración
    `0007_v5_expansion`, dueño real un WP de seguimiento que adapta el modelo
    de trust tiers/validación de capacidades de `src/openjarvis/skills/` del
    clon de referencia OpenJarvis, ver `DIRECCION_ACTUAL.md` "Usar OpenJarvis
    más agresivamente" y `NOTICE`): `trust_tier` es texto abierto a propósito
    (sin CHECK, mismo criterio que `tenants.status`/`campaigns.status` — ver
    docstring del módulo) porque ese WP define su propia escala; nace
    `'sin_revisar'` (server_default) para que ninguna skill recién instalada
    quede marcada como confiable por accidente. `capabilities` es la lista
    (JSONB) de capacidades declaradas/detectadas del skill (p. ej. acceso a
    red, ejecución de shell) que ese mismo WP usa para el sandboxing —
    `'[]'` hasta que se parsea."""

    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("tenant_id", "slug", name="uq_skills_tenant_id_slug"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    descripcion: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    version: Mapped[str | None] = mapped_column(Text, nullable=True)
    contenido: Mapped[str] = mapped_column(Text, nullable=False)
    recursos: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    trust_tier: Mapped[str] = mapped_column(Text, nullable=False, server_default="sin_revisar")
    capabilities: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )


# ---------------------------------------------------------------------------
# v4 (ARCHITECTURE.md §13, dueño WP-V4-01) — todas tenant-scoped (RLS), sin
# excepción declarada en §13.
# ---------------------------------------------------------------------------


class Product(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`products(user_id, sku, nombre, descripcion default '', unidad default
    'unidad', precio nullable, costo nullable, stock default 0, stock_minimo
    default 0, activo default true)` + `UNIQUE(tenant_id, sku)` — inventario/
    ERP ligero (WP-V4-06, `ARCHITECTURE.md` §13)."""

    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("tenant_id", "sku", name="uq_products_tenant_id_sku"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    descripcion: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    unidad: Mapped[str] = mapped_column(Text, nullable=False, server_default="unidad")
    precio: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    costo: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    stock: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False, server_default=text("0"))
    stock_minimo: Mapped[Decimal] = mapped_column(
        Numeric(14, 3), nullable=False, server_default=text("0")
    )
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class StockMove(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`stock_moves(user_id, product_id, delta, motivo, nota default '', ref
    nullable)` — movimiento (entrada/salida/ajuste) sobre un `Product`
    (WP-V4-06, `ARCHITECTURE.md` §13). Sin CHECK en `motivo`: vocabulario
    abierto a propósito, mismo criterio que `automations.trigger`/`accion` u
    otras columnas de texto libre sin enum pinned."""

    __tablename__ = "stock_moves"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    delta: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    motivo: Mapped[str] = mapped_column(Text, nullable=False)
    nota: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    ref: Mapped[str | None] = mapped_column(Text, nullable=True)


class AdDraft(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`ad_drafts(user_id, provider default 'meta', nombre, objetivo,
    presupuesto_diario nullable, moneda default 'USD', payload default '{}',
    status default 'draft', external_id nullable, error nullable,
    confirmed_at nullable, pushed_at nullable)` — borrador de campaña
    publicitaria (WP-V4-07, `ARCHITECTURE.md` §13). Mismo espíritu que
    `Order` (v2, WP-V2-10, ROADMAP_V2.md §8.1): nace SIEMPRE en
    `status="draft"` (`server_default`, nunca lo decide el caller) y ninguna
    fila de esta tabla publica/mueve nada real por sí sola — `pushed_at`
    queda `NULL` hasta que un paso explícito y confirmado por el humano la
    empuje de verdad a un proveedor de ads."""

    __tablename__ = "ad_drafts"
    __table_args__ = (
        _enum_check("status", ("draft", "confirmed", "pushed", "error", "cancelled")),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False, server_default="meta")
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    objetivo: Mapped[str] = mapped_column(Text, nullable=False)
    presupuesto_diario: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    moneda: Mapped[str] = mapped_column(CHAR(3), nullable=False, server_default="USD")
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="draft")
    external_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    pushed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# v5 (ARCHITECTURE.md §14, dueño WP-V5-01) — todas tenant-scoped (RLS), sin
# excepción declarada en §14. Ver el docstring del módulo para el criterio de
# tipos/defaults (mismo que v4) y de qué columnas `status` llevan CHECK.
# ---------------------------------------------------------------------------


class Employee(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`employees(user_id, nombre, email nullable, puesto default '',
    salario_mensual nullable, moneda default 'USD', fecha_ingreso nullable,
    status default 'active', meta default '{}')` — RRHH ligero (dueño real un
    WP de seguimiento, `ARCHITECTURE.md` §14, flag `erp.hr`). `status` queda
    texto abierto a propósito (sin vocabulario pinned en §14, ver docstring
    del módulo)."""

    __tablename__ = "employees"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    puesto: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    salario_mensual: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    moneda: Mapped[str] = mapped_column(CHAR(3), nullable=False, server_default="USD")
    fecha_ingreso: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class TimeOff(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`time_off(employee_id FK->employees, kind, desde date, hasta date,
    status default 'pending', notas default '')` — ausencia/vacaciones de un
    `Employee` (`ARCHITECTURE.md` §14, flag `erp.hr`). `status` SÍ lleva
    CHECK (a diferencia de `employees.status`): es una máquina de estados
    explícita de aprobar/rechazar una solicitud, no una etiqueta libre — ver
    docstring del módulo."""

    __tablename__ = "time_off"
    __table_args__ = (_enum_check("status", ("pending", "approved", "rejected", "cancelled")),)

    employee_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    desde: Mapped[date] = mapped_column(Date, nullable=False)
    hasta: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    notas: Mapped[str] = mapped_column(Text, nullable=False, server_default="")


class PayrollRun(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`payroll_runs(user_id, periodo, status default 'draft', total default
    0, moneda default 'USD', notas default '', approved_at nullable)` — una
    corrida de nómina (`ARCHITECTURE.md` §14, flag `erp.hr`). Mismo espíritu
    que `AdDraft`/`Order`: nace SIEMPRE en `status="draft"` (`server_default`,
    nunca lo decide el caller) y ninguna fila de esta tabla paga nada real
    por sí sola — `approved_at` queda `NULL` hasta que un paso explícito y
    confirmado por el humano la apruebe (`DIRECCION_ACTUAL.md`: "dinero real
    nunca se mueve solo"); la tool `preparar_nomina` que la crea es
    `dangerous=True` (§14)."""

    __tablename__ = "payroll_runs"
    __table_args__ = (_enum_check("status", ("draft", "approved", "paid", "cancelled")),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    periodo: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="draft")
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, server_default=text("0"))
    moneda: Mapped[str] = mapped_column(CHAR(3), nullable=False, server_default="USD")
    notas: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    approved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class PayrollItem(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`payroll_items(payroll_run_id FK->payroll_runs, employee_id
    FK->employees, bruto, deducciones default 0, neto)` — la línea de un
    `Employee` dentro de un `PayrollRun` (`ARCHITECTURE.md` §14, flag
    `erp.hr`)."""

    __tablename__ = "payroll_items"

    payroll_run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("payroll_runs.id", ondelete="CASCADE"), nullable=False
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    bruto: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    deducciones: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0")
    )
    neto: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)


class VoiceConsent(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`voice_consents(user_id, voice_name, provider_voice_id nullable,
    consent_file_id nullable, attestation default false, status default
    'attested', meta default '{}')` — consentimiento de clonación de voz
    (`ARCHITECTURE.md` §14, flag `voice.cloning`). `consent_file_id` NO lleva
    FK (§14 no la pinnea): referencia informativa opcional a un `File` con la
    evidencia de consentimiento, no forzada a nivel de base de datos.
    `attestation` es la afirmación explícita del usuario de que tiene derecho
    a clonar esa voz (propia o con permiso) — mismo espíritu de auditoría
    legal que `Consent` (v1, telefonía), aplicado a voz sintética."""

    __tablename__ = "voice_consents"
    __table_args__ = (_enum_check("status", ("attested", "revoked")),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    voice_name: Mapped[str] = mapped_column(Text, nullable=False)
    provider_voice_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    consent_file_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    attestation: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="attested")
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


# ---------------------------------------------------------------------------
# v6 (ARCHITECTURE.md §15, dueño WP-V6-01) — todas tenant-scoped (RLS), sin
# excepción declarada en §15. Ver el docstring del módulo para el criterio de
# tipos/defaults (mismo que v4/v5) y por qué `status` en ambas tablas SÍ lleva
# CHECK.
# ---------------------------------------------------------------------------


class Meeting(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`meetings(user_id, titulo default '', source_file_id nullable,
    transcript_file_id nullable, resumen nullable, minutos nullable, status
    default 'pending', error nullable, duracion_segundos nullable)` — una
    reunión resumida por la tool `resumir_reunion` (`ARCHITECTURE.md` §15,
    flag `tools.meetings`, dueño real WP-V6-05).

    `source_file_id`/`transcript_file_id` referencian informalmente un `File`
    (audio/video fuente y transcripción, respectivamente) SIN FK — mismo
    criterio que `voice_consents.consent_file_id` (v5). `minutos` (JSONB)
    guarda la minuta estructurada (acuerdos, tareas, participantes) que
    produce el job `process_meeting`; `resumen` es el resumen en texto plano.
    El job mueve `status` de `'pending'` a `'running'` y luego a
    `'done'`/`'error'` (`error` queda el detalle si falla)."""

    __tablename__ = "meetings"
    __table_args__ = (_enum_check("status", ("pending", "running", "done", "error")),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    titulo: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    source_file_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    transcript_file_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    resumen: Mapped[str | None] = mapped_column(Text, nullable=True)
    minutos: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duracion_segundos: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Podcast(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """`podcasts(user_id, titulo, guion nullable, status default 'pending',
    file_id nullable, error nullable)` — un podcast generado vía `POST
    /v1/voz/podcasts` (`ARCHITECTURE.md` §15, flag `tools.podcast`, dueño
    real WP-V6-04, router `voz_avanzada`).

    A diferencia de `meetings.titulo` (default `''`), `titulo` aquí es
    obligatorio SIN default: el endpoint que crea la fila exige el título en
    el request, no lo completa perezosamente un job de fondo. `guion` (JSONB)
    guarda el guion/script del podcast; `file_id` referencia informalmente el
    `File` de audio resultante (mismo criterio sin-FK que
    `meetings.source_file_id`/`voice_consents.consent_file_id`). El job
    `generate_podcast` (v5, `ARCHITECTURE.md` §14) mueve `status` de
    `'pending'` a `'running'` y luego a `'done'`/`'error'` — mismo vocabulario
    que `meetings.status`."""

    __tablename__ = "podcasts"
    __table_args__ = (_enum_check("status", ("pending", "running", "done", "error")),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    titulo: Mapped[str] = mapped_column(Text, nullable=False)
    guion: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    file_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# v0.4 — telefonía como canal de la misma conversación.
# ---------------------------------------------------------------------------


class PhoneAgentTemplate(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """Persona y objetivo reutilizables para una llamada saliente.

    Las llamadas copian estos campos al prepararse. La FK queda solo como
    procedencia: borrar o editar la plantilla nunca reescribe el historial.
    """

    __tablename__ = "phone_agent_templates"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "user_id",
            "name",
            name="uq_phone_agent_templates_tenant_user_name",
        ),
        Index(
            "uq_phone_agent_templates_default",
            "tenant_id",
            "user_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    agent_name: Mapped[str] = mapped_column(String, nullable=False)
    persona_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    default_goal: Mapped[str] = mapped_column(Text, nullable=False)
    opening_message: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    knowledge_context: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    required_information: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


class PhoneCall(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """Una llamada entrante/saliente ligada a `Conversation(channel='phone')`."""

    __tablename__ = "phone_calls"
    __table_args__ = (
        _enum_check("direction", ("incoming", "outgoing")),
        _enum_check(
            "status",
            (
                "draft",
                "confirmed",
                "queued",
                "ringing",
                "in_progress",
                "completed",
                "failed",
                "busy",
                "no_answer",
                "cancelled",
            ),
        ),
        UniqueConstraint("provider_call_sid", name="uq_phone_calls_provider_call_sid"),
        UniqueConstraint("tenant_id", "id", name="uq_phone_calls_tenant_id_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    from_e164: Mapped[str] = mapped_column(Text, nullable=False)
    to_e164: Mapped[str] = mapped_column(Text, nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    agent_template_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("phone_agent_templates.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    agent_template_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    opening_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="draft")
    provider: Mapped[str] = mapped_column(Text, nullable=False, server_default="twilio")
    provider_call_sid: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    summary_generated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    summary_push_attempted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )


class PhoneCallEvent(IDMixin, TenantScopedMixin, TimestampMixin, Base):
    """Evento inmutable de proveedor, consentimiento o transcripción."""

    __tablename__ = "phone_call_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "call_id"],
            ["phone_calls.tenant_id", "phone_calls.id"],
            name="fk_phone_call_events_tenant_call",
            ondelete="CASCADE",
        ),
    )

    call_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    occurred_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Registro explícito de todas las tablas — útil para tests de "import de
# modelos" y para que `alembic`/scripts puedan iterar `Base.metadata` sin
# tener que enumerar clases a mano en más de un lugar.
# ---------------------------------------------------------------------------

ALL_MODELS: tuple[type[Base], ...] = (
    Tenant,
    User,
    TenantKey,
    Membership,
    Persona,
    Conversation,
    Message,
    MemoryItem,
    MemoryEdge,
    ConnectorAccount,
    OAuthToken,
    File,
    FileChunk,
    Reminder,
    Contact,
    Transaction,
    Campaign,
    CampaignTarget,
    Consent,
    Job,
    UsageEvent,
    AuditLog,
    Subscription,
    # --- v2 (ROADMAP_V2.md §7.4) ---------------------------------------------
    AgentMission,
    AgentStep,
    Automation,
    AutomationRun,
    Device,
    RemoteSession,
    Order,
    Holding,
    Budget,
    Invoice,
    InvoiceItem,
    HealthLog,
    LearningProgress,
    UserProfile,
    # --- v3 (ARCHITECTURE.md §12e) -------------------------------------------
    Skill,
    # --- v4 (ARCHITECTURE.md §13) --------------------------------------------
    Product,
    StockMove,
    AdDraft,
    # --- v5 (ARCHITECTURE.md §14) --------------------------------------------
    Employee,
    TimeOff,
    PayrollRun,
    PayrollItem,
    VoiceConsent,
    # --- v6 (ARCHITECTURE.md §15) --------------------------------------------
    Meeting,
    Podcast,
    # --- v0.4 (telefonía conversacional OSS) ----------------------------------
    PhoneAgentTemplate,
    PhoneCall,
    PhoneCallEvent,
)
"""Las 23 tablas de v1 (`ARCHITECTURE.md` §10.3) + las 14 de v2
(`ROADMAP_V2.md` §7.4) + la 1 de v3 (`ARCHITECTURE.md` §12e) + las 3 de v4
(`ARCHITECTURE.md` §13) + las 5 de v5 (`ARCHITECTURE.md` §14) + las 2 de v6
(`ARCHITECTURE.md` §15) + 3 de telefonía v0.4 = 51 tablas. Las globales sin RLS (`Tenant`, `User`,
`TenantKey`) van agrupadas primero, como en la sección "Globales (sin RLS)"
de arriba; el resto respeta el mismo orden relativo en que aparecen en
§10.3, las 14 de v2 van a continuación en el mismo orden en que aparecen en
§7.4, `Skill` (única tabla v3) sigue, `Product`/`StockMove`/`AdDraft` (v4,
mismo orden que §13) continúan, `Employee`/`TimeOff`/`PayrollRun`/
`PayrollItem`/`VoiceConsent` (v5, mismo orden que §14) siguen, y
`Meeting`/`Podcast` (v6, mismo orden que §15) siguen, y las 3 tablas de
telefonía OSS (`PhoneAgentTemplate`, `PhoneCall`, `PhoneCallEvent`) cierran
la tupla."""

# Tablas SIN Row-Level Security (globales) — usado por la migración y por
# `test_rls.py` para saber qué tablas NO debe verificar aislamiento.
GLOBAL_TABLES: frozenset[str] = frozenset({"tenants", "users", "tenant_keys"})

# Tablas CON Row-Level Security (todas las demás).
RLS_TABLES: frozenset[str] = frozenset(m.__tablename__ for m in ALL_MODELS) - GLOBAL_TABLES
