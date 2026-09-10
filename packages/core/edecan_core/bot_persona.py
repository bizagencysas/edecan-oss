"""Neutral persona construction for user-configured persistent agents."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from edecan_schemas import PersonaConfig

BOT_CHAT_SOCIAL_TOOL_NAMES: tuple[str, ...] = (
    "enviar_mensaje_bot",
    "listar_bots",
    "avisar_avance",
    "preguntar_al_usuario",
    "buscar_skills",
    "listar_skills",
    "instalar_skill",
    "usar_skill",
    "listar_mcp",
    "conectar_mcp",
    "desconectar_mcp",
)
BOT_CHAT_COMMUNITY_MANAGER_TOOL_NAMES: tuple[str, ...] = (
    "generar_contenido",
    "crear_contenido_social",
    "crear_post_linkedin",
    "publicar_social",
    "estado_conectores_sociales",
    "listar_borradores_sociales",
)
BOT_CHAT_AUTOMATIONS_TOOL_NAMES: tuple[str, ...] = ("gestionar_automatizacion",)
BOT_CHAT_DOCUMENT_TOOL_NAMES: tuple[str, ...] = (
    "leer_archivo",
    "analizar_imagen",
    "consultar_documentos",
)
BOT_CHAT_REMOTE_ALWAYS_TOOL_NAMES: tuple[str, ...] = (
    *BOT_CHAT_SOCIAL_TOOL_NAMES,
    *BOT_CHAT_COMMUNITY_MANAGER_TOOL_NAMES,
    *BOT_CHAT_AUTOMATIONS_TOOL_NAMES,
    *BOT_CHAT_DOCUMENT_TOOL_NAMES,
)


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


def _avisar_avance_block(language: str) -> str:
    if language == "en":
        return (
            "REPORT PROGRESS — NEVER SILENCE: during long or multi-step work, use "
            "`avisar_avance` to report progress (when starting, at each finding, "
            "blocker or decision, and when closing). Do not stay silent until the "
            "user asks."
        )
    return (
        "AVISAR AVANCE — NUNCA SILENCIO: en trabajos largos o de varios pasos, usa "
        "`avisar_avance` para informar del progreso (al empezar, ante hallazgos, "
        "bloqueos o decisiones, y al cerrar). No te quedes callado hasta que el "
        "usuario pregunte."
    )


def _oauth_honesty_block(language: str) -> str:
    if language == "en":
        return (
            "OAuth honesty contract: before claiming a social account is connected, "
            "verify the real state with `estado_conectores_sociales`. Never say "
            "\"you're already connected\" without having checked it. If a connector "
            "is missing, say so plainly and without pressure, and point the user to "
            "connect it in Perfil → Conectores."
        )
    return (
        "Contrato de honestidad OAuth: antes de afirmar que una cuenta de redes "
        "sociales está conectada, verifica el estado real con "
        "`estado_conectores_sociales`. PROHIBIDO decir «ya estás conectado» sin "
        "haberlo comprobado. Si falta un conector, dilo con claridad y sin presión, "
        "y orienta al usuario a conectarlo en Perfil → Conectores."
    )


def bot_turn_instructions(worker: Mapping[str, Any], *, language: str = "es") -> str:
    """Build instructions solely from generic safety rules and tenant configuration."""
    return "\n\n".join(
        (
            _identity_block(worker, language),
            _avisar_avance_block(language),
            _oauth_honesty_block(language),
        )
    )


def persona_from_worker(worker: Mapping[str, Any], *, language: str = "es") -> PersonaConfig:
    relation = str(worker.get("relation") or "").strip()
    if relation not in ("profesional", "amigo", "coach"):
        relation = "profesional"
    return PersonaConfig(nombre_asistente=worker_display_name(worker), idioma=language, instrucciones=bot_turn_instructions(worker, language=language), memoria_activada=True, estilo_relacion=relation)
