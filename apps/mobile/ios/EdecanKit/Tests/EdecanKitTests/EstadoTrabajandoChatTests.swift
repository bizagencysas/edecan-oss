import Testing
@testable import EdecanKit

struct EstadoTrabajandoChatTests {
    @Test func filaVisibleSoloCuandoAsistenteEnProgreso() {
        #expect(EstadoTrabajandoChat.debeMostrarFila(enProgreso: true, rolEsAsistente: true))
        #expect(!EstadoTrabajandoChat.debeMostrarFila(enProgreso: false, rolEsAsistente: true))
        #expect(!EstadoTrabajandoChat.debeMostrarFila(enProgreso: true, rolEsAsistente: false))
        #expect(!EstadoTrabajandoChat.debeMostrarFila(enProgreso: false, rolEsAsistente: false))
    }

    @Test func etiquetaAccesibilidadIncluyeNombreYTrabajando() {
        #expect(EstadoTrabajandoChat.etiquetaAccesibilidad(nombreAgente: "Edecán") == "Edecán está trabajando")
        #expect(EstadoTrabajandoChat.etiquetaAccesibilidad(nombreAgente: "  ") == "Edecán está trabajando")
    }

    @Test func tarjetaProgresoOcultaEnElHilo() {
        #expect(!EstadoTrabajandoChat.debeMostrarTarjetaProgresoHerramientas(enProgreso: true))
        #expect(!EstadoTrabajandoChat.debeMostrarTarjetaProgresoHerramientas(enProgreso: false))
    }
}
