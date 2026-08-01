"""Dirigir al agente mientras trabaja, sin cortarle el trabajo.

Un mensaje mandado con el agente ocupado antes se RECHAZABA y se perdía. Estas
pruebas fijan el comportamiento medible del reemplazo:

- se entrega en la vuelta SIGUIENTE del ciclo agente-herramientas, nunca a
  mitad de una llamada a herramienta (ni a mitad de un lote de varias);
- entra como turno de usuario ANTES de la próxima llamada al modelo;
- varios mensajes se entregan TODOS y en el orden en que se escribieron;
- la cola tiene tope y el que sobra recibe un "no" explícito, sin descartar en
  silencio ninguno de los que ya estaban;
- y el caso que más duele: si el turno TERMINA con algo sin entregar, ese
  mensaje se convierte en el turno siguiente, o se dice claro que no llegó.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

import pytest
from edecan_companion import ide_workers_agent as agent_module
from edecan_companion.ide_sessions import (
    MAX_QUEUED_USER_MESSAGES,
    IDESessionError,
    SessionManager,
)
from edecan_companion.ide_workers_agent import WorkersIDEAgent
from edecan_companion.ide_workspaces import WorkspaceStore
from edecan_llm.base import CompletionRequest, StreamChunk, ToolCall
from edecan_llm.workers_ai import MODELO_IDE_POR_DEFECTO

# --------------------------------------------------------------------- #
# Punto de entrega: ``WorkersIDEAgent.run`` con un proveedor de mentira.
# Aquí se mide DÓNDE entra el mensaje dentro del ciclo real de herramientas.
# --------------------------------------------------------------------- #


class _WorkspaceStub:
    def __init__(self, root: Path) -> None:
        self._root = root

    def root(self, workspace_id: str) -> Path:
        return self._root


class _FileStubQueEncola:
    """``leer_archivo`` que, al ejecutarse, simula a la persona escribiendo.

    Es la única forma honesta de probar "no se entrega a mitad de una
    herramienta": el mensaje tiene que llegar EXACTAMENTE mientras la
    herramienta corre, no antes ni después.
    """

    def __init__(self, cola: list[str], linea_de_tiempo: list[str], mensajes: list[str]) -> None:
        self.cola = cola
        self.linea_de_tiempo = linea_de_tiempo
        self.mensajes = mensajes
        self.llamadas = 0

    def read(self, workspace_id: str, path: str) -> dict[str, Any]:
        self.llamadas += 1
        self.linea_de_tiempo.append(f"tool_inicio:{path}")
        if self.llamadas == 1:
            # La persona escribe justo mientras la primera herramienta del
            # lote está a mitad de camino.
            self.cola.extend(self.mensajes)
        self.linea_de_tiempo.append(f"tool_fin:{path}")
        return {"path": path, "content": "contenido"}


class _ProveedorDosVueltas:
    """Vuelta 1: pide DOS herramientas en el mismo lote. Vuelta 2: cierra."""

    name = "fake"

    def __init__(self, model: str) -> None:
        self.model = model
        self.closed = False
        self.requests: list[CompletionRequest] = []

    async def aclose(self) -> None:
        self.closed = True

    async def stream(self, request: CompletionRequest):
        self.requests.append(request)
        if len(self.requests) == 1:
            yield StreamChunk(
                type="tool_call",
                tool_call=ToolCall(id="leer-1", name="leer_archivo", arguments={"ruta": "a.py"}),
            )
            yield StreamChunk(
                type="tool_call",
                tool_call=ToolCall(id="leer-2", name="leer_archivo", arguments={"ruta": "b.py"}),
            )
            return
        yield StreamChunk(type="text", text="Listo, y ya leí lo que me mandaste después.")


def _mensajes_de_usuario(request: CompletionRequest) -> list[str]:
    return [
        mensaje.content
        for mensaje in request.messages
        if mensaje.role == "user" and isinstance(mensaje.content, str)
    ]


async def test_el_mensaje_llega_en_la_vuelta_siguiente_y_nunca_a_mitad_de_una_herramienta(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider = _ProveedorDosVueltas(MODELO_IDE_POR_DEFECTO)
    monkeypatch.setattr(agent_module, "WorkersAIProvider", lambda model: provider)

    cola: list[str] = []
    linea_de_tiempo: list[str] = []
    files = _FileStubQueEncola(cola, linea_de_tiempo, ["y de paso arregla el test"])

    def entregar() -> list[str]:
        pendientes = list(cola)
        cola.clear()
        if pendientes:
            linea_de_tiempo.append("entrega")
        return pendientes

    await WorkersIDEAgent(_WorkspaceStub(tmp_path), files).run(  # type: ignore[arg-type]
        workspace_id="workspace",
        prompt="Arregla el login.",
        write_event=lambda kind, text: None,
        cancelled=lambda: False,
        pending_user_messages=entregar,
    )

    # Medible 1: la entrega ocurrió DESPUÉS de que el lote entero de
    # herramientas terminó -- ni entre el inicio y el fin de una, ni entre las
    # dos herramientas del mismo lote.
    assert linea_de_tiempo == [
        "tool_inicio:a.py",
        "tool_fin:a.py",
        "tool_inicio:b.py",
        "tool_fin:b.py",
        "entrega",
    ]

    # Medible 2: no viajó en la llamada al modelo de la vuelta en la que se
    # escribió (ahí el modelo ya estaba trabajando con lo anterior)...
    assert _mensajes_de_usuario(provider.requests[0]) == ["Arregla el login."]
    # ...y sí en la siguiente, ANTES de que el modelo respondiera.
    assert _mensajes_de_usuario(provider.requests[1]) == [
        "Arregla el login.",
        "y de paso arregla el test",
    ]


async def test_varios_mensajes_se_entregan_todos_y_en_orden(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Tres ideas seguidas son tres datos, no tres versiones de uno."""
    provider = _ProveedorDosVueltas(MODELO_IDE_POR_DEFECTO)
    monkeypatch.setattr(agent_module, "WorkersAIProvider", lambda model: provider)

    cola: list[str] = []
    files = _FileStubQueEncola(
        cola, [], ["usa la API nueva", "no toques el schema", "y corre los tests"]
    )

    def entregar() -> list[str]:
        pendientes = list(cola)
        cola.clear()
        return pendientes

    await WorkersIDEAgent(_WorkspaceStub(tmp_path), files).run(  # type: ignore[arg-type]
        workspace_id="workspace",
        prompt="Arregla el login.",
        write_event=lambda kind, text: None,
        cancelled=lambda: False,
        pending_user_messages=entregar,
    )

    assert _mensajes_de_usuario(provider.requests[1]) == [
        "Arregla el login.",
        "usa la API nueva",
        "no toques el schema",
        "y corre los tests",
    ]


# --------------------------------------------------------------------- #
# Cola, tope y cierre del turno: ``SessionManager`` completo.
# --------------------------------------------------------------------- #


def _make_manager(state_dir: Path) -> SessionManager:
    return SessionManager(state_dir, WorkspaceStore(state_dir))


def _authorize(manager: SessionManager, project: Path) -> dict[str, Any]:
    return manager.workspaces.authorize(str(project))


async def _wait_until_not_running(manager: SessionManager, session_id: str, *, attempts=200):
    for _ in range(attempts):
        state = manager.read(session_id, "agent", 0)
        if state["session"]["status"] != "running":
            return state
        await asyncio.sleep(0.02)
    pytest.fail(f"La sesión {session_id} nunca dejó de estar 'running'.")


async def _wait_for_event(manager: SessionManager, session_id: str, tipo: str, *, attempts=200):
    """Espera un evento concreto, no un estado.

    ``close()`` marca "cancelled" en el acto, así que esperar por el estado
    devolvería antes de que el hilo del turno haya corrido su cierre -- y el
    cierre es justamente lo que decide qué pasa con lo encolado.
    """
    for _ in range(attempts):
        state = manager.read(session_id, "agent", 0)
        if any(event["type"] == tipo for event in state["events"]):
            return state
        await asyncio.sleep(0.02)
    pytest.fail(f"La sesión {session_id} nunca emitió un evento «{tipo}».")


def _textos(events: list[dict[str, Any]], tipo: str) -> list[str]:
    return [event["text"] for event in events if event["type"] == tipo]


async def test_mensaje_encolado_queda_visible_y_se_entrega_al_turno_vivo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """De punta a punta: la persona escribe con el agente ocupado, ve que su
    mensaje llegó (``user_queued``) y después ve cuándo se entregó
    (``user_delivered``), sin que se abra una sesión nueva."""
    project = tmp_path / "proyecto"
    project.mkdir()
    manager = _make_manager(tmp_path / "ide")
    workspace = _authorize(manager, project)

    entregados: list[str] = []
    libre = threading.Event()
    primera_vuelta_lista = threading.Event()

    async def fake_run(_self, **kwargs):
        pendientes = kwargs["pending_user_messages"]
        # Primera vuelta: todavía no hay nada que entregar.
        assert pendientes() == []
        primera_vuelta_lista.set()
        libre.wait(timeout=5)
        # Vuelta siguiente: aquí sí.
        entregados.extend(pendientes())
        kwargs["write_event"]("assistant_final", "listo")

    monkeypatch.setattr("edecan_companion.ide_workers_agent.WorkersIDEAgent.run", fake_run)

    primero = manager.start_agent(workspace["id"], "Arregla el login", conversation_id="conv-d")
    session_id = primero["session"]["id"]
    assert primera_vuelta_lista.wait(timeout=5)

    segundo = manager.start_agent(workspace["id"], "y de paso el logout", conversation_id="conv-d")
    assert segundo["session"]["id"] == session_id
    assert segundo["queued"]["position"] == 1
    assert segundo["queued"]["max_pending"] == MAX_QUEUED_USER_MESSAGES
    # El mensaje ya es visible en el hilo aunque todavía no se haya entregado.
    assert _textos(manager.read(session_id, "agent", 0)["events"], "user_queued") == [
        "y de paso el logout"
    ]

    libre.set()
    state = await _wait_until_not_running(manager, session_id)

    assert entregados == ["y de paso el logout"]
    assert state["session"]["status"] == "completed"
    assert _textos(state["events"], "user_delivered") == ["y de paso el logout"]
    # Y ese mensaje cuenta como turno de usuario para el contexto del próximo
    # turno: si no, el agente no recordaría lo que se le pidió a mitad.
    assert "[user] y de paso el logout" in manager._continuation_prompt(
        manager._get(session_id, "agent"), "¿lo hiciste?"
    )


async def test_la_cola_tiene_tope_y_el_que_sobra_recibe_un_no_explicito(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    project = tmp_path / "proyecto"
    project.mkdir()
    manager = _make_manager(tmp_path / "ide")
    workspace = _authorize(manager, project)

    libre = threading.Event()
    entregados: list[str] = []

    async def fake_run(_self, **kwargs):
        libre.wait(timeout=5)
        entregados.extend(kwargs["pending_user_messages"]())
        kwargs["write_event"]("assistant_final", "listo")

    monkeypatch.setattr("edecan_companion.ide_workers_agent.WorkersIDEAgent.run", fake_run)

    started = manager.start_agent(workspace["id"], "Mensaje 0", conversation_id="conv-tope")
    session_id = started["session"]["id"]

    for i in range(MAX_QUEUED_USER_MESSAGES):
        encolado = manager.start_agent(
            workspace["id"], f"Mensaje {i + 1}", conversation_id="conv-tope"
        )
        assert encolado["queued"]["position"] == i + 1

    # El que sobra recibe un no con motivo, y NO desplaza a ninguno de los que
    # la persona ya vio confirmados como "en cola".
    with pytest.raises(IDESessionError, match="esperando a que el agente"):
        manager.start_agent(workspace["id"], "Mensaje de más", conversation_id="conv-tope")

    libre.set()
    await _wait_until_not_running(manager, session_id)

    assert entregados == [f"Mensaje {i + 1}" for i in range(MAX_QUEUED_USER_MESSAGES)]
    assert "Mensaje de más" not in entregados


async def test_turno_que_termina_sin_entregar_convierte_lo_encolado_en_el_turno_siguiente(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """El caso que más duele: el agente cierra justo cuando la persona manda
    algo. Ese mensaje no puede evaporarse -- se vuelve el turno siguiente."""
    project = tmp_path / "proyecto"
    project.mkdir()
    manager = _make_manager(tmp_path / "ide")
    workspace = _authorize(manager, project)

    prompts: list[str] = []
    libre = threading.Event()

    async def fake_run(_self, **kwargs):
        prompts.append(kwargs["prompt"])
        if len(prompts) == 1:
            # Turno 1: termina SIN llamar nunca a ``pending_user_messages``
            # (el mensaje llegó después de su última vuelta).
            libre.wait(timeout=5)
        kwargs["write_event"]("assistant_final", f"respuesta #{len(prompts)}")

    monkeypatch.setattr("edecan_companion.ide_workers_agent.WorkersIDEAgent.run", fake_run)

    started = manager.start_agent(workspace["id"], "Arregla el login", conversation_id="conv-fin")
    session_id = started["session"]["id"]
    manager.start_agent(workspace["id"], "faltó el logout", conversation_id="conv-fin")
    libre.set()

    for _ in range(200):
        state = manager.read(session_id, "agent", 0)
        if len(prompts) >= 2 and state["session"]["status"] != "running":
            break
        await asyncio.sleep(0.02)

    state = manager.read(session_id, "agent", 0)
    assert state["session"]["status"] == "completed"
    # Se convirtió en un turno de verdad de la MISMA conversación, con su
    # propio evento de usuario y su propia respuesta.
    assert len(manager.list("agent")["sessions"]) == 1
    assert "faltó el logout" in prompts[1]
    assert _textos(state["events"], "user") == ["Arregla el login", "faltó el logout"]
    assert _textos(state["events"], "assistant_final") == ["respuesta #1", "respuesta #2"]
    assert not _textos(state["events"], "user_undelivered")


async def test_turno_cancelado_no_arranca_otro_solo_y_dice_que_no_se_entrego(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Cancelar es "para". Un mensaje en cola no puede convertirlo en "sigue":
    se reporta como no entregado, con el texto a la vista para reenviarlo."""
    project = tmp_path / "proyecto"
    project.mkdir()
    manager = _make_manager(tmp_path / "ide")
    workspace = _authorize(manager, project)

    turnos: list[str] = []

    async def fake_run(_self, **kwargs):
        turnos.append(kwargs["prompt"])
        while not kwargs["cancelled"]():
            await asyncio.sleep(0.01)
        kwargs["write_event"]("status", "Trabajo cancelado.")

    monkeypatch.setattr("edecan_companion.ide_workers_agent.WorkersIDEAgent.run", fake_run)

    started = manager.start_agent(workspace["id"], "Migra la base", conversation_id="conv-cancel")
    session_id = started["session"]["id"]
    manager.start_agent(workspace["id"], "usa postgres", conversation_id="conv-cancel")

    manager.close(session_id, "agent")
    state = await _wait_for_event(manager, session_id, "user_undelivered")

    assert state["session"]["status"] == "cancelled"
    assert len(turnos) == 1
    assert _textos(state["events"], "user_undelivered") == ["usa postgres"]
    assert any(
        "NO se entregaron porque cancelaste el turno" in texto
        for texto in _textos(state["events"], "status")
    )


async def test_mensaje_mandado_durante_un_plan_aprobado_se_entrega_al_terminar_el_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Un plan aprobado no tiene punto de entrega intermedio -- cada paso es un
    sub-agente aislado con su propio alcance. Lo encolado no se pierde igual:
    se entrega al cerrar el plan, como el turno siguiente."""
    project = tmp_path / "proyecto"
    project.mkdir()
    manager = _make_manager(tmp_path / "ide")
    workspace = _authorize(manager, project)

    prompts: list[str] = []
    paso_corriendo = threading.Event()
    seguir = threading.Event()

    async def fake_run(_self, **kwargs):
        prompts.append(kwargs["prompt"])
        if kwargs.get("plan_store") is not None:
            plan = kwargs["plan_store"].propose(
                kwargs["session_id"], "Migrar la base", ["Crear apps/a.py", "Crear apps/b.py"]
            )
            kwargs["write_event"](
                "plan_proposed",
                json.dumps(
                    {"plan": plan.public(), "rutas_por_paso": [["apps/a.py"], ["apps/b.py"]]},
                    ensure_ascii=False,
                ),
            )
            return
        if "Paso a ejecutar" in kwargs["prompt"]:
            paso_corriendo.set()
            seguir.wait(timeout=5)
        kwargs["write_event"]("assistant_final", "hecho")

    monkeypatch.setattr("edecan_companion.ide_workers_agent.WorkersIDEAgent.run", fake_run)

    started = manager.start_agent(workspace["id"], "Migra la base", conversation_id="conv-plan-d")
    session_id = started["session"]["id"]
    state = await _wait_until_not_running(manager, session_id)
    assert state["session"]["status"] == "plan_pending"

    plan = manager.get_active_plan(session_id)
    assert plan is not None
    manager.approve_plan(session_id, plan["id"])
    assert paso_corriendo.wait(timeout=5)

    encolado = manager.start_agent(
        workspace["id"], "y agrega el índice", conversation_id="conv-plan-d"
    )
    assert encolado["queued"]["position"] == 1
    seguir.set()

    state = await _wait_for_event(manager, session_id, "user")
    for _ in range(200):
        state = manager.read(session_id, "agent", 0)
        if "y agrega el índice" in _textos(state["events"], "user"):
            break
        await asyncio.sleep(0.02)

    assert "y agrega el índice" in _textos(state["events"], "user")
    assert not _textos(state["events"], "user_undelivered")


async def test_cancelar_un_plan_en_curso_tampoco_promueve_lo_encolado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Misma regla que un turno normal, en el camino del plan aprobado.

    Cuando la persona aprieta detener a mitad de un plan, los pasos fallan
    JUSTAMENTE por eso y el reparto los reporta como fallidos. Si ese "failed"
    se escribe encima del "cancelled", lo encolado pasa a ser promovible y se
    le arranca un turno nuevo -- con el plan cancelado -- a quien acababa de
    pedir que se pare. Ver ``_cerrar_turno_de_plan``.
    """
    project = tmp_path / "proyecto"
    project.mkdir()
    manager = _make_manager(tmp_path / "ide")
    workspace = _authorize(manager, project)

    prompts: list[str] = []
    paso_corriendo = threading.Event()

    async def fake_run(_self, **kwargs):
        prompts.append(kwargs["prompt"])
        if kwargs.get("plan_store") is not None:
            plan = kwargs["plan_store"].propose(
                kwargs["session_id"], "Migrar la base", ["Crear apps/a.py", "Crear apps/b.py"]
            )
            kwargs["write_event"](
                "plan_proposed",
                json.dumps(
                    {"plan": plan.public(), "rutas_por_paso": [["apps/a.py"], ["apps/b.py"]]},
                    ensure_ascii=False,
                ),
            )
            return
        paso_corriendo.set()
        while not kwargs["cancelled"]():
            await asyncio.sleep(0.01)
        kwargs["write_event"]("status", "Trabajo cancelado.")

    monkeypatch.setattr("edecan_companion.ide_workers_agent.WorkersIDEAgent.run", fake_run)

    started = manager.start_agent(workspace["id"], "Migra la base", conversation_id="conv-plan-x")
    session_id = started["session"]["id"]
    state = await _wait_until_not_running(manager, session_id)
    assert state["session"]["status"] == "plan_pending"

    plan = manager.get_active_plan(session_id)
    assert plan is not None
    manager.approve_plan(session_id, plan["id"])
    assert paso_corriendo.wait(timeout=5)

    manager.start_agent(workspace["id"], "usa postgres mejor", conversation_id="conv-plan-x")
    manager.close(session_id, "agent")

    state = await _wait_for_event(manager, session_id, "user_undelivered")
    assert state["session"]["status"] == "cancelled"
    assert _textos(state["events"], "user_undelivered") == ["usa postgres mejor"]
    # Y sobre todo: NO se le arrancó un turno nuevo al que pidió parar.
    assert _textos(state["events"], "user") == ["Migra la base"]
    assert not any("usa postgres mejor" in prompt for prompt in prompts)


async def test_no_se_encolan_imagenes_porque_el_modelo_del_turno_ya_esta_fijado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    project = tmp_path / "proyecto"
    project.mkdir()
    manager = _make_manager(tmp_path / "ide")
    workspace = _authorize(manager, project)

    libre = threading.Event()

    async def fake_run(_self, **kwargs):
        libre.wait(timeout=5)
        kwargs["write_event"]("assistant_final", "listo")

    monkeypatch.setattr("edecan_companion.ide_workers_agent.WorkersIDEAgent.run", fake_run)

    started = manager.start_agent(workspace["id"], "Arregla esto", conversation_id="conv-img")
    session_id = started["session"]["id"]

    with pytest.raises(IDESessionError, match="no imágenes"):
        manager.start_agent(
            workspace["id"],
            "mira la captura",
            conversation_id="conv-img",
            attachments=[{"name": "captura.png", "media_type": "image/png", "data": "x"}],
        )

    libre.set()
    state = await _wait_until_not_running(manager, session_id)
    assert not _textos(state["events"], "user_queued")
