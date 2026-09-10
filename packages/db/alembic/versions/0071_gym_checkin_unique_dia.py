"""0071_gym_checkin_unique_dia

Backstop de base de datos para la carrera del check-in de gym (hallazgo C4 de
auditoría): `POST /v1/gym/checkin` era read-then-insert (TOCTOU), así que dos
POST concurrentes del mismo día podían persistir dos planes, dos sesiones y dos
check-ins. El arreglo de la capa HTTP (ver `apps/api/edecan_api/routers/gym.py`)
usa `INSERT ... ON CONFLICT DO NOTHING` y reutiliza la fila existente, pero eso
solo funciona si la base de datos IMPIDE el duplicado: aquí se agrega el
`UNIQUE (tenant_id, user_id, fecha)` en `workout_plans` y `gym_checkins`.

Migración ADITIVA con deduplicación defensiva: si el bug TOCTOU ya había
dejado duplicados en una base real, `CREATE UNIQUE ...` fallaría y tumbaría el
arranque del backend (las migraciones corren en el boot). Para no romper datos
existentes:

1. `gym_checkins`: se conserva UN check-in por día, prefiriendo el "si" con
   sesión (session_id NOT NULL) y, en empate, el más reciente; el resto se
   elimina (eran duplicados del bug, no información).
2. `workout_plans`: se conserva UN plan por día — el plan referenciado por la
   sesión del check-in conservado (el que el dueño vio/entrenó), o el más
   reciente si no hay check-in. Antes de eliminar los planes duplicados se
   re-apuntan sus sesiones al plan canónico (la FK `workout_sessions.plan_id`
   es `ON DELETE CASCADE`; sin el re-apunte, borrar el plan borraría sesiones
   reales del historial).

Igual que `0002_twilio_number_global_unique`, esta migración NO crea tablas,
así que no declara `RLS_TABLES` ni toca el guardarraíl de
`test_migration_rls_tables.py`.

Revision ID: 0071_gym_checkin_unique_dia
Revises: 0070_job_outbox_bot_runs
Create Date: 2026-09-09 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0071_gym_checkin_unique_dia"
down_revision: str | None = "0070_job_outbox_bot_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Plan canónico de un día = el plan de la sesión del check-in conservado (si lo
# hay), o el más reciente. La expresión se repite en el UPDATE y el DELETE para
# que ambos usen EXACTAMENTE el mismo orden (cualquier divergencia re-apuntaría
# sesiones a un plan distinto del que se conserva y perdería entrenamientos).
_DEDUP_PLAN_CTE = """
WITH day_checkin_plan AS (
    -- El plan al que apunta la sesión del check-in del día (post-dedup de
    -- gym_checkins hay a lo sumo una fila por día con session_id).
    SELECT gc.tenant_id, gc.user_id, gc.fecha, ws.plan_id
    FROM gym_checkins gc
    JOIN workout_sessions ws ON ws.id = gc.session_id
    WHERE gc.session_id IS NOT NULL
)
"""


def upgrade() -> None:
    # 1. Dedupe gym_checkins: uno por (tenant_id, user_id, fecha).
    #    Preferencia: "si" con sesión > más reciente (created_at, luego id).
    op.execute(
        """
        DELETE FROM gym_checkins
        WHERE id IN (
            SELECT id FROM (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY tenant_id, user_id, fecha
                           ORDER BY (session_id IS NOT NULL) DESC, created_at DESC, id DESC
                       ) AS rn
                FROM gym_checkins
            ) AS ranked
            WHERE rn > 1
        )
        """
    )

    # 2. Dedupe workout_plans: primero re-apunta las sesiones de los planes
    #    duplicados al plan canónico del día (evita el CASCADE de la FK), luego
    #    elimina los planes duplicados ya sin sesiones que los referencien.
    op.execute(
        _DEDUP_PLAN_CTE
        + """
        , canonical AS (
            SELECT p.id AS plan_id,
                   first_value(p.id) OVER (
                       PARTITION BY p.tenant_id, p.user_id, p.fecha
                       ORDER BY
                           CASE WHEN p.id = dcp.plan_id THEN 0 ELSE 1 END,
                           p.created_at DESC,
                           p.id DESC
                   ) AS canonical_id
            FROM workout_plans p
            LEFT JOIN day_checkin_plan dcp
                ON dcp.tenant_id = p.tenant_id
               AND dcp.user_id = p.user_id
               AND dcp.fecha = p.fecha
        )
        UPDATE workout_sessions ws
        SET plan_id = c.canonical_id, updated_at = now()
        FROM canonical c
        WHERE ws.plan_id = c.plan_id
          AND ws.plan_id <> c.canonical_id
        """
    )

    op.execute(
        _DEDUP_PLAN_CTE
        + """
        , ranked AS (
            SELECT p.id,
                   row_number() OVER (
                       PARTITION BY p.tenant_id, p.user_id, p.fecha
                       ORDER BY
                           CASE WHEN p.id = dcp.plan_id THEN 0 ELSE 1 END,
                           p.created_at DESC,
                           p.id DESC
                   ) AS rn
            FROM workout_plans p
            LEFT JOIN day_checkin_plan dcp
                ON dcp.tenant_id = p.tenant_id
               AND dcp.user_id = p.user_id
               AND dcp.fecha = p.fecha
        )
        DELETE FROM workout_plans
        WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
        """
    )

    # 3. Las restricciones que hacen imposible la carrera en adelante.
    op.create_unique_constraint(
        "uq_workout_plans_tenant_id_user_id_fecha",
        "workout_plans",
        ["tenant_id", "user_id", "fecha"],
    )
    op.create_unique_constraint(
        "uq_gym_checkins_tenant_id_user_id_fecha",
        "gym_checkins",
        ["tenant_id", "user_id", "fecha"],
    )


def downgrade() -> None:
    # La deduplicación NO es reversible (los duplicados ya no existen), mismo
    # criterio que otras migraciones de datos. Solo se retiran las constraints.
    op.drop_constraint(
        "uq_gym_checkins_tenant_id_user_id_fecha", "gym_checkins", type_="unique"
    )
    op.drop_constraint(
        "uq_workout_plans_tenant_id_user_id_fecha", "workout_plans", type_="unique"
    )