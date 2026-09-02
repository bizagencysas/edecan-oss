"""Tests del encargo a equipo: tracker (merge), carreras e idempotencia."""
from __future__ import annotations

import uuid
from typing import Any

import pytest
from edecan_worker.handlers.run_persistent_agent import _notificar_team_mission


class _Sesion:
    """Fake de sesión con cola de filas (por orden de consulta del tracker):

    1. SELECT del handoff→team_mission (fila tm)
    2. UPDATE de resultado (rowcount: pendiente=1)
    3. SELECT de conteo (fila total/fin)
    4. UPDATE CAS de status (fila id si marca; vacío si no)
    5. SELECT de nombres (lista)
    """

    def __init__(self) -> None:
        self.filas: list[Any] = []
        self.rowcounts: list[int] = []
        self.llamadas: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None):
        sql = str(stmt)
        params = dict(params or {})
        self.llamadas.append((sql, params))

        class R:
            def __init__(self, rows, count=None):
                if isinstance(rows, dict):
                    rows = [rows]
                self._rows = rows or []
                self._count = count
            def mappings(self):
                return self
            def all(self):
                return self._rows
            def first(self):
                return self._rows[0] if self._rows else None
            @property
            def rowcount(self):
                return self._count if self._count is not None else 1

        if "SELECT r.team_mission_id" in sql:
            return R(self.filas.pop(0) if self.filas else [])
        if "UPDATE team_mission_results SET estado" in sql:
            rc = self.rowcounts.pop(0) if self.rowcounts else 1
            return R(None, rc)
        if "COUNT" in sql and "FROM team_mission_results" in sql:
            return R(self.filas.pop(0) if self.filas else [])
        if "UPDATE team_missions SET status" in sql:
            return R(self.filas.pop(0) if self.filas else [])
        if "SELECT 1 FROM persistent_agents" in sql:
            return R([{"1": 1}])
        if "COALESCE(a.display_name" in sql:
            return R(self.filas.pop(0) if self.filas else [])
        return R([])


def _deps(fake_queues: list[dict[str, Any]], sesion: _Sesion) -> Any:

    class _Deps:
        settings = type("S", (), {})()

        def session_factory(self, _tenant):
            class _CM:
                async def __aenter__(self_inner):
                    return sesion

                async def __aexit__(self_inner, *a):
                    return False

            return _CM()

    return _Deps()


def _install_queue(monkeypatch: pytest.MonkeyPatch, captura: list[dict[str, Any]]) -> None:
    import sys
    import types

    async def _enqueue(_settings, job_type, payload, tenant_id=None):
        captura.append({"job_type": job_type, "payload": payload, "tenant_id": tenant_id})
        return uuid.uuid4()

    q = types.ModuleType("edecan_core.queue")
    q.enqueue = _enqueue
    core = types.ModuleType("edecan_core")
    core.queue = q
    monkeypatch.setitem(sys.modules, "edecan_core", core)
    monkeypatch.setitem(sys.modules, "edecan_core.queue", q)


def _mision_y_resultados(n_miembros: int) -> dict[str, Any]:
    mision = str(uuid.uuid4())
    tm = {
        "team_mission_id": mision,
        "agent_id": str(uuid.uuid4()),
        "coordinator_agent_id": str(uuid.uuid4()),
        "pedido": "pide una landing",
        "user_id": str(uuid.uuid4()),
        "esperados": n_miembros,
    }
    resultados: dict[str, dict[str, Any]] = {}
    for i in range(n_miembros):
        agente = str(uuid.uuid4())
        resultados.setdefault(mision, {})[agente] = "pending"
    return {"mision": mision, "tm": tm, "resultados": resultados}


@pytest.fixture
def sesion():
    return _Sesion()


async def test_sin_fila_de_equipo_es_noop(sesion, monkeypatch):
    captura: list[dict[str, Any]] = []
    _install_queue(monkeypatch, captura)
    await _notificar_team_mission(_deps(captura, sesion), tenant_id=uuid.uuid4(), handoff_id=uuid.uuid4(), estado="done", resumen="x")
    assert not any("UPDATE team_missions SET status = 'merging'" in c for c, _ in sesion.llamadas)
    assert captura == []


async def test_parcial_no_despierta_al_coordinador(sesion, monkeypatch):
    datos = _mision_y_resultados(2)
    sesion.filas = [dict(datos["tm"]), {"fin": 1}]
    sesion.rowcounts = [1]
    captura: list[dict[str, Any]] = []
    _install_queue(monkeypatch, captura)
    await _notificar_team_mission(
        _deps(captura, sesion), tenant_id=uuid.uuid4(), handoff_id=uuid.uuid4(), estado="done", resumen="r1"
    )
    assert captura == []


async def test_todos_terminados_merge_una_sola_vez(sesion, monkeypatch):
    datos = _mision_y_resultados(2)
    nombres = [
        {"nombre": "Fronti", "estado": "done", "resumen": "r1"},
        {"nombre": "Analista", "estado": "done", "resumen": "r2"},
    ]
    cas_row = [{"id": datos["mision"]}]
    sesion.filas = [dict(datos["tm"]), {"fin": 2}, cas_row, nombres]
    sesion.rowcounts = [1]
    captura: list[dict[str, Any]] = []
    _install_queue(monkeypatch, captura)
    # notificación del ÚLTIMO miembro: el pending pasa a done y el merge despierta
    await _notificar_team_mission(
        _deps(captura, sesion), tenant_id=uuid.uuid4(), handoff_id=uuid.uuid4(), estado="done", resumen="r1"
    )
    assert len(captura) == 1
    assert captura[0]["job_type"] == "run_persistent_agent"
    assert captura[0]["payload"]["source"] == "team_merge"
    # segundo notificador (carrera/mismo estado): CAS no marcado → no encola
    await _notificar_team_mission(
        _deps(captura, sesion), tenant_id=uuid.uuid4(), handoff_id=uuid.uuid4(), estado="done", resumen="r1"
    )
    assert len(captura) == 1


async def test_error_de_un_miembro_tambien_llega_al_merge(sesion, monkeypatch):
    datos = _mision_y_resultados(3)
    nombres = [
        {"nombre": "Fronti", "estado": "error", "resumen": "cayó"},
        {"nombre": "Analista", "estado": "done", "resumen": "r2"},
        {"nombre": "Backendsito", "estado": "done", "resumen": "r3"},
    ]
    sesion.filas = [dict(datos["tm"]), {"fin": 3}, [{"id": datos["mision"]}], nombres]
    sesion.rowcounts = [1]
    captura: list[dict[str, Any]] = []
    _install_queue(monkeypatch, captura)
    await _notificar_team_mission(
        _deps(captura, sesion), tenant_id=uuid.uuid4(), handoff_id=uuid.uuid4(), estado="done", resumen="r2"
    )
    assert len(captura) == 1
    assert "error" in captura[0]["payload"]["instruction"] or "sin resultado" in captura[0]["payload"]["instruction"]


async def test_idempotente_por_estado_pending(sesion, monkeypatch):
    datos = _mision_y_resultados(1)
    nombres = [{"nombre": "Fronti", "estado": "done", "resumen": "r1"}]
    sesion.filas = [dict(datos["tm"]), {"fin": 1}, [{"id": datos["mision"]}], nombres]
    sesion.rowcounts = [1]
    captura: list[dict[str, Any]] = []
    _install_queue(monkeypatch, captura)
    await _notificar_team_mission(
        _deps(captura, sesion), tenant_id=uuid.uuid4(), handoff_id=uuid.uuid4(), estado="done", resumen="r1"
    )
    assert len(captura) == 1
    # misma notificación repetida: el UPDATE devuelve rowcount 0 → nada
    sesion.filas = []
    sesion.rowcounts = [0]
    await _notificar_team_mission(
        _deps(captura, sesion), tenant_id=uuid.uuid4(), handoff_id=uuid.uuid4(), estado="done", resumen="r1"
    )
    assert len(captura) == 1
