"""Barrido dedicado del patrón de bug (b) de v5 (evidencia legal/cumplimiento vs
rollback de sesión) sobre TODOS los routers de `apps/api/edecan_api/routers/`
que este paquete de trabajo (WP-V6-03) tenía en su lista: `auth.py`,
`billing.py`, `connectors.py`, `consents.py`, `credentials.py`, `erp.py`,
`rrhh.py`, `viajes.py`, `mensajes.py`, `negocios.py`, `perfil.py`.

Ver `docs/cumplimiento/barrido-evidencia-v6.md` para la tabla completa
archivo→veredicto→evidencia. Resumen: **ningún archivo de esta lista tenía el
patrón "escribe evidencia y LUEGO alcanza un `raise`"** — a diferencia de
`campaigns.handle` (WP-V6-03 #1, el hallazgo real de este paquete de
trabajo), en cada uno de estos 11 routers la escritura de evidencia
(`repo.add_audit_log`/`INSERT INTO audit_log` directo) es la ÚLTIMA
operación de la función (nada la sigue salvo `return`/un `dict` construido en
memoria a partir de valores YA conocidos), o cualquier validación/ping de red
que pudiera fallar ocurre ANTES de escribir nada (`credentials.py`,
`connectors.py`, `viajes.py`: los 12 sitios de escritura verificados, ping
SIEMPRE antes de persistir — la pregunta explícita del paquete de trabajo
"¿credentials.py hace ping DESPUÉS de escribir?" se responde NO en los 8
sitios de ese archivo). Por eso **no hizo falta ningún commit explícito
nuevo** en ninguno de los 11 archivos — el commit implícito de
`get_tenant_session` al terminar la request normalmente ya persiste todo.

Este archivo NO repite la cobertura ya exhaustiva de
`test_erp_router.py::test_create_producto_sku_duplicado_returns_409` (verifica
que el 409 nunca toca `audit_log`) ni de
`test_rrhh_router.py::test_aprobar_nomina_doble_aprobacion_returns_409`
("Nunca llegó al UPDATE ni al audit_log") ni de
`test_credentials_router.py::test_put_llm_anthropic_key_rechazada_no_guarda_nada`
(ping rechazado -> nada se guarda) ni de
`test_connectors.py::test_callback_success_stores_connector_account_and_token`
— esos tres ya prueban, cada uno en su propio dominio, que el ORDEN
validación-antes-que-escritura es correcto. Lo que este archivo SÍ aporta,
nuevo: una verificación DIRECTA (a través del stack HTTP real, con un
`FakeSession` que cuenta `commits`) de que `erp.py`/`rrhh.py` —los dos
routers de este paquete de trabajo con un helper `_audit()` propio, SQL
crudo, calcado de `edecan_api.routers.commerce._audit`— NUNCA llaman
`session.commit()` por su cuenta: dependen enteramente del commit implícito
de fin de request, precisamente PORQUE (a diferencia de `commerce.py`/
`ads.py`/`remote.py`/`voz_avanzada.py`) ninguno de los dos tiene un `raise`
alcanzable después de escribir la evidencia, así que nunca necesitaron el
patrón "commit explícito antes del raise".

Mismas convenciones que `test_erp_router.py`/`test_negocios_router.py`:
`client`/`app`/`auth_headers` de `conftest.py`; las funciones de
`edecan_business` importadas por nombre en `erp.py`/`rrhh.py` se sustituyen
con `monkeypatch.setattr(<router_module>, "<funcion>", ...)` (patrón "módulos
puros/fakeables" — evita reconstruir a mano la secuencia SQL exacta de
`edecan_business`, que ya tiene su propia suite en `packages/business/tests/`)
mientras que `_audit()` (código PROPIO del router, sin fakear) sí escribe de
verdad contra un `FakeSession` local con contador de `commits`.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from conftest import auth_headers
from httpx import ASGITransport, AsyncClient

import edecan_api.deps as edecan_deps
import edecan_api.routers.erp as erp_module
import edecan_api.routers.rrhh as rrhh_module

PLAN_CON_ERP = "hosted_pro"  # erp.inventory=True (ARCHITECTURE.md §13.c)
PLAN_CON_RRHH = "hosted_pro"  # erp.hr=True en los 4 planes (ARCHITECTURE.md §14.c)


class FakeResult:
    def __init__(self, row: Any = None) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row


@dataclass
class FakeSession:
    """`llamadas` registra CADA `execute()` (incluidos los del `_audit()` propio
    del router); `commits` cuenta llamadas a `.commit()` — mismo criterio que
    `test_commerce_router.py::FakeSession`/`test_remote_router.py::
    _FakeDbSession`. Ninguna de las funciones de `edecan_business` se ejercita
    de verdad aquí (van monkeypatcheadas), así que esta `FakeSession` solo
    necesita entender la única query real que corre: el `INSERT INTO
    audit_log` de `_audit()`."""

    llamadas: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    commits: int = 0

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> FakeResult:
        self.llamadas.append((str(stmt), dict(params or {})))
        return FakeResult()

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        self.commits += 1


@pytest.fixture
def fake_session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def _mounted_app(app: Any, fake_session: FakeSession) -> Any:
    app.include_router(erp_module.router)
    app.include_router(rrhh_module.router)
    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: fake_session
    return app


@pytest.fixture
async def client(_mounted_app: Any) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=_mounted_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _headers(*, plan_key: str) -> dict[str, str]:
    return auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key=plan_key)


# ---------------------------------------------------------------------------
# erp.py — POST /v1/erp/productos, camino feliz.
# ---------------------------------------------------------------------------


async def test_create_producto_camino_feliz_nunca_comitea_su_propia_sesion(
    client: AsyncClient, fake_session: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    producto_id = uuid.uuid4()

    async def fake_crear_producto(session, **kwargs):
        assert session is fake_session  # el router SÍ pasa la sesión real, sin fakear eso
        return {"id": producto_id, "sku": kwargs["sku"], "nombre": kwargs["nombre"]}

    monkeypatch.setattr(erp_module, "crear_producto", fake_crear_producto)

    response = await client.post(
        "/v1/erp/productos",
        headers=_headers(plan_key=PLAN_CON_ERP),
        json={"sku": "ABC-001", "nombre": "Widget"},
    )

    assert response.status_code == 201
    assert any("INSERT INTO audit_log" in sql for sql, _ in fake_session.llamadas)
    # WP-V6-03: nunca hizo falta un commit explícito acá -- `crear_producto`
    # (monkeypatcheado, pero representa la escritura real de negocio) corre
    # SIN lanzar, y nada después del INSERT de auditoría puede fallar (ver
    # docstring del módulo). El commit real ocurre implícito, al cerrar la
    # request (`get_tenant_session`), fuera del alcance de esta `FakeSession`.
    assert fake_session.commits == 0


async def test_create_producto_error_de_negocio_no_escribe_auditoria(
    client: AsyncClient, fake_session: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Complementa (no repite) `test_erp_router.py::
    test_create_producto_sku_duplicado_returns_409`: aquí se verifica el
    mismo principio con `ValueError` (422) en vez de `SkuDuplicadoError`
    (409) -- CUALQUIER excepción de negocio corta ANTES del `_audit()`."""

    async def fake_crear_producto_que_falla(session, **kwargs):
        raise ValueError("stock_minimo inválido")

    monkeypatch.setattr(erp_module, "crear_producto", fake_crear_producto_que_falla)

    response = await client.post(
        "/v1/erp/productos",
        headers=_headers(plan_key=PLAN_CON_ERP),
        json={"sku": "ABC-001", "nombre": "Widget"},
    )

    assert response.status_code == 422
    assert fake_session.llamadas == []  # ni siquiera se intentó auditar
    assert fake_session.commits == 0


# ---------------------------------------------------------------------------
# rrhh.py — POST /v1/rrhh/nominas/{id}/aprobar, camino feliz.
# ---------------------------------------------------------------------------


async def test_aprobar_nomina_camino_feliz_nunca_comitea_su_propia_sesion(
    client: AsyncClient, fake_session: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    payroll_run_id = uuid.uuid4()

    async def fake_aprobar_nomina(session, *, tenant_id, payroll_run_id):
        assert session is fake_session
        return {"id": payroll_run_id, "periodo": "2026-07", "status": "approved"}

    monkeypatch.setattr(rrhh_module, "aprobar_nomina", fake_aprobar_nomina)

    response = await client.post(
        f"/v1/rrhh/nominas/{payroll_run_id}/aprobar",
        headers=_headers(plan_key=PLAN_CON_RRHH),
        json={"confirmar": True},
    )

    assert response.status_code == 200
    assert "NO mueve dinero" in response.json()["mensaje"]
    assert any("INSERT INTO audit_log" in sql for sql, _ in fake_session.llamadas)
    # Mismo razonamiento que erp.py arriba (WP-V6-03, ver docstring del
    # módulo): `aprobar_nomina` (guardián de dinero real, pero sin llamada de
    # red -- nunca mueve nada, solo registra el estado) nunca puede fallar
    # DESPUÉS de escribir el audit_log en este camino, así que jamás hizo
    # falta un commit explícito.
    assert fake_session.commits == 0


async def test_aprobar_nomina_sin_confirmar_no_toca_la_sesion(
    client: AsyncClient, fake_session: FakeSession
) -> None:
    """Sin `{"confirmar": true}` el guardrail de dinero corta ANTES de
    siquiera llamar a `aprobar_nomina` -- ni la sesión ni la auditoría se
    tocan (mismo principio que `test_rrhh_router.py::
    test_aprobar_nomina_sin_confirmar_returns_400`, aquí re-verificado con
    `commits` incluido)."""
    response = await client.post(
        f"/v1/rrhh/nominas/{uuid.uuid4()}/aprobar",
        headers=_headers(plan_key=PLAN_CON_RRHH),
    )
    assert response.status_code == 400
    assert fake_session.llamadas == []
    assert fake_session.commits == 0
