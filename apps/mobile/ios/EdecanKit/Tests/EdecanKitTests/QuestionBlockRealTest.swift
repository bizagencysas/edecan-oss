import Foundation
import Testing
@testable import EdecanKit

/// El bloque de pregunta se decodifica desde el JSON EXACTO que produce el backend.
///
/// El payload de abajo no está escrito a mano: es la salida literal de
/// `preguntar_al_usuario` después de pasar por `rich_blocks_from_tool_data` (el mismo filtro
/// de lista blanca que corre en un turno real). Si el contrato del servidor cambia y el
/// cliente deja de entenderlo, esto se cae acá y no en el teléfono.
@Suite("QuestionBlock contra el payload real del backend")
struct QuestionBlockRealTest {
    static let payloadReal = """
    {"schema_version": 1,
     "fallback_text": "¿A qué cuenta publico este post? Opciones: Personal, Acme.",
     "type": "question",
     "question": "¿A qué cuenta publico este post?",
     "header": "Destino",
     "options": [
       {"label": "Personal", "description": "Tu perfil", "value": null},
       {"label": "Acme", "description": "La página de la empresa", "value": null}
     ],
     "multi_select": false,
     "allow_free_text": true}
    """

    @Test("decodifica como .question y no como bloque desconocido")
    func decodifica() throws {
        let bloque = try JSONDecoder().decode(
            ChatBlock.self, from: Data(Self.payloadReal.utf8)
        )
        guard case .question(let pregunta) = bloque else {
            Issue.record("Cayó a unsupported: el modal NUNCA se dibujaría. \(bloque)")
            return
        }
        #expect(pregunta.question == "¿A qué cuenta publico este post?")
        #expect(pregunta.header == "Destino")
        #expect(pregunta.options.count == 2)
        #expect(pregunta.options.map(\.label) == ["Personal", "Acme"])
        #expect(pregunta.multiSelect == false)
        #expect(pregunta.allowFreeText == true)
    }

    @Test("el texto que se envía al tocar sale de la opción")
    func textoDeRespuesta() throws {
        let bloque = try JSONDecoder().decode(
            ChatBlock.self, from: Data(Self.payloadReal.utf8)
        )
        guard case .question(let pregunta) = bloque else { return }
        // Sin `value`, se manda la etiqueta; con `value`, la instrucción explícita.
        #expect(pregunta.options[0].messageText == "Personal")

        let conValor = """
        {"schema_version":1,"type":"question","question":"¿Cuál?","header":null,
         "options":[{"label":"Personal","description":null,
                     "value":"Publícalo en mi cuenta personal"},
                    {"label":"Otra","description":null,"value":null}],
         "multi_select":false,"allow_free_text":true}
        """
        let b2 = try JSONDecoder().decode(ChatBlock.self, from: Data(conValor.utf8))
        guard case .question(let p2) = b2 else {
            Issue.record("no decodificó"); return
        }
        #expect(p2.options[0].messageText == "Publícalo en mi cuenta personal")
    }
}
