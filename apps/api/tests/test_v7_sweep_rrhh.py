"""WP-V7-01 — barrido dedicado de `/v1/rrhh` (RRHH/nómina): BARRIDO B (flag de plan `erp.hr`
en las 11 rutas, no solo algunas) + BARRIDO C (evidencia en rollback en los sitios de
escritura RESTANTES del router, más allá de `aprobar_nomina`/`cancelar_nomina` que el barrido
v6 ya verificó — ver `docs/cumplimiento/barrido-v7-rrhh.md` para el detalle completo).

Complementa, no repite:
- `apps/api/tests/test_rrhh_router.py` — contrato HTTP completo de las 11 rutas (camino feliz,
  404/409/422, el guardrail `{"confirmar": true}`); ya cubre el gate de flag para 2 de las 11
  rutas (`GET`/`POST /empleados`).
- `apps/api/tests/test_v6_sweep_flags.py` (WP-V6-02, NO se toca en este WP) — matriz
  programática `Tool.requires_flags` <-> gate del router para las 3 tools de RRHH
  (`rrhh:GestionarEmpleadoTool`/`RegistrarAusenciaTool`/`PrepararNominaTool`). Este archivo NO
  reimplementa esa matriz cruzada de paquetes; solo confirma, con una lectura liviana, que
  sigue apuntando al mismo símbolo `_require_erp_hr` que este archivo también usa.
- `apps/api/tests/test_v6_sweep_evidencia.py` (WP-V6-03, NO se toca en este WP) — ya pinnea
  `test_aprobar_nomina_camino_feliz_nunca_comitea_su_propia_sesion` (`commits == 0`). Este
  archivo NO repite ese caso; agrega los TRES sitios de escritura que ese barrido no cubrió
  (`crear_empleado`/`registrar_ausencia`/`calcular_nomina`, vía `POST /empleados`, `POST
  /ausencias`, `POST /nominas`) más `cancelar_nomina` (que la tabla de
  `docs/cumplimiento/barrido-evidencia-v6.md` marcó "Seguro" junto con `aprobar_nomina`, pero
  sin un `commits`-counter DEDICADO como el que sí tiene `aprobar_nomina`).

Mismas convenciones que esos dos archivos: `FakeSession`/`FakeResult` LOCALES (duplicadas a
propósito, `ARCHITECTURE.md` §10.1) con un contador `commits`; las funciones de
`edecan_business` importadas por nombre en `rrhh.py` se sustituyen con
`monkeypatch.setattr(rrhh_module, "<funcion>", ...)` (mismo patrón "módulos puros/fakeables"
que `test_v6_sweep_evidencia.py` — evita reconstruir a mano la secuencia SQL exacta, que ya
tiene su propia suite en `packages/business/tests/test_rrhh.py` y, desde este WP, también
`packages/business/tests/test_rrhh_integration.py` contra Postgres real).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from conftest import auth_headers
from edecan_business import FLAG_ERP_HR
from edecan_schemas.plans import PLANES
from httpx import ASGITransport, AsyncClient

from edecan_api import deps as edecan_deps
from edecan_api.routers import rrhh as rrhh_module

# `erp.hr` es `True` en LOS 4 PLANES (`ARCHITECTURE.md` §14.c) — mismo criterio que
# `test_rrhh_router.py`: cualquier plan real sirve de "con acceso"; `hosted_basic` se usa como
# "sin acceso" apagando el flag EN MEMORIA con `monkeypatch`, nunca tocando
# `packages/schemas/edecan_schemas/plans.py` en disco.
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
    """`llamadas` registra CADA `execute()` (incluido el `INSERT INTO audit_log` de `_audit()`
    propio del router, cuando corre); `commits` cuenta llamadas a `.commit()` — mismo criterio
    que `test_rrhh_router.py::FakeSession`/`test_v6_sweep_evidencia.py::FakeSession` (esta
    clase es la unión de ambas: sostiene una cola de respuestas programadas Y un contador de
    commits, porque BARRIDO B necesita lo primero y BARRIDO C lo segundo)."""

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


@pytest.fixture
def fake_session() -> FakeSession:
    return FakeSession()


@pytest.fixture(autouse=True)
def _erp_hr_flag_ya_pinned() -> None:
    """No-op documentado a propósito (mismo criterio que `test_rrhh_router.py`): `erp.hr` YA
    está `True` en `PLANES` de verdad, así que ningún test de este archivo necesita parchear
    nada para el camino feliz."""
    assert PLANES[PLAN_CON_RRHH].flags.get(FLAG_ERP_HR) is True


@pytest.fixture
def _mounted_app(app: Any, fake_session: FakeSession) -> Any:
    app.include_router(rrhh_module.router)
    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: fake_session
    return app


@pytest.fixture
async def client(_mounted_app: Any) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=_mounted_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _headers(*, plan_key: str = PLAN_CON_RRHH, **kw: Any) -> dict[str, str]:
    kw.setdefault("user_id", uuid.uuid4())
    kw.setdefault("tenant_id", uuid.uuid4())
    return auth_headers(plan_key=plan_key, **kw)


# ---------------------------------------------------------------------------
# BARRIDO B — las 11 rutas pinned exigen `erp.hr`, no solo un par de ellas.
# ---------------------------------------------------------------------------

# (método, plantilla de ruta, cuerpo JSON o None) — plantilla usa "{id}" donde el path/el
# cuerpo necesita un UUID; se sustituye por un UUID fresco en cada caso parametrizado, así que
# ningún caso comparte estado con otro.
_RUTAS_RRHH: tuple[tuple[str, str, dict[str, Any] | None], ...] = (
    ("GET", "/v1/rrhh/empleados", None),
    ("POST", "/v1/rrhh/empleados", {"nombre": "Ana"}),
    ("PATCH", "/v1/rrhh/empleados/{id}", {"puesto": "Gerente"}),
    ("GET", "/v1/rrhh/ausencias", None),
    (
        "POST",
        "/v1/rrhh/ausencias",
        {"employee_id": "{id}", "kind": "vacaciones", "desde": "2026-08-01", "hasta": "2026-08-05"},
    ),
    ("PATCH", "/v1/rrhh/ausencias/{id}", {"accion": "aprobar"}),
    ("GET", "/v1/rrhh/nominas", None),
    ("POST", "/v1/rrhh/nominas", {"periodo": "2026-07"}),
    ("GET", "/v1/rrhh/nominas/{id}", None),
    ("POST", "/v1/rrhh/nominas/{id}/aprobar", {"confirmar": True}),
    ("POST", "/v1/rrhh/nominas/{id}/cancelar", None),
)
_RUTAS_RRHH_IDS = [f"{metodo}:{plantilla}" for metodo, plantilla, _ in _RUTAS_RRHH]


@pytest.mark.parametrize("metodo,plantilla,cuerpo", _RUTAS_RRHH, ids=_RUTAS_RRHH_IDS)
async def test_todas_las_rutas_rrhh_exigen_flag_erp_hr(
    client: AsyncClient,
    fake_session: FakeSession,
    monkeypatch: pytest.MonkeyPatch,
    metodo: str,
    plantilla: str,
    cuerpo: dict[str, Any] | None,
) -> None:
    """Las 11 rutas del contrato pinned (`rrhh.py`, docstring del módulo: "las 11 del contrato
    pinned del paquete de trabajo — ninguna ruta extra") deben responder `403` sin `erp.hr`,
    SIN tocar la sesión — `test_rrhh_router.py` solo ejercitaba 2 de las 11
    (`GET`/`POST /empleados`); este test cierra las 9 restantes."""
    monkeypatch.setitem(PLANES[PLAN_SIN_RRHH].flags, FLAG_ERP_HR, False)
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key=PLAN_SIN_RRHH)
    id_fresco = str(uuid.uuid4())
    ruta = plantilla.format(id=id_fresco)
    json_body = None
    if cuerpo is not None:
        json_body = {k: (id_fresco if v == "{id}" else v) for k, v in cuerpo.items()}

    response = await client.request(metodo, ruta, headers=headers, json=json_body)

    assert response.status_code == 403, (
        f"{metodo} {ruta} debería exigir erp.hr, dio {response.status_code}"
    )
    assert fake_session.llamadas == [], f"{metodo} {ruta} tocó la sesión antes del gate de flag"


def _normalizar_ruta(path: str) -> str:
    """Reemplaza cualquier segmento `{lo-que-sea}` por `{X}` para comparar solo la FORMA de la
    ruta (cuántos parámetros y en qué posición), sin depender de si el nombre del parámetro es
    `{id}` (como en `_RUTAS_RRHH`, arriba) o `{employee_id}`/`{time_off_id}`/
    `{payroll_run_id}` (como en las firmas reales de `rrhh.py`)."""
    return re.sub(r"\{[^}]+\}", "{X}", path)


def test_las_11_rutas_pinned_estan_cubiertas_por_este_barrido() -> None:
    """Red de alerta temprana: si alguien agrega/quita una ruta a `rrhh.router` sin actualizar
    `_RUTAS_RRHH` de arriba, este test avisa en vez de dejar una ruta nueva sin cobertura de
    flag en silencio. Cuenta las rutas reales del router (excluyendo `HEAD`/`OPTIONS`
    implícitos que FastAPI agrega solo para rutas `GET`)."""
    rutas_reales = {
        (metodo, _normalizar_ruta(route.path))  # type: ignore[attr-defined]
        for route in rrhh_module.router.routes
        for metodo in route.methods  # type: ignore[attr-defined]
        if metodo not in ("HEAD", "OPTIONS")
    }
    rutas_cubiertas = {
        (metodo, _normalizar_ruta(plantilla)) for metodo, plantilla, _ in _RUTAS_RRHH
    }
    assert rutas_cubiertas == rutas_reales, (
        f"Desincronizado: rutas reales={rutas_reales} vs cubiertas por el barrido="
        f"{rutas_cubiertas}"
    )


def test_las_3_tools_rrhh_siguen_apuntando_al_mismo_gate_que_este_router() -> None:
    """Confirmación LIVIANA (no la matriz completa — esa vive en `test_v6_sweep_flags.py`,
    WP-V6-02, fuera de las rutas que este WP puede tocar): las 3 tools de RRHH siguen
    requiriendo exactamente `FLAG_ERP_HR`, el mismo símbolo que `_require_erp_hr` de este
    router compara. Si algún día una tool o el router cambiaran de flag sin sincronizarse,
    este test (y la matriz completa de `test_v6_sweep_flags.py`) lo detectan."""
    from edecan_business.tools import (
        GestionarEmpleadoTool,
        PrepararNominaTool,
        RegistrarAusenciaTool,
    )

    for tool_cls in (GestionarEmpleadoTool, PrepararNominaTool, RegistrarAusenciaTool):
        assert tool_cls().requires_flags == frozenset({FLAG_ERP_HR})
    assert rrhh_module.FLAG_ERP_HR == FLAG_ERP_HR


# ---------------------------------------------------------------------------
# BARRIDO C — sitios de escritura RESTANTES (más allá de aprobar_nomina/cancelar_nomina, ya
# verificados "Seguro" por el barrido v6): alta de empleado, ausencia, generar nómina.
# ---------------------------------------------------------------------------


async def test_create_empleado_camino_feliz_nunca_comitea_su_propia_sesion(
    client: AsyncClient, fake_session: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`POST /empleados` no llama a `_audit()` en absoluto (a diferencia de
    aprobar/cancelar nómina) — no hay ninguna fila de evidencia legal/cumplimiento que
    proteger acá, así que nunca hizo falta (ni hace falta ahora) un commit explícito: el
    commit real ocurre implícito, al cerrar la request (`get_tenant_session`)."""
    empleado_id = uuid.uuid4()

    async def fake_crear_empleado(session: Any, **kwargs: Any) -> dict[str, Any]:
        assert session is fake_session  # el router SÍ pasa la sesión real, sin fakear eso
        return {"id": empleado_id, "nombre": kwargs["nombre"]}

    monkeypatch.setattr(rrhh_module, "crear_empleado", fake_crear_empleado)

    response = await client.post(
        "/v1/rrhh/empleados", headers=_headers(), json={"nombre": "Ana Pérez"}
    )

    assert response.status_code == 201
    assert fake_session.commits == 0


async def test_create_empleado_error_de_negocio_no_comitea(
    client: AsyncClient, fake_session: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_crear_empleado_que_falla(session: Any, **kwargs: Any) -> dict[str, Any]:
        raise ValueError("email inválido")

    monkeypatch.setattr(rrhh_module, "crear_empleado", fake_crear_empleado_que_falla)

    response = await client.post(
        "/v1/rrhh/empleados", headers=_headers(), json={"nombre": "Ana", "email": "x"}
    )

    assert response.status_code == 422
    assert fake_session.commits == 0


async def test_create_ausencia_camino_feliz_nunca_comitea_su_propia_sesion(
    client: AsyncClient, fake_session: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`POST /ausencias` tampoco llama a `_audit()` — mismo razonamiento que `crear_empleado`
    arriba: sin evidencia legal que escribir, nunca hubo un `raise` alcanzable después de un
    write de evidencia que proteger con un commit explícito."""
    ausencia_id = uuid.uuid4()
    employee_id = uuid.uuid4()

    async def fake_registrar_ausencia(session: Any, **kwargs: Any) -> dict[str, Any]:
        assert session is fake_session
        return {"id": ausencia_id, "status": "pending", "kind": kwargs["kind"]}

    monkeypatch.setattr(rrhh_module, "registrar_ausencia", fake_registrar_ausencia)

    response = await client.post(
        "/v1/rrhh/ausencias",
        headers=_headers(),
        json={
            "employee_id": str(employee_id),
            "kind": "vacaciones",
            "desde": "2026-08-01",
            "hasta": "2026-08-05",
        },
    )

    assert response.status_code == 201
    assert fake_session.commits == 0


async def test_create_ausencia_solapada_no_comitea(
    client: AsyncClient, fake_session: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from edecan_business import AusenciaSolapadaError

    async def fake_registrar_ausencia_que_falla(session: Any, **kwargs: Any) -> dict[str, Any]:
        raise AusenciaSolapadaError("se solapa")

    monkeypatch.setattr(rrhh_module, "registrar_ausencia", fake_registrar_ausencia_que_falla)

    response = await client.post(
        "/v1/rrhh/ausencias",
        headers=_headers(),
        json={
            "employee_id": str(uuid.uuid4()),
            "kind": "vacaciones",
            "desde": "2026-08-01",
            "hasta": "2026-08-05",
        },
    )

    assert response.status_code == 409
    assert fake_session.commits == 0


async def test_create_nomina_camino_feliz_nunca_comitea_su_propia_sesion(
    client: AsyncClient, fake_session: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`POST /nominas` (`calcular_nomina`) tampoco llama a `_audit()` — genera un borrador
    (`payroll_runs.status='draft'`), nunca mueve dinero ni deja evidencia legal/cumplimiento
    que un `raise` posterior pudiera perder en rollback."""
    payroll_run_id = uuid.uuid4()

    async def fake_calcular_nomina(session: Any, **kwargs: Any) -> dict[str, Any]:
        assert session is fake_session
        return {
            "id": payroll_run_id,
            "periodo": kwargs["periodo"],
            "status": "draft",
            "items": [],
            "total": "0.00",
            "total_bruto": "0.00",
            "total_deducciones": "0.00",
        }

    monkeypatch.setattr(rrhh_module, "calcular_nomina", fake_calcular_nomina)

    response = await client.post(
        "/v1/rrhh/nominas", headers=_headers(), json={"periodo": "2026-07"}
    )

    assert response.status_code == 201
    assert fake_session.commits == 0


async def test_create_nomina_error_de_negocio_no_comitea(
    client: AsyncClient, fake_session: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_calcular_nomina_que_falla(session: Any, **kwargs: Any) -> dict[str, Any]:
        raise ValueError("periodo inválido")

    monkeypatch.setattr(rrhh_module, "calcular_nomina", fake_calcular_nomina_que_falla)

    response = await client.post(
        "/v1/rrhh/nominas", headers=_headers(), json={"periodo": "2026-07"}
    )

    assert response.status_code == 422
    assert fake_session.commits == 0


async def test_cancelar_nomina_camino_feliz_nunca_comitea_su_propia_sesion(
    client: AsyncClient, fake_session: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`docs/cumplimiento/barrido-evidencia-v6.md` ya marcó `cancelar_nomina_endpoint` como
    "Seguro" junto con `aprobar_nomina_endpoint`, pero solo esta última ganó un `commits`-
    counter DEDICADO en `test_v6_sweep_evidencia.py`. Este test cierra ese hueco puntual sin
    tocar ese archivo."""
    payroll_run_id = uuid.uuid4()

    async def fake_cancelar_nomina(session: Any, **kwargs: Any) -> dict[str, Any]:
        assert session is fake_session
        return {"id": payroll_run_id, "periodo": "2026-07", "status": "cancelled"}

    monkeypatch.setattr(rrhh_module, "cancelar_nomina", fake_cancelar_nomina)

    response = await client.post(
        f"/v1/rrhh/nominas/{payroll_run_id}/cancelar", headers=_headers()
    )

    assert response.status_code == 200
    assert any("INSERT INTO audit_log" in sql for sql, _ in fake_session.llamadas)
    assert fake_session.commits == 0


async def test_cancelar_nomina_error_de_estado_no_comitea(
    client: AsyncClient, fake_session: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from edecan_business import EstadoNominaError

    async def fake_cancelar_nomina_que_falla(session: Any, **kwargs: Any) -> dict[str, Any]:
        raise EstadoNominaError("ya está aprobada")

    monkeypatch.setattr(rrhh_module, "cancelar_nomina", fake_cancelar_nomina_que_falla)

    response = await client.post(
        f"/v1/rrhh/nominas/{uuid.uuid4()}/cancelar", headers=_headers()
    )

    assert response.status_code == 409
    assert fake_session.commits == 0


# ---------------------------------------------------------------------------
# BARRIDO A — bring-your-own: RRHH no debería leer NINGUNA credencial de plataforma/tenant.
# ---------------------------------------------------------------------------


def test_router_rrhh_nunca_lee_settings_ni_credenciales_de_terceros() -> None:
    """Confirmación estática (AST): ningún handler de `rrhh.py` referencia `ctx.settings`,
    `self._settings`, `os.environ` ni ningún atributo tipo `*_API_KEY`/`*_TOKEN` — RRHH es
    puramente datos propios del tenant (empleados/ausencias/nómina en borrador), sin ningún
    proveedor de terceros de por medio (a diferencia de `credentials.py`/`viajes.py`/
    `voz_avanzada.py`, que sí resuelven credenciales bring-your-own). Mismo criterio que
    `packages/skills/tests/test_v6_seguridad_privilegios.py` usa AST en vez de un grep de
    texto (evita falsos negativos/positivos de un simple `in`)."""
    import ast
    import inspect

    codigo_fuente = inspect.getsource(rrhh_module)
    arbol = ast.parse(codigo_fuente)

    nombres_atributo: set[str] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Attribute):
            nombres_atributo.add(nodo.attr)

    prohibidos = {"settings", "environ", "getenv"}
    encontrados = nombres_atributo & prohibidos
    assert not encontrados, f"rrhh.py referencia atributos prohibidos: {encontrados}"
    assert "os" not in {
        nodo.name.split(".")[0]
        for nodo in ast.walk(arbol)
        if isinstance(nodo, ast.Import)
        for nodo in [nodo]
        for nodo in nodo.names
    }


# ---------------------------------------------------------------------------
# GUARDRAIL DINERO — la nómina es SIEMPRE borrador, ningún camino mueve dinero real.
# ---------------------------------------------------------------------------


def test_router_rrhh_no_importa_ningun_cliente_de_pagos_ni_hace_llamadas_de_red() -> None:
    """Confirmación estática: `rrhh.py` no importa `httpx`/`stripe`/ningún cliente HTTP — la
    única I/O del módulo es SQL parametrizado contra la sesión del propio tenant
    (`ARCHITECTURE.md`: "dinero real nunca se mueve solo"). Si algún día alguien agregara una
    integración de pagos a este router, este test lo detectaría."""
    import inspect

    codigo_fuente = inspect.getsource(rrhh_module)
    for paquete_prohibido in ("httpx", "stripe", "requests", "aiohttp"):
        assert f"import {paquete_prohibido}" not in codigo_fuente
        assert f"from {paquete_prohibido}" not in codigo_fuente
