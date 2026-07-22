"""Render seguro HTML -> PNG/PDF con Chromium opcional y fallback portable."""

from __future__ import annotations

import io
import logging
import textwrap
from html.parser import HTMLParser
from typing import Protocol, runtime_checkable

from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFont

from .models import RenderBundle

logger = logging.getLogger(__name__)

MIN_DIMENSION = 240
MAX_DIMENSION = 2400


@runtime_checkable
class DesignRenderer(Protocol):
    async def render(
        self,
        html: str,
        *,
        width: int,
        height: int,
        include_png: bool,
        include_pdf: bool,
    ) -> RenderBundle: ...


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: list[str] = []
        self.text: list[str] = []
        self._hidden = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"head", "style", "svg"}:
            self._hidden += 1
        if tag == "title":
            self._in_title = True
        if tag in {"br", "li", "p", "h1", "h2", "h3", "section", "div"} and not self._hidden:
            self.text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        if tag in {"head", "style", "svg"} and self._hidden:
            self._hidden -= 1

    def handle_data(self, data: str) -> None:
        clean = " ".join(data.split())
        if not clean:
            return
        if self._in_title:
            self.title.append(clean)
        elif not self._hidden:
            self.text.append(clean)


def validate_dimensions(width: int, height: int) -> tuple[int, int]:
    if not (MIN_DIMENSION <= width <= MAX_DIMENSION):
        raise ValueError(f"El ancho debe estar entre {MIN_DIMENSION} y {MAX_DIMENSION} px.")
    if not (MIN_DIMENSION <= height <= MAX_DIMENSION):
        raise ValueError(f"El alto debe estar entre {MIN_DIMENSION} y {MAX_DIMENSION} px.")
    return width, height


def _portable_png(html: str, width: int, height: int) -> bytes:
    parser = _VisibleTextParser()
    parser.feed(html)
    title = " ".join(parser.title).strip() or "Diseño"
    body = " ".join(" ".join(parser.text).split()).strip()

    image = Image.new("RGB", (width, height), "#f4f1ea")
    draw = ImageDraw.Draw(image)
    border = max(16, min(width, height) // 24)
    draw.rounded_rectangle(
        (border, border, width - border, height - border),
        radius=max(12, border // 2),
        fill="#ffffff",
        outline="#d9d4c8",
        width=max(1, border // 12),
    )
    title_font = ImageFont.load_default(size=max(20, min(54, width // 18)))
    body_font = ImageFont.load_default(size=max(13, min(24, width // 42)))
    x = border * 2
    y = border * 2
    max_chars = max(20, int((width - border * 4) / max(7, width // 110)))
    for line in textwrap.wrap(title, width=max_chars)[:4]:
        draw.text((x, y), line, font=title_font, fill="#18201d")
        y += int(title_font.size * 1.25)
    y += border // 2
    max_body_lines = max(1, int((height - y - border * 2) / (body_font.size * 1.45)))
    for line in textwrap.wrap(body, width=max_chars + 12)[:max_body_lines]:
        draw.text((x, y), line, font=body_font, fill="#34443e")
        y += int(body_font.size * 1.45)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _pdf_from_png(png: bytes, width: int, height: int) -> bytes:
    pdf = FPDF(unit="pt", format=(float(width), float(height)))
    pdf.set_auto_page_break(False)
    pdf.add_page()
    pdf.image(io.BytesIO(png), x=0, y=0, w=width, h=height)
    return bytes(pdf.output())


class BrowserFirstRenderer:
    """Usa Chromium si está instalado; si no, produce exports portables reales.

    Chromium corre con JavaScript deshabilitado y todas las solicitudes de red
    abortadas. El fallback conserva texto, dimensiones y un layout legible; no
    promete fidelidad CSS pixel-perfect y lo declara mediante ``engine``.
    """

    async def render(
        self,
        html: str,
        *,
        width: int,
        height: int,
        include_png: bool,
        include_pdf: bool,
    ) -> RenderBundle:
        width, height = validate_dimensions(width, height)
        if not include_png and not include_pdf:
            return RenderBundle(png=None, pdf=None, engine="none")
        try:
            return await self._render_chromium(
                html,
                width=width,
                height=height,
                include_png=include_png,
                include_pdf=include_pdf,
            )
        except (ImportError, RuntimeError, OSError) as exc:
            logger.info("Chromium no disponible; usando renderer portable: %s", exc)
        except Exception:
            logger.warning("Falló el renderer Chromium; usando fallback seguro", exc_info=True)

        png = _portable_png(html, width, height)
        pdf = _pdf_from_png(png, width, height) if include_pdf else None
        return RenderBundle(png=png if include_png else None, pdf=pdf, engine="portable")

    async def _render_chromium(
        self,
        html: str,
        *,
        width: int,
        height: int,
        include_png: bool,
        include_pdf: bool,
    ) -> RenderBundle:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # extra opcional, import deliberadamente diferido
            raise ImportError("Playwright no está instalado") from exc

        async with async_playwright() as playwright:
            try:
                browser = await playwright.chromium.launch(headless=True)
            except Exception as exc:
                raise RuntimeError("Chromium no está instalado para Playwright") from exc
            try:
                context = await browser.new_context(
                    java_script_enabled=False,
                    viewport={"width": width, "height": height},
                    device_scale_factor=1,
                )
                page = await context.new_page()

                async def block_network(route):
                    url = route.request.url
                    if url.startswith(("about:", "data:", "blob:")):
                        await route.continue_()
                    else:
                        await route.abort()

                await page.route("**/*", block_network)
                await page.set_content(html, wait_until="domcontentloaded", timeout=10_000)
                png = (
                    await page.screenshot(type="png", full_page=False, timeout=10_000)
                    if include_png
                    else None
                )
                pdf = (
                    await page.pdf(
                        width=f"{width}px",
                        height=f"{height}px",
                        print_background=True,
                        prefer_css_page_size=False,
                    )
                    if include_pdf
                    else None
                )
                await context.close()
                return RenderBundle(
                    png=bytes(png) if png else None,
                    pdf=bytes(pdf) if pdf else None,
                    engine="chromium",
                )
            finally:
                await browser.close()
