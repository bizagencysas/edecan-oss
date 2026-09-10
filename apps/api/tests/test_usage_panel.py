"""`GET /v1/usage/diario` y `GET /v1/usage/por_job` — panel de costos server-side.

Estos dos endpoints completan la pantalla de uso del iOS con vistas que solo
el servidor puede armar: la serie diaria de tokens/costo y el desglose por
job (`meta->>'job'` de `llm_tokens`), más una alerta de presupuesto por log.

Los tests usan un fake local del repo (solo los dos métodos nuevos) en vez de
`api_fakes.FakeRepo`, para poder controlar días/costos exactos sin tocar el
fake compartido.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from conftest import TEST_JWT_SECRET, auth_headers

from edecan_api import deps as edecan_deps
from edecan_api.config import Settings, get_settings


class _FakePanelRepo:
    """Repo mínimo para los endpoints `/diario` y `/por_job`."""

    def __init__(
        self,
        *,
        diario: list[dict[str, Any]] | None = None,
        jobs: list[dict[str, Any]] | None = None,
    ) -> None:
        self.diario = list(diario or [])
        self.jobs = list(jobs or [])
        self.calls_diario: list[tuple[uuid.UUID, datetime | None]] = []
        self.calls_jobs: list[tuple[uuid.UUID, datetime | None]] = []

    async def usage_llm_diario_desde(self, *, tenant_id: uuid.UUID, since: datetime | None):
        self.calls_diario.append((tenant_id, since))
        return self.diario

    async def usage_llm_por_job_desde(self, *, tenant_id: uuid.UUID, since: datetime | None):
        self.calls_jobs.append((tenant_id, since))
        return self.jobs


def _fila_dia(
    dia: date,
    *,
    llamadas: int = 1,
    tin: int = 100,
    tout: int = 50,
    costo: float = 1.0,
    completo: bool = True,
) -> dict[str, Any]:
    return {
        "dia": dia,
        "dia_completo": completo,
        "llamadas": llamadas,
        "tokens_entrada": tin,
        "tokens_salida": tout,
        "costo_usd": costo,
    }


def _fila_job(
    job: str, *, llamadas: int = 1, tin: int = 100, tout: int = 50, costo: float = 1.0
) -> dict[str, Any]:
    return {
        "job": job,
        "llamadas": llamadas,
        "tokens_entrada": tin,
        "tokens_salida": tout,
        "costo_usd": costo,
    }


def _override_repo(app, repo: _FakePanelRepo) -> _FakePanelRepo:
    app.dependency_overrides[edecan_deps.get_repo] = lambda: repo
    return repo


def _override_settings(app, *, umbral: float) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,
        ENV="dev",
        JWT_SECRET=TEST_JWT_SECRET,
        USAGE_ALERT_USD_PER_DAY=umbral,
    )


# ---------------------------------------------------------------------------
# /v1/usage/diario
# ---------------------------------------------------------------------------


async def test_diario_devuelve_serie_y_pasa_since_de_30_dias(client, app) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    ayer = date.today() - timedelta(days=1)
    repo = _override_repo(
        app,
        _FakePanelRepo(
            diario=[
                _fila_dia(ayer, llamadas=2, tin=120, tout=80, costo=0.75),
                _fila_dia(ayer - timedelta(days=1), llamadas=1, tin=10, tout=5, costo=0.05),
            ]
        ),
    )

    response = await client.get("/v1/usage/diario?periodo=30", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["periodo"] == "30"
    assert body["dias"][0]["dia"] == ayer.isoformat()
    assert body["dias"][0]["llamadas"] == 2
    assert body["dias"][0]["tokens_entrada"] == 120
    assert body["dias"][0]["tokens_salida"] == 80
    assert body["dias"][0]["costo_usd"] == 0.75
    assert body["dias"][1]["dia"] == (ayer - timedelta(days=1)).isoformat()

    (tenant_recibido, since) = repo.calls_diario[0]
    assert tenant_recibido == tenant_id
    esperado = datetime.now(UTC) - timedelta(days=30)
    assert abs((since - esperado).total_seconds()) < 5


async def test_diario_periodo_todo_pasa_since_none(client, app) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    repo = _override_repo(app, _FakePanelRepo(diario=[]))

    response = await client.get("/v1/usage/diario?periodo=todo", headers=headers)

    assert response.status_code == 200
    assert response.json()["dias"] == []
    assert repo.calls_diario[0][1] is None


async def test_diario_periodo_invalido_422(client, app) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    _override_repo(app, _FakePanelRepo())

    response = await client.get("/v1/usage/diario?periodo=90", headers=headers)

    assert response.status_code == 422
    assert "periodo inválido" in response.json()["detail"]


async def test_diario_alerta_cuando_ultimo_dia_completo_supera_umbral(
    client, app, caplog: pytest.LogCaptureFixture
) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    hoy = date.today()
    ayer = hoy - timedelta(days=1)
    _override_settings(app, umbral=5.0)
    _override_repo(
        app,
        _FakePanelRepo(
            diario=[
                # Hoy está incompleto: costo enorme pero NO debe alertar.
                _fila_dia(hoy, costo=999.0, completo=False),
                _fila_dia(ayer, costo=7.5, completo=True),
            ]
        ),
    )

    with caplog.at_level(logging.WARNING, logger="edecan_api.routers.usage"):
        response = await client.get("/v1/usage/diario", headers=headers)

    assert response.status_code == 200
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    mensaje = warnings[0].getMessage()
    assert f"dia={ayer.isoformat()}" in mensaje
    assert "costo_usd=7.5000" in mensaje
    assert "umbral_usd=5.0000" in mensaje
    assert hoy.isoformat() not in mensaje


async def test_diario_no_alerta_bajo_umbral(client, app, caplog: pytest.LogCaptureFixture) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    ayer = date.today() - timedelta(days=1)
    _override_settings(app, umbral=5.0)
    _override_repo(app, _FakePanelRepo(diario=[_fila_dia(ayer, costo=4.99, completo=True)]))

    with caplog.at_level(logging.WARNING, logger="edecan_api.routers.usage"):
        response = await client.get("/v1/usage/diario", headers=headers)

    assert response.status_code == 200
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


async def test_diario_no_alerta_si_el_dia_reciente_esta_incompleto(
    client, app, caplog: pytest.LogCaptureFixture
) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    _override_settings(app, umbral=0.01)
    _override_repo(
        app,
        _FakePanelRepo(diario=[_fila_dia(date.today(), costo=999.0, completo=False)]),
    )

    with caplog.at_level(logging.WARNING, logger="edecan_api.routers.usage"):
        response = await client.get("/v1/usage/diario", headers=headers)

    assert response.status_code == 200
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


async def test_diario_alerta_sin_estado_avisa_en_cada_request(
    client, app, caplog: pytest.LogCaptureFixture
) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    ayer = date.today() - timedelta(days=1)
    _override_settings(app, umbral=5.0)
    _override_repo(app, _FakePanelRepo(diario=[_fila_dia(ayer, costo=6.0, completo=True)]))

    with caplog.at_level(logging.WARNING, logger="edecan_api.routers.usage"):
        for _ in range(2):
            response = await client.get("/v1/usage/diario", headers=headers)
            assert response.status_code == 200

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2


async def test_diario_sin_filas_no_alerta(client, app, caplog: pytest.LogCaptureFixture) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    _override_settings(app, umbral=0.001)
    _override_repo(app, _FakePanelRepo(diario=[]))

    with caplog.at_level(logging.WARNING, logger="edecan_api.routers.usage"):
        response = await client.get("/v1/usage/diario", headers=headers)

    assert response.status_code == 200
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


# ---------------------------------------------------------------------------
# /v1/usage/por_job
# ---------------------------------------------------------------------------


async def test_por_job_devuelve_desglose_y_pasa_since(client, app) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    repo = _override_repo(
        app,
        _FakePanelRepo(
            jobs=[
                _fila_job("memory_consolidate", llamadas=3, tin=300, tout=150, costo=1.25),
                _fila_job("sin_job", llamadas=1, tin=20, tout=10, costo=0.0),
            ]
        ),
    )

    response = await client.get("/v1/usage/por_job?periodo=7", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["periodo"] == "7"
    assert body["jobs"][0]["job"] == "memory_consolidate"
    assert body["jobs"][0]["llamadas"] == 3
    assert body["jobs"][0]["tokens_entrada"] == 300
    assert body["jobs"][0]["tokens_salida"] == 150
    assert body["jobs"][0]["costo_usd"] == 1.25
    assert body["jobs"][1]["job"] == "sin_job"

    (tenant_recibido, since) = repo.calls_jobs[0]
    assert tenant_recibido == tenant_id
    esperado = datetime.now(UTC) - timedelta(days=7)
    assert abs((since - esperado).total_seconds()) < 5


async def test_por_job_periodo_invalido_422(client, app) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    _override_repo(app, _FakePanelRepo())

    response = await client.get("/v1/usage/por_job?periodo=año", headers=headers)

    assert response.status_code == 422
    assert "periodo inválido" in response.json()["detail"]