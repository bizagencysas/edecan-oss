"""Tests de `edecan_advisory._plantillas`: las plantillas rellenan sus campos."""

from __future__ import annotations

import pytest
from edecan_advisory._plantillas import TIPOS_BORRADOR, renderizar


def test_tipos_borrador_tiene_los_tres_pinned():
    assert TIPOS_BORRADOR == ("nda", "carta_formal", "acuerdo_simple")


def test_renderizar_nda_rellena_todos_los_campos():
    resultado = renderizar(
        "nda",
        {
            "parte_a": "Acme S.A.",
            "parte_b": "Beta Ltda.",
            "objeto": "un proyecto de software",
            "vigencia": "2 años",
            "jurisdiccion": "Colombia",
            "fecha": "2026-01-15",
        },
    )

    assert "Acme S.A." in resultado
    assert "Beta Ltda." in resultado
    assert "un proyecto de software" in resultado
    assert "2 años" in resultado
    assert "Colombia" in resultado
    assert "2026-01-15" in resultado
    assert "{" not in resultado and "}" not in resultado  # ningún placeholder sin resolver


def test_renderizar_carta_formal_rellena_campos():
    resultado = renderizar(
        "carta_formal",
        {
            "destinatario": "Juan Pérez",
            "asunto": "Confirmación de reunión",
            "cuerpo": "Confirmo nuestra reunión del jueves a las 10am.",
            "remitente": "Ana Gómez",
            "fecha": "2026-02-01",
        },
    )

    assert "Juan Pérez" in resultado
    assert "Confirmación de reunión" in resultado
    assert "Confirmo nuestra reunión del jueves a las 10am." in resultado
    assert "Ana Gómez" in resultado


def test_renderizar_campo_faltante_se_muestra_entre_corchetes():
    # No se manda 'objeto' — debe quedar visible como marcador, no reventar.
    resultado = renderizar("acuerdo_simple", {"parte_a": "Ana", "parte_b": "Luis"})

    assert "Ana" in resultado
    assert "Luis" in resultado
    assert "[objeto]" in resultado
    assert "[terminos]" in resultado


def test_renderizar_sin_fecha_usa_hoy_por_defecto():
    from datetime import date

    campos = {"parte_a": "Ana", "parte_b": "Luis", "objeto": "x", "terminos": "y", "vigencia": "z"}
    resultado = renderizar("acuerdo_simple", campos)

    assert date.today().isoformat() in resultado


def test_renderizar_tipo_desconocido_lanza_value_error():
    with pytest.raises(ValueError, match="desconocido"):
        renderizar("contrato_laboral", {"parte_a": "Ana"})


def test_renderizar_ignora_campos_none():
    # Un campo con valor `None` no debe pisar el fallback `[campo]`.
    resultado = renderizar(
        "acuerdo_simple",
        {"parte_a": "Ana", "parte_b": "Luis", "objeto": None, "terminos": "y", "vigencia": "z"},
    )
    assert "[objeto]" in resultado
