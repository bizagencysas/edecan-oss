"""Cable 5 del cableado del IDE: el sub-agente REFUTADOR conectado a un plan
real (``ide_sessions.SessionManager._ejecutar_refutador``, invocado desde
``_run_plan_execution`` después de que el reparto termina).

Mismo patrón que ``test_ide_sessions_plan.py``: ``WorkersIDEAgent.run`` queda
parcheado, así que estas pruebas fijan cómo reacciona ``SessionManager`` a lo
que el refutador reporta -- no dependen de ningún proveedor real de Workers
AI ni de la lógica interna de ``run()`` (eso ya lo fijan
``test_ide_workers_agent.py`` y ``test_ide_refutador.py``).

Lo que se fija aquí, medible:
- el refutador SOLO corre cuando el plan modificó archivos de verdad y tuvo
  al menos un paso completado -- y siempre deja dicho por qué corrió o no;
- un veredicto REFUTADO tumba el cierre del plan aunque todos sus pasos
  hayan dicho "completada";
- un veredicto APROBADO sin ninguna herramienta de evidencia se degrada a
  NO_DEMOSTRADO y NO tumba el plan;
- el refutador nunca ve el razonamiento intermedio del reparador, solo el
  encargo original y su resultado final;
- ``IDE_REFUTADOR_HABILITADO=0`` lo apaga sin importar qué tan grande fue
  el plan.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from edecan_companion import ide_refutador
from edecan_companion.ide_sessions import SessionManager
from edecan_companion.ide_workspaces import WorkspaceStore


def _make_manager(state_dir: Path) -> SessionManager:
    workspaces = WorkspaceStore(state_dir)
    return SessionManager(state_dir, workspaces)


def _authorize(manager: SessionManager, project: Path) -> dict[str, Any]:
    return manager.workspaces.authorize(str(project))


async def _wait_until_not_running(manager: SessionManager, session_id: str, *, attempts=200):
    import asyncio

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


def _es_turno_del_refutador(kwargs: dict[str, Any]) -> bool:
    return kwargs.get("model") == ide_refutador.MODELO_REFUTADOR_POR_DEFECTO


async def test_refutador_corre_audita_y_aprueba_con_evidencia(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    project = tmp_path / "proyecto"
    project.mkdir()
    manager = _make_manager(tmp_path / "ide")
    workspace = _authorize(manager, project)

    async def fake_run(_self, **kwargs):
        if kwargs.get("plan_store") is not None:
            _propone_plan(
                kwargs, "Agregar un endpoint", ["Crear apps/a.py"], [["apps/a.py"]]
            )
            return
        write_event = kwargs["write_event"]
        if _es_turno_del_refutador(kwargs):
            # Evidencia real: reporta haber usado 'leer_archivo' antes de
            # aprobar -- el gate de evidencia exige exactamente esto.
            write_event("tool", "Usando leer_archivo.")
            write_event(
                "assistant_final",
                "Leí apps/a.py y coincide con lo que pedía el encargo.\n\n"
                "VEREDICTO: APROBADO",
            )
            return
        (project / "apps").mkdir(exist_ok=True)
        kwargs["track_file"]("apps/a.py")
        (project / "apps" / "a.py").write_text("contenido real\n")
        write_event("assistant_final", "Creé apps/a.py con el endpoint.")

    monkeypatch.setattr("edecan_companion.ide_workers_agent.WorkersIDEAgent.run", fake_run)

    started = manager.start_agent(
        workspace["id"], "Agrega un endpoint nuevo", conversation_id="conv-refutador-ok"
    )
    session_id = started["session"]["id"]
    await _wait_until_not_running(manager, session_id)
    plan_id = manager.get_active_plan(session_id)["id"]

    manager.approve_plan(session_id, plan_id)
    final_state = await _wait_until_not_running(manager, session_id)

    assert final_state["session"]["status"] == "completed"
    finals = [e["text"] for e in final_state["events"] if e["type"] == "assistant_final"]
    assert any("Auditoría independiente" in text for text in finals)
    assert any(ide_refutador.MODELO_REFUTADOR_POR_DEFECTO in text for text in finals)
    statuses = [e["text"] for e in final_state["events"] if e["type"] == "status"]
    assert any("Refutador: auditando 1 archivo(s)" in text for text in statuses)


async def test_refutador_refutado_tumba_el_cierre_aunque_los_pasos_digan_completada(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    project = tmp_path / "proyecto"
    project.mkdir()
    manager = _make_manager(tmp_path / "ide")
    workspace = _authorize(manager, project)

    async def fake_run(_self, **kwargs):
        if kwargs.get("plan_store") is not None:
            _propone_plan(
                kwargs, "Agregar un endpoint", ["Crear apps/a.py"], [["apps/a.py"]]
            )
            return
        write_event = kwargs["write_event"]
        if _es_turno_del_refutador(kwargs):
            write_event("tool", "Usando leer_archivo.")
            write_event("tool", "Usando verificar.")
            write_event(
                "assistant_final",
                "Corrí verificar y falló: el endpoint que el reparador dice haber "
                "creado no existe en el archivo.\n\nVEREDICTO: REFUTADO",
            )
            return
        (project / "apps").mkdir(exist_ok=True)
        kwargs["track_file"]("apps/a.py")
        (project / "apps" / "a.py").write_text("# vacío, el endpoint no está\n")
        write_event("assistant_final", "Creé apps/a.py con el endpoint (mentira).")

    monkeypatch.setattr("edecan_companion.ide_workers_agent.WorkersIDEAgent.run", fake_run)

    started = manager.start_agent(
        workspace["id"], "Agrega un endpoint nuevo", conversation_id="conv-refutador-tumba"
    )
    session_id = started["session"]["id"]
    await _wait_until_not_running(manager, session_id)
    plan_id = manager.get_active_plan(session_id)["id"]

    manager.approve_plan(session_id, plan_id)
    final_state = await _wait_until_not_running(manager, session_id)

    # El punto entero del cable: el reparto reportó 1/1 completado, pero el
    # refutador lo tumbó -- el cierre TIENE que reflejar eso, no el resumen
    # optimista del reparto.
    assert final_state["session"]["status"] == "failed"
    finals = [e["text"] for e in final_state["events"] if e["type"] == "assistant_final"]
    assert any("1/1 paso(s) completados" in text for text in finals)
    assert any("REVISÓ EL TRABAJO Y LO TUMBÓ" in text for text in finals)
    assert any("no existe en el archivo" in text for text in finals)


async def test_refutador_aprobado_sin_evidencia_se_degrada_y_no_tumba_el_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    project = tmp_path / "proyecto"
    project.mkdir()
    manager = _make_manager(tmp_path / "ide")
    workspace = _authorize(manager, project)

    async def fake_run(_self, **kwargs):
        if kwargs.get("plan_store") is not None:
            _propone_plan(
                kwargs, "Agregar un endpoint", ["Crear apps/a.py"], [["apps/a.py"]]
            )
            return
        write_event = kwargs["write_event"]
        if _es_turno_del_refutador(kwargs):
            # Aprueba SIN haber usado ninguna herramienta -- opinó, no midió.
            write_event("assistant_final", "Se ve bien.\n\nVEREDICTO: APROBADO")
            return
        (project / "apps").mkdir(exist_ok=True)
        kwargs["track_file"]("apps/a.py")
        (project / "apps" / "a.py").write_text("contenido real\n")
        write_event("assistant_final", "Creé apps/a.py con el endpoint.")

    monkeypatch.setattr("edecan_companion.ide_workers_agent.WorkersIDEAgent.run", fake_run)

    started = manager.start_agent(
        workspace["id"], "Agrega un endpoint nuevo", conversation_id="conv-refutador-degrada"
    )
    session_id = started["session"]["id"]
    await _wait_until_not_running(manager, session_id)
    plan_id = manager.get_active_plan(session_id)["id"]

    manager.approve_plan(session_id, plan_id)
    final_state = await _wait_until_not_running(manager, session_id)

    # Un "aprobado" sin evidencia no cuenta como refutación -- el plan sigue
    # completo -- pero SÍ queda visible que no se pudo confirmar.
    assert final_state["session"]["status"] == "completed"
    finals = [e["text"] for e in final_state["events"] if e["type"] == "assistant_final"]
    assert any("no pudo confirmar el trabajo" in text for text in finals)
    assert any("degradado de APROBADO a NO_DEMOSTRADO" in text for text in finals)


async def test_refutador_no_corre_sin_archivos_modificados(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    project = tmp_path / "proyecto"
    project.mkdir()
    manager = _make_manager(tmp_path / "ide")
    workspace = _authorize(manager, project)

    refutador_invocado = {"si": False}

    async def fake_run(_self, **kwargs):
        if kwargs.get("plan_store") is not None:
            _propone_plan(
                kwargs, "Solo explicar algo", ["Explicar apps/a.py"], [None]
            )
            return
        if _es_turno_del_refutador(kwargs):
            refutador_invocado["si"] = True
            kwargs["write_event"]("assistant_final", "VEREDICTO: APROBADO")
            return
        # Este paso NUNCA toca disco ni llama a 'track_file' -- nada que auditar.
        kwargs["write_event"]("assistant_final", "Ya expliqué apps/a.py, no toqué nada.")

    monkeypatch.setattr("edecan_companion.ide_workers_agent.WorkersIDEAgent.run", fake_run)

    started = manager.start_agent(
        workspace["id"], "Explica qué hace apps/a.py", conversation_id="conv-refutador-sin-archivos"
    )
    session_id = started["session"]["id"]
    await _wait_until_not_running(manager, session_id)
    plan_id = manager.get_active_plan(session_id)["id"]

    manager.approve_plan(session_id, plan_id)
    final_state = await _wait_until_not_running(manager, session_id)

    assert final_state["session"]["status"] == "completed"
    assert refutador_invocado["si"] is False
    statuses = [e["text"] for e in final_state["events"] if e["type"] == "status"]
    assert any(
        "Refutador: no corrió (el plan no modificó ningún archivo)" in text for text in statuses
    )


async def test_refutador_desactivado_por_env_no_corre_pese_a_archivos_modificados(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(ide_refutador.REFUTADOR_HABILITADO_ENV, "0")
    project = tmp_path / "proyecto"
    project.mkdir()
    manager = _make_manager(tmp_path / "ide")
    workspace = _authorize(manager, project)

    refutador_invocado = {"si": False}

    async def fake_run(_self, **kwargs):
        if kwargs.get("plan_store") is not None:
            _propone_plan(kwargs, "Agregar un endpoint", ["Crear apps/a.py"], [["apps/a.py"]])
            return
        if _es_turno_del_refutador(kwargs):
            refutador_invocado["si"] = True
            kwargs["write_event"]("assistant_final", "VEREDICTO: APROBADO")
            return
        (project / "apps").mkdir(exist_ok=True)
        kwargs["track_file"]("apps/a.py")
        (project / "apps" / "a.py").write_text("contenido\n")
        kwargs["write_event"]("assistant_final", "Creé apps/a.py.")

    monkeypatch.setattr("edecan_companion.ide_workers_agent.WorkersIDEAgent.run", fake_run)

    started = manager.start_agent(
        workspace["id"], "Agrega un endpoint nuevo", conversation_id="conv-refutador-off"
    )
    session_id = started["session"]["id"]
    await _wait_until_not_running(manager, session_id)
    plan_id = manager.get_active_plan(session_id)["id"]

    manager.approve_plan(session_id, plan_id)
    final_state = await _wait_until_not_running(manager, session_id)

    assert final_state["session"]["status"] == "completed"
    assert refutador_invocado["si"] is False
    statuses = [e["text"] for e in final_state["events"] if e["type"] == "status"]
    assert any("Refutador: no corrió (desactivado" in text for text in statuses)


async def test_refutador_nunca_recibe_el_razonamiento_intermedio_del_reparador(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Garantía central del encargo: el refutador recibe el encargo original
    y el resultado -- NUNCA el razonamiento del reparador. Se fija
    inyectando un texto de "pensamiento interno" que el reparador emite como
    progreso (nunca como su respuesta final) y confirmando que el prompt del
    refutador no lo contiene."""
    project = tmp_path / "proyecto"
    project.mkdir()
    manager = _make_manager(tmp_path / "ide")
    workspace = _authorize(manager, project)

    prompt_del_refutador: dict[str, str] = {}
    RAZONAMIENTO_SECRETO = "SECRETO-DE-RAZONAMIENTO-QUE-NUNCA-DEBE-VIAJAR-XYZ"

    async def fake_run(_self, **kwargs):
        if kwargs.get("plan_store") is not None:
            _propone_plan(kwargs, "Agregar un endpoint", ["Crear apps/a.py"], [["apps/a.py"]])
            return
        write_event = kwargs["write_event"]
        if _es_turno_del_refutador(kwargs):
            prompt_del_refutador["texto"] = kwargs["prompt"]
            write_event("tool", "Usando leer_archivo.")
            write_event("assistant_final", "Confirmado.\n\nVEREDICTO: APROBADO")
            return
        # Progreso interno del reparador -- NUNCA debe llegar al refutador.
        write_event("progress", RAZONAMIENTO_SECRETO)
        (project / "apps").mkdir(exist_ok=True)
        kwargs["track_file"]("apps/a.py")
        (project / "apps" / "a.py").write_text("contenido\n")
        write_event("assistant_final", "Creé apps/a.py con el endpoint.")

    monkeypatch.setattr("edecan_companion.ide_workers_agent.WorkersIDEAgent.run", fake_run)

    started = manager.start_agent(
        workspace["id"], "Agrega un endpoint nuevo", conversation_id="conv-refutador-aislado"
    )
    session_id = started["session"]["id"]
    await _wait_until_not_running(manager, session_id)
    plan_id = manager.get_active_plan(session_id)["id"]

    manager.approve_plan(session_id, plan_id)
    await _wait_until_not_running(manager, session_id)

    assert "texto" in prompt_del_refutador, "el refutador nunca llegó a correr"
    assert RAZONAMIENTO_SECRETO not in prompt_del_refutador["texto"]
    # Sí debe llegarle el encargo original y el resultado reportado.
    assert "Crear apps/a.py" in prompt_del_refutador["texto"]
    assert "Creé apps/a.py con el endpoint." in prompt_del_refutador["texto"]


async def test_refutador_roto_no_tumba_el_cierre_del_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Riesgo 2 del cableado: un ``arreglar``/refutador que revienta no puede
    matar el turno completo -- el plan cierra con lo que sí logró, y el
    fallo del refutador queda dicho, no silenciado."""
    project = tmp_path / "proyecto"
    project.mkdir()
    manager = _make_manager(tmp_path / "ide")
    workspace = _authorize(manager, project)

    async def fake_run(_self, **kwargs):
        if kwargs.get("plan_store") is not None:
            _propone_plan(kwargs, "Agregar un endpoint", ["Crear apps/a.py"], [["apps/a.py"]])
            return
        if _es_turno_del_refutador(kwargs):
            raise RuntimeError("el refutador se cayó (simulado)")
        (project / "apps").mkdir(exist_ok=True)
        kwargs["track_file"]("apps/a.py")
        (project / "apps" / "a.py").write_text("contenido\n")
        kwargs["write_event"]("assistant_final", "Creé apps/a.py.")

    monkeypatch.setattr("edecan_companion.ide_workers_agent.WorkersIDEAgent.run", fake_run)

    started = manager.start_agent(
        workspace["id"], "Agrega un endpoint nuevo", conversation_id="conv-refutador-roto"
    )
    session_id = started["session"]["id"]
    await _wait_until_not_running(manager, session_id)
    plan_id = manager.get_active_plan(session_id)["id"]

    manager.approve_plan(session_id, plan_id)
    final_state = await _wait_until_not_running(manager, session_id)

    # El plan en sí completó (1/1) pese a que el refutador reventó -- el
    # cierre del turno no puede depender de que la auditoría no falle.
    assert final_state["session"]["status"] == "completed"
    statuses = [e["text"] for e in final_state["events"] if e["type"] == "status"]
    assert any("Refutador: se cortó por un error interno" in text for text in statuses)
