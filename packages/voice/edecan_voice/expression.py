"""Preparación de texto hablado sin contaminar el mensaje visible.

La respuesta de chat se conserva como texto limpio. Solo justo antes de TTS
se elimina Markdown y, cuando el proveedor declara soporte para Eleven v3,
se agrega una dirección vocal breve. Otros proveedores reciben únicamente
el texto limpio y jamás intentan pronunciar etiquetas entre corchetes.
"""

from __future__ import annotations

import re

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

    value = _MARKDOWN_IMAGE_RE.sub(lambda match: match.group(1), text)
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
    """Agrega una indicación vocal compatible con Eleven v3.

    Se usa una sola etiqueta de dirección. Evita efectos de sonido, diálogo
    inventado y cambios de significado. La heurística es deliberadamente
    conservadora para una conversación cotidiana.
    """

    spoken = plain_text_for_speech(text)
    lowered = spoken.casefold()
    if not spoken:
        return spoken
    if any(word in lowered for word in ("lo siento", "lamento", "entiendo que moleste")):
        tag = "[gently]"
    elif any(word in lowered for word in ("cuidado", "importante", "riesgo", "urgente")):
        tag = "[serious]"
    elif any(word in lowered for word in ("listo", "excelente", "funcionó", "conseguimos")):
        tag = "[warmly]"
    elif spoken.rstrip().endswith("?"):
        tag = "[curious]"
    elif "!" in spoken:
        tag = "[excited]"
    else:
        tag = "[warmly]"
    return f"{tag} {spoken}"
