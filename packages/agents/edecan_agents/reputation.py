"""Reputación de perfiles de agente (PHASE2.md §76).

Hasta ahora `Orchestrator.plan` elegía perfiles a ciegas: un perfil que falló
repetidamente sobre un objetivo similar se volvía a elegir idéntico en la
siguiente misión. Este módulo guarda, por perfil, una señal de rendimiento
ligera (éxitos/fallos + latencia media) para que el planificador pueda sesgar
su elección: si un perfil viene fallando seguido sobre una "forma" de objetivo
parecida, se prefiere un alternativo.

Es deliberadamente **in-memory** (un dict de módulo): no hay tabla nueva. La
reputación es una señal de la sesión del proceso, no estado crítico — perderla
al reiniciar el worker degrada a "sin sesgo" (comportamiento exacto de antes),
que es un fallback seguro. No se persiste en `agent_missions.detalle` a
propósito: el objetivo es sesgar el plan de la MISIÓN SIGUIENTE, no auditar
una misión ya terminada, y un dict de proceso basta para eso sin acoplar el
esquema.
"""

from __future__ import annotations

# Una "forma" de objetivo es su conjunto de tokens de 4+ letras (mismo criterio
# de `merge._TOKEN_RE`): dos objetivos sobre "crédito hipotecario" comparten
# tokens aunque el texto exacto difiera. La reputación se indexa por perfil y
# se consulta por la forma del objetivo nuevo.
import re
from dataclasses import dataclass, field
from typing import Any

_TOKEN_RE = re.compile(r"[A-Za-zÁÉÍÓÚáéíóúÑñ]{4,}")


def _forma(objetivo: str) -> frozenset[str]:
    if not objetivo:
        return frozenset()
    return frozenset(m.lower() for m in _TOKEN_RE.findall(objetivo))


@dataclass
class _PerfilStats:
    exitos: int = 0
    fallos: int = 0
    latencia_total: float = 0.0
    formas: dict[frozenset[str], int] = field(default_factory=dict)
    """Conteo de éxitos POR forma de objetivo (para `best_profile_for`)."""


_reputacion: dict[str, _PerfilStats] = {}


def _stats(perfil: str) -> _PerfilStats:
    return _reputacion.setdefault(perfil, _PerfilStats())


def record_step_outcome(perfil: str, *, success: bool, duration_s: float) -> None:
    """Registra el resultado de un paso para un perfil.

    Nunca lanza: la reputación es una señal de mejora, jamás puede tumbar un
    paso ya terminado."""
    try:
        s = _stats(perfil)
        if success:
            s.exitos += 1
        else:
            s.fallos += 1
        s.latencia_total += max(0.0, duration_s or 0.0)
    except Exception:  # noqa: BLE001 - señal best-effort
        pass


def record_objective_outcome(perfil: str, objetivo: str, *, success: bool) -> None:
    """Además del conteo global, registra el resultado por la FORMA del
    objetivo, para poder sesgar `best_profile_for`."""
    try:
        s = _stats(perfil)
        forma = _forma(objetivo)
        if forma:
            key = forma
            actual = s.formas.get(key, 0)
            s.formas[key] = actual + (1 if success else -1)
    except Exception:  # noqa: BLE001
        pass


def _tasa_fallo(perfil: str) -> float | None:
    s = _reputacion.get(perfil)
    if s is None:
        return None
    total = s.exitos + s.fallos
    if total < 3:
        return None  # poca muestra: no sesgar
    return s.fallos / total


def best_profile_for(objetivo: str, candidatos: list[str]) -> str | None:
    """Devuelve el perfil con mejor historial para la FORMA del objetivo, o
    `None` si no hay señal suficiente (el caller conserva su default).

    Criterio: entre `candidatos`, el que tenga mayor saldo de éxito para la
    forma del objetivo nuevo (éxitos - fallos) y, en caso de empate, mayor
    saldo global. Si ninguno tiene historia para esa forma, devuelve `None`
    (no sesgar sin evidencia)."""
    try:
        forma = _forma(objetivo)
        if not forma or not candidatos:
            return None
        puntajes: list[tuple[int, int, str]] = []
        for perfil in candidatos:
            s = _reputacion.get(perfil)
            if s is None:
                continue
            saldo_forma = s.formas.get(forma, 0)
            if saldo_forma == 0:
                continue
            saldo_global = s.exitos - s.fallos
            puntajes.append((saldo_forma, saldo_global, perfil))
        if not puntajes:
            return None
        # Max por (saldo_forma, saldo_global); desempate estable por nombre.
        return max(puntajes, key=lambda p: (p[0], p[1], p[2]))[2]
    except Exception:  # noqa: BLE001
        return None


def snapshot() -> dict[str, dict[str, Any]]:
    """Vista read-only del estado (para tests/depuración)."""
    return {
        perfil: {"exitos": s.exitos, "fallos": s.fallos, "latencia_total": s.latencia_total}
        for perfil, s in _reputacion.items()
    }


def reset() -> None:
    """Limpia el estado (solo tests)."""
    _reputacion.clear()


__all__ = [
    "record_step_outcome",
    "record_objective_outcome",
    "best_profile_for",
    "snapshot",
    "reset",
]
