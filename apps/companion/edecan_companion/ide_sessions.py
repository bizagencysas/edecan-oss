"""Sesiones persistentes de terminal y agentes locales.

Los procesos viven en el companion de escritorio, no en la conexión HTTP o
WebSocket del teléfono. Minimizar/cerrar la app móvil solo deja de leer
eventos; el proceso continúa y se puede rehidratar con ``list`` + ``read``.
"""

from __future__ import annotations

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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from edecan_companion.ide_workspaces import WorkspaceStore

MAX_EVENTS_PER_SESSION = 2000
MAX_EVENT_TEXT_CHARS = 8_000
MAX_EVENT_LOG_BYTES = 5 * 1024 * 1024

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
        self, event_type: str, text: str, *, stream: str | None = None
    ) -> dict[str, Any]:
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
            self.events.append(event)
            self.manager.events_dir.mkdir(parents=True, exist_ok=True)
            try:
                with self._event_path().open("a", encoding="utf-8") as file:
                    file.write(json.dumps(event, ensure_ascii=False) + "\n")
                try:
                    self._event_path().chmod(0o600)
                except OSError:
                    pass
                if self._event_path().stat().st_size > MAX_EVENT_LOG_BYTES:
                    self._compact_events()
            except OSError:
                pass
            return dict(event)

    def _compact_events(self) -> None:
        """Conserva eventos recientes hasta ~la mitad del límite.

        Así una sesión ruidosa no reescribe un archivo que todavía supera el
        límite en cada chunk posterior.
        """

        budget = MAX_EVENT_LOG_BYTES // 2
        selected: list[tuple[dict[str, Any], bytes]] = []
        used = 0
        for row in reversed(self.events):
            encoded = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
            if selected and used + len(encoded) > budget:
                break
            selected.append((row, encoded))
            used += len(encoded)
        selected.reverse()
        self.events = deque((row for row, _ in selected), maxlen=MAX_EVENTS_PER_SESSION)
        temporary = self._event_path().with_suffix(f".{uuid.uuid4().hex}.tmp")
        with temporary.open("wb") as file:
            for _, encoded in selected:
                file.write(encoded)
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary, self._event_path())

    def public(self) -> dict[str, Any]:
        return dict(self.metadata)

    def read(self, cursor: int) -> dict[str, Any]:
        if cursor < 0:
            raise IDESessionError("El cursor no puede ser negativo.")
        with self._lock:
            rows = [dict(event) for event in self.events if int(event["cursor"]) > cursor]
            return {
                "session": self.public(),
                "events": rows,
                "next_cursor": rows[-1]["cursor"] if rows else max(cursor, self.cursor),
            }


class SessionManager:
    def __init__(self, state_dir: Path, workspaces: WorkspaceStore) -> None:
        self.state_dir = state_dir
        self.events_dir = state_dir / "ide-session-events"
        self.metadata_path = state_dir / "ide-sessions.json"
        self.workspaces = workspaces
        self._lock = threading.RLock()
        self._sessions: dict[str, Session] = {}
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
                and (
                    workspace_id is None
                    or session.metadata.get("workspace_id") == workspace_id
                )
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

    def start_terminal(
        self, workspace_id: str, raw_argv: Any = None, title: Any = None
    ) -> dict[str, Any]:
        cwd = self.workspaces.root(workspace_id)
        if raw_argv is None:
            shell = os.environ.get("SHELL") if os.name != "nt" else os.environ.get("COMSPEC")
            shell = shell or ("/bin/zsh" if Path("/bin/zsh").exists() else "/bin/sh")
            argv = self._validate_argv([shell])
        else:
            argv = self._validate_argv(raw_argv)
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
            if session.metadata["status"] not in {"cancelled", "closed"}:
                session.metadata["status"] = "completed" if return_code == 0 else "failed"
            session.metadata["exit_code"] = return_code
            session.metadata["ended_at"] = _now()
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

    @staticmethod
    def _agent_argv(provider: str, model: str | None) -> tuple[str, list[str]]:
        selected = provider
        if provider == "auto":
            selected = "codex" if shutil.which("codex") else "claude"
        if selected == "codex":
            executable = shutil.which("codex")
            if not executable:
                raise IDESessionError("Codex CLI no está instalado o no está en PATH.")
            argv = [
                executable,
                "exec",
                "--json",
                "--sandbox",
                "workspace-write",
                "--skip-git-repo-check",
            ]
            if model:
                argv.extend(["--model", model])
            # ``-`` obliga a Codex a leer la instrucción desde stdin. Nunca
            # debe aparecer en argv porque otros procesos locales pueden
            # inspeccionarlo con ``ps``.
            argv.append("-")
            return selected, argv
        if selected == "claude":
            executable = shutil.which("claude")
            if not executable:
                raise IDESessionError("Claude CLI no está instalado o no está en PATH.")
            argv = [
                executable,
                "--print",
                "--output-format",
                "stream-json",
                "--verbose",
                "--permission-mode",
                "acceptEdits",
            ]
            if model:
                argv.extend(["--model", model])
            # Claude ``--print`` sin argumento también lee stdin.
            return selected, argv
        raise IDESessionError("provider debe ser auto, codex o claude.")

    def start_agent(
        self,
        workspace_id: str,
        prompt: str,
        provider: str = "auto",
        title: Any = None,
        model: str | None = None,
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
        selected, argv = self._agent_argv(provider, model)
        cwd = self.workspaces.root(workspace_id)
        session = self._register(
            kind="agent",
            workspace_id=workspace_id,
            title=_clean_title(title, f"Agente {selected.title()}"),
            extra={"provider": selected, "model": model},
        )
        try:
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            session.process = process
            if process.stdin is None:  # pragma: no cover - Popen contract
                raise IDESessionError("El agente local no acepta instrucciones por stdin.")
            try:
                process.stdin.write(prompt.encode("utf-8"))
                process.stdin.close()
            except BaseException:
                with contextlib.suppress(OSError):
                    process.terminate()
                raise
            session.metadata["status"] = "running"
            session.append("status", f"Agente {selected} iniciado.")
            self._save()
            threading.Thread(
                target=self._read_agent,
                args=(session,),
                daemon=True,
                name=f"edecan-agent-{session.id}",
            ).start()
            return {"session": session.public()}
        except Exception:
            session.metadata["status"] = "failed"
            session.metadata["ended_at"] = _now()
            self._save()
            raise

    @staticmethod
    def _readable_agent_event(line: str, stream: str) -> tuple[str, str]:
        stripped = line.strip()
        if not stripped:
            return "output", ""
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            # Los proveedores soportados se arrancan en JSONL. Una línea
            # libre puede ser diagnóstico interno, razonamiento o incluso un
            # fragmento de JSON corrupto; nunca se retransmite al teléfono.
            return ("error" if stream == "stderr" else "status"), ""
        if not isinstance(payload, dict):
            return "status", ""
        event_type = str(payload.get("type") or "event")
        item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
        item_type = str(item.get("type") or "").lower()
        lowered_type = event_type.lower()

        # Nunca enviar razonamiento interno/chain-of-thought al chat. Algunos
        # CLIs lo nombran ``reasoning``, otros ``thinking`` y Claude puede
        # incluirlo como bloque dentro de ``message.content``.
        if (
            item_type in {"reasoning", "thinking"}
            or "reasoning" in lowered_type
            or "thinking" in lowered_type
        ):
            return "status", ""

        if item_type in {"command_execution", "command"}:
            command = item.get("command")
            status = str(item.get("status") or "en curso")
            if isinstance(command, str) and command:
                return "command", f"{command}\nEstado: {status}"
            return "command", f"Comando {status}."
        if item_type in {"file_change", "file_changes"}:
            changes = item.get("changes")
            paths: list[str] = []
            if isinstance(changes, list):
                for change in changes:
                    if isinstance(change, dict) and isinstance(change.get("path"), str):
                        paths.append(change["path"])
            return "file", (
                "Archivos actualizados: " + ", ".join(paths[:20])
                if paths
                else "Archivos actualizados."
            )
        if item_type in {"mcp_tool_call", "tool_call", "tool_use"}:
            name = item.get("name") or item.get("server")
            return "tool", f"Usando herramienta: {name or 'herramienta local'}."

        message = payload.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, list):
                visible: list[str] = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    block_type = str(block.get("type") or "").lower()
                    if block_type in {"thinking", "reasoning", "redacted_thinking"}:
                        continue
                    if block_type == "text" and isinstance(block.get("text"), str):
                        visible.append(block["text"])
                    elif block_type in {"tool_use", "server_tool_use"}:
                        name = block.get("name")
                        visible.append(f"Usando herramienta: {name or 'herramienta'}.")
                if visible:
                    return "assistant", "\n".join(visible)

        # Codex y Claude cambian su JSON con el tiempo. Solo se extraen campos
        # de salida humana conocidos; nunca se vuelca JSON crudo desconocido.
        safe_text_event_types = {
            "assistant",
            "assistant.message",
            "assistant_message",
            "content_block_delta",
            "item.completed",
            "item.updated",
            "message",
            "progress",
            "result",
            "status",
            "text",
        }
        safe_item_types = {
            "agent_message",
            "assistant",
            "assistant_message",
            "message",
            "text",
        }
        if lowered_type not in safe_text_event_types and item_type not in safe_item_types:
            if lowered_type in {"thread.started", "turn.started", "system", "init"}:
                return "status", ""
            if lowered_type in {"turn.completed", "done"}:
                return "status", "Trabajo completado."
            return "status", ""
        candidates: list[Any] = [
            payload.get("text"),
            payload.get("result"),
            item.get("text") if item_type in safe_item_types else None,
            (payload.get("delta") or {}).get("text")
            if isinstance(payload.get("delta"), dict)
            else None,
        ]
        for value in candidates:
            if isinstance(value, str) and value:
                return event_type, value
        return "status", ""

    def _agent_pipe(self, session: Session, pipe: BinaryIO, stream: str) -> None:
        for raw_line in iter(pipe.readline, b""):
            line = raw_line.decode("utf-8", errors="replace")
            event_type, text = self._readable_agent_event(line, stream)
            if text:
                session.append(event_type, text, stream=stream)

    def _read_agent(self, session: Session) -> None:
        assert session.process is not None
        threads: list[threading.Thread] = []
        for pipe, stream in (
            (session.process.stdout, "stdout"),
            (session.process.stderr, "stderr"),
        ):
            if pipe is None:
                continue
            thread = threading.Thread(
                target=self._agent_pipe, args=(session, pipe, stream), daemon=True
            )
            thread.start()
            threads.append(thread)
        for thread in threads:
            thread.join()
        self._finish(session)
