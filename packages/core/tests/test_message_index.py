"""Contrato de `edecan_core.message_index` — búsqueda, auto-título,
segmentación de temas y tarjeta de resumen (PHASE2 §223-228).

Ver el docstring del módulo bajo prueba para el PORQUÉ de cada decisión
(tokenización sin tildes, índice invertido en memoria, stopwords solo en la
consulta, Jaccard para cortes de tema). Estos tests fijan el comportamiento
determinista y los casos borde (vacío, un solo mensaje, unicode, tildes).
"""

from __future__ import annotations

import pytest
from edecan_core.message_index import (
    clear_index,
    filter_messages,
    index_message,
    search_messages,
    segment_topics,
    suggest_title,
    summarize_conversation,
    title_changed_significantly,
)


@pytest.fixture(autouse=True)
def _indice_limpio() -> None:
    clear_index()
    yield
    clear_index()


# ---------------------------------------------------------------------------
# Búsqueda (§223)
# ---------------------------------------------------------------------------


def test_search_encuentra_por_solapamiento_de_tokens():
    index_message("m1", "el plan de migración de datos", {})
    index_message("m2", "la receta de pasta", {})
    assert search_messages("migración de datos") == ["m1"]


def test_search_ranquea_por_frecuencia_tf():
    index_message("m1", "api api api", {})
    index_message("m2", "api", {})
    assert search_messages("api") == ["m1", "m2"]


def test_search_empate_se_desempata_por_orden_de_indexado():
    index_message("a", "foo bar", {})
    index_message("b", "foo bar baz", {})
    assert search_messages("foo bar") == ["a", "b"]


def test_search_es_insensible_a_mayusculas_y_tildes():
    index_message("m1", "Música clásica", {})
    assert search_messages("musica") == ["m1"]
    assert search_messages("MÚSICA CLÁSICA") == ["m1"]


def test_search_ignora_stopwords_de_la_consulta():
    index_message("m1", "la api de pagos", {})
    assert search_messages("¿dónde hablamos de la api?") == ["m1"]


def test_search_solo_stopwords_devuelve_vacio():
    index_message("m1", "la api de pagos", {})
    assert search_messages("de la el") == []


def test_search_consulta_vacia_o_sin_coincidencias_devuelve_vacio():
    index_message("m1", "foo", {})
    assert search_messages("") == []
    assert search_messages("nada que ver") == []


def test_search_respeta_el_limit():
    for i in range(3):
        index_message(f"m{i}", "api", {})
    assert search_messages("api", limit=2) == ["m0", "m1"]
    assert search_messages("api", limit=0) == []


def test_indexar_dos_veces_reemplaza_no_acumula():
    index_message("m1", "foo", {})
    index_message("m1", "bar", {})
    assert search_messages("foo") == []
    assert search_messages("bar") == ["m1"]


# ---------------------------------------------------------------------------
# Filtros (§224)
# ---------------------------------------------------------------------------


def _poblar() -> None:
    index_message(
        "m1", "diseño del dashboard", {"project": "web", "date": "2026-08-10", "person": "ana"}
    )
    index_message(
        "m2", "migración de datos", {"project": "web", "date": "2026-08-20", "person": "luis"}
    )
    index_message("m3", "cierre del sprint", {"project": "móvil", "date": "2026-09-01"})


def test_filtro_por_proyecto():
    _poblar()
    assert filter_messages(["m1", "m2", "m3"], project="web") == ["m1", "m2"]


def test_filtro_por_rango_de_fecha_inclusivo():
    _poblar()
    assert filter_messages(
        ["m1", "m2", "m3"], date_from="2026-08-01", date_to="2026-08-31"
    ) == ["m1", "m2"]
    assert filter_messages(["m1", "m2", "m3"], date_from="2026-08-15") == ["m2", "m3"]


def test_filtro_por_persona():
    _poblar()
    assert filter_messages(["m1", "m2", "m3"], person="ana") == ["m1"]


def test_filtros_se_combinan_con_and():
    _poblar()
    assert filter_messages(["m1", "m2", "m3"], project="web", date_from="2026-08-15") == ["m2"]


def test_filtro_acepta_valores_lista_en_metadata():
    index_message("m1", "subí los adjuntos", {"file": ["reporte.pdf", "logo.png"]})
    index_message("m2", "subí el otro", {"file": "nota.txt"})
    assert filter_messages(["m1", "m2"], file="reporte.pdf") == ["m1"]
    assert filter_messages(["m1", "m2"], file="nota.txt") == ["m2"]


def test_filtro_tool_y_artifact():
    index_message("m1", "genera imagen", {"tool": "generar_imagen", "artifact": "portada.png"})
    index_message("m2", "publica", {"tool": "publicar_social"})
    assert filter_messages(["m1", "m2"], tool="generar_imagen") == ["m1"]
    assert filter_messages(["m1", "m2"], artifact="portada.png") == ["m1"]


def test_filtro_descarta_metadata_ausente_cuando_hay_filtro():
    _poblar()
    assert filter_messages(["m1", "m3"], person="ana") == ["m1"]


def test_filtro_preserva_el_orden_de_entrada():
    _poblar()
    assert filter_messages(["m3", "m1", "m2"], project="web") == ["m1", "m2"]


def test_sin_filtros_devuelve_todos():
    _poblar()
    assert filter_messages(["m3", "m1"]) == ["m3", "m1"]


# ---------------------------------------------------------------------------
# Auto-título (§227)
# ---------------------------------------------------------------------------


def test_titulo_capitaliza_la_primera_letra():
    assert suggest_title("vamos a diseñar el nuevo dashboard") == (
        "Vamos a diseñar el nuevo dashboard"
    )


def test_titulo_quita_interrogativos_del_inicio():
    assert suggest_title("¿cómo puedo migrar la base de datos?") == "Puedo migrar la base de datos"


def test_titulo_quita_verbos_de_orden():
    assert suggest_title("dime cómo arreglar el bug de auth") == "Arreglar el bug de auth"


def test_titulo_quita_por_que_como_frase():
    assert suggest_title("por qué falló el build de producción") == "Falló el build de producción"


def test_titulo_conserva_tildes_y_mayusculas_de_siglas():
    titulo = suggest_title("¿cómo está la implementación de la API?")
    assert titulo == "Está la implementación de la API"


def test_titulo_trunca_a_60_caracteres_sin_partir_palabras():
    largo = (
        "este es un mensaje que describe un tema con mucho detalle y nunca termina "
        "de explicar todo lo que pasa"
    )
    titulo = suggest_title(largo)
    assert 0 < len(titulo) <= 60
    assert titulo.startswith("Este es")
    assert not titulo.endswith(" ")


def test_titulo_vacio_o_solo_espacios_cae_a_conversacion():
    assert suggest_title("") == "Conversación"
    assert suggest_title("   ") == "Conversación"


def test_titulo_salta_saludo_inicial_corto():
    assert suggest_title("hola. Vamos a diseñar el nuevo dashboard") == (
        "Vamos a diseñar el nuevo dashboard"
    )


def test_title_changed_identicos_no_cambia():
    assert title_changed_significantly("Migración de datos", "Migración de datos") is False


def test_title_changed_sin_solapamiento_cambia():
    assert title_changed_significantly(
        "Diseño del dashboard", "Migración de la base de datos"
    ) is True


def test_title_changed_solapamiento_alto_no_cambia():
    assert title_changed_significantly(
        "Migración de la base de datos", "Migración de datos"
    ) is False


def test_title_changed_respeta_umbral_personalizado():
    assert title_changed_significantly(
        "Migración de la base de datos", "Migración de datos", threshold=0.7
    ) is True


def test_title_changed_vacio_contra_lleno_cambia():
    assert title_changed_significantly("", "algo") is True
    assert title_changed_significantly("algo", "") is True
    assert title_changed_significantly("", "") is False


# ---------------------------------------------------------------------------
# Segmentación de temas (§228)
# ---------------------------------------------------------------------------


def test_segmentar_lista_vacia():
    assert segment_topics([]) == []


def test_segmentar_un_solo_mensaje():
    assert segment_topics(["hola mundo"]) == [{"start": 0, "end": 0, "summary": "hola mundo"}]


def test_segmentar_mismo_tema_no_corta():
    mensajes = ["la api de pagos falla", "la api de pagos sigue fallando en producción"]
    assert segment_topics(mensajes) == [
        {"start": 0, "end": 1, "summary": "api pagos falla sigue fallando"}
    ]


def test_segmentar_cambio_de_tema_abre_nuevo_segmento():
    mensajes = ["la api de pagos falla", "mañana tengo cita con el dentista"]
    segmentos = segment_topics(mensajes)
    assert [s["start"] for s in segmentos] == [0, 1]
    assert [s["end"] for s in segmentos] == [0, 1]


def test_segmentar_summary_usa_palabras_clave_sin_stopwords():
    mensajes = ["el motor de búsqueda", "el motor de búsqueda es rápido"]
    assert segment_topics(mensajes)[0]["summary"] == "motor busqueda rapido"


def test_segmentar_umbral_alto_corta_mas():
    mensajes = ["la api de pagos falla", "la api de pagos sigue fallando en producción"]
    assert len(segment_topics(mensajes, threshold=0.99)) == 2


# ---------------------------------------------------------------------------
# Tarjeta de resumen (§226)
# ---------------------------------------------------------------------------


def test_resumen_detecta_decisiones():
    resumen = summarize_conversation(["Decidimos usar PostgreSQL", "vamos a crear un índice"])
    assert resumen["decisions"] == ["Decidimos usar PostgreSQL", "vamos a crear un índice"]


def test_resumen_detecta_pendientes():
    resumen = summarize_conversation(
        ["Queda pendiente el script de migración", "hay que revisar los logs"]
    )
    assert resumen["pending"] == [
        "Queda pendiente el script de migración",
        "hay que revisar los logs",
    ]


def test_resumen_detecta_archivos():
    resumen = summarize_conversation(
        ["Adjunto el archivo reporte.pdf", "el documento tiene el esquema"]
    )
    assert resumen["files"] == [
        "Adjunto el archivo reporte.pdf",
        "el documento tiene el esquema",
    ]


def test_resumen_es_insensible_a_tildes():
    resumen = summarize_conversation(["Decidí usar Postgres"])
    assert resumen["decisions"] == ["Decidí usar Postgres"]


def test_resumen_una_oracion_puede_caer_en_varias_categorias():
    resumen = summarize_conversation(["Decidimos que falta el archivo de migración"])
    assert "Decidimos que falta el archivo de migración" in resumen["decisions"]
    assert "Decidimos que falta el archivo de migración" in resumen["pending"]
    assert "Decidimos que falta el archivo de migración" in resumen["files"]


def test_resumen_sin_duplicados():
    resumen = summarize_conversation(["Decidimos usar PostgreSQL", "Decidimos usar PostgreSQL"])
    assert resumen["decisions"] == ["Decidimos usar PostgreSQL"]


def test_resumen_vacio_devuelve_listas_vacias():
    assert summarize_conversation([]) == {"decisions": [], "pending": [], "files": []}
    assert summarize_conversation(["nada relevante por aquí"]) == {
        "decisions": [],
        "pending": [],
        "files": [],
    }