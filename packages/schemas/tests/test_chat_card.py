"""Contrato de `GenericCardBlock` (MVP de Server-Driven UI del chat).

Tres cosas que este archivo tiene que probar, porque son la razón de ser del
diseño (ver `/private/tmp/sdui_plan_mvp.json`):

1. Una card realista, con las 6 primitivas, decodifica y viaja entera por
   JSON (round-trip estable).
2. Los límites duros (profundidad ≤ 4, ≤ 40 nodos, ≤ 4 botones,
   `fallback_text` obligatorio) SÍ rechazan -- son DoS visual, no estética.
3. Un nodo o una acción con discriminador desconocido (versión futura del
   contrato) es el caso TOLERANTE: se decodifica sin tumbar la card ni a sus
   hermanos -- es justo lo que hoy NO existe (hoy el fallback es por bloque
   entero) y lo que hace posible que un backend más nuevo que un cliente
   viejo no le rompa el chat.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from edecan_schemas.chat import (
    BadgeNode,
    BotonNode,
    ChatBlockAdapter,
    DivisorNode,
    GenericCardBlock,
    ImagenNode,
    NodoDesconocido,
    StackNode,
    TextoNode,
    UnsupportedAction,
)
from pydantic import ValidationError

# Directorio de fixtures DORADAS (paso 3 del plan): estas mismas fixtures se
# copian tal cual a los tests de iOS/web/Android -- si una plataforma se
# desvía de este contrato, su test dorado truena. No editar sin actualizar
# las 4 plataformas a la vez.
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "chat_blocks"


def _cargar_fixture(nombre: str) -> dict:
    return json.loads((FIXTURES_DIR / nombre).read_text(encoding="utf-8"))


def _card_linkedin() -> dict:
    """La card LinkedIn del plan expresada en primitivas."""

    return {
        "type": "card",
        "card_id": "linkedin-draft-1",
        "fallback_text": "Post de LinkedIn listo para revisar. Aprobar o copiar el texto.",
        "raiz": {
            "nodo": "stack",
            "eje": "vertical",
            "espaciado": "m",
            "relleno": "tarjeta",
            "fondo": "acento_suave",
            "hijos": [
                {
                    "nodo": "stack",
                    "eje": "horizontal",
                    "alineacion": "centro",
                    "hijos": [
                        {
                            "nodo": "texto",
                            "contenido": "Post de LinkedIn listo para revisar",
                            "rol": "titulo",
                            "enfasis": "fuerte",
                            "max_lineas": 2,
                        },
                        {"nodo": "badge", "contenido": "En vivo", "tono": "exito"},
                    ],
                },
                {
                    "nodo": "imagen",
                    "artifact": {
                        "file_id": str(uuid4()),
                        "filename": "post_organization.png",
                        "mime": "image/png",
                    },
                    "alt": "Gráfico sobre crédito en Venezuela",
                    "aspecto": "panoramica",
                    "esquinas": "m",
                },
                {"nodo": "divisor"},
                {
                    "nodo": "stack",
                    "eje": "horizontal",
                    "espaciado": "s",
                    "hijos": [
                        {
                            "nodo": "boton",
                            "estilo": "secundario",
                            "accion": {
                                "id": "post.copiar",
                                "label": "Copiar",
                                "action": "copy_text",
                                "text": "Un post real sobre crédito en Venezuela.",
                            },
                        },
                        {
                            "nodo": "boton",
                            "estilo": "primario",
                            "accion": {
                                "id": "post.aprobar",
                                "label": "Aprobar",
                                "action": "approve_draft",
                                "draft_id": "draft-123",
                            },
                        },
                    ],
                },
            ],
        },
    }


# ---------------------------------------------------------------------------
# La card válida decodifica y sobrevive el viaje por JSON
# ---------------------------------------------------------------------------


def test_card_linkedin_decodifica_con_las_6_primitivas():
    block = ChatBlockAdapter.validate_python(_card_linkedin())

    assert isinstance(block, GenericCardBlock)
    assert block.type == "card"
    assert block.card_id == "linkedin-draft-1"
    assert isinstance(block.raiz, StackNode)

    encabezado, imagen, divisor, botones = block.raiz.hijos
    assert isinstance(encabezado, StackNode)
    titulo, badge = encabezado.hijos
    assert isinstance(titulo, TextoNode) and titulo.rol == "titulo"
    assert isinstance(badge, BadgeNode) and badge.tono == "exito"
    assert isinstance(imagen, ImagenNode)
    assert isinstance(divisor, DivisorNode)
    assert isinstance(botones, StackNode)
    copiar, aprobar = botones.hijos
    assert isinstance(copiar, BotonNode) and copiar.accion.action == "copy_text"
    assert isinstance(aprobar, BotonNode) and aprobar.accion.action == "approve_draft"


def test_card_linkedin_viaja_entera_por_json_y_vuelve_igual():
    original = ChatBlockAdapter.validate_python(_card_linkedin())

    ida = json.dumps(ChatBlockAdapter.dump_python(original, mode="json"))
    vuelta = ChatBlockAdapter.validate_python(json.loads(ida))

    assert vuelta == original


# ---------------------------------------------------------------------------
# Límites duros: DoS visual, no estética -- se rechazan en Pydantic
# ---------------------------------------------------------------------------


def _stack_anidado(niveles: int) -> dict:
    """Un stack anidado `niveles` veces (contando el raíz). `niveles=1` es
    solo el stack raíz sin hijos."""

    nodo = {"nodo": "stack", "hijos": []}
    actual = nodo
    for _ in range(niveles - 1):
        hijo = {"nodo": "stack", "hijos": []}
        actual["hijos"] = [hijo]
        actual = hijo
    return nodo


def _card_base(raiz: dict, **overrides) -> dict:
    base = {
        "type": "card",
        "card_id": "card-test",
        "fallback_text": "Contenido de prueba.",
        "raiz": raiz,
    }
    base.update(overrides)
    return base


def test_card_rechaza_profundidad_5():
    with pytest.raises(ValidationError):
        ChatBlockAdapter.validate_python(_card_base(_stack_anidado(5)))


def test_card_acepta_profundidad_4_en_el_limite():
    block = ChatBlockAdapter.validate_python(_card_base(_stack_anidado(4)))
    assert isinstance(block, GenericCardBlock)


def test_card_rechaza_41_nodos():
    raiz = {
        "nodo": "stack",
        "hijos": [{"nodo": "texto", "contenido": f"línea {i}"} for i in range(40)],
    }
    # 1 (raíz) + 40 hijos = 41 nodos totales.
    with pytest.raises(ValidationError):
        ChatBlockAdapter.validate_python(_card_base(raiz))


def test_card_acepta_40_nodos_en_el_limite():
    raiz = {
        "nodo": "stack",
        "hijos": [{"nodo": "texto", "contenido": f"línea {i}"} for i in range(39)],
    }
    # 1 (raíz) + 39 hijos = 40 nodos totales.
    block = ChatBlockAdapter.validate_python(_card_base(raiz))
    assert isinstance(block, GenericCardBlock)


def _boton_copiar(indice: int) -> dict:
    return {
        "nodo": "boton",
        "accion": {
            "id": f"boton.{indice}",
            "label": f"Botón {indice}",
            "action": "copy_text",
            "text": f"texto {indice}",
        },
    }


def test_card_rechaza_5_botones():
    raiz = {"nodo": "stack", "hijos": [_boton_copiar(i) for i in range(5)]}
    with pytest.raises(ValidationError):
        ChatBlockAdapter.validate_python(_card_base(raiz))


def test_card_acepta_4_botones_en_el_limite():
    raiz = {"nodo": "stack", "hijos": [_boton_copiar(i) for i in range(4)]}
    block = ChatBlockAdapter.validate_python(_card_base(raiz))
    assert isinstance(block, GenericCardBlock)


def test_card_sin_fallback_text_es_rechazada():
    payload = _card_base({"nodo": "stack", "hijos": []})
    del payload["fallback_text"]
    with pytest.raises(ValidationError):
        ChatBlockAdapter.validate_python(payload)


def test_card_con_fallback_text_vacio_es_rechazada():
    with pytest.raises(ValidationError):
        ChatBlockAdapter.validate_python(
            _card_base({"nodo": "stack", "hijos": []}, fallback_text="")
        )


# ---------------------------------------------------------------------------
# Forward-compat: discriminador desconocido es tolerante, no tumba la card
# ---------------------------------------------------------------------------


def test_nodo_desconocido_se_decodifica_sin_tumbar_a_sus_hermanos():
    raiz = {
        "nodo": "stack",
        "hijos": [
            {"nodo": "texto", "contenido": "antes"},
            {"nodo": "holograma", "algo_futuro": True},
            {"nodo": "texto", "contenido": "después"},
        ],
    }
    block = ChatBlockAdapter.validate_python(_card_base(raiz))

    assert isinstance(block, GenericCardBlock)
    antes, desconocido, despues = block.raiz.hijos
    assert isinstance(antes, TextoNode) and antes.contenido == "antes"
    assert isinstance(desconocido, NodoDesconocido)
    assert desconocido.nodo == "holograma"
    assert isinstance(despues, TextoNode) and despues.contenido == "después"


def test_accion_de_boton_desconocida_se_decodifica_sin_tumbar_la_card():
    raiz = {
        "nodo": "stack",
        "hijos": [
            {
                "nodo": "boton",
                "estilo": "primario",
                "accion": {
                    "id": "futuro.1",
                    "label": "Lanzar",
                    "action": "lanzar_cohete",
                    "launch_pad": "39A",
                },
            }
        ],
    }
    block = ChatBlockAdapter.validate_python(_card_base(raiz))

    assert isinstance(block, GenericCardBlock)
    (boton,) = block.raiz.hijos
    assert isinstance(boton, BotonNode)
    assert isinstance(boton.accion, UnsupportedAction)
    assert boton.accion.action == "lanzar_cohete"


def test_bloque_tipo_desconocido_sigue_fallando_a_nivel_de_blocktype():
    # Nivel 3 de forward-compat: un TYPE de bloque que no existe (a diferencia
    # de un NODO dentro de una card) sigue siendo all-or-nothing -- se
    # resuelve mandando la próxima UI nueva como `card`, no como otro `type`.
    with pytest.raises(ValidationError):
        ChatBlockAdapter.validate_python({"type": "holograma_block", "fallback_text": "x"})


# ---------------------------------------------------------------------------
# Paso 3: fixtures DORADAS -- el mismo JSON que cargarán iOS/web/Android.
# Congelan el contrato en bytes, no solo en dicts construidos en Python, para
# que un drift entre plataformas truene aquí primero.
# ---------------------------------------------------------------------------


def test_fixture_card_linkedin_decodifica_con_las_6_primitivas():
    payload = _cargar_fixture("card_linkedin.json")
    block = ChatBlockAdapter.validate_python(payload)

    assert isinstance(block, GenericCardBlock)
    assert block.type == "card"
    assert block.card_id == "linkedin-draft-1"
    assert isinstance(block.raiz, StackNode)

    encabezado, imagen, divisor, botones = block.raiz.hijos
    assert isinstance(encabezado, StackNode)
    titulo, badge = encabezado.hijos
    assert isinstance(titulo, TextoNode) and titulo.rol == "titulo"
    assert isinstance(badge, BadgeNode) and badge.tono == "exito"
    assert isinstance(imagen, ImagenNode)
    assert str(imagen.artifact.file_id) == "6f6b7f9a-6b0e-4b1e-9f7f-8f9a6b7f9a6b"
    assert isinstance(divisor, DivisorNode)
    assert isinstance(botones, StackNode)
    copiar, aprobar = botones.hijos
    assert isinstance(copiar, BotonNode) and copiar.accion.action == "copy_text"
    assert isinstance(aprobar, BotonNode) and aprobar.accion.action == "approve_draft"


def test_fixture_card_linkedin_round_trip_json_estable():
    # La fixture es el contrato -- si el round-trip cambia el dict, algo del
    # modelo se movió sin que el JSON congelado se haya actualizado a la vez.
    payload = _cargar_fixture("card_linkedin.json")
    original = ChatBlockAdapter.validate_python(payload)

    ida = json.dumps(ChatBlockAdapter.dump_python(original, mode="json"))
    vuelta = ChatBlockAdapter.validate_python(json.loads(ida))

    assert vuelta == original


def test_fixture_card_nodo_desconocido_decodifica_y_conserva_los_nodos_conocidos():
    payload = _cargar_fixture("card_nodo_desconocido.json")
    block = ChatBlockAdapter.validate_python(payload)

    assert isinstance(block, GenericCardBlock)
    antes, holograma, divisor, despues, boton = block.raiz.hijos
    assert isinstance(antes, TextoNode) and antes.contenido == "Antes del nodo futuro"
    # El nodo `holograma` no existe en el vocabulario -- decodifica como el
    # caso tolerante y el cliente lo salta al pintar, sin tumbar la card.
    assert isinstance(holograma, NodoDesconocido)
    assert holograma.nodo == "holograma"
    assert isinstance(divisor, DivisorNode)
    assert isinstance(despues, TextoNode) and despues.contenido == "Después del nodo futuro"
    assert isinstance(boton, BotonNode)
    # La acción `lanzar_cohete` tampoco existe -- mismo caso tolerante, a
    # nivel de acción en vez de a nivel de nodo.
    assert isinstance(boton.accion, UnsupportedAction)
    assert boton.accion.action == "lanzar_cohete"


def test_fixture_card_version_futura_no_decodifica_en_este_backend_a_proposito():
    # Nivel 3 de forward-compat, pero visto desde el lado del EMISOR: este
    # paquete de esquemas es el ÚNICO emisor real y fija `schema_version` en
    # Literal[1] a propósito (nunca sube en un cambio aditivo, ver la
    # gobernanza documentada en chat.py). Este fixture representa lo que un
    # SERVIDOR más nuevo mandaría a un CLIENTE más viejo -- el gate que debe
    # tolerarlo vive en cada cliente (p.ej. iOS:
    # `schemaVersion > versionMaximaSoportada -> unsupported`), no en este
    # backend. Por eso aquí el contrato es "no valida" y lo que sí probamos
    # es que el fixture trae un `fallback_text` útil por sí solo -- la red
    # final para cualquier cliente (incluido este backend, si algún día
    # tuviera que interpretar un payload ajeno) que no reconozca la versión.
    payload = _cargar_fixture("card_version_futura.json")
    assert payload["schema_version"] == 99

    with pytest.raises(ValidationError):
        ChatBlockAdapter.validate_python(payload)

    assert payload["fallback_text"]
