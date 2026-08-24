"""Estado de sesión multimodal persistente (PHASE2.md §50).

Punto único de persistencia del contexto multimodal en tiempo real de una
conversación: la memoria visual (`VisualMemory`), los últimos resúmenes de
cámara y pantalla, las referencias de medios activos y las entidades
detectadas. Todo viaja junto en ``to_dict()``/``from_dict()`` para que quien
inyecte el agente pueda persistirlo/restaurarlo entre turnos sin reenviar los
píxeles.
"""

from __future__ import annotations

from typing import Any

from .visual_memory import VisualMemory


class MultimodalSessionState:
    """Estado multimodal de una conversación, serializable a dict.

    ``context_key`` etiqueta la conversación dueña del estado; se propaga a la
    ``VisualMemory`` para que ``merge`` nunca mezcle conversaciones distintas.
    """

    def __init__(self, context_key: str | None = None) -> None:
        self.visual_memory = VisualMemory(context_key=context_key)
        self.last_camera_frame_summary: str | None = None
        self.last_screen_frame_summary: str | None = None
        self.active_media_refs: list[str] = []
        self.detected_entities: list[str] = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "visual_memory": self.visual_memory.to_dict(),
            "last_camera_frame_summary": self.last_camera_frame_summary,
            "last_screen_frame_summary": self.last_screen_frame_summary,
            "active_media_refs": list(self.active_media_refs),
            "detected_entities": list(self.detected_entities),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MultimodalSessionState:
        estado = cls()
        estado.visual_memory = VisualMemory.from_dict(d.get("visual_memory") or {})
        estado.last_camera_frame_summary = d.get("last_camera_frame_summary")
        estado.last_screen_frame_summary = d.get("last_screen_frame_summary")
        estado.active_media_refs = list(d.get("active_media_refs") or [])
        estado.detected_entities = list(d.get("detected_entities") or [])
        return estado