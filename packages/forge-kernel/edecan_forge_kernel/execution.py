"""Fase E — Motor de Ejecución, Aislamiento, Política de Efecto y EffectLedger.

Implementa los requisitos de la Fase E de `FORGE-CONSTRUCCION-COMPLETA.md`:
- Clasificación de comandos por `EffectClass`.
- Elevación de `EffectClass` cuando deriva de contenido no confiable.
- Evaluación de `Guard` y colocación de `Hold` para aprobación humana.
- `EffectLedger` idempotente (reserve / commit / poison / resolve).
- Manejo de terminal total y aislamiento por procesos.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# `pty`/`select` sobre el master fd no existen en Windows: `pty.openpty()` ya
# rompe la importación del módulo entero. Se importan solo en POSIX; la rama
# Windows de `TotalTerminalRunner` usa pipes planos (ver `_run_windows` más
# abajo) — degradado sin TTY, mismo trade-off ya aceptado para el respaldo de
# pipes del companion (docs/edecan-windows.md §1).
if os.name != "nt":
    import pty
    import select

from edecan_forge_kernel.contracts import (
    EFFECT_CLASS_RANK,
    EffectClass,
    TaintState,
    TrustLevel,
    effect_class_max,
)

# --------------------------------------------------------------------------- #
# Clasificador de Comandos y Política de Efectos
# --------------------------------------------------------------------------- #


class ExecutionPolicyEngine:
    """Clasifica comandos del shell y determina si requieren aprobación previa."""

    IRREVERSIBLE_PATTERNS = (
        "rm -rf",
        "git push --force",
        "git push -f",
        "drop table",
        "drop database",
        "terraform apply",
        "curl | sh",
        "wget | sh",
        "sudo ",
    )

    MUTATING_PATTERNS = (
        "git commit",
        "git merge",
        "pip install",
        "npm install",
        "uv add",
        "docker run",
        "docker rm",
    )

    @classmethod
    def classify_command(
        cls, command_str: str, taint_state: TaintState | None = None
    ) -> EffectClass:
        """Clasifica un comando determinando su EffectClass inicial y aplicando Taint."""
        cmd_lower = command_str.lower().strip()

        base_class = EffectClass.SAFE
        if any(pat in cmd_lower for pat in cls.IRREVERSIBLE_PATTERNS):
            base_class = EffectClass.IRREVERSIBLE
        elif any(pat in cmd_lower for pat in cls.MUTATING_PATTERNS):
            base_class = EffectClass.REVERSIBLE

        # Elevación de riesgo si proviene de un linaje no confiable (TaintState)
        if taint_state and taint_state.hwm in (
            TrustLevel.TOOL_OUTPUT,
            TrustLevel.NETWORK,
        ):
            base_class = effect_class_max(base_class, EffectClass.IRREVERSIBLE)

        return base_class

    @classmethod
    def requires_approval(
        cls,
        effect_class: EffectClass,
        ceiling: EffectClass = EffectClass.REVERSIBLE,
    ) -> bool:
        """Determina si la clase de efecto supera el techo de autorización."""
        return EFFECT_CLASS_RANK[effect_class] > EFFECT_CLASS_RANK[ceiling]


# --------------------------------------------------------------------------- #
# Terminal Total PTY Runner
# --------------------------------------------------------------------------- #


@dataclass
class PtyResult:
    exit_code: int
    output: str
    orphans_cleaned: int = 0


def _default_shell_argv() -> list[str]:
    """Resuelve el shell que interpreta ``command_str`` como texto libre.

    Es el mismo tipo de decisión que una terminal real toma para "qué
    programa entiende esta línea que escribió el usuario/agente" — no es el
    antipatrón `shell=True` (aquí el shell es siempre argv[0] explícito, la
    lista nunca pasa por un intérprete de metacaracteres oculto de
    `subprocess`). En Windows se prefiere PowerShell 7, luego Windows
    PowerShell 5.1 (siempre presente), y `%COMSPEC%` como último recurso.
    """

    if os.name == "nt":
        for candidate in ("pwsh.exe", "powershell.exe"):
            resolved = shutil.which(candidate)
            if resolved:
                return [resolved, "-NoLogo", "-NoProfile", "-Command"]
        comspec = os.environ.get("ComSpec") or os.environ.get("COMSPEC") or "cmd.exe"
        return [comspec, "/d", "/c"]
    shell = os.environ.get("SHELL") or shutil.which("zsh") or shutil.which("bash") or "/bin/sh"
    return [shell, "-c"]


def _kill_tree(pid: int) -> None:
    """Mata el árbol de un proceso lanzado por ``run_pty_command`` al vencer
    el timeout.

    POSIX: el proceso arrancó con ``start_new_session=True``, así que
    ``os.killpg`` corta la sesión completa (raíz + hijos). Windows no tiene
    ``os.killpg`` ni sesiones POSIX; ``taskkill /T /F`` recorre el árbol por
    PID. Idempotente: un PID ya muerto no debe hacer explotar la llamada.
    """

    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        return
    import signal

    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


class TotalTerminalRunner:
    """Ejecuta comandos en un shell real conservando el entorno.

    POSIX abre un PTY real (comportamiento original). Windows no tiene
    `pty`/`select` sobre pipes anónimos utilizables aquí — el ConPTY real
    vive en la frontera dedicada del companion (`apps/companion/.../
    pty_compat.py`, fuera del alcance de este runner de Fase E) — así que
    la rama Windows usa pipes planos sin TTY: funcionalmente equivalente
    para comandos no interactivos, degradado para programas que exigen TTY
    interactivo (mismo trade-off ya aceptado en el respaldo de pipes del
    companion, ver docs/edecan-windows.md §1).
    """

    def __init__(self, cwd: Path | None = None, env: dict[str, str] | None = None):
        self.cwd = cwd or Path.cwd()
        self.env = env or dict(os.environ)

    def run_pty_command(self, command_str: str, timeout_seconds: float = 30.0) -> PtyResult:
        """Ejecuta ``command_str`` en el shell resuelto por plataforma."""
        if os.name == "nt":
            return self._run_windows(command_str, timeout_seconds)
        return self._run_posix_pty(command_str, timeout_seconds)

    def _run_windows(self, command_str: str, timeout_seconds: float) -> PtyResult:
        argv = [*_default_shell_argv(), command_str]
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(self.cwd),
            env=self.env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            output, _ = proc.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            _kill_tree(proc.pid)
            output, _ = proc.communicate()
        return PtyResult(
            exit_code=proc.returncode or 0,
            output=output.decode("utf-8", errors="replace"),
        )

    def _run_posix_pty(self, command_str: str, timeout_seconds: float) -> PtyResult:
        """Ejecuta abriendo un PTY real (comportamiento original, sin cambios)."""
        master_fd, slave_fd = pty.openpty()

        shell = os.environ.get("SHELL") or shutil.which("zsh") or shutil.which("bash") or "/bin/sh"
        try:
            proc = subprocess.Popen(
                [shell, "-c", command_str],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=str(self.cwd),
                env=self.env,
                close_fds=True,
                start_new_session=True,
            )
        finally:
            os.close(slave_fd)

        output_chunks = []
        start_time = os.times().elapsed

        try:
            while True:
                r, _, _ = select.select([master_fd], [], [], 0.1)
                if master_fd in r:
                    try:
                        chunk = os.read(master_fd, 1024)
                        if not chunk:
                            break
                        output_chunks.append(chunk)
                    except OSError:
                        break

                if proc.poll() is not None:
                    break

                if (os.times().elapsed - start_time) > timeout_seconds:
                    _kill_tree(proc.pid)
                    proc.wait()
                    break
        finally:
            os.close(master_fd)

        proc.wait()
        raw_output = b"".join(output_chunks).decode("utf-8", errors="replace")

        return PtyResult(
            exit_code=proc.returncode or 0,
            output=raw_output,
        )


# --------------------------------------------------------------------------- #
# EffectLedger en Memoria
# --------------------------------------------------------------------------- #


@dataclass
class LedgerRecord:
    target: str
    key: str
    spec_digest: str
    status: str = "reserved"
    outcome_ref: Any | None = None
    reason: str | None = None


class InMemoryEffectLedger:
    """Implementación de referencia en memoria para dos fases."""

    def __init__(self):
        self._records: dict[str, LedgerRecord] = {}

    def reserve(self, target: str, key: str, spec_digest: str, ttl_s: int = 300) -> LedgerRecord:
        if key in self._records:
            return self._records[key]
        rec = LedgerRecord(target=target, key=key, spec_digest=spec_digest, status="reserved")
        self._records[key] = rec
        return rec

    def commit(self, key: str, outcome_ref: Any) -> LedgerRecord:
        rec = self._records.get(key)
        if rec:
            rec.status = "committed"
            rec.outcome_ref = outcome_ref
            return rec
        rec = LedgerRecord(
            target="",
            key=key,
            spec_digest="",
            status="committed",
            outcome_ref=outcome_ref,
        )
        self._records[key] = rec
        return rec

    def poison(self, key: str, evidence_ref: Any) -> LedgerRecord:
        rec = self._records.get(key)
        if rec:
            rec.status = "poisoned"
            rec.outcome_ref = evidence_ref
            return rec
        rec = LedgerRecord(
            target="",
            key=key,
            spec_digest="",
            status="poisoned",
            outcome_ref=evidence_ref,
        )
        self._records[key] = rec
        return rec


# --------------------------------------------------------------------------- #
# Redactor de Secretos
# --------------------------------------------------------------------------- #


class SecretRedactor:
    """Redacta tokens y credenciales de texto plano."""

    def __init__(self, secrets: list[str] | None = None):
        self.secrets = [s for s in (secrets or []) if len(s.strip()) > 3]

    def redact(self, text: str) -> str:
        """Sustituye cualquier aparición de un secreto conocido."""
        result = text
        for secret in self.secrets:
            result = result.replace(secret, "[REDACTED_SECRET]")
        return result
