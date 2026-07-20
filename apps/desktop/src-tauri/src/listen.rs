//! "Escuchar siempre": wake word + captura de comando en segundo plano, con
//! la ventana cerrada o minimizada — como Alexa/Siri, no un botón por
//! sesión. Hasta este work package el modo "escuchar siempre" solo existía
//! en el navegador (`apps/web/src/components/chat/AlwaysListenMode.tsx`,
//! Web Speech API), lo que exigía que la ventana estuviera abierta y un
//! click del usuario en cada sesión.
//!
//! Este módulo NO sabe nada de turnos de chat, LLM ni TTS — su único
//! trabajo es: (a) entrenar un modelo de wake word con la voz del usuario,
//! (b) mientras esté activado, escuchar el micrófono en un hilo dedicado
//! esperando esa wake word, y (c) al detectarla, grabar lo que siga hasta
//! que haya silencio (o se agote un tope duro) y entregar ese audio a JS vía
//! un evento. Todo lo que pase después (mostrar el turno, mandarlo al LLM,
//! hablar la respuesta) es responsabilidad del frontend, igual que en el
//! modo del navegador.
//!
//! ## Motor de detección: rustpotter
//! Se usa `rustpotter` (ver Cargo.toml) en vez de un servicio con API key o
//! de whisper.cpp (necesita cmake, no instalado en esta máquina — ver
//! docs/desktop.md). rustpotter entrena "wakeword references": compara los
//! MFCCs del audio en vivo contra los MFCCs de 3 a 8 muestras grabadas por
//! el propio usuario, sin red neuronal ni dataset externo. La API real
//! (`Rustpotter`, `WakewordRef`, `WakewordRefBuildFromBuffers`,
//! `WakewordSave`) se confirmó leyendo el código fuente del crate ya
//! descargado en el registro local de cargo
//! (`~/.cargo/registry/src/.../rustpotter-3.0.2/src/`), no se asumió nada
//! de memoria — el README de la versión publicada no documenta el paso de
//! "crear una wakeword ref desde buffers" con el detalle suficiente.
//!
//! ## Micrófono: cpal
//! `cpal::Stream` no implementa `Send` en la mayoría de sus backends
//! (contiene handles nativos de CoreAudio/WASAPI/ALSA). Por eso todo el
//! ciclo de vida de un stream — crearlo, tocar `.play()`, y `drop`earlo —
//! ocurre siempre dentro de un único hilo dedicado que nunca lo mueve ni lo
//! comparte: el hilo que lanza `tauri::async_runtime::spawn_blocking` para
//! las grabaciones cortas (muestras de entrenamiento), o el `std::thread`
//! propio del loop de escucha en segundo plano. Ningún `cpal::Stream` cruza
//! nunca un canal ni un `Mutex` compartido entre hilos.
//!
//! ## Formatos de audio soportados
//! Los dispositivos de entrada exponen distintos `cpal::SampleFormat`
//! según la plataforma (CoreAudio en Mac suele dar F32; WASAPI/ALSA pueden
//! dar I16). Este módulo soporta I8, I16, I32 y F32 — el mismo subconjunto
//! que usa el propio ejemplo oficial `examples/record_wav.rs` del crate
//! cpal (confirmado leyendo ese ejemplo en el registro local), que cubre
//! los formatos por defecto reales de mac/Windows/Linux. Un dispositivo que
//! reporte otro formato (p. ej. I24/U16, poco común como default) devuelve
//! un error explícito en vez de asumir una conversión no verificada.

use std::collections::HashMap;
use std::io::Cursor;
use std::path::{Path, PathBuf};
use std::sync::mpsc::{self, RecvTimeoutError};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::Sample as CpalSample;
use rustpotter::{
    Rustpotter, RustpotterConfig, WakewordRef, WakewordRefBuildFromBuffers, WakewordSave,
};
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager};

const STATE_FILE_NAME: &str = "always_listen.json";
const MODEL_FILE_NAME: &str = "wake_model.rpw";
const MAX_WAKE_LABEL_CHARS: usize = 80;

/// Duración de cada muestra de entrenamiento. ~1.6s alcanza para una wake
/// word corta ("Oye Edecán") con margen; es también, a propósito, la misma
/// duración que documenta el contrato de este work package para que el
/// `await invoke(...)` del lado JS tarde lo esperado.
const SAMPLE_SECONDS: f32 = 1.6;

/// Cantidad de coeficientes MFCC por wakeword reference. rustpotter no
/// expone ningún valor por defecto para esto (es un parámetro obligatorio
/// de `WakewordRefBuildFromBuffers::new_from_sample_buffers`, ver
/// wakewords/comp/wakeword_ref_build.rs en el crate) — 16 es un valor
/// razonable y común en proyectos de wake-word comparables, sin evidencia
/// de que el crate recomiende otro.
const MFCC_SIZE: u16 = 16;

// --- Criterio de corte del comando post-wake-word ---------------------
// Mismo criterio que `AlwaysListenMode.tsx` (apps/web/src/components/chat/
// AlwaysListenMode.tsx): cortar tras un silencio sostenido después de haber
// detectado voz, con un tope duro. La diferencia es cómo se mide "nivel de
// audio": el navegador usa energía espectral promedio de un `AnalyserNode`
// (Web Audio API); acá, sin FFT disponible sin sumar una dependencia nueva
// solo para esto, se usa el RMS de las muestras i16 crudas normalizado
// sobre `i16::MAX` — mismo rango 0-1, mismo umbral 0.08, criterio
// equivalente en espíritu aunque no bit-a-bit idéntico.
const COMMAND_SILENCE_MS: u64 = 1400;
const COMMAND_MAX_MS: u64 = 30_000;
const COMMAND_VOICE_THRESHOLD: f32 = 0.08;

/// Estado persistido en `{app_data_dir}/always_listen.json`.
#[derive(Serialize, Deserialize, Clone)]
struct PersistedState {
    enabled: bool,
    trained: bool,
    wake_label: String,
}

impl Default for PersistedState {
    fn default() -> Self {
        PersistedState {
            enabled: false,
            trained: false,
            wake_label: String::new(),
        }
    }
}

/// Payload de `always_listen_get_state`. Claves JSON en snake_case a
/// propósito (mismo criterio que el resto de las respuestas de este repo,
/// ver el payload de `edecan://backend-error` en backend.rs) — los nombres
/// de campo ya son snake_case así que no hace falta ningún atributo serde
/// extra para lograrlo.
#[derive(Serialize)]
pub struct AlwaysListenStateOut {
    pub enabled: bool,
    pub trained: bool,
    pub wake_label: String,
    pub listening: bool,
    pub samples_recorded: u8,
}

/// Handle del hilo de escucha en segundo plano actualmente vivo, si hay
/// uno. Guarda solo el emisor del canal de parada: unir (`.join()`) el
/// hilo bloquearía el comando que llama a `set_enabled(false)` hasta que el
/// stream de cpal termine de cerrarse del todo, y no hace falta esperar eso
/// de forma sincrónica — el hilo se limpia solo apenas procesa la señal.
struct ListenLoopHandle {
    stop_tx: mpsc::Sender<()>,
}

/// Estado en memoria del subsistema, gestionado por Tauri (`app.manage(...)`
/// en `lib.rs`, mismo patrón que `BackendState`/`PortState` en backend.rs).
pub struct AlwaysListenRuntime {
    /// Las hasta 3 muestras crudas grabadas por `always_listen_record_sample`,
    /// cada una un WAV completo (con header) codificado por `hound` — mismo
    /// formato que espera `WakewordRef::new_from_sample_buffers` (lee el
    /// header vía `hound::WavReader`, confirmado en
    /// mfcc/wav_file_extractor.rs del crate).
    samples: Mutex<[Option<Vec<u8>>; 3]>,
    /// Espejo en memoria de `enabled` (el archivo persistido es la fuente de
    /// verdad de largo plazo, pero `lib.rs` necesita consultar esto en el
    /// hot path del cierre de ventana sin tocar el filesystem cada vez).
    enabled_flag: Mutex<bool>,
    /// Control del hilo de escucha en segundo plano, si está corriendo.
    loop_handle: Mutex<Option<ListenLoopHandle>>,
}

impl Default for AlwaysListenRuntime {
    fn default() -> Self {
        AlwaysListenRuntime {
            samples: Mutex::new([None, None, None]),
            enabled_flag: Mutex::new(false),
            loop_handle: Mutex::new(None),
        }
    }
}

// --- Rutas -------------------------------------------------------------

fn app_data_root(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_data_dir()
        .map_err(|err| format!("No se pudo resolver el directorio de datos de la app: {err}"))
}

fn state_file_path(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(app_data_root(app)?.join(STATE_FILE_NAME))
}

fn model_file_path(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(app_data_root(app)?.join(MODEL_FILE_NAME))
}

// --- Persistencia --------------------------------------------------------

/// Nunca falla: cualquier problema (no existe el archivo, JSON corrupto, no
/// se pudo resolver `app_data_dir`) devuelve el estado default.
fn load_persisted(app: &AppHandle) -> PersistedState {
    let Ok(path) = state_file_path(app) else {
        return PersistedState::default();
    };
    match std::fs::read(&path) {
        Ok(bytes) => serde_json::from_slice(&bytes).unwrap_or_default(),
        Err(_) => PersistedState::default(),
    }
}

fn save_persisted(app: &AppHandle, state: &PersistedState) -> Result<(), String> {
    let path = state_file_path(app)?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|err| err.to_string())?;
    }
    let bytes = serde_json::to_vec_pretty(state).map_err(|err| err.to_string())?;
    std::fs::write(&path, bytes).map_err(|err| err.to_string())
}

// --- Getter sincrónico usado por lib.rs en el hot path de cierre de ventana

/// Lee el flag en memoria (NO el archivo de disco a propósito — ver
/// docstring de `AlwaysListenRuntime::enabled_flag`). Se llama desde el
/// handler de `CloseRequested` en lib.rs para decidir si cerrar la ventana
/// oculta la app (escucha activa) o la cierra del todo (comportamiento de
/// siempre).
pub fn is_enabled(app: &AppHandle) -> bool {
    *app.state::<AlwaysListenRuntime>()
        .enabled_flag
        .lock()
        .unwrap()
}

// --- Comandos (lógica; los wrappers `#[tauri::command]` viven en commands.rs)

/// Nunca falla — si no hay archivo de estado (o está corrupto), devuelve el
/// default (`enabled:false, trained:false, wake_label:"", listening:false,
/// samples_recorded:0`).
pub fn get_state(app: &AppHandle) -> AlwaysListenStateOut {
    let persisted = load_persisted(app);
    let runtime = app.state::<AlwaysListenRuntime>();
    let listening = runtime.loop_handle.lock().unwrap().is_some();
    let samples_recorded = runtime
        .samples
        .lock()
        .unwrap()
        .iter()
        .filter(|sample| sample.is_some())
        .count() as u8;
    AlwaysListenStateOut {
        enabled: persisted.enabled,
        trained: persisted.trained,
        wake_label: persisted.wake_label,
        listening,
        samples_recorded,
    }
}

/// Graba ~1.6s desde el micrófono por defecto y guarda el resultado (WAV
/// completo, en memoria) en el slot `index` (0, 1 o 2). Llamada bloqueante
/// desde JS a propósito (ver contrato del work package) — el trabajo real
/// de cpal corre en `tauri::async_runtime::spawn_blocking` para no bloquear
/// el runtime async de Tauri mientras dura la grabación.
pub async fn record_sample(app: AppHandle, index: u8) -> Result<(), String> {
    if index > 2 {
        return Err("Índice de muestra inválido (debe ser 0, 1 o 2).".to_string());
    }
    let capture = tauri::async_runtime::spawn_blocking(move || capture_wav_sample(SAMPLE_SECONDS))
        .await
        .map_err(|err| format!("Fallo interno grabando la muestra: {err}"))?;
    let wav_bytes = capture?;

    let runtime = app.state::<AlwaysListenRuntime>();
    runtime.samples.lock().unwrap()[index as usize] = Some(wav_bytes);
    Ok(())
}

/// Entrena el modelo de wake word a partir de las 3 muestras grabadas y lo
/// deja guardado en `wake_model.rpw`. Si ya había un modelo entrenado antes
/// y `enabled:true`, lo vuelve a dejar en `false` a propósito — reentrenar
/// exige que el usuario reactive "escuchar siempre" conscientemente en vez
/// de asumir que la wake word vieja seguía sirviendo.
pub async fn train(app: AppHandle, wake_label: String) -> Result<(), String> {
    let wake_label = validate_wake_label(wake_label)?;
    let samples: Vec<Vec<u8>> = {
        let runtime = app.state::<AlwaysListenRuntime>();
        let guard = runtime.samples.lock().unwrap();
        if guard.iter().any(|sample| sample.is_none()) {
            return Err("Grabá las 3 muestras primero.".to_string());
        }
        guard.iter().map(|sample| sample.clone().unwrap()).collect()
    };

    let label_for_training = wake_label.clone();
    let build_result = tauri::async_runtime::spawn_blocking(move || {
        build_wakeword_model_buffer(label_for_training, samples)
    })
    .await
    .map_err(|err| format!("Fallo interno entrenando el modelo: {err}"))?;
    let model_bytes = build_result?;

    let model_path = model_file_path(&app)?;
    if let Some(parent) = model_path.parent() {
        std::fs::create_dir_all(parent).map_err(|err| err.to_string())?;
    }
    std::fs::write(&model_path, &model_bytes).map_err(|err| err.to_string())?;

    save_persisted(
        &app,
        &PersistedState {
            enabled: false,
            trained: true,
            wake_label,
        },
    )?;

    // Si venía de un entrenamiento anterior con el loop corriendo, se apaga
    // (ver motivo en el docstring de la función) y se limpia el flag.
    stop_listen_loop(&app);
    *app.state::<AlwaysListenRuntime>()
        .enabled_flag
        .lock()
        .unwrap() = false;

    // Limpia las muestras en memoria — ya no hacen falta una vez guardado
    // el modelo entrenado en disco.
    *app.state::<AlwaysListenRuntime>().samples.lock().unwrap() = [None, None, None];

    Ok(())
}

fn validate_wake_label(value: String) -> Result<String, String> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return Err("La palabra clave no puede estar vacía.".to_string());
    }
    if trimmed.chars().count() > MAX_WAKE_LABEL_CHARS {
        return Err(format!(
            "La palabra clave no puede superar {MAX_WAKE_LABEL_CHARS} caracteres."
        ));
    }
    if trimmed.chars().any(char::is_control) {
        return Err("La palabra clave contiene caracteres de control inválidos.".to_string());
    }
    Ok(trimmed.to_string())
}

/// Activa o desactiva "escuchar siempre". Activar sin haber entrenado antes
/// es un error explícito; desactivar siempre es válido (incluso si ya
/// estaba desactivado — es idempotente a propósito, así el ítem fijo del
/// menú de bandeja puede llamarlo sin chequear el estado primero).
pub fn set_enabled(app: AppHandle, enabled: bool) -> Result<(), String> {
    let persisted = load_persisted(&app);
    if enabled && !persisted.trained {
        return Err("Entrená tu voz primero.".to_string());
    }

    // Arranca/para el loop ANTES de tocar el estado persistido/en memoria:
    // si `start_listen_loop` falla (p. ej. no hay micrófono), se corta acá
    // con el `?` y ni el JSON ni `enabled_flag` quedan diciendo `true` con
    // ningún loop realmente corriendo detrás.
    if enabled {
        start_listen_loop(&app)?;
    } else {
        stop_listen_loop(&app);
    }

    let next = PersistedState {
        enabled,
        trained: persisted.trained,
        wake_label: persisted.wake_label,
    };
    if let Err(error) = save_persisted(&app, &next) {
        // La escucha nunca debe quedar activa si no pudimos persistir el
        // consentimiento del usuario. También reflejamos el estado real en
        // memoria para que cerrar la ventana no la oculte como si siguiera
        // escuchando.
        if enabled {
            stop_listen_loop(&app);
        }
        *app.state::<AlwaysListenRuntime>()
            .enabled_flag
            .lock()
            .unwrap() = false;
        return Err(error);
    }
    *app.state::<AlwaysListenRuntime>()
        .enabled_flag
        .lock()
        .unwrap() = enabled;
    Ok(())
}

/// Borra el entrenamiento por completo: para el loop si estaba activo,
/// borra `wake_model.rpw` si existe, resetea el JSON persistido y limpia
/// las muestras en memoria.
pub fn reset_training(app: AppHandle) -> Result<(), String> {
    stop_listen_loop(&app);

    let model_path = model_file_path(&app)?;
    if model_path.is_file() {
        std::fs::remove_file(&model_path).map_err(|err| err.to_string())?;
    }

    save_persisted(&app, &PersistedState::default())?;
    *app.state::<AlwaysListenRuntime>()
        .enabled_flag
        .lock()
        .unwrap() = false;
    *app.state::<AlwaysListenRuntime>().samples.lock().unwrap() = [None, None, None];
    Ok(())
}

/// Se llama una única vez desde `lib.rs::setup()`, después de arrancar el
/// backend. Si el estado persistido dice `enabled:true` y el modelo
/// entrenado sigue en disco, deja "escuchar siempre" corriendo desde el
/// arranque mismo de la app — así sobrevive a cerrar y reabrir Edecán sin
/// que el usuario tenga que reactivarlo a mano cada vez. Nunca hace panic:
/// cualquier fallo se loguea y la app sigue arrancando con la escucha
/// apagada.
pub fn maybe_autostart(app: &AppHandle) -> Result<(), String> {
    let persisted = load_persisted(app);
    if !persisted.enabled || !persisted.trained {
        return Ok(());
    }
    let model_path = model_file_path(app)?;
    if !model_path.is_file() {
        return Ok(());
    }
    // Arranca el loop PRIMERO y solo marca `enabled_flag` en memoria si
    // arrancó sin error — mismo orden que `set_enabled`, para que
    // `is_enabled()` nunca reporte `true` con ningún loop realmente
    // corriendo detrás (ver getter sincrónico usado por el hot path de
    // cierre de ventana en lib.rs).
    start_listen_loop(app)?;
    *app.state::<AlwaysListenRuntime>()
        .enabled_flag
        .lock()
        .unwrap() = true;
    Ok(())
}

// --- Entrenamiento -------------------------------------------------------

/// Corre en `spawn_blocking` (no hace nada async por sí misma, pero
/// `WakewordRef::new_from_sample_buffers` recalcula MFCCs de las 3 muestras
/// completas — trabajo de CPU no trivial que no debe correr en el runtime
/// async de Tauri).
fn build_wakeword_model_buffer(label: String, samples: Vec<Vec<u8>>) -> Result<Vec<u8>, String> {
    let mut sample_map: HashMap<String, Vec<u8>> = HashMap::new();
    for (index, wav_bytes) in samples.into_iter().enumerate() {
        sample_map.insert(format!("sample_{index}.wav"), wav_bytes);
    }
    // `threshold`/`avg_threshold` en `None`: cada wakeword individual cae
    // entonces al threshold/avg_threshold GLOBAL del detector
    // (`DetectorConfig`, ver `WakewordComparator::run_detection` en
    // wakewords/comp/wakeword_comp.rs) — no hace falta pisarlos acá.
    let wakeword = WakewordRef::new_from_sample_buffers(label, None, None, sample_map, MFCC_SIZE)?;
    wakeword.save_to_buffer()
}

// --- Captura de una muestra corta (entrenamiento) -------------------------

/// Graba `seconds` segundos del micrófono por defecto y devuelve un WAV
/// completo (mono, PCM16, al sample rate real del dispositivo). Bloqueante
/// a propósito — se llama siempre desde `tauri::async_runtime::spawn_blocking`.
fn capture_wav_sample(seconds: f32) -> Result<Vec<u8>, String> {
    let collected: Arc<Mutex<Vec<i16>>> = Arc::new(Mutex::new(Vec::new()));
    let collected_for_callback = collected.clone();

    let (stream, supported) = open_input_stream(move |frame| {
        collected_for_callback
            .lock()
            .unwrap()
            .extend_from_slice(frame);
    })?;

    stream
        .play()
        .map_err(|err| format!("No se pudo iniciar la grabación: {err}"))?;
    std::thread::sleep(Duration::from_secs_f32(seconds));
    // El stream se abre, se usa y se dropea acá mismo, en este único hilo
    // (ver docstring del módulo sobre `cpal::Stream` y `Send`).
    drop(stream);

    let samples = collected.lock().unwrap().clone();
    encode_wav_mono_i16(&samples, supported.sample_rate())
}

// --- Apertura del micrófono ------------------------------------------------

/// Abre el dispositivo de entrada por defecto con su configuración por
/// defecto y arranca un stream que, por cada bloque de audio entrante, lo
/// convierte a mono `i16` (tomando el primer canal si el dispositivo es
/// estéreo/multicanal, igual que hace `AudioEncoder::reencode_to_mono_with_sample_rate`
/// dentro de rustpotter) y se lo entrega a `on_frame`. Devuelve también la
/// config resuelta (para leer `sample_rate`/`channels` después). El stream
/// arranca en pausa — el caller decide cuándo `play()`earlo.
fn open_input_stream(
    on_frame: impl FnMut(&[i16]) + Send + 'static,
) -> Result<(cpal::Stream, cpal::SupportedStreamConfig), String> {
    let host = cpal::default_host();
    let device = host
        .default_input_device()
        .ok_or_else(|| "No se encontró un micrófono de entrada.".to_string())?;
    let supported = device
        .default_input_config()
        .map_err(|err| format!("No se pudo leer la configuración del micrófono: {err}"))?;

    let channels = supported.channels() as usize;
    let sample_format = supported.sample_format();
    let stream_config: cpal::StreamConfig = supported.clone().into();
    let err_fn = |err| eprintln!("[edecan-desktop] error del stream de audio: {err}");

    let mut on_frame = on_frame;
    let stream_result = match sample_format {
        cpal::SampleFormat::I8 => device.build_input_stream(
            &stream_config,
            move |data: &[i8], _: &cpal::InputCallbackInfo| {
                on_frame(&downmix_to_i16(data, channels));
            },
            err_fn,
            None,
        ),
        cpal::SampleFormat::I16 => device.build_input_stream(
            &stream_config,
            move |data: &[i16], _: &cpal::InputCallbackInfo| {
                on_frame(&downmix_to_i16(data, channels));
            },
            err_fn,
            None,
        ),
        cpal::SampleFormat::I32 => device.build_input_stream(
            &stream_config,
            move |data: &[i32], _: &cpal::InputCallbackInfo| {
                on_frame(&downmix_to_i16(data, channels));
            },
            err_fn,
            None,
        ),
        cpal::SampleFormat::F32 => device.build_input_stream(
            &stream_config,
            move |data: &[f32], _: &cpal::InputCallbackInfo| {
                on_frame(&downmix_to_i16(data, channels));
            },
            err_fn,
            None,
        ),
        other => {
            return Err(format!(
                "Formato de audio del micrófono no soportado todavía: {other} \
                 (se soportan i8, i16, i32 y f32 — ver docstring de src/listen.rs)."
            ))
        }
    };
    let stream = stream_result.map_err(|err| format!("No se pudo abrir el micrófono: {err}"))?;
    Ok((stream, supported))
}

/// Convierte un bloque de muestras `T` (del formato que reporte el
/// dispositivo) a mono `i16`, tomando el primer canal de cada frame si hay
/// más de uno. `T: CpalSample` para poder llamar `i16::from_sample(..)`
/// (trait `cpal::Sample`, re-exporta `dasp_sample::Sample` — confirmado
/// contra el ejemplo oficial `examples/record_wav.rs` del crate cpal).
fn downmix_to_i16<T>(data: &[T], channels: usize) -> Vec<i16>
where
    T: Copy,
    i16: cpal::FromSample<T>,
{
    if channels <= 1 {
        data.iter()
            .map(|&sample| i16::from_sample(sample))
            .collect()
    } else {
        data.chunks_exact(channels)
            .map(|frame| i16::from_sample(frame[0]))
            .collect()
    }
}

// --- Codificación WAV ------------------------------------------------------

/// Codifica `samples` (mono, i16) como un WAV completo en memoria. Se
/// escribe sobre `Cursor<&mut Vec<u8>>` (no `Cursor<Vec<u8>>`) a propósito:
/// `hound::WavWriter::finalize` consume el writer sin devolver el `Vec`
/// subyacente, así que hay que retener el `Vec` afuera y solo prestarle una
/// referencia mutable al writer.
fn encode_wav_mono_i16(samples: &[i16], sample_rate: u32) -> Result<Vec<u8>, String> {
    let spec = hound::WavSpec {
        channels: 1,
        sample_rate,
        bits_per_sample: 16,
        sample_format: hound::SampleFormat::Int,
    };
    let mut bytes: Vec<u8> = Vec::new();
    {
        let cursor = Cursor::new(&mut bytes);
        let mut writer = hound::WavWriter::new(cursor, spec).map_err(|err| err.to_string())?;
        for &sample in samples {
            writer.write_sample(sample).map_err(|err| err.to_string())?;
        }
        writer.finalize().map_err(|err| err.to_string())?;
    }
    Ok(bytes)
}

/// Codificador base64 (alfabeto estándar RFC 4648, con padding) escrito a
/// mano a propósito: el contrato de este work package solo agrega cpal,
/// rustpotter y hound a Cargo.toml, y una función de ~15 líneas no amerita
/// sumar una dependencia nueva solo para esto.
fn encode_base64(data: &[u8]) -> String {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity((data.len() + 2) / 3 * 4);
    for chunk in data.chunks(3) {
        let b0 = chunk[0] as u32;
        let b1 = *chunk.get(1).unwrap_or(&0) as u32;
        let b2 = *chunk.get(2).unwrap_or(&0) as u32;
        let n = (b0 << 16) | (b1 << 8) | b2;
        out.push(ALPHABET[((n >> 18) & 0x3F) as usize] as char);
        out.push(ALPHABET[((n >> 12) & 0x3F) as usize] as char);
        out.push(if chunk.len() > 1 {
            ALPHABET[((n >> 6) & 0x3F) as usize] as char
        } else {
            '='
        });
        out.push(if chunk.len() > 2 {
            ALPHABET[(n & 0x3F) as usize] as char
        } else {
            '='
        });
    }
    out
}

// --- Loop de escucha en segundo plano --------------------------------------

fn start_listen_loop(app: &AppHandle) -> Result<(), String> {
    let runtime = app.state::<AlwaysListenRuntime>();
    let mut handle_guard = runtime.loop_handle.lock().unwrap();
    if handle_guard.is_some() {
        // Ya está corriendo (p. ej. `maybe_autostart` seguido de un
        // `set_enabled(true)` manual) — no arrancar un segundo stream.
        return Ok(());
    }

    let model_path = model_file_path(app)?;
    if !model_path.is_file() {
        return Err("No hay un modelo de voz entrenado.".to_string());
    }

    let (stop_tx, stop_rx) = mpsc::channel::<()>();
    let app_for_thread = app.clone();
    std::thread::spawn(move || run_listen_loop(app_for_thread, model_path, stop_rx));

    *handle_guard = Some(ListenLoopHandle { stop_tx });
    Ok(())
}

/// Señaliza al hilo de escucha que pare. No bloquea esperando a que
/// termine (ver docstring de `ListenLoopHandle`) — es correcto llamarla
/// aunque no haya ningún loop corriendo (no-op en ese caso).
fn stop_listen_loop(app: &AppHandle) {
    let runtime = app.state::<AlwaysListenRuntime>();
    // El `.take()` se resuelve en su propio `let` (no directo en la
    // condición de un `if let`) a propósito: el `MutexGuard` temporario que
    // devuelve `.lock().unwrap()` extiende su alcance de drop hasta el
    // final del `if let` cuando vive en la propia condición, lo que en una
    // función que termina ahí mismo choca con el drop de `runtime` (E0597,
    // "does not live long enough") — terminar el lock acá, en esta
    // sentencia, evita el problema.
    let handle = runtime.loop_handle.lock().unwrap().take();
    if let Some(handle) = handle {
        // Si el receptor ya se cerró solo (el hilo murió por su cuenta, p.
        // ej. el micrófono se desconectó), no hay nada más que hacer.
        let _ = handle.stop_tx.send(());
    }
}

/// Cuerpo completo del hilo de escucha en segundo plano: abre el
/// micrófono, carga el modelo entrenado, y alterna entre esperar la wake
/// word y bufferear el comando que sigue. Corre hasta recibir la señal de
/// `stop_rx` o hasta que el micrófono falle.
fn run_listen_loop(app: AppHandle, model_path: PathBuf, stop_rx: mpsc::Receiver<()>) {
    let (audio_tx, audio_rx) = mpsc::channel::<Vec<i16>>();
    let stream_and_config = open_input_stream(move |frame| {
        // Si el receptor ya no existe (el hilo está terminando de salir),
        // no hay nada mejor que hacer con este frame que descartarlo.
        let _ = audio_tx.send(frame.to_vec());
    });
    let (stream, supported) = match stream_and_config {
        Ok(pair) => pair,
        Err(err) => {
            eprintln!("[edecan-desktop] no se pudo iniciar la escucha en segundo plano: {err}");
            clear_loop_handle(&app);
            return;
        }
    };
    if let Err(err) = stream.play() {
        eprintln!("[edecan-desktop] no se pudo arrancar el micrófono para la escucha en segundo plano: {err}");
        clear_loop_handle(&app);
        return;
    }

    let mut rustpotter = match build_rustpotter(&model_path, supported.sample_rate()) {
        Ok(rp) => rp,
        Err(err) => {
            eprintln!("[edecan-desktop] no se pudo inicializar el detector de wake word: {err}");
            drop(stream);
            clear_loop_handle(&app);
            return;
        }
    };

    let frame_len = rustpotter.get_samples_per_frame();
    let sample_rate = supported.sample_rate();
    let mut pending: Vec<i16> = Vec::new();
    let mut command: Option<CommandCapture> = None;

    'outer: loop {
        if stop_rx.try_recv().is_ok() {
            break;
        }
        match audio_rx.recv_timeout(Duration::from_millis(200)) {
            Ok(chunk) => pending.extend(chunk),
            Err(RecvTimeoutError::Timeout) => continue,
            Err(RecvTimeoutError::Disconnected) => break,
        }

        while pending.len() >= frame_len {
            if stop_rx.try_recv().is_ok() {
                break 'outer;
            }
            let frame: Vec<i16> = pending.drain(0..frame_len).collect();
            match command.as_mut() {
                None => {
                    if rustpotter.process_samples(frame).is_some() {
                        command = Some(CommandCapture::new());
                    }
                }
                Some(capture) => {
                    if capture.push_and_check_done(&frame) {
                        let finished = command.take().unwrap();
                        if !finished.buffer.is_empty() {
                            emit_wake_detected(&app, &finished.buffer, sample_rate);
                        }
                    }
                }
            }
        }
    }

    // El stream se abrió, se usó y se dropea acá, en este mismo hilo que lo
    // creó (ver docstring del módulo sobre `cpal::Stream` y `Send`).
    drop(stream);
    clear_loop_handle(&app);
}

/// Si el hilo de escucha termina por su cuenta (micrófono desconectado,
/// error al abrirlo), limpia el handle para que `always_listen_get_state`
/// deje de reportar `listening:true` con un hilo que ya murió.
fn clear_loop_handle(app: &AppHandle) {
    let runtime = app.state::<AlwaysListenRuntime>();
    *runtime.loop_handle.lock().unwrap() = None;
}

fn build_rustpotter(model_path: &Path, sample_rate: u32) -> Result<Rustpotter, String> {
    let mut config = RustpotterConfig::default();
    // Solo `sample_rate`/`channels` importan para el camino
    // `process_samples::<i16>` (`AudioEncoder::rencode_and_resample`, ver
    // audio/encoder.rs del crate) — `sample_format`/`endianness` de
    // `AudioFmt` solo se usan en el camino de bytes (`process_bytes`, que
    // este módulo no usa), así que se dejan en su default.
    config.fmt.sample_rate = sample_rate as usize;
    config.fmt.channels = 1;

    let mut rustpotter = Rustpotter::new(&config)?;
    let model_path_str = model_path
        .to_str()
        .ok_or_else(|| "Ruta del modelo de voz con caracteres inválidos.".to_string())?;
    rustpotter.add_wakeword_from_file("wake", model_path_str)?;
    Ok(rustpotter)
}

/// Estado de "ya detecté la wake word, estoy bufereando lo que el usuario
/// dice después". Ver criterio de corte al principio del archivo.
struct CommandCapture {
    buffer: Vec<i16>,
    spoke: bool,
    silence_since: Instant,
    started_at: Instant,
}

impl CommandCapture {
    fn new() -> Self {
        let now = Instant::now();
        CommandCapture {
            buffer: Vec::new(),
            spoke: false,
            silence_since: now,
            started_at: now,
        }
    }

    /// Agrega `frame` al buffer y devuelve `true` si corresponde cortar la
    /// captura acá (silencio sostenido tras haber hablado, o tope duro).
    fn push_and_check_done(&mut self, frame: &[i16]) -> bool {
        self.buffer.extend_from_slice(frame);
        let now = Instant::now();
        if frame_energy(frame) > COMMAND_VOICE_THRESHOLD {
            self.spoke = true;
            self.silence_since = now;
        }
        let silence_long = self.spoke
            && now.duration_since(self.silence_since) > Duration::from_millis(COMMAND_SILENCE_MS);
        let timed_out = now.duration_since(self.started_at) > Duration::from_millis(COMMAND_MAX_MS);
        silence_long || timed_out
    }
}

/// RMS de `frame` normalizado a 0-1 sobre `i16::MAX` — ver nota sobre el
/// criterio de corte al principio del archivo.
fn frame_energy(frame: &[i16]) -> f32 {
    if frame.is_empty() {
        return 0.0;
    }
    let sum_squares: f64 = frame
        .iter()
        .map(|&sample| (sample as f64) * (sample as f64))
        .sum();
    let rms = (sum_squares / frame.len() as f64).sqrt();
    (rms / i16::MAX as f64) as f32
}

/// Codifica el comando capturado como WAV, lo pasa a base64, muestra/enfoca
/// la ventana principal si existe, y emite `edecan://wake-detected` con el
/// payload exacto que espera el frontend: `{"audio_base64": "...", "mime":
/// "audio/wav"}`.
fn emit_wake_detected(app: &AppHandle, samples: &[i16], sample_rate: u32) {
    let wav_bytes = match encode_wav_mono_i16(samples, sample_rate) {
        Ok(bytes) => bytes,
        Err(err) => {
            eprintln!(
                "[edecan-desktop] no se pudo codificar el audio del comando capturado: {err}"
            );
            return;
        }
    };

    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
    }
    // Si "main" todavía no existe (p. ej. el backend local no terminó de
    // arrancar todavía la primera vez), no hay ventana que mostrar/enfocar
    // acá. Se deja como limitación conocida en vez de resolverla de forma
    // más elaborada (esperar a que aparezca, encolar el evento, etc.) — el
    // evento se emite igual y el frontend lo recibe apenas la ventana
    // exista y esté escuchando.

    let payload = serde_json::json!({
        "audio_base64": encode_base64(&wav_bytes),
        "mime": "audio/wav",
    });
    let _ = app.emit("edecan://wake-detected", payload);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn base64_matches_rfc_4648_vectors() {
        let vectors = [
            (b"".as_slice(), ""),
            (b"f".as_slice(), "Zg=="),
            (b"fo".as_slice(), "Zm8="),
            (b"foo".as_slice(), "Zm9v"),
            (b"foobar".as_slice(), "Zm9vYmFy"),
        ];

        for (input, expected) in vectors {
            assert_eq!(encode_base64(input), expected);
        }
    }

    #[test]
    fn downmix_uses_first_channel_from_each_frame() {
        assert_eq!(downmix_to_i16(&[10_i16, 20, 30, 40], 2), vec![10, 30]);
    }

    #[test]
    fn wake_label_is_trimmed_and_bounded() {
        assert_eq!(
            validate_wake_label("  Oye Edecán  ".to_string()).unwrap(),
            "Oye Edecán"
        );
        assert!(validate_wake_label("   ".to_string()).is_err());
        assert!(validate_wake_label("hola\nEdecán".to_string()).is_err());
        assert!(validate_wake_label("x".repeat(MAX_WAKE_LABEL_CHARS + 1)).is_err());
    }

    #[test]
    fn frame_energy_handles_silence_and_full_scale() {
        assert_eq!(frame_energy(&[]), 0.0);
        assert_eq!(frame_energy(&[0, 0, 0]), 0.0);
        assert!((frame_energy(&[i16::MAX, i16::MAX]) - 1.0).abs() < f32::EPSILON);
    }
}
