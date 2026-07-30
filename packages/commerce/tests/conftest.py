"""Fixtures compartidas de `edecan_commerce` (ver `ARCHITECTURE.md` §10.1, §10.15).

Fakes deliberadamente ligeros, por duck typing: ningún test de este paquete importa
`edecan_db` ni `edecan_api` para construir sus dobles — `ctx.session` se completa con un
objeto local que solo implementa lo que este paquete realmente usa (`execute()` con una
respuesta programada), y `ctx` en sí es un `SimpleNamespace` (no `edecan_core.ToolContext`).
Mismo patrón que `packages/toolkit/tests/conftest.py`, duplicado a propósito (los tests no
importan paquetes hermanos).
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
    """`ctx.session` (o el `session` explícito de `paper.py`/`budgets.py`) falso: cada
    `execute()` consume la siguiente respuesta programada (una lista de filas-dict, en el
    orden exacto en que el código bajo prueba las pide) y registra `(sql, params)` en
    `llamadas`. `flush()` es un no-op registrado en `flushes` (útil para afirmar cuántas
    veces se llamó)."""

    respuestas: list[list[dict[str, Any]]] = field(default_factory=list)
    llamadas: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    flushes: int = 0

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> FakeResult:
        self.llamadas.append((str(stmt), dict(params or {})))
        filas = self.respuestas.pop(0) if self.respuestas else []
        return FakeResult(filas)

    async def flush(self) -> None:
        self.flushes += 1


def _fake_settings(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "QUOTES_PROVIDER": "stub",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def make_session():
    """Factory de `FakeSession`: `make_session([[{"id": "..."}], []])`."""

    def _make_session(respuestas: list[list[dict[str, Any]]] | None = None) -> FakeSession:
        return FakeSession(respuestas=list(respuestas or []))

    return _make_session


@pytest.fixture
def fake_settings():
    """Factory de `ctx.settings` falso: `fake_settings(QUOTES_PROVIDER="coingecko")`."""

    return _fake_settings


@pytest.fixture
def make_ctx():
    """Factory de un `ToolContext` falso (`SimpleNamespace` duck-typed, ver arriba). Sin
    argumentos, cada dependencia se rellena con un fake vacío."""

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
