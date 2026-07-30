"""`edecan_evals` — suites de evaluación del agente (WP-15).

Ver `ARCHITECTURE.md` §10.6, §10.7 y `PLAN.md`. Submódulos:

- `edecan_evals.schema` — modelos Pydantic de una suite (`Suite`, `Caso`,
  `Esperado`, `GuionEntry`).
- `edecan_evals.loader` — carga de suites YAML desde `packages/evals/suites/`.
- `edecan_evals.fakes` — `FakeLLMProvider`, doble determinista de
  `edecan_llm.LLMProvider`.
- `edecan_evals.judge` — rúbrica de juez LLM (alias `"rapido"`), solo para
  `--live`.
- `edecan_evals.runner` — orquestación (`ejecutar_caso`/`ejecutar_suite`),
  evaluación (`evaluar_caso`) y el CLI (`python -m edecan_evals.run`).

Ninguno de estos submódulos importa `edecan_core` a nivel de módulo (ver el
docstring de `edecan_evals.runner`, sección "Imports diferidos"), así que
`import edecan_evals` funciona siempre, incluso antes de que ese paquete
hermano exista en el workspace.
"""

from __future__ import annotations

from edecan_evals.fakes import FakeLLMProvider
from edecan_evals.judge import VeredictoJuez, evaluar_tono_persona
from edecan_evals.loader import cargar_suite, cargar_todas, listar_suites
from edecan_evals.runner import (
    ResultadoCaso,
    ResultadoSuite,
    ejecutar_caso,
    ejecutar_suite,
    evaluar_caso,
    main,
)
from edecan_evals.schema import (
    NOMBRES_HERRAMIENTAS_TOOLKIT,
    Caso,
    Esperado,
    GuionEntry,
    Suite,
)

__all__ = [
    "NOMBRES_HERRAMIENTAS_TOOLKIT",
    "Caso",
    "Esperado",
    "FakeLLMProvider",
    "GuionEntry",
    "ResultadoCaso",
    "ResultadoSuite",
    "Suite",
    "VeredictoJuez",
    "cargar_suite",
    "cargar_todas",
    "ejecutar_caso",
    "ejecutar_suite",
    "evaluar_caso",
    "evaluar_tono_persona",
    "listar_suites",
    "main",
]
