import Foundation

/// Formato/copy compartido de la tarjeta de confirmación de herramientas
/// `dangerous` (``TarjetaConfirmacion`` en `EdecanApp`, reutilizada por
/// Chat/Voz/Misiones) — vive en `EdecanKit` (no en la vista) por el mismo
/// motivo que `JSONValue.vistaPrevia`: es formato de datos puro, testeable
/// sin SwiftUI, aunque su único consumidor real sea una vista.
public enum ConfirmacionFormato {
    /// Advertencias específicas por herramienta, en lenguaje llano, ADEMÁS
    /// del JSON crudo de sus argumentos — mismo hallazgo de auditoría
    /// "riesgo-legal-tos" que ya corrigió la web
    /// (`apps/web/src/components/chat/ConfirmationCard.tsx`,
    /// `ADVERTENCIAS_POR_HERRAMIENTA`): una tarjeta genérica no le da a quien
    /// aprueba ninguna pista concreta de qué mirar antes de confirmar.
    /// `usar_computadora` (control remoto de pantalla/mouse/teclado,
    /// `packages/toolkit/edecan_toolkit/computadora.py`) es la más
    /// importante — texto calcado tal cual del de la web. Diccionario
    /// extensible: cualquier herramienta sin entrada acá se sigue mostrando
    /// exactamente igual que antes de este cambio, sin ningún texto extra.
    public static let advertenciasPorHerramienta: [String: String] = [
        "usar_computadora":
            "Esto va a mover el mouse, escribir o mirar la pantalla de tu computadora de verdad. " +
            "Revisa qué hay en pantalla antes de aprobar: Edecán nunca debe navegar, hacer clic, " +
            "escribir ni leer contenido de LinkedIn, ni completar un pago, cobro o inicio de sesión " +
            "por ti. Si eso es lo que está a punto de hacer, rechaza.",
    ]

    /// `nil` si `nombre` no tiene una advertencia específica registrada.
    public static func advertencia(paraHerramienta nombre: String) -> String? {
        advertenciasPorHerramienta[nombre]
    }

    /// Recorta `texto` a `limite` caracteres (más una elipsis) — mismo motivo
    /// que el `max-h-32 overflow-auto` de la tarjeta web, pero para el
    /// `Text` de SwiftUI dentro de una tarjeta de tamaño más o menos fijo:
    /// evita que un argumento enorme (p. ej. un `write_file` con contenido
    /// largo, o un `usar_computadora` con un `image_b64`) reviente el layout
    /// de ``TarjetaConfirmacion``. `texto` más corto que `limite` vuelve tal
    /// cual, sin elipsis.
    public static func vistaPreviaRecortada(_ texto: String, limite: Int = 400) -> String {
        guard texto.count > limite else { return texto }
        let indice = texto.index(texto.startIndex, offsetBy: limite)
        return String(texto[..<indice]) + "…"
    }
}
