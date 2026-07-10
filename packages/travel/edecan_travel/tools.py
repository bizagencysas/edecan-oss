"""Las 5 herramientas de `edecan_travel` (nombres exactos, `ARCHITECTURE.md` §14,
WP-V5-09): `buscar_vuelos`, `buscar_hoteles`, `estado_vuelo`, `rastrear_paquete` (todas
de solo lectura) y `preparar_reserva` (`dangerous=True`).

Las 5 requieren el flag de plan `tools.travel` — usado aquí como string local
(`_FLAG_TRAVEL`, no importado de `edecan_schemas.plans`) por el MISMO motivo exacto que
`edecan_ads.tools._FLAG_ADS`: este paquete no declara `edecan-schemas` como dependencia
(solo `edecan-core`/`httpx`/`sqlalchemy`, ver `pyproject.toml`). La cadena coincide con
la que usa el router (`apps/api/edecan_api/routers/viajes.py`, que sí importa el flag
—con guardia, ver su docstring— porque `edecan_api` sí depende de `edecan-schemas`).

`preparar_reserva` es el ÚNICO lugar de este paquete donde una tool `dangerous=True`
toca la base de datos, y lo único que hace es un `INSERT` en la tabla `orders` YA
EXISTENTE (`kind='purchase'`, `status='draft'`) — JAMÁS llama a Amadeus ni a
`edecan_travel.providers` (ver `docs/viajes.md`, "guardrail de dinero"): reservar de
verdad es, siempre, una decisión y una acción humana fuera de Edecán por completo.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from typing import Any

from edecan_core import Tool, ToolContext, ToolResult
from sqlalchemy import text

_FLAG_TRAVEL = "tools.travel"
_MONEDA_DEFECTO = "USD"
_ADULTOS_DEFECTO = 1
_MAX_RESULTADOS_DEFECTO = 10
_TIPOS_RESERVA = ("vuelo", "hotel")

# Mensaje pinned EXACTO en el paquete de trabajo — no reformular.
_MENSAJE_RESERVA_CREADA = (
    "Borrador creado en Órdenes. Nada está reservado ni pagado: revisa y decide tú; "
    "la compra real la haces con la aerolínea/hotel."
)


def _texto(args: dict[str, Any], campo: str) -> str:
    return str(args.get(campo) or "").strip()


def _parse_monto(valor: Any) -> Decimal | None:
    if valor is None or valor == "":
        return None
    try:
        return Decimal(str(valor))
    except InvalidOperation:
        return None


def _parse_entero(valor: Any, defecto: int) -> int:
    if valor is None or valor == "":
        return defecto
    try:
        entero = int(valor)
    except (TypeError, ValueError):
        return defecto
    return entero if entero > 0 else defecto


class BuscarVuelosTool(Tool):
    name = "buscar_vuelos"
    description = (
        "Busca ofertas de vuelo entre dos aeropuertos (Amadeus). Solo consulta — no "
        "reserva ni paga nada. Si el tenant no conectó su cuenta de Amadeus (PUT "
        "/v1/viajes/credentials), muestra ejemplos de demostración en modo offline."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "origen": {
                "type": "string",
                "description": "Código IATA del aeropuerto de origen, p. ej. 'BOG'.",
            },
            "destino": {
                "type": "string",
                "description": "Código IATA del aeropuerto de destino, p. ej. 'MIA'.",
            },
            "fecha": {"type": "string", "description": "Fecha de salida en formato AAAA-MM-DD."},
            "adultos": {
                "type": "integer",
                "description": "Número de pasajeros adultos.",
                "default": 1,
            },
            "max_resultados": {
                "type": "integer",
                "description": "Máximo de ofertas a devolver.",
                "default": 10,
            },
        },
        "required": ["origen", "destino", "fecha"],
    }
    requires_flags = frozenset({_FLAG_TRAVEL})

    def __init__(self, *, provider_resolver: Any = None) -> None:
        # Patrón inyectable (mismo criterio que `AdsResumenTool` de `edecan_ads.tools`):
        # por defecto resuelve el proveedor bring-your-own real; los tests pueden
        # sustituirlo por un doble sin tocar `ctx.vault`/`ctx.session`.
        from .providers import get_tenant_travel_provider

        self._resolver = provider_resolver or get_tenant_travel_provider

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        origen = _texto(args, "origen").upper()
        destino = _texto(args, "destino").upper()
        fecha = _texto(args, "fecha")
        if not origen or not destino:
            return ToolResult(
                content="Necesito el código IATA de origen y destino (p. ej. 'BOG', 'MIA')."
            )
        if not fecha:
            return ToolResult(content="Necesito la fecha de salida (AAAA-MM-DD).")
        adultos = _parse_entero(args.get("adultos"), _ADULTOS_DEFECTO)
        max_resultados = _parse_entero(args.get("max_resultados"), _MAX_RESULTADOS_DEFECTO)

        provider = await self._resolver(ctx)
        try:
            ofertas = await provider.buscar_vuelos(
                origen, destino, fecha, adultos=adultos, max_resultados=max_resultados
            )
        except Exception as exc:
            return ToolResult(content=f"No pude buscar vuelos: {exc}")

        if not ofertas:
            return ToolResult(
                content=f"No encontré vuelos de {origen} a {destino} el {fecha}.",
                data={"ofertas": []},
            )

        lineas = [
            f"- [{o.id}] {o.aerolinea}: {o.origen or origen} {o.salida or '?'} → "
            f"{o.destino or destino} {o.llegada or '?'} ({o.escalas} escala(s)) — "
            f"{o.moneda} {o.precio_total}"
            for o in ofertas
        ]
        return ToolResult(
            content=f"Vuelos {origen} → {destino} el {fecha}:\n" + "\n".join(lineas),
            data={"ofertas": [asdict(o) for o in ofertas]},
        )


class BuscarHotelesTool(Tool):
    name = "buscar_hoteles"
    description = (
        "Busca ofertas de hotel en una ciudad para un rango de fechas (Amadeus). Solo "
        "consulta — no reserva ni paga nada. Si el tenant no conectó su cuenta de "
        "Amadeus, muestra ejemplos de demostración en modo offline."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "ciudad": {
                "type": "string",
                "description": "Código IATA de la ciudad, p. ej. 'PAR', 'NYC'.",
            },
            "checkin": {"type": "string", "description": "Fecha de entrada en formato AAAA-MM-DD."},
            "checkout": {"type": "string", "description": "Fecha de salida en formato AAAA-MM-DD."},
            "adultos": {
                "type": "integer",
                "description": "Número de huéspedes adultos.",
                "default": 1,
            },
        },
        "required": ["ciudad", "checkin", "checkout"],
    }
    requires_flags = frozenset({_FLAG_TRAVEL})

    def __init__(self, *, provider_resolver: Any = None) -> None:
        from .providers import get_tenant_travel_provider

        self._resolver = provider_resolver or get_tenant_travel_provider

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        ciudad = _texto(args, "ciudad").upper()
        checkin = _texto(args, "checkin")
        checkout = _texto(args, "checkout")
        if not ciudad:
            return ToolResult(
                content="Necesito el código IATA de la ciudad (p. ej. 'PAR', 'NYC')."
            )
        if not checkin or not checkout:
            return ToolResult(content="Necesito las fechas de check-in y check-out (AAAA-MM-DD).")
        adultos = _parse_entero(args.get("adultos"), _ADULTOS_DEFECTO)

        provider = await self._resolver(ctx)
        try:
            ofertas = await provider.buscar_hoteles(ciudad, checkin, checkout, adultos=adultos)
        except Exception as exc:
            return ToolResult(content=f"No pude buscar hoteles: {exc}")

        if not ofertas:
            return ToolResult(
                content=f"No encontré hoteles en {ciudad} para esas fechas.", data={"ofertas": []}
            )

        lineas = [
            f"- [{o.id}] {o.nombre}"
            + (f" ({o.rating}★)" if o.rating else "")
            + f" — {o.moneda} {o.precio_total}"
            for o in ofertas
        ]
        return ToolResult(
            content=f"Hoteles en {ciudad} ({checkin} → {checkout}):\n" + "\n".join(lineas),
            data={"ofertas": [asdict(o) for o in ofertas]},
        )


class EstadoVueloTool(Tool):
    name = "estado_vuelo"
    description = (
        "Consulta los horarios programados de un vuelo específico (Amadeus): "
        "aerolínea, número, fecha. Solo información — no modifica ninguna reserva."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "carrier": {
                "type": "string",
                "description": "Código IATA de la aerolínea, p. ej. 'AV', 'BA'.",
            },
            "numero": {"type": "string", "description": "Número de vuelo, p. ej. '123'."},
            "fecha": {
                "type": "string",
                "description": "Fecha programada de salida en formato AAAA-MM-DD.",
            },
        },
        "required": ["carrier", "numero", "fecha"],
    }
    requires_flags = frozenset({_FLAG_TRAVEL})

    def __init__(self, *, provider_resolver: Any = None) -> None:
        from .providers import get_tenant_travel_provider

        self._resolver = provider_resolver or get_tenant_travel_provider

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        carrier = _texto(args, "carrier").upper()
        numero = _texto(args, "numero")
        fecha = _texto(args, "fecha")
        if not carrier or not numero:
            return ToolResult(content="Necesito la aerolínea (código IATA) y el número de vuelo.")
        if not fecha:
            return ToolResult(content="Necesito la fecha programada de salida (AAAA-MM-DD).")

        provider = await self._resolver(ctx)
        try:
            estado = await provider.estado_vuelo(carrier, numero, fecha)
        except Exception as exc:
            return ToolResult(
                content=f"No pude consultar el estado del vuelo {carrier}{numero}: {exc}"
            )

        detalle = f"Vuelo {estado.carrier}{estado.numero} del {estado.fecha}: "
        detalle += f"{estado.origen or '?'} {estado.salida_programada or 'sin horario'} → "
        detalle += f"{estado.destino or '?'} {estado.llegada_programada or 'sin horario'}."
        if estado.terminal_salida:
            detalle += f" Terminal de salida: {estado.terminal_salida}."
        if estado.puerta_salida:
            detalle += f" Puerta: {estado.puerta_salida}."
        return ToolResult(content=detalle, data=asdict(estado))


class RastrearPaqueteTool(Tool):
    name = "rastrear_paquete"
    description = (
        "Rastrea un paquete/envío por su número de guía (AfterShip): estado actual e "
        "historial de checkpoints. Si el tenant no conectó su cuenta de AfterShip, "
        "muestra un ejemplo de demostración en modo offline."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "tracking_number": {
                "type": "string",
                "description": "Número de guía/tracking del envío.",
            },
            "courier_slug": {
                "type": "string",
                "description": (
                    "Identificador de la empresa de envío (p. ej. 'dhl', 'fedex', 'ups'). "
                    "Opcional: si se omite, se intenta detectar automáticamente."
                ),
            },
        },
        "required": ["tracking_number"],
    }
    requires_flags = frozenset({_FLAG_TRAVEL})

    def __init__(self, *, provider_resolver: Any = None) -> None:
        from .providers import get_tenant_tracking_provider

        self._resolver = provider_resolver or get_tenant_tracking_provider

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        tracking_number = _texto(args, "tracking_number")
        if not tracking_number:
            return ToolResult(content="Necesito el número de guía/tracking del envío.")
        courier_slug = _texto(args, "courier_slug") or None

        provider = await self._resolver(ctx)
        try:
            rastreo = await provider.rastrear(tracking_number, courier_slug)
        except Exception as exc:
            return ToolResult(content=f"No pude rastrear el paquete '{tracking_number}': {exc}")

        contenido = f"Estado: {rastreo.estado}"
        if rastreo.courier:
            contenido += f" (empresa: {rastreo.courier})"
        if rastreo.entrega_estimada:
            contenido += f"\nEntrega estimada: {rastreo.entrega_estimada}"
        if rastreo.checkpoints:
            lineas = [
                f"- {cp.fecha or '(sin fecha)'}: {cp.mensaje}"
                + (f" ({cp.lugar})" if cp.lugar else "")
                for cp in rastreo.checkpoints
            ]
            contenido += "\n\nHistorial:\n" + "\n".join(lineas)
        return ToolResult(content=contenido, data=asdict(rastreo))


async def _crear_reserva_draft(
    session: Any,
    *,
    tenant_id: Any,
    user_id: Any,
    descripcion: str,
    monto: Decimal | None,
    moneda: str,
    meta: dict[str, Any],
) -> Any:
    """`INSERT` directo en la tabla `orders` YA EXISTENTE con `kind='purchase'` y
    `status='draft'` — lo ÚNICO que hace `preparar_reserva`. No hay ninguna otra
    sentencia SQL en esta función y nunca se llama a Amadeus desde aquí (ver el
    docstring del módulo). Mismas columnas que usa `packages/commerce/edecan_commerce/
    tools.py::_crear_orden_draft` para sus propios borradores."""
    row = (
        await session.execute(
            text(
                "INSERT INTO orders "
                "(tenant_id, user_id, kind, status, descripcion, monto, moneda, meta) "
                "VALUES (:tenant_id ::uuid, :user_id ::uuid, 'purchase', 'draft', :descripcion, "
                ":monto, :moneda, CAST(:meta AS jsonb)) "
                "RETURNING id"
            ),
            {
                "tenant_id": str(tenant_id),
                "user_id": str(user_id),
                "descripcion": descripcion,
                "monto": monto,
                "moneda": moneda,
                "meta": json.dumps(meta),
            },
        )
    ).mappings().first()
    await session.flush()
    if row is None:  # defensivo: Postgres no devolvió la fila recién insertada.
        raise RuntimeError("No se pudo crear el borrador de reserva.")
    return row["id"]


class PrepararReservaTool(Tool):
    name = "preparar_reserva"
    description = (
        "Crea un BORRADOR de reserva (vuelo u hotel) en la página de Órdenes, a partir "
        "de una oferta encontrada con buscar_vuelos/buscar_hoteles. NO reserva ni paga "
        "nada real: Edecán nunca llama a ninguna API de compra de Amadeus. El usuario "
        "decide si de verdad reservar, directamente con la aerolínea/hotel."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "tipo": {
                "type": "string",
                "enum": list(_TIPOS_RESERVA),
                "description": "Qué se está reservando.",
            },
            "descripcion": {
                "type": "string",
                "description": (
                    "Descripción legible, p. ej. 'Vuelo BOG-MIA 2026-08-01, aerolínea AV'."
                ),
            },
            "monto": {
                "type": "number",
                "description": "Precio total de la oferta (mayor que cero).",
            },
            "moneda": {
                "type": "string",
                "description": "Código ISO-4217 de 3 letras.",
                "default": _MONEDA_DEFECTO,
            },
            "oferta_id": {
                "type": "string",
                "description": (
                    "El id de la oferta devuelta por buscar_vuelos/buscar_hoteles, si aplica."
                ),
            },
        },
        "required": ["tipo", "descripcion", "monto"],
    }
    requires_flags = frozenset({_FLAG_TRAVEL})
    dangerous = True

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        tipo = args.get("tipo")
        if tipo not in _TIPOS_RESERVA:
            return ToolResult(content="'tipo' debe ser 'vuelo' o 'hotel'.")
        descripcion = _texto(args, "descripcion")
        if not descripcion:
            return ToolResult(content="Necesito una descripción legible de la reserva.")
        monto = _parse_monto(args.get("monto"))
        if monto is None:
            return ToolResult(content=f"'{args.get('monto')}' no es un monto válido.")
        if monto <= 0:
            return ToolResult(content="El monto debe ser mayor que cero.")
        moneda = str(args.get("moneda") or _MONEDA_DEFECTO).strip().upper()
        if len(moneda) != 3 or not moneda.isalpha():
            moneda = _MONEDA_DEFECTO
        oferta_id = _texto(args, "oferta_id")

        meta: dict[str, Any] = {"tipo": tipo, "oferta": {"id": oferta_id} if oferta_id else {}}
        draft_id = await _crear_reserva_draft(
            ctx.session,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            descripcion=descripcion,
            monto=monto,
            moneda=moneda,
            meta=meta,
        )
        return ToolResult(content=_MENSAJE_RESERVA_CREADA, data={"order_id": str(draft_id)})


def get_all_tools() -> list[Tool]:
    """Instancia las 5 herramientas de viajes. Consumido por
    `edecan_travel.__init__.get_all_tools` (`try: from .tools import get_all_tools`)."""
    return [
        BuscarVuelosTool(),
        BuscarHotelesTool(),
        EstadoVueloTool(),
        RastrearPaqueteTool(),
        PrepararReservaTool(),
    ]
