"""Fixtures compartidas.

Importante (`ARCHITECTURE.md` §10.1): los tests de este paquete **no importan
`edecan_schemas`** (paquete hermano) — usan `FakeTokenBundle`, un fake local
que replica la forma de `TokenBundle` (§10.5), sin más dependencia que eso.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest


@dataclass
class FakeTokenBundle:
    """Réplica local (solo para tests) de la forma de `edecan_schemas.TokenBundle`."""

    access_token: str
    refresh_token: str | None = None
    expires_at: object | None = None
    scopes: list = field(default_factory=list)
    token_type: str = "bearer"


@pytest.fixture
def token_bundle() -> FakeTokenBundle:
    return FakeTokenBundle(
        access_token="fake-access-token",
        refresh_token="fake-refresh-token",
        scopes=["scope-a", "scope-b"],
    )
