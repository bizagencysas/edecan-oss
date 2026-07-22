from __future__ import annotations

from edecan_design_studio.render import BrowserFirstRenderer
from PIL import Image


async def test_portable_renderer_produces_real_png_and_pdf(monkeypatch) -> None:
    renderer = BrowserFirstRenderer()

    async def unavailable(*args, **kwargs):
        raise RuntimeError("sin chromium")

    monkeypatch.setattr(renderer, "_render_chromium", unavailable)
    result = await renderer.render(
        "<!doctype html><html><head><title>Demo</title></head>"
        "<body><main><h1>Título</h1><p>Contenido real.</p></main></body></html>",
        width=640,
        height=480,
        include_png=True,
        include_pdf=True,
    )
    assert result.engine == "portable"
    assert result.png and result.png.startswith(b"\x89PNG\r\n\x1a\n")
    assert result.pdf and result.pdf.startswith(b"%PDF-")
    with Image.open(__import__("io").BytesIO(result.png)) as image:
        assert image.size == (640, 480)
