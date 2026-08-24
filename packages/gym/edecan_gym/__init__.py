"""`edecan_gym` — dominio puro del gimnasio inteligente.

Modela la generación de planes de entrenamiento (`plan.py`), la máquina de
estados de una sesión (`session.py`), el contexto de memoria/historial
(`memory.py`) y el check-in del usuario (`checkin.py`). Sin base de datos, sin
HTTP y sin imports de `apps/`: el LLM se inyecta como un callable `completar`.
"""

from __future__ import annotations

from .checkin import decidir
from .memory import contexto_para_plan
from .plan import Ejercicio, WorkoutPlan, generar_plan, prompt_collage
from .session import ESTADOS, SerieRegistrada, WorkoutSession

__all__ = [
    "ESTADOS",
    "Ejercicio",
    "SerieRegistrada",
    "WorkoutPlan",
    "WorkoutSession",
    "contexto_para_plan",
    "decidir",
    "generar_plan",
    "prompt_collage",
]