# Speech Engine — Voz gestionada (ElevenLabs)

> Estado: implementado. El proveedor es un
> servicio PAGO explícito: nada se factura sin la API key del tenant, la
> activación explícita y el consentimiento por sesión.

## Qué es

Speech Engine conecta el móvil/escritorio con ElevenLabs por **WebRTC**; a su
vez ElevenLabs conecta al **callback WSS público de Edecan** y le envía los
transcripts. Edecan corre **su propio LLM/herramientas** (el cerebro de
siempre: persona, memoria, historial, tools, aprobaciones) y devuelve
**deltas de texto**. ElevenLabs se ocupa de ASR, TTS, detección de turno e
interrupciones.

La conversación es UNA sola: escribir y hablar son dos modos del mismo chat.
No hay un segundo historial ni un segundo cerebro.

```
móvil/escritorio  ⇄ WebRTC ⇄  ElevenLabs  ⇄ WSS ⇄  callback de Edecan  →  LLM/tools
     (token efímero)          (ASR/TTS/turnos)        (este repo)
```

## Por qué no es el pipeline Deepgram/PCM

El pipeline anterior (`/v1/voice/realtime`, `LlamadaViewModel`,
`VozRecorderContinuo`, `ReproductorPCMStream`) es un stack propio
Deepgram/PCM con VAD y reproducción manuales. Sufrió turnos muertos, eco y
reproducción incorrecta. **Ese pipeline NO se disfraza de "voz gestionada"**:
el modo gestionado usa exclusivamente el stack `audio/turn/interruption` de
ElevenLabs (SDK oficial del cliente, WebRTC) y el callback WSS del lado
servidor. Los dos modos no corren a la vez.

## Componentes

| Pieza | Archivo | Rol |
|---|---|---|
| Migración | `packages/db/alembic/versions/0073_speech_engine.py` | `voice_preferences`, `speech_engine_sessions`, `speech_engine_events` (RLS) |
| Modelos | `packages/db/edecan_db/models.py` | `VoicePreference`, `SpeechEngineSession`, `SpeechEngineEvent` |
| Repo | `apps/api/edecan_api/repo.py` | claims atómicos `(session_id, event_id)`, upsert de preferencias, sesiones |
| Preferencias | `apps/api/edecan_api/routers/speech_engine.py` | `GET/PUT /v1/voice/preferences` |
| Sesiones | ídem | `POST /v1/voice/speech-engine/sessions`, `GET .../{id}`, `POST .../{id}/end` |
| Callback WSS | ídem | `WS /v1/voice/speech-engine/ws/{session_id}` |
| Wire protocol | `apps/api/edecan_api/speech_engine_wire.py` | adaptador verificado del protocolo documentado por el SDK |
| Turnos | `apps/api/edecan_api/speech_engine_turn_service.py` | interlocutor rápido + delegación al agente completo |
| Credencial | `apps/api/edecan_api/routers/credentials.py` | `PUT/DELETE /v1/credentials/voice/speech-engine` |

## Modelos (TODO configurable, nada hardcodeado)

- **Interlocutor rápido** (`voice_model_id`): se elige del catálogo del chat
  (`config/modelos.yml` → `modelos_chat`). Scout es una opción disponible, no
  un requisito. Default = `modelo_chat_por_defecto()`.
- **Delegación** (`delegation_model_id`): modelo para el trabajo real
  (tools, memoria, aprobaciones). `NULL` = hereda el modelo que YA tiene la
  conversación (`conversations.chat_model`). **Arrancar voz nunca pisa
  `chat_model`/`chat_effort`**: la selección viaja por turno como
  `SeleccionDeModelo` y solo si el id está en el catálogo declarado.
- **TTS**: `tts_model_id` (default `eleven_turbo_v2_5`, modelo público de
  ElevenLabs) y `voice_id` (sin default hardcodeado: si no se fija, hereda la
  voz de la credencial TTS del tenant).

## Contrato de seguridad

1. **El path no autoriza nada.** `WS /ws/{session_id}` exige el JWT del
   proveedor (`X-Elevenlabs-Speech-Engine-Authorization`, HS256 con
   `SHA256(api_key)` de ElevenLabs; issuer/sub/exp verificados con la función
   pública `verify_speech_engine_jwt` del SDK oficial) y que la fila de
   sesión esté `active` y no expirada. `disable_auth` no existe en este
   código.
2. **Un engine por sesión.** Cada sesión crea su recurso (`ws_url` apunta al
   callback con SU `session_id`). El payload del proveedor jamás elige
   tenant/chat/usuario: todo sale de la fila de sesión persistida.
3. **Claims durables.** `speech_engine_events` con UNIQUE
   `(session_id, event_id)`: un replay del proveedor no re-ejecuta el turno
   (no duplica mensajes ni efectos de tools); solo reenvía el texto ya
   persistido.
4. **El transcript del proveedor es dato no confiable.** Solo se persiste el
   enunciado final NUEVO del usuario (redactado); nunca reescribe la historia
   de Edecan.
5. **Interrupción cancela generación, no efectos.** Un transcript nuevo
   cancela el turno anterior (texto/audio); las tools ya commiteadas quedan
   como están y los turnos siguientes ven su estado real.
6. **Sin doble ejecución.** El flujo delegado usa
   `already_persisted_input=True`; el enunciado se persiste UNA vez.
7. **Tokens, no keys.** El cliente recibe SOLO el `conversation_token`
   efímero de WebRTC; la API key del tenant vive en el vault y jamás sale.

## API

- `GET /v1/voice/preferences` → `{preference, credential_connected,
  paid_provider_notice, catalogs{voice_models,tts_voices,tts_models},
  defaults}`.
- `PUT /v1/voice/preferences` → `{enabled, provider, voice_model_id,
  delegation_model_id, delegation_effort, voice_id, tts_model_id,
  max_duration_seconds}`. Validaciones: ids en el catálogo del chat,
  esfuerzo ∈ {bajo,medio,alto}, duración 60–86400 s. `enabled=true` exige la
  credencial conectada.
- `POST /v1/voice/speech-engine/sessions` → `{conversation_id?,
  paid_consent:true}` (obligatorio). Crea/verifica la conversación canónica,
  provisiona el engine del proveedor (privacy `record_voice=false`,
  retención acotada, `bursting_enabled=false`, concurrencia 1), obtiene el
  token WebRTC y devuelve `{session_id, conversation_id,
  conversation_token, expires_at, token_expires_at, voice_model_id,
  delegation_model_id, tts_model_id, voice_id, language}`. Máximo
  `SPEECH_ENGINE_MAX_ACTIVE_SESSIONS` sesiones abiertas por usuario.
- `POST /v1/voice/speech-engine/sessions/{id}/end` — idempotente; elimina el
  recurso del proveedor (best-effort) y marca `ended`.
- `GET /v1/voice/speech-engine/sessions/{id}` — estado.
- `WS /v1/voice/speech-engine/ws/{session_id}` — callback del proveedor.

## Configuración (backend, por instalación OSS)

Cada instalación trae sus propias credenciales y URL pública; NO hay
endpoints/modelos/voces de ningún operador hardcodeados.

| Setting | Default | Nota |
|---|---|---|
| `SPEECH_ENGINE_CALLBACK_BASE` | (usa `PUBLIC_BASE_URL`) | base pública WSS de ElevenLabs hacia acá; vacío = sesiones fallan fail-closed |
| `SPEECH_ENGINE_DEFAULT_MAX_DURATION_SECONDS` | 900 | duración máxima por sesión |
| `SPEECH_ENGINE_TOKEN_TTL_SECONDS` | 3600 | expiración del token WebRTC |
| `SPEECH_ENGINE_DEFAULT_TTS_MODEL_ID` | `eleven_turbo_v2_5` | modelo público de ElevenLabs |
| `SPEECH_ENGINE_PRIVACY_RETENTION_DAYS` | 30 | retención en el proveedor |
| `SPEECH_ENGINE_MAX_ACTIVE_SESSIONS` | 3 | concurrencia por usuario |
| `SPEECH_ENGINE_TURN_TIMEOUT_SECONDS` | 120 | presupuesto por turno |

## Despliegue / prerequisitos

1. URL pública con WSS (`SPEECH_ENGINE_CALLBACK_BASE`) — el callback debe ser
   alcanzable por ElevenLabs.
2. Migración `0073` aplicada.
3. Tenant: `PUT /v1/credentials/voice/speech-engine` (API key de ElevenLabs,
   con validación), `PUT /v1/voice/preferences` (`enabled=true`), y el
   cliente pide sesión con `paid_consent=true`.

## Rollback

- Desactivar `enabled=false` en preferencias o borrar la credencial: las
  sesiones nuevas devuelven 403; las activas expiran solas (`expires_at`) o
  se cierran con `end`. El pipeline legacy (`/v1/voice/realtime`) queda
  intacto para instalaciones que no activen el modo gestionado.
- `alembic downgrade 0071_gym_checkin_unique_dia` elimina las tres tablas (aditivo
  y reversible mientras no haya datos que conservar).

## Riesgos residuales aceptados (auditoría ronda 2)

1. **Una conexión de pool por turno de LLM.** El turno (LLM + tools, acotado a
   `SPEECH_ENGINE_TURN_TIMEOUT_SECONDS`=120 s) corre dentro de una transacción.
   Con muchas sesiones concurrentes puede presionar la pool y el
   `unified_sessions` queda bloqueado hasta el commit. Aceptado para el primer
   despliegue; revisar antes de habilitar la feature a todos los tenants.
   Dueño: implementador · revisión: antes del rollout de producción.
2. **Sin tests automatizados de `_on_close` y del timeout.** El cierre vía wire
   y el `TimeoutError → failed` están trazados pero sin regresión automatizada.
   No bloqueante; agregar en la próxima pasada de tests.

## Pruebas de aceptación

En `apps/api/tests/test_speech_engine.py` (dobles deterministas del
proveedor; NO prueban audio real):

1. Preferencias: defaults, validación de catálogo/esfuerzo/duración,
   `enabled=true` sin credencial → 400, tenant B no ve las de A.
2. Sesión: consentimiento pagado obligatorio, conversación canónica creada,
   token efímero (nunca la key), límite de sesiones activas, end idempotente,
   fallo de provisionamiento → `failed` + cleanup.
3. Callback WSS: JWT inválido/ausente → 4401; sesión de otro tenant/estado
   → 4403; turno rápido persiste enunciado UNA vez y streama deltas; replay
   del mismo `event_id` no duplica; interrupción marca `interrupted`;
   delegación determinista no pasa por el interlocutor rápido; delegación por
   tool `delegate_to_edecan` cambia al modelo de delegación sin pisar
   `chat_model`.

Verificación con proveedor real (audio de verdad, interrupciones, eco) queda
**fuera de los tests unitarios** y requiere una cuenta de ElevenLabs de
prueba del operador.

## Fuentes verificadas (2026-09-11)

- SDK Python oficial `elevenlabs==2.68.0` (pinned en `uv.lock`): contrato de
  `verify_speech_engine_jwt` (HS256/SHA256(key), issuer
  `https://api.elevenlabs.io/convai/speech-engine`, sub
  `convai_speech_engine_upstream`, exp/iat +60 s), `SpeechEngineSession`
  (eventos `init`/`user_transcript`/`ping`/`close`/`error`, cancelación del
  turno anterior, dedupe de `event_id`), `wrap_websocket` (adaptador ASGI),
  `create`/`get`/`delete`, `get_webrtc_token`, tipos `SpeechEngineConfig`,
  `PrivacyConfigInput`, `AgentCallLimits`, `TtsConversationalConfigInput`.
- Docs oficiales: cookbook Speech Engine
  (https://elevenlabs.io/docs/eleven-api/guides/cookbooks/speech-engine) —
  arquitectura WebRTC + callback WSS + token endpoint.
- SDK Swift oficial `elevenlabs/elevenlabs-swift-sdk` (v3.3.1): iOS usa
  `ElevenLabs.startConversation(auth: .conversationToken(_:))`.
- SDK JS oficial `@elevenlabs/client` / `@elevenlabs/react`: web usa
  `Conversation.startSession({conversationToken})`.