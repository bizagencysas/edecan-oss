# Runbook: cola atascada — redrive de la DLQ

Cubre jobs que fallan en bucle y terminan en `edecan-jobs-dlq`, o una cola principal (`edecan-jobs`) que deja de vaciarse.

## Cómo funciona el reintento (para entender qué estás viendo)

`apps/worker` consume `SQS_QUEUE_URL` (`edecan-jobs`); cada mensaje es un `JobEnvelope` (`ARCHITECTURE.md` §10.5 y §10.11). Cuando un handler falla:

- Si `attempt < 5`, el worker vuelve a encolar el mismo job con `attempt` incrementado y un backoff de `min(900, 2**attempt * 30)` segundos. En la práctica, eso son reintentos escalonados aproximadamente en 30s, 60s, 120s, 240s y 480s (8 min) — unos 15 minutos totales de reintentos antes de agotar los 5 intentos.
- Si ya se agotaron los 5 intentos, el job **no** se vuelve a encolar en `edecan-jobs` y termina en la Dead Letter Queue `edecan-jobs-dlq`.

Un mensaje en la DLQ significa: *este job falló 5 veces seguidas y necesita intervención humana antes de reintentarse* — no se reprocesa solo.

## Cuándo se activa

- Alarma de CloudWatch sobre `ApproximateNumberOfMessagesVisible` de `edecan-jobs-dlq` > 0 sostenido.
- La cola principal `edecan-jobs` crece sin bajar (mensajes se encolan más rápido de lo que el worker los procesa, o el worker está caído).
- Un tipo de job específico (`ingest_file`, `sync_connector`, `send_reminder`, `send_reminder_scan`, `run_campaign_step`, `generate_content`, `memory_consolidate`) deja de completarse para uno o varios tenants.

## Prerrequisitos

Acceso de lectura a los logs de `apps/worker`, y credenciales AWS (o `AWS_ENDPOINT_URL` hacia LocalStack en dev/self-host) con permisos sobre SQS.

## Pasos

### 1. Detección y medición

```bash
# Cola principal — ¿cuántos mensajes visibles y cuántos "in flight"?
aws sqs get-queue-attributes \
  --queue-url "$SQS_QUEUE_URL" \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible

# DLQ — lo mismo
aws sqs get-queue-attributes \
  --queue-url "$SQS_QUEUE_URL_DLQ" \
  --attribute-names ApproximateNumberOfMessages
```

(En dev/self-host, añade `--endpoint-url=http://localhost:4566` a ambos comandos.)

### 2. Diagnóstico — encuentra la causa raíz antes de redrivear nada

Redrivear sin arreglar la causa raíz solo hace que los mismos jobs vuelvan a fallar 5 veces más y regresen a la DLQ — es ruido, no una solución.

- Revisa los logs del worker alrededor del horario en que empezaron a acumularse fallos: busca el `type` de job y, si el log lo incluye, el `tenant_id` y el `payload` para identificar el patrón común.
- Clasifica la causa probable:
  - **Bug del handler** (excepción no manejada, cambio de esquema no reflejado en el código) → requiere un fix de código y un deploy.
  - **Dependencia externa caída o con rate-limit** (el proveedor LLM, un conector OAuth, Twilio) → puede resolverse solo cuando el proveedor se recupera; considera si vale la pena redrivear ya o esperar.
  - **Payload inválido para uno o pocos jobs puntuales** (dato corrupto de un solo tenant) → esos mensajes específicos probablemente deban descartarse (o corregirse manualmente) en vez de redrivearse tal cual, porque van a volver a fallar igual.
- Si necesitas inspeccionar el contenido de los mensajes en la DLQ sin sacarlos de la cola (para no perderlos si algo sale mal), usa `receive-message` con cuidado: cada `ReceiveMessage` sin `DeleteMessage` posterior los deja disponibles de nuevo tras el *visibility timeout*, así que es seguro para solo "mirar".

  ```bash
  aws sqs receive-message \
    --queue-url "$SQS_QUEUE_URL_DLQ" \
    --max-number-of-messages 10 \
    --visibility-timeout 5
  ```

### 3. Corregir la causa raíz

- Si es un bug: aplica el fix, corre `make test` y `make lint`, despliega.
- Si es una dependencia externa: confirma que ya se recuperó (revisa el estado del proveedor) antes de redrivear.
- Si son payloads puntuales corruptos: decide caso por caso si se pueden corregir y reintentar, o si simplemente se descartan (documentando cuáles y por qué).

### 4. Redrive — mover los mensajes de vuelta a la cola principal

Con la causa raíz ya resuelta, usa la función nativa de redrive de SQS (mueve los mensajes de la DLQ de vuelta a su cola de origen sin que tengas que recibir/reenviar/borrar uno por uno):

```bash
aws sqs start-message-move-task \
  --source-arn "arn:aws:sqs:us-east-1:<account-id>:edecan-jobs-dlq" \
  --destination-arn "arn:aws:sqs:us-east-1:<account-id>:edecan-jobs"

# Verifica el progreso:
aws sqs list-message-move-tasks --source-arn "arn:aws:sqs:us-east-1:<account-id>:edecan-jobs-dlq"
```

Si tu entorno no soporta esa API (versiones antiguas de LocalStack en dev, por ejemplo), el equivalente manual es: `receive-message` de la DLQ, `send-message` del mismo cuerpo hacia `edecan-jobs`, y solo entonces `delete-message` en la DLQ — en ese orden, para no perder el mensaje si algo falla a mitad de camino.

**Antes de redrivear jobs de `run_campaign_step` en particular**: verifica que el handler sea seguro de reintentar sin duplicar efectos (una llamada o SMS ya enviado no debería repetirse solo porque el job se procesó dos veces). Si tienes dudas sobre la idempotencia de un tipo de job concreto, redrive esos mensajes de uno en uno y observa el resultado antes de mover el lote completo — un `run_campaign_step` duplicado no es solo un bug, es potencialmente un SMS o llamada repetida a un destinatario real, lo cual toca directamente el checklist de cumplimiento de [`../voz-telefonia.md`](../voz-telefonia.md).

### 5. Verificación

- La DLQ vuelve a `ApproximateNumberOfMessages = 0` (o al menos deja de crecer).
- Los logs del worker muestran los jobs redriveados completándose exitosamente esta vez.
- No hay una nueva ronda de mensajes reapareciendo en la DLQ minutos después (señal de que la causa raíz no estaba realmente resuelta).

## Prevención

- Alarma de CloudWatch sobre `edecan-jobs-dlq` con `ApproximateNumberOfMessagesVisible >= 1` — un solo mensaje en la DLQ ya merece revisión, no hace falta esperar a que se acumulen muchos.
- Diseña los handlers de `HANDLERS` (`edecan_worker.handlers`) para ser idempotentes siempre que sea razonable (especialmente los que tienen efectos externos: `run_campaign_step`, `send_reminder`), de forma que un redrive nunca sea peligroso por sí mismo.
- Revisa este runbook después de cada incidente real de DLQ y ajústalo si el flujo real terminó siendo distinto al descrito aquí.
