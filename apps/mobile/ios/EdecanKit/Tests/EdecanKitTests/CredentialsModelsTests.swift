import Testing
import Foundation
@testable import EdecanKit

/// `CredentialsOut`/`LLMCredentialsIn` decodifican/codifican EXACTAMENTE los
/// ejemplos de `docs/api.md` §"Rutas v3"; `SetupStatus`/`DetectLocalProviders`
/// decodifican el shape REAL de `apps/api/edecan_api/routers/setup.py`
/// (verificado contra el código fuente — distinto del ejemplo aspiracional
/// que trae `docs/api.md`, escrito antes de que ese router aterrizara).
struct CredentialsModelsTests {
    // MARK: - GET /v1/credentials

    @Test func decodificaCredencialesCompletas() throws {
        let json = """
        {
          "llm": {"kind": "claude_cli", "model_principal": null, "model_rapido": null, "base_url": null, "masked": null},
          "voice_stt": {"provider": "deepgram", "masked": "…9f2a"},
          "voice_tts": null,
          "images": null,
          "search": {"provider": "brave", "masked": "…7f3a"}
        }
        """
        let out = try JSONDecoder().decode(CredentialsOut.self, from: Data(json.utf8))
        #expect(out.llm?.kind == "claude_cli")
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

    // MARK: - PUT /v1/credentials/llm

    @Test func codificaLLMCredentialsInConValidatePorDefecto() throws {
        let payload = LLMCredentialsIn(kind: "anthropic", apiKey: "sk-ant-...")
        let data = try JSONEncoder().encode(payload)
        let obj = try #require(try JSONSerialization.jsonObject(with: data) as? [String: Any])
        #expect(obj["kind"] as? String == "anthropic")
        #expect(obj["api_key"] as? String == "sk-ant-...")
        // La clave en el wire es "validate" (alias del backend, `Field(alias:
        // "validate")`), NUNCA "validate_" — eso es un detalle interno de
        // Pydantic del lado Python que este cliente no debe filtrar.
        #expect(obj["validate"] as? Bool == true)
        #expect(obj["validate_"] == nil)
    }

    @Test func llmProviderKindMarcaCualesRequierenModoLocal() {
        #expect(LLMProviderKind.claudeCli.requiereModoLocal)
        #expect(LLMProviderKind.codexCli.requiereModoLocal)
        #expect(LLMProviderKind.ollama.requiereModoLocal)
        #expect(!LLMProviderKind.anthropic.requiereModoLocal)
        #expect(!LLMProviderKind.openaiCompat.requiereModoLocal)
        #expect(!LLMProviderKind.vertex.requiereModoLocal)
        #expect(LLMProviderKind.allCases.count == 6)
    }

    // MARK: - GET /v1/setup/status (shape real de setup.py, no el de docs/api.md)

    @Test func decodificaSetupStatus() throws {
        let json = #"{"local_mode": true, "llm_configured": false, "version": "0.4.0"}"#
        let status = try JSONDecoder().decode(SetupStatus.self, from: Data(json.utf8))
        #expect(status.localMode == true)
        #expect(status.llmConfigured == false)
        #expect(status.version == "0.4.0")
    }

    // MARK: - GET /v1/setup/detect

    @Test func decodificaDeteccionEnModoLocal() throws {
        let json = """
        {
          "local_mode": true,
          "claude_cli": {"installed": true, "path": "/usr/local/bin/claude", "version": "1.4.2"},
          "codex_cli": {"installed": false, "path": null, "version": null},
          "ollama": {"running": true, "base_url": "http://localhost:11434", "models": ["llama3.1", "qwen2.5-coder"]}
        }
        """
        let deteccion = try JSONDecoder().decode(DetectLocalProviders.self, from: Data(json.utf8))
        #expect(deteccion.localMode == true)
        #expect(deteccion.claudeCli.installed == true)
        #expect(deteccion.claudeCli.path == "/usr/local/bin/claude")
        #expect(deteccion.codexCli.installed == false)
        #expect(deteccion.ollama.models == ["llama3.1", "qwen2.5-coder"])
    }

    @Test func decodificaDeteccionVaciaFueraDeModoLocal() throws {
        let json = """
        {
          "local_mode": false,
          "claude_cli": {"installed": false, "path": null, "version": null},
          "codex_cli": {"installed": false, "path": null, "version": null},
          "ollama": {"running": false, "base_url": "http://localhost:11434", "models": []}
        }
        """
        let deteccion = try JSONDecoder().decode(DetectLocalProviders.self, from: Data(json.utf8))
        #expect(deteccion.localMode == false)
        #expect(deteccion.ollama.running == false)
        #expect(deteccion.ollama.models.isEmpty)
    }
}
