import Foundation
import Testing
@testable import EdecanKit

@Suite struct ConnectorModelsTests {
    @Test func cuentaSinHasAccessTokenNoSeMarcaConectada() throws {
        let json = """
        {"id":"a1","connector_key":"linkedin","external_account_id":"x","display_name":"Test"}
        """
        let account = try JSONDecoder().decode(ConnectorAccountSummary.self, from: Data(json.utf8))
        #expect(account.hasAccessToken == nil)
        #expect(account.conectada == false)
        #expect(account.esPendiente == true)
    }

    @Test func cuentaActiveSinTokenNoSeMarcaConectada() throws {
        let json = """
        {"id":"a1","connector_key":"linkedin","status":"active","display_name":"Test"}
        """
        let account = try JSONDecoder().decode(ConnectorAccountSummary.self, from: Data(json.utf8))
        #expect(account.conectada == false)
        #expect(account.esPendiente == true)
    }

    @Test func cuentaConHasAccessTokenTrueEsConectada() throws {
        let json = """
        {"id":"a1","connector_key":"linkedin","status":"active","has_access_token":true,"display_name":"Test"}
        """
        let account = try JSONDecoder().decode(ConnectorAccountSummary.self, from: Data(json.utf8))
        #expect(account.hasAccessToken == true)
        #expect(account.conectada == true)
        #expect(account.esPendiente == false)
    }

    @Test func cuentaConHasAccessTokenFalseEsPendiente() throws {
        let json = """
        {"id":"a1","connector_key":"linkedin","status":"active","has_access_token":false,"display_name":"Test"}
        """
        let account = try JSONDecoder().decode(ConnectorAccountSummary.self, from: Data(json.utf8))
        #expect(account.conectada == false)
        #expect(account.esPendiente == true)
    }

    @Test func mcpSinEstadoNiHealthNoInventaVerde() throws {
        let json = """
        {"nombre":"demo","transporte":"http","url":"https://example.com/mcp"}
        """
        let server = try JSONDecoder().decode(MCPServerSummary.self, from: Data(json.utf8))
        #expect(server.estado.isEmpty)
        #expect(server.etiquetaSalud == "Sin datos")
        #expect(server.colorSalud == "secondary")
        #expect(server.estaConectado == false)
    }
}
