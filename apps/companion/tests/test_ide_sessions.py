"""Continuidad de conversación en ``ide_sessions.SessionManager``.

CIMIENTO A: una sesión de agente = una conversación completa, no un mensaje
suelto. Estas pruebas fijan el comportamiento medible que pedía el arreglo:
- el segundo mensaje de la misma conversación reusa la sesión (mismo id) en
  vez de crear una nueva y volver a pegar el historial entero;
- el prompt de continuación no repite la orden de "inspecciona el workspace
  antes de actuar" (esa orden solo tiene sentido al abrir la conversación);
- una conversación distinta sigue creando su propia sesión;
- tras "reiniciar" el companion (sesión previa marcada "interrupted" por
  ``_load``), el siguiente mensaje cae al respaldo sin romperse;
- un turno que revienta después de reusar la sesión no hereda por error el
  ``assistant_final`` de un turno anterior.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest
from edecan_companion.ide_sessions import SessionManager
from edecan_companion.ide_workspaces import WorkspaceStore


def _make_manager(state_dir: Path) -> SessionManager:
    workspaces = WorkspaceStore(state_dir)
    return SessionManager(state_dir, workspaces)


def _authorize(manager: SessionManager, project: Path) -> dict[str, Any]:
    return manager.workspaces.authorize(str(project))


async def _wait_until_not_running(manager: SessionManager, session_id: str, *, attempts=100):
    for _ in range(attempts):
        state = manager.read(session_id, "agent", 0)
        if state["session"]["status"] != "running":
            return state
        await asyncio.sleep(0.02)
    pytest.fail(f"La sesión {session_id} nunca dejó de estar 'running'.")


async def test_second_message_same_conversation_reuses_the_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    project = tmp_path / "proyecto"
    project.mkdir()
    manager = _make_manager(tmp_path / "ide")
    workspace = _authorize(manager, project)

    seen_prompts: list[str] = []

    async def fake_run(_self, **kwargs):
        seen_prompts.append(kwargs["prompt"])
        kwargs["write_event"]("assistant_final", f"respuesta #{len(seen_prompts)}")

    monkeypatch.setattr(
        "edecan_companion.ide_workers_agent.WorkersIDEAgent.run",
        fake_run,
    )

    first = manager.start_agent(
        workspace["id"], "Arregla el bug de login", conversation_id="conv-1"
    )
    first_id = first["session"]["id"]
    await _wait_until_not_running(manager, first_id)

    second = manager.start_agent(
        workspace["id"], "Ahora agrega un test", conversation_id="conv-1"
    )
    second_id = second["session"]["id"]
    await _wait_until_not_running(manager, second_id)

    # Medible: mismo id de sesión, no una sesión nueva.
    assert second_id == first_id
    listed = manager.list("agent")["sessions"]
    assert len([row for row in listed if row["id"] == first_id]) == 1

    # Medible: el segundo prompt NO repite la orden de inspeccionar el
    # workspace (eso solo aplica al abrir la conversación) y sí trae el
    # contexto de lo que ya se habló, sin reescanear otras sesiones.
    assert len(seen_prompts) == 2
    assert "Inspecciona el workspace antes de actuar" not in seen_prompts[1]
    assert "<historial_de_esta_conversacion>" in seen_prompts[1]
    assert "Arregla el bug de login" in seen_prompts[1]
    assert "respuesta #1" in seen_prompts[1]
    assert "Ahora agrega un test" in seen_prompts[1]

    # Ambos turnos quedan en el mismo hilo de eventos.
    events = manager.read(first_id, "agent", 0)["events"]
    user_texts = [e["text"] for e in events if e["type"] == "user"]
    assert user_texts == ["Arregla el bug de login", "Ahora agrega un test"]


async def test_different_conversation_id_creates_a_new_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    project = tmp_path / "proyecto"
    project.mkdir()
    manager = _make_manager(tmp_path / "ide")
    workspace = _authorize(manager, project)

    async def fake_run(_self, **kwargs):
        kwargs["write_event"]("assistant_final", "listo")

    monkeypatch.setattr(
        "edecan_companion.ide_workers_agent.WorkersIDEAgent.run",
        fake_run,
    )

    first = manager.start_agent(workspace["id"], "Tarea A", conversation_id="conv-a")
    await _wait_until_not_running(manager, first["session"]["id"])

    second = manager.start_agent(workspace["id"], "Tarea B", conversation_id="conv-b")
    await _wait_until_not_running(manager, second["session"]["id"])

    assert first["session"]["id"] != second["session"]["id"]
    ids = {row["id"] for row in manager.list("agent")["sessions"]}
    assert {first["session"]["id"], second["session"]["id"]} <= ids


async def test_falls_back_to_a_new_session_after_companion_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    state_dir = tmp_path / "ide"
    project = tmp_path / "proyecto"
    project.mkdir()

    manager = _make_manager(state_dir)
    workspace = _authorize(manager, project)

    async def fake_run_hangs(_self, **kwargs):
        # Simula un turno que quedó a medias cuando "se apagó" el companion:
        # nunca llega a escribir assistant_final. El hilo real queda
        # colgado a propósito (es un daemon thread: no bloquea el proceso
        # de pruebas) para que el estado persistido en disco siga diciendo
        # "running", tal como quedaría tras un corte real del companion.
        while True:
            await asyncio.sleep(0.01)

    monkeypatch.setattr(
        "edecan_companion.ide_workers_agent.WorkersIDEAgent.run",
        fake_run_hangs,
    )
    first = manager.start_agent(workspace["id"], "Primer mensaje", conversation_id="conv-r")
    first_id = first["session"]["id"]
    assert manager.read(first_id, "agent", 0)["session"]["status"] == "running"

    # "Reinicio del companion": se crea un SessionManager nuevo sobre el
    # mismo state_dir. ``_load`` encuentra la sesión anterior en "running" y
    # la marca "interrupted" porque ningún hilo real sigue vivo para ella.
    manager2 = _make_manager(state_dir)
    reloaded = manager2.read(first_id, "agent", 0)["session"]
    assert reloaded["status"] == "interrupted"

    async def fake_run_ok(_self, **kwargs):
        kwargs["write_event"]("assistant_final", "segunda sesión")

    monkeypatch.setattr(
        "edecan_companion.ide_workers_agent.WorkersIDEAgent.run",
        fake_run_ok,
    )
    second = manager2.start_agent(
        workspace["id"], "Segundo mensaje", conversation_id="conv-r"
    )
    second_id = second["session"]["id"]
    await _wait_until_not_running(manager2, second_id)

    # Medible: no revienta, y como la sesión anterior ya no es confiable,
    # cae al respaldo -- una sesión NUEVA, distinta de la interrumpida.
    assert second_id != first_id
    assert manager2.read(second_id, "agent", 0)["session"]["status"] == "completed"


async def test_concurrent_message_while_previous_turn_is_running_is_queued(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Antes esto se rechazaba con "ya hay un turno en curso" y el mensaje se
    perdía. Ahora se acepta y se encola contra la MISMA sesión: el detalle de
    cuándo y cómo se entrega vive en ``test_ide_dirigir.py``; aquí solo se fija
    que no hay rechazo ni sesión duplicada."""
    project = tmp_path / "proyecto"
    project.mkdir()
    manager = _make_manager(tmp_path / "ide")
    workspace = _authorize(manager, project)

    # ``fake_run_slow`` corre en el event loop propio del hilo en segundo
    # plano (``_run_workers_agent`` hace su propio ``asyncio.run``), así que
    # la señal de liberación tiene que ser un primitivo de threading, no un
    # ``asyncio.Event`` atado al loop del test.
    release = threading.Event()

    async def fake_run_slow(_self, **kwargs):
        while not release.is_set():
            await asyncio.sleep(0.01)
        kwargs["write_event"]("assistant_final", "listo")

    monkeypatch.setattr(
        "edecan_companion.ide_workers_agent.WorkersIDEAgent.run",
        fake_run_slow,
    )
    first = manager.start_agent(workspace["id"], "Mensaje 1", conversation_id="conv-busy")
    first_id = first["session"]["id"]
    assert manager.read(first_id, "agent", 0)["session"]["status"] == "running"

    second = manager.start_agent(workspace["id"], "Mensaje 2", conversation_id="conv-busy")

    assert second["session"]["id"] == first_id
    assert second["queued"]["position"] == 1
    assert len(manager.list("agent")["sessions"]) == 1

    release.set()
    await _wait_until_not_running(manager, first_id)


async def test_reused_session_does_not_inherit_a_stale_assistant_final(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Fija el bug que introduciría reusar la sesión sin acotar el turno.

    ``has_assistant_final`` miraba TODOS los eventos de la sesión. Con la
    sesión reusada, el ``assistant_final`` del primer turno seguía ahí
    cuando arrancaba el segundo: un segundo turno que revienta sin decir
    nada se habría marcado como "completed" por error, ocultando el fallo.
    """
    project = tmp_path / "proyecto"
    project.mkdir()
    manager = _make_manager(tmp_path / "ide")
    workspace = _authorize(manager, project)

    async def fake_run_ok(_self, **kwargs):
        kwargs["write_event"]("assistant_final", "primera respuesta")

    monkeypatch.setattr(
        "edecan_companion.ide_workers_agent.WorkersIDEAgent.run",
        fake_run_ok,
    )
    first = manager.start_agent(workspace["id"], "Mensaje 1", conversation_id="conv-crash")
    first_id = first["session"]["id"]
    await _wait_until_not_running(manager, first_id)
    assert manager.read(first_id, "agent", 0)["session"]["status"] == "completed"

    async def fake_run_crashes(_self, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "edecan_companion.ide_workers_agent.WorkersIDEAgent.run",
        fake_run_crashes,
    )
    second = manager.start_agent(workspace["id"], "Mensaje 2", conversation_id="conv-crash")
    assert second["session"]["id"] == first_id
    state = await _wait_until_not_running(manager, first_id)

    assert state["session"]["status"] == "failed"
    finals = [e for e in state["events"] if e["type"] == "assistant_final"]
    assert len(finals) == 2
    assert finals[0]["text"] == "primera respuesta"
    assert "No pude terminar el trabajo" in finals[1]["text"]
