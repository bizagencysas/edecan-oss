from __future__ import annotations

from edecan_core.freshness import (
    assess_freshness,
    grounding_queries,
    grounding_query,
    official_source_domains,
)


def test_modelos_de_ia_recientes_exigen_comprobacion() -> None:
    decision = assess_freshness("¿Cuál es la diferencia entre Luna, Terra y Sol de ChatGPT?")

    assert decision.required is True
    assert decision.reason == "el tema cambia con frecuencia"


def test_actualidad_legal_financiera_y_de_viajes_exige_comprobacion() -> None:
    assert assess_freshness("¿Cuál es la ley vigente para esto?").required is True
    assert assess_freshness("¿Cómo está el precio de esta acción?").required is True
    assert assess_freshness("¿Qué vuelos están disponibles ahora?").required is True


def test_conocimiento_estable_y_creacion_no_disparan_busqueda() -> None:
    assert assess_freshness("¿Qué es la fotosíntesis?").required is False
    assert assess_freshness("Escribe un poema sobre la luna.").required is False
    assert assess_freshness("Ayúdame a organizar mis ideas.").required is False


def test_grounding_query_pide_fuentes_primarias_y_fecha() -> None:
    query = grounding_query("Modelos de IA", language="es", date_iso="2026-07-22")

    assert "oficial vigente" in query
    assert "2026" in query


def test_grounding_prioriza_proveedor_sin_hardcodear_modelos() -> None:
    queries = grounding_queries(
        "¿Cuál es la diferencia entre Luna, Terra y Sol de ChatGPT?",
        language="es",
        date_iso="2026-07-22",
    )

    assert queries[0] == "OpenAI Luna Terra Sol ChatGPT official"
    assert queries[1] == "Luna Terra Sol ChatGPT oficial vigente 2026"
    assert official_source_domains("novedades de ChatGPT") == ("openai.com", "chatgpt.com")


def test_grounding_es_independiente_del_proveedor() -> None:
    assert grounding_queries(
        "¿Cuál es el modelo más reciente de Claude?",
        language="es",
        date_iso="2026-07-22",
    )[0].startswith("Anthropic ")
    assert official_source_domains("Cambios recientes de Gemini") == (
        "ai.google.dev",
        "deepmind.google",
        "cloud.google.com",
        "blog.google",
    )
