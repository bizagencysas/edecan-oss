import Testing
import Foundation
@testable import EdecanKit

/// ``ConfirmacionFormato`` — advertencias por herramienta (usadas por
/// `TarjetaConfirmacion` en `EdecanApp`) y el recorte de la vista previa de
/// argumentos. Mismo criterio de "calcado de la web" que el resto de tests
/// de este paquete: `advertencia(paraHerramienta: "usar_computadora")` debe
/// mencionar los mismos puntos que
/// `apps/web/src/components/chat/ConfirmationCard.tsx::ADVERTENCIAS_POR_HERRAMIENTA`.
struct ConfirmacionFormatoTests {
    @Test func usarComputadoraTieneAdvertenciaEspecifica() {
        let advertencia = ConfirmacionFormato.advertencia(paraHerramienta: "usar_computadora")
        #expect(advertencia != nil)
        #expect(advertencia?.contains("LinkedIn") == true)
        #expect(advertencia?.contains("mouse") == true)
        #expect(advertencia?.contains("pantalla") == true)
    }

    @Test func herramientaSinEntradaNoTieneAdvertencia() {
        #expect(ConfirmacionFormato.advertencia(paraHerramienta: "enviar_correo") == nil)
        #expect(ConfirmacionFormato.advertencia(paraHerramienta: "vehiculo_controlar") == nil)
    }

    @Test func vistaPreviaCortaVuelveIgual() {
        let corta = "texto corto"
        #expect(ConfirmacionFormato.vistaPreviaRecortada(corta, limite: 400) == corta)
    }

    @Test func vistaPreviaLargaSeRecortaConElipsis() {
        let larga = String(repeating: "a", count: 500)
        let recortada = ConfirmacionFormato.vistaPreviaRecortada(larga, limite: 400)
        #expect(recortada.count == 401) // 400 caracteres + "…"
        #expect(recortada.hasPrefix(String(repeating: "a", count: 400)))
        #expect(recortada.hasSuffix("…"))
    }

    @Test func vistaPreviaExactamenteEnElLimiteNoSeRecorta() {
        let exacta = String(repeating: "b", count: 400)
        #expect(ConfirmacionFormato.vistaPreviaRecortada(exacta, limite: 400) == exacta)
    }
}
