import Foundation

/// Bloques ricos del IDE (`edecan_schemas.ide_blocks`): tabla y gráfica.
///
/// El agente los acuña con `mostrar_tabla`/`mostrar_grafica` y viajan en el
/// canal `presentation` de un evento de sesión — nunca en el texto, y nunca
/// desde el resultado que una herramienta le devuelve al modelo. El servidor ya
/// los valida antes de mandarlos (`_sanitize_agent_events` en
/// `edecan_api.routers.ide`); acá se validan otra vez por la misma razón por la
/// que existe la validación de ``ChatBlock``: un bloque malformado no puede
/// tumbar el turno, y lo peor que puede pasar es que se lea el `text` del
/// evento en vez de la tarjeta.
///
/// Un bloque de un tipo que este cliente no conoce se DESCARTA, igual que en la
/// web (`apps/web/src/components/ide/ide-blocks.ts`). El evento conserva su
/// texto equivalente, así que lo que se pierde es la tarjeta, no el mensaje.

/// Topes espejo de `edecan_schemas.ide_blocks`.
public enum IDEBlockLimites {
    public static let maxColumnas = 8
    public static let maxFilas = 30
    public static let maxCeldaChars = 120
    public static let maxSeries = 6
    public static let maxPuntos = 60
    public static let maxBloquesPorEvento = 3

    /// Recorta a ``maxCeldaChars`` DEJANDO la marca del recorte.
    ///
    /// Espejo de `recortar_celda` en `edecan_schemas/ide_blocks.py`. Sin el
    /// "…", una ruta o una URL cortada se lee como el valor completo: la celda
    /// no revienta, se ve perfecta y dice otra cosa. El emisor ya recorta así,
    /// con lo que esto solo actúa ante un companion de otra versión.
    public static func recortarCelda(_ valor: String) -> String {
        guard valor.count > maxCeldaChars else { return valor }
        return String(valor.prefix(maxCeldaChars - 1)) + "…"
    }
}

public enum IDEChartKind: String, Decodable, Sendable, Equatable {
    case bar
    case line
}

/// Envoltorios que nunca fallan al decodificar un elemento de una lista.
///
/// Sin esto, UNA columna o UN punto malformado tumbaba la tabla o la gráfica
/// completa, mientras que la web descartaba solo el elemento malo y seguía. Dos
/// clientes que reaccionan distinto al mismo JSON son la clase de diferencia
/// que nadie reporta hasta que alguien compara las dos pantallas.
private struct ColumnaTolerante: Decodable {
    let columna: IDETableColumn?

    init(from decoder: Decoder) throws {
        columna = try? IDETableColumn(from: decoder)
    }
}

private struct PuntoTolerante: Decodable {
    let punto: IDEChartPoint?

    init(from decoder: Decoder) throws {
        punto = try? IDEChartPoint(from: decoder)
    }
}

private struct SerieTolerante: Decodable {
    let serie: IDEChartSeries?

    init(from decoder: Decoder) throws {
        serie = try? IDEChartSeries(from: decoder)
    }
}

public struct IDETableColumn: Decodable, Sendable, Equatable {
    public let key: String
    public let title: String
    public let align: AlineacionColumna

    enum CodingKeys: String, CodingKey {
        case key, title, align
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        key = try container.decode(String.self, forKey: .key)
        title = try container.decode(String.self, forKey: .title)
        switch try container.decodeIfPresent(String.self, forKey: .align) {
        case "right": align = .derecha
        case "center": align = .centro
        default: align = .izquierda
        }
        guard !key.isEmpty, !title.isEmpty else {
            throw DecodingError.dataCorruptedError(
                forKey: .key,
                in: container,
                debugDescription: "una columna sin clave o sin título no se puede dibujar"
            )
        }
    }
}

public struct IDETableBlock: Decodable, Sendable, Equatable {
    public let fallbackText: String
    public let title: String?
    public let columns: [IDETableColumn]
    /// Celdas por CLAVE de columna. Una clave ausente es "sin dato", no un cero.
    public let rows: [[String: String]]
    public let note: String?

    enum CodingKeys: String, CodingKey {
        case title, columns, rows, note
        case fallbackText = "fallback_text"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        fallbackText = try container.decode(String.self, forKey: .fallbackText)
        title = try container.decodeIfPresent(String.self, forKey: .title)
        note = try container.decodeIfPresent(String.self, forKey: .note)

        var columnasValidas: [IDETableColumn] = []
        var vistas: Set<String> = []
        // Dos columnas con la misma clave harían que una tape a la otra al leer la fila.
        let columnasCrudas = try container.decode([ColumnaTolerante].self, forKey: .columns)
        for columna in columnasCrudas.compactMap(\.columna) where !vistas.contains(columna.key) {
            vistas.insert(columna.key)
            columnasValidas.append(columna)
            if columnasValidas.count == IDEBlockLimites.maxColumnas { break }
        }
        columns = columnasValidas

        let crudas = try container.decode([[String: String]].self, forKey: .rows)
        rows = crudas.prefix(IDEBlockLimites.maxFilas).map { fila in
            var limpia: [String: String] = [:]
            for columna in columnasValidas {
                guard let valor = fila[columna.key], !valor.isEmpty else { continue }
                limpia[columna.key] = IDEBlockLimites.recortarCelda(valor)
            }
            return limpia
        }

        guard !fallbackText.isEmpty, !columns.isEmpty, rows.contains(where: { !$0.isEmpty }) else {
            throw DecodingError.dataCorruptedError(
                forKey: .rows,
                in: container,
                debugDescription: "una tabla sin ni una celda con datos no dice nada que el texto no diga"
            )
        }
    }

    /// Proyección al mismo modelo que produce el Markdown, para que UNA sola
    /// vista dibuje las dos. Las celdas se ordenan por columna: la que falta
    /// queda vacía en SU columna (la vista pinta "—"). Con filas posicionales,
    /// una celda faltante correría las demás y cada valor quedaría bajo la
    /// columna equivocada: un render que no revienta pero miente.
    public var tabla: TablaRica {
        TablaRica(
            encabezados: columns.map(\.title),
            alineaciones: columns.map(\.align),
            filas: rows.map { fila in columns.map { fila[$0.key] ?? "" } },
            filasTotales: rows.count
        )
    }
}

public struct IDEChartPoint: Decodable, Sendable, Equatable {
    public let label: String
    public let value: Double

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        label = try container.decode(String.self, forKey: .label)
        value = try container.decode(Double.self, forKey: .value)
        // NaN/infinito revientan el cálculo del dominio del eje: no son un dato
        // que se pueda dibujar.
        guard !label.isEmpty, value.isFinite else {
            throw DecodingError.dataCorruptedError(
                forKey: .value,
                in: container,
                debugDescription: "un punto necesita etiqueta y un número finito"
            )
        }
    }

    enum CodingKeys: String, CodingKey {
        case label, value
    }
}

public struct IDEChartSeries: Decodable, Sendable, Equatable {
    public let name: String
    public let points: [IDEChartPoint]

    enum CodingKeys: String, CodingKey {
        case name, points
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        name = try container.decode(String.self, forKey: .name)

        var validos: [IDEChartPoint] = []
        var etiquetas: Set<String> = []
        // Dos puntos con la misma etiqueta son indistinguibles al mirarlos.
        let puntosCrudos = try container.decode([PuntoTolerante].self, forKey: .points)
        for punto in puntosCrudos.compactMap(\.punto) where !etiquetas.contains(punto.label) {
            etiquetas.insert(punto.label)
            validos.append(punto)
            if validos.count == IDEBlockLimites.maxPuntos { break }
        }
        points = validos

        guard !name.isEmpty, points.count >= 2 else {
            throw DecodingError.dataCorruptedError(
                forKey: .points,
                in: container,
                debugDescription: "una serie con menos de dos puntos no es una serie"
            )
        }
    }
}

public struct IDEChartBlock: Decodable, Sendable, Equatable {
    public let fallbackText: String
    public let chartKind: IDEChartKind
    public let title: String
    public let series: [IDEChartSeries]
    public let xLabel: String?
    public let yLabel: String?
    public let note: String?

    enum CodingKeys: String, CodingKey {
        case title, series, note
        case fallbackText = "fallback_text"
        case chartKind = "chart_kind"
        case xLabel = "x_label"
        case yLabel = "y_label"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        fallbackText = try container.decode(String.self, forKey: .fallbackText)
        title = try container.decode(String.self, forKey: .title)
        // Un tipo desconocido cae a barras en vez de tirar la gráfica entera.
        chartKind = (try? container.decode(IDEChartKind.self, forKey: .chartKind)) ?? .bar
        xLabel = try container.decodeIfPresent(String.self, forKey: .xLabel)
        yLabel = try container.decodeIfPresent(String.self, forKey: .yLabel)
        note = try container.decodeIfPresent(String.self, forKey: .note)

        var validas: [IDEChartSeries] = []
        var nombres: Set<String> = []
        let seriesCrudas = try container.decode([SerieTolerante].self, forKey: .series)
        for serie in seriesCrudas.compactMap(\.serie) where !nombres.contains(serie.name) {
            nombres.insert(serie.name)
            validas.append(serie)
            if validas.count == IDEBlockLimites.maxSeries { break }
        }
        series = validas

        guard !fallbackText.isEmpty, !title.isEmpty, !series.isEmpty else {
            throw DecodingError.dataCorruptedError(
                forKey: .series,
                in: container,
                debugDescription: "una gráfica sin título o sin series no se puede dibujar"
            )
        }
    }

    /// Proyección al mismo modelo que la tabla en Markdown. Los valores se
    /// buscan POR ETIQUETA y no por posición: una serie a la que le falte un
    /// punto se descarta entera en vez de dibujarse corrida (o rellenada con
    /// ceros, que serían datos inventados).
    public var grafica: GraficaTabla? {
        guard let primera = series.first else { return nil }
        let etiquetas = primera.points.map(\.label)
        var completas: [SerieTabla] = []
        for (indice, serie) in series.enumerated() {
            let porEtiqueta = Dictionary(
                serie.points.map { ($0.label, $0.value) },
                uniquingKeysWith: { primero, _ in primero }
            )
            let valores = etiquetas.compactMap { porEtiqueta[$0] }
            guard valores.count == etiquetas.count else { continue }
            completas.append(SerieTabla(id: indice, nombre: serie.name, valores: valores))
        }
        guard !completas.isEmpty else { return nil }
        return GraficaTabla(
            etiquetas: etiquetas,
            series: completas,
            seriesOmitidas: series.count - completas.count
        )
    }
}

/// Bloque tipado del IDE. Un tipo que este cliente no conoce no se decodifica:
/// queda el `text` del evento, que siempre trae el equivalente en texto.
public enum IDEBlock: Decodable, Sendable, Equatable {
    case table(IDETableBlock)
    case chart(IDEChartBlock)

    private enum CodingKeys: String, CodingKey {
        case type
        case schemaVersion = "schema_version"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let tipo = try container.decode(String.self, forKey: .type)
        let version = try container.decodeIfPresent(Int.self, forKey: .schemaVersion) ?? 1
        // Una versión futura no se interpreta como v1 por accidente.
        guard version == 1 else {
            throw DecodingError.dataCorruptedError(
                forKey: .schemaVersion,
                in: container,
                debugDescription: "versión de bloque no soportada: \(version)"
            )
        }
        switch tipo {
        case "table":
            self = .table(try IDETableBlock(from: decoder))
        case "chart":
            self = .chart(try IDEChartBlock(from: decoder))
        default:
            throw DecodingError.dataCorruptedError(
                forKey: .type,
                in: container,
                debugDescription: "bloque de IDE desconocido: \(tipo)"
            )
        }
    }

}

/// Envoltorio que nunca falla al decodificar.
///
/// Un `try?` sobre el arreglo completo perdería también los bloques buenos que
/// venían al lado del malo; con esto, el inválido queda en `nil` y los demás se
/// dibujan. Mismo criterio que `ide_blocks_from_presentation` en el servidor.
public struct IDEBlockTolerante: Decodable, Sendable, Equatable {
    public let bloque: IDEBlock?

    public init(from decoder: Decoder) throws {
        bloque = try? IDEBlock(from: decoder)
    }
}
