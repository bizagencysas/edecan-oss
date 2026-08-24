"""Máquina de estados para turnos de voz interruptibles.

El transporte (HTTP, WebSocket o una llamada nativa) no debe inventar estados
ni continuar reproduciendo audio después de un barge-in. Esta clase pequeña
centraliza ese contrato y usa tokens de turno para invalidar resultados tardíos.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

VoiceTurnState = Literal["idle", "listening", "processing", "speaking", "interrupted", "closed"]


@dataclass
class RealtimeVoiceSession:
    state: VoiceTurnState = "idle"
    turn_id: int = 0
    buffered_audio_bytes: int = 0
    interruption_reason: str | None = None

    def begin_listening(self) -> int:
        if self.state in {"closed", "listening", "processing", "speaking"}:
            raise RuntimeError(f"No se puede iniciar escucha desde {self.state}")
        self.turn_id += 1
        self.state = "listening"
        self.buffered_audio_bytes = 0
        self.interruption_reason = None
        return self.turn_id

    def append_audio(self, size_bytes: int) -> None:
        if self.state != "listening":
            raise RuntimeError("El audio solo puede llegar mientras se escucha")
        if size_bytes < 0:
            raise ValueError("El tamaño de audio no puede ser negativo")
        self.buffered_audio_bytes += size_bytes

    def commit_audio(self, turn_id: int) -> bool:
        if self.state != "listening" or not self.is_current(turn_id):
            return False
        self.state = "processing"
        return True

    def begin_speaking(self, turn_id: int) -> bool:
        if self.state != "processing" or not self.is_current(turn_id):
            return False
        self.state = "speaking"
        return True

    def complete_input(self, turn_id: int) -> bool:
        """Cierra STT y devuelve el transporte a reposo antes del LLM."""

        if self.state != "processing" or not self.is_current(turn_id):
            return False
        self.state = "idle"
        self.buffered_audio_bytes = 0
        return True

    def interrupt(self, reason: str = "user") -> bool:
        if self.state in {"idle", "interrupted", "closed"}:
            return False
        self.state = "interrupted"
        self.interruption_reason = reason.strip() or "user"
        self.buffered_audio_bytes = 0
        # Invalida cualquier resultado que haya quedado en vuelo.
        self.turn_id += 1
        return True

    def finish(self, turn_id: int) -> bool:
        if self.state != "speaking" or not self.is_current(turn_id):
            return False
        self.state = "idle"
        self.buffered_audio_bytes = 0
        self.interruption_reason = None
        return True

    def close(self) -> None:
        self.state = "closed"
        self.buffered_audio_bytes = 0

    def is_current(self, turn_id: int) -> bool:
        return turn_id == self.turn_id and self.state != "closed"
