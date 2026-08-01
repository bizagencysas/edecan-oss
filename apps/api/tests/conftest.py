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
    # `_env_file=None` AÍSLA los tests del `.env` LOCAL del dueño (que tiene su
    # config real de R2: `S3_BUCKET=edecan`, `AWS_ENDPOINT_URL=...r2...`, etc.).
    # Sin esto, esos valores reales se filtraban a la Settings de test y rompían
    # ~18 tests que esperan los DEFAULTS del código (p. ej. `S3_BUCKET=edecan-files`,
    # sin `SQS_QUEUE_URL`). Los tests deben depender solo de lo declarado acá y de
    # los defaults, nunca del entorno de la máquina donde corren.
    return Settings(
        _env_file=None,
        ENV="dev",
        JWT_SECRET=TEST_JWT_SECRET,
        WEB_BASE_URL="http://localhost:3000",
        PUBLIC_BASE_URL="http://localhost:8000",
        LOCAL_DESKTOP_CAPABILITY="test-desktop-capability",
        CLOUDFLARE_ACCOUNT_ID="test-cloudflare-account",
        CLOUDFLARE_API_TOKEN="test-cloudflare-token",
    )


@pytest.fixture(autouse=True)
def _conectores_sin_estado_entre_tests():
    """Devuelve los conectores compartidos a como estaban, tras CADA test.

    Las instancias de `CONNECTORS` las comparte TODO el proceso, entre todos los
    tenants; su seguridad multi-tenant depende de que no tengan ni un atributo de
    instancia (lo verifica `test_connectors_registry_usa_instancias_singleton_sin_estado`
    en `packages/connectors`). Si un tenant pudiera dejarle algo pisado al objeto,
    se lo dejaría pisado al siguiente.

    La trampa que esto cierra es de `monkeypatch`, y es sutil: varios tests hacen
    `monkeypatch.setattr(CONNECTORS["google"], "exchange_code", fake)`. Pero
    `exchange_code` es un método de CLASE, no un atributo de la instancia --
    monkeypatch guarda el valor viejo con `getattr` (que devuelve un método
    LIGADO) y al deshacer hace `setattr`, así que **restaura poniendo, no
    borrando**: deja pegado en la instancia un atributo que antes no existía.

    Lo insidioso es que el daño no se ve donde se causa. La suite de `apps/api`
    pasa igual; lo que falla es un test de OTRO paquete, y solo si el orden de
    ejecución lo pone después. Por eso se limpia acá y no en cada test: un
    `monkeypatch` nuevo con el mismo patrón vuelve a introducirlo sin que nadie
    lo note.
    """
    from edecan_connectors import CONNECTORS

    previos = {clave: dict(vars(conector)) for clave, conector in CONNECTORS.items()}
    yield
    for clave, conector in CONNECTORS.items():
        estado = vars(conector)
        estado.clear()
        estado.update(previos.get(clave, {}))


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
    application.dependency_overrides[edecan_deps.get_platform_vault] = lambda: None
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
