"""`edecan_api.routers.ads` — `/v1/ads/*` (Meta Marketing API bring-your-own,
`ARCHITECTURE.md` §13, WP-V4-07; ver el docstring del propio router para el
contrato completo, especialmente el guardrail de dinero de
`confirmar_borrador`).

`edecan-ads` todavía no es una dependencia declarada de `apps/api`
(`apps/api/pyproject.toml`, fuera de las rutas que este paquete de trabajo
puede tocar) — así que, igual que `test_commerce_router.py` tuvo que hacer en
su momento, este módulo agrega `packages/ads` a `sys.path` por su cuenta,
antes de importar `edecan_api.routers.ads`. `edecan_api.main.create_app()` YA
monta `ads.router` de forma defensiva (`V4_ROUTER_NAMES`, WP-V4-01 — ver
`test_router_ya_montado_por_create_app_sin_necesidad_de_montaje_local` más
abajo, que lo prueba directo). Aun así, el fixture `_mounted_app` monta el
router también de forma explícita sobre la `app` de `conftest.py` para el
resto de los tests (mismo criterio que `test_commerce_router.py`/
`test_erp_router.py`): así no dependen de que `edecan_ads` ya esté en
`sys.path` en el momento exacto en que `create_app()` corre.

Modelo de precio de pago único (2026-07-09, `edecan_schemas.plans`
docstring): `tools.ads` (y cualquier otro flag) ya está en `True` en las 4
entradas de `PLANES` por igual — no hay más "flag apagado" que probar. Los
tests usan `auth_headers(plan_key="hosted_pro")` como cualquier `plan_key`
válido (mismo criterio que `test_erp_router.py`/`test_commerce_router.py`).

`FakeSession`/`FakeVault` locales (duplicadas a propósito, `ARCHITECTURE.md`
§10.1) reemplazan `edecan_api.deps.get_tenant_session`/`get_vault`; nunca se
toca `conftest.py`/`api_fakes.py`/`edecan_api/repo.py`. Para
`confirmar_borrador`, la mayoría de los tests sustituyen
`ads.get_tenant_ads_provider` con `monkeypatch.setattr` (mismo patrón que
`test_conversations.py` sustituye `Agent`: el símbolo ya importado dentro del
propio módulo del router) — así se prueba la orquestación SQL/auditoría/
guardrail del router de forma aislada, sin tener que encadenar además la
resolución completa del proveedor (que `packages/ads/tests/test_providers.py`
ya cubre en detalle). Un test adicional (`test_confirmar_sin_credenciales_
usa_stub_de_todos_modos_queda_pausada`) ejercita el camino SIN monkeypatch,
con `StubAdsProvider` real, para probar la integración de punta a punta al
menos una vez.
"""

from __future__ import annotations

import json
import sys
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from conftest import auth_headers
from edecan_schemas import TokenBundle
from httpx import ASGITransport, AsyncClient

_ADS_SRC = str(Path(__file__).resolve().parents[3] / "packages" / "ads")
if _ADS_SRC not in sys.path:
    sys.path.insert(0, _ADS_SRC)

import edecan_api.deps as edecan_deps  # noqa: E402
from edecan_api.routers import ads  # noqa: E402

PLAN_WITH_ADS = "hosted_pro"

AD_ACCOUNT_ID = "123456789"
ACCESS_TOKEN = "token-de-prueba"
META_BASE = ads.META_GRAPH_BASE_URL


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
    """`get_tenant_session` falso: cada `execute()` consume la siguiente
    respuesta programada, EN EL ORDEN EXACTO en que el router las pide (ver
    el docstring de cada test que la usa directamente)."""

    respuestas: list[list[dict[str, Any]]] = field(default_factory=list)
    llamadas: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    commits: int = 0

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> FakeResult:
        self.llamadas.append((str(stmt), dict(params or {})))
        filas = self.respuestas.pop(0) if self.respuestas else []
        return FakeResult(filas)

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        self.commits += 1


@dataclass
class FakeVault:
    """`get_vault` falso: `bundle` es lo que devuelve `get()`; `puts` registra
    cada `put()` (mismo patrón que `test_smarthome_router.py::FakeVault`)."""

    bundle: TokenBundle | None = None
    puts: list[tuple[uuid.UUID, uuid.UUID, TokenBundle]] = field(default_factory=list)

    async def put(self, tenant_id: uuid.UUID, account_id: uuid.UUID, bundle: TokenBundle) -> None:
        self.puts.append((tenant_id, account_id, bundle))
        self.bundle = bundle

    async def get(self, tenant_id: uuid.UUID, account_id: uuid.UUID) -> TokenBundle | None:
        return self.bundle


class _FakeAdsProvider:
    """Doble de `edecan_ads.providers.AdsProvider`: inyectado vía
    `monkeypatch.setattr(ads, "get_tenant_ads_provider", ...)` (símbolo ya
    importado dentro del propio módulo del router, mismo patrón que
    `test_conversations.py` sustituye `Agent`)."""

    def __init__(
        self,
        *,
        campanas: list[dict[str, Any]] | None = None,
        metricas: dict[str, Any] | None = None,
        external_id: str = "camp-fake-123",
        error: Exception | None = None,
    ) -> None:
        self._campanas = campanas if campanas is not None else []
        self._metricas = metricas or {"spend": "0", "impressions": "0", "clicks": "0"}
        self._external_id = external_id
        self._error = error
        self.create_campaign_paused_llamadas: list[tuple[Any, ...]] = []

    async def list_campaigns(self) -> list[dict[str, Any]]:
        if self._error:
            raise self._error
        return self._campanas

    async def insights(self, date_preset: str = "last_30d") -> dict[str, Any]:
        if self._error:
            raise self._error
        return self._metricas

    async def create_campaign_paused(
        self, nombre: str, objetivo: str, presupuesto_diario: Any, moneda: str, payload: Any
    ) -> str:
        self.create_campaign_paused_llamadas.append(
            (nombre, objetivo, presupuesto_diario, moneda, payload)
        )
        if self._error:
            raise self._error
        return self._external_id


def _fake_resolver(provider: _FakeAdsProvider):
    async def _resolve(ctx: Any) -> _FakeAdsProvider:
        return provider

    return _resolve


def _draft_row(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "provider": "meta",
        "nombre": "Campaña de prueba",
        "objetivo": "OUTCOME_TRAFFIC",
        "presupuesto_diario": Decimal("50.00"),
        "moneda": "USD",
        "payload": {},
        "status": "draft",
        "external_id": None,
        "error": None,
        "confirmed_at": None,
        "pushed_at": None,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return base


@pytest.fixture
def fake_session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def fake_vault() -> FakeVault:
    return FakeVault()


@pytest.fixture
def _mounted_app(app, fake_session: FakeSession, fake_vault: FakeVault):
    """`app` (de `conftest.py`) + `ads.router` montado + `get_tenant_session`/
    `get_vault` reemplazados (mismo patrón que `test_commerce_router.py`/
    `test_erp_router.py`/`test_smarthome_router.py`)."""
    app.include_router(ads.router)
    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: fake_session
    app.dependency_overrides[edecan_deps.get_vault] = lambda: fake_vault
    return app


@pytest.fixture
async def client(_mounted_app) -> AsyncIterator[AsyncClient]:
    # Sombrea a propósito el fixture `client` de `conftest.py`: ese vive sobre
    # `app` sin `ads.router` montado explícitamente (aunque `create_app()` YA
    # lo monta solo, ver `test_router_ya_montado_por_create_app_...` abajo).
    transport = ASGITransport(app=_mounted_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _headers_con_acceso(**kw: Any) -> dict[str, str]:
    kw.setdefault("user_id", uuid.uuid4())
    kw.setdefault("tenant_id", uuid.uuid4())
    return auth_headers(plan_key=PLAN_WITH_ADS, **kw)


# ---------------------------------------------------------------------------
# `create_app()` ya monta el router solo (V4_ROUTER_NAMES, WP-V4-01) — sin
# depender del montaje local `_mounted_app` de arriba.
# ---------------------------------------------------------------------------


async def test_router_ya_montado_por_create_app_sin_necesidad_de_montaje_local(app) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/v1/ads/status")
    # 401 (no 404): el path operation existe -- get_current_user corta antes
    # de necesitar sesión/vault real, así que este test no depende de Postgres.
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Autenticación / flag gate — en TODAS las rutas.
# ---------------------------------------------------------------------------


async def test_put_credentials_requires_authentication(client) -> None:
    response = await client.put(
        "/v1/ads/credentials", json={"access_token": "x", "ad_account_id": "1"}
    )
    assert response.status_code == 401


async def test_delete_credentials_requires_authentication(client) -> None:
    response = await client.delete("/v1/ads/credentials")
    assert response.status_code == 401


async def test_status_requires_authentication(client) -> None:
    response = await client.get("/v1/ads/status")
    assert response.status_code == 401


async def test_resumen_requires_authentication(client) -> None:
    response = await client.get("/v1/ads/resumen")
    assert response.status_code == 401


async def test_borradores_requires_authentication(client) -> None:
    response = await client.get("/v1/ads/borradores")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# PUT /v1/ads/credentials — "pegar y validar"
# ---------------------------------------------------------------------------


@respx.mock
async def test_put_rechaza_access_token_vacio(client, fake_vault: FakeVault) -> None:
    response = await client.put(
        "/v1/ads/credentials",
        json={"access_token": "   ", "ad_account_id": AD_ACCOUNT_ID, "validate": False},
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 400
    assert fake_vault.puts == []


@respx.mock
async def test_put_rechaza_ad_account_id_vacio(client, fake_vault: FakeVault) -> None:
    response = await client.put(
        "/v1/ads/credentials",
        json={"access_token": ACCESS_TOKEN, "ad_account_id": "   ", "validate": False},
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 400
    assert fake_vault.puts == []


@respx.mock
async def test_put_validate_false_no_pega_a_la_red_y_guarda_normalizado(
    client, fake_vault: FakeVault
) -> None:
    tenant_id = uuid.uuid4()
    headers = _headers_con_acceso(tenant_id=tenant_id)

    response = await client.put(
        "/v1/ads/credentials",
        json={
            "access_token": ACCESS_TOKEN,
            "ad_account_id": f"act_{AD_ACCOUNT_ID}",
            "validate": False,
        },
        headers=headers,
    )

    assert response.status_code == 204
    assert len(fake_vault.puts) == 1
    stored_tenant_id, _account_id, bundle = fake_vault.puts[0]
    assert stored_tenant_id == tenant_id
    assert bundle.token_type == "config"
    assert bundle.scopes == ["meta"]
    config = json.loads(bundle.access_token)
    assert config == {"access_token": ACCESS_TOKEN, "ad_account_id": AD_ACCOUNT_ID}  # sin 'act_'


@respx.mock
async def test_put_valida_contra_meta_real_y_guarda(client, fake_vault: FakeVault) -> None:
    respx.get(f"{META_BASE}/me").mock(return_value=httpx.Response(200, json={"id": "user-1"}))
    respx.get(f"{META_BASE}/act_{AD_ACCOUNT_ID}").mock(
        return_value=httpx.Response(200, json={"name": "Mi cuenta", "currency": "USD"})
    )

    response = await client.put(
        "/v1/ads/credentials",
        json={"access_token": ACCESS_TOKEN, "ad_account_id": AD_ACCOUNT_ID},
        headers=_headers_con_acceso(),
    )

    assert response.status_code == 204
    assert len(fake_vault.puts) == 1


@respx.mock
async def test_put_token_rechazado_400_no_guarda_nada(client, fake_vault: FakeVault) -> None:
    respx.get(f"{META_BASE}/me").mock(
        return_value=httpx.Response(
            400, json={"error": {"message": "Error validating access token"}}
        )
    )

    response = await client.put(
        "/v1/ads/credentials",
        json={"access_token": "invalido", "ad_account_id": AD_ACCOUNT_ID},
        headers=_headers_con_acceso(),
    )

    assert response.status_code == 400
    assert "Error validating access token" in response.json()["detail"]
    assert fake_vault.puts == []


@respx.mock
async def test_put_cuenta_rechazada_400_no_guarda_nada(client, fake_vault: FakeVault) -> None:
    respx.get(f"{META_BASE}/me").mock(return_value=httpx.Response(200, json={"id": "user-1"}))
    respx.get(f"{META_BASE}/act_{AD_ACCOUNT_ID}").mock(
        return_value=httpx.Response(400, json={"error": {"message": "Unsupported get request."}})
    )

    response = await client.put(
        "/v1/ads/credentials",
        json={"access_token": ACCESS_TOKEN, "ad_account_id": AD_ACCOUNT_ID},
        headers=_headers_con_acceso(),
    )

    assert response.status_code == 400
    assert fake_vault.puts == []


@respx.mock
async def test_put_meta_inalcanzable_400_no_guarda_nada(client, fake_vault: FakeVault) -> None:
    respx.get(f"{META_BASE}/me").mock(side_effect=httpx.ConnectError("connection refused"))

    response = await client.put(
        "/v1/ads/credentials",
        json={"access_token": ACCESS_TOKEN, "ad_account_id": AD_ACCOUNT_ID},
        headers=_headers_con_acceso(),
    )

    assert response.status_code == 400
    assert fake_vault.puts == []


# ---------------------------------------------------------------------------
# DELETE /v1/ads/credentials
# ---------------------------------------------------------------------------


@respx.mock
async def test_delete_es_idempotente(client) -> None:
    response = await client.delete("/v1/ads/credentials", headers=_headers_con_acceso())
    assert response.status_code == 204


# ---------------------------------------------------------------------------
# GET /v1/ads/status
# ---------------------------------------------------------------------------


@respx.mock
async def test_status_no_configurado(client) -> None:
    response = await client.get("/v1/ads/status", headers=_headers_con_acceso())
    assert response.status_code == 200
    assert response.json() == {
        "configured": False,
        "ad_account_id": None,
        "nombre_cuenta": None,
        "moneda": None,
        "reachable": None,
    }


@respx.mock
async def test_status_configurado_y_reachable(client) -> None:
    headers = _headers_con_acceso()
    await client.put(
        "/v1/ads/credentials",
        json={"access_token": ACCESS_TOKEN, "ad_account_id": AD_ACCOUNT_ID, "validate": False},
        headers=headers,
    )
    respx.get(f"{META_BASE}/act_{AD_ACCOUNT_ID}").mock(
        return_value=httpx.Response(200, json={"name": "Mi cuenta", "currency": "USD"})
    )

    response = await client.get("/v1/ads/status", headers=headers)

    assert response.status_code == 200
    assert response.json() == {
        "configured": True,
        "ad_account_id": AD_ACCOUNT_ID,
        "nombre_cuenta": "Mi cuenta",
        "moneda": "USD",
        "reachable": True,
    }


@respx.mock
async def test_status_token_vencido_reachable_false(client) -> None:
    headers = _headers_con_acceso()
    await client.put(
        "/v1/ads/credentials",
        json={"access_token": ACCESS_TOKEN, "ad_account_id": AD_ACCOUNT_ID, "validate": False},
        headers=headers,
    )
    respx.get(f"{META_BASE}/act_{AD_ACCOUNT_ID}").mock(return_value=httpx.Response(401))

    response = await client.get("/v1/ads/status", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["reachable"] is False


@respx.mock
async def test_status_red_falla_reachable_null_nunca_500(client) -> None:
    headers = _headers_con_acceso()
    await client.put(
        "/v1/ads/credentials",
        json={"access_token": ACCESS_TOKEN, "ad_account_id": AD_ACCOUNT_ID, "validate": False},
        headers=headers,
    )
    respx.get(f"{META_BASE}/act_{AD_ACCOUNT_ID}").mock(side_effect=httpx.ConnectTimeout("timeout"))

    response = await client.get("/v1/ads/status", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["reachable"] is None


# ---------------------------------------------------------------------------
# GET /v1/ads/resumen — proveedor del tenant.
# ---------------------------------------------------------------------------


async def test_resumen_sin_credenciales_usa_stub(client) -> None:
    """`fake_session`/`fake_vault` vacíos -> `get_tenant_ads_provider` real
    (sin monkeypatch) cae a `StubAdsProvider` -- integración de punta a
    punta, sin red."""
    response = await client.get("/v1/ads/resumen", headers=_headers_con_acceso())
    assert response.status_code == 200
    body = response.json()
    assert len(body["campanas"]) >= 1
    assert body["periodo"] == "last_30d"


async def test_resumen_con_proveedor_monkeypatcheado(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _FakeAdsProvider(
        campanas=[{"name": "Campaña real", "status": "PAUSED", "objective": "OUTCOME_TRAFFIC"}],
        metricas={"spend": "10.00", "impressions": "500", "clicks": "5"},
    )
    monkeypatch.setattr(ads, "get_tenant_ads_provider", _fake_resolver(provider))

    response = await client.get("/v1/ads/resumen?periodo=last_7d", headers=_headers_con_acceso())

    assert response.status_code == 200
    body = response.json()
    assert body["campanas"][0]["name"] == "Campaña real"
    assert body["metricas"]["spend"] == "10.00"
    assert body["periodo"] == "last_7d"


async def test_resumen_error_del_proveedor_devuelve_502(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _FakeAdsProvider(error=RuntimeError("Meta caído"))
    monkeypatch.setattr(ads, "get_tenant_ads_provider", _fake_resolver(provider))

    response = await client.get("/v1/ads/resumen", headers=_headers_con_acceso())

    assert response.status_code == 502
    assert "Meta caído" in response.json()["detail"]


# ---------------------------------------------------------------------------
# GET /v1/ads/borradores
# ---------------------------------------------------------------------------


async def test_list_borradores_filtra_por_tenant_y_usuario(
    client, fake_session: FakeSession
) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    fila = _draft_row(tenant_id=tenant_id, user_id=user_id)
    fake_session.respuestas = [[fila]]

    response = await client.get(
        "/v1/ads/borradores", headers=_headers_con_acceso(tenant_id=tenant_id, user_id=user_id)
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["nombre"] == "Campaña de prueba"
    _sql, params = fake_session.llamadas[0]
    assert params["tenant_id"] == str(tenant_id)
    assert params["user_id"] == str(user_id)


async def test_list_borradores_vacio(client, fake_session: FakeSession) -> None:
    fake_session.respuestas = [[]]
    response = await client.get("/v1/ads/borradores", headers=_headers_con_acceso())
    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# POST /v1/ads/borradores/{id}/confirmar — EL guardrail de dinero.
# ---------------------------------------------------------------------------


async def test_confirmar_borrador_inexistente_404(client, fake_session: FakeSession) -> None:
    fake_session.respuestas = [[]]  # SELECT no encuentra nada
    response = await client.post(
        f"/v1/ads/borradores/{uuid.uuid4()}/confirmar", headers=_headers_con_acceso()
    )
    assert response.status_code == 404


async def test_confirmar_borrador_no_draft_409(client, fake_session: FakeSession) -> None:
    fake_session.respuestas = [[_draft_row(status="pushed")]]
    response = await client.post(
        f"/v1/ads/borradores/{uuid.uuid4()}/confirmar", headers=_headers_con_acceso()
    )
    assert response.status_code == 409


async def test_confirmar_ok_marca_pushed_y_avisa_que_esta_pausada(
    client, fake_session: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    draft_id = uuid.uuid4()
    draft = _draft_row(id=draft_id, status="draft")
    confirmado = _draft_row(id=draft_id, status="confirmed")
    pusheado = _draft_row(id=draft_id, status="pushed", external_id="camp-real-1")
    fake_session.respuestas = [
        [draft],  # 1: SELECT (_get_draft_or_404)
        [confirmado],  # 2: UPDATE -> confirmed RETURNING *
        [],  # 3: INSERT audit_log "ads.draft.confirmed"
        [pusheado],  # 4: UPDATE -> pushed RETURNING *
        [],  # 5: INSERT audit_log "ads.draft.pushed"
    ]
    provider = _FakeAdsProvider(external_id="camp-real-1")
    monkeypatch.setattr(ads, "get_tenant_ads_provider", _fake_resolver(provider))

    response = await client.post(
        f"/v1/ads/borradores/{draft_id}/confirmar", headers=_headers_con_acceso()
    )

    assert response.status_code == 200
    body = response.json()
    assert body["borrador"]["status"] == "pushed"
    assert body["borrador"]["external_id"] == "camp-real-1"
    assert "PAUSA" in body["mensaje"].upper()
    assert "ads manager" in body["mensaje"].lower()
    assert fake_session.commits == 0  # camino feliz: commit implícito, no explícito.

    # La campaña se pidió con los datos EXACTOS del borrador.
    assert provider.create_campaign_paused_llamadas == [
        ("Campaña de prueba", "OUTCOME_TRAFFIC", Decimal("50.00"), "USD", {})
    ]

    # Orden exacto de las 5 llamadas de sesión.
    acciones = [sql.split()[0] for sql, _ in fake_session.llamadas]
    assert acciones == ["SELECT", "UPDATE", "INSERT", "UPDATE", "INSERT"]


async def test_confirmar_error_de_meta_marca_error_y_commitea_antes_del_502(
    client, fake_session: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El guardrail de `HOTFIXES_PENDIENTES.md` puntos 8/9: si Meta rechaza el
    push, el borrador queda en `status='error'` con el commit hecho ANTES del
    `raise` -- si no, el rollback automático de la transacción de la request
    se llevaría puesta la evidencia de que el usuario confirmó."""
    draft_id = uuid.uuid4()
    draft = _draft_row(id=draft_id, status="draft")
    confirmado = _draft_row(id=draft_id, status="confirmed")
    fake_session.respuestas = [
        [draft],  # 1: SELECT
        [confirmado],  # 2: UPDATE -> confirmed
        [],  # 3: INSERT audit "confirmed"
        [],  # 4: UPDATE -> error (el handler no lee el resultado, pero igual consume la cola)
        [],  # 5: INSERT audit "error"
    ]
    provider = _FakeAdsProvider(error=RuntimeError("Invalid parameter"))
    monkeypatch.setattr(ads, "get_tenant_ads_provider", _fake_resolver(provider))

    response = await client.post(
        f"/v1/ads/borradores/{draft_id}/confirmar", headers=_headers_con_acceso()
    )

    assert response.status_code == 502
    assert "Invalid parameter" in response.json()["detail"]
    assert fake_session.commits == 1  # commit explícito ANTES del raise.

    acciones = [sql.split()[0] for sql, _ in fake_session.llamadas]
    assert acciones == ["SELECT", "UPDATE", "INSERT", "UPDATE", "INSERT"]
    # La 4ta llamada (UPDATE -> error) deja constancia del mensaje de Meta.
    sql_error, params_error = fake_session.llamadas[3]
    assert "'error'" in sql_error
    assert params_error["error"] == "Invalid parameter"


async def test_confirmar_sin_credenciales_usa_stub_de_todos_modos_queda_pausada(
    client, fake_session: FakeSession
) -> None:
    """Sin monkeypatch: ejercita `get_tenant_ads_provider` real -> sin cuenta
    de Meta conectada -> `StubAdsProvider` -- la campaña "se crea" (falsa,
    determinista, sin red) igual en pausa."""
    draft_id = uuid.uuid4()
    draft = _draft_row(id=draft_id, status="draft")
    confirmado = _draft_row(id=draft_id, status="confirmed")
    pusheado = _draft_row(id=draft_id, status="pushed", external_id="stub-campaign-abc")
    fake_session.respuestas = [
        [draft],  # 1: SELECT
        [confirmado],  # 2: UPDATE -> confirmed
        [],  # 3: INSERT audit "confirmed"
        [],  # 4: SELECT connector_accounts (get_tenant_ads_provider) -- vacío -> stub
        [pusheado],  # 5: UPDATE -> pushed
        [],  # 6: INSERT audit "pushed"
    ]

    response = await client.post(
        f"/v1/ads/borradores/{draft_id}/confirmar", headers=_headers_con_acceso()
    )

    assert response.status_code == 200
    assert response.json()["borrador"]["external_id"] == "stub-campaign-abc"


# ---------------------------------------------------------------------------
# POST /v1/ads/borradores/{id}/cancelar
# ---------------------------------------------------------------------------


async def test_cancelar_desde_draft_ok(client, fake_session: FakeSession) -> None:
    draft_id = uuid.uuid4()
    draft = _draft_row(id=draft_id, status="draft")
    cancelado = _draft_row(id=draft_id, status="cancelled")
    fake_session.respuestas = [[draft], [cancelado], []]

    response = await client.post(
        f"/v1/ads/borradores/{draft_id}/cancelar", headers=_headers_con_acceso()
    )

    assert response.status_code == 200
    assert response.json()["borrador"]["status"] == "cancelled"


async def test_cancelar_desde_pushed_409(client, fake_session: FakeSession) -> None:
    draft_id = uuid.uuid4()
    fake_session.respuestas = [[_draft_row(id=draft_id, status="pushed")]]

    response = await client.post(
        f"/v1/ads/borradores/{draft_id}/cancelar", headers=_headers_con_acceso()
    )

    assert response.status_code == 409


async def test_cancelar_inexistente_404(client, fake_session: FakeSession) -> None:
    fake_session.respuestas = [[]]
    response = await client.post(
        f"/v1/ads/borradores/{uuid.uuid4()}/cancelar", headers=_headers_con_acceso()
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Ningún código path puede mandar un status distinto de PAUSED a Meta —
# verificación adicional a nivel de router (el detalle exhaustivo vive en
# `packages/ads/tests/test_providers.py`): el propio `_FakeAdsProvider`
# registra los argumentos EXACTOS que el router le pasó, y `create_campaign_
# paused` nunca recibe ni envía un `status` explícito desde este router — es
# el proveedor (real o stub) quien decide, y siempre en pausa.
# ---------------------------------------------------------------------------


async def test_confirmar_nunca_pasa_un_status_explicito_al_proveedor(
    client, fake_session: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    draft_id = uuid.uuid4()
    draft = _draft_row(id=draft_id, status="draft")
    confirmado = _draft_row(id=draft_id, status="confirmed")
    pusheado = _draft_row(id=draft_id, status="pushed")
    fake_session.respuestas = [[draft], [confirmado], [], [pusheado], []]
    provider = _FakeAdsProvider()
    monkeypatch.setattr(ads, "get_tenant_ads_provider", _fake_resolver(provider))

    await client.post(f"/v1/ads/borradores/{draft_id}/confirmar", headers=_headers_con_acceso())

    llamada = provider.create_campaign_paused_llamadas[0]
    payload = llamada[4]
    assert "status" not in payload
