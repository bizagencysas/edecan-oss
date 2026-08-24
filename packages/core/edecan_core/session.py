"""Estado operativo compartido por todas las modalidades de Edecán.

La interfaz de usuario puede cambiar de texto a voz, imagen o control remoto
sin crear otro cerebro. Este objeto es el contrato pequeño que viaja con el
turno: identifica al usuario y la conversación, conserva el trabajo activo y
expone solo el contexto operativo necesario para el agente.

Es deliberadamente agnóstico de base de datos. La API puede persistirlo en el
store que corresponda; ``to_dict``/``from_dict`` mantienen el contrato estable
para Redis, una sesión local o una futura tabla.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

from .multimodal_session import MultimodalSessionState

SessionModality = Literal["text", "voice", "image", "camera", "screen", "computer"]
_MODALITIES = frozenset({"text", "voice", "image", "camera", "screen", "computer"})


@dataclass
class UnifiedSessionState:
    """Estado operativo de una conversación, seguro para serializar.

    ``active_agents`` y ``connected_tools`` son nombres/identificadores, no
    objetos ejecutables. Eso evita que el estado serializado se convierta en
    una vía para inyectar código o permisos.
    """

    session_id: str
    tenant_id: str
    user_id: str
    conversation_id: str
    workspace_id: str | None = None
    project_id: str | None = None
    modality: SessionModality = "text"
    active_task: str | None = None
    context: dict[str, Any] = field(default_factory=dict)
    active_agents: list[str] = field(default_factory=list)
    connected_tools: list[str] = field(default_factory=list)
    permissions: dict[str, Any] = field(default_factory=dict)
    realtime_state: dict[str, Any] = field(default_factory=dict)
    multimodal: MultimodalSessionState = field(default_factory=MultimodalSessionState)
    updated_at: float = field(default_factory=time.time)

    @property
    def visual_memory(self):
        """Compatibilidad con el contrato existente de la fase 2."""

        return self.multimodal.visual_memory

    def touch(self, *, modality: str | None = None) -> None:
        if modality is not None:
            if modality not in _MODALITIES:
                raise ValueError(f"Modalidad no soportada: {modality}")
            self.modality = modality  # type: ignore[assignment]
        self.updated_at = time.time()

    def attach_task(self, task_id: str | None) -> None:
        self.active_task = task_id.strip() if isinstance(task_id, str) and task_id.strip() else None
        self.touch()

    def register_agent(self, agent_id: str) -> None:
        self._add_unique(self.active_agents, agent_id, label="agente")
        self.touch()

    def register_tool(self, tool_name: str) -> None:
        self._add_unique(self.connected_tools, tool_name, label="tool")
        self.touch()

    def set_realtime_state(self, **values: Any) -> None:
        self.realtime_state.update(values)
        self.touch()

    @staticmethod
    def _add_unique(target: list[str], value: str, *, label: str) -> None:
        normalized = value.strip() if isinstance(value, str) else ""
        if not normalized:
            raise ValueError(f"El nombre de {label} no puede estar vacío")
        if normalized not in target:
            target.append(normalized)

    def prompt_summary(self) -> str:
        """Resumen breve y no ejecutable para el contexto del modelo."""

        lines = [f"- Modality: {self.modality}"]
        if self.active_task:
            lines.append(f"- Active task: {self.active_task}")
        if self.active_agents:
            lines.append(f"- Active agents: {', '.join(self.active_agents[:8])}")
        if self.project_id:
            lines.append(f"- Active project: {self.project_id}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "conversation_id": self.conversation_id,
            "workspace_id": self.workspace_id,
            "project_id": self.project_id,
            "modality": self.modality,
            "active_task": self.active_task,
            "context": dict(self.context),
            "active_agents": list(self.active_agents),
            "connected_tools": list(self.connected_tools),
            "permissions": dict(self.permissions),
            "realtime_state": dict(self.realtime_state),
            "multimodal": self.multimodal.to_dict(),
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UnifiedSessionState:
        modality = data.get("modality", "text")
        if modality not in _MODALITIES:
            modality = "text"
        return cls(
            session_id=str(data.get("session_id") or ""),
            tenant_id=str(data.get("tenant_id") or ""),
            user_id=str(data.get("user_id") or ""),
            conversation_id=str(data.get("conversation_id") or ""),
            workspace_id=data.get("workspace_id"),
            project_id=data.get("project_id"),
            modality=modality,
            active_task=data.get("active_task"),
            context=dict(data.get("context") or {}),
            active_agents=list(data.get("active_agents") or []),
            connected_tools=list(data.get("connected_tools") or []),
            permissions=dict(data.get("permissions") or {}),
            realtime_state=dict(data.get("realtime_state") or {}),
            multimodal=MultimodalSessionState.from_dict(data.get("multimodal") or {}),
            updated_at=float(data.get("updated_at") or time.time()),
        )
