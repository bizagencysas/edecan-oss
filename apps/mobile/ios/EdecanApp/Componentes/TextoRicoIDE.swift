import Charts
import EdecanKit
import SwiftUI

/// Tablas y gráficas del agente del IDE, dentro del teléfono.
///
/// El parseo vive en `EdecanKit` (``TextoRicoParser``, archivo
/// `TextoRicoMarkdown.swift`), que es donde está testeado. Acá solo está el
/// dibujo, con dos decisiones que no son obvias y no hay que deshacer sin leer
/// esto:
///
/// - **No se usa `Table` de SwiftUI.** En ancho compacto (todo iPhone en
///   vertical) `Table` esconde los encabezados y descarta todo menos la primera
///   columna: la tabla terminaría diciendo otra cosa. Se arma con `Grid` dentro
///   de un `ScrollView` horizontal, que sí muestra las columnas completas.
/// - **Nada se recorta en silencio.** Si la tabla trae más filas de las que se
///   pintan, o la gráfica deja series numéricas fuera, se dice con todas sus
///   letras.
///
/// La web pinta lo mismo con las mismas reglas
/// (`apps/web/src/components/ide/AgentRichText.tsx`): dos renders del mismo
/// texto que digan cosas distintas son un bug que nadie reporta.

/// Paleta compartida con la web (`CHART_COLORS` en `rich-text.ts`): la misma
/// serie tiene que salir del mismo color en el navegador y en el teléfono.
enum PaletaGrafica {
    static let colores: [Color] = [
        Color(red: 0.31, green: 0.27, blue: 0.90),
        Color(red: 0.05, green: 0.65, blue: 0.91),
        Color(red: 0.06, green: 0.73, blue: 0.51),
        Color(red: 0.96, green: 0.62, blue: 0.07),
        Color(red: 0.93, green: 0.28, blue: 0.60),
        Color(red: 0.08, green: 0.72, blue: 0.65),
    ]
}

struct TextoRicoIDE: View {
    private let segmentos: [SegmentoRico]

    init(texto: String) {
        self.segmentos = TextoRicoParser.segmentar(texto)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(segmentos) { segmento in
                switch segmento {
                case .texto(_, let contenido):
                    Text(textoMarkdown(contenido))
                        .font(.subheadline)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                case .tabla(_, let tabla):
                    TablaRicaView(tabla: tabla)
                }
            }
        }
    }
}

private enum VistaDeTabla: String, CaseIterable {
    case tabla
    case grafica

    var etiqueta: String {
        switch self {
        case .tabla: "Tabla"
        case .grafica: "Gráfica"
        }
    }
}

struct TablaRicaView: View {
    let tabla: TablaRica
    /// Título y nota solo los trae la tabla TIPADA (``IDETableBlock``); una
    /// tabla en Markdown no tiene dónde declararlos.
    let titulo: String?
    let nota: String?
    private let grafica: GraficaTabla?
    @State private var vista: VistaDeTabla = .tabla

    init(tabla: TablaRica, titulo: String? = nil, nota: String? = nil) {
        self.tabla = tabla
        self.titulo = titulo
        self.nota = nota
        self.grafica = TextoRicoParser.grafica(de: tabla)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            if let titulo, !titulo.isEmpty {
                Text(titulo)
                    .font(.subheadline.bold())
                    .frame(maxWidth: .infinity, alignment: .leading)
            }

            if let grafica {
                encabezado(grafica)
            }

            if vista == .grafica, let grafica {
                GraficaTablaView(grafica: grafica)
                if grafica.seriesOmitidas > 0 {
                    aviso(
                        "La gráfica muestra \(grafica.series.count) de "
                            + "\(grafica.series.count + grafica.seriesOmitidas) columnas numéricas. "
                            + "Cambia a Tabla para verlas todas.",
                        destacado: true
                    )
                }
            } else {
                rejilla
                if tabla.encabezados.count > 3 {
                    aviso("Desliza para ver las \(tabla.encabezados.count) columnas.")
                }
            }

            if tabla.filasTotales > tabla.filas.count {
                aviso(
                    "Se muestran las primeras \(tabla.filas.count) filas de \(tabla.filasTotales).",
                    destacado: true
                )
            }

            if let nota, !nota.isEmpty {
                aviso(nota)
            }
        }
        .padding(11)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 14))
        .overlay(
            RoundedRectangle(cornerRadius: 14)
                .strokeBorder(Color.primary.opacity(0.07))
        )
    }

    private func encabezado(_ grafica: GraficaTabla) -> some View {
        HStack(spacing: 8) {
            Text("\(tabla.filas.count) \(tabla.filas.count == 1 ? "fila" : "filas") · \(tabla.encabezados.count) columnas")
                .font(.caption2)
                .foregroundStyle(.tertiary)
            Spacer(minLength: 6)
            Picker("Cómo ver la tabla", selection: $vista) {
                ForEach(VistaDeTabla.allCases, id: \.self) { opcion in
                    Text(opcion.etiqueta).tag(opcion)
                }
            }
            .pickerStyle(.segmented)
            .frame(width: 152)
            .controlSize(.small)
        }
    }

    /// `Grid` y no `Table`: en un iPhone `Table` esconde los encabezados y se
    /// queda con una sola columna. Acá se ven todas y se desplazan.
    private var rejilla: some View {
        ScrollView(.horizontal, showsIndicators: true) {
            Grid(alignment: .topLeading, horizontalSpacing: 0, verticalSpacing: 0) {
                GridRow {
                    ForEach(Array(tabla.encabezados.enumerated()), id: \.offset) { indice, encabezado in
                        celda(encabezado, columna: indice, esEncabezado: true, fila: 0)
                    }
                }
                Divider()
                ForEach(Array(tabla.filas.enumerated()), id: \.offset) { indiceFila, fila in
                    GridRow {
                        ForEach(Array(fila.enumerated()), id: \.offset) { indice, valor in
                            celda(valor, columna: indice, esEncabezado: false, fila: indiceFila + 1)
                        }
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func celda(_ valor: String, columna: Int, esEncabezado: Bool, fila: Int) -> some View {
        Text(valor.isEmpty ? "—" : valor)
            .font(esEncabezado ? .caption.bold() : .caption)
            .foregroundStyle(esEncabezado ? AnyShapeStyle(.secondary) : AnyShapeStyle(.primary))
            .multilineTextAlignment(alineacionTexto(columna))
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, 10)
            .padding(.vertical, 7)
            .frame(minWidth: 62, maxWidth: 210, alignment: alineacionCaja(columna))
            .background(fondo(esEncabezado: esEncabezado, fila: fila))
    }

    private func fondo(esEncabezado: Bool, fila: Int) -> Color {
        if esEncabezado { return Color.primary.opacity(0.06) }
        return fila.isMultiple(of: 2) ? Color.primary.opacity(0.025) : .clear
    }

    private func alineacionTexto(_ columna: Int) -> TextAlignment {
        switch alineacion(columna) {
        case .izquierda: .leading
        case .centro: .center
        case .derecha: .trailing
        }
    }

    private func alineacionCaja(_ columna: Int) -> Alignment {
        switch alineacion(columna) {
        case .izquierda: .leading
        case .centro: .center
        case .derecha: .trailing
        }
    }

    private func alineacion(_ columna: Int) -> AlineacionColumna {
        columna < tabla.alineaciones.count ? tabla.alineaciones[columna] : .izquierda
    }

    private func aviso(_ texto: String, destacado: Bool = false) -> some View {
        Text(texto)
            .font(.caption2)
            .foregroundStyle(destacado ? AnyShapeStyle(Color.orange) : AnyShapeStyle(.tertiary))
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// Un valor de la gráfica, ya aplanado. Swift Charts trabaja mejor con una
/// lista plana que con `ForEach` anidados, y así el contenido del `Chart` queda
/// en una sola rama (ver la nota de `@ChartContentBuilder` abajo).
private struct PuntoGrafica: Identifiable {
    let id: String
    let etiqueta: String
    let serie: String
    let valor: Double
}

struct GraficaTablaView: View {
    let grafica: GraficaTabla
    /// Barras o líneas, según lo pidió quien acuñó la gráfica
    /// (``IDEChartBlock/chartKind``). Una gráfica derivada de una tabla en
    /// Markdown es siempre de barras: son categorías, no una serie en el tiempo.
    var tipo: IDEChartKind = .bar

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            Chart {
                marcas(puntos)
            }
            .chartXScale(domain: grafica.etiquetas)
            .chartForegroundStyleScale(
                domain: grafica.series.map(\.nombre),
                range: Array(PaletaGrafica.colores.prefix(max(1, grafica.series.count)))
            )
            .chartYAxis {
                AxisMarks(position: .leading)
            }
            .chartLegend(grafica.series.count > 1 ? .visible : .hidden)
            .frame(width: ancho, height: 210)
            .padding(.top, 4)
        }
    }

    /// El contenido del `Chart` se arma en funciones marcadas
    /// `@ChartContentBuilder` a propósito: el objetivo de despliegue es iOS 26
    /// (`project.yml`), por debajo de 27, donde el chequeo de tipos de un
    /// contenido de gráfica con varias ramas se vuelve lentísimo. Escrito así
    /// desde el principio, no como arreglo posterior.
    @ChartContentBuilder
    private func marcas(_ puntos: [PuntoGrafica]) -> some ChartContent {
        ForEach(puntos) { punto in
            marca(punto)
        }
    }

    /// La rama barras/líneas vive en su propia función marcada
    /// `@ChartContentBuilder` justamente por lo de arriba: un `if` dentro del
    /// cuerpo del `Chart` es lo que dispara el chequeo de tipos lento.
    @ChartContentBuilder
    private func marca(_ punto: PuntoGrafica) -> some ChartContent {
        if tipo == .line {
            linea(punto)
        } else {
            barra(punto)
        }
    }

    @ChartContentBuilder
    private func barra(_ punto: PuntoGrafica) -> some ChartContent {
        BarMark(
            x: .value("Categoría", punto.etiqueta),
            y: .value("Valor", punto.valor)
        )
        .foregroundStyle(by: .value("Serie", punto.serie))
        .position(by: .value("Serie", punto.serie))
        .cornerRadius(3)
    }

    @ChartContentBuilder
    private func linea(_ punto: PuntoGrafica) -> some ChartContent {
        LineMark(
            x: .value("Categoría", punto.etiqueta),
            y: .value("Valor", punto.valor)
        )
        .foregroundStyle(by: .value("Serie", punto.serie))
        .symbol(by: .value("Serie", punto.serie))
        .interpolationMethod(.monotone)
    }

    private var puntos: [PuntoGrafica] {
        grafica.series.flatMap { serie in
            serie.valores.enumerated().compactMap { indice, valor -> PuntoGrafica? in
                guard indice < grafica.etiquetas.count else { return nil }
                return PuntoGrafica(
                    id: "\(serie.id)-\(indice)",
                    etiqueta: grafica.etiquetas[indice],
                    serie: serie.nombre,
                    valor: valor
                )
            }
        }
    }

    /// Ancho por grupo en vez de comprimir: veinte barras aplastadas en 320
    /// puntos no son un dato, son una mancha.
    private var ancho: CGFloat {
        let porGrupo = tipo == .line ? CGFloat(56) : CGFloat(max(1, grafica.series.count)) * 20 + 16
        return max(300, CGFloat(grafica.etiquetas.count) * porGrupo + 60)
    }
}

/// Tarjetas de los bloques TIPADOS del IDE (``IDEBlock``), los que el agente
/// acuña con `mostrar_tabla`/`mostrar_grafica`.
///
/// Se dibujan con las mismas vistas que una tabla en Markdown a propósito: dos
/// renders de tabla en la misma pantalla acabarían diciendo cosas distintas del
/// mismo dato.
struct BloquesIDEView: View {
    let bloques: [IDEBlock]

    var body: some View {
        ForEach(Array(bloques.enumerated()), id: \.offset) { _, bloque in
            switch bloque {
            case .table(let tabla):
                TablaRicaView(tabla: tabla.tabla, titulo: tabla.title, nota: tabla.note)
            case .chart(let grafica):
                GraficaIDEView(bloque: grafica)
            }
        }
    }
}

private struct GraficaIDEView: View {
    let bloque: IDEChartBlock

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            Text(bloque.title)
                .font(.subheadline.bold())
                .frame(maxWidth: .infinity, alignment: .leading)

            if let grafica = bloque.grafica {
                GraficaTablaView(grafica: grafica, tipo: bloque.chartKind)
                if !ejes.isEmpty {
                    pie(ejes)
                }
                if grafica.seriesOmitidas > 0 {
                    pie(
                        grafica.seriesOmitidas == 1
                            ? "1 serie llegó incompleta y no se dibujó."
                            : "\(grafica.seriesOmitidas) series llegaron incompletas y no se dibujaron.",
                        destacado: true
                    )
                }
            } else {
                // Una gráfica que no se puede dibujar cae a su texto
                // equivalente, nunca a un hueco en blanco.
                Text(bloque.fallbackText)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }

            if let nota = bloque.note, !nota.isEmpty {
                pie(nota)
            }
        }
        .padding(11)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 14))
        .overlay(
            RoundedRectangle(cornerRadius: 14)
                .strokeBorder(Color.primary.opacity(0.07))
        )
    }

    private var ejes: String {
        [bloque.xLabel.map { "X: \($0)" }, bloque.yLabel.map { "Y: \($0)" }]
            .compactMap { $0 }
            .joined(separator: " · ")
    }

    private func pie(_ texto: String, destacado: Bool = false) -> some View {
        Text(texto)
            .font(.caption2)
            .foregroundStyle(destacado ? AnyShapeStyle(Color.orange) : AnyShapeStyle(.tertiary))
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}
