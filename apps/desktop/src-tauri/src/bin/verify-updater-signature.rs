use base64::{engine::general_purpose::STANDARD as BASE64_STANDARD, Engine as _};
use minisign_verify::{PublicKey, Signature};
use std::{
    env,
    fs::{self, File},
    io::Read,
    path::Path,
    process,
};

fn verify(artifact: &Path, signature_path: &Path, public_key_path: &Path) -> Result<(), String> {
    let public_key = PublicKey::from_file(public_key_path)
        .map_err(|error| format!("no se pudo leer la clave pública fijada: {error}"))?;
    let encoded_signature = fs::read_to_string(signature_path)
        .map_err(|error| format!("no se pudo leer la firma del updater: {error}"))?;
    let decoded_signature = BASE64_STANDARD
        .decode(encoded_signature.trim())
        .map_err(|error| format!("la firma del updater no usa el formato de Tauri: {error}"))?;
    let decoded_signature = String::from_utf8(decoded_signature)
        .map_err(|error| format!("la firma decodificada no es texto minisign: {error}"))?;
    let signature = Signature::decode(&decoded_signature)
        .map_err(|error| format!("la firma minisign no es válida: {error}"))?;
    let mut verifier = public_key
        .verify_stream(&signature)
        .map_err(|error| format!("la firma no admite verificación en streaming: {error}"))?;
    let mut file =
        File::open(artifact).map_err(|error| format!("no se pudo abrir el artefacto: {error}"))?;
    let mut buffer = vec![0_u8; 1024 * 1024];
    loop {
        let read = file
            .read(&mut buffer)
            .map_err(|error| format!("no se pudo leer el artefacto: {error}"))?;
        if read == 0 {
            break;
        }
        verifier.update(&buffer[..read]);
    }
    verifier
        .finalize()
        .map_err(|error| format!("la firma no pertenece a la clave pública fijada: {error}"))
}

fn main() {
    let arguments: Vec<_> = env::args_os().skip(1).collect();
    if arguments.len() != 3 {
        eprintln!("uso: verify-updater-signature ARTEFACTO FIRMA CLAVE_PUBLICA");
        process::exit(2);
    }
    if let Err(error) = verify(
        Path::new(&arguments[0]),
        Path::new(&arguments[1]),
        Path::new(&arguments[2]),
    ) {
        eprintln!("error: {error}");
        process::exit(1);
    }
    println!("Firma del updater verificada.");
}
