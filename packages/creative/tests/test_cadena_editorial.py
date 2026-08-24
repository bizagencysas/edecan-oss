"""Cablea `agenda.py` / `investigacion.py` / `auditoria.py` (escritos pero sin llamador antes
de esta fase) dentro del camino real de `CrearContenidoSocialTool.run` y de las dos tools
nuevas que completan la cadena editorial (`planificar_contenido_social`,
`investigar_noticias_frescas`). Cada test aísla UN eslabón de la cadena
investigar -> escribir -> auditar -> re-gate -> registrar rotación, igual criterio que
`test_social.py` para el resto de la tool.
"""

from __future__ import annotations

import json
from uuid import uuid4

import edecan_creative.social as social
from edecan_creative.investigacion import Titular
from edecan_creative.social import (
    CrearContenidoSocialTool,
    InvestigarNoticiasFrescasTool,
    PlanificarContenidoSocialTool,
    save_editorial_profile,
)


class UniqueUploader:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, ctx, *, data: bytes, filename: str, mime: str):  # noqa: ANN001
        file_id = uuid4()
        self.calls.append({"id": file_id, "data": data, "filename": filename, "mime": mime})
        return file_id, filename


class FakeLLM:
    """Doble de `LLMRouter`: `complete(alias, flags, request)` devuelve las respuestas
    programadas en orden, una por llamada (una por cada paso de la auditoría)."""

    def __init__(self, textos: list[str]) -> None:
        self._pendientes = list(textos)
        self.calls: list[tuple] = []

    async def complete(self, alias, flags, request):  # noqa: ANN001
        self.calls.append((alias, flags, request))
        from edecan_llm.base import CompletionResponse, Usage

        texto = self._pendientes.pop(0) if self._pendientes else "{}"
        return CompletionResponse(
            text=texto, usage=Usage(input_tokens=12, output_tokens=12), stop_reason="end"
        )


_TEXTO_LIMPIO = (
    "Un banco mexicano ajustó su tasa de interés para tarjetas de crédito este trimestre."
)


# ---------------------------------------------------------------------------
# Auditoría de segunda pasada (`edecan_creative.auditoria`), cableada dentro de
# `CrearContenidoSocialTool.run`.
# ---------------------------------------------------------------------------


async def test_run_sin_ctx_llm_no_ejecuta_auditoria(make_ctx):
    """`ctx.llm is None` (embebedores/pruebas sin modelo) degrada sin auditoría, igual que
    `get_editorial_profile` degrada sin `ctx.session` -- nunca tumba la tool."""
    uploader = UniqueUploader()
    tool = CrearContenidoSocialTool(uploader=uploader)

    result = await tool.run(
        make_ctx(),
        {"plataforma": "linkedin", "tema": "Créditos", "texto": _TEXTO_LIMPIO, "con_imagen": False},
    )

    assert result.data["copy"] == _TEXTO_LIMPIO
    assert len(uploader.calls) == 2  # markdown + json, sin imagen


async def test_run_con_llm_usa_el_texto_reescrito_por_el_auditor(make_ctx):
    texto_corregido = (
        "Un banco mexicano ajustó su tasa de interés para tarjetas de crédito, según la "
        "fuente entregada para este borrador."
    )
    llm = FakeLLM(
        [
            json.dumps({"publicable": True, "motivo": "Tiene un hecho concreto y verificable."}),
            json.dumps({"publicable": True, "problemas": [], "texto": texto_corregido}),
        ]
    )
    ctx = make_ctx()
    ctx.llm = llm
    tool = CrearContenidoSocialTool(uploader=UniqueUploader())

    result = await tool.run(
        ctx,
        {
            "plataforma": "linkedin",
            "tema": "Créditos en México",
            "texto": _TEXTO_LIMPIO,
            "con_imagen": False,
            "fuentes": [{"title": "Banco anuncia ajuste", "url": "https://example.com/n1"}],
        },
    )

    assert result.data["copy"] == texto_corregido
    assert len(llm.calls) == 2
    # `revisar_calidad` primero, `auditar_hechos` después (orden de `auditoria.finalizar_post`).
    assert llm.calls[0][0] == "principal"
    assert "editor jefe" in llm.calls[0][2].system.lower()
    assert "auditor factual" in llm.calls[1][2].system.lower()


async def test_run_veta_por_editor_de_calidad_sin_subir_nada(make_ctx):
    llm = FakeLLM([json.dumps({"publicable": False, "motivo": "No tiene un ángulo concreto."})])
    ctx = make_ctx()
    ctx.llm = llm
    uploader = UniqueUploader()
    tool = CrearContenidoSocialTool(uploader=uploader)

    result = await tool.run(
        ctx,
        {"plataforma": "linkedin", "tema": "Tema", "texto": _TEXTO_LIMPIO, "con_imagen": False},
    )

    assert "editor automático" in result.content
    assert "No tiene un ángulo concreto" in result.content
    assert uploader.calls == []
    assert len(llm.calls) == 1  # nunca llega a auditar_hechos


async def test_run_veta_por_auditor_de_hechos_sin_subir_nada(make_ctx):
    llm = FakeLLM(
        [
            json.dumps({"publicable": True, "motivo": "bien"}),
            json.dumps(
                {
                    "publicable": False,
                    "problemas": ["La tasa mencionada no aparece en ninguna fuente entregada."],
                }
            ),
        ]
    )
    ctx = make_ctx()
    ctx.llm = llm
    uploader = UniqueUploader()
    tool = CrearContenidoSocialTool(uploader=uploader)

    result = await tool.run(
        ctx,
        {"plataforma": "linkedin", "tema": "Créditos", "texto": _TEXTO_LIMPIO, "con_imagen": False},
    )

    assert "auditor de hechos" in result.content
    assert "no aparece en ninguna fuente" in result.content
    assert uploader.calls == []


async def test_run_repara_la_primera_persona_que_reintrodujo_el_auditor(make_ctx):
    """La lección crítica de `auditoria.py`, pero REPARANDO antes de rechazar.

    El auditor reescribe con un prompt que no conoce ninguna regla de estilo, así que meter un
    "vimos" es lo esperado. Aquí se rechazaba de plano; ahora se intenta el mismo pase de
    reparación que hace REFERENCIA en ese punto (`linkedin_content.py:1601-1605`) y el borrador
    sobrevive. Un rechazo por un pronombre mecánicamente arreglable le devolvía al usuario un
    turno perdido con un texto que él mismo había dictado bien.
    """
    texto_con_primera_persona = "Vimos que el texto reescrito por el auditor quedó distinto."
    llm = FakeLLM(
        [
            json.dumps({"publicable": True, "motivo": "bien"}),
            json.dumps({"publicable": True, "problemas": [], "texto": texto_con_primera_persona}),
            # El reparador de primera persona responde con el texto pelado.
            _TEXTO_LIMPIO,
        ]
    )
    ctx = make_ctx()
    ctx.llm = llm
    uploader = UniqueUploader()
    tool = CrearContenidoSocialTool(uploader=uploader)

    result = await tool.run(
        ctx,
        {"plataforma": "linkedin", "tema": "Tema", "texto": _TEXTO_LIMPIO, "con_imagen": False},
    )

    assert "ya no pasa el control de estilo" not in result.content
    assert uploader.calls, "el borrador reparado tiene que llegar al almacén"
    assert result.data["copy"] == _TEXTO_LIMPIO


async def test_run_veta_si_ni_la_reparacion_arregla_la_violacion(make_ctx):
    """Reparar no es perdonar: si el reparador tampoco lo limpia, el borrador se rechaza igual.

    (`crear_contenido_social` recibe un texto que el usuario dictó, así que aquí no hay rescate
    con aviso: lo correcto es devolverle exactamente qué corregir.)
    """
    texto_con_primera_persona = "Vimos que el texto reescrito por el auditor quedó distinto."
    llm = FakeLLM(
        [
            json.dumps({"publicable": True, "motivo": "bien"}),
            json.dumps({"publicable": True, "problemas": [], "texto": texto_con_primera_persona}),
            # El reparador devuelve algo que TAMBIÉN trae primera persona: no sirvió.
            "Nosotros seguimos viendo lo mismo en el texto.",
        ]
    )
    ctx = make_ctx()
    ctx.llm = llm
    uploader = UniqueUploader()
    tool = CrearContenidoSocialTool(uploader=uploader)

    result = await tool.run(
        ctx,
        {"plataforma": "linkedin", "tema": "Tema", "texto": _TEXTO_LIMPIO, "con_imagen": False},
    )

    assert "ya no pasa el control de estilo" in result.content
    assert uploader.calls == []


# ---------------------------------------------------------------------------
# Cooldown de `edecan_creative.agenda`: registro de historial tras un borrador exitoso.
# ---------------------------------------------------------------------------


async def test_run_registra_temas_detectados_en_el_estado_de_agenda(make_ctx, make_session):
    session = make_session()
    ctx = make_ctx(session=session)
    tool = CrearContenidoSocialTool(uploader=UniqueUploader())

    result = await tool.run(
        ctx,
        {
            "plataforma": "linkedin",
            "tema": "Créditos en México",
            "texto": _TEXTO_LIMPIO,
            "con_imagen": False,
        },
    )
    assert result.data["copy"] == _TEXTO_LIMPIO

    llamadas_agenda = [
        params for _, params in session.llamadas if params.get("key") == "agenda_estado"
    ]
    assert llamadas_agenda, "crear_contenido_social debe persistir el estado de agenda al terminar"
    estado_guardado = json.loads(llamadas_agenda[-1]["value"])
    assert estado_guardado["historial"][-1]["temas"] == ["mexico"]
    assert estado_guardado["historial"][-1]["tema"] == "Créditos en México"


# ---------------------------------------------------------------------------
# `save_editorial_profile` preserva claves internas (p. ej. `agenda_estado`) que no forman
# parte de `_EDITORIAL_FIELDS` -- regresión del fix necesario para que el estado de rotación
# sobreviva a un guardado normal del perfil editorial.
# ---------------------------------------------------------------------------


async def test_save_editorial_profile_preserva_claves_internas_ajenas(make_ctx, make_session):
    fila_existente = {
        "config": json.dumps({"voice": "Directo", "agenda_estado": {"territorio_idx": 3}}),
        "version": 1,
    }
    session = make_session([[fila_existente], []])
    ctx = make_ctx(session=session)

    await save_editorial_profile(ctx, "linkedin", {"voice": "Directo y cercano"})

    insert_params = session.llamadas[-1][1]
    config_guardado = json.loads(insert_params["config"])
    assert config_guardado["voice"] == "Directo y cercano"
    assert config_guardado["agenda_estado"] == {"territorio_idx": 3}


# ---------------------------------------------------------------------------
# `planificar_contenido_social`: rotación de territorio/formato + consulta con diversidad.
# ---------------------------------------------------------------------------


async def test_planificar_contenido_social_devuelve_territorio_formato_y_query(
    make_ctx, make_session
):
    session = make_session()
    ctx = make_ctx(session=session)
    tool = PlanificarContenidoSocialTool()

    result = await tool.run(ctx, {"plataforma": "linkedin"})

    assert result.data["territorio"]["id"]
    assert result.data["formato"]["id"]
    assert result.data["instruccion_formato"]
    assert result.data["query_sugerida"]
    assert result.data["temas_en_cooldown"] == []

    # Persiste el reloj YA avanzado (territorio/formato/foco), pero nunca toca el historial de
    # cooldown -- eso solo lo escribe `crear_contenido_social` sobre un borrador aceptado.
    llamada = next(p for _, p in session.llamadas if p.get("key") == "agenda_estado")
    estado_persistido = json.loads(llamada["value"])
    assert estado_persistido["territorio_idx"] == 1
    assert estado_persistido["formato_idx"] == 1
    assert estado_persistido["foco_idx"] == 1
    assert estado_persistido["historial"] == []


async def test_planificar_contenido_social_rechaza_plataforma_invalida(make_ctx):
    tool = PlanificarContenidoSocialTool()
    result = await tool.run(make_ctx(), {"plataforma": "tiktok"})
    assert "LinkedIn o X" in result.content


# ---------------------------------------------------------------------------
# `investigar_noticias_frescas`: envoltorio de tool sobre `investigacion.titulares_frescos`.
# ---------------------------------------------------------------------------


async def test_investigar_noticias_frescas_devuelve_fuentes_listas_para_crear_contenido(
    make_ctx, monkeypatch
):
    async def _fake_titulares_frescos(http, consulta, *, maximo=8, max_dias=3, **kwargs):
        return [
            Titular(
                titulo="Un banco mexicano ajusta tasas",
                snippet="Reforma · hace 3 h",
                url="https://example.com/nota-1",
                fuente="Reforma",
                fuente_url="https://reforma.com",
                publicado_en="2026-07-28T00:00:00+00:00",
                antiguedad_horas=3.0,
            )
        ]

    monkeypatch.setattr(social, "titulares_frescos", _fake_titulares_frescos)
    tool = InvestigarNoticiasFrescasTool()

    result = await tool.run(make_ctx(), {"consulta": "bancos méxico tasas"})

    assert result.data["fuentes"] == [
        {
            "title": "Un banco mexicano ajusta tasas",
            "url": "https://example.com/nota-1",
            "snippet": "Reforma · hace 3 h",
        }
    ]


async def test_investigar_noticias_frescas_sin_resultados_no_inventa_nada(make_ctx, monkeypatch):
    async def _sin_titulares(http, consulta, *, maximo=8, max_dias=3, **kwargs):
        return []

    monkeypatch.setattr(social, "titulares_frescos", _sin_titulares)
    tool = InvestigarNoticiasFrescasTool()

    result = await tool.run(make_ctx(), {"consulta": "tema sin cobertura reciente"})

    assert result.data["fuentes"] == []
    assert "no inventes" in result.content.lower()


async def test_investigar_noticias_frescas_sin_consulta_pide_una(make_ctx):
    tool = InvestigarNoticiasFrescasTool()
    result = await tool.run(make_ctx(), {"consulta": "   "})
    assert "Dime qué tema" in result.content
