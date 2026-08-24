"""Extracción y sanitización estricta para previews HTML sin scripts ni red."""

from __future__ import annotations

import html as html_lib
import re
from html.parser import HTMLParser

MAX_HTML_BYTES = 500_000

_ALLOWED_TAGS = frozenset(
    {
        "a",
        "article",
        "aside",
        "b",
        "blockquote",
        "body",
        "br",
        "button",
        "caption",
        "circle",
        "code",
        "col",
        "colgroup",
        "dd",
        "defs",
        "details",
        "div",
        "dl",
        "dt",
        "ellipse",
        "em",
        "figcaption",
        "figure",
        "footer",
        "g",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "head",
        "header",
        "hr",
        "html",
        "i",
        "img",
        "li",
        "line",
        "main",
        "mark",
        "nav",
        "ol",
        "p",
        "path",
        "polygon",
        "polyline",
        "pre",
        "rect",
        "section",
        "small",
        "span",
        "strong",
        "style",
        "sub",
        "summary",
        "sup",
        "svg",
        "table",
        "tbody",
        "td",
        "text",
        "tfoot",
        "th",
        "thead",
        "title",
        "tr",
        "ul",
    }
)
_VOID_TAGS = frozenset({"br", "col", "hr", "img"})
_BLOCKED_CONTENT_TAGS = frozenset(
    {"applet", "audio", "embed", "form", "iframe", "link", "object", "script", "template", "video"}
)
_GLOBAL_ATTRIBUTES = frozenset(
    {
        "class",
        "dir",
        "height",
        "id",
        "lang",
        "role",
        "style",
        "title",
        "viewbox",
        "width",
    }
)
_TAG_ATTRIBUTES: dict[str, frozenset[str]] = {
    "a": frozenset({"href"}),
    "button": frozenset({"disabled", "type"}),
    "circle": frozenset({"cx", "cy", "fill", "r", "stroke", "stroke-width"}),
    "col": frozenset({"span"}),
    "ellipse": frozenset({"cx", "cy", "fill", "rx", "ry", "stroke"}),
    "img": frozenset({"alt", "src"}),
    "line": frozenset({"stroke", "stroke-width", "x1", "x2", "y1", "y2"}),
    "path": frozenset({"d", "fill", "stroke", "stroke-width"}),
    "polygon": frozenset({"fill", "points", "stroke"}),
    "polyline": frozenset({"fill", "points", "stroke"}),
    "rect": frozenset({"fill", "height", "rx", "ry", "stroke", "width", "x", "y"}),
    "svg": frozenset({"fill", "preserveaspectratio", "stroke", "xmlns"}),
    "td": frozenset({"colspan", "rowspan"}),
    "th": frozenset({"colspan", "rowspan", "scope"}),
}
_DATA_IMAGE_RE = re.compile(r"^data:image/(?:gif|jpe?g|png|webp);base64,[a-z0-9+/=\s]+$", re.I)
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
_CSS_IMPORT_RE = re.compile(r"@import\s+[^;]+;?", re.I)
_CSS_URL_RE = re.compile(r"url\s*\([^)]*\)", re.I)
_CSS_DANGEROUS_DECLARATION_RE = re.compile(
    r"(?:^|;)[^;]*(?:expression\s*\(|behavior\s*:|-moz-binding\s*:)[^;]*;?", re.I
)
_FENCE_RE = re.compile(r"```(?:html?|HTML?)?\s*\n?([\s\S]*?)```")


class HtmlValidationError(ValueError):
    pass


def extract_html(raw: str) -> str:
    """Extrae el bloque HTML mayor de una respuesta de modelo sin acoplarse al proveedor."""
    value = str(raw or "").strip()
    if not value:
        return ""
    blocks = [match.strip() for match in _FENCE_RE.findall(value) if match.strip()]
    if blocks:
        return max(blocks, key=len)
    document = re.search(r"(<!doctype\s+html[\s\S]*?</html>)", value, re.I)
    if document:
        return document.group(1).strip()
    html_document = re.search(r"(<html\b[\s\S]*?</html>)", value, re.I)
    if html_document:
        return html_document.group(1).strip()
    partial = re.search(r"(<!doctype\s+html[\s\S]*$|<html\b[\s\S]*$)", value, re.I)
    if partial:
        return partial.group(1).strip()
    return re.sub(r"^```\w*\s*|```$", "", value, flags=re.I).strip()


def _sanitize_css(css: str) -> str:
    clean = _CSS_COMMENT_RE.sub("", css)
    clean = _CSS_IMPORT_RE.sub("", clean)
    clean = _CSS_URL_RE.sub("none", clean)
    clean = _CSS_DANGEROUS_DECLARATION_RE.sub("", clean)
    clean = clean.replace("</style", "<\\/style")
    return clean[:MAX_HTML_BYTES]


def _sanitize_inline_style(style: str) -> str:
    declarations: list[str] = []
    for raw in style.split(";"):
        if ":" not in raw:
            continue
        name, value = raw.split(":", 1)
        name = name.strip().lower()
        value = value.strip()
        if not re.fullmatch(r"--[a-z0-9_-]+|[a-z][a-z0-9-]*", name):
            continue
        lowered = value.lower()
        if any(token in lowered for token in ("url(", "expression(", "javascript:", "behavior:")):
            continue
        declarations.append(f"{name}:{value}")
    return ";".join(declarations)[:20_000]


class _SafeHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.output: list[str] = []
        self.open_tags: list[str] = []
        self.blocked_tags: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self.blocked_tags:
            if tag in _BLOCKED_CONTENT_TAGS:
                self.blocked_tags.append(tag)
            return
        if tag in _BLOCKED_CONTENT_TAGS:
            self.blocked_tags.append(tag)
            return
        if tag not in _ALLOWED_TAGS:
            return

        safe_attrs: list[str] = []
        allowed = _GLOBAL_ATTRIBUTES | _TAG_ATTRIBUTES.get(tag, frozenset())
        for raw_name, raw_value in attrs:
            name = raw_name.lower()
            value = str(raw_value or "")
            if name.startswith("on"):
                continue
            if name.startswith("aria-") or name.startswith("data-"):
                if len(value) <= 2_000:
                    safe_attrs.append(f'{name}="{html_lib.escape(value, quote=True)}"')
                continue
            if name not in allowed:
                continue
            if name == "style":
                value = _sanitize_inline_style(value)
                if not value:
                    continue
            elif name == "href":
                if not value.startswith("#"):
                    continue
            elif name == "src":
                if not _DATA_IMAGE_RE.fullmatch(value) or len(value) > 2_000_000:
                    continue
            elif name == "type" and tag == "button":
                value = "button"
            safe_attrs.append(f'{name}="{html_lib.escape(value, quote=True)}"')

        suffix = f" {' '.join(safe_attrs)}" if safe_attrs else ""
        self.output.append(f"<{tag}{suffix}>")
        if tag not in _VOID_TAGS:
            self.open_tags.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if self.open_tags and self.open_tags[-1] == tag.lower():
            self.open_tags.pop()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.blocked_tags:
            if tag == self.blocked_tags[-1]:
                self.blocked_tags.pop()
            return
        if tag not in _ALLOWED_TAGS or tag in _VOID_TAGS:
            return
        if tag in self.open_tags:
            while self.open_tags:
                current = self.open_tags.pop()
                self.output.append(f"</{current}>")
                if current == tag:
                    break

    def handle_data(self, data: str) -> None:
        if self.blocked_tags:
            return
        if self.open_tags and self.open_tags[-1] == "style":
            self.output.append(_sanitize_css(data))
        else:
            self.output.append(html_lib.escape(data))

    def close_document(self) -> str:
        super().close()
        while self.open_tags:
            self.output.append(f"</{self.open_tags.pop()}>")
        return "".join(self.output)


def sanitize_html(raw: str) -> str:
    extracted = extract_html(raw)
    encoded = extracted.encode("utf-8")
    if len(encoded) > MAX_HTML_BYTES:
        raise HtmlValidationError(f"El HTML supera el límite de {MAX_HTML_BYTES} bytes.")
    if len(extracted) < 50 or not re.search(r"<(?:html|body|main|section|div)\b", extracted, re.I):
        raise HtmlValidationError("No encontré una estructura HTML visual válida.")

    parser = _SafeHtmlParser()
    try:
        parser.feed(extracted)
        clean = parser.close_document()
    except (ValueError, AssertionError) as exc:
        raise HtmlValidationError("El HTML está mal formado y no pudo sanearse.") from exc

    if "<html" not in clean.lower():
        clean = f"<html><head></head><body>{clean}</body></html>"
    elif "<body" not in clean.lower():
        clean = clean.replace("</html>", "<body></body></html>")
    if "<head" not in clean.lower():
        clean = re.sub(r"<html([^>]*)>", r"<html\1><head></head>", clean, count=1, flags=re.I)

    policy = (
        "default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:; "
        "script-src 'none'; connect-src 'none'; frame-src 'none'; media-src 'none'; "
        "object-src 'none'; base-uri 'none'; form-action 'none'"
    )
    security_head = (
        f'<meta http-equiv="Content-Security-Policy" content="{policy}">'
        '<meta name="referrer" content="no-referrer">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
    )
    clean = re.sub(r"<head([^>]*)>", rf"<head\1>{security_head}", clean, count=1, flags=re.I)
    clean = "<!doctype html>\n" + clean

    dangerous = re.compile(
        r"<(?:script|iframe|object|embed|form|link)\b|\son[a-z]+\s*=|javascript:|@import|url\s*\(",
        re.I,
    )
    if dangerous.search(clean):
        raise HtmlValidationError("El HTML conserva contenido activo o acceso de red inseguro.")
    return clean
