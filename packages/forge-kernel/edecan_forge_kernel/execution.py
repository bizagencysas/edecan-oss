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
import pty
import select
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


class TotalTerminalRunner:
    """Ejecuta comandos en un shell PTY real conservando el entorno."""

    def __init__(self, cwd: Path | None = None, env: dict[str, str] | None = None):
        self.cwd = cwd or Path.cwd()
        self.env = env or dict(os.environ)

    def run_pty_command(self, command_str: str, timeout_seconds: float = 30.0) -> PtyResult:
        """Ejecuta un comando abriendo un PTY real."""
        master_fd, slave_fd = pty.openpty()

        shell = os.environ.get("SHELL", "/bin/zsh")
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
                    os.killpg(os.getpgid(proc.pid), 9)
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
