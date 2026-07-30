"""Montaje defensivo de los routers v5 en `edecan_api.main.create_app()`
(`ARCHITECTURE.md` §14, dueño WP-V5-01).

Espejo exacto de `test_v4_mounting.py` (que a su vez sigue el mismo criterio
que `test_v3_mounting.py`/`test_v2_mounting.py`, ver sus docstrings para el
porqué): no depende de si algún router v5 real (`edecan_api/routers/{rrhh,
viajes,voz_avanzada}.py`) ya aterrizó en disco -- otros WPs los agregan en
paralelo -- así que monkeypatchea `importlib.import_module` para simular
determinísticamente "el módulo no existe todavía" y "el módulo existe y
expone `router`".

A diferencia de `test_v4_mounting.py` (que sí tenía un test dedicado a
`devices`, construido de verdad POR ese mismo WP), este archivo NO tiene un
equivalente: WP-V5-01 (este WP) es puramente el linchpin de contratos
compartidos -- ningún router v5 real aterriza en este work package, los 3
quedan para WPs paralelos.
"""

from __future__ import annotations

import importlib
import types

import pytest
from httpx import ASGITransport, AsyncClient

from edecan_api.config import Settings, get_settings
from edecan_api.main import V5_ROUTER_NAMES, create_app

# Prefijos pinned en ARCHITECTURE.md §14 -- uno por cada nombre de
# `V5_ROUTER_NAMES`, en el mismo orden. `voz_avanzada` es la única excepción
# a la convención módulo=prefix del resto del repo (su `prefix=` real es
# `/v1/voz`, más corto -- decisión de su dueño real WP-V5-10, ver §14.a); el
# montaje defensivo de abajo no depende de que coincidan, solo el nombre de
# módulo importa.
_V5_PREFIXES: dict[str, str] = {
    "rrhh": "/v1/rrhh",
    "viajes": "/v1/viajes",
    "voz_avanzada": "/v1/voz",
}


def _siempre_import_error(name: str, package: str | None = None):
    if name.startswith("edecan_api.routers."):
        raise ImportError(f"módulo simulado ausente: {name}")
    return importlib.import_module(name, package)


@pytest.fixture
def test_settings() -> Settings:
    return Settings(ENV="dev", WEB_BASE_URL="http://localhost:3000")


def test_v5_router_names_coincide_con_architecture_14():
    assert V5_ROUTER_NAMES == ("rrhh", "viajes", "voz_avanzada")


def test_create_app_no_falla_cuando_faltan_todos_los_routers_v5(
    monkeypatch: pytest.MonkeyPatch, test_settings: Settings
) -> None:
    monkeypatch.setattr(importlib, "import_module", _siempre_import_error)

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: test_settings

    assert app is not None


async def test_healthz_sigue_ok_cuando_faltan_todos_los_routers_v5(
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


@pytest.mark.parametrize("router_name", list(_V5_PREFIXES))
async def test_ninguna_ruta_v5_existe_cuando_su_modulo_falta(
    monkeypatch: pytest.MonkeyPatch, test_settings: Settings, router_name: str
) -> None:
    monkeypatch.setattr(importlib, "import_module", _siempre_import_error)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: test_settings

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(_V5_PREFIXES[router_name])
    # 404: FastAPI ni siquiera reconoce el path (el router nunca se montó).
    # Si el router SÍ estuviera montado, la falta de auth daría 401/403, nunca 404.
    assert response.status_code == 404


async def test_router_v5_presente_se_monta_y_queda_alcanzable(
    monkeypatch: pytest.MonkeyPatch, test_settings: Settings
) -> None:
    """Simula que SOLO `edecan_api.routers.rrhh` ya aterrizó."""
    from fastapi import APIRouter

    fake_router = APIRouter(prefix="/v1/rrhh", tags=["rrhh-fake"])

    @fake_router.get("/ping")
    async def _ping() -> dict[str, str]:  # pragma: no cover - trivial
        return {"pong": "v5"}

    fake_module = types.ModuleType("edecan_api.routers.rrhh")
    fake_module.router = fake_router  # type: ignore[attr-defined]

    def _import_module(name: str, package: str | None = None):
        if name == "edecan_api.routers.rrhh":
            return fake_module
        if name.startswith("edecan_api.routers."):
            raise ImportError(f"módulo simulado ausente: {name}")
        return importlib.import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", _import_module)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: test_settings

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/rrhh/ping")
        otro = await client.get("/v1/viajes")

    assert response.status_code == 200
    assert response.json() == {"pong": "v5"}
    # El resto sigue ausente: el montaje es POR módulo, no todo-o-nada.
    assert otro.status_code == 404


def test_create_app_no_falla_con_los_routers_v5_reales_que_existan_hoy(
    test_settings: Settings,
) -> None:
    """Smoke test sin monkeypatch: `create_app()` debe seguir funcionando sin
    importar cuántos de los 3 routers v5 ya aterrizaron de verdad en disco
    (0, algunos o todos) -- a diferencia de los tests de arriba, este NO fija
    cuáles están montados, solo que construir la app nunca revienta. Hoy
    (este mismo WP) son 0 -- ninguno de los 3 existe todavía en disco."""
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: test_settings
    assert app is not None
