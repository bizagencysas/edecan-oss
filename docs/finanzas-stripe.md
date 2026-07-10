# Finanzas — Stripe bring-your-own (solo lectura)

`/app/finanzas` puede sincronizar los movimientos de tu propia cuenta de Stripe hacia tu
libro de transacciones — no Plaid (no todos los países del mercado de Edecán tienen
cobertura de Plaid, y Stripe ya es la única integración de pagos que el resto de la
plataforma soporta). Igual que el resto de las credenciales de Edecán: es tuya, vos la
creás, vos la podés revocar cuando quieras — Edecán nunca ve tu Secret key completa.

## Por qué una Restricted key, no tu Secret key

Stripe tiene dos tipos de API key:

- **Secret key** (`sk_live_...`/`sk_test_...`): acceso total — puede cobrar, reembolsar,
  cambiar tu cuenta bancaria de payout, borrar productos, todo.
- **Restricted key** (`rk_live_...`/`rk_test_...`): vos elegís, permiso por permiso, qué
  puede hacer. Podés crear una que **solo pueda leer**, nunca escribir ni mover dinero.

Edecán **rechaza cualquier key que no empiece con `rk_`** — no es una sugerencia, el
backend corta la petición antes de intentar nada si le pegás una `sk_...`. Solo necesita
leer tu balance y tus movimientos, así que solo eso le vas a dar.

## Cómo crear la Restricted key (2 minutos)

1. Entrá al [Stripe Dashboard](https://dashboard.stripe.com/apikeys) → **Developers → API keys**.
2. Click **Create restricted key**.
3. Nombrala algo como `Edecán (solo lectura)`.
4. En la lista de permisos, buscá y activá **SOLO** estos dos, en modo **Read**:
   - **Balance** → Read
   - **Balance Transactions** → Read
5. Dejá TODO lo demás en **None** (Charges, Customers, Payouts, etc. — Edecán no los
   necesita para esto).
6. Click **Create key**, copiá el valor que empieza con `rk_live_...` (o `rk_test_...` si
   estás probando con tu cuenta de test de Stripe).
7. Pegala en `/app/finanzas` → "Conectar Stripe".

Si alguna vez querés cortar el acceso, simplemente **revocá la key desde el Dashboard de
Stripe** (Developers → API keys → esa key → Revoke) — no hace falta avisarle a Edecán,
la próxima sincronización simplemente va a fallar con un error claro.

## Sobre restringir por IP

Stripe **no soporta** limitar una API key a un rango de IPs de origen (esa opción no
existe hoy en su dashboard/API — sí existe para *webhook endpoints*, que es algo
distinto: filtra desde dónde Stripe te manda eventos a vos, no desde dónde alguien puede
usar tu key). La mitigación real disponible es exactamente la de arriba: una Restricted
key con el mínimo de permisos posible, para que aunque se filtrara, lo único que alguien
podría hacer con ella es leer tu balance — nunca mover un centavo.

## Qué hace la sincronización

`POST /v1/finance/stripe/sync` trae tus últimos 100 movimientos (`balance_transactions`
de Stripe) y los agrega a tu libro de Finanzas — no duplica en corridas repetidas (cada
transacción de Stripe se sincroniza una sola vez). No mueve dinero, no crea cargos, no
emite reembolsos: es de solo lectura, de punta a punta.
