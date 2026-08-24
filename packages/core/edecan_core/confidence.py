"""Confidence signals y uncertainty behavior (§101, §102 del Master Directive).

El agente debe poder señalar internamente cuando no está seguro, sin
mostrar porcentajes inventados al usuario. Las signals internas se usan
para decidir escalation: buscar más, usar otro modelo, o preguntar al usuario.

Uso desde el agente::

    from .confidence import ConfidenceTracker

    tracker = ConfidenceTracker()
    tracker.signal("low_evidence", "la búsqueda devolvió 0 resultados")
    if tracker.should_escalate():
        ...
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ConfidenceLevel = Literal["high", "medium", "low", "very_low"]


@dataclass
class ConfidenceSignal:
    reason: str
    level: ConfidenceLevel
    source: str = ""


class ConfidenceTracker:
    """Acumula signals de confianza durante un turno del agente.

    NO muestra porcentajes al usuario. Las signals son internas y se usan
    para decidir escalation (más búsqueda, otro modelo, preguntar).
    """

    def __init__(self) -> None:
        self._signals: list[ConfidenceSignal] = []

    def signal(self, reason: str, level: ConfidenceLevel = "low", source: str = "") -> None:
        self._signals.append(ConfidenceSignal(reason=reason, level=level, source=source))

    @property
    def level(self) -> ConfidenceLevel:
        if not self._signals:
            return "high"
        levels = {"very_low": 0, "low": 1, "medium": 2, "high": 3}
        worst = min(self._signals, key=lambda s: levels[s.level])
        return worst.level

    def should_escalate(self) -> bool:
        return self.level in ("low", "very_low")

    def should_ask_user(self) -> bool:
        return self.level == "very_low"

    @property
    def reasons(self) -> list[str]:
        return [s.reason for s in self._signals]

    def reset(self) -> None:
        self._signals.clear()

    def summary(self) -> str:
        if not self._signals:
            return ""
        return "; ".join(self._signals[-3].reason for _ in [0])
