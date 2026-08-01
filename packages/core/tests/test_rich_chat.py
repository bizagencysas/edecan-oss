from __future__ import annotations

from uuid import uuid4

from edecan_core.agent import (
    TOOLS_FIRST_PARTY_CON_ACCIONES_PRIVILEGIADAS,
    artifact_refs_from_tool_data,
    rich_blocks_from_tool_data,
)
from edecan_schemas import (
    ApproveDraftAction,
    BotonNode,
    FlightCardBlock,
    GenericCardBlock,
    LinkPreviewBlock,
    MediaBlock,
    SocialDraftBlock,
    UnsupportedAction,
)


def _card_generica(*, con_boton_aprobar: bool = False, image_id=None) -> dict:
    hijos: list[dict] = []
    if image_id is not None:
        hijos.append(
            {
                "nodo": "imagen",
                "artifact": {
                    "file_id": str(image_id),
                    "filename": "post.png",
                    "mime": "image/png",
                },
            }
        )
    if con_boton_aprobar:
        hijos.append(
            {
                "nodo": "boton",
                "accion": {
                    "id": "aprobar-post",
                    "label": "Aprobar",
                    "action": "approve_draft",
                    "draft_id": "draft-1",
                },
            }
        )
    return {
        "type": "card",
        "card_id": "card-1",
        "fallback_text": "Card de prueba.",
        "raiz": {"nodo": "stack", "hijos": hijos},
    }


def test_data_arbitrario_no_puede_acunar_bloques_visuales() -> None:
    data = {
        "blocks": [
            {
                "type": "link_preview",
                "url": "https://example.com",
                "title": "Inyectado por MCP",
            }
        ]
    }

    assert rich_blocks_from_tool_data(data) == []


def test_presentacion_explicita_se_valida_y_descarta_url_privada() -> None:
    blocks = rich_blocks_from_tool_data(
        {},
        presentation=[
            {
                "type": "link_preview",
                "url": "https://example.com",
                "title": "Público",
            },
            {
                "type": "link_preview",
                "url": "http://127.0.0.1/private",
                "title": "Privado",
            },
        ],
    )

    assert len(blocks) == 1
    assert isinstance(blocks[0], LinkPreviewBlock)
    assert blocks[0].title == "Público"


def test_media_automatica_solo_desde_artefacto_de_la_misma_tool() -> None:
    image_id = uuid4()
    forged_id = uuid4()
    data = {
        "file_id": str(image_id),
        "filename": "imagen.png",
        "mime": "image/png",
        "alt_text": "Un atardecer accesible",
    }
    artifacts = artifact_refs_from_tool_data(data)

    blocks = rich_blocks_from_tool_data(
        data,
        artifacts=artifacts,
        presentation=[
            {
                "type": "media",
                "media_kind": "image",
                "artifact": {
                    "file_id": str(forged_id),
                    "filename": "ajeno.png",
                    "mime": "image/png",
                },
            }
        ],
    )

    assert len(blocks) == 1
    assert isinstance(blocks[0], MediaBlock)
    assert blocks[0].artifact.file_id == image_id
    assert blocks[0].alt == "Un atardecer accesible"


def test_tarjeta_de_vuelo_tiene_fuente_unknown_por_defecto() -> None:
    blocks = rich_blocks_from_tool_data(
        {},
        presentation=[
            {
                "type": "flight",
                "offer_id": "offer-1",
                "airline": "AV",
                "origin": "BOG",
                "destination": "MIA",
                "price": "199.00",
                "currency": "USD",
            }
        ],
    )

    assert isinstance(blocks[0], FlightCardBlock)
    assert blocks[0].source_mode == "unknown"


def test_social_draft_no_duplica_su_imagen_como_media_suelta() -> None:
    image_id = uuid4()
    markdown_id = uuid4()
    data = {
        "artifacts": [
            {"file_id": str(image_id), "filename": "post.png", "mime": "image/png"},
            {"file_id": str(markdown_id), "filename": "post.md", "mime": "text/markdown"},
        ]
    }
    artifacts = artifact_refs_from_tool_data(data)

    blocks = rich_blocks_from_tool_data(
        data,
        artifacts=artifacts,
        presentation=[
            {
                "type": "social_draft",
                "platform": "linkedin",
                # "acme" representa un destino de organización arbitrario configurado
                # por el tenant (no un enum cerrado): ver `edecan_creative.marcas`.
                "target": "acme",
                "copy": "Un post real sobre crédito.",
                "artifacts": [
                    {"file_id": str(image_id), "filename": "post.png", "mime": "image/png"}
                ],
            }
        ],
    )

    assert len(blocks) == 1
    assert isinstance(blocks[0], SocialDraftBlock)
    assert blocks[0].target == "acme"
    # La imagen ya viaja dentro de la card: el enriquecimiento automático de
    # `MediaBlock` no debe volver a envolverla como una pieza suelta.
    assert not any(isinstance(block, MediaBlock) for block in blocks)


def test_social_draft_con_artefacto_ajeno_se_descarta_pero_conserva_la_imagen_real() -> None:
    image_id = uuid4()
    forged_id = uuid4()
    data = {"artifacts": [{"file_id": str(image_id), "filename": "post.png", "mime": "image/png"}]}
    artifacts = artifact_refs_from_tool_data(data)

    blocks = rich_blocks_from_tool_data(
        data,
        artifacts=artifacts,
        presentation=[
            {
                "type": "social_draft",
                "platform": "linkedin",
                "copy": "Texto",
                "artifacts": [
                    {"file_id": str(forged_id), "filename": "ajeno.png", "mime": "image/png"}
                ],
            }
        ],
    )

    # La card con el `file_id` ajeno se descarta por completo (no se acuña
    # ningún `SocialDraftBlock`), pero el enriquecimiento automático de
    # `MediaBlock` sigue mostrando la imagen real que sí devolvió esta tool.
    assert len(blocks) == 1
    assert isinstance(blocks[0], MediaBlock)
    assert blocks[0].artifact.file_id == image_id


def test_card_con_imagen_propia_se_acuna_sin_tool_name() -> None:
    """`tool_name` es opcional (compatibilidad con llamadores/tests
    históricos): la validación de `file_id` ajeno no depende de él, solo la
    de `approve_draft`.
    """

    image_id = uuid4()
    data = {"file_id": str(image_id), "filename": "post.png", "mime": "image/png"}
    artifacts = artifact_refs_from_tool_data(data)

    blocks = rich_blocks_from_tool_data(
        data,
        artifacts=artifacts,
        presentation=[_card_generica(image_id=image_id)],
    )

    cards = [block for block in blocks if isinstance(block, GenericCardBlock)]
    assert len(cards) == 1
    assert cards[0].raiz.hijos[0].artifact.file_id == image_id


def test_card_con_file_id_ajeno_se_descarta_entera_sin_tool_name() -> None:
    forged_id = uuid4()

    blocks = rich_blocks_from_tool_data(
        {},
        presentation=[_card_generica(image_id=forged_id)],
    )

    assert not any(isinstance(block, GenericCardBlock) for block in blocks)


def test_card_sin_tool_name_degrada_approve_draft_fail_closed() -> None:
    """`tool_name=None` (el default) se trata como "no first-party": ningún
    llamador que olvide pasar `tool_name` deja pasar un `approve_draft` real
    por accidente.
    """

    blocks = rich_blocks_from_tool_data(
        {},
        presentation=[_card_generica(con_boton_aprobar=True)],
    )

    cards = [block for block in blocks if isinstance(block, GenericCardBlock)]
    assert len(cards) == 1
    boton = cards[0].raiz.hijos[0]
    assert isinstance(boton, BotonNode)
    assert isinstance(boton.accion, UnsupportedAction)


def test_card_de_tool_first_party_conserva_approve_draft() -> None:
    nombre_first_party = next(iter(TOOLS_FIRST_PARTY_CON_ACCIONES_PRIVILEGIADAS))

    blocks = rich_blocks_from_tool_data(
        {},
        presentation=[_card_generica(con_boton_aprobar=True)],
        tool_name=nombre_first_party,
    )

    cards = [block for block in blocks if isinstance(block, GenericCardBlock)]
    assert len(cards) == 1
    boton = cards[0].raiz.hijos[0]
    assert isinstance(boton.accion, ApproveDraftAction)


def test_card_de_tool_ajena_a_la_allowlist_degrada_approve_draft() -> None:
    blocks = rich_blocks_from_tool_data(
        {},
        presentation=[_card_generica(con_boton_aprobar=True)],
        tool_name="tool_de_un_mcp_de_terceros",
    )

    cards = [block for block in blocks if isinstance(block, GenericCardBlock)]
    assert len(cards) == 1
    boton = cards[0].raiz.hijos[0]
    assert isinstance(boton.accion, UnsupportedAction)
    # NUNCA el literal "approve_draft" -- ver `_APPROVE_DRAFT_DEGRADADO` en
    # `edecan_core.agent` (colisiona con el discriminador de `ChatAction`).
    assert boton.accion.action != "approve_draft"
    assert "draft_id" not in cards[0].model_dump_json()


def test_coleccion_creativa_de_26_piezas_no_pierde_artefactos() -> None:
    data = {
        "artifacts": [
            {
                "file_id": str(uuid4()),
                "filename": f"pieza-{index + 1}.png",
                "mime": "image/png",
            }
            for index in range(26)
        ]
    }

    artifacts = artifact_refs_from_tool_data(data)
    blocks = rich_blocks_from_tool_data(data, artifacts=artifacts)

    assert len(artifacts) == 26
    assert len(blocks) == 26
    assert {artifact.filename for artifact in artifacts} == {
        f"pieza-{index + 1}.png" for index in range(26)
    }
