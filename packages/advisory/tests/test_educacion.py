"""Tests de `edecan_advisory.educacion`: `tutor_leccion` y `tutor_evaluar`."""

from __future__ import annotations

import json
from uuid import uuid4

from edecan_advisory._disclaimers import DISCLAIMER_EDU
from edecan_advisory.educacion import TutorEvaluarTool, TutorLeccionTool

# ---------------------------------------------------------------------------
# tutor_leccion
# ---------------------------------------------------------------------------


async def test_tutor_leccion_sin_tema_no_llama_al_llm_ni_a_la_sesion(
    make_ctx, make_session, make_llm
):
    session = make_session([])
    llm = make_llm()
    ctx = make_ctx(session=session, llm=llm)

    resultado = await TutorLeccionTool().run(ctx, {"tema": "  "})

    assert "Dime sobre qué tema" in resultado.content
    assert llm.llamadas == []
    assert session.llamadas == []


async def test_tutor_leccion_llm_no_devuelve_nada_util(make_ctx, make_llm):
    ctx = make_ctx(llm=make_llm(texto="no tengo idea"))
    resultado = await TutorLeccionTool().run(ctx, {"tema": "álgebra"})
    assert "No logré generar una lección" in resultado.content


async def test_tutor_leccion_muestra_preguntas_sin_revelar_las_respuestas(
    make_ctx, make_session, make_llm
):
    llm = make_llm(
        texto=json.dumps(
            {
                "explicacion": "Las fracciones representan partes de un todo.",
                "ejemplos": ["1/2 es la mitad de algo."],
                "ejercicios": [{"pregunta": "¿Cuánto es 1/2 + 1/4?", "respuesta_correcta": "3/4"}],
            }
        )
    )
    session = make_session([])
    ctx = make_ctx(session=session, llm=llm)

    resultado = await TutorLeccionTool().run(ctx, {"tema": "fracciones", "nivel": "intermedio"})

    assert "¿Cuánto es 1/2 + 1/4?" in resultado.content
    assert "3/4" not in resultado.content  # la respuesta correcta NUNCA se revela aquí
    assert resultado.content.endswith(DISCLAIMER_EDU)
    assert resultado.data == {"tema": "fracciones", "nivel": "intermedio", "n_ejercicios": 1}

    sql, params = session.llamadas[0]
    assert "INSERT INTO learning_progress" in sql
    assert params["tema"] == "fracciones"
    assert params["nivel"] == "intermedio"
    leccion_guardada = json.loads(params["leccion"])
    assert leccion_guardada["ejercicios"][0]["respuesta_correcta"] == "3/4"  # sí se persiste


async def test_tutor_leccion_nivel_por_defecto_es_inicial(make_ctx, make_session, make_llm):
    llm = make_llm(texto=json.dumps({"explicacion": "x", "ejemplos": [], "ejercicios": []}))
    ctx = make_ctx(session=make_session([]), llm=llm)

    resultado = await TutorLeccionTool().run(ctx, {"tema": "historia"})

    assert resultado.data["nivel"] == "inicial"


# ---------------------------------------------------------------------------
# tutor_evaluar
# ---------------------------------------------------------------------------


async def test_tutor_evaluar_sin_tema(make_ctx):
    resultado = await TutorEvaluarTool().run(make_ctx(), {"tema": "  ", "respuestas": ["x"]})
    assert "Dime de qué tema" in resultado.content


async def test_tutor_evaluar_sin_respuestas(make_ctx):
    resultado = await TutorEvaluarTool().run(make_ctx(), {"tema": "fracciones", "respuestas": []})
    assert "Mándame al menos una respuesta" in resultado.content


async def test_tutor_evaluar_sin_leccion_previa(make_ctx, make_session):
    ctx = make_ctx(session=make_session([[]]))
    resultado = await TutorEvaluarTool().run(ctx, {"tema": "fracciones", "respuestas": ["3/4"]})
    assert "No encontré ninguna lección previa" in resultado.content


async def test_tutor_evaluar_leccion_sin_ejercicios(make_ctx, make_session):
    leccion_previa = {"id": uuid4(), "leccion": {"ejercicios": []}}
    ctx = make_ctx(session=make_session([[leccion_previa]]))
    resultado = await TutorEvaluarTool().run(ctx, {"tema": "fracciones", "respuestas": ["3/4"]})
    assert "no tiene ejercicios para evaluar" in resultado.content


async def test_tutor_evaluar_corrige_y_persiste_resultados(make_ctx, make_session, make_llm):
    ejercicio_previo = {"pregunta": "¿Cuánto es 1/2 + 1/4?", "respuesta_correcta": "3/4"}
    leccion_previa = {"id": uuid4(), "leccion": {"ejercicios": [ejercicio_previo]}}
    correcciones = {"correcciones": [{"correcto": True, "comentario": "¡Exacto!"}]}
    llm = make_llm(texto=json.dumps(correcciones))
    session = make_session([[leccion_previa]])
    ctx = make_ctx(session=session, llm=llm)

    resultado = await TutorEvaluarTool().run(ctx, {"tema": "fracciones", "respuestas": ["3/4"]})

    assert resultado.data == {
        "aciertos": 1,
        "total": 1,
        "feedback": [{"correcto": True, "comentario": "¡Exacto!"}],
    }
    assert "1/1" in resultado.content
    assert "¡Perfecto!" in resultado.content
    assert resultado.content.endswith(DISCLAIMER_EDU)

    sql_update, params_update = session.llamadas[1]
    assert "UPDATE learning_progress" in sql_update
    assert json.loads(params_update["resultados"])["aciertos"] == 1


async def test_tutor_evaluar_llm_formato_invalido_cae_a_comparacion_automatica(
    make_ctx, make_session, make_llm
):
    leccion_previa = {
        "id": uuid4(),
        "leccion": {
            "ejercicios": [
                {"pregunta": "2+2", "respuesta_correcta": "4"},
                {"pregunta": "3+3", "respuesta_correcta": "6"},
            ]
        },
    }
    llm = make_llm(texto="esto no es el JSON que pedí")
    ctx = make_ctx(session=make_session([[leccion_previa]]), llm=llm)

    resultado = await TutorEvaluarTool().run(ctx, {"tema": "sumas", "respuestas": ["4", "siete"]})

    assert resultado.data["aciertos"] == 1
    assert resultado.data["total"] == 2
    assert "Comparación automática" in resultado.content


async def test_tutor_evaluar_respuestas_de_mas_se_ignoran(make_ctx, make_session, make_llm):
    leccion_previa = {
        "id": uuid4(),
        "leccion": {"ejercicios": [{"pregunta": "2+2", "respuesta_correcta": "4"}]},
    }
    llm = make_llm(texto=json.dumps({"correcciones": [{"correcto": True, "comentario": "bien"}]}))
    ctx = make_ctx(session=make_session([[leccion_previa]]), llm=llm)

    # Manda dos respuestas para una lección con un solo ejercicio.
    resultado = await TutorEvaluarTool().run(ctx, {"tema": "sumas", "respuestas": ["4", "8"]})

    assert resultado.data["total"] == 1
