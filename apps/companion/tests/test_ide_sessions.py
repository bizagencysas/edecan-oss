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
import contextlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from edecan_companion import ide_sessions as ide_sessions_module
from edecan_companion.ide_opencode_config import PROVEEDOR_WORKERS_AI_ID
from edecan_companion.ide_opencode_permisos import SolicitudPermiso
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


def test_save_usa_reemplazar_con_reintentos_compartido(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """``SessionManager._save`` delega el reemplazo atómico del JSON de
    metadatos a ``platform_paths.reemplazar_con_reintentos`` -- la MISMA
    implementación que usan ``ide_files.py`` e ``ide_workspaces.py`` (antes
    esto era una función local duplicada en este archivo; el contrato del
    mitigador de "archivo bloqueado en Windows" ahora se fija una sola vez,
    en ``tests/test_platform_paths.py``). Este test es de integración: solo
    confirma que ``_save`` de verdad pasa por esa función compartida, no
    repite el contrato completo del reintento."""
    workspaces = WorkspaceStore(tmp_path)
    manager = ide_sessions_module.SessionManager(tmp_path, workspaces)

    llamadas: list[tuple[Any, Any]] = []
    real = ide_sessions_module.reemplazar_con_reintentos

    def _espia(origen: Any, destino: Any) -> None:
        llamadas.append((origen, destino))
        real(origen, destino)

    monkeypatch.setattr(ide_sessions_module, "reemplazar_con_reintentos", _espia)

    manager._save()

    assert len(llamadas) == 1


# --------------------------------------------------------------------------- #
# Motor opencode -- integración real (opencode serve + Cloudflare Workers AI
# reales), mismo criterio que ``test_ide_opencode*.py``: se salta sola sin
# credenciales, nunca se simula. Confirma el cableado que pide el encargo
# (docs/opencode-motor.md §4.1/§4.2): un ``start_agent`` normal, corrido con
# ``EDECAN_IDE_MOTOR=opencode``, de verdad arranca opencode, manda el prompt,
# consume sus eventos traducidos y dice un archivo real en disco -- no un
# evento simulado.
# --------------------------------------------------------------------------- #


def _leer_env_raiz(nombre: str) -> str | None:
    ruta = Path(__file__).resolve().parents[3] / ".env"
    if not ruta.is_file():
        return None
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        if linea.startswith(f"{nombre}="):
            return linea.split("=", 1)[1].strip()
    return None


_CUENTA_CLOUDFLARE = os.environ.get("CLOUDFLARE_ACCOUNT_ID") or _leer_env_raiz(
    "CLOUDFLARE_ACCOUNT_ID"
)
_TOKEN_CLOUDFLARE = os.environ.get("CLOUDFLARE_API_TOKEN") or _leer_env_raiz("CLOUDFLARE_API_TOKEN")

requiere_credenciales_opencode = pytest.mark.skipif(
    not (_CUENTA_CLOUDFLARE and _TOKEN_CLOUDFLARE),
    reason=(
        "Sin CLOUDFLARE_ACCOUNT_ID/CLOUDFLARE_API_TOKEN (entorno o .env de la raíz) no hay "
        "proveedor real contra el que arrancar opencode -- se salta, no se simula."
    ),
)


async def _wait_until_not_running_opencode(
    manager: SessionManager, session_id: str, *, timeout_s: float = 180.0
) -> dict[str, Any]:
    """Mismo contrato que ``_wait_until_not_running`` de arriba, pero con un
    tope de espera realista para un turno de opencode real (arrancar el
    proceso + un modelo real de Cloudflare puede tardar bastantes segundos,
    muy por encima de lo que necesita el motor viejo con ``fake_run``)."""
    limite = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < limite:
        state = manager.read(session_id, "agent", 0)
        if state["session"]["status"] != "running":
            return state
        await asyncio.sleep(0.5)
    pytest.fail(f"La sesión {session_id} nunca dejó de estar 'running' tras {timeout_s}s.")


@requiere_credenciales_opencode
async def test_start_agent_sobre_opencode_crea_un_archivo_real(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Encargo: "que una sesión de Edecán corra sobre opencode". Con
    ``EDECAN_IDE_MOTOR=opencode`` (el default de producción -- acá se fija
    explícito porque ``conftest._motor_ide_por_defecto_en_pruebas`` lo pone
    en "viejo" para el resto de la suite), ``start_agent`` normal tiene que:
    arrancar opencode de verdad, mandar el prompt, traducir sus eventos al
    hilo de la sesión (``ide_opencode_eventos``) y dejar la sesión
    "completed" con una respuesta final real -- Y, la prueba de verdad, el
    archivo pedido tiene que existir en disco con el contenido pedido.

    NOTA sobre la flakiness real que un verificador reportó sin poder
    explicarla ("network flakiness", "rate limiting") -- la investigué a
    fondo esta ronda con repros standalone (fuera de pytest, para ver el
    hilo de eventos COMPLETO sin el truncado de ``reprlib`` en el mensaje de
    assert) y NO es de red: es el modelo (Kimi K2.7 vía Workers AI) que a
    veces cierra un paso sin dejar NINGÚN texto en el mensaje final -- ya
    sea porque razona explícitamente "no debo decir nada más" (con la
    redacción vieja, "no hagas nada más, no expliques, solo créalo": falló
    1/3 en una corrida standalone), ya sea porque mete la palabra de
    confirmación pedida DENTRO del bloque de razonamiento ("Razonando:
    listo") en vez de en el mensaje real (con la redacción de abajo, que sí
    pide una confirmación explícita: 2 fallos en 9 corridas, contra 1/3 de
    la redacción vieja -- mejora real, no eliminación). El diseño de
    ``_consumir_turno_opencode``/``TraductorDeTurno`` es deliberado y nunca
    fabrica un "completed" a partir de una respuesta vacía o de texto que
    solo vive en el razonamiento interno (ver el docstring de ese método:
    corrige justo el bug contrario, inventar éxito de un silencio) -- así
    que con el mensaje final vacío el turno cierra "failed" aunque el
    archivo ya esté en disco. Pedirle al modelo una confirmación explícita
    (en vez de solo "no expliques") reduce la incidencia pero no la
    elimina: es una propiedad estocástica real del modelo/proveedor, no algo
    que un prompt distinto pueda garantizar al 100% -- documentado acá en
    vez de fingir que quedó resuelto."""
    monkeypatch.setenv("EDECAN_IDE_MOTOR", "opencode")
    project = tmp_path / "proyecto"
    project.mkdir()
    manager = _make_manager(tmp_path / "ide")
    workspace = _authorize(manager, project)

    resultado = manager.start_agent(
        workspace["id"],
        "Crea el archivo saludo.txt con exactamente esta línea de texto: "
        "hola desde opencode -- no expliques cómo lo hiciste, pero en cuanto "
        "termines confirma con la palabra: listo.",
        conversation_id="conv-opencode-real",
    )
    session_id = resultado["session"]["id"]
    try:
        state = await _wait_until_not_running_opencode(manager, session_id)
    finally:
        manager.shutdown()

    assert state["session"]["status"] == "completed", state["events"]
    assert any(e["type"] == "assistant_final" for e in state["events"])
    archivo = project / "saludo.txt"
    assert archivo.is_file(), (
        f"opencode no creó saludo.txt de verdad -- eventos vistos: "
        f"{[(e['type'], e.get('text', '')[:80]) for e in state['events']]}"
    )
    assert "hola desde opencode" in archivo.read_text(encoding="utf-8")
    assert manager.metadata_path.exists()


# --------------------------------------------------------------------------- #
# Permiso pendiente a mitad de turno -- cierra el fallo real que un
# verificador reprodujo en vivo (modo Manual, ver el docstring de
# ``SessionManager._consumir_turno_opencode``): antes de esta ronda no había
# ninguna acción para conceder/rechazar un permiso pedido a mitad de turno, y
# la única forma de progresar era abandonar ese turno y mandar uno nuevo en
# modo Auto -- no "conceder el permiso pedido y que el MISMO turno continúe",
# que es lo que pide el encargo.
# --------------------------------------------------------------------------- #


@requiere_credenciales_opencode
async def test_permiso_pendiente_en_modo_manual_se_puede_conceder_y_el_turno_sigue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("EDECAN_IDE_MOTOR", "opencode")
    project = tmp_path / "proyecto"
    project.mkdir()
    manager = _make_manager(tmp_path / "ide")
    workspace = _authorize(manager, project)

    # Primer turno: inicializa la sesión de opencode (arranca sola en modo
    # Auto, ver el docstring de ``_turno_opencode`` -- nadie puede responder
    # un permiso todavía en el primer mensaje de una conversación nueva).
    primero = manager.start_agent(
        workspace["id"],
        "Responde solo con la palabra: listo",
        conversation_id="conv-permiso-manual",
    )
    session_id = primero["session"]["id"]
    try:
        await _wait_until_not_running_opencode(manager, session_id)

        # A partir de acá, modo Manual: la próxima edición tiene que pedir
        # permiso (tabla de modos: MANUAL x EDICION -> pedir_aprobacion).
        manager.set_modo_agente(session_id, "manual")
        # Ver la nota extensa en ``test_start_agent_sobre_opencode_crea_un_archivo_real``
        # sobre por qué este prompt pide una confirmación explícita en vez de
        # "no expliques nada", y por qué eso reduce -- sin eliminar del todo
        # -- la flakiness real (no de red) de que el modelo cierre el paso
        # sin texto final tras crear el archivo. El sistema, a propósito,
        # nunca disfraza ese silencio de "completed".
        manager.start_agent(
            workspace["id"],
            "Crea el archivo hola_manual.txt con exactamente esta línea: "
            "hola desde manual -- no expliques cómo lo hiciste, pero en "
            "cuanto termines confirma con la palabra: listo.",
            conversation_id="conv-permiso-manual",
        )

        # El turno se queda "running" esperando -- no un cuelgue mudo, sino
        # una espera real con una acción de verdad para resolverla (ver
        # ``listar_permisos_agente``/``responder_permiso_agente``).
        permiso: dict[str, Any] | None = None
        limite = asyncio.get_event_loop().time() + 60.0
        while asyncio.get_event_loop().time() < limite:
            listado = manager.listar_permisos_agente(session_id)
            if listado["permisos"]:
                permiso = listado["permisos"][0]
                break
            await asyncio.sleep(0.5)
        assert permiso is not None, (
            "opencode nunca pidió permiso para la edición en modo manual -- ¿la clasificó "
            "distinto? Revisar ide_opencode_permisos.clasificar_accion."
        )

        # La mitad "conceder" del fallo real: existe de verdad, y el MISMO
        # turno sigue -- no hace falta mandar un mensaje nuevo.
        respuesta = manager.responder_permiso_agente(
            session_id, permiso["request_id"], conceder=True
        )
        assert respuesta == {"ok": True}

        state = await _wait_until_not_running_opencode(manager, session_id, timeout_s=150.0)
    finally:
        manager.shutdown()

    assert state["session"]["status"] == "completed", state["events"]
    archivo = project / "hola_manual.txt"
    assert archivo.is_file(), (
        "conceder el permiso no dejó que el MISMO turno terminara la edición -- eventos: "
        f"{[(e['type'], e.get('text', '')[:80]) for e in state['events']]}"
    )
    assert "hola desde manual" in archivo.read_text(encoding="utf-8")

    # NOTA ACTUALIZADA (ya NO aplica la limitación que decía esta nota antes
    # de la ronda que agregó ``_vigilar_bus_para_sesion``): este test sigue
    # sin exigir que ``agent_permission`` haya quedado escrito en el hilo
    # ANTES de conceder, porque la carrera entre "el bus avisa" y "el test
    # ya vio el permiso por la API directa y lo concedió" no está acotada
    # acá (el test hace polling cada 0.5s, mucho más lento que el aviso del
    # bus, así que normalmente SÍ alcanza a aparecer, pero no es lo que este
    # test mide). Lo que el aviso instantáneo del bus SÍ tiene su propio
    # test determinista, sin depender de un servidor real:
    # ``test_permiso_pendiente_llega_al_hilo_en_menos_de_2s_por_el_bus`` en
    # este mismo archivo. Lo que este test de integración real sigue
    # confirmando es lo que arregló esta ronda: que CONCEDER exista y el
    # MISMO turno realmente siga sin mandar un mensaje nuevo.


@requiere_credenciales_opencode
async def test_permiso_pendiente_que_se_agota_cierra_con_un_solo_mensaje_claro(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Fallo real de un segundo verificador: el cierre por permiso pendiente
    ya escribía un mensaje CLARO (``El turno está esperando tu
    aprobación...``), pero ``_run_opencode_agent`` agregaba ENCIMA un
    ``assistant_final`` genérico ("terminó sin entregar una respuesta
    final") más un evento ``error`` que no mencionaban el permiso -- dos
    mensajes contradictorios donde solo uno explica de verdad qué pasó. Se
    monkeypatchea el tope de espera a un valor bajo para no esperar 20
    minutos reales en la prueba."""
    monkeypatch.setenv("EDECAN_IDE_MOTOR", "opencode")
    monkeypatch.setattr(ide_sessions_module, "_TIEMPO_MAX_ESPERA_PERMISO_OPENCODE", 1.0)
    project = tmp_path / "proyecto"
    project.mkdir()
    manager = _make_manager(tmp_path / "ide")
    workspace = _authorize(manager, project)

    primero = manager.start_agent(
        workspace["id"],
        "Responde solo con la palabra: listo",
        conversation_id="conv-permiso-timeout",
    )
    session_id = primero["session"]["id"]
    try:
        await _wait_until_not_running_opencode(manager, session_id)
        manager.set_modo_agente(session_id, "manual")
        manager.start_agent(
            workspace["id"],
            "Crea el archivo nunca_se_crea.txt con el texto: x -- no hagas nada más.",
            conversation_id="conv-permiso-timeout",
        )
        # Nadie responde el permiso a propósito: se deja agotar la espera.
        state = await _wait_until_not_running_opencode(manager, session_id, timeout_s=150.0)
    finally:
        manager.shutdown()

    assert state["session"]["status"] == "failed", state["events"]
    archivo = project / "nunca_se_crea.txt"
    assert not archivo.exists()

    textos_finales = [e["text"] for e in state["events"] if e["type"] == "assistant_final"]
    assert not any("terminó sin entregar una respuesta final" in t for t in textos_finales), (
        "El cierre por permiso agotado no debe agregar el assistant_final genérico encima "
        f"del mensaje claro que ya explica el motivo real -- eventos: {textos_finales}"
    )
    assert not any(e["type"] == "error" for e in state["events"]), (
        "El cierre por permiso agotado no debe agregar un evento error genérico -- "
        f"eventos: {[(e['type'], e.get('text', '')[:120]) for e in state['events']]}"
    )
    mensajes_status = [e["text"] for e in state["events"] if e["type"] == "status"]
    assert any("Se agotó la espera por tu aprobación" in t for t in mensajes_status), (
        f"No se encontró el mensaje claro de cierre -- eventos: {mensajes_status}"
    )


# --------------------------------------------------------------------------- #
# Aviso instantáneo de permisos/preguntas -- encargo: "que un permiso
# pendiente se vea al instante, no a los 25 segundos". Dos pruebas, ninguna
# necesita opencode real ni credenciales:
#
# 1. ``_vigilar_bus_para_sesion`` filtra correctamente el bus global (solo
#    reacciona a la sesión y los tipos que le importan) -- contra un
#    ``httpx.MockTransport`` propio, no contra opencode.
# 2. ``_consumir_turno_opencode`` de verdad despierta cuando el aviso llega,
#    en vez de esperar ``_TIEMPO_INACTIVIDAD_EVENTOS_OPENCODE`` -- la
#    obligación concreta del encargo: medir que el evento aparece en el
#    hilo en menos de 2 segundos desde que "el puente ve la solicitud"
#    (acá, desde que ``notificacion_bus`` se enciende).
# --------------------------------------------------------------------------- #


async def test_vigilar_bus_para_sesion_solo_reacciona_a_su_sesion_y_sus_tipos(
    tmp_path: Path,
):
    """``_vigilar_bus_para_sesion`` NUNCA debe encender el aviso para: (a)
    una sesión de opencode que no es la nuestra (otra sesión del mismo
    workspace, ver hallazgo 4 de ``ide_opencode_permisos``: el bus es
    server-wide) ni (b) un tipo de evento que ``_consumir_turno_opencode``
    no sabe mostrar (solo ``permission.v2.asked``/``question.v2.asked``
    producen tarjeta -- ver ``_TIPOS_BUS_QUE_DESPIERTAN_EL_TURNO``)."""
    manager = _make_manager(tmp_path / "ide")

    def _linea(tipo: str, session_id: str) -> bytes:
        payload = json.dumps(
            {"id": "evt-1", "type": tipo, "data": {"sessionID": session_id}}
        ).encode("utf-8")
        return b"data: " + payload + b"\n\n"

    cuerpo_ajeno = (
        _linea("permission.v2.asked", "otra-sesion")
        + _linea("question.v2.asked", "otra-sesion")
        # Nuestra sesión, pero un tipo que no le importa a este aviso
        # (informativo -- ver ``_TIPOS_BUS_QUE_DESPIERTAN_EL_TURNO``).
        + _linea("permission.v2.replied", "oc-1")
    )

    def _handler_ajeno(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/event"
        return httpx.Response(200, content=cuerpo_ajeno)

    notificacion_ajena = asyncio.Event()
    cliente_ajeno = httpx.AsyncClient(
        transport=httpx.MockTransport(_handler_ajeno), base_url="http://opencode-fake"
    )
    try:
        await manager._vigilar_bus_para_sesion(
            cliente=cliente_ajeno, opencode_session_id="oc-1", notificacion=notificacion_ajena
        )
    finally:
        await cliente_ajeno.aclose()

    cuerpo_nuestro = (
        _linea("permission.v2.asked", "otra-sesion")  # ruido, debe ignorarse
        + _linea("permission.v2.asked", "oc-1")  # el que sí importa
    )

    def _handler_nuestro(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=cuerpo_nuestro)

    notificacion_nuestra = asyncio.Event()
    cliente_nuestro = httpx.AsyncClient(
        transport=httpx.MockTransport(_handler_nuestro), base_url="http://opencode-fake"
    )
    try:
        await manager._vigilar_bus_para_sesion(
            cliente=cliente_nuestro,
            opencode_session_id="oc-1",
            notificacion=notificacion_nuestra,
        )
    finally:
        await cliente_nuestro.aclose()

    assert not notificacion_ajena.is_set(), (
        "El aviso se encendió con eventos de otra sesión o de un tipo irrelevante -- "
        "debía ignorarlos."
    )
    assert notificacion_nuestra.is_set(), (
        "El aviso NO se encendió con un permission.v2.asked real de nuestra sesión."
    )


class _ServidorEventosDeSesionEnSilencio:
    """Doble de ``ServidorOpencode`` para el test de carrera de abajo: el
    stream POR SESIÓN nunca dice nada (como si opencode estuviera callado
    de verdad -- que es justo el escenario que antes obligaba a esperar
    ``_TIEMPO_INACTIVIDAD_EVENTOS_OPENCODE``). Solo termina si algo cancela
    la tarea que lo está leyendo -- exactamente lo que hace
    ``_leer_stream_sesion_o_avisar_permiso`` cuando gana el aviso del bus."""

    async def eventos(self, _session_id: str, *, tiempo_maximo_sin_eventos: float | None = None):
        if False:
            yield  # hace de este método un generador async de verdad
        await asyncio.sleep(3600)

    async def interrumpir(self, _session_id: str) -> None:
        return None


class _ConsultaConUnPermisoPendiente:
    """Doble de ``PuenteDePermisos`` para el mismo test: siempre hay
    EXACTAMENTE un permiso pendiente (el que el bus avisó), nunca una
    pregunta -- así el bucle de ``_consumir_turno_opencode`` llega directo
    a la rama que emite ``agent_permission``."""

    def __init__(self, permiso: SolicitudPermiso) -> None:
        self._permiso = permiso

    async def permisos_pendientes(self, _session_id: str) -> list[SolicitudPermiso]:
        return [self._permiso]

    async def preguntas_pendientes(self, _session_id: str) -> list[Any]:
        return []


async def test_permiso_pendiente_llega_al_hilo_en_menos_de_2s_por_el_bus(
    tmp_path: Path,
):
    """EL test obligatorio del encargo: demuestra que el evento
    ``agent_permission`` llega al hilo de la sesión en menos de 2 segundos
    desde que el puente ve la solicitud (acá, desde que
    ``notificacion_bus`` se enciende) -- NO a los ``_TIEMPO_INACTIVIDAD_
    EVENTOS_OPENCODE`` (25s) que antes eran la única vía.

    Sin opencode real: ``servidor``/``consulta`` son dobles controlados
    (arriba), así que el único camino por el que ``agent_permission`` puede
    aparecer es la carrera nueva en ``_leer_stream_sesion_o_avisar_permiso``
    -- si esa carrera no existiera (o no despertara a tiempo), este test
    fallaría por timeout, no por casualidad."""
    manager = _make_manager(tmp_path / "ide")
    session = ide_sessions_module.Session(manager, {"id": "sess-bus-test", "kind": "agent"})
    permiso = SolicitudPermiso(id="perm-1", sessionID="oc-1", action="bash", resources=["rm -rf x"])
    servidor = _ServidorEventosDeSesionEnSilencio()
    consulta = _ConsultaConUnPermisoPendiente(permiso)
    notificacion_bus = asyncio.Event()

    async def _el_puente_ve_la_solicitud() -> None:
        # Latencia deliberadamente chica pero no cero -- para que el test
        # mida una carrera real (el stream de sesión sigue "corriendo"
        # cuando el aviso llega), no una que ya arrancó ganada.
        await asyncio.sleep(0.05)
        notificacion_bus.set()

    tarea_aviso = asyncio.create_task(_el_puente_ve_la_solicitud())
    inicio = time.monotonic()
    tarea_turno = asyncio.create_task(
        manager._consumir_turno_opencode(
            session=session,
            servidor=servidor,  # type: ignore[arg-type]
            consulta=consulta,  # type: ignore[arg-type]
            opencode_session_id="oc-1",
            track_file=lambda _ruta: None,
            notificacion_bus=notificacion_bus,
        )
    )
    transcurrido: float | None = None
    try:
        for _ in range(400):  # tope de 4s reales -- el aserto de abajo exige < 2s
            if any(evento["type"] == "agent_permission" for evento in session.events):
                transcurrido = time.monotonic() - inicio
                break
            await asyncio.sleep(0.01)
    finally:
        tarea_turno.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tarea_turno
        tarea_aviso.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tarea_aviso

    assert transcurrido is not None, (
        "El evento agent_permission nunca llegó al hilo -- eventos vistos: "
        f"{[evento['type'] for evento in session.events]}"
    )
    assert transcurrido < 2.0, (
        f"El permiso tardó {transcurrido:.2f}s en llegar al hilo -- debía ser casi "
        "instantáneo por el aviso del bus, no depender de _TIEMPO_INACTIVIDAD_EVENTOS_OPENCODE "
        "(25s)."
    )
    # "Sin duplicar" (encargo, punto 2): una sola tarjeta por solicitud,
    # aunque el bus siga avisando el mismo permiso pendiente en vueltas
    # siguientes del bucle.
    eventos_permiso = [evento for evento in session.events if evento["type"] == "agent_permission"]
    assert len(eventos_permiso) == 1, eventos_permiso


# --------------------------------------------------------------------------- #
# Selector de modelo vs. esfuerzo -- el fallo real que reportó el dueño:
# "elijo Kimi, toco el esfuerzo, y mi elección se pierde". La causa era
# ``fijar_esfuerzo``/``_turno_opencode`` llamando a
# ``modelo_para(perfil, nivel)`` -- que ELIGE el modelo por el nivel de
# esfuerzo e ignora por completo lo que la persona escogió en el selector.
# Regla del arreglo: el selector manda QUÉ modelo; el esfuerzo (Bajo/Medio/
# Alto) solo manda CUÁNTO piensa ESE modelo, nunca lo cambia.
#
# Dos grupos, mismo criterio que el resto del archivo:
# 1. Puros (``_referencia_modelo_para_sesion``, ``set_modelo_agente`` sin
#    sesión de opencode viva): sin credenciales, sin red.
# 2. Reales (``requiere_credenciales_opencode``): arrancan opencode de
#    verdad y leen el modelo DE LA SESIÓN DE OPENCODE -- nunca del almacén
#    de Edecán -- que es exactamente lo que pedía el encargo.
# --------------------------------------------------------------------------- #


def test_referencia_modelo_para_sesion_usa_el_modelo_elegido_no_el_del_perfil(
    tmp_path: Path,
) -> None:
    manager = _make_manager(tmp_path / "ide")
    session = ide_sessions_module.Session(
        manager,
        {
            "id": "sess-modelo-elegido",
            "kind": "agent",
            "model": "@cf/nvidia/nemotron-3-120b-a12b",
        },
    )
    referencia = manager._referencia_modelo_para_sesion(session, "bajo")
    assert referencia.model_id == "@cf/nvidia/nemotron-3-120b-a12b", (
        "el nivel 'bajo' no debe elegir el modelo_alternativo del perfil cuando la persona "
        "ya eligió uno explícito -- ese es exactamente el bug que reportó el dueño"
    )
    assert referencia.reasoning_effort == "low"
    assert referencia.nivel == "bajo"
    assert referencia.variante is None


def test_referencia_modelo_para_sesion_sin_eleccion_cae_al_defecto_del_perfil(
    tmp_path: Path,
) -> None:
    manager = _make_manager(tmp_path / "ide")
    session = ide_sessions_module.Session(
        manager, {"id": "sess-sin-modelo", "kind": "agent", "model": None}
    )
    referencia = manager._referencia_modelo_para_sesion(session, "alto")
    # Modelo fuerte real de config/modelos.yml -> perfiles.ingenieria_software.modelo
    # (ver test_ide_opencode_motor.py::test_modelo_para_alto_usa_el_modelo_fuerte_del_yaml_real).
    assert referencia.model_id == "@cf/moonshotai/kimi-k2.7-code"
    assert referencia.reasoning_effort == "high"


def test_referencia_modelo_para_sesion_modelo_fuera_de_catalogo_cae_al_defecto(
    tmp_path: Path,
) -> None:
    """Un modelo que YA no está en ``config/modelos.yml -> modelos_ide`` (el
    YAML cambió a mitad de vuelo, o algo inyectó metadata a mano) no debe
    mandarse tal cual a opencode -- cae al defecto del perfil, mismo
    criterio que ``TaskRouter.decide`` ya usa para el selector del chat."""
    manager = _make_manager(tmp_path / "ide")
    session = ide_sessions_module.Session(
        manager,
        {"id": "sess-modelo-invalido", "kind": "agent", "model": "@cf/no-existe/inventado"},
    )
    referencia = manager._referencia_modelo_para_sesion(session, "bajo")
    assert referencia.model_id == "@cf/openai/gpt-oss-120b"


async def test_set_modelo_agente_guarda_el_modelo_y_rechaza_uno_fuera_de_catalogo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sin sesión de opencode viva (motor viejo en pruebas, ver conftest):
    ``set_modelo_agente`` solo tiene que guardar el modelo elegido en la
    sesión de Edecán -- y seguir validando contra el catálogo del IDE,
    igual que ``start_agent``/``AgentStartIn.model``."""
    project = tmp_path / "proyecto"
    project.mkdir()
    manager = _make_manager(tmp_path / "ide")
    workspace = _authorize(manager, project)

    async def fake_run(_self, **kwargs):
        kwargs["write_event"]("assistant_final", "listo")

    monkeypatch.setattr("edecan_companion.ide_workers_agent.WorkersIDEAgent.run", fake_run)

    primero = manager.start_agent(workspace["id"], "hola", conversation_id="conv-set-modelo")
    session_id = primero["session"]["id"]
    await _wait_until_not_running(manager, session_id)

    respuesta = manager.set_modelo_agente(session_id, "@cf/zai-org/glm-5.2")
    assert respuesta == {"model": "@cf/zai-org/glm-5.2"}
    assert manager.read(session_id, "agent", 0)["session"]["model"] == "@cf/zai-org/glm-5.2"

    with pytest.raises(ide_sessions_module.IDESessionError):
        manager.set_modelo_agente(session_id, "@cf/no-existe/inventado")


def _leer_modelo_opencode_en_vivo(manager: SessionManager, session_id: str) -> tuple[str, str]:
    """Lee el ``ModelRef`` REAL de la sesión de opencode -- de opencode, no
    del almacén de Edecán (encargo: "leelo de la sesión de opencode, no del
    almacén de Edecán"). Devuelve ``(providerID, id)``."""
    state = manager.read(session_id, "agent", 0)
    opencode_session_id = state["session"]["opencode_session_id"]
    workspace_root = manager.workspaces.root(str(state["session"]["workspace_id"]))
    assert manager.motor_opencode is not None

    async def _leer() -> Any:
        servidor = await manager.motor_opencode.servidor_para(workspace_root)  # type: ignore[union-attr]
        info = await servidor.obtener_sesion(opencode_session_id)
        return info.model

    modelo = manager._ejecutar_opencode(_leer())
    assert modelo is not None, "la sesión de opencode no trae ningún ModelRef asociado"
    return modelo.providerID, modelo.id


@requiere_credenciales_opencode
async def test_elegir_modelo_gana_y_cambiar_esfuerzo_no_lo_pisa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """El caso real que reportó el dueño, cerrado con opencode de verdad: se
    elige un modelo A explícito -- deliberadamente NI el modelo fuerte NI el
    ``modelo_alternativo`` del perfil ``ingenieria_software`` (ver
    ``config/modelos.yml``), para que el bug viejo lo hubiera reemplazado en
    CUALQUIER nivel, no solo en "bajo" -- y se recorren los tres niveles de
    esfuerzo. En cada uno, la sesión de opencode sigue en A."""
    monkeypatch.setenv("EDECAN_IDE_MOTOR", "opencode")
    project = tmp_path / "proyecto"
    project.mkdir()
    manager = _make_manager(tmp_path / "ide")
    workspace = _authorize(manager, project)
    modelo_a = "@cf/nvidia/nemotron-3-120b-a12b"

    resultado = manager.start_agent(
        workspace["id"],
        "Responde solo con la palabra: listo",
        conversation_id="conv-modelo-gana",
        model=modelo_a,
    )
    session_id = resultado["session"]["id"]
    try:
        await _wait_until_not_running_opencode(manager, session_id)

        proveedor, modelo_en_vivo = _leer_modelo_opencode_en_vivo(manager, session_id)
        assert proveedor == PROVEEDOR_WORKERS_AI_ID
        assert modelo_en_vivo == modelo_a, (
            "el modelo elegido al arrancar la conversación no llegó a la sesión de opencode "
            f"-- opencode tiene {modelo_en_vivo!r}"
        )

        for nivel in ("bajo", "medio", "alto"):
            manager.fijar_esfuerzo(session_id, nivel)
            _, modelo_en_vivo = _leer_modelo_opencode_en_vivo(manager, session_id)
            assert modelo_en_vivo == modelo_a, (
                f"cambiar el esfuerzo a «{nivel}» cambió el modelo de la sesión de opencode a "
                f"{modelo_en_vivo!r} -- el esfuerzo NO debe elegir el modelo, solo cuánto "
                "piensa el que la persona eligió (este es el bug real que reportó el dueño)"
            )

        # El almacén de Edecán tampoco perdió la elección.
        assert manager.read(session_id, "agent", 0)["session"]["model"] == modelo_a
    finally:
        manager.shutdown()


@requiere_credenciales_opencode
async def test_cambiar_modelo_con_la_sesion_viva_llega_a_opencode_de_inmediato(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Encargo, punto 4: "cambiar de modelo a mitad de sesión (el selector,
    con la sesión viva) tiene que aplicarse de inmediato". Se arranca con un
    modelo, se cambia con ``set_modelo_agente`` -- sin mandar ningún mensaje
    nuevo, la sesión de opencode ya existía -- y se comprueba, leyendo la
    sesión de opencode, que el cambio llegó ya, no en el turno siguiente."""
    monkeypatch.setenv("EDECAN_IDE_MOTOR", "opencode")
    project = tmp_path / "proyecto"
    project.mkdir()
    manager = _make_manager(tmp_path / "ide")
    workspace = _authorize(manager, project)
    modelo_inicial = "@cf/nvidia/nemotron-3-120b-a12b"
    modelo_nuevo = "@cf/meta/llama-4-scout-17b-16e-instruct"

    resultado = manager.start_agent(
        workspace["id"],
        "Responde solo con la palabra: listo",
        conversation_id="conv-cambio-modelo-vivo",
        model=modelo_inicial,
    )
    session_id = resultado["session"]["id"]
    try:
        await _wait_until_not_running_opencode(manager, session_id)
        _, modelo_en_vivo = _leer_modelo_opencode_en_vivo(manager, session_id)
        assert modelo_en_vivo == modelo_inicial

        respuesta = manager.set_modelo_agente(session_id, modelo_nuevo)
        assert respuesta == {"model": modelo_nuevo}

        proveedor, modelo_en_vivo = _leer_modelo_opencode_en_vivo(manager, session_id)
        assert proveedor == PROVEEDOR_WORKERS_AI_ID
        assert modelo_en_vivo == modelo_nuevo, (
            "set_modelo_agente no aplicó el cambio de inmediato -- la sesión de opencode "
            f"sigue en {modelo_en_vivo!r}"
        )
        assert manager.read(session_id, "agent", 0)["session"]["model"] == modelo_nuevo
    finally:
        manager.shutdown()
