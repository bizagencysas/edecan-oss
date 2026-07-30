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
import json
import os
import shutil
import signal
import subprocess
import threading
import uuid
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from edecan_companion.ide_bloques import validar_bloques
from edecan_companion.ide_busqueda_semantica import SemanticSearchService
from edecan_companion.ide_checkpoints import CheckpointStore, IDECheckpointError
from edecan_companion.ide_costos import analizar_tarea
from edecan_companion.ide_files import FileService, IDEFileError
from edecan_companion.ide_memoria import IDEMemoriaError, MemoriaStore
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

if os.name != "nt":
    import pty


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
        *,
        process: subprocess.Popen[bytes] | None = None,
        master_fd: int | None = None,
    ) -> None:
        self.manager = manager
        self.metadata = metadata
        self.process = process
        self.master_fd = master_fd
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


class SessionManager:
    def __init__(self, state_dir: Path, workspaces: WorkspaceStore) -> None:
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
            os.replace(temp, self.metadata_path)

    def _get(self, session_id: str, kind: str | None = None) -> Session:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None or (kind is not None and session.metadata.get("kind") != kind):
            raise IDESessionError("Sesión no encontrada.")
        return session

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
        shell = os.environ.get("SHELL") if os.name != "nt" else os.environ.get("COMSPEC")
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
            if os.name != "nt":
                master_fd, slave_fd = pty.openpty()
                try:
                    process = subprocess.Popen(
                        argv,
                        cwd=cwd,
                        stdin=slave_fd,
                        stdout=slave_fd,
                        stderr=slave_fd,
                        start_new_session=True,
                        close_fds=True,
                        env=process_environment,
                    )
                finally:
                    os.close(slave_fd)
                session.process = process
                session.master_fd = master_fd
                reader = threading.Thread(
                    target=self._read_pty,
                    args=(session,),
                    daemon=True,
                    name=f"edecan-terminal-{session.id}",
                )
            else:
                process = subprocess.Popen(
                    argv,
                    cwd=cwd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                    env=process_environment,
                )
                session.process = process
                reader = threading.Thread(
                    target=self._read_pipes,
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
        process = session.process
        return_code = process.wait() if process is not None else None
        with session._lock:
            deliberately_closed = session.metadata["status"] in {"cancelled", "closed"}
            if not deliberately_closed:
                session.metadata["status"] = "completed" if return_code == 0 else "failed"
            session.metadata["exit_code"] = return_code
            session.metadata["ended_at"] = _now()
        if not deliberately_closed:
            session.append("exit", f"Proceso finalizado con código {return_code}.")
        self._save()
        if session.master_fd is not None:
            with contextlib.suppress(OSError):
                os.close(session.master_fd)

    def _read_pty(self, session: Session) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        assert session.master_fd is not None
        while True:
            try:
                chunk = os.read(session.master_fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            text = decoder.decode(chunk)
            if text:
                session.append("output", text, stream="stdout")
        tail = decoder.decode(b"", final=True)
        if tail:
            session.append("output", tail, stream="stdout")
        self._finish(session)

    def _pipe_reader(self, session: Session, pipe: BinaryIO, stream: str) -> None:
        while True:
            chunk = pipe.read(4096)
            if not chunk:
                return
            session.append("output", chunk.decode("utf-8", errors="replace"), stream=stream)

    def _read_pipes(self, session: Session) -> None:
        assert session.process is not None
        threads: list[threading.Thread] = []
        for pipe, stream in (
            (session.process.stdout, "stdout"),
            (session.process.stderr, "stderr"),
        ):
            if pipe is None:
                continue
            thread = threading.Thread(
                target=self._pipe_reader, args=(session, pipe, stream), daemon=True
            )
            thread.start()
            threads.append(thread)
        for thread in threads:
            thread.join()
        self._finish(session)

    def input_terminal(self, session_id: str, data: str) -> dict[str, Any]:
        session = self._get(session_id, "terminal")
        if session.metadata["status"] != "running" or session.process is None:
            raise IDESessionError("La terminal ya no está activa.")
        if not isinstance(data, str) or not data or len(data) > 64_000:
            raise IDESessionError("La entrada de terminal no es válida.")
        encoded = data.encode("utf-8")
        if session.master_fd is not None:
            os.write(session.master_fd, encoded)
        elif session.process.stdin is not None:
            session.process.stdin.write(encoded)
            session.process.stdin.flush()
        else:
            raise IDESessionError("La terminal no acepta entrada.")
        return {"accepted": True, "bytes": len(encoded)}

    def close(self, session_id: str, kind: str) -> dict[str, Any]:
        session = self._get(session_id, kind)
        process = session.process
        if kind == "agent" and process is None:
            # Bajo ``_turn_lock``: cancelar tiene que ganarle a la promoción de
            # lo que quedó en cola. Sin este candado, un "detener" que cae
            # justo mientras el turno se cierra podría quedar pisado por el
            # turno siguiente que ese cierre estaba arrancando (que además
            # limpia ``cancel_event``), y la persona vería al agente seguir
            # trabajando después de haberle dicho que pare.
            with session._turn_lock:
                session.cancel_event.set()
                if session.metadata.get("status") in _AGENT_BUSY_STATUSES:
                    session.metadata["status"] = "cancelled"
                    session.metadata["ended_at"] = _now()
                    session.append("status", "Sesión cancelada.")
                    self._save()
            return {"session": session.public()}
        if process is not None and process.poll() is None:
            if os.name != "nt":
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            session.metadata["status"] = "cancelled" if kind == "agent" else "closed"
            message = "Sesión cancelada." if kind == "agent" else "Terminal cerrada."
            session.append("status", message)
            self._save()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
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
            target=self._run_workers_agent,
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
                target=self._run_workers_agent,
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

    def pending_mcp(self, session_id: str, call_id: str) -> dict[str, Any]:
        self._get(session_id, "agent")
        with self._lock:
            row = self._mcp_pending.get((session_id, call_id))
            if row is None:
                raise IDESessionError("La solicitud MCP ya no está pendiente.")
            return {
                "session_id": session_id,
                "call_id": call_id,
                "name": row["name"],
                "arguments": dict(row["arguments"]),
            }

    def resolve_mcp(self, session_id: str, call_id: str, result: dict[str, Any]) -> dict[str, Any]:
        self._get(session_id, "agent")
        with self._lock:
            row = self._mcp_pending.get((session_id, call_id))
            if row is None:
                raise IDESessionError("La solicitud MCP ya no está pendiente.")
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
