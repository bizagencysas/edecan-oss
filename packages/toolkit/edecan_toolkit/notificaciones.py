"""Herramientas de notificaciones móviles del propio usuario.

La tool no conoce APNs, FCM ni credenciales. La API inyecta un dispatcher
tenant/user-scoped que únicamente puede emitir el evento controlado
``push_test`` hacia los dispositivos activos de la persona actual.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from edecan_core import Tool, ToolContext, ToolResult

PushTestDispatcher = Callable[[], Awaitable[dict[str, Any]]]


class ProbarNotificacionesPushTool(Tool):
    """Solicita una prueba push real, sin aceptar texto libre ni destinatarios."""

    name = "probar_notificaciones_push"
    description = (
        "Envía una notificación push real de prueba únicamente a los dispositivos "
        "móviles activos del usuario actual. Úsala cuando la persona pida "
        "explícitamente probar, comprobar o enviar un push a su iPhone o Android."
    )
    category = "admin"
    risk_level = "low"
    input_schema = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }
    requires_flags = frozenset({"notifications.push"})

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        dispatcher = ctx.extras.get("push_test_dispatcher")
        if not callable(dispatcher):
            return ToolResult(
                content=(
                    "La prueba push no está disponible en este host. "
                    "Abre Edecán desde la computadora principal y vuelve a intentarlo."
                ),
                data={"queued": False},
            )

        try:
            result = await dispatcher()
        except Exception:
            return ToolResult(
                content=(
                    "No pude solicitar la prueba push. Revisa que exista una credencial "
                    "APNs o FCM y un teléfono activo con token registrado."
                ),
                data={"queued": False},
            )

        queued = bool(result.get("queued"))
        if not queued:
            return ToolResult(
                content="No se pudo encolar la prueba push en este momento.",
                data={"queued": False},
            )
        return ToolResult(
            content=(
                "Listo. Envié una notificación push de prueba a tus dispositivos "
                "móviles activos."
            ),
            data={
                "queued": True,
                "event_id": result.get("event_id"),
                "job_id": result.get("job_id"),
            },
        )


__all__ = ["ProbarNotificacionesPushTool", "PushTestDispatcher"]
