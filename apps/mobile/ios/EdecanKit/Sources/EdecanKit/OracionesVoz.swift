import Foundation

/// Trocea texto en oraciones para TTS progresivo si fuera necesario.
/// Fragmentos de menos de 20 caracteres se fusionan con el siguiente para no
/// disparar una síntesis por una abreviatura.
public enum OracionesVoz {
    public static func partir(_ texto: String) -> [String] {
        let recortado = texto.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !recortado.isEmpty else { return [] }

        let crudo = separarPorPunto(recortado)
        var fusionadas: [String] = []
        for oracion in crudo {
            if let ultima = fusionadas.last, ultima.count < 20 {
                fusionadas[fusionadas.count - 1] = "\(ultima) \(oracion)"
            } else {
                fusionadas.append(oracion)
            }
        }
        return fusionadas
    }

    private static func separarPorPunto(_ texto: String) -> [String] {
        guard let regex = try? NSRegularExpression(pattern: #"(?<=[.!?])\s+"#) else {
            return [texto]
        }
        let rango = NSRange(texto.startIndex..., in: texto)
        var piezas: [String] = []
        var cursor = texto.startIndex
        for match in regex.matches(in: texto, range: rango) {
            guard let corte = Range(match.range, in: texto) else { continue }
            let pieza = texto[cursor..<corte.lowerBound]
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if !pieza.isEmpty { piezas.append(String(pieza)) }
            cursor = corte.upperBound
        }
        let cola = texto[cursor...].trimmingCharacters(in: .whitespacesAndNewlines)
        if !cola.isEmpty { piezas.append(cola) }
        return piezas
    }
}
