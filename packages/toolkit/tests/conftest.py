"""Fixtures compartidas de `edecan_toolkit` (ver `ARCHITECTURE.md` §10.1, §10.15).

Fakes deliberadamente ligeros, por duck typing: ningún test de este paquete
importa `edecan_db`, `edecan_llm` ni `edecan_connectors` para construir sus
dobles — `ctx.session` / `ctx.vault` / `ctx.llm` se completan con objetos
locales que solo implementan lo que las tools realmente usan, y `ctx` en sí es
un `SimpleNamespace` (no `edecan_core.ToolContext`): las tools acceden a sus
atributos por duck typing, así que tampoco hace falta esa importación aquí.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest


class FakeResult:
    """Imita lo mínimo de `sqlalchemy.engine.Result` que usa el toolkit."""

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
    programada (una lista de filas-dict) y registra `(sql, params)` en `llamadas`.
    """

    respuestas: list[list[dict[str, Any]]] = field(default_factory=list)
    llamadas: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> FakeResult:
        self.llamadas.append((str(stmt), dict(params or {})))
        filas = self.respuestas.pop(0) if self.respuestas else []
        return FakeResult(filas)


@dataclass
class FakeVault:
    """`ctx.vault` falso: devuelve siempre el mismo `bundle` (o `None` si no
    hay cuenta conectada) y registra cada `get()`/`put()` en `llamadas`/`puts`.
    """

    bundle: Any = None
    llamadas: list[tuple[Any, Any]] = field(default_factory=list)
    puts: list[tuple[Any, Any, Any]] = field(default_factory=list)

    async def get(self, tenant_id: Any, connector_account_id: Any) -> Any:
        self.llamadas.append((tenant_id, connector_account_id))
        return self.bundle

    async def put(self, tenant_id: Any, connector_account_id: Any, bundle: Any) -> None:
        self.puts.append((tenant_id, connector_account_id, bundle))
        self.bundle = bundle


@dataclass
class FakeLLM:
    """`ctx.llm` falso: imita `edecan_llm.router.LLMRouter.complete(alias,
    tenant_flags, req)` sin red ni Pydantic — devuelve un `SimpleNamespace`
    con `.text`, suficiente para lo que lee `generar_contenido`.
    """

    texto: str = "contenido de prueba"
    llamadas: list[tuple[str, dict[str, Any], Any]] = field(default_factory=list)

    async def complete(self, alias: str, tenant_flags: dict[str, Any], req: Any) -> SimpleNamespace:
        self.llamadas.append((alias, tenant_flags, req))
        return SimpleNamespace(text=self.texto, tool_calls=[], usage=None, stop_reason="end")


def _fake_settings(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "SEARCH_PROVIDER": "stub",
        "BRAVE_API_KEY": None,
        "TAVILY_API_KEY": None,
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
def make_vault():
    """Factory de `FakeVault`: `make_vault(bundle=SimpleNamespace(access_token="x"))`."""

    def _make_vault(bundle: Any = None) -> FakeVault:
        return FakeVault(bundle=bundle)

    return _make_vault


@pytest.fixture
def make_llm():
    """Factory de `FakeLLM`: `make_llm(texto="...")`."""

    def _make_llm(texto: str = "contenido de prueba") -> FakeLLM:
        return FakeLLM(texto=texto)

    return _make_llm


@pytest.fixture
def fake_settings():
    """Factory de `ctx.settings` falso: `fake_settings(SEARCH_PROVIDER="brave", ...)`."""

    return _fake_settings


@pytest.fixture
def make_ctx():
    """Factory de un `ToolContext` falso (`SimpleNamespace` duck-typed, ver
    arriba). Sin argumentos, cada dependencia se rellena con un fake vacío.
    """

    def _make_ctx(
        *,
        session: Any = None,
        vault: Any = None,
        llm: Any = None,
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
            llm=llm if llm is not None else FakeLLM(),
            vault=vault if vault is not None else FakeVault(),
            extras=extras if extras is not None else {},
        )

    return _make_ctx
