"""`edecan_api.routers.missions` (`ROADMAP_V2.md` §7.4, §7.6, §7.9).

`edecan_api.main.create_app()` ya monta `missions.router` de forma defensiva
(`ROADMAP_V2.md` §7.6, dueño WP-V2-01: `importlib.import_module` + `try/except
ImportError` por cada router v2 — este WP aterrizó el archivo que ese loop
importa). Aun así, el fixture `_mounted_app` de aquí abajo revisa si el
router YA está montado antes de incluirlo a mano (mismo patrón defensivo que
`test_remote_router.py`) — así este archivo sigue funcionando sin cambios si
algún día se ejecuta contra un `main.py` más viejo que todavía no lo monte
(p. ej. una app de test armada a mano sin pasar por `create_app()`).

`conftest.app` deja `get_tenant_session` apuntando a `None` (ver docstring de
`test_consents.py`) — aquí sí hace falta un doble que entienda el SQL de
`missions.py`, así que cada test se lo asigna con `app.dependency_overrides[...]`
vía el fixture `_mounted_app`.

Los flags/límites de plan (`agents.missions`, `limits.missions_per_day`) YA
existen en `edecan_schemas.plans.PLANES` (WP-V2-01 los aterrizó). Modelo de
precio de pago único (2026-07-09, `edecan_schemas.plans` docstring):
`agents.missions` está en `True` y `limits.missions_per_day` en `UNLIMITED`
(`-1`) en las 4 entradas de `PLANES` por igual — no hay más "flag apagado"
ni "cuota chica por plan" que probar. La mayoría de los tests usa
`auth_headers(plan_key="hosted_pro")` como cualquier `plan_key` válido. Solo
el test del cupo agotado necesita un límite chico y manejable: usa
`monkeypatch.setitem` sobre `PLANES["hosted_pro"].flags` (se revierte solo al
terminar el test, sin mutar estado global entre tests).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from conftest import auth_headers
from edecan_schemas.plans import LIMIT_MISSIONS_PER_DAY, PLANES
from httpx import ASGITransport, AsyncClient

import edecan_api.deps as edecan_deps
from edecan_api.routers import missions

PLAN_WITH_MISSIONS = "hosted_pro"  # agents.missions=True, limits.missions_per_day=20
PLAN_UNLIMITED = "free_selfhost"  # agents.missions=True, limits.missions_per_day=-1


class _FakeResult:
    def __init__(self, rows: list[dict] | None = None, scalar_value: int | None = None) -> None:
        self._rows = rows or []
        self._scalar_value = scalar_value

    def mappings(self) -> _FakeResult:
        return self

    def first(self) -> dict | None:
        return dict(self._rows[0]) if self._rows else None

    def all(self) -> list[dict]:
        return [dict(r) for r in self._rows]

    def scalar(self) -> int | None:
        return self._scalar_value


class FakeSession:
    """Entiende (por prefijo SQL + claves de `params`) las queries de
    `missions.py` — mismo espíritu que `_FakeSession` en
    `premium/tests/test_campaigns.py`/`test_consents.py`."""

    def __init__(self) -> None:
        self.missions: dict[str, dict] = {}
        self.steps: dict[tuple[str, int], dict] = {}
        self.executed: list[tuple[str, dict]] = []

    def seed_mission(
        self, *, mission_id: uuid.UUID, tenant_id: uuid.UUID, user_id: uuid.UUID, **fields
    ) -> dict:
        # `tenant_id`/`user_id`/`id` se guardan como `str(...)`: el router
        # SIEMPRE liga sus params con `str(uuid)` (ver `missions.py`), así que
        # comparar un `uuid.UUID` guardado aquí contra el `str` que llega en
        # `params` nunca sería igual (`UUID.__eq__` no compara con `str`) —
        # mismo criterio de normalización en ambos lados que usaría Postgres
        # de verdad al comparar una columna `uuid` con un bind param.
        row = {
            "id": str(mission_id),
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "objetivo": "Objetivo de prueba",
            "status": "planning",
            "plan": None,
            "resultado": None,
            "presupuesto": {"max_steps": 8},
            "error": None,
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
        row.update(fields)
        self.missions[str(mission_id)] = row
        return row

    def seed_step(self, *, mission_id: uuid.UUID, tenant_id: uuid.UUID, seq: int, **fields) -> dict:
        row = {
            "id": str(uuid.uuid4()),
            "tenant_id": str(tenant_id),
            "mission_id": str(mission_id),
            "seq": seq,
            "agente": "research",
            "instruccion": f"paso {seq}",
            "status": "pending",
            "resultado": None,
            "usage": None,
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
        row.update(fields)
        self.steps[(str(mission_id), seq)] = row
        return row

    async def execute(self, clause, params=None) -> _FakeResult:
        sql = str(clause)
        params = dict(params or {})
        self.executed.append((sql, params))
        primer = sql.strip().split(None, 1)[0].upper()
        es_missions = "agent_missions" in sql
        es_steps = "agent_steps" in sql

        if primer == "SELECT" and es_missions and "COUNT(*)" in sql:
            since = params["since"]
            count = sum(
                1
                for row in self.missions.values()
                if row["tenant_id"] == params["tenant_id"] and row["created_at"] >= since
            )
            return _FakeResult(scalar_value=count)

        if primer == "SELECT" and es_missions and "id" in params:
            row = self.missions.get(params["id"])
            coincide = (
                row is not None
                and row["tenant_id"] == params["tenant_id"]
                and row["user_id"] == params["user_id"]
            )
            return _FakeResult(rows=[row] if coincide else [])

        if primer == "SELECT" and es_missions:
            rows = [
                row
                for row in self.missions.values()
                if row["tenant_id"] == params["tenant_id"] and row["user_id"] == params["user_id"]
            ]
            rows.sort(key=lambda r: r["created_at"], reverse=True)
            return _FakeResult(rows=rows)

        if primer == "INSERT" and es_missions:
            row = {
                "id": str(uuid.uuid4()),
                "tenant_id": params["tenant_id"],
                "user_id": params["user_id"],
                "objetivo": params["objetivo"],
                "status": "planning",
                "plan": None,
                "resultado": None,
                "presupuesto": json.loads(params["presupuesto"]),
                "error": None,
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            }
            self.missions[str(row["id"])] = row
            return _FakeResult(rows=[row])

        if primer == "SELECT" and es_steps and "waiting" in params:
            candidatos = sorted(
                (
                    row
                    for (mission_id, _seq), row in self.steps.items()
                    if mission_id == params["mission_id"]
                    and row["tenant_id"] == params["tenant_id"]
                    and row["status"] == params["waiting"]
                ),
                key=lambda r: r["seq"],
            )
            return _FakeResult(rows=[candidatos[0]] if candidatos else [])

        if primer == "SELECT" and es_steps:
            rows = sorted(
                (
                    row
                    for (mission_id, _seq), row in self.steps.items()
                    if mission_id == params["mission_id"]
                    and row["tenant_id"] == params["tenant_id"]
                ),
                key=lambda r: r["seq"],
            )
            return _FakeResult(rows=rows)

        if primer == "UPDATE" and es_steps and "waiting" in params:
            for (mission_id, _seq), row in self.steps.items():
                if (
                    mission_id == params["mission_id"]
                    and row["tenant_id"] == params["tenant_id"]
                    and row["status"] == params["waiting"]
                ):
                    row["status"] = "skipped"
            return _FakeResult()

        if primer == "UPDATE" and es_missions:
            row = self.missions.get(params["id"])
            if row is not None and row["tenant_id"] == params["tenant_id"]:
                row["status"] = params["status"]
            return _FakeResult()

        raise AssertionError(f"query inesperada en el fake: {sql} params={params}")


@pytest.fixture
def fake_session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def _mounted_app(app, fake_session: FakeSession):
    # `edecan_api.main.create_app()` puede o no traer ya `missions.router`
    # montado (montaje defensivo v2, ROADMAP_V2.md §7.6, dueño WP-V2-01):
    # solo se incluye a mano si todavía no está, para no registrar las
    # mismas rutas dos veces (evita duplicar `APIRoute`s sobre la misma
    # `app`, que confunde el orden de resolución de dependencias).
    ya_montado = any(getattr(route, "path", "") == "/v1/missions" for route in app.routes)
    if not ya_montado:
        app.include_router(missions.router)
    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: fake_session
    return app


@pytest.fixture
async def client(_mounted_app) -> AsyncIterator[AsyncClient]:
    # Sombrea a propósito el `client` de `conftest.py`: ese vive sobre `app`
    # sin `missions.router` montado ni `get_tenant_session` con un doble útil.
    transport = ASGITransport(app=_mounted_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _install_fake_enqueue(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    calls: list[tuple] = []

    async def fake_enqueue(settings, job_type, payload, tenant_id, **kwargs):
        calls.append((settings, job_type, payload, tenant_id))
        return uuid.uuid4()

    monkeypatch.setattr(missions, "enqueue", fake_enqueue)
    return calls


@pytest.fixture(autouse=True)
def fake_enqueue_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    return _install_fake_enqueue(monkeypatch)


# ---------------------------------------------------------------------------
# Autenticación / flag gate
# ---------------------------------------------------------------------------


async def test_create_mission_requires_authentication(client) -> None:
    response = await client.post("/v1/missions", json={"objetivo": "x"})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /v1/missions
# ---------------------------------------------------------------------------


async def test_create_mission_rejects_objetivo_vacio(client, fake_session: FakeSession) -> None:
    headers = auth_headers(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key=PLAN_WITH_MISSIONS
    )
    response = await client.post("/v1/missions", json={"objetivo": "   "}, headers=headers)
    assert response.status_code == 400
    assert fake_session.missions == {}


async def test_create_mission_success(
    client, fake_session: FakeSession, fake_enqueue_calls: list[tuple]
) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key=PLAN_WITH_MISSIONS)

    response = await client.post(
        "/v1/missions", json={"objetivo": "Investiga el mercado"}, headers=headers
    )

    assert response.status_code == 201
    body = response.json()
    assert body["objetivo"] == "Investiga el mercado"
    assert body["status"] == "planning"
    assert body["presupuesto"] == {"max_steps": 8}
    mission_id = body["id"]

    assert len(fake_enqueue_calls) == 1
    _settings, job_type, payload, enq_tenant_id = fake_enqueue_calls[0]
    assert job_type == "run_mission"
    assert payload == {"mission_id": mission_id}
    assert str(enq_tenant_id) == str(tenant_id)


async def test_create_mission_cupo_agotado_devuelve_429(
    client,
    fake_session: FakeSession,
    monkeypatch: pytest.MonkeyPatch,
    fake_enqueue_calls: list[tuple],
) -> None:
    monkeypatch.setitem(PLANES[PLAN_WITH_MISSIONS].flags, LIMIT_MISSIONS_PER_DAY, 1)
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    fake_session.seed_mission(mission_id=uuid.uuid4(), tenant_id=tenant_id, user_id=user_id)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key=PLAN_WITH_MISSIONS)

    response = await client.post("/v1/missions", json={"objetivo": "x"}, headers=headers)

    assert response.status_code == 429
    assert fake_enqueue_calls == []


async def test_create_mission_plan_ilimitado_no_bloquea_aunque_haya_muchas(
    client, fake_session: FakeSession
) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    for _ in range(5):
        fake_session.seed_mission(mission_id=uuid.uuid4(), tenant_id=tenant_id, user_id=user_id)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key=PLAN_UNLIMITED)

    response = await client.post("/v1/missions", json={"objetivo": "x"}, headers=headers)

    assert response.status_code == 201


# ---------------------------------------------------------------------------
# GET /v1/missions, GET /v1/missions/{id}
# ---------------------------------------------------------------------------


async def test_list_missions_solo_devuelve_las_del_usuario_y_tenant_actual(
    client, fake_session: FakeSession
) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    mia = fake_session.seed_mission(
        mission_id=uuid.uuid4(), tenant_id=tenant_id, user_id=user_id, objetivo="la mía"
    )
    fake_session.seed_mission(
        mission_id=uuid.uuid4(),
        tenant_id=tenant_id,
        user_id=uuid.uuid4(),
        objetivo="de otro usuario",
    )
    fake_session.seed_mission(
        mission_id=uuid.uuid4(), tenant_id=uuid.uuid4(), user_id=user_id, objetivo="de otro tenant"
    )
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key=PLAN_WITH_MISSIONS)

    response = await client.get("/v1/missions", headers=headers)

    assert response.status_code == 200
    objetivos = [m["objetivo"] for m in response.json()]
    assert objetivos == ["la mía"]
    assert response.json()[0]["id"] == str(mia["id"])


async def test_get_mission_incluye_los_pasos(client, fake_session: FakeSession) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    mission_id = uuid.uuid4()
    fake_session.seed_mission(mission_id=mission_id, tenant_id=tenant_id, user_id=user_id)
    fake_session.seed_step(
        mission_id=mission_id, tenant_id=tenant_id, seq=1, agente="research", status="done"
    )
    fake_session.seed_step(
        mission_id=mission_id, tenant_id=tenant_id, seq=2, agente="content", status="pending"
    )
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key=PLAN_WITH_MISSIONS)

    response = await client.get(f"/v1/missions/{mission_id}", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["mission"]["id"] == str(mission_id)
    assert [s["seq"] for s in body["steps"]] == [1, 2]
    assert body["steps"][0]["status"] == "done"


async def test_get_mission_404_si_no_existe(client) -> None:
    headers = auth_headers(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key=PLAN_WITH_MISSIONS
    )
    response = await client.get(f"/v1/missions/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404


async def test_get_mission_404_si_es_de_otro_tenant(client, fake_session: FakeSession) -> None:
    mission_id = uuid.uuid4()
    fake_session.seed_mission(mission_id=mission_id, tenant_id=uuid.uuid4(), user_id=uuid.uuid4())
    headers = auth_headers(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key=PLAN_WITH_MISSIONS
    )

    response = await client.get(f"/v1/missions/{mission_id}", headers=headers)

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /v1/missions/{id}/detalle (WP-V6-10)
# ---------------------------------------------------------------------------


async def test_get_mission_detalle_requires_authentication(client) -> None:
    response = await client.get(f"/v1/missions/{uuid.uuid4()}/detalle")
    assert response.status_code == 401


async def test_get_mission_detalle_404_si_no_existe(client) -> None:
    headers = auth_headers(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key=PLAN_WITH_MISSIONS
    )
    response = await client.get(f"/v1/missions/{uuid.uuid4()}/detalle", headers=headers)
    assert response.status_code == 404


async def test_get_mission_detalle_404_si_es_de_otro_tenant(
    client, fake_session: FakeSession
) -> None:
    mission_id = uuid.uuid4()
    fake_session.seed_mission(mission_id=mission_id, tenant_id=uuid.uuid4(), user_id=uuid.uuid4())
    headers = auth_headers(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key=PLAN_WITH_MISSIONS
    )

    response = await client.get(f"/v1/missions/{mission_id}/detalle", headers=headers)

    assert response.status_code == 404


async def test_get_mission_detalle_expone_presupuesto_con_replans_usados(
    client, fake_session: FakeSession
) -> None:
    """`mission.presupuesto` viaja tal cual vive en el jsonb — incluye
    `replans_usados` cuando `Orchestrator.run` ya replaneó (WP-V5-05), sin
    que este endpoint tenga que inventar/calcular ese campo."""
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    mission_id = uuid.uuid4()
    fake_session.seed_mission(
        mission_id=mission_id,
        tenant_id=tenant_id,
        user_id=user_id,
        presupuesto={"max_steps": 8, "replans_usados": 1},
    )
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key=PLAN_WITH_MISSIONS)

    response = await client.get(f"/v1/missions/{mission_id}/detalle", headers=headers)

    assert response.status_code == 200
    assert response.json()["mission"]["presupuesto"] == {"max_steps": 8, "replans_usados": 1}


async def test_get_mission_detalle_enriquece_cada_paso_con_usage_started_finished(
    client, fake_session: FakeSession
) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    mission_id = uuid.uuid4()
    fake_session.seed_mission(mission_id=mission_id, tenant_id=tenant_id, user_id=user_id)
    fake_session.seed_step(
        mission_id=mission_id,
        tenant_id=tenant_id,
        seq=1,
        agente="research",
        status="done",
        resultado="resultado corto",
        usage={
            "input_tokens": 100,
            "output_tokens": 50,
            "started_at": "2026-01-01T00:00:00+00:00",
            "finished_at": "2026-01-01T00:00:05+00:00",
        },
    )
    fake_session.seed_step(
        mission_id=mission_id,
        tenant_id=tenant_id,
        seq=2,
        agente="content",
        status="waiting_confirmation",
        usage={
            "pending_tool_call": {"id": "call-1", "name": "usar_computadora", "args": {}},
            "started_at": "2026-01-01T00:00:06+00:00",
            "finished_at": "2026-01-01T00:00:07+00:00",
        },
    )
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key=PLAN_WITH_MISSIONS)

    response = await client.get(f"/v1/missions/{mission_id}/detalle", headers=headers)

    assert response.status_code == 200
    steps = response.json()["steps"]
    assert len(steps) == 2

    paso1 = steps[0]
    assert paso1["seq"] == 1
    assert paso1["resultado_truncado"] == "resultado corto"
    assert paso1["usage"]["input_tokens"] == 100
    assert paso1["started"] == "2026-01-01T00:00:00+00:00"
    assert paso1["finished"] == "2026-01-01T00:00:05+00:00"

    paso2 = steps[1]
    assert paso2["status"] == "waiting_confirmation"
    assert paso2["usage"]["pending_tool_call"] == {
        "id": "call-1",
        "name": "usar_computadora",
        "args": {},
    }
    assert paso2["started"] == "2026-01-01T00:00:06+00:00"
    assert paso2["finished"] == "2026-01-01T00:00:07+00:00"


async def test_get_mission_detalle_pasos_sin_usage_devuelven_started_finished_none(
    client, fake_session: FakeSession
) -> None:
    """Pasos que corrieron antes de WP-V6-10 (o que siguen `pending`, sin
    `usage` en absoluto) no inventan `started`/`finished`: quedan `None`."""
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    mission_id = uuid.uuid4()
    fake_session.seed_mission(mission_id=mission_id, tenant_id=tenant_id, user_id=user_id)
    fake_session.seed_step(mission_id=mission_id, tenant_id=tenant_id, seq=1, status="pending")
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key=PLAN_WITH_MISSIONS)

    response = await client.get(f"/v1/missions/{mission_id}/detalle", headers=headers)

    assert response.status_code == 200
    paso = response.json()["steps"][0]
    assert paso["usage"] is None
    assert paso["started"] is None
    assert paso["finished"] is None


async def test_get_mission_detalle_trunca_resultados_largos(
    client, fake_session: FakeSession
) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    mission_id = uuid.uuid4()
    fake_session.seed_mission(mission_id=mission_id, tenant_id=tenant_id, user_id=user_id)
    texto_largo = "x" * 2500
    fake_session.seed_step(
        mission_id=mission_id, tenant_id=tenant_id, seq=1, status="done", resultado=texto_largo
    )
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key=PLAN_WITH_MISSIONS)

    response = await client.get(f"/v1/missions/{mission_id}/detalle", headers=headers)

    assert response.status_code == 200
    resultado = response.json()["steps"][0]["resultado_truncado"]
    assert len(resultado) < len(texto_largo)
    assert resultado.startswith("x" * 2000)
    assert "truncado" in resultado


async def test_get_mission_detalle_no_trunca_resultados_cortos(
    client, fake_session: FakeSession
) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    mission_id = uuid.uuid4()
    fake_session.seed_mission(mission_id=mission_id, tenant_id=tenant_id, user_id=user_id)
    fake_session.seed_step(
        mission_id=mission_id, tenant_id=tenant_id, seq=1, status="done", resultado="ok"
    )
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key=PLAN_WITH_MISSIONS)

    response = await client.get(f"/v1/missions/{mission_id}/detalle", headers=headers)

    assert response.status_code == 200
    assert response.json()["steps"][0]["resultado_truncado"] == "ok"


async def test_get_mission_detalle_agregados_suma_tokens_y_cuenta_status(
    client, fake_session: FakeSession
) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    mission_id = uuid.uuid4()
    fake_session.seed_mission(mission_id=mission_id, tenant_id=tenant_id, user_id=user_id)
    fake_session.seed_step(
        mission_id=mission_id,
        tenant_id=tenant_id,
        seq=1,
        status="done",
        usage={"input_tokens": 100, "output_tokens": 20},
    )
    fake_session.seed_step(
        mission_id=mission_id,
        tenant_id=tenant_id,
        seq=2,
        status="done",
        usage={"input_tokens": 50, "output_tokens": 10, "cache_read_tokens": 5},
    )
    fake_session.seed_step(mission_id=mission_id, tenant_id=tenant_id, seq=3, status="error")
    fake_session.seed_step(mission_id=mission_id, tenant_id=tenant_id, seq=4, status="skipped")
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key=PLAN_WITH_MISSIONS)

    response = await client.get(f"/v1/missions/{mission_id}/detalle", headers=headers)

    assert response.status_code == 200
    agregados = response.json()["agregados"]
    assert agregados["tokens_totales_por_tipo"] == {
        "input_tokens": 150,
        "output_tokens": 30,
        "cache_read_tokens": 5,
    }
    # las 6 claves de `MISSION_STEP_STATUSES` siempre presentes, en 0 si el
    # status no aparece entre los pasos de la misión.
    assert agregados["pasos_por_status"] == {
        "pending": 0,
        "running": 0,
        "waiting_confirmation": 0,
        "done": 2,
        "error": 1,
        "skipped": 1,
    }


async def test_get_mission_detalle_agregados_ignora_claves_no_token_y_booleanos(
    client, fake_session: FakeSession
) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    mission_id = uuid.uuid4()
    fake_session.seed_mission(mission_id=mission_id, tenant_id=tenant_id, user_id=user_id)
    fake_session.seed_step(
        mission_id=mission_id,
        tenant_id=tenant_id,
        seq=1,
        status="done",
        usage={
            "input_tokens": 10,
            "started_at": "2026-01-01T00:00:00+00:00",  # no termina en "_tokens"
            "es_gratis_tokens": True,  # termina en "_tokens" pero es bool -> se ignora
        },
    )
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key=PLAN_WITH_MISSIONS)

    response = await client.get(f"/v1/missions/{mission_id}/detalle", headers=headers)

    assert response.status_code == 200
    assert response.json()["agregados"]["tokens_totales_por_tipo"] == {"input_tokens": 10}


async def test_get_mission_detalle_sin_pasos_agregados_en_cero(
    client, fake_session: FakeSession
) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    mission_id = uuid.uuid4()
    fake_session.seed_mission(mission_id=mission_id, tenant_id=tenant_id, user_id=user_id)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key=PLAN_WITH_MISSIONS)

    response = await client.get(f"/v1/missions/{mission_id}/detalle", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["steps"] == []
    assert body["agregados"]["tokens_totales_por_tipo"] == {}
    assert body["agregados"]["pasos_por_status"] == {
        "pending": 0,
        "running": 0,
        "waiting_confirmation": 0,
        "done": 0,
        "error": 0,
        "skipped": 0,
    }


async def test_get_mission_no_cambia_su_contrato_tras_agregar_detalle(
    client, fake_session: FakeSession
) -> None:
    """`GET /{id}` y `GET /{id}/detalle` comparten `_get_mission_and_steps`
    (WP-V6-10) — este test fija que el contrato ADITIVO se cumplió: `GET
    /{id}` sigue sin `agregados` ni `resultado_truncado`, `resultado` tal
    cual sin recortar."""
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    mission_id = uuid.uuid4()
    fake_session.seed_mission(mission_id=mission_id, tenant_id=tenant_id, user_id=user_id)
    fake_session.seed_step(
        mission_id=mission_id, tenant_id=tenant_id, seq=1, status="done", resultado="ok"
    )
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key=PLAN_WITH_MISSIONS)

    response = await client.get(f"/v1/missions/{mission_id}", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert "agregados" not in body
    assert body["steps"][0]["resultado"] == "ok"
    assert "resultado_truncado" not in body["steps"][0]


# ---------------------------------------------------------------------------
# POST /v1/missions/{id}/confirm
# ---------------------------------------------------------------------------


async def test_confirm_404_si_no_existe(client) -> None:
    headers = auth_headers(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key=PLAN_WITH_MISSIONS
    )
    response = await client.post(
        f"/v1/missions/{uuid.uuid4()}/confirm", json={"approved": True}, headers=headers
    )
    assert response.status_code == 404


async def test_confirm_409_si_no_esta_waiting_confirmation(
    client, fake_session: FakeSession
) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    mission_id = uuid.uuid4()
    fake_session.seed_mission(
        mission_id=mission_id, tenant_id=tenant_id, user_id=user_id, status="running"
    )
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key=PLAN_WITH_MISSIONS)

    response = await client.post(
        f"/v1/missions/{mission_id}/confirm", json={"approved": True}, headers=headers
    )

    assert response.status_code == 409


async def test_confirm_rechazado_cancela_mision_y_marca_paso_skipped(
    client, fake_session: FakeSession, fake_enqueue_calls: list[tuple]
) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    mission_id = uuid.uuid4()
    fake_session.seed_mission(
        mission_id=mission_id, tenant_id=tenant_id, user_id=user_id, status="waiting_confirmation"
    )
    fake_session.seed_step(
        mission_id=mission_id,
        tenant_id=tenant_id,
        seq=1,
        status="waiting_confirmation",
        usage={"pending_tool_call": {"id": "call-1", "name": "x", "args": {}}},
    )
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key=PLAN_WITH_MISSIONS)

    response = await client.post(
        f"/v1/missions/{mission_id}/confirm", json={"approved": False}, headers=headers
    )

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert fake_session.steps[(str(mission_id), 1)]["status"] == "skipped"
    assert fake_enqueue_calls == []  # rechazar nunca encola


async def test_confirm_aprobado_reencola_resume_con_el_seq_pendiente(
    client, fake_session: FakeSession, fake_enqueue_calls: list[tuple]
) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    mission_id = uuid.uuid4()
    fake_session.seed_mission(
        mission_id=mission_id, tenant_id=tenant_id, user_id=user_id, status="waiting_confirmation"
    )
    fake_session.seed_step(mission_id=mission_id, tenant_id=tenant_id, seq=1, status="done")
    fake_session.seed_step(
        mission_id=mission_id,
        tenant_id=tenant_id,
        seq=2,
        status="waiting_confirmation",
        usage={"pending_tool_call": {"id": "call-guardado", "name": "x", "args": {}}},
    )
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key=PLAN_WITH_MISSIONS)

    response = await client.post(
        f"/v1/missions/{mission_id}/confirm", json={"approved": True}, headers=headers
    )

    assert response.status_code == 200
    assert response.json()["status"] == "running"
    assert fake_session.missions[str(mission_id)]["status"] == "running"

    assert len(fake_enqueue_calls) == 1
    _settings, job_type, payload, enq_tenant_id = fake_enqueue_calls[0]
    assert job_type == "run_mission"
    assert payload == {"mission_id": str(mission_id), "resume": True, "approved_step_seq": 2}
    assert str(enq_tenant_id) == str(tenant_id)


async def test_confirm_aprobado_sin_paso_pendiente_devuelve_409(
    client, fake_session: FakeSession, fake_enqueue_calls: list[tuple]
) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    mission_id = uuid.uuid4()
    fake_session.seed_mission(
        mission_id=mission_id, tenant_id=tenant_id, user_id=user_id, status="waiting_confirmation"
    )
    # sin ningún paso "waiting_confirmation" real: inconsistencia defensiva.
    fake_session.seed_step(mission_id=mission_id, tenant_id=tenant_id, seq=1, status="done")
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key=PLAN_WITH_MISSIONS)

    response = await client.post(
        f"/v1/missions/{mission_id}/confirm", json={"approved": True}, headers=headers
    )

    assert response.status_code == 409
    assert fake_enqueue_calls == []


# ---------------------------------------------------------------------------
# POST /v1/missions/{id}/cancel
# ---------------------------------------------------------------------------


async def test_cancel_404_si_no_existe(client) -> None:
    headers = auth_headers(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key=PLAN_WITH_MISSIONS
    )
    response = await client.post(f"/v1/missions/{uuid.uuid4()}/cancel", headers=headers)
    assert response.status_code == 404


async def test_cancel_exitoso(client, fake_session: FakeSession) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    mission_id = uuid.uuid4()
    fake_session.seed_mission(
        mission_id=mission_id, tenant_id=tenant_id, user_id=user_id, status="running"
    )
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key=PLAN_WITH_MISSIONS)

    response = await client.post(f"/v1/missions/{mission_id}/cancel", headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert fake_session.missions[str(mission_id)]["status"] == "cancelled"


@pytest.mark.parametrize("status_terminal", ["done", "error", "cancelled"])
async def test_cancel_409_si_ya_termino(
    client, fake_session: FakeSession, status_terminal: str
) -> None:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    mission_id = uuid.uuid4()
    fake_session.seed_mission(
        mission_id=mission_id, tenant_id=tenant_id, user_id=user_id, status=status_terminal
    )
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key=PLAN_WITH_MISSIONS)

    response = await client.post(f"/v1/missions/{mission_id}/cancel", headers=headers)

    assert response.status_code == 409
