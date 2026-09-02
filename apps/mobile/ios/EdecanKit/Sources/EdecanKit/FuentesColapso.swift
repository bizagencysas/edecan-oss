import Foundation

/// Reglas de colapso para la sección «Fuentes» del chat iOS. Vive en EdecanKit
/// para poder probarse sin SwiftUI.
public enum FuentesColapso {
    public static let umbralColapso = 2
    public static let visiblesColapsado = 1

    public static func debeColapsar(total: Int) -> Bool {
        total > umbralColapso
    }

    public static func cantidadVisible(total: Int, expandido: Bool) -> Int {
        guard total > 0 else { return 0 }
        guard debeColapsar(total: total) else { return total }
        return expandido ? total : visiblesColapsado
    }

    public static func etiquetaExpansion(total: Int, expandido: Bool) -> String? {
        guard debeColapsar(total: total) else { return nil }
        if expandido { return "Ver menos" }
        return "Ver \(total - visiblesColapsado) más"
    }
}
