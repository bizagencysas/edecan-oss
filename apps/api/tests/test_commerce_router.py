"""`edecan_api.routers.commerce` — `/v1/commerce/*` (`ROADMAP_V2.md` §7 WP-V2-10; ver el
docstring del propio router para el diseño completo y qué es real vs. pendiente de la
migración `0003_v2_expansion`).

`edecan_api.main.create_app()` YA monta `commerce.router` de forma defensiva
(`V2_ROUTER_NAMES`, `ROADMAP_V2.md` §7.6, WP-V2-01 — ver `test_v2_mounting.py`) siempre que
`edecan_commerce` sea importable en el momento en que corre `create_app()`. Aun así, igual
que `test_remote_router.py` (WP-V2-09) sigue haciendo con `remote.router` aunque ESE router
también quedó cubierto por el mismo mecanismo, el fixture `_mounted_app` de aquí abajo monta
el router manualmente y de forma explícita sobre la `app` de `conftest.py`: así estos tests
no dependen de que otro archivo se haya importado antes (y por lo tanto de que `sys.path` ya
tenga `packages/commerce`, ver más abajo) para que `create_app()` encuentre el módulo —
`app.include_router()` es idempotente en la práctica para este propósito (dos registros del
mismo `router` a lo sumo duplican rutas idénticas, nunca las contradicen).

Modelo de precio de pago único (2026-07-09, `edecan_schemas.plans` docstring): el flag
`commerce.orders` (y cualquier otro flag) ya está en `True` en las 4 entradas de `PLANES`
por igual — no hay más "flag apagado" que probar. Los tests usan `auth_headers(plan_key=...)`
con cualquier `plan_key` real (`hosted_pro`, `free_selfhost`, etc.) en vez de tener que
fabricar un `CurrentUser` a mano.

`fake_session` reemplaza `edecan_api.deps.get_tenant_session` (mismo patrón que
`test_consents.py`): una `FakeSession`/`FakeResult` locales (duplicadas a propósito, ver
`ARCHITECTURE.md` §10.1) que consumen una cola de respuestas programadas, en el orden EXACTO
en que el router las pide (ver el docstring de cada test). `PaperBroker` se sustituye por un
`_FakeBroker` (`monkeypatch.setattr`, mismo criterio que `test_conversations.py` sustituye
`Agent`): el router en sí solo necesita probar que LO LLAMA y qué hace con su resultado —
`packages/commerce/tests/test_paper.py` ya prueba la aritmética/SQL de `PaperBroker` en
detalle, no hace falta re-simularla aquí.

`edecan_api.routers.commerce` importa `edecan_commerce` a nivel de módulo (necesita la
clase real `PaperBroker` para poder sustituirla con `monkeypatch`, y las funciones reales
de `.budgets`). `apps/api/tests/_stub_siblings.py` (que agrega `packages/{schemas,db,llm,
connectors,voice,core,toolkit}` a `sys.path` para que un `pytest` corrido solo sobre
`apps/api` no dependa de un `uv sync` completo del workspace) todavía no incluye
`"commerce"` en su tupla — ese archivo no está en la lista de rutas que este paquete de
trabajo puede tocar. Así que este módulo agrega `packages/commerce` a `sys.path` por su
cuenta, con el mismo patrón exacto, antes de importar `edecan_api.routers.commerce`. Una
vez `_stub_siblings.py` incluya `"commerce"` (o el workspace esté `uv sync`-eado completo),
este bloque es un no-op inofensivo (`if _src not in sys.path`).
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from conftest import auth_headers
from httpx import ASGITransport, AsyncClient

_COMMERCE_SRC = str(Path(__file__).resolve().parents[3] / "packages" / "commerce")
if _COMMERCE_SRC not in sys.path:
    sys.path.insert(0, _COMMERCE_SRC)

import edecan_api.deps as edecan_deps  # noqa: E402
from edecan_api.routers import commerce  # noqa: E402


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
    commits: int = 0

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> FakeResult:
        self.llamadas.append((str(stmt), dict(params or {})))
        filas = self.respuestas.pop(0) if self.respuestas else []
        return FakeResult(filas)

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        # `confirm_order` (HOTFIXES_PENDIENTES.md punto 9) comitea explícitamente antes de
        # ciertos `raise` para que la confirmación humana sobreviva un rollback posterior —
        # ver su docstring en `edecan_api/routers/commerce.py`. Este doble solo cuenta
        # llamadas (mismo criterio "extender mínimamente DENTRO del test file" que
        # `test_remote_router.py::_FakeDbSession`); no modela transacciones reales.
        self.commits += 1


@dataclass
class _FakeBroker:
    """Doble de `edecan_commerce.paper.PaperBroker`: `execute()` devuelve `resultado` (o
    lanza `error`) sin tocar la `session` — el router no necesita saber cómo ejecuta, solo
    qué hace con lo que `PaperBroker` le devuelve."""

    resultado: dict[str, Any] | None = None
    error: Exception | None = None
    llamadas: list[tuple[dict[str, Any], Any]] = field(default_factory=list)

    async def execute(self, order: dict[str, Any], session: Any) -> dict[str, Any]:
        self.llamadas.append((order, session))
        if self.error is not None:
            raise self.error
        if self.resultado is not None:
            return self.resultado
        return {**order, "status": "executed_paper"}


def _order_row(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    base = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "kind": "trade",
        "status": "draft",
        "descripcion": "Compra de 0.1 BTC",
        "monto": Decimal("6000.00"),
        "moneda": "USD",
        "simbolo": "BTC",
        "lado": "buy",
        "cantidad": Decimal("0.1"),
        "meta": {"cotizacion": {"precio": 60000.0, "moneda": "USD", "fuente": "stub"}},
        "confirmed_at": None,
        "executed_at": None,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return base


@pytest.fixture
def fake_session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def _mounted_app(app, fake_session: FakeSession):
    """`app` (de `conftest.py`) + `commerce.router` montado + `get_tenant_session`
    reemplazado por `fake_session` (mismo patrón que `test_consents.py`)."""
    app.include_router(commerce.router)
    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: fake_session
    return app


@pytest.fixture
async def client(_mounted_app) -> AsyncIterator[AsyncClient]:
    # Sombrea a propósito el fixture `client` de `conftest.py`: ese vive sobre `app` sin
    # `commerce.router` montado.
    transport = ASGITransport(app=_mounted_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _headers_con_acceso(**kw: Any) -> dict[str, str]:
    """`hosted_pro` trae `commerce.orders=True` en `PLANES` (como cualquier otro plan, ver
    docstring del módulo)."""
    kw.setdefault("user_id", uuid.uuid4())
    kw.setdefault("tenant_id", uuid.uuid4())
    return auth_headers(plan_key="hosted_pro", **kw)


# ---------------------------------------------------------------------------
# Autenticación / flag gate
# ---------------------------------------------------------------------------


async def test_list_orders_requires_authentication(client) -> None:
    response = await client.get("/v1/commerce/orders")
    assert response.status_code == 401


async def test_free_selfhost_plan_has_commerce_access(client, fake_session) -> None:
    # free_selfhost SÍ trae commerce.orders=True (self-host trae todo, ROADMAP_V2.md §7.2).
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="free_selfhost")
    fake_session.respuestas = [[], []]
    response = await client.get("/v1/commerce/orders", headers=headers)
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Expiración perezosa de drafts (> 7 días -> expired, ROADMAP_V2.md §7 WP-V2-10)
# ---------------------------------------------------------------------------


async def test_expire_stale_drafts_sql_shape_is_scoped_to_7_day_old_drafts(
    client, fake_session
) -> None:
    """No hay Postgres real para probar el efecto de `now() - interval` contra filas de
    verdad (ver docstring del router) — este test fija el CONTRATO del SQL que se ejecuta:
    filtra por `status = 'draft'`, por `tenant_id`/`user_id` del usuario actual, y por una
    ventana de 7 días exacta."""
    fake_session.respuestas = [[], []]
    await client.get("/v1/commerce/orders", headers=_headers_con_acceso())

    sql_expire, params_expire = fake_session.llamadas[0]
    assert "status = 'expired'" in sql_expire
    assert "status = 'draft'" in sql_expire
    assert "interval '7 days'" in sql_expire
    assert "tenant_id = :tenant_id" in sql_expire
    assert "user_id = :user_id" in sql_expire
    assert set(params_expire) == {"tenant_id", "user_id"}


async def test_confirm_and_cancel_also_expire_stale_drafts_first(
    client, fake_session
) -> None:
    """La expiración perezosa no es solo de `GET /orders` (ver el docstring del router: se
    aplica defensivamente antes de leer/confirmar/cancelar una orden puntual también)."""
    fake_session.respuestas = [[], []]  # expire + get_order (vacío) -> 404
    response = await client.post(
        f"/v1/commerce/orders/{uuid.uuid4()}/cancel", headers=_headers_con_acceso()
    )
    assert response.status_code == 404
    sql_expire, _ = fake_session.llamadas[0]
    assert "status = 'expired'" in sql_expire


# ---------------------------------------------------------------------------
# GET /orders (+status) y GET /orders/{id}
# ---------------------------------------------------------------------------


async def test_list_orders_filters_by_status_and_expires_stale_drafts_first(
    client, fake_session
) -> None:
    fila = _order_row(status="confirmed")
    fake_session.respuestas = [[], [fila]]  # 1) expire (ignorado)  2) SELECT filtrado

    response = await client.get(
        "/v1/commerce/orders?status=confirmed", headers=_headers_con_acceso()
    )

    assert response.status_code == 200
    assert response.json()[0]["status"] == "confirmed"
    assert len(fake_session.llamadas) == 2
    sql_expire, _ = fake_session.llamadas[0]
    assert "status = 'expired'" in sql_expire
    sql_select, params_select = fake_session.llamadas[1]
    assert "SELECT * FROM orders" in sql_select
    assert params_select["status"] == "confirmed"


async def test_list_orders_decodes_meta_jsonb_string(client, fake_session) -> None:
    fila = _order_row(meta='{"beneficiario": "CFE"}')
    fake_session.respuestas = [[], [fila]]

    response = await client.get("/v1/commerce/orders", headers=_headers_con_acceso())

    assert response.json()[0]["meta"] == {"beneficiario": "CFE"}


async def test_get_order_404_for_unknown_id(client, fake_session) -> None:
    fake_session.respuestas = [[], []]  # expire + SELECT (vacío)
    response = await client.get(
        f"/v1/commerce/orders/{uuid.uuid4()}", headers=_headers_con_acceso()
    )
    assert response.status_code == 404


async def test_get_order_is_tenant_and_user_scoped_in_query(client, fake_session) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    fila = _order_row(tenant_id=tenant_id, user_id=user_id)
    fake_session.respuestas = [[], [fila]]

    response = await client.get(
        f"/v1/commerce/orders/{fila['id']}",
        headers=_headers_con_acceso(tenant_id=tenant_id, user_id=user_id),
    )

    assert response.status_code == 200
    _, params = fake_session.llamadas[1]
    assert params["tenant_id"] == str(tenant_id)
    assert params["user_id"] == str(user_id)


# ---------------------------------------------------------------------------
# POST /orders/{id}/confirm — kind=trade, modo paper (feliz)
# ---------------------------------------------------------------------------


async def test_confirm_trade_order_executes_via_paper_broker(
    client, fake_session, monkeypatch
) -> None:
    draft = _order_row(kind="trade", status="draft")
    confirmed = {**draft, "status": "confirmed"}
    fake_session.respuestas = [
        [],  # expire
        [draft],  # get_order
        [confirmed],  # UPDATE -> confirmed RETURNING *
        [],  # audit insert
    ]
    ejecutada = {**confirmed, "status": "executed_paper"}
    fake_broker = _FakeBroker(resultado=ejecutada)
    monkeypatch.setattr(commerce, "PaperBroker", lambda: fake_broker)

    response = await client.post(
        f"/v1/commerce/orders/{draft['id']}/confirm", headers=_headers_con_acceso()
    )

    assert response.status_code == 200
    body = response.json()
    assert body["order"]["status"] == "executed_paper"
    assert "No se movió dinero real" in body["mensaje"]
    assert len(fake_broker.llamadas) == 1
    orden_pasada_al_broker, _ = fake_broker.llamadas[0]
    assert orden_pasada_al_broker["status"] == "confirmed"

    sql_audit, params_audit = fake_session.llamadas[3]
    assert "INSERT INTO audit_log" in sql_audit
    assert params_audit["action"] == "commerce.order.confirmed"


async def test_confirm_trade_order_insufficient_quantity_returns_400(
    client, fake_session, monkeypatch
) -> None:
    draft = _order_row(kind="trade", status="draft", lado="sell")
    confirmed = {**draft, "status": "confirmed"}
    fake_session.respuestas = [[], [draft], [confirmed], [], [], []]
    fake_broker = _FakeBroker(error=ValueError("No hay suficiente BTC en holdings paper."))
    monkeypatch.setattr(commerce, "PaperBroker", lambda: fake_broker)

    response = await client.post(
        f"/v1/commerce/orders/{draft['id']}/confirm", headers=_headers_con_acceso()
    )

    assert response.status_code == 400
    assert "No hay suficiente BTC" in response.json()["detail"]


async def test_confirm_trade_order_insufficient_quantity_commits_before_raising(
    client, fake_session, monkeypatch
) -> None:
    """`HOTFIXES_PENDIENTES.md` punto 9: cuando `PaperBroker.execute` lanza `ValueError`
    (holdings insuficientes), la confirmación (`commerce.order.confirmed`) y el detalle del
    fallo (`meta.execution_error` + audit `commerce.order.execution_failed`) deben comitearse
    en un ÚNICO commit ANTES del 400 — de lo contrario el rollback automático de
    `get_tenant_session` (`edecan_db.session.get_session`, ver su docstring) se los llevaría
    puestos junto con la excepción, y el usuario habría confirmado sin dejar ningún rastro.
    Ver el docstring de `confirm_order` en `edecan_api/routers/commerce.py` para por qué el
    commit no puede pasar ANTES de invocar `PaperBroker.execute` (rompería SIEMPRE, no solo
    en el caso de error — verificado empíricamente contra SQLAlchemy 2.0 real)."""
    draft = _order_row(kind="trade", status="draft", lado="sell")
    confirmed = {**draft, "status": "confirmed"}
    fake_session.respuestas = [[], [draft], [confirmed], [], [], []]
    fake_broker = _FakeBroker(error=ValueError("No hay suficiente BTC en holdings paper."))
    monkeypatch.setattr(commerce, "PaperBroker", lambda: fake_broker)

    response = await client.post(
        f"/v1/commerce/orders/{draft['id']}/confirm", headers=_headers_con_acceso()
    )

    assert response.status_code == 400
    # Un único commit, hecho DESPUÉS de escribir todo (confirmación + fallo) y ANTES del raise.
    assert fake_session.commits == 1

    # 6 llamadas de sesión en total: expire, get_order, UPDATE->confirmed, audit "confirmed",
    # UPDATE meta.execution_error, audit "execution_failed" (`PaperBroker` está sustituido por
    # `_FakeBroker`, que no toca `session` — ver su docstring).
    assert len(fake_session.llamadas) == 6

    sql_meta_error, params_meta_error = fake_session.llamadas[4]
    assert "UPDATE orders SET meta = meta ||" in sql_meta_error
    assert "execution_error" in params_meta_error["extra"]
    assert "No hay suficiente BTC" in params_meta_error["extra"]

    sql_audit_fallo, params_audit_fallo = fake_session.llamadas[5]
    assert "INSERT INTO audit_log" in sql_audit_fallo
    assert params_audit_fallo["action"] == "commerce.order.execution_failed"
    assert "No hay suficiente BTC" in params_audit_fallo["meta"]


async def test_confirm_trade_order_non_paper_mode_returns_501(
    _mounted_app, client, fake_session, test_settings
) -> None:
    """`getattr(settings, "COMMERCE_MODE", "paper")` — ese campo todavía no existe en
    `edecan_api.config.Settings` (lo agrega WP-V2-01, `ROADMAP_V2.md` §7.5), así que se
    sobreescribe `get_settings` con un envoltorio que delega todo lo demás (incluido
    `JWT_SECRET`, que `get_current_user` sigue necesitando para decodificar el Bearer) al
    `test_settings` real de `conftest.py`, y solo agrega `COMMERCE_MODE`."""
    draft = _order_row(kind="trade", status="draft")
    confirmed = {**draft, "status": "confirmed"}
    fake_session.respuestas = [[], [draft], [confirmed], []]

    from edecan_api.config import get_settings

    class _LiveSettings:
        COMMERCE_MODE = "live"

        def __getattr__(self, name: str) -> Any:
            return getattr(test_settings, name)

    _mounted_app.dependency_overrides[get_settings] = lambda: _LiveSettings()
    try:
        response = await client.post(
            f"/v1/commerce/orders/{draft['id']}/confirm", headers=_headers_con_acceso()
        )
    finally:
        del _mounted_app.dependency_overrides[get_settings]

    assert response.status_code == 501
    assert "COMMERCE_MODE='live'" in response.json()["detail"]
    # HOTFIXES_PENDIENTES.md punto 9: la confirmación (+ audit) se comitea explícitamente
    # ANTES de este 501 — sin eso, el rollback automático se llevaría puesta la confirmación
    # aunque el modo no esté soportado.
    assert fake_session.commits == 1


# ---------------------------------------------------------------------------
# POST /orders/{id}/confirm — kind=payment (feliz)
# ---------------------------------------------------------------------------


async def test_confirm_payment_order_generates_placeholder_link(client, fake_session) -> None:
    draft = _order_row(kind="payment", status="draft", simbolo=None, lado=None, meta={})
    confirmed = {**draft, "status": "confirmed"}
    con_link = {**confirmed, "meta": {"payment_link": f"https://TU_PROVEEDOR_DE_PAGOS_AQUI/checkout/{draft['id']}"}}
    fake_session.respuestas = [
        [],  # expire
        [draft],  # get_order
        [confirmed],  # UPDATE -> confirmed
        [],  # audit
        [con_link],  # UPDATE meta -> payment_link
    ]

    response = await client.post(
        f"/v1/commerce/orders/{draft['id']}/confirm", headers=_headers_con_acceso()
    )

    assert response.status_code == 200
    body = response.json()
    assert body["order"]["meta"]["payment_link"].startswith("https://TU_PROVEEDOR_DE_PAGOS_AQUI")
    assert "TÚ debes abrir el enlace" in body["mensaje"]
    # Nunca queda en un estado "ejecutado": el pago real jamás lo dispara este endpoint.
    assert body["order"]["status"] != "executed_paper"


# ---------------------------------------------------------------------------
# POST /orders/{id}/confirm — estados inválidos
# ---------------------------------------------------------------------------


async def test_confirm_order_not_draft_returns_409(client, fake_session) -> None:
    ya_confirmada = _order_row(status="confirmed")
    fake_session.respuestas = [[], [ya_confirmada]]

    response = await client.post(
        f"/v1/commerce/orders/{ya_confirmada['id']}/confirm", headers=_headers_con_acceso()
    )

    assert response.status_code == 409
    assert "draft" in response.json()["detail"]


async def test_confirm_order_404_for_unknown_id(client, fake_session) -> None:
    fake_session.respuestas = [[], []]
    response = await client.post(
        f"/v1/commerce/orders/{uuid.uuid4()}/confirm", headers=_headers_con_acceso()
    )
    assert response.status_code == 404


async def test_confirm_order_unhandled_kind_returns_501(client, fake_session) -> None:
    draft = _order_row(kind="purchase", status="draft")
    confirmed = {**draft, "status": "confirmed"}
    fake_session.respuestas = [[], [draft], [confirmed], []]

    response = await client.post(
        f"/v1/commerce/orders/{draft['id']}/confirm", headers=_headers_con_acceso()
    )

    assert response.status_code == 501
    assert "purchase" in response.json()["detail"]
    # HOTFIXES_PENDIENTES.md punto 9: mismo tratamiento que el 501 de COMMERCE_MODE no
    # soportado — la confirmación (+ audit) se comitea explícitamente ANTES de este 501.
    assert fake_session.commits == 1


# ---------------------------------------------------------------------------
# POST /orders/{id}/cancel
# ---------------------------------------------------------------------------


async def test_cancel_draft_order_succeeds_and_audits(client, fake_session) -> None:
    draft = _order_row(status="draft")
    cancelada = {**draft, "status": "cancelled"}
    fake_session.respuestas = [[], [draft], [cancelada], []]

    response = await client.post(
        f"/v1/commerce/orders/{draft['id']}/cancel", headers=_headers_con_acceso()
    )

    assert response.status_code == 200
    assert response.json()["order"]["status"] == "cancelled"
    assert response.json()["mensaje"] == "Orden cancelada."

    sql_audit, params_audit = fake_session.llamadas[3]
    assert "INSERT INTO audit_log" in sql_audit
    assert params_audit["action"] == "commerce.order.cancelled"


async def test_cancel_confirmed_order_also_succeeds(client, fake_session) -> None:
    confirmada = _order_row(status="confirmed")
    cancelada = {**confirmada, "status": "cancelled"}
    fake_session.respuestas = [[], [confirmada], [cancelada], []]

    response = await client.post(
        f"/v1/commerce/orders/{confirmada['id']}/cancel", headers=_headers_con_acceso()
    )
    assert response.status_code == 200


async def test_cancel_already_cancelled_order_returns_409(client, fake_session) -> None:
    ya_cancelada = _order_row(status="cancelled")
    fake_session.respuestas = [[], [ya_cancelada]]

    response = await client.post(
        f"/v1/commerce/orders/{ya_cancelada['id']}/cancel", headers=_headers_con_acceso()
    )
    assert response.status_code == 409


async def test_cancel_executed_order_returns_409(client, fake_session) -> None:
    ejecutada = _order_row(status="executed_paper")
    fake_session.respuestas = [[], [ejecutada]]

    response = await client.post(
        f"/v1/commerce/orders/{ejecutada['id']}/cancel", headers=_headers_con_acceso()
    )
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# Holdings
# ---------------------------------------------------------------------------


async def test_list_holdings_returns_rows(client, fake_session) -> None:
    fila = {
        "id": uuid.uuid4(),
        "simbolo": "BTC",
        "cantidad": Decimal("0.5"),
        "costo_promedio": Decimal("60000.0000"),
        "moneda": "USD",
        "kind": "paper",
    }
    fake_session.respuestas = [[fila]]

    response = await client.get("/v1/commerce/holdings", headers=_headers_con_acceso())

    assert response.status_code == 200
    assert response.json()[0]["simbolo"] == "BTC"
    sql, _ = fake_session.llamadas[0]
    assert "FROM holdings" in sql
    assert "kind = 'paper'" in sql


# ---------------------------------------------------------------------------
# Presupuestos
# ---------------------------------------------------------------------------


async def test_get_budgets_returns_estado_con_pct_y_alerta(client, fake_session) -> None:
    fila = {
        "id": uuid.uuid4(),
        "categoria": "comida",
        "monto_mensual": Decimal("100.00"),
        "moneda": "USD",
        "gastado": Decimal("95.00"),
    }
    fake_session.respuestas = [[fila]]

    response = await client.get("/v1/commerce/budgets", headers=_headers_con_acceso())

    assert response.status_code == 200
    body = response.json()
    assert body[0]["pct"] == 95.0
    assert body[0]["alerta"] is True


async def test_put_budget_creates_when_missing(client, fake_session) -> None:
    fila_creada = {
        "id": uuid.uuid4(),
        "categoria": "transporte",
        "monto_mensual": Decimal("200.00"),
        "moneda": "USD",
    }
    fake_session.respuestas = [[], [fila_creada]]  # SELECT existing (vacío) + INSERT RETURNING

    response = await client.put(
        "/v1/commerce/budgets",
        json={"categoria": "transporte", "monto_mensual": 200},
        headers=_headers_con_acceso(),
    )

    assert response.status_code == 200
    assert response.json()["categoria"] == "transporte"


async def test_put_budget_rejects_non_positive_amount(client, fake_session) -> None:
    response = await client.put(
        "/v1/commerce/budgets",
        json={"categoria": "ocio", "monto_mensual": 0},
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 422  # Field(gt=0) lo rechaza antes del handler
    assert fake_session.llamadas == []


async def test_put_budget_rejects_invalid_currency(client, fake_session) -> None:
    response = await client.put(
        "/v1/commerce/budgets",
        json={"categoria": "ocio", "monto_mensual": 100, "moneda": "dólares"},
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 422
    assert fake_session.llamadas == []
