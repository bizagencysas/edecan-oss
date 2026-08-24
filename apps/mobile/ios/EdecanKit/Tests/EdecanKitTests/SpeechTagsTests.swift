import Testing
@testable import EdecanKit

struct SpeechTagsTests {
    @Test func ocultaCualquierTagOEfecto() {
        let fuente = """
        [thoughtfully] Recuerdo que eres Alex. [laughs] También trabajas \
        en tecnología. [applause] ¿Es correcto?
        """
        let visible = SpeechTags.ocultar(fuente)
        #expect(!visible.contains("[thoughtfully]"))
        #expect(!visible.contains("[laughs]"))
        #expect(!visible.contains("[applause]"))
        #expect(visible.contains("Recuerdo que eres Alex."))
        #expect(visible.contains("También trabajas en tecnología."))
        #expect(visible.contains("¿Es correcto?"))
    }

    @Test func ocultaEfectosConEspacios() {
        let visible = SpeechTags.ocultar("[clears throat] Un segundo. [sighs deeply] Listo.")
        #expect(!visible.contains("["))
        #expect(visible.contains("Un segundo."))
        #expect(visible.contains("Listo."))
    }

    @Test func conservaEnlacesEImagenesMarkdown() {
        let fuente = "[warmly] Mira [Edecán](https://edecan.cc) y ![foto](https://x/a.png)."
        let visible = SpeechTags.ocultar(fuente)
        #expect(!visible.contains("[warmly]"))
        #expect(visible.contains("[Edecán](https://edecan.cc)"))
        #expect(visible.contains("![foto](https://x/a.png)"))
    }

    @Test func noTocaTextoSinTagsAlOcultar() {
        let fuente = "Hola. ¿Qué recuerdas de mí?"
        #expect(SpeechTags.ocultar(fuente) == fuente)
    }

    @Test func enriqueceLimpiaCualquierTagExistente() {
        let fuente = """
        [thoughtfully] Recuerdo que eres Alex Manuel Example Gonzalez, \
        nacido el 8 de enero de 1996. También recuerdo que trabajas en \
        tecnología. Además, valoras la comunicación humana. ¿Es correcto?
        """
        let enriquecido = SpeechTags.enriquecer(fuente)
        #expect(!enriquecido.contains("[thoughtfully]"))
        #expect(!enriquecido.contains("["))
        #expect(enriquecido.contains("Recuerdo que eres Alex"))
        #expect(enriquecido.contains("¿Es correcto?"))
    }

    @Test func limpiaTagsDelModeloAlOcultar() {
        let fuente = "[laughs] Jajaja. [applause] Eso sí."
        let limpio = SpeechTags.ocultar(fuente)
        #expect(!limpio.contains("["))
        #expect(limpio.contains("Jajaja."))
        #expect(limpio.contains("Eso sí."))
    }
}
