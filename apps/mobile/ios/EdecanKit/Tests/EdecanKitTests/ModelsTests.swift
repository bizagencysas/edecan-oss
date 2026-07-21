import Testing
import Foundation
@testable import EdecanKit

/// Estos tests decodifican los mismos ejemplos de JSON que aparecen en
/// `docs/api.md` — si el contrato del backend cambia de forma, uno de estos
/// tests debería fallar antes de que lo note un usuario real.
struct ModelsTests {
    // MARK: - TokenPair

    @Test func decodificaTokenPairDeLogin() throws {
        let json = """
        {
          "access_token": "eyJhbGciOiJIUzI1NiIs...",
          "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
          "token_type": "bearer"
        }
        """
        let tokens = try APIClient.crearDecoder().decode(TokenPair.self, from: Data(json.utf8))
        #expect(tokens.accessToken == "eyJhbGciOiJIUzI1NiIs...")
        #expect(tokens.refreshToken == "eyJhbGciOiJIUzI1NiIs...")
        #expect(tokens.tokenType == "bearer")
    }

    // MARK: - Me

    @Test func decodificaMeConFlagsMixtos() throws {
        let json = """
        {
          "user": {"id": "8b1c...", "email": "tu@correo.com", "is_superadmin": false, "created_at": "2026-07-01T10:00:00Z"},
          "tenant": {"id": "3fa2...", "name": "Mi Empresa", "slug": "mi-empresa", "plan_key": "hosted_pro", "status": "active", "created_at": "2026-07-01T10:00:00Z"},
          "flags": {
            "voice.web": true,
            "voice.telephony": true,
            "connectors.social": true,
            "campaigns": false,
            "companion": true,
            "models.premium": true,
            "limits.messages_per_day": 600,
            "limits.voice_minutes_month": 300,
            "limits.storage_mb": 10240,
            "limits.phone_numbers": 1,
            "limits.seats": 1
          }
        }
        """
        let me = try APIClient.crearDecoder().decode(Me.self, from: Data(json.utf8))
        #expect(me.user.email == "tu@correo.com")
        #expect(me.user.isSuperadmin == false)
        #expect(me.tenant.planKey == "hosted_pro")
        #expect(me.tenant.status == "active")
        #expect(me.flags["voice.web"]?.boolValue == true)
        #expect(me.flags["campaigns"]?.boolValue == false)
        #expect(me.flags["limits.messages_per_day"]?.intValue == 600)
        #expect(me.flags["limits.phone_numbers"]?.intValue == 1)
        #expect(me.nombrePila == "tu")
    }

    @Test func nombrePilaCaeAlEmailCompletoSinArroba() {
        let me = Me(
            user: .init(id: "1", email: "sinarroba", isSuperadmin: false, createdAt: .now),
            tenant: .init(id: "2", name: "T", slug: "t", planKey: "free_selfhost", status: "active", createdAt: .now),
            flags: [:]
        )
        #expect(me.nombrePila == "sinarroba")
    }

    @Test func perfilPersonalDecodificaIdentidadCompartida() throws {
        let json = """
        {
          "resumen":"Construye productos con IA.",
          "datos":{
            "identidad":{"nombre_preferido":"Isacc","nombre_completo":"Isacc Lara","pronombres":"él","fecha_nacimiento":"8 de enero de 1996","pais":"Venezuela","ciudad":"Medellín","zona_horaria":"America/Bogota","ocupacion":"Fundador","idioma_preferido":"Español de Venezuela","forma_de_trato":"Cercano y directo","biografia":"Construye startups."},
            "gustos":[],"proyectos":["Edecán"],"metas":[],"relaciones":[],"empresas":[],"habitos":[]
          },
          "version":4,"updated_at":"2026-07-21T18:00:00Z"
        }
        """
        let perfil = try APIClient.crearDecoder().decode(LiveProfile.self, from: Data(json.utf8))
        #expect(perfil.datos.identidad.nombrePreferido == "Isacc")
        #expect(perfil.datos.identidad.formaDeTrato == "Cercano y directo")
        #expect(perfil.datos.proyectos == ["Edecán"])
        #expect(perfil.version == 4)
    }

    // MARK: - Conversation

    @Test func decodificaListaDeConversaciones() throws {
        let json = """
        [{"id": "c1a0...", "title": "Planear el viaje a Bogotá", "channel": "web", "created_at": "2026-07-01T10:00:00Z"}]
        """
        let conversaciones = try APIClient.crearDecoder().decode([Conversation].self, from: Data(json.utf8))
        #expect(conversaciones.count == 1)
        #expect(conversaciones[0].id == "c1a0...")
        #expect(conversaciones[0].title == "Planear el viaje a Bogotá")
        #expect(conversaciones[0].channel == "web")
    }

    @Test func conversationAceptaTituloNulo() throws {
        let json = """
        {"id": "c2", "title": null, "channel": "api", "created_at": "2026-07-01T10:00:00Z"}
        """
        let conversacion = try APIClient.crearDecoder().decode(Conversation.self, from: Data(json.utf8))
        #expect(conversacion.title == nil)
        #expect(conversacion.updatedAt == nil)
    }

    @Test func historialNormalizaAdjuntosYConservaBloquesDeHerramienta() throws {
        let json = """
        {
          "id":"c3","title":"Propuesta","channel":"web",
          "created_at":"2026-07-01T10:00:00Z","updated_at":"2026-07-01T10:01:00Z",
          "pending_confirmation":{"tool_call_id":"confirm-1","name":"enviar_correo","args":{"to":"ana@example.com"}},
          "messages":[
            {
              "id":"m1","role":"user","tokens_in":0,"tokens_out":0,
              "created_at":"2026-07-01T10:00:01Z",
              "content":{"text":"Revísalo","attachments":[{"file_id":"f1","filename":"brief.pdf","mime":"application/pdf"}]},
              "tool_calls":[]
            },
            {
              "id":"m2","role":"assistant","content":"Listo","tokens_in":2,"tokens_out":1,
              "created_at":"2026-07-01T10:01:00Z",
              "tool_calls":[{"type":"tool_end","tool_call_id":"tc1","name":"create_pdf","ok":true,"blocks_version":1,"artifacts":[],"blocks":[{"type":"link","id":"l1","title":"Resultado","url":"https://example.com"}]}]
            }
          ]
        }
        """

        let detail = try APIClient.crearDecoder().decode(ConversationDetail.self, from: Data(json.utf8))
        #expect(detail.messages.count == 2)
        #expect(detail.messages[0].text == "Revísalo")
        #expect(detail.messages[0].attachments == [ChatAttachment(fileId: "f1", filename: "brief.pdf", mime: "application/pdf")])
        guard case .toolEnd(let callId, _, _, _, let version, let blocks, _) = detail.messages[1].toolCalls.first else {
            Issue.record("El historial debía conservar tool_end")
            return
        }
        #expect(callId == "tc1")
        #expect(version == 1)
        #expect(blocks.count == 1)
        #expect(detail.pendingConfirmation?.toolCallId == "confirm-1")
        #expect(detail.pendingConfirmation?.name == "enviar_correo")
        #expect(detail.pendingConfirmation?.args["to"] == .string("ana@example.com"))
    }

    @Test func decodificaLlamadaRealConEstadoAbierto() throws {
        let json = """
        {
          "id":"call-1","conversation_id":"phone-thread","direction":"outgoing",
          "from_e164":"+12025550100","to_e164":"+573001112233",
          "goal":"Confirmar la cita","status":"in_progress","confirmed_at":"2026-07-01T10:00:00Z",
          "started_at":"2026-07-01T10:00:05Z","ended_at":null,"duration_seconds":null,
          "error":null,"created_at":"2026-07-01T09:59:00Z","updated_at":"2026-07-01T10:00:05Z"
        }
        """
        let call = try APIClient.crearDecoder().decode(PhoneCallOut.self, from: Data(json.utf8))
        #expect(call.toE164 == "+573001112233")
        #expect(call.status == "in_progress")
        #expect(call.startedAt != nil)
    }

    // MARK: - Fechas ISO 8601

    @Test func decodificaFechaSinFraccionDeSegundo() throws {
        let json = #"{"id":"c1","title":null,"channel":"web","created_at":"2026-07-01T10:00:00Z"}"#
        let conversacion = try APIClient.crearDecoder().decode(Conversation.self, from: Data(json.utf8))
        let esperado = ISO8601DateFormatter().date(from: "2026-07-01T10:00:00Z")
        #expect(conversacion.createdAt == esperado)
    }

    @Test func decodificaFechaConFraccionDeSegundo() throws {
        let json = #"{"id":"c1","title":null,"channel":"web","created_at":"2026-07-01T10:00:00.123Z"}"#
        let conversacion = try APIClient.crearDecoder().decode(Conversation.self, from: Data(json.utf8))
        let formateador = ISO8601DateFormatter()
        formateador.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let esperado = formateador.date(from: "2026-07-01T10:00:00.123Z")
        #expect(conversacion.createdAt == esperado)
    }
}
