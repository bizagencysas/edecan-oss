# Continuidad híbrida privada

Edecán es Mac/Windows/Linux-first. La computadora maestra conserva la memoria,
archivos, herramientas, IDE y permisos. La capa serverless evita que el chat
quede inutilizable por una caída del túnel y conserva trabajos hasta que la
computadora vuelva.

No es un SaaS compartido. Cada instalación despliega su propio stack y guarda
sus capacidades fuera de Git.

## Flujo operativo

1. La app maestra envía un heartbeat autenticado a AWS, y al apagarse limpio
   avisa que se va (ver "Despedida al apagar").
2. El teléfono usa siempre su URL privada de Cloudflare y su sesión revocable.
3. Cloudflare intenta el origen local.
4. Si un mensaje de chat no puede alcanzar el origen local:
   - con la computadora offline, AWS responde chat básico con Bedrock y deja
     un job de reconciliación durable;
   - con la computadora online pero el túnel interrumpido, AWS encola el turno.
5. El runtime local reclama el trabajo, lo ejecuta contra la API local real y
   confirma el resultado.
6. Cloudflare espera el job hasta 25 segundos y devuelve el mismo SSE
   `message.delta` / `message.done` que ya consumen iOS y Android.
7. Si el turno tarda más, el cliente recibe confirmación de que quedó en cola;
   el resultado permanece consultable en `/v1/edge/jobs/{job_id}`.
8. Al volver la computadora, cada turno Bedrock se inserta una sola vez en la
   conversación local original mediante `/v1/continuity/sync`.

## Despedida al apagar

El latido tiene un TTL: AWS da por viva a la computadora durante un margen
después del último latido. Ese margen es un punto ciego — la persona cierra la
app, sale y pregunta algo enseguida, y AWS encola el mensaje en vez de
contestarlo porque todavía cree encendida la computadora.

Por eso el apagado limpio se avisa. La app maestra manda, con la misma
credencial del latido:

```text
POST /v1/edge/goodbye
{"last_seen_at": <server_time del último latido aceptado>}
```

`server_time` es el que devuelve `/v1/edge/heartbeat`. Citarlo es lo que
permite al edge aplicar la despedida solo si sigue siendo el latido vigente:
una despedida demorada o repetida no puede apagar una computadora que ya
volvió, y un latido posterior la revive. No es un secreto —
`/v1/edge/status` lo publica— y no defiende de nadie: ordena reintentos.

Quien defiende es la ruta. La despedida exige llegar directo a API Gateway, no
por Cloudflare: el Worker también tiene la credencial del latido y con ella
habla por cualquier dispositivo del dueño, así que si bastara la credencial un
teléfono podría declarar ausente una computadora encendida y dejar al dueño
conversando con el modelo de emergencia. El edge rechaza con 403 toda despedida
que llegue multiplexada por el Worker. Declararse ausente es una decisión que
solo puede tomar la computadora sobre sí misma.

Detalles que importan en la app maestra:

- es de mejor esfuerzo, con un solo intento y un tope de pocos segundos;
  cerrar la app nunca espera a que AWS conteste;
- solo sale en el apagado limpio (`SIGTERM`/`SIGINT`, que es lo que manda la
  app de escritorio antes de cualquier kill duro). Un corte de luz, un kernel
  panic o un kill forzado no avisan nada: para eso está el TTL;
- si la continuidad está apagada o nunca hubo un latido aceptado, no se manda
  nada;
- en Windows el sidecar todavía termina con `taskkill /F`, así que ese cierre
  cae al TTL como cualquier caída sucia.

La clave de idempotencia del turno se reutiliza como identidad del job.
DynamoDB la reserva de forma atómica antes de encolar y SQS FIFO añade
deduplicación en tránsito. Un reintento de red, incluso concurrente, no crea
una segunda ejecución.

## Datos del chat offline

Cuando la computadora está desconectada, el mensaje se envía al modelo Bedrock
configurado en la cuenta AWS de esa instalación. Para sincronizarlo después,
DynamoDB conserva como máximo 30 días:

- `user_message`;
- `assistant_message`;
- modelo utilizado;
- fecha e identificador de conversación.

El vencimiento se controla con TTL. No se guarda una API key del modelo en
DynamoDB y el registro no se comparte con otras instalaciones. Quien no quiera
que esos mensajes salgan de su computadora puede desactivar la continuidad o
no desplegar el stack.

## Configurar la app maestra

El despliegue AWS devuelve la URL de API y crea una capacidad en Secrets
Manager. En el `DATA_DIR` privado de la instalación crea:

La ruta recomendada evita copiar el secreto por el portapapeles:

```bash
AWS_PROFILE=default AWS_REGION=us-east-1 \
infra/aws/edge/configure-local.sh
```

Para otro stack o carpeta define `EDECAN_EDGE_STACK` y `EDECAN_DATA_DIR`. El
script obtiene URL, installation id y secreto desde CloudFormation/Secrets
Manager, no los imprime y escribe ambos archivos con permisos `0600`.
Sin override usa el `app_data_dir/data` de la app Tauri en macOS, Windows o
Linux; `~/.edecan/data` queda como fallback para el runner CLI.

El formato resultante es:

`edge-continuity.json`:

```json
{
  "enabled": true,
  "base_url": "https://api-id.execute-api.region.amazonaws.com",
  "installation_id": "mi-instalacion",
  "heartbeat_seconds": 30,
  "claim_seconds": 2
}
```

`edge-continuity.secret`:

```text
capacidad-recuperada-de-secrets-manager
```

En macOS/Linux:

```bash
chmod 600 "$DATA_DIR/edge-continuity.json"
chmod 600 "$DATA_DIR/edge-continuity.secret"
```

Nunca copies esos archivos al repositorio. También se admite configuración
administrada mediante:

- `EDECAN_EDGE_BASE_URL`
- `EDECAN_EDGE_INSTALLATION_ID`
- `EDECAN_EDGE_SHARED_SECRET`
- `EDECAN_EDGE_ENABLED`
- `EDECAN_EDGE_HEARTBEAT_SECONDS`
- `EDECAN_EDGE_CLAIM_SECONDS`

La URL debe ser HTTPS y no puede contener credenciales, query ni fragmento.
El runtime no arranca la continuidad con una configuración incompleta, pero
continúa funcionando localmente.

## Estado

Una sesión autenticada puede consultar:

```text
GET /v1/continuity/status
```

La respuesta indica conexión, último heartbeat/claim, trabajos completados y
un código de error seguro. Nunca muestra endpoint, installation id o secreto.

## Límites reales

- Chat de texto básico: funciona con la computadora apagada.
- Cola y ejecución de chat con memoria/herramientas: requiere que la app
  maestra esté encendida y pueda salir a Internet.
- Adjuntos, pantalla, archivos privados e IDE: requieren el origen local.
- Un mensaje en cola que exceda la espera HTTP termina en segundo plano; las
  apps todavía necesitan una vista de actividad/push para presentar su
  resultado automáticamente.
- Telefonía no está resuelta por este stack. Los webhooks y agentes Twilio
  actuales viven en la API principal. Para recibir o mantener llamadas con la
  computadora apagada hace falta un plano telefónico cloud separado, con
  validación de firma Twilio, identidades versionadas de agentes, STT/TTS/LLM,
  consentimiento y credenciales privadas en Secrets Manager. No debe
  confundirse la cola genérica con una llamada activa.

## Verificación

```bash
uv run --package edecan-local pytest apps/local/tests/test_edge_continuity.py -q
uv run pytest infra/aws/edge/tests/test_handler.py -q
cd infra/cloudflare/edge
npm run check
npm test
```

Estas pruebas validan configuración privada, heartbeat, claim, ack,
idempotencia, preservación de conversación y adaptación al SSE móvil. Una
prueba contra nube real requiere un stack propio y no forma parte de CI.
