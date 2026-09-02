"""Persona compartida para bots persistentes."""

from __future__ import annotations

from edecan_core.bot_persona import bot_turn_instructions, persona_from_worker, worker_display_name


def test_worker_display_name_prefers_display_name():
    worker = {"name": "worker-1", "display_name": "  Malandri  "}
    assert worker_display_name(worker) == "Malandri"


def test_bot_turn_instructions_incluye_identidad_y_never_refuse():
    worker = {
        "name": "bot-1",
        "display_name": "Botsito",
        "purpose": "Analista de crédito con tono directo.",
    }
    text = bot_turn_instructions(worker, language="es").lower()
    assert "botsito" in text
    assert "analista de crédito" in text
    assert "agente configurado por el usuario" in text
    assert "protege los datos del usuario" in text


def test_bot_turn_instructions_no_inyecta_configuracion_de_una_instalacion():
    worker = {"name": "bot-1", "display_name": "Botsito", "purpose": "X."}
    text = bot_turn_instructions(worker, language="es")
    assert "botsito" in text.lower()
    assert "protege los datos del usuario" in text.lower()
    assert "127.0.0.1" not in text
    assert "/users/" not in text.lower()
    assert "sol xhigh" not in text.lower()


def test_persona_from_worker_usa_nombre_y_memoria():
    worker = {"name": "x", "display_name": "Fronti", "purpose": "Curioso y breve."}
    persona = persona_from_worker(worker, language="es")
    assert persona.nombre_asistente == "Fronti"
    assert persona.memoria_activada is True
    assert persona.estilo_relacion == "profesional"
    assert "Fronti" in persona.instrucciones
