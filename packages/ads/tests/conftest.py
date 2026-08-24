"""Fixtures compartidas de `edecan_ads` (ver `ARCHITECTURE.md` §10.1, §10.15).

Fakes deliberadamente ligeros, por duck typing: ningún test de este paquete
importa `edecan_db` ni `edecan_api` para construir sus dobles — `ctx.session`/
`ctx.vault` se completan con objetos locales que solo implementan lo que este
paquete realmente usa, y `ctx` en sí es un `SimpleNamespace` (no
`edecan_core.ToolContext`). Mismo patrón que `packages/commerce/tests/conftest.py`
y `packages/creative/tests/conftest.py`, duplicado a propósito.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest


class FakeResult:
    """Imita lo mínimo de `sqlalchemy.engine.Result` que usa este paquete."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> FakeResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)


@dataclass
class FakeSession:
    """`ctx.session` falso: cada `execute()` consume la siguiente respuesta
    programada (una lista de filas-dict, en el orden exacto en que el código
    bajo prueba las pide) y registra `(sql, params)` en `llamadas`. `flush()`
    es un no-op registrado en `flushes`."""

    respuestas: list[list[dict[str, Any]]] = field(default_factory=list)
    llamadas: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    flushes: int = 0

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> FakeResult:
        self.llamadas.append((str(stmt), dict(params or {})))
        filas = self.respuestas.pop(0) if self.respuestas else []
        return FakeResult(filas)

    async def flush(self) -> None:
        self.flushes += 1


@dataclass
class FakeVault:
    """`ctx.vault` falso (`providers.get_tenant_ads_provider`): devuelve
    siempre el mismo `bundle` (o `None` si no hay cuenta conectada) y
    registra cada `get()` en `llamadas`."""

    bundle: Any = None
    llamadas: list[tuple[Any, Any]] = field(default_factory=list)

    async def get(self, tenant_id: Any, connector_account_id: Any) -> Any:
        self.llamadas.append((tenant_id, connector_account_id))
        return self.bundle


def _fake_settings(**overrides: Any) -> SimpleNamespace:
    return SimpleNamespace(**overrides)


@pytest.fixture
def make_session():
    """Factory de `FakeSession`: `make_session([[{"id": "..."}], []])`."""

    def _make_session(respuestas: list[list[dict[str, Any]]] | None = None) -> FakeSession:
        return FakeSession(respuestas=list(respuestas or []))

    return _make_session


@pytest.fixture
def fake_settings():
    """Factory de `ctx.settings` falso: `fake_settings(CAMPO="valor")`."""

    return _fake_settings


@pytest.fixture
def make_vault():
    """Factory de `FakeVault`: `make_vault(bundle=SimpleNamespace(access_token="x"))`."""

    def _make_vault(bundle: Any = None) -> FakeVault:
        return FakeVault(bundle=bundle)

    return _make_vault


@pytest.fixture
def make_ctx():
    """Factory de un `ToolContext` falso (`SimpleNamespace` duck-typed, ver
    arriba). Sin argumentos, cada dependencia se rellena con un fake vacío
    (`vault=None` por defecto: `get_tenant_ads_provider` lo trata como "sin
    credencial bring-your-own del tenant" y cae a `StubAdsProvider`)."""

    def _make_ctx(
        *,
        session: Any = None,
        settings: Any = None,
        vault: Any = None,
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
            vault=vault,
            extras=extras if extras is not None else {},
        )

    return _make_ctx
