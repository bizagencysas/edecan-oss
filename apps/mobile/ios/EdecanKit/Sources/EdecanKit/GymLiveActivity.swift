import Foundation

#if os(iOS)
import ActivityKit

/// Atributos y estado del Live Activity del gimnasio. Vive en EdecanKit (el
/// paquete compartido) para que `EdecanApp` lo arranque y `EdecanWidgets` lo
/// renderice con el MISMO tipo, sin duplicar la definición entre targets.
///
/// El `#if os(iOS)` existe por una sola razón: EdecanKit también compila para
/// macOS (para que `swift build`/`swift test` corran en local sin simulador,
/// ver `Package.swift`), y ActivityKit solo existe en iOS — ahí el protocolo
/// `ActivityAttributes` está marcado como no disponible. En macOS este archivo
/// compila a nada y ningún otro símbolo de EdecanKit lo referencia.
///
/// `ActivityAttributes` hereda de `Decodable`/`Encodable`, así que este tipo
/// (sin propiedades) implementa las dos requirements como no-ops.
public struct GymActivityAttributes: ActivityAttributes {
    public typealias ContentState = GymActivityContentState

    /// Estado que pinta el widget: ejercicio actual, series hechas/totales y
    /// el instante en que arrancó la sesión (para el cronómetro).
    public struct GymActivityContentState: Codable, Hashable {
        public var ejercicio: String
        public var seriesHechas: Int
        public var seriesTotales: Int
        public var startedAt: Date

        public init(ejercicio: String, seriesHechas: Int, seriesTotales: Int, startedAt: Date) {
            self.ejercicio = ejercicio
            self.seriesHechas = seriesHechas
            self.seriesTotales = seriesTotales
            self.startedAt = startedAt
        }
    }

    public init() {}

    public init(from decoder: Decoder) throws {}

    public func encode(to encoder: Encoder) throws {}
}
#endif