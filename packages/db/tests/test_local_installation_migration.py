"""Contrato de la migración singleton para instalaciones locales existentes."""

from __future__ import annotations

from pathlib import Path


def test_migration_backfills_oldest_owner_without_deleting_tenants() -> None:
    source = (
        Path(__file__).parents[1] / "alembic/versions/0014_local_installation_owner.py"
    ).read_text(encoding="utf-8")

    assert 'down_revision: str | None = "0013_mobile_pairing"' in source
    assert '"local_installation"' in source
    assert "ORDER BY m.created_at ASC, m.id ASC" in source
    assert "ON CONFLICT (installation_key) DO NOTHING" in source
    assert "DELETE FROM" not in source.upper()
    assert "UPDATE tenants" not in source
