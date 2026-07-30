"""Integración entre las piezas construidas-pero-no-cableadas y el camino REAL.

T1.4 del plan de paridad de IDE (ver `plan-ide-paridad.local.md`): `ide_plan.py`,
`ide_checkpoints.py`, `ide_costos.py` e `ide_equipo.py` ya tienen sus propios
tests unitarios (`test_ide_plan.py`, `test_ide_checkpoints.py`,
`test_ide_costos.py`, `test_ide_equipo.py`) y pasan con eventos armados a
mano. Este archivo NO repite eso: dirige un turno de agente REAL a través de
`ide_sessions.SessionManager` (el mismo monkeypatch de `WorkersIDEAgent.run`
que usa `test_ide_sessions.py`, más escrituras reales vía `FileService`) y
alimenta el estado que ese camino de verdad produce a cada pieza suelta, para
encontrar dónde el contrato que cada pieza ASUME no coincide con lo que
`ide_sessions.py` / `ide_workers_agent.py` de verdad entregan hoy.

Ningún archivo cuello de botella se toca ni se modifica: solo se importa y
se ejercita desde aquí.

Hallazgos que estos tests fijan como comportamiento medible (ver cada test
para el detalle):

1. `Session.events` es un `deque`, no una `list` -- pasarlo tal cual a
   `ide_costos.analizar_tarea` REVIENTA con `TypeError` en cuanto el turno
   tuvo al menos una herramienta, porque `_construir_acciones` hace slicing
   (`eventos[pos:fin]`) y `deque` no soporta slices. El integrador tiene que
   acordarse de `list(session.events)` -- no está documentado en
   `ide_costos.py` y el error de tipo no aparece hasta ejecutarse con datos
   reales, no con los eventos de prueba armados a mano.
2. Nada en `ide_sessions.py` expone el `turn_start_cursor` públicamente
   (ni en `session.public()` ni en el dict que devuelve `start_agent`): es
   una variable local que solo conoce el hilo de `_run_workers_agent`. Como
   una sesión de agente reusa el mismo hilo de eventos entre turnos (ver
   `test_ide_sessions.py`), quien quiera contabilizar el costo de "el turno
   que acaba de terminar" con `ide_costos` no tiene, hoy, ningún campo
   público de dónde sacar ese corte.
3. `ide_checkpoints.py` SÍ funciona correctamente contra rutas y contenidos
   reales de `FileService`/`WorkspaceStore` una vez que alguien llama
   `track()`/`seal()` a mano -- pero hoy NADA los llama: ni
   `ide_sessions._run_workers_agent` ni `ide_workers_agent.WorkersIDEAgent`
   tienen un parámetro para inyectar ese gancho. El punto de escritura real
   está dentro de `WorkersIDEAgent._execute` (dos capas más abajo de donde
   el docstring de `ide_checkpoints.py` sugiere conectarlo).
4. `ide_equipo.SubtareaRunner` debe devolver `str` (la respuesta del turno),
   pero `WorkersIDEAgent.run(...) -> None` -- el resultado real solo sale
   por el canal lateral `write_event("assistant_final", texto)`. Conectar
   `workers_agent.run` directo como runner de un sub-agente pierde la
   respuesta sin ningún error: la sub-tarea queda "completada" con
   `salida=None`.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest
from edecan_companion.ide_checkpoints import CheckpointStore
from edecan_companion.ide_costos import analizar_tarea
from edecan_companion.ide_equipo import (
    ControlEquipo,
    EquipoDeAgentes,
    Subtarea,
    construir_plan,
)
from edecan_companion.ide_plan import PlanStore
from edecan_companion.ide_sessions import Session, SessionManager
from edecan_companion.ide_workspaces import WorkspaceStore

# --------------------------------------------------------------------- #
# Helpers compartidos: levantar un SessionManager real y hacerle correr un
# turno de agente real (con escrituras reales vía FileService), igual que lo
# haría el companion en producción salvo por el proveedor LLM, que se
# reemplaza por una función determinista que emite EXACTAMENTE la misma
# secuencia/forma de eventos que ``ide_workers_agent.WorkersIDEAgent.run``
# emite hoy (ver esa fuente: ``progress`` antes de cada tanda de
# herramientas, ``tool`` por cada llamada, ``file``/``command``/``output``
# según la herramienta, y un único ``assistant_final`` al cerrar).
# --------------------------------------------------------------------- #


def _make_manager(tmp_path: Path) -> SessionManager:
    state_dir = tmp_path / "state"
    workspaces = WorkspaceStore(state_dir)
    return SessionManager(state_dir, workspaces)


def _authorize(
    manager: SessionManager, tmp_path: Path, *, name: str = "proyecto"
) -> dict[str, Any]:
    project = tmp_path / name
    project.mkdir()
    (project / "nota.txt").write_text("version original\n", encoding="utf-8")
    return manager.workspaces.authorize(str(project))


async def _wait_until_not_running(
    manager: SessionManager, session_id: str, *, attempts: int = 200
) -> dict[str, Any]:
    for _ in range(attempts):
        state = manager.read(session_id, "agent", 0)
        if state["session"]["status"] != "running":
            return state
        await asyncio.sleep(0.01)
    pytest.fail(f"La sesión {session_id} nunca dejó de estar 'running'.")


def _install_realistic_fake_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reemplaza ``WorkersIDEAgent.run`` por una tanda de eventos realista.

    Escribe/edita ``nota.txt`` de verdad con el ``FileService`` real (mismo
    camino que usaría el agente de producción) para que las piezas bajo
    prueba reciban rutas y contenidos genuinos, no simulados.
    """

    async def fake_run(self_agent, **kwargs):
        workspace_id = kwargs["workspace_id"]
        write_event = kwargs["write_event"]
        prompt = kwargs["prompt"]

        write_event("progress", f"Voy a resolver: {prompt}")
        write_event("tool", "Usando escribir_archivo.")
        resultado = self_agent.files.write(workspace_id, "nota.txt", "version corregida\n")
        write_event("file", f"Archivo actualizado: {resultado['path']}")

        write_event("progress", "Corro las pruebas para confirmar.")
        write_event("tool", "Usando ejecutar_comando.")
        write_event("command", "pytest -q")
        write_event("output", "1 passed in 0.01s\n")

        write_event("assistant_final", "Corregí nota.txt y las pruebas pasaron.")

    monkeypatch.setattr(
        "edecan_companion.ide_workers_agent.WorkersIDEAgent.run",
        fake_run,
    )


# ======================================================================= #
# Hallazgo 1 — Session.events es un deque: pasarlo tal cual revienta
# ide_costos.analizar_tarea con datos reales (no con los eventos armados a
# mano que usan los tests unitarios de ide_costos).
# ======================================================================= #


async def test_pasar_session_events_directo_ya_no_revienta_ide_costos(tmp_path, monkeypatch):
    """Fija el ARREGLO del hallazgo 1 (ver docstring del módulo e
    `ide_costos._construir_acciones`): pasar ``session.events`` (un
    ``collections.deque``) directo a ``analizar_tarea`` ya no revienta con
    ``TypeError`` -- la función normaliza a ``list`` en su propio cuerpo, así
    que quien la integre no tiene que acordarse de envolverlo."""
    _install_realistic_fake_run(monkeypatch)
    manager = _make_manager(tmp_path)
    workspace = _authorize(manager, tmp_path)

    started = manager.start_agent(workspace["id"], "Arregla el typo de nota.txt")
    session_id = started["session"]["id"]
    await _wait_until_not_running(manager, session_id)

    session: Session = manager._get(session_id, "agent")
    assert isinstance(session.events, __import__("collections").deque)

    resumen_directo = analizar_tarea(
        session.events,
        modelo=session.metadata.get("model"),
        started_at=session.metadata["started_at"],
        ended_at=session.metadata["ended_at"],
    ).resumen()

    # Sigue coincidiendo con la forma explícita (`list(...)`): el arreglo no
    # cambia el resultado, solo deja de exigirle esa envoltura al llamador.
    resumen_explicito = analizar_tarea(
        list(session.events),
        modelo=session.metadata.get("model"),
        started_at=session.metadata["started_at"],
        ended_at=session.metadata["ended_at"],
    ).resumen()
    assert resumen_directo == resumen_explicito
    assert resumen_directo["total_eventos"] == len(session.events)


# ======================================================================= #
# ide_costos contra el formato real que sí coincide: nombres de herramienta,
# tokens estimados, y que el resumen es serializable para la API/UI.
# ======================================================================= #


async def test_ide_costos_lee_correctamente_los_eventos_reales_de_un_turno(tmp_path, monkeypatch):
    _install_realistic_fake_run(monkeypatch)
    manager = _make_manager(tmp_path)
    workspace = _authorize(manager, tmp_path)

    started = manager.start_agent(
        workspace["id"], "Arregla el typo de nota.txt", model="kimi-k2.7-code"
    )
    session_id = started["session"]["id"]
    await _wait_until_not_running(manager, session_id)
    session: Session = manager._get(session_id, "agent")

    resultado = analizar_tarea(
        list(session.events),
        modelo=session.metadata.get("model"),
        started_at=session.metadata["started_at"],
        ended_at=session.metadata["ended_at"],
    )

    nombres_herramienta = {item.nombre for item in resultado.por_herramienta}
    # Los nombres reales que emite ide_workers_agent.py vía "Usando {name}."
    assert nombres_herramienta == {"escribir_archivo", "ejecutar_comando"}

    # Ningún evento "usage" existe todavía en el camino real (ide_costos.py
    # lo documenta como el estado actual): confirma que sigue siendo así.
    assert resultado.tokens_son_estimados is True
    assert any("estimados" in adv for adv in resultado.advertencias)

    # Debe poder pintarse en la UI/API sin sorpresas de serialización.
    json.dumps(resultado.resumen(), ensure_ascii=False)


# ======================================================================= #
# Hallazgo 2 — turn_start_cursor no se expone: aislar "el turno que acaba de
# terminar" en una sesión reusada (dos turnos) no tiene ningún campo público
# de dónde sacarse.
# ======================================================================= #


async def test_turn_start_cursor_ahora_expuesto_aisla_el_ultimo_turno(tmp_path, monkeypatch):
    """Fija el ARREGLO del hallazgo 2: ``session.public()`` ahora expone
    ``turn_start_cursor`` (y ``turn_user_cursor``, el anchor real que usa
    ``SessionManager.turn_cost``) -- ya no hace falta acoplarse al texto
    literal de un evento "status" para acotar "el turno que acaba de
    terminar" dentro de una sesión reusada. ``SessionManager.turn_cost`` (ver
    ``ide_sessions.py``) es el consumidor real de ese campo."""
    _install_realistic_fake_run(monkeypatch)
    manager = _make_manager(tmp_path)
    workspace = _authorize(manager, tmp_path)

    primero = manager.start_agent(
        workspace["id"], "Primer pedido", conversation_id="conv-costos"
    )
    session_id = primero["session"]["id"]
    await _wait_until_not_running(manager, session_id)

    segundo = manager.start_agent(
        workspace["id"],
        "Segundo pedido, ya en la misma conversación",
        conversation_id="conv-costos",
    )
    assert segundo["session"]["id"] == session_id  # misma sesión, reusada (CIMIENTO A)
    await _wait_until_not_running(manager, session_id)

    session: Session = manager._get(session_id, "agent")

    public = session.public()
    assert isinstance(public["turn_start_cursor"], int)
    assert isinstance(public["turn_user_cursor"], int)
    # El cursor del segundo turno tiene que quedar DESPUÉS del primero: si no
    # se refrescara en cada turno reusado, este campo mentiría sobre cuál es
    # "el último" turno de la conversación.
    assert public["turn_start_cursor"] > public["turn_user_cursor"]

    # Sin acotar, analizar_tarea sobre TODO el historial mezcla ambos turnos
    # -- infla acciones/eventos frente a mirar solo el último.
    todo_el_historial = analizar_tarea(list(session.events)).resumen()
    solo_el_ultimo_turno = manager.turn_cost(session_id)

    assert todo_el_historial["total_acciones"] > solo_el_ultimo_turno["total_acciones"]
    assert todo_el_historial["total_eventos"] > solo_el_ultimo_turno["total_eventos"]


# ======================================================================= #
# ide_checkpoints: SÍ funciona con rutas/contenidos reales una vez cableado
# a mano -- confirma que el gap es solo "nadie llama track()/seal() hoy", no
# un desajuste de formato.
# ======================================================================= #


async def test_ide_checkpoints_cableado_a_mano_deshace_un_turno_real(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    workspaces = WorkspaceStore(state_dir)
    manager = SessionManager(state_dir, workspaces)
    workspace = _authorize(manager, tmp_path)
    checkpoints = CheckpointStore(state_dir, workspaces)

    checkpoint = checkpoints.create(workspace["id"], label="turno de prueba")

    async def fake_run_con_checkpoint(self_agent, **kwargs):
        workspace_id = kwargs["workspace_id"]
        write_event = kwargs["write_event"]
        # Esto es exactamente lo que el integrador tendría que insertar
        # dentro de WorkersIDEAgent._execute, ANTES de cada escritura real
        # (ver el hallazgo 3 en el docstring del módulo): hoy no existe
        # ningún parámetro para inyectarlo, así que aquí se simula a mano.
        checkpoints.track(checkpoint["id"], "nota.txt")
        resultado = self_agent.files.write(workspace_id, "nota.txt", "version rota por el agente\n")
        write_event("file", f"Archivo actualizado: {resultado['path']}")
        write_event("assistant_final", "Listo.")

    monkeypatch.setattr(
        "edecan_companion.ide_workers_agent.WorkersIDEAgent.run",
        fake_run_con_checkpoint,
    )

    started = manager.start_agent(workspace["id"], "Rompe nota.txt")
    await _wait_until_not_running(manager, started["session"]["id"])

    # Sellar ocurre al terminar el turno (aquí, a mano; en producción sería
    # el "finally" de _run_workers_agent, que hoy tampoco lo llama).
    checkpoints.seal(checkpoint["id"])

    nota = (Path(workspace["path"]) / "nota.txt")
    assert nota.read_text(encoding="utf-8") == "version rota por el agente\n"

    resultado_restore = checkpoints.restore(checkpoint["id"])
    assert resultado_restore["restored"] == ["nota.txt"]
    assert resultado_restore["conflicts"] == []
    assert nota.read_text(encoding="utf-8") == "version original\n"


def test_ide_checkpoints_solo_tiene_el_path_como_texto_libre_en_el_evento(tmp_path):
    """Menor, pero real: el evento "file" no trae un campo `path` estructurado.

    Quien quiera construir la vista de diffs aceptar/rechazar (1.2 del plan)
    a partir del historial de eventos tiene que parsear el texto humano
    "Archivo actualizado: {ruta}" con una expresión regular -- no hay un
    campo `path` aparte en el evento. Este test fija ese formato para que
    romperlo (p. ej. cambiar el texto sin querer) se note en un test, no en
    producción.
    """

    texto = "Archivo actualizado: apps/foo/bar.py"
    patron = re.compile(r"^Archivo actualizado: (?P<ruta>.+)$")
    match = patron.match(texto)
    assert match is not None
    assert match.group("ruta") == "apps/foo/bar.py"


# ======================================================================= #
# Hallazgo 4 — ide_equipo.SubtareaRunner debe devolver str, pero
# WorkersIDEAgent.run(...) -> None: conectarlo directo pierde la respuesta.
# ======================================================================= #


async def test_conectar_workers_agent_run_directo_como_runner_pierde_la_respuesta(
    tmp_path, monkeypatch
):
    manager = _make_manager(tmp_path)
    workspace = _authorize(manager, tmp_path)
    workers_agent = manager.workers_agent  # el mismo objeto real que usa ide_sessions

    async def fake_run_con_contrato_real(self_agent, **kwargs):
        """Mismo contrato REAL de WorkersIDEAgent.run: devuelve None, entrega
        el texto solo por write_event("assistant_final", ...)."""
        write_event = kwargs["write_event"]
        write_event("assistant_final", "La sub-tarea terminó: archivo X quedó arreglado.")
        return None

    monkeypatch.setattr(
        "edecan_companion.ide_workers_agent.WorkersIDEAgent.run",
        fake_run_con_contrato_real,
    )

    capturado: dict[str, Any] = {}

    async def runner_ingenuo(sub: Subtarea, control: ControlEquipo) -> str:
        # Lo "obvio": llamar directo a workers_agent.run e ignorar que su
        # tipo de retorno es None -- exactamente lo que un integrador que no
        # leyó el código de ide_workers_agent.py haría.
        return await workers_agent.run(
            workspace_id=workspace["id"],
            prompt=sub.instrucciones,
            write_event=lambda tipo, texto: capturado.setdefault(tipo, texto),
            cancelled=lambda: False,
        )

    plan = construir_plan(
        [
            Subtarea(
                id="sub-1",
                titulo="Arreglar X",
                instrucciones="Arregla el archivo X.",
                rutas=("apps/x.py",),
            )
        ]
    )
    equipo = EquipoDeAgentes(runner=runner_ingenuo, max_concurrencia=1)
    resultado = await equipo.ejecutar(plan)

    assert resultado.completadas == ["sub-1"]
    estado = resultado.estados["sub-1"]
    # El desajuste real: la sub-tarea "completó" pero su salida es None,
    # aunque el agente sí produjo una respuesta real -- solo que viajó por
    # el canal lateral (write_event), no por el valor de retorno.
    assert estado.salida is None
    assert capturado.get("assistant_final") == "La sub-tarea terminó: archivo X quedó arreglado."

    # La forma correcta: envolver el runner para capturar assistant_final y
    # devolverlo explícitamente.
    async def runner_correcto(sub: Subtarea, control: ControlEquipo) -> str:
        recogido: dict[str, str] = {}

        def capturar_evento(tipo: str, texto: str) -> None:
            if tipo == "assistant_final":
                recogido["texto"] = texto

        await workers_agent.run(
            workspace_id=workspace["id"],
            prompt=sub.instrucciones,
            write_event=capturar_evento,
            cancelled=lambda: False,
        )
        return recogido.get("texto") or ""

    equipo_correcto = EquipoDeAgentes(runner=runner_correcto, max_concurrencia=1)
    resultado_correcto = await equipo_correcto.ejecutar(plan)
    assert resultado_correcto.estados["sub-1"].salida == (
        "La sub-tarea terminó: archivo X quedó arreglado."
    )


# ======================================================================= #
# ide_plan: session_id es un string opaco -- confirma que no hay desajuste
# al usar ids reales de Session (dos sesiones reales, sin cruce de planes).
# ======================================================================= #


async def test_ide_plan_funciona_con_ids_reales_de_sesion_sin_cruzarse(tmp_path, monkeypatch):
    _install_realistic_fake_run(monkeypatch)
    manager = _make_manager(tmp_path)
    workspace = _authorize(manager, tmp_path, name="proyecto_a")
    workspace_b = _authorize(manager, tmp_path, name="proyecto_b")

    sesion_1 = manager.start_agent(workspace["id"], "Tarea 1")["session"]["id"]
    sesion_2 = manager.start_agent(workspace_b["id"], "Tarea 2")["session"]["id"]
    await _wait_until_not_running(manager, sesion_1)
    await _wait_until_not_running(manager, sesion_2)
    assert sesion_1 != sesion_2

    planes = PlanStore()
    plan_1 = planes.propose(sesion_1, "Meta de la sesión 1", ["paso a", "paso b", "paso c"])
    plan_2 = planes.propose(sesion_2, "Meta de la sesión 2", ["paso x", "paso y", "paso z"])

    assert planes.get_active_for_session(sesion_1).id == plan_1.id
    assert planes.get_active_for_session(sesion_2).id == plan_2.id

    planes.approve(plan_1.id)
    planes.advance(plan_1.id)

    # Avanzar el plan de la sesión 1 no toca en nada el de la sesión 2.
    plan_2_recargado = planes.get(plan_2.id)
    assert plan_2_recargado.status == "proposed"
    assert plan_2_recargado.current_step_index is None
