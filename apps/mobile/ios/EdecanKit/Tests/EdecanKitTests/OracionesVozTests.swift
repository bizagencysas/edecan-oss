import Foundation
import Testing
@testable import EdecanKit

struct OracionesVozTests {
    @Test func partePorPuntoYConservaSpeechTags() {
        let texto = "[warmly] Buenas noches, Alex. ¿Cómo te encuentras esta noche? Todo bien."
        let partes = OracionesVoz.partir(texto)
        #expect(partes.count == 3)
        #expect(partes[0].contains("[warmly]"))
        #expect(partes[0].contains("Alex."))
        #expect(partes[1].contains("encuentras"))
    }

    @Test func fusionaAbreviaturasCortas() {
        let partes = OracionesVoz.partir("Sr. Pérez llega mañana.")
        #expect(partes.count == 1)
        #expect(partes[0].contains("Sr."))
        #expect(partes[0].contains("Pérez"))
    }

    @Test func vacioDevuelveNada() {
        #expect(OracionesVoz.partir("   ").isEmpty)
    }
}
