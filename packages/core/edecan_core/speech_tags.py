"""Speech tags de Eleven v3: detección, densidad y relleno determinista.

El system prompt les pide a todos los modelos que etiqueten cada oración.
Muchos (sobre todo los rápidos) lo ignoran y dejan prosa plana. Esta capa
completa lo que falte **después** de generar, para que CUALQUIER LLM que
el dueño coloque suene humano al pulsar Escuchar.

No hay lista blanca de tags: [thoughtfully], [laughs], [applause],
[clears throat] o el efecto que invente el modelo son válidos. El chat
los oculta TODOS; la voz los usa tal cual. La rotación de abajo solo
rellena oraciones que el modelo dejó planas.
"""

from __future__ import annotations

import re

SPEECH_TAG_NAMES: tuple[str, ...] = (
    "warmly",
    "gently",
    "excited",
    "curious",
    "thoughtful",
    "serious",
    "playful",
    "calmly",
    "calm",
    "surprised",
    "empathetic",
    "pause",
    "whispering",
    "firmly",
    "amused",
    "reassuring",
    "hesitant",
)

# Cualquier [efecto] de una sola línea, corto. Conserva Markdown
# `[texto](url)` e `![alt](url)` — esos no son speech tags.
SPEECH_TAG_RE = re.compile(
    r"(?<!!)\[(?![^\]]{0,120}\]\()[^\]\n]{1,120}\]",
    re.I,
)

_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`]+`")
_MD_LINK_RE = re.compile(r"\[[^\]]+\]\([^)]+\)")
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
_PLACEHOLDER_RE = re.compile(r"\x00H(\d+)\x00")
_BOUNDARY_RE = re.compile(r"[.!?;…]+[ \t]+|\n{2,}")

# Primera oración: tono, nunca [pause] suelto al arrancar.
_FIRST_DEFAULT = "warmly"
_ROTATION = ("pause", "thoughtful", "calm", "curious", "reassuring", "gently")

_CUE_TAGS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b(lo siento|perdón|perdon|sorry|unfortunately|lástima|lastima)\b",
            re.I,
        ),
        "gently",
    ),
    (
        re.compile(
            r"\b(no (tengo|sé|se|estoy seguro)|not sure|i don.?t have|dato exacto|"
            r"informaci[oó]n exacta)\b",
            re.I,
        ),
        "hesitant",
    ),
    (
        re.compile(
            r"^(sin embargo|pero\b|aunque\b|however\b|but\b|aunque)",
            re.I,
        ),
        "thoughtful",
    ),
    (
        re.compile(
            r"\b(ten en cuenta|ojo\b|cuidado|importante|advertencia|"
            r"keep in mind|note that|warning)\b",
            re.I,
        ),
        "reassuring",
    ),
    (re.compile(r"[?¿]"), "curious"),
    (
        re.compile(
            r"[!¡]|\b(genial|increíble|excelente|wow|great|amazing)\b",
            re.I,
        ),
        "excited",
    ),
    (
        re.compile(
            r"\b(hola|hey|gracias|thank|claro|por supuesto|sure)\b",
            re.I,
        ),
        "warmly",
    ),
    (re.compile(r"[$€£%]|\d{2,}"), "thoughtful"),
)


def _proteger(text: str) -> tuple[str, list[str]]:
    held: list[str] = []

    def stash(match: re.Match[str]) -> str:
        held.append(match.group(0))
        return f"\x00H{len(held) - 1}\x00"

    value = _FENCE_RE.sub(stash, text)
    value = _MD_IMAGE_RE.sub(stash, value)
    value = _MD_LINK_RE.sub(stash, value)
    value = _INLINE_CODE_RE.sub(stash, value)
    return value, held


def _restaurar(text: str, held: list[str]) -> str:
    def unstash(match: re.Match[str]) -> str:
        return held[int(match.group(1))]

    return _PLACEHOLDER_RE.sub(unstash, text)


def _spans(text: str) -> list[tuple[int, int]]:
    if not text.strip():
        return []
    starts = [0]
    for match in _BOUNDARY_RE.finditer(text):
        nxt = match.end()
        if nxt < len(text) and nxt not in starts:
            starts.append(nxt)
    spans: list[tuple[int, int]] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        if text[start:end].strip():
            spans.append((start, end))
    return spans


def _elegir_tag(sentence: str, prev: str | None, *, first: bool) -> str:
    stripped = SPEECH_TAG_RE.sub("", sentence).strip()
    for pattern, tag in _CUE_TAGS:
        if pattern.search(stripped):
            if tag == prev:
                continue
            if first and tag == "pause":
                continue
            return tag
    if first:
        return _FIRST_DEFAULT if prev != _FIRST_DEFAULT else "calm"
    for tag in _ROTATION:
        if tag != prev:
            return tag
    return "thoughtful"


def ocultar_speech_tags(text: str) -> str:
    """Texto para pintar o copiar: sin tags ni efectos, con el Markdown intacto."""

    if not text:
        return text
    if not text.strip():
        return text
    protegido, held = _proteger(text)
    value = SPEECH_TAG_RE.sub("", protegido)
    value = re.sub(r"[ \t]{2,}", " ", value)
    value = re.sub(r"^[ \t]+", "", value)
    return _restaurar(value, held)


def enriquecer_speech_tags(text: str) -> str:
    """Devuelve el texto limpio, sin tags inventadas ni speech tags."""
    return ocultar_speech_tags(text)
