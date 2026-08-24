"""Fast path: detección de preguntas triviales que no necesitan tool routing (§124).

Preguntas como "¿cuánto es 2+2?" o "hola" no necesitan 8 agentes ni
tool selection. Este módulo las detecta y permite que el agente responda
directamente sin pasar por toda la maquinaria.

El fast path NO salta el modelo — solo reduce el overhead de tool
selection y orchestration. El modelo sigue generando la respuesta.
"""

from __future__ import annotations

import re

_TRIVIAL_PATTERNS = [
    re.compile(
        r"^(hola|buenas|hi|hey|hello|buenos?\s+d[ií]as?|buenas?\s+tardes?"
        r"|buenas?\s:noches?)\s*[!.?]?$",
        re.I,
    ),
    re.compile(r"^(gracias|thank you|thanks|muchas gracias)\s*[!.]?$", re.I),
    re.compile(r"^(ok|okay|vale|entendido|perfecto|genial|cool|nice)\s*[!.]?$", re.I),
    re.compile(r"^\d+\s*[\+\-\*/x]\s*\d+\s*=?\s*\??$", re.I),
    re.compile(r"^(qu[eé]\s+m[aá]s|y\s+t[uú]\?|c[oó]mo\s+est[aá]s)\s*[??]?$", re.I),
    re.compile(r"^(s[ií]|no|claro|por\s+supuesto|por\s+supuesto\s+que\s+s[ií])\s*[!.]$", re.I),
    # Saludos/conversación suelta que la gente hace de verdad y que el modelo,
    # con el prompt gigante, respondía en vacío (bug real 20-ago-2026: "Todo
    # bien contigo?" -> "Me quedé sin respuesta"). Son triviales: no necesitan
    # ni memoria ni capacidades ni grounding, solo conversar.
    re.compile(
        r"^c[oó]mo\s+(est[aá]s|est[aá]|vas|andas|te\s+va|va\s+todo|te\s+sientes|sigues)\b", re.I
    ),
    re.compile(
        r"^(todo\s+bien|todo\s+ok|todo\s+en\s+orden|todo\s+est[aá]\s+bien|todo\s+bien\s+contigo|est[aá]s\s+bien|qu[eé]\s+tal)\b",
        re.I,
    ),
    re.compile(r"^qu[eé]\s+(haces|haces\s+ahora|andas\s+haciendo|est[aá]s\s+haciendo)\b", re.I),
]

_MAX_TRIVIAL_CHARS = 80


def is_trivial(text: str) -> bool:
    """True si el mensaje es lo bastante simple para fast path."""
    text = text.strip()
    if not text or len(text) > _MAX_TRIVIAL_CHARS:
        return False
    return any(p.match(text) for p in _TRIVIAL_PATTERNS)


def classify_intent(text: str) -> str:
    """Clasifica la intención del mensaje para response style routing (§104).

    Returns: "simple", "technical", "creative", "research", "code",
    "vision", "conversation"
    """
    text_lower = text.lower().strip()
    if not text_lower:
        return "conversation"

    if any(
        w in text_lower
        for w in ("arregla", "bug", "código", "code", "error", "fix", "implementa", "función")
    ):
        return "code"
    if any(w in text_lower for w in ("investiga", "research", "analiza", "compara", "estudia")):
        return "research"
    if any(w in text_lower for w in ("crea", "genera", "diseña", "escribe", "redacta", "crea")):
        return "creative"
    if any(w in text_lower for w in ("foto", "imagen", "screenshot", "ver", "mira")):
        return "vision"
    if any(w in text_lower for w in ("explica", "cómo", "por qué", "qué es", "diferencia")):
        return "technical"
    if len(text_lower) < 20:
        return "simple"
    return "conversation"


def should_be_brief(intent: str) -> bool:
    """Si la respuesta debería ser corta (§104)."""
    return intent in ("simple", "conversation")
