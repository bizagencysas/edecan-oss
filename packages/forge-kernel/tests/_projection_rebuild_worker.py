"""Trabajador de subproceso para el test de reconstrucción determinista de proyecciones.

Se invoca como `python -m tests._projection_rebuild_worker`, lee por stdin un JSON con una
lista de `Event` serializados (`Event.model_dump(mode="json")`), reconstruye
`SessionTimelineProjection` y `BudgetLedgerProjection` DESDE CERO con `Projection.rebuild()`, y
escribe a stdout un JSON `{"session_timeline": <state_hash>, "budget_ledger": <state_hash>}`.

Vive en un archivo propio (no una función dentro de `test_projections.py`) porque el punto del
test es ejecutar la reconstrucción en OTRO INTÉRPRETE de Python, con su propio
`PYTHONHASHSEED` — una función del proceso de test no sirve para eso, por definición.
"""

from __future__ import annotations

import json
import sys

from edecan_forge_kernel.contracts import Event
from edecan_forge_kernel.projections import BudgetLedgerProjection, SessionTimelineProjection


def main() -> None:
    crudo = json.load(sys.stdin)
    eventos = [Event.model_validate(e) for e in crudo]
    timeline = SessionTimelineProjection.rebuild(eventos)
    ledger = BudgetLedgerProjection.rebuild(eventos)
    json.dump(
        {"session_timeline": timeline.state_hash(), "budget_ledger": ledger.state_hash()},
        sys.stdout,
    )


if __name__ == "__main__":
    main()
