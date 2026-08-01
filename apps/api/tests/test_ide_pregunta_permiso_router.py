"""Preguntas y permisos del agente: el cable HTTP que faltaba.

El companion ya exponía las cinco acciones (`ide_agent_question_list|answer|
reject`, `ide_agent_permission_list|answer`) y la interfaz ya traía la tarjeta
que las usa (`AgentThread.tsx::AgentQuestionCard`), pero NO había ninguna ruta
REST en medio: pulsar "Responder" devolvía 404. En modo Manual eso convertía el
freno en una trampa -- opencode pausa esperando permiso, la persona ve el aviso
y no tiene con qué concederlo.

Estos tests cubren el mapeo router→companion (nombre de acción y parámetros),
igual que `test_ide_plan_router.py`. La forma de `respuestas` importa: el
companion valida que sea una lista DE LISTAS (una por pregunta, porque opencode
admite opción múltiple), y el cliente web envuelve cada respuesta antes de
mandarla.
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


# --- Preguntas -------------------------------------------------------------


async def test_listar_preguntas(client, fake):
    r = await client.get("/v1/ide/agents/s1/question", headers=_headers())
    assert r.status_code == 200
    _, accion, params = fake.calls[-1]
    assert accion == "ide_agent_question_list"
    assert params == {"session_id": "s1"}


async def test_responder_manda_la_lista_de_listas_que_espera_el_companion(client, fake):
    # Una respuesta por pregunta, cada una en su propia lista. Si esto se
    # aplanara, `ide_agent_question_answer` lo rechazaría con
    # "«respuestas» debe ser una lista de listas de texto".
    r = await client.post(
        "/v1/ide/agents/s1/question/q1/reply",
        headers=_headers(),
        json={"respuestas": [["Sí"], ["La opción B"]]},
    )
    assert r.status_code == 200
    _, accion, params = fake.calls[-1]
    assert accion == "ide_agent_question_answer"
    assert params["request_id"] == "q1"
    assert params["respuestas"] == [["Sí"], ["La opción B"]]


async def test_responder_sin_ninguna_respuesta_es_invalido(client, fake):
    r = await client.post(
        "/v1/ide/agents/s1/question/q1/reply", headers=_headers(), json={"respuestas": []}
    )
    assert r.status_code == 422
    assert not fake.calls


async def test_rechazar_pregunta(client, fake):
    r = await client.post("/v1/ide/agents/s1/question/q1/reject", headers=_headers())
    assert r.status_code == 200
    _, accion, params = fake.calls[-1]
    assert accion == "ide_agent_question_reject"
    assert params == {"session_id": "s1", "request_id": "q1"}


# --- Permisos --------------------------------------------------------------


async def test_listar_permisos_pendientes(client, fake):
    r = await client.get("/v1/ide/agents/s1/permission", headers=_headers())
    assert r.status_code == 200
    _, accion, params = fake.calls[-1]
    assert accion == "ide_agent_permission_list"
    assert params == {"session_id": "s1"}


async def test_conceder_permiso_continua_el_mismo_turno(client, fake):
    r = await client.post(
        "/v1/ide/agents/s1/permission/p1/answer",
        headers=_headers(),
        json={"conceder": True},
    )
    assert r.status_code == 200
    _, accion, params = fake.calls[-1]
    assert accion == "ide_agent_permission_answer"
    assert params["conceder"] is True
    # `recordar` viaja siempre explícito: el companion decide si lo acepta
    # (lo rechaza para acciones irreversibles), no el router.
    assert params["recordar"] is False


async def test_denegar_permiso(client, fake):
    r = await client.post(
        "/v1/ide/agents/s1/permission/p1/answer",
        headers=_headers(),
        json={"conceder": False, "mensaje": "eso no"},
    )
    assert r.status_code == 200
    _, accion, params = fake.calls[-1]
    assert accion == "ide_agent_permission_answer"
    assert params["conceder"] is False
    assert params["mensaje"] == "eso no"


async def test_conceder_exige_decir_si_o_no(client, fake):
    # Sin `conceder` no hay decisión: 422 antes de tocar el companion. Un
    # default silencioso aquí sería conceder o denegar en nombre de la persona.
    r = await client.post("/v1/ide/agents/s1/permission/p1/answer", headers=_headers(), json={})
    assert r.status_code == 422
    assert not fake.calls


async def test_recordar_viaja_tal_cual_al_companion(client, fake):
    r = await client.post(
        "/v1/ide/agents/s1/permission/p1/answer",
        headers=_headers(),
        json={"conceder": True, "recordar": True},
    )
    assert r.status_code == 200
    _, _, params = fake.calls[-1]
    assert params["recordar"] is True


# --- Autenticación ---------------------------------------------------------


async def test_las_rutas_nuevas_exigen_autenticacion(client):
    assert (await client.get("/v1/ide/agents/s1/question")).status_code == 401
    assert (await client.get("/v1/ide/agents/s1/permission")).status_code == 401
    assert (
        await client.post("/v1/ide/agents/s1/permission/p1/answer", json={"conceder": True})
    ).status_code == 401
