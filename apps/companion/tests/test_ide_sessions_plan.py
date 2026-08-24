"""Cabo 1 del encargo de integración: plan previo + reparto entre
sub-agentes conectados al ciclo real del agente (``ide_sessions.SessionManager``).

Estas pruebas fijan el comportamiento medible que pedía el encargo:
- una tarea no trivial deja el turno en ``plan_pending`` (nunca ``failed``)
  hasta que la persona apruebe/edite/rechace -- nunca toca archivos antes;
- al aprobar, los pasos se ejecutan de verdad vía ``ide_reparto``/
  ``ide_equipo`` y el avance por paso queda visible en los eventos;
- un paso que pide una herramienta peligrosa NUNCA la ejecuta sin
  confirmación humana -- ni siquiera dentro del reparto -- y solo ESE paso
  se cuenta como fallido;
- un plan que falló a medias se puede retomar sin repetir los pasos que ya
  terminaron.

No usa ningún proveedor real de Workers AI: ``WorkersIDEAgent.run`` queda
parcheado exactamente como en ``test_ide_sessions.py``, así que estas pruebas
no dependen de la lógica interna de la puerta de ``proponer_plan`` (eso ya
lo fija ``test_ide_workers_agent.py``) -- solo de cómo ``SessionManager``
reacciona a lo que ``run`` reporta.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from edecan_companion.ide_sessions import IDESessionError, SessionManager
from edecan_companion.ide_workspaces import WorkspaceStore


def _make_manager(state_dir: Path) -> SessionManager:
    workspaces = WorkspaceStore(state_dir)
    return SessionManager(state_dir, workspaces)


def _authorize(manager: SessionManager, project: Path) -> dict[str, Any]:
    return manager.workspaces.authorize(str(project))


async def _wait_until_not_running(manager: SessionManager, session_id: str, *, attempts=200):
    for _ in range(attempts):
        state = manager.read(session_id, "agent", 0)
        if state["session"]["status"] not in {"starting", "running"}:
            return state
        await asyncio.sleep(0.02)
    pytest.fail(f"La sesión {session_id} nunca dejó de estar 'running'.")


def _propone_plan(
    kwargs: dict[str, Any], meta: str, pasos: list[str], rutas: list[list[str] | None]
):
    plan = kwargs["plan_store"].propose(kwargs["session_id"], meta, pasos)
    kwargs["write_event"](
        "plan_proposed",
        json.dumps({"plan": plan.public(), "rutas_por_paso": rutas}, ensure_ascii=False),
    )
    return plan


async def test_tarea_no_trivial_queda_plan_pending_y_no_toca_archivos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    project = tmp_path / "proyecto"
    project.mkdir()
    manager = _make_manager(tmp_path / "ide")
    workspace = _authorize(manager, project)

    async def fake_run(_self, **kwargs):
        if kwargs.get("plan_store") is not None:
            _propone_plan(
                kwargs,
                "Agregar dos endpoints",
                ["Crear apps/a.py", "Crear apps/b.py"],
                [["apps/a.py"], ["apps/b.py"]],
            )
            return
        pytest.fail("no debía arrancar ningún paso sin aprobación")

    monkeypatch.setattr("edecan_companion.ide_workers_agent.WorkersIDEAgent.run", fake_run)

    started = manager.start_agent(
        workspace["id"], "Agrega dos endpoints nuevos", conversation_id="conv-plan"
    )
    session_id = started["session"]["id"]
    state = await _wait_until_not_running(manager, session_id)

    # Medible: nunca "failed" ni "completed" -- una pausa legítima, no un error.
    assert state["session"]["status"] == "plan_pending"
    assert not any(e["type"] == "assistant_final" for e in state["events"])
    assert not (project / "apps").exists()

    plan = manager.get_active_plan(session_id)
    assert plan is not None
    assert plan["status"] == "proposed"
    assert [s["description"] for s in plan["steps"]] == ["Crear apps/a.py", "Crear apps/b.py"]

    # Un mensaje nuevo en la misma conversación se rechaza mientras el plan
    # sigue sin resolver -- si no, el gate sería decorativo. Y se rechaza en
    # vez de encolarse (a diferencia de un turno de verdad en curso, ver
    # test_ide_dirigir.py): en "plan_pending" no hay ningún ciclo corriendo
    # que pueda leer la cola, así que encolar sería prometer una entrega que
    # no va a pasar. El error nombra lo que sí destraba la conversación.
    with pytest.raises(IDESessionError, match="plan esperando tu decisión"):
        manager.start_agent(workspace["id"], "otra cosa", conversation_id="conv-plan")
    assert not any(
        e["type"] == "user_queued" for e in manager.read(session_id, "agent", 0)["events"]
    )


async def test_plan_aprobado_ejecuta_pasos_independientes_y_completa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    project = tmp_path / "proyecto"
    project.mkdir()
    manager = _make_manager(tmp_path / "ide")
    workspace = _authorize(manager, project)

    async def fake_run(_self, **kwargs):
        if kwargs.get("plan_store") is not None:
            _propone_plan(
                kwargs,
                "Agregar dos endpoints",
                ["Crear apps/a.py", "Crear apps/b.py"],
                [["apps/a.py"], ["apps/b.py"]],
            )
            return
        prompt = kwargs["prompt"]
        write_event = kwargs["write_event"]
        if "apps/a.py" in prompt:
            write_event("assistant_final", "Paso A listo")
        elif "apps/b.py" in prompt:
            write_event("assistant_final", "Paso B listo")
        else:
            pytest.fail(f"prompt de paso inesperado: {prompt}")

    monkeypatch.setattr("edecan_companion.ide_workers_agent.WorkersIDEAgent.run", fake_run)

    started = manager.start_agent(
        workspace["id"], "Agrega dos endpoints nuevos", conversation_id="conv-ok"
    )
    session_id = started["session"]["id"]
    await _wait_until_not_running(manager, session_id)
    plan_id = manager.get_active_plan(session_id)["id"]

    manager.approve_plan(session_id, plan_id)
    final_state = await _wait_until_not_running(manager, session_id)

    assert final_state["session"]["status"] == "completed"
    finals = [e["text"] for e in final_state["events"] if e["type"] == "assistant_final"]
    assert any("2/2 paso(s) completados" in text for text in finals)
    # Visibilidad por paso (punto 3 del encargo): cada paso deja su propio
    # rastro de inicio/fin, no solo el resumen agregado al final.
    plan_steps_events = [e["text"] for e in final_state["events"] if e["type"] == "plan_step"]
    assert any("iniciando" in text for text in plan_steps_events)
    assert any("completada" in text for text in plan_steps_events)
    assert manager.plans.get(plan_id).status == "completed"


async def test_paso_peligroso_no_se_ejecuta_sin_confirmacion_y_solo_ese_paso_falla(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Fija la garantía dura del encargo: un sub-agente NUNCA puede saltarse
    el gate de confirmación humana que el agente principal sí respeta. Si el
    reparto abriera esa puerta, este paso se ejecutaría igual -- lo que se
    fija aquí es que en vez de eso queda como "fallido" sin haber corrido
    nada peligroso."""
    project = tmp_path / "proyecto"
    project.mkdir()
    manager = _make_manager(tmp_path / "ide")
    workspace = _authorize(manager, project)

    ejecuto_algo_peligroso = {"paso_b": False}

    async def fake_run(_self, **kwargs):
        if kwargs.get("plan_store") is not None:
            _propone_plan(
                kwargs,
                "Agregar dos endpoints",
                ["Crear apps/a.py", "Tocar algo riesgoso en apps/b.py"],
                [["apps/a.py"], ["apps/b.py"]],
            )
            return
        prompt = kwargs["prompt"]
        write_event = kwargs["write_event"]
        if "apps/a.py" in prompt:
            write_event("assistant_final", "Paso A listo")
            return
        # Paso B: simula que el turno pidió una tool peligrosa -- ``run()``
        # real pausaría exactamente así (evento y retorno limpio, sin
        # excepción, ver ``DANGEROUS_TOOL_NAMES`` en ide_workers_agent.py).
        write_event(
            "confirmation_required",
            json.dumps({"tool_call_id": "x", "name": "ejecutar_pentestgpt_autorizado", "args": {}}),
        )
        ejecuto_algo_peligroso["paso_b"] = True

    monkeypatch.setattr("edecan_companion.ide_workers_agent.WorkersIDEAgent.run", fake_run)

    started = manager.start_agent(
        workspace["id"], "Agrega dos endpoints nuevos", conversation_id="conv-danger"
    )
    session_id = started["session"]["id"]
    await _wait_until_not_running(manager, session_id)
    plan_id = manager.get_active_plan(session_id)["id"]

    manager.approve_plan(session_id, plan_id)
    final_state = await _wait_until_not_running(manager, session_id)

    # El paso SÍ corrió su propio turno (por eso la variable se marcó), pero
    # nunca ejecutó la tool peligrosa de verdad -- eso lo garantiza
    # ``WorkersIDEAgent.run`` (fuera del alcance de este archivo); lo que
    # importa aquí es que el reparto NO lo trata como éxito.
    assert ejecuto_algo_peligroso["paso_b"] is True
    assert final_state["session"]["status"] == "failed"
    finals = [e["text"] for e in final_state["events"] if e["type"] == "assistant_final"]
    assert any("1/2 paso(s) completados" in text and "1 fallido" in text for text in finals)


async def test_resume_plan_no_repite_el_paso_que_ya_completo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    project = tmp_path / "proyecto"
    project.mkdir()
    manager = _make_manager(tmp_path / "ide")
    workspace = _authorize(manager, project)

    llamadas_por_paso: dict[str, int] = {"a": 0, "b": 0}
    falla_b_la_primera_vez = {"activo": True}

    async def fake_run(_self, **kwargs):
        if kwargs.get("plan_store") is not None:
            _propone_plan(
                kwargs,
                "Agregar dos endpoints",
                ["Crear apps/a.py", "Crear apps/b.py"],
                [["apps/a.py"], ["apps/b.py"]],
            )
            return
        prompt = kwargs["prompt"]
        write_event = kwargs["write_event"]
        if "apps/a.py" in prompt:
            llamadas_por_paso["a"] += 1
            write_event("assistant_final", "Paso A listo")
            return
        llamadas_por_paso["b"] += 1
        if falla_b_la_primera_vez["activo"]:
            raise RuntimeError("el paso B reventó la primera vez")
        write_event("assistant_final", "Paso B listo al reintentar")

    monkeypatch.setattr("edecan_companion.ide_workers_agent.WorkersIDEAgent.run", fake_run)

    started = manager.start_agent(
        workspace["id"], "Agrega dos endpoints nuevos", conversation_id="conv-resume"
    )
    session_id = started["session"]["id"]
    await _wait_until_not_running(manager, session_id)
    plan_id = manager.get_active_plan(session_id)["id"]

    manager.approve_plan(session_id, plan_id)
    first_run = await _wait_until_not_running(manager, session_id)
    assert first_run["session"]["status"] == "failed"
    assert llamadas_por_paso == {"a": 1, "b": 1}

    falla_b_la_primera_vez["activo"] = False
    manager.resume_plan(session_id)
    second_run = await _wait_until_not_running(manager, session_id)

    # Medible: el paso A NO se repitió (sigue en 1 llamada); solo B corrió
    # de nuevo.
    assert llamadas_por_paso == {"a": 1, "b": 2}
    assert second_run["session"]["status"] == "completed"
    finals = [e["text"] for e in second_run["events"] if e["type"] == "assistant_final"]
    assert any("2/2 paso(s) completados" in text for text in finals)


async def test_reject_plan_libera_la_sesion_para_el_siguiente_mensaje(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    project = tmp_path / "proyecto"
    project.mkdir()
    manager = _make_manager(tmp_path / "ide")
    workspace = _authorize(manager, project)

    async def fake_run(_self, **kwargs):
        # Solo el PRIMER mensaje propone un plan; el segundo ("algo más
        # simple") es la clase de tarea trivial que no debe estorbar. Se
        # distingue por la solicitud ACTUAL, no por si "Migra" aparece en
        # alguna parte del prompt -- el segundo turno reinyecta el historial
        # del primero (ver ``_continuation_prompt``), así que ese texto
        # también aparecería ahí sin ser la petición vigente.
        if kwargs.get("plan_store") is not None and "algo más simple" not in kwargs["prompt"]:
            _propone_plan(
                kwargs,
                "Migrar el esquema de auth",
                ["paso 1", "paso 2", "paso 3"],
                [None, None, None],
            )
            return
        kwargs["write_event"]("assistant_final", "listo tras rechazar y reintentar")

    monkeypatch.setattr("edecan_companion.ide_workers_agent.WorkersIDEAgent.run", fake_run)

    started = manager.start_agent(
        workspace["id"], "Migra el esquema de autenticación", conversation_id="conv-reject"
    )
    session_id = started["session"]["id"]
    await _wait_until_not_running(manager, session_id)
    plan = manager.get_active_plan(session_id)
    assert plan is not None

    manager.reject_plan(session_id, plan["id"], reason="mejor no todavía")
    assert manager.read(session_id, "agent", 0)["session"]["status"] == "cancelled"
    assert manager.get_active_plan(session_id) is None

    # La sesión sigue viva: un mensaje nuevo la reusa en vez de chocar.
    second = manager.start_agent(workspace["id"], "algo más simple", conversation_id="conv-reject")
    assert second["session"]["id"] == session_id
    final_state = await _wait_until_not_running(manager, session_id)
    assert final_state["session"]["status"] == "completed"
