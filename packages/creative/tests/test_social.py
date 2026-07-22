from __future__ import annotations

import io
import json
from uuid import uuid4

from edecan_creative.social import CrearContenidoSocialTool, _split_x_thread
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
            "hashtags": ["IA", "Productividad", "IA"],
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
    assert manifest["hashtags"] == ["IA", "Productividad"]
    image = Image.open(io.BytesIO(uploader.calls[2]["data"]))
    assert image.size == (1200, 627)


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


async def test_non_x_copy_over_limit_is_rejected_without_upload(make_ctx):
    uploader = UniqueUploader()
    result = await CrearContenidoSocialTool(uploader=uploader).run(
        make_ctx(),
        {"plataforma": "threads", "tema": "Demasiado", "texto": "x" * 501},
    )

    assert "excede" in result.content
    assert uploader.calls == []


def test_hilo_x_de_mas_de_cien_partes_respeta_limite_real() -> None:
    parts = _split_x_thread("palabra " * 8_000)

    assert len(parts) >= 100
    assert all(len(part) <= 280 for part in parts)
    assert parts[0].endswith(f"1/{len(parts)}")
    assert parts[-1].endswith(f"{len(parts)}/{len(parts)}")
