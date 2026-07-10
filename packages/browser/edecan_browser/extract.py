"""Extracción de contenido legible desde HTML (`ROADMAP_V2.md` §7.7).

`extract_page(html, base_url)` usa BeautifulSoup para sacar título, texto
legible (sin `script`/`style`/`nav`/`footer`/`aside`), enlaces absolutos
(cap `_CAP_ENLACES`) y meta description. `render_markdown(...)` arma con eso
una salida markdown-ish con cap de caracteres — es lo que
`edecan_browser.tools` le muestra al modelo/usuario.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urljoin

from bs4 import BeautifulSoup

_TAGS_A_DESCARTAR = ("script", "style", "nav", "footer", "aside", "noscript")
_CAP_ENLACES = 40
_CAP_TEXTO_DEFECTO = 6000
_PREFIJOS_ENLACE_IGNORADOS = ("javascript:", "mailto:", "tel:")


@dataclass(frozen=True)
class ExtractedPage:
    """Contenido legible extraído de una página HTML."""

    titulo: str
    texto: str
    meta_description: str
    enlaces: list[str] = field(default_factory=list)


def extract_page(html: str, base_url: str) -> ExtractedPage:
    """Parsea `html` (recibido tal cual de `FetchedPage.html`) y extrae el
    contenido legible, resolviendo enlaces relativos contra `base_url`
    (normalmente `FetchedPage.url_final`, ya después de redirects).
    """
    soup = BeautifulSoup(html or "", "html.parser")

    for tag in soup(_TAGS_A_DESCARTAR):
        tag.decompose()

    titulo = soup.title.get_text(strip=True) if soup.title else ""

    meta = soup.find("meta", attrs={"name": "description"})
    meta_description = str(meta.get("content", "")).strip() if meta else ""

    texto = _texto_legible(soup)
    enlaces = _enlaces_absolutos(soup, base_url)

    return ExtractedPage(
        titulo=titulo, texto=texto, meta_description=meta_description, enlaces=enlaces
    )


def _texto_legible(soup: BeautifulSoup) -> str:
    # `soup.body` si existe: evita que texto de `<head>` (sobre todo
    # `<title>`, que se reporta aparte en `ExtractedPage.titulo`) se
    # duplique dentro del cuerpo legible.
    contenedor = soup.body or soup
    crudo = contenedor.get_text(separator="\n")
    lineas = [linea.strip() for linea in crudo.splitlines()]
    return "\n".join(linea for linea in lineas if linea)


def _enlaces_absolutos(soup: BeautifulSoup, base_url: str) -> list[str]:
    vistos: list[str] = []
    for a in soup.find_all("a", href=True):
        href = str(a["href"]).strip()
        if not href or href.startswith("#") or href.lower().startswith(_PREFIJOS_ENLACE_IGNORADOS):
            continue
        absoluto = urljoin(base_url, href)
        if absoluto not in vistos:
            vistos.append(absoluto)
        if len(vistos) >= _CAP_ENLACES:
            break
    return vistos


def render_markdown(page: ExtractedPage, *, max_chars: int = _CAP_TEXTO_DEFECTO) -> str:
    """Arma una salida markdown-ish (título, meta description, texto capado a
    `max_chars`, enlaces) — es el `content` que ven el modelo y el usuario.
    """
    partes = [f"# {page.titulo}" if page.titulo else "# (sin título)"]
    if page.meta_description:
        partes.append(f"> {page.meta_description}")

    texto = page.texto[:max_chars]
    if len(page.texto) > max_chars:
        texto += "\n\n[... contenido recortado ...]"
    if texto:
        partes.append(texto)

    if page.enlaces:
        partes.append("## Enlaces\n" + "\n".join(f"- {enlace}" for enlace in page.enlaces))

    return "\n\n".join(partes)
