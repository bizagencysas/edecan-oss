"""0029_social_drafts_automation_timezone

Dos cambios de esquema que van juntos EN UNA SOLA revisión a propósito: dos
revisiones creadas en paralelo se pelean el mismo `down_revision` y parten la
cadena en dos cabezas (`alembic upgrade head` falla con "Multiple head
revisions"). Son independientes entre sí, pero ninguna de las dos justifica
por sí sola el riesgo de una rama.

1. `social_drafts` — el cable que hoy falta para que "Aprobar y publicar"
   publique de verdad. La card ya se pinta con el botón (`ApproveDraftAction`,
   `packages/schemas/edecan_schemas/chat.py`), el endpoint que publica ya
   existe y ya sabe de `org_urn` + imagen privada
   (`POST /v1/content/social/publish`), pero entre los dos no hay NADA que
   traduzca el `draft_id` de la tarjeta a texto+imagen+destino: el teléfono
   toca el botón y recibe un error. Esta tabla es esa traducción, y es la
   razón por la que el `draft_id` puede "revalidarse server-side contra el
   tenant" como promete el docstring de `ApproveDraftAction` — sin fila con
   `tenant_id`, esa revalidación no tiene contra qué correr.

   Molde: `ad_drafts` (v4, `0006_v4_expansion`). Mismo espíritu: la fila nace
   en el estado inerte (`status='borrador'`, `server_default`, nunca lo decide
   el caller) y por sí sola no publica nada; `published_provider_id` queda
   NULL hasta que un paso explícito y confirmado por el humano la empuje de
   verdad a la red social.

2. `automations.timezone` — para que el cron dispare en la hora del dueño y no
   en UTC. Hoy `compute_next_run` resuelve todo en UTC, así que una
   automatización sembrada con `BYHOUR=9` se dispara a las 4:00 a.m. en
   Bogotá. En un producto multi-tenant esto NO puede ser una variable de
   entorno del proceso (un solo huso para todos los tenants): va como columna
   de la fila. `server_default='UTC'` mantiene el comportamiento EXACTO de hoy
   para todas las filas existentes — esta migración por sí sola no mueve
   ningún horario; el cambio de conducta llega cuando el motor lea la columna.

Revision ID: 0029_social_drafts_tz
Revises: 0028_job_create_linkedin_post
Create Date: 2026-07-30 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029_social_drafts_tz"
down_revision: str | None = "0028_job_create_linkedin_post"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "app_user"

# Tabla tenant-scoped nueva que esta migración crea; la lee
# `packages/db/tests/test_migration_rls_tables.py` para cruzarla contra
# `edecan_db.models.RLS_TABLES` y que ninguna tabla nueva se quede sin política
# `tenant_isolation` en silencio.
RLS_TABLES: tuple[str, ...] = ("social_drafts",)


def upgrade() -> None:
    # ========================================================================
    # 1) social_drafts
    # ========================================================================
    op.create_table(
        "social_drafts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # `draft_id` es TEXTO, no UUID: es el `card_id` que el handler ya
        # calcula para la tarjeta (p. ej. `linkedin-3f9a1c22`) y que viaja al
        # teléfono dentro del botón. Guardarlo tal cual evita un segundo
        # identificador que mantener sincronizado — el id que el usuario tocó
        # es literalmente el que busca el endpoint.
        sa.Column("draft_id", sa.Text(), nullable=False),
        # Sin CHECK a propósito: el catálogo de redes es DATO del producto y
        # crece sin migración (`x` ya tiene conector). Mismo criterio que
        # `conversations.chat_model` en `0027`: la validación vive en la API,
        # contra el catálogo vigente, no congelada en el esquema.
        sa.Column("platform", sa.Text(), nullable=False),
        # Destino (perfil personal vs. página de organización), resuelto por
        # `_resolve_destination`. NOT NULL y SIN server_default a propósito:
        # publicar en el destino equivocado es un error caro y silencioso, así
        # que el escritor está obligado a decir a dónde va — un INSERT
        # incompleto revienta aquí en vez de mandar a la página un texto que
        # era para el perfil personal.
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        # Referencia al PNG ya subido como fila de `files`. `SET NULL` (mismo
        # criterio que `invoices.pdf_file_id`) porque el borrador sigue siendo
        # publicable como texto si la imagen se borra; un puntero colgante, en
        # cambio, haría fallar la publicación con un error de S3 imposible de
        # leer para el dueño.
        sa.Column(
            "image_file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("files.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="borrador"),
        # Id que devuelve la red social al publicar (el URN de LinkedIn). Es
        # también el candado contra el doble post: con fila ya en `publicado`
        # y este campo lleno, un segundo toque del botón se responde con lo ya
        # publicado en vez de publicarlo otra vez.
        sa.Column("published_provider_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # Vocabulario en español, igual que el resto de la capa creativa. SÍ
        # lleva CHECK (a diferencia de `platform`) porque es una máquina de
        # estados del dominio, no un catálogo: mismo criterio que
        # `time_off.status`/`ad_drafts.status`.
        sa.CheckConstraint(
            "status IN ('borrador', 'publicado', 'rechazado')",
            name="status",
        ),
        # La unicidad es POR TENANT, no global: dos tenants pueden generar el
        # mismo `card_id` corto sin pisarse. Y es lo que hace idempotente al
        # escritor — si el handler reintenta, el `ON CONFLICT` lo detecta en
        # vez de dejar dos borradores que el botón no sabría distinguir.
        sa.UniqueConstraint("tenant_id", "draft_id", name="uq_social_drafts_tenant_id_draft_id"),
    )
    op.create_index("ix_social_drafts_tenant_id", "social_drafts", ["tenant_id"])

    # Red de seguridad: `0001_initial` dejó corriendo un `ALTER DEFAULT
    # PRIVILEGES ... TO app_user`, pero ese default está atado al ROL que corrió
    # aquella migración. Si esta corre con otro rol, el default no aplica y la
    # tabla nacería invisible para `app_user` (o sea, para toda la API). Mismo
    # GRANT explícito que `0016_phone_agent_templates`.
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE social_drafts TO {APP_ROLE}")

    # Row-Level Security. SIN `FORCE ROW LEVEL SECURITY`, igual que
    # `0001_initial` y toda la cadena: el dueño de las tablas debe seguir
    # bypasseando RLS porque así se conecta el worker (`session_factory(None)`,
    # ver `edecan_db.session.get_session`) — y es JUSTO el worker quien escribe
    # esta tabla desde el handler `create_linkedin_post`. Con `FORCE`, el
    # borrador no se podría ni guardar.
    #
    # Sin `WITH CHECK` explícito: cuando se omite, Postgres usa la misma
    # expresión de `USING` para validar las filas nuevas, así que el INSERT
    # queda igual de restringido.
    #
    # Sobre qué pasa si NADIE fijó `app.tenant_id`: falla cerrado, pero de dos
    # maneras distintas según la conexión, y conviene saberlo para no perseguir
    # un fantasma leyendo un log. En una conexión que nunca lo fijó,
    # `current_setting(..., true)` devuelve NULL, la comparación nunca es
    # verdadera y la consulta devuelve CERO filas. En una conexión REUSADA del
    # pool que ya sirvió a otro request, en cambio, el `SET LOCAL` de
    # `edecan_db.session` dejó el GUC "definido pero vacío" al cerrar aquella
    # transacción, así que `current_setting` devuelve `''` y el `::uuid`
    # revienta con `invalid input syntax for type uuid: ""`. Las dos son
    # seguras (jamás devuelven filas de otro tenant); la segunda es ruidosa.
    # Verificado contra Postgres 17 al escribir esto. Esto NO es propio de esta
    # tabla: es la misma expresión de las 12 políticas de la cadena desde
    # `0001_initial`, y se mantiene idéntica A PROPÓSITO — una tabla con su
    # propia variante de RLS es peor que una consistente con todas las demás.
    op.execute("ALTER TABLE social_drafts ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON social_drafts "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )

    # ========================================================================
    # 2) automations.timezone
    # ========================================================================
    # El `server_default` hace el backfill solo: Postgres rellena las filas
    # existentes con 'UTC' en el mismo ALTER, así que la columna puede nacer
    # NOT NULL sin un UPDATE aparte y sin ventana de esquema inconsistente.
    #
    # Sin CHECK: el valor es un nombre IANA ('America/Bogota') y esa base de
    # datos de husos cambia sola con el sistema operativo. Congelar una lista
    # en el esquema obligaría a una migración cada vez que el mundo mueve un
    # huso; la validación va en la app, con `ZoneInfo`.
    op.add_column(
        "automations",
        sa.Column("timezone", sa.Text(), nullable=False, server_default="UTC"),
    )


def downgrade() -> None:
    op.drop_column("automations", "timezone")

    op.execute("DROP POLICY IF EXISTS tenant_isolation ON social_drafts")
    # Sin REVOKE: mismo motivo que las migraciones previas — `DROP TABLE` ya
    # retira por sí solo los grants de esta tabla.
    op.drop_index("ix_social_drafts_tenant_id", table_name="social_drafts")
    op.drop_table("social_drafts")
