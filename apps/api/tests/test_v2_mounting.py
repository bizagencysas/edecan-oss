"""Montaje defensivo de los routers v2 en `edecan_api.main.create_app()`
(ROADMAP_V2.md §7.6, dueño WP-V2-01).

No depende de si algún router v2 real (`edecan_api/routers/{missions,
automations,hooks,ide,remote,commerce,negocios,perfil}.py`) ya aterrizó en
disco -- otros WPs los agregan en paralelo (ARCHITECTURE.md §10.1) y este
archivo NO debe volverse intermitente según qué WPs ya corrieron. En su
lugar, monkeypatchea `importlib.import_module` (el símbolo que
`edecan_api.main` invoca) para simular determinísticamente los dos casos:
"el módulo no existe todavía" (ImportError) y "el módulo existe y expone
`router`".
"""

from __future__ import annotations

import importlib
import types

import pytest
from httpx import ASGITransport, AsyncClient

from edecan_api.config import Settings, get_settings
from edecan_api.main import V2_ROUTER_NAMES, create_app

# Prefijos pinned en ROADMAP_V2.md §7.6 -- uno por cada nombre de
# `V2_ROUTER_NAMES`, en el mismo orden.
_V2_PREFIXES: dict[str, str] = {
    "missions": "/v1/missions",
    "automations": "/v1/automations",
    "hooks": "/v1/hooks",
    "ide": "/v1/ide",
    "remote": "/v1/remote",
    "commerce": "/v1/commerce",
    "negocios": "/v1/negocios",
    "perfil": "/v1/perfil",
}


def _siempre_import_error(name: str, package: str | None = None):
    if name.startswith("edecan_api.routers."):
        raise ImportError(f"módulo simulado ausente: {name}")
    return importlib.import_module(name, package)


@pytest.fixture
def test_settings() -> Settings:
    return Settings(ENV="dev", WEB_BASE_URL="http://localhost:3000")


def test_v2_router_names_coincide_con_roadmap_v2_7_6():
    assert V2_ROUTER_NAMES == (
        "missions",
        "automations",
        "hooks",
        "ide",
        "remote",
        "commerce",
        "negocios",
        "perfil",
    )


def test_create_app_no_falla_cuando_faltan_todos_los_routers_v2(
    monkeypatch: pytest.MonkeyPatch, test_settings: Settings
) -> None:
    monkeypatch.setattr(importlib, "import_module", _siempre_import_error)

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: test_settings

    assert app is not None


async def test_healthz_sigue_ok_cuando_faltan_todos_los_routers_v2(
    monkeypatch: pytest.MonkeyPatch, test_settings: Settings
) -> None:
    monkeypatch.setattr(importlib, "import_module", _siempre_import_error)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: test_settings

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("router_name", list(_V2_PREFIXES))
async def test_ninguna_ruta_v2_existe_cuando_su_modulo_falta(
    monkeypatch: pytest.MonkeyPatch, test_settings: Settings, router_name: str
) -> None:
    monkeypatch.setattr(importlib, "import_module", _siempre_import_error)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: test_settings

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(_V2_PREFIXES[router_name])
    # 404: FastAPI ni siquiera reconoce el path (el router nunca se montó).
    # Si el router SÍ estuviera montado, la falta de auth daría 401/403, nunca 404.
    assert response.status_code == 404


async def test_router_v2_presente_se_monta_y_queda_alcanzable(
    monkeypatch: pytest.MonkeyPatch, test_settings: Settings
) -> None:
    """Simula que SOLO `edecan_api.routers.missions` ya aterrizó."""
    from fastapi import APIRouter

    fake_router = APIRouter(prefix="/v1/missions", tags=["missions-fake"])

    @fake_router.get("/ping")
    async def _ping() -> dict[str, str]:  # pragma: no cover - trivial
        return {"pong": "v2"}

    fake_module = types.ModuleType("edecan_api.routers.missions")
    fake_module.router = fake_router  # type: ignore[attr-defined]

    def _import_module(name: str, package: str | None = None):
        if name == "edecan_api.routers.missions":
            return fake_module
        if name.startswith("edecan_api.routers."):
            raise ImportError(f"módulo simulado ausente: {name}")
        return importlib.import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", _import_module)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: test_settings

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/missions/ping")
        otro = await client.get("/v1/automations")

    assert response.status_code == 200
    assert response.json() == {"pong": "v2"}
    # El resto sigue ausente: el montaje es POR módulo, no todo-o-nada.
    assert otro.status_code == 404


def test_create_app_no_falla_con_los_routers_v2_reales_que_existan_hoy(
    test_settings: Settings,
) -> None:
    """Smoke test sin monkeypatch: `create_app()` debe seguir funcionando sin
    importar cuántos de los 8 routers v2 ya aterrizaron de verdad en disco
    (0, algunos o todos) -- a diferencia de los tests de arriba, este NO fija
    cuáles están montados, solo que construir la app nunca revienta."""
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: test_settings
    assert app is not None
