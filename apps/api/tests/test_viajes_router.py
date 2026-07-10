"""`edecan_api.routers.viajes` — `/v1/viajes/*` (Amadeus + AfterShip bring-your-own,
`ARCHITECTURE.md` §14, WP-V5-09; ver el docstring del propio router para el contrato
completo, especialmente el guardrail de dinero).

`edecan-travel` todavía no es necesariamente una dependencia declarada de `apps/api`
(`apps/api/pyproject.toml`, fuera de las rutas que este paquete de trabajo puede tocar
— eso es trabajo del linchpin de v5, WP-V5-01, mismo patrón que `edecan-ads` hasta que
WP-V4-01 lo declaró) — así que, igual que `test_ads_router.py` tuvo que hacer en su
momento, este módulo agrega `packages/travel` a `sys.path` por su cuenta, antes de
importar `edecan_api.routers.viajes`. El fixture `_mounted_app` monta el router de
forma explícita sobre la `app` de `conftest.py` (mismo criterio que
`test_ads_router.py`/`test_commerce_router.py`/`test_erp_router.py`): así estos tests
no dependen de que `edecan_api.main.create_app()` ya lo monte solo (eso es cosa del
linchpin de v5 vía un futuro `V5_ROUTER_NAMES`, que puede aterrizar antes o después que
este WP en un build paralelo) ni de que `edecan_travel` ya esté en `sys.path` en el
momento exacto en que `create_app()` corre.

## El flag `tools.travel` en `edecan_schemas.plans.PLANES`

Modelo de precio de pago único (2026-07-09, `edecan_schemas.plans` docstring):
`tools.travel` (y cualquier otro flag) ya está en `True` en las 4 entradas de
`PLANES` por igual — no hay más "flag apagado" que probar. El fixture
`_con_flag_travel` de abajo SE CONSERVA tal cual (fuerza el flag con
`monkeypatch.setitem(PLANES[PLAN_WITH_TRAVEL].flags, ...)`, revierte solo al
terminar cada test) porque forzar un valor que hoy YA es `True` es idempotente — no
cambia ningún resultado — y actúa como red de seguridad si el mecanismo de flags
se restaurara alguna vez.
"""

from __future__ import annotations

import json
import sys
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from api_fakes import FakeRepo
from conftest import auth_headers
from edecan_schemas import TokenBundle
from httpx import ASGITransport, AsyncClient

_TRAVEL_SRC = str(Path(__file__).resolve().parents[3] / "packages" / "travel")
if _TRAVEL_SRC not in sys.path:
    sys.path.insert(0, _TRAVEL_SRC)

import edecan_api.deps as edecan_deps  # noqa: E402
from edecan_api.routers import viajes  # noqa: E402

PLAN_WITH_TRAVEL = "hosted_pro"

API_KEY = "amadeus-key-de-prueba"
API_SECRET = "amadeus-secret-de-prueba"
AFTERSHIP_KEY = "aftership-key-de-prueba"
AMADEUS_TEST_BASE = viajes.AMADEUS_TEST_BASE_URL
AFTERSHIP_BASE = viajes.AFTERSHIP_BASE_URL


class FakeResult:
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
    respuestas: list[list[dict[str, Any]]] = field(default_factory=list)
    llamadas: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> FakeResult:
        self.llamadas.append((str(stmt), dict(params or {})))
        filas = self.respuestas.pop(0) if self.respuestas else []
        return FakeResult(filas)

    async def flush(self) -> None:
        pass


@dataclass
class FakeVault:
    """`get_vault` falso: `bundles` mapea `account_id -> TokenBundle`; `puts` registra
    cada `put()` (mismo patrón que `test_ads_router.py::FakeVault`, pero con un dict en
    vez de un único `bundle` porque este router maneja DOS cuentas — travel/tracking—
    simultáneamente en varios tests)."""

    bundles: dict[uuid.UUID, TokenBundle] = field(default_factory=dict)
    puts: list[tuple[uuid.UUID, uuid.UUID, TokenBundle]] = field(default_factory=list)

    async def put(self, tenant_id: uuid.UUID, account_id: uuid.UUID, bundle: TokenBundle) -> None:
        self.puts.append((tenant_id, account_id, bundle))
        self.bundles[account_id] = bundle

    async def get(self, tenant_id: uuid.UUID, account_id: uuid.UUID) -> TokenBundle | None:
        return self.bundles.get(account_id)


@pytest.fixture
def fake_session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def fake_vault() -> FakeVault:
    return FakeVault()


@pytest.fixture
def _mounted_app(app, fake_session: FakeSession, fake_vault: FakeVault):
    """`app` (de `conftest.py`) + `viajes.router` montado + `get_tenant_session`/
    `get_vault` reemplazados (mismo patrón que `test_ads_router.py`)."""
    app.include_router(viajes.router)
    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: fake_session
    app.dependency_overrides[edecan_deps.get_vault] = lambda: fake_vault
    return app


@pytest.fixture
async def client(_mounted_app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=_mounted_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def _con_flag_travel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ver el docstring del módulo: fuerza `tools.travel=True` en `PLAN_WITH_TRAVEL`
    solo durante el test (revierte solo), sin tocar `packages/schemas` (fuera del
    alcance de WP-V5-09)."""
    from edecan_schemas.plans import PLANES

    monkeypatch.setitem(PLANES[PLAN_WITH_TRAVEL].flags, viajes.FLAG_TOOLS_TRAVEL, True)


def _headers_con_acceso(**kw: Any) -> dict[str, str]:
    kw.setdefault("user_id", uuid.uuid4())
    kw.setdefault("tenant_id", uuid.uuid4())
    return auth_headers(plan_key=PLAN_WITH_TRAVEL, **kw)


# ---------------------------------------------------------------------------
# Autenticación / flag gate — en TODAS las rutas.
# ---------------------------------------------------------------------------


async def test_put_credentials_requires_authentication(client) -> None:
    response = await client.put("/v1/viajes/credentials", json={"api_key": "x", "api_secret": "y"})
    assert response.status_code == 401


async def test_status_requires_authentication(client) -> None:
    response = await client.get("/v1/viajes/status")
    assert response.status_code == 401


async def test_buscar_vuelos_requires_authentication(client) -> None:
    response = await client.get("/v1/viajes/buscar/vuelos?origen=BOG&destino=MIA&fecha=2026-08-01")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# PUT /v1/viajes/credentials — "pegar y validar" (Amadeus)
# ---------------------------------------------------------------------------


@respx.mock
async def test_put_credentials_rechaza_api_key_vacio(
    client, fake_vault: FakeVault, _con_flag_travel
) -> None:
    response = await client.put(
        "/v1/viajes/credentials",
        json={"api_key": "   ", "api_secret": API_SECRET, "validate": False},
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 400
    assert fake_vault.puts == []


@respx.mock
async def test_put_credentials_rechaza_api_secret_vacio(
    client, fake_vault: FakeVault, _con_flag_travel
) -> None:
    response = await client.put(
        "/v1/viajes/credentials",
        json={"api_key": API_KEY, "api_secret": "  ", "validate": False},
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 400
    assert fake_vault.puts == []


@respx.mock
async def test_put_credentials_rechaza_environment_invalido(
    client, fake_vault: FakeVault, _con_flag_travel
) -> None:
    response = await client.put(
        "/v1/viajes/credentials",
        json={
            "api_key": API_KEY,
            "api_secret": API_SECRET,
            "environment": "staging",
            "validate": False,
        },
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 400
    assert "environment" in response.json()["detail"].lower()
    assert fake_vault.puts == []


@respx.mock
async def test_put_credentials_validate_false_no_pega_a_la_red_y_guarda(
    client, fake_vault: FakeVault, _con_flag_travel
) -> None:
    tenant_id = uuid.uuid4()
    headers = _headers_con_acceso(tenant_id=tenant_id)

    response = await client.put(
        "/v1/viajes/credentials",
        json={"api_key": API_KEY, "api_secret": API_SECRET, "validate": False},
        headers=headers,
    )

    assert response.status_code == 204
    assert len(fake_vault.puts) == 1
    stored_tenant_id, _account_id, bundle = fake_vault.puts[0]
    assert stored_tenant_id == tenant_id
    assert bundle.token_type == "config"
    config = json.loads(bundle.access_token)
    assert config == {"api_key": API_KEY, "api_secret": API_SECRET, "environment": "test"}


@respx.mock
async def test_put_credentials_valida_contra_amadeus_real_y_guarda(
    client, fake_vault: FakeVault, _con_flag_travel
) -> None:
    respx.post(f"{AMADEUS_TEST_BASE}/v1/security/oauth2/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok-123", "expires_in": 1799})
    )

    response = await client.put(
        "/v1/viajes/credentials",
        json={"api_key": API_KEY, "api_secret": API_SECRET, "environment": "test"},
        headers=_headers_con_acceso(),
    )

    assert response.status_code == 204
    assert len(fake_vault.puts) == 1


@respx.mock
async def test_put_credentials_credencial_invalida_400_no_guarda_nada(
    client, fake_vault: FakeVault, _con_flag_travel
) -> None:
    respx.post(f"{AMADEUS_TEST_BASE}/v1/security/oauth2/token").mock(
        return_value=httpx.Response(
            401,
            json={
                "error": "invalid_client",
                "error_description": "Client credentials are invalid",
            },
        )
    )

    response = await client.put(
        "/v1/viajes/credentials",
        json={"api_key": "invalido", "api_secret": "invalido"},
        headers=_headers_con_acceso(),
    )

    assert response.status_code == 400
    assert fake_vault.puts == []


@respx.mock
async def test_put_credentials_amadeus_inalcanzable_400_no_guarda_nada(
    client, fake_vault: FakeVault, _con_flag_travel
) -> None:
    respx.post(f"{AMADEUS_TEST_BASE}/v1/security/oauth2/token").mock(
        side_effect=httpx.ConnectError("connection refused")
    )

    response = await client.put(
        "/v1/viajes/credentials",
        json={"api_key": API_KEY, "api_secret": API_SECRET},
        headers=_headers_con_acceso(),
    )

    assert response.status_code == 400
    assert fake_vault.puts == []


# ---------------------------------------------------------------------------
# DELETE /v1/viajes/credentials
# ---------------------------------------------------------------------------


async def test_delete_credentials_es_idempotente(client, _con_flag_travel) -> None:
    response = await client.delete("/v1/viajes/credentials", headers=_headers_con_acceso())
    assert response.status_code == 204


# ---------------------------------------------------------------------------
# PUT/DELETE /v1/viajes/rastreo/credentials — "pegar y validar" (AfterShip)
# ---------------------------------------------------------------------------


@respx.mock
async def test_put_rastreo_credentials_rechaza_api_key_vacio(
    client, fake_vault: FakeVault, _con_flag_travel
) -> None:
    response = await client.put(
        "/v1/viajes/rastreo/credentials",
        json={"api_key": "  ", "validate": False},
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 400
    assert fake_vault.puts == []


@respx.mock
async def test_put_rastreo_credentials_validate_false_guarda_sin_red(
    client, fake_vault: FakeVault, _con_flag_travel
) -> None:
    response = await client.put(
        "/v1/viajes/rastreo/credentials",
        json={"api_key": AFTERSHIP_KEY, "validate": False},
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 204
    assert len(fake_vault.puts) == 1
    _t, _a, bundle = fake_vault.puts[0]
    assert json.loads(bundle.access_token) == {"api_key": AFTERSHIP_KEY}


@respx.mock
async def test_put_rastreo_credentials_valida_con_get_couriers_y_guarda(
    client, fake_vault: FakeVault, _con_flag_travel
) -> None:
    ruta = respx.get(f"{AFTERSHIP_BASE}/couriers").mock(
        return_value=httpx.Response(200, json={"meta": {"code": 200}, "data": {"couriers": []}})
    )

    response = await client.put(
        "/v1/viajes/rastreo/credentials",
        json={"api_key": AFTERSHIP_KEY},
        headers=_headers_con_acceso(),
    )

    assert response.status_code == 204
    assert len(fake_vault.puts) == 1
    assert ruta.calls.last.request.headers["as-api-key"] == AFTERSHIP_KEY


@respx.mock
async def test_put_rastreo_credentials_invalida_400_no_guarda_nada(
    client, fake_vault: FakeVault, _con_flag_travel
) -> None:
    respx.get(f"{AFTERSHIP_BASE}/couriers").mock(
        return_value=httpx.Response(
            401, json={"meta": {"code": 401, "message": "Invalid API key"}, "data": {}}
        )
    )

    response = await client.put(
        "/v1/viajes/rastreo/credentials",
        json={"api_key": "invalido"},
        headers=_headers_con_acceso(),
    )

    assert response.status_code == 400
    assert "Invalid API key" in response.json()["detail"]
    assert fake_vault.puts == []


async def test_delete_rastreo_credentials_es_idempotente(client, _con_flag_travel) -> None:
    response = await client.delete("/v1/viajes/rastreo/credentials", headers=_headers_con_acceso())
    assert response.status_code == 204


# ---------------------------------------------------------------------------
# GET /v1/viajes/status
# ---------------------------------------------------------------------------


async def test_status_nada_configurado(client, _con_flag_travel) -> None:
    response = await client.get("/v1/viajes/status", headers=_headers_con_acceso())
    assert response.status_code == 200
    assert response.json() == {
        "travel": {"configured": False, "environment": None},
        "tracking": {"configured": False},
    }


async def test_status_solo_travel_configurado(
    client, fake_repo: FakeRepo, fake_vault: FakeVault, _con_flag_travel
) -> None:
    """`_find_account`/`GET /status` usan `Repo` (backed por `fake_repo`, ya inyectado
    globalmente en `app` por `conftest.py`) — NO `session.execute` (eso es solo para
    `buscar_vuelos`/`buscar_hoteles`/`rastreo`, que sí necesitan `ToolContext.session`
    para `get_tenant_travel_provider`)."""
    tenant_id = uuid.uuid4()
    account = await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key=viajes.TRAVEL_CONNECTOR_KEY,
        external_account_id=viajes.TRAVEL_CONNECTOR_KEY,
        display_name="Amadeus",
        scopes=[],
    )
    fake_vault.bundles[account["id"]] = TokenBundle(
        access_token=json.dumps({"api_key": "k", "api_secret": "s", "environment": "production"}),
        token_type="config",
    )

    response = await client.get(
        "/v1/viajes/status", headers=_headers_con_acceso(tenant_id=tenant_id)
    )

    assert response.status_code == 200
    body = response.json()
    assert body["travel"] == {"configured": True, "environment": "production"}
    assert body["tracking"] == {"configured": False}


async def test_status_travel_y_tracking_configurados(
    client, fake_repo: FakeRepo, fake_vault: FakeVault, _con_flag_travel
) -> None:
    tenant_id = uuid.uuid4()
    travel_account = await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key=viajes.TRAVEL_CONNECTOR_KEY,
        external_account_id=viajes.TRAVEL_CONNECTOR_KEY,
        display_name="Amadeus",
        scopes=[],
    )
    tracking_account = await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key=viajes.TRACKING_CONNECTOR_KEY,
        external_account_id=viajes.TRACKING_CONNECTOR_KEY,
        display_name="AfterShip",
        scopes=[],
    )
    fake_vault.bundles[travel_account["id"]] = TokenBundle(
        access_token=json.dumps({"api_key": "k", "api_secret": "s", "environment": "test"}),
        token_type="config",
    )
    fake_vault.bundles[tracking_account["id"]] = TokenBundle(
        access_token=json.dumps({"api_key": "k"}), token_type="config"
    )

    response = await client.get(
        "/v1/viajes/status", headers=_headers_con_acceso(tenant_id=tenant_id)
    )

    assert response.status_code == 200
    body = response.json()
    assert body["travel"]["configured"] is True
    assert body["tracking"]["configured"] is True


# ---------------------------------------------------------------------------
# GET /v1/viajes/buscar/vuelos, /buscar/hoteles, /rastreo/{numero} — proveedor del
# tenant, con fallback a stub cuando no hay credencial (integración de punta a punta,
# sin monkeypatch de proveedor).
# ---------------------------------------------------------------------------


async def test_buscar_vuelos_sin_credenciales_usa_stub(client, _con_flag_travel) -> None:
    response = await client.get(
        "/v1/viajes/buscar/vuelos?origen=BOG&destino=MIA&fecha=2026-08-01",
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["ofertas"]) >= 1
    assert "ejemplo" in body["ofertas"][0]["aerolinea"].lower()


async def test_buscar_hoteles_sin_credenciales_usa_stub(client, _con_flag_travel) -> None:
    response = await client.get(
        "/v1/viajes/buscar/hoteles?ciudad=PAR&checkin=2026-09-01&checkout=2026-09-05",
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["ofertas"]) >= 1
    assert "ejemplo" in body["ofertas"][0]["nombre"].lower()


async def test_rastreo_sin_credenciales_usa_stub(client, _con_flag_travel) -> None:
    response = await client.get("/v1/viajes/rastreo/999999", headers=_headers_con_acceso())
    assert response.status_code == 200
    body = response.json()
    assert body["estado"]
    assert len(body["checkpoints"]) >= 1


async def test_buscar_vuelos_con_proveedor_monkeypatcheado(
    client, monkeypatch: pytest.MonkeyPatch, _con_flag_travel
) -> None:
    from edecan_travel.amadeus import VueloOferta

    oferta = VueloOferta(
        id="off-real",
        aerolinea="AV",
        salida="2026-08-01T08:00:00",
        llegada="2026-08-01T10:00:00",
        origen="BOG",
        destino="MIA",
        escalas=0,
        precio_total="199.00",
        moneda="USD",
    )

    class _FakeProvider:
        async def buscar_vuelos(self, *args: Any, **kwargs: Any) -> list[VueloOferta]:
            return [oferta]

    async def _resolver(ctx: Any) -> _FakeProvider:
        return _FakeProvider()

    monkeypatch.setattr(viajes, "get_tenant_travel_provider", _resolver)

    response = await client.get(
        "/v1/viajes/buscar/vuelos?origen=BOG&destino=MIA&fecha=2026-08-01",
        headers=_headers_con_acceso(),
    )

    assert response.status_code == 200
    assert response.json()["ofertas"][0]["id"] == "off-real"


async def test_buscar_vuelos_error_del_proveedor_devuelve_502(
    client, monkeypatch: pytest.MonkeyPatch, _con_flag_travel
) -> None:
    class _FakeProvider:
        async def buscar_vuelos(self, *args: Any, **kwargs: Any) -> list[Any]:
            raise RuntimeError("Amadeus caído")

    async def _resolver(ctx: Any) -> _FakeProvider:
        return _FakeProvider()

    monkeypatch.setattr(viajes, "get_tenant_travel_provider", _resolver)

    response = await client.get(
        "/v1/viajes/buscar/vuelos?origen=BOG&destino=MIA&fecha=2026-08-01",
        headers=_headers_con_acceso(),
    )

    assert response.status_code == 502
    assert "Amadeus caído" in response.json()["detail"]


async def test_rastreo_error_del_proveedor_devuelve_502(
    client, monkeypatch: pytest.MonkeyPatch, _con_flag_travel
) -> None:
    class _FakeProvider:
        async def rastrear(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("AfterShip caído")

    async def _resolver(ctx: Any) -> _FakeProvider:
        return _FakeProvider()

    monkeypatch.setattr(viajes, "get_tenant_tracking_provider", _resolver)

    response = await client.get("/v1/viajes/rastreo/999", headers=_headers_con_acceso())

    assert response.status_code == 502
    assert "AfterShip caído" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Anti-fuga: sin credencial del tenant, un valor centinela "de plataforma" (aunque
# existiera en el entorno del proceso) NUNCA debe llegar a la respuesta -- mismo
# espíritu que el hallazgo crítico de v4 en
# `edecan_llm.router.LLMRouter._build_provider_from_config`
# (`HOTFIXES_PENDIENTES.md`): un campo vacío del tenant jamás cae a una credencial de
# plataforma, ni siquiera si esa credencial "existiera" en el entorno del proceso.
# ---------------------------------------------------------------------------


async def test_buscar_vuelos_nunca_usa_centinela_de_plataforma_del_entorno(
    client, monkeypatch: pytest.MonkeyPatch, _con_flag_travel
) -> None:
    centinela = "CENTINELA-DE-PLATAFORMA-NUNCA-DEBERIA-APARECER"
    monkeypatch.setenv("AMADEUS_API_KEY", centinela)
    monkeypatch.setenv("AMADEUS_API_SECRET", centinela)

    response = await client.get(
        "/v1/viajes/buscar/vuelos?origen=BOG&destino=MIA&fecha=2026-08-01",
        headers=_headers_con_acceso(),
    )

    assert response.status_code == 200
    assert centinela not in response.text
    # Confirma además que de verdad cayó al stub (nunca a un AmadeusClient construido
    # con alguna credencial fantasma leída del entorno).
    assert "ejemplo" in response.text.lower()


async def test_rastreo_nunca_usa_centinela_de_plataforma_del_entorno(
    client, monkeypatch: pytest.MonkeyPatch, _con_flag_travel
) -> None:
    centinela = "CENTINELA-DE-PLATAFORMA-NUNCA-DEBERIA-APARECER"
    monkeypatch.setenv("AFTERSHIP_API_KEY", centinela)

    response = await client.get("/v1/viajes/rastreo/999999", headers=_headers_con_acceso())

    assert response.status_code == 200
    assert centinela not in response.text


async def test_status_nunca_reporta_configurado_sin_bundle_real(
    client, fake_repo: FakeRepo, _con_flag_travel
) -> None:
    """Cuenta `connector_accounts` existe (el tenant alguna vez la creó) pero el vault
    no tiene ningún bundle real detrás (`fake_vault` sin `bundles` poblado) -- status
    debe seguir reportando `configured=False`, nunca inventar un estado 'conectado'."""
    tenant_id = uuid.uuid4()
    await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key=viajes.TRAVEL_CONNECTOR_KEY,
        external_account_id=viajes.TRAVEL_CONNECTOR_KEY,
        display_name="Amadeus",
        scopes=[],
    )

    response = await client.get(
        "/v1/viajes/status", headers=_headers_con_acceso(tenant_id=tenant_id)
    )

    assert response.status_code == 200
    assert response.json()["travel"]["configured"] is False
