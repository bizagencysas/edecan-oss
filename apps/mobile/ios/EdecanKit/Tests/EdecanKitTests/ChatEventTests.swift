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

    @Test func toolStartConservaToolCallId() throws {
        let evento = try decodificar(#"{"type":"tool_start","tool_call_id":"call-vuelo-1","name":"buscar_vuelos","args":{}}"#)
        #expect(evento == .toolStart(toolCallId: "call-vuelo-1", name: "buscar_vuelos", args: [:]))
    }

    @Test func toolStartSinArgsUsaDiccionarioVacio() throws {
        let evento = try decodificar(#"{"type":"tool_start","name":"hora_actual"}"#)
        #expect(evento == .toolStart(name: "hora_actual", args: [:]))
    }

    @Test func toolProgressMuestraAvanceSinExponerRazonamiento() throws {
        let evento = try decodificar(
            #"{"type":"tool_progress","tool_call_id":"call-1","name":"construir_app","elapsed_seconds":12,"message":"Edecán sigue trabajando"}"#
        )
        #expect(
            evento == .toolProgress(
                toolCallId: "call-1",
                name: "construir_app",
                elapsedSeconds: 12,
                message: "Edecán sigue trabajando"
            )
        )
    }

    @Test func toolEnd() throws {
        let evento = try decodificar(#"{"type":"tool_end","name":"agenda_eventos","result_preview":"2 eventos encontrados"}"#)
        #expect(evento == .toolEnd(name: "agenda_eventos", resultPreview: "2 eventos encontrados", artifacts: []))
    }

    @Test func toolEndVinculaMisionAsincronaAlMismoChat() throws {
        let evento = try decodificar(
            #"{"type":"tool_end","name":"delegar_mision","result_preview":"Misión creada","mission_id":"22222222-2222-4222-a222-222222222222"}"#
        )
        guard case .toolEnd(_, _, _, _, _, _, let missionId) = evento else {
            Issue.record("se esperaba .toolEnd")
            return
        }
        #expect(missionId == "22222222-2222-4222-a222-222222222222")
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

    @Test func messageDoneConUsageVacioNoRompe() throws {
        let evento = try decodificar(#"{"type":"done","usage":{}}"#)
        #expect(evento == .done(usage: nil))
    }

    @Test func messageDoneCapturadoDelRelayPublico() throws {
        let evento = try SSEClient.decodificarEvento(
            nombre: "message.done",
            payload: #"{"type": "done", "usage": {"input_tokens": 10243, "output_tokens": 6}}"#
        )
        #expect(evento == .done(usage: Usage(inputTokens: 10243, outputTokens: 6)))
    }

    @Test func marcadorDoneAlternativoNoInvalidaLaRespuestaRecibida() throws {
        let evento = try SSEClient.decodificarEvento(
            nombre: "message.done\r",
            payload: "[DONE]"
        )
        #expect(evento == .done(usage: nil))
    }

    @Test func eventoDeContenidoMalformadoSigueFallando() {
        #expect(throws: SSEClient.SSEError.self) {
            try SSEClient.decodificarEvento(
                nombre: "message.delta",
                payload: "{json roto"
            )
        }
    }

    @Test func error() throws {
        let evento = try decodificar(#"{"type":"error","message":"El proveedor LLM no respondió a tiempo"}"#)
        #expect(evento == .error(message: "El proveedor LLM no respondió a tiempo"))
    }

    @Test func tipoDesconocidoNoRompeElStream() throws {
        let evento = try decodificar(#"{"type":"algo_que_no_existe","payload":{"futuro":true}}"#)
        #expect(evento == .unknown(type: "algo_que_no_existe"))
    }

    @Test func argsConClavesSnakeCaseNoSeTransforman() throws {
        // Las claves de `args` son nombres reales de argumentos de la
        // herramienta (p. ej. de `crear_factura`) — deben llegar tal cual,
        // nunca convertidas a camelCase por accidente.
        let evento = try decodificar(#"{"type":"tool_start","name":"crear_factura","args":{"cliente_nombre":"Acme"}}"#)
        guard case .toolStart(_, _, let args) = evento else {
            Issue.record("se esperaba .toolStart")
            return
        }
        #expect(args["cliente_nombre"] == .string("Acme"))
        #expect(args["clienteNombre"] == nil)
    }

    @Test func toolEndDecodificaBloquesV1YToolCallId() throws {
        let evento = try decodificar(
            #"{"type":"tool_end","tool_call_id":"call-42","name":"buscar_viaje","result_preview":"Opciones listas","blocks_version":1,"blocks":[{"type":"link_preview","schema_version":1,"url":"https://example.com/oferta","title":"Guia del destino","site_name":"Example","source_mode":"live","actions":[{"id":"link.open","label":"Abrir","action":"open_url","url":"https://example.com/oferta"}]},{"type":"flight","offer_id":"F1","airline":"Avianca","origin":"BOG","destination":"MAD","departure":"2026-08-01T10:00:00Z","arrival":"2026-08-02T05:00:00Z","stops":0,"price":"650.00","currency":"USD","source_mode":"live","provider":"Amadeus","actions":[{"id":"flight.draft","label":"Preparar","action":"prefill_message","message":"Prepara el borrador sin reservar."}]},{"type":"hotel","offer_id":"H1","name":"Hotel Central","city":"Madrid","checkin":"2026-08-02","checkout":"2026-08-05","rating":"4.7","price":"420.00","currency":"USD","source_mode":"demo","actions":[{"id":"hotel.activity","label":"Ver actividad","action":"open_screen","screen":"activity"}]},{"type":"media","media_kind":"image","artifact":{"file_id":"018f7f4c-07f4-7ed0-93c8-cf0525d1092b","filename":"mapa.png","mime":"image/png"},"alt":"Mapa de Madrid"}]}"#
        )

        guard case .toolEnd(let toolCallId, _, _, _, let version, let blocks, _) = evento else {
            Issue.record("se esperaba .toolEnd")
            return
        }
        #expect(toolCallId == "call-42")
        #expect(version == 1)
        #expect(blocks.count == 4)

        guard case .linkPreview(let link) = blocks[0],
              case .openURL(_, let label, let url) = link.actions[0]
        else {
            Issue.record("link/action no decodificado")
            return
        }
        #expect(label == "Abrir")
        #expect(url.absoluteString == "https://example.com/oferta")

        guard case .flight(let flight) = blocks[1],
              case .prefillMessage(_, _, let message) = flight.actions[0]
        else {
            Issue.record("vuelo/prefill no decodificado")
            return
        }
        #expect(flight.origin == "BOG")
        #expect(message == "Prepara el borrador sin reservar.")

        guard case .hotel(let hotel) = blocks[2],
              case .openScreen(_, _, let screen) = hotel.actions[0]
        else {
            Issue.record("hotel/pantalla no decodificado")
            return
        }
        #expect(hotel.sourceMode == .demo)
        #expect(screen == .activity)

        guard case .media(let media) = blocks[3] else {
            Issue.record("media no decodificada")
            return
        }
        #expect(media.mediaKind == .image)
        #expect(media.artifact.filename == "mapa.png")
    }

    @Test func bloqueDesconocidoConservaFallbackSinRomperToolEnd() throws {
        let evento = try decodificar(
            #"{"type":"tool_end","name":"tool_futura","result_preview":"ok","blocks":[{"type":"timeline_3d","schema_version":2,"fallback_text":"Resultado disponible en texto."}]}"#
        )
        guard case .toolEnd(_, _, _, _, _, let blocks, _) = evento,
              case .unsupported(let type, let fallback) = blocks.first
        else {
            Issue.record("se esperaba bloque unsupported")
            return
        }
        #expect(type == "timeline_3d")
        #expect(fallback == "Resultado disponible en texto.")
    }

    @Test func accionDesconocidaNoDescartaLaTarjeta() throws {
        let evento = try decodificar(
            #"{"type":"tool_end","name":"enlace","result_preview":"ok","blocks":[{"type":"link_preview","url":"https://example.com","title":"Example","actions":[{"id":"future","label":"Teleportar","action":"teleport","destination":"moon"}]}]}"#
        )
        guard case .toolEnd(_, _, _, _, _, let blocks, _) = evento,
              case .linkPreview(let link) = blocks.first,
              case .unsupported(let id, let label, let action) = link.actions.first
        else {
            Issue.record("se esperaba accion unsupported dentro del link")
            return
        }
        #expect(id == "future")
        #expect(label == "Teleportar")
        #expect(action == "teleport")
    }

    @Test func prefillLegacyNuncaSeConvierteEnAutoenvio() throws {
        let canonical = try JSONDecoder().decode(
            ChatAction.self,
            from: Data(#"{"id":"one","label":"Usar","action":"prefill_message","message":"Reserva esto"}"#.utf8)
        )
        let legacy = try JSONDecoder().decode(
            ChatAction.self,
            from: Data(#"{"id":"two","label":"Usar","action":"send_message","message":"Reserva esto"}"#.utf8)
        )
        #expect(canonical == .prefillMessage(id: "one", label: "Usar", message: "Reserva esto"))
        #expect(legacy == .prefillMessage(id: "two", label: "Usar", message: "Reserva esto"))
    }

    @Test func openURLRechazaEsquemasYCredencialesNoSeguros() throws {
        let ftp = try JSONDecoder().decode(
            ChatAction.self,
            from: Data(#"{"id":"ftp","label":"Abrir","action":"open_url","url":"ftp://example.com/a"}"#.utf8)
        )
        let credentials = try JSONDecoder().decode(
            ChatAction.self,
            from: Data(#"{"id":"credentials","label":"Abrir","action":"open_url","url":"https://user:pass@example.com/a"}"#.utf8)
        )
        #expect(!ftp.isSupported)
        #expect(!credentials.isSupported)
        #expect(ChatAction.httpURLSegura("http://localhost/admin") == nil)
        #expect(ChatAction.httpURLSegura("http://192.168.1.1/admin") == nil)
        #expect(ChatAction.httpURLSegura("https://example.com/a")?.scheme == "https")
    }

    @Test func openScreenSoloAceptaAllowlistCompartida() throws {
        let settings = try JSONDecoder().decode(
            ChatAction.self,
            from: Data(#"{"id":"settings","label":"Ajustes","action":"open_screen","screen":"settings"}"#.utf8)
        )
        let arbitrary = try JSONDecoder().decode(
            ChatAction.self,
            from: Data(#"{"id":"admin","label":"Admin","action":"open_screen","screen":"internal_admin"}"#.utf8)
        )
        #expect(settings == .openScreen(id: "settings", label: "Ajustes", screen: .settings))
        #expect(!arbitrary.isSupported)
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
