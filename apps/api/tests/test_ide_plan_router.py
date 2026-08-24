"""Aprobar, editar y rechazar un plan del IDE.

Estos tres endpoints faltaban. El companion YA sabía ejecutar un plan aprobado
—`ide_sessions.approve_plan` reparte los pasos independientes entre sub-agentes
vía `ide_reparto`/`ide_equipo`— pero nadie había escrito el cable HTTP: el botón
"Aprobar" de la torre de control estaba deshabilitado con esa explicación a la
vista, y un plan propuesto se quedaba colgado para siempre.

Mismo estilo y mismos dobles que `test_ide_router.py`: esto cubre el mapeo
router→companion, no la criptografía del emparejamiento.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from conftest import auth_headers

from edecan_api.ide_security import PairedIDEDevice
from edecan_api.routers import ide


class _FakeCompanionManager:
    def __init__(self) -> None:
        self.calls: list[tuple[uuid.UUID, str, dict[str, Any]]] = []

    def is_connected(self, tenant_id: uuid.UUID) -> bool:
        return True

    async def send_command(
        self, tenant_id: uuid.UUID, action: str, params: dict[str, Any], timeout: float = 30
    ) -> dict[str, Any]:
        self.calls.append((tenant_id, action, dict(params)))
        return {"ok": True, "result": {}}


@pytest.fixture
def fake(app) -> _FakeCompanionManager:
    manager = _FakeCompanionManager()
    app.dependency_overrides[ide.get_companion_manager] = lambda: manager
    yield manager
    app.dependency_overrides.pop(ide.get_companion_manager, None)


@pytest.fixture(autouse=True)
def _paired(app):
    async def paired() -> PairedIDEDevice:
        return PairedIDEDevice(device_id=uuid.uuid4())

    app.dependency_overrides[ide.require_paired_ide_device] = paired
    yield
    app.dependency_overrides.pop(ide.require_paired_ide_device, None)


def _headers() -> dict[str, str]:
    return auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())


async def test_aprobar_manda_la_accion_al_companion(client, fake):
    r = await client.post("/v1/ide/agents/s1/plan/p1/approve", headers=_headers())
    assert r.status_code == 200
    _, accion, params = fake.calls[-1]
    assert accion == "ide_plan_approve"
    assert params == {"session_id": "s1", "plan_id": "p1"}


async def test_editar_exige_al_menos_un_paso(client, fake):
    r = await client.post(
        "/v1/ide/agents/s1/plan/p1/edit", headers=_headers(), json={"steps": []}
    )
    assert r.status_code == 422
    assert not fake.calls


async def test_editar_manda_los_pasos_corregidos(client, fake):
    pasos = ["leer el README", "reescribir la introducción"]
    r = await client.post(
        "/v1/ide/agents/s1/plan/p1/edit", headers=_headers(), json={"steps": pasos}
    )
    assert r.status_code == 200
    _, accion, params = fake.calls[-1]
    assert accion == "ide_plan_edit"
    assert params["steps"] == pasos


async def test_rechazar_sin_motivo(client, fake):
    r = await client.post("/v1/ide/agents/s1/plan/p1/reject", headers=_headers(), json={})
    assert r.status_code == 200
    _, accion, params = fake.calls[-1]
    assert accion == "ide_plan_reject"
    assert params["reason"] is None


async def test_aprobar_exige_autenticacion(client):
    r = await client.post("/v1/ide/agents/s1/plan/p1/approve")
    assert r.status_code == 401


async def test_consultar_el_plan_vivo(client, fake):
    """`get_active_plan` existía en el companion con un docstring que decía
    "para que la UI sepa si debe mostrar la tarjeta de aprobación", y nunca
    tuvo endpoint. Ese fue el motivo de que un plan propuesto quedara
    invisible en la conversación."""
    r = await client.get("/v1/ide/agents/s1/plan", headers=_headers())
    assert r.status_code == 200
    _, accion, params = fake.calls[-1]
    assert accion == "ide_plan_active"
    assert params == {"session_id": "s1"}


async def test_retomar_no_pide_plan_id(client, fake):
    """La sesión solo puede tener un plan vivo, así que `resume_plan` retoma
    el último y no recibe identificador."""
    r = await client.post("/v1/ide/agents/s1/plan/resume", headers=_headers())
    assert r.status_code == 200
    _, accion, params = fake.calls[-1]
    assert accion == "ide_plan_resume"
    assert params == {"session_id": "s1"}
