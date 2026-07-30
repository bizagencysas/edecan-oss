"""Criterio de `edecan-finanzas-fecha-no-texto`.

`RegistrarTransaccionTool` debe responder con un `ToolResult` explicativo
cuando `fecha` no es una cadena ISO, en vez de reventar el turno. Falla hoy:
`date.fromisoformat(20260727)` lanza `TypeError`, que no está atrapado.

La comprobación NUNCA toca la base: la sesión es un doble que revienta si
alguien la usa, así que un `ToolResult` solo puede llegar por la vía de
validación temprana.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from typing import Any

from edecan_core import ToolContext, ToolResult
from edecan_toolkit.finanzas import RegistrarTransaccionTool


class _SesionProhibida:
    """Doble de `AsyncSession` que falla si la herramienta llega a la base."""

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("la herramienta no debe llegar a la base con una fecha inválida")


def _ctx() -> ToolContext:
    return ToolContext(
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        session=_SesionProhibida(),
        settings=None,
        llm=None,
        vault=None,
        extras={},
    )


async def _correr(args: dict[str, Any]) -> ToolResult:
    return await RegistrarTransaccionTool().run(_ctx(), args)


def main() -> int:
    casos: list[dict[str, Any]] = [
        {"monto": 10, "categoria": "comida", "fecha": 20260727},
        {"monto": 10, "categoria": "comida", "fecha": ["2026-07-27"]},
        {"monto": 10, "categoria": "comida", "fecha": {"dia": 27}},
    ]
    for args in casos:
        try:
            resultado = asyncio.run(_correr(args))
        except Exception as exc:  # noqa: BLE001 - el fallo medido es justamente "lanzó"
            print(f"con fecha={args['fecha']!r} la herramienta lanzó {type(exc).__name__}: {exc}")
            return 1
        if not isinstance(resultado, ToolResult) or not resultado.content.strip():
            print(f"con fecha={args['fecha']!r} no devolvió un ToolResult con texto")
            return 1
        if "fecha" not in resultado.content.lower():
            print(f"el mensaje no habla de la fecha inválida: {resultado.content!r}")
            return 1

    print("ok: una fecha no textual devuelve un ToolResult explicativo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
