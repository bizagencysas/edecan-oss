"""Fixtures compartidas de `edecan_api` — `httpx.AsyncClient` + `dependency_overrides`
sobre fakes en memoria (sin Postgres real), como exige el paquete de trabajo.

`_stub_siblings` se importa primero (por su efecto secundario: agrega los
paquetes hermanos reales a `sys.path` y registra los que aún no existen como
módulos falsos en `sys.modules`) para que `edecan_api.main` sea importable —
ver el docstring de ese módulo para el porqué.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import _stub_siblings  # noqa: F401  (efecto secundario: puebla sys.path/sys.modules)
import pytest
from api_fakes import FakeRedis, FakeRepo
from httpx import ASGITransport, AsyncClient

from edecan_api import deps as edecan_deps
from edecan_api.config import Settings, get_settings
from edecan_api.main import create_app

TEST_JWT_SECRET = "test-jwt-secret-solo-para-tests-32-bytes-o-mas"


def pytest_configure(config: pytest.Config) -> None:
    """Registra el marker `integration` (usado por `test_repo_sql_integration.py`)
    localmente, igual que `packages/db/tests/conftest.py`, en vez de tocar el
    `[tool.pytest.ini_options]` de la raíz del monorepo."""
    config.addinivalue_line(
        "markers",
        "integration: requiere una base de datos Postgres real y alcanzable "
        "(ver DATABASE_URL); se salta automáticamente si no hay una.",
    )


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        ENV="dev",
        JWT_SECRET=TEST_JWT_SECRET,
        WEB_BASE_URL="http://localhost:3000",
        PUBLIC_BASE_URL="http://localhost:8000",
        LOCAL_DESKTOP_CAPABILITY="test-desktop-capability",
    )


@pytest.fixture
def fake_repo() -> FakeRepo:
    return FakeRepo()


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def app(fake_repo: FakeRepo, fake_redis: FakeRedis, test_settings: Settings):
    """App con `dependency_overrides`: settings/repo/redis fakes; sin Postgres real.

    Deliberadamente NO se sobreescribe `get_current_user`: cada test que
    necesita autenticarse arma su propio Bearer token con
    `edecan_api.security.create_access_token(..., secret=TEST_JWT_SECRET)`,
    para ejercitar la verificación JWT real (incluido el caso "token expirado").
    """
    application = create_app()

    application.dependency_overrides[get_settings] = lambda: test_settings
    application.dependency_overrides[edecan_deps.get_platform_repo] = lambda: fake_repo
    application.dependency_overrides[edecan_deps.get_repo] = lambda: fake_repo
    application.dependency_overrides[edecan_deps.get_streaming_repo] = lambda: fake_repo
    application.dependency_overrides[edecan_deps.get_redis] = lambda: fake_redis
    application.dependency_overrides[edecan_deps.get_tenant_session] = lambda: None
    application.dependency_overrides[edecan_deps.get_vault] = lambda: None
    application.dependency_overrides[edecan_deps.get_streaming_vault] = lambda: None
    application.dependency_overrides[edecan_deps.get_llm_router] = lambda: None

    @asynccontextmanager
    async def fake_phone_transaction(_tenant_id: uuid.UUID):
        yield fake_repo

    application.state.phone_repo_transaction_factory = fake_phone_transaction

    yield application

    application.dependency_overrides.clear()


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def auth_headers(
    *, user_id: uuid.UUID, tenant_id: uuid.UUID, plan_key: str = "hosted_basic"
) -> dict[str, str]:
    """Header `Authorization: Bearer <access token>` firmado con `TEST_JWT_SECRET`."""
    from edecan_api.security import create_access_token

    token = create_access_token(
        user_id=user_id, tenant_id=tenant_id, plan_key=plan_key, secret=TEST_JWT_SECRET
    )
    return {"Authorization": f"Bearer {token}"}
