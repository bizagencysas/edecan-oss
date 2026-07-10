"""`edecan_api.routers.rrhh` — `/v1/rrhh/*` (RRHH: empleados, ausencias y nómina en borrador,
`ARCHITECTURE.md` §14, WP-V5-08; ver el docstring del propio router para el contrato completo).

`edecan-business` YA es una dependencia declarada de `apps/api` (`from edecan_business import
...` a nivel de módulo en `routers/negocios.py` desde WP-V2-12, ver el comentario de
`apps/api/pyproject.toml`) — así que, igual que `test_erp_router.py`, este módulo NO necesita
ningún truco de `sys.path`: `import edecan_api.routers.rrhh` funciona directo.

`edecan_api.main.create_app()` YA monta `rrhh.router` de forma defensiva (`V5_ROUTER_NAMES`,
`ARCHITECTURE.md` §14.a, WP-V5-01 — ya aterrizado). Aun así, igual que `test_erp_router.py`
sigue haciendo con `erp.router`, el fixture `_mounted_app` de aquí abajo monta el router
manualmente y de forma explícita sobre la `app` de `conftest.py`: así estos tests no dependen
del orden de import de otros archivos.

**Flag `erp.hr` — matriz DISTINTA de `erp.inventory`**: a diferencia de `test_erp_router.py`
(donde `hosted_basic` es un plan real sin el flag), `erp.hr` está pinned en `True` en los
CUATRO planes (`ARCHITECTURE.md` §14.c: "básica", mismo criterio que `companion`) — no existe
ningún plan real sin él. El test de "flag apagado" (`test_..._rejects_plan_without_erp_hr_flag`)
apaga el flag EN MEMORIA con `monkeypatch.setitem(PLANES[...].flags, FLAG_ERP_HR, False)`
sobre `hosted_basic` solo para la duración de ese test — nunca toca
`packages/schemas/edecan_schemas/plans.py` en disco (fuera de las rutas que este paquete de
trabajo puede tocar); todos los demás tests usan cualquier plan real tal cual, sin parchear
nada, porque el flag YA está en `True` ahí.

`fake_session` (`FakeSession`/`FakeResult` locales, duplicadas a propósito — `ARCHITECTURE.md`
§10.1) reemplaza `edecan_api.deps.get_tenant_session`: consume una cola de respuestas
programadas en el ORDEN EXACTO en que `edecan_business.rrhh` las pide (ver el docstring de
cada test). Nunca se toca `edecan_api/repo.py`, `conftest.py` ni `api_fakes.py`.
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
from edecan_business import FLAG_ERP_HR
from edecan_schemas.plans import PLANES
from httpx import ASGITransport, AsyncClient

from edecan_api import deps as edecan_deps
from edecan_api.routers import rrhh

# `erp.hr` es `True` en LOS 4 PLANES (`ARCHITECTURE.md` §14.c) — cualquiera sirve de "plan con
# acceso"; se usa `hosted_basic` para el "plan sin acceso" apagando el flag en memoria (ver el
# docstring del módulo).
PLAN_CON_RRHH = "hosted_pro"
PLAN_SIN_RRHH = "hosted_basic"


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

    async def commit(self) -> None:
        pass


def _empleado_row(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    base = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "nombre": "Ana Pérez",
        "email": None,
        "puesto": "",
        "salario_mensual": None,
        "moneda": "USD",
        "fecha_ingreso": None,
        "status": "active",
        "meta": {},
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return base


def _ausencia_row(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    base = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "employee_id": uuid.uuid4(),
        "kind": "vacaciones",
        "desde": "2026-08-01",
        "hasta": "2026-08-05",
        "status": "pending",
        "notas": "",
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return base


def _payroll_run_row(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    base = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "periodo": "2026-07",
        "status": "draft",
        "total": Decimal("0.00"),
        "moneda": "USD",
        "notas": "",
        "approved_at": None,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return base


def _item_row(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    base = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "payroll_run_id": uuid.uuid4(),
        "employee_id": uuid.uuid4(),
        "bruto": Decimal("0.00"),
        "deducciones": Decimal("0.00"),
        "neto": Decimal("0.00"),
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return base


@pytest.fixture
def fake_session() -> FakeSession:
    return FakeSession()


@pytest.fixture(autouse=True)
def _erp_hr_flag_ya_pinned() -> None:
    """No-op documentado a propósito: `erp.hr` YA está `True` en `PLANES` de verdad
    (`ARCHITECTURE.md` §14.c) — a diferencia de un flag todavía no aterrizado, ningún test de
    este archivo necesita parchear nada para el camino feliz. Este fixture existe solo para
    dejar registrado en el propio archivo de tests que esa verificación se hizo a propósito
    (ver el docstring del módulo), no como un olvido."""
    assert PLANES[PLAN_CON_RRHH].flags.get(FLAG_ERP_HR) is True


@pytest.fixture
def _mounted_app(app, fake_session: FakeSession):
    """`app` (de `conftest.py`) + `rrhh.router` montado + `get_tenant_session` reemplazado
    por `fake_session` (mismo patrón que `test_erp_router.py`)."""
    app.include_router(rrhh.router)
    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: fake_session
    return app


@pytest.fixture
async def client(_mounted_app) -> AsyncIterator[AsyncClient]:
    # Sombrea a propósito el fixture `client` de `conftest.py`: ese vive sobre `app` sin
    # `rrhh.router` montado.
    transport = ASGITransport(app=_mounted_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _headers_con_acceso(**kw: Any) -> dict[str, str]:
    kw.setdefault("user_id", uuid.uuid4())
    kw.setdefault("tenant_id", uuid.uuid4())
    return auth_headers(plan_key=PLAN_CON_RRHH, **kw)


# ---------------------------------------------------------------------------
# Autenticación / flag gate
# ---------------------------------------------------------------------------


async def test_list_empleados_requires_authentication(client) -> None:
    response = await client.get("/v1/rrhh/empleados")
    assert response.status_code == 401


async def test_list_empleados_rejects_plan_without_erp_hr_flag(
    client, fake_session, monkeypatch
) -> None:
    monkeypatch.setitem(PLANES[PLAN_SIN_RRHH].flags, FLAG_ERP_HR, False)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key=PLAN_SIN_RRHH)
    response = await client.get("/v1/rrhh/empleados", headers=headers)
    assert response.status_code == 403
    assert fake_session.llamadas == []


async def test_create_empleado_rejects_plan_without_erp_hr_flag(
    client, fake_session, monkeypatch
) -> None:
    monkeypatch.setitem(PLANES[PLAN_SIN_RRHH].flags, FLAG_ERP_HR, False)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key=PLAN_SIN_RRHH)
    response = await client.post(
        "/v1/rrhh/empleados", json={"nombre": "Ana"}, headers=headers
    )
    assert response.status_code == 403
    assert fake_session.llamadas == []  # el gate corta antes de tocar la sesión.


async def test_free_selfhost_plan_has_rrhh_access(client, fake_session) -> None:
    # free_selfhost también trae erp.hr=True (§14.c: básico en los 4 planes).
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="free_selfhost")
    fake_session.respuestas = [[]]
    response = await client.get("/v1/rrhh/empleados", headers=headers)
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# GET/POST /empleados, PATCH /empleados/{id}
# ---------------------------------------------------------------------------


async def test_list_empleados_returns_rows(client, fake_session) -> None:
    fila = _empleado_row(nombre="Ana")
    fake_session.respuestas = [[fila]]
    response = await client.get("/v1/rrhh/empleados", headers=_headers_con_acceso())
    assert response.status_code == 200
    assert response.json()[0]["nombre"] == "Ana"


async def test_list_empleados_passes_status_and_q_filters(client, fake_session) -> None:
    fake_session.respuestas = [[]]
    response = await client.get(
        "/v1/rrhh/empleados?status=active&q=ana", headers=_headers_con_acceso()
    )
    assert response.status_code == 200
    sql, params = fake_session.llamadas[0]
    assert "status = :status" in sql
    assert params["status"] == "active"
    assert params["patron"] == "%ana%"


async def test_create_empleado_happy_path(client, fake_session) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    fila = _empleado_row(nombre="Ana Pérez", salario_mensual=Decimal("1500.00"))
    fake_session.respuestas = [[fila]]

    response = await client.post(
        "/v1/rrhh/empleados",
        json={"nombre": "Ana Pérez", "salario_mensual": 1500},
        headers=_headers_con_acceso(tenant_id=tenant_id, user_id=user_id),
    )

    assert response.status_code == 201
    assert response.json()["nombre"] == "Ana Pérez"
    _, params_insert = fake_session.llamadas[0]
    assert params_insert["user_id"] == str(user_id)
    assert params_insert["tenant_id"] == str(tenant_id)
    assert params_insert["salario_mensual"] == Decimal("1500")
    assert params_insert["status"] == "active"


async def test_create_empleado_missing_nombre_returns_422(client, fake_session) -> None:
    response = await client.post(
        "/v1/rrhh/empleados", json={}, headers=_headers_con_acceso()
    )
    assert response.status_code == 422
    assert fake_session.llamadas == []


async def test_create_empleado_negative_salario_returns_422(client, fake_session) -> None:
    response = await client.post(
        "/v1/rrhh/empleados",
        json={"nombre": "Ana", "salario_mensual": -5},
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 422
    assert fake_session.llamadas == []


async def test_create_empleado_malformed_email_returns_422_from_business_rule(
    client, fake_session
) -> None:
    """El formato de `email` no lo valida Pydantic (es un `str` sin restricción): lo rechaza
    `edecan_business.rrhh.crear_empleado` (`ValueError`), mapeado a `422` por el router."""
    response = await client.post(
        "/v1/rrhh/empleados",
        json={"nombre": "Ana", "email": "no-es-un-email"},
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 422
    assert fake_session.llamadas == []


async def test_update_empleado_happy_path(client, fake_session) -> None:
    employee_id = uuid.uuid4()
    fila = _empleado_row(id=employee_id, puesto="Gerente")
    fake_session.respuestas = [[fila]]

    response = await client.patch(
        f"/v1/rrhh/empleados/{employee_id}",
        json={"puesto": "Gerente"},
        headers=_headers_con_acceso(),
    )

    assert response.status_code == 200
    assert response.json()["puesto"] == "Gerente"
    sql_update, params_update = fake_session.llamadas[0]
    assert "puesto = :puesto" in sql_update
    assert params_update["puesto"] == "Gerente"


async def test_update_empleado_not_found_returns_404(client, fake_session) -> None:
    fake_session.respuestas = [[]]
    response = await client.patch(
        f"/v1/rrhh/empleados/{uuid.uuid4()}", json={"puesto": "X"}, headers=_headers_con_acceso()
    )
    assert response.status_code == 404


async def test_update_empleado_empty_nombre_returns_422_from_pydantic(client, fake_session) -> None:
    response = await client.patch(
        f"/v1/rrhh/empleados/{uuid.uuid4()}", json={"nombre": ""}, headers=_headers_con_acceso()
    )
    assert response.status_code == 422
    assert fake_session.llamadas == []


async def test_update_empleado_invalid_status_returns_422_from_pydantic(
    client, fake_session
) -> None:
    response = await client.patch(
        f"/v1/rrhh/empleados/{uuid.uuid4()}",
        json={"status": "jubilado"},
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 422
    assert fake_session.llamadas == []


# ---------------------------------------------------------------------------
# GET/POST /ausencias, PATCH /ausencias/{id}
# ---------------------------------------------------------------------------


async def test_list_ausencias_returns_rows(client, fake_session) -> None:
    fila = _ausencia_row()
    fake_session.respuestas = [[fila]]
    response = await client.get("/v1/rrhh/ausencias", headers=_headers_con_acceso())
    assert response.status_code == 200
    assert response.json()[0]["kind"] == "vacaciones"


async def test_list_ausencias_passes_employee_id_and_status_filters(client, fake_session) -> None:
    employee_id = uuid.uuid4()
    fake_session.respuestas = [[]]
    response = await client.get(
        f"/v1/rrhh/ausencias?employee_id={employee_id}&status=pending",
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 200
    sql, params = fake_session.llamadas[0]
    assert "employee_id = :employee_id" in sql
    assert params["employee_id"] == str(employee_id)
    assert params["status"] == "pending"


async def test_create_ausencia_happy_path(client, fake_session) -> None:
    employee_id = uuid.uuid4()
    fila = _ausencia_row(employee_id=employee_id)
    # 1) SELECT empleado existe  2) SELECT solapamiento (vacío)  3) INSERT time_off
    fake_session.respuestas = [[{"id": employee_id}], [], [fila]]

    response = await client.post(
        "/v1/rrhh/ausencias",
        json={
            "employee_id": str(employee_id),
            "kind": "vacaciones",
            "desde": "2026-08-01",
            "hasta": "2026-08-05",
        },
        headers=_headers_con_acceso(),
    )

    assert response.status_code == 201
    assert response.json()["status"] == "pending"
    _, params_insert = fake_session.llamadas[2]
    assert "user_id" not in params_insert  # time_off no tiene esa columna


async def test_create_ausencia_employee_not_found_returns_404(client, fake_session) -> None:
    fake_session.respuestas = [[]]
    response = await client.post(
        "/v1/rrhh/ausencias",
        json={
            "employee_id": str(uuid.uuid4()),
            "kind": "vacaciones",
            "desde": "2026-08-01",
            "hasta": "2026-08-05",
        },
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 404


async def test_create_ausencia_overlap_returns_409(client, fake_session) -> None:
    employee_id = uuid.uuid4()
    fake_session.respuestas = [[{"id": employee_id}], [{"id": uuid.uuid4()}]]
    response = await client.post(
        "/v1/rrhh/ausencias",
        json={
            "employee_id": str(employee_id),
            "kind": "vacaciones",
            "desde": "2026-08-01",
            "hasta": "2026-08-05",
        },
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 409
    assert "solapa" in response.json()["detail"].lower()


async def test_create_ausencia_invalid_kind_returns_422(client, fake_session) -> None:
    response = await client.post(
        "/v1/rrhh/ausencias",
        json={
            "employee_id": str(uuid.uuid4()),
            "kind": "siesta",
            "desde": "2026-08-01",
            "hasta": "2026-08-05",
        },
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 422
    assert fake_session.llamadas == []


async def test_create_ausencia_desde_after_hasta_returns_422_from_business_rule(
    client, fake_session
) -> None:
    employee_id = uuid.uuid4()
    fake_session.respuestas = [[{"id": employee_id}]]
    response = await client.post(
        "/v1/rrhh/ausencias",
        json={
            "employee_id": str(employee_id),
            "kind": "vacaciones",
            "desde": "2026-08-10",
            "hasta": "2026-08-05",
        },
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 422


async def test_update_ausencia_aprobar_happy_path(client, fake_session) -> None:
    fila = _ausencia_row(status="approved")
    fake_session.respuestas = [[{"status": "pending"}], [fila]]
    response = await client.patch(
        f"/v1/rrhh/ausencias/{uuid.uuid4()}",
        json={"accion": "aprobar"},
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "approved"


async def test_update_ausencia_rechazar_happy_path(client, fake_session) -> None:
    fila = _ausencia_row(status="rejected")
    fake_session.respuestas = [[{"status": "pending"}], [fila]]
    response = await client.patch(
        f"/v1/rrhh/ausencias/{uuid.uuid4()}",
        json={"accion": "rechazar"},
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


async def test_update_ausencia_not_found_returns_404(client, fake_session) -> None:
    fake_session.respuestas = [[]]
    response = await client.patch(
        f"/v1/rrhh/ausencias/{uuid.uuid4()}",
        json={"accion": "aprobar"},
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 404


async def test_update_ausencia_already_resolved_returns_409(client, fake_session) -> None:
    fake_session.respuestas = [[{"status": "approved"}]]
    response = await client.patch(
        f"/v1/rrhh/ausencias/{uuid.uuid4()}",
        json={"accion": "aprobar"},
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 409


async def test_update_ausencia_invalid_accion_returns_422(client, fake_session) -> None:
    response = await client.patch(
        f"/v1/rrhh/ausencias/{uuid.uuid4()}",
        json={"accion": "posponer"},
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 422
    assert fake_session.llamadas == []


# ---------------------------------------------------------------------------
# GET/POST /nominas, GET /nominas/{id}
# ---------------------------------------------------------------------------


async def test_list_nominas_returns_rows(client, fake_session) -> None:
    fila = _payroll_run_row()
    fake_session.respuestas = [[fila]]
    response = await client.get("/v1/rrhh/nominas", headers=_headers_con_acceso())
    assert response.status_code == 200
    assert response.json()[0]["periodo"] == "2026-07"


async def test_create_nomina_happy_path(client, fake_session) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    e1 = uuid.uuid4()
    empleados = [{"id": e1, "nombre": "Ana", "salario_mensual": Decimal("1000.00")}]
    run_row = _payroll_run_row(total=Decimal("1000.00"))
    item1 = _item_row(employee_id=e1, bruto=Decimal("1000.00"), neto=Decimal("1000.00"))
    fake_session.respuestas = [empleados, [run_row], [item1]]

    response = await client.post(
        "/v1/rrhh/nominas",
        json={"periodo": "2026-07"},
        headers=_headers_con_acceso(tenant_id=tenant_id, user_id=user_id),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "draft"
    assert len(body["items"]) == 1
    assert body["items"][0]["empleado_nombre"] == "Ana"
    assert float(body["total_bruto"]) == 1000.0


async def test_create_nomina_invalid_periodo_returns_422(client, fake_session) -> None:
    response = await client.post(
        "/v1/rrhh/nominas", json={"periodo": "julio-2026"}, headers=_headers_con_acceso()
    )
    assert response.status_code == 422
    assert fake_session.llamadas == []


async def test_create_nomina_deducciones_pct_out_of_range_returns_422(client, fake_session) -> None:
    response = await client.post(
        "/v1/rrhh/nominas",
        json={"periodo": "2026-07", "deducciones_pct": 75},
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 422
    assert fake_session.llamadas == []


async def test_get_nomina_returns_shape(client, fake_session) -> None:
    run_row = _payroll_run_row()
    item_row = _item_row()
    item_row["empleado_nombre"] = "Ana"
    fake_session.respuestas = [[run_row], [item_row]]
    response = await client.get(f"/v1/rrhh/nominas/{uuid.uuid4()}", headers=_headers_con_acceso())
    assert response.status_code == 200
    assert response.json()["items"][0]["empleado_nombre"] == "Ana"


async def test_get_nomina_not_found_returns_404(client, fake_session) -> None:
    fake_session.respuestas = [[]]
    response = await client.get(f"/v1/rrhh/nominas/{uuid.uuid4()}", headers=_headers_con_acceso())
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /nominas/{id}/aprobar — guardrail de dinero (confirmar:true obligatorio)
# ---------------------------------------------------------------------------


async def test_aprobar_nomina_sin_confirmar_returns_400(client, fake_session) -> None:
    response = await client.post(
        f"/v1/rrhh/nominas/{uuid.uuid4()}/aprobar", headers=_headers_con_acceso()
    )
    assert response.status_code == 400
    assert fake_session.llamadas == []  # nunca toca la sesión sin confirmar


async def test_aprobar_nomina_confirmar_false_explicito_returns_400(client, fake_session) -> None:
    response = await client.post(
        f"/v1/rrhh/nominas/{uuid.uuid4()}/aprobar",
        json={"confirmar": False},
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 400
    assert "dinero" in response.json()["detail"].lower()
    assert fake_session.llamadas == []


async def test_aprobar_nomina_happy_path_audits_and_says_no_mueve_dinero(
    client, fake_session
) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    payroll_run_id = uuid.uuid4()
    run_draft = _payroll_run_row(id=payroll_run_id, status="draft")
    run_approved = _payroll_run_row(id=payroll_run_id, status="approved")
    fake_session.respuestas = [[run_draft], [run_approved]]

    response = await client.post(
        f"/v1/rrhh/nominas/{payroll_run_id}/aprobar",
        json={"confirmar": True},
        headers=_headers_con_acceso(tenant_id=tenant_id, user_id=user_id),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["nomina"]["status"] == "approved"
    assert "no mueve dinero" in body["mensaje"].lower()

    sql_select, _ = fake_session.llamadas[0]
    assert "FOR UPDATE" in sql_select

    sql_audit, params_audit = fake_session.llamadas[2]
    assert "INSERT INTO audit_log" in sql_audit
    assert params_audit["action"] == "rrhh.payroll.approved"
    assert params_audit["tenant_id"] == str(tenant_id)
    assert params_audit["actor_user_id"] == str(user_id)


async def test_aprobar_nomina_not_found_returns_404(client, fake_session) -> None:
    fake_session.respuestas = [[]]
    response = await client.post(
        f"/v1/rrhh/nominas/{uuid.uuid4()}/aprobar",
        json={"confirmar": True},
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 404


async def test_aprobar_nomina_doble_aprobacion_returns_409(client, fake_session) -> None:
    """Segunda aprobación sobre una corrida ya `'approved'` — idempotencia explícita."""
    run_approved = _payroll_run_row(status="approved")
    fake_session.respuestas = [[run_approved]]
    response = await client.post(
        f"/v1/rrhh/nominas/{uuid.uuid4()}/aprobar",
        json={"confirmar": True},
        headers=_headers_con_acceso(),
    )
    assert response.status_code == 409
    # Nunca llegó al UPDATE ni al audit_log.
    assert len(fake_session.llamadas) == 1


# ---------------------------------------------------------------------------
# POST /nominas/{id}/cancelar
# ---------------------------------------------------------------------------


async def test_cancelar_nomina_happy_path_audits(client, fake_session) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    payroll_run_id = uuid.uuid4()
    run_draft = _payroll_run_row(id=payroll_run_id, status="draft")
    run_cancelled = _payroll_run_row(id=payroll_run_id, status="cancelled")
    fake_session.respuestas = [[run_draft], [run_cancelled]]

    response = await client.post(
        f"/v1/rrhh/nominas/{payroll_run_id}/cancelar",
        headers=_headers_con_acceso(tenant_id=tenant_id, user_id=user_id),
    )

    assert response.status_code == 200
    assert response.json()["nomina"]["status"] == "cancelled"
    sql_audit, params_audit = fake_session.llamadas[2]
    assert "INSERT INTO audit_log" in sql_audit
    assert params_audit["action"] == "rrhh.payroll.cancelled"


async def test_cancelar_nomina_not_found_returns_404(client, fake_session) -> None:
    fake_session.respuestas = [[]]
    response = await client.post(
        f"/v1/rrhh/nominas/{uuid.uuid4()}/cancelar", headers=_headers_con_acceso()
    )
    assert response.status_code == 404


async def test_cancelar_nomina_ya_aprobada_returns_409(client, fake_session) -> None:
    run_approved = _payroll_run_row(status="approved")
    fake_session.respuestas = [[run_approved]]
    response = await client.post(
        f"/v1/rrhh/nominas/{uuid.uuid4()}/cancelar", headers=_headers_con_acceso()
    )
    assert response.status_code == 409
    assert len(fake_session.llamadas) == 1
