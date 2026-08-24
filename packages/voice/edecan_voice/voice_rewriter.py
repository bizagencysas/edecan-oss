"""Reescritor de respuestas escritas a respuestas aptas para voz (PHASE2.md §21-25).

La salida del agente es Markdown pensado para pantalla: listas numeradas,
viñetas, tablas, URLs, bloques de código y marcadores de citación ``[1] [2]``.
Antes de mandar ese texto al TTS conviene convertirlo a habla natural para que
el sintetizador no lea literalmente ``"asterisco asterisco"`` o
``"corchete uno corchete dos"``.

``rewrite_for_voice`` hace esa conversión preservando las *speech tags* que el
modelo pueda haber incluido (``[warmly]``, ``[pause]``, …): esas etiquetas son
instrucciones de tono para Eleven v3 y deben llegar intactas al TTS.
"""

from __future__ import annotations

import re

_SPEECH_TAG_RE = re.compile(
    r"\[(?:warmly|gently|excited|curious|thoughtful|serious|playful|calmly|calm|surprised|empathetic|pause|whispering|firmly|amused|reassuring|hesitant)\]",
    re.I,
)

_FENCED_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_URL_RE = re.compile(r"\bhttps?://[^\s)>\]]+", re.I)
_CITATION_RE = re.compile(r"\[(?:\d{1,3}(?:\s*,\s*\d{1,3})*)\]")
_MD_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*|__([^_]+)__")
_MD_ITALIC_RE = re.compile(r"(?<!\w)\*([^*]+)\*(?!\w)|(?<!\w)_([^_]+)_(?!\w)")
_MD_STRIKE_RE = re.compile(r"~~([^~]+)~~")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
_MD_HEADER_RE = re.compile(r"(?m)^\s{0,3}#{1,6}\s+")
_MD_HR_RE = re.compile(r"(?m)^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_MD_BLOCKQUOTE_RE = re.compile(r"(?m)^\s{0,3}>\s?")
_MD_TABLE_ROW_RE = re.compile(r"(?m)^\s*\|.*\|\s*$")
_MD_TABLE_SEP_RE = re.compile(r"(?m)^\s*\|?[\s:|-]+\|?\s*$")
_MD_BULLET_RE = re.compile(r"(?m)^\s*[-*+]\s+")
_MD_ORDERED_RE = re.compile(r"(?m)^\s*(\d{1,3})[.)]\s+")

_WHITESPACE_RE = re.compile(r"\s+")

_ORDINALS_ES = [
    "Primero", "Segundo", "Tercero", "Cuarto", "Quinto",
    "Sexto", "Séptimo", "Octavo", "Noveno", "Décimo",
    "Además", "Luego", "Después", "Finalmente",
]
_ORDINALS_EN = [
    "First", "Second", "Third", "Fourth", "Fifth",
    "Sixth", "Seventh", "Eighth", "Ninth", "Tenth",
    "Also", "Then", "After that", "Finally",
]

_PHRASES = {
    "es": {
        "code": "te puse el código en pantalla",
        "link": "te dejé el enlace en pantalla",
        "table": "te dejé una tabla comparativa en pantalla",
        "sources": "según las fuentes",
    },
    "en": {
        "code": "I put the code on screen",
        "link": "I left the link on screen",
        "table": "I left a comparison table on screen",
        "sources": "according to the sources",
    },
}


def _phrases(language: str) -> dict[str, str]:
    return _PHRASES.get(language, _PHRASES["es"])


def _ordinals(language: str) -> list[str]:
    return _ORDINALS_EN if language == "en" else _ORDINALS_ES


def _convert_lists(text: str, language: str) -> str:
    """Convierte listas numeradas y de viñetas a enumeración hablada.

    ``1. A`` ``2. B`` → ``Primero, A. Segundo, B.`` y ``- A`` ``- B`` recibe el
    mismo tratamiento de enumeración, ya que para voz una viñeta se lee igual
    que un ítem ordenado.
    """

    ordinals = _ordinals(language)
    out_lines: list[str] = []
    item_index = 0
    in_list = False

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        ordered = _MD_ORDERED_RE.match(line)
        bullet = _MD_BULLET_RE.match(line)
        if ordered is not None or bullet is not None:
            content = line[ordered.end():] if ordered is not None else line[bullet.end():]
            marker = ordinals[min(item_index, len(ordinals) - 1)]
            out_lines.append(f"{marker}, {content.strip()}.")
            item_index += 1
            in_list = True
        else:
            if in_list and line.strip():
                in_list = False
                item_index = 0
            out_lines.append(raw_line)

    return "\n".join(out_lines)


def _convert_tables(text: str, language: str) -> str:
    """Reemplaza tablas Markdown por una mención hablada.

    Detectar tablas con certeza sin un parser completo es frágil, así que se
    exige una fila separadora (``|---|---|``) entre dos filas de ``|``: es la
    firma que distingue una tabla de texto con barras sueltas.
    """

    if not _MD_TABLE_ROW_RE.search(text) or not _MD_TABLE_SEP_RE.search(text):
        return text
    lines = text.splitlines()
    keep: list[str] = []
    skipping = False
    for line in lines:
        is_row = bool(_MD_TABLE_ROW_RE.match(line))
        is_sep = bool(_MD_TABLE_SEP_RE.match(line))
        if is_row or is_sep:
            if not skipping:
                keep.append(_phrases(language)["table"] + ".")
                skipping = True
            continue
        skipping = False
        keep.append(line)
    return "\n".join(keep)


def _truncate(text: str, max_words: int) -> str:
    """Recorta a ``max_words`` palabras sin partir una oración a la mitad cuando
    sea posible. Si la primera oración ya excede el límite, se corta por
    palabras para no devolver un bloque enorme al TTS."""

    words = text.split()
    if len(words) <= max_words:
        return text
    head = words[:max_words]
    joined = " ".join(head)
    cut = joined.rfind(". ")
    if cut == -1:
        cut = joined.rfind("? ") if "?" in joined else -1
    if cut != -1:
        return joined[: cut + 1]
    return joined.rstrip(" ,;:") + "."


def rewrite_for_voice(text: str, language: str = "es", *, max_words: int = 150) -> str:
    """Convierte una respuesta escrita en una respuesta apta para voz.

    Transformaciones (PHASE2.md §21-25):

    - Bloques de código → ``"te puse el código en pantalla"``.
    - URLs → ``"te dejé el enlace en pantalla"``.
    - Tablas → mención hablada en vez de leer ``|---|``.
    - Marcadores de citación ``[1] [2]`` → ``"según las fuentes"``.
    - Listas numeradas y de viñetas → ``"Primero… Segundo… Tercero…"``.
    - Markdown restante (negrita, cursiva, enlaces, encabezados) → texto limpio.
    - Recorte a ``max_words`` palabras (por defecto 150) para no saturar el TTS.

    Las *speech tags* (``[warmly]``, ``[pause]``, …) se conservan: son
    instrucciones de tono para Eleven v3, no contenido a leer.
    """

    if not text or not text.strip():
        return text

    phrases = _phrases(language)
    value = text

    value = _FENCED_CODE_BLOCK_RE.sub(lambda _m: phrases["code"] + ".", value)
    value = _convert_tables(value, language)
    value = _MD_IMAGE_RE.sub(lambda m: m.group(1), value)
    value = _MD_LINK_RE.sub(lambda m: m.group(1), value)
    value = _URL_RE.sub(lambda _m: phrases["link"], value)
    value = _CITATION_RE.sub(lambda _m: phrases["sources"], value)
    value = _convert_lists(value, language)
    value = _MD_HEADER_RE.sub("", value)
    value = _MD_BLOCKQUOTE_RE.sub("", value)
    value = _MD_HR_RE.sub(" ", value)
    value = _INLINE_CODE_RE.sub(lambda m: m.group(1), value)
    value = _MD_BOLD_RE.sub(lambda m: m.group(1) or m.group(2), value)
    value = _MD_ITALIC_RE.sub(lambda m: m.group(1) or m.group(2), value)
    value = _MD_STRIKE_RE.sub(lambda m: m.group(1), value)
    value = _WHITESPACE_RE.sub(" ", value)
    value = value.strip()

    value = _truncate(value, max_words)
    return value
