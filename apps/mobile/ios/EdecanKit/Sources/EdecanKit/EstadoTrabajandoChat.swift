import Foundation

/// Reglas de visibilidad para la fila silenciosa «{Nombre} está trabajando»
/// del chat. Vive en EdecanKit para poder probarse sin SwiftUI.
public enum EstadoTrabajandoChat {
    /// Muestra la fila mientras el turno del asistente sigue en curso.
    public static func debeMostrarFila(enProgreso: Bool, rolEsAsistente: Bool) -> Bool {
        enProgreso && rolEsAsistente
    }

    /// Etiqueta de accesibilidad VoiceOver / lectura unificada.
    public static func etiquetaAccesibilidad(nombreAgente: String) -> String {
        let nombre = nombreAgente.trimmingCharacters(in: .whitespacesAndNewlines)
        let visible = nombre.isEmpty ? "Edecán" : nombre
        return "\(visible) está trabajando"
    }

    /// La tarjeta «Ejecutó N comandos» ya no se muestra en el hilo del chat.
    public static func debeMostrarTarjetaProgresoHerramientas(enProgreso: Bool) -> Bool {
        false
    }
}
