"""`/v1/automations` (`edecan_api.routers.automations`, ROADMAP_V2.md §7.6,
dueño WP-V2-07).

`create_app()` (`edecan_api.main`) todavía no monta este router —
`routers/automations.py` aterriza en este mismo paquete de trabajo, ANTES de
que WP-V2-01 termine el montaje defensivo de `main.py` (ROADMAP_V2.md §7.6).
Por eso este archivo define su PROPIA fixture `app` (mismo nombre que la de
`conftest.py`: pytest resuelve por closest scope, así que esta versión
reemplaza a la de `conftest.py` solo para los tests de este módulo — `client`,
definido en `conftest.py`, la recibe igual porque pytest inyecta `app` por
nombre en el momento de usarla) que arranca `create_app()` normal y le monta
además `automations.router` — cuando `main.py` lo monte de verdad, esta
fixture sigue funcionando (incluir el mismo router dos veces no rompe nada
en un test, cada uno crea su propia instancia de `FastAPI`).

`get_tenant_session` no tiene una tabla `automations`/`automation_runs` en
`edecan_db.models` todavía (WP-V2-01 sigue construyendo la migración
`0003_v2_expansion` en paralelo) — mismo motivo que `test_consents.py`: se
necesita un doble de sesión que "entienda" el SQL de este router, así que
cada test se lo asigna con `_FakeSession` (duplicado a propósito de
`test_hooks_router.py`/`packages/automations/tests/conftest.py`, `ARCHITECTURE.md`
§10.1: los tests no importan paquetes hermanos)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from conftest import auth_headers

import edecan_api.deps as edecan_deps
import edecan_api.routers.automations as automations_module
from edecan_api.main import create_app


class _FakeResult:
    def __init__(self, rows=None, rowcount: int = 0, scalar=None) -> None:
        self._rows = rows if rows is not None else []
        self.rowcount = rowcount
        self._scalar = scalar

    def mappings(self) -> _FakeResult:
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)

    def scalar_one(self):
        return self._scalar


class _FakeSession:
    """`ctx`/`session` falso: cada `execute()` consume la siguiente respuesta
    programada (`respuestas`) y registra `(sql, params)` en `executed`. Sin
    respuesta programada, devuelve un `_FakeResult` vacío (no revienta) —
    útil para rutas que no llegan a ejecutar todas las queries posibles."""

    def __init__(self, respuestas=None) -> None:
        self.respuestas = list(respuestas or [])
        self.executed: list[tuple[str, dict]] = []

    async def execute(self, clause, params=None):
        self.executed.append((str(clause), dict(params or {})))
        if not self.respuestas:
            return _FakeResult()
        siguiente = self.respuestas.pop(0)
        return siguiente if isinstance(siguiente, _FakeResult) else _FakeResult(rows=siguiente)

    async def flush(self) -> None:
        pass


@pytest.fixture
def app(fake_repo, fake_redis, test_settings):
    application = create_app()
    application.include_router(automations_module.router)

    application.dependency_overrides[edecan_deps.get_settings] = lambda: test_settings
    application.dependency_overrides[edecan_deps.get_platform_repo] = lambda: fake_repo
    application.dependency_overrides[edecan_deps.get_repo] = lambda: fake_repo
    application.dependency_overrides[edecan_deps.get_redis] = lambda: fake_redis
    application.dependency_overrides[edecan_deps.get_tenant_session] = lambda: None
    application.dependency_overrides[edecan_deps.get_vault] = lambda: None
    application.dependency_overrides[edecan_deps.get_llm_router] = lambda: None

    yield application
    application.dependency_overrides.clear()


@pytest.fixture
def fake_session() -> _FakeSession:
    return _FakeSession()


@pytest.fixture(autouse=True)
def _wire_fake_session(app, fake_session: _FakeSession):
    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: fake_session


def _row(**overrides) -> dict:
    now = datetime.now(UTC)
    base = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "nombre": "Reporte diario",
        "descripcion": "",
        "trigger": json.dumps({"kind": "schedule", "rrule": "FREQ=DAILY;BYHOUR=9"}),
        "accion": json.dumps({"kind": "agent_instruction", "instruccion": "Manda el reporte."}),
        "enabled": True,
        "next_run_at": now,
        "last_run_at": None,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return base


def _schedule_body(**overrides) -> dict:
    body = {
        "nombre": "Reporte diario",
        "trigger": {"kind": "schedule", "rrule": "FREQ=DAILY;BYHOUR=9"},
        "accion": {"instruccion": "Manda el reporte de ventas."},
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# Autenticación y gate de plan
# ---------------------------------------------------------------------------


async def test_create_requires_authentication(client) -> None:
    response = await client.post("/v1/automations", json=_schedule_body())
    assert response.status_code == 401


async def test_create_rejects_plan_without_automations_flag(client) -> None:
    # "unknown_plan" no existe en PLANES -> flags_for_plan devuelve {} (huérfano).
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="unknown_plan")
    response = await client.post("/v1/automations", json=_schedule_body(), headers=headers)
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# POST /v1/automations (crear)
# ---------------------------------------------------------------------------


async def test_create_schedule_success(client, fake_session: _FakeSession) -> None:
    # free_selfhost: limits.automations_active = -1 (ilimitado) -> _check_limit
    # no consulta nada; la única query es el INSERT.
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="free_selfhost")
    fake_session.respuestas = [[_row(tenant_id=tenant_id)]]

    response = await client.post("/v1/automations", json=_schedule_body(), headers=headers)

    assert response.status_code == 201
    body = response.json()
    assert body["nombre"] == "Reporte diario"
    assert body["trigger"] == {"kind": "schedule", "rrule": "FREQ=DAILY;BYHOUR=9"}
    assert "hook_secret" not in body

    insert_sql, insert_params = fake_session.executed[-1]
    assert "INSERT INTO automations" in insert_sql
    next_run_at = insert_params["next_run_at"]
    assert next_run_at is not None
    assert next_run_at.tzinfo is not None
    assert next_run_at > datetime.now(UTC)


async def test_create_webhook_returns_secret_once(client, fake_session: _FakeSession) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="free_selfhost")
    fake_session.respuestas = [
        [_row(tenant_id=tenant_id, trigger=json.dumps({"kind": "webhook", "hook_secret": "x"}))]
    ]

    response = await client.post(
        "/v1/automations",
        json=_schedule_body(trigger={"kind": "webhook"}),
        headers=headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert "hook_secret" in body and len(body["hook_secret"]) > 10
    assert body["trigger"]["kind"] == "webhook"
    assert body["trigger"]["has_secret"] is True
    assert body["trigger"]["hook_url"].endswith(f"/v1/hooks/{body['id']}")
    assert "hook_secret" not in body["trigger"]  # nunca en el objeto trigger anidado

    insert_sql, insert_params = fake_session.executed[-1]
    trigger_guardado = json.loads(insert_params["trigger"])
    assert trigger_guardado["hook_secret"] == body["hook_secret"]


async def test_create_client_cannot_set_own_hook_secret(client, fake_session: _FakeSession) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="free_selfhost")
    fake_session.respuestas = [[_row(tenant_id=tenant_id)]]

    await client.post(
        "/v1/automations",
        json=_schedule_body(trigger={"kind": "webhook", "hook_secret": "lo-que-yo-quiera"}),
        headers=headers,
    )

    _, insert_params = fake_session.executed[-1]
    trigger_guardado = json.loads(insert_params["trigger"])
    assert trigger_guardado["hook_secret"] != "lo-que-yo-quiera"


async def test_create_invalid_trigger_returns_400_without_query(
    client, fake_session: _FakeSession
) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="free_selfhost")

    response = await client.post(
        "/v1/automations",
        json=_schedule_body(trigger={"kind": "schedule", "rrule": "ESTO NO ES UNA RRULE"}),
        headers=headers,
    )

    assert response.status_code == 400
    assert fake_session.executed == []


async def test_create_invalid_accion_returns_400(client, fake_session: _FakeSession) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="free_selfhost")

    response = await client.post(
        "/v1/automations",
        json=_schedule_body(accion={"instruccion": "   "}),
        headers=headers,
    )

    assert response.status_code == 400
    assert fake_session.executed == []


async def test_create_disabled_skips_limit_check(client, fake_session: _FakeSession) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")
    fake_session.respuestas = [[_row(tenant_id=tenant_id, enabled=False)]]

    response = await client.post(
        "/v1/automations", json=_schedule_body(enabled=False), headers=headers
    )

    assert response.status_code == 201
    assert len(fake_session.executed) == 1  # solo el INSERT, ningún COUNT


# ---------------------------------------------------------------------------
# GET /v1/automations, GET /v1/automations/{id}
# ---------------------------------------------------------------------------


async def test_list_automations_redacts_webhook_secret(client, fake_session: _FakeSession) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="free_selfhost")
    fake_session.respuestas = [
        [_row(trigger=json.dumps({"kind": "webhook", "hook_secret": "muy-secreto"}))]
    ]

    response = await client.get("/v1/automations", headers=headers)

    assert response.status_code == 200
    [item] = response.json()
    assert item["trigger"] == {
        "kind": "webhook",
        "has_secret": True,
        "hook_url": item["trigger"]["hook_url"],
    }
    assert "muy-secreto" not in response.text


async def test_get_automation_not_found(client, fake_session: _FakeSession) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="free_selfhost")
    fake_session.respuestas = [[]]

    response = await client.get(f"/v1/automations/{uuid.uuid4()}", headers=headers)

    assert response.status_code == 404


async def test_get_automation_success(client, fake_session: _FakeSession) -> None:
    tenant_id = uuid.uuid4()
    automation_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="free_selfhost")
    fake_session.respuestas = [[_row(id=automation_id, tenant_id=tenant_id)]]

    response = await client.get(f"/v1/automations/{automation_id}", headers=headers)

    assert response.status_code == 200
    assert response.json()["id"] == str(automation_id)


# ---------------------------------------------------------------------------
# PATCH /v1/automations/{id}
# ---------------------------------------------------------------------------


async def test_patch_not_found(client, fake_session: _FakeSession) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="free_selfhost")
    fake_session.respuestas = [[]]

    response = await client.patch(
        f"/v1/automations/{uuid.uuid4()}", json={"nombre": "Nuevo nombre"}, headers=headers
    )

    assert response.status_code == 404


async def test_patch_nombre_only(client, fake_session: _FakeSession) -> None:
    tenant_id = uuid.uuid4()
    automation_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="free_selfhost")
    current = _row(id=automation_id, tenant_id=tenant_id)
    updated = dict(current, nombre="Reporte semanal")
    fake_session.respuestas = [[current], [updated]]

    response = await client.patch(
        f"/v1/automations/{automation_id}", json={"nombre": "Reporte semanal"}, headers=headers
    )

    assert response.status_code == 200
    assert response.json()["nombre"] == "Reporte semanal"
    update_sql, update_params = fake_session.executed[-1]
    assert "UPDATE automations" in update_sql
    assert "trigger" not in update_params  # no tocado: el PATCH no lo incluyó


async def test_patch_trigger_recomputes_next_run_at(client, fake_session: _FakeSession) -> None:
    tenant_id = uuid.uuid4()
    automation_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="free_selfhost")
    current = _row(id=automation_id, tenant_id=tenant_id)
    updated = dict(current, trigger=json.dumps({"kind": "schedule", "rrule": "FREQ=WEEKLY"}))
    fake_session.respuestas = [[current], [updated]]

    response = await client.patch(
        f"/v1/automations/{automation_id}",
        json={"trigger": {"kind": "schedule", "rrule": "FREQ=WEEKLY"}},
        headers=headers,
    )

    assert response.status_code == 200
    _, update_params = fake_session.executed[-1]
    next_run_at = update_params["next_run_at"]
    assert next_run_at is not None and next_run_at > datetime.now(UTC)


async def test_patch_preserves_existing_webhook_secret(client, fake_session: _FakeSession) -> None:
    tenant_id = uuid.uuid4()
    automation_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="free_selfhost")
    current = _row(
        id=automation_id,
        tenant_id=tenant_id,
        trigger=json.dumps({"kind": "webhook", "hook_secret": "secreto-original"}),
    )
    updated = dict(current, nombre="Renombrada")
    fake_session.respuestas = [[current], [updated]]

    response = await client.patch(
        f"/v1/automations/{automation_id}",
        json={"nombre": "Renombrada", "trigger": {"kind": "webhook"}},
        headers=headers,
    )

    assert response.status_code == 200
    assert "hook_secret" not in response.json()  # no se generó uno nuevo
    _, update_params = fake_session.executed[-1]
    assert json.loads(update_params["trigger"])["hook_secret"] == "secreto-original"


# ---------------------------------------------------------------------------
# DELETE /v1/automations/{id}
# ---------------------------------------------------------------------------


async def test_delete_success(client, fake_session: _FakeSession) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="free_selfhost")
    fake_session.respuestas = [_FakeResult(rowcount=1)]

    response = await client.delete(f"/v1/automations/{uuid.uuid4()}", headers=headers)

    assert response.status_code == 204


async def test_delete_not_found(client, fake_session: _FakeSession) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="free_selfhost")
    fake_session.respuestas = [_FakeResult(rowcount=0)]

    response = await client.delete(f"/v1/automations/{uuid.uuid4()}", headers=headers)

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /v1/automations/{id}/probar, GET /v1/automations/{id}/runs
# ---------------------------------------------------------------------------


async def test_probar_not_found(client, fake_session: _FakeSession) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="free_selfhost")
    fake_session.respuestas = [[]]

    response = await client.post(f"/v1/automations/{uuid.uuid4()}/probar", headers=headers)

    assert response.status_code == 404


async def test_probar_encola_run_automation(
    client, fake_session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = uuid.uuid4()
    automation_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="free_selfhost")
    fake_session.respuestas = [[_row(id=automation_id, tenant_id=tenant_id)]]

    encolados = []

    async def fake_enqueue(settings, job_type, payload, tid):
        encolados.append((job_type, payload, tid))
        return uuid.uuid4()

    monkeypatch.setattr(automations_module, "enqueue", fake_enqueue)

    response = await client.post(f"/v1/automations/{automation_id}/probar", headers=headers)

    assert response.status_code == 202
    assert encolados == [
        ("run_automation", {"automation_id": str(automation_id)}, tenant_id)
    ]


async def test_list_runs_not_found(client, fake_session: _FakeSession) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="free_selfhost")
    fake_session.respuestas = [[]]

    response = await client.get(f"/v1/automations/{uuid.uuid4()}/runs", headers=headers)

    assert response.status_code == 404


async def test_list_runs_success(client, fake_session: _FakeSession) -> None:
    tenant_id = uuid.uuid4()
    automation_id = uuid.uuid4()
    run_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="free_selfhost")
    fake_session.respuestas = [
        [_row(id=automation_id, tenant_id=tenant_id)],
        [
            {
                "id": run_id,
                "status": "waiting_confirmation",
                "detalle": json.dumps({"pendiente": {"name": "enviar_correo"}}),
                "started_at": datetime.now(UTC),
                "finished_at": None,
            }
        ],
    ]

    response = await client.get(f"/v1/automations/{automation_id}/runs", headers=headers)

    assert response.status_code == 200
    [run] = response.json()
    assert run["id"] == str(run_id)
    assert run["status"] == "waiting_confirmation"
    assert run["detalle"]["pendiente"]["name"] == "enviar_correo"
    assert run["finished_at"] is None
