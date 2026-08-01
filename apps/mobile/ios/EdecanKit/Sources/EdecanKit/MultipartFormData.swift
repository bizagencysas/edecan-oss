import Foundation

/// Helpers mínimos para armar un cuerpo `multipart/form-data` a mano — sin
/// dependencias externas (regla dura del repo), usados hoy solo por
/// `APIClient.subirAudioMultipart(audioData:mimeType:language:)` para
/// `POST /v1/voice/transcribe`.
extension Data {
    mutating func append(_ string: String) {
        if let data = string.data(using: .utf8) {
            append(data)
        }
    }

    mutating func appendCampoDeTexto(nombre: String, valor: String, boundary: String) {
        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"\(nombre)\"\r\n\r\n")
        append("\(valor)\r\n")
    }

    mutating func appendCampoDeArchivo(
        nombre: String, filename: String, mimeType: String, contenido: Data, boundary: String
    ) {
        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"\(nombre)\"; filename=\"\(filename)\"\r\n")
        append("Content-Type: \(mimeType)\r\n\r\n")
        append(contenido)
        append("\r\n")
    }
}
