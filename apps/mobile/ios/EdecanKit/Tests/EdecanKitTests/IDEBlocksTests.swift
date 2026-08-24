import Foundation
import Testing
@testable import EdecanKit

/// Espejo de `apps/web/ide-blocks.test.mjs`: los mismos casos, para que el
/// iPhone y el navegador no se separen sin que nadie se dé cuenta.
@Suite("Bloques tipados del IDE")
struct IDEBlocksTests {
    private static let tabla = """
    {
      "schema_version": 1,
      "type": "table",
      "fallback_text": "Ruta | ms\\n/a | 120",
      "title": "Rutas más lentas",
      "columns": [
        {"key": "ruta", "title": "Ruta", "align": "left"},
        {"key": "ms", "title": "Latencia", "align": "right"}
      ],
      "rows": [{"ruta": "/a", "ms": "120"}, {"ruta": "/b", "ms": "340"}],
      "note": "Se muestran 2 de 18 rutas."
    }
    """

    private static let grafica = """
    {
      "schema_version": 1,
      "type": "chart",
      "chart_kind": "line",
      "fallback_text": "ene 10, feb 20, mar 15",
      "title": "Latencia por mes",
      "series": [
        {"name": "p95", "points": [
          {"label": "ene", "value": 10},
          {"label": "feb", "value": 20},
          {"label": "mar", "value": 15}
        ]}
      ],
      "x_label": "mes",
      "y_label": "ms"
    }
    """

    private func bloque(_ json: String) -> IDEBlock? {
        try? JSONDecoder().decode(IDEBlock.self, from: Data(json.utf8))
    }

    private func decodificarEvento(presentation: String) -> IDESessionEvent? {
        let json = """
        {"cursor": 4, "type": "blocks", "text": "equivalente", "timestamp": 0,
         "presentation": \(presentation)}
        """
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .secondsSince1970
        return try? decoder.decode(IDESessionEvent.self, from: Data(json.utf8))
    }

    @Test("lee una tabla tipada con sus columnas y notas")
    func tablaTipada() throws {
        guard case .table(let tabla)? = bloque(Self.tabla) else {
            Issue.record("la tabla no decodificó")
            return
        }
        #expect(tabla.title == "Rutas más lentas")
        #expect(tabla.note == "Se muestran 2 de 18 rutas.")
        #expect(tabla.columns.map(\.align) == [.izquierda, .derecha])
    }

    @Test("ordena las celdas por CLAVE y deja vacía la que falta")
    func celdasPorClave() throws {
        let json = Self.tabla.replacingOccurrences(
            of: #""rows": [{"ruta": "/a", "ms": "120"}, {"ruta": "/b", "ms": "340"}]"#,
            with: #""rows": [{"ms": "340"}, {"ruta": "/c", "ms": "10"}]"#
        )
        guard case .table(let tabla)? = bloque(json) else {
            Issue.record("la tabla no decodificó")
            return
        }
        // Sin dato en "ruta": queda vacío en SU columna. Posicionalmente, "340"
        // habría caído bajo "Ruta" y la fila diría otra cosa sin que se note.
        #expect(tabla.tabla.filas == [["", "340"], ["/c", "10"]])
    }

    @Test("descarta claves que no corresponden a ninguna columna")
    func clavesDeMas() throws {
        let json = Self.tabla.replacingOccurrences(
            of: #""rows": [{"ruta": "/a", "ms": "120"}, {"ruta": "/b", "ms": "340"}]"#,
            with: #""rows": [{"ruta": "/a", "ms": "1", "secreto": "x"}]"#
        )
        guard case .table(let tabla)? = bloque(json) else {
            Issue.record("la tabla no decodificó")
            return
        }
        #expect(tabla.rows == [["ruta": "/a", "ms": "1"]])
    }

    @Test("una tabla sin ninguna celda con datos no se dibuja")
    func tablaVacia() {
        let json = Self.tabla.replacingOccurrences(
            of: #""rows": [{"ruta": "/a", "ms": "120"}, {"ruta": "/b", "ms": "340"}]"#,
            with: #""rows": [{}, {}]"#
        )
        #expect(bloque(json) == nil)
    }

    @Test("una versión futura del esquema no se interpreta como v1")
    func versionFutura() {
        #expect(bloque(Self.tabla.replacingOccurrences(of: "\"schema_version\": 1", with: "\"schema_version\": 2")) == nil)
    }

    @Test("un bloque de tipo desconocido se descarta sin tumbar el resto")
    func tipoDesconocido() throws {
        let evento = try #require(decodificarEvento(presentation: "[{\"schema_version\": 1, \"type\": \"mapa\"}, \(Self.tabla)]"))
        #expect(evento.presentation.count == 1)
        if case .table = evento.presentation[0] {} else { Issue.record("debía quedar la tabla") }
    }

    @Test("no dibuja más de tres bloques por evento")
    func topeDeBloques() throws {
        let lista = "[\(Array(repeating: Self.tabla, count: 4).joined(separator: ","))]"
        let evento = try #require(decodificarEvento(presentation: lista))
        #expect(evento.presentation.count == IDEBlockLimites.maxBloquesPorEvento)
    }

    @Test("un evento sin canal de presentación no trae bloques")
    func sinPresentation() throws {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .secondsSince1970
        let evento = try decoder.decode(
            IDESessionEvent.self,
            from: Data(#"{"cursor": 1, "type": "assistant", "text": "hola", "timestamp": 0}"#.utf8)
        )
        #expect(evento.presentation.isEmpty)
    }

    @Test("lee una gráfica de líneas con sus ejes")
    func graficaLineas() throws {
        guard case .chart(let grafica)? = bloque(Self.grafica) else {
            Issue.record("la gráfica no decodificó")
            return
        }
        #expect(grafica.chartKind == .line)
        #expect(grafica.xLabel == "mes")
        let proyectada = try #require(grafica.grafica)
        #expect(proyectada.etiquetas == ["ene", "feb", "mar"])
        #expect(proyectada.series[0].valores == [10, 20, 15])
        #expect(proyectada.seriesOmitidas == 0)
    }

    @Test("un tipo de gráfica desconocido cae a barras en vez de no dibujarse")
    func tipoDeGraficaDesconocido() throws {
        let json = Self.grafica.replacingOccurrences(of: "\"chart_kind\": \"line\"", with: "\"chart_kind\": \"burbujas\"")
        guard case .chart(let grafica)? = bloque(json) else {
            Issue.record("la gráfica no decodificó")
            return
        }
        #expect(grafica.chartKind == .bar)
    }

    @Test("una serie con menos de dos puntos no es una serie")
    func serieCorta() {
        let corta = """
        {"schema_version": 1, "type": "chart", "chart_kind": "bar",
         "fallback_text": "x", "title": "t",
         "series": [{"name": "p95", "points": [{"label": "ene", "value": 1}]}]}
        """
        #expect(bloque(corta) == nil)
    }

    @Test("un valor que no es número finito descarta ese punto")
    func valorNoFinito() throws {
        let json = """
        {"schema_version": 1, "type": "chart", "chart_kind": "bar",
         "fallback_text": "x", "title": "t",
         "series": [{"name": "p95", "points": [
            {"label": "ene", "value": 1},
            {"label": "feb", "value": "20"},
            {"label": "mar", "value": 3}]}]}
        """
        guard case .chart(let grafica)? = bloque(json) else {
            Issue.record("la gráfica no decodificó")
            return
        }
        #expect(grafica.series[0].points.map(\.label) == ["ene", "mar"])
    }

    @Test("una serie a la que le falta una etiqueta se descarta entera, no se rellena con ceros")
    func serieIncompleta() throws {
        let json = """
        {"schema_version": 1, "type": "chart", "chart_kind": "bar",
         "fallback_text": "x", "title": "t",
         "series": [
           {"name": "p95", "points": [
             {"label": "ene", "value": 1},
             {"label": "feb", "value": 2},
             {"label": "mar", "value": 3}]},
           {"name": "p50", "points": [
             {"label": "ene", "value": 5},
             {"label": "feb", "value": 6}]}
         ]}
        """
        guard case .chart(let grafica)? = bloque(json) else {
            Issue.record("la gráfica no decodificó")
            return
        }
        let proyectada = try #require(grafica.grafica)
        #expect(proyectada.series.count == 1)
        #expect(proyectada.series[0].nombre == "p95")
        #expect(proyectada.seriesOmitidas == 1)
    }
}
