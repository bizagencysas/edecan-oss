import Foundation
import Testing
@testable import EdecanKit

/// QuestionBlock al recargar historial de bot (CONTINUAR-EDECAN-HYPERAPP §6).
///
/// El JSON imita `normalize_stored_message` + `preguntar_al_usuario` real
/// (`QuestionBlock` de `edecan_schemas.chat`), no el fixture suelto con `prompt`.
@Suite("TeamMessage reload → QuestionBlock")
struct TeamMessageReloadQuestionTests {
    /// Payload de `QuestionBlockRealTest` anidado en `tool_end` persistido.
    private static let historialConPregunta = """
    {
      "id": "msg-reload-q",
      "role": "assistant",
      "text": "¿A qué cuenta publico este post?",
      "sender_id": "bot-community",
      "sender_name": "Community",
      "created_at": "2026-09-07T00:00:00Z",
      "tool_calls": [
        {
          "type": "tool_end",
          "tool_call_id": "call_q",
          "name": "preguntar_al_usuario",
          "result_preview": "Le mostré la pregunta al usuario",
          "blocks_version": 1,
          "artifacts": [],
          "blocks": [{
            "schema_version": 1,
            "fallback_text": "¿A qué cuenta publico este post? Opciones: Personal, Acme.",
            "type": "question",
            "question": "¿A qué cuenta publico este post?",
            "header": "Destino",
            "options": [
              {"label": "Personal", "description": "Tu perfil", "value": null},
              {"label": "Acme", "description": "La página de la empresa", "value": null}
            ],
            "multi_select": false,
            "allow_free_text": true
          }]
        }
      ]
    }
    """

    @Test("al reabrir el chat, tool_calls reconstruye el widget question")
    func bloquesDesdeToolCallsIncluyeQuestion() throws {
        let mensaje = try APIClient.crearDecoder().decode(
            TeamMessage.self,
            from: Data(Self.historialConPregunta.utf8)
        )
        let bloques = mensaje.bloquesDesdeToolCalls
        #expect(bloques.count == 1)
        guard case .question(let pregunta) = bloques.first else {
            Issue.record("Reload perdió QuestionBlock: \(bloques)")
            return
        }
        #expect(pregunta.question == "¿A qué cuenta publico este post?")
        #expect(pregunta.options.map(\.label) == ["Personal", "Acme"])
        #expect(pregunta.allowFreeText == true)
    }

    @Test("sin blocks_version sigue siendo versión 1 (no se traga el widget)")
    func toolEndSinBlocksVersionIgualReconstruye() throws {
        let json = """
        {
          "id": "msg-q2",
          "text": "¿Cuál?",
          "sender_id": "bot-1",
          "tool_calls": [{
            "type": "tool_end",
            "name": "preguntar_al_usuario",
            "blocks": [{
              "type": "question",
              "question": "¿Cuál?",
              "options": [{"label": "A"}, {"label": "B"}],
              "allow_free_text": true
            }]
          }]
        }
        """
        let mensaje = try JSONDecoder().decode(TeamMessage.self, from: Data(json.utf8))
        guard case .question(let pregunta) = mensaje.bloquesDesdeToolCalls.first else {
            Issue.record("blocks_version omitido no debe ocultar la pregunta")
            return
        }
        #expect(pregunta.question == "¿Cuál?")
    }
}
