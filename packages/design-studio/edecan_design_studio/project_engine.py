"""Frontera ejecutable del Studio de proyectos HTML, separada del MCP media."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .engine import FYDESIGN_SECRET_ENV_ALLOWLIST, StudioEngineError
from .process_boundary import (
    ProcessExecutionTimeoutError,
    ProcessOutputLimitError,
    communicate_bounded,
    isolated_process_kwargs,
)

PROJECT_ACTIONS = frozenset(
    {
        "health",
        "list",
        "create",
        "edit",
        "read",
        "render",
        "history",
        "variants",
        "duplicate",
        "brand-health",
        "tidy",
        "archive",
        "restore",
        "export",
        "template-list",
        "template-save",
        "template-create",
        "design-system-list",
        "design-system-generate",
        "corpus-ingest",
        "corpus-search",
        "share-package",
    }
)
_BASE_ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")


@dataclass(frozen=True)
class ProjectEngineConfig:
    root: Path
    output_dir: Path
    state_dir: Path
    node_binary: Path | str = "node"
    timeout_seconds: float = 1_200.0
    max_output_bytes: int = 16 * 1024 * 1024
    runtime_env: dict[str, str] = field(default_factory=dict)


class ProjectEngineClient:
    """Ejecuta solamente el CLI fijado de proyectos, sin shell ni paths del modelo."""

    def __init__(self, config: ProjectEngineConfig) -> None:
        self.config = config

    def _root(self) -> Path:
        root = self.config.root.expanduser().resolve()
        if not root.is_dir():
            raise StudioEngineError(f"El motor de proyectos no existe: {root}")
        return root

    def _error_detail(self, stderr: bytes) -> str:
        detail = stderr.decode("utf-8", errors="replace")
        for candidate in (self.config.root, self.config.output_dir, self.config.state_dir):
            detail = detail.replace(os.fspath(candidate.expanduser().resolve()), "<studio>")
        detail = re.sub(
            r"(?i)(api[_-]?key|token|authorization|bearer)\s*[:=]\s*[^\s,;]+",
            r"\1=<redacted>",
            detail,
        )
        lines = [line.strip() for line in detail.splitlines() if line.strip()]
        return (lines[-1] if lines else "sin detalle adicional")[:600]

    def _node(self) -> str:
        raw = os.fspath(self.config.node_binary)
        if os.path.isabs(raw) or os.sep in raw:
            candidate = Path(raw).expanduser().resolve()
            if candidate.is_file():
                return os.fspath(candidate)
            raise StudioEngineError(f"No encontré el runtime Node: {candidate}")
        resolved = shutil.which(raw)
        if not resolved:
            raise StudioEngineError("No encontré el runtime Node empaquetado de Studio.")
        return resolved

    def _command(self) -> list[str]:
        root = self._root()
        script = root / "scripts" / "fydesign-project.ts"
        tsx_cli = root / "node_modules" / "tsx" / "dist" / "cli.mjs"
        if not script.is_file() or not tsx_cli.is_file():
            raise StudioEngineError("La instalación de Studio Projects está incompleta.")
        return [self._node(), os.fspath(tsx_cli), "scripts/fydesign-project.ts"]

    def _env(self, credentials: dict[str, str] | None) -> dict[str, str]:
        env = {key: os.environ[key] for key in _BASE_ENV_ALLOWLIST if os.environ.get(key)}
        for key, value in (credentials or {}).items():
            if key in FYDESIGN_SECRET_ENV_ALLOWLIST and isinstance(value, str) and value:
                env[key] = value
        output = self.config.output_dir.expanduser().resolve()
        state = self.config.state_dir.expanduser().resolve()
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        env["FYDESIGN_DIR"] = os.fspath(self._root())
        env["FYDESIGN_OUTPUT_ROOT"] = os.fspath(output)
        env["FYDESIGN_STATE_ROOT"] = os.fspath(state)
        env["FYDESIGN_STORE_PATH"] = os.fspath(state / "fydesign-store.json")
        for key in (
            "CHROMIUM_PATH",
            "PUPPETEER_EXECUTABLE_PATH",
            "PLAYWRIGHT_BROWSERS_PATH",
            "FFMPEG_PATH",
            "FFPROBE_PATH",
            "YTDLP_PATH",
        ):
            value = self.config.runtime_env.get(key)
            if value:
                env[key] = value
        bundled_browsers = self._root() / "playwright-browsers"
        if bundled_browsers.is_dir():
            env["PLAYWRIGHT_BROWSERS_PATH"] = os.fspath(bundled_browsers)
        return env

    async def execute(
        self,
        action: str,
        arguments: dict[str, Any],
        *,
        credentials: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if action not in PROJECT_ACTIONS:
            raise StudioEngineError(f"La acción de proyecto {action!r} no está permitida.")
        payload = {key: value for key, value in arguments.items() if key != "action"}
        payload["action"] = action
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        process = await asyncio.create_subprocess_exec(
            *self._command(),
            cwd=self._root(),
            env=self._env(credentials),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **isolated_process_kwargs(),
        )
        try:
            stdout, stderr = await communicate_bounded(
                process,
                encoded,
                timeout_seconds=self.config.timeout_seconds,
                max_output_bytes=self.config.max_output_bytes,
            )
        except ProcessExecutionTimeoutError as exc:
            raise StudioEngineError("Studio Projects agotó su tiempo y se canceló.") from exc
        except ProcessOutputLimitError as exc:
            raise StudioEngineError(
                "Studio Projects superó el límite de salida permitido y se canceló."
            ) from exc
        if process.returncode != 0:
            raise StudioEngineError(
                "Studio Projects no completó la operación. "
                f"Detalle seguro: {self._error_detail(stderr)}"
            )
        try:
            result = json.loads(stdout.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StudioEngineError("Studio Projects devolvió una respuesta inválida.") from exc
        if not isinstance(result, dict):
            raise StudioEngineError("Studio Projects devolvió un resultado inesperado.")
        return result


__all__ = ["PROJECT_ACTIONS", "ProjectEngineClient", "ProjectEngineConfig"]
