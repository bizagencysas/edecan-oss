"""Sesiones persistentes de terminal y agentes locales.

Los procesos viven en el companion de escritorio, no en la conexión HTTP o
WebSocket del teléfono. Minimizar/cerrar la app móvil solo deja de leer
eventos; el proceso continúa y se puede rehidratar con ``list`` + ``read``.

Dirigir un turno en curso
-------------------------
Un mensaje que llega mientras el agente trabaja NO se rechaza: se encola en
la sesión y se le entrega al modelo ENTRE VUELTAS del ciclo
agente-herramientas (``ide_workers_agent.WorkersIDEAgent.run``), nunca a
mitad de una llamada a herramienta -- entregar a mitad significa un archivo
escrito a medias o un comando corriendo cuyo resultado ya no le importa a
nadie. En la vuelta siguiente entra como turno de usuario ANTES de la
próxima llamada al modelo, así el modelo lo lee junto con lo que ya venía
haciendo.

Contrato de eventos para la interfaz (los tres llevan el texto del mensaje,
y se emiten SIEMPRE en este orden por mensaje):
- ``user_queued``: se recibió y quedó en cola (su ``timestamp`` es el
  "cuándo se mandó").
- ``user_delivered``: el agente ya lo leyó, entre dos vueltas de su ciclo
  (su ``timestamp`` es el "cuándo se entregó").
- ``user_undelivered``: el turno terminó sin poder entregarlo y tampoco se
  pudo convertir en el turno siguiente. Nunca se descarta en silencio.
Cuando el turno termina con algo sin entregar y la sesión quedó en un
estado continuable, no hay ``user_undelivered``: el mensaje se convierte en
el turno siguiente (evento ``user`` normal, ver
``_entregar_pendientes_al_cerrar``).

Dirigir NO es cancelar: si la persona escribe "para", eso lo interpreta el
MODELO leyendo el mensaje. La cancelación de verdad es otra cosa y ya
existe aparte (``close()`` / acción ``ide_agent_cancel``).

La cola vive en memoria, como ``self.plans``: si el companion se reinicia a
mitad de turno, la sesión queda "interrupted" (ver ``_load``) y lo encolado
se pierde -- pero no en silencio, porque el evento ``user_queued`` sí quedó
persistido en el JSONL de la sesión.
"""

from __future__ import annotations

import asyncio
import codecs
import contextlib
import dataclasses
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from edecan_llm.task_router import modelo_ide_permitido
from pydantic import ValidationError

from edecan_companion import ide_modos, pty_compat
from edecan_companion.ide_bloques import validar_bloques
from edecan_companion.ide_busqueda_semantica import SemanticSearchService
from edecan_companion.ide_checkpoints import CheckpointStore, IDECheckpointError
from edecan_companion.ide_costos import analizar_tarea
from edecan_companion.ide_files import FileService, IDEFileError
from edecan_companion.ide_memoria import IDEMemoriaError, MemoriaStore
from edecan_companion.ide_modos import EsfuerzoStore
from edecan_companion.ide_opencode import ErrorOpencode, ServidorOpencode
from edecan_companion.ide_opencode_eventos import (
    EventoTraducido,
    TraductorDeTurno,
    traducir_permiso,
    traducir_pregunta,
)
from edecan_companion.ide_opencode_motor import (
    MotorOpencode,
    ReferenciaModeloEsfuerzo,
    modelo_para,
    referencia_para_modelo,
    variante_de_esfuerzo,
)
from edecan_companion.ide_opencode_permisos import (
    EventoBus,
    PuenteDePermisos,
    SolicitudPermiso,
    SolicitudPregunta,
)
from edecan_companion.ide_plan import IDEPlanError, Plan, PlanStore
from edecan_companion.ide_reglas import IDEReglasError, ProjectRules, load_project_rules
from edecan_companion.ide_reparto import (
    EstadoPaso,
    PasoReparto,
    PlanificadorReparto,
    ResultadoReparto,
    rutas_desde_texto,
)
from edecan_companion.ide_workers_agent import WorkersIDEAgent, build_failure_final
from edecan_companion.ide_workspaces import WorkspaceStore
from edecan_companion.platform_paths import reemplazar_con_reintentos

logger = logging.getLogger(__name__)

MAX_EVENTS_PER_SESSION = 2000
MAX_EVENT_TEXT_CHARS = 8_000
MAX_EVENTS_PER_READ = 500

# Estados en los que un turno de agente sigue ocupado: hay algo que cancelar
# (``close()``) y no se puede arrancar otro turno encima. "plan_pending" (ver
# ``_run_workers_agent``/``approve_plan``) cuenta como ocupado a propósito: si
# un mensaje nuevo pudiera colarse mientras un plan sigue sin aprobar, el gate
# de ``ide_plan.requires_plan`` sería decorativo -- el agente podría terminar
# tocando archivos en un turno distinto sin que la persona haya dicho que sí
# al plan pendiente.
#
# Ocupado ya NO significa "no me hables": un mensaje que llega con el turno en
# curso se encola y se le entrega entre vueltas (ver
# ``_AGENT_STEERABLE_STATUSES``, que es el subconjunto donde eso aplica).
_AGENT_BUSY_STATUSES = {"starting", "running", "plan_pending"}
# Subconjunto de lo de arriba para el gate de ``approve_plan``/``resume_plan``:
# esos dos métodos son precisamente lo que resuelve un "plan_pending" (o un
# plan "failed" a medias), así que NO pueden rechazarse a sí mismos por ese
# estado -- solo un turno de verdad en curso (``starting``/``running``) debe
# bloquearlos.
_AGENT_TURN_IN_PROGRESS_STATUSES = {"starting", "running"}
# Estados en los que un mensaje nuevo se ENCOLA en vez de rechazarse: hay un
# ciclo agente-herramientas vivo que va a pasar por el punto de entrega de
# ``_run_workers_agent`` (o, si es un plan aprobado, por el cierre de
# ``_run_plan_execution``). Coincide hoy con
# ``_AGENT_TURN_IN_PROGRESS_STATUSES`` y se nombra aparte porque responde a
# otra pregunta: aquel dice "no arranques otra cosa encima", este dice "hay
# alguien trabajando a quien se le puede hablar".
#
# "plan_pending" queda FUERA a propósito y sigue rechazándose: ahí no hay
# ningún ciclo corriendo que pueda leer la cola -- el turno está detenido
# esperando que la persona apruebe, edite o rechace el plan, y lo que la
# interfaz le está mostrando es justamente esa tarjeta. Encolar en ese
# estado sería prometer una entrega que no llegaría hasta que el plan se
# resuelva, y para entonces el mensaje se entregaría a un sub-agente de un
# paso concreto (``_ejecutar_paso_de_plan``), que es otro contexto. Mejor un
# error que nombra la acción que sí destraba la conversación.
_AGENT_STEERABLE_STATUSES = _AGENT_TURN_IN_PROGRESS_STATUSES
# Tope de mensajes esperando entrega en UN turno. Alguien pega diez ideas
# seguidas: las primeras cinco entran y la sexta recibe un "no" explícito con
# el motivo. Se rechaza el mensaje NUEVO y no se descarta uno viejo: los que
# ya están en cola la persona los vio confirmados ("en cola") y darlos de
# baja en silencio es exactamente el bug que este archivo viene a arreglar.
MAX_QUEUED_USER_MESSAGES = 5
# Estados en los que el turno anterior terminó de forma limpia: la sesión
# puede recibir el siguiente mensaje de la misma conversación sin perder
# contexto. "interrupted" (companion reiniciado a medio turno) queda fuera a
# propósito: esa sesión ya no es una continuación confiable y cae al
# respaldo de ``_prompt_with_conversation_context``.
_AGENT_CONTINUABLE_STATUSES = {"completed", "failed", "cancelled"}
# Subconjunto del anterior: estados en los que un mensaje que quedó encolado
# sin entregar SÍ se convierte solo en el turno siguiente (ver
# ``_entregar_pendientes_al_cerrar``). "cancelled" queda fuera a propósito:
# la persona apretó "detener", y arrancarle un turno nuevo en la cara --
# justo cuando pidió parar -- es lo contrario de lo que pidió. Ahí el mensaje
# se reporta como no entregado, con su texto a la vista para reenviarlo.
_AGENT_PROMOTABLE_STATUSES = {"completed", "failed"}
# Techo de contexto reinyectado por mensaje. 120k (el límite anterior) hacía
# que cada mensaje de una conversación fuera más lento que el anterior; con
# la sesión viva reusada como camino principal, esta cifra solo se usa en
# los dos caminos que aún pegan texto: la continuación en caliente (recorte
# a lo esencial) y el respaldo tras un reinicio del companion.
MAX_REINJECTED_CONTEXT_CHARS = 20_000

# --------------------------------------------------------------------------- #
# Motor opencode (por defecto -- ver docs/opencode-motor.md). El motor VIEJO
# (arriba, ``WorkersIDEAgent``) se conserva intacto detrás de
# ``EDECAN_IDE_MOTOR=viejo`` -- decisión ya tomada, no se re-abre.
# --------------------------------------------------------------------------- #

#: Perfil de ``config/modelos.yml`` que usa el IDE -- mismo que
#: ``ide_opencode_config.perfil_a_referencia_modelo`` usaría por defecto, pero
#: acá se pasa explícito a ``ide_opencode_motor.modelo_para`` porque además
#: necesitamos el nivel de esfuerzo (bajo/medio/alto), no solo el modelo.
_PERFIL_OPENCODE_IDE = "ingenieria_software"
#: Cuánto puede quedarse callado ``ServidorOpencode.eventos()`` antes de que
#: el turno reevalúe qué está pasando (¿terminó? ¿hay un permiso/pregunta
#: pendiente?) -- mismo orden de magnitud que usó la integración real de
#: ``test_ide_opencode.py`` (20s) para distinguir "el modelo piensa" de "el
#: servidor murió a media transmisión".
#:
#: YA NO es la vía PRINCIPAL por la que un permiso/pregunta pendiente llega
#: al hilo -- eso lo hace ahora ``_vigilar_bus_para_sesion`` en cuanto ve la
#: solicitud en el bus global (normalmente por debajo de 1s, ver el test
#: ``test_permiso_pendiente_llega_por_el_bus_en_menos_de_2s``). Esta
#: constante se queda como RESPALDO para lo que ese aviso no cubra -- una
#: conexión al bus que se cayó, una ventana de red perdida, cualquier
#: solicitud que igual haya quedado huérfana -- así que bajarla a 0 sería
#: cambiar un fallo (25s de silencio) por otro (ningún respaldo si el bus
#: falla). Ver ``_leer_stream_sesion_o_avisar_permiso``.
_TIEMPO_INACTIVIDAD_EVENTOS_OPENCODE = 25.0
#: Tope TOTAL esperando que una PERSONA responda una pregunta del agente
#: (``question.v2.asked``) antes de cerrar el turno igual -- hay una acción
#: real para responderla (``SessionManager.responder_pregunta_agente``), así
#: que vale la pena esperar más que el silencio normal entre eventos. 20
#: minutos es una cifra propia, deliberadamente generosa pero acotada: nunca
#: es un cuelgue mudo, siempre hay un aviso en el hilo y un cierre honesto si
#: se agota.
_TIEMPO_MAX_ESPERA_PREGUNTA_OPENCODE = 1200.0
#: Mismo criterio y mismo valor que ``_TIEMPO_MAX_ESPERA_PREGUNTA_OPENCODE``, pero para un
#: permiso (``permission.v2.asked``) que quedó en ``pedir_aprobacion`` -- antes de esta ronda no
#: había ninguna acción real para concederlo, así que se rechazaba solo (ver el fallo real que
#: cierra esta constante en el docstring de ``_consumir_turno_opencode``); ahora sí existe
#: (``SessionManager.responder_permiso_agente``), así que vale la pena esperar igual que con una
#: pregunta, en vez de rechazar de inmediato.
_TIEMPO_MAX_ESPERA_PERMISO_OPENCODE = 1200.0
#: HALLAZGO REAL de un verificador (no una sospecha): contra opencode
#: ``1.17.18`` corriendo de verdad, el stream POR SESIÓN
#: (``GET /session/{id}/event``) a veces se cierra solo -- ``StopAsyncIteration``
#: limpio, SIN ``TimeoutError``, a los pocos milisegundos de un evento
#: ``tool.called`` -- mucho antes de que el turno haya terminado de verdad
#: (el archivo pedido ya estaba en disco cuando esto pasó). Antes de esta
#: ronda, ``_leer_stream_sesion_una_vez`` trataba ese cierre exactamente
#: igual que un silencio real de 25s: si no había ningún permiso/pregunta
#: pendiente en ese instante, el turno se daba por terminado y se cerraba
#: "failed" con un mensaje inventado ("no dejó una respuesta final") aunque
#: el agente siguiera trabajando. Lo mismo le pasa a un
#: ``ErrorOpencode``/``httpx.TransportError`` a mitad de lectura ("se perdió
#: la conexión SSE ... peer closed connection sin terminar el cuerpo") --
#: tampoco es una señal de que el turno acabó, es la conexión la que se cayó.
#: Estas dos constantes acotan cuántas veces (y por cuánto tiempo total) se
#: reconecta el stream de sesión ANTES de rendirse y dejar que quien llama
#: decida con lo que sepa -- nunca reconexión infinita (si opencode de verdad
#: murió, hay que decirlo, no colgarse mudo para siempre).
_MAX_RECONEXIONES_STREAM_SESION = 6
_ESPERA_ENTRE_RECONEXIONES_STREAM_SESION = 1.0
#: Mismo formato EXACTO que escribe ``ide_opencode_eventos.TraductorDeTurno``
#: para el evento ``file`` (y que el motor viejo también respeta, ver
#: ``ide_contexto._PATRON_ARCHIVO``) -- se redeclara acá en vez de importar
#: esa constante privada de ``ide_contexto.py`` (módulo fuera de mi alcance
#: en esta ronda).
_PATRON_ARCHIVO_OPENCODE = re.compile(r"^Archivo actualizado: (?P<ruta>.+)$")
#: Los dos tipos del bus global (``GET /api/event``, ver hallazgo 2 del
#: docstring de ``ide_opencode_permisos``) que le importan a
#: ``_vigilar_bus_para_sesion``: son los ÚNICOS dos que
#: ``_consumir_turno_opencode`` puede llegar a mostrar como tarjeta
#: (``agent_permission``/``agent_question``, vía ``traducir_permiso``/
#: ``traducir_pregunta``). Cualquier otro tipo del bus (``permission.v2.replied``,
#: ``question.v2.replied``, etc.) se ignora acá a propósito -- esos son
#: informativos y no corresponden a nada que este turno tenga que despertar
#: a mostrar.
_TIPOS_BUS_QUE_DESPIERTAN_EL_TURNO: frozenset[str] = frozenset(
    {"permission.v2.asked", "question.v2.asked"}
)


class IDESessionError(ValueError):
    """Solicitud de sesión inválida o sesión inexistente."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clean_title(value: Any, fallback: str) -> str:
    if value is None:
        return fallback
    title = str(value).strip()
    if not title or len(title) > 160 or any(ord(char) < 32 for char in title):
        raise IDESessionError("El título de la sesión no es válido.")
    return title


class Session:
    def __init__(
        self,
        manager: SessionManager,
        metadata: dict[str, Any],
    ) -> None:
        self.manager = manager
        self.metadata = metadata
        # Solo lo usan las sesiones "terminal" (ver ``SessionManager.start_terminal``):
        # el pseudo-terminal vivo, detrás de la frontera única de
        # ``pty_compat`` -- este archivo ya no importa ``pty`` ni ramifica por
        # ``os.name`` para arrancarlo/leerlo/escribirlo/cerrarlo. Las sesiones
        # "agent" nunca lo asignan (corren en un hilo, no en un subproceso).
        self.terminal: pty_compat.TerminalPTY | None = None
        self.events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS_PER_SESSION)
        self.cursor = 0
        self._lock = threading.RLock()
        self.cancel_event = threading.Event()
        # Mensajes que la persona mandó mientras el agente trabajaba, en el
        # orden en que los escribió. SIN ``maxlen``: un deque que descarta
        # solo al llenarse perdería mensajes en silencio, y el tope se
        # aplica a mano en ``enqueue_user_message`` para poder decir que no.
        self.pending_messages: deque[dict[str, Any]] = deque()
        # Serializa "decidir qué hacer con un mensaje nuevo" (encolar vs.
        # abrir un turno) contra "cerrar el turno y ver qué quedó en cola".
        # Sin esto hay una ventana real entre que el turno marca su estado
        # final y que drena la cola: un mensaje que entra justo ahí podría
        # arrancar un turno mientras el cierre arranca otro, y dos turnos
        # escribiendo la misma sesión corrompen el hilo. Es RLock porque el
        # camino de cierre vuelve a entrar por ``_continue_agent_session``.
        self._turn_lock = threading.RLock()
        self._load_events()

    @property
    def id(self) -> str:
        return str(self.metadata["id"])

    def _event_path(self) -> Path:
        return self.manager.events_dir / f"{self.id}.jsonl"

    def _load_events(self) -> None:
        try:
            with self._event_path().open(encoding="utf-8") as file:
                lines = deque(file, maxlen=MAX_EVENTS_PER_SESSION)
        except OSError:
            return
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and isinstance(event.get("cursor"), int):
                self.events.append(event)
                self.cursor = max(self.cursor, int(event["cursor"]))

    def append(
        self,
        event_type: str,
        text: str,
        *,
        stream: str | None = None,
        presentation: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Escribe un evento en el hilo (memoria + JSONL) y lo devuelve.

        ``presentation`` es el ÚNICO camino por el que un bloque rico (tabla,
        gráfica: ver ``ide_bloques.py``) llega al hilo, al disco y al
        teléfono. Todo lo que entra por ahí pasa antes por
        ``validar_bloques``, que solo deja pasar lo que se puede dibujar: un
        resultado de herramienta -- texto que el modelo produjo o leyó de un
        archivo del repo -- nunca puede acuñar UI por tener la forma correcta.

        ``text`` sigue siendo obligatorio incluso cuando hay bloques, y no es
        una formalidad: el historial que se le reinyecta al modelo, el
        ``/export`` a Markdown, el estimador de costos y cualquier cliente que
        todavía no dibuje bloques leen ``text`` y nada más. Un bloque cuyo
        texto equivalente esté vacío no se escribe -- se perdería en silencio
        en todas esas superficies.
        """
        if not text:
            return {}
        with self._lock:
            self.cursor += 1
            event: dict[str, Any] = {
                "cursor": self.cursor,
                "type": event_type,
                "text": text[:MAX_EVENT_TEXT_CHARS],
                "timestamp": _now(),
            }
            if stream is not None:
                event["stream"] = stream
            bloques = validar_bloques(presentation) if presentation else []
            if bloques:
                event["presentation"] = bloques
            self.events.append(event)
            self.manager.events_dir.mkdir(parents=True, exist_ok=True)
            try:
                with self._event_path().open("a", encoding="utf-8") as file:
                    file.write(json.dumps(event, ensure_ascii=False) + "\n")
                try:
                    self._event_path().chmod(0o600)
                except OSError:
                    pass
            except OSError:
                pass
            return dict(event)

    def enqueue_user_message(self, text: str) -> dict[str, Any]:
        """Encola un mensaje para el turno que ya está corriendo.

        Deja el evento ``user_queued`` en el hilo de la sesión en el acto: si
        la persona no ve que su mensaje llegó, lo va a mandar otra vez. La
        posición devuelta es 1-based y es la que la API le responde a quien
        mandó el mensaje.
        """
        with self._turn_lock:
            if len(self.pending_messages) >= MAX_QUEUED_USER_MESSAGES:
                raise IDESessionError(
                    f"Ya hay {MAX_QUEUED_USER_MESSAGES} mensajes esperando a que el agente "
                    "los lea. Espera a que lea los que ya mandaste, o cancela el turno si "
                    "quieres empezar de nuevo."
                )
            item = {"id": str(uuid.uuid4()), "text": text, "queued_at": _now()}
            self.pending_messages.append(item)
            position = len(self.pending_messages)
            self.append("user_queued", text)
        return {
            "id": item["id"],
            "position": position,
            "queued_at": item["queued_at"],
            "pending": position,
            "max_pending": MAX_QUEUED_USER_MESSAGES,
        }

    def drain_user_messages(self) -> list[dict[str, Any]]:
        """Saca TODOS los mensajes encolados, en el orden en que llegaron.

        Todos, no solo el último: tres ideas escritas seguidas son tres datos
        distintos ("y de paso arregla el test", "usa la API nueva", "no toques
        el schema"), no tres versiones de la misma. Quedarse con la última
        descartaría contenido que la persona escribió y ya vio confirmado como
        recibido. Si de verdad una contradice a otra, el que sabe resolverlo es
        el modelo leyéndolas en orden, no este archivo adivinando.
        """
        with self._turn_lock:
            pendientes = list(self.pending_messages)
            self.pending_messages.clear()
            return pendientes

    def public(self) -> dict[str, Any]:
        return dict(self.metadata)

    def read(self, cursor: int) -> dict[str, Any]:
        if cursor < 0:
            raise IDESessionError("El cursor no puede ser negativo.")
        with self._lock:
            oldest_memory_cursor = int(self.events[0]["cursor"]) if self.events else self.cursor + 1
            if cursor < oldest_memory_cursor - 1:
                rows = self._read_persisted(cursor, limit=MAX_EVENTS_PER_READ)
            else:
                rows = [dict(event) for event in self.events if int(event["cursor"]) > cursor][
                    :MAX_EVENTS_PER_READ
                ]
            next_cursor = rows[-1]["cursor"] if rows else max(cursor, self.cursor)
            return {
                "session": self.public(),
                "events": rows,
                "next_cursor": next_cursor,
                "has_more": next_cursor < self.cursor,
            }

    def _read_persisted(self, cursor: int, *, limit: int) -> list[dict[str, Any]]:
        """Pagina el historial append-only sin cargarlo completo en memoria.

        El deque mantiene solo la cola caliente para streaming. El JSONL es la
        fuente duradera y nunca se compacta silenciosamente, por lo que cerrar
        el iPhone o superar miles de eventos no destruye el contexto anterior.
        """

        rows: list[dict[str, Any]] = []
        try:
            with self._event_path().open(encoding="utf-8") as file:
                for line in file:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    event_cursor = event.get("cursor")
                    if not isinstance(event_cursor, int) or event_cursor <= cursor:
                        continue
                    rows.append(event)
                    if len(rows) >= limit:
                        break
        except OSError:
            return []
        return rows


class _BucleOpencode:
    """Hilo dedicado con su propio event loop asyncio, vivo mientras dure el
    companion (o hasta ``detener()``).

    ``MotorOpencode`` necesita un loop PERSISTENTE porque
    ``PuenteDePermisos.escuchar()`` corre como tarea de fondo que tiene que
    sobrevivir ENTRE turnos -- la regla dura de ``docs/opencode-motor.md``
    §3 ("el puente arranca antes del primer prompt, siempre, y se queda
    escuchando") solo se sostiene si esa tarea no muere cuando el turno que
    la disparó termina. El patrón que usa el motor VIEJO
    (``asyncio.run(...)`` por turno, ver ``_run_workers_agent``) crea y
    destruye un event loop nuevo cada vez -- perfecto para ese motor (no
    tiene nada que sobreviva al turno), letal para este: la tarea del
    puente moriría cancelada en cuanto ``asyncio.run()`` cerrara el loop al
    final del primer turno, y el turno siguiente se colgaría en su primera
    edición sin que nadie lo note (exactamente el hallazgo de §3).
    """

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._listo = threading.Event()
        self._hilo = threading.Thread(
            target=self._correr, daemon=True, name="edecan-opencode-loop"
        )
        self._hilo.start()
        self._listo.wait()

    def _correr(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._listo.set()
        self._loop.run_forever()

    def ejecutar(self, corutina: Any, *, timeout: float | None = None) -> Any:
        """Agenda ``corutina`` en el loop persistente y bloquea al hilo
        LLAMADOR (nunca al loop en sí) hasta que termine. Cualquier
        excepción real de la corutina se re-lanza acá tal cual -- nunca se
        traga un fallo de opencode en silencio."""

        futuro = asyncio.run_coroutine_threadsafe(corutina, self._loop)
        return futuro.result(timeout)

    def detener(self) -> None:
        if self._loop.is_closed():
            return
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._hilo.join(timeout=5.0)


class SessionManager:
    def __init__(
        self, state_dir: Path, workspaces: WorkspaceStore, *, motor: str | None = None
    ) -> None:
        self.state_dir = state_dir
        self.events_dir = state_dir / "ide-session-events"
        self.metadata_path = state_dir / "ide-sessions.json"
        self.workspaces = workspaces
        self.files = FileService(workspaces)
        self.workers_agent = WorkersIDEAgent(workspaces, self.files)
        # Un punto de control por TURNO (no por sesión): cada mensaje que el
        # agente procesa dentro de una conversación reusada obtiene su propio
        # checkpoint, así "deshacer" en la UI puede ofrecerse turno a turno en
        # vez de todo-o-nada sobre el hilo completo. Ver `_run_workers_agent`.
        self.checkpoints = CheckpointStore(state_dir, workspaces)
        # 2.2/2.3 del plan de paridad: índice semántico y memoria de proyecto
        # viven un ciclo de vida completo por companion (igual que
        # ``checkpoints``), no por turno -- ver ``_memory_block_for`` y el
        # tool ``buscar_semanticamente``/``recordar_nota_proyecto`` de
        # ``ide_workers_agent.py``. ``IDERuntime`` reusa estas MISMAS
        # instancias (``self.sessions.semantic`` / ``self.sessions.memoria``)
        # para los endpoints del panel de búsqueda y de memoria -- nunca se
        # crea una segunda instancia por separado.
        self.semantic = SemanticSearchService(workspaces, state_dir)
        self.memoria = MemoriaStore(state_dir, workspaces)
        # Cabo 1 del encargo de integración: aprobación previa (``ide_plan``)
        # + reparto/sub-agentes (``ide_reparto``/``ide_equipo``) conectados al
        # ciclo real del agente. ``self.plans`` es puro estado en memoria (no
        # sobrevive un reinicio del companion, igual que ``ide_plan.PlanStore``
        # fue diseñado) -- un plan propuesto que no se llegó a aprobar antes
        # de reiniciar simplemente se pierde, y la sesión (marcada
        # "interrupted" por ``_load``) cae al respaldo normal de contexto.
        # ``_plan_routes``/``_plan_progress`` son el estado adicional que ESTE
        # módulo necesita y que ``ide_plan.Plan`` no modela (rutas por paso
        # para el reparto, y el resultado real de la última corrida para
        # poder retomarla sin repetir lo ya hecho -- ver ``resume_plan``).
        self.plans = PlanStore()
        # Nivel de ``/effort`` vigente por sesión. Vive ACÁ (no una segunda
        # instancia en ``IDERuntime``) por la misma razón que ``semantic``/
        # ``memoria`` de arriba: el ciclo real del agente (``_run_workers_agent``,
        # ``_ejecutar_paso_de_plan``) necesita la MISMA instancia que la UI
        # toca por ``ide_modo_get``/``ide_modo_set`` -- dos ``EsfuerzoStore``
        # separados significaría que cambiar el nivel desde la interfaz nunca
        # llega al agente. ``IDERuntime.effort_store`` reusa esta instancia
        # (``self.sessions.effort_store``) en vez de crear la suya.
        self.effort_store = EsfuerzoStore()
        # --- Motor opencode (por defecto -- ver docs/opencode-motor.md) --- #
        # ``motor`` (parámetro del constructor) gana sobre ``EDECAN_IDE_MOTOR``
        # (variable de entorno), que a su vez gana sobre "opencode" (el
        # default que pidió el dueño). Se resuelve en CADA turno
        # (``_motor_vigente``), no una sola vez acá, para que un test pueda
        # fijar la variable de entorno con ``monkeypatch`` sin reconstruir el
        # ``SessionManager``.
        self._motor_forzado = motor
        # ``ModoAgenteStore`` (``ide_modos.py``) ya existía completo y sin
        # cable (docs/opencode-motor.md §4.3): es la fuente de verdad de los
        # 4 modos (manual/aceptar_ediciones/plan/auto) que ahora sí frenan de
        # verdad, vía ``PuenteDePermisos``. Se pasa su ``.obtener`` directo
        # como ``obtener_modo`` -- funciona sin traducir nada porque
        # ``ide_modos.ModoAgente`` e ``ide_opencode_permisos.ModoAgente`` son
        # dos ``StrEnum`` con los mismos valores por diseño (ver el docstring
        # de ``ide_opencode_permisos.py``): un ``ide_modos.ModoAgente.MANUAL``
        # ES un ``str`` que vale "manual", y eso es exactamente lo que
        # ``ide_opencode_permisos._parse_modo`` sabe leer.
        self.modo_agente_store = ide_modos.ModoAgenteStore()
        # Perezosos a propósito: un ``SessionManager`` que nunca arranca un
        # turno sobre opencode (motor "viejo", o un proceso que solo abre
        # terminales) no paga el costo de un hilo + event loop de más. Ver
        # ``_asegurar_motor_opencode``.
        self._bucle_opencode: _BucleOpencode | None = None
        self.motor_opencode: MotorOpencode | None = None
        self._plan_routes: dict[str, list[tuple[str, ...] | None]] = {}
        self._plan_progress: dict[str, dict[str, EstadoPaso]] = {}
        self._lock = threading.RLock()
        self._sessions: dict[str, Session] = {}
        self._mcp_pending: dict[tuple[str, str], dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        try:
            payload = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        rows = payload.get("sessions", []) if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            rows = []
        changed = False
        for raw in rows:
            if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
                continue
            metadata = dict(raw)
            if metadata.get("status") in {"starting", "running"}:
                metadata["status"] = "interrupted"
                metadata["ended_at"] = _now()
                metadata["exit_code"] = None
                changed = True
            session = Session(self, metadata)
            self._sessions[session.id] = session
        if changed:
            self._save()

    def _save(self) -> None:
        with self._lock:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            temp = self.metadata_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
            payload = {
                "version": 1,
                "sessions": [session.public() for session in self._sessions.values()],
            }
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                temp.chmod(0o600)
            except OSError:
                pass
            reemplazar_con_reintentos(temp, self.metadata_path)

    def _get(self, session_id: str, kind: str | None = None) -> Session:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None or (kind is not None and session.metadata.get("kind") != kind):
            raise IDESessionError("Sesión no encontrada.")
        return session

    # ------------------------------------------------------------------ #
    # Motor opencode -- ver docs/opencode-motor.md. Estos tres métodos son
    # el ÚNICO lugar del archivo que decide qué motor corre y cómo se habla
    # con el loop persistente; todo lo demás llama a estos.
    # ------------------------------------------------------------------ #

    _MOTORES_IDE_VALIDOS = frozenset({"opencode", "viejo"})

    def _motor_vigente(self) -> str:
        """"viejo" solo si se pidió EXPLÍCITO (constructor o
        ``EDECAN_IDE_MOTOR``); cualquier otro valor, incluido no fijar nada,
        es "opencode" -- el default que pidió el dueño. Un valor irreconocible
        también cae a "opencode" (fallar cerrado hacia el motor nuevo, no
        hacia uno viejo silencioso)."""
        bruto = (
            self._motor_forzado
            if self._motor_forzado is not None
            else os.environ.get("EDECAN_IDE_MOTOR")
        )
        motor = (bruto or "opencode").strip().casefold()
        return motor if motor in self._MOTORES_IDE_VALIDOS else "opencode"

    def _modo_para_opencode_session(self, opencode_session_id: str) -> ide_modos.ModoAgente:
        """El callback ``obtener_modo`` que ``PuenteDePermisos``/``MotorOpencode``
        invocan de verdad -- y lo invocan con el ID DE SESIÓN DE OPENCODE
        (``SolicitudPermiso.sessionID``), NO con el ``id`` del ``Session`` de
        Edecán. ``ModoAgenteStore`` (y todo lo que este archivo expone,
        ``set_modo_agente`` incluido) está keyed por el id de Edecán, que es
        el único que la interfaz/API conoce -- así que acá se traduce: se
        busca qué ``Session`` de Edecán tiene guardado este
        ``opencode_session_id`` y se lee el modo de ESA.

        Sin esta traducción, ``ModoAgenteStore.obtener(id_de_opencode)``
        nunca encuentra nada (esa clave nunca se fijó) y cae siempre al
        default MANUAL -- se comprobó en vivo: el turno se quedaba pidiendo
        aprobación de "edit" aunque la sesión ya se hubiera inicializado en
        Auto, porque el Auto se había guardado bajo la clave equivocada.
        """
        with self._lock:
            candidatos = [
                session
                for session in self._sessions.values()
                if session.metadata.get("opencode_session_id") == opencode_session_id
            ]
        if not candidatos:
            # Sesión de opencode que este manager no reconoce (o una
            # ventana muy angosta justo antes de guardar el id, ver
            # ``_turno_opencode``): cae al modo por defecto de
            # ``ModoAgenteStore`` -- el más conservador, nunca el más
            # permisivo, ante la duda.
            return self.modo_agente_store.obtener(opencode_session_id)
        return self.modo_agente_store.obtener(candidatos[0].id)

    def _asegurar_motor_opencode(self) -> MotorOpencode:
        with self._lock:
            if self._bucle_opencode is None:
                self._bucle_opencode = _BucleOpencode()
            if self.motor_opencode is None:
                self.motor_opencode = MotorOpencode(obtener_modo=self._modo_para_opencode_session)
            return self.motor_opencode

    def _ejecutar_opencode(self, corutina: Any, *, timeout: float | None = None) -> Any:
        """Arranca (si hace falta) el motor+loop de opencode y corre
        ``corutina`` sobre él, bloqueando al hilo llamador. Punto de entrada
        único para toda operación async contra opencode desde este archivo."""
        self._asegurar_motor_opencode()
        assert self._bucle_opencode is not None
        return self._bucle_opencode.ejecutar(corutina, timeout=timeout)

    def _objetivo_turno(self) -> Callable[..., None]:
        """Qué función corre un turno de agente, según el motor vigente EN
        ESTE INSTANTE (no se cachea: ``EDECAN_IDE_MOTOR`` puede cambiar entre
        un turno y el siguiente, típicamente en un test). Mismo contrato de
        argumentos posicionales para las dos -- ver ``_run_workers_agent`` y
        ``_run_opencode_agent``."""
        if self._motor_vigente() == "viejo":
            return self._run_workers_agent
        return self._run_opencode_agent

    def list(self, kind: str, workspace_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            sessions = [
                session.public()
                for session in self._sessions.values()
                if session.metadata.get("kind") == kind
                and (workspace_id is None or session.metadata.get("workspace_id") == workspace_id)
            ]
        sessions.sort(key=lambda row: str(row.get("started_at", "")), reverse=True)
        return {"sessions": sessions}

    def read(self, session_id: str, kind: str, cursor: int) -> dict[str, Any]:
        return self._get(session_id, kind).read(cursor)

    def _register(
        self,
        *,
        kind: str,
        workspace_id: str,
        title: str,
        extra: dict[str, Any],
    ) -> Session:
        workspace = self.workspaces.get(workspace_id)
        session_id = str(uuid.uuid4())
        metadata = {
            "id": session_id,
            "kind": kind,
            "workspace_id": workspace_id,
            "workspace_name": workspace["name"],
            "title": title,
            "status": "starting",
            "started_at": _now(),
            "ended_at": None,
            "exit_code": None,
            **extra,
        }
        session = Session(self, metadata)
        with self._lock:
            self._sessions[session_id] = session
            self._save()
        return session

    @staticmethod
    def _validate_argv(raw: Any) -> list[str]:
        if not isinstance(raw, list) or not raw:
            raise IDESessionError("argv debe ser una lista no vacía.")
        argv: list[str] = []
        for item in raw:
            if not isinstance(item, str) or not item or "\x00" in item:
                raise IDESessionError("argv contiene un argumento inválido.")
            argv.append(item)
        executable = argv[0] if os.path.isabs(argv[0]) else shutil.which(argv[0])
        if not executable:
            raise IDESessionError(f"No se encontró el ejecutable: {argv[0]}.")
        argv[0] = executable
        return argv

    @classmethod
    def _default_terminal_argv(cls) -> list[str]:
        if os.name == "nt":
            # Decisión del plan (docs/edecan-windows.md §"Shell por defecto en
            # Windows"): pwsh.exe -> powershell.exe -> %COMSPEC%, con
            # ``-NoLogo -NoProfile`` como espejo de "zsh -f" (arranque limpio,
            # sin perfiles ni banners de usuario). ``shutil.which`` es lo
            # portable -- nada de rutas fijas tipo ``C:\Windows\System32``.
            for candidate in ("pwsh.exe", "pwsh", "powershell.exe", "powershell"):
                found = shutil.which(candidate)
                if found:
                    return cls._validate_argv([found, "-NoLogo", "-NoProfile"])
            comspec = os.environ.get("COMSPEC") or shutil.which("cmd.exe") or shutil.which("cmd")
            if not comspec:
                raise IDESessionError("No se encontró ninguna shell disponible en Windows.")
            return cls._validate_argv([comspec])
        shell = os.environ.get("SHELL")
        shell = shell or ("/bin/zsh" if Path("/bin/zsh").exists() else "/bin/sh")
        shell_name = Path(shell).name.lower()
        if shell_name == "zsh":
            return cls._validate_argv([shell, "-f"])
        if shell_name == "bash":
            return cls._validate_argv([shell, "--noprofile", "--norc"])
        if shell_name == "fish":
            return cls._validate_argv([shell, "--no-config"])
        return cls._validate_argv([shell])

    def start_terminal(
        self, workspace_id: str, raw_argv: Any = None, title: Any = None
    ) -> dict[str, Any]:
        cwd = self.workspaces.root(workspace_id)
        if raw_argv is None:
            argv = self._default_terminal_argv()
        else:
            argv = self._validate_argv(raw_argv)
        process_environment = os.environ.copy()
        process_environment.setdefault("TERM", "xterm-256color")
        process_environment.setdefault("COLORTERM", "truecolor")
        process_environment["PS1"] = "› "
        process_environment["PROMPT"] = "› "
        process_environment["RPROMPT"] = ""
        session = self._register(
            kind="terminal",
            workspace_id=workspace_id,
            title=_clean_title(title, "Terminal"),
            extra={
                "command": [argv[0]]
                if len(argv) == 1
                else [argv[0], f"<{len(argv) - 1} argumentos omitidos>"]
            },
        )
        try:
            # Toda la mecánica de "abrir un pseudo-terminal, conectarle
            # ``argv`` y quedarme con un mango para leer/escribir/cerrar"
            # vive detrás de ``pty_compat`` -- este método ya no sabe si por
            # debajo hay ``pty.openpty()`` (POSIX) o ConPTY vía ``pywinpty``
            # (Windows), ni tiene que ramificar por ``os.name`` para elegir.
            try:
                session.terminal = pty_compat.abrir_pty(
                    argv, cwd=str(cwd), env=process_environment
                )
            except (pty_compat.PTYError, NotImplementedError) as exc:
                # Se traduce a ``IDESessionError`` porque es lo que el
                # despachador de acciones (``ide_runtime.py``) sabe atrapar y
                # convertir en una respuesta ``{"ok": False, "error": ...}``
                # en vez de un 500 sin forma -- ver la nota de integración en
                # el docstring de ``pty_compat.py``.
                raise IDESessionError(f"No se pudo iniciar la terminal: {exc}") from exc
            reader = threading.Thread(
                target=self._read_pty,
                args=(session,),
                daemon=True,
                name=f"edecan-terminal-{session.id}",
            )
            session.metadata["status"] = "running"
            session.append("status", "Terminal iniciada.")
            self._save()
            reader.start()
            return {"session": session.public()}
        except Exception:
            session.metadata["status"] = "failed"
            session.metadata["ended_at"] = _now()
            self._save()
            raise

    def _finish(self, session: Session) -> None:
        # ``esperar()`` no manda ninguna señal (el hijo ya terminó por su
        # cuenta -- se llega aquí tras observar EOF en ``_read_pty``) y
        # libera los recursos del pseudo-terminal al volver; ver el
        # contrato en ``pty_compat.TerminalPTY.esperar``.
        return_code = session.terminal.esperar() if session.terminal is not None else None
        with session._lock:
            deliberately_closed = session.metadata["status"] in {"cancelled", "closed"}
            if not deliberately_closed:
                session.metadata["status"] = "completed" if return_code == 0 else "failed"
            session.metadata["exit_code"] = return_code
            session.metadata["ended_at"] = _now()
        if not deliberately_closed:
            session.append("exit", f"Proceso finalizado con código {return_code}.")
        self._save()

    def _read_pty(self, session: Session) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        assert session.terminal is not None
        while True:
            chunk = session.terminal.leer(4096)
            if not chunk:
                break
            text = decoder.decode(chunk)
            if text:
                session.append("output", text, stream="stdout")
        tail = decoder.decode(b"", final=True)
        if tail:
            session.append("output", tail, stream="stdout")
        self._finish(session)

    def input_terminal(self, session_id: str, data: str) -> dict[str, Any]:
        session = self._get(session_id, "terminal")
        if session.metadata["status"] != "running" or session.terminal is None:
            raise IDESessionError("La terminal ya no está activa.")
        if not isinstance(data, str) or not data or len(data) > 64_000:
            raise IDESessionError("La entrada de terminal no es válida.")
        encoded = data.encode("utf-8")
        try:
            session.terminal.escribir(encoded)
        except pty_compat.PTYError as exc:
            raise IDESessionError(f"No se pudo escribir en la terminal: {exc}") from exc
        return {"accepted": True, "bytes": len(encoded)}

    def close(self, session_id: str, kind: str) -> dict[str, Any]:
        session = self._get(session_id, kind)
        if kind == "agent" and session.terminal is None:
            # Bajo ``_turn_lock``: cancelar tiene que ganarle a la promoción de
            # lo que quedó en cola. Sin este candado, un "detener" que cae
            # justo mientras el turno se cierra podría quedar pisado por el
            # turno siguiente que ese cierre estaba arrancando (que además
            # limpia ``cancel_event``), y la persona vería al agente seguir
            # trabajando después de haberle dicho que pare.
            opencode_session_id: str | None = None
            with session._turn_lock:
                session.cancel_event.set()
                if session.metadata.get("status") in _AGENT_BUSY_STATUSES:
                    session.metadata["status"] = "cancelled"
                    session.metadata["ended_at"] = _now()
                    session.append("status", "Sesión cancelada.")
                    self._save()
                raw = session.metadata.get("opencode_session_id")
                if isinstance(raw, str) and raw:
                    opencode_session_id = raw
            if opencode_session_id is not None:
                # Además de la señal local (``cancel_event``, que
                # ``_turno_opencode`` revisa entre eventos), se le pide a
                # opencode que interrumpa la generación YA -- no hace falta
                # esperar a que el bucle note la señal en su próximo evento.
                # Fuera de ``_turn_lock`` a propósito: es una llamada de red
                # y el hilo del turno también necesita ese candado en su
                # cierre. Si esto falla (servidor caído, red), la
                # cancelación local ya quedó registrada arriba -- el
                # "detener" de la persona no se pierde.
                with contextlib.suppress(Exception):
                    self._ejecutar_opencode(
                        self._interrumpir_opencode(session, opencode_session_id), timeout=10.0
                    )
            return {"session": session.public()}
        if session.terminal is not None and session.terminal.codigo_salida is None:
            session.metadata["status"] = "cancelled" if kind == "agent" else "closed"
            message = "Sesión cancelada." if kind == "agent" else "Terminal cerrada."
            session.append("status", message)
            self._save()
            # ``cerrar()`` ya hace el cierre cooperativo con escalamiento
            # (TERM/``taskkill /T`` -> espera -> KILL/``taskkill /T /F``) que
            # antes vivía acá mismo, ramificado por ``os.name``. Ver
            # ``pty_compat.TerminalPTY.cerrar``.
            session.terminal.cerrar()
        return {"session": session.public()}

    def shutdown(self) -> None:
        """Cierra procesos activos durante un cierre normal del companion."""

        with self._lock:
            active = [
                (session.id, str(session.metadata.get("kind")))
                for session in self._sessions.values()
                if session.metadata.get("status") in {"starting", "running"}
            ]
        for session_id, kind in active:
            with contextlib.suppress(IDESessionError, OSError):
                self.close(session_id, kind)
        # Apaga TODOS los servidores opencode + puentes que este manager haya
        # arrancado (uno por workspace, ver ``MotorOpencode``), y solo
        # después el hilo del loop persistente -- si nunca se usó opencode en
        # este proceso, los dos siguen en ``None`` y no hay nada que apagar.
        if self._bucle_opencode is not None and self.motor_opencode is not None:
            with contextlib.suppress(Exception):
                self._bucle_opencode.ejecutar(self.motor_opencode.detener_todo(), timeout=15.0)
            self._bucle_opencode.detener()

    def _project_rules_for(self, workspace_id: str) -> ProjectRules | None:
        """Reglas del repo (``AGENTS.md``/``CLAUDE.md``/``.cursorrules``) para
        inyectar en el prompt de sistema del turno -- 1.3 del plan de
        paridad. Nunca revienta un turno por esto: un workspace inválido o un
        error de lectura real solo deja de agregar reglas, no cancela la
        tarea que el usuario sí pidió.
        """
        try:
            root = self.workspaces.root(workspace_id)
            return load_project_rules(root)
        except IDEReglasError:
            return None

    def _memory_block_for(self, workspace_id: str, prompt: str) -> str | None:
        """Recuerdos del proyecto relevantes para ESTE prompt -- 2.3 del plan
        de paridad. Mismo contrato de "nunca revienta el turno" que
        ``_project_rules_for``: un workspace inválido o cualquier error de
        lectura de la memoria solo deja de agregar contexto, no cancela la
        tarea. ``recall_as_prompt_block`` ya devuelve ``None`` cuando nada de
        lo recordado toca el prompt actual -- no hay nada más que decidir
        acá."""
        try:
            return self.memoria.recall_as_prompt_block(workspace_id, prompt)
        except IDEMemoriaError:
            return None

    def _create_turn_checkpoint(self, workspace_id: str, label: str) -> str | None:
        """Abre el punto de control de ESTE turno. ``None`` si no se pudo abrir
        (workspace inválido, disco lleno, etc.) -- deshacer es una red de
        seguridad, no un requisito para que el turno arranque."""
        try:
            checkpoint = self.checkpoints.create(workspace_id, label=label)
            return str(checkpoint["id"])
        except IDECheckpointError:
            return None

    def start_agent(
        self,
        workspace_id: str,
        prompt: str,
        provider: str = "workers_ai",
        title: Any = None,
        model: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        skill_context: str | None = None,
        conversation_id: str | None = None,
        mcp_tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 200_000:
            raise IDESessionError("El prompt del agente no es válido.")
        if model is not None and (
            not isinstance(model, str)
            or not model.strip()
            or len(model) > 120
            or any(ord(char) < 32 for char in model)
        ):
            raise IDESessionError("El modelo indicado no es válido.")
        if attachments is not None and (
            not isinstance(attachments, list)
            or len(attachments) > 5
            or any(not isinstance(item, dict) for item in attachments)
        ):
            raise IDESessionError("Los adjuntos del agente no son válidos.")
        if skill_context is not None and (
            not isinstance(skill_context, str) or len(skill_context) > 120_000
        ):
            raise IDESessionError("El contexto de skills no es válido.")
        if mcp_tools is not None and (
            not isinstance(mcp_tools, list)
            or len(mcp_tools) > 500
            or any(not isinstance(item, dict) for item in mcp_tools)
        ):
            raise IDESessionError("El catálogo MCP no es válido.")
        if provider not in {"auto", "workers_ai"}:
            raise IDESessionError("El IDE solo admite el proveedor Workers AI.")
        if conversation_id is not None and (
            not isinstance(conversation_id, str)
            or not conversation_id.strip()
            or len(conversation_id) > 128
            or any(ord(char) < 32 for char in conversation_id)
        ):
            raise IDESessionError("El identificador de conversación no es válido.")
        selected = "workers_ai"
        self.workspaces.root(workspace_id)

        # Una sesión = una conversación, no un mensaje. Si ya existe una
        # sesión de esta misma conversación que terminó su turno de forma
        # limpia, se REUSA (mismo id, mismo hilo de eventos) en vez de crear
        # una sesión nueva cada vez que el usuario escribe otra vez. Eso es
        # lo que evita que el Studio muestre "3 chats con el mismo nombre" y
        # que el agente tenga que releer el repo desde cero en cada mensaje.
        reusable = (
            self._find_reusable_agent_session(conversation_id, workspace_id)
            if conversation_id
            else None
        )
        if reusable is not None:
            # Leer el estado y actuar tienen que ser UNA operación atómica
            # (ver ``Session._turn_lock``): entre "está corriendo" y "encolo"
            # el turno puede haber terminado, y entre "terminó" y "abro un
            # turno nuevo" el cierre del turno anterior puede estar arrancando
            # uno con lo que quedó en cola.
            with reusable._turn_lock:
                status = reusable.metadata.get("status")
                if status in _AGENT_STEERABLE_STATUSES:
                    return self._encolar_mensaje_para_turno_vivo(
                        reusable, prompt, attachments=attachments
                    )
                if status == "plan_pending":
                    # Ver ``_AGENT_STEERABLE_STATUSES``: aquí el turno no está
                    # trabajando, está esperando a la persona. El mensaje se
                    # rechaza nombrando lo que sí destraba la conversación.
                    raise IDESessionError(
                        "Hay un plan esperando tu decisión en esta conversación; "
                        "apruébalo, edítalo o recházalo antes de mandar otro mensaje."
                    )
                if status in _AGENT_CONTINUABLE_STATUSES:
                    return self._continue_agent_session(
                        reusable,
                        prompt,
                        title=title,
                        attachments=attachments,
                        skill_context=skill_context,
                        model=model,
                        mcp_tools=mcp_tools,
                    )

        resolved_conversation_id = conversation_id or str(uuid.uuid4())
        session = self._register(
            kind="agent",
            workspace_id=workspace_id,
            title=_clean_title(title, "Agente Edecán"),
            extra={
                "provider": selected,
                "model": model,
                "conversation_id": resolved_conversation_id,
                "attachment_names": [
                    str(item.get("name") or "imagen")
                    for item in (attachments or [])[:5]
                    if isinstance(item, dict)
                ],
            },
        )
        try:
            # ``_turn_lock`` tomado ya: entre ``_register`` (que la deja
            # "starting" y visible para ``_find_reusable_agent_session``) y el
            # hilo que de verdad la va a atender hay una ventana en la que un
            # mensaje simultáneo de la misma conversación podría encolarse
            # contra un turno que todavía no existe -- o peor, contra uno que
            # revienta abajo y nadie va a drenar.
            with session._turn_lock:
                return self._arrancar_turno_de_sesion_nueva(
                    session,
                    prompt,
                    workspace_id=workspace_id,
                    attachments=attachments,
                    skill_context=skill_context,
                    model=model,
                    mcp_tools=mcp_tools,
                )
        except Exception:
            session.metadata["status"] = "failed"
            session.metadata["ended_at"] = _now()
            self._save()
            raise

    def _arrancar_turno_de_sesion_nueva(
        self,
        session: Session,
        prompt: str,
        *,
        workspace_id: str,
        attachments: list[dict[str, Any]] | None,
        skill_context: str | None,
        model: str | None,
        mcp_tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Primer turno de una sesión recién registrada (ver ``start_agent``)."""
        # ``_prompt_with_conversation_context`` solo entra aquí cuando NO
        # hubo sesión viva que reusar: primer mensaje de una conversación
        # nueva (no encuentra nada, devuelve el prompt tal cual) o
        # respaldo tras un reinicio del companion (la sesión previa quedó
        # "interrupted" y ya no es una continuación confiable).
        final_prompt = self._prompt_with_conversation_context(session, prompt)
        rules = self._project_rules_for(workspace_id)
        rules_block = rules.as_prompt_block() if rules else None
        # Se recuerda contra el prompt CRUDO de la persona, no
        # ``final_prompt`` (que ya trae el envoltorio de historial
        # previo) -- es lo que de verdad se está preguntando ahora mismo.
        memory_block = self._memory_block_for(workspace_id, prompt)
        checkpoint_id = self._create_turn_checkpoint(
            workspace_id, session.metadata.get("title") or "Turno de agente"
        )
        session.metadata["status"] = "running"
        session.metadata["turn_checkpoint_id"] = checkpoint_id
        if rules is not None and rules.found:
            session.append("status", f"Reglas del repo leídas de «{rules.source_path}».")
        if memory_block is not None:
            session.append("status", "Memoria de sesiones anteriores agregada a este turno.")
        turn_user_cursor = session.append("user", prompt)["cursor"]
        turn_start_cursor = session.append("status", "Agente de Workers AI iniciado.")["cursor"]
        session.metadata["turn_start_cursor"] = turn_start_cursor
        session.metadata["turn_user_cursor"] = turn_user_cursor
        self._save()
        threading.Thread(
            target=self._objetivo_turno(),
            args=(
                session,
                final_prompt,
                attachments,
                skill_context,
                model,
                mcp_tools,
                turn_start_cursor,
                checkpoint_id,
                rules_block,
                memory_block,
            ),
            daemon=True,
            name=f"edecan-agent-{session.id}",
        ).start()
        return {"session": session.public()}

    def _encolar_mensaje_para_turno_vivo(
        self,
        session: Session,
        prompt: str,
        *,
        attachments: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Acepta un mensaje mandado mientras el agente trabaja.

        Se llama con ``session._turn_lock`` tomado (ver ``start_agent``). El
        turno en curso sigue intacto: lo encolado se entrega entre vueltas de
        su ciclo de herramientas, no ahora.

        Los adjuntos sí se rechazan, y a propósito: el modelo de ESTE turno ya
        quedó fijado al arrancar (``ide_workers_agent._model_for_turn``), así
        que una imagen que llega a mitad de camino puede caer en un modelo sin
        visión y el turno seguiría con un mensaje que depende de algo que el
        modelo nunca vio. Un "espera a que termine" es peor experiencia que
        encolar, pero mejor que una respuesta segura sobre una imagen
        invisible.
        """
        if attachments:
            raise IDESessionError(
                "El agente está trabajando: puedo encolarle texto, pero no imágenes "
                "(el modelo de este turno ya está fijado y podría no verlas). Manda la "
                "imagen cuando termine el turno."
            )
        encolado = session.enqueue_user_message(prompt)
        session.append(
            "status",
            f"Mensaje recibido y en cola (posición {encolado['position']}): el agente lo va "
            "a leer en cuanto cierre la acción que tiene en curso, sin cortarla.",
        )
        return {"session": session.public(), "queued": encolado}

    def _find_reusable_agent_session(
        self, conversation_id: str, workspace_id: str
    ) -> Session | None:
        """Busca la sesión de agente más reciente de esta conversación, si sigue viva.

        "Viva" no significa "con un hilo corriendo ahora mismo": significa que
        esta sesión sigue siendo el hilo de esa conversación, esté su turno
        terminado o en curso. Devolverla NO decide qué se hace con el mensaje
        -- eso lo decide ``start_agent`` leyendo el estado bajo
        ``Session._turn_lock``: continuar la conversación con un turno nuevo,
        o encolar el mensaje para el turno que ya está corriendo.

        Antes esto lanzaba "ya hay un turno en curso" cuando el estado estaba
        ocupado, y ahí se perdía el mensaje. Ese rechazo era la razón por la
        que no se podía dirigir al agente mientras trabajaba.

        Si el companion se reinició a medio turno, ``_load`` ya la marcó
        "interrupted" y ``start_agent`` la trata como no reusable: cae al
        respaldo de ``_prompt_with_conversation_context``, que sí sabe leer
        sesiones viejas de esa conversación desde disco. Un ``workspace_id``
        distinto tampoco reusa: un conversation_id repetido apuntando a otro
        workspace es un error del llamador, no la misma conversación.
        """

        with self._lock:
            candidates = [
                row
                for row in self._sessions.values()
                if row.metadata.get("kind") == "agent"
                and row.metadata.get("conversation_id") == conversation_id
                and row.metadata.get("workspace_id") == workspace_id
            ]
        if not candidates:
            return None
        candidates.sort(key=lambda row: str(row.metadata.get("started_at") or ""))
        return candidates[-1]

    def _continue_agent_session(
        self,
        session: Session,
        prompt: str,
        *,
        title: Any,
        attachments: list[dict[str, Any]] | None,
        skill_context: str | None,
        model: str | None,
        mcp_tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Arranca un turno más dentro de una sesión de agente ya existente.

        Siempre bajo ``session._turn_lock`` (es RLock: los dos caminos que
        llegan aquí -- ``start_agent`` y el cierre de un turno que promueve lo
        que quedó encolado -- ya lo tienen tomado, y tomarlo de nuevo es
        barato). Sin ese candado, "vi que terminó" y "arranco el turno
        siguiente" son dos pasos separados y dos hilos pueden colarse entre
        medio.
        """

        with session._turn_lock:
            return self._arrancar_turno_de_continuacion(
                session,
                prompt,
                title=title,
                attachments=attachments,
                skill_context=skill_context,
                model=model,
                mcp_tools=mcp_tools,
            )

    def _arrancar_turno_de_continuacion(
        self,
        session: Session,
        prompt: str,
        *,
        title: Any,
        attachments: list[dict[str, Any]] | None,
        skill_context: str | None,
        model: str | None,
        mcp_tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        # El recorte se arma ANTES de anotar el mensaje nuevo: si no, el
        # propio prompt que se está por mandar aparecería duplicado dentro
        # de su propio historial.
        final_prompt = self._continuation_prompt(session, prompt)
        if title is not None:
            session.metadata["title"] = _clean_title(title, str(session.metadata.get("title")))
        session.metadata["model"] = model
        session.metadata["attachment_names"] = [
            str(item.get("name") or "imagen")
            for item in (attachments or [])[:5]
            if isinstance(item, dict)
        ]
        session.metadata["status"] = "running"
        session.metadata["ended_at"] = None
        session.metadata["exit_code"] = None
        session.cancel_event.clear()
        try:
            workspace_id = str(session.metadata["workspace_id"])
            rules = self._project_rules_for(workspace_id)
            rules_block = rules.as_prompt_block() if rules else None
            memory_block = self._memory_block_for(workspace_id, prompt)
            # Un checkpoint NUEVO por turno, aunque la sesión se reuse: cada
            # mensaje de la conversación debe poder deshacerse por separado,
            # no todo el hilo completo de una sola vez.
            checkpoint_id = self._create_turn_checkpoint(
                workspace_id, session.metadata.get("title") or "Turno de agente"
            )
            session.metadata["turn_checkpoint_id"] = checkpoint_id
            if rules is not None and rules.found:
                session.append(
                    "status", f"Reglas del repo leídas de «{rules.source_path}»."
                )
            if memory_block is not None:
                session.append(
                    "status", "Memoria de sesiones anteriores agregada a este turno."
                )
            turn_user_cursor = session.append("user", prompt)["cursor"]
            turn_start_cursor = session.append("status", "Agente de Workers AI iniciado.")[
                "cursor"
            ]
            session.metadata["turn_start_cursor"] = turn_start_cursor
            session.metadata["turn_user_cursor"] = turn_user_cursor
            self._save()
            threading.Thread(
                target=self._objetivo_turno(),
                args=(
                    session,
                    final_prompt,
                    attachments,
                    skill_context,
                    model,
                    mcp_tools,
                    turn_start_cursor,
                    checkpoint_id,
                    rules_block,
                    memory_block,
                ),
                daemon=True,
                name=f"edecan-agent-{session.id}",
            ).start()
            return {"session": session.public()}
        except Exception:
            session.metadata["status"] = "failed"
            session.metadata["ended_at"] = _now()
            self._save()
            raise

    def _continuation_prompt(self, session: Session, prompt: str) -> str:
        """Da continuidad real dentro de LA MISMA sesión, sin ordenar re-explorar.

        A diferencia del respaldo de abajo, aquí no hace falta escanear otras
        sesiones ni releer JSONL: los eventos de los turnos anteriores ya
        viven en esta misma sesión reusada. Se recorta a lo que de verdad es
        "la conversación" -- lo que se pidió, lo que se respondió y qué
        archivos cambiaron -- y se deja fuera el ruido de comandos/salida
        cruda, que es lo que antes hacía crecer el prompt sin límite turno a
        turno y empujaba al modelo a releer el árbol "por si acaso".
        """

        with session._lock:
            events = list(session.events)
        chunks: list[str] = []
        for event in events:
            event_type = str(event.get("type") or "")
            # "blocks" entra (su ``text`` es la tabla/gráfica ya rendida en
            # texto compacto) porque es lo que la persona TIENE EN PANTALLA:
            # sin esto, un "ordénala por costo" en el mensaje siguiente le
            # llega a un modelo que no recuerda qué mostró y tendría que
            # medirlo todo otra vez. Ver ``ide_bloques.py``.
            if event_type not in {"user", "user_delivered", "assistant_final", "file", "blocks"}:
                continue
            text = str(event.get("text") or "").strip()
            if text:
                # ``user_delivered`` es un mensaje que la persona mandó a mitad
                # del turno anterior y que el agente sí llegó a leer: en el
                # historial es un turno de usuario más, no una categoría
                # aparte. Los ``user_queued`` que nunca se entregaron NO entran
                # (o se promovieron a un turno propio, con su evento ``user``,
                # o quedaron como ``user_undelivered`` sin llegar al modelo).
                etiqueta = "user" if event_type == "user_delivered" else event_type
                chunks.append(f"[{etiqueta}] {text}")
        if not chunks:
            return prompt
        recent_context = "\n".join(chunks)[-MAX_REINJECTED_CONTEXT_CHARS:]
        return (
            "Sigues la misma conversación de ingeniería con esta persona; ya "
            "tienes el contexto de abajo, incluidos los archivos que ya "
            "leíste y cambiaste. No vuelvas a listar el árbol completo ni a "
            "releer archivos que ya conoces, salvo que la solicitud actual "
            "dependa de algo que pudo haber cambiado por fuera.\n\n"
            "<historial_de_esta_conversacion>\n"
            f"{recent_context}\n"
            "</historial_de_esta_conversacion>\n\n"
            "<solicitud_actual>\n"
            f"{prompt}\n"
            "</solicitud_actual>"
        )

    def _prompt_with_conversation_context(self, session: Session, prompt: str) -> str:
        """Respaldo: reconstruye contexto desde disco cuando NO hay sesión viva.

        Solo se usa para el primer mensaje de una conversación (no encuentra
        nada y devuelve el prompt intacto) o tras un reinicio del companion,
        cuando ``_load`` marcó la sesión previa como "interrupted" y ya no es
        segura de reusar en caliente. En ese caso sí tiene sentido pedirle al
        modelo que inspeccione el workspace: no hay continuidad en memoria, y
        el disco es la única fuente de verdad que le queda.
        """

        conversation_id = str(session.metadata.get("conversation_id") or "")
        if not conversation_id:
            return prompt
        with self._lock:
            previous = [
                row
                for row in self._sessions.values()
                if row.id != session.id
                and row.metadata.get("kind") == "agent"
                and row.metadata.get("conversation_id") == conversation_id
            ]
        previous.sort(key=lambda row: str(row.metadata.get("started_at") or ""))
        chunks: list[str] = []
        for row in previous:
            for event in row._read_persisted(0, limit=100_000):
                event_type = str(event.get("type") or "")
                if event_type not in {
                    "user",
                    # Mismo criterio que ``_continuation_prompt``: lo que la
                    # persona mandó a mitad de un turno y el agente alcanzó a
                    # leer es un turno de usuario más.
                    "user_delivered",
                    "assistant",
                    "assistant_final",
                    "progress",
                    "file",
                    "command",
                    "output",
                    "error",
                    "status",
                    # Mismo criterio que ``_continuation_prompt``: lo que la
                    # persona vio dibujado es contexto de la conversación.
                    "blocks",
                }:
                    continue
                text = str(event.get("text") or "").strip()
                if text:
                    etiqueta = "user" if event_type == "user_delivered" else event_type
                    chunks.append(f"[{etiqueta}] {text}")
        if not chunks:
            return prompt
        recent_context = "\n".join(chunks)[-MAX_REINJECTED_CONTEXT_CHARS:]
        return (
            "Continúa la misma conversación de ingeniería. La sesión anterior "
            "no siguió viva (el companion se reinició), así que inspecciona "
            "el workspace antes de actuar porque los archivos son la fuente "
            "de verdad.\n\n"
            "<historial_previo>\n"
            f"{recent_context}\n"
            "</historial_previo>\n\n"
            "<solicitud_actual>\n"
            f"{prompt}\n"
            "</solicitud_actual>"
        )

    def _run_workers_agent(
        self,
        session: Session,
        prompt: str,
        attachments: list[dict[str, Any]] | None,
        skill_context: str | None,
        model: str | None,
        mcp_tools: list[dict[str, Any]] | None,
        turn_start_cursor: int = 0,
        checkpoint_id: str | None = None,
        rules_block: str | None = None,
        memory_block: str | None = None,
    ) -> None:
        def write_event(
            event_type: str,
            text: str,
            *,
            presentation: list[dict[str, Any]] | None = None,
        ) -> None:
            stream = "stderr" if event_type == "error" else "stdout"
            if event_type == "plan_proposed":
                # ``WorkersIDEAgent.run`` ya registró el plan en
                # ``self.plans`` (se lo pasamos como ``plan_store`` abajo);
                # lo único que falta capturar acá es lo que ESE store no
                # modela -- las rutas por paso, que ``approve_plan`` necesita
                # para armar el reparto real (``ide_reparto.PasoReparto``).
                with contextlib.suppress(
                    json.JSONDecodeError, TypeError, KeyError, ValueError
                ):
                    payload = json.loads(text)
                    plan_id = str(payload["plan"]["id"])
                    rutas_crudo = payload.get("rutas_por_paso") or []
                    self._plan_routes[plan_id] = [
                        tuple(str(r) for r in item) if isinstance(item, list) and item else None
                        for item in rutas_crudo
                    ]
                    session.metadata["last_plan_id"] = plan_id
            session.append(event_type, text, stream=stream, presentation=presentation)

        def track_file(path: str) -> None:
            # Nunca deja que el "deshacer" tumbe el turno real: un checkpoint
            # que no se pudo abrir (``checkpoint_id`` es ``None``) o un error
            # de disco al trackear un archivo puntual solo apagan la red de
            # seguridad de ESE archivo, no la escritura que el usuario pidió.
            if checkpoint_id is None:
                return
            with contextlib.suppress(IDECheckpointError, OSError):
                self.checkpoints.track(checkpoint_id, path)

        def has_assistant_final() -> bool:
            # Cursor > turn_start_cursor, no una búsqueda global: una sesión
            # reusada arrastra el ``assistant_final`` de turnos anteriores en
            # su cola de eventos, y contar ese evento viejo como si fuera la
            # respuesta de ESTE turno ocultaría un fallo real (turno que
            # revienta sin decir nada se leería como "completado").
            with session._lock:
                return any(
                    event.get("type") == "assistant_final"
                    and isinstance(event.get("cursor"), int)
                    and event["cursor"] > turn_start_cursor
                    for event in session.events
                )

        def has_plan_pending() -> bool:
            # Mismo criterio (cursor > turn_start_cursor) que
            # ``has_assistant_final``: ``WorkersIDEAgent.run`` sale de forma
            # limpia (sin excepción) cuando pausa a esperar aprobación de un
            # plan -- ver el gate de ``proponer_plan`` ahí --, así que sin
            # este chequeo el bloque de abajo confundiría "está esperando a
            # la persona" con "terminó sin decir nada" y marcaría el turno
            # como fallido.
            with session._lock:
                return any(
                    event.get("type") == "plan_proposed"
                    and isinstance(event.get("cursor"), int)
                    and event["cursor"] > turn_start_cursor
                    for event in session.events
                )

        async def invoke_mcp(name: str, args: dict[str, Any]) -> dict[str, Any]:
            call_id = str(uuid.uuid4())
            waiter = threading.Event()
            pending = {
                "session_id": session.id,
                "call_id": call_id,
                "name": name,
                "arguments": dict(args),
                "waiter": waiter,
                "result": None,
            }
            with self._lock:
                self._mcp_pending[(session.id, call_id)] = pending
            session.append(
                "mcp_confirmation",
                json.dumps(
                    {
                        "call_id": call_id,
                        "name": name,
                        "arguments": args,
                    },
                    ensure_ascii=False,
                ),
            )
            while not waiter.is_set():
                if session.cancel_event.is_set():
                    with self._lock:
                        self._mcp_pending.pop((session.id, call_id), None)
                    return {"ok": False, "error": "Trabajo cancelado."}
                await asyncio.sleep(0.25)
            with self._lock:
                resolved = self._mcp_pending.pop((session.id, call_id), pending)
            result = resolved.get("result")
            return (
                dict(result)
                if isinstance(result, dict)
                else {
                    "ok": False,
                    "error": "La confirmación MCP no produjo un resultado.",
                }
            )

        def take_pending_user_messages() -> list[str]:
            """Entrega lo encolado. La llama ``WorkersIDEAgent.run`` ENTRE
            vueltas de su ciclo, nunca a mitad de una llamada a herramienta:
            ese es el punto fino de todo esto (ver el docstring del módulo)."""
            pendientes = session.drain_user_messages()
            textos: list[str] = []
            for item in pendientes:
                texto = str(item.get("text") or "")
                if not texto:
                    continue
                # El evento se emite en el momento exacto de la entrega: su
                # ``timestamp`` es el "cuándo llegó" que la interfaz muestra.
                session.append("user_delivered", texto)
                textos.append(texto)
            return textos

        error_turno: Exception | None = None
        try:
            asyncio.run(
                self.workers_agent.run(
                    workspace_id=str(session.metadata["workspace_id"]),
                    prompt=prompt,
                    write_event=write_event,
                    cancelled=session.cancel_event.is_set,
                    attachments=attachments,
                    skill_context=skill_context,
                    model=model,
                    mcp_tools=mcp_tools,
                    invoke_mcp=invoke_mcp,
                    project_rules_block=rules_block,
                    memory_block=memory_block,
                    track_file=track_file,
                    semantic=self.semantic,
                    memoria=self.memoria,
                    plan_store=self.plans,
                    session_id=session.id,
                    pending_user_messages=take_pending_user_messages,
                    effort_store=self.effort_store,
                )
            )
        except Exception as exc:  # noqa: BLE001 - el cierre de abajo es quien decide qué fue
            # No se maneja aquí: fijar el estado final y decidir qué pasa con
            # lo que quedó encolado tienen que ser UNA sola operación atómica
            # frente a ``start_agent`` (ver ``Session._turn_lock``), y para eso
            # los dos caminos -- éxito y fallo -- tienen que confluir abajo.
            error_turno = exc
        finally:
            with session._turn_lock:
                if error_turno is None and session.metadata.get("status") != "cancelled":
                    if has_plan_pending():
                        # Pausa legítima, no una falla: el turno se detuvo a
                        # esperar aprobación humana del plan (ver
                        # ``approve_plan``/``edit_plan``/``reject_plan``), igual
                        # que la pausa de confirmación de una tool peligrosa.
                        session.metadata["status"] = "plan_pending"
                        session.metadata["exit_code"] = None
                    elif not has_assistant_final():
                        error_turno = IDESessionError(
                            "El agente terminó sin entregar una respuesta final."
                        )
                    else:
                        session.metadata["status"] = "completed"
                        session.metadata["exit_code"] = 0
                if error_turno is not None:
                    was_cancelled = (
                        session.metadata.get("status") == "cancelled"
                        or session.cancel_event.is_set()
                    )
                    if was_cancelled:
                        session.metadata["status"] = "cancelled"
                    else:
                        # Defensa en profundidad: WorkersIDEAgent normalmente
                        # crea el cierre de fallo con el contexto de
                        # herramientas. Si una implementación futura retorna o
                        # falla antes de hacerlo, la sesión tampoco puede
                        # quedar sin una respuesta humana visible.
                        if not has_assistant_final():
                            session.append(
                                "assistant_final",
                                build_failure_final(error_turno),
                            )
                        session.append("error", str(error_turno)[:2000], stream="stderr")
                        session.metadata["status"] = "failed"
                        session.metadata["exit_code"] = 1
                if checkpoint_id is not None:
                    # Sella una vez el turno termina (éxito, fallo o cancelación):
                    # registra el hash "después" de cada archivo tocado, para que
                    # `restore()` sepa distinguir "esto lo dejó el agente" de
                    # "alguien lo tocó a mano después" (ver `ide_checkpoints.py`).
                    with contextlib.suppress(IDECheckpointError, OSError):
                        self.checkpoints.seal(checkpoint_id)
                session.metadata["ended_at"] = _now()
                self._save()
                # Último acto del turno, y lo más fácil de dejar mal: si algo
                # quedó sin entregar, aquí se decide si se convierte en el
                # turno siguiente o se dice que no llegó.
                self._entregar_pendientes_al_cerrar(
                    session,
                    skill_context=skill_context,
                    model=model,
                    mcp_tools=mcp_tools,
                )

    # ------------------------------------------------------------------ #
    # Motor opencode -- el turno real (ver docs/opencode-motor.md). Mismo
    # contrato posicional que ``_run_workers_agent`` (``_objetivo_turno``
    # elige entre las dos) y mismo compromiso de cierre: al volver, la
    # sesión SIEMPRE queda en un estado terminal, con su checkpoint sellado
    # y lo encolado resuelto -- la interfaz no debe notar cuál de los dos
    # motores corrió.
    # ------------------------------------------------------------------ #

    def _run_opencode_agent(
        self,
        session: Session,
        prompt: str,
        attachments: list[dict[str, Any]] | None,
        skill_context: str | None,
        model: str | None,
        mcp_tools: list[dict[str, Any]] | None,
        turn_start_cursor: int = 0,
        checkpoint_id: str | None = None,
        rules_block: str | None = None,
        memory_block: str | None = None,
    ) -> None:
        """Turno de agente corrido sobre opencode -- el motor por defecto.

        Corre en su propio hilo (como ``_run_workers_agent``); todo el
        trabajo async real pasa por ``self._ejecutar_opencode``, que lo
        agenda en el loop persistente (``_BucleOpencode``) y bloquea ESTE
        hilo hasta que el turno completo (mandar el prompt + consumir todos
        sus eventos) termine.

        LIMITACIÓN DECLARADA de esta ronda (regla 7 del encargo: nada
        simulado): ``ServidorOpencode.enviar_prompt`` solo admite texto --
        ``attachments`` (imágenes), ``skill_context`` y ``mcp_tools`` no se
        le pasan a opencode todavía porque esa superficie no existe en el
        cimiento (``ide_opencode.py``). Se avisa en el hilo de la sesión en
        vez de fingir que se enviaron o descartarlos en silencio.
        """
        workspace_id = str(session.metadata["workspace_id"])
        workspace_root = self.workspaces.root(workspace_id)

        def track_file(path: str) -> None:
            if checkpoint_id is None:
                return
            with contextlib.suppress(IDECheckpointError, OSError):
                self.checkpoints.track(checkpoint_id, path)

        avisos: list[str] = []
        if attachments:
            avisos.append(
                "No mandé la(s) imagen(es) adjunta(s) a opencode: el motor opencode de esta "
                "ronda solo admite prompts de texto (ver ServidorOpencode.enviar_prompt)."
            )
        if skill_context:
            avisos.append(
                "El contexto de skills no se envió: todavía no existe esa superficie para "
                "opencode."
            )
        if mcp_tools:
            avisos.append(
                "Las herramientas MCP del teléfono no están conectadas al motor opencode "
                "todavía."
            )

        prompt_final = prompt
        bloques_previos = [bloque for bloque in (rules_block, memory_block) if bloque]
        if bloques_previos:
            prompt_final = "\n\n".join([*bloques_previos, prompt_final])

        error_turno: Exception | None = None
        cerro_con_texto = False
        try:
            cerro_con_texto = self._ejecutar_opencode(
                self._turno_opencode(
                    session=session,
                    workspace_root=workspace_root,
                    prompt=prompt_final,
                    avisos=avisos,
                    track_file=track_file,
                )
            )
        except Exception as exc:  # noqa: BLE001 - el cierre de abajo decide qué fue
            error_turno = exc
        finally:
            with session._turn_lock:
                # Ver "mensaje final confuso" (hallazgo real de un
                # verificador, docstring de ``_consumir_turno_opencode``):
                # cuando el turno se cerró esperando un permiso/pregunta que
                # nunca llegó, ``_consumir_turno_opencode`` ya escribió un
                # ``status`` que dice EXACTAMENTE qué pasó -- no hace falta
                # (y confunde) inventar además un ``assistant_final``
                # genérico y un evento ``error`` que no lo mencionan.
                cierre_ya_explicado = session.metadata.pop("opencode_cierre_explicado", None)
                if error_turno is None and session.metadata.get("status") != "cancelled":
                    if cerro_con_texto:
                        session.metadata["status"] = "completed"
                        session.metadata["exit_code"] = 0
                    elif cierre_ya_explicado is not None:
                        session.metadata["status"] = "failed"
                        session.metadata["exit_code"] = 1
                    else:
                        error_turno = IDESessionError(
                            "El agente terminó sin entregar una respuesta final."
                        )
                if error_turno is not None:
                    was_cancelled = (
                        session.metadata.get("status") == "cancelled"
                        or session.cancel_event.is_set()
                    )
                    if was_cancelled:
                        session.metadata["status"] = "cancelled"
                    else:
                        with session._lock:
                            ya_tiene_final = any(
                                event.get("type") == "assistant_final"
                                and isinstance(event.get("cursor"), int)
                                and event["cursor"] > turn_start_cursor
                                for event in session.events
                            )
                        if not ya_tiene_final:
                            session.append("assistant_final", build_failure_final(error_turno))
                        session.append("error", str(error_turno)[:2000], stream="stderr")
                        session.metadata["status"] = "failed"
                        session.metadata["exit_code"] = 1
                if checkpoint_id is not None:
                    with contextlib.suppress(IDECheckpointError, OSError):
                        self.checkpoints.seal(checkpoint_id)
                session.metadata["ended_at"] = _now()
                self._save()
                self._entregar_pendientes_al_cerrar(
                    session,
                    skill_context=skill_context,
                    model=model,
                    mcp_tools=mcp_tools,
                )

    async def _turno_opencode(
        self,
        *,
        session: Session,
        workspace_root: Path,
        prompt: str,
        avisos: list[str],
        track_file: Callable[[str], None],
    ) -> bool:
        """Cuerpo async de UN turno sobre opencode -- corre en el loop
        persistente (``_BucleOpencode``), nunca en un ``asyncio.run()`` por
        turno (ver el docstring de esa clase). Devuelve ``True`` si el turno
        cerró con una respuesta de texto real (equivalente al
        ``has_assistant_final`` del motor viejo).

        Orden exacto (regla dura de docs/opencode-motor.md §3 -- el puente
        de permisos SIEMPRE escuchando antes del primer prompt):
        1. ``MotorOpencode.servidor_para``: asegura ``opencode.json``,
           arranca o reusa el servidor del workspace, y DENTRO de esa misma
           llamada arranca y confirma el puente -- si el puente no pudo
           arrancar, esto lanza y el turno se cierra "failed" con el motivo
           real; nunca se manda un prompt con el puente muerto.
        2. Se crea (primera vez) o reusa (conversación continuada, mismo
           ``Session`` de Edecán) la sesión de opencode, con el modelo del
           nivel de esfuerzo vigente y el agente ("plan"/"build") del modo
           vigente.
        3. ``enviar_prompt`` + consumir ``eventos()``, traduciendo cada uno
           con ``TraductorDeTurno`` y escribiéndolo en el hilo de la
           sesión de Edecán.

        Punto 4, agregado en esta ronda: en cuanto se conoce
        ``opencode_session_id`` (antes de ``enviar_prompt``, mismo criterio
        de "conectar antes de que pueda pasar algo" que ``MotorOpencode``
        ya aplica para el puente completo) se arranca
        ``_vigilar_bus_para_sesion`` en un ``asyncio.Task`` aparte -- ES lo
        que le avisa a ``_consumir_turno_opencode`` en cuanto haya un
        permiso o pregunta pendiente, en vez de esperar a que
        ``_TIEMPO_INACTIVIDAD_EVENTOS_OPENCODE`` se agote. Se cancela y se
        cierra en el ``finally``, junto con ``consulta``.
        """
        assert self.motor_opencode is not None
        servidor = await self.motor_opencode.servidor_para(workspace_root)
        consulta = PuenteDePermisos(servidor, obtener_modo=self._modo_para_opencode_session)
        notificacion_bus = asyncio.Event()
        # ``None`` hasta que se resuelva ``opencode_session_id`` más abajo --
        # si algo revienta antes de eso (p. ej. ``crear_sesion``), no hay
        # nada que este ``finally`` tenga que cancelar/cerrar todavía.
        tarea_bus: asyncio.Task[None] | None = None
        cliente_bus: httpx.AsyncClient | None = None
        try:
            for aviso in avisos:
                session.append("status", aviso)

            # Primera vez que ESTA sesión de Edecán habla con opencode: no
            # hay UI todavía (esta ronda) para responder un PERMISO pedido a
            # mitad de turno (solo preguntas, ver más abajo) -- si se dejara
            # el default de ``ModoAgenteStore`` (Manual, el más
            # conservador -- pensado para el gate de acciones DIRECTAS del
            # teléfono, no para esto) toda sesión nueva se colgaría en su
            # primera edición esperando una aprobación que nadie puede dar
            # todavía. Se inicializa en Auto UNA sola vez; a partir de ahí,
            # ``SessionManager.set_modo_agente`` manda de verdad.
            if not session.metadata.get("modo_agente_inicializado"):
                self.modo_agente_store.fijar(session.id, "auto")
                session.metadata["modo_agente_inicializado"] = True

            modo_vigente = self.modo_agente_store.obtener(session.id)
            agente_opencode = "plan" if modo_vigente.value == "plan" else "build"

            # Encargo de esta ronda: "el selector manda QUÉ modelo, el
            # esfuerzo solo manda CUÁNTO piensa ESE modelo" -- antes acá se
            # llamaba directo a ``modelo_para(perfil, nivel.nombre)``, que
            # ELEGÍA el modelo por el nivel de esfuerzo e ignoraba por
            # completo lo que la persona escogió en el selector (el modelo
            # SÍ queda guardado en ``session.metadata['model']``, ver
            # ``start_agent``/``_arrancar_turno_de_continuacion`` -- el bug
            # era que nadie lo leía acá). ``_referencia_modelo_para_sesion``
            # es la única fuente de verdad ahora: usa el modelo elegido si
            # sigue en el catálogo, y solo cae al defecto del perfil si la
            # persona no eligió nada.
            nivel = self.effort_store.obtener(session.id)
            referencia = self._referencia_modelo_para_sesion(session, nivel.nombre)

            opencode_session_id = session.metadata.get("opencode_session_id")
            if not isinstance(opencode_session_id, str) or not opencode_session_id:
                variante = await variante_de_esfuerzo(
                    servidor,
                    proveedor=referencia.provider_id,
                    modelo=referencia.model_id,
                    reasoning_effort=referencia.reasoning_effort,
                )
                info = await servidor.crear_sesion(
                    agente=agente_opencode,
                    proveedor=referencia.provider_id,
                    modelo=referencia.model_id,
                    variante=variante,
                )
                opencode_session_id = info.id
                session.metadata["opencode_session_id"] = opencode_session_id
                session.metadata["opencode_eventos_vistos"] = 0
                self._save()
            else:
                # Turno de continuación de la MISMA conversación: se
                # reafirma modelo/agente vigentes en cada turno (barato, dos
                # POST) para que un cambio de modelo/esfuerzo/modo entre
                # mensajes -- aunque no haya pasado por
                # ``set_modelo_agente``/``set_modo_agente``/``fijar_esfuerzo``
                # en vivo -- de todas formas se refleje en el turno que está
                # por arrancar. ``_aplicar_referencia_en_vivo`` es la MISMA
                # función que usan esos tres métodos, así que el modelo que
                # opencode recibe acá es siempre el vigente de verdad, con
                # su variante de esfuerzo si el modelo la declara.
                with contextlib.suppress(ErrorOpencode):
                    await self._aplicar_referencia_en_vivo(
                        servidor, opencode_session_id, referencia
                    )
                with contextlib.suppress(ErrorOpencode):
                    await servidor.cambiar_agente(opencode_session_id, agente_opencode)

            cliente_bus = httpx.AsyncClient(
                base_url=servidor.base_url,
                timeout=httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0),
            )
            tarea_bus = asyncio.create_task(
                self._vigilar_bus_para_sesion(
                    cliente=cliente_bus,
                    opencode_session_id=opencode_session_id,
                    notificacion=notificacion_bus,
                )
            )

            await servidor.enviar_prompt(opencode_session_id, prompt)
            return await self._consumir_turno_opencode(
                session=session,
                servidor=servidor,
                consulta=consulta,
                opencode_session_id=opencode_session_id,
                track_file=track_file,
                notificacion_bus=notificacion_bus,
            )
        finally:
            if tarea_bus is not None:
                tarea_bus.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await tarea_bus
            if cliente_bus is not None:
                await cliente_bus.aclose()
            await consulta.cerrar()

    async def _vigilar_bus_para_sesion(
        self,
        *,
        cliente: httpx.AsyncClient,
        opencode_session_id: str,
        notificacion: asyncio.Event,
    ) -> None:
        """Tarea de fondo, UNA por turno: escucha el bus GLOBAL de opencode
        (``GET /api/event``, ``cliente`` ya trae ``base_url`` con el
        prefijo ``/api`` -- ver hallazgo 2 del docstring de
        ``ide_opencode_permisos``) SOLO para detectar, lo más rápido
        posible, que llegó un ``permission.v2.asked``/``question.v2.asked``
        de ESTA sesión -- y entonces despertar a
        ``_consumir_turno_opencode`` (vía ``notificacion.set()``), que ya
        sabe leer el estado real con ``permisos_pendientes``/
        ``preguntas_pendientes``.

        Deliberadamente NO usa ``PuenteDePermisos.escuchar()`` aunque
        ``consulta`` (el ``PuenteDePermisos`` de este turno) ya sepa
        escuchar ese mismo bus: ``escuchar()`` también DECIDE
        (``procesar_solicitud``, que además RESPONDE por HTTP los casos
        "permitir"/"bloquear") -- y ``MotorOpencode`` ya tiene su PROPIO
        puente escuchando el MISMO bus para este workspace desde antes del
        primer prompt (``ide_opencode_motor._correr_puente``, arrancado
        dentro de ``servidor_para``). Si este método también decidiera, dos
        puentes independientes responderían la MISMA solicitud dos veces --
        la segunda respuesta la rechaza opencode, y eso reventaría CADA
        permiso auto-decidido en modo Auto (el modo por defecto de toda
        sesión nueva, ver más arriba). Este método solo MIRA -- nunca llama
        a ``procesar_solicitud`` ni a ningún endpoint de respuesta -- así
        que no compite con nada: decidir sigue siendo trabajo exclusivo del
        puente de ``MotorOpencode``; este solo adelanta el aviso a la
        persona cuando la solicitud sí queda pendiente de verdad
        (``pedir_aprobacion``) o es una pregunta (que nunca se auto-decide).

        Best-effort a propósito: si esta conexión falla o se cae, no
        revienta el turno -- ``_TIEMPO_INACTIVIDAD_EVENTOS_OPENCODE`` sigue
        cubriendo el turno como respaldo (ver esa constante). Sin
        reintentos: esta tarea vive UNA sola vez por turno (se crea de
        nuevo en el próximo), así que un reintento acá solo alargaría un
        turno que de todas formas va a caer al respaldo.
        """
        try:
            async with cliente.stream("GET", "/event") as resp:
                if resp.status_code >= 400:
                    return
                async for linea in resp.aiter_lines():
                    if not linea or linea.startswith(":") or not linea.startswith("data:"):
                        continue
                    crudo = linea[len("data:") :].strip()
                    if not crudo:
                        continue
                    try:
                        evento = EventoBus.model_validate(json.loads(crudo))
                    except (json.JSONDecodeError, ValidationError):
                        # Línea rara del bus: se ignora esta sola línea, no
                        # se aborta la escucha por ella.
                        continue
                    if evento.type not in _TIPOS_BUS_QUE_DESPIERTAN_EL_TURNO:
                        continue
                    if str(evento.data.get("sessionID") or "") != opencode_session_id:
                        continue  # de OTRA sesión de este mismo workspace -- no es lo nuestro
                    notificacion.set()
        except httpx.HTTPError:
            return

    async def _leer_stream_sesion_una_vez(
        self,
        *,
        servidor: ServidorOpencode,
        opencode_session_id: str,
        vistos: int,
        traductor: TraductorDeTurno,
        session: Session,
        track_file: Callable[[str], None],
    ) -> tuple[int, bool]:
        """UNA pasada de lectura del stream POR SESIÓN
        (``servidor.eventos``) -- el cuerpo que ``_consumir_turno_opencode``
        tenía inline antes de esta ronda, ahora aparte para poder
        carrerearlo contra el aviso del bus (ver
        ``_leer_stream_sesion_o_avisar_permiso``).

        Devuelve ``(vistos_actualizado, cancelado)``: ``cancelado=True``
        significa que ``session.cancel_event`` se disparó a mitad de
        lectura y quien llama debe interrumpir el turno en opencode y
        cerrarlo.

        Un ``TimeoutError`` (``_TIEMPO_INACTIVIDAD_EVENTOS_OPENCODE`` de
        silencio real sobre una conexión que seguía viva) se absorbe y se
        devuelve de inmediato -- sigue siendo una señal válida de "hay que
        revisar qué está pendiente", sin tocar nada de esta ronda.

        HALLAZGO REAL corregido en esta ronda (ver
        ``_MAX_RECONEXIONES_STREAM_SESION``): un cierre LIMPIO del generador
        sin ``TimeoutError`` (``StopAsyncIteration`` a los pocos ms de un
        evento, verificado en vivo con el archivo ya escrito en disco) o un
        ``ErrorOpencode`` a mitad de lectura (conexión SSE perdida, "peer
        closed connection...") YA NO se tratan como si el turno hubiera
        terminado -- ninguno de los dos es esa señal, es la conexión la que
        se cayó. Se reconecta desde el principio (saltando por conteo, ya
        documentado en ``_consumir_turno_opencode``) hasta
        ``_MAX_RECONEXIONES_STREAM_SESION`` veces, con una pausa corta entre
        intentos -- solo si se agotan los reintentos se devuelve el control
        a quien llama, igual que antes."""

        intentos_cierre_prematuro = 0
        while True:
            try:
                indice = 0
                async for evento in servidor.eventos(
                    opencode_session_id,
                    tiempo_maximo_sin_eventos=_TIEMPO_INACTIVIDAD_EVENTOS_OPENCODE,
                ):
                    indice += 1
                    if indice <= vistos:
                        continue  # ya se procesó en una vuelta/turno anterior
                    vistos = indice
                    session.metadata["opencode_eventos_vistos"] = vistos
                    for traducido in traductor.traducir(evento):
                        self._escribir_evento_opencode(session, traducido, track_file)
                    if session.cancel_event.is_set():
                        return vistos, True
                if session.cancel_event.is_set():
                    return vistos, True
                # El generador terminó SIN ``TimeoutError``: cierre
                # prematuro real (ver el docstring de arriba) -- se
                # reconecta en vez de asumir que el turno acabó.
            except TimeoutError:
                return vistos, False  # silencio real: señal legítima, se devuelve tal cual
            except ErrorOpencode:
                if session.cancel_event.is_set():
                    return vistos, True
                # Conexión SSE perdida a mitad de lectura -- mismo trato que
                # el cierre prematuro limpio de arriba: se reconecta.

            intentos_cierre_prematuro += 1
            if intentos_cierre_prematuro > _MAX_RECONEXIONES_STREAM_SESION:
                # Se agotaron los reintentos: se rinde y deja que quien
                # llama decida con lo que sepa (permisos/preguntas
                # pendientes, o el cierre honesto de ``cerrar_turno``) --
                # nunca una reconexión infinita.
                return vistos, False
            await asyncio.sleep(_ESPERA_ENTRE_RECONEXIONES_STREAM_SESION)

    async def _leer_stream_sesion_o_avisar_permiso(
        self,
        *,
        servidor: ServidorOpencode,
        opencode_session_id: str,
        vistos: int,
        traductor: TraductorDeTurno,
        session: Session,
        track_file: Callable[[str], None],
        notificacion_bus: asyncio.Event,
    ) -> tuple[int, bool, bool]:
        """Carrerea UNA pasada de ``_leer_stream_sesion_una_vez`` contra
        ``notificacion_bus`` (encendido por ``_vigilar_bus_para_sesion`` en
        cuanto ve un ``permission.v2.asked``/``question.v2.asked`` de ESTA
        sesión). Gana quien termine primero:

        - Si termina la lectura del stream (evento traducido, silencio de
          ``_TIEMPO_INACTIVIDAD_EVENTOS_OPENCODE``, o cierre del stream):
          se devuelve su resultado tal cual, igual que antes de esta ronda,
          con ``via_aviso_bus=False``.
        - Si gana el aviso del bus: se corta la lectura ahí mismo -- el
          stream de sesión NUNCA trae permisos ni preguntas (hallazgo 1 del
          docstring de ``ide_opencode_permisos``), así que no se pierde
          nada cancelándola -- y se devuelve sin tocar ``vistos`` (la
          próxima pasada, en el siguiente ``while True`` de
          ``_consumir_turno_opencode``, reconecta desde cero y retoma
          exactamente donde iba, igual que ya hacía tras un
          ``TimeoutError``). ``cancelado`` siempre sale ``False`` por esta
          vía: el aviso del bus nunca cancela el turno, solo adelanta la
          revisión de qué quedó pendiente.

        HALLAZGO REAL corregido en esta ronda: ``via_aviso_bus=True`` es una
        señal DISTINTA a un silencio real -- el bus avisa de CUALQUIER
        ``permission.v2.asked``, incluso uno que el puente de ``MotorOpencode``
        ya auto-concedió en modo Auto casi al mismo tiempo (la clasificación
        de la clase LECTURA/EDICIÓN/COMANDO se resuelve sola, ver
        ``PuenteDePermisos.procesar_solicitud``) -- para cuando
        ``_consumir_turno_opencode`` alcanza a llamar ``permisos_pendientes()``
        ya puede no haber nada pendiente, aunque el turno siga trabajando de
        verdad (más pasos, más herramientas, la respuesta final todavía no
        llegó). Antes de esta ronda, quien llama trataba "nada pendiente"
        igual sin importar el motivo y cerraba el turno como terminado --
        reproducido en vivo: el turno se cerraba "failed" justo después del
        primer ``tool.called`` de una escritura en modo Auto, con el archivo
        YA creado en disco. Devolver ``via_aviso_bus`` deja que quien llama
        distinga "aviso del bus que resultó ser una falsa alarma -- sigue
        leyendo" de "silencio real -- ahí sí se puede concluir"."""

        tarea_lectura = asyncio.ensure_future(
            self._leer_stream_sesion_una_vez(
                servidor=servidor,
                opencode_session_id=opencode_session_id,
                vistos=vistos,
                traductor=traductor,
                session=session,
                track_file=track_file,
            )
        )
        tarea_aviso = asyncio.ensure_future(notificacion_bus.wait())
        try:
            await asyncio.wait({tarea_lectura, tarea_aviso}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            if not tarea_aviso.done():
                tarea_aviso.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await tarea_aviso

        if tarea_lectura.done():
            vistos_final, cancelado = tarea_lectura.result()
            return vistos_final, cancelado, False

        # Ganó el aviso del bus: se corta esta pasada y se limpia la señal
        # (``Event`` no se "consume" solo, ver su propia documentación) para
        # que la próxima vuelta no crea que hay un aviso nuevo cuando en
        # realidad es este mismo que ya se atendió.
        notificacion_bus.clear()
        tarea_lectura.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tarea_lectura
        return vistos, False, True

    async def _consumir_turno_opencode(
        self,
        *,
        session: Session,
        servidor: ServidorOpencode,
        consulta: PuenteDePermisos,
        opencode_session_id: str,
        track_file: Callable[[str], None],
        notificacion_bus: asyncio.Event,
    ) -> bool:
        """El bucle de eventos de un turno ya admitido.

        AVISO INSTANTÁNEO (agregado en esta ronda): cada pasada por el
        stream de sesión corre carrereada contra ``notificacion_bus`` (ver
        ``_leer_stream_sesion_o_avisar_permiso``) -- en cuanto
        ``_vigilar_bus_para_sesion`` ve un ``permission.v2.asked``/
        ``question.v2.asked`` de ESTA sesión en el bus global, esta pasada
        se corta ahí mismo (el stream de sesión NUNCA trae permisos ni
        preguntas -- hallazgo 1 de ``ide_opencode_permisos`` -- así que no
        hay nada que perder cortándola) y se salta directo a
        ``permisos_pendientes``/``preguntas_pendientes`` de abajo, sin
        esperar los ``_TIEMPO_INACTIVIDAD_EVENTOS_OPENCODE`` segundos de
        silencio que antes eran la ÚNICA vía. Esa constante se queda como
        respaldo (bus caído, ventana de red perdida), nunca como el camino
        normal.

        LIMITACIÓN DECLARADA de esta ronda (regla 7 del encargo -- nada
        simulado, y esto se comprobó en vivo, no se supuso): el parámetro
        ``desde=`` de ``ServidorOpencode.eventos()`` documenta que acepta el
        ``id`` de evento (``evt_...``) para reanudar sin reproducir todo el
        historial -- pero contra un servidor real (v1.17.18), reconectar con
        ``desde=<ese evt_...>`` devuelve ``400 InvalidRequestError: Expected
        an integer, got NaN`` en ``["after"]``: la API real espera un
        ENTERO, no el id que ``EventoSesion.id`` expone, y este cimiento no
        expone ningún campo numérico equivalente. Así que este bucle nunca
        pasa ``desde``: siempre reconecta desde el principio (que sí
        funciona, comprobado) y se salta -- por CONTEO, no por id -- los
        eventos que ya procesó, guardando cuántos lleva vistos en
        ``session.metadata["opencode_eventos_vistos"]`` para que una
        conversación continuada (turno siguiente, mismo ``opencode_session_id``)
        tampoco re-escriba en el hilo de Edecán lo que ya escribió un turno
        anterior. Costo declarado: cada reconexión (dentro de un turno, o al
        abrir uno nuevo) re-lee el historial completo de la sesión de
        opencode desde el principio -- aceptable para una conversación de
        IDE, no gratis en una muy larga.

        CORRECCIÓN de un fallo real de verificación (no supuesto -- un
        verificador lo reprodujo en vivo: modo Manual, una edición real, y
        marcó ``no_cumple`` porque la única forma de progresar era abandonar
        el turno bloqueado y mandar uno nuevo en modo Auto, nunca conceder el
        permiso pedido y que el MISMO turno continuara). Antes de esta ronda,
        un permiso que quedaba en ``pedir_aprobacion`` se RECHAZABA solo acá
        mismo, con el argumento de que "no hay acción para aprobarlo a mitad
        de turno". Eso ya no es cierto:
        ``SessionManager.responder_permiso_agente`` existe ahora (mismo
        patrón que ``responder_pregunta_agente``, misma llamada real a
        ``PuenteDePermisos.responder_permiso`` que YA existía desde la ronda
        anterior sin nadie exponiéndola). Así que este bucle trata un permiso
        pendiente EXACTAMENTE como trata una pregunta pendiente: avisa una
        sola vez (evento estructurado ``agent_permission`` +
        ``cerrar_turno(permiso_pendiente=...)``), y sigue reintentando leer
        eventos hasta que alguien lo resuelva (por ``responder_permiso_agente``
        o por cualquier otra vía que hable con opencode directo -- el bucle
        no le importa quién, solo si ``permisos_pendientes()`` lo sigue
        listando) o hasta que se agote
        ``_TIEMPO_MAX_ESPERA_PERMISO_OPENCODE``, en cuyo caso el turno se
        cierra explícito, nunca mudo."""

        traductor = TraductorDeTurno()
        vistos_crudo = session.metadata.get("opencode_eventos_vistos")
        vistos = vistos_crudo if isinstance(vistos_crudo, int) and vistos_crudo >= 0 else 0
        inicio_espera_pregunta: float | None = None
        inicio_espera_permiso: float | None = None

        while True:
            if session.cancel_event.is_set():
                with contextlib.suppress(ErrorOpencode):
                    await servidor.interrumpir(opencode_session_id)
                return False

            vistos, cancelado, via_aviso_bus = await self._leer_stream_sesion_o_avisar_permiso(
                servidor=servidor,
                opencode_session_id=opencode_session_id,
                vistos=vistos,
                traductor=traductor,
                session=session,
                track_file=track_file,
                notificacion_bus=notificacion_bus,
            )
            if cancelado:
                with contextlib.suppress(ErrorOpencode):
                    await servidor.interrumpir(opencode_session_id)
                return False

            # Si algo sigue pidiendo aprobación acá es casi siempre la clase
            # PELIGROSA (las otras tres -- lectura/edición/comando bajo un
            # modo que las permite -- ya las resuelve solo el puente, incluso
            # en Auto: ver ``PuenteDePermisos.procesar_solicitud``). Antes de
            # esta ronda esto se rechazaba solo; ahora hay una acción real
            # (``SessionManager.responder_permiso_agente``) así que se trata
            # igual que una pregunta pendiente: se avisa una sola vez (con un
            # evento estructurado para que la interfaz pueda pintar botones
            # de conceder/rechazar, igual que ya hace con ``agent_question``)
            # y se sigue reintentando hasta que alguien lo resuelva o se
            # agote la espera -- nunca un cuelgue mudo (docs/opencode-motor.md
            # §3), y ya no un rechazo automático que obligaba a abandonar el
            # turno (el fallo real que esta ronda cierra).
            permisos = await consulta.permisos_pendientes(opencode_session_id)
            if permisos:
                permiso = permisos[0]
                ahora = time.monotonic()
                if inicio_espera_permiso is None:
                    inicio_espera_permiso = ahora
                    cierre = traductor.cerrar_turno(permiso_pendiente=permiso)
                    self._escribir_evento_opencode(session, cierre, track_file)
                    permiso_evt = traducir_permiso(permiso)
                    self._escribir_evento_opencode(session, permiso_evt, track_file)
                elif ahora - inicio_espera_permiso > _TIEMPO_MAX_ESPERA_PERMISO_OPENCODE:
                    recursos_txt = ", ".join(permiso.resources) or "(sin recursos)"
                    mensaje = (
                        "Se agotó la espera por tu aprobación; se rechaza el permiso pedido "
                        f"(«{permiso.action}» sobre {recursos_txt}) para no dejarlo huérfano "
                        "en opencode, y el turno se cierra. Si quieres que el agente lo "
                        "intente de nuevo, manda otro mensaje."
                    )
                    with contextlib.suppress(ErrorOpencode):
                        await consulta.responder_permiso(
                            opencode_session_id, permiso.id, conceder=False, mensaje=mensaje
                        )
                    session.append("status", mensaje)
                    # Ver "mensaje final confuso" en el hallazgo del segundo
                    # verificador: sin esta marca, ``_run_opencode_agent``
                    # agregaba ENCIMA de este mensaje claro un
                    # ``assistant_final`` genérico ("terminó sin entregar una
                    # respuesta final") más un evento ``error`` que no
                    # mencionan el permiso -- dos mensajes contradiciendo al
                    # único que sí explica qué pasó. Esta marca le dice a
                    # ``_run_opencode_agent`` que NO hace falta inventar nada
                    # más, el hilo ya tiene la explicación real.
                    session.metadata["opencode_cierre_explicado"] = mensaje
                    return False
                continue  # se vuelve a intentar leer eventos: puede que ya se haya resuelto
            else:
                inicio_espera_permiso = None

            preguntas = await consulta.preguntas_pendientes(opencode_session_id)
            if preguntas:
                ahora = time.monotonic()
                if inicio_espera_pregunta is None:
                    inicio_espera_pregunta = ahora
                    cierre = traductor.cerrar_turno(pregunta_pendiente=preguntas[0])
                    self._escribir_evento_opencode(session, cierre, track_file)
                    pregunta_evt = traducir_pregunta(preguntas[0])
                    self._escribir_evento_opencode(session, pregunta_evt, track_file)
                elif ahora - inicio_espera_pregunta > _TIEMPO_MAX_ESPERA_PREGUNTA_OPENCODE:
                    mensaje = (
                        "Se agotó la espera por tu respuesta a la pregunta del agente; el "
                        "turno se cierra. Si ya la respondiste, manda otro mensaje para que "
                        "el agente siga."
                    )
                    session.append("status", mensaje)
                    # Misma marca y mismo motivo que en el timeout de permiso
                    # de arriba -- evita el "assistant_final" genérico encima
                    # de un mensaje que ya explica exactamente qué se estaba
                    # esperando.
                    session.metadata["opencode_cierre_explicado"] = mensaje
                    return False
                continue  # se vuelve a intentar leer eventos: puede que ya haya respuesta
            else:
                inicio_espera_pregunta = None

            if via_aviso_bus:
                # HALLAZGO REAL (ver el docstring de
                # ``_leer_stream_sesion_o_avisar_permiso``): llegar acá con
                # nada pendiente NO significa que el turno terminó -- el bus
                # avisó de un ``permission.v2.asked`` que ya se resolvió solo
                # (auto-concedido por el puente de ``MotorOpencode`` en modo
                # Auto, o cualquier otra clase que ``procesar_solicitud``
                # decide sin dejar nada pendiente) mientras el agente sigue
                # trabajando de verdad. Antes de esta ronda esto cerraba el
                # turno como "failed" justo tras el primer ``tool.called`` de
                # una escritura en Auto, con el archivo YA creado en disco.
                # Se vuelve a leer el stream -- ahí sí llegarán
                # ``tool.success``/``step.ended``/el texto final, o un
                # silencio REAL (``TimeoutError``) que sí es señal legítima
                # de concluir.
                continue

            # Nada pendiente y silencio real (TimeoutError, o se agotaron los
            # reintentos de reconexión del stream de sesión -- ver
            # ``_MAX_RECONEXIONES_STREAM_SESION``): el turno terminó de
            # verdad, o al menos no hay forma confiable de saber más.
            cierre = traductor.cerrar_turno()
            self._escribir_evento_opencode(session, cierre, track_file)
            return cierre.tipo == "assistant_final"

    @staticmethod
    def _escribir_evento_opencode(
        session: Session, traducido: EventoTraducido, track_file: Callable[[str], None]
    ) -> None:
        session.append(
            traducido.tipo,
            traducido.texto,
            stream=traducido.stream,
            presentation=traducido.presentation,
        )
        if traducido.tipo == "file":
            match = _PATRON_ARCHIVO_OPENCODE.match(traducido.texto.strip())
            if match:
                track_file(match.group("ruta").strip())

    async def _interrumpir_opencode(self, session: Session, opencode_session_id: str) -> None:
        assert self.motor_opencode is not None
        workspace_root = self.workspaces.root(str(session.metadata["workspace_id"]))
        servidor = await self.motor_opencode.servidor_para(workspace_root)
        await servidor.interrumpir(opencode_session_id)

    async def _cambiar_agente_en_vivo(
        self, session: Session, opencode_session_id: str, agente: str
    ) -> None:
        assert self.motor_opencode is not None
        workspace_root = self.workspaces.root(str(session.metadata["workspace_id"]))
        servidor = await self.motor_opencode.servidor_para(workspace_root)
        await servidor.cambiar_agente(opencode_session_id, agente)

    async def _aplicar_referencia_en_vivo(
        self,
        servidor: ServidorOpencode,
        opencode_session_id: str,
        referencia: ReferenciaModeloEsfuerzo,
    ) -> ReferenciaModeloEsfuerzo:
        """Manda ``referencia`` a una sesión de opencode YA viva --
        compartido por ``fijar_esfuerzo``, ``set_modelo_agente`` y la
        reafirmación de cada turno de continuación (``_turno_opencode``).

        Antes de mandarla, busca si el modelo declara una variante para su
        ``reasoning_effort`` (``ide_opencode_motor.variante_de_esfuerzo`` --
        ver su docstring y la sección "selector vs. esfuerzo" del docstring
        de ese módulo para por qué esa es la ÚNICA vía real que expone
        opencode hoy). Devuelve la referencia REALMENTE aplicada -- con
        ``variante`` puesto si se encontró una, o ``None`` si no -- para que
        el llamador sepa si el ``reasoning_effort`` tuvo o no una vía en
        vivo; nunca se calla esa diferencia."""

        variante = await variante_de_esfuerzo(
            servidor,
            proveedor=referencia.provider_id,
            modelo=referencia.model_id,
            reasoning_effort=referencia.reasoning_effort,
        )
        aplicada = dataclasses.replace(referencia, variante=variante)
        await servidor.cambiar_modelo(
            opencode_session_id,
            proveedor=aplicada.provider_id,
            modelo=aplicada.model_id,
            variante=aplicada.variante,
        )
        return aplicada

    async def _cambiar_modelo_en_vivo(
        self, session: Session, opencode_session_id: str, referencia: ReferenciaModeloEsfuerzo
    ) -> ReferenciaModeloEsfuerzo:
        assert self.motor_opencode is not None
        workspace_root = self.workspaces.root(str(session.metadata["workspace_id"]))
        servidor = await self.motor_opencode.servidor_para(workspace_root)
        return await self._aplicar_referencia_en_vivo(servidor, opencode_session_id, referencia)

    def _referencia_modelo_para_sesion(
        self, session: Session, nivel_nombre: str
    ) -> ReferenciaModeloEsfuerzo:
        """La parte central del arreglo de esta ronda: "el selector manda
        QUÉ modelo, el esfuerzo manda CUÁNTO piensa ESE modelo -- nunca lo
        cambia". El modelo elegido por la persona
        (``session.metadata['model']``, guardado por ``start_agent``/
        ``_arrancar_turno_de_continuacion``/``set_modelo_agente``) gana
        SIEMPRE que siga declarado en el catálogo del IDE
        (``edecan_llm.task_router.modelo_ide_permitido`` -- lee
        ``config/modelos.yml -> modelos_ide``, la misma lista que ya sirve
        ``GET /v1/ide/models``). Si la persona no eligió nada, o lo que
        eligió ya no está en el catálogo (el YAML cambió a mitad de vuelo),
        cae al defecto por perfil (``modelo_para`` -- mismo criterio que
        ``TaskRouter.decide`` ya usa para el selector del chat)."""

        modelo_elegido = session.metadata.get("model")
        if isinstance(modelo_elegido, str) and modelo_elegido.strip():
            elegido = modelo_elegido.strip()
            if modelo_ide_permitido(elegido):
                return referencia_para_modelo(elegido, nivel_nombre)
            logger.warning(
                "El modelo elegido para la sesión %s (%r) ya no está en el catálogo del "
                "IDE -- cae al defecto del perfil.",
                session.id,
                elegido,
            )
        return modelo_para(_PERFIL_OPENCODE_IDE, nivel_nombre)

    def set_modelo_agente(self, session_id: str, model: str) -> dict[str, Any]:
        """Cambia el modelo elegido para esta sesión -- encargo punto 4: "el
        selector, con la sesión viva, se aplica DE INMEDIATO", mismo patrón
        que ``set_modo_agente`` para plan/build. El esfuerzo VIGENTE de la
        sesión NO se toca (encargo punto 5: "cambiar el modelo no puede
        resetear el esfuerzo") -- se lee y se manda junto con el modelo
        nuevo, nunca se reinicia."""
        if not isinstance(model, str) or not model.strip():
            raise IDESessionError("El modelo indicado no es válido.")
        modelo = model.strip()
        if not modelo_ide_permitido(modelo):
            raise IDESessionError(f"Modelo desconocido para el IDE: «{modelo}».")
        session = self._get(session_id, "agent")
        session.metadata["model"] = modelo
        opencode_session_id = session.metadata.get("opencode_session_id")
        if isinstance(opencode_session_id, str) and opencode_session_id:
            nivel = self.effort_store.obtener(session_id)
            referencia = referencia_para_modelo(modelo, nivel.nombre)
            with contextlib.suppress(Exception):
                self._ejecutar_opencode(
                    self._cambiar_modelo_en_vivo(session, opencode_session_id, referencia),
                    timeout=15.0,
                )
        self._save()
        return {"model": modelo}

    def set_modo_agente(
        self, session_id: str, modo: str, motivo: str | None = None
    ) -> dict[str, Any]:
        """Fija el modo (manual/aceptar_ediciones/plan/auto) de una sesión --
        encargo punto 6: alimenta ``obtener_modo`` de ``PuenteDePermisos``,
        así que a partir de acá SÍ frena de verdad. Si la sesión ya tiene una
        sesión de opencode viva, el cambio de agente (plan/build) se aplica
        DE INMEDIATO, no en el próximo turno."""
        session = self._get(session_id, "agent")
        modo_obj = self.modo_agente_store.fijar(session_id, modo, motivo)
        session.metadata["modo_agente_inicializado"] = True
        opencode_session_id = session.metadata.get("opencode_session_id")
        if isinstance(opencode_session_id, str) and opencode_session_id:
            agente = "plan" if modo_obj.value == "plan" else "build"
            with contextlib.suppress(Exception):
                self._ejecutar_opencode(
                    self._cambiar_agente_en_vivo(session, opencode_session_id, agente),
                    timeout=15.0,
                )
        return self.modo_agente_store.public(session_id)

    def fijar_esfuerzo(self, session_id: str, nivel: str) -> Any:
        """Fija el nivel de esfuerzo de la sesión -- encargo punto 3: esto YA
        NO cambia el modelo (esa era la queja real del dueño: "elijo Kimi,
        toco el esfuerzo, y mi elección se pierde"). El modelo elegido por
        la persona (o el defecto del perfil si no eligió nada) se queda
        exactamente igual; lo único que cambia es CUÁNTO piensa ESE modelo.

        Si la sesión ya tiene una sesión de opencode viva, se intenta
        mandarle el ``reasoning_effort`` de inmediato vía
        ``_aplicar_referencia_en_vivo`` -- pero solo hay una vía real para
        eso si el modelo vigente declara una variante con ese nombre (ver
        ``ide_opencode_motor.variante_de_esfuerzo``). Si no la declara (hoy,
        ninguno de los cinco modelos de Workers AI la declara -- comprobado
        contra ``/doc``), no hay NINGUNA vía para mandárselo a una sesión ya
        viva: se deja constancia en ``session.metadata['esfuerzo_sin_via_en_vivo']``
        y en el hilo de la sesión en vez de fingir que se aplicó. El nivel
        SÍ queda guardado (``effort_store.fijar`` ya corrió arriba) para
        cuando exista esa vía -- nunca se pierde."""
        nivel_obj = self.effort_store.fijar(session_id, nivel)
        with self._lock:
            session = self._sessions.get(session_id)
        opencode_session_id = session.metadata.get("opencode_session_id") if session else None
        if session is not None and isinstance(opencode_session_id, str) and opencode_session_id:
            referencia = self._referencia_modelo_para_sesion(session, nivel_obj.nombre)
            aplicada: ReferenciaModeloEsfuerzo | None = None
            try:
                aplicada = self._ejecutar_opencode(
                    self._cambiar_modelo_en_vivo(session, opencode_session_id, referencia),
                    timeout=15.0,
                )
            except Exception:  # noqa: BLE001 -- best-effort, ver docstring de arriba
                aplicada = None
            if aplicada is not None and aplicada.variante is None:
                session.metadata["esfuerzo_sin_via_en_vivo"] = nivel_obj.nombre
                session.append(
                    "status",
                    f"El nivel de esfuerzo cambió a «{nivel_obj.nombre}», pero el modelo "
                    f"vigente ({aplicada.model_id}) no declara ninguna variante en opencode "
                    "para mandarle el reasoning_effort a una sesión ya viva -- no hay "
                    "ninguna vía en vivo para esto hoy. El modelo SIGUE siendo el elegido; "
                    "el nivel queda guardado para en cuanto exista esa vía.",
                )
            elif aplicada is not None:
                session.metadata.pop("esfuerzo_sin_via_en_vivo", None)
            self._save()
        return nivel_obj

    def _requiere_sesion_opencode(self, session_id: str) -> tuple[Session, Path, str]:
        session = self._get(session_id, "agent")
        opencode_session_id = session.metadata.get("opencode_session_id")
        if not isinstance(opencode_session_id, str) or not opencode_session_id:
            raise IDESessionError(
                "Esta sesión no tiene una sesión de opencode asociada todavía (motor "
                "«viejo», o esta conversación no arrancó ningún turno sobre opencode)."
            )
        workspace_root = self.workspaces.root(str(session.metadata["workspace_id"]))
        return session, workspace_root, opencode_session_id

    async def _listar_preguntas_opencode(
        self, workspace_root: Path, opencode_session_id: str
    ) -> list[SolicitudPregunta]:
        assert self.motor_opencode is not None
        servidor = await self.motor_opencode.servidor_para(workspace_root)
        puente = PuenteDePermisos(servidor, obtener_modo=self._modo_para_opencode_session)
        try:
            return await puente.preguntas_pendientes(opencode_session_id)
        finally:
            await puente.cerrar()

    def listar_preguntas_agente(self, session_id: str) -> dict[str, Any]:
        """Encargo punto 7: expone lo que el agente le está preguntando a la
        persona (``question.v2.asked``), nunca respondido por este módulo."""
        _session, workspace_root, opencode_session_id = self._requiere_sesion_opencode(
            session_id
        )
        preguntas = self._ejecutar_opencode(
            self._listar_preguntas_opencode(workspace_root, opencode_session_id), timeout=30.0
        )
        return {"preguntas": [json.loads(traducir_pregunta(p).texto) for p in preguntas]}

    async def _responder_pregunta_opencode(
        self,
        workspace_root: Path,
        opencode_session_id: str,
        request_id: str,
        respuestas: list[list[str]],
    ) -> None:
        assert self.motor_opencode is not None
        servidor = await self.motor_opencode.servidor_para(workspace_root)
        puente = PuenteDePermisos(servidor, obtener_modo=self._modo_para_opencode_session)
        try:
            await puente.responder_pregunta(opencode_session_id, request_id, respuestas)
        finally:
            await puente.cerrar()

    def responder_pregunta_agente(
        self, session_id: str, request_id: str, respuestas: list[list[str]]
    ) -> dict[str, Any]:
        session, workspace_root, opencode_session_id = self._requiere_sesion_opencode(session_id)
        self._ejecutar_opencode(
            self._responder_pregunta_opencode(
                workspace_root, opencode_session_id, request_id, respuestas
            ),
            timeout=30.0,
        )
        session.append(
            "status", "Respondiste la pregunta del agente; el turno sigue en cuanto la vea."
        )
        return {"ok": True}

    async def _rechazar_pregunta_opencode(
        self, workspace_root: Path, opencode_session_id: str, request_id: str
    ) -> None:
        assert self.motor_opencode is not None
        servidor = await self.motor_opencode.servidor_para(workspace_root)
        puente = PuenteDePermisos(servidor, obtener_modo=self._modo_para_opencode_session)
        try:
            await puente.rechazar_pregunta(opencode_session_id, request_id)
        finally:
            await puente.cerrar()

    def rechazar_pregunta_agente(self, session_id: str, request_id: str) -> dict[str, Any]:
        session, workspace_root, opencode_session_id = self._requiere_sesion_opencode(session_id)
        self._ejecutar_opencode(
            self._rechazar_pregunta_opencode(workspace_root, opencode_session_id, request_id),
            timeout=30.0,
        )
        session.append("status", "Rechazaste la pregunta del agente.")
        return {"ok": True}

    # ----------------------------------------------------------------- #
    # Permisos del agente a mitad de turno -- cierra el fallo real que un
    # verificador reprodujo en vivo (modo Manual, ver docstring de
    # ``_consumir_turno_opencode``): antes de esta ronda no había ninguna
    # acción para conceder/rechazar un permiso que quedó en
    # ``pedir_aprobacion``, así que el propio bucle lo rechazaba solo. Mismo
    # patrón EXACTO que las tres funciones de preguntas de arriba --
    # ``PuenteDePermisos.responder_permiso`` ya existía desde la ronda
    # anterior, solo faltaba exponerlo.
    # ----------------------------------------------------------------- #

    async def _listar_permisos_opencode(
        self, workspace_root: Path, opencode_session_id: str
    ) -> list[SolicitudPermiso]:
        assert self.motor_opencode is not None
        servidor = await self.motor_opencode.servidor_para(workspace_root)
        puente = PuenteDePermisos(servidor, obtener_modo=self._modo_para_opencode_session)
        try:
            return await puente.permisos_pendientes(opencode_session_id)
        finally:
            await puente.cerrar()

    def listar_permisos_agente(self, session_id: str) -> dict[str, Any]:
        """Lo que el agente está pidiendo permiso para hacer ahora mismo
        (``permission.v2.asked`` que quedó en ``pedir_aprobacion`` -- las
        otras tres decisiones de la tabla de modos ya se resuelven solas y
        nunca aparecen acá)."""
        _session, workspace_root, opencode_session_id = self._requiere_sesion_opencode(
            session_id
        )
        permisos = self._ejecutar_opencode(
            self._listar_permisos_opencode(workspace_root, opencode_session_id), timeout=30.0
        )
        return {"permisos": [json.loads(traducir_permiso(p).texto) for p in permisos]}

    async def _responder_permiso_opencode(
        self,
        workspace_root: Path,
        opencode_session_id: str,
        request_id: str,
        *,
        conceder: bool,
        recordar: bool,
        mensaje: str | None,
    ) -> None:
        assert self.motor_opencode is not None
        servidor = await self.motor_opencode.servidor_para(workspace_root)
        puente = PuenteDePermisos(servidor, obtener_modo=self._modo_para_opencode_session)
        try:
            await puente.responder_permiso(
                opencode_session_id,
                request_id,
                conceder=conceder,
                recordar=recordar,
                mensaje=mensaje,
            )
        finally:
            await puente.cerrar()

    def responder_permiso_agente(
        self,
        session_id: str,
        request_id: str,
        *,
        conceder: bool,
        recordar: bool = False,
        mensaje: str | None = None,
    ) -> dict[str, Any]:
        """La persona concede o rechaza un permiso pedido a mitad de turno --
        el turno bloqueado en ``_consumir_turno_opencode`` lo nota en su
        próxima vuelta de encuesta (``permisos_pendientes`` ya no lo lista) y
        sigue SOLO, sin que haga falta mandar un mensaje nuevo. ``recordar``
        se propaga tal cual a ``PuenteDePermisos.responder_permiso``, que ya
        falla cerrado (``NoSePuedeRecordarError``) si la clase es
        irreversible o si opencode no ofrece esta solicitud como recordable
        -- ese error se deja subir tal cual, nunca se traga en silencio."""
        session, workspace_root, opencode_session_id = self._requiere_sesion_opencode(session_id)
        self._ejecutar_opencode(
            self._responder_permiso_opencode(
                workspace_root,
                opencode_session_id,
                request_id,
                conceder=conceder,
                recordar=recordar,
                mensaje=mensaje,
            ),
            timeout=30.0,
        )
        session.append(
            "status",
            "Concediste el permiso pedido; el turno sigue en cuanto opencode lo note."
            if conceder
            else "Rechazaste el permiso pedido; el turno se cierra si no tenía otra vía.",
        )
        return {"ok": True}

    def _entregar_pendientes_al_cerrar(
        self,
        session: Session,
        *,
        skill_context: str | None,
        model: str | None,
        mcp_tools: list[dict[str, Any]] | None,
    ) -> None:
        """Qué pasa con lo encolado cuando el turno termina antes de leerlo.

        Un mensaje encolado que el turno no alcanzó a leer es el caso que más
        duele si se deja mal: la persona lo escribió, vio que quedó "en cola" y
        se quedó esperando una respuesta que nunca iba a llegar. Así que solo
        hay dos salidas honestas, y las dos quedan escritas en el hilo:

        - La sesión quedó lista para otro turno (``completed``/``failed``):
          lo encolado SE CONVIERTE en el turno siguiente, tal como habría
          pasado si la persona lo hubiera mandado un segundo después. Se
          arrastran ``skill_context``/``model``/``mcp_tools`` del turno que
          acaba de cerrar para que el turno promovido no pierda capacidades
          por el camino.
        - No se puede arrancar otro turno (la persona canceló, o el turno se
          detuvo esperando su decisión sobre un plan): se dice CLARO que no se
          entregó, con el texto a la vista para copiarlo y reenviarlo.

        Se llama siempre con ``session._turn_lock`` tomado.
        """
        pendientes = session.drain_user_messages()
        if not pendientes:
            return
        textos = [str(item.get("text") or "") for item in pendientes]
        textos = [texto for texto in textos if texto]
        if not textos:
            return
        status = str(session.metadata.get("status") or "")
        motivo = {
            "cancelled": "cancelaste el turno",
            "plan_pending": "el turno se detuvo a esperar tu decisión sobre el plan",
        }.get(status, f"el turno terminó en estado «{status}»")
        if status in _AGENT_PROMOTABLE_STATUSES:
            session.append(
                "status",
                f"El turno terminó antes de leer {len(textos)} mensaje(s) que mandaste; "
                "se entregan ahora como el turno siguiente.",
            )
            try:
                # Varios mensajes se unen en un solo turno, en orden: es lo
                # mismo que el modelo habría visto si el turno hubiera durado
                # una vuelta más.
                self._continue_agent_session(
                    session,
                    "\n\n".join(textos),
                    title=None,
                    attachments=None,
                    skill_context=skill_context,
                    model=model,
                    mcp_tools=mcp_tools,
                )
                return
            except Exception as exc:  # noqa: BLE001 - ni así puede perderse el mensaje
                session.append("error", str(exc)[:2000], stream="stderr")
                motivo = "no se pudo arrancar el turno siguiente"
        for texto in textos:
            session.append("user_undelivered", texto)
        session.append(
            "status",
            f"{len(textos)} mensaje(s) que mandaste NO se entregaron porque {motivo}. "
            "Vuelve a mandarlos si siguen aplicando.",
        )

    def turn_diff(self, session_id: str) -> dict[str, Any]:
        """Archivos tocados en el ÚLTIMO turno de este agente, con contenido
        "antes"/"después" listo para `components/ide/DiffReview.tsx` -- 1.2
        del plan de paridad. Sin checkpoint (turno viejo, anterior a este
        cableado, o que no llegó a abrir uno) devuelve la lista vacía: no hay
        nada que decidir aceptar o rechazar.
        """
        session = self._get(session_id, "agent")
        checkpoint_id = session.metadata.get("turn_checkpoint_id")
        if not checkpoint_id:
            return {"checkpoint_id": None, "sealed": False, "files": []}
        checkpoint = self.checkpoints.get(str(checkpoint_id))
        workspace_id = str(session.metadata["workspace_id"])
        files: list[dict[str, Any]] = []
        for path in checkpoint["paths"]:
            before = self.checkpoints.read_before(str(checkpoint_id), path)
            try:
                after_content: str | None = self.files.read(workspace_id, path)["content"]
                after_exists = True
            except IDEFileError:
                after_content = None
                after_exists = False
            if before["status"] in ("skipped_too_large", "skipped_budget"):
                kind = "unavailable"
                reason = (
                    "El archivo es demasiado grande para guardar un punto de "
                    "control (supera el tope por archivo o el presupuesto del "
                    "turno); no hay contenido «antes» que comparar."
                )
            elif before["status"] == "absent":
                kind = "added" if after_exists else "unavailable"
                reason = (
                    None
                    if after_exists
                    else "El agente creó y luego borró este archivo en el mismo turno."
                )
            elif not after_exists:
                kind = "deleted"
                reason = None
            else:
                kind = "modified"
                reason = None
            files.append(
                {
                    "path": path,
                    "kind": kind,
                    "before_content": before["content"],
                    "after_content": after_content,
                    "unavailable_reason": reason,
                }
            )
        return {
            "checkpoint_id": str(checkpoint_id),
            "sealed": bool(checkpoint["sealed"]),
            "files": files,
        }

    def reject_turn_file(self, session_id: str, path: str) -> dict[str, Any]:
        """Deshace UN archivo del último turno, restaurando su contenido
        "antes" desde el checkpoint (1.2 del plan de paridad). Reporta
        conflicto en vez de pisar en silencio si alguien lo tocó a mano
        después de que el turno cerró -- ver `ide_checkpoints.restore`."""
        session = self._get(session_id, "agent")
        checkpoint_id = session.metadata.get("turn_checkpoint_id")
        if not checkpoint_id:
            raise IDESessionError("Este turno no tiene un punto de control asociado.")
        return self.checkpoints.restore_file(str(checkpoint_id), path)

    def turn_cost(self, session_id: str) -> dict[str, Any]:
        """Contabilidad de costo (`ide_costos.analizar_tarea`) del ÚLTIMO
        turno de este agente -- 4 del plan de paridad. Se acota con
        `turn_user_cursor` (no toda la sesión reusada) para no mezclar
        turnos anteriores en el mismo hilo, el hallazgo 2 real de
        `test_piezas_ide_integrables.py`."""
        session = self._get(session_id, "agent")
        with session._lock:
            events = list(session.events)
        anchor = session.metadata.get("turn_user_cursor")
        if isinstance(anchor, int):
            turn_events = [
                event
                for event in events
                if isinstance(event.get("cursor"), int) and event["cursor"] >= anchor
            ]
        else:
            turn_events = events
        started_at = next(
            (event.get("timestamp") for event in turn_events if event.get("cursor") == anchor),
            None,
        )
        resultado = analizar_tarea(
            turn_events,
            modelo=session.metadata.get("model"),
            started_at=started_at,
            ended_at=session.metadata.get("ended_at"),
        )
        return resultado.resumen()

    def _mensaje_mcp_no_pendiente(self, session: Session) -> str:
        """Mensaje de ``pending_mcp``/``resolve_mcp`` cuando no hay nada que
        resolver -- distingue el caso "de verdad ya se resolvió" del caso
        "esta sesión corre sobre opencode, que no tiene esta superficie
        todavía" (gap declarado, no un bug: ``ServidorOpencode.enviar_prompt``
        no intercepta llamadas MCP directas -- solo el motor viejo puebla
        ``self._mcp_pending``). Antes esto daba el mismo mensaje genérico en
        los dos casos; un verificador lo marcó como "podría ser más
        específico" -- esta ronda lo hace."""

        opencode_session_id = session.metadata.get("opencode_session_id")
        if isinstance(opencode_session_id, str) and opencode_session_id:
            return (
                "Esta sesión corre sobre el motor opencode, que todavía no soporta MCP "
                "directo (solo el motor «viejo» puebla solicitudes MCP pendientes) -- no es "
                "que se haya resuelto, es que esta capacidad no existe para esta sesión."
            )
        return "La solicitud MCP ya no está pendiente."

    def pending_mcp(self, session_id: str, call_id: str) -> dict[str, Any]:
        session = self._get(session_id, "agent")
        with self._lock:
            row = self._mcp_pending.get((session_id, call_id))
            if row is None:
                raise IDESessionError(self._mensaje_mcp_no_pendiente(session))
            return {
                "session_id": session_id,
                "call_id": call_id,
                "name": row["name"],
                "arguments": dict(row["arguments"]),
            }

    def resolve_mcp(self, session_id: str, call_id: str, result: dict[str, Any]) -> dict[str, Any]:
        session = self._get(session_id, "agent")
        with self._lock:
            row = self._mcp_pending.get((session_id, call_id))
            if row is None:
                raise IDESessionError(self._mensaje_mcp_no_pendiente(session))
            row["result"] = dict(result)
            waiter = row["waiter"]
        waiter.set()
        return {"accepted": True}

    # ------------------------------------------------------------------ #
    # Cabo 1 del encargo de integración: plan previo (``ide_plan``) +
    # reparto entre sub-agentes (``ide_reparto``/``ide_equipo``) conectados
    # al ciclo real del agente. Ver el docstring de ``self.plans`` en
    # ``__init__`` y el gate de la tool ``proponer_plan`` en
    # ``ide_workers_agent.WorkersIDEAgent.run``.
    # ------------------------------------------------------------------ #

    def get_active_plan(self, session_id: str) -> dict[str, Any] | None:
        """El plan vivo (``proposed``/``executing``) de esta sesión, si hay
        uno -- para que la UI sepa si debe mostrar la tarjeta de aprobación."""
        self._get(session_id, "agent")
        plan = self.plans.get_active_for_session(session_id)
        return plan.public() if plan is not None else None

    def _plan_de_la_sesion(self, session_id: str, plan_id: str) -> Plan:
        """Valida la titularidad ANTES de dejar que ``ide_plan.PlanStore``
        mute nada -- editar/rechazar/aprobar el ``plan_id`` de OTRA sesión
        por un id adivinado o mal pasado no debe alcanzar a tocar ese estado
        ajeno, ni siquiera para fallar a medias."""
        plan = self.plans.get(plan_id)
        if plan.session_id != session_id:
            raise IDESessionError("Este plan no pertenece a esta sesión.")
        return plan

    def edit_plan(self, session_id: str, plan_id: str, steps: list[str]) -> dict[str, Any]:
        """La persona corrige el desglose ANTES de aprobar -- solo mientras
        sigue ``proposed`` (ver ``ide_plan.PlanStore.edit``)."""
        self._get(session_id, "agent")
        self._plan_de_la_sesion(session_id, plan_id)
        plan = self.plans.edit(plan_id, steps)
        return {"plan": plan.public()}

    def reject_plan(
        self, session_id: str, plan_id: str, reason: str | None = None
    ) -> dict[str, Any]:
        """La persona dice que no: el plan se descarta y la sesión queda
        libre para el siguiente mensaje (mismo estado ``cancelled`` que
        cancelar un turno normal -- ver ``_AGENT_CONTINUABLE_STATUSES``)."""
        session = self._get(session_id, "agent")
        self._plan_de_la_sesion(session_id, plan_id)
        plan = self.plans.reject(plan_id, reason)
        session.append("status", "Plan rechazado por la persona; no se ejecutó ningún paso.")
        if session.metadata.get("status") == "plan_pending":
            session.metadata["status"] = "cancelled"
            session.metadata["ended_at"] = _now()
            self._save()
        return {"plan": plan.public()}

    def approve_plan(self, session_id: str, plan_id: str) -> dict[str, Any]:
        """La persona aprueba: arranca la ejecución REAL (puntos 2 y 3 del
        encargo) -- reparte los pasos independientes entre sub-agentes
        (``ide_reparto``/``ide_equipo``) y corre en orden los que comparten
        archivos o no declararon ninguno. Ver ``_run_plan_execution`` para el
        detalle y el gate de seguridad que impide que un sub-agente se salte
        una confirmación humana."""
        session = self._get(session_id, "agent")
        if session.metadata.get("status") in _AGENT_TURN_IN_PROGRESS_STATUSES:
            raise IDESessionError(
                "Ya hay un turno en curso en esta sesión; espera a que termine."
            )
        self._plan_de_la_sesion(session_id, plan_id)
        plan = self.plans.approve(plan_id)
        rutas_por_paso = self._plan_routes.get(plan_id) or [None] * len(plan.steps)
        session.metadata["status"] = "running"
        session.metadata["ended_at"] = None
        session.metadata["exit_code"] = None
        session.metadata["last_plan_id"] = plan_id
        session.cancel_event.clear()
        session.append("status", f"Plan aprobado: «{plan.goal}» ({len(plan.steps)} paso(s)).")
        self._save()
        threading.Thread(
            target=self._run_plan_execution,
            args=(session, plan_id, rutas_por_paso, None),
            daemon=True,
            name=f"edecan-plan-{session.id}",
        ).start()
        return {"plan": plan.public()}

    def resume_plan(self, session_id: str) -> dict[str, Any]:
        """Retoma el último plan de esta sesión sin repetir los pasos que ya
        terminaron (punto 3 del encargo).

        Fuente de verdad de "qué ya se completó": el snapshot que ESTA clase
        guarda al cerrar la corrida anterior (``self._plan_progress``), no
        ``ide_plan.PlanStore`` -- esa máquina de estados es secuencial de a
        un paso activo a la vez (ver su docstring) y no modela un plan
        retomado a medias con pasos corridos en paralelo; una vez ``failed``
        queda terminal ahí. Lo que la persona VE (eventos de la sesión y el
        resumen final) sí refleja el avance real.
        """
        session = self._get(session_id, "agent")
        if session.metadata.get("status") in _AGENT_TURN_IN_PROGRESS_STATUSES:
            raise IDESessionError(
                "Ya hay un turno en curso en esta sesión; espera a que termine."
            )
        plan_id = session.metadata.get("last_plan_id")
        if not isinstance(plan_id, str) or plan_id not in self._plan_progress:
            raise IDESessionError("No hay un plan previo de esta sesión para retomar.")
        plan = self.plans.get(plan_id)
        progreso_previo = self._plan_progress[plan_id]
        rutas_por_paso = self._plan_routes.get(plan_id) or [None] * len(plan.steps)
        pasos = self._pasos_reparto_desde_plan(plan, rutas_por_paso)
        pendientes = [
            paso
            for paso in pasos
            if progreso_previo.get(paso.id) is None
            or progreso_previo[paso.id].estado != "completada"
        ]
        if not pendientes:
            return {"plan": plan.public(), "mensaje": "Este plan ya se completó por entero."}
        session.metadata["status"] = "running"
        session.metadata["ended_at"] = None
        session.metadata["exit_code"] = None
        session.cancel_event.clear()
        session.append(
            "status",
            f"Retomando el plan: {len(pendientes)} de {len(pasos)} paso(s) pendientes "
            "(los ya completados no se repiten).",
        )
        self._save()
        threading.Thread(
            target=self._run_plan_execution,
            args=(session, plan_id, rutas_por_paso, progreso_previo),
            daemon=True,
            name=f"edecan-plan-{session.id}",
        ).start()
        return {"session": session.public()}

    def _pasos_reparto_desde_plan(
        self, plan: Plan, rutas_por_paso: list[tuple[str, ...] | None]
    ) -> list[PasoReparto]:
        """Convierte los pasos de un ``ide_plan.Plan`` ya aprobado en
        ``ide_reparto.PasoReparto``: rutas explícitas (las que el modelo
        declaró al llamar ``proponer_plan``) primero, y si no hay, la
        heurística de respaldo ``rutas_desde_texto`` sobre la propia
        descripción -- sin ninguna de las dos, el paso va solo (la regla dura
        de ``ide_reparto``: ante la duda, secuencial)."""
        pasos: list[PasoReparto] = []
        for i, step in enumerate(plan.steps):
            rutas = rutas_por_paso[i] if i < len(rutas_por_paso) else None
            if not rutas:
                rutas = rutas_desde_texto(step.description)
            titulo = (
                step.description if len(step.description) <= 80 else step.description[:77] + "..."
            )
            pasos.append(
                PasoReparto(
                    id=step.id, titulo=titulo, instrucciones=step.description, rutas=rutas
                )
            )
        return pasos

    def _run_plan_execution(
        self,
        session: Session,
        plan_id: str,
        rutas_por_paso: list[tuple[str, ...] | None],
        progreso_previo: dict[str, EstadoPaso] | None,
    ) -> None:
        """Ejecuta (``approve_plan``) o retoma (``resume_plan``) un plan ya
        aprobado. Cada paso es un turno normal y acotado de
        ``WorkersIDEAgent``: su propio ``MAX_TOOL_ROUNDS``, y su propio gate
        de herramientas peligrosas -- si un paso pide una que necesita
        confirmación humana, ESE paso se cuenta como fallido en vez de abrir
        un atajo (ver ``_ejecutar_paso_de_plan``). Corre en su propio hilo con
        su propio ``asyncio.run``, mismo patrón que ``_run_workers_agent``.

        El cuerpo entero va dentro del ``try``, no solo la corrida del
        reparto: el ``finally`` es lo único que le da una salida a lo que la
        persona haya encolado, y un fallo en la preparación (leer el plan,
        abrir el checkpoint) dejaba la sesión "running" para siempre -- con
        mensajes aceptados como "en cola" que nadie iba a drenar nunca.
        """
        checkpoint_id: str | None = None
        # ``None`` = el reparto ni siquiera llegó a correr (algo reventó en la
        # preparación); ``True``/``False`` = corrió y ese fue su resultado. El
        # cierre de abajo necesita distinguir los dos casos: el segundo ya dejó
        # su propia respuesta final en el hilo, el primero todavía no.
        exito_del_plan: bool | None = None
        error_de_preparacion: Exception | None = None
        try:
            turn_start_cursor = session.append(
                "status", "Ejecutando el plan aprobado, paso a paso."
            )["cursor"]
            session.metadata["turn_start_cursor"] = turn_start_cursor
            self._save()

            workspace_id = str(session.metadata["workspace_id"])
            plan = self.plans.get(plan_id)
            pasos = self._pasos_reparto_desde_plan(plan, rutas_por_paso)
            pasos_por_id = {paso.id: paso for paso in pasos}
            if progreso_previo:
                pasos_a_correr = [
                    paso
                    for paso in pasos
                    if progreso_previo.get(paso.id) is None
                    or progreso_previo[paso.id].estado != "completada"
                ]
            else:
                pasos_a_correr = pasos

            rules = self._project_rules_for(workspace_id)
            rules_block = rules.as_prompt_block() if rules else None
            # Un solo checkpoint para el plan entero (no uno por paso): alcanza
            # para poder deshacer todo lo que tocó, y evita abrir N puntos de
            # control para una sola aprobación humana.
            checkpoint_id = self._create_turn_checkpoint(workspace_id, f"Plan: {plan.goal}")
            session.metadata["turn_checkpoint_id"] = checkpoint_id

            def track_file(path: str) -> None:
                if checkpoint_id is None:
                    return
                with contextlib.suppress(IDECheckpointError, OSError):
                    self.checkpoints.track(checkpoint_id, path)

            def on_evento(id_subtarea: str, tipo: str, texto: str) -> None:
                titulo = (
                    pasos_por_id[id_subtarea].titulo
                    if id_subtarea in pasos_por_id
                    else id_subtarea
                )
                session.append("plan_step", f"[{titulo}] {tipo}: {texto}"[:2000])

            async def runner(sub: Any, _control: Any) -> str:
                # ``PlanificadorReparto`` convierte cada ``PasoReparto`` a
                # ``ide_equipo.Subtarea`` antes de invocar el runner -- se
                # recupera el ``PasoReparto`` original (mismo ``id``) para el
                # texto de alcance real, ver ``_ejecutar_paso_de_plan``.
                paso_original = pasos_por_id[sub.id]
                return await self._ejecutar_paso_de_plan(
                    session, workspace_id, paso_original, rules_block, track_file
                )

            # Un mensaje que llega mientras corre un plan aprobado también se
            # encola (la sesión está "running"), pero aquí NO hay punto de
            # entrega intermedio: cada paso es un sub-agente aislado con su
            # propio prompt de alcance (``_ejecutar_paso_de_plan``), y meterle
            # a mitad un mensaje dirigido al plan completo sería entregárselo
            # al contexto equivocado. Se entrega al cerrar, como el turno
            # siguiente -- ver ``_entregar_pendientes_al_cerrar``.
            try:
                resultado = asyncio.run(
                    PlanificadorReparto(
                        runner=runner, max_concurrencia=3, on_evento=on_evento
                    ).ejecutar(pasos_a_correr)
                )
            except Exception as exc:  # noqa: BLE001 - un reparto roto también necesita cierre humano
                session.append("error", str(exc)[:2000], stream="stderr")
                session.append("assistant_final", build_failure_final(exc))
                exito_del_plan = False
                return

            estados_combinados: dict[str, EstadoPaso] = dict(progreso_previo or {})
            estados_combinados.update(resultado.estados)
            self._plan_progress[plan_id] = estados_combinados
            self._sincronizar_plan_store(plan_id, plan, estados_combinados)

            resumen_final = ResultadoReparto(
                estados=estados_combinados,
                cancelado=resultado.cancelado,
                oleadas_totales=resultado.oleadas_totales,
            )
            session.append("assistant_final", resumen_final.resumen())
            exito_del_plan = resumen_final.exito_total
        except Exception as exc:  # noqa: BLE001 - el cierre de abajo decide qué fue
            # Mismo trato que ``_run_workers_agent``: fallar en la preparación
            # no puede dejar la sesión muda NI trabada en "running".
            error_de_preparacion = exc
        finally:
            modelo = session.metadata.get("model")
            with session._turn_lock:
                # Fijar el estado final y decidir qué pasa con lo encolado van
                # bajo EL MISMO candado, igual que en ``_run_workers_agent``:
                # si se sueltan entre medio, un mensaje que entra justo ahí ve
                # la sesión ya "completed" y abre un turno por su cuenta
                # mientras este cierre estaba por abrir otro con lo que quedaba
                # en cola.
                if exito_del_plan is None:
                    # El reparto ni siquiera llegó a correr.
                    fallo = error_de_preparacion or RuntimeError(
                        "La ejecución del plan se cortó antes de empezar."
                    )
                    session.append("error", str(fallo)[:2000], stream="stderr")
                    session.append("assistant_final", build_failure_final(fallo))
                self._cerrar_turno_de_plan(session, checkpoint_id, exito=bool(exito_del_plan))
                # ``skill_context``/``mcp_tools`` van vacíos porque
                # ``approve_plan``/``resume_plan`` tampoco los reciben: el
                # turno promovido queda igual de equipado que los pasos del
                # plan que acaban de correr, ni más ni menos.
                self._entregar_pendientes_al_cerrar(
                    session,
                    skill_context=None,
                    model=modelo if isinstance(modelo, str) else None,
                    mcp_tools=None,
                )

    def _cerrar_turno_de_plan(
        self, session: Session, checkpoint_id: str | None, *, exito: bool
    ) -> None:
        """Fija el estado final de un plan, sella su checkpoint y guarda.

        Va bajo ``_turn_lock`` por lo mismo que el cierre de
        ``_run_workers_agent``: ``_entregar_pendientes_al_cerrar`` decide qué
        hacer con lo encolado LEYENDO este estado, así que fijarlo y leerlo
        tienen que ser una sola operación frente a ``start_agent``.

        "cancelled" le gana a "failed", y esa es la razón de que esta función
        exista: cuando la persona aprieta detener a mitad de un plan, los pasos
        fallan JUSTAMENTE por eso, y el reparto los reporta como fallidos. Si
        ese "failed" se escribía encima del "cancelled" que puso ``close()``,
        lo encolado pasaba a ser promovible y se le arrancaba un turno nuevo a
        quien acababa de pedir que se pare -- con el plan cancelado, el agente
        seguía trabajando. Ver ``_AGENT_PROMOTABLE_STATUSES``.
        """
        with session._turn_lock:
            cancelado = (
                session.metadata.get("status") == "cancelled" or session.cancel_event.is_set()
            )
            if cancelado:
                session.metadata["status"] = "cancelled"
                session.metadata["exit_code"] = None
            else:
                session.metadata["status"] = "completed" if exito else "failed"
                session.metadata["exit_code"] = 0 if exito else 1
            session.metadata["ended_at"] = _now()
            if checkpoint_id is not None:
                with contextlib.suppress(IDECheckpointError, OSError):
                    self.checkpoints.seal(checkpoint_id)
            self._save()

    async def _ejecutar_paso_de_plan(
        self,
        session: Session,
        workspace_id: str,
        paso: PasoReparto,
        rules_block: str | None,
        track_file: Callable[[str], None],
    ) -> str:
        """Corre UN paso de un plan aprobado como un turno normal y acotado de
        ``WorkersIDEAgent`` -- MISMO ``MAX_TOOL_ROUNDS`` y MISMO gate de
        herramientas peligrosas que cualquier otro turno: este sub-agente
        NUNCA recibe ``approved_tool_call_ids``, así que si pide una tool
        peligrosa, ``run()`` pausa sin ejecutar nada (ver
        ``DANGEROUS_TOOL_NAMES`` en ``ide_workers_agent.py``) y esta función
        lo detecta y cuenta el paso como fallido -- nunca como un atajo que
        se salte la confirmación humana.
        """
        alcance = (
            "Tu alcance exclusivo en este paso son estos archivos/zonas: "
            f"{', '.join(paso.rutas)}. No toques nada fuera de ese alcance; si de "
            "verdad hace falta, dilo en tu respuesta final en vez de hacerlo."
            if paso.rutas
            else "Este paso no declaró archivos exclusivos: sé conservador y toca "
            "solo lo mínimo indispensable para cumplirlo."
        )
        prompt = (
            "Estás ejecutando UN paso de un plan de ingeniería que la persona ya "
            f"aprobó explícitamente; no vuelvas a proponer un plan. {alcance}\n\n"
            f"Paso a ejecutar: {paso.instrucciones}"
        )
        vio_confirmacion_pendiente = False
        respuesta_final: str | None = None

        def write_event(
            event_type: str,
            text: str,
            *,
            presentation: list[dict[str, Any]] | None = None,
        ) -> None:
            nonlocal vio_confirmacion_pendiente, respuesta_final
            if event_type == "confirmation_required":
                vio_confirmacion_pendiente = True
            elif event_type == "assistant_final":
                respuesta_final = text
            # Los bloques del sub-agente van al hilo igual que su texto: la
            # tabla que produce un paso de un plan es tan real como la de un
            # turno normal, y el prefijo ``[paso]`` solo aplica al texto (el
            # bloque ya trae su propio título).
            session.append(event_type, f"[{paso.titulo}] {text}"[:2000], presentation=presentation)

        await self.workers_agent.run(
            workspace_id=workspace_id,
            prompt=prompt,
            write_event=write_event,
            cancelled=session.cancel_event.is_set,
            project_rules_block=rules_block,
            track_file=track_file,
            semantic=self.semantic,
            memoria=self.memoria,
            # ``session_id`` (sin ``plan_store``, a propósito -- ver el
            # docstring: "no vuelvas a proponer un plan") solo habilita el
            # gate de ``/effort``: este paso arranca con el nivel VIGENTE de la
            # conversación que aprobó el plan, y si alguien lo cambia mientras
            # el paso sigue corriendo, la vuelta siguiente de ESTE paso ya sale
            # con el nivel nuevo -- mismo mecanismo "entre vueltas" que un
            # turno normal.
            session_id=session.id,
            effort_store=self.effort_store,
        )
        if vio_confirmacion_pendiente:
            raise RuntimeError(
                "Este paso pidió una herramienta que necesita confirmación humana "
                "explícita; no se ejecutó nada. Resuélvelo por fuera del plan (un "
                "mensaje normal en esta conversación) y reintenta con resume_plan."
            )
        if respuesta_final is None:
            raise RuntimeError("El paso terminó sin una respuesta final del sub-agente.")
        return respuesta_final

    def _sincronizar_plan_store(
        self, plan_id: str, plan: Plan, estados: dict[str, EstadoPaso]
    ) -> None:
        """Refleja en ``ide_plan.PlanStore`` (que solo modela UN paso activo a
        la vez -- ver su docstring) el resultado de una corrida real que pudo
        ejecutar pasos en paralelo: avanza en el orden del plan mientras cada
        paso haya terminado ``completada``, y se detiene (``fail``) en el
        primer paso que no lo esté.

        Es una aproximación honesta a propósito: el conteo de pasos
        completados que queda visible es real, pero "el paso en el que se
        rompió" es el primero sin resolver EN EL ORDEN DEL PLAN, no
        necesariamente el primero en fallar en el tiempo real (varios pueden
        haber fallado a la vez en oleadas distintas). Tras un ``resume_plan``
        sobre un plan que este mismo método ya dejó ``failed`` (terminal),
        ``advance``/``fail`` no pueden reabrirlo -- exigen ``executing`` (ver
        ``ide_plan.PlanStore``) -- así que esas llamadas se descartan en
        silencio: el snapshot autoritativo de qué se completó de verdad sigue
        siendo ``self._plan_progress``, no la lectura de ``self.plans`` para
        ese plan en particular.
        """
        for step in plan.steps:
            estado = estados.get(step.id)
            if estado is not None and estado.estado == "completada":
                with contextlib.suppress(IDEPlanError):
                    self.plans.advance(plan_id, note="completado")
                continue
            motivo = "paso no ejecutado"
            if estado is not None:
                motivo = estado.error or f"paso {estado.estado}"
            with contextlib.suppress(IDEPlanError):
                self.plans.fail(plan_id, motivo[:300])
            return
