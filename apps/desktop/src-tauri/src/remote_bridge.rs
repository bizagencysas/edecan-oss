//! Puente remoto local entre el sidecar y el proceso `.app` autorizado.
//!
//! macOS concede Grabacion de pantalla y Accesibilidad al proceso principal
//! de Edecan. El sidecar sigue siendo el orquestador, pero no debe tocar TCC:
//! envia acciones tipadas por un socket Unix privado y este modulo ejecuta
//! solo captura, mouse y teclado. No hay shell, rutas elegidas por el cliente
//! ni comandos arbitrarios.

use std::sync::Mutex;

use tauri::{AppHandle, Manager};

#[derive(Clone, Debug)]
pub struct RemoteBridgeCredentials {
    pub socket_path: String,
    pub token: String,
}

#[derive(Default)]
pub struct RemoteBridgeState(pub Mutex<Option<RemoteBridgeCredentials>>);

#[cfg(not(target_os = "macos"))]
pub fn ensure_started(_app: &AppHandle) -> Result<Option<RemoteBridgeCredentials>, String> {
    Ok(None)
}

#[cfg(target_os = "macos")]
pub fn ensure_started(app: &AppHandle) -> Result<Option<RemoteBridgeCredentials>, String> {
    use std::os::unix::fs::PermissionsExt;
    use std::os::unix::net::UnixListener;

    let state = app.state::<RemoteBridgeState>();
    if let Some(existing) = state.0.lock().unwrap().clone() {
        return Ok(Some(existing));
    }

    let token = random_hex(32)?;
    let socket_path = std::env::temp_dir().join(format!(
        "edecan-remote-{}-{}.sock",
        std::process::id(),
        &token[..12]
    ));
    let listener = UnixListener::bind(&socket_path)
        .map_err(|error| format!("no se pudo crear el socket privado: {error}"))?;
    std::fs::set_permissions(&socket_path, std::fs::Permissions::from_mode(0o600))
        .map_err(|error| format!("no se pudieron restringir los permisos del socket: {error}"))?;

    let credentials = RemoteBridgeCredentials {
        socket_path: socket_path.to_string_lossy().into_owned(),
        token: token.clone(),
    };
    *state.0.lock().unwrap() = Some(credentials.clone());

    std::thread::Builder::new()
        .name("edecan-remote-bridge".into())
        .spawn(move || serve(listener, token))
        .map_err(|error| format!("no se pudo iniciar el hilo del puente: {error}"))?;

    Ok(Some(credentials))
}

#[cfg(target_os = "macos")]
fn random_hex(byte_count: usize) -> Result<String, String> {
    let mut bytes = vec![0_u8; byte_count];
    getrandom::getrandom(&mut bytes)
        .map_err(|error| format!("no se pudo generar la capacidad remota: {error}"))?;
    Ok(bytes.iter().map(|byte| format!("{byte:02x}")).collect())
}

#[cfg(target_os = "macos")]
fn serve(listener: std::os::unix::net::UnixListener, token: String) {
    for connection in listener.incoming() {
        let Ok(mut stream) = connection else { continue };
        if let Err(error) = handle_connection(&mut stream, &token) {
            let response = serde_json::json!({"ok": false, "error": error});
            let _ = write_response(&mut stream, &response);
        }
    }
}

#[cfg(target_os = "macos")]
fn handle_connection(
    stream: &mut std::os::unix::net::UnixStream,
    expected_token: &str,
) -> Result<(), String> {
    use std::io::{BufRead, Read};

    const MAX_REQUEST_BYTES: u64 = 1024 * 1024;
    let mut request_line = String::new();
    let mut reader = std::io::BufReader::new(
        stream
            .try_clone()
            .map_err(|error| format!("no se pudo leer el socket: {error}"))?,
    );
    reader
        .by_ref()
        .take(MAX_REQUEST_BYTES)
        .read_line(&mut request_line)
        .map_err(|error| format!("no se pudo leer la solicitud: {error}"))?;
    if request_line.len() >= MAX_REQUEST_BYTES as usize {
        return Err("solicitud remota demasiado grande".into());
    }

    let request: serde_json::Value =
        serde_json::from_str(&request_line).map_err(|_| "solicitud remota invalida".to_string())?;
    if request.get("token").and_then(|value| value.as_str()) != Some(expected_token) {
        return Err("capacidad remota invalida".into());
    }
    let action = request
        .get("action")
        .and_then(|value| value.as_str())
        .ok_or_else(|| "falta la accion remota".to_string())?;
    let params = request
        .get("params")
        .and_then(|value| value.as_object())
        .cloned()
        .unwrap_or_default();

    let result = match action {
        "screenshot" => capture_screen(&params)?,
        "move_pointer" | "click_pointer" | "pointer_down" | "pointer_up" | "scroll_pointer"
        | "type_text" | "press_key" => execute_input(action, &params)?,
        _ => return Err("accion remota no permitida".into()),
    };
    write_response(stream, &serde_json::json!({"ok": true, "result": result}))
}

#[cfg(target_os = "macos")]
fn write_response(
    stream: &mut std::os::unix::net::UnixStream,
    value: &serde_json::Value,
) -> Result<(), String> {
    use std::io::Write;

    let mut bytes = serde_json::to_vec(value)
        .map_err(|error| format!("no se pudo serializar la respuesta: {error}"))?;
    bytes.push(b'\n');
    stream
        .write_all(&bytes)
        .map_err(|error| format!("no se pudo responder por el socket: {error}"))
}

#[cfg(target_os = "macos")]
#[link(name = "CoreGraphics", kind = "framework")]
extern "C" {
    fn CGPreflightScreenCaptureAccess() -> bool;
}

#[cfg(target_os = "macos")]
#[link(name = "ApplicationServices", kind = "framework")]
extern "C" {
    fn AXIsProcessTrusted() -> bool;
}

#[cfg(target_os = "macos")]
fn capture_screen(
    params: &serde_json::Map<String, serde_json::Value>,
) -> Result<serde_json::Value, String> {
    use base64::Engine as _;

    if !unsafe { CGPreflightScreenCaptureAccess() } {
        return Err(
            "Grabacion de pantalla no esta autorizada para la app principal de Edecan".into(),
        );
    }

    let display = params
        .get("display")
        .and_then(|value| value.as_u64())
        .unwrap_or(1);
    if !(1..=32).contains(&display) {
        return Err("pantalla fuera de rango".into());
    }
    let include_cursor = params
        .get("include_cursor")
        .and_then(|value| value.as_bool())
        .unwrap_or(true);
    let output = std::env::temp_dir().join(format!(
        "edecan-native-capture-{}-{}.png",
        std::process::id(),
        random_hex(8)?
    ));
    let mut command = std::process::Command::new("/usr/sbin/screencapture");
    command.args(["-x", "-t", "png", "-D", &display.to_string()]);
    if include_cursor {
        command.arg("-C");
    }
    let status = command
        .arg(&output)
        .status()
        .map_err(|error| format!("no se pudo ejecutar screencapture: {error}"))?;
    if !status.success() {
        let _ = std::fs::remove_file(&output);
        return Err(format!("screencapture termino con {status}"));
    }
    let image =
        std::fs::read(&output).map_err(|error| format!("no se pudo leer la captura: {error}"));
    let _ = std::fs::remove_file(&output);
    let image = image?;
    if image.is_empty() {
        return Err("screencapture devolvio una imagen vacia".into());
    }
    Ok(serde_json::json!({
        "image_b64": base64::engine::general_purpose::STANDARD.encode(image)
    }))
}

#[cfg(target_os = "macos")]
fn int_param(
    params: &serde_json::Map<String, serde_json::Value>,
    name: &str,
) -> Result<i64, String> {
    params
        .get(name)
        .and_then(|value| value.as_i64())
        .ok_or_else(|| format!("falta el parametro {name}"))
}

#[cfg(target_os = "macos")]
fn execute_input(
    action: &str,
    params: &serde_json::Map<String, serde_json::Value>,
) -> Result<serde_json::Value, String> {
    use core_graphics::event::{
        CGEvent, CGEventFlags, CGEventTapLocation, CGEventType, CGMouseButton, KeyCode,
        ScrollEventUnit,
    };
    use core_graphics::event_source::{CGEventSource, CGEventSourceStateID};
    use core_graphics::geometry::CGPoint;

    if !unsafe { AXIsProcessTrusted() } {
        return Err("Accesibilidad no esta autorizada para la app principal de Edecan".into());
    }
    let source = || {
        CGEventSource::new(CGEventSourceStateID::HIDSystemState)
            .map_err(|_| "no se pudo crear la fuente de eventos".to_string())
    };
    let point = |params: &serde_json::Map<String, serde_json::Value>| -> Result<CGPoint, String> {
        Ok(CGPoint::new(
            int_param(params, "x")? as f64,
            int_param(params, "y")? as f64,
        ))
    };
    let button = match params
        .get("button")
        .and_then(|value| value.as_str())
        .unwrap_or("left")
    {
        "left" => CGMouseButton::Left,
        "right" => CGMouseButton::Right,
        "middle" => CGMouseButton::Center,
        _ => return Err("boton de mouse invalido".into()),
    };
    let mouse_types = |down: bool, button: CGMouseButton| match (down, button) {
        (true, CGMouseButton::Left) => CGEventType::LeftMouseDown,
        (false, CGMouseButton::Left) => CGEventType::LeftMouseUp,
        (true, CGMouseButton::Right) => CGEventType::RightMouseDown,
        (false, CGMouseButton::Right) => CGEventType::RightMouseUp,
        (true, CGMouseButton::Center) => CGEventType::OtherMouseDown,
        (false, CGMouseButton::Center) => CGEventType::OtherMouseUp,
    };
    let post_mouse = |event_type, location, button| -> Result<(), String> {
        CGEvent::new_mouse_event(source()?, event_type, location, button)
            .map_err(|_| "no se pudo crear el evento de mouse".to_string())?
            .post(CGEventTapLocation::HID);
        Ok(())
    };

    match action {
        "move_pointer" => post_mouse(CGEventType::MouseMoved, point(params)?, button)?,
        "click_pointer" => {
            let location = point(params)?;
            post_mouse(mouse_types(true, button), location, button)?;
            post_mouse(mouse_types(false, button), location, button)?;
        }
        "pointer_down" => post_mouse(mouse_types(true, button), point(params)?, button)?,
        "pointer_up" => post_mouse(mouse_types(false, button), point(params)?, button)?,
        "scroll_pointer" => {
            let delta_x = int_param(params, "delta_x")? as i32;
            let delta_y = int_param(params, "delta_y")? as i32;
            CGEvent::new_scroll_event(source()?, ScrollEventUnit::PIXEL, 2, delta_y, delta_x, 0)
                .map_err(|_| "no se pudo crear el evento de scroll".to_string())?
                .post(CGEventTapLocation::HID);
        }
        "type_text" => {
            let text = params
                .get("text")
                .and_then(|value| value.as_str())
                .ok_or_else(|| "falta el texto".to_string())?;
            for character in text.chars() {
                let rendered = character.to_string();
                for key_down in [true, false] {
                    let event = CGEvent::new_keyboard_event(source()?, 0, key_down)
                        .map_err(|_| "no se pudo crear el evento de teclado".to_string())?;
                    event.set_string(&rendered);
                    event.post(CGEventTapLocation::HID);
                }
            }
        }
        "press_key" => {
            let key = params
                .get("key")
                .and_then(|value| value.as_str())
                .ok_or_else(|| "falta la tecla".to_string())?;
            let keycode = match key {
                "enter" => KeyCode::RETURN,
                "tab" => KeyCode::TAB,
                "escape" => KeyCode::ESCAPE,
                "backspace" => KeyCode::DELETE,
                "delete_forward" => KeyCode::FORWARD_DELETE,
                "arrow_up" => KeyCode::UP_ARROW,
                "arrow_down" => KeyCode::DOWN_ARROW,
                "arrow_left" => KeyCode::LEFT_ARROW,
                "arrow_right" => KeyCode::RIGHT_ARROW,
                "home" => KeyCode::HOME,
                "end" => KeyCode::END,
                "page_up" => KeyCode::PAGE_UP,
                "page_down" => KeyCode::PAGE_DOWN,
                "space" => KeyCode::SPACE,
                "a" => KeyCode::ANSI_A,
                "c" => KeyCode::ANSI_C,
                "v" => KeyCode::ANSI_V,
                "x" => KeyCode::ANSI_X,
                "z" => KeyCode::ANSI_Z,
                "s" => KeyCode::ANSI_S,
                _ => return Err("tecla no permitida".into()),
            };
            let mut flags = CGEventFlags::empty();
            if let Some(modifiers) = params.get("modifiers").and_then(|value| value.as_array()) {
                for modifier in modifiers.iter().filter_map(|value| value.as_str()) {
                    flags |= match modifier {
                        "command" => CGEventFlags::CGEventFlagCommand,
                        "control" => CGEventFlags::CGEventFlagControl,
                        "option" => CGEventFlags::CGEventFlagAlternate,
                        "shift" => CGEventFlags::CGEventFlagShift,
                        _ => return Err("modificador no permitido".into()),
                    };
                }
            }
            for key_down in [true, false] {
                let event = CGEvent::new_keyboard_event(source()?, keycode, key_down)
                    .map_err(|_| "no se pudo crear el evento de teclado".to_string())?;
                event.set_flags(flags);
                event.post(CGEventTapLocation::HID);
            }
        }
        _ => return Err("accion de input no permitida".into()),
    }
    Ok(serde_json::json!({"executed": true}))
}

#[cfg(all(test, target_os = "macos"))]
mod tests {
    use super::*;

    #[test]
    fn generated_tokens_are_random_and_fixed_length() {
        let first = random_hex(32).unwrap();
        let second = random_hex(32).unwrap();
        assert_eq!(first.len(), 64);
        assert_ne!(first, second);
    }
}
