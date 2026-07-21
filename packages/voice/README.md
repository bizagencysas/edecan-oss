# packages/voice — `edecan_voice`

Abstracción de voz para el flujo de **voz web** (`ARCHITECTURE.md` §4 y §10.9): STT (voz → texto)
y TTS (texto → voz) con proveedores intercambiables y stubs offline deterministas.

## Contratos (`edecan_voice.base`)

```python
class STTProvider(ABC):
    async def transcribe(self, audio: bytes, mime: str, language: str | None = None) -> Transcript: ...

class TTSProvider(ABC):
    async def synthesize(self, text: str, voice_id: str | None = None, fmt: Literal["mp3", "wav"] = "mp3") -> bytes: ...
```

`Transcript(text, language, confidence=None)`.

## Proveedores

| Proveedor    | Tipo | Clase          | Se activa con                       | Requiere                                                             | Notas                                                                                          |
| ------------ | ---- | -------------- | ------------------------------------ | --------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| Deepgram     | STT  | `DeepgramSTT`  | `VOICE_STT_PROVIDER=deepgram`        | `DEEPGRAM_API_KEY`                                                     | `POST /v1/listen`, modelo `nova-2`, `smart_format=true`, `language` (default `es`).              |
| ElevenLabs   | TTS  | `ElevenLabsTTS`| `VOICE_TTS_PROVIDER=elevenlabs`      | `ELEVENLABS_API_KEY` (+ `ELEVENLABS_VOICE_ID` o un `voice_id` por llamada) | Modelo `eleven_multilingual_v2`; siempre devuelve mp3.                                          |
| Amazon Polly | TTS  | `PollyTTS`     | `VOICE_TTS_PROVIDER=polly`           | Credenciales AWS estándar (rol IAM en prod; `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` en dev) | Voz `POLLY_VOICE` (default `Lupe`), motor `neural`, siempre mp3. Usa `AWS_ENDPOINT_URL`/`AWS_REGION` si están definidos (LocalStack en dev). |
| Stub         | STT  | `StubSTT`      | `VOICE_STT_PROVIDER=stub` (o cualquier valor no reconocido, o proveedor real sin credencial) | —                                                                      | Devuelve siempre `Transcript(text="(transcripción de prueba)", language="es")`. Determinista.  |
| Stub         | TTS  | `StubTTS`      | `VOICE_TTS_PROVIDER=stub` (ídem)     | —                                                                      | Genera un WAV PCM válido de 0.5 s de silencio (módulo `wave`, sin red ni dependencias).          |

`edecan_voice.registry.get_stt(settings)` / `get_tts(settings)` leen `VOICE_STT_PROVIDER` /
`VOICE_TTS_PROVIDER` y las credenciales anteriores desde `settings` (cualquier objeto con esos
atributos — normalmente la configuración pydantic-settings de la app, ver `ARCHITECTURE.md`
§10.2). **Nunca lanzan**: si falta una credencial requerida (Deepgram/ElevenLabs) o el proveedor
pedido no existe, caen a la implementación *stub* correspondiente y dejan un `logging.warning`.
Polly es la excepción a "requiere credencial única": usa la cadena estándar de credenciales AWS
(igual que S3/SQS en el resto de la plataforma), así que seleccionarlo nunca cae a stub por sí
solo — un problema real de credenciales AWS fallará en el momento de sintetizar, no al elegir el
proveedor.

## Cómo añadir un proveedor nuevo

1. Crea `edecan_voice/<proveedor>.py` con una clase que herede de `STTProvider` o `TTSProvider`
   e implemente el método abstracto respetando la firma exacta de `base.py`.
2. Si necesita credenciales, añade la variable de entorno a `ARCHITECTURE.md` §10.2 y a
   `.env.example` (raíz del repo) como placeholder `TU_X_AQUI` — nunca un valor real.
3. Registra la nueva opción en `edecan_voice/registry.py` (`get_stt`/`get_tts`), siguiendo el
   mismo patrón: *fallback* seguro a stub + `logging.warning` si falta la credencial.
4. Añade tests offline en `packages/voice/tests/`: usa `respx` si el proveedor habla HTTP
   directo (como Deepgram/ElevenLabs), o inyecta un cliente/sesión falso por constructor si usa
   un SDK como `aioboto3` (como Polly) — nunca red real ni servicios de pago.
5. Documenta el proveedor en la tabla de arriba.

## `edecan_voice.pipeline.voice_turn`

```python
async def voice_turn(stt, tts, run_agent_text, audio: bytes, mime: str) -> tuple[str, str, bytes]
```

Helper de orquestación: transcribe el audio de entrada (`stt`), ejecuta el turno normal del
agente vía `run_agent_text` (una envoltura de texto sobre `edecan_core.agent.Agent.run_turn`) y
sintetiza la respuesta (`tts`). Retorna `(texto_usuario, texto_respuesta, audio_respuesta)`.
Pensado para que `apps/api` lo reutilice al implementar las rutas `/v1/voice/*`
(`ARCHITECTURE.md` §10.12).

## Telefonía

La telefonía Twilio sí forma parte del núcleo OSS. `edecan_voice.telephony` contiene el cliente
BYO-Twilio, validación de firmas y TwiML seguro; `LlamarContactoTool` delega la persistencia y el
despacho transaccional al router `/v1/phone` de `apps/api`. Twilio hace reconocimiento y síntesis
mediante `<Gather input="speech">` y `<Say>`, por lo que este canal no depende de
`STTProvider`/`TTSProvider`. Las credenciales siempre son por tenant y viven cifradas en
`TokenVault`; las pruebas usan clientes falsos y nunca hacen llamadas reales.

La extensión opcional `edecan_premium` conserva funciones legadas no incluidas en este flujo:
SMS, campañas y Media Streams con interrupciones naturales.

## Tests

```
uv run pytest packages/voice
```

Todos offline y deterministas: `respx` simula Deepgram y ElevenLabs, `PollyTTS` recibe una
sesión de `aioboto3` fake inyectada por constructor, y los stubs no hacen red ni usan
dependencias externas.
