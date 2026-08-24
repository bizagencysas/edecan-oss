"""Fixtures compartidas de `edecan_automations` (ver `ARCHITECTURE.md` §10.1, §10.15).

Fakes deliberadamente ligeros, por duck typing — mismo patrón que
`packages/toolkit/tests/conftest.py` (duplicado a propósito, no se importa
entre paquetes hermanos): `ctx` es un `SimpleNamespace` (no
`edecan_core.tools.ToolContext`) y `ctx.session` es un `FakeSession` que solo
implementa lo que este paquete usa de verdad.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest


class FakeResult:
    """Imita lo mínimo de `sqlalchemy.engine.Result` que usa este paquete."""

    def __init__(self, rows: list[dict[str, Any]] | None = None, scalar: Any = None) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def mappings(self) -> FakeResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def scalar_one(self) -> Any:
        return self._scalar


@dataclass
class FakeSession:
    """`ctx.session` falso: cada `execute()` consume la siguiente respuesta
    programada y registra `(sql, params)` en `llamadas`.

    `respuestas` es una lista de `FakeResult` (o de listas de filas-dict, que
    se envuelven automáticamente en un `FakeResult`) — un `execute()` de más
    (sin respuesta programada) devuelve un `FakeResult` vacío en vez de
    reventar, para no acoplar cada test a contar exactamente cuántas queries
    dispara la implementación.
    """

    respuestas: list[Any] = field(default_factory=list)
    llamadas: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> FakeResult:
        self.llamadas.append((str(stmt), dict(params or {})))
        if not self.respuestas:
            return FakeResult()
        siguiente = self.respuestas.pop(0)
        return siguiente if isinstance(siguiente, FakeResult) else FakeResult(siguiente)


@pytest.fixture
def make_session():
    """Factory de `FakeSession`: `make_session([[{"id": "..."}], make_result(scalar=0)])`."""

    def _make_session(respuestas: list[Any] | None = None) -> FakeSession:
        return FakeSession(respuestas=list(respuestas or []))

    return _make_session


@pytest.fixture
def make_result():
    """Factory de `FakeResult`: `make_result(scalar=3)` para un `COUNT(*)`, o
    `make_result(rows=[...])` (equivalente a pasar la lista de filas directo
    a `make_session`, útil solo cuando además hace falta fijar `scalar` en la
    misma respuesta)."""

    def _make_result(rows: list[dict[str, Any]] | None = None, scalar: Any = None) -> FakeResult:
        return FakeResult(rows=rows, scalar=scalar)

    return _make_result


@pytest.fixture
def make_ctx():
    """Factory de un `ToolContext` falso (`SimpleNamespace` duck-typed)."""

    def _make_ctx(
        *,
        session: Any = None,
        extras: dict[str, Any] | None = None,
        tenant_id: UUID | None = None,
        user_id: UUID | None = None,
        flags: dict[str, Any] | None = None,
    ) -> SimpleNamespace:
        base_extras: dict[str, Any] = {"flags": flags or {}}
        if extras:
            base_extras.update(extras)
        return SimpleNamespace(
            tenant_id=tenant_id or uuid4(),
            user_id=user_id or uuid4(),
            session=session if session is not None else FakeSession(),
            settings=SimpleNamespace(),
            llm=SimpleNamespace(),
            vault=SimpleNamespace(),
            extras=base_extras,
        )

    return _make_ctx
