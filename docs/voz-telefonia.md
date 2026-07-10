# Voz y telefonía

Edecán separa dos capacidades distintas, con distinto costo de operación y distinto riesgo legal: **voz web** (núcleo, gratuita) y **telefonía** (`premium/`, licencia comercial). Este documento explica la diferencia técnica y, sobre todo, el checklist de cumplimiento **obligatorio** antes de que cualquier instancia (self-host o hosted) haga una sola llamada o envíe un solo SMS saliente de verdad.

> Este documento describe el diseño técnico y una lista de controles legales conocidos, **no es asesoría legal**. Las leyes de telemarketing, protección al consumidor y grabación de llamadas varían por país y por estado/provincia, y cambian con el tiempo. Antes de operar campañas reales de voz/SMS —especialmente salientes y a escala— consulta a un abogado en cada jurisdicción donde tengas destinatarios.

## Voz web vs. telefonía — comparación rápida

| | Voz web (`voice.web`) | Telefonía (`voice.telephony`, `premium/`) |
|---|---|---|
| Paquete | Núcleo (`edecan_voice`, Apache-2.0) | `premium/` (licencia comercial) |
| Qué es | Push-to-talk en el navegador: hablas, se transcribe, el agente responde, se sintetiza en audio | Llamadas y SMS reales por teléfono, con número propio del tenant |
| Credenciales | **Del propio tenant** (Deepgram/ElevenLabs), conectadas en `/v1/credentials/voice/{stt,tts}` y cifradas en el `TokenVault` — sin credencial propia cae directo a `stub` offline, NUNCA a una clave de plataforma en `.env` (ver [`credenciales.md`](./credenciales.md), WP-V3-02). Polly es la excepción: no tiene credencial propia del tenant (se autentica con la identidad AWS del proceso), así que solo se acepta con `EDECAN_LOCAL_MODE=true` (self-host de un único tenant) — ver `credenciales.md`. | Cuenta de Twilio **del propio tenant**, conectada vía el panel, cifrada en el `TokenVault` |
| Endpoints | `POST /v1/voice/transcribe`, `POST /v1/voice/speak` | `POST /v1/voice/twilio/{incoming,gather,sms,status}` (webhooks TwiML) + `WS /v1/twilio/media` (Media Streams, beta opt-in — ver "Interrupciones naturales (beta)" más abajo) |
| Riesgo legal | Bajo — el usuario inicia la interacción voluntariamente en su propia sesión | Alto si no hay consentimiento/horario/opt-out — puede implicar sanciones regulatorias (ver checklist abajo) |
| Disponible en | Todos los planes (incluido `free_selfhost`) | Solo `hosted_pro`/`hosted_business` (u operadores self-host que instalen `edecan_premium` bajo su propia licencia comercial) |

## Voz web (núcleo)

Flujo (`ARCHITECTURE.md` §4): el usuario graba audio en el navegador → `POST /v1/voice/transcribe` lo convierte a texto con el `STTProvider` resuelto → ese texto entra como un turno normal del agente (mismo loop de herramientas que el chat) → la respuesta de texto se convierte a audio con `POST /v1/voice/speak` usando el `TTSProvider` resuelto.

- **Resolución por tenant, en dos niveles, sin paso de plataforma** (WP-V3-02, `DIRECCION_ACTUAL.md` "Modelo de credenciales: TODO lo trae el cliente"; detalle completo en [`credenciales.md`](./credenciales.md)): **(1) tenant** — si conectó su propia credencial vía `PUT /v1/credentials/voice/stt`/`/tts` (cifrada en el `TokenVault`, mismo mecanismo que ya usaba Twilio), se usa SIEMPRE esa; **(2) stub** — si no (o si algo falla leyéndola), `StubSTT`/`StubTTS` (offline, sin llamadas externas, determinista). `apps/api/edecan_api/routers/voice.py` implementa esta prioridad y deliberadamente NUNCA llama a `edecan_voice.registry` con `VOICE_STT_PROVIDER`/`VOICE_TTS_PROVIDER`/`DEEPGRAM_API_KEY`/`ELEVENLABS_API_KEY`/`POLLY_VOICE` de `Settings` (ver [`configuracion.md`](./configuracion.md)) — ningún tenant sin credencial propia reutiliza una API key de voz compartida de la plataforma, y ningún operador de plataforma ve ni administra la credencial de voz de un tenant que conectó la suya propia.
- **Proveedores soportados**: `deepgram` para STT; `elevenlabs`/`polly` para TTS (`polly` solo con `EDECAN_LOCAL_MODE=true`, ver fila "Credenciales" arriba y [`credenciales.md`](./credenciales.md)).
- **Voz por persona**: si `PersonaConfig.voice_id` está definido, se usa esa voz en vez del default (ver [`personalizacion-nivel-dios.md`](./personalizacion-nivel-dios.md)).
- **Cuotas**: gatea por el flag `voice.web` y el límite `limits.voice_minutes_month` del plan del tenant (§10.13); `free_selfhost` no tiene límite. Se mide igual sin importar qué nivel de la resolución respondió — el minutaje es del tenant, no del proveedor.
- No hay número de teléfono involucrado, no hay "llamada saliente" — es audio dentro de la propia sesión del usuario. Por eso el riesgo de cumplimiento es mucho menor: es equivalente a que el usuario le hable a un micrófono, no a que el sistema llame a un tercero.

## Voces y clonación autorizada

**Voces del tenant** (`GET /v1/voz/voces`, WP-V5-10, `ROADMAP_V2.md` §6.3 "voz avanzada") reutiliza la MISMA credencial de TTS que ya conectaste en `PUT /v1/credentials/voice/tts` — no hay una credencial nueva que conectar. Si esa credencial es `elevenlabs`, ves el catálogo real de tu cuenta (voces de stock + tus propios clones ya creados); si es `polly` o no conectaste nada, ves 2 voces de ejemplo offline (mismo criterio "tenant → stub, sin paso de plataforma" que el resto de este documento). Las herramientas del agente `listar_voces`/`sintetizar_voz` (`edecan_voice.tools`, flag `voice.web`) siguen exactamente esta misma resolución — el agente puede listar voces y convertir texto en audio con ellas, pero no puede clonar ninguna (ver más abajo).

### El agente JAMÁS clona una voz

Clonar la voz de una persona es una acción con implicaciones legales serias (ver "Disclaimers legales" abajo) que exige que un humano, con el consentimiento ya en la mano, la confirme explícitamente en la interfaz — nunca algo que el modelo decida hacer por su cuenta en medio de una conversación. Por diseño:

- Ninguna `Tool` de `edecan_voice.tools` (ni de ningún otro paquete) expone una acción de clonación — el catálogo de herramientas del agente (`listar_voces`, `sintetizar_voz`) es intencionalmente incompleto en este sentido.
- La ÚNICA vía de clonación de todo el producto es `POST /v1/voz/clones` (`apps/api/edecan_api/routers/voz_avanzada.py`), un endpoint HTTP pensado para la página **Voz** de la app (`apps/web/src/app/(app)/app/voz`), donde un humano sube los archivos y marca la casilla de consentimiento — nunca invocable por el agente conversacional.
- El flag de plan `voice.cloning` gatea todo `/v1/voz/clones/*` (`POST`/`GET`/`DELETE`): sin ese flag, `403` — fail-closed por diseño, nadie clona nada hasta que su plan lo habilite explícitamente.

### Flujo de clonación (`POST /v1/voz/clones`)

1. El usuario sube: un **nombre** para la voz, entre **1 y 5 muestras de audio** de la voz a clonar, y la **grabación del consentimiento** — un archivo de audio aparte donde la PERSONA cuya voz se va a clonar dice, en su propia voz, que autoriza esta clonación. Este último archivo es obligatorio: sin él, `400`.
2. El usuario marca la casilla de declaración explícita (texto exacto, pinned, en `apps/web/src/app/(app)/app/voz/page.tsx` y en el router):
   > «Declaro que tengo el consentimiento explícito y grabado de la persona cuya voz voy a clonar, y que la grabación adjunta lo demuestra.»

   El backend exige literalmente `attestation=true` en el cuerpo del formulario — cualquier otro valor (u omitirlo) es `400` con el requisito legal explicado, nunca un fallo silencioso ni un valor por defecto en `true`.
3. La grabación del consentimiento se sube a S3 (mismo layout que `POST /v1/files`) y se registra en la tabla `voice_consents` (`attestation=true`, `status='attested'`) **antes** de intentar nada contra ElevenLabs — la evidencia de que la persona consintió queda guardada aunque el paso técnico de clonar falle después (p. ej. porque el tenant todavía no conectó ElevenLabs).
4. Recién ahí se valida que la credencial de voz (TTS) del tenant sea `elevenlabs` — si no, `400` pidiendo conectarla primero en `PUT /v1/credentials/voice/tts` (la fila de consentimiento ya insertada en el paso 3 queda con `provider_voice_id` vacío).
5. Con la `api_key` **del tenant** (nunca una compartida de plataforma), `edecan_voice.cloning.crear_clon` sube las muestras a ElevenLabs (`POST /v1/voices/add`) y guarda el `voice_id` que devuelve en la misma fila de `voice_consents`.

### Qué guarda Edecán y por qué

| Dato | Dónde | Por qué |
|---|---|---|
| `voice_name` | `voice_consents` | Identifica la voz clonada en la UI y en `listar_voces`. |
| `attestation` | `voice_consents` | Constancia de que el usuario declaró explícitamente tener el consentimiento — nunca `true` por defecto. |
| `consent_file_id` | `voice_consents` → `files` → S3 | La grabación en sí: la evidencia real de que la persona autorizó la clonación, no solo una casilla marcada. |
| `provider_voice_id` | `voice_consents` | El `voice_id` de ElevenLabs, para poder sintetizar con esa voz y para poder borrarla ahí si se revoca. |
| `status` (`attested`/`revoked`) | `voice_consents` | Si el clon sigue vigente o fue revocado. |

**La fila de `voice_consents` NUNCA se borra**, ni siquiera al revocar el clon (`DELETE /v1/voz/clones/{id}`): es evidencia legal de que hubo una declaración de consentimiento en una fecha concreta, no una simple referencia técnica al clon de ElevenLabs. Revocar marca `status='revoked'` y borra el clon técnico en ElevenLabs en modo **best-effort** (`edecan_voice.cloning.borrar_clon`, con manejo de error: si ElevenLabs falla o ya no responde, la revocación local sigue adelante igual — nunca se bloquea por un proveedor externo caído).

### Límite conocido: sin reintento sobre una fila existente

Si el paso 4 del flujo de arriba falla (el tenant no tenía ElevenLabs conectado todavía), la fila de `voice_consents` queda con `provider_voice_id` vacío y no hay hoy un endpoint para "reintentar la clonación" sobre esa misma fila — el usuario tiene que volver a enviar el formulario completo (incluida la grabación de consentimiento) una vez conecte ElevenLabs. Es una limitación de UX conocida, no un bug: prioriza nunca perder la evidencia ya capturada sobre la conveniencia de un reintento de un clic.

### Disclaimers legales de uso de voz ajena

> Igual que el resto de este documento: esto describe el diseño técnico y una lista de controles conocidos, **no es asesoría legal**.

- Clonar la voz de una persona sin su consentimiento puede violar derechos de imagen/voz (*right of publicity*), leyes de protección al consumidor (usar una voz clonada para hacerse pasar por alguien, p. ej. en fraude o estafa telefónica, es ilegal en la gran mayoría de jurisdicciones), y en algunos países leyes específicas de IA/deepfakes de voz que exigen divulgación o prohíben ciertos usos directamente.
- El consentimiento debe venir de la persona **titular de la voz**, no de quien la está clonando — clonar tu propia voz no tiene este problema, pero clonar la de otra persona (familiar, colega, un actor de voz, una figura pública) sí lo tiene, incluso si esa persona es conocida tuya y "seguro que no le molesta".
- Usar una voz clonada para hacerse pasar por alguien en una llamada telefónica real combina este riesgo con el checklist de telefonía de más arriba (consentimiento del DESTINATARIO de la llamada, identificación como sistema automatizado) — son dos consentimientos distintos: el de la persona cuya voz se clona, y el del destinatario que recibe la llamada.
- Guarda la grabación de consentimiento el tiempo que tu jurisdicción exija como evidencia (ver también [`cumplimiento/privacidad.md`](./cumplimiento/privacidad.md)) — Edecán nunca la borra automáticamente, pero borrarla manualmente de todas formas (fuera del producto) puede dejarte sin la evidencia que necesitarías si alguna vez se cuestiona el consentimiento.

## Podcasts (mismo router que voces/clones, flag distinto)

`POST /v1/voz/podcasts`, `GET /v1/voz/podcasts` y `GET /v1/voz/podcasts/{id}` (WP-V6-04) viven en el MISMO router que las voces/clones de arriba (`apps/api/edecan_api/routers/voz_avanzada.py`, prefix `/v1/voz`) pero exigen un flag de plan **distinto**: `tools.podcast`, nunca `voice.cloning` — cada endpoint de este router revisa el flag de SU PROPIA capacidad, no uno genérico compartido entre las dos (verificado endpoint por endpoint, en ambas direcciones, con flags aislados de un plan real — `voice.cloning=true`/`tools.podcast=false` y viceversa — en `apps/api/tests/test_v7_sweep_voz.py`; los 4 planes reales de hoy siempre traen ambos flags iguales, `edecan_schemas.plans.PLANES`, así que por sí sola la matriz de planes vigente no puede distinguir "cada endpoint exige el suyo" de "los dos comparten un flag por error"). El detalle completo (guion, límites de tamaño, proveedores, ensamblado con ffmpeg, formatos, la tool de chat `crear_podcast` vs. este endpoint REST) vive en [`creatividad.md`](./creatividad.md) sección "Podcasts y efectos de sonido" — aquí solo lo que comparte con voz web/clonación de este documento:

- **Mismo bring-your-own que el resto de este documento, pero con un matiz**: el job `generate_podcast` (`apps/worker/edecan_worker/handlers/generate_podcast.py`) sintetiza SIEMPRE con el TTS que el propio TENANT conectó (`PUT /v1/credentials/voice/tts`) — jamás con una credencial de plataforma, mismo criterio "tenant → stub, jamás plataforma" de arriba. El matiz: sin credencial propia, el job **no falla** — cada segmento sale como un WAV corto de silencio (stub) y el podcast completo se guarda igual, en formato `wav`, para que el tenant tenga un archivo real (aunque silencioso) que reemplazar más tarde si conecta su credencial. Es lo opuesto a un LLM sin conectar (`TenantLLMNotConnectedError`, que sí hace fallar el job claramente) — a propósito: sin LLM el job no puede hacer nada útil, sin TTS propio sí puede entregar *algo*.
- **Registro operativo, no evidencia legal**: a diferencia de `voice_consents` (arriba, nunca se pierde en un rollback), la fila `podcasts` no necesita ese mismo cuidado — si `POST /v1/voz/podcasts` falla encolando el job justo después de insertar la fila, el rollback automático se lleva la fila entera (todo o nada): nunca queda una fila `'pending'` huérfana que ningún job vaya a procesar.
- **Limitación conocida**: si la síntesis/ensamblado y la subida a S3 tienen éxito pero la escritura de Postgres que sigue (`INSERT INTO files`/marcar `'done'`) falla por un blip de conectividad, el handler intenta un best-effort de borrar el objeto de S3 recién subido (nunca bloquea el `status='error'` ni la excepción real si el propio borrado también falla) — pero un reintento del job de todas formas vuelve a sintetizar TODO el guion desde cero (gasto real si el proveedor es de pago): no hay una clave de idempotencia por segmento. Detalle en el docstring de `generate_podcast.py`, sección "Audio huérfano en S3...".

## Telefonía (premium)

La telefonía real —llamadas y SMS a números de teléfono, entrantes o salientes— vive en `premium/` y usa **Twilio por tenant** (`ARCHITECTURE.md` §4 y §10.10):

- Cada tenant conecta **su propia cuenta de Twilio** (Account SID + Auth Token) desde el panel; nunca hay una cuenta de Twilio compartida de la plataforma. Se guarda cifrada en el `TokenVault` bajo el connector key `"twilio"` (`TokenBundle.access_token` = Auth Token, `scopes` = `[ACCOUNT_SID]`).
- `edecan_premium.telephony.TwilioTenantClient.start_call(...)` y `.send_sms(...)` son el **único** punto de salida de llamadas/SMS del sistema, y **exigen** los controles del checklist de abajo antes de ejecutar nada — no hay una vía alterna que los salte.
- Los webhooks entrantes de Twilio (`POST /v1/voice/twilio/incoming`, `/gather`, `/sms`, `/status`) validan estrictamente el header `X-Twilio-Signature` para descartar solicitudes falsificadas.
- Las **campañas** (`campaigns` + `campaign_targets`, flag de plan `campaigns`) las procesa `edecan_premium.campaigns.handle(...)`: hasta 10 destinatarios por paso del job, re-encolando el resto — cada destinatario individual sigue pasando por los mismos controles de consentimiento/horario que una llamada o SMS suelto.
- Toda llamada y SMS deja rastro en `audit_log` (quién/qué/cuándo) y en `usage_events` (para medición de plan y facturación).

## Interrupciones naturales (beta)

**Qué hace.** El ciclo de voz de siempre (`/incoming` → `<Say>` + `<Gather input="speech">` → `/gather` → turno del agente → nuevo `<Gather>`) es estrictamente por turnos: mientras Twilio reproduce la respuesta del bot, no escucha nada — si quien llama empieza a hablar encima, Twilio lo descarta. **Interrupciones naturales** cambia esto usando [Twilio Media Streams](https://www.twilio.com/docs/voice/media-streams) en vez de `<Gather>`: un WebSocket bidireccional (`WS /v1/twilio/media`, `premium/edecan_premium/media_streams.py`) por el que Twilio manda el audio del llamante en vivo (μ-law 8 kHz, 20 ms por mensaje) y nosotros mandamos de vuelta la voz del bot en el mismo formato. Un detector de actividad de voz por energía (`VadEnergia`, puro Python, sin ML) decide cuándo el llamante terminó de hablar (para transcribir y responder) y, más importante: si detecta que el llamante **vuelve a hablar mientras el bot todavía está reproduciendo su respuesta**, corta esa reproducción al instante (evento `clear` del protocolo de Twilio) — el llamante puede interrumpir al bot igual que interrumpiría a una persona, en vez de tener que esperar a que termine de hablar.

### Cómo activarlo

Está apagado por defecto y es **beta, opt-in explícito** — nada de esto cambia si no lo prendés a propósito:

1. **`TWILIO_MEDIA_STREAMS_ENABLED=true`** (`Settings`, `ARCHITECTURE.md` §15.h) en el proceso de `apps/api`.
2. **URL pública `wss://` alcanzable por Twilio.** El `<Stream url="...">` que arma `/incoming` apunta a `{PUBLIC_BASE_URL sin esquema}/v1/twilio/media` (o, si `PUBLIC_BASE_URL` no está configurado, al mismo host por el que Twilio llegó a `/incoming`) — Twilio necesita poder abrir un WebSocket contra ese host de verdad (con TLS válido; `wss://localhost:...` solo sirve para pruebas locales con un túnel tipo ngrok, nunca para una llamada real).
3. **`phone_agent` configurado** en `app.state` — el mismo requisito que ya tenía `/gather`: sin un agente conectado, `/incoming` sigue usando `<Gather>` aunque el flag esté prendido.
4. **STT/TTS del tenant conectados** (`PUT /v1/credentials/voice/{stt,tts}`, igual que la voz web — ver más arriba). Sin credencial propia del tenant, la sesión de Media Streams sigue funcionando pero degrada a un stub offline (transcripción fija / silencio) — nunca usa una API key de plataforma (mismo principio "tenant → stub, jamás plataforma" del resto de este documento). Deepgram (STT) y ElevenLabs (TTS, con `output_format=ulaw_8000` — el propio formato de salida μ-law que ElevenLabs ofrece pensado para telefonía) son los dos proveedores soportados hoy.

**Resuelto (barrido v7, WP-V7-12):** `apps/api/edecan_api/main.py::create_app()` ahora inyecta `app.state.settings = settings` al montar `edecan_premium.twilio_router.router`, junto a `app.state.get_session`/`app.state.make_vault` que ya estaban — con esto `TWILIO_MEDIA_STREAMS_ENABLED` se lee correctamente en un despliegue real. Hasta esta corrección, el flag existía y el código que lo consume ya estaba probado, pero el "cableado" final entre `Settings` y `app.state` faltaba ese paso; la función quedaba construida y con tests verdes, pero inalcanzable en producción incluso con la variable de entorno en `true`.

### Limitaciones honestas (v1)

- **Sin streaming STT.** La transcripción es *batch*: se acumula el audio de toda la locución (hasta detectar silencio sostenido) y RECIÉN AHÍ se manda de una sola vez a Deepgram. El barge-in en sí es instantáneo (cortar audio que ya se está reproduciendo no depende de STT), pero la SIGUIENTE respuesta del bot sigue tardando el ciclo completo: transcribir → turno del agente → sintetizar. No hay transcripción incremental palabra-por-palabra ni respuesta anticipada.
- **VAD por energía, no por semántica.** `VadEnergia` decide "está hablando" por volumen (RMS sobre un umbral), no por contenido — un ruido fuerte de fondo puede disparar un falso "empezó a hablar" (y por lo tanto un barge-in innecesario), y una voz muy baja puede no activar el umbral. No hay cancelación de eco: si el propio audio del bot se filtrara de vuelta al micrófono del llamante (no debería pasar en una llamada telefónica normal, donde el audio del bot solo sale por el canal `media` saliente y Twilio no lo re-inyecta al canal entrante), el VAD podría malinterpretarlo como habla humana.
- **Sin grabación ni transcripción persistida.** Igual que el resto de telefonía (grabación desactivada por defecto, ver el checklist de abajo), esta función no guarda el audio ni las transcripciones intermedias en ningún lado — solo lo que ya guardaba `/gather` (nada, hoy) sigue igual.
- **Opt-out y consentimiento: mismas garantías, mismo camino.** El texto transcrito de cada locución pasa por el mismo `_is_optout`/`_apply_optout` que ya usa `/gather` (inyectados, no reimplementados — ver el docstring de `media_streams.py`); decir "STOP"/"BAJA" en medio de una llamada con Media Streams revoca el consentimiento y cierra la sesión igual que en el flujo clásico.

### Diagrama del flujo de eventos

```
Llamante                Twilio                    Edecán (premium)
   |                       |                              |
   |--- marca, suena ----->|                               |
   |                       |--- POST /incoming ----------->|
   |                       |<-- TwiML <Say> + <Connect>    |
   |                       |    <Stream url="wss://.../v1/twilio/media?t=TOKEN">
   |                       |
   |                       |=== WS connect (?t=TOKEN) ====>|  el WS valida el token
   |                       |                               |  (HMAC con el Auth Token
   |                       |                               |  del tenant) ANTES de aceptar
   |                       |<====== accept ================|
   |                       |--- {event:"start", ...} ----->|  arranca SesionMediaStream
   |                       |
   |--- "hola, quiero" --->|--- {event:"media", ulaw} ---->|  VAD: hablando=true, buffer
   |--- "  información" -->|--- {event:"media", ulaw} ---->|  ...
   |--- (silencio) ------->|--- {event:"media", ulaw} ---->|  VAD: hangover -> hablando=false
   |                       |                               |  -> transcribe buffer (Deepgram)
   |                       |                               |  -> turno del phone_agent
   |                       |                               |  -> sintetiza (ElevenLabs, μ-law)
   |<==== reproduce ======|<-- {event:"media", ulaw} ------|  frames de 20 ms, en cola
   |<==== reproduce ======|<-- {event:"media", ulaw} ------|
   |--- "espera, quiero -->|--- {event:"media", ulaw} ---->|  VAD detecta habla EN MEDIO
   |     preguntar algo"   |                               |  de la reproducción -> BARGE-IN
   |                       |<-- {event:"clear"} -----------|  corta lo que Twilio tenía
   |    (silencio abrupto) |                               |  en cola de reproducción
   |                       |                               |  vuelve a "escuchando"
   |    ...                |                               |  ...
   |--- cuelga ------------|--- {event:"stop"} ----------->|  cierra la sesión, cancela
   |                       |                               |  cualquier tarea de fondo
```

### Auditoría de la sesión

**Auditoría de la sesión (WP-V7-06).** Cada conexión WS deja una fila `audit_log`
(`action="telephony.media_stream.session"`) al cerrarse, con `call_sid`, número de
turnos, si terminó en opt-out, y la duración de la sesión — en su propia sesión
corta e independiente, para que un fallo de infraestructura durante la llamada
nunca le impida quedar registrada. La duración de la llamada telefónica en sí ya
la mide `POST /v1/voice/twilio/status` (`voice_seconds`, igual que el flujo
`<Gather>` clásico). **Limitación conocida, sin corregir**: el consumo real de
Deepgram/ElevenLabs del tenant durante la sesión (llamadas HTTP con costo real,
aunque con su propia credencial) no se mide en `usage_events` —
`usage_events.kind` es un enum cerrado (`ARCHITECTURE.md` §10.3) sin ningún valor
apto para esto sin una migración nueva.

## Checklist legal para operar llamadas y SMS

Estos seis controles son condiciones **obligatorias** de diseño, no opcionales de configuración. Aplican tanto a llamadas/SMS individuales como a pasos de campaña.

### 1. Consentimiento previo verificable

Ninguna llamada o SMS sale sin un registro en la tabla `consents` (`tenant_id, phone_e164, kind: sms|voice, granted_at, revoked_at nullable, source`) que demuestre que **ese número específico** aceptó recibir **ese tipo específico** de contacto (`sms` y `voice` se consienten por separado). `source` debe dejar constancia de cómo se obtuvo el consentimiento (formulario web, respuesta explícita a un mensaje anterior, importación con evidencia externa, etc.) — un consentimiento no verificable equivale legalmente a no tener consentimiento. Es la base de cumplimiento del *Telephone Consumer Protection Act* (TCPA) en EE. UU. y de regímenes equivalentes de protección al consumidor/telemarketing en otras jurisdicciones (ver también [`cumplimiento/privacidad.md`](./cumplimiento/privacidad.md)).

`compliance.grant_consent` (`premium/edecan_premium/compliance.py`) es la única función que debe usarse para insertar esa fila. El punto de entrada auditado hacia esa función es `POST /v1/consents` (`apps/api/edecan_api/routers/consents.py`, montado solo si `edecan_premium` está instalado): requiere `Authorization: Bearer` de un usuario del tenant y el flag de plan `voice.telephony`, y traduce cualquier `kind`/`source` inválido a `400`/`422` — ver `docs/api.md`. `has_consent` sigue fallando cerrado (sin fila, no se contacta).

> **Limitación conocida:** `apps/web/src/app/(app)/app/conectores` tiene un formulario mínimo para otorgar consentimiento a un número a la vez sobre `POST /v1/consents`, pero todavía no hay un flujo de importación masiva con evidencia — suficiente para operar sin escribir SQL directo, pero no pensado para migrar una lista grande de contactos de una sola vez. Ver el riesgo (ya marcado como resuelto en su forma original, con esta nota como pendiente residual) en `RIESGOS.md`.

### 2. Registro A2P 10DLC en EE. UU.

Para enviar SMS **application-to-person** (A2P) a números de EE. UU. usando un *long code* (10-digit long code, un número de teléfono normal en vez de un short code), los carriers estadounidenses (a través de The Campaign Registry, gestionado por Twilio en tu nombre) exigen registrar:

- Una **marca** (*brand*) — identifica a la organización/tenant que envía los mensajes.
- Una o más **campañas** (*campaigns*) — describen el caso de uso (p. ej. "recordatorios transaccionales", "notificaciones conversacionales") y el tipo de contenido que se enviará.

Sin este registro, los carriers de EE. UU. **filtran o bloquean directamente** el tráfico A2P no registrado (tasas de entrega degradadas o mensajes descartados sin aviso) — no es solo una recomendación de buenas prácticas, es una condición operativa para que el SMS llegue. Este registro se hace **por tenant** que vaya a enviar SMS reales a números de EE. UU. (cada tenant conecta su propia cuenta de Twilio, así que el registro A2P 10DLC de esa cuenta es responsabilidad del tenant, con soporte de la documentación de Twilio). Fuera de EE. UU. las reglas varían por país (algunos exigen *sender ID* registrado, otros no); confírmalo para cada mercado antes de enviar SMS ahí.

### 3. Ventana horaria 08:00–21:00 local del destinatario

`TwilioTenantClient` calcula la hora **local del número de destino** (no la del servidor ni la del tenant) y rechaza iniciar la llamada o el SMS fuera de la ventana **08:00–21:00**. Es el estándar más común en regulaciones de telemarketing (incluida la práctica bajo TCPA en EE. UU. y equivalentes en LATAM) para evitar contacto no solicitado en horas de descanso. Si tu jurisdicción exige una ventana distinta (más estrecha, o que excluya domingos/festivos), ese ajuste debe hacerse explícito antes de operar ahí — la ventana pinned en el código es un mínimo razonable, no un techo legal universal.

> **Limitación conocida:** no hay columna de zona horaria en `contacts`/`campaign_targets`, así que la "hora local del destinatario" se aproxima desde el prefijo E.164 (`telephony.guess_timezone`) a una zona representativa por país — no es un servicio real de numbering plans. Para países que abarcan varias zonas horarias reales (notablemente `"+1"`, EE. UU./Canadá: hasta 3 horas de Este a Pacífico; y `"+52"`, México: Ciudad de México vs. Baja California), `telephony.is_quiet_hours` aplica un margen de seguridad (`_MULTI_ZONE_MARGIN_HOURS`) que exige que la ventana se cumpla incluso en el peor caso, en vez de confiar en la zona representativa — ante la duda, se prefiere no contactar (y reintentar más tarde) a arriesgarse a violar la ventana real del destinatario. Ese margen no cubre Alaska, Hawái ni el este de Canadá/Terranova (una porción pequeña del numbering plan "+1"); ver el riesgo registrado en `RIESGOS.md`.

### 4. Opt-out automático (STOP/BAJA)

Todo destinatario puede darse de baja respondiendo `STOP` (o su equivalente en español, `BAJA`) a un SMS, o expresándolo verbalmente en una llamada. El sistema debe:

- Reconocer ambas palabras clave (y sus variantes razonables) sin intervención humana.
- Marcar `consents.revoked_at` de inmediato para ese `phone_e164` + `kind`.
- Detener cualquier `campaign_target` pendiente para ese número (`status: optout`).
- Confirmar la baja al usuario en un único mensaje de cierre (no reabrir la conversación de marketing después de la baja).

Un opt-out que tarda en aplicarse, o que sigue enviando mensajes tras la palabra clave, es una de las infracciones más comunes y más perseguidas en regulación de telemarketing — trátalo como control crítico, no como detalle de UX.

### 5. Identificación como sistema automatizado

El bot **siempre** se identifica como un asistente automatizado al inicio del contacto — nunca se hace pasar por una persona. Esto aplica tanto a la primera línea de una llamada (`<Say>` inicial en el webhook `/incoming`) como al primer SMS de una conversación o campaña. Varias jurisdicciones exigen explícitamente esta divulgación para llamadas con voz sintética/grabada o bots conversacionales; además de ser un requisito legal en muchos sitios, es simplemente honesto con el destinatario.

### 6. Grabación desactivada por defecto y leyes de una/dos partes

Twilio puede grabar llamadas, pero **Edecán la deja desactivada por defecto**. Si un tenant la activa explícitamente:

- Debe **anunciarse al inicio de la llamada** que se está grabando (p. ej. añadir esa línea al `<Say>` inicial), antes de que continúe la conversación.
- Debe respetarse el régimen de consentimiento **más estricto** que aplique a los participantes de la llamada. En EE. UU. esto varía por estado: la mayoría son de **"una parte"** (basta que una de las partes —el propio sistema— consienta), pero varios estados (California, Florida, Illinois, Pensilvania, entre otros) son de **"dos partes"** (o más precisamente, "todas las partes"): se requiere el consentimiento de **todos** los participantes de la llamada, no solo de quien graba. Si no sabes con certeza en qué estado/país está el destinatario, trata la llamada como si aplicara el régimen de dos partes — es la postura conservadora y segura por defecto.
- Fuera de EE. UU., muchos países tienen sus propias leyes de interceptación/grabación de comunicaciones (a menudo bajo el paraguas de protección de datos personales, ver [`cumplimiento/privacidad.md`](./cumplimiento/privacidad.md)) que también pueden exigir consentimiento previo explícito para grabar.

## Resumen operativo

| Control | Dónde vive en el código | Qué pasa si falla |
|---|---|---|
| Consentimiento verificable | Tabla `consents`, chequeado en `TwilioTenantClient` | Llamada/SMS bloqueado antes de salir |
| A2P 10DLC (EE. UU.) | Registro externo en Twilio/The Campaign Registry, por cuenta del tenant | Carrier filtra o descarta el SMS — no es un bloqueo del código, es un bloqueo de red |
| Ventana 08:00–21:00 destinatario | `TwilioTenantClient` | Llamada/SMS bloqueado antes de salir |
| Opt-out STOP/BAJA | Manejo de webhook + `consents.revoked_at` + `campaign_targets.status` | Riesgo legal directo si no se implementa correctamente |
| Identificación como bot | Plantilla TwiML/SMS inicial | Riesgo legal + daño reputacional |
| Grabación off por defecto | Config de la llamada en `TwilioTenantClient` | Riesgo legal si se activa sin anuncio/consentimiento |

Ver también: [`api.md`](./api.md) para el detalle de las rutas de telefonía, [`seguridad-modelo-amenazas.md`](./seguridad-modelo-amenazas.md) para el riesgo de webhooks sin validar firma, y `RIESGOS.md` (raíz del repo) para el registro vivo de riesgos legales/de cumplimiento del proyecto.
