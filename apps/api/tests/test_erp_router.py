"""`edecan_api.routers.erp` — `/v1/erp/*` (inventario / ERP básico, `ARCHITECTURE.md` §13,
WP-V4-06; ver el docstring del propio router para el contrato completo).

`edecan-business` YA es una dependencia declarada de `apps/api` (`from edecan_business import
...` a nivel de módulo en `routers/negocios.py` desde WP-V2-12, ver el comentario de
`apps/api/pyproject.toml`) — así que, a diferencia de `test_commerce_router.py` (que sí
necesitó agregar `packages/commerce` a `sys.path` a mano porque `edecan-commerce` no estaba
declarado cuando se escribió ese archivo), este módulo NO necesita ningún truco de
`sys.path`: `import edecan_api.routers.erp` funciona igual que en `test_negocios_router.py`.

`edecan_api.main.create_app()` YA monta `erp.router` de forma defensiva (`V4_ROUTER_NAMES`,
`ARCHITECTURE.md` §13.a, WP-V4-01 — ver `test_v4_mounting.py` si existe) siempre que
`edecan_business` sea importable. Aun así, igual que `test_commerce_router.py` sigue
haciendo con `commerce.router` aunque ESE router también quedó cubierto por el mismo
mecanismo, el fixture `_mounted_app` de aquí abajo monta el router manualmente y de forma
explícita sobre la `app` de `conftest.py`: así estos tests no dependen del orden de import de
otros archivos.

Modelo de precio de pago único (2026-07-09, `edecan_schemas.plans` docstring): `erp.inventory`
(y cualquier otro flag) ya está en `True` en las 4 entradas de `PLANES` por igual — no hay más
"flag apagado" que probar. Los tests usan `auth_headers(plan_key="hosted_pro")` como cualquier
`plan_key` válido (mismo criterio que `test_commerce_router.py::_headers_con_acceso`).

`fake_session` (`FakeSession`/`FakeResult` locales, duplicadas a propósito — `ARCHITECTURE.md`
§10.1) reemplaza `edecan_api.deps.get_tenant_session`: consume una cola de respuestas
programadas en el ORDEN EXACTO en que `edecan_business.inventory` las pide (ver el docstring
de cada test). Nunca se toca `edecan_api/repo.py`, `conftest.py` ni `api_fakes.py`.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from conftest import auth_headers
from httpx import ASGITransport, AsyncClient

from edecan_api import deps as edecan_deps
from edecan_api.routers import erp

# hosted_pro trae erp.inventory=True en PLANES (ARCHITECTURE.md §13.c) -- como cualquier plan.
PLAN_WITH_ERP = "hosted_pro"


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


def _producto_row(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    base = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "sku": "ABC-001",
        "nombre": "Widget",
        "descripcion": "",
        "unidad": "unidad",
        "precio": Decimal("10.00"),
        "costo": Decimal("5.00"),
        "stock": Decimal("0"),
        "stock_minimo": Decimal("0"),
        "activo": True,
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
    """`app` (de `conftest.py`) + `erp.router` montado + `get_tenant_session` reemplazado
    por `fake_session` (mismo patrón que `test_consents.py`/`test_commerce_router.py`)."""
    app.include_router(erp.router)
    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: fake_session
    return app


@pytest.fixture
async def client(_mounted_app) -> AsyncIterator[AsyncClient]:
    # Sombrea a propósito el fixture `client` de `conftest.py`: ese vive sobre `app` sin
    # `erp.router` montado.
    transport = ASGITransport(app=_mounted_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _headers_con_acceso(**kw: Any) -> dict[str, str]:
    kw.setdefault("user_id", uuid.uuid4())
    kw.setdefault("tenant_id", uuid.uuid4())
    return auth_headers(plan_key=PLAN_WITH_ERP, **kw)


# ---------------------------------------------------------------------------
# Autenticación / flag gate
# ---------------------------------------------------------------------------


async def test_list_productos_requires_authentication(client) -> None:
    response = await client.get("/v1/erp/productos")
    assert response.status_code == 401


async def test_free_selfhost_plan_has_erp_access(client, fake_session) -> None:
    # free_selfhost SÍ trae erp.inventory=True (self-host trae todo, ARCHITECTURE.md §13.c).
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="free_selfhost")
    fake_session.respuestas = [[]]
    response = await client.get("/v1/erp/productos", headers=headers)
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# GET /productos
# ---------------------------------------------------------------------------


async def test_list_productos_returns_rows(client, fake_session) -> None:
    fila = _producto_row(sku="X1")
    fake_session.respuestas = [[fila]]
    response = await client.get("/v1/erp/productos", headers=_headers_con_acceso())
    assert response.status_code == 200
    assert response.json()[0]["sku"] == "X1"


async def test_list_productos_passes_activo_and_q_filters(client, fake_session) -> None:
    fake_session.respuestas = [[]]
    response = await client.get(
        "/v1/erp/productos?activo=true&q=widget", headers=_headers_con_acceso()
    )
    assert response.status_code == 200
    sql, params = fake_session.llamadas[0]
    assert "activo = :activo" in sql
    assert params["activo"] is True
    assert params["patron"] == "%widget%"


# ---------------------------------------------------------------------------
# POST /productos
# ---------------------------------------------------------------------------


async def test_create_producto_happy_path_audits(client, fake_session) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    fila = _producto_row(sku="X1", nombre="Widget")
    fake_session.respuestas = [[], [fila]]  # SELECT dup-check (vacío) + INSERT RETURNING

    response = await client.post(
        "/v1/erp/productos",
        json={"sku": "x1", "nombre": "Widget"},
        headers=_headers_con_acceso(tenant_id=tenant_id, user_id=user_id),
    )

    assert response.status_code == 201
    assert response.json()["sku"] == "X1"

    _, params_insert = fake_session.llamadas[1]
    assert params_insert["user_id"] == str(user_id)
    assert params_insert["tenant_id"] == str(tenant_id)
    assert params_insert["descripcion"] == ""
    assert params_insert["unidad"] == "unidad"

    sql_audit, params_audit = fake_session.llamadas[2]
    assert "INSERT INTO audit_log" in sql_audit
    assert params_audit["action"] == "erp.product.created"
    assert params_audit["target"] == str(fila["id"])


async def test_create_producto_sku_duplicado_returns_409(client, fake_session) -> None:
    fake_session.respuestas = [[{"id": uuid.uuid4()}]]  # ya existe
    response = await client.post(
        "/v1/erp/productos", json={"sku": "X1", "nombre": "Widget"}, headers=_headers_con_acceso()
    )
    assert response.status_code == 409


async def test_create_producto_missing_nombre_returns_422(client, fake_session) -> None:
    response = await client.post(
        "/v1/erp/productos", json={"sku": "X1"}, headers=_headers_con_acceso()
    )
    assert response.status_code == 422
    assert fake_session.llamadas == []


async def test_create_producto_missing_sku_returns_422(client, fake_session) -> None:
    response = await client.post(
        "/v1/erp/productos", json={"nombre": "Widget"}, headers=_headers_con_acceso()
    )
    assert response.status_code == 422
    assert fake_session.llamadas == []


async def test_create_producto_negative_precio_returns_422(client, fake_session) -> None:
    response = await client.post(
        "/v1/erp/productos",
        json={"sku": "X1", "nombre": "Widget", "precio": -5},
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 422
    assert fake_session.llamadas == []


async def test_create_producto_negative_stock_minimo_returns_422(client, fake_session) -> None:
    response = await client.post(
        "/v1/erp/productos",
        json={"sku": "X1", "nombre": "Widget", "stock_minimo": -1},
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 422
    assert fake_session.llamadas == []


# ---------------------------------------------------------------------------
# PATCH /productos/{id}
# ---------------------------------------------------------------------------


async def test_update_producto_happy_path_audits(client, fake_session) -> None:
    product_id = uuid.uuid4()
    fila = _producto_row(id=product_id, nombre="Widget Pro")
    fake_session.respuestas = [[fila]]

    response = await client.patch(
        f"/v1/erp/productos/{product_id}",
        json={"nombre": "Widget Pro"},
        headers=_headers_con_acceso(),
    )

    assert response.status_code == 200
    assert response.json()["nombre"] == "Widget Pro"
    sql_update, params_update = fake_session.llamadas[0]
    assert "nombre = :nombre" in sql_update
    assert params_update["nombre"] == "Widget Pro"

    sql_audit, params_audit = fake_session.llamadas[1]
    assert "INSERT INTO audit_log" in sql_audit
    assert params_audit["action"] == "erp.product.updated"


async def test_update_producto_activo_false_desactiva_sin_ruta_dedicada(
    client, fake_session
) -> None:
    """No hay `POST .../desactivar`: `PATCH` con `{"activo": false}` es la única vía HTTP
    (ver el docstring del router)."""
    product_id = uuid.uuid4()
    fila = _producto_row(id=product_id, activo=False)
    fake_session.respuestas = [[fila]]

    response = await client.patch(
        f"/v1/erp/productos/{product_id}", json={"activo": False}, headers=_headers_con_acceso()
    )

    assert response.status_code == 200
    assert response.json()["activo"] is False
    _, params_update = fake_session.llamadas[0]
    assert params_update["activo"] is False


async def test_update_producto_not_found_returns_404(client, fake_session) -> None:
    fake_session.respuestas = [[]]
    response = await client.patch(
        f"/v1/erp/productos/{uuid.uuid4()}", json={"nombre": "X"}, headers=_headers_con_acceso()
    )
    assert response.status_code == 404


async def test_update_producto_empty_nombre_returns_422_from_pydantic(client, fake_session) -> None:
    response = await client.patch(
        f"/v1/erp/productos/{uuid.uuid4()}", json={"nombre": ""}, headers=_headers_con_acceso()
    )
    assert response.status_code == 422
    assert fake_session.llamadas == []


async def test_update_producto_null_nombre_returns_422_from_business_rule(
    client, fake_session
) -> None:
    """`nombre: null` pasa la validación de Pydantic (`min_length` no se evalúa sobre `None`)
    pero SÍ está presente en `exclude_unset=True` — `edecan_business.inventory.
    editar_producto` lo rechaza (`ValueError`) antes de tocar la sesión."""
    response = await client.patch(
        f"/v1/erp/productos/{uuid.uuid4()}", json={"nombre": None}, headers=_headers_con_acceso()
    )
    assert response.status_code == 422
    assert fake_session.llamadas == []


async def test_update_producto_negative_precio_returns_422(client, fake_session) -> None:
    response = await client.patch(
        f"/v1/erp/productos/{uuid.uuid4()}", json={"precio": -1}, headers=_headers_con_acceso()
    )
    assert response.status_code == 422
    assert fake_session.llamadas == []


async def test_update_producto_sku_is_ignored_not_editable(client, fake_session) -> None:
    """`sku` no es un campo de `ProductoUpdateIn` — Pydantic lo descarta como extra en vez
    de fallar (comportamiento por defecto de `BaseModel`), así que el `UPDATE` ni siquiera lo
    menciona."""
    product_id = uuid.uuid4()
    fila = _producto_row(id=product_id)
    fake_session.respuestas = [[fila]]
    response = await client.patch(
        f"/v1/erp/productos/{product_id}",
        json={"sku": "OTRO", "nombre": "Widget"},
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 200
    sql_update, params_update = fake_session.llamadas[0]
    assert "sku" not in params_update


# ---------------------------------------------------------------------------
# POST /productos/{id}/movimientos
# ---------------------------------------------------------------------------


async def test_create_movimiento_happy_path_audits(client, fake_session) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    product_id = uuid.uuid4()
    producto_actual = _producto_row(id=product_id, stock=Decimal("10"))
    move_row = {"id": uuid.uuid4(), "delta": Decimal("5"), "motivo": "compra"}
    producto_actualizado = _producto_row(id=product_id, stock=Decimal("15"))
    fake_session.respuestas = [[producto_actual], [move_row], [producto_actualizado]]

    response = await client.post(
        f"/v1/erp/productos/{product_id}/movimientos",
        json={"delta": 5, "motivo": "compra"},
        headers=_headers_con_acceso(tenant_id=tenant_id, user_id=user_id),
    )

    assert response.status_code == 201
    body = response.json()
    assert float(body["producto"]["stock"]) == 15.0

    sql_insert, params_insert = fake_session.llamadas[1]
    assert "INSERT INTO stock_moves" in sql_insert
    assert params_insert["motivo"] == "compra"
    assert params_insert["user_id"] == str(user_id)
    assert params_insert["tenant_id"] == str(tenant_id)
    sql_update, _ = fake_session.llamadas[2]
    assert "stock = stock + :delta" in sql_update

    sql_audit, params_audit = fake_session.llamadas[3]
    assert "INSERT INTO audit_log" in sql_audit
    assert params_audit["action"] == "erp.stock_move.created"


async def test_create_movimiento_que_dejaria_stock_negativo_returns_400(
    client, fake_session
) -> None:
    product_id = uuid.uuid4()
    producto_actual = _producto_row(id=product_id, stock=Decimal("2"))
    fake_session.respuestas = [[producto_actual]]

    response = await client.post(
        f"/v1/erp/productos/{product_id}/movimientos",
        json={"delta": -5, "motivo": "venta"},
        headers=_headers_con_acceso(),
    )

    assert response.status_code == 400
    assert "ajuste" in response.json()["detail"].lower()
    # nunca llegó a insertar el movimiento ni a actualizar el stock.
    assert len(fake_session.llamadas) == 1


async def test_create_movimiento_ajuste_permite_stock_negativo(client, fake_session) -> None:
    product_id = uuid.uuid4()
    producto_actual = _producto_row(id=product_id, stock=Decimal("2"))
    move_row = {"id": uuid.uuid4()}
    producto_actualizado = _producto_row(id=product_id, stock=Decimal("-3"))
    fake_session.respuestas = [[producto_actual], [move_row], [producto_actualizado]]

    response = await client.post(
        f"/v1/erp/productos/{product_id}/movimientos",
        json={"delta": -5, "motivo": "ajuste"},
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 201


async def test_create_movimiento_producto_not_found_returns_404(client, fake_session) -> None:
    fake_session.respuestas = [[]]
    response = await client.post(
        f"/v1/erp/productos/{uuid.uuid4()}/movimientos",
        json={"delta": 5, "motivo": "compra"},
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 404


async def test_create_movimiento_invalid_motivo_returns_422(client, fake_session) -> None:
    response = await client.post(
        f"/v1/erp/productos/{uuid.uuid4()}/movimientos",
        json={"delta": 5, "motivo": "no-es-un-motivo"},
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 422
    assert fake_session.llamadas == []


async def test_create_movimiento_delta_cero_returns_422(client, fake_session) -> None:
    response = await client.post(
        f"/v1/erp/productos/{uuid.uuid4()}/movimientos",
        json={"delta": 0, "motivo": "ajuste"},
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 422
    assert fake_session.llamadas == []


async def test_create_movimiento_missing_motivo_returns_422(client, fake_session) -> None:
    response = await client.post(
        f"/v1/erp/productos/{uuid.uuid4()}/movimientos",
        json={"delta": 5},
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 422
    assert fake_session.llamadas == []


# ---------------------------------------------------------------------------
# GET /resumen
# ---------------------------------------------------------------------------


async def test_get_resumen_returns_shape(client, fake_session) -> None:
    filas = [_producto_row(sku="A", stock=Decimal("2"), stock_minimo=Decimal("5"))]
    fake_session.respuestas = [filas]

    response = await client.get("/v1/erp/resumen", headers=_headers_con_acceso())

    assert response.status_code == 200
    body = response.json()
    assert body["total_skus"] == 1
    assert len(body["stock_bajo"]) == 1
    assert body["stock_bajo"][0]["sku"] == "A"


async def test_get_resumen_sin_productos_es_todo_cero(client, fake_session) -> None:
    fake_session.respuestas = [[]]
    response = await client.get("/v1/erp/resumen", headers=_headers_con_acceso())
    assert response.status_code == 200
    assert response.json() == {
        "total_skus": 0,
        "valor_costo": 0.0,
        "valor_precio": 0.0,
        "stock_bajo": [],
    }
