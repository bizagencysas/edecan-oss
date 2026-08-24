from __future__ import annotations

import pytest
from edecan_llm.base import CompletionRequest, ToolSpec
from edecan_llm.errors import LLMError
from edecan_llm.task_router import (
    ESFUERZO_CHAT_POR_DEFECTO,
    ESFUERZOS_CHAT,
    MODELOS_CHAT_FALLBACK,
    TaskKind,
    TaskRouter,
    modelo_chat_con_vision_por_defecto,
    modelo_chat_info,
    modelo_chat_permitido,
    modelo_chat_por_defecto,
    modelo_para_perfil,
    modelos_chat_disponibles,
)
from edecan_llm.workers_ai import MODELO_POR_DEFECTO


def test_default_is_fast_chat() -> None:
    """El chat resuelve al modelo del perfil `chat_rapido`, sea cual sea.

    Se compara contra `config/modelos.yml` en vez de contra un nombre escrito a mano: ese
    archivo es la autoridad y su propia regla `sin_nombres_de_modelo_en_el_codigo` pide no
    duplicarlos por ahí. Fijar la cadena obligaba a editar este test cada vez que se cambia
    de modelo -- y hacía fallar la suite por un cambio de DATO, no de comportamiento.
    """
    decision = TaskRouter().decide(CompletionRequest(model="", messages=[]), alias="rapido")
    assert decision.kind is TaskKind.CHAT
    assert decision.model == modelo_para_perfil("chat_rapido")


def test_voice_is_detected_from_channel_not_model_choice() -> None:
    request = CompletionRequest(model="ignored", metadata={"channel": "phone"})
    decision = TaskRouter().decide(request)
    assert decision.kind is TaskKind.VOICE


def test_tools_are_detected_automatically() -> None:
    request = CompletionRequest(
        model="",
        tools=[ToolSpec(name="buscar", description="Busca", input_schema={"type": "object"})],
    )
    assert TaskRouter().decide(request).kind is TaskKind.LIGHT_TOOL_CALL


def test_ide_cannot_leak_into_workers_ai_router() -> None:
    request = CompletionRequest(model="", metadata={"surface": "ide"})
    with pytest.raises(LLMError, match="IDE"):
        TaskRouter().decide(request)


# --------------------------------------------------------------------------- #
# Catálogo del selector del chat (`modelos_chat` de config/modelos.yml)
# --------------------------------------------------------------------------- #


def test_catalogo_de_chat_sale_del_yaml_y_respeta_el_orden_declarado() -> None:
    catalogo = modelos_chat_disponibles()

    assert [row["id"] for row in catalogo if row["principal"]] == [
        row["id"] for row in MODELOS_CHAT_FALLBACK if row["principal"]
    ]
    # Los cuatro de la portada van 1..4 y los secundarios detrás, siempre.
    principales = [row for row in catalogo if row["principal"]]
    assert [row["orden"] for row in principales] == [1, 2, 3, 4]
    assert all(row["ve_imagenes"] for row in principales)
    assert [row["id"] for row in catalogo if not row["principal"]] == []


def test_catalogo_de_chat_cae_al_fallback_sin_archivo(tmp_path) -> None:
    """Una instalación sin `config/modelos.yml` sigue teniendo selector."""

    catalogo = modelos_chat_disponibles(tmp_path / "no-existe.yml")

    assert [row["id"] for row in catalogo] == [row["id"] for row in MODELOS_CHAT_FALLBACK]


def test_catalogo_de_chat_ignora_filas_sin_id_y_normaliza_los_flags(tmp_path) -> None:
    ruta = tmp_path / "modelos.yml"
    ruta.write_text(
        "modelos_chat:\n"
        "  - nombre: 'Sin id'\n"
        "  - id: '  @cf/vendor/uno  '\n"
        "    nombre: 'Uno'\n",
        encoding="utf-8",
    )

    catalogo = modelos_chat_disponibles(ruta)

    assert [row["id"] for row in catalogo] == ["@cf/vendor/uno"]
    assert catalogo[0]["principal"] is False
    assert catalogo[0]["ve_imagenes"] is False
    assert catalogo[0]["soporta_esfuerzo"] is False
    assert catalogo[0]["descripcion"] == ""


def test_modelo_chat_permitido_acepta_el_catalogo_y_rechaza_lo_de_afuera() -> None:
    for row in modelos_chat_disponibles():
        assert modelo_chat_permitido(row["id"]) is True
    # `None` es "automático": siempre válido.
    assert modelo_chat_permitido(None) is True
    # Descartados con evidencia: glm-5.2 (42 s por vuelta del ciclo
    # agente-herramientas) y llama-3.2-11b-vision (ve, pero sin function
    # calling es inútil para un asistente con herramientas).
    assert modelo_chat_permitido("@cf/zai-org/glm-5.2") is False
    assert modelo_chat_permitido("@cf/meta/llama-3.3-70b-instruct-fp8-fast") is False
    assert modelo_chat_permitido("@cf/meta/llama-3.2-11b-vision-instruct") is False
    assert modelo_chat_permitido("") is False


def test_default_del_selector_coincide_con_el_default_del_chat() -> None:
    """"Automático" y el primer modelo del selector tienen que ser el mismo,
    o estrenar el selector cambiaría el comportamiento de conversaciones que
    nadie tocó."""

    assert modelo_chat_por_defecto() == modelo_para_perfil("chat_rapido")
    assert modelo_chat_por_defecto() == MODELO_POR_DEFECTO
    # El default con visión existe para degradar un turno con imagen; hoy es el
    # mismo, y ambos DEBEN ver imágenes.
    ficha = modelo_chat_info(modelo_chat_con_vision_por_defecto())
    assert ficha is not None and ficha["ve_imagenes"] is True


def test_esfuerzo_solo_en_los_que_razonan() -> None:
    """La fila Esfuerzo no se muestra para quien no razona: un control que no
    cambia nada es peor que no tenerlo."""

    con_esfuerzo = {
        row["id"] for row in modelos_chat_disponibles() if row["soporta_esfuerzo"]
    }
    assert modelo_chat_por_defecto() not in con_esfuerzo  # Scout no razona
    assert len(con_esfuerzo) == 3
    assert ESFUERZOS_CHAT == ("bajo", "medio", "alto")
    assert ESFUERZO_CHAT_POR_DEFECTO in ESFUERZOS_CHAT


# --------------------------------------------------------------------------- #
# La elección del usuario llega por metadata y la decide igual TaskRouter
# --------------------------------------------------------------------------- #


def test_modelo_elegido_valido_gana_sobre_la_heuristica() -> None:
    elegido = [row["id"] for row in modelos_chat_disponibles() if row["principal"]][-1]

    decision = TaskRouter().decide(
        CompletionRequest(model="", messages=[]),
        alias="rapido",
        metadata={"modelo_elegido": elegido},
    )

    assert decision.model == elegido
    assert decision.reason == "modelo elegido por el usuario para esta conversación"
    # El `kind` se sigue calculando igual: lo que cambia es de dónde sale el
    # modelo, no qué clase de trabajo es.
    assert decision.kind is TaskKind.CHAT


def test_modelo_elegido_conserva_el_kind_del_turno_con_herramientas() -> None:
    elegido = modelo_chat_por_defecto()
    request = CompletionRequest(
        model="",
        tools=[ToolSpec(name="buscar", description="Busca", input_schema={"type": "object"})],
    )

    decision = TaskRouter().decide(request, metadata={"modelo_elegido": elegido})

    assert decision.kind is TaskKind.LIGHT_TOOL_CALL
    assert decision.model == elegido


def test_modelo_elegido_fuera_de_catalogo_se_ignora_con_warning(caplog) -> None:
    with caplog.at_level("WARNING"):
        decision = TaskRouter().decide(
            CompletionRequest(model="", messages=[]),
            alias="rapido",
            metadata={"modelo_elegido": "@cf/zai-org/glm-5.2"},
        )

    assert decision.model == modelo_para_perfil("chat_rapido")
    assert decision.reason == "conversación normal"
    assert "fuera del catálogo" in caplog.text


def test_modelo_elegido_no_puede_colarse_en_el_perfil_de_ingenieria() -> None:
    """Forge tiene su propio runtime: el selector del chat no lo toca."""

    decision = TaskRouter().decide(
        alias="ingenieria_software",
        metadata={"modelo_elegido": modelo_chat_por_defecto()},
    )

    assert decision.kind is TaskKind.ENGINEERING
    assert decision.model == modelo_para_perfil("ingenieria_software")


def test_sin_metadata_la_decision_es_identica_a_la_de_siempre() -> None:
    request = CompletionRequest(model="", messages=[], metadata={"channel": "phone"})

    sin_kwarg = TaskRouter().decide(request)
    con_none = TaskRouter().decide(request, metadata=None)
    con_vacio = TaskRouter().decide(request, metadata={"modelo_elegido": None})

    assert sin_kwarg == con_none == con_vacio
    assert sin_kwarg.kind is TaskKind.VOICE
    assert sin_kwarg.model == modelo_para_perfil("voz_llamada")
    assert "baja latencia" in sin_kwarg.reason
