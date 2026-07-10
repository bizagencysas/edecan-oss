"""Montaje defensivo de los routers v4 en `edecan_api.main.create_app()`
(`ARCHITECTURE.md` §13, dueño WP-V4-01).

Espejo exacto de `test_v3_mounting.py` (que a su vez sigue el mismo criterio
que `test_v2_mounting.py`, ver su docstring para el porqué): no depende de si
algún router v4 real (`edecan_api/routers/{devices,erp,ads,vehiculos,
mensajes}.py`) ya aterrizó en disco -- otros WPs los agregan en paralelo --
así que monkeypatchea `importlib.import_module` para simular
determinísticamente "el módulo no existe todavía" y "el módulo existe y
expone `router`".

A diferencia de `test_v3_mounting.py`, este archivo NO repite las pruebas de
`SERVE_WEB_DIR` (`ARCHITECTURE.md` §12g): ese es un contrato de v3 (dueño
WP-V3-01, montaje estático al final de `create_app()`) que WP-V4-01 no toca
ni extiende -- ya está cubierto ahí, duplicarlo aquí no agregaría señal.
"""

from __future__ import annotations

import importlib
import types

import pytest
from httpx import ASGITransport, AsyncClient

from edecan_api.config import Settings, get_settings
from edecan_api.main import V4_ROUTER_NAMES, create_app

# Prefijos pinned en ARCHITECTURE.md §13 -- uno por cada nombre de
# `V4_ROUTER_NAMES`, en el mismo orden.
_V4_PREFIXES: dict[str, str] = {
    "devices": "/v1/devices",
    "erp": "/v1/erp",
    "ads": "/v1/ads",
    "vehiculos": "/v1/vehiculos",
    "mensajes": "/v1/mensajes",
}


def _siempre_import_error(name: str, package: str | None = None):
    if name.startswith("edecan_api.routers."):
        raise ImportError(f"módulo simulado ausente: {name}")
    return importlib.import_module(name, package)


@pytest.fixture
def test_settings() -> Settings:
    return Settings(ENV="dev", WEB_BASE_URL="http://localhost:3000")


def test_v4_router_names_coincide_con_architecture_13():
    assert V4_ROUTER_NAMES == ("devices", "erp", "ads", "vehiculos", "mensajes")


def test_create_app_no_falla_cuando_faltan_todos_los_routers_v4(
    monkeypatch: pytest.MonkeyPatch, test_settings: Settings
) -> None:
    monkeypatch.setattr(importlib, "import_module", _siempre_import_error)

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: test_settings

    assert app is not None


async def test_healthz_sigue_ok_cuando_faltan_todos_los_routers_v4(
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


@pytest.mark.parametrize("router_name", list(_V4_PREFIXES))
async def test_ninguna_ruta_v4_existe_cuando_su_modulo_falta(
    monkeypatch: pytest.MonkeyPatch, test_settings: Settings, router_name: str
) -> None:
    monkeypatch.setattr(importlib, "import_module", _siempre_import_error)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: test_settings

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(_V4_PREFIXES[router_name])
    # 404: FastAPI ni siquiera reconoce el path (el router nunca se montó).
    # Si el router SÍ estuviera montado, la falta de auth daría 401/403, nunca 404.
    assert response.status_code == 404


async def test_router_v4_presente_se_monta_y_queda_alcanzable(
    monkeypatch: pytest.MonkeyPatch, test_settings: Settings
) -> None:
    """Simula que SOLO `edecan_api.routers.devices` ya aterrizó."""
    from fastapi import APIRouter

    fake_router = APIRouter(prefix="/v1/devices", tags=["devices-fake"])

    @fake_router.get("/ping")
    async def _ping() -> dict[str, str]:  # pragma: no cover - trivial
        return {"pong": "v4"}

    fake_module = types.ModuleType("edecan_api.routers.devices")
    fake_module.router = fake_router  # type: ignore[attr-defined]

    def _import_module(name: str, package: str | None = None):
        if name == "edecan_api.routers.devices":
            return fake_module
        if name.startswith("edecan_api.routers."):
            raise ImportError(f"módulo simulado ausente: {name}")
        return importlib.import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", _import_module)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: test_settings

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/devices/ping")
        otro = await client.get("/v1/erp")

    assert response.status_code == 200
    assert response.json() == {"pong": "v4"}
    # El resto sigue ausente: el montaje es POR módulo, no todo-o-nada.
    assert otro.status_code == 404


def test_create_app_no_falla_con_los_routers_v4_reales_que_existan_hoy(
    test_settings: Settings,
) -> None:
    """Smoke test sin monkeypatch: `create_app()` debe seguir funcionando sin
    importar cuántos de los 5 routers v4 ya aterrizaron de verdad en disco
    (0, algunos o todos) -- a diferencia de los tests de arriba, este NO fija
    cuáles están montados, solo que construir la app nunca revienta."""
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: test_settings
    assert app is not None


async def test_devices_router_real_ya_aterrizado_queda_montado_con_su_prefix() -> None:
    """A diferencia del resto de este archivo (que monkeypatchea para simular
    ausencia/presencia), este WP SÍ construye `edecan_api.routers.devices` de
    verdad (`ARCHITECTURE.md` §13) -- confirma que `create_app()` real (sin
    monkeypatch) ya lo monta en `/v1/devices`. Pide SIN autenticar: `401`
    (no `404`) ya prueba que el path operation existe -- `get_current_user`
    corta antes de necesitar una sesión de Postgres real, así que este test
    no depende de tener una base de datos alcanzable."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/devices")
    assert response.status_code == 401
