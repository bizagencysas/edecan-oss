"""Tests for grok_face avatar descriptors."""

from __future__ import annotations

import pytest
from edecan_creative import avatars

_HEX = "#0123456789abcdef"


def _es_hex(color: str) -> bool:
    return (
        len(color) == 7
        and color.startswith("#")
        and all(c in _HEX for c in color[1:])
    )


def test_grok_face_devuelve_forma_ojos_y_relleno():
    descriptor = avatars.generar_avatar_grok_face("Botsito")

    assert descriptor["style"] == "grok_face"
    assert descriptor["seed"] == "Botsito"
    assert descriptor["shape"] in (
        "circle",
        "rounded_square",
        "oval",
        "hexagon",
        "squircle",
    )
    assert _es_hex(descriptor["fill"])
    eyes = descriptor["eyes"]
    assert eyes["style"] == "slanted_dots"
    assert eyes["color"] == "#ffffff"
    assert "x" in eyes["left"] and "rotation" in eyes["left"]
    assert "x" in eyes["right"] and "rotation" in eyes["right"]


def test_mismo_seed_da_el_mismo_descriptor_grok_face():
    assert avatars.generar_avatar_grok_face("Malandri") == avatars.generar_avatar_grok_face(
        "Malandri"
    )


def test_seeds_distintos_dan_descriptores_grok_distintos():
    assert avatars.avatar_para_agente("alfa") != avatars.avatar_para_agente("beta")


def test_geometrico_devuelve_la_forma_esperada():
    descriptor = avatars.generar_avatar_geometrico("Sofía")

    assert descriptor["style"] == "geometric"
    assert descriptor["seed"] == "Sofía"
    assert _es_hex(descriptor["accent"])
    assert isinstance(descriptor["gradient"], list)
    assert len(descriptor["gradient"]) == 2
    assert all(_es_hex(c) for c in descriptor["gradient"])
    assert descriptor["initials"]


def test_profesional_devuelve_la_forma_esperada():
    descriptor = avatars.generar_avatar_profesional("Elena")

    assert descriptor["style"] == "professional"
    assert descriptor["seed"] == "Elena"
    assert _es_hex(descriptor["accent"])
    assert _es_hex(descriptor["base"])
    assert descriptor["initials"]


def test_acento_valido_normaliza_a_minusculas_y_seis_digitos():
    descriptor = avatars.generar_avatar_grok_face("x", acento="#FF0000")
    assert descriptor["fill"] == "#ff0000"


def test_acento_invalido_lanza_value_error():
    with pytest.raises(ValueError):
        avatars.generar_avatar_grok_face("x", acento="no es un color")


def test_estilo_desconocido_lanza_value_error():
    with pytest.raises(ValueError):
        avatars.avatar_para_agente("x", style="fotorealista")


def test_avatar_para_agente_default_es_grok_face():
    assert avatars.avatar_para_agente("x")["style"] == "grok_face"
    assert avatars.avatar_para_agente("x", style="geometric")["style"] == "geometric"
    assert avatars.avatar_para_agente("x", style="professional")["style"] == "professional"
