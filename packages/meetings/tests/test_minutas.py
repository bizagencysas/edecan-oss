"""Tests de `edecan_meetings.minutas` — funciones puras, sin red ni DB."""

from __future__ import annotations

import json

from edecan_meetings.minutas import (
    AccionMinuta,
    Minutas,
    construir_prompt_minutas,
    parsear_minutas,
)

# ---------------------------------------------------------------------------
# construir_prompt_minutas
# ---------------------------------------------------------------------------


def test_construir_prompt_minutas_incluye_titulo_y_transcript() -> None:
    prompt = construir_prompt_minutas("Hola a todos, hoy hablamos de X.", titulo="Sprint planning")
    assert "Sprint planning" in prompt
    assert "Hola a todos, hoy hablamos de X." in prompt
    assert "JSON" in prompt
    assert "resumen" in prompt and "decisiones" in prompt and "acciones" in prompt


def test_construir_prompt_minutas_titulo_ausente_usa_default() -> None:
    prompt = construir_prompt_minutas("texto", titulo=None)
    assert "Reunión sin título" in prompt


def test_construir_prompt_minutas_transcript_vacio_no_revienta() -> None:
    prompt = construir_prompt_minutas("   ", titulo="X")
    assert "no se detectó voz" in prompt or "no está conectado" in prompt


def test_construir_prompt_minutas_trunca_transcript_largo() -> None:
    largo = "palabra " * 20_000  # bastante más que _MAX_TRANSCRIPT_CHARS
    prompt = construir_prompt_minutas(largo, titulo="X")
    assert "se truncó" in prompt
    assert len(prompt) < len(largo)  # el prompt no incluye el texto completo


# ---------------------------------------------------------------------------
# parsear_minutas — camino feliz
# ---------------------------------------------------------------------------


def test_parsear_minutas_json_limpio() -> None:
    texto = json.dumps(
        {
            "resumen": "Se revisó el roadmap del trimestre.",
            "decisiones": ["Lanzar la v2 en marzo"],
            "acciones": [{"tarea": "Escribir el plan", "responsable": "Ana"}],
            "temas": ["roadmap", "planificación"],
        }
    )
    minutas = parsear_minutas(texto)
    assert isinstance(minutas, Minutas)
    assert minutas.resumen == "Se revisó el roadmap del trimestre."
    assert minutas.decisiones == ["Lanzar la v2 en marzo"]
    assert minutas.acciones == [AccionMinuta(tarea="Escribir el plan", responsable="Ana")]
    assert minutas.temas == ["roadmap", "planificación"]


def test_parsear_minutas_tolera_fence_json() -> None:
    contenido = {"resumen": "R", "decisiones": [], "acciones": [], "temas": []}
    texto = "```json\n" + json.dumps(contenido) + "\n```"
    minutas = parsear_minutas(texto)
    assert minutas.resumen == "R"


def test_parsear_minutas_tolera_fence_sin_lenguaje() -> None:
    texto = "```\n" + json.dumps({"resumen": "R2"}) + "\n```"
    minutas = parsear_minutas(texto)
    assert minutas.resumen == "R2"


def test_parsear_minutas_tolera_texto_antes_y_despues_del_json() -> None:
    texto = (
        "Aquí están las minutas:\n"
        + json.dumps({"resumen": "R3", "decisiones": ["d1"], "acciones": [], "temas": []})
        + "\nEspero que sirva."
    )
    minutas = parsear_minutas(texto)
    assert minutas.resumen == "R3"
    assert minutas.decisiones == ["d1"]


def test_parsear_minutas_accion_como_string_suelto() -> None:
    texto = json.dumps({"resumen": "R", "acciones": ["Enviar el contrato"]})
    minutas = parsear_minutas(texto)
    assert minutas.acciones == [AccionMinuta(tarea="Enviar el contrato", responsable=None)]


def test_parsear_minutas_accion_sin_responsable_queda_none() -> None:
    texto = json.dumps({"resumen": "R", "acciones": [{"tarea": "X"}]})
    minutas = parsear_minutas(texto)
    assert minutas.acciones[0].responsable is None


def test_parsear_minutas_ignora_campos_extra() -> None:
    texto = json.dumps({"resumen": "R", "campo_inventado": "algo", "decisiones": []})
    minutas = parsear_minutas(texto)
    assert minutas.resumen == "R"


# ---------------------------------------------------------------------------
# parsear_minutas — tolerancia a fallos (nunca lanza)
# ---------------------------------------------------------------------------


def test_parsear_minutas_texto_vacio() -> None:
    minutas = parsear_minutas("")
    assert minutas.resumen == "El modelo no devolvió ninguna minuta."
    assert minutas.decisiones == []
    assert minutas.acciones == []
    assert minutas.temas == []


def test_parsear_minutas_json_invalido_cae_a_resumen_crudo() -> None:
    texto = "esto no es JSON en absoluto, es prosa libre sobre la reunión."
    minutas = parsear_minutas(texto)
    assert minutas.resumen == texto
    assert minutas.decisiones == []
    assert minutas.acciones == []
    assert minutas.temas == []


def test_parsear_minutas_json_es_una_lista_no_un_objeto() -> None:
    minutas = parsear_minutas(json.dumps(["no", "es", "un", "objeto"]))
    assert minutas.decisiones == []
    assert minutas.acciones == []


def test_parsear_minutas_tipos_inesperados_se_ignoran_sin_lanzar() -> None:
    texto = json.dumps(
        {
            "resumen": 123,  # no es string
            "decisiones": "no es una lista",
            "acciones": {"no": "es una lista"},
            "temas": None,
        }
    )
    minutas = parsear_minutas(texto)
    # `resumen` no-string cae al fallback del texto crudo (recortado).
    assert isinstance(minutas.resumen, str) and minutas.resumen
    assert minutas.decisiones == []
    assert minutas.acciones == []
    assert minutas.temas == []


def test_parsear_minutas_sin_resumen_usa_texto_crudo() -> None:
    texto = json.dumps({"decisiones": ["d1"]})
    minutas = parsear_minutas(texto)
    assert minutas.resumen  # no vacío
    assert minutas.decisiones == ["d1"]


# ---------------------------------------------------------------------------
# to_dict — el shape que persiste process_meeting
# ---------------------------------------------------------------------------


def test_minutas_to_dict_shape() -> None:
    minutas = Minutas(
        resumen="R",
        decisiones=["d1"],
        acciones=[AccionMinuta(tarea="t1", responsable="Ana")],
        temas=["tema1"],
    )
    assert minutas.to_dict() == {
        "resumen": "R",
        "decisiones": ["d1"],
        "acciones": [{"tarea": "t1", "responsable": "Ana"}],
        "temas": ["tema1"],
    }
