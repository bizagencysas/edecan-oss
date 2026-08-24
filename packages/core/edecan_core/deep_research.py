"""Deep research mode: investigación profunda multi-paso (§20 del Master Directive).

Pipeline:
    Pregunta → plan de investigación → subpreguntas → búsquedas paralelas
    → leer fuentes → extraer hallazgos → identificar gaps → buscar de nuevo
    → cross-check → análisis de contradicciones → síntesis final

No es un agente separado: es un pipeline que usa el Agent existente
con tools de búsqueda y un prompt estructurado.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MAX_RESEARCH_STEPS = 10
MAX_PARALLEL_SEARCHES = 4
MIN_SOURCES_FOR_SYNTHESIS = 3
_LOCAL_SEARCH_TERMS = frozenset(
    {
        "restaurante", "restaurantes", "café", "cafe", "hotel", "barbería",
        "barberia", "peluquería", "peluqueria", "gimnasio", "tienda", "médico",
        "medico", "farmacia", "cerca", "cercano", "cercana", "local", "negocio",
    }
)


@dataclass
class ResearchFinding:
    """Un hallazgo individual de la investigación."""

    question: str
    answer: str
    sources: list[dict[str, str]] = field(default_factory=list)
    confidence: str = "medium"  # high, medium, low


@dataclass
class ResearchResult:
    """Resultado completo de una investigación profunda."""

    original_question: str
    sub_questions: list[str] = field(default_factory=list)
    findings: list[ResearchFinding] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    synthesis: str = ""
    sources: list[dict[str, str]] = field(default_factory=list)
    total_sources_checked: int = 0

    def to_prompt_section(self) -> str:
        """Convierte el resultado en una sección para el system prompt del agente."""
        if not self.findings:
            return ""
        lines = ["<investigacion_profunda>"]
        for finding in self.findings:
            lines.append(f"Pregunta: {finding.question}")
            lines.append(f"Respuesta: {finding.answer}")
            lines.append(f"Confianza: {finding.confidence}")
            if finding.sources:
                for src in finding.sources[:3]:
                    lines.append(f"  Fuente: {src.get('title', '')} — {src.get('url', '')}")
        if self.contradictions:
            lines.append("Contradiciones:")
            for c in self.contradictions:
                lines.append(f"  - {c}")
        if self.gaps:
            lines.append("Gaps identificados:")
            for g in self.gaps:
                lines.append(f"  - {g}")
        if self.synthesis:
            lines.append(f"Síntesis: {self.synthesis}")
        lines.append("</investigacion_profunda>")
        return "\n".join(lines)


def generate_sub_questions(question: str, context: str = "") -> list[str]:
    """Genera subpreguntas para una investigación (heurística, sin LLM).

    En producción, el agente usa el modelo para generar subpreguntas.
    Esta función es un fallback estructural para cuando no hay modelo.
    """
    q = question.strip().lower()
    sub_q: list[str] = [question]
    if any(w in q for w in ("qué ", "que ", "cuál ", "cual ")):
        sub_q.append(f"Contexto histórico de: {question}")
        sub_q.append(f"Opiniones divergentes sobre: {question}")
        sub_q.append(f"Evidencia actual (2026) sobre: {question}")
    elif any(w in q for w in ("cómo ", "como ")):
        sub_q.append(f"Pasos para: {question}")
        sub_q.append(f"Mejores prácticas para: {question}")
        sub_q.append(f"Errores comunes con: {question}")
    elif any(w in q for w in ("compara", "diferencia", "vs", "versus")):
        sub_q.append(f"Ventajas de la opción A en: {question}")
        sub_q.append(f"Ventajas de la opción B en: {question}")
        sub_q.append(f"Criterios de decisión para: {question}")
    else:
        sub_q.append(f"Definición de: {question}")
        sub_q.append(f"Ejemplos de: {question}")
        sub_q.append(f"Implicaciones de: {question}")
    return sub_q[:MAX_PARALLEL_SEARCHES]


def is_local_search_question(question: str) -> bool:
    """Detecta una búsqueda de sitio físico sin depender de un proveedor."""
    words = set(question.casefold().split())
    return bool(words & _LOCAL_SEARCH_TERMS)


def local_search_subquestions(question: str) -> list[str]:
    """Plan comparativo mínimo exigido para recomendar un lugar físico."""
    return [
        f"Opciones actuales y ubicación para: {question}",
        f"Horarios, precio y disponibilidad verificables de: {question}",
        f"Comparación de alternativas y reseñas recientes para: {question}",
        f"Cómo llegar y qué datos siguen sin confirmar para: {question}",
    ]


def deduplicate_sources(sources: list[dict[str, str]]) -> list[dict[str, str]]:
    """Deduplica fuentes por URL y por dominio+titulo (§194)."""
    seen_urls: set[str] = set()
    seen_keys: set[str] = set()
    unique: list[dict[str, str]] = []
    for src in sources:
        url = src.get("url", "")
        if url in seen_urls:
            continue
        seen_urls.add(url)
        title = src.get("title", "").lower().strip()
        key = f"{url}|{title}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        unique.append(src)
    return unique


def detect_contradictions(findings: list[ResearchFinding]) -> list[str]:
    """Detecta contradicciones entre hallazgos (§195)."""
    contradictions: list[str] = []
    for i, a in enumerate(findings):
        for b in findings[i + 1:]:
            if (
                a.question == b.question
                and a.answer != b.answer
                and a.confidence != "low"
                and b.confidence != "low"
            ):
                contradictions.append(
                    f"Para '{a.question}': una fuente dice '{a.answer[:100]}' "
                    f"y otra dice '{b.answer[:100]}'"
                )
    return contradictions
