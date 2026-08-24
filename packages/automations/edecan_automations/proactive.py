"""Filtro proactivo determinista (PHASE2.md §54-55): decide si un evento
merece una notificación proactiva SIN llamar a un LLM.

El filtro responde a las cinco preguntas de PHASE2 §54:

1. ¿Es importante?
2. ¿Es nuevo?
3. ¿Le importa al usuario?
4. ¿Se necesita una acción?
5. ¿Ya se reportó?

La heurística es deliberadamente simple y transparente: puntúa el evento,
exige superar un umbral de importancia, descarta eventos viejos, respeta la
suscripción del usuario, premia los que requieren acción y suprime duplicados
dentro de una ventana de tiempo (PHASE2 §55: "no generar 40 notificaciones
porque 40 eventos ocurrieron").

Todo es aritmética y diccionarios. Los eventos entran como ``dict`` con un
vocabulario libre pero documentado; las claves ausentes caen a defaults
seguros (fail-closed: si no hay evidencia de que algo es importante, NO se
notifica).

Claves reconocidas en un evento:

- ``importance`` (numérico 0..1): puntuación explícita, gana sobre el tipo.
- ``kind`` o ``type`` (str): tipo del evento (p. ej. ``"automation_failed"``).
- ``nuevo`` (bool): ``False`` descarta el evento por no ser nuevo.
- ``occurred_at``/``created_at``/``timestamp``: marca temporal para novedad.
- ``suscrito`` (bool): ``False`` indica que el usuario no quiere este tipo.
- ``silenciado`` (bool): ``True`` apaga la notificación de este evento.
- ``requires_action`` (bool): si el evento requiere una acción del usuario.
- ``reportado`` (bool): ``True`` indica que ya se reportó antes.
- ``event_key``/``dedup_key``/``key``/``id``/``event_id``: clave de dedup.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

# Puntuación base por tipo de evento (0..1). Un tipo desconocido puntúa 0:
# sin evidencia de importancia, no se notifica (fail-closed).
_PUNTUACION_POR_TIPO: dict[str, float] = {
    "automation_failed": 0.9,
    "work_failed": 0.9,
    "self_repair_completed": 0.8,
    "phone_call_incoming": 0.8,
    "reminder_triggered": 0.6,
    "content_published": 0.5,
    "design_export_ready": 0.4,
    "automation_completed": 0.35,
    "work_completed": 0.3,
    "content_created": 0.3,
    "file_ready": 0.3,
    "pdf_ready": 0.3,
    "design_ready": 0.3,
    "push_test": 0.0,
}

# Umbral por debajo del cual un evento NO se notifica (fail-closed).
IMPORTANCIA_MINIMA = 0.5

# Ventana por defecto para suprimir duplicados (PHASE2 §55).
VENTANA_DUPLICADOS = timedelta(hours=6)

# Ventana por defecto para considerar un evento "nuevo" (§54, pregunta 2).
VENTANA_NOVEDAD = timedelta(hours=24)


def _tipo(event: Mapping[str, Any]) -> str:
    return str(event.get("kind", event.get("type", "")))


def score_event(event: Mapping[str, Any]) -> float:
    """Puntuación del evento en el rango ``0..1``.

    Prioridad:

    1. ``event["importance"]`` explícito si es numérico (no booleano).
    2. El tipo (``kind``/``type``) contra ``_PUNTUACION_POR_TIPO``.

    Un tipo desconocido sin ``importance`` puntúa ``0.0``.
    """
    explicita = event.get("importance")
    if isinstance(explicita, (int, float)) and not isinstance(explicita, bool):
        return max(0.0, min(1.0, float(explicita)))
    return _PUNTUACION_POR_TIPO.get(_tipo(event), 0.0)


def _marca_temporal(event: Mapping[str, Any]) -> datetime | None:
    """Extrae la marca temporal del evento, si trae una parseable."""
    for clave in ("occurred_at", "created_at", "timestamp"):
        valor = event.get(clave)
        if valor is None:
            continue
        if isinstance(valor, datetime):
            return valor
        try:
            return datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
    return None


def _clave(event: Mapping[str, Any]) -> str:
    """Clave estable de dedup del evento (vacía si no se puede derivar)."""
    clave = event.get("event_key") or event.get("dedup_key") or event.get("key")
    if clave:
        return str(clave)
    tipo = _tipo(event)
    identificador = event.get("id") or event.get("event_id")
    if tipo and identificador:
        return f"{tipo}:{identificador}"
    return ""


def es_importante(event: Mapping[str, Any]) -> bool:
    """¿Es importante? (pregunta 1). True solo si la puntuación supera el umbral."""
    return score_event(event) >= IMPORTANCIA_MINIMA


def es_nuevo(
    event: Mapping[str, Any],
    *,
    now: datetime | None = None,
    ventana: timedelta = VENTANA_NOVEDAD,
) -> bool:
    """¿Es nuevo? (pregunta 2).

    Un ``nuevo=False`` explícito lo descarta; sin marca temporal no hay forma
    de demostrar que es viejo, así que se asume nuevo (fail-open en la única
    pregunta donde no hay evidencia para fallar cerrado).
    """
    if event.get("nuevo") is False:
        return False
    marca = _marca_temporal(event)
    if marca is None:
        return True
    ahora = now or datetime.now(UTC)
    return (ahora - marca) <= ventana


def le_importa_al_usuario(event: Mapping[str, Any]) -> bool:
    """¿Le importa al usuario? (pregunta 3).

    Respeta una cancelación explícita (``suscrito=False`` o ``silenciado=True``);
    por defecto, True (no hay señal de que no le importe).
    """
    if event.get("suscrito") is False:
        return False
    if event.get("silenciado") is True:
        return False
    return True


def requiere_accion(event: Mapping[str, Any]) -> bool:
    """¿Se necesita una acción? (pregunta 4).

    Solo ``requires_action=False`` explícito puede desactivar esta pregunta:
    sin declaración, se asume que un evento que ya pasó el umbral de
    importancia merece atención (fail-open en la única pregunta donde el
    evento no trae evidencia para fallar cerrado). El filtro de importancia
    (`es_importante`) es el que de verdad descarta el ruido informativo.
    """
    if "requires_action" in event:
        return bool(event["requires_action"])
    return True


def ya_reportado(event: Mapping[str, Any]) -> bool:
    """¿Ya se reportó? (pregunta 5). True solo si el evento lo declara."""
    valor = event.get("reportado")
    return valor is True


def should_notify(event: dict[str, Any]) -> bool:
    """Decide si un evento merece una notificación proactiva (PHASE2 §54).

    Es la conjunción de las cinco preguntas del filtro. Determinista y sin
    LLM: mismo evento → misma decisión.
    """
    return (
        es_importante(event)
        and es_nuevo(event)
        and le_importa_al_usuario(event)
        and requiere_accion(event)
        and not ya_reportado(event)
    )


def suggest_automation_from_event(event: Mapping[str, Any]) -> dict[str, Any] | None:
    """Propone revisar una automatización fallida, sin mutar configuración."""
    if _tipo(event) != "automation_failed":
        return None
    failures = event.get("failure_count")
    automation_id = event.get("automation_id") or event.get("id")
    if isinstance(failures, bool) or not isinstance(failures, int) or failures < 3:
        return None
    if not automation_id or event.get("silenciado") is True:
        return None
    return {
        "kind": "automation_suggestion",
        "action": "review_automation",
        "automation_id": str(automation_id),
        "failure_count": failures,
        "requires_user_confirmation": True,
        "reason": (
            "La automatización falló varias veces consecutivas; revisa su conexión o condición."
        ),
    }


def suprimir_duplicado(
    historial: dict[str, datetime],
    event: Mapping[str, Any],
    *,
    now: datetime | None = None,
    ventana: timedelta = VENTANA_DUPLICADOS,
) -> bool:
    """Suprime eventos repetidos dentro de una ventana de tiempo (PHASE2 §55).

    ``historial`` es un mapeo mutado in-place que asocia la clave de dedup de
    cada evento a la última vez que se vio. Devuelve ``True`` si el evento es
    un duplicado reciente (debe suprimirse) y ``False`` si es nuevo o no tiene
    clave (sin clave no se puede deduplicar, así que se deja pasar).

    Ejemplo:

        historial: dict[str, datetime] = {}
        suprimir_duplicado(historial, ev)   # False (primera vez)
        suprimir_duplicado(historial, ev)   # True (duplicado reciente)
    """
    clave = _clave(event)
    if not clave:
        return False
    ahora = now or datetime.now(UTC)
    visto = historial.get(clave)
    if visto is not None and (ahora - visto) <= ventana:
        historial[clave] = ahora
        return True
    historial[clave] = ahora
    return False


__all__ = [
    "IMPORTANCIA_MINIMA",
    "VENTANA_DUPLICADOS",
    "VENTANA_NOVEDAD",
    "es_importante",
    "es_nuevo",
    "le_importa_al_usuario",
    "requiere_accion",
    "score_event",
    "should_notify",
    "suggest_automation_from_event",
    "suprimir_duplicado",
    "ya_reportado",
]
