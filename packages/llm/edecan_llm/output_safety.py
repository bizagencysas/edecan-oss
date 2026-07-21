"""Protecciones para el texto visible devuelto por proveedores LLM locales.

Los CLI de agentes pueden, de forma ocasional, incluir un breve prefacio de
autonarracion dentro del mismo campo que contiene la respuesta final. El
prompt es la defensa principal; este modulo es una ultima barrera deliberada
y conservadora para patrones inequivocos como ``debo responder``.

No intenta detectar ni reconstruir razonamiento general: hacerlo produciria
falsos positivos y podria alterar respuestas legitimas.
"""

from __future__ import annotations

import re

VISIBLE_OUTPUT_CONTRACT_ES = (
    "Entrega unicamente la respuesta final destinada a la persona. No muestres "
    "analisis, razonamiento, planificacion, borradores, notas internas ni "
    "autonarracion como 'el usuario dijo...', 'debo responder...' o 'no necesito "
    "herramientas'. Piensa en privado y responde directamente."
)

_BOUNDARY_RE = re.compile(
    r"[.!?…]+[\"'”’)]*(?=\s*(?:[A-ZÁÉÍÓÚÜÑ¿¡]|$))",
    flags=re.UNICODE,
)
_INTERNAL_SENTENCE_PATTERNS = tuple(
    re.compile(pattern, flags=re.IGNORECASE | re.UNICODE)
    for pattern in (
        r"^(?:el usuario|la usuaria|la persona)\s+(?:aclar[óo]|indic[óo]|dij[óo]|"
        r"pidi[óo]|pregunt[óo]|quiere|se refiere|mencion[óo])\b",
        r"^(?:debo|tengo que|voy a)\s+(?:responder|contestar|explicar|aclarar|decir)\b",
        r"^(?:no necesito|no hace falta)\s+(?:usar\s+)?(?:ninguna\s+)?herramientas?\b",
        r"^nada de\s+(?:tool calls?|herramientas)\b",
        r"^respondo\s+(?:con|de forma|en tono)\b",
        r"^(?:el\s+)?tono\s+(?:debe|ser[aá]|elegido|adecuado|profesional|c[aá]lido)\b",
        r"^la respuesta\s+(?:debe|ser[aá]|puede)\b",
        r"^(?:the user|the person)\s+(?:clarified|said|asked|wants|mentioned)\b",
        r"^(?:i should|i need to|i will|i'll)\s+(?:answer|respond|explain|clarify|say)\b",
        r"^(?:no tools?|no tool calls?)\s+(?:are|is)\s+needed\b",
        r"^(?:i will|i'll)\s+respond\s+(?:with|in)\b",
        r"^(?:the\s+)?tone\s+(?:should|will|is)\b",
        r"^the response\s+(?:should|will|can)\b",
    )
)
_STRONG_INTERNAL_PATTERNS = tuple(
    re.compile(pattern, flags=re.IGNORECASE | re.UNICODE)
    for pattern in (
        r"\bdebo responder\b",
        r"\bvoy a responder\b",
        r"\brespondo (?:con|de forma|en tono)\b",
        r"\bno necesito (?:usar )?(?:ninguna )?herramientas?\b",
        r"\bnada de (?:tool calls?|herramientas)\b",
        r"\bi should (?:answer|respond)\b",
        r"\bi(?: will|'ll) respond\b",
        r"\bno tool calls? (?:are|is) needed\b",
    )
)


def sanitize_visible_assistant_text(text: str) -> str:
    """Quita solo un prefacio inicial de autonarracion inequívoca.

    La eliminacion requiere al menos un marcador fuerte (por ejemplo,
    ``debo responder``). Si no queda una respuesta visible despues del
    prefacio, se conserva el original para no convertir contenido valido en
    una respuesta vacia.
    """

    original = text.strip()
    if not original:
        return original

    cursor = 0
    matched_sentences = 0
    has_strong_marker = False

    for boundary in _BOUNDARY_RE.finditer(original):
        sentence = original[cursor : boundary.end()].strip()
        if not sentence or not _looks_like_internal_sentence(sentence):
            break
        matched_sentences += 1
        has_strong_marker = has_strong_marker or any(
            pattern.search(sentence) for pattern in _STRONG_INTERNAL_PATTERNS
        )
        cursor = boundary.end()

    visible = original[cursor:].lstrip()
    if matched_sentences and has_strong_marker and visible:
        return visible
    return original


def _looks_like_internal_sentence(sentence: str) -> bool:
    return any(pattern.search(sentence) for pattern in _INTERNAL_SENTENCE_PATTERNS)
