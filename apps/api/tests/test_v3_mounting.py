"""Montaje defensivo de los routers v3 en `edecan_api.main.create_app()`
(`ARCHITECTURE.md` §12, dueño WP-V3-01).

Mismo criterio que `test_v2_mounting.py` (ver su docstring para el porqué):
no depende de si algún router v3 real (`edecan_api/routers/{credentials,
setup,skills,smarthome}.py`) ya aterrizó en disco -- otros WPs los agregan en
paralelo -- así que monkeypatchea `importlib.import_module` para simular
determinísticamente "el módulo no existe todavía" y "el módulo existe y
expone `router`".

Además cubre el segundo contrato de este WP: el mount estático de
`SERVE_WEB_DIR` al final de `create_app()` (ARCHITECTURE.md §12g). Como esa
carpeta se lee de `get_settings()` en tiempo de CONSTRUCCIÓN de la app (no
vía `Depends`), `app.dependency_overrides` no sirve para variarla -- estos
tests monkeypatchean directamente `edecan_api.main.get_settings` (el símbolo
que `create_app()` invoca), igual que el resto del archivo monkeypatchea
`importlib.import_module`.
"""

from __future__ import annotations

import importlib
import types
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from edecan_api import main
from edecan_api.config import Settings, get_settings
from edecan_api.main import V3_ROUTER_NAMES, create_app

# Prefijos pinned en ARCHITECTURE.md §12a -- uno por cada nombre de
# `V3_ROUTER_NAMES`, en el mismo orden.
_V3_PREFIXES: dict[str, str] = {
    "credentials": "/v1/credentials",
    "setup": "/v1/setup",
    "skills": "/v1/skills",
    "smarthome": "/v1/smarthome",
}


def _siempre_import_error(name: str, package: str | None = None):
    if name.startswith("edecan_api.routers."):
        raise ImportError(f"módulo simulado ausente: {name}")
    return importlib.import_module(name, package)


@pytest.fixture
def test_settings() -> Settings:
    return Settings(ENV="dev", WEB_BASE_URL="http://localhost:3000")


def test_v3_router_names_coincide_con_architecture_12a():
    assert V3_ROUTER_NAMES == ("credentials", "setup", "skills", "smarthome")


def test_create_app_no_falla_cuando_faltan_todos_los_routers_v3(
    monkeypatch: pytest.MonkeyPatch, test_settings: Settings
) -> None:
    monkeypatch.setattr(importlib, "import_module", _siempre_import_error)

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: test_settings

    assert app is not None


async def test_healthz_sigue_ok_cuando_faltan_todos_los_routers_v3(
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


@pytest.mark.parametrize("router_name", list(_V3_PREFIXES))
async def test_ninguna_ruta_v3_existe_cuando_su_modulo_falta(
    monkeypatch: pytest.MonkeyPatch, test_settings: Settings, router_name: str
) -> None:
    monkeypatch.setattr(importlib, "import_module", _siempre_import_error)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: test_settings

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(_V3_PREFIXES[router_name])
    # 404: FastAPI ni siquiera reconoce el path (el router nunca se montó).
    # Si el router SÍ estuviera montado, la falta de auth daría 401/403, nunca 404.
    assert response.status_code == 404


async def test_router_v3_presente_se_monta_y_queda_alcanzable(
    monkeypatch: pytest.MonkeyPatch, test_settings: Settings
) -> None:
    """Simula que SOLO `edecan_api.routers.skills` ya aterrizó."""
    from fastapi import APIRouter

    fake_router = APIRouter(prefix="/v1/skills", tags=["skills-fake"])

    @fake_router.get("/ping")
    async def _ping() -> dict[str, str]:  # pragma: no cover - trivial
        return {"pong": "v3"}

    fake_module = types.ModuleType("edecan_api.routers.skills")
    fake_module.router = fake_router  # type: ignore[attr-defined]

    def _import_module(name: str, package: str | None = None):
        if name == "edecan_api.routers.skills":
            return fake_module
        if name.startswith("edecan_api.routers."):
            raise ImportError(f"módulo simulado ausente: {name}")
        return importlib.import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", _import_module)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: test_settings

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/skills/ping")
        otro = await client.get("/v1/setup")

    assert response.status_code == 200
    assert response.json() == {"pong": "v3"}
    # El resto sigue ausente: el montaje es POR módulo, no todo-o-nada.
    assert otro.status_code == 404


def test_create_app_no_falla_con_los_routers_v3_reales_que_existan_hoy(
    test_settings: Settings,
) -> None:
    """Smoke test sin monkeypatch: `create_app()` debe seguir funcionando sin
    importar cuántos de los 4 routers v3 ya aterrizaron de verdad en disco
    (0, algunos o todos) -- a diferencia de los tests de arriba, este NO fija
    cuáles están montados, solo que construir la app nunca revienta."""
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: test_settings
    assert app is not None


# ---------------------------------------------------------------------------
# SERVE_WEB_DIR (ARCHITECTURE.md §12g) -- mount estático al final de "/".
# ---------------------------------------------------------------------------


async def test_serve_web_dir_definido_y_existente_se_monta_y_sirve_index(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "index.html").write_text("<html>hola edecan local</html>", encoding="utf-8")
    settings_con_web = Settings(
        ENV="dev", WEB_BASE_URL="http://localhost:3000", SERVE_WEB_DIR=str(tmp_path)
    )
    monkeypatch.setattr(main, "get_settings", lambda: settings_con_web)

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        raiz = await client.get("/")
        salud = await client.get("/healthz")

    assert raiz.status_code == 200
    assert "hola edecan local" in raiz.text
    assert raiz.headers["x-content-type-options"] == "nosniff"
    assert raiz.headers["x-frame-options"] == "DENY"
    assert raiz.headers["referrer-policy"] == "no-referrer"
    assert "microphone=(self)" in raiz.headers["permissions-policy"]
    csp = raiz.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp
    assert "frame-src blob:" in csp
    assert "frame-src http" not in csp
    assert "connect-src 'self' ipc: http://ipc.localhost" in csp
    assert "unsafe-eval" not in csp
    # El mount en "/" va AL FINAL: /healthz sigue alcanzable, no queda tapado.
    assert salud.status_code == 200
    assert salud.json() == {"status": "ok"}


async def test_serve_web_dir_expande_tilde_de_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "index.html").write_text("<html>home</html>", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    settings_con_tilde = Settings(
        ENV="dev", WEB_BASE_URL="http://localhost:3000", SERVE_WEB_DIR="~"
    )
    monkeypatch.setattr(main, "get_settings", lambda: settings_con_tilde)

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        raiz = await client.get("/")

    assert raiz.status_code == 200
    assert "home" in raiz.text


def test_serve_web_dir_ausente_no_monta_nada(test_settings: Settings) -> None:
    # `test_settings` no define SERVE_WEB_DIR (default None) -- `create_app()`
    # no debe agregar ningún mount adicional en "/".
    assert test_settings.SERVE_WEB_DIR is None
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: test_settings
    nombres_mounts = [getattr(route, "name", None) for route in app.router.routes]
    assert "web" not in nombres_mounts


async def test_serve_web_dir_apuntando_a_carpeta_inexistente_no_revienta(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    carpeta_inexistente = tmp_path / "no-existe-todavia"
    settings_con_web_falso = Settings(
        ENV="dev", WEB_BASE_URL="http://localhost:3000", SERVE_WEB_DIR=str(carpeta_inexistente)
    )
    monkeypatch.setattr(main, "get_settings", lambda: settings_con_web_falso)

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        raiz = await client.get("/")
        salud = await client.get("/healthz")

    # Sin carpeta real, "/" no queda montado (404) pero el resto de la API
    # sigue sana -- nunca una excepción al construir la app.
    assert raiz.status_code == 404
    assert salud.status_code == 200
