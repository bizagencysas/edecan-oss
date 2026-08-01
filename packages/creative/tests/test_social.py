from __future__ import annotations

import io
import json
from uuid import UUID, uuid4

from edecan_creative.social import CrearContenidoSocialTool, _split_x_thread
from edecan_schemas import ChatBlockAdapter, GenericCardBlock
from PIL import Image


class UniqueUploader:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, ctx, *, data: bytes, filename: str, mime: str):  # noqa: ANN001
        file_id = uuid4()
        self.calls.append(
            {"id": file_id, "ctx": ctx, "data": data, "filename": filename, "mime": mime}
        )
        return file_id, filename


class BrokenImageProvider:
    async def generate(self, prompt: str, size: str = "1024x1024") -> bytes:
        raise RuntimeError("upstream image provider unavailable")


async def test_social_package_creates_mobile_ready_artifacts_offline(make_ctx):
    uploader = UniqueUploader()
    tool = CrearContenidoSocialTool(uploader=uploader)

    result = await tool.run(
        make_ctx(),
        {
            "plataforma": "linkedin",
            "tema": "La IA local cambia el costo de trabajar",
            "texto": "Una computadora local puede convertirse en un equipo de trabajo.",
            "titular_visual": "Tu computadora también puede trabajar",
            "alt_text": "Tarjeta oscura sobre automatización local.",
            # Sin `hashtags` a propósito: LinkedIn los rechaza (ver el test de abajo).
        },
    )

    assert [call["mime"] for call in uploader.calls] == [
        "text/markdown",
        "application/json",
        "image/png",
    ]
    assert len(result.data["artifacts"]) == 3
    assert result.data["offline_visual"] is True
    assert len({artifact["file_id"] for artifact in result.data["artifacts"]}) == 3
    manifest = json.loads(uploader.calls[1]["data"])
    assert manifest["publication"]["requires_human_confirmation"] is True
    assert manifest["hashtags"] == []
    image = Image.open(io.BytesIO(uploader.calls[2]["data"]))
    assert image.size == (1080, 1350)  # vertical 4:5 de LinkedIn, ver PLATFORMS["linkedin"]


async def test_linkedin_rechaza_hashtags_sin_subir_nada(make_ctx):
    """LinkedIn no lleva hashtags (regla editorial portada): la tool rechaza y no sube nada."""
    uploader = UniqueUploader()
    tool = CrearContenidoSocialTool(uploader=uploader)

    result = await tool.run(
        make_ctx(),
        {
            "plataforma": "linkedin",
            "tema": "Una idea sin hashtags",
            "texto": "El copy se sostiene solo, sin etiquetas al final.",
            "hashtags": ["IA"],
        },
    )

    assert "hashtags" in str(result.content).lower()
    assert uploader.calls == []  # nada subido: falla antes de tocar S3


async def test_x_long_copy_becomes_numbered_thread(make_ctx):
    uploader = UniqueUploader()
    tool = CrearContenidoSocialTool(uploader=uploader)

    result = await tool.run(
        make_ctx(),
        {"plataforma": "x", "tema": "Hilo", "texto": "palabra " * 180, "con_imagen": False},
    )

    manifest = json.loads(uploader.calls[1]["data"])
    assert len(manifest["parts"]) > 1
    assert all(len(part) <= 280 for part in manifest["parts"])
    assert manifest["parts"][0].endswith(f"1/{len(manifest['parts'])}")
    assert len(result.data["artifacts"]) == 2


async def test_social_package_preserves_copy_when_image_provider_fails(make_ctx):
    uploader = UniqueUploader()
    tool = CrearContenidoSocialTool(
        uploader=uploader,
        image_provider=BrokenImageProvider(),
    )

    result = await tool.run(
        make_ctx(),
        {
            "plataforma": "linkedin",
            "tema": "Una idea que no debe perderse",
            "texto": "Este copy sigue siendo útil aunque el proveedor visual falle.",
            "titular_visual": "El trabajo se conserva",
        },
    )

    assert result.data["copy"].startswith("Este copy sigue")
    assert result.data["offline_visual"] is True
    assert "Conservé el post" in result.data["visual_warning"]
    assert [call["mime"] for call in uploader.calls] == [
        "text/markdown",
        "application/json",
        "image/png",
    ]
    image = Image.open(io.BytesIO(uploader.calls[2]["data"]))
    assert image.size == (1080, 1350)  # vertical 4:5 de LinkedIn, ver PLATFORMS["linkedin"]


async def test_non_x_copy_over_limit_is_rejected_without_upload(make_ctx):
    uploader = UniqueUploader()
    result = await CrearContenidoSocialTool(uploader=uploader).run(
        make_ctx(),
        {"plataforma": "threads", "tema": "Demasiado", "texto": "x" * 501},
    )

    assert "excede" in result.content
    assert uploader.calls == []


async def test_social_package_llena_presentation_con_destino_de_organizacion(make_ctx):
    uploader = UniqueUploader()
    tool = CrearContenidoSocialTool(uploader=uploader)

    result = await tool.run(
        make_ctx(),
        {
            "plataforma": "linkedin",
            "tema": "Crédito y confianza en Latinoamérica",
            "texto": "Un texto verdadero sobre crédito.",
            # "acme" es un id de destino de organización arbitrario configurado por el
            # tenant, no un enum cerrado -- ver `edecan_creative.marcas.BrandDestination`.
            "destino": "acme",
        },
    )

    assert result.presentation is not None
    assert len(result.presentation) == 1
    block = result.presentation[0]
    assert block["type"] == "social_draft"
    assert block["platform"] == "linkedin"
    assert block["target"] == "acme"
    assert block["copy"] == "Un texto verdadero sobre crédito."
    assert block["requires_human_confirmation"] is True
    assert {item["mime"] for item in block["artifacts"]} == {
        "text/markdown",
        "application/json",
        "image/png",
    }


async def test_social_package_target_none_sin_destino_conocido(make_ctx):
    uploader = UniqueUploader()
    tool = CrearContenidoSocialTool(uploader=uploader)

    result = await tool.run(
        make_ctx(),
        {"plataforma": "x", "tema": "Tema", "texto": "Un post corto.", "con_imagen": False},
    )

    assert result.presentation[0]["target"] is None
    assert result.presentation[0]["platform"] == "x"


async def test_social_package_acepta_cualquier_id_de_destino_bien_formado(make_ctx):
    # `destino` ya no es un enum cerrado a "personal"/"acme": cualquier id con la
    # forma correcta (ver `edecan_creative.marcas.DESTINATION_ID_PATTERN`) se acepta como
    # el destino de organización que el propio tenant haya configurado.
    uploader = UniqueUploader()
    tool = CrearContenidoSocialTool(uploader=uploader)

    result = await tool.run(
        make_ctx(),
        {
            "plataforma": "linkedin",
            "tema": "Tema",
            "texto": "Texto",
            "destino": "otra_cosa",
            "con_imagen": False,
        },
    )

    assert result.presentation[0]["target"] == "otra_cosa"


async def test_social_package_ignora_destino_con_forma_invalida(make_ctx):
    uploader = UniqueUploader()
    tool = CrearContenidoSocialTool(uploader=uploader)

    result = await tool.run(
        make_ctx(),
        {
            "plataforma": "linkedin",
            "tema": "Tema",
            "texto": "Texto",
            "destino": "Destino inválido!",
            "con_imagen": False,
        },
    )

    assert result.presentation[0]["target"] is None


def test_hilo_x_de_mas_de_cien_partes_respeta_limite_real() -> None:
    parts = _split_x_thread("palabra " * 8_000)

    assert len(parts) >= 100
    assert all(len(part) <= 280 for part in parts)
    assert parts[0].endswith(f"1/{len(parts)}")
    assert parts[-1].endswith(f"{len(parts)}/{len(parts)}")


# --------------------------------------------------------------------------
# Piloto backend-only de SDUI (paso 5 del plan): `EDECAN_SDUI_CARD_PILOTO`
# agrega una `GenericCardBlock` ADEMÁS de `social_draft`, nunca en su lugar.
# --------------------------------------------------------------------------


async def test_sin_env_var_el_piloto_no_agrega_ninguna_card(make_ctx):
    """Comportamiento por defecto: SIN la env var, `presentation` sigue
    trayendo únicamente el `social_draft` de siempre -- cero riesgo para el
    flujo de producción real mientras el piloto no se encienda a propósito."""
    uploader = UniqueUploader()
    tool = CrearContenidoSocialTool(uploader=uploader)

    result = await tool.run(
        make_ctx(),
        {"plataforma": "linkedin", "tema": "Crédito", "texto": "Un texto verdadero sobre crédito."},
    )

    assert [block["type"] for block in result.presentation] == ["social_draft"]


async def test_env_var_agrega_una_card_generica_junto_al_social_draft(make_ctx, monkeypatch):
    """Con la env var encendida, la MISMA tool first-party
    (`crear_contenido_social`) también acuña una `GenericCardBlock` -- la
    prueba de que una card nueva se define enteramente en el backend, sin
    tocar Swift. `social_draft` sigue presente e intacto."""
    monkeypatch.setenv("EDECAN_SDUI_CARD_PILOTO", "1")
    uploader = UniqueUploader()
    tool = CrearContenidoSocialTool(uploader=uploader)

    result = await tool.run(
        make_ctx(),
        {
            "plataforma": "linkedin",
            "tema": "Crédito y confianza",
            "texto": "Un texto verdadero sobre crédito y confianza.",
            "titular_visual": "El crédito construye confianza",
        },
    )

    tipos = [block["type"] for block in result.presentation]
    assert tipos == ["social_draft", "card"]

    social_draft, card = result.presentation
    image_file_id = next(
        item["file_id"] for item in social_draft["artifacts"] if item["mime"] == "image/png"
    )

    decoded = ChatBlockAdapter.validate_python(card)
    assert isinstance(decoded, GenericCardBlock)
    assert decoded.card_id.startswith("resumen-")
    assert decoded.fallback_text.startswith("Borrador de LinkedIn")

    hijos = decoded.raiz.hijos
    textos = [nodo for nodo in hijos if getattr(nodo, "nodo", None) == "texto"]
    imagenes = [nodo for nodo in hijos if getattr(nodo, "nodo", None) == "imagen"]
    badges = [nodo for nodo in hijos if getattr(nodo, "nodo", None) == "badge"]
    assert textos[0].contenido == "LinkedIn"
    assert textos[1].contenido == "El crédito construye confianza"
    assert len(imagenes) == 1
    assert imagenes[0].artifact.file_id == UUID(image_file_id)
    # `offline_visual=True` en este entorno de test (sin proveedor de
    # imágenes conectado, ver `StubImageProvider`): el badge de advertencia
    # se pinta, demostrando la primitiva `badge` además de `imagen`/`texto`.
    assert len(badges) == 1
    assert badges[0].tono == "advertencia"

    botones_planos = [
        boton
        for nodo in hijos
        if getattr(nodo, "nodo", None) == "stack"
        for boton in nodo.hijos
    ]
    acciones = {type(boton.accion).__name__ for boton in botones_planos}
    assert acciones == {"CopyTextAction", "SaveArtifactAction"}
    # `approve_draft` NO se ofrece aquí: hoy no existe un endpoint que
    # publique por `draft_id` (ver la desviación en `ChatView.swift`).
    assert "ApproveDraftAction" not in acciones


async def test_env_var_apagada_explicitamente_tampoco_agrega_card(make_ctx, monkeypatch):
    monkeypatch.setenv("EDECAN_SDUI_CARD_PILOTO", "0")
    uploader = UniqueUploader()
    tool = CrearContenidoSocialTool(uploader=uploader)

    result = await tool.run(
        make_ctx(),
        {"plataforma": "linkedin", "tema": "Crédito", "texto": "Un texto verdadero sobre crédito."},
    )

    assert [block["type"] for block in result.presentation] == ["social_draft"]
