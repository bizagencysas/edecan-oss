"""Máquina de estados de una sesión de entrenamiento.

`WorkoutSession` modela el ciclo de vida de una sesión (planned → active →
paused → completed / cancelled) con transiciones explícitas que lanzan
`ValueError` ante estados ilegales. Módulo puro: sin base de datos, HTTP ni
imports de `apps/`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from .plan import WorkoutPlan

ESTADOS = ("planned", "active", "paused", "completed", "cancelled")


@dataclass(frozen=True)
class SerieRegistrada:
    """Una serie efectivamente realizada dentro de una sesión."""

    ejercicio_idx: int
    repeticiones: int
    peso_kg: float | None = None
    en: str = ""

    def to_dict(self) -> dict:
        return {
            "ejercicio_idx": self.ejercicio_idx,
            "repeticiones": self.repeticiones,
            "peso_kg": self.peso_kg,
            "en": self.en,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SerieRegistrada:
        return cls(
            ejercicio_idx=int(d["ejercicio_idx"]),
            repeticiones=int(d["repeticiones"]),
            peso_kg=d.get("peso_kg"),
            en=d.get("en", ""),
        )


def _timestamp(now: str | None) -> str:
    """ISO-8601 de `now` si viene dado; si no, el instante actual en UTC."""
    if now is not None:
        return now
    return datetime.now(UTC).isoformat()


class WorkoutSession:
    """Sesión de entrenamiento ligada a un `WorkoutPlan`.

    Transiciones válidas: `iniciar` (planned|paused → active, guarda
    `started_at` si falta), `pausar` (active → paused), `reanudar`
    (paused → active), `terminar` (active|paused → completed) y `cancelar`
    (cualquier estado → cancelled). `registrar_serie` solo se permite en
    active|paused.
    """

    def __init__(
        self,
        plan: WorkoutPlan,
        *,
        estado: str = "planned",
        started_at: str | None = None,
        series: list[SerieRegistrada] | None = None,
    ) -> None:
        if estado not in ESTADOS:
            raise ValueError(f"estado inválido: {estado!r}")
        self.plan = plan
        self._estado = estado
        self.started_at = started_at
        self.series = list(series or [])

    @property
    def estado(self) -> str:
        return self._estado

    def iniciar(self, now: str | None = None) -> None:
        if self._estado not in ("planned", "paused"):
            raise ValueError(f"no se puede iniciar desde el estado {self._estado!r}")
        if self.started_at is None:
            self.started_at = _timestamp(now)
        self._estado = "active"

    def pausar(self, now: str | None = None) -> None:
        if self._estado != "active":
            raise ValueError(f"no se puede pausar desde el estado {self._estado!r}")
        self._estado = "paused"

    def reanudar(self, now: str | None = None) -> None:
        if self._estado != "paused":
            raise ValueError(f"no se puede reanudar desde el estado {self._estado!r}")
        self._estado = "active"

    def registrar_serie(
        self,
        ejercicio_idx: int,
        repeticiones: int,
        peso_kg: float | None = None,
        now: str | None = None,
    ) -> None:
        if self._estado not in ("active", "paused"):
            raise ValueError(f"no se puede registrar una serie en estado {self._estado!r}")
        if not 0 <= ejercicio_idx < len(self.plan.ejercicios):
            raise ValueError(f"ejercicio_idx {ejercicio_idx!r} fuera de rango")
        if type(repeticiones) is not int or repeticiones <= 0:
            raise ValueError("repeticiones debe ser un entero positivo")
        self.series.append(
            SerieRegistrada(
                ejercicio_idx=ejercicio_idx,
                repeticiones=repeticiones,
                peso_kg=peso_kg,
                en=_timestamp(now),
            )
        )

    def terminar(self, now: str | None = None) -> None:
        if self._estado not in ("active", "paused"):
            raise ValueError(f"no se puede terminar desde el estado {self._estado!r}")
        self._estado = "completed"

    def cancelar(self) -> None:
        self._estado = "cancelled"

    def series_completadas(self, ejercicio_idx: int) -> int:
        return sum(1 for s in self.series if s.ejercicio_idx == ejercicio_idx)

    def resumen(self) -> dict:
        progreso = [
            {
                "idx": i,
                "series_hechas": self.series_completadas(i),
                "series_total": ejercicio.series,
            }
            for i, ejercicio in enumerate(self.plan.ejercicios)
        ]
        return {
            "estado": self._estado,
            "titulo": self.plan.titulo,
            "objetivo": self.plan.objetivo,
            "started_at": self.started_at,
            "duracion_min": self.plan.duracion_min,
            "series_total": sum(e.series for e in self.plan.ejercicios),
            "series_hechas": len(self.series),
            "progreso": progreso,
            "series": [s.to_dict() for s in self.series],
        }

    def to_dict(self) -> dict:
        return {
            "estado": self._estado,
            "started_at": self.started_at,
            "plan": self.plan.to_dict(),
            "series": [s.to_dict() for s in self.series],
        }

    @classmethod
    def from_dict(cls, d: dict, plan: WorkoutPlan) -> WorkoutSession:
        return cls(
            plan,
            estado=d.get("estado", "planned"),
            started_at=d.get("started_at"),
            series=[SerieRegistrada.from_dict(s) for s in d.get("series", [])],
        )