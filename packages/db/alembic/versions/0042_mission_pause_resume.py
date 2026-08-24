"""0042_mission_pause_resume

Agrega el estado durable ``paused`` a las misiones. Pausar conserva el plan y
los checkpoints; reanudar crea un job nuevo que vuelve a pasar por los flags y
los permisos vigentes.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0042_mission_pause_resume"
down_revision: str | None = "0041_mission_archival"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace_check(values: str) -> None:
    op.execute("ALTER TABLE agent_missions DROP CONSTRAINT IF EXISTS ck_agent_missions_status")
    op.execute("ALTER TABLE agent_missions DROP CONSTRAINT IF EXISTS status")
    op.execute(
        "ALTER TABLE agent_missions ADD CONSTRAINT ck_agent_missions_status "
        f"CHECK (status IN ({values}))"
    )


def upgrade() -> None:
    _replace_check(
        "'planning', 'running', 'waiting_confirmation', 'paused', 'done', 'error', 'cancelled'"
    )


def downgrade() -> None:
    op.execute("UPDATE agent_missions SET status = 'cancelled' WHERE status = 'paused'")
    _replace_check("'planning', 'running', 'waiting_confirmation', 'done', 'error', 'cancelled'")
