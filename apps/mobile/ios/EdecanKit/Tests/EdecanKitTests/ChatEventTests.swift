import Testing
import Foundation
@testable import EdecanKit

/// Un test por cada fila de la tabla de eventos SSE de
/// `docs/api.md` §"Conversaciones y chat (SSE)", con el `data:` de ejemplo
/// EXACTO que aparece ahí — si `edecan_schemas.chat` cambia de forma en el
/// backend sin actualizar la doc y este cliente a la vez, uno de estos tests
/// debería fallar.
struct ChatEventTests {
    private func decodificar(_ json: String) throws -> ChatEvent {
        try JSONDecoder().decode(ChatEvent.self, from: Data(json.utf8))
    }

    @Test func textDelta() throws {
        let evento = try decodificar(#"{"type":"text_delta","text":"Mañana tienes..."}"#)
        #expect(evento == .textDelta(text: "Mañana tienes..."))
    }

    @Test func toolStart() throws {
        let evento = try decodificar(#"{"type":"tool_start","name":"agenda_eventos","args":{"dia":"2026-07-08"}}"#)
        #expect(evento == .toolStart(name: "agenda_eventos", args: ["dia": .string("2026-07-08")]))
    }

    @Test func toolStartSinArgsUsaDiccionarioVacio() throws {
        let evento = try decodificar(#"{"type":"tool_start","name":"hora_actual"}"#)
        #expect(evento == .toolStart(name: "hora_actual", args: [:]))
    }

    @Test func toolEnd() throws {
        let evento = try decodificar(#"{"type":"tool_end","name":"agenda_eventos","result_preview":"2 eventos encontrados"}"#)
        #expect(evento == .toolEnd(name: "agenda_eventos", resultPreview: "2 eventos encontrados", artifacts: []))
    }

    @Test func toolEndConArtefactosDescargables() throws {
        let evento = try decodificar(
            #"{"type":"tool_end","name":"crear_pdf","result_preview":"PDF listo","artifacts":[{"file_id":"018f7f4c-07f4-7ed0-93c8-cf0525d1092b","filename":"propuesta.pdf","mime":"application/pdf"}]}"#
        )
        #expect(
            evento == .toolEnd(
                name: "crear_pdf",
                resultPreview: "PDF listo",
                artifacts: [
                    ArtifactRef(
                        fileId: "018f7f4c-07f4-7ed0-93c8-cf0525d1092b",
                        filename: "propuesta.pdf",
                        mime: "application/pdf"
                    )
                ]
            )
        )
    }

    @Test func confirmationRequired() throws {
        let evento = try decodificar(#"{"type":"confirmation_required","tool_call_id":"call_abc123","name":"enviar_correo","args":{"para":"..."}}"#)
        #expect(evento == .confirmationRequired(toolCallId: "call_abc123", name: "enviar_correo", args: ["para": .string("...")]))
    }

    @Test func messageDoneConUsage() throws {
        let evento = try decodificar(#"{"type":"done","usage":{"input_tokens":812,"output_tokens":143}}"#)
        #expect(evento == .done(usage: Usage(inputTokens: 812, outputTokens: 143)))
    }

    @Test func messageDoneSinUsage() throws {
        let evento = try decodificar(#"{"type":"done"}"#)
        #expect(evento == .done(usage: nil))
    }

    @Test func error() throws {
        let evento = try decodificar(#"{"type":"error","message":"El proveedor LLM no respondió a tiempo"}"#)
        #expect(evento == .error(message: "El proveedor LLM no respondió a tiempo"))
    }

    @Test func tipoDesconocidoLanzaError() {
        #expect(throws: (any Error).self) {
            try decodificar(#"{"type":"algo_que_no_existe"}"#)
        }
    }

    @Test func argsConClavesSnakeCaseNoSeTransforman() throws {
        // Las claves de `args` son nombres reales de argumentos de la
        // herramienta (p. ej. de `crear_factura`) — deben llegar tal cual,
        // nunca convertidas a camelCase por accidente.
        let evento = try decodificar(#"{"type":"tool_start","name":"crear_factura","args":{"cliente_nombre":"Acme"}}"#)
        guard case .toolStart(_, let args) = evento else {
            Issue.record("se esperaba .toolStart")
            return
        }
        #expect(args["cliente_nombre"] == .string("Acme"))
        #expect(args["clienteNombre"] == nil)
    }
}

struct JSONValueTests {
    @Test func idaYVueltaDeUnObjetoAnidado() throws {
        let original: JSONValue = .object([
            "nombre": .string("Edecán"),
            "activo": .bool(true),
            "version": .number(3),
            "tags": .array([.string("a"), .string("b")]),
            "extra": .null,
        ])
        let data = try JSONEncoder().encode(original)
        let decodificado = try JSONDecoder().decode(JSONValue.self, from: data)
        #expect(decodificado == original)
    }

    @Test func vistaPreviaDeUnStringEsElTextoPlano() {
        #expect(JSONValue.string("hola").vistaPrevia == "hola")
    }

    @Test func vistaPreviaDeUnObjetoListaClavesOrdenadas() {
        let valor = JSONValue.object(["b": .number(2), "a": .number(1)])
        #expect(valor.vistaPrevia == "{a: 1.0, b: 2.0}")
    }
}
