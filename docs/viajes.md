# Viajes en Edecán

Edecán puede buscar vuelos y hoteles reales sin pedir una API key. Esta capacidad
pertenece a Edecán, no al modelo: funciona igual con Claude, Codex, Ollama o un
proveedor OpenAI-compatible.

## Cómo funciona

La capa `EdecanTravelProvider` usa el cliente MCP propio del proyecto y normaliza
resultados de:

- Kiwi para vuelos;
- Trivago para hoteles;
- Skiplagged como segunda fuente de hoteles;
- Google Flights y Google Hotels como salida verificable si los proveedores
  temporariamente no responden.

Las respuestas externas se tratan exclusivamente como datos. Texto remoto que
intente dar instrucciones se ignora; para hoteles solo se procesa la estructura de
ofertas permitida. Los enlaces de reserva se aceptan únicamente si son HTTPS y no
contienen credenciales.

Los resultados llegan al Mega Chat como bloques ricos de vuelo u hotel con precio,
moneda, fechas, proveedor, hora de observación y acciones para abrir la oferta o
preparar un borrador.

## Cero datos inventados

Sin una conexión heredada de Amadeus, vuelos y hoteles usan la búsqueda nativa real.
`StubTravelProvider` existe solo para pruebas explícitas y nunca es el fallback del
flujo normal. Si una fuente falla, Edecán informa el problema y entrega un enlace de
continuación; no inventa aerolíneas, hoteles, precios ni horarios.

El estado operacional de un vuelo debe confirmarse con la aerolínea. Cuando no hay
una fuente de estado en vivo, `estado_vuelo` entrega un enlace de consulta en vez de
fabricar una puerta o una hora.

## Guardrail de dinero

Edecán nunca reserva ni paga un vuelo u hotel por su cuenta.

`preparar_reserva` está marcada como `dangerous=True` y necesita confirmación
explícita. Incluso después de aprobarla, su único efecto es crear un borrador en
`orders` con `status='draft'`. La compra real ocurre en el sitio del proveedor y la
realiza la persona.

```text
buscar oferta -> revisar fuente y precio -> preparar borrador con aprobación
              -> abrir proveedor -> la persona decide y compra
```

## Herramientas del agente

| Herramienta | Qué hace | Confirmación |
|---|---|---|
| `buscar_vuelos` | Busca ofertas reales por origen, destino, fecha y pasajeros | No |
| `buscar_hoteles` | Busca hoteles reales por ciudad y fechas | No |
| `estado_vuelo` | Entrega información verificable para consultar el vuelo | No |
| `rastrear_paquete` | Consulta AfterShip o muestra un resultado de prueba claramente marcado | No |
| `preparar_reserva` | Crea un borrador local, nunca una compra | Sí |

Todas se ofrecen mediante el mismo `ToolRegistry`; cambiar el modelo activo no cambia
su disponibilidad.

## API HTTP

Todas las rutas requieren el flag `tools.travel`.

| Ruta | Comportamiento |
|---|---|
| `GET /v1/viajes/buscar/vuelos` | Usa Edecán Viajes o una conexión heredada con fallback nativo |
| `GET /v1/viajes/buscar/hoteles` | Igual para hoteles |
| `GET /v1/viajes/rastreo/{numero}` | Rastreo mediante la cuenta AfterShip del tenant |
| `GET /v1/viajes/status` | Estado de conexiones heredadas y rastreo |
| `PUT/DELETE /v1/viajes/rastreo/credentials` | Conecta o quita AfterShip |
| `PUT/DELETE /v1/viajes/credentials` | Compatibilidad con cuentas Amadeus Enterprise existentes |

Los endpoints de búsqueda son de solo lectura. Ninguno escribe en `orders`; ese efecto
solo existe en la herramienta confirmada `preparar_reserva`.

## Compatibilidad con Amadeus

El portal Self-Service fue retirado. Edecán no lo muestra como requisito ni invita a
crear una cuenta nueva. Las instalaciones que ya tengan acceso Enterprise pueden
conservar su configuración histórica; `ResilientTravelProvider` prueba primero esa
cuenta y cae a Edecán Viajes si falla.

## Rastreo con AfterShip

AfterShip sigue siendo opcional y bring-your-own. Se conecta desde Ajustes o con:

```json
{ "api_key": "TU_API_KEY", "validate": true }
```

La clave se valida contra `GET /couriers`, se cifra en el `TokenVault` del tenant y
nunca se devuelve completa ni se registra en logs.

## Pruebas

`packages/travel/tests/` cubre el parseo estricto de Kiwi, Trivago y Skiplagged, URLs
seguras, fallback heredado, bloques ricos, aislamiento de credenciales y el guardrail
de borrador. Las suites automatizadas usan dobles locales; las comprobaciones contra
los MCP públicos se ejecutan de forma manual para no hacer que CI dependa de terceros.
