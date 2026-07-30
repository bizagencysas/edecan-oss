"""Fixtures compartidas de `edecan_smarthome` (ver `ARCHITECTURE.md` §10.1, §10.15).

El bloque de `sys.path` de abajo se ejecuta primero (por su efecto
secundario: agrega el propio código fuente de este paquete,
`packages/smarthome/`, a `sys.path`) para que `import edecan_smarthome`
funcione en un `pytest` corrido solo sobre este paquete, sin depender de que
ya haya corrido `uv sync --all-packages` en el workspace — mismo motivo y
misma técnica que `packages/messaging/tests/conftest.py` y
`packages/skills/tests/conftest.py`. Una vez `packages/smarthome` queda
instalado editable en el entorno compartido, este bloque es un no-op inocuo
(`if ... not in sys.path` ya lo evita duplicar).

Fakes deliberadamente ligeros, por duck typing (calcan
`packages/messaging/tests/conftest.py`): ningún test de este paquete importa
`edecan_db` para construir sus dobles — `ctx.session`/`ctx.vault` se
completan con objetos locales que solo implementan lo que
`edecan_smarthome` realmente usa, y `ctx` en sí es un `SimpleNamespace` (no
`edecan_core.ToolContext`), aunque `edecan_core` SÍ debe estar instalado/en
`sys.path` para poder importar el código de PRODUCCIÓN de este paquete, que
sí lo usa.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

import pytest  # noqa: E402  (después del shim de sys.path, ver docstring)


class FakeResult:
    """Imita lo mínimo de `sqlalchemy.engine.Result` que usa este paquete
    (`.mappings().first()` — ver `edecan_smarthome.tools._cliente_desde_vault`)."""

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
    programada (una lista de filas-dict) y registra `(sql, params)` en
    `llamadas`."""

    respuestas: list[list[dict[str, Any]]] = field(default_factory=list)
    llamadas: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> FakeResult:
        self.llamadas.append((str(stmt), dict(params or {})))
        filas = self.respuestas.pop(0) if self.respuestas else []
        return FakeResult(filas)


@dataclass
class FakeVault:
    """`ctx.vault` falso: devuelve siempre el mismo `bundle` (o `None` si no
    hay credencial) y registra cada `get()` en `llamadas`."""

    bundle: Any = None
    llamadas: list[tuple[Any, Any]] = field(default_factory=list)

    async def get(self, tenant_id: Any, connector_account_id: Any) -> Any:
        self.llamadas.append((tenant_id, connector_account_id))
        return self.bundle


@pytest.fixture
def make_session():
    """Factory de `FakeSession`: `make_session([[{"id": "acc-1"}], []])`."""

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
    """Factory de un `ToolContext` falso (`SimpleNamespace` duck-typed, ver
    docstring del módulo). Sin argumentos, cada dependencia se rellena con un
    fake vacío."""

    def _make_ctx(
        *,
        session: Any = None,
        vault: Any = None,
        settings: Any = None,
        extras: dict[str, Any] | None = None,
        tenant_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            tenant_id=tenant_id or uuid4(),
            user_id=user_id or uuid4(),
            session=session if session is not None else FakeSession(),
            settings=settings if settings is not None else SimpleNamespace(),
            llm=None,
            vault=vault if vault is not None else FakeVault(),
            extras=extras if extras is not None else {},
        )

    return _make_ctx
