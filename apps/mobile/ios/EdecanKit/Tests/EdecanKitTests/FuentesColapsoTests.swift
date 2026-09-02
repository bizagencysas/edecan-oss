import Testing
@testable import EdecanKit

struct FuentesColapsoTests {
    @Test func sinColapsoHastaDosFuentes() {
        #expect(!FuentesColapso.debeColapsar(total: 0))
        #expect(!FuentesColapso.debeColapsar(total: 1))
        #expect(!FuentesColapso.debeColapsar(total: 2))
        #expect(FuentesColapso.cantidadVisible(total: 2, expandido: false) == 2)
        #expect(FuentesColapso.etiquetaExpansion(total: 2, expandido: false) == nil)
    }

    @Test func colapsadoMuestraUnaFilaYVerMas() {
        #expect(FuentesColapso.debeColapsar(total: 13))
        #expect(FuentesColapso.cantidadVisible(total: 13, expandido: false) == 1)
        #expect(FuentesColapso.cantidadVisible(total: 13, expandido: true) == 13)
        #expect(FuentesColapso.etiquetaExpansion(total: 13, expandido: false) == "Ver 12 más")
        #expect(FuentesColapso.etiquetaExpansion(total: 13, expandido: true) == "Ver menos")
    }
}
