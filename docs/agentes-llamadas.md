# Agentes de llamadas configurables

Edecan permite guardar hasta 20 perfiles independientes para llamadas salientes
y entrantes, por ejemplo asistencia, ventas consultivas o seguimiento. Una
plantilla define:

- un nombre interno para reconocerla;
- el nombre con el que el agente se presenta;
- su criterio, personalidad y forma de conversar;
- su función y misión;
- los problemas que sí puede resolver;
- los problemas que están fuera de su alcance;
- las acciones concretas que puede realizar;
- las acciones y promesas que tiene prohibidas;
- cuándo debe pedir ayuda, transferir o tomar un recado;
- cómo reconoce un resultado correcto y cierra la llamada;
- un objetivo reutilizable;
- una primera frase opcional;
- el contexto comercial que sí puede compartir con terceros;
- la información que debe preguntar y obtener;
- una voz opcional de la cuenta ElevenLabs del propietario;
- si puede recibir llamadas, hacer llamadas o ambas;
- si será el agente predeterminado para entrantes, salientes o cada canal por
  separado.

Se administran desde **Llamadas → Agentes de llamadas**, desde el chat con
`configurar_agente_llamadas`, o mediante `/v1/phone/agent-templates`. El primer
perfil creado se vuelve predeterminado para las direcciones que atiende. Solo
puede existir un predeterminado saliente y un predeterminado entrante por
usuario. Pueden ser dos identidades completamente distintas, con voces,
criterios y alcances diferentes.

## Por qué cada llamada guarda una copia

Al preparar una llamada, `phone_calls` copia el destinatario, nombre, prompt,
perfil operativo, apertura y voz de la plantilla. `agent_template_id` conserva
la procedencia, pero no es la fuente de verdad de una llamada ya creada. Por
eso:

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
  → exige nombre del destinatario, número, objetivo y agente exacto
  → resuelve la plantilla explícita sin sustituir identidades
  → guarda conversación + borrador + snapshot
  → muestra persona + número + agente + objetivo para verificación
  → cuatro confirmaciones explícitas
  → commit en PostgreSQL
  → Twilio REST inicia la llamada
  → webhook firmado entrega saludo con la voz del agente
  → TTS ElevenLabs del tenant + TwiML Play/Gather conversan por turnos
  → LLM rápido usa persona telefónica + snapshot + objetivo
  → webhook firmado de estado actualiza la verdad del proveedor
  → cierre terminal guarda resumen + actividad de forma idempotente
  → job reclama una sola vez el push genérico best-effort
```

La ruta de chat `llamar_contacto` continúa siendo `dangerous=True`. Antes de
invocarla el modelo debe tener persona destinataria, número internacional,
objetivo y agente exacto. Si falta la persona o el agente, Edecan pregunta en el
chat y no llama. Si el agente no existe o coincide con varios, detiene la
acción y muestra los nombres disponibles; nunca sustituye silenciosamente el
agente solicitado por otro. La tarjeta sensible vuelve a mostrar los cuatro
datos y Twilio solo se invoca después de la aprobación humana.
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
- Consentimiento, verificación de persona, número, agente y objetivo, más
  auditoría, siguen siendo obligatorios.
- La plantilla no programa llamadas, no crea campañas y no decide destinatarios.

## Llamadas entrantes y voces

El agente entrante predeterminado atiende las llamadas que llegan al número
Twilio conectado. Puede ser distinto del agente saliente predeterminado. Al
conectar un número nuevo, Edecan configura automáticamente su
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
verificación de persona, número, agente y objetivo seguida de aprobación final.
