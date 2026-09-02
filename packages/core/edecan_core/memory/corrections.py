"""Detección determinista de candidatos a preferencia durable a partir de
correcciones repetidas del usuario (product design§172).

Regla (determinista y sin LLM ni IO): si el usuario ha re-afirmado la MISMA
preferencia varias veces —filas de `memory_items` con `kind='preference'` y
`namespace='user'`— es señal de una preferencia durable que merece ser
PROPUESTA, nunca guardada automáticamente (§126: "No guardar todo
automáticamente"; §172: "Add to profile? Add / Ignore").

El caller (`GET /v1/memory/suggestions`) trae las preferencias recientes
(ya filtradas por tenant/usuario/namespace en el `Repo`) y este módulo solo
las cuenta: agrupa por contenido normalizado (minúsculas + espacios
colapsados, el mismo proxy de "es el mismo recuerdo" que
`memory_consolidate._normalizar_contenido`), exige un mínimo de
repeticiones y devuelve, como mucho, unos pocos candidatos con su confianza.
Mismo input → mismo output.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

MIN_REPETICIONES = 3
MAX_CANDIDATOS = 5

# Frases imperativas que el usuario suele usar para corregir el comportamiento
# del asistente en el chat (product design). Se comparan contra el texto ya
# normalizado (minúsculas + espacios colapsados), así que el acento del habla
# no importa y ninguna de estas frases lleva tilde.
_PATRONES_CORRECCION: tuple[str, ...] = (
    "no vuelvas a",
    "nunca vuelvas a",
    "siempre haz",
    "primero dame",
    "de ahora en adelante",
    "a partir de ahora",
)


def _normalizar(content: str) -> str:
    return " ".join(content.casefold().split())


def _texto_de_contenido(content: Any) -> str:
    """Extrae el texto crudo de un `content` de mensaje (jsonb `{"text": ...}`
    o string plano), tolerante a ambos formatos."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):
        texto = content.get("text")
        return str(texto).strip() if texto is not None else ""
    return ""


def propose_preference_candidates(
    items: list[Mapping[str, Any]],
    *,
    min_repetitions: int = MIN_REPETICIONES,
    max_candidates: int = MAX_CANDIDATOS,
) -> list[dict[str, Any]]:
    """Propone candidatos a preferencia durable desde correcciones repetidas.

    `items` es una lista de filas `memory_items` (o dicts equivalentes), cada
    una con `kind` y `content`. Solo se cuentan las de `kind='preference'`
    con `content` no vacío; el resto se ignora. Devuelve una lista (a lo sumo
    `max_candidates`) de candidatos con esta forma:

        {"text": str, "source": "corrección repetida (Nx)", "scope": "user",
         "confidence": float}

    `confidence` crece con el número de repeticiones y se satura en 0.95.
    La salida es una PROPUESTA: ninguna de estas funciones escribe memoria.
    """
    repeticiones: dict[str, int] = {}
    original_por_clave: dict[str, str] = {}
    for item in items:
        if str(item.get("kind") or "").casefold() != "preference":
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        clave = _normalizar(content)
        repeticiones[clave] = repeticiones.get(clave, 0) + 1
        if clave not in original_por_clave:
            original_por_clave[clave] = content

    candidatos: list[dict[str, Any]] = []
    for clave, conteo in sorted(
        repeticiones.items(), key=lambda item: (-item[1], item[0])
    ):
        if conteo < min_repetitions:
            continue
        candidatos.append(
            {
                "text": original_por_clave[clave],
                "source": f"corrección repetida ({conteo}x)",
                "scope": "user",
                "confidence": round(min(0.95, 0.55 + 0.1 * conteo), 2),
            }
        )
        if len(candidatos) >= max_candidates:
            break
    return candidatos


def propose_correction_candidates_from_messages(
    messages: list[Mapping[str, Any]],
    *,
    max_candidates: int = MAX_CANDIDATOS,
) -> list[dict[str, Any]]:
    """Propone candidatos a preferencia durable desde patrones de corrección
    en mensajes recientes del usuario (product design).

    A diferencia de `propose_preference_candidates` (que cuenta repeticiones
    de `memory_items` con `kind='preference'`), esta función escanea mensajes
    del chat con `role='user'` buscando frases imperativas de corrección
    ("no vuelvas a", "siempre haz", "primero dame", ...). Una sola aparición
    ya es señal suficiente (el usuario está corrigiendo de forma explícita),
    así que no se exige umbral de repeticiones; la `confidence` crece con las
    repeticiones y se satura en 0.95.

    Devuelve candidatos con la misma forma que `propose_preference_candidates`
    (`text`/`source`/`scope`/`confidence`), pero con `source` distinguible
    (`"corrección en mensajes (Nx)"`) para que la UI sepa que vino del chat y
    no de `memory_items`. Determinista y sin IO: misma lista → misma salida.
    Solo PROPONE, nunca escribe memoria.
    """
    conteo: dict[str, int] = {}
    original_por_clave: dict[str, str] = {}
    for message in messages:
        if str(message.get("role") or "").casefold() != "user":
            continue
        texto = _texto_de_contenido(message.get("content"))
        if not texto:
            continue
        normalizado = _normalizar(texto)
        if not any(patron in normalizado for patron in _PATRONES_CORRECCION):
            continue
        conteo[normalizado] = conteo.get(normalizado, 0) + 1
        original_por_clave.setdefault(normalizado, texto[:160])

    candidatos: list[dict[str, Any]] = []
    for clave, conteo_n in sorted(conteo.items(), key=lambda item: (-item[1], item[0])):
        candidatos.append(
            {
                "text": original_por_clave[clave],
                "source": f"corrección en mensajes ({conteo_n}x)",
                "scope": "user",
                "confidence": round(min(0.95, 0.55 + 0.1 * conteo_n), 2),
            }
        )
        if len(candidatos) >= max_candidates:
            break
    return candidatos


__all__ = [
    "MAX_CANDIDATOS",
    "MIN_REPETICIONES",
    "propose_correction_candidates_from_messages",
    "propose_preference_candidates",
]
