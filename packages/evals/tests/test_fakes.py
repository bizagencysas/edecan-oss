"""Tests de `edecan_evals.fakes.FakeLLMProvider` — sin dependencias de
paquetes hermanos (solo `edecan_llm`, ya real)."""

from __future__ import annotations

from edecan_evals.fakes import FakeLLMProvider
from edecan_evals.schema import GuionEntry
from edecan_llm.base import ChatMessage, CompletionRequest


def _req(ultimo_mensaje: str | list[dict]) -> CompletionRequest:
    return CompletionRequest(
        model="fake", messages=[ChatMessage(role="user", content=ultimo_mensaje)]
    )


async def test_regex_matchea_produce_tool_call() -> None:
    provider = FakeLLMProvider({"hora": GuionEntry(tool="hora_actual", args={})})
    resp = await provider.complete(_req("¿qué hora es?"))
    assert resp.stop_reason == "tool_use"
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "hora_actual"


async def test_regex_matchea_produce_texto() -> None:
    provider = FakeLLMProvider({"hola": GuionEntry(texto="¡Hola! ¿En qué te ayudo?")})
    resp = await provider.complete(_req("hola, buenas"))
    assert resp.stop_reason == "end"
    assert resp.text == "¡Hola! ¿En qué te ayudo?"
    assert resp.tool_calls == []


async def test_sin_match_usa_respuesta_por_defecto() -> None:
    provider = FakeLLMProvider({"hora": GuionEntry(tool="hora_actual", args={})})
    resp = await provider.complete(_req("esto no matchea nada"))
    assert resp.stop_reason == "end"
    assert resp.text == "Entendido."


async def test_respuesta_por_defecto_personalizable() -> None:
    provider = FakeLLMProvider({}, respuesta_por_defecto=GuionEntry(texto="Otra cosa."))
    resp = await provider.complete(_req("cualquier cosa"))
    assert resp.text == "Otra cosa."


async def test_primer_match_gana_en_orden_de_definicion() -> None:
    guion = {
        "a": GuionEntry(texto="primero"),
        "abc": GuionEntry(texto="segundo"),
    }
    provider = FakeLLMProvider(guion)
    resp = await provider.complete(_req("abc"))
    assert resp.text == "primero"


async def test_match_es_case_insensitive() -> None:
    provider = FakeLLMProvider({"hora actual": GuionEntry(texto="son las 5")})
    resp = await provider.complete(_req("¿HORA ACTUAL, por favor?"))
    assert resp.text == "son las 5"


async def test_extrae_texto_de_mensaje_con_bloques() -> None:
    provider = FakeLLMProvider({"buscar": GuionEntry(texto="buscando...")})
    bloques = [{"type": "text", "text": "quiero buscar algo"}]
    resp = await provider.complete(_req(bloques))
    assert resp.text == "buscando..."


async def test_extrae_texto_de_tool_result() -> None:
    provider = FakeLLMProvider({"recordatorio creado": GuionEntry(texto="perfecto")})
    bloques = [
        {"type": "tool_result", "tool_use_id": "t1", "content": "recordatorio creado exitosamente"}
    ]
    resp = await provider.complete(_req(bloques))
    assert resp.text == "perfecto"


async def test_mensajes_vacios_no_falla() -> None:
    provider = FakeLLMProvider({"x": GuionEntry(texto="y")})
    req = CompletionRequest(model="fake", messages=[])
    resp = await provider.complete(req)
    assert resp.text == "Entendido."


async def test_registra_llamadas_recibidas() -> None:
    provider = FakeLLMProvider({})
    await provider.complete(_req("uno"))
    await provider.complete(_req("dos"))
    assert len(provider.llamadas) == 2


async def test_stream_tool_call() -> None:
    provider = FakeLLMProvider({"hora": GuionEntry(tool="hora_actual", args={"x": 1})})
    chunks = [chunk async for chunk in provider.stream(_req("qué hora es"))]
    tipos = [c.type for c in chunks]
    assert "tool_call" in tipos
    assert tipos[-1] == "stop"
    llamada = next(c.tool_call for c in chunks if c.type == "tool_call")
    assert llamada.name == "hora_actual"
    assert llamada.arguments == {"x": 1}


async def test_stream_texto() -> None:
    provider = FakeLLMProvider({"hola": GuionEntry(texto="qué tal")})
    chunks = [chunk async for chunk in provider.stream(_req("hola"))]
    tipos = [c.type for c in chunks]
    assert tipos == ["text", "usage", "stop"]
    assert next(c.text for c in chunks if c.type == "text") == "qué tal"
