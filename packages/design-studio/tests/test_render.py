from __future__ import annotations

import io

import pytest
from edecan_design_studio.render import BrowserFirstRenderer, _find_chromium_executable
from PIL import Image


async def test_portable_renderer_produces_real_png_and_pdf(monkeypatch) -> None:
    renderer = BrowserFirstRenderer()

    async def unavailable(*args, **kwargs):
        raise RuntimeError("sin chromium")

    monkeypatch.setattr(renderer, "_render_chromium", unavailable)
    monkeypatch.setattr(renderer, "_render_chromium_cli", unavailable)
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
    with Image.open(io.BytesIO(result.png)) as image:
        assert image.size == (640, 480)


async def test_renderer_usa_chrome_local_si_playwright_no_esta(monkeypatch) -> None:
    renderer = BrowserFirstRenderer()

    async def unavailable(*args, **kwargs):
        raise ImportError("playwright no instalado")

    async def chrome_cli(*args, **kwargs):
        from edecan_design_studio.models import RenderBundle

        return RenderBundle(
            png=b"\x89PNG\r\n\x1a\nchrome",
            pdf=b"%PDF-1.7\n%%EOF",
            engine="chrome-headless",
        )

    monkeypatch.setattr(renderer, "_render_chromium", unavailable)
    monkeypatch.setattr(renderer, "_render_chromium_cli", chrome_cli)

    result = await renderer.render(
        "<!doctype html><html><head></head><body><h1>CSS real</h1></body></html>",
        width=640,
        height=480,
        include_png=True,
        include_pdf=True,
    )

    assert result.engine == "chrome-headless"
    assert result.png and result.pdf


@pytest.mark.skipif(_find_chromium_executable() is None, reason="Chrome/Chromium no instalado")
async def test_chrome_headless_smoke_produce_png_pdf_reales() -> None:
    renderer = BrowserFirstRenderer()
    result = await renderer._render_chromium_cli(
        """<!doctype html><html><head><style>
        html,body{margin:0;width:100%;height:100%;background:#123456}
        h1{color:white;font:700 48px sans-serif;padding:40px}
        </style></head><body><h1>Vista fiel</h1></body></html>""",
        width=640,
        height=480,
        include_png=True,
        include_pdf=True,
    )

    assert result.engine == "chrome-headless"
    assert result.png and result.png.startswith(b"\x89PNG\r\n\x1a\n")
    assert result.pdf and result.pdf.startswith(b"%PDF-")
    with Image.open(io.BytesIO(result.png)) as image:
        assert image.size == (640, 480)
        assert image.getpixel((10, 10))[:3] == (0x12, 0x34, 0x56)
