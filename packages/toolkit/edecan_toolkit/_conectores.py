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
# Copy amable para el dueño (iOS: Perfil → Conectores; web: panel VPS).
RUTA_CONECTORES_UI = "Perfil → Conectores"


@dataclass(frozen=True)
class CuentaConectada:
    """Lo mínimo de una fila de `connector_accounts` que necesitan las tools."""

    connector_account_id: Any
    connector_key: str


async def token_bundle_operativo(ctx: ToolContext, connector_account_id: Any) -> bool:
    """True solo si el vault devuelve un bundle con `access_token` no vacío."""
    bundle = await ctx.vault.get(ctx.tenant_id, connector_account_id)
    if bundle is None:
        return False
    token = getattr(bundle, "access_token", None)
    return isinstance(token, str) and bool(token.strip())


async def buscar_cuenta_conectada(
    ctx: ToolContext, connector_keys: tuple[str, ...]
) -> CuentaConectada | None:
    """Devuelve la cuenta más reciente del tenant con token OAuth real en vault.

    Una fila en `connector_accounts` sin token en vault NO cuenta como conectada
    (evita prometer publicación o estado «conectado» con datos huérfanos).
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
            "ORDER BY created_at DESC"
        ),
        params,
    )
    for fila in resultado.mappings().all():
        account_id = fila["id"]
        if await token_bundle_operativo(ctx, account_id):
            return CuentaConectada(
                connector_account_id=account_id,
                connector_key=str(fila["connector_key"]),
            )
    return None


def resultado_falta_conexion(nombre_legible: str) -> ToolResult:
    """`ToolResult` uniforme cuando falta la cuenta conectada que la tool necesita."""
    return ToolResult(
        content=(
            f"Todavía no tienes conectada una cuenta de {nombre_legible}. "
            f"Cuando quieras, puedes conectarla en {RUTA_CONECTORES_UI} "
            f"(o en {RUTA_CONECTORES} en el navegador) — sin prisa."
        ),
    )
