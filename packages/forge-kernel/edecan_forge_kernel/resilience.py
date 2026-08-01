"""Fase G — Resiliencia, Autocuración y TUI.

Implementa los requisitos de la Fase G de `FORGE-CONSTRUCCION-COMPLETA.md`:
- Detección de agentes atascados mediante firmas VFS y recuento de intentos.
- Estrategias de autocuración (Inyección de Hint de Recuperación).
- Interfaz e Inspección TUI / Observabilidad en tiempo real.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from edecan_forge_kernel.vfs import Vfs, calculate_subtree_hash

# --------------------------------------------------------------------------- #
# Detector de Agentes Atascados / Loops Inútiles
# --------------------------------------------------------------------------- #


@dataclass
class AgentProgressSnapshot:
    turn_index: int
    subtree_hash: str
    last_tool_name: str
    last_args_hash: str


class StuckAgentDetector:
    """Detecta si un agente se encuentra atascado en un bucle repetitivo."""

    def __init__(self, max_no_progress_turns: int = 3):
        self.max_no_progress_turns = max_no_progress_turns
        self.history: list[AgentProgressSnapshot] = []

    def record_turn(self, vfs: Vfs, tool_name: str, args: dict[str, Any]) -> bool:
        """Registra un turno y devuelve True si se detecta un atascamiento."""
        sub_hash = calculate_subtree_hash(vfs.root_tree)
        args_hash = str(sorted(args.items()))

        snapshot = AgentProgressSnapshot(
            turn_index=len(self.history) + 1,
            subtree_hash=sub_hash,
            last_tool_name=tool_name,
            last_args_hash=args_hash,
        )
        self.history.append(snapshot)

        if len(self.history) < self.max_no_progress_turns:
            return False

        # Verificar si las últimas N ejecuciones tuvieron el mismo estado
        recent = self.history[-self.max_no_progress_turns :]
        first_hash = recent[0].subtree_hash
        first_tool = recent[0].last_tool_name

        all_same_vfs = all(s.subtree_hash == first_hash for s in recent)
        all_same_tool = all(s.last_tool_name == first_tool for s in recent)

        return all_same_vfs and all_same_tool


# --------------------------------------------------------------------------- #
# Motor de Autocuración y Estrategias de Recuperación
# --------------------------------------------------------------------------- #


class AutoHealingEngine:
    """Aplica medidas correctivas automáticas cuando un agente se atasca."""

    @classmethod
    def generate_recovery_prompt(cls, stuck_tool_name: str, retry_count: int) -> str:
        """Genera un prompt de sugerencia (hint) para guiar al agente."""
        return (
            f"[SYSTEM RECOVERY HINT - INTENTO {retry_count}] "
            f"La herramienta '{stuck_tool_name}' no modifica el VFS. "
            "Revisa los errores anteriores o cambia de estrategia."
        )


# --------------------------------------------------------------------------- #
# Inspección y Observabilidad TUI (Estado del Kernel)
# --------------------------------------------------------------------------- #


class TuiStateInspector:
    """Inspecciona y exporta el estado interno del kernel para TUI."""

    @classmethod
    def format_status_dashboard(
        cls,
        session_id: str,
        vfs_version: int,
        effect_class: str,
        turns_count: int,
    ) -> str:
        """Formatea un resumen visual limpio del estado del sistema Forge."""
        return (
            f"┌────────────────────────────────────────────────────────┐\n"
            f"│ EDECÁN FORGE KERNEL - TUI DASHBOARD                    │\n"
            f"├────────────────────────────────────────────────────────┤\n"
            f"│ Session ID    : {session_id:<38} │\n"
            f"│ VFS Version   : {vfs_version:<38} │\n"
            f"│ Effect Class  : {effect_class:<38} │\n"
            f"│ Total Turns   : {turns_count:<38} │\n"
            f"└────────────────────────────────────────────────────────┘"
        )
