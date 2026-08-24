"""`construir_card_generica` (`edecan_core.cards`) -- helper del piloto
backend-only de SDUI (paso 5 del plan): construye una `GenericCardBlock` a
partir de datos simples, sin que el llamador conozca la forma de las 6
primitivas.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from edecan_core.cards import BadgeCard, BotonCard, LineaCuerpo, construir_card_generica
from edecan_schemas import (
    ArtifactRef,
    BadgeNode,
    BotonNode,
    ChatBlockAdapter,
    CopyTextAction,
    DivisorNode,
    GenericCardBlock,
    ImagenNode,
    SaveArtifactAction,
    StackNode,
    TextoNode,
)
from pydantic import ValidationError


def test_construir_card_generica_arma_el_arbol_completo_en_orden() -> None:
    file_id = uuid4()
    card = construir_card_generica(
        card_id="resumen-linkedin",
        fallback_text="Borrador de LinkedIn: el crédito en Venezuela",
        kicker="LinkedIn",
        titulo="El crédito en Venezuela",
        badge=BadgeCard(contenido="Listo", tono="exito"),
        imagen=ArtifactRef(file_id=file_id, filename="post.png", mime="image/png"),
        cuerpo=["Primera línea.", LineaCuerpo(texto="Segunda línea.", enfasis="fuerte")],
        botones=[
            BotonCard(
                accion=CopyTextAction(id="copiar", label="Copiar texto", text="el copy completo")
            ),
            BotonCard(
                estilo="secundario",
                accion=SaveArtifactAction(id="guardar", label="Guardar imagen", file_id=file_id),
            ),
        ],
    )

    assert isinstance(card, GenericCardBlock)
    assert card.card_id == "resumen-linkedin"
    assert card.type == "card"
    assert card.schema_version == 1

    raiz = card.raiz
    assert isinstance(raiz, StackNode)
    assert [type(hijo) for hijo in raiz.hijos] == [
        TextoNode,  # kicker
        TextoNode,  # titulo
        BadgeNode,
        ImagenNode,
        TextoNode,  # cuerpo[0]
        TextoNode,  # cuerpo[1]
        DivisorNode,
        StackNode,  # fila de botones
    ]

    kicker_node, titulo_node = raiz.hijos[0], raiz.hijos[1]
    assert kicker_node.rol == "etiqueta"
    assert kicker_node.contenido == "LinkedIn"
    assert titulo_node.rol == "titulo"
    assert titulo_node.enfasis == "fuerte"

    badge_node = raiz.hijos[2]
    assert badge_node.contenido == "Listo"
    assert badge_node.tono == "exito"

    imagen_node = raiz.hijos[3]
    assert imagen_node.artifact.file_id == file_id

    cuerpo_1, cuerpo_2 = raiz.hijos[4], raiz.hijos[5]
    assert cuerpo_1.contenido == "Primera línea."
    assert cuerpo_1.enfasis == "normal"
    assert cuerpo_2.contenido == "Segunda línea."
    assert cuerpo_2.enfasis == "fuerte"

    fila_botones = raiz.hijos[7]
    assert fila_botones.eje == "horizontal"
    assert len(fila_botones.hijos) == 2
    assert all(isinstance(hijo, BotonNode) for hijo in fila_botones.hijos)
    assert isinstance(fila_botones.hijos[0].accion, CopyTextAction)
    assert isinstance(fila_botones.hijos[1].accion, SaveArtifactAction)


def test_construir_card_generica_omite_nodos_opcionales_ausentes() -> None:
    card = construir_card_generica(
        card_id="card-minima",
        fallback_text="Solo un cuerpo.",
        cuerpo=["Único texto."],
    )

    assert [type(hijo) for hijo in card.raiz.hijos] == [TextoNode]
    assert card.raiz.hijos[0].contenido == "Único texto."


def test_construir_card_generica_rechaza_mas_de_cuatro_botones() -> None:
    botones = [
        BotonCard(accion=CopyTextAction(id=f"copiar-{i}", label="Copiar", text="x"))
        for i in range(5)
    ]

    with pytest.raises(ValidationError, match="no puede tener más de 4 botones"):
        construir_card_generica(
            card_id="card-con-exceso-de-botones",
            fallback_text="Demasiados botones.",
            botones=botones,
        )


def test_construir_card_generica_exige_fallback_text_no_vacio() -> None:
    with pytest.raises(ValidationError):
        construir_card_generica(card_id="card-sin-fallback", fallback_text="")


def test_construir_card_generica_sobrevive_el_viaje_por_el_contrato() -> None:
    """Round-trip: `model_dump(mode="json")` -> `ChatBlockAdapter.validate_python`
    debe devolver una `GenericCardBlock` equivalente -- el mismo criterio que
    exige el paso 3 del plan (fixtures doradas) para cualquier card real."""

    file_id = uuid4()
    card = construir_card_generica(
        card_id="card-round-trip",
        fallback_text="Card de prueba.",
        kicker="Prueba",
        titulo="Título",
        imagen=ArtifactRef(file_id=file_id, filename="a.png", mime="image/png"),
        cuerpo=["Cuerpo."],
        botones=[BotonCard(accion=CopyTextAction(id="c", label="Copiar", text="x"))],
    )

    payload = card.model_dump(mode="json")
    decoded = ChatBlockAdapter.validate_python(payload)

    assert isinstance(decoded, GenericCardBlock)
    assert decoded.model_dump(mode="json") == payload
