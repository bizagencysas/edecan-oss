"""Búsqueda de viajes nativa de Edecán, independiente del modelo de IA.

El modelo decide *cuándo* llamar ``buscar_vuelos`` o ``buscar_hoteles``. Esta
capa decide *cómo* obtener datos reales: usa el cliente MCP propio de Edecán
contra conectores públicos de viajes y convierte sus respuestas a los tipos
estables del producto. Claude, Codex, Ollama, Kimi, Qwen, Grok o cualquier
proveedor OpenAI-compatible reciben exactamente la misma tool y el mismo
resultado.

Las respuestas remotas se tratan estrictamente como datos. En particular, el
conector de hoteles puede incluir texto dirigido a un modelo; este módulo no lo
reenvía ni lo interpreta como instrucciones: extrae únicamente el JSON
allowlisted de ofertas.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any
from urllib.parse import quote_plus, urlsplit

from edecan_mcp.client import MCPClient
from edecan_mcp.transport import HTTPTransport

from .amadeus import EstadoVuelo, HotelOferta, TravelError, VueloOferta

logger = logging.getLogger(__name__)

KIWI_MCP_URL = "https://mcp.kiwi.com"
SKIPLAGGED_MCP_URL = "https://mcp.skiplagged.com/mcp"
TRIVAGO_MCP_URL = "https://mcp.trivago.com/mcp"

MCPCaller = Callable[[str, str, dict[str, Any]], Awaitable[str]]


def _fecha_kiwi(valor: str) -> str:
    try:
        parsed = date.fromisoformat(valor)
    except ValueError as exc:
        raise TravelError("La fecha debe usar el formato AAAA-MM-DD.") from exc
    return parsed.strftime("%d/%m/%Y")


def _url_https(valor: Any) -> str | None:
    texto = str(valor or "").strip()
    partes = urlsplit(texto)
    if (
        partes.scheme != "https"
        or not partes.hostname
        or partes.username is not None
        or partes.password is not None
    ):
        return None
    return texto


def _monto_limpio(valor: Any) -> str:
    texto = str(valor or "").strip()
    coincidencia = re.search(r"-?[0-9][0-9.,]*", texto)
    if not coincidencia:
        return "0"
    numero = coincidencia.group(0)
    if "," in numero and "." not in numero:
        # Los proveedores usan la coma como separador de miles en montos como
        # "$1,025". Un único grupo final de tres dígitos no es decimal.
        izquierda, derecha = numero.rsplit(",", 1)
        numero = f"{izquierda}{derecha}" if len(derecha) == 3 else f"{izquierda}.{derecha}"
    else:
        numero = numero.replace(",", "")
    return numero


def _json_objeto_en_texto(texto: str) -> dict[str, Any]:
    inicio = texto.find("{")
    if inicio < 0:
        raise TravelError("El proveedor de viajes devolvió una respuesta inesperada.")
    try:
        valor, _ = json.JSONDecoder().raw_decode(texto[inicio:])
    except json.JSONDecodeError as exc:
        raise TravelError("El proveedor de viajes devolvió datos que no se pudieron leer.") from exc
    if not isinstance(valor, dict):
        raise TravelError("El proveedor de viajes devolvió una respuesta inesperada.")
    return valor


async def _llamar_mcp(url: str, tool: str, argumentos: dict[str, Any]) -> str:
    async with MCPClient(HTTPTransport(url)) as client:
        await client.initialize()
        resultado = await client.call_tool(tool, argumentos, max_chars=None)
    if resultado.startswith("Error MCP:"):
        raise TravelError(resultado.removeprefix("Error MCP:").strip())
    return resultado


class EdecanTravelProvider:
    """Proveedor real sin API key, operado por la capa de capacidades de Edecán."""

    name = "edecan_travel"
    display_name = "Kiwi, Trivago y Skiplagged"

    def __init__(self, *, mcp_caller: MCPCaller | None = None) -> None:
        self._mcp_caller = mcp_caller or _llamar_mcp

    async def buscar_vuelos(
        self,
        origen: str,
        destino: str,
        fecha: str,
        *,
        adultos: int = 1,
        max_resultados: int = 10,
    ) -> list[VueloOferta]:
        argumentos = {
            "flyFrom": origen.strip(),
            "flyTo": destino.strip(),
            "departureDate": _fecha_kiwi(fecha),
            "adults": max(1, min(int(adultos), 9)),
            "currency": "USD",
            "locale": "es",
            "sort": "price",
        }
        try:
            texto = await self._mcp_caller(KIWI_MCP_URL, "search-flight", argumentos)
            payload = _json_objeto_en_texto(texto)
            moneda = str(payload.get("currency") or "USD").upper()
            itinerarios = payload.get("itineraries") or []
            ofertas: list[VueloOferta] = []
            for indice, item in enumerate(itinerarios):
                if not isinstance(item, dict):
                    continue
                tramo = item.get("outbound")
                if not isinstance(tramo, dict):
                    continue
                segmentos = tramo.get("segments") or []
                aerolineas = sorted(
                    {
                        str(segmento.get("carrier")).upper()
                        for segmento in segmentos
                        if isinstance(segmento, dict) and segmento.get("carrier")
                    }
                )
                url = _url_https(item.get("bookingUrl"))
                ofertas.append(
                    VueloOferta(
                        id=str(item.get("id") or f"kiwi-{indice + 1}"),
                        aerolinea=" + ".join(aerolineas) or "Varias aerolíneas",
                        salida=str(tramo.get("departureTime") or "") or None,
                        llegada=str(tramo.get("arrivalTime") or "") or None,
                        origen=str(tramo.get("from") or origen).upper(),
                        destino=str(tramo.get("to") or destino).upper(),
                        escalas=max(0, int(tramo.get("stops") or 0)),
                        precio_total=_monto_limpio(item.get("price")),
                        moneda=moneda,
                        booking_url=url,
                    )
                )
                if len(ofertas) >= max(1, min(int(max_resultados), 20)):
                    break
            return ofertas
        except TravelError:
            raise
        except Exception as exc:  # el error remoto nunca filtra un traceback al chat
            logger.warning("Falló la búsqueda nativa de vuelos.", exc_info=True)
            enlace = self.enlace_vuelos(origen, destino, fecha)
            raise TravelError(
                "El buscador de vuelos no respondió en este momento. "
                f"Puedes continuar en [Google Flights]({enlace})."
            ) from exc

    async def buscar_hoteles(
        self, ciudad: str, checkin: str, checkout: str, *, adultos: int = 1
    ) -> list[HotelOferta]:
        errores: list[Exception] = []
        try:
            return await self._buscar_hoteles_trivago(ciudad, checkin, checkout, adultos)
        except Exception as exc:  # noqa: BLE001 - se intenta el segundo proveedor
            errores.append(exc)
            logger.info("Trivago no respondió; probando Skiplagged.", exc_info=True)
        try:
            return await self._buscar_hoteles_skiplagged(ciudad, checkin, checkout, adultos)
        except Exception as exc:  # noqa: BLE001 - mensaje final normalizado
            errores.append(exc)
            logger.warning("Fallaron ambos buscadores nativos de hoteles.", exc_info=True)
        enlace = self.enlace_hoteles(ciudad, checkin, checkout)
        raise TravelError(
            "Los buscadores de hoteles no respondieron en este momento. "
            f"Puedes continuar en [Google Hotels]({enlace})."
        ) from errores[-1]

    async def _buscar_hoteles_trivago(
        self, ciudad: str, checkin: str, checkout: str, adultos: int
    ) -> list[HotelOferta]:
        # Validar aquí evita enviar fechas mal formadas a cualquier tercero.
        date.fromisoformat(checkin)
        date.fromisoformat(checkout)
        texto = await self._mcp_caller(
            TRIVAGO_MCP_URL,
            "trivago-accommodation-search",
            {
                "query": ciudad,
                "arrival": checkin,
                "departure": checkout,
                "adults": max(1, min(int(adultos), 10)),
                "rooms": 1,
                "currency": "USD",
                "country": "US",
                "language": "ES_CO",
            },
        )
        envoltura = _json_objeto_en_texto(texto)
        salida = envoltura.get("output")
        items = json.loads(salida) if isinstance(salida, str) else salida
        if not isinstance(items, list):
            raise TravelError("Trivago devolvió una lista de hoteles inválida.")
        ofertas: list[HotelOferta] = []
        for item in items[:10]:
            if not isinstance(item, dict) or not item.get("accommodation_name"):
                continue
            ofertas.append(
                HotelOferta(
                    id=str(item.get("accommodation_id") or f"trivago-{len(ofertas) + 1}"),
                    nombre=str(item["accommodation_name"]),
                    rating=str(item.get("hotel_rating") or item.get("review_rating") or "")
                    or None,
                    precio_total=_monto_limpio(
                        item.get("price_per_stay") or item.get("price_per_night")
                    ),
                    moneda=str(item.get("currency") or "USD").upper(),
                    checkin=str(item.get("arrival") or checkin),
                    checkout=str(item.get("departure") or checkout),
                    booking_url=_url_https(item.get("accommodation_url")),
                )
            )
        return ofertas

    async def _buscar_hoteles_skiplagged(
        self, ciudad: str, checkin: str, checkout: str, adultos: int
    ) -> list[HotelOferta]:
        texto = await self._mcp_caller(
            SKIPLAGGED_MCP_URL,
            "sk_hotels_search",
            {
                "city": ciudad,
                "checkin": checkin,
                "checkout": checkout,
                "limit": 10,
                "numAdults": max(1, min(int(adultos), 10)),
                "renderMode": "text",
            },
        )
        patron = re.compile(
            r"- Hotel: \*\*(?P<nombre>.+?)\*\*<br/>(?P<detalle>.*?)(?=\n- Hotel:|\Z)",
            flags=re.DOTALL,
        )
        ofertas: list[HotelOferta] = []
        for indice, coincidencia in enumerate(patron.finditer(texto)):
            detalle = " ".join(coincidencia.group("detalle").split())
            rating = re.search(r"Rating:\s*([^|]+?)(?:\s*\||$)", detalle)
            total = re.search(r"Total:\s*([^|]+?)(?:\s*\||$)", detalle)
            noche = re.search(r"Price/night:\s*([^|]+?)(?:\s*\||$)", detalle)
            booking = re.search(r"Booking:\s*\[[^]]+]\((https://[^)]+)\)", detalle)
            url = _url_https(booking.group(1)) if booking else None
            moneda = "USD" if "$" in (total.group(1) if total else detalle) else "USD"
            identificador = ""
            if url:
                segmentos = [segmento for segmento in urlsplit(url).path.split("/") if segmento]
                if len(segmentos) >= 2 and segmentos[0] == "hotel":
                    identificador = segmentos[1]
            ofertas.append(
                HotelOferta(
                    id=identificador or f"skiplagged-{indice + 1}",
                    nombre=coincidencia.group("nombre").strip(),
                    rating=rating.group(1).strip() if rating else None,
                    precio_total=_monto_limpio(
                        total.group(1) if total else noche.group(1) if noche else "0"
                    ),
                    moneda=moneda,
                    checkin=checkin,
                    checkout=checkout,
                    booking_url=url,
                )
            )
        if not ofertas:
            raise TravelError("Skiplagged no devolvió hoteles que se pudieran leer.")
        return ofertas

    async def estado_vuelo(self, carrier: str, numero: str, fecha: str) -> EstadoVuelo:
        # Los conectores públicos usados para precios no ofrecen estado operacional.
        # No inventamos un horario: entregamos una salida útil y verificable.
        consulta = quote_plus(f"{carrier.upper()}{numero} flight status {fecha}")
        raise TravelError(
            "El estado operacional debe confirmarse con la aerolínea. "
            f"[Consultar el vuelo en vivo](https://www.google.com/search?q={consulta})."
        )

    @staticmethod
    def enlace_vuelos(origen: str, destino: str, fecha: str) -> str:
        consulta = quote_plus(f"Flights from {origen} to {destino} on {fecha}")
        return f"https://www.google.com/travel/flights?q={consulta}"

    @staticmethod
    def enlace_hoteles(ciudad: str, checkin: str, checkout: str) -> str:
        consulta = quote_plus(f"Hotels in {ciudad} from {checkin} to {checkout}")
        return f"https://www.google.com/travel/search?q={consulta}"


__all__ = [
    "EdecanTravelProvider",
    "KIWI_MCP_URL",
    "SKIPLAGGED_MCP_URL",
    "TRIVAGO_MCP_URL",
]
