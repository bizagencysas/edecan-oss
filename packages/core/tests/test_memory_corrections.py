"""`propose_preference_candidates` — candidatos a preferencia durable desde
correcciones repetidas (product design§172), más
`propose_correction_candidates_from_messages` (patrones de corrección en
mensajes del chat)."""

from typing import Any

from edecan_core.memory.corrections import (
    propose_correction_candidates_from_messages,
    propose_preference_candidates,
)


def _pref(content: str, kind: str = "preference") -> dict:
    return {"kind": kind, "content": content}


def _msg(content: Any, role: str = "user") -> dict:
    return {"role": role, "content": content}


def test_tres_repeticiones_producen_un_candidato() -> None:
    items = [
        _pref("Siempre incluye fuentes primarias"),
        _pref("Siempre incluye fuentes primarias"),
        _pref("  siempre incluye fuentes primarias "),
        _pref("Prefiere respuestas breves"),
    ]

    candidatos = propose_preference_candidates(items)

    assert candidatos == [
        {
            "text": "Siempre incluye fuentes primarias",
            "source": "corrección repetida (3x)",
            "scope": "user",
            "confidence": 0.85,
        }
    ]


def test_bajo_el_umbral_no_propone_nada() -> None:
    items = [_pref("Primero dame el resumen"), _pref("primero dame el resumen")]

    assert propose_preference_candidates(items) == []


def test_ignora_kinds_no_preference_y_contenido_vacio() -> None:
    items = [
        _pref("Siempre cita la fuente"),
        _pref("Siempre cita la fuente"),
        _pref("Siempre cita la fuente"),
        {"kind": "fact", "content": "Trabaja en una agencia"},
        {"kind": "preference", "content": "   "},
    ]

    assert len(propose_preference_candidates(items)) == 1


def test_ordena_por_conteo_y_respeta_el_tope() -> None:
    items = (
        [_pref("Siempre incluye fuentes primarias")] * 4
        + [_pref("Primero dame el resumen")] * 3
    )

    candidatos = propose_preference_candidates(items, max_candidates=1)

    assert len(candidatos) == 1
    assert candidatos[0]["text"] == "Siempre incluye fuentes primarias"
    assert candidatos[0]["source"] == "corrección repetida (4x)"


def test_confidence_crece_con_las_repeticiones_y_se_satura() -> None:
    assert (
        propose_preference_candidates([_pref("Prefiere el modo oscuro")] * 3)[0]["confidence"]
        == 0.85
    )
    assert (
        propose_preference_candidates([_pref("Prefiere el modo oscuro")] * 6)[0]["confidence"]
        == 0.95
    )


def test_no_muta_ni_escribe_nada() -> None:
    items = [_pref("Siempre cita la fuente")] * 3

    candidatos = propose_preference_candidates(items)

    assert items == [_pref("Siempre cita la fuente")] * 3
    assert candidatos[0]["scope"] == "user"


# ---------------------------------------------------------------------------
# propose_correction_candidates_from_messages (patrones de corrección en chat)
# ---------------------------------------------------------------------------


def test_detecta_patron_no_vuelvas_a_en_mensaje() -> None:
    mensajes = [
        _msg("hola"),
        _msg({"text": "No vuelvas a saludarme tan formal"}),
    ]

    candidatos = propose_correction_candidates_from_messages(mensajes)

    assert candidatos == [
        {
            "text": "No vuelvas a saludarme tan formal",
            "source": "corrección en mensajes (1x)",
            "scope": "user",
            "confidence": 0.65,
        }
    ]


def test_detecta_patrones_siempre_haz_y_primero_dame() -> None:
    mensajes = [
        _msg({"text": "Siempre haz el resumen primero"}),
        _msg({"text": "Primero dame los números, después la opinión"}),
    ]

    candidatos = propose_correction_candidates_from_messages(mensajes)

    assert {c["text"] for c in candidatos} == {
        "Siempre haz el resumen primero",
        "Primero dame los números, después la opinión",
    }


def test_ignora_roles_no_user_y_contenido_sin_patron() -> None:
    mensajes = [
        _msg("Recuérdame comprar pan"),  # sin patrón de corrección
        _msg({"text": "No vuelvas a interrumpirme"}, role="assistant"),  # no es user
        _msg({"text": ""}),  # vacío
    ]

    assert propose_correction_candidates_from_messages(mensajes) == []


def test_contenido_string_plano_tambien_se_escanea() -> None:
    mensajes = [_msg("De ahora en adelante responde en español")]

    candidatos = propose_correction_candidates_from_messages(mensajes)

    assert len(candidatos) == 1
    assert candidatos[0]["source"] == "corrección en mensajes (1x)"


def test_repeticiones_aumentan_confidence_y_el_source_las_refleja() -> None:
    mensajes = [
        _msg({"text": "No vuelvas a usar emojis"}),
        _msg({"text": "no vuelvas a usar emojis"}),
    ]

    candidatos = propose_correction_candidates_from_messages(mensajes)

    assert len(candidatos) == 1
    assert candidatos[0]["source"] == "corrección en mensajes (2x)"
    assert candidatos[0]["confidence"] == 0.75


def test_respeta_el_tope_max_candidates() -> None:
    mensajes = [
        _msg({"text": f"No vuelvas a hacer {i}"}) for i in range(10)
    ]

    assert len(propose_correction_candidates_from_messages(mensajes, max_candidates=3)) == 3
