import Testing
@testable import EdecanKit

struct SegmentadorNarracionTests {
    @Test func parteElRelatoDeOdaEnMiniCards() {
        let texto = """
        Abro Cursor y te muestro qué hay. Ya abrí Cursor. Ahora veo qué hay en pantalla \
        para saber dónde escribir. Hago clic en el campo de texto del chat de Cursor. \
        Tomo otra captura para ver bien dónde está el campo de texto. Intento escribir \
        directamente en Cursor; si el foco no está en el chat, ajusto después. Ahora \
        presiono Enter para enviarlo. Listo, ya presioné Enter. Te muestro cómo quedó \
        la pantalla de Cursor. Ejecuté los pasos: abrí Cursor, escribí el mensaje y di \
        Enter. Pero te voy a ser honesto: las capturas de pantalla no me llegan con \
        suficiente detalle para leer lo que hay en cada ventana, así que no te puedo \
        confirmar al 100 % que el mensaje se envió al chat correcto de Cursor. Revisa \
        tú la pantalla o dime si viste el texto publicado; si no quedó, lo reintento.
        """
        let tarjetas = SegmentadorNarracion.tarjetas(texto)
        #expect(tarjetas.count >= 6)
        #expect(tarjetas.first == "Abro Cursor y te muestro qué hay.")
        #expect(tarjetas.contains { $0.contains("presiono Enter") })
        #expect(tarjetas.contains { $0.contains("te voy a ser honesto") })
        #expect(tarjetas.allSatisfy { !$0.contains("[") })
    }

    @Test func respetaParrafosYNoParteCodigo() {
        let parrafos = SegmentadorNarracion.tarjetas("Primero.\n\nDespués, el cierre.")
        #expect(parrafos.count == 2)
        #expect(SegmentadorNarracion.tarjetas("Usa `code` y ya.") == ["Usa `code` y ya."])
        let fenced = "Mira:\n```\nfoo.bar()\n```\nListo."
        #expect(SegmentadorNarracion.tarjetas(fenced).count == 1)
    }

    @Test func unSaludoCortoSeQuedaEnUnaSolaTarjeta() {
        #expect(SegmentadorNarracion.tarjetas("Listo.") == ["Listo."])
        #expect(SegmentadorNarracion.tarjetas("   ").isEmpty)
    }

    @Test func elHiloSoloValeConDosPasosOMas() {
        #expect(SegmentadorNarracion.debeMostrarHilo(tarjetas: ["Hola."]) == false)
        #expect(SegmentadorNarracion.debeMostrarHilo(tarjetas: ["Uno.", "Dos."]) == true)
    }
}
