import Foundation

/// Comandos que el composer del chat intercepta ANTES de mandar el texto como
/// un mensaje normal -- viven en el cliente, no en el modelo.
///
/// Por qué: `/clear` escrito en el chat se interpretaba como texto suelto y
/// el modelo decidía qué hacer con él, así que "limpiaba" el contexto por su
/// cuenta (a veces) pero nunca la pantalla -- el dueño lo reportó así:
/// "si escribo /clear se limpia es el contexto más no el chat". La corrección
/// es reconocerlo ACÁ, de forma determinista, y que `ChatViewModel` actúe
/// llamando al backend directo (`POST /v1/conversations/{id}/clear`) en vez
/// de mandarlo como mensaje. Sacarlo a `EdecanKit` (y no dejarlo en
/// `ChatViewModel`, que vive en el target de la app sin bundle de pruebas)
/// es lo que permite escribirle un test de verdad.
public enum LocalChatCommand: Equatable {
    /// Reinicia el contexto de la conversación abierta. Por defecto es NO
    /// destructivo: no borra ningún mensaje, solo mueve el límite desde el
    /// que el próximo turno arma lo que el modelo recuerda (ver el docstring
    /// de `POST /{conversation_id}/clear` en el backend).
    case clear
    case branch
    case rewind

    /// Reconoce el comando si `texto` (ya recortado o no de espacios) es
    /// ÚNICAMENTE `/clear`, `/branch` o `/rewind`, sin importar mayúsculas.
    /// Debe ser el mensaje completo a propósito: una frase que solo MENCIONA
    /// el comando no debe dispararlo.
    public static func parse(_ texto: String) -> LocalChatCommand? {
        let limpio = texto.trimmingCharacters(in: .whitespacesAndNewlines)
        if limpio.caseInsensitiveCompare("/clear") == .orderedSame { return .clear }
        if limpio.caseInsensitiveCompare("/branch") == .orderedSame { return .branch }
        if limpio.caseInsensitiveCompare("/rewind") == .orderedSame { return .rewind }
        return nil
    }
}
