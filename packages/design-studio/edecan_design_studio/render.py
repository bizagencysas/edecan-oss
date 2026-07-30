"""Render seguro HTML -> PNG/PDF con Chromium opcional y fallback portable."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import shutil
import signal
import subprocess
import tempfile
import textwrap
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Protocol, runtime_checkable

from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFont

from .models import RenderBundle

logger = logging.getLogger(__name__)

MIN_DIMENSION = 240
MAX_DIMENSION = 2400
_CHROMIUM_TIMEOUT_SECONDS = 25


def _find_chromium_executable(preferred: str | None = None) -> str | None:
    """Encuentra un Chromium local sin depender de Playwright ni descargar nada.

    ``preferred`` permite inyectar una ruta en instalaciones poco comunes.
    Después se consultan los nombres de PATH y las ubicaciones estándar de
    macOS/Windows. Linux normalmente resuelve por PATH.
    """

    candidates = [str(preferred).strip()] if preferred else []
    for name in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "chrome",
    ):
        if resolved := shutil.which(name):
            candidates.append(resolved)
    candidates.extend(
        [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            str(Path.home() / "Applications/Chromium.app/Contents/MacOS/Chromium"),
            str(Path.home() / "AppData/Local/Google/Chrome/Application/chrome.exe"),
            str(Path.home() / "AppData/Local/Chromium/Application/chrome.exe"),
            "C:/Program Files/Google/Chrome/Application/chrome.exe",
            "C:/Program Files/Chromium/Application/chrome.exe",
            "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
        ]
    )
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def _stop_browser(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=2)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        process.wait(timeout=2)


def _run_browser(command: list[str], output_path: Path) -> subprocess.CompletedProcess[bytes]:
    """Ejecuta Chrome y corta procesos residuales cuando el archivo ya quedó estable.

    Algunas versiones macOS de Chrome escriben correctamente ``--print-to-pdf``
    pero mantienen procesos auxiliares vivos. Esperar el timeout completo haría
    lento cada diseño; se acepta el resultado solo después de observar tamaño
    estable y las firmas se validan de nuevo al volver al renderer.
    """

    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=os.name == "posix",
    )
    deadline = time.monotonic() + _CHROMIUM_TIMEOUT_SECONDS
    last_size = -1
    stable_since: float | None = None
    try:
        while time.monotonic() < deadline:
            returncode = process.poll()
            if returncode is not None:
                stdout, stderr = process.communicate()
                return subprocess.CompletedProcess(command, returncode, stdout, stderr)
            try:
                size = output_path.stat().st_size
            except OSError:
                size = 0
            if size > 0 and size == last_size:
                stable_since = stable_since or time.monotonic()
                if time.monotonic() - stable_since >= 0.35:
                    _stop_browser(process)
                    stdout, stderr = process.communicate()
                    return subprocess.CompletedProcess(command, 0, stdout, stderr)
            else:
                last_size = size
                stable_since = None
            time.sleep(0.05)
        raise subprocess.TimeoutExpired(command, _CHROMIUM_TIMEOUT_SECONDS)
    finally:
        _stop_browser(process)


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

    Primero intenta Playwright. Si el módulo no está instalado pero Chrome o
    Chromium sí existen en el sistema, usa su CLI headless con un perfil
    efímero, resolución DNS anulada y la CSP ya insertada por el saneador. El
    fallback final conserva texto, dimensiones y un layout legible; no promete
    fidelidad CSS pixel-perfect y lo declara mediante ``engine``.
    """

    def __init__(self, *, chromium_path: str | None = None) -> None:
        self._chromium_path = chromium_path

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
            logger.warning("Falló el renderer Playwright", exc_info=True)

        try:
            return await self._render_chromium_cli(
                html,
                width=width,
                height=height,
                include_png=include_png,
                include_pdf=include_pdf,
            )
        except (FileNotFoundError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
            logger.info("Chrome/Chromium headless no disponible; usando renderer portable: %s", exc)
        except Exception:
            logger.warning("Falló Chrome/Chromium headless; usando fallback seguro", exc_info=True)

        png = _portable_png(html, width, height)
        pdf = _pdf_from_png(png, width, height) if include_pdf else None
        return RenderBundle(png=png if include_png else None, pdf=pdf, engine="portable")

    async def _render_chromium_cli(
        self,
        html: str,
        *,
        width: int,
        height: int,
        include_png: bool,
        include_pdf: bool,
    ) -> RenderBundle:
        executable = _find_chromium_executable(self._chromium_path)
        if executable is None:
            raise FileNotFoundError("No encontré Chrome/Chromium ejecutable")

        with tempfile.TemporaryDirectory(prefix="edecan-design-render-") as temp_name:
            temp = Path(temp_name)
            html_path = temp / "design.html"
            png_path = temp / "preview.png"
            pdf_path = temp / "export.pdf"
            # Asegura que imprimir preserve fondos y use las dimensiones del
            # artefacto, sin cambiar el layout de pantalla usado por PNG.
            print_style = (
                "<style id=\"edecan-print\">"
                f"@page{{size:{width}px {height}px;margin:0}}"
                "html,body{-webkit-print-color-adjust:exact;print-color-adjust:exact}"
                "</style>"
            )
            printable_html = html.replace("</head>", f"{print_style}</head>", 1)
            html_path.write_text(printable_html, encoding="utf-8")

            base_command = [
                executable,
                "--headless=new",
                "--disable-background-networking",
                "--disable-breakpad",
                "--disable-component-update",
                "--disable-default-apps",
                "--disable-extensions",
                "--disable-features=Translate,MediaRouter,OptimizationHints",
                "--disable-gpu",
                "--disable-sync",
                "--hide-scrollbars",
                "--host-resolver-rules=MAP * 0.0.0.0",
                "--metrics-recording-only",
                "--mute-audio",
                "--no-default-browser-check",
                "--no-first-run",
                "--no-proxy-server",
                "--run-all-compositor-stages-before-draw",
                f"--window-size={width},{height}",
            ]
            document_url = html_path.as_uri()

            if include_png:
                completed = await asyncio.to_thread(
                    _run_browser,
                    [
                        *base_command,
                        f"--user-data-dir={temp / 'profile-png'}",
                        f"--screenshot={png_path}",
                        document_url,
                    ],
                    png_path,
                )
                if completed.returncode != 0 or not png_path.is_file():
                    raise RuntimeError(
                        f"Chrome no produjo PNG (exit={completed.returncode})"
                    )

            if include_pdf:
                completed = await asyncio.to_thread(
                    _run_browser,
                    [
                        *base_command,
                        f"--user-data-dir={temp / 'profile-pdf'}",
                        f"--print-to-pdf={pdf_path}",
                        "--print-to-pdf-no-header",
                        document_url,
                    ],
                    pdf_path,
                )
                if completed.returncode != 0 or not pdf_path.is_file():
                    raise RuntimeError(
                        f"Chrome no produjo PDF (exit={completed.returncode})"
                    )

            png = png_path.read_bytes() if include_png else None
            pdf = pdf_path.read_bytes() if include_pdf else None
            if png is not None:
                if not png.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise RuntimeError("Chrome devolvió una vista previa PNG inválida")
                with Image.open(io.BytesIO(png)) as image:
                    if image.size != (width, height):
                        raise RuntimeError(
                            "Chrome devolvió una vista previa con dimensiones inesperadas"
                        )
            if pdf is not None and not pdf.startswith(b"%PDF-"):
                raise RuntimeError("Chrome devolvió un PDF inválido")
            return RenderBundle(png=png, pdf=pdf, engine="chrome-headless")

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
