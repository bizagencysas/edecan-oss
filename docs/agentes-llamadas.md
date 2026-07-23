# Agentes de llamadas configurables

Edecan permite guardar hasta 20 perfiles independientes para llamadas salientes
y entrantes, por ejemplo asistencia, ventas consultivas o seguimiento. Una
plantilla define:

- un nombre interno para reconocerla;
- el nombre con el que el agente se presenta;
- su personalidad y forma de conversar;
- un objetivo reutilizable;
- una primera frase opcional;
- el contexto comercial que sí puede compartir con terceros;
- la información que debe preguntar y obtener;
- una voz opcional de la cuenta ElevenLabs del propietario;
- si será el agente predeterminado para entrantes y llamadas sin agente explícito.

Se administran desde **Llamadas → Agentes de llamadas**, desde el chat con
`configurar_agente_llamadas`, o mediante `/v1/phone/agent-templates`. El primer
perfil creado se vuelve predeterminado; solo puede existir uno predeterminado
por usuario.

## Por qué cada llamada guarda una copia

Al preparar una llamada, `phone_calls` copia el nombre, prompt y apertura de la
plantilla. `agent_template_id` conserva la procedencia, pero no es la fuente de
verdad de una llamada ya creada. Por eso:

- editar una plantilla solo afecta llamadas futuras;
- eliminarla no cambia el historial ni una llamada pendiente;
- la persona confirma exactamente el objetivo que quedó en el borrador;
- un webhook tardío nunca reconstruye comportamiento desde configuración nueva.

Este snapshot sigue el mismo principio que el objetivo confirmado: lo revisado
por la persona debe ser lo que llega al proveedor.

## Cadena real de una llamada

```text
Chat o API
  → herramienta peligrosa / POST prepare
  → valida cuenta Twilio y consentimiento de voz
  → resuelve plantilla explícita o predeterminada
  → guarda conversación + borrador + snapshot
  → muestra número y objetivo para confirmación
  → confirmación explícita
  → commit en PostgreSQL
  → Twilio REST inicia la llamada
  → webhook firmado entrega saludo con la voz del agente
  → TTS ElevenLabs del tenant + TwiML Play/Gather conversan por turnos
  → LLM rápido usa persona telefónica + snapshot + objetivo
  → webhook firmado de estado actualiza la verdad del proveedor
  → cierre terminal guarda resumen + actividad de forma idempotente
  → job reclama una sola vez el push genérico best-effort
```

La ruta de chat `llamar_contacto` continúa siendo `dangerous=True`. Si la
persona dice «con el agente de negocios», el dispatcher resuelve el nombre
exacto de la plantilla. Si no existe o coincide con varios agentes, detiene la
acción y muestra los nombres disponibles; nunca sustituye silenciosamente el
agente solicitado por otro. Twilio solo se invoca después del gate existente.
La API permite elegir otra plantilla con `agent_template_id` en
`POST /v1/phone/calls/prepare` y omitir `goal` para usar su objetivo
predeterminado.

## Resumen automático al finalizar

Todo estado terminal de Twilio (`completed`, `failed`, `busy`, `no_answer` o
`cancelled`) genera un `phone_calls.summary` estructurado con:

- estado y dirección;
- participantes asistente/externo;
- duración cuando Twilio la entregó;
- puntos clave;
- compromisos detectados;
- próximos pasos;
- disponibilidad y número de turnos de transcripción.

El resumen base es determinista: no depende de que el tenant tenga un LLM
conectado y también existe para una llamada fallida o sin transcripción. La
transcripción completa continúa en `phone_call_events`; no se duplica dentro
del resumen ni en la lista de actividad.

El primer webhook terminal hace un `UPDATE ... WHERE summary IS NULL` y agrega
un único evento `activity/phone_call_finished` en la misma transacción. Un
callback repetido o fuera de orden conserva ese primer resumen. Solo después
del commit se encola `notify_phone_call_summary`. El worker reclama
`summary_push_attempted_at` antes de salir a APNs/FCM, por lo que una redelivery
no duplica el aviso. Su texto es siempre genérico: nunca incluye teléfonos,
nombres, objetivo, puntos clave, compromisos ni transcripción en la pantalla
bloqueada.

## Aislamiento y límites de seguridad

- Las plantillas tienen `tenant_id`, RLS y `user_id`; otro usuario no puede
  seleccionarlas ni modificarlas.
- El interlocutor no recibe memoria, rasgos, instrucciones privadas ni estilo
  romántico del propietario.
- El prompt de la plantilla queda delimitado y antes de reglas duras que
  prohíben ejecutar o afirmar acciones sensibles. El canal telefónico no recibe
  tools de compra, envío, reserva o modificación.
- El saludo siempre declara que quien habla es un asistente automatizado.
- Consentimiento, doble confirmación y auditoría siguen siendo obligatorios.
- La plantilla no programa llamadas, no crea campañas y no decide destinatarios.

## Llamadas entrantes y voces

El agente predeterminado atiende las llamadas que llegan al número Twilio
conectado. Al conectar un número nuevo, Edecan configura automáticamente su
webhook de voz. El botón **Configurar o reparar recepción** repite esa operación
de forma segura si cambió el dominio o el túnel.

Cada agente puede elegir una voz de ElevenLabs. La voz queda copiada en la
llamada junto con la identidad y el objetivo, de modo que editar la plantilla
no cambia una llamada ya confirmada. Cada respuesta se sintetiza con la
credencial cifrada del tenant, se conserva en Redis durante cinco minutos y se
entrega a Twilio mediante una URL opaca de un solo tenant. Si el TTS no está
configurado o falla, la llamada continúa con `<Say>` de Twilio.

Al comenzar una llamada entrante, Edecán registra primero la llamada y su evento
`incoming`. Después intenta un push genérico hacia Actividad, respetando la
preferencia `work`. Reintentos del webhook o del job reutilizan el mismo UUID y
no producen un segundo push. Este aviso inicial no contiene número, nombre ni
transcripción y es independiente del resumen estructurado que se crea al cierre.

El motor OSS actual sigue siendo conversacional por turnos con `<Gather>`. No es
todavía una conversación full-duplex con interrupciones naturales; alcanzar esa
latencia requiere Media Streams, STT incremental y audio bidireccional. Esta
limitación no relaja la selección exacta del agente, el consentimiento ni la
doble confirmación.
