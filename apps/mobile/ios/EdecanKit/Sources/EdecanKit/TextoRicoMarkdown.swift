import Foundation

/// Parseo del Markdown rico que responde el agente del IDE: prosa y TABLAS.
///
/// Vive en `EdecanKit` y no en la vista por el mismo motivo que
/// ``ConfirmacionFormato``: es formato de datos puro, testeable sin SwiftUI,
/// aunque su único consumidor sea una pantalla (``TextoRicoIDE`` en
/// `EdecanApp`).
///
/// Por qué existe: la respuesta del agente llega en Markdown y la vista del IDE
/// la pintaba como texto plano, así que una tabla se veía como barras y
/// guiones — el peor resultado posible, porque el dato PARECE decir otra cosa.
///
/// **Estas reglas están duplicadas en `apps/web/src/components/ide/rich-text.ts`.**
/// Es duplicación a propósito (el iPhone no importa TypeScript) pero es un
/// contrato: cambiar una regla de un solo lado hace que el mismo texto diga
/// cosas distintas en el navegador y en el teléfono, que es justo el bug que
/// nadie reporta. Los tests de las dos superficies cubren los mismos casos.

public enum AlineacionColumna: Equatable, Sendable {
    case izquierda
    case centro
    case derecha
}

public struct TablaRica: Equatable, Sendable {
    public let encabezados: [String]
    public let alineaciones: [AlineacionColumna]
    /// Filas ya acotadas a ``TextoRicoParser/maxFilas``.
    public let filas: [[String]]
    /// Filas que traía el texto: si supera a `filas.count`, la vista lo avisa.
    public let filasTotales: Int
}

public struct SerieTabla: Identifiable, Equatable, Sendable {
    /// Índice de la columna en la tabla: único aunque dos columnas se llamen igual.
    public let id: Int
    public let nombre: String
    public let valores: [Double]
}

public struct GraficaTabla: Equatable, Sendable {
    public let etiquetas: [String]
    public let series: [SerieTabla]
    /// Columnas numéricas que no caben en la gráfica; la vista DEBE decirlo.
    public let seriesOmitidas: Int
}

public enum SegmentoRico: Identifiable, Sendable {
    case texto(indice: Int, contenido: String)
    case tabla(indice: Int, tabla: TablaRica)

    public var id: Int {
        switch self {
        case .texto(let indice, _): indice
        case .tabla(let indice, _): indice
        }
    }
}

public enum TextoRicoParser {
    public static let maxFilas = 200
    /// Mismo tope que `MAX_CHART_SERIES` en `rich-text.ts` y en
    /// `edecan_schemas.ide_blocks`. Tiene que ser el mismo número en las tres
    /// partes: con 4 acá, una tabla de 5 columnas numéricas dibujaba 5 series en
    /// el navegador y 4 en el teléfono, y solo el teléfono avisaba de la que
    /// faltaba. La paleta (``PaletaGrafica``) también tiene 6 colores.
    public static let maxSeries = 6

    public static func segmentar(_ texto: String) -> [SegmentoRico] {
        let lineas = texto
            .components(separatedBy: "\n")
            .map { $0.hasSuffix("\r") ? String($0.dropLast()) : $0 }
        var segmentos: [SegmentoRico] = []
        var prosa: [String] = []
        var valla: Character?
        var indice = 0

        func volcarProsa() {
            guard !prosa.isEmpty else { return }
            let unido = prosa.joined(separator: "\n")
            if !unido.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                segmentos.append(.texto(indice: segmentos.count, contenido: unido))
            }
            prosa = []
        }

        while indice < lineas.count {
            let linea = lineas[indice]

            // Una tabla dentro de un bloque de código es código, no una tabla.
            if let marca = marcaDeValla(linea) {
                if valla == nil {
                    valla = marca
                } else if valla == marca {
                    valla = nil
                }
                prosa.append(linea)
                indice += 1
                continue
            }
            if valla != nil {
                prosa.append(linea)
                indice += 1
                continue
            }

            if linea.contains("|"), indice + 1 < lineas.count {
                let encabezados = celdas(linea)
                // Con una sola columna, `a | b` dentro de una frase sería tabla.
                let alineados = encabezados.count >= 2
                    ? alineaciones(lineas[indice + 1], columnas: encabezados.count)
                    : nil
                if let alineados {
                    var filas: [[String]] = []
                    var cursor = indice + 2
                    while cursor < lineas.count {
                        let filaLinea = lineas[cursor]
                        if filaLinea.trimmingCharacters(in: .whitespaces).isEmpty { break }
                        if !filaLinea.contains("|") { break }
                        if marcaDeValla(filaLinea) != nil { break }
                        let celdasFila = celdas(filaLinea)
                        // GFM: las celdas de más se descartan, las que faltan quedan vacías.
                        filas.append(
                            (0..<encabezados.count).map {
                                celdaPlana($0 < celdasFila.count ? celdasFila[$0] : "")
                            }
                        )
                        cursor += 1
                    }
                    volcarProsa()
                    segmentos.append(
                        .tabla(
                            indice: segmentos.count,
                            tabla: TablaRica(
                                encabezados: encabezados.map(celdaPlana),
                                alineaciones: alineados,
                                filas: Array(filas.prefix(maxFilas)),
                                filasTotales: filas.count
                            )
                        )
                    )
                    indice = cursor
                    continue
                }
            }

            prosa.append(linea)
            indice += 1
        }

        volcarProsa()
        return segmentos
    }

    /// Parte una fila por `|` respetando `\|` y las barras de los extremos.
    public static func celdas(_ linea: String) -> [String] {
        var work = Substring(linea.trimmingCharacters(in: .whitespaces))
        if work.hasPrefix("|") { work = work.dropFirst() }
        if work.hasSuffix("|"), !work.hasSuffix("\\|") { work = work.dropLast() }

        var resultado: [String] = []
        var actual = ""
        var escapando = false
        for char in work {
            if escapando {
                if char == "|" {
                    actual.append("|")
                } else {
                    actual.append("\\")
                    actual.append(char)
                }
                escapando = false
                continue
            }
            if char == "\\" {
                escapando = true
                continue
            }
            if char == "|" {
                resultado.append(actual.trimmingCharacters(in: .whitespaces))
                actual = ""
                continue
            }
            actual.append(char)
        }
        if escapando { actual.append("\\") }
        resultado.append(actual.trimmingCharacters(in: .whitespaces))
        return resultado
    }

    /// La línea `| --- | ---: |` que confirma que la anterior era un encabezado.
    public static func alineaciones(_ linea: String, columnas: Int) -> [AlineacionColumna]? {
        guard linea.contains("-"), linea.contains("|") else { return nil }
        let partes = celdas(linea)
        guard partes.count == columnas else { return nil }
        var resultado: [AlineacionColumna] = []
        for parte in partes {
            let limpia = parte.filter { !$0.isWhitespace }
            guard esSeparador(limpia) else { return nil }
            let izquierda = limpia.hasPrefix(":")
            let derecha = limpia.hasSuffix(":")
            resultado.append(izquierda && derecha ? .centro : derecha ? .derecha : .izquierda)
        }
        return resultado
    }

    /// Deja la celda en texto plano: la web hace lo mismo, para que `**Total**`
    /// se lea igual en las dos superficies sin meter un render inline por celda.
    public static func celdaPlana(_ raw: String) -> String {
        var valor = raw.replacingOccurrences(
            of: "\\[([^\\]]*)\\]\\([^)]*\\)",
            with: "$1",
            options: .regularExpression
        )
        valor = valor.replacingOccurrences(
            of: "(\\*\\*|__|~~)(.*?)\\1",
            with: "$2",
            options: .regularExpression
        )
        valor = valor.replacingOccurrences(of: "`([^`]*)`", with: "$1", options: .regularExpression)
        return valor.trimmingCharacters(in: .whitespaces)
    }

    /// Lee los formatos que el modelo mezcla: `1.234,56`, `1,234.56`, `$ 1 200`,
    /// `45%`, `(320)` de contabilidad. Devuelve `nil` en cuanto la celda no sea
    /// SOLO un número: "12 días" no es una serie y forzarla mentiría sobre el dato.
    public static func numero(_ raw: String) -> Double? {
        let base = celdaPlana(raw)
        guard !base.isEmpty else { return nil }
        var limpio = base.filter { !$0.isWhitespace }
        limpio = limpio.replacingOccurrences(
            of: "[$€£¥%]",
            with: "",
            options: .regularExpression
        )
        if limpio.hasPrefix("+") { limpio.removeFirst() }
        if limpio.hasPrefix("("), limpio.hasSuffix(")"), limpio.count > 2 {
            limpio = "-" + limpio.dropFirst().dropLast()
        }
        guard coincide(limpio, "^-?[0-9.,]+$") else { return nil }

        let tienePunto = limpio.contains(".")
        let tieneComa = limpio.contains(",")
        if tienePunto, tieneComa {
            // El separador que aparece más a la derecha es el decimal.
            let ultimoPunto = limpio.lastIndex(of: ".")
            let ultimaComa = limpio.lastIndex(of: ",")
            let decimalEsPunto = (ultimoPunto ?? limpio.startIndex) > (ultimaComa ?? limpio.startIndex)
            let miles: Character = decimalEsPunto ? "," : "."
            limpio = limpio.filter { $0 != miles }
            if !decimalEsPunto {
                limpio = limpio.replacingOccurrences(of: ",", with: ".")
            }
        } else if tieneComa {
            if coincide(limpio, "^-?[0-9]{1,3}(,[0-9]{3})+$") {
                limpio = limpio.filter { $0 != "," }
            } else {
                limpio = limpio.replacingOccurrences(of: ",", with: ".")
            }
        } else if tienePunto, coincide(limpio, "^-?[0-9]{1,3}(\\.[0-9]{3})+$") {
            limpio = limpio.filter { $0 != "." }
        }

        guard coincide(limpio, "^-?[0-9]+(\\.[0-9]+)?$") else { return nil }
        return Double(limpio)
    }

    /// Series graficables: primera columna = etiquetas, y cada columna siguiente
    /// cuyas celdas sean TODAS números es una serie.
    public static func grafica(de tabla: TablaRica) -> GraficaTabla? {
        guard tabla.encabezados.count >= 2, tabla.filas.count >= 2 else { return nil }

        // Dos filas con la misma etiqueta se fundirían en una sola barra (la
        // categoría es la clave del eje). Numerarlas es feo pero visible;
        // fundirlas sería un dato distinto al de la tabla sin que nadie lo note.
        var vistas: [String: Int] = [:]
        let etiquetas = tabla.filas.enumerated().map { indice, fila -> String in
            let bruto = (fila.first ?? "").trimmingCharacters(in: .whitespaces)
            let base = bruto.isEmpty ? "Fila \(indice + 1)" : bruto
            let repeticiones = (vistas[base] ?? 0) + 1
            vistas[base] = repeticiones
            return repeticiones == 1 ? base : "\(base) (\(repeticiones))"
        }

        var series: [SerieTabla] = []
        for columna in 1..<tabla.encabezados.count {
            var valores: [Double] = []
            var completa = true
            for fila in tabla.filas {
                guard columna < fila.count, let valor = numero(fila[columna]) else {
                    completa = false
                    break
                }
                valores.append(valor)
            }
            guard completa else { continue }
            let nombre = tabla.encabezados[columna].trimmingCharacters(in: .whitespaces)
            series.append(
                SerieTabla(
                    id: columna,
                    nombre: nombre.isEmpty ? "Columna \(columna + 1)" : nombre,
                    valores: valores
                )
            )
        }
        guard !series.isEmpty else { return nil }
        return GraficaTabla(
            etiquetas: etiquetas,
            series: Array(series.prefix(maxSeries)),
            seriesOmitidas: max(0, series.count - maxSeries)
        )
    }

    private static func esSeparador(_ valor: String) -> Bool {
        var cuerpo = Substring(valor)
        if cuerpo.hasPrefix(":") { cuerpo = cuerpo.dropFirst() }
        if cuerpo.hasSuffix(":") { cuerpo = cuerpo.dropLast() }
        return !cuerpo.isEmpty && cuerpo.allSatisfy { $0 == "-" }
    }

    private static func marcaDeValla(_ linea: String) -> Character? {
        let cuerpo = linea.drop { $0 == " " || $0 == "\t" }
        if cuerpo.hasPrefix("```") { return "`" }
        if cuerpo.hasPrefix("~~~") { return "~" }
        return nil
    }

    private static func coincide(_ valor: String, _ patron: String) -> Bool {
        valor.range(of: patron, options: .regularExpression) != nil
    }
}
