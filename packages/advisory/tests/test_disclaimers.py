"""Tests de `edecan_advisory._disclaimers` y, sobre todo,
`test_disclaimers_en_todas`: EL TEST MÁS IMPORTANTE DEL WP (ROADMAP_V2.md
§8.3 — "Salud/Legal/Finanzas: informativo + disclaimer", regla no
negociable). Itera las 8 tools del paquete con fakes y comprueba que
`resultado.content` termina EXACTAMENTE con el disclaimer correcto para cada
una en su camino feliz.
"""

from __future__ import annotations

import json
from uuid import uuid4

from edecan_advisory._disclaimers import (
    DISCLAIMER_EDU,
    DISCLAIMER_LEGAL,
    DISCLAIMER_SALUD,
    with_disclaimer,
)
from edecan_advisory.educacion import TutorEvaluarTool, TutorLeccionTool
from edecan_advisory.legal import (
    AnalizarContratoTool,
    CompararContratosTool,
    GenerarBorradorLegalTool,
)
from edecan_advisory.salud import AnalizarLaboratorioTool, RegistrarSaludTool, ResumenSaludTool


def test_with_disclaimer_agrega_al_final_separado_por_linea_en_blanco():
    resultado = with_disclaimer("legal", "Cuerpo de la respuesta.")
    assert resultado == f"Cuerpo de la respuesta.\n\n{DISCLAIMER_LEGAL}"


def test_with_disclaimer_no_duplica_si_ya_termina_en_el_disclaimer():
    ya_tiene = f"Cuerpo.\n\n{DISCLAIMER_SALUD}"
    assert with_disclaimer("salud", ya_tiene) == ya_tiene


def test_with_disclaimer_texto_vacio_devuelve_solo_el_disclaimer():
    assert with_disclaimer("edu", "") == DISCLAIMER_EDU


async def test_disclaimers_en_todas(make_ctx, make_session, make_llm, make_archivo, fake_texto):
    """Itera las 8 tools de `edecan_advisory` con fakes y verifica que la
    respuesta del camino feliz termina con el disclaimer de su categoría."""

    casos: list[tuple[str, object, str]] = []

    # 1) analizar_contrato (legal) — vía `texto` directo, sin tocar S3.
    llm_contrato = make_llm(
        texto=json.dumps(
            {
                "partes": ["Acme S.A.", "Beta Ltda."],
                "objeto": "prestación de servicios de consultoría",
                "vigencia": "12 meses",
                "obligaciones_clave": [
                    "Acme debe pagar mensualmente",
                    "Beta debe entregar informes",
                ],
                "riesgos": [
                    {"clausula": "5.2", "riesgo": "penalidad ambigua", "severidad": "media"}
                ],
                "resumen": "Contrato de servicios estándar con riesgos menores.",
            }
        )
    )
    ctx_contrato = make_ctx(llm=llm_contrato)
    resultado_contrato = await AnalizarContratoTool().run(
        ctx_contrato, {"texto": "Contrato de prueba entre Acme S.A. y Beta Ltda."}
    )
    casos.append(("analizar_contrato", resultado_contrato, DISCLAIMER_LEGAL))

    # 2) comparar_contratos (legal) — dos archivos de texto plano con un diff real.
    texto_v1 = b"Clausula 1: pago mensual.\nClausula 2: plazo 6 meses.\n"
    texto_v2 = b"Clausula 1: pago mensual.\nClausula 2: plazo 12 meses.\n"
    fake_texto.archivos = [
        make_archivo(contenido=texto_v1, filename="v1.txt", mime="text/plain"),
        make_archivo(contenido=texto_v2, filename="v2.txt", mime="text/plain"),
    ]
    llm_diff = make_llm(texto="Se extendió el plazo de 6 a 12 meses.")
    ctx_diff = make_ctx(llm=llm_diff)
    resultado_diff = await CompararContratosTool().run(
        ctx_diff, {"file_id_a": str(uuid4()), "file_id_b": str(uuid4())}
    )
    casos.append(("comparar_contratos", resultado_diff, DISCLAIMER_LEGAL))

    # 3) generar_borrador_legal (legal) — sube el .md vía el `fake_texto` compartido.
    llm_borrador = make_llm(texto="ACUERDO SIMPLE pulido entre Ana y Luis sobre consultoría.")
    ctx_borrador = make_ctx(llm=llm_borrador)
    resultado_borrador = await GenerarBorradorLegalTool().run(
        ctx_borrador,
        {
            "tipo": "acuerdo_simple",
            "campos": {
                "parte_a": "Ana",
                "parte_b": "Luis",
                "objeto": "servicios de consultoría",
                "terminos": "Ana pagará a Luis 1000 USD mensuales.",
                "vigencia": "6 meses",
            },
        },
    )
    casos.append(("generar_borrador_legal", resultado_borrador, DISCLAIMER_LEGAL))

    # 4) registrar_salud (salud)
    ctx_registrar = make_ctx(session=make_session([[{"id": uuid4()}]]))
    resultado_registrar = await RegistrarSaludTool().run(
        ctx_registrar, {"kind": "agua", "valor": {"cantidad": 500, "unidad": "ml"}}
    )
    casos.append(("registrar_salud", resultado_registrar, DISCLAIMER_SALUD))

    # 5) resumen_salud (salud)
    from datetime import UTC, datetime

    fila_salud = {
        "kind": "habito",
        "valor": {"cantidad": 1},
        "registrado_en": datetime(2026, 1, 3, 8, 0, tzinfo=UTC),
    }
    ctx_resumen = make_ctx(session=make_session([[fila_salud]]))
    resultado_resumen = await ResumenSaludTool().run(ctx_resumen, {})
    casos.append(("resumen_salud", resultado_resumen, DISCLAIMER_SALUD))

    # 6) analizar_laboratorio (salud, reforzado)
    fake_texto.archivos = [
        make_archivo(
            contenido=b"Glucosa 95 mg/dL\nColesterol total 180 mg/dL\n",
            filename="laboratorio.txt",
            mime="text/plain",
        )
    ]
    llm_lab = make_llm(texto="La glucosa mide el azúcar circulante en la sangre.")
    ctx_lab = make_ctx(llm=llm_lab)
    resultado_lab = await AnalizarLaboratorioTool().run(ctx_lab, {"file_id": str(uuid4())})
    casos.append(("analizar_laboratorio", resultado_lab, DISCLAIMER_SALUD))

    # 7) tutor_leccion (edu)
    llm_leccion = make_llm(
        texto=json.dumps(
            {
                "explicacion": "Una fracción representa una parte de un todo.",
                "ejemplos": ["1/2 es la mitad de algo."],
                "ejercicios": [{"pregunta": "¿Cuánto es 1/2 + 1/4?", "respuesta_correcta": "3/4"}],
            }
        )
    )
    ctx_leccion = make_ctx(llm=llm_leccion)
    resultado_leccion = await TutorLeccionTool().run(ctx_leccion, {"tema": "fracciones"})
    casos.append(("tutor_leccion", resultado_leccion, DISCLAIMER_EDU))

    # 8) tutor_evaluar (edu) — recupera la "última lección" desde el session fake.
    ejercicio_previo = {"pregunta": "¿Cuánto es 1/2 + 1/4?", "respuesta_correcta": "3/4"}
    leccion_previa = {"id": uuid4(), "leccion": {"ejercicios": [ejercicio_previo]}}
    llm_evaluar = make_llm(
        texto=json.dumps({"correcciones": [{"correcto": True, "comentario": "¡Exacto!"}]})
    )
    ctx_evaluar = make_ctx(session=make_session([[leccion_previa]]), llm=llm_evaluar)
    resultado_evaluar = await TutorEvaluarTool().run(
        ctx_evaluar, {"tema": "fracciones", "respuestas": ["3/4"]}
    )
    casos.append(("tutor_evaluar", resultado_evaluar, DISCLAIMER_EDU))

    assert len(casos) == 8, "deben cubrirse las 8 tools de ROADMAP_V2.md §7.7"
    for nombre, resultado, disclaimer_esperado in casos:
        assert resultado.content.endswith(disclaimer_esperado), (
            f"{nombre}: el content no termina con el disclaimer esperado.\n"
            f"content=\n{resultado.content}"
        )
