import Testing
@testable import EdecanKit

/// Espejo de `apps/web/ide-rich-text.test.mjs`: los mismos casos, para que el
/// iPhone y el navegador no se separen sin que nadie se dé cuenta.
@Suite("Tablas y gráficas del texto del IDE")
struct TextoRicoMarkdownTests {
    private func extraerTabla(_ texto: String) -> TablaRica? {
        for segmento in TextoRicoParser.segmentar(texto) {
            if case .tabla(_, let tabla) = segmento { return tabla }
        }
        return nil
    }

    @Test("reconoce una tabla GFM y separa la prosa de alrededor")
    func tablaConProsa() throws {
        let segmentos = TextoRicoParser.segmentar(
            """
            Resultado:

            | Mes | Ventas |
            | --- | ---: |
            | Enero | 10 |
            | Febrero | 12 |

            Listo.
            """
        )
        #expect(segmentos.count == 3)
        guard case .texto = segmentos[0] else { Issue.record("el primer segmento es prosa"); return }
        guard case .tabla(_, let tabla) = segmentos[1] else { Issue.record("el segundo es tabla"); return }
        guard case .texto = segmentos[2] else { Issue.record("el tercero es prosa"); return }
        #expect(tabla.encabezados == ["Mes", "Ventas"])
        #expect(tabla.alineaciones == [.izquierda, .derecha])
        #expect(tabla.filas == [["Enero", "10"], ["Febrero", "12"]])
        #expect(tabla.filasTotales == 2)
    }

    @Test("acepta tablas sin barras en los extremos y con alineación centrada")
    func sinBarrasExteriores() throws {
        let tabla = try #require(extraerTabla("a | b | c\n:-: | :-- | --:\n1 | 2 | 3"))
        #expect(tabla.alineaciones == [.centro, .izquierda, .derecha])
        #expect(tabla.filas == [["1", "2", "3"]])
    }

    @Test("una tabla dentro de un bloque de código sigue siendo código")
    func dentroDeValla() {
        let texto = "```\n| a | b |\n| --- | --- |\n| 1 | 2 |\n```"
        let segmentos = TextoRicoParser.segmentar(texto)
        #expect(segmentos.count == 1)
        #expect(extraerTabla(texto) == nil)
    }

    @Test("no convierte en tabla una frase con barras")
    func frasesConBarras() {
        #expect(extraerTabla("Corre npm run lint | npm test y avisa.\nOtra línea.") == nil)
    }

    @Test("rellena celdas faltantes y descarta las que sobran, como GFM")
    func filasDesparejas() throws {
        let tabla = try #require(extraerTabla("| a | b |\n| --- | --- |\n| 1 |\n| 1 | 2 | 3 |"))
        #expect(tabla.filas == [["1", ""], ["1", "2"]])
    }

    @Test("respeta las barras escapadas dentro de una celda")
    func barrasEscapadas() throws {
        #expect(TextoRicoParser.celdas("| a \\| b | c |") == ["a | b", "c"])
        let tabla = try #require(extraerTabla("| x | y |\n| --- | --- |\n| a \\| b | c |"))
        #expect(tabla.filas == [["a | b", "c"]])
    }

    @Test("las celdas quedan en texto plano para que iOS y web muestren lo mismo")
    func celdasPlanas() {
        #expect(TextoRicoParser.celdaPlana("**Total**") == "Total")
        #expect(TextoRicoParser.celdaPlana("`uv run pytest`") == "uv run pytest")
        #expect(TextoRicoParser.celdaPlana("[Edecán](https://edecan.example)") == "Edecán")
    }

    @Test("avisa cuando la tabla trae más filas de las que se pintan")
    func topeDeFilas() throws {
        let filas = (0..<(TextoRicoParser.maxFilas + 5)).map { "| f\($0) | \($0) |" }
        let texto = (["| a | b |", "| --- | --- |"] + filas).joined(separator: "\n")
        let tabla = try #require(extraerTabla(texto))
        #expect(tabla.filas.count == TextoRicoParser.maxFilas)
        #expect(tabla.filasTotales == TextoRicoParser.maxFilas + 5)
    }

    @Test("lee números en los formatos que mezcla el modelo")
    func numeros() {
        #expect(TextoRicoParser.numero("1.234,56") == 1234.56)
        #expect(TextoRicoParser.numero("1,234.56") == 1234.56)
        #expect(TextoRicoParser.numero("1,234") == 1234)
        #expect(TextoRicoParser.numero("1.234") == 1234)
        #expect(TextoRicoParser.numero("12,5") == 12.5)
        #expect(TextoRicoParser.numero("$ 1 200") == 1200)
        #expect(TextoRicoParser.numero("45%") == 45)
        #expect(TextoRicoParser.numero("(320)") == -320)
        #expect(TextoRicoParser.numero("-7.5") == -7.5)
        #expect(TextoRicoParser.numero("**42**") == 42)
    }

    @Test("una celda que no es solo un número no se grafica")
    func noNumeros() {
        #expect(TextoRicoParser.numero("12 días") == nil)
        #expect(TextoRicoParser.numero("") == nil)
        #expect(TextoRicoParser.numero("—") == nil)
        #expect(TextoRicoParser.numero("1.2.3.4") == nil)
    }

    @Test("arma series solo con columnas enteramente numéricas")
    func seriesNumericas() throws {
        let tabla = try #require(
            extraerTabla(
                """
                | Canal | Ventas | Notas |
                | --- | ---: | --- |
                | Web | 1.200 | sube |
                | Tienda | 800 | baja |
                """
            )
        )
        let grafica = try #require(TextoRicoParser.grafica(de: tabla))
        #expect(grafica.etiquetas == ["Web", "Tienda"])
        #expect(grafica.series.count == 1)
        #expect(grafica.series[0].nombre == "Ventas")
        #expect(grafica.series[0].valores == [1200, 800])
        #expect(grafica.seriesOmitidas == 0)
    }

    @Test("numera las etiquetas repetidas en vez de fundir dos filas en una barra")
    func etiquetasRepetidas() throws {
        let tabla = try #require(extraerTabla("| Mes | Ventas |\n| --- | --- |\n| Enero | 1 |\n| Enero | 2 |"))
        let grafica = try #require(TextoRicoParser.grafica(de: tabla))
        #expect(grafica.etiquetas == ["Enero", "Enero (2)"])
    }

    @Test("sin ninguna columna numérica no hay gráfica que ofrecer")
    func sinSeries() throws {
        let tabla = try #require(extraerTabla("| a | b |\n| --- | --- |\n| uno | dos |\n| tres | cuatro |"))
        #expect(TextoRicoParser.grafica(de: tabla) == nil)
    }

    /// Espejo exacto de "pasado el tope de series dice cuántas quedaron fuera"
    /// en `apps/web/ide-rich-text.test.mjs`: mismas 7 columnas numéricas y mismo
    /// resultado. Antes este caso usaba 5 columnas contra un tope de 4, así que
    /// pasaba en verde mientras las dos superficies dibujaban gráficas distintas.
    @Test("pasado el tope de series dice cuántas quedaron fuera")
    func seriesOmitidas() throws {
        let tabla = try #require(
            extraerTabla(
                """
                | clave | s1 | s2 | s3 | s4 | s5 | s6 | s7 |
                | --- | --- | --- | --- | --- | --- | --- | --- |
                | a | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
                | b | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
                """
            )
        )
        let grafica = try #require(TextoRicoParser.grafica(de: tabla))
        #expect(TextoRicoParser.maxSeries == 6)
        #expect(grafica.series.count == TextoRicoParser.maxSeries)
        #expect(grafica.seriesOmitidas == 7 - TextoRicoParser.maxSeries)
    }
}
