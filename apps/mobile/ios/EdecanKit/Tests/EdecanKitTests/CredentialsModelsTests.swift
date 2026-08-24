import Testing
import Foundation
@testable import EdecanKit

/// Contrato de lectura del estado de capacidades administradas.
struct CredentialsModelsTests {
    // MARK: - GET /v1/credentials

    @Test func decodificaCredencialesCompletas() throws {
        let json = """
        {
          "llm": {"kind": "workers_ai", "model_principal": "@cf/zai-org/glm-4.7-flash", "model_rapido": "@cf/zai-org/glm-4.7-flash", "base_url": null, "masked": null},
          "voice_stt": {"provider": "deepgram", "masked": "…9f2a"},
          "voice_tts": null,
          "images": null,
          "search": {"provider": "brave", "masked": "…7f3a"}
        }
        """
        let out = try JSONDecoder().decode(CredentialsOut.self, from: Data(json.utf8))
        #expect(out.llm?.kind == "workers_ai")
        #expect(out.llm?.masked == nil)
        #expect(out.voiceStt?.provider == "deepgram")
        #expect(out.voiceStt?.masked == "…9f2a")
        #expect(out.voiceTts == nil)
        #expect(out.images == nil)
        #expect(out.search?.provider == "brave")
    }

    @Test func decodificaCredencialesTodasEnNil() throws {
        let json = #"{"llm": null, "voice_stt": null, "voice_tts": null, "images": null, "search": null}"#
        let out = try JSONDecoder().decode(CredentialsOut.self, from: Data(json.utf8))
        #expect(out.llm == nil)
        #expect(out.search == nil)
    }

    // MARK: - GET /v1/setup/status (shape real de setup.py, no el de docs/api.md)

    @Test func decodificaSetupStatus() throws {
        let json = #"{"local_mode": true, "llm_configured": false, "version": "0.4.0"}"#
        let status = try JSONDecoder().decode(SetupStatus.self, from: Data(json.utf8))
        #expect(status.localMode == true)
        #expect(status.llmConfigured == false)
        #expect(status.version == "0.4.0")
    }

}
