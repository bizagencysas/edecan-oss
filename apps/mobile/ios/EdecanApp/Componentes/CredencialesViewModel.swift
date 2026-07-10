import Foundation
import Observation
import EdecanKit

/// Estado de conexión de LLM/voz/imágenes/búsqueda + el formulario de
/// "Conectar LLM" de ``PerfilView`` (`GET /v1/credentials`, `GET
/// /v1/setup/status`, `GET /v1/setup/detect`, `PUT /v1/credentials/llm` —
/// `ARCHITECTURE.md` §12.b/§12.c/§12.d).
@MainActor
@Observable
final class CredencialesViewModel {
    private(set) var credenciales: CredentialsOut?
    private(set) var setup: SetupStatus?
    private(set) var deteccion: DetectLocalProviders?
    private(set) var cargando = false
    var errorMensaje: String?

    // MARK: - Formulario "Conectar LLM" ("pegar y validar",
    // `DIRECCION_ACTUAL.md` "Principio de UX no negociable")

    var kindSeleccionado: LLMProviderKind = .anthropic
    var apiKey: String = ""
    var baseURL: String = ""
    var modeloPrincipal: String = ""
    private(set) var guardando = false
    var errorGuardado: String?
    var guardadoExitoso = false

    /// `true` si el servidor corre local (`EDECAN_LOCAL_MODE=true`) — solo
    /// entonces tiene sentido ofrecer `claude_cli`/`codex_cli`/`ollama`
    /// (`ARCHITECTURE.md` §12.c/§12.d).
    var modoLocal: Bool { setup?.localMode ?? false }

    func cargar(client: APIClient?) async {
        guard let client else {
            errorMensaje = "No hay sesión activa."
            return
        }
        cargando = true
        errorMensaje = nil
        defer { cargando = false }

        async let credencialesTarea = client.credenciales()
        async let setupTarea = client.setupStatus()
        do {
            let (credencialesResultado, setupResultado) = try await (credencialesTarea, setupTarea)
            credenciales = credencialesResultado
            setup = setupResultado
        } catch {
            errorMensaje = error.localizedDescription
            return
        }
        // La autodetección es un extra de un clic (`ARCHITECTURE.md` §12.d) —
        // si falla o el router de setup no monta `/detect` todavía, no debe
        // tapar el resto de la pantalla (que ya cargó bien arriba).
        deteccion = try? await client.setupDetect()
    }

    /// Atajo de un clic: usa un proveedor ya detectado (`claude_cli`/`codex_cli`/`ollama`)
    /// sin pedir ninguna credencial (`DIRECCION_ACTUAL.md` "configuración de
    /// pocos clicks").
    func conectarDetectado(_ kind: LLMProviderKind, modelo: String? = nil, client: APIClient?) async {
        kindSeleccionado = kind
        apiKey = ""
        baseURL = ""
        modeloPrincipal = modelo ?? ""
        await conectarLLM(client: client)
    }

    /// `PUT /v1/credentials/llm` con lo que haya en el formulario —
    /// `validate: true` (default del backend): si el proveedor rechaza la
    /// credencial, `errorGuardado` queda con el detalle EXACTO que dio,
    /// listo para mostrar tal cual.
    func conectarLLM(client: APIClient?) async {
        guard let client else {
            errorGuardado = "No hay sesión activa."
            return
        }
        guardando = true
        errorGuardado = nil
        guardadoExitoso = false
        defer { guardando = false }

        let payload = LLMCredentialsIn(
            kind: kindSeleccionado.rawValue,
            apiKey: apiKey.trimmingCharacters(in: .whitespaces).isEmpty ? nil : apiKey,
            baseURL: baseURL.trimmingCharacters(in: .whitespaces).isEmpty ? nil : baseURL,
            modelPrincipal: modeloPrincipal.trimmingCharacters(in: .whitespaces).isEmpty ? nil : modeloPrincipal
        )
        do {
            try await client.conectarLLM(payload)
            guardadoExitoso = true
            apiKey = ""
            credenciales = try await client.credenciales()
        } catch {
            errorGuardado = error.localizedDescription
        }
    }

    func desconectarLLM(client: APIClient?) async {
        guard let client else { return }
        do {
            try await client.desconectarLLM()
            credenciales = try await client.credenciales()
        } catch {
            errorMensaje = error.localizedDescription
        }
    }
}
