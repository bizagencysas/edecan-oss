"""Helpers privados compartidos por las tools que hablan con conectores OAuth
del tenant: `agenda`, `correo` y `contenido.publicar_social` (ver
`ARCHITECTURE.md` §10.8, tabla `connector_accounts` en §10.3).

No importa `edecan_connectors` — solo necesita la clave del conector (`"google"`,
`"microsoft"`, `"meta"`, `"x"`, `"youtube"`) para consultar `connector_accounts`
y, con el `connector_account_id` resuelto, pedirle el `TokenBundle` a `ctx.vault`.
No forma parte del contrato público del paquete (por eso el prefijo `_`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from edecan_core import ToolContext, ToolResult
from sqlalchemy import text

RUTA_CONECTORES = "/app/conectores"


@dataclass(frozen=True)
class CuentaConectada:
    """Lo mínimo de una fila de `connector_accounts` que necesitan las tools."""

    connector_account_id: Any
    connector_key: str


async def buscar_cuenta_conectada(
    ctx: ToolContext, connector_keys: tuple[str, ...]
) -> CuentaConectada | None:
    """Devuelve la cuenta conectada más reciente del tenant/usuario entre
    `connector_keys` (p. ej. `("google", "microsoft")` o `("meta",)`), o
    `None` si no hay ninguna. Si hay varias, se queda con la más reciente.
    """
    if not connector_keys:
        return None

    placeholders = ", ".join(f":clave{i}" for i in range(len(connector_keys)))
    params: dict[str, Any] = {f"clave{i}": clave for i, clave in enumerate(connector_keys)}
    params["tenant_id"] = str(ctx.tenant_id)

    resultado = await ctx.session.execute(
        text(
            "SELECT id, connector_key FROM connector_accounts "
            f"WHERE tenant_id = :tenant_id AND connector_key IN ({placeholders}) "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        params,
    )
    fila = resultado.mappings().first()
    if fila is None:
        return None
    return CuentaConectada(connector_account_id=fila["id"], connector_key=fila["connector_key"])


def resultado_falta_conexion(nombre_legible: str) -> ToolResult:
    """`ToolResult` uniforme cuando falta la cuenta conectada que la tool necesita."""
    return ToolResult(
        content=(
            f"Todavía no tienes conectada una cuenta de {nombre_legible}. "
            f"Conéctala en {RUTA_CONECTORES} y vuelve a pedírmelo."
        ),
    )
