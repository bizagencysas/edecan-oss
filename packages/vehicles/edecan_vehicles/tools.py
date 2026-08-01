"""Las 2 herramientas de vehículos (`ARCHITECTURE.md` §13, `DIRECCION_ACTUAL.md`,
`ROADMAP_V2.md` §6.3 — WP-V4-08): `vehiculo_estado`, `vehiculo_controlar`.

## Flag de plan `tools.vehicles`

Ambas tools exigen `requires_flags = frozenset({FLAG_TOOLS_VEHICLES})`
(`edecan_schemas.plans.FLAG_TOOLS_VEHICLES = "tools.vehicles"`, pinned en
`ARCHITECTURE.md` §13/§10.13, dueño WP-V4-01: `True` en
`free_selfhost`/`hosted_pro`/`hosted_business`, `False` en `hosted_basic`).
Importar la constante (en vez de repetir el string a mano, como hizo alguna
vez `apps/api/edecan_api/routers/remote.py` con `companion.remote_view`
antes de que su flag aterrizara en `PLANES`) es seguro acá porque
`edecan_schemas` ya es dependencia TRANSITIVA real de este paquete
(`edecan-core`, la única dependencia propia declarada en `pyproject.toml`,
depende a su vez de `edecan-schemas` — ver `edecan_core.agent`) — mismo
criterio que ya usa `apps/api/edecan_api/routers/commerce.py` con
`FLAG_COMMERCE_ORDERS` una vez que su flag quedó pinned.

## `vehiculo_estado` es "lista o detalle" según si mandas `vehicle_id`

El work package solo nombra 2 tools (no una tercera "listar vehículos"), así
que `vehiculo_estado` cubre las dos preguntas de solo-lectura con UN
argumento opcional: sin `vehicle_id`, lista los vehículos conectados (usa
`VehicleProvider.list_vehicles()`); con `vehicle_id`, da el detalle de ESE
vehículo (`VehicleProvider.estado()`). Los ids de Smartcar son opacos (UUIDs
que el usuario nunca ve en ningún lado), así que el modelo NECESITA una
forma de descubrirlos antes de poder pedir un estado puntual — sin el modo
"lista", `vehiculo_estado` sería inalcanzable en la práctica.

## Modo demo, siempre disponible

`providers.get_tenant_vehicle_provider(ctx)` YA resuelve "tenant → stub"
(`providers.py`): a diferencia de `edecan_smarthome` (que no tiene stub y en
su lugar devuelve un `ToolResult` explicando cómo conectar), acá SIEMPRE hay
un proveedor utilizable — el modo demo, con 1 vehículo de ejemplo — así que
estas tools nunca necesitan un mensaje de "todavía no conectaste nada": el
modo demo responde igual, y el propio texto de la respuesta avisa cuando el
vehículo consultado es el de demo (`_aviso_demo`).
"""

from __future__ import annotations

from typing import Any

from edecan_core import Tool, ToolContext, ToolResult
from edecan_schemas.plans import FLAG_TOOLS_VEHICLES

from .providers import (
    StubVehiclesProvider,
    VehicleProvider,
    VehicleProviderError,
    get_tenant_vehicle_provider,
)

_ACCIONES_VALIDAS = ("bloquear", "desbloquear")


def _aviso_demo(provider: VehicleProvider) -> str:
    if isinstance(provider, StubVehiclesProvider):
        return (
            " (modo demo — conecta tu cuenta de Smartcar en Configuración → Vehículos para "
            "ver tus vehículos reales)"
        )
    return ""


def _formatear_vehiculo(v: dict[str, Any]) -> str:
    campos = (str(v[k]) for k in ("marca", "modelo", "anio") if v.get(k))
    etiqueta = " ".join(campos) or "(sin datos de marca/modelo)"
    return f"- {v['id']}: {etiqueta}"


def _formatear_campo_porcentaje(nombre: str, campo: dict[str, Any] | None) -> str | None:
    if not campo or campo.get("porcentaje") is None:
        return None
    texto = f"{nombre} {campo['porcentaje']:g}%"
    autonomia = campo.get("autonomia_km")
    if autonomia is not None:
        texto += f" (autonomía ~{autonomia:.0f} km)"
    return texto


def _formatear_estado(vehicle_id: str, estado: dict[str, Any]) -> str:
    partes = [
        p
        for p in (
            _formatear_campo_porcentaje("Batería", estado.get("bateria")),
            _formatear_campo_porcentaje("Combustible", estado.get("combustible")),
        )
        if p
    ]
    if estado.get("odometro") is not None:
        partes.append(f"odómetro {estado['odometro']:.1f} km")
    ubicacion = estado.get("ubicacion")
    if ubicacion and ubicacion.get("lat") is not None and ubicacion.get("lon") is not None:
        partes.append(f"ubicación ({ubicacion['lat']:.4f}, {ubicacion['lon']:.4f})")

    if not partes:
        return (
            f"No obtuve ningún dato de estado del vehículo «{vehicle_id}» — tu vehículo o cuenta "
            "puede no exponer esa información."
        )
    return f"Vehículo «{vehicle_id}»: " + ", ".join(partes) + "."


class VehiculoEstadoTool(Tool):
    """Lista los vehículos conectados o da el estado de uno en concreto."""

    name = "vehiculo_estado"
    description = (
        "Consulta tus vehículos conectados. Sin argumentos, lista tus vehículos (id, marca, "
        "modelo, año). Con 'vehicle_id', da el estado de ESE vehículo (batería, combustible, "
        "odómetro, ubicación — según lo que tu marca/vehículo exponga, no todas exponen todo). "
        "Solo lectura, nunca cambia nada."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "vehicle_id": {
                "type": "string",
                "description": (
                    "id del vehículo a consultar. Déjalo vacío para listar tus vehículos "
                    "conectados y ver sus ids."
                ),
            },
        },
    }
    requires_flags = frozenset({FLAG_TOOLS_VEHICLES})

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        provider = await get_tenant_vehicle_provider(ctx)
        vehicle_id = str(args.get("vehicle_id") or "").strip()

        if not vehicle_id:
            try:
                vehiculos = await provider.list_vehicles()
            except VehicleProviderError as exc:
                return ToolResult(content=str(exc))
            if not vehiculos:
                return ToolResult(
                    content=(
                        "No tienes ningún vehículo conectado todavía. Conéctalo en "
                        "Configuración → Vehículos."
                    ),
                    data={"vehiculos": []},
                )
            lineas = [_formatear_vehiculo(v) for v in vehiculos]
            contenido = "Tus vehículos:\n" + "\n".join(lineas) + _aviso_demo(provider)
            return ToolResult(content=contenido, data={"vehiculos": vehiculos})

        try:
            estado = await provider.estado(vehicle_id)
        except VehicleProviderError as exc:
            return ToolResult(content=str(exc))
        contenido = _formatear_estado(vehicle_id, estado) + _aviso_demo(provider)
        return ToolResult(content=contenido, data={"vehicle_id": vehicle_id, "estado": estado})


class VehiculoControlarTool(Tool):
    """Bloquea/desbloquea las puertas de un vehículo — acción física real."""

    name = "vehiculo_controlar"
    description = (
        "Bloquea o desbloquea las puertas de uno de tus vehículos conectados. Es una acción "
        "física real sobre tu auto: requiere tu confirmación explícita antes de ejecutarse. "
        "Argumentos: vehicle_id (usa 'vehiculo_estado' sin argumentos para verlo) y accion "
        "('bloquear' o 'desbloquear')."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "vehicle_id": {"type": "string", "description": "id del vehículo a controlar."},
            "accion": {
                "type": "string",
                "description": "'bloquear' o 'desbloquear' — ninguna otra acción está soportada.",
            },
        },
        "required": ["vehicle_id", "accion"],
    }
    requires_flags = frozenset({FLAG_TOOLS_VEHICLES})
    dangerous = True

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        vehicle_id = str(args.get("vehicle_id", "")).strip()
        accion = str(args.get("accion", "")).strip().lower()
        if not vehicle_id:
            return ToolResult(content="Necesito el id del vehículo a controlar.")
        if accion not in _ACCIONES_VALIDAS:
            return ToolResult(
                content=(
                    f"No entendí la acción «{args.get('accion')}». Usa 'bloquear' o "
                    "'desbloquear' — ninguna otra acción está soportada."
                )
            )

        provider = await get_tenant_vehicle_provider(ctx)
        try:
            resultado = await provider.controlar_puertas(vehicle_id, accion)
        except VehicleProviderError as exc:
            return ToolResult(content=str(exc))

        verbo = "Bloqueé" if accion == "bloquear" else "Desbloqueé"
        contenido = f"{verbo} las puertas del vehículo «{vehicle_id}»." + _aviso_demo(provider)
        return ToolResult(content=contenido, data=resultado)


def get_all_tools() -> list[Tool]:
    """Entry point `edecan.tools` (ver `pyproject.toml` y `ToolRegistry.load_entry_points`)."""
    return [VehiculoEstadoTool(), VehiculoControlarTool()]
