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

_SEARCH_STOPWORDS = frozenset(
    {
        "a",
        "al",
        "and",
        "cual",
        "cuales",
        "de",
        "del",
        "difference",
        "diferencia",
        "diferencias",
        "el",
        "en",
        "entre",
        "es",
        "esta",
        "hay",
        "is",
        "la",
        "las",
        "los",
        "of",
        "o",
        "por",
        "que",
        "son",
        "the",
        "un",
        "una",
        "what",
        "y",
    }
)

# No contiene respuestas ni nombres de modelos. Solo identifica el lugar donde
# un producto publica su verdad cambiante. Añadir un proveedor nuevo no exige
# tocar el razonamiento ni los prompts.
_OFFICIAL_SOURCE_PROFILES: tuple[tuple[frozenset[str], str, tuple[str, ...]], ...] = (
    (
        frozenset({"chatgpt", "codex", "gpt", "openai"}),
        "OpenAI",
        ("openai.com", "chatgpt.com"),
    ),
    (
        frozenset({"anthropic", "claude"}),
        "Anthropic",
        ("anthropic.com",),
    ),
    (
        frozenset({"gemini", "google"}),
        "Google",
        ("ai.google.dev", "deepmind.google", "cloud.google.com", "blog.google"),
    ),
    (
        frozenset({"cursor"}),
        "Cursor",
        ("cursor.com",),
    ),
    (
        frozenset({"ollama"}),
        "Ollama",
        ("ollama.com",),
    ),
    (
        frozenset({"deepseek"}),
        "DeepSeek",
        ("deepseek.com",),
    ),
    (
        frozenset({"grok", "xai"}),
        "xAI",
        ("x.ai",),
    ),
    (
        frozenset({"qwen"}),
        "Qwen",
        ("qwenlm.ai", "alibabacloud.com"),
    ),
    (
        frozenset({"kimi", "moonshot"}),
        "Moonshot AI",
        ("moonshot.ai", "kimi.com"),
    ),
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
    """Construye la búsqueda general de respaldo.

    Reduce palabras interrogativas y signos que algunos buscadores HTML
    interpretan mal. La fecha conserva el requisito de actualidad sin volver
    la consulta demasiado larga.
    """

    terms = _salient_search_terms(user_text)
    year = date_iso[:4]
    suffix = f"official current {year}" if language == "en" else f"oficial vigente {year}"
    return f"{terms} {suffix}".strip()


def grounding_queries(user_text: str, *, language: str, date_iso: str) -> tuple[str, ...]:
    """Devuelve consultas escalonadas, empezando por la fuente propietaria.

    Una consulta de proveedor simple funciona mejor que añadir frases largas
    como «fuentes oficiales primarias» al texto original. Si no se reconoce el
    producto, queda la búsqueda general de respaldo.
    """

    general = grounding_query(user_text, language=language, date_iso=date_iso)
    profile = _official_source_profile(user_text)
    if profile is None:
        return (general,)
    provider_name, _domains = profile
    terms = _salient_search_terms(user_text)
    official = f"{provider_name} {terms} official".strip()
    return tuple(dict.fromkeys((official, general)))


def official_source_domains(user_text: str) -> tuple[str, ...]:
    """Dominios primarios esperados para el producto mencionado."""

    profile = _official_source_profile(user_text)
    return profile[1] if profile is not None else ()


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", without_marks).strip()


def _official_source_profile(text: str) -> tuple[str, tuple[str, ...]] | None:
    tokens = set(_normalize(text).split())
    for signals, provider_name, domains in _OFFICIAL_SOURCE_PROFILES:
        if tokens & signals:
            return provider_name, domains
    return None


def _salient_search_terms(text: str) -> str:
    raw_tokens = re.findall(r"[^\W_][\w.+-]*", text, flags=re.UNICODE)
    useful = [token for token in raw_tokens if _normalize(token) not in _SEARCH_STOPWORDS]
    return " ".join(useful[:16]) or text.strip()
