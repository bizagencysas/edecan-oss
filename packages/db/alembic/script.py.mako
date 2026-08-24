"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
#
# EL `revision` NO PUEDE PASAR DE 32 CARACTERES. `alembic_version.version_num` es
# `varchar(32)`: un id más largo NO falla al escribir esta migración ni al correr los
# tests del paquete -- falla AL ESTAMPARSE en la base real, o sea en el ARRANQUE del
# backend, y como las migraciones corren en el boot de la app instalada el síntoma es
# "la app quedó sin motor", no un test rojo. Pasó el 02-ago-2026 con un id de 33.
# El archivo SÍ puede tener nombre largo; el que tiene que caber es este id.
# Lo vigila `packages/db/tests/test_revision_ids_caben_en_alembic.py`.
revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
