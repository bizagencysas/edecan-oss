import SwiftUI

/// Resaltado de sintaxis básico para el visor del editor IDE (light mode).
enum IDESyntaxHighlight {
    private static let keywords: Set<String> = [
        "import", "from", "class", "struct", "enum", "protocol", "extension",
        "func", "let", "var", "const", "if", "else", "elif", "for", "while",
        "return", "break", "continue", "switch", "case", "default", "try",
        "catch", "throw", "async", "await", "public", "private", "internal",
        "static", "final", "override", "init", "deinit", "self", "super",
        "true", "false", "nil", "null", "undefined", "type", "interface",
        "export", "new", "def", "pass", "raise", "with", "as", "in", "not",
        "and", "or", "fn", "mut", "impl", "trait", "pub", "use", "package",
        "go", "defer", "select", "chan"
    ]

    static func resaltar(_ linea: String) -> AttributedString {
        var attr = AttributedString(linea)
        attr.foregroundColor = IDETheme.texto
        attr.font = .system(.footnote, design: .monospaced)

        let trimmed = linea.trimmingCharacters(in: .whitespaces)
        if trimmed.hasPrefix("//") || trimmed.hasPrefix("#") {
            attr.foregroundColor = IDETheme.textoSuave
            return attr
        }

        colorearStrings(en: &attr, linea: linea)
        colorearKeywords(en: &attr, linea: linea)

        if trimmed.hasPrefix("@") {
            attr.foregroundColor = EdecanTheme.azul
        }

        return attr
    }

    private static func colorearStrings(en attr: inout AttributedString, linea: String) {
        var i = linea.startIndex
        while i < linea.endIndex {
            let c = linea[i]
            guard c == "\"" || c == "'" else {
                i = linea.index(after: i)
                continue
            }
            let quote = c
            var j = linea.index(after: i)
            while j < linea.endIndex {
                if linea[j] == quote {
                    j = linea.index(after: j)
                    break
                }
                j = linea.index(after: j)
            }
            let fragmento = String(linea[i..<j])
            if let rango = attr.range(of: fragmento) {
                attr[rango].foregroundColor = Color(red: 0.05, green: 0.55, blue: 0.35)
            }
            i = j
        }
    }

    private static func colorearKeywords(en attr: inout AttributedString, linea: String) {
        var token = ""
        var inicio: String.Index?
        var enString = false
        var quote: Character?

        var i = linea.startIndex
        while i <= linea.endIndex {
            let c: Character? = i < linea.endIndex ? linea[i] : nil

            if enString {
                if let c, c == quote {
                    enString = false
                }
            } else if let c, c == "\"" || c == "'" {
                enString = true
                quote = c
                if !token.isEmpty, let ini = inicio {
                    aplicarKeyword(&attr, token: token, en: linea, desde: ini, hasta: i)
                    token = ""
                    inicio = nil
                }
            } else if let c, (c.isLetter || c == "_") {
                if inicio == nil { inicio = i }
                token.append(c)
            } else {
                if !token.isEmpty, let ini = inicio {
                    aplicarKeyword(&attr, token: token, en: linea, desde: ini, hasta: i)
                    token = ""
                    inicio = nil
                }
            }
            if i < linea.endIndex {
                i = linea.index(after: i)
            } else {
                break
            }
        }
    }

    private static func aplicarKeyword(
        _ attr: inout AttributedString,
        token: String,
        en linea: String,
        desde: String.Index,
        hasta: String.Index
    ) {
        guard keywords.contains(token.lowercased()) else { return }
        let fragmento = String(linea[desde..<hasta])
        if let rango = attr.range(of: fragmento) {
            attr[rango].foregroundColor = EdecanTheme.morado
            attr[rango].font = .system(.footnote, design: .monospaced).bold()
        }
    }
}
