"""Explainability: resúmenes de ejecución y explicación segura de acciones (§240-244).

Edecán debe poder decirle al usuario QUÉ hizo y POR QUÉ, pero sin revelar
el chain-of-thought privado del modelo. Tres responsabilidades:

1. ``execution_summary`` — compacta "Done / Changed / Verified / Remaining"
   para tareas complejas, priorizando valor real sobre ruido.
2. ``why_did_you_do`` — explicación breve y segura de evidencia / tools / razón,
   redactando cualquier marcador de razonamiento interno (CoT).
3. ``render_for_mode`` — el usuario normal ve solo el resumen; el técnico puede
   expandir detalles (tests, logs, diffs, tools).

Funciones puras, sin I/O: reciben datos y devuelven texto. No guardan estado
ni revelan contenido interno; los marcadores de razonamiento se redactan a
nivel de string para que una cadena de CoT nunca llegue al usuario.
"""

from __future__ import annotations

import re

# Marcadores que indican razonamiento interno / chain-of-thought privado.
# Se redactan para que la explicación entregada sea segura (no revela CoT).
_REASONING_MARKERS = (
    "chain of thought",
    "chain-of-thought",
    "chainofthought",
    "scratchpad",
    "internal reasoning",
    "private reasoning",
    "reasoning trace",
    "reasoning tokens",
    "step by step",
    "step-by-step",
    "let me think",
    "let's think",
    "let us think",
    "my reasoning",
    "my thinking",
    "hidden thought",
    "inner monologue",
    "internal monologue",
)

_REDACTED = "[omitido]"

_TAG_NAMES = (
    "thinking",
    "reasoning",
    "scratchpad",
    "chain_of_thought",
    "analysis",
    "internal_monologue",
    "deliberation",
)

# Bloques con apertura y cierre: se eliminan completos (ej. <thinking>...</thinking>).
_TAG_PAIR = re.compile(
    r"<\s*(?:" + "|".join(_TAG_NAMES) + r")\b[^>]*>.*?<\s*/\s*(?:"
    + "|".join(_TAG_NAMES)
    + r")\s*>",
    re.IGNORECASE | re.DOTALL,
)

# Etiquetas sueltas (sin cierre): se reemplazan por el marcador de omitido.
_TAG_TOKEN = re.compile(
    r"<\s*/?\s*(?:" + "|".join(_TAG_NAMES) + r")\s*>",
    re.IGNORECASE,
)

# Frases de razonamiento interno: se reemplazan por el marcador de omitido.
_MARKER_PATTERN = re.compile(
    "|".join(re.escape(marker) for marker in _REASONING_MARKERS),
    re.IGNORECASE,
)


def _redact_reasoning(text: str) -> str:
    """Redacta marcadores de razonamiento interno en un texto.

    Elimina bloques etiquetados completos y reemplaza frases/tokens de CoT por
    ``[omitido]``, preservando el contenido legítimo que sí puede mostrarse.
    """
    if not text:
        return ""
    result = _TAG_PAIR.sub(_REDACTED, text)
    result = _TAG_TOKEN.sub(_REDACTED, result)
    result = _MARKER_PATTERN.sub(_REDACTED, result)
    return result


def _clean_item(item: str) -> str | None:
    """Redacta un ítem y devuelve ``None`` si solo quedan marcadores/vacío.

    Así los ítems que eran puro razonamiento interno se descartan en lugar de
    renderizarse como "[omitido]" suelto, manteniendo la salida sin ruido.
    """
    cleaned = _redact_reasoning(item or "")
    meaningful = cleaned.replace(_REDACTED, "").strip()
    if not meaningful:
        return None
    return cleaned.strip()


def execution_summary(
    done: list[str],
    changed: list[str],
    verified: list[str],
    remaining: list[str],
) -> str:
    """Renderiza "Done / Changed / Verified / Remaining" de forma compacta.

    Muestra el conteo y los nombres reales de cada sección (máximo valor),
    omitiendo los ítems vacíos (mínimo ruido). Las secciones sin ítems se
    marcan como ``none`` para que "vacío" no se confunda con "desconocido".
    """
    sections = (
        ("Done", done),
        ("Changed", changed),
        ("Verified", verified),
        ("Remaining", remaining),
    )
    lines: list[str] = []
    for label, items in sections:
        cleaned = [item for item in items if item and str(item).strip()]
        if not cleaned:
            lines.append(f"{label}: none")
        else:
            lines.append(f"{label} ({len(cleaned)}): " + ", ".join(cleaned))
    return "\n".join(lines)


def why_did_you_do(
    evidence: list[str],
    tools_used: list[str],
    reason: str,
) -> str:
    """Explica de forma breve y segura evidencia, tools y motivo de la acción.

    Redacta marcadores de razonamiento interno en ``evidence`` y ``reason``
    (nunca expone CoT privado, §240). Los ítems que eran puro razonamiento se
    descartan; las secciones vacías se marcan como ``none``.
    """
    evidence_clean = [item for item in (_clean_item(e) for e in evidence) if item]
    tools_clean = [tool.strip() for tool in tools_used if tool and tool.strip()]
    reason_clean = _clean_item(reason) or ""

    lines = [
        "Evidence: " + ("; ".join(evidence_clean) if evidence_clean else "none"),
        "Tools: " + (", ".join(tools_clean) if tools_clean else "none"),
        "Why: " + (reason_clean if reason_clean else "none"),
    ]
    return "\n".join(lines)


def render_for_mode(
    summary: str,
    technical_details: list[str],
    *,
    expert: bool,
) -> str:
    """Devuelve el resumen solo (modo simple) o con detalles técnicos (experto).

    En modo simple se oculta la infraestructura y los detalles internos (§244).
    En modo experto los detalles van dentro de un bloque colapsable
    (``<details>``), para que el usuario técnico los expanda bajo demanda (§242-243).
    Si no hay detalles reales, se devuelve solo el resumen en ambos modos.
    """
    if not expert:
        return summary

    details = [detail for detail in technical_details if detail and detail.strip()]
    if not details:
        return summary

    lines: list[str] = [summary, "", "<details>", "<summary>Technical details</summary>", ""]
    for detail in details:
        lines.append(f"- {detail.strip()}")
    lines.extend(["", "</details>"])
    return "\n".join(lines)