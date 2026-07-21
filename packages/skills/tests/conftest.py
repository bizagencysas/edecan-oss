"""Fixtures compartidas de `edecan_skills` (ver `ARCHITECTURE.md` §10.1, §10.15).

El bloque de `sys.path` de abajo se ejecuta primero (por su efecto secundario: agrega el
propio código fuente de este paquete, `packages/skills/`, a `sys.path`) para que `import
edecan_skills` funcione en un `pytest` corrido solo sobre este paquete, sin depender de que
ya haya corrido `uv sync --all-packages` en el workspace — mismo motivo y misma técnica que
`packages/messaging/tests/conftest.py`/`apps/api/tests/_stub_siblings.py`. Una vez
`packages/skills` queda instalado editable en el entorno compartido, este bloque es un
no-op inocuo (`if ... not in sys.path` ya lo evita duplicar).

Fakes deliberadamente ligeros, por duck typing: ningún test de este paquete importa
`edecan_core`, `edecan_db` ni `sqlalchemy` para construir sus dobles — `ctx.session` se
completa con un objeto local que solo implementa lo que `edecan_skills` realmente usa
(`execute()`/`flush()`), y `ctx` en sí es un `SimpleNamespace` (no `edecan_core.ToolContext`).
"""

from __future__ import annotations

import sys
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

import json  # noqa: E402  (después del shim de sys.path, ver docstring)
import re  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from datetime import UTC, datetime  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from typing import Any  # noqa: E402
from uuid import UUID, uuid4  # noqa: E402

import pytest  # noqa: E402

_SLUG_INVALID_RE = re.compile(r"[^a-z0-9]+")


def _slugify(nombre: str) -> str:
    """Réplica local de `edecan_skills.store.slugify` — a propósito NO se importa (los
    tests no importan el módulo bajo prueba desde el fake que lo simula), son 2 líneas."""
    base = _SLUG_INVALID_RE.sub("-", (nombre or "").strip().lower()).strip("-")
    return base or "skill"


class FakeResult:
    """Imita lo mínimo de `sqlalchemy.engine.Result` que usa `edecan_skills.store`."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> FakeResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return dict(self._rows[0]) if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._rows]


@dataclass
class FakeSession:
    """`ctx.session` falso que entiende (por prefijo SQL + claves de `params`) las queries
    de `edecan_skills.store` — mismo espíritu que `FakeSession` en
    `apps/api/tests/test_missions_router.py`. Guarda las filas en un `dict` por `id` para
    que INSERT/UPDATE/SELECT/DELETE se comporten de forma consistente entre sí dentro de un
    mismo test, sin tener que programar cada respuesta a mano.
    """

    filas: dict[str, dict[str, Any]] = field(default_factory=dict)
    llamadas: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    flushes: int = 0

    def seed_skill(self, *, tenant_id: UUID, user_id: UUID, nombre: str, **overrides: Any) -> dict:
        row = {
            "id": str(uuid4()),
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "nombre": nombre,
            "slug": _slugify(nombre),
            "source": "owner/repo",
            "descripcion": "",
            "version": None,
            "contenido": "cuerpo de la skill",
            "recursos": {},
            "trust_tier": "sin_revisar",
            "capabilities": [],
            "enabled": True,
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
        row.update(overrides)
        self.filas[row["id"]] = row
        return row

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> FakeResult:
        sql = str(stmt)
        params = dict(params or {})
        self.llamadas.append((sql, params))
        primero = sql.strip().split(None, 1)[0].upper()

        if primero == "SELECT" and "id = :id" in sql:
            row = self.filas.get(params.get("id"))
            if row is not None and row["tenant_id"] == params.get("tenant_id"):
                return FakeResult([row])
            return FakeResult([])

        if primero == "SELECT" and "slug = :slug" in sql:
            for row in self.filas.values():
                mismo_tenant = row["tenant_id"] == params.get("tenant_id")
                mismo_slug = row["slug"] == params.get("slug")
                if mismo_tenant and mismo_slug:
                    return FakeResult([row])
            return FakeResult([])

        if primero == "SELECT":
            # `list_skills`: filtra por tenant_id+user_id y, si `solo_enabled=True`, por
            # `enabled = true` (embebido en el SQL, no en `params` — se detecta por
            # substring, igual que arma la query real `store.list_skills`).
            filas = [
                row
                for row in self.filas.values()
                if row["tenant_id"] == params.get("tenant_id")
                and row["user_id"] == params.get("user_id")
            ]
            if "enabled = true" in sql:
                filas = [f for f in filas if f["enabled"]]
            filas.sort(key=lambda r: r["created_at"], reverse=True)
            return FakeResult(filas)

        if primero == "INSERT" and "ON CONFLICT" in sql.upper():
            # `insert_skill`: upsert atómico por `(tenant_id, slug)` — si ya hay una fila
            # con ese par, se actualiza en el sitio (mismos campos que el `DO UPDATE SET`
            # real: contenido/version/descripcion/source/trust_tier/capabilities/
            # updated_at, sin tocar user_id/nombre; `enabled` sigue el mismo `CASE WHEN`
            # que el SQL real: se fuerza a `false` si la fuente nueva trae hallazgos,
            # si no se preserva el `enabled` existente); si no, se comporta como el
            # INSERT de abajo.
            existente = next(
                (
                    row
                    for row in self.filas.values()
                    if row["tenant_id"] == params.get("tenant_id")
                    and row["slug"] == params.get("slug")
                ),
                None,
            )
            if existente is not None:
                existente["contenido"] = params["contenido"]
                existente["version"] = params["version"]
                existente["descripcion"] = params["descripcion"]
                existente["source"] = params["source"]
                existente["recursos"] = json.loads(params["recursos"])
                existente["trust_tier"] = params["trust_tier"]
                existente["capabilities"] = json.loads(params["capabilities"])
                if params["enabled"] is False:
                    existente["enabled"] = False
                existente["updated_at"] = datetime.now(UTC)
                return FakeResult([existente])

        if primero == "INSERT":
            row = {
                "id": str(uuid4()),
                "tenant_id": params["tenant_id"],
                "user_id": params["user_id"],
                "nombre": params["nombre"],
                "slug": params["slug"],
                "source": params["source"],
                "descripcion": params["descripcion"],
                "version": params["version"],
                "contenido": params["contenido"],
                "recursos": json.loads(params.get("recursos") or "{}"),
                "trust_tier": params.get("trust_tier", "sin_revisar"),
                "capabilities": json.loads(params.get("capabilities") or "[]"),
                "enabled": params.get("enabled", True),
            }
            row["created_at"] = datetime.now(UTC)
            row["updated_at"] = datetime.now(UTC)
            self.filas[row["id"]] = row
            return FakeResult([row])

        if primero == "UPDATE":
            row = self.filas.get(params.get("id"))
            if row is None or row["tenant_id"] != params.get("tenant_id"):
                return FakeResult([])
            if "contenido" in params:
                row["contenido"] = params["contenido"]
                row["version"] = params["version"]
                row["descripcion"] = params["descripcion"]
                row["source"] = params["source"]
            if "enabled" in params:
                row["enabled"] = params["enabled"]
            return FakeResult([row])

        if primero == "DELETE":
            row = self.filas.get(params.get("id"))
            if row is not None and row["tenant_id"] == params.get("tenant_id"):
                del self.filas[row["id"]]
                return FakeResult([{"id": row["id"]}])
            return FakeResult([])

        raise AssertionError(f"query inesperada en el fake: {sql} params={params}")

    async def flush(self) -> None:
        self.flushes += 1


@pytest.fixture
def make_session():
    """Factory de `FakeSession` vacía."""

    def _make_session() -> FakeSession:
        return FakeSession()

    return _make_session


def _fake_settings(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "SKILLS_INDEX_URL": "https://skills.sh",
        "BROWSER_TIMEOUT_SECONDS": 20,
        "EDECAN_LOCAL_MODE": True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def fake_settings():
    """Factory de `ctx.settings` falso: `fake_settings(SKILLS_INDEX_URL="https://otro")`."""

    return _fake_settings


@pytest.fixture
def make_ctx():
    """Factory de un `ToolContext` falso (`SimpleNamespace` duck-typed, ver docstring)."""

    def _make_ctx(
        *,
        session: Any = None,
        settings: Any = None,
        extras: dict[str, Any] | None = None,
        tenant_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            tenant_id=tenant_id or uuid4(),
            user_id=user_id or uuid4(),
            session=session if session is not None else FakeSession(),
            settings=settings if settings is not None else _fake_settings(),
            llm=None,
            vault=None,
            extras=extras if extras is not None else {},
        )

    return _make_ctx
