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
