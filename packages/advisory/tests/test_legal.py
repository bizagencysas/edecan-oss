"""Tests de `edecan_advisory.legal`: `analizar_contrato`, `comparar_contratos`
(con `difflib` determinista) y `generar_borrador_legal`."""

from __future__ import annotations

import difflib
import json
from uuid import uuid4

from edecan_advisory._disclaimers import DISCLAIMER_LEGAL
from edecan_advisory.legal import (
    AnalizarContratoTool,
    CompararContratosTool,
    GenerarBorradorLegalTool,
)

# ---------------------------------------------------------------------------
# analizar_contrato
# ---------------------------------------------------------------------------


async def test_analizar_contrato_sin_file_id_ni_texto(make_ctx):
    resultado = await AnalizarContratoTool().run(make_ctx(), {})
    assert "Necesito 'file_id' o 'texto'" in resultado.content


async def test_analizar_contrato_file_id_invalido(make_ctx):
    resultado = await AnalizarContratoTool().run(make_ctx(), {"file_id": "no-es-uuid"})
    assert "identificador válido" in resultado.content


async def test_analizar_contrato_archivo_no_encontrado(make_ctx, fake_texto):
    resultado = await AnalizarContratoTool().run(make_ctx(), {"file_id": str(uuid4())})
    assert "No encontré ese archivo" in resultado.content


async def test_analizar_contrato_prefiere_texto_directo_sobre_file_id(make_ctx, make_llm):
    llm = make_llm(texto=json.dumps({"objeto": "prueba"}))
    ctx = make_ctx(llm=llm)

    resultado = await AnalizarContratoTool().run(
        ctx, {"texto": "Contrato pegado directo.", "file_id": str(uuid4())}
    )

    assert "el texto que me diste" in resultado.content
    assert resultado.content.endswith(DISCLAIMER_LEGAL)


async def test_analizar_contrato_enfoque_se_incluye_en_el_prompt(make_ctx, make_llm):
    llm = make_llm(texto=json.dumps({"objeto": "x"}))
    ctx = make_ctx(llm=llm)

    await AnalizarContratoTool().run(
        ctx, {"texto": "contrato", "enfoque": "cláusulas de terminación"}
    )

    _alias, _flags, req = llm.llamadas[0]
    assert "cláusulas de terminación" in req.messages[0].content


async def test_analizar_contrato_llm_responde_json_invalido_cae_a_valores_por_defecto(
    make_ctx, make_llm
):
    llm = make_llm(texto="esto no es JSON en absoluto")
    ctx = make_ctx(llm=llm)

    resultado = await AnalizarContratoTool().run(ctx, {"texto": "Un contrato cualquiera."})

    assert "no identificadas" in resultado.content
    assert "no especificado" in resultado.content
    assert resultado.content.endswith(DISCLAIMER_LEGAL)


async def test_analizar_contrato_json_envuelto_en_markdown_se_parsea(make_ctx, make_llm):
    riesgo = {"clausula": "1", "riesgo": "x", "severidad": "alta"}
    datos = {"partes": ["A", "B"], "objeto": "servicios", "riesgos": [riesgo]}
    llm = make_llm(texto=f"```json\n{json.dumps(datos)}\n```")
    ctx = make_ctx(llm=llm)

    resultado = await AnalizarContratoTool().run(ctx, {"texto": "contrato"})

    assert "[ALTA]" in resultado.content
    assert resultado.data["objeto"] == "servicios"


# ---------------------------------------------------------------------------
# comparar_contratos
# ---------------------------------------------------------------------------


async def test_comparar_contratos_uuid_invalido(make_ctx):
    resultado = await CompararContratosTool().run(
        make_ctx(), {"file_id_a": "no-es-uuid", "file_id_b": str(uuid4())}
    )
    assert "identificadores válidos" in resultado.content


async def test_comparar_contratos_archivo_no_encontrado(make_ctx, fake_texto):
    fake_texto.archivos = []
    resultado = await CompararContratosTool().run(
        make_ctx(), {"file_id_a": str(uuid4()), "file_id_b": str(uuid4())}
    )
    assert "No encontré uno de los dos archivos" in resultado.content


async def test_comparar_contratos_diff_es_deterministico_via_difflib(
    make_ctx, make_llm, make_archivo, fake_texto
):
    texto_a = "linea1\nlinea2\nlinea3\n"
    texto_b = "linea1\nlinea2 cambiada\nlinea3\n"
    fake_texto.archivos = [
        make_archivo(contenido=texto_a.encode(), filename="v1.txt", mime="text/plain"),
        make_archivo(contenido=texto_b.encode(), filename="v2.txt", mime="text/plain"),
    ]
    ctx = make_ctx(llm=make_llm(texto="Cambio menor en línea 2."))

    resultado = await CompararContratosTool().run(
        ctx, {"file_id_a": str(uuid4()), "file_id_b": str(uuid4())}
    )

    esperado = list(
        difflib.unified_diff(
            texto_a.splitlines(),
            texto_b.splitlines(),
            fromfile="v1.txt",
            tofile="v2.txt",
            lineterm="",
        )
    )
    assert resultado.data["diff"] == esperado
    assert resultado.data["cambios_materiales"] == "Cambio menor en línea 2."


async def test_comparar_contratos_sin_diferencias_no_llama_al_llm(
    make_ctx, make_llm, make_archivo, fake_texto
):
    texto = "exactamente el mismo contenido\n"
    fake_texto.archivos = [
        make_archivo(contenido=texto.encode(), filename="v1.txt", mime="text/plain"),
        make_archivo(contenido=texto.encode(), filename="v2.txt", mime="text/plain"),
    ]
    llm = make_llm()
    ctx = make_ctx(llm=llm)

    resultado = await CompararContratosTool().run(
        ctx, {"file_id_a": str(uuid4()), "file_id_b": str(uuid4())}
    )

    assert "no encontré diferencias" in resultado.content.lower()
    assert llm.llamadas == []
    assert resultado.data["diff"] == []


async def test_comparar_contratos_diff_se_capa_a_200_lineas(
    make_ctx, make_llm, make_archivo, fake_texto
):
    texto_a = "\n".join(f"linea{i}" for i in range(300)) + "\n"
    texto_b = "\n".join(f"linea{i}-cambiada" for i in range(300)) + "\n"
    fake_texto.archivos = [
        make_archivo(contenido=texto_a.encode(), filename="v1.txt", mime="text/plain"),
        make_archivo(contenido=texto_b.encode(), filename="v2.txt", mime="text/plain"),
    ]
    ctx = make_ctx(llm=make_llm(texto="Cambios masivos."))

    resultado = await CompararContratosTool().run(
        ctx, {"file_id_a": str(uuid4()), "file_id_b": str(uuid4())}
    )

    assert len(resultado.data["diff"]) == 200
    assert "primeras 200" in resultado.content


# ---------------------------------------------------------------------------
# generar_borrador_legal
# ---------------------------------------------------------------------------


async def test_generar_borrador_legal_tipo_invalido(make_ctx):
    resultado = await GenerarBorradorLegalTool().run(
        make_ctx(), {"tipo": "contrato_laboral", "campos": {"parte_a": "Ana"}}
    )
    assert "no es un tipo de borrador válido" in resultado.content


async def test_generar_borrador_legal_campos_vacios(make_ctx):
    resultado = await GenerarBorradorLegalTool().run(make_ctx(), {"tipo": "nda", "campos": {}})
    assert "Necesito al menos algunos campos" in resultado.content


async def test_generar_borrador_legal_campos_no_es_dict(make_ctx):
    resultado = await GenerarBorradorLegalTool().run(
        make_ctx(), {"tipo": "nda", "campos": "no es un objeto"}
    )
    assert "Necesito al menos algunos campos" in resultado.content


async def test_generar_borrador_legal_guarda_md_en_s3_y_marca_borrador(
    make_ctx, make_llm, fake_texto
):
    llm = make_llm(texto="Texto pulido del NDA entre Acme y Beta.")
    ctx = make_ctx(llm=llm)

    resultado = await GenerarBorradorLegalTool().run(
        ctx,
        {
            "tipo": "nda",
            "campos": {
                "parte_a": "Acme",
                "parte_b": "Beta",
                "objeto": "un proyecto conjunto",
                "vigencia": "1 año",
                "jurisdiccion": "Colombia",
            },
        },
    )

    assert "BORRADOR" in resultado.content
    assert "revísalo con un abogado" in resultado.content
    assert resultado.content.endswith(DISCLAIMER_LEGAL)

    assert len(fake_texto.subidas) == 1
    subida = fake_texto.subidas[0]
    assert subida["mime"] == "text/markdown"
    assert subida["filename"].startswith("borrador-nda-acme")
    assert b"Texto pulido del NDA" in subida["contenido"]
    assert resultado.data["file_id"] == str(subida["file_id"])


async def test_generar_borrador_legal_llm_vacio_cae_al_borrador_sin_pulir(
    make_ctx, make_llm, fake_texto
):
    ctx = make_ctx(llm=make_llm(texto=""))

    resultado = await GenerarBorradorLegalTool().run(
        ctx,
        {
            "tipo": "acuerdo_simple",
            "campos": {
                "parte_a": "Ana",
                "parte_b": "Luis",
                "objeto": "x",
                "terminos": "y",
                "vigencia": "z",
            },
        },
    )

    assert "ACUERDO SIMPLE" in fake_texto.subidas[0]["contenido"].decode("utf-8")
    assert resultado.content.endswith(DISCLAIMER_LEGAL)
