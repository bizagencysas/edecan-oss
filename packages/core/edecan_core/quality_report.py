"""Quality reporting: informe semanal y señales de satisfacción (§214-219).

Dos bloques de responsabilidad:

1. **Informe semanal interno** (§214): agrega eventos crudos del agente en un
   :class:`QualitySignals` y lo renderiza en un formato escaneable para detectar
   fallos recurrentes, herramientas lentas, rutas caras, correcciones comunes,
   loops del agente y fallos de retrieval.
2. **Señales de satisfacción** (§217-219): detecta señales de fricción del
   usuario (corrección, regeneración, abandono, pregunta repetida, completado)
   desde el metadata de los mensajes, y convierte feedback negativo en un caso
   de eval anonimizado (sin contenido crudo, solo categoría + referencia).

Funciones puras, sin I/O. Los diccionarios de entrada se tratan de forma
defensiva (claves ausentes, ``None``, detalles en varios formatos) para que la
agregación nunca rompa con eventos malformados.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

# Límite de entradas "top" que se conservan en el informe (mantiene el ruido bajo).
_TOP_LIMIT = 5

# Kinds de eventos que reconoce aggregate_signals (§214).
_KIND_TOOL_FAILURE = "tool_failure"
_KIND_TOOL_LATENCY = "tool_latency"
_KIND_EXPENSIVE_ROUTE = "expensive_route"
_KIND_USER_CORRECTION = "user_correction"
_KIND_AGENT_LOOP = "agent_loop"
_KIND_RETRIEVAL_FAILURE = "retrieval_failure"


@dataclass
class QualitySignals:
    """Agregado de señales de calidad para el informe semanal (§214).

    ``slowest_tools`` guarda pares ``(nombre, segundos)`` ya ordenados del más
    lento al menos lento. ``top_failures`` y ``common_user_corrections`` van
    ordenados por frecuencia descendente.
    """

    top_failures: list[str] = field(default_factory=list)
    slowest_tools: list[tuple[str, float]] = field(default_factory=list)
    expensive_routes: list[str] = field(default_factory=list)
    common_user_corrections: list[str] = field(default_factory=list)
    agent_loops: int = 0
    retrieval_failures: int = 0


def _detail_text(detail: object) -> str | None:
    """Extrae un identificador textual seguro desde el ``detail`` de un evento."""
    if detail is None:
        return None
    if isinstance(detail, str):
        value = detail.strip()
        return value or None
    if isinstance(detail, dict):
        for key in (
            "text",
            "name",
            "tool",
            "tool_name",
            "message",
            "route",
            "correction",
            "category",
            "path",
        ):
            value = detail.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None
    if isinstance(detail, (int, float)) and not isinstance(detail, bool):
        return str(detail)
    return None


def _to_seconds(value: object) -> float | None:
    """Convierte un valor a segundos; ``None`` si no es numérico."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _latency_pair(detail: object) -> tuple[str | None, float | None]:
    """Devuelve ``(nombre, segundos)`` desde un ``detail`` de latencia.

    Acepta dicts (``{"tool"/"name", "seconds"/"ms"/...}``) o pares
    ``(nombre, segundos)``.
    """
    if isinstance(detail, dict):
        name = _detail_text(detail)
        seconds: float | None = None
        for key in ("seconds", "latency", "elapsed", "latency_seconds", "duration"):
            seconds = _to_seconds(detail.get(key))
            if seconds is not None:
                return name, seconds
        for key in ("ms", "milliseconds", "latency_ms", "duration_ms"):
            milliseconds = _to_seconds(detail.get(key))
            if milliseconds is not None:
                return name, milliseconds / 1000.0
        return name, None
    if isinstance(detail, (tuple, list)) and len(detail) >= 2:
        return _detail_text(detail[0]), _to_seconds(detail[1])
    return None, None


def _count(items: list[str]) -> dict[str, int]:
    """Cuenta ocurrencias de ítems preservando solo los no vacíos."""
    counts: dict[str, int] = {}
    for item in items:
        if item:
            counts[item] = counts.get(item, 0) + 1
    return counts


def aggregate_signals(events: list[dict]) -> QualitySignals:
    """Agrega eventos crudos ``{"kind", "detail"}`` en un :class:`QualitySignals`.

    Reconoce los kinds de §214 y descarta silenciosamente los desconocidos.
    Los conteos de fallos y correcciones se ordenan por frecuencia; las
    latencias se promedian por herramienta y se ordenan de más lento a menos
    lento, todo acotado a ``_TOP_LIMIT`` entradas.
    """
    failures: list[str] = []
    latency_sums: dict[str, float] = {}
    latency_counts: dict[str, int] = {}
    expensive_routes: list[str] = []
    corrections: list[str] = []
    agent_loops = 0
    retrieval_failures = 0

    for event in events:
        if not isinstance(event, dict):
            continue
        kind = event.get("kind")
        detail = event.get("detail")

        if kind == _KIND_TOOL_FAILURE:
            name = _detail_text(detail)
            if name:
                failures.append(name)
        elif kind == _KIND_TOOL_LATENCY:
            name, seconds = _latency_pair(detail)
            if name and seconds is not None:
                latency_sums[name] = latency_sums.get(name, 0.0) + seconds
                latency_counts[name] = latency_counts.get(name, 0) + 1
        elif kind == _KIND_EXPENSIVE_ROUTE:
            route = _detail_text(detail)
            if route and route not in expensive_routes:
                expensive_routes.append(route)
        elif kind == _KIND_USER_CORRECTION:
            correction = _detail_text(detail)
            if correction:
                corrections.append(correction)
        elif kind == _KIND_AGENT_LOOP:
            agent_loops += 1
        elif kind == _KIND_RETRIEVAL_FAILURE:
            retrieval_failures += 1

    failure_counts = _count(failures)
    correction_counts = _count(corrections)

    top_failures = [
        name
        for name, _ in sorted(failure_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ][:_TOP_LIMIT]

    common_user_corrections = [
        text
        for text, _ in sorted(
            correction_counts.items(), key=lambda kv: (-kv[1], kv[0])
        )
    ][:_TOP_LIMIT]

    slowest_tools: list[tuple[str, float]] = []
    if latency_sums:
        averages = [
            (name, latency_sums[name] / latency_counts[name])
            for name in latency_sums
            if latency_counts[name]
        ]
        slowest_tools = [
            (name, round(avg, 3))
            for name, avg in sorted(averages, key=lambda pair: (-pair[1], pair[0]))
        ][:_TOP_LIMIT]

    return QualitySignals(
        top_failures=top_failures,
        slowest_tools=slowest_tools,
        expensive_routes=expensive_routes,
        common_user_corrections=common_user_corrections,
        agent_loops=agent_loops,
        retrieval_failures=retrieval_failures,
    )


def render_weekly_report(signals: QualitySignals) -> str:
    """Renderiza el informe semanal de calidad en un formato escaneable (§214).

    Cada sección lista sus entradas o ``(none)`` si está vacía, y los contadores
    (agent loops, retrieval failures) se muestran siempre como números.
    """
    lines: list[str] = ["# Weekly Quality Report", ""]

    lines.append("## Top failures")
    if signals.top_failures:
        lines.extend(f"{i}. {name}" for i, name in enumerate(signals.top_failures, 1))
    else:
        lines.append("(none)")
    lines.append("")

    lines.append("## Slowest tools")
    if signals.slowest_tools:
        lines.extend(
            f"{i}. {name} — {seconds:.3f}s"
            for i, (name, seconds) in enumerate(signals.slowest_tools, 1)
        )
    else:
        lines.append("(none)")
    lines.append("")

    lines.append("## Expensive routes")
    if signals.expensive_routes:
        lines.extend(f"- {route}" for route in signals.expensive_routes)
    else:
        lines.append("(none)")
    lines.append("")

    lines.append("## Common user corrections")
    if signals.common_user_corrections:
        lines.extend(
            f"{i}. {correction}"
            for i, correction in enumerate(signals.common_user_corrections, 1)
        )
    else:
        lines.append("(none)")
    lines.append("")

    lines.append("## Counters")
    lines.append(f"- Agent loops: {signals.agent_loops}")
    lines.append(f"- Retrieval failures: {signals.retrieval_failures}")

    return "\n".join(lines)


# --- Satisfacción del usuario (§217-219) -----------------------------------

_CORRECTION_PATTERNS = (
    re.compile(r"\bno\s+es\s+eso\b", re.IGNORECASE),
    re.compile(r"\beso\s+no\s+es\b", re.IGNORECASE),
    re.compile(r"\best[aá]\s+mal\b", re.IGNORECASE),
    re.compile(r"\beso\s+est[aá]\s+mal\b", re.IGNORECASE),
    re.compile(r"\bte\s+equivocas\b", re.IGNORECASE),
    re.compile(r"\bincorrecto\b", re.IGNORECASE),
    re.compile(r"\bno\s+es\s+correcto\b", re.IGNORECASE),
    re.compile(r"\bcorr[ií]g(?:e|eme|elo)\b", re.IGNORECASE),
    re.compile(r"\bno\s+es\s+lo\s+que\s+ped[ií]\b", re.IGNORECASE),
    re.compile(r"\bas[ií]\s+no\s+(?:es|era|funciona)\b", re.IGNORECASE),
)

_SIGNAL_ALIASES = {
    "correction": "correction",
    "user_correction": "correction",
    "regeneration": "regeneration",
    "regenerated": "regeneration",
    "abandonment": "abandonment",
    "abandoned": "abandonment",
    "immediate_abandonment": "abandonment",
    "repeated_question": "repeated_question",
    "repeated": "repeated_question",
    "repeat": "repeated_question",
    "completion": "completion",
    "completed": "completion",
    "successful_completion": "completion",
    "success": "completion",
}


def _canonical_signal(raw: str) -> str | None:
    """Normaliza un nombre de señal (lista de meta) a su forma canónica."""
    key = re.sub(r"[\s\-]+", "_", raw.strip().lower())
    return _SIGNAL_ALIASES.get(key)


def _meta_signals(meta: dict) -> set[str]:
    """Extrae señales canónicas desde el ``meta`` de un mensaje.

    Acepta tanto una lista/string en ``meta["signals"]`` como flags booleanos
    sueltos (``correction``, ``regenerated``, ``abandoned``, etc.).
    """
    found: set[str] = set()

    raw_signals = meta.get("signals")
    if isinstance(raw_signals, str):
        canonical = _canonical_signal(raw_signals)
        if canonical:
            found.add(canonical)
    elif isinstance(raw_signals, (list, tuple, set)):
        for signal in raw_signals:
            canonical = _canonical_signal(str(signal))
            if canonical:
                found.add(canonical)

    flag_map = {
        "correction": ("correction", "corrected", "user_correction"),
        "regeneration": ("regeneration", "regenerated", "was_regenerated"),
        "abandonment": ("abandonment", "abandoned", "immediate_abandonment"),
        "repeated_question": ("repeated_question", "repeated", "repeat"),
        "completion": ("completion", "completed", "success", "successful_completion"),
    }
    for canonical, keys in flag_map.items():
        if any(meta.get(key) for key in keys):
            found.add(canonical)

    return found


def _normalize(text: str) -> str:
    """Normaliza texto para comparar preguntas repetidas (quita signos/case)."""
    value = re.sub(r"[^\w\s]+", "", text.strip().lower())
    return re.sub(r"\s+", " ", value).strip()


def _looks_like_correction(role: str, text: str) -> bool:
    """True si un mensaje de usuario parece una corrección explícita."""
    if role != "user" or not text:
        return False
    return any(pattern.search(text) for pattern in _CORRECTION_PATTERNS)


def satisfaction_signals(messages: list[dict]) -> dict:
    """Detecta señales de satisfacción desde el metadata de los mensajes (§217).

    Cada mensaje es ``{"role", "text", "meta"}``. Se detectan cinco señales:
    corrección de usuario, regeneración, abandono, pregunta repetida y
    completado exitoso. El metadata es la fuente primaria; como respaldo, las
    correcciones también se detectan por texto y las preguntas repetidas por
    texto normalizado duplicado (sin doble conteo).

    Devuelve un dict de conteos por señal.
    """
    counts = {
        "user_corrections": 0,
        "regenerations": 0,
        "abandonments": 0,
        "repeated_questions": 0,
        "successful_completions": 0,
    }
    seen_user_texts: set[str] = set()

    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role", ""))
        text = str(message.get("text", "") or "")
        meta = message.get("meta") or {}
        if not isinstance(meta, dict):
            meta = {}

        signals = _meta_signals(meta)
        flagged_repeat = "repeated_question" in signals

        if role == "user":
            normalized = _normalize(text)
            if normalized:
                if not flagged_repeat and normalized in seen_user_texts:
                    counts["repeated_questions"] += 1
                seen_user_texts.add(normalized)

        if "correction" in signals or _looks_like_correction(role, text):
            counts["user_corrections"] += 1
        if "regeneration" in signals:
            counts["regenerations"] += 1
        if "abandonment" in signals:
            counts["abandonments"] += 1
        if "repeated_question" in signals:
            counts["repeated_questions"] += 1
        if "completion" in signals:
            counts["successful_completions"] += 1

    return counts


def _anonymized_reference(feedback: dict) -> str:
    """Genera una referencia pseudónima determinista para un feedback.

    No expone contenido crudo: solo se devuelven 8 caracteres hex de un hash
    sobre campos identificadores. Sirve para correlacionar el caso de eval con
    el evento original sin guardar el texto del usuario.
    """
    parts = [
        str(feedback.get("category") or ""),
        str(
            feedback.get("text")
            or feedback.get("input")
            or feedback.get("message")
            or ""
        ),
        str(
            feedback.get("turn_id")
            or feedback.get("id")
            or feedback.get("message_id")
            or ""
        ),
    ]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


def negative_feedback_to_case(feedback: dict) -> dict:
    """Convierte feedback negativo en un caso de eval anonimizado (§218-219).

    El resultado es ``{"name", "input", "category", "expected"}`` y NUNCA
    incluye el contenido crudo del usuario: solo la categoría y una referencia
    pseudónima. Así los fallos reales se convierten en casos de prueba sin
    filtrar datos sensibles.
    """
    source = feedback if isinstance(feedback, dict) else {}
    category = _detail_text(source.get("category")) or "negative_feedback"
    reference = _anonymized_reference(source)

    name = f"negative_feedback_{reference}"
    case_input = (
        f"Negative feedback in category '{category}' "
        f"(anonymized ref {reference})."
    )
    expected = (
        f"The agent should have produced a correct, accurate answer for "
        f"category '{category}' without triggering the reported correction."
    )

    return {
        "name": name,
        "input": case_input,
        "category": category,
        "expected": expected,
    }