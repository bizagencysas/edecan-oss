from __future__ import annotations

import importlib.util
from pathlib import Path

from edecan_db.models import Base


def test_social_editorial_profile_model_is_tenant_scoped_and_unique():
    table = Base.metadata.tables["social_editorial_profiles"]
    assert {"tenant_id", "user_id", "platform", "config", "version"} <= set(table.columns.keys())
    assert table.columns["config"].nullable is False
    assert any(
        constraint.name == "uq_social_editorial_profiles_owner_platform"
        for constraint in table.constraints
    )


def test_social_editorial_profile_migration_follows_phone_profiles():
    path = Path(__file__).parents[1] / "alembic/versions/0025_social_editorial_profiles.py"
    spec = importlib.util.spec_from_file_location("social_editorial_profiles_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "0025_social_editorial"
    assert migration.down_revision == "0024_phone_agent_profiles"
