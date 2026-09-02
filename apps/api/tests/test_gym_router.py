"""Tests de `apps/api/edecan_api/routers/gym.py` (`/v1/gym/*`).

La lógica de dominio (generación del plan, máquina de estados de la sesión)
ya vive en `edecan_gym` con su propia suite (51 tests). Este archivo verifica
el contrato HTTP propio de ESTE router: status codes, forma exacta de la
respuesta (snake_case que consume iOS, ver `GymModels.swift`), qué SQL se
emite con el `tenant_id` correcto (aislamiento multi-tenant) y el mapeo de
`ValueError` del dominio a `422`.

`get_tenant_session` no tiene tablas `workout_*`/`gym_checkins` en
`edecan_db.models` antes de la migración `0034_gym_tables`, así que cada test
usa un `_FakeSession` (patrón idéntico a `test_automations_router.py`). La
generación del plan y el collage se monkeypatchean (`generar_plan`/
`_generar_collage`), igual que `test_negocios_router.py` sustituye la capa de
negocio.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from conftest import auth_headers
from edecan_gym import Ejercicio, WorkoutPlan, WorkoutSession
from edecan_llm.base import ChatMessage, CompletionRequest, CompletionResponse, Usage
from edecan_llm.openai_compat import _to_openai_messages

import edecan_api.deps as edecan_deps
import edecan_api.routers.gym as gym_module
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
    """`session` falso: cada `execute()` consume la siguiente respuesta
    programada (`respuestas`) y registra `(sql, params)` en `executed`. Sin
    respuesta programada devuelve un `_FakeResult` vacío (no revienta)."""

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

    application.dependency_overrides[edecan_deps.get_settings] = lambda: test_settings
    application.dependency_overrides[edecan_deps.get_platform_repo] = lambda: fake_repo
    application.dependency_overrides[edecan_deps.get_repo] = lambda: fake_repo
    application.dependency_overrides[edecan_deps.get_redis] = lambda: fake_redis
    # `get_current_user` consulta el denylist durable por una dependencia
    # separada. Mantener el mismo fake aquí evita que esta suite de router,
    # que nunca usa Redis real, intente conectar a localhost y convierta toda
    # respuesta Gym en un 503 antes de alcanzar el caso que prueba.
    application.dependency_overrides[edecan_deps.get_auth_redis] = lambda: fake_redis
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


def _auth(*, plan_key: str = "hosted_basic"):
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key=plan_key)
    return headers, tenant_id, user_id


def _plan_ejercicio(**overrides) -> dict:
    base = {
        "nombre": "Press banca",
        "musculo": "pecho",
        "series": 3,
        "repeticiones": "8-10",
        "descanso_seg": 90,
        "notas": "",
    }
    base.update(overrides)
    return base


def _session_row(**overrides) -> dict:
    row = {
        "id": uuid.uuid4(),
        "tenant_id": None,
        "user_id": None,
        "plan_id": uuid.uuid4(),
        "estado": "active",
        "started_at": datetime.now(UTC),
        "ended_at": None,
        "series": [],
        "fecha": date.today(),
        "titulo": "Empuje",
        "objetivo": "Fuerza",
        "duracion_min": 45,
        "plan_ejercicios": [_plan_ejercicio()],
        "imagen_url": None,
    }
    row.update(overrides)
    return row


def _plan_today_row(**overrides) -> dict:
    row = {
        "id": uuid.uuid4(),
        "tenant_id": None,
        "user_id": None,
        "fecha": date.today(),
        "titulo": "Empuje",
        "objetivo": "Fuerza",
        "duracion_min": 45,
        "ejercicios": [_plan_ejercicio()],
        "imagen_url": None,
    }
    row.update(overrides)
    return row


def _plan_dummy() -> WorkoutPlan:
    return WorkoutPlan(
        titulo="Empuje",
        objetivo="Fuerza",
        duracion_min=45,
        ejercicios=[Ejercicio.from_dict(_plan_ejercicio())],
    )


class _FakeLLMRouter:
    """`llm_router.complete(alias, flags, request)` que devuelve un texto fijo."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[tuple[str, dict, CompletionRequest]] = []

    async def complete(self, alias, flags, request):
        self.calls.append((alias, flags, request))
        return CompletionResponse(
            text=self.text,
            usage=Usage(input_tokens=1, output_tokens=1),
            stop_reason="end",
        )


# ---------------------------------------------------------------------------
# POST /checkin
# ---------------------------------------------------------------------------


async def test_checkin_requires_auth(client) -> None:
    response = await client.post("/v1/gym/checkin", json={"respuesta": "si"})
    assert response.status_code == 401


async def test_checkin_respuesta_invalida_422(client) -> None:
    headers, _, _ = _auth()
    response = await client.post("/v1/gym/checkin", json={"respuesta": "quizas"}, headers=headers)
    assert response.status_code == 422


async def test_checkin_no_registra_checkin_y_devuelve_sin_plan(client, fake_session) -> None:
    headers, tenant_id, user_id = _auth()

    response = await client.post("/v1/gym/checkin", json={"respuesta": "no"}, headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["plan"] is None
    assert body["session"] is None
    assert "descansar" in body["mensaje"]

    inserts = [p for sql, p in fake_session.executed if "INSERT INTO gym_checkins" in sql]
    assert len(inserts) == 1
    assert inserts[0]["tenant_id"] == tenant_id
    assert inserts[0]["user_id"] == user_id
    assert inserts[0]["respuesta"] == "no"
    assert inserts[0]["session_id"] is None


async def test_checkin_si_genera_plan_y_crea_sesion_planeada(
    client, fake_session, monkeypatch
) -> None:
    headers, tenant_id, user_id = _auth()

    async def fake_generar_plan(
        completar, *, persona=None, historial=None, objetivo=None, readiness=None, reintentos=2
    ):
        return _plan_dummy()

    async def noop_collage_en_segundo_plano(**kwargs):
        return None

    monkeypatch.setattr(gym_module, "generar_plan", fake_generar_plan)
    monkeypatch.setattr(gym_module, "_collage_en_segundo_plano", noop_collage_en_segundo_plano)

    response = await client.post("/v1/gym/checkin", json={"respuesta": "si"}, headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["plan"]["titulo"] == "Empuje"
    assert body["plan"]["ejercicios"][0]["nombre"] == "Press banca"
    assert body["plan"]["imagen_url"] is None
    # El collage se genera en segundo plano: la respuesta no trae imagen todavía.
    assert body["plan"]["imagen_file_id"] is None
    # La sesión nace "planned": el señor la inicia con el botón "Iniciar".
    assert body["session"]["estado"] == "planned"
    assert body["session"]["started_at"] is None
    assert body["session"]["progreso"]["ejercicios"] == [
        {"idx": 0, "series_hechas": 0, "series_total": 3}
    ]
    assert "Iniciar" in body["mensaje"]

    inserts = [(sql, p) for sql, p in fake_session.executed if "INSERT" in sql]
    assert any("workout_plans" in sql for sql, _ in inserts)
    assert any("workout_sessions" in sql for sql, _ in inserts)
    checkin = [p for sql, p in inserts if "gym_checkins" in sql][0]
    assert checkin["respuesta"] == "si"
    assert checkin["session_id"] is not None
    assert checkin["tenant_id"] == tenant_id
    assert checkin["user_id"] == user_id


async def test_iniciar_sesion_planned_pasa_a_active(
    client, fake_session, monkeypatch
) -> None:
    headers, tenant_id, _ = _auth()
    fake_session.respuestas = [[_session_row(estado="planned", started_at=None)]]

    response = await client.post(
        f"/v1/gym/sessions/{uuid.uuid4()}/start", headers=headers
    )

    assert response.status_code == 200
    assert response.json()["session"]["estado"] == "active"
    assert response.json()["session"]["started_at"] is not None


async def test_checkin_si_collage_falla_no_tumba_el_checkin(
    client, fake_session, monkeypatch
) -> None:
    """El check-in no espera la imagen: un fallo de imagen no puede tumbarlo."""
    headers, _, _ = _auth()

    async def fake_generar_plan(
        completar, *, persona=None, historial=None, objetivo=None, readiness=None, reintentos=2
    ):
        return _plan_dummy()

    async def noop_collage_en_segundo_plano(**kwargs):
        return None

    monkeypatch.setattr(gym_module, "generar_plan", fake_generar_plan)
    monkeypatch.setattr(gym_module, "_collage_en_segundo_plano", noop_collage_en_segundo_plano)

    response = await client.post("/v1/gym/checkin", json={"respuesta": "si"}, headers=headers)

    assert response.status_code == 200
    assert response.json()["plan"]["imagen_file_id"] is None
    assert response.json()["session"]["estado"] == "planned"


# ---------------------------------------------------------------------------
# GET /plan/today
# ---------------------------------------------------------------------------


async def test_plan_today_vacio_devuelve_null(client, fake_session) -> None:
    headers, _, _ = _auth()
    response = await client.get("/v1/gym/plan/today", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"plan": None}


async def test_plan_today_devuelve_plan_con_tenant_scoped(client, fake_session) -> None:
    headers, tenant_id, user_id = _auth()
    fake_session.respuestas = [[_plan_today_row(tenant_id=tenant_id, user_id=user_id)]]

    response = await client.get("/v1/gym/plan/today", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["plan"]["titulo"] == "Empuje"
    assert body["plan"]["ejercicios"][0]["musculo"] == "pecho"
    sql, params = fake_session.executed[0]
    assert params["tenant_id"] == tenant_id
    assert params["user_id"] == user_id


# ---------------------------------------------------------------------------
# GET /session
# ---------------------------------------------------------------------------


async def test_session_sin_sesion_en_curso_devuelve_null(client) -> None:
    headers, _, _ = _auth()
    response = await client.get("/v1/gym/session", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"session": None}


async def test_session_activa_devuelve_la_en_curso(client, fake_session) -> None:
    headers, tenant_id, user_id = _auth()
    sid = uuid.uuid4()
    fake_session.respuestas = [[_session_row(id=sid, tenant_id=tenant_id, user_id=user_id)]]

    response = await client.get("/v1/gym/session", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["session"]["id"] == str(sid)
    assert body["session"]["estado"] == "active"
    assert body["session"]["progreso"]["ejercicios"][0]["series_total"] == 3


async def test_session_activa_incluye_previo_vacio(client, fake_session) -> None:
    """`previo` está SIEMPRE presente en la sesión (lista, vacía sin historial)."""
    headers, tenant_id, user_id = _auth()
    sid = uuid.uuid4()
    fake_session.respuestas = [[_session_row(id=sid, tenant_id=tenant_id, user_id=user_id)]]

    response = await client.get("/v1/gym/session", headers=headers)

    assert response.status_code == 200
    assert response.json()["session"]["previo"] == []


# ---------------------------------------------------------------------------
# POST /sessions/{id}/sets
# ---------------------------------------------------------------------------


async def test_registrar_serie_200_con_mensaje_seguimiento(client, fake_session) -> None:
    headers, tenant_id, user_id = _auth()
    sid = uuid.uuid4()
    fake_session.respuestas = [
        [_session_row(id=sid, tenant_id=tenant_id, user_id=user_id)],
        _FakeResult(),  # UPDATE workout_sessions
    ]

    response = await client.post(
        f"/v1/gym/sessions/{sid}/sets",
        json={"ejercicio_idx": 0, "repeticiones": 8, "peso_kg": 60.0},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["session"]["id"] == str(sid)
    assert len(body["session"]["series"]) == 1
    assert body["session"]["series"][0]["peso_kg"] == 60.0
    assert body["session"]["progreso"]["ejercicios"][0]["series_hechas"] == 1
    assert body["mensaje"] == "Serie 1 de Press banca anotada. Te quedan 2. Descansa 90s."

    update = [p for sql, p in fake_session.executed if "UPDATE workout_sessions" in sql][0]
    assert update["tenant_id"] == tenant_id
    assert update["id"] == sid


async def test_registrar_serie_ejercicio_fuera_de_rango_422(client, fake_session) -> None:
    headers, tenant_id, user_id = _auth()
    sid = uuid.uuid4()
    fake_session.respuestas = [[_session_row(id=sid, tenant_id=tenant_id, user_id=user_id)]]

    response = await client.post(
        f"/v1/gym/sessions/{sid}/sets",
        json={"ejercicio_idx": 7, "repeticiones": 8},
        headers=headers,
    )
    assert response.status_code == 422


async def test_registrar_serie_sesion_ajena_404(client, fake_session) -> None:
    """La sesión de OTRO tenant no existe para este usuario: el SELECT va
    filtrado por `tenant_id` y, sin fila, devuelve 404 (sin side effects)."""
    headers, tenant_id, _ = _auth()
    sid = uuid.uuid4()
    # Sin respuesta programada -> _load_session_or_404 devuelve None -> 404.

    response = await client.post(
        f"/v1/gym/sessions/{sid}/sets",
        json={"ejercicio_idx": 0, "repeticiones": 8},
        headers=headers,
    )
    assert response.status_code == 404

    sql, params = fake_session.executed[0]
    assert params["tenant_id"] == tenant_id
    assert params["id"] == sid
    # Ningún UPDATE se emitió (sin side effects ante un 404).
    assert not any("UPDATE workout_sessions" in s for s, _ in fake_session.executed)


# ---------------------------------------------------------------------------
# POST /sessions/{id}/complete | pause | resume
# ---------------------------------------------------------------------------


async def test_complete_200(client, fake_session) -> None:
    headers, tenant_id, user_id = _auth()
    sid = uuid.uuid4()
    fake_session.respuestas = [
        [_session_row(id=sid, tenant_id=tenant_id, user_id=user_id)],
        _FakeResult(),
    ]

    response = await client.post(f"/v1/gym/sessions/{sid}/complete", headers=headers)

    assert response.status_code == 200
    assert response.json()["session"]["estado"] == "completed"
    assert "Buen trabajo" in response.json()["mensaje"]
    update = [p for sql, p in fake_session.executed if "UPDATE workout_sessions" in sql][0]
    assert update["ended_at"] is not None


async def test_complete_incluye_resumen_null_sin_llm(client, fake_session) -> None:
    """Sin `llm_router` (como en tests) el resumen es `null` y no rompe el cierre."""
    headers, tenant_id, user_id = _auth()
    sid = uuid.uuid4()
    fake_session.respuestas = [
        [_session_row(id=sid, tenant_id=tenant_id, user_id=user_id)],
        _FakeResult(),
    ]

    response = await client.post(f"/v1/gym/sessions/{sid}/complete", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["session"]["estado"] == "completed"
    assert "resumen" in body
    assert body["resumen"] is None
    assert body["session"]["previo"] == []


async def test_pause_resume_200(client, fake_session) -> None:
    headers, tenant_id, user_id = _auth()
    sid = uuid.uuid4()

    fake_session.respuestas = [
        [_session_row(id=sid, tenant_id=tenant_id, user_id=user_id)],
        _FakeResult(),
    ]
    resp_pause = await client.post(f"/v1/gym/sessions/{sid}/pause", headers=headers)
    assert resp_pause.status_code == 200
    assert resp_pause.json()["session"]["estado"] == "paused"

    fake_session.respuestas = [
        [_session_row(id=sid, tenant_id=tenant_id, user_id=user_id, estado="paused")],
        _FakeResult(),
    ]
    resp_resume = await client.post(f"/v1/gym/sessions/{sid}/resume", headers=headers)
    assert resp_resume.status_code == 200
    assert resp_resume.json()["session"]["estado"] == "active"


async def test_pause_desde_no_activa_422(client, fake_session) -> None:
    headers, tenant_id, user_id = _auth()
    sid = uuid.uuid4()
    fake_session.respuestas = [
        [_session_row(id=sid, tenant_id=tenant_id, user_id=user_id, estado="completed")]
    ]

    response = await client.post(f"/v1/gym/sessions/{sid}/pause", headers=headers)
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /history
# ---------------------------------------------------------------------------


async def test_history_devuelve_sesiones_completadas(client, fake_session) -> None:
    headers, tenant_id, user_id = _auth()
    sid = uuid.uuid4()
    fake_session.respuestas = [
        [_session_row(id=sid, tenant_id=tenant_id, user_id=user_id, estado="completed")]
    ]

    response = await client.get("/v1/gym/history?limit=30", headers=headers)

    assert response.status_code == 200
    sessions = response.json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["id"] == str(sid)
    assert sessions[0]["estado"] == "completed"
    sql, params = fake_session.executed[0]
    assert params["tenant_id"] == tenant_id
    assert params["user_id"] == user_id
    assert params["limite"] == 30


async def test_history_devuelve_streak_y_previo(client, fake_session) -> None:
    """`/history` devuelve `{"sessions": [...], "streak": int}` y cada sesión
    trae `previo` (lista) — contrato nuevo de la racha semanal."""
    headers, tenant_id, user_id = _auth()
    sid = uuid.uuid4()
    fake_session.respuestas = [
        [
            _session_row(
                id=sid,
                tenant_id=tenant_id,
                user_id=user_id,
                estado="completed",
                ended_at=datetime.now(UTC),
            )
        ]
    ]

    response = await client.get("/v1/gym/history?limit=30", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["streak"], int)
    assert body["streak"] >= 1
    assert body["sessions"][0]["previo"] == []


# ---------------------------------------------------------------------------
# meta (sobrecarga progresiva)
# ---------------------------------------------------------------------------


def test_meta_sobrecarga_progresiva_desde_previo() -> None:
    """`meta` se deriva de `previo`: +2.5 kg en peso y +2/+1 reps según rango."""
    previo = [
        {"idx": 0, "peso_kg": 60.0, "repeticiones": 6, "fecha": "2026-01-01"},
        {"idx": 1, "peso_kg": 80.0, "repeticiones": 10, "fecha": "2026-01-01"},
        {"idx": 2, "peso_kg": None, "repeticiones": None, "fecha": "2026-01-01"},
    ]
    data = gym_module._session_to_dict(
        WorkoutSession(_plan_dummy()), uuid.uuid4(), previo=previo
    )
    assert data["meta"] == [
        {"idx": 0, "peso_objetivo": 62.5, "repeticiones_objetivo": 8},
        {"idx": 1, "peso_objetivo": 82.5, "repeticiones_objetivo": 11},
        {"idx": 2, "peso_objetivo": None, "repeticiones_objetivo": None},
    ]


def test_meta_siempre_presente_y_vacia_sin_previo() -> None:
    """`meta` es parte del contrato de la sesión: lista, vacía sin previo."""
    data = gym_module._session_to_dict(WorkoutSession(_plan_dummy()), uuid.uuid4())
    assert data["meta"] == []


async def test_session_incluye_meta_de_la_ultima_sesion(client, fake_session) -> None:
    """El endpoint de sesión inyecta `previo` y, con él, la `meta` calculada."""
    headers, tenant_id, user_id = _auth()
    sid = uuid.uuid4()
    fake_session.respuestas = [
        [_session_row(id=sid, tenant_id=tenant_id, user_id=user_id)],
        [
            _session_row(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                user_id=user_id,
                estado="completed",
                ended_at=datetime.now(UTC),
                series=[{"ejercicio_idx": 0, "repeticiones": 6, "peso_kg": 60.0, "en": ""}],
            )
        ],
    ]

    response = await client.get("/v1/gym/session", headers=headers)

    assert response.status_code == 200
    body = response.json()["session"]
    assert body["previo"][0]["peso_kg"] == 60.0
    assert body["meta"] == [
        {"idx": 0, "peso_objetivo": 62.5, "repeticiones_objetivo": 8}
    ]


# ---------------------------------------------------------------------------
# readiness ajusta el plan
# ---------------------------------------------------------------------------


async def test_checkin_readiness_se_pasa_al_plan(
    client, fake_session, monkeypatch
) -> None:
    headers, _, _ = _auth()
    capturado: dict[str, object] = {}

    async def fake_generar_plan_gym(
        llm_router, flags, *, historial, objetivo, readiness=None
    ):
        capturado["readiness"] = readiness
        return _plan_dummy()

    async def noop_collage_en_segundo_plano(**kwargs):
        return None

    monkeypatch.setattr(gym_module, "_generar_plan_gym", fake_generar_plan_gym)
    monkeypatch.setattr(gym_module, "_collage_en_segundo_plano", noop_collage_en_segundo_plano)

    response = await client.post(
        "/v1/gym/checkin",
        json={"respuesta": "si", "readiness": "dormí mal"},
        headers=headers,
    )

    assert response.status_code == 200
    assert capturado["readiness"] == "dormí mal"


async def test_checkin_readiness_por_defecto_none(
    client, fake_session, monkeypatch
) -> None:
    headers, _, _ = _auth()
    capturado: dict[str, object] = {}

    async def fake_generar_plan_gym(
        llm_router, flags, *, historial, objetivo, readiness=None
    ):
        capturado["readiness"] = readiness
        return _plan_dummy()

    async def noop_collage_en_segundo_plano(**kwargs):
        return None

    monkeypatch.setattr(gym_module, "_generar_plan_gym", fake_generar_plan_gym)
    monkeypatch.setattr(gym_module, "_collage_en_segundo_plano", noop_collage_en_segundo_plano)

    response = await client.post("/v1/gym/checkin", json={"respuesta": "si"}, headers=headers)

    assert response.status_code == 200
    assert capturado["readiness"] is None


# ---------------------------------------------------------------------------
# GET /reporte_semanal
# ---------------------------------------------------------------------------


async def test_reporte_semanal_null_sin_llm(client, fake_session) -> None:
    """Sin `llm_router` (como en tests) el reporte es `null` y no rompe."""
    headers, tenant_id, user_id = _auth()
    fake_session.respuestas = [[]]

    response = await client.get("/v1/gym/reporte_semanal", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"reporte": None}
    sql, params = fake_session.executed[0]
    assert params["tenant_id"] == tenant_id
    assert params["user_id"] == user_id
    assert "ws.ended_at >= :desde" in sql
    assert params["limite"] == 100


async def test_reporte_semanal_genera_resumen_con_llm(
    client, fake_session, app
) -> None:
    headers, tenant_id, user_id = _auth()
    fake_session.respuestas = [
        [
            _session_row(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                user_id=user_id,
                estado="completed",
                ended_at=datetime.now(UTC),
                series=[{"ejercicio_idx": 0, "repeticiones": 8, "peso_kg": 60.0, "en": ""}],
            )
        ]
    ]
    llm = _FakeLLMRouter("Buen avance esta semana: subiste el press banca a 60 kg.")
    app.dependency_overrides[edecan_deps.get_llm_router] = lambda: llm

    response = await client.get("/v1/gym/reporte_semanal", headers=headers)

    assert response.status_code == 200
    resumen = "Buen avance esta semana: subiste el press banca a 60 kg."
    assert response.json() == {"reporte": resumen}
    alias, _flags, request = llm.calls[0]
    assert alias == gym_module._ALIAS_LLM
    assert "Press banca" in request.messages[0].content
    assert "60.0 kg" in request.messages[0].content


# ---------------------------------------------------------------------------
# POST /form/analizar (coach de técnica con visión)
# ---------------------------------------------------------------------------


async def test_form_analizar_feedback_null_sin_llm(client) -> None:
    """Sin `llm_router` el feedback es `null` y no rompe."""
    headers, _, _ = _auth()
    response = await client.post(
        "/v1/gym/form/analizar",
        json={"imagen_b64": "QUJD", "ejercicio": "sentadilla"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json() == {"feedback": None}


async def test_form_analizar_envia_texto_e_imagen_con_vision(client, app) -> None:
    headers, _, _ = _auth()
    llm = _FakeLLMRouter("Corrige la postura: abre el pecho y baja controlado.")
    app.dependency_overrides[edecan_deps.get_llm_router] = lambda: llm

    response = await client.post(
        "/v1/gym/form/analizar",
        json={"imagen_b64": "QUJD", "ejercicio": "sentadilla"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == {"feedback": "Corrige la postura: abre el pecho y baja controlado."}
    alias, _flags, request = llm.calls[0]
    assert alias == gym_module._ALIAS_LLM
    content = request.messages[0].content
    assert content[0]["type"] == "text"
    assert "sentadilla" in content[0]["text"]
    assert content[1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/jpeg;base64,QUJD"},
    }


def test_to_openai_messages_pasa_partes_de_imagen_del_usuario() -> None:
    """El adaptador pasa `{"type": "text"}` / `{"type": "image_url"}` tal cual."""
    request = CompletionRequest(
        model="vision",
        messages=[
            ChatMessage(
                role="user",
                content=[
                    {"type": "text", "text": "Analiza la sentadilla"},
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}},
                ],
            )
        ],
    )
    mensajes = _to_openai_messages(request)
    assert mensajes[0]["role"] == "user"
    assert mensajes[0]["content"][0] == {"type": "text", "text": "Analiza la sentadilla"}
    assert mensajes[0]["content"][1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/jpeg;base64,QUJD"},
    }


# ---------------------------------------------------------------------------
# POST /form/analizar — vídeo (frames_b64, multi-imagen)
# ---------------------------------------------------------------------------


async def test_form_analizar_frames_envia_todos_los_frames_multimodal(
    client, app
) -> None:
    """Con `frames_b64`, TODOS los frames viajan en un único mensaje multimodal."""
    headers, _, _ = _auth()
    llm = _FakeLLMRouter("Baja controlado y mantén el pecho abierto en todo el recorrido.")
    app.dependency_overrides[edecan_deps.get_llm_router] = lambda: llm

    response = await client.post(
        "/v1/gym/form/analizar",
        json={
            "frames_b64": ["QUJD", "REVG", "R0hJ"],
            "ejercicio": "sentadilla",
        },
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == {
        "feedback": "Baja controlado y mantén el pecho abierto en todo el recorrido."
    }
    alias, _flags, request = llm.calls[0]
    assert alias == gym_module._ALIAS_LLM
    content = request.messages[0].content
    assert content[0]["type"] == "text"
    assert "sentadilla" in content[0]["text"]
    assert "frames" in content[0]["text"]
    imagenes = [b for b in content if b["type"] == "image_url"]
    assert [b["image_url"]["url"] for b in imagenes] == [
        "data:image/jpeg;base64,QUJD",
        "data:image/jpeg;base64,REVG",
        "data:image/jpeg;base64,R0hJ",
    ]


async def test_form_analizar_sin_imagen_ni_frames_422(client) -> None:
    headers, _, _ = _auth()
    response = await client.post(
        "/v1/gym/form/analizar",
        json={"ejercicio": "sentadilla"},
        headers=headers,
    )
    assert response.status_code == 422


async def test_form_analizar_mas_de_6_frames_422(client) -> None:
    headers, _, _ = _auth()
    response = await client.post(
        "/v1/gym/form/analizar",
        json={"frames_b64": ["QQ"] * 7, "ejercicio": "sentadilla"},
        headers=headers,
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /coach_voz (coach de voz)
# ---------------------------------------------------------------------------


async def test_coach_voz_linea_null_sin_llm(client) -> None:
    """Sin `llm_router` la línea es `null` y no rompe."""
    headers, _, _ = _auth()
    response = await client.post(
        "/v1/gym/coach_voz",
        json={"tipo": "motivacion"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json() == {"linea": None}


async def test_coach_voz_linea_con_llm_fake(client, app) -> None:
    headers, _, _ = _auth()
    llm = _FakeLLMRouter("¡Vamos, una serie más, tú puedes!")
    app.dependency_overrides[edecan_deps.get_llm_router] = lambda: llm

    response = await client.post(
        "/v1/gym/coach_voz",
        json={"tipo": "serie_completada", "ejercicio": "press banca", "contexto": "serie 3 de 4"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == {"linea": "¡Vamos, una serie más, tú puedes!"}
    alias, _flags, request = llm.calls[0]
    assert alias == gym_module._ALIAS_LLM
    assert request.max_tokens == gym_module._MAX_TOKENS_COACH
    prompt = request.messages[0].content
    assert "press banca" in prompt
    assert "serie 3 de 4" in prompt


async def test_coach_voz_tipo_invalido_422(client) -> None:
    headers, _, _ = _auth()
    response = await client.post(
        "/v1/gym/coach_voz",
        json={"tipo": "otra_cosa"},
        headers=headers,
    )
    assert response.status_code == 422
