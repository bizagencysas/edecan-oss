# edecan_meetings

Reuniones: transcripción de audio/video ya subido con el **STT del propio
tenant** (Deepgram bring-your-own; stub offline si no conectó nada) + minutas
estructuradas (resumen, decisiones, acciones, temas) generadas con el **LLM
del propio tenant**. Cubre "resume reuniones, toma notas" (📞 Comunicación) y
"📹 Video: resume reuniones" de `docs/roadmap.md`. Ver `docs/reuniones.md`
para el flujo completo end-to-end (router + worker + UI).

100% bring-your-own, nunca una credencial de plataforma — mismo criterio que
el resto del repo (`docs/roadmap.md` "Modelo de credenciales").

## Módulos

- `audio.py` — `extraer_audio_wav(bytes_video_o_audio, mime)` → WAV mono
  16 kHz vía `ffmpeg` (binario del sistema). Duplica el helper de subprocess
  de `packages/docanalysis/edecan_docanalysis/video.py` a propósito (paquete
  hermano de OTRO work package, `ARCHITECTURE.md` §10.1: "importar hermanos
  en código de producción sí está permitido, pero cada paquete resuelve su
  propia dependencia técnica sin acoplarse a la forma interna de otro WP en
  vuelo") — mismo patrón canónico: `shutil.which`/`FFMPEG_PATH`,
  `asyncio.create_subprocess_exec` con una lista de argumentos (JAMÁS
  `shell=True`), `tempfile.TemporaryDirectory`, mensaje instructivo si falta
  el binario.
- `minutas.py` — funciones **puras**: `construir_prompt_minutas(transcript,
  titulo)` arma el prompt en español que le pide al LLM un JSON
  `{resumen, decisiones[], acciones[{tarea, responsable?}], temas[]}`;
  `parsear_minutas(texto_llm)` parsea esa respuesta tolerando fences
  ` ```json ... ``` ` y JSON parcialmente inválido (nunca lanza).
- `stt.py` — resolución **bring-your-own del STT del tenant** (Deepgram),
  fail-closed a `StubSTT`. No existe hoy un `resolver_stt_del_tenant` en
  `edecan_voice.tenant` (ese módulo, ver su propio docstring, solo expone la
  mitad TTS — la consolidación de fase v6 solo tocó
  `resolver_config_tts_tenant`/`_elevenlabs_sound_generation`). Este módulo
  replica el ÚNICO precedente real de resolución STT-por-tenant que existe en
  el repo (`apps/api/edecan_api/routers/voice.py::_stt_para_tenant`),
  adaptado a la firma `(session, vault, tenant_id)` que usa un handler del
  worker — mismo criterio que `edecan_creative.podcast.resolver_config_tts_tenant`,
  que tampoco recibe un `ToolContext` porque los handlers del worker no
  tienen uno. `connector_key = "voice_stt"` es el mismo string EXACTO que
  `apps/api/edecan_api/deps.VOICE_STT_CONNECTOR_KEY` /
  `edecan_voice.tenant` (duplicado a propósito entre paquetes hermanos, ver
  `ARCHITECTURE.md` §10.1).
- `tools.py` — `ResumirReunionTool` (`name="resumir_reunion"`,
  `requires_flags={"tools.meetings"}`, `dangerous=False`): valida que el
  archivo exista, pertenezca al tenant y sea audio/video, encola el job
  `process_meeting` y devuelve el disclaimer de consentimiento obligatorio.
  **No inserta la fila `meetings` ella misma** — ver el docstring del propio
  módulo, sección "Por qué la tool no inserta la fila `meetings`", para la
  decisión (y su justificación leyendo el código real) que pedía el paquete
  de trabajo.

## Tabla `meetings` — reconciliada contra el esquema real (2026-07-09)

Este pin era originalmente especulativo (escrito antes de que la migración
`0008_v6_expansion.py` aterrizara) — quedó desincronizado del esquema real
que fase v6 terminó pinneando en `ARCHITECTURE.md` §15.b (mismo problema
que ya pasó una vez con `docs/api.md`, ver `docs/seguridad-modelo-amenazas.md`). El
router (`apps/api/edecan_api/routers/reuniones.py`) y el handler
(`apps/worker/edecan_worker/handlers/process_meeting.py`) ya se corrigieron
para hablar contra el esquema REAL de abajo — este documento se actualiza
para que coincida, no al revés:

```
meetings(
    tenant_id UUID NOT NULL, user_id UUID NOT NULL,
    source_file_id UUID NULL,          -- audio/video de origen; SIN FK
                                        -- (referencia informativa a files.id,
                                        -- mismo criterio que
                                        -- voice_consents.consent_file_id)
    titulo TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',  -- 'pending'|'running'|'done'|'error' (CHECK)
    transcript_file_id UUID NULL,      -- fila files (text/plain) con la
                                        -- transcripción completa; SIN FK
    resumen TEXT NULL,
    minutos JSONB NULL,                -- {"decisiones": [...], "acciones": [{"tarea",
                                        -- "responsable"}], "temas": [...]} — un único
                                        -- blob, NO columnas separadas
    duracion_segundos INTEGER NULL,    -- NO numeric/float (corregido 2026-07-09,
                                        -- fase v7): `duracion_wav_segundos` (puro
                                        -- Python) devuelve un float con precisión de
                                        -- sub-segundo, pero la columna real es INTEGER
                                        -- — verificado EMPÍRICAMENTE contra Postgres
                                        -- real que asyncpg NUNCA rechaza el float (no
                                        -- revienta), pero lo trunca en silencio hacia
                                        -- cero (1.9 -> 1, nunca redondea). `process_
                                        -- meeting.py::_guardar_resultado` ahora
                                        -- redondea explícito antes de escribir, ver
                                        -- docs/cumplimiento/barrido-v7-reuniones-
                                        -- analista.md.
    error TEXT NULL
)
```

El contrato HTTP público de `/v1/reuniones` (`ReunionOut.file_id`, y
`decisiones`/`acciones`/`temas` como campos separados en la respuesta JSON)
se mantiene tal cual para el cliente — el router traduce internamente entre
ese contrato y el esquema real de arriba (`source_file_id`/`minutos`).

**Actualizado 2026-07-09 (fase v7, ya no es "pendiente"):** el linchpin de
v6 (fase v6) aterrizó las tres piezas que este párrafo pedía cuando fase v6
corría todavía en paralelo — `edecan_schemas.queue.JOB_TYPES` SÍ incluye
`"process_meeting"` (12º valor), `edecan_schemas.plans` SÍ tiene
`FLAG_TOOLS_MEETINGS = "tools.meetings"` (`✔` en
`free_selfhost`/`hosted_pro`/`hosted_business`, `✖` en `hosted_basic` — el
patrón "premium" sugerido abajo, confirmado tal cual), y
`apps/api/edecan_api/main.py::V6_ROUTER_NAMES` SÍ incluye `"reuniones"`
(junto a `"analista"`/`"mcp"`). Re-verificado empíricamente contra Postgres
real (no solo leyendo el código) en `docs/cumplimiento/barrido-v7-reuniones-
analista.md`. El párrafo original queda abajo como registro histórico de la
brecha que existía mientras v6 estaba en vuelo.

`edecan_schemas.queue.JOB_TYPES` necesita sumar `"process_meeting"` (12º
valor) y `edecan_schemas.plans` necesita el flag booleano
`FLAG_TOOLS_MEETINGS = "tools.meetings"` (¿en qué planes? sugerido: mismo
patrón "premium" que `tools.podcast`/`tools.travel` — `✔` en
`free_selfhost`/`hosted_pro`/`hosted_business`, `✖` en `hosted_basic`) — sin
esto, `edecan_core.queue.enqueue(..., "process_meeting", ...)` lanza
`ValueError` en producción (los tests de este paquete de trabajo lo
monkeypatchean, así que pasan igual). `apps/api/edecan_api/main.py` necesita
sumar `"reuniones"` a su tupla `V6_ROUTER_NAMES` para montar
`apps/api/edecan_api/routers/reuniones.py` (que ya se escribió, monta con el
mismo patrón defensivo `importlib`/`try-except` que el resto — se puede
montar manualmente en tests mientras tanto, ver
`apps/api/tests/test_reuniones_router.py`).

**Nota sobre el workspace uv**: `packages/meetings` SÍ se agregó a
`[tool.uv.workspace].members` del `pyproject.toml` raíz (sección "v6
(fase v6)") — excepción puntual, mínima y documentada al "no tocar el
pyproject raíz": se verificó empíricamente (`uv sync --all-packages`) que
esta lista es explícita, no un glob `packages/*`, así que sin esa línea
`edecan_meetings` queda no-instalable/no-importable en ningún venv del
repo, incluidos sus propios tests. `apps/api/pyproject.toml`/
`apps/worker/pyproject.toml` YA declaran `edecan-meetings` como dependencia
(cerrado el mismo hueco temporal que documentó en su momento `edecan_travel`
antes de que fase v5 lo agregara, `ARCHITECTURE.md` §14.f) — el import
directo en `process_meeting.py` funciona igual en dev (`uv sync
--all-packages` instala TODO miembro del workspace en el mismo venv
compartido, sin importar quién lo declara como dependencia — mismo mecanismo
que ya documentó `docs/roadmap.md` para `packages/vehicles`), y ahora
también queda declarado para que las imágenes Docker de producción
(`uv sync --no-dev --package edecan-api`/`edecan-worker`, verificado con
`uv tree --package edecan-api/edecan-worker --frozen`) y el empaquetado de
escritorio (`apps/desktop/packaging/edecan_local.spec`,
`EDECAN_TOOL_PACKAGES`) lo incluyan de verdad.

## Tests

Offline y deterministas: `ffmpeg` se mockea con `monkeypatch` de
`shutil.which`/`asyncio.create_subprocess_exec` (nunca se ejecuta un binario
real), STT/LLM/S3/vault son fakes en memoria. Nunca importan paquetes
hermanos (`ARCHITECTURE.md` §10.1).
