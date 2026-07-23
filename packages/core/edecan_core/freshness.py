"""Criterio de actualidad para respuestas que pueden quedar obsoletas.

Los modelos cambian sin que cambie Edecán. Esta capa pertenece al orquestador:
detecta cuándo una respuesta factual necesita evidencia reciente y permite
investigar antes de pedirle al modelo que redacte la respuesta. Es
deliberadamente independiente de OpenAI, Anthropic, Ollama o cualquier otro
proveedor.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FreshnessDecision:
    """Resultado explicable y estable del clasificador de actualidad."""

    required: bool
    reason: str | None = None


_EXPLICIT_RECENCY = (
    "actual",
    "actualmente",
    "ahora",
    "a dia de hoy",
    "a fecha de hoy",
    "hoy",
    "latest",
    "newest",
    "recent",
    "reciente",
    "ultima version",
    "ultimo modelo",
    "vigente",
    "this week",
    "this month",
)

_HIGH_STAKES = frozenset(
    {
        "abogado",
        "diagnostico",
        "financiera",
        "financiero",
        "impuesto",
        "impuestos",
        "inversion",
        "legal",
        "ley",
        "medica",
        "medico",
        "medicina",
        "reglamento",
        "regulacion",
        "salud",
    }
)

_FAST_CHANGING_DOMAINS = frozenset(
    {
        "accion",
        "acciones",
        "api",
        "biblioteca",
        "bolsa",
        "chatgpt",
        "claude",
        "clima",
        "codex",
        "cotizacion",
        "crypto",
        "criptomoneda",
        "cursor",
        "deepseek",
        "disponibilidad",
        "eleccion",
        "elecciones",
        "gemini",
        "grok",
        "hotel",
        "hoteles",
        "kimi",
        "libreria",
        "modelo",
        "modelos",
        "noticia",
        "noticias",
        "ollama",
        "openai",
        "precio",
        "precios",
        "presidente",
        "qwen",
        "sdk",
        "tarifa",
        "tasas",
        "version",
        "versiones",
        "vuelo",
        "vuelos",
    }
)

_FACTUAL_INTENT = frozenset(
    {
        "cual",
        "cuales",
        "como",
        "compara",
        "comparacion",
        "diferencia",
        "diferencias",
        "disponible",
        "existe",
        "existen",
        "mejor",
        "oficial",
        "oficiales",
        "puede",
        "que",
        "son",
    }
)


def assess_freshness(text: str) -> FreshnessDecision:
    """Decide si una respuesta necesita evidencia web reciente."""

    normalized = _normalize(text)
    if not normalized:
        return FreshnessDecision(False)
    tokens = set(normalized.split())

    if any(phrase in normalized for phrase in _EXPLICIT_RECENCY):
        return FreshnessDecision(True, "la petición exige información actual")
    if tokens & _HIGH_STAKES:
        return FreshnessDecision(True, "la precisión es sensible y puede haber cambiado")
    if tokens & _FAST_CHANGING_DOMAINS and tokens & _FACTUAL_INTENT:
        return FreshnessDecision(True, "el tema cambia con frecuencia")
    if re.search(r"\b20(?:2[5-9]|[3-9]\d)\b", normalized):
        return FreshnessDecision(True, "la petición menciona una fecha reciente")
    if re.search(r"\bv?\d+(?:\.\d+){1,3}\b", normalized) and tokens & {
        "modelo",
        "modelos",
        "software",
        "api",
        "sdk",
        "version",
    }:
        return FreshnessDecision(True, "la petición depende de una versión")

    return FreshnessDecision(False)


def grounding_query(user_text: str, *, language: str, date_iso: str) -> str:
    """Construye una búsqueda centrada en fuentes actuales y primarias."""

    suffix = (
        f"current official primary sources as of {date_iso}"
        if language == "en"
        else f"fuentes oficiales primarias información vigente al {date_iso}"
    )
    return f"{user_text.strip()} {suffix}".strip()


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", without_marks).strip()
