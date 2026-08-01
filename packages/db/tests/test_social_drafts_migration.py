"""Forma de `social_drafts` + `automations.timezone` (`0029_social_drafts_tz`).

No abre conexión: inspecciona `Base.metadata`, igual que `test_db_models.py`.
Lo que se protege aquí es el contrato del que depende el botón "Aprobar y
publicar": si alguien afloja el `UNIQUE(tenant_id, draft_id)` o le pone un
`server_default` a `target`, el bug vuelve en silencio (dos borradores con el
mismo id, o un post que sale por el destino equivocado).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from edecan_db.models import ALL_MODELS, RLS_TABLES, Base, SocialDraft


def test_social_draft_es_tenant_scoped_y_unico_por_draft_id():
    assert SocialDraft in ALL_MODELS
    assert "social_drafts" in RLS_TABLES

    table = Base.metadata.tables["social_drafts"]
    columnas = set(table.columns.keys())
    assert {
        "id",
        "tenant_id",
        "user_id",
        "draft_id",
        "platform",
        "target",
        "text",
        "image_file_id",
        "status",
        "published_provider_id",
        "created_at",
        "updated_at",
    } <= columnas

    assert table.columns["tenant_id"].nullable is False
    assert table.columns["draft_id"].nullable is False
    # Publicar en el destino equivocado es un error caro y silencioso: el
    # escritor está OBLIGADO a decir a dónde va (NOT NULL, sin default).
    assert table.columns["target"].nullable is False
    assert table.columns["target"].server_default is None
    # Lo único opcional: la imagen (un post puede ser solo texto) y el id que
    # devuelve la red al publicar.
    assert table.columns["image_file_id"].nullable is True
    assert table.columns["published_provider_id"].nullable is True

    assert any(
        constraint.name == "uq_social_drafts_tenant_id_draft_id" for constraint in table.constraints
    )
    assert list(table.columns["image_file_id"].foreign_keys)[0].target_fullname == "files.id"


def test_social_draft_nace_inerte():
    # Mismo guardrail que `ad_drafts`/`orders`: la fila nace en el estado que
    # no publica nada, y ese estado NO lo decide el caller.
    columna = Base.metadata.tables["social_drafts"].columns["status"]
    assert columna.server_default is not None
    assert "borrador" in str(columna.server_default.arg)
    check = next(
        constraint
        for constraint in Base.metadata.tables["social_drafts"].constraints
        if constraint.__class__.__name__ == "CheckConstraint"
        and "status" in str(constraint.sqltext)
    )
    for estado in ("borrador", "publicado", "rechazado"):
        assert estado in str(check.sqltext)


def test_automations_tiene_zona_horaria_que_no_mueve_lo_ya_sembrado():
    columna = Base.metadata.tables["automations"].columns["timezone"]
    assert columna.nullable is False
    # 'UTC' de default = estrenar la columna no cambia ni un horario existente;
    # el cambio de conducta llega cuando el motor la lea.
    assert str(columna.server_default.arg) == "UTC"


def test_migracion_0029_encadena_con_la_cabeza_anterior():
    path = Path(__file__).parents[1] / "alembic/versions/0029_social_drafts_automation_timezone.py"
    spec = importlib.util.spec_from_file_location("social_drafts_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "0029_social_drafts_tz"
    assert migration.down_revision == "0028_job_create_linkedin_post"
    assert migration.RLS_TABLES == ("social_drafts",)
