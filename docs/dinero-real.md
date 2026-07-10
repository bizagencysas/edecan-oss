# Dinero real: la política permanente

> Este documento describe una **regla de producto permanente e innegociable**, no una
> limitación temporal del nivel P1 de este work package. Ver `ARCHITECTURE.md` §0,
> `ROADMAP_V2.md` §8.1 y `REQUISITOS_V2.md` ("Guardrails que Claude aplica de todos modos"):
>
> **Dinero real nunca se mueve solo.**

Pagos, compras y trading de cripto/bolsa se construyen con la integración/plomería
completa — pero la ejecución final, la que de verdad movería un centavo real, **siempre**
exige una confirmación explícita del usuario en la UI. Nada en el agente, ninguna
herramienta, ningún job del worker, puede auto-ejecutar una orden de dinero. Esto no
cambia cuando exista un broker o un proveedor de pagos ("PSP") reales conectados — ver
la última sección.

## Por qué

1. **El agente actúa sobre lenguaje natural**, potencialmente influenciado por contenido
   que el propio agente leyó de fuentes no confiables (un correo, una página web, un
   documento subido por otra persona — ver `RIESGOS.md`, sección "Técnicos y de
   arquitectura", sobre inyección de prompt). Dejar que una instrucción en lenguaje
   natural mueva dinero directamente sería, literalmente, dejar que cualquier texto que el
   modelo procese pueda gastar el dinero del usuario.
2. **Es la misma regla que ya protege acciones "dangerous" del agente** (`edecan_core.tools.
   Tool.dangerous`, `ARCHITECTURE.md` §10.7) — llamar por teléfono, publicar en una red
   social, enviar un mensaje. El dinero es, si acaso, el caso donde esta protección importa
   MÁS, no menos — así que aquí se aplica **dos veces** (ver "El doble gate" abajo).
3. **Confianza del producto.** Un asistente que puede gastar dinero sin que el humano lo vea
   venir, ni siquiera "solo esta vez, con un monto pequeño", deja de ser una herramienta que
   el usuario controla. El límite no es de monto ni de frecuencia: es estructural.

## El doble gate

Toda orden de dinero pasa por **dos** puntos de confirmación humana, en dos capas
distintas, antes de que exista la más mínima posibilidad de que algo se ejecute:

```
Chat (agente)                          Página /app/ordenes (UI)
─────────────                          ─────────────────────
Usuario: "págale $250 a CFE"
        │
        ▼
preparar_pago (Tool dangerous=True)
        │
        │  1er gate: edecan_core.agent.Agent.run_turn exige que el
        │  usuario apruebe ESTE tool call antes de correrlo
        │  (SSE `confirmation_required` → POST /v1/conversations/{id}/confirm)
        ▼
INSERT orders(kind='payment', status='draft')   ◄── lo ÚNICO que hace la tool.
        │                                            Nunca ejecuta nada.
        │
        └──────────────────────────────────────────────┐
                                                          ▼
                                          Usuario abre /app/ordenes, ve el
                                          borrador, revisa monto/descripción
                                                          │
                                                          │  2do gate: modal de
                                                          │  doble confirmación
                                                          ▼
                                          POST /v1/commerce/orders/{id}/confirm
                                                          │
                                                          ▼
                                          kind=payment → enlace de pago PLACEHOLDER
                                          (ningún PSP real conectado hoy: el usuario
                                          debe abrir y aprobar el pago él mismo)
                                          kind=trade   → PaperBroker (broker SIMULADO,
                                          nunca dinero real — ver más abajo)
```

- **Gate 1 — confirmación del *tool call* en el chat**: `preparar_pago`/`preparar_orden`
  (`packages/commerce/edecan_commerce/tools.py`) son `dangerous=True`. `edecan_core.agent.
  Agent.run_turn` (`ARCHITECTURE.md` §10.7) NUNCA las corre sin que `ctx.extras
  ["approved_tool_calls"]` contenga ese `tool_call_id` — si no está aprobado, el turno se
  detiene y emite `confirmation_required` en vez de ejecutar la tool. Este gate ya existía
  en v1 para otras acciones (llamar por teléfono, publicar en redes) — aquí se reutiliza
  tal cual, sin ninguna excepción para dinero.
- **Incluso aprobado el tool call, lo ÚNICO que pasa es un `INSERT` con `status='draft'`.**
  Ni `preparar_pago` ni `preparar_orden` tocan `holdings`, `transactions`, ni generan
  ningún enlace de pago. Ver el test explícito
  `test_preparar_pago_y_preparar_orden_nunca_ejecutan_solo_crean_draft`
  (`packages/commerce/tests/test_tools.py`): verifica, mirando el SQL exacto que se
  ejecutó, que la única sentencia es ese `INSERT`.
- **Gate 2 — confirmación en la página `/app/ordenes`**: el borrador aparece en la UI
  (`apps/web/src/app/(app)/app/ordenes/page.tsx`) con su resumen (monto, símbolo,
  cantidad, cotización adjunta). El botón "Confirmar" abre un **modal** con el texto fijo:
  > "Esto NO mueve dinero real. Las operaciones de trading se ejecutan en modo simulado
  > (paper). Los pagos generan un enlace que TÚ debes abrir y aprobar."

  Solo tras ese segundo "sí, confirmar" se llama a `POST /v1/commerce/orders/{id}/confirm`
  (`apps/api/edecan_api/routers/commerce.py`).

Ningún atajo salta ninguno de los dos gates: no hay un endpoint que cree una orden YA
confirmada, no hay una tool que ejecute sin pasar por `confirm`, y `confirm` en sí solo
transiciona `draft → confirmed` — la ejecución (paper) o la generación del enlace de pago
son un paso **posterior**, dentro de la misma llamada pero después de que el humano ya dijo
que sí en la UI.

## Qué existe hoy, en modo real

| Pieza | Estado hoy |
|---|---|
| Cotizaciones (`cotizar_activo`, `GET`-únicamente) | **Real.** `StubQuotes` (determinista, offline) o `CoinGeckoQuotes` (API pública oficial, sin key) según `QUOTES_PROVIDER`. Nunca escribe nada. |
| Presupuestos (`gestionar_presupuesto`, `GET`/`PUT /v1/commerce/budgets`) | **Real.** `% gastado` calculado sobre la tabla `transactions` (v1, ya existe). |
| Borradores de orden (`preparar_pago`, `preparar_orden`) | **Real** en el sentido de que insertan una fila de verdad — pero esa fila **nunca representa dinero movido**: es, por definición, un borrador. |
| Confirmar un pago (`kind=payment`) | **Real como generador de enlace placeholder.** `meta.payment_link` apunta a `https://TU_PROVEEDOR_DE_PAGOS_AQUI/checkout/{id}` — un placeholder explícito, NO una URL de ningún proveedor real. El texto de respuesta deja clarísimo que el usuario debe abrir y aprobar el pago por su cuenta. |
| Confirmar un trade (`kind=trade`, `COMMERCE_MODE=paper`) | **Real como simulación contable.** `edecan_commerce.paper.PaperBroker` ejecuta la compra/venta contra `holdings` con costo promedio ponderado y dejar constancia en `transactions`/`audit_log` — pero es contabilidad *de mentira*: no hay ningún exchange ni broker real conectado, en ningún punto. |
| Cualquier otro `COMMERCE_MODE`, o `kind="purchase"` | `501 Not Implemented`, documentado explícitamente — nunca se simula una ejecución que no existe ni se falla en silencio. |

### Lo que falta para que esto persista contra Postgres real

Las tablas `orders`/`holdings`/`budgets` (`ROADMAP_V2.md` §7.4) llegan con la migración
`0003_v2_expansion`, propiedad de WP-V2-01. Este work package (WP-V2-10) asume ese esquema
al pie de la letra (nombres de tabla/columna EXACTOS) — el día que la migración aterriza,
todo el código de `packages/commerce/` y `apps/api/edecan_api/routers/commerce.py` funciona
contra Postgres real sin cambios. Mientras tanto, la lógica (aritmética del broker paper,
% de presupuesto, validaciones, contrato HTTP) está probada con dobles de sesión que
verifican el SQL exacto que se ejecutaría (`packages/commerce/tests/`,
`apps/api/tests/test_commerce_router.py`) — ver el README de `packages/commerce` para el
detalle de qué corre hoy sin red ni Postgres.

`transactions`/`audit_log`, en cambio, son tablas v1 que **ya existen** desde
`0001_initial` — la parte de `PaperBroker` que las escribe funciona hoy mismo, en cuanto
exista la tabla `orders` de la que lee la orden a ejecutar.

## Qué haría falta para un broker/PSP "live" — y por qué la confirmación humana seguiría siendo obligatoria

Nada de lo anterior es un sustituto temporal de "ya vendrá lo real" en el sentido de que el
gate desaparecería. Si en el futuro se conecta un broker de trading o un proveedor de pagos
de verdad, haría falta:

1. **Credenciales propias del tenant**, vía OAuth o API key de SU cuenta en ese broker/PSP —
   nunca una cuenta compartida de la plataforma, nunca credenciales hardcodeadas (mismo
   principio que conectores sociales/Twilio, `ARCHITECTURE.md` §5).
2. **Un nuevo valor de `COMMERCE_MODE`** (p. ej. `"live"`) y una implementación del mismo
   contrato de entrada que `PaperBroker` ya respeta hoy: solo acepta órdenes ya
   `status="confirmed"`. Un broker/PSP live NUNCA decide por sí mismo que una orden está
   lista — exactamente la misma restricción que tiene el broker simulado.
3. **El mismo doble gate de arriba, sin excepciones.** La UI seguiría mostrando el mismo
   modal (con el texto ajustado para dejar claro que esta vez SÍ es dinero real) antes de
   llamar a `confirm`. Para pagos, lo más probable es que el "enlace de pago" siga siendo
   un checkout hospedado por el PSP (Stripe Checkout, un link de pago, etc.) que el usuario
   abre y completa él mismo — el patrón "payment-link, el humano lo abre y aprueba" no es
   una limitación de este P1, es el diseño objetivo incluso con un PSP real conectado: ni
   siquiera con una integración completa el sistema tipearía los datos de una tarjeta o
   aprobaría una transferencia por su cuenta.
4. **Límites y cuotas más estrictos** (p. ej. un tope máximo por orden, alertas si el gasto
   mensual total supera cierto umbral) — hoy fuera de alcance de este P1, documentado aquí
   como trabajo futuro razonable antes de operar con dinero real a cualquier escala.
5. **Auditoría reforzada** (ya existe `audit_log` en cada confirmación/cancelación/ejecución
   — con dinero real probablemente se querría además notificación al usuario por email/push
   en cada ejecución, fuera de alcance hoy).

Ninguno de estos puntos, ni todos juntos, elimina el requisito de que un humano confirme
explícitamente en la UI antes de que algo se ejecute. Esa es la parte de la regla que **no**
es negociable ni siquiera con la integración más completa imaginable.

## Referencia rápida

- Herramientas del agente: `cotizar_activo`, `gestionar_presupuesto` (no `dangerous`),
  `preparar_pago`, `preparar_orden` (`dangerous=True`) — `packages/commerce/edecan_commerce/tools.py`.
- Endpoints: `GET/POST /v1/commerce/orders[/{id}[/confirm|cancel]]`, `GET /v1/commerce/holdings`,
  `GET/PUT /v1/commerce/budgets` — `apps/api/edecan_api/routers/commerce.py` (docstring del
  módulo tiene el detalle completo de qué es real hoy vs. pendiente de migración).
- UI: `apps/web/src/app/(app)/app/ordenes/page.tsx` — tabla de órdenes, modal de doble
  confirmación, holdings (paper) y presupuestos con barras de `%` gastado.
- Flag de plan: `commerce.orders` (`edecan_schemas.plans.FLAG_COMMERCE_ORDERS`) — activo en
  `free_selfhost`/`hosted_pro`/`hosted_business`, no en `hosted_basic`
  (`ROADMAP_V2.md` §7.2).
- Guardrail general de dinero, control remoto y salud/legal/finanzas: `ARCHITECTURE.md` §0,
  `ROADMAP_V2.md` §8.
