"""Query rewriting: resuelve referencias vagas usando contexto (§56).

"eso que hablamos ayer" → consulta concreta usando memoria y contexto
de la conversación.

No reescribe el mensaje del usuario — genera una consulta interna
para memory retrieval y web search.
"""

from __future__ import annotations

import re

_TEMPORAL_PATTERNS = [
    (re.compile(r"\b(ayer|anoche|el\s+de\s+ayer)\b", re.I), "-1d"),
    (re.compile(r"\b(antier|anteayer)\b", re.I), "-2d"),
    (re.compile(r"\b(esta\s+mañana|esta\s+tarde|esta\s+noche)\b", re.I), "0d"),
    (re.compile(r"\b(esta\s+semana)\b", re.I), "-7d"),
    (re.compile(r"\b(el\s+mes\s+pasado|la\s+semana\s+pasada)\b", re.I), "-30d"),
]

_REFERENCE_PATTERNS = [
    re.compile(r"\b(eso|aquello|lo\s+que\s+hablamos|lo\s+que\s+vimos|lo\s+de\s+\w+)\b", re.I),
    re.compile(r"\b(el\s+proyecto|la\s+app|el\s+repo|el\s+código|el\s+archivo)\b", re.I),
    re.compile(r"\b(Acme|Data\s*Cred|Edecan|Example App|Referencia)\b", re.I),
]


def rewrite_query(text: str, recent_context: str = "") -> str:
    """Reescribe una consulta vaga usando contexto temporal y referencial.

    Si el texto contiene referencias temporales ("ayer") o referenciales
    ("eso que hablamos"), intenta resolverlas usando el contexto reciente.
    """
    result = text
    for pattern, offset in _TEMPORAL_PATTERNS:
        if pattern.search(result):
            result = f"{result} (contexto temporal: {offset})"
            break
    for pattern in _REFERENCE_PATTERNS:
        if pattern.search(result) and recent_context:
            result = f"{result} (contexto: {recent_context[:200]})"
            break
    return result


def is_reference_query(text: str) -> bool:
    """True si el mensaje contiene referencias que necesitan resolución."""
    return any(p.search(text) for p in _REFERENCE_PATTERNS) or any(
        p.search(text) for p, _ in _TEMPORAL_PATTERNS
    )
