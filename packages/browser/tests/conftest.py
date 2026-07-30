"""Fixtures compartidas de `edecan_browser` (ver `ARCHITECTURE.md` §10.1, §10.15).

Fakes deliberadamente ligeros, por duck typing: ningún test de este paquete
importa `edecan_core`, `edecan_llm` ni `edecan_toolkit` para construir sus
dobles — `ctx.llm`/`ctx.session`/`ctx.vault` se completan con objetos locales
que solo implementan lo que las tools realmente usan (`FakeSession`/`FakeVault`
abajo son dobles LOCALES de este paquete, no un import de
`packages/toolkit/tests/conftest.py` — `edecan_toolkit` sí es una dependencia
de producción declarada de `edecan_browser`, ver su `pyproject.toml`, pero los
*tests* de un paquete no importan los `tests/` de un hermano, ARCHITECTURE.md
§10.1), y `ctx` en sí es un `SimpleNamespace` (no `edecan_core.ToolContext`).
Los tests SÍ importan `edecan_browser` (el propio paquete bajo prueba, no un
hermano) — eso es normal y necesario.

Dos fixtures `autouse` mantienen los tests deterministas y aislados entre sí:

- `_sin_dns_real`: reemplaza `edecan_browser.policy.resolve_hostname_ips` por
  un resolutor falso que siempre devuelve una IP pública fija — así ningún
  test que navegue un nombre de dominio (ej. `https://tienda.ejemplo.com`)
  depende de DNS real. Los tests de SSRF por dominio la vuelven a reemplazar
  puntualmente con `monkeypatch.setattr` para simular una resolución privada.
- `_cache_robots_fresca`: reemplaza `edecan_browser.policy._CACHE_GLOBAL` por
  una `RobotsCache` nueva antes de cada test, para que la caché de robots.txt
  de un test no contamine a otro que reutilice el mismo dominio de ejemplo
  (los mocks de `respx` son por test; la caché de módulo, sin este fixture,
  no lo sería).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from edecan_browser import policy

_IP_PUBLICA_FALSA = "8.8.8.8"


async def _resolver_falso(hostname: str) -> list[str]:
    return [_IP_PUBLICA_FALSA]


@pytest.fixture(autouse=True)
def _sin_dns_real(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(policy, "resolve_hostname_ips", _resolver_falso)


@pytest.fixture(autouse=True)
def _cache_robots_fresca(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(policy, "_CACHE_GLOBAL", policy.RobotsCache())


class FakeResult:
    """Imita lo mínimo de `sqlalchemy.engine.Result` que usa
    `edecan_toolkit.research.get_tenant_search_provider` (doble LOCAL, ver
    docstring del módulo — no importado de `packages/toolkit/tests/conftest.py`)."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> FakeResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


@dataclass
class FakeSession:
    """`ctx.session` falso: cada `execute()` consume la siguiente respuesta
    programada (una lista de filas-dict) y registra `(sql, params)` en
    `llamadas` — mismo shape que `get_tenant_search_provider` espera."""

    respuestas: list[list[dict[str, Any]]] = field(default_factory=list)
    llamadas: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> FakeResult:
        self.llamadas.append((str(stmt), dict(params or {})))
        filas = self.respuestas.pop(0) if self.respuestas else []
        return FakeResult(filas)


@dataclass
class FakeVault:
    """`ctx.vault` falso: devuelve siempre el mismo `bundle` (o `None` si no
    hay cuenta conectada) y registra cada `get()` en `llamadas`."""

    bundle: Any = None
    llamadas: list[tuple[Any, Any]] = field(default_factory=list)

    async def get(self, tenant_id: Any, connector_account_id: Any) -> Any:
        self.llamadas.append((tenant_id, connector_account_id))
        return self.bundle


@dataclass
class FakeLLM:
    """`ctx.llm` falso: imita `edecan_llm.router.LLMRouter.complete(alias,
    tenant_flags, req)` sin red ni Pydantic.

    `respuestas` es una cola de textos: cada `.complete()` consume la
    siguiente (y se queda en la última si se agotan) — así un test puede
    simular un precio distinto por cada página que `comparar_precios`
    procese y así probar que la tabla queda ordenada de verdad, no solo por
    casualidad del orden de llegada.
    """

    respuestas: list[str] = field(default_factory=lambda: ['{"ok": true}'])
    llamadas: list[tuple[str, dict[str, Any], Any]] = field(default_factory=list)

    async def complete(self, alias: str, tenant_flags: dict[str, Any], req: Any) -> SimpleNamespace:
        indice = min(len(self.llamadas), len(self.respuestas) - 1)
        self.llamadas.append((alias, tenant_flags, req))
        texto = self.respuestas[indice]
        return SimpleNamespace(text=texto, tool_calls=[], usage=None, stop_reason="end")


def _fake_settings(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "BROWSER_FETCH_PROVIDER": "httpx",
        "BROWSER_USER_AGENT": "EdecanBot/1.0",
        "BROWSER_MAX_FETCH_BYTES": 2_000_000,
        "BROWSER_TIMEOUT_SECONDS": 20.0,
        "SEARCH_PROVIDER": "stub",
        "BRAVE_API_KEY": None,
        "TAVILY_API_KEY": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def fake_settings():
    """Factory de `ctx.settings` falso: `fake_settings(BROWSER_MAX_FETCH_BYTES=500)`."""

    return _fake_settings


@pytest.fixture
def make_llm():
    """Factory de `FakeLLM`: `make_llm(["{...}", "{...}"])`."""

    def _make_llm(respuestas: list[str] | None = None) -> FakeLLM:
        return FakeLLM(respuestas=respuestas if respuestas is not None else ['{"ok": true}'])

    return _make_llm


@pytest.fixture
def make_session():
    """Factory de `FakeSession`: `make_session([[{"id": "..."}], []])` — para
    ejercitar `get_tenant_search_provider` de verdad desde `comparar_precios`
    (ver `FakeSession`/`FakeVault` arriba)."""

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
def make_ctx():
    """Factory de un `ToolContext` falso (`SimpleNamespace` duck-typed, ver arriba).

    `session`/`vault` quedan en `None` por defecto (ningún test previo los
    pasaba): `get_tenant_search_provider` (`edecan_toolkit.research`, ya
    llamado por `comparar_precios`) trata `ctx.session is None`/`ctx.vault is
    None` como "sin credencial bring-your-own del tenant" y cae directo a
    `StubSearch` — mismo comportamiento que antes de que `comparar_precios`
    resolviera el proveedor por tenant.
    """

    def _make_ctx(
        *,
        llm: Any = None,
        settings: Any = None,
        session: Any = None,
        vault: Any = None,
        extras: dict[str, Any] | None = None,
        tenant_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            tenant_id=tenant_id or uuid4(),
            user_id=user_id or uuid4(),
            session=session,
            settings=settings if settings is not None else _fake_settings(),
            llm=llm if llm is not None else FakeLLM(),
            vault=vault,
            extras=extras if extras is not None else {},
        )

    return _make_ctx
