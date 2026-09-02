"""Neutral persona construction for user-configured persistent agents."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from edecan_schemas import PersonaConfig


def worker_display_name(worker: Mapping[str, Any]) -> str:
    display = str(worker.get("display_name") or "").strip()
    if display:
        return display
    return str(worker.get("name") or "Agent").strip() or "Agent"


def _identity_block(worker: Mapping[str, Any], language: str) -> str:
    name = worker_display_name(worker)
    if language == "en":
        parts = [f"You are {name}, a user-configured agent in Edecán."]
        labels = {"purpose": "Purpose", "personality": "Personality", "communication_style": "Communication style", "job_description": "Role", "instructions": "Instructions", "constraints": "Constraints"}
        closing = "Keep this agent identity distinct, follow the configured constraints, protect user data, and report results truthfully."
    else:
        parts = [f"Eres {name}, un agente configurado por el usuario en Edecán."]
        labels = {"purpose": "Propósito", "personality": "Personalidad", "communication_style": "Estilo de comunicación", "job_description": "Rol", "instructions": "Instrucciones", "constraints": "Límites"}
        closing = "Mantén esta identidad separada, respeta los límites configurados, protege los datos del usuario y reporta los resultados con veracidad."
    for key, label in labels.items():
        value = str(worker.get(key) or "").strip()
        if value:
            parts.append(f"{label}: {value}")
    role = str(worker.get("role_title") or worker.get("role_short") or "").strip()
    if role:
        parts.append(f"Role title: {role}" if language == "en" else f"Título de rol: {role}")
    parts.append(closing)
    return "\n\n".join(parts)


def bot_turn_instructions(worker: Mapping[str, Any], *, language: str = "es") -> str:
    """Build instructions solely from generic safety rules and tenant configuration."""
    return _identity_block(worker, language)


def persona_from_worker(worker: Mapping[str, Any], *, language: str = "es") -> PersonaConfig:
    relation = str(worker.get("relation") or "").strip()
    if relation not in ("profesional", "amigo", "coach"):
        relation = "profesional"
    return PersonaConfig(nombre_asistente=worker_display_name(worker), idioma=language, instrucciones=bot_turn_instructions(worker, language=language), memoria_activada=True, estilo_relacion=relation)
