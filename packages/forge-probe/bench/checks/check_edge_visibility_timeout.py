"""Criterio de `edecan-edge-visibility-timeout`.

`_claim` reserva cada trabajo en SQS con `VisibilityTimeout=300` escrito a
mano. Una tarea del nodo residente que tarde más de cinco minutos vuelve a la
cola y se ejecuta DOS veces, sin que nadie pueda ajustarlo sin volver a
desplegar el Lambda. El valor tiene que venir del entorno, y el entorno lo
declara `template.yml`.

Falla hoy: la variable no existe y el timeout está clavado. No usa AWS:
sustituye `handler.sqs`/`handler.table` por dobles en memoria.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Any

_RAIZ = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_RAIZ))

_VARIABLE = "CLAIM_VISIBILITY_TIMEOUT_SECONDS"
_PLANTILLA = _RAIZ / "infra/aws/edge/template.yml"

os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("STATE_TABLE", "test-state")
os.environ.setdefault("JOBS_QUEUE_URL", "https://sqs.example.test/jobs")
os.environ.setdefault("SHARED_SECRET_ARN", "test-secret")


class _SqsFalso:
    def __init__(self) -> None:
        self.recibidos: list[dict[str, Any]] = []

    def receive_message(self, **kwargs: Any) -> dict[str, Any]:
        self.recibidos.append(kwargs)
        return {"Messages": []}


class _TablaFalsa:
    def get_item(self, **_kwargs: Any) -> dict[str, Any]:
        return {}

    def update_item(self, **_kwargs: Any) -> None:
        return None

    def put_item(self, **_kwargs: Any) -> None:
        return None


def _timeout_usado(valor: str | None) -> int | None:
    if valor is None:
        os.environ.pop(_VARIABLE, None)
    else:
        os.environ[_VARIABLE] = valor
    modulo = importlib.import_module("infra.aws.edge.src.handler")
    modulo = importlib.reload(modulo)
    sqs = _SqsFalso()
    modulo.sqs = sqs
    modulo.table = _TablaFalsa()
    modulo._claim({"body": "{}", "headers": {"x-edecan-installation": "casa"}})
    if not sqs.recibidos:
        return None
    return sqs.recibidos[0].get("VisibilityTimeout")


def main() -> int:
    previo = os.environ.get(_VARIABLE)
    try:
        for pedido in (45, 900):
            usado = _timeout_usado(str(pedido))
            if usado != pedido:
                print(f"con {_VARIABLE}={pedido} se reservó con VisibilityTimeout={usado}")
                return 1
        por_omision = _timeout_usado(None)
        if por_omision != 300:
            print(f"sin la variable el timeout debe seguir en 300, no {por_omision}")
            return 1
    finally:
        if previo is None:
            os.environ.pop(_VARIABLE, None)
        else:
            os.environ[_VARIABLE] = previo

    plantilla = _PLANTILLA.read_text(encoding="utf-8")
    if _VARIABLE not in plantilla:
        print(f"{_PLANTILLA.relative_to(_RAIZ)} no declara {_VARIABLE} en el entorno del Lambda")
        return 1

    print("ok: el timeout de reserva viene del entorno y está declarado en template.yml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
