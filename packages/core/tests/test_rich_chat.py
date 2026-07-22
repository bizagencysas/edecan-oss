from __future__ import annotations

from uuid import uuid4

from edecan_core.agent import artifact_refs_from_tool_data, rich_blocks_from_tool_data
from edecan_schemas import FlightCardBlock, LinkPreviewBlock, MediaBlock


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
