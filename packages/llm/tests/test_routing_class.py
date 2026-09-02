"""Unit tests for `edecan_llm.routing_class` (heuristic routing by class)."""

from __future__ import annotations

from edecan_llm.routing_class import RoutingClass, clasificar_tarea, elegir_modelo


def test_modalidad_embedding_voz_vision() -> None:
    assert clasificar_tarea("lo que sea", [], "embedding") is RoutingClass.EMBEDDING
    assert clasificar_tarea("llámame a Juan", [], "voz") is RoutingClass.VOICE
    assert clasificar_tarea("mira esta foto", [], "imagen") is RoutingClass.VISION


def test_computer_use_por_tool_o_por_texto() -> None:
    assert (
        clasificar_tarea("haz algo", ["usar_computadora"]) is RoutingClass.COMPUTER_USE
    )
    assert clasificar_tarea("abre Safari y busca precios") is RoutingClass.COMPUTER_USE


def test_codigo_por_keywords() -> None:
    assert clasificar_tarea("arregla el bug del endpoint de pagos") is RoutingClass.CODING


def test_codigo_por_tool_heavy() -> None:
    assert (
        clasificar_tarea("organiza mi semana", ["buscar", "leer", "enviar", "crear"])
        is RoutingClass.CODING
    )


def test_review_por_keywords() -> None:
    assert clasificar_tarea("revisa este contrato y evalúa las cláusulas") is RoutingClass.REVIEW


def test_reasoning_por_keywords() -> None:
    assert clasificar_tarea("analiza por qué falló la arquitectura") is RoutingClass.REASONING


def test_vision_por_texto() -> None:
    assert clasificar_tarea("qué dice esta imagen") is RoutingClass.VISION


def test_fast_por_texto_corto() -> None:
    assert clasificar_tarea("hola") is RoutingClass.FAST


def test_fast_por_intencion_de_formato() -> None:
    assert clasificar_tarea("resume este texto largo en una frase") is RoutingClass.FAST


def test_standard_por_defecto() -> None:
    assert (
        clasificar_tarea(
            "me gustaría que escribieras un texto largo y detallado describiendo "
            "cómo era la vida cotidiana en una ciudad costera durante el siglo pasado"
        )
        is RoutingClass.STANDARD
    )


def test_clasificacion_determinista() -> None:
    texto = "escribe una función de python que ordene una lista"
    primera = clasificar_tarea(texto)
    segunda = clasificar_tarea(texto)
    assert primera is segunda is RoutingClass.CODING


_REGISTRO_SINTETICO = [
    {"id": "@cf/meta/rapido", "capacidades": ["herramientas", "vision"], "orden": 1},
    {"id": "@cf/moonshotai/code", "capacidades": ["codigo", "razonamiento", "vision"], "orden": 2},
    {"id": "@cf/zai/razonador", "capacidades": ["razonamiento", "herramientas"], "orden": 3},
]


def test_elegir_modelo_coding_elige_tarjeta_con_codigo() -> None:
    assert elegir_modelo(RoutingClass.CODING, _REGISTRO_SINTETICO) == "@cf/moonshotai/code"


def test_elegir_modelo_vision_elige_tarjeta_con_vision() -> None:
    assert elegir_modelo(RoutingClass.VISION, _REGISTRO_SINTETICO) == "@cf/meta/rapido"


def test_elegir_modelo_reasoning_elige_primera_tarjeta_con_razonamiento() -> None:
    # La primera tarjeta que cubre "razonamiento" es la de código (la rápida no
    # lo declara); el orden del catálogo desempata, no se inventa un modelo.
    assert elegir_modelo(RoutingClass.REASONING, _REGISTRO_SINTETICO) == "@cf/moonshotai/code"


def test_elegir_modelo_fast_prefiere_menor_orden() -> None:
    assert elegir_modelo(RoutingClass.FAST, _REGISTRO_SINTETICO) == "@cf/meta/rapido"


def test_elegir_modelo_computer_use_exige_herramientas_y_vision() -> None:
    # Ninguna tarjeta cubre ambas -> cae al fallback (primera tarjeta).
    assert elegir_modelo(RoutingClass.COMPUTER_USE, _REGISTRO_SINTETICO) == "@cf/meta/rapido"


def test_elegir_modelo_registry_vacio_y_embedding_devuelven_none() -> None:
    assert elegir_modelo(RoutingClass.CODING, []) is None
    assert elegir_modelo(RoutingClass.EMBEDDING, _REGISTRO_SINTETICO) is None


def test_elegir_modelo_acepta_tarjetas_de_chat_con_ve_imagenes() -> None:
    chat = [
        {"id": "scout", "ve_imagenes": True, "soporta_esfuerzo": False, "orden": 1},
        {"id": "silva", "ve_imagenes": True, "soporta_esfuerzo": True, "orden": 2},
    ]
    assert elegir_modelo(RoutingClass.VISION, chat) == "scout"
    assert elegir_modelo(RoutingClass.REASONING, chat) == "silva"


def test_elegir_modelo_ignora_tarjetas_sin_id() -> None:
    tarjetas = [{"nombre": "sin id"}, {"id": "valida", "capacidades": ["codigo"]}]
    assert elegir_modelo(RoutingClass.CODING, tarjetas) == "valida"