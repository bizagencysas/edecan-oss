"""Fixtures compartidas de `edecan_creative` (ver `ARCHITECTURE.md` §10.1, §10.15).

Fakes deliberadamente ligeros, por duck typing: ningún test de este paquete
importa `edecan_core`, `edecan_db` ni `edecan_api` para construir sus dobles —
`ctx.session`/`ctx.settings` se completan con objetos locales que solo
implementan lo que el paquete realmente usa, y `ctx` en sí es un
`SimpleNamespace` (no `edecan_core.ToolContext`): las tools acceden a sus
atributos por duck typing, así que tampoco hace falta esa importación aquí.
`edecan_creative._files` y `edecan_creative.tools`/`providers` sí se importan
directamente en los tests — no son un paquete "hermano", son el código bajo
prueba de este mismo paquete.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Registra el marker `integration` (usado por `test_podcast.py`) localmente,
    mismo patrón que `packages/db/tests/conftest.py`/`apps/api/tests/conftest.py`/
    `apps/local/tests/conftest.py`, en vez de tocar el `[tool.pytest.ini_options]`
    de la raíz del monorepo (que pertenece a otro paquete de trabajo)."""
    config.addinivalue_line(
        "markers",
        "integration: requiere ffmpeg real instalado en el sistema; se salta "
        "automáticamente si no está disponible (ver docs/creatividad.md).",
    )


class FakeResult:
    """Imita lo mínimo de `sqlalchemy.engine.Result` que usa el paquete."""

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
    """`ctx.session` falso: cada `execute()` registra `(sql, params)` en
    `llamadas` y devuelve la siguiente respuesta programada (vacía por
    defecto — `_files.subir_archivo` nunca lee el resultado del `INSERT`).
    """

    respuestas: list[list[dict[str, Any]]] = field(default_factory=list)
    llamadas: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> FakeResult:
        self.llamadas.append((str(stmt), dict(params or {})))
        filas = self.respuestas.pop(0) if self.respuestas else []
        return FakeResult(filas)


@dataclass
class FakeVault:
    """`ctx.vault` falso (`providers.get_tenant_image_provider`): devuelve
    siempre el mismo `bundle` (o `None` si no hay cuenta conectada) y
    registra cada `get()` en `llamadas`.
    """

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
    """Factory de `ctx.settings` falso: `fake_settings(IMAGES_PROVIDER="openai_compat", ...)`."""

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
    (`settings` sin ningún atributo — ejercita los `getattr(..., default)`
    defensivos de `providers.get_image_provider`/`_files.subir_archivo`).
    """

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
            # `vault` sigue en `None` por defecto (ningún test previo lo
            # pasaba): `providers.get_tenant_image_provider` trata
            # `ctx.vault is None` como "sin credencial bring-your-own del
            # tenant" y cae a `get_image_provider(ctx.settings)` — mismo
            # comportamiento que antes de que existiera ese parámetro.
            vault=vault,
            extras=extras if extras is not None else {},
        )

    return _make_ctx


@dataclass
class FakeUploader:
    """Doble de `edecan_creative._files.Uploader`: registra cada llamada
    (`data`/`filename`/`mime`) en `llamadas` y devuelve un `(file_id,
    filename)` determinista sin tocar S3 ni Postgres — el "patrón inyectable"
    que exponen las tools de este paquete vía su constructor (`uploader=...`).
    """

    file_id: UUID = field(default_factory=uuid4)
    llamadas: list[dict[str, Any]] = field(default_factory=list)

    async def __call__(
        self, ctx: Any, *, data: bytes, filename: str, mime: str
    ) -> tuple[UUID, str]:
        self.llamadas.append({"ctx": ctx, "data": data, "filename": filename, "mime": mime})
        return self.file_id, filename


@pytest.fixture
def make_uploader():
    """Factory de `FakeUploader`: `make_uploader(file_id=uuid4())`."""

    def _make_uploader(file_id: UUID | None = None) -> FakeUploader:
        return FakeUploader(file_id=file_id or uuid4())

    return _make_uploader
