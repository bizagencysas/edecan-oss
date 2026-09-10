"""avisar_avance must stay in the always-on bot tool set."""

from edecan_core.capability_routing import _ALWAYS_AVAILABLE


def test_avisar_avance_siempre_disponible() -> None:
    assert "avisar_avance" in _ALWAYS_AVAILABLE


def test_persona_manda_avisar_avance() -> None:
    from edecan_core.bot_persona import bot_turn_instructions

    texto = bot_turn_instructions({"name": "BotBeta", "purpose": "código"})
    assert "avisar_avance" in texto
    assert "NUNCA SILENCIO" in texto or "nunca silencio" in texto.lower()
