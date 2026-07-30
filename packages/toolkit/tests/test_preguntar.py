"""`preguntar_al_usuario`: el modal de opciones que evita que el agente adivine.

El bloque que emite se valida contra `ChatBlockAdapter` (el MISMO contrato que decodifican
web, iOS y Android), no contra un dict a mano: así un cambio en el esquema compartido rompe
este test en vez de romper el render en el teléfono.
"""

from __future__ import annotations

from edecan_schemas import ChatBlockAdapter
from edecan_toolkit import get_all_tools
from edecan_toolkit.preguntar import PreguntarAlUsuarioTool


def _tool() -> PreguntarAlUsuarioTool:
    return PreguntarAlUsuarioTool()


def test_la_tool_esta_registrada_en_el_catalogo() -> None:
    assert any(t.name == "preguntar_al_usuario" for t in get_all_tools())


async def test_emite_un_bloque_question_valido_contra_el_contrato_compartido() -> None:
    result = await _tool().run(
        None,
        {
            "pregunta": "¿A qué cuenta publico este post?",
            "encabezado": "Destino",
            "opciones": [
                {"etiqueta": "Personal", "descripcion": "Tu cuenta"},
                {"etiqueta": "La empresa", "descripcion": "La página de la organización"},
            ],
        },
    )

    assert result.presentation is not None and len(result.presentation) == 1
    bloque = ChatBlockAdapter.validate_python(result.presentation[0])
    assert bloque.type == "question"
    assert bloque.header == "Destino"
    assert [o.label for o in bloque.options] == ["Personal", "La empresa"]
    assert bloque.multi_select is False
    # Nunca se obliga a escoger entre opciones que podrían no incluir la respuesta real.
    assert bloque.allow_free_text is True


async def test_descarta_opciones_con_la_misma_etiqueta() -> None:
    # Dos botones con el mismo texto son indistinguibles al tocarlos: elegir no significaría
    # nada. Se pierde la repetida, no se muestra un modal ambiguo.
    result = await _tool().run(
        None,
        {
            "pregunta": "¿Cuál?",
            "opciones": [
                {"etiqueta": "Personal"},
                {"etiqueta": "personal"},
                {"etiqueta": "Empresa"},
            ],
        },
    )

    bloque = ChatBlockAdapter.validate_python(result.presentation[0])
    assert [o.label for o in bloque.options] == ["Personal", "Empresa"]


async def test_recorta_a_cuatro_opciones() -> None:
    result = await _tool().run(
        None,
        {
            "pregunta": "¿Cuál?",
            "opciones": [{"etiqueta": f"Opción {i}"} for i in range(9)],
        },
    )

    bloque = ChatBlockAdapter.validate_python(result.presentation[0])
    assert len(bloque.options) == 4


async def test_sin_dos_opciones_utiles_no_muestra_modal() -> None:
    result = await _tool().run(None, {"pregunta": "¿Sí?", "opciones": [{"etiqueta": "Una"}]})

    assert result.presentation is None
    assert "2 opciones" in result.content


async def test_sin_pregunta_no_muestra_modal() -> None:
    result = await _tool().run(
        None, {"pregunta": "   ", "opciones": [{"etiqueta": "A"}, {"etiqueta": "B"}]}
    )

    assert result.presentation is None


async def test_varias_activa_multi_select() -> None:
    result = await _tool().run(
        None,
        {
            "pregunta": "¿Cuáles?",
            "opciones": [{"etiqueta": "A"}, {"etiqueta": "B"}],
            "varias": True,
        },
    )

    bloque = ChatBlockAdapter.validate_python(result.presentation[0])
    assert bloque.multi_select is True


async def test_le_dice_al_modelo_que_termine_el_turno() -> None:
    # Si el modelo sigue escribiendo después de preguntar, suele contestarse solo la pregunta
    # que acaba de hacer -- exactamente lo que esta herramienta existe para evitar.
    result = await _tool().run(
        None, {"pregunta": "¿Cuál?", "opciones": [{"etiqueta": "A"}, {"etiqueta": "B"}]}
    )

    assert "Termina tu turno" in result.content
