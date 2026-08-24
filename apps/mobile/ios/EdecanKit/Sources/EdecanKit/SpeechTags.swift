import Foundation

/// Speech tags y efectos de Eleven v3. No hay lista blanca: `[thoughtfully]`,
/// `[laughs]`, `[applause]`, `[clears throat]` o lo que invente el modelo
/// viaja intacto hasta el TTS. El chat las oculta todas.
public enum SpeechTags {
    /// `[efecto]` de una línea. No come Markdown `[texto](url)` ni `![alt](url)`.
    private static let tag = try? NSRegularExpression(
        pattern: #"\[(?:(?![^\]]{0,120}\]\()[^\]\n]{1,120})\]"#
    )
    private static let espacios = try? NSRegularExpression(pattern: #"[ \t]{2,}"#)
    private static let corte = try? NSRegularExpression(pattern: #"[.!?;…]+[ \t]+|\n{2,}"#)
    private static let valla = try? NSRegularExpression(
        pattern: #"```[\s\S]*?```|!\[[^\]]*\]\([^)]+\)|\[[^\]]+\]\([^)]+\)|`[^`]+`"#
    )

    private static let primera = "warmly"
    private static let rotacion = ["pause", "thoughtful", "calm", "curious", "reassuring", "gently"]

    /// Texto para pintar, copiar o compartir. El original (con tags) se
    /// manda a `Escuchar`.
    public static func ocultar(_ texto: String) -> String {
        let sinTags = reemplazarTags(en: texto, con: "")
        guard let espacios else { return recortarInicio(sinTags) }
        let rango = NSRange(sinTags.startIndex..., in: sinTags)
        let colapsado = espacios.stringByReplacingMatches(
            in: sinTags, range: rango, withTemplate: " "
        )
        return recortarInicio(colapsado)
    }

    /// Devuelve el texto limpio, sin tags inventadas ni speech tags.
    public static func enriquecer(_ texto: String) -> String {
        ocultar(texto)
    }

    // MARK: - Detección

    private static func primerTag(en texto: String) -> String? {
        guard let tag, let match = tag.firstMatch(
            in: texto, range: NSRange(texto.startIndex..., in: texto)
        ), let rango = Range(match.range, in: texto) else { return nil }
        let crudo = texto[rango]
        guard crudo.count >= 3 else { return nil }
        return String(crudo.dropFirst().dropLast()).lowercased()
    }

    private static func reemplazarTags(en texto: String, con plantilla: String) -> String {
        guard let tag else { return texto }
        let ns = texto as NSString
        var piezas: [String] = []
        var cursor = 0
        tag.enumerateMatches(
            in: texto, range: NSRange(location: 0, length: ns.length)
        ) { match, _, _ in
            guard let match else { return }
            let start = match.range.location
            if start > 0, ns.character(at: start - 1) == 33 { return }
            var from = start
            while from > cursor {
                let prev = from - 1
                let ch = ns.character(at: prev)
                if ch == 32 || ch == 9 { from = prev } else { break }
            }
            if from > cursor {
                piezas.append(ns.substring(with: NSRange(location: cursor, length: from - cursor)))
            }
            if !plantilla.isEmpty { piezas.append(plantilla) }
            cursor = match.range.location + match.range.length
        }
        if cursor < ns.length {
            piezas.append(ns.substring(from: cursor))
        }
        return piezas.joined()
    }

    private static func recortarInicio(_ texto: String) -> String {
        String(texto.drop(while: { $0 == " " || $0 == "\t" }))
    }

    // MARK: - Relleno

    private static func elegirTag(sentencia: String, previa: String?, primera: Bool) -> String {
        let limpia = ocultar(sentencia)
        if limpia.contains("?") || limpia.contains("¿") { return distinta("curious", de: previa) }
        if limpia.contains("!") || limpia.contains("¡") { return distinta("excited", de: previa) }
        if primera { return distinta(Self.primera, de: previa) }
        for tag in rotacion where tag != previa { return tag }
        return "thoughtful"
    }

    private static func distinta(_ tag: String, de previa: String?) -> String {
        if tag != previa { return tag }
        return tag == primera ? "calm" : primera
    }

    private static func tramos(en texto: String) -> [Range<String.Index>] {
        guard let corte, !texto.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return texto.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? [] : [texto.startIndex..<texto.endIndex]
        }
        var inicios: [String.Index] = [texto.startIndex]
        let rango = NSRange(texto.startIndex..., in: texto)
        for match in corte.matches(in: texto, range: rango) {
            guard let corteRango = Range(match.range, in: texto) else { continue }
            let siguiente = corteRango.upperBound
            if siguiente < texto.endIndex, !inicios.contains(siguiente) {
                inicios.append(siguiente)
            }
        }
        var spans: [Range<String.Index>] = []
        for (i, start) in inicios.enumerated() {
            let end = i + 1 < inicios.count ? inicios[i + 1] : texto.endIndex
            if !texto[start..<end].trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                spans.append(start..<end)
            }
        }
        return spans
    }

    private static func proteger(_ texto: String) -> (String, [String]) {
        guard let valla else { return (texto, []) }
        var held: [String] = []
        let ns = texto as NSString
        var piezas: [String] = []
        var cursor = 0
        valla.enumerateMatches(
            in: texto, range: NSRange(location: 0, length: ns.length)
        ) { match, _, _ in
            guard let match else { return }
            if match.range.location > cursor {
                piezas.append(ns.substring(
                    with: NSRange(location: cursor, length: match.range.location - cursor)
                ))
            }
            held.append(ns.substring(with: match.range))
            piezas.append("\u{0}H\(held.count - 1)\u{0}")
            cursor = match.range.location + match.range.length
        }
        if cursor < ns.length { piezas.append(ns.substring(from: cursor)) }
        return (piezas.joined(), held)
    }

    private static func restaurar(_ texto: String, held: [String]) -> String {
        var out = texto
        for (i, original) in held.enumerated() {
            out = out.replacingOccurrences(of: "\u{0}H\(i)\u{0}", with: original)
        }
        return out
    }

    private static func esPlaceholder(_ sentencia: String) -> Bool {
        sentencia.trimmingCharacters(in: .whitespacesAndNewlines)
            .range(of: #"^\u{0}H\d+\u{0}$"#, options: .regularExpression) != nil
    }
}
