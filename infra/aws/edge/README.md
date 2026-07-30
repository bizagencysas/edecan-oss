# Edecán Edge en AWS

Capa de continuidad serverless. No duplica el runtime completo de escritorio.

Incluye:

- API Gateway HTTP;
- Lambda ARM64;
- DynamoDB bajo demanda;
- SQS con DLQ;
- S3 privado y versionado;
- secreto generado por AWS;
- chat de emergencia con Amazon Bedrock;
- heartbeat, cola y sincronización con el companion local.

Despliegue:

```bash
AWS_PROFILE=default \
AWS_REGION=us-east-1 \
EDECAN_INSTALLATION_NAME=edecan-personal \
EDECAN_ALLOWED_ORIGIN=https://edge.example.net \
infra/aws/edge/deploy.sh
```

`EDECAN_ALLOWED_ORIGIN` es obligatorio y debe ser el origen HTTPS exacto de la
puerta privada de esa instalación, sin ruta, query ni fragmento. El script
termina antes de consultar o modificar AWS cuando falta o es inválido. Ningún
dominio real de una instalación, cuenta AWS o identificador personal vive en
el template versionado.

El secreto de conexión no se imprime. El desktop lo recupera durante su
configuración inicial mediante una sesión AWS autenticada y lo guarda en el
almacén seguro local.

Después de desplegar, conecta la computadora actual sin copiar ni imprimir la
capacidad:

```bash
AWS_PROFILE=default AWS_REGION=us-east-1 \
infra/aws/edge/configure-local.sh
```

El script consulta los outputs y parámetros del stack, recupera el secreto
directamente a un archivo `0600` y deja la configuración bajo `DATA_DIR`. Para
un stack o carpeta distintos usa `EDECAN_EDGE_STACK` y `EDECAN_DATA_DIR`.

La conexión residente, los dos archivos privados que consume la app maestra,
el contrato heartbeat/claim/ack y sus límites están documentados en
[`docs/continuidad-hibrida.md`](../../../docs/continuidad-hibrida.md).

Los recursos persistentes usan `DeletionPolicy: Retain`: eliminar por accidente
el stack no elimina conversaciones, archivos ni el secreto. CloudWatch conserva
solo siete días de logs para controlar costos.

La API no expone ni siquiera `/healthz` sin el secreto del origen. API Gateway
solo acepta CORS desde el dominio de Cloudflare configurado durante el
despliegue; Cloudflare es la única puerta pública prevista.

Los POST de trabajo admiten `idempotency_key` UUID. DynamoDB reserva esa
identidad de forma condicional antes de encolar y SQS FIFO aplica una segunda
deduplicación. Dos reintentos concurrentes producen una sola ejecución. Si SQS
confirma un fallo, la misma identidad puede reintentarse sin crear otro job.
El chat online conserva además el `conversation_id` al delegarlo a la
computadora.

Al apagarse limpio, la computadora avisa con `POST /v1/edge/goodbye` y queda
ausente en el acto, sin esperar a que venza su último heartbeat. El primer
heartbeat posterior la revive sola.

Ese aviso exige el secreto compartido **y** llegar directo a API Gateway. El
secreto solo no alcanza porque no es exclusivo de la computadora: el Worker de
Cloudflare lo tiene en `AWS_ORIGIN_KEY` y con él habla por cualquier dispositivo
del dueño que traiga un token de sesión válido, dejando el token en
`x-edecan-device-authorization`. Un pedido con esa cabecera recibe 403: si
bastara el secreto, un teléfono robado dejaría al dueño hablando con el modelo
de emergencia mientras su computadora está encendida. La computadora no pasa por
el Worker — `configure-local.sh` le escribe el `ApiUrl` del stack — así que su
propia despedida no lleva la cabecera y se aplica normal.

`HEARTBEAT_TTL_SECONDS` no cambia y sigue cubriendo las caídas que nadie alcanza
a avisar: batería, kernel panic, cable. Acortarlo sería peor que el punto ciego
que cierra el aviso, porque un bache de red mandaría la conversación al modelo
de emergencia con la computadora encendida.

El cuerpo admite `last_seen_at`, el `server_time` que devolvió el último
heartbeat. No es una credencial y no protege de nadie — `GET /v1/edge/status` lo
publica: ata el aviso a esa sesión para que uno demorado o reintentado no apague
una computadora que ya volvió. Si llega tarde, la respuesta trae
`applied: false` y no cambia nada.

Cuando la computadora está offline, el texto de la persona se envía al modelo
Bedrock configurado. Para reconciliar luego la conversación, DynamoDB conserva
`user_message`, `assistant_message`, modelo, fecha e identificador de
conversación durante un máximo de 30 días mediante TTL. Ese registro vive en
la cuenta AWS de la propia instalación, no en una base compartida de Edecán.
