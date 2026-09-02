import SwiftUI
import EdecanKit

/// Paleta light-only del estudio nativo iOS.
///
/// El IDE **nunca** sigue el modo oscuro del sistema: chrome, explorador,
/// terminal y listas usan superficies claras al estilo Cursor light / Linear,
/// con acentos de ``EdecanTheme``. El editor mantiene contraste legible sobre
/// fondo claro con resaltado de sintaxis.
enum IDETheme {
    // MARK: Superficies (light-only)

    /// Fondo principal del estudio.
    static let fondo = Color(red: 0.97, green: 0.975, blue: 0.985)
    /// Tarjetas, paneles y sidebar.
    static let superficie = Color.white
    /// Borde sutil entre paneles.
    static let superficieBorde = Color.black.opacity(0.08)
    /// Panel del terminal (off-white, no caja negra).
    static let terminal = Color(red: 0.985, green: 0.988, blue: 0.995)
    /// Barra de herramientas del terminal / cabecera de editor.
    static let terminalLinea = Color(red: 0.955, green: 0.96, blue: 0.975)
    /// Gutter del editor (números de línea).
    static let gutter = Color(red: 0.945, green: 0.95, blue: 0.965)
    /// Línea de selección / archivo activo.
    static let seleccion = EdecanTheme.morado.opacity(0.10)

    // MARK: Texto

    static let texto = Color(red: 0.11, green: 0.13, blue: 0.18)
    static let textoSuave = Color(red: 0.42, green: 0.46, blue: 0.54)
    static let numeroLinea = Color(red: 0.62, green: 0.66, blue: 0.74)

    // MARK: Acentos (alineados con EdecanTheme)

    static let acento = EdecanTheme.morado
    static let acentoSecundario = EdecanTheme.azul
    static let verde = Color(red: 0.13, green: 0.65, blue: 0.42)
    static let naranja = Color(red: 0.88, green: 0.52, blue: 0.12)
    static let rojo = Color(red: 0.86, green: 0.24, blue: 0.28)

    // MARK: Terminal (light, legible)

    static let terminalTexto = Color(red: 0.14, green: 0.16, blue: 0.22)
    static let terminalStderr = Color(red: 0.78, green: 0.22, blue: 0.18)
    static let terminalPrompt = EdecanTheme.morado
    static let terminalPlaceholder = Color(red: 0.55, green: 0.58, blue: 0.65)

    // MARK: Segmented control

    static let segTrack = Color.black.opacity(0.04)
    static let segSeleccion = Color.white

    // MARK: Sombras

    static let sombraSuave = Color.black.opacity(0.06)

    // MARK: Helpers compartidos

    static func estadoDescripcion(_ sesion: IDESession?) -> String {
        guard let sesion else { return "Sin sesión activa" }
        switch sesion.status {
        case "starting": return "Preparando"
        case "running": return sesion.isActive ? "En ejecución" : "Finalizada"
        case "completed": return "Terminado"
        case "failed": return "Falló"
        case "closed": return "Cerrada"
        case "cancelled": return "Cancelado"
        case "interrupted": return "Interrumpida al reiniciar"
        default: return sesion.status.capitalized
        }
    }

    static func estadoColor(_ sesion: IDESession?) -> Color {
        guard let sesion else { return textoSuave }
        if sesion.isActive { return verde }
        switch sesion.status {
        case "completed": return textoSuave
        case "failed", "interrupted": return rojo
        case "cancelled", "closed": return textoSuave
        default: return naranja
        }
    }

    /// Etiqueta corta para filas de sesión (activa vs histórica).
    static func estadoEtiqueta(_ sesion: IDESession) -> String {
        if sesion.isActive { return "Activa" }
        switch sesion.status {
        case "completed": return "Terminada"
        case "failed": return "Falló"
        case "interrupted": return "Interrumpida"
        case "cancelled", "closed": return "Cerrada"
        default: return "Inactiva"
        }
    }

    static func eventoEtiqueta(_ evento: IDESessionEvent) -> String {
        if evento.stream == "stderr" { return "Aviso" }
        if evento.type == "status" { return "Estado" }
        if evento.type == "exit" { return "Finalizado" }
        return "Edecán"
    }

    static func eventoIcono(_ evento: IDESessionEvent) -> String {
        if evento.stream == "stderr" || evento.type == "error" {
            return "exclamationmark.triangle.fill"
        }
        if evento.type == "exit" { return "checkmark.circle.fill" }
        if evento.type == "status" { return "bolt.fill" }
        return "sparkles"
    }

    static func eventoColor(_ evento: IDESessionEvent) -> Color {
        if evento.stream == "stderr" || evento.type == "error" { return naranja }
        if evento.type == "exit" { return verde }
        return acento
    }

    static func archivoIcono(_ nombre: String) -> String {
        switch (nombre as NSString).pathExtension.lowercased() {
        case "swift", "py", "js", "ts", "tsx", "jsx", "rs", "go", "java", "kt":
            return "chevron.left.forwardslash.chevron.right"
        case "md", "txt":
            return "doc.text"
        case "json", "yml", "yaml", "toml":
            return "gearshape"
        case "png", "jpg", "jpeg", "gif", "svg":
            return "photo"
        default:
            return "doc"
        }
    }
}

// MARK: - Modificadores reutilizables

extension View {
    /// Fuerza light mode en todo el árbol del IDE (regla de producto).
    func ideLightOnly() -> some View {
        preferredColorScheme(.light)
    }

    /// Tarjeta elevada del estudio light.
    func idePanel(esquina: CGFloat = 16) -> some View {
        background(IDETheme.superficie, in: RoundedRectangle(cornerRadius: esquina, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: esquina, style: .continuous)
                    .strokeBorder(IDETheme.superficieBorde, lineWidth: 1)
            )
            .shadow(color: IDETheme.sombraSuave, radius: 8, y: 2)
    }
}
