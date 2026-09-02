"""Preparación de texto hablado sin contaminar el mensaje visible.

La respuesta de chat se conserva como texto (con speech tags si el modelo o
el relleno las pusieron). Justo antes de TTS se elimina Markdown y, cuando
el proveedor declara soporte para Eleven v3, se garantiza una tag por
oración. Otros proveedores reciben únicamente el texto limpio y jamás
intentan pronunciar etiquetas entre corchetes.
"""

from __future__ import annotations

import re

from edecan_core.speech_tags import ocultar_speech_tags

_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)]\([^)]+\)")
_MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)]\([^)]+\)")
_FENCED_CODE_RE = re.compile(r"```(?:\w+)?\s*(.*?)```", re.DOTALL)
_HEADING_RE = re.compile(r"(?m)^\s{0,3}#{1,6}\s+")
_LIST_RE = re.compile(r"(?m)^\s*(?:[-*+] |\d+[.)]\s+)")
_EMPHASIS_RE = re.compile(r"(?<!\w)(?:\*\*|__|\*|_)(.*?)(?:\*\*|__|\*|_)(?!\w)")
_WHITESPACE_RE = re.compile(r"[ \t]+")
_MANY_LINES_RE = re.compile(r"\n{3,}")


def plain_text_for_speech(text: str) -> str:
    """Convierte Markdown de chat a una lectura natural y estable."""

    value = ocultar_speech_tags(text)
    value = _MARKDOWN_IMAGE_RE.sub(lambda match: match.group(1), value)
    value = _MARKDOWN_LINK_RE.sub(lambda match: match.group(1), value)
    value = _FENCED_CODE_RE.sub(lambda match: match.group(1).strip(), value)
    value = _HEADING_RE.sub("", value)
    value = _LIST_RE.sub("", value)
    value = value.replace("`", "")
    for _ in range(2):
        value = _EMPHASIS_RE.sub(lambda match: match.group(1), value)
    value = _WHITESPACE_RE.sub(" ", value)
    value = _MANY_LINES_RE.sub("\n\n", value)
    return value.strip()


def expressive_eleven_v3_text(text: str) -> str:
    """Prepara texto limpio para Eleven v3 sin inyectar etiquetas no solicitadas."""
    return plain_text_for_speech(text)
