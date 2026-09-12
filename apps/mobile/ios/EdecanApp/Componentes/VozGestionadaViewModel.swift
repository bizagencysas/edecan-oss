import EdecanKit
import ElevenLabs
import Foundation
import Observation

/// Voz GESTIONADA por Speech Engine de ElevenLabs (`docs/speech-engine.md`).
///
/// El móvil habla con ElevenLabs por WebRTC usando SOLO el SDK oficial
/// (`ElevenLabs.startConversation(conversationToken:)`): el micrófono, el eco,
/// la detección de turno, las interrupciones y el audio de salida los maneja
/// el proveedor. Este view model NO toca `VozRecorderContinuo`,
/// `ReproductorPCMStream` ni el pipeline Deepgram/PCM — esos son del modo
/// legacy (`LlamadaViewModel`) y nunca corren a la vez.
///
/// La conversación es UNA: la sesión se ata a la MISMA `conversation_id`
/// canónica del chat, así que al terminar la voz el chat muestra lo hablado
/// y el texto posterior hereda TODO el contexto.
@MainActor
@Observable
final class VozGestionadaViewModel {
    enum Estado: Equatable {
        case inactivo
        case activando
        case activo
        case error(String)
    }

    private(set) var estado: Estado = .inactivo
    /// Último enunciado transcrito del usuario (en vivo mientras habla).
    private(set) var textoUsuario: String = ""
    /// Respuesta del agente acumulada en este turno.
    private(set) var textoAgente: String = ""
    /// Nivel de voz (0…1) del micrófono, para la onda del orb.
    private(set) var nivelEntrada: Double = 0
    /// Lo que el agente está haciendo (`listening`/`thinking`/`speaking`).
    private(set) var actividadAgente: String = "escuchando"

    private var conversation: ConversationGestionada?
    private var sessionId: String?
    /// Conversación canónica del chat a la que se ató la sesión.
    private(set) var conversationId: String?
    /// Ciclo de arranque: `detener` lo invalida para que una conexión que
    /// todavía estaba provisionándose NO se active después del cierre.
    private var ciclo = 0

    var estaActivo: Bool { estado == .activo }
    private var esError: Bool {
        if case .error = estado { return true }
        return false
    }

    /// Arranca la voz gestionada sobre la conversación canónica.
    ///
    /// `conversationId` nil = el servidor asegura la conversación principal
    /// antes de provisionar. Si las preferencias no están activadas (o no hay
    /// credencial del proveedor pagado conectada), NO se hace nada y se
    /// reporta el error para que la UI muestre la hoja de configuración.
    func iniciar(client: APIClient?, conversationId: String? = nil) async {
        guard estado == .inactivo || esError else { return }
        guard let client else {
            estado = .error("No hay sesión activa.")
            return
        }
        estado = .activando
        ciclo += 1
        let cicloActual = ciclo
        do {
            let preferencias = try await client.preferenciasVozGestionada()
            guard cicloActual == ciclo else { return }
            guard preferencias.preference?.enabled == true, preferencias.credentialConnected else {
                estado = .error("Configúrala primero")
                return
            }
            // Consentimiento pagado explícito POR sesión: los minutos de
            // Speech Engine se facturan a la API key de ElevenLabs del tenant.
            let sesion = try await client.crearSesionVozGestionada(
                conversationId: conversationId, paidConsent: true
            )
            guard cicloActual == ciclo else {
                // Se cerró mientras provisionaba: limpia la sesión remota.
                try? await client.terminarSesionVozGestionada(sessionId: sesion.sessionId)
                return
            }
            sessionId = sesion.sessionId
            self.conversationId = sesion.conversationId
            textoUsuario = ""
            textoAgente = ""

            let config = ConversationConfig(
                onAgentResponse: { [weak self] text, _ in
                    Task { @MainActor [weak self] in
                        self?.textoAgente += text
                    }
                },
                onUserTranscript: { [weak self] text, _ in
                    Task { @MainActor [weak self] in
                        self?.textoUsuario = text
                        self?.textoAgente = ""
                    }
                },
                onVadScore: { [weak self] score in
                    Task { @MainActor [weak self] in
                        self?.nivelEntrada = max(0, min(1, score))
                    }
                }
            )
            let conversation = try await ElevenLabs.startConversation(
                conversationToken: sesion.conversationToken,
                config: config,
                onDisconnect: { [weak self] _ in
                    Task { @MainActor [weak self] in
                        self?.estado = .inactivo
                        self?.conversation = nil
                    }
                }
            )
            guard cicloActual == ciclo else {
                await conversation.endConversation()
                return
            }
            self.conversation = conversation
            estado = .activo
            actualizarActividad(conversation.agentState)
        } catch {
            guard cicloActual == ciclo else { return }
            estado = .error(mensajeLegible(error))
            await limpiarSesionRemota(client: client)
        }
    }

    /// X / salir: detiene micrófono y audio del SDK y cierra la sesión del
    /// proveedor (idempotente en el servidor). El chat NO se reinicia: lo
    /// hablado ya vive en la conversación canónica.
    func detener(client: APIClient?) async {
        ciclo += 1
        let conversation = self.conversation
        self.conversation = nil
        await conversation?.endConversation()
        await limpiarSesionRemota(client: client)
        textoUsuario = ""
        textoAgente = ""
        nivelEntrada = 0
        estado = .inactivo
    }

    /// Best-effort: si el arranque falló a mitad de provisionamiento, el
    /// servidor limpia el engine del proveedor (end idempotente + expiración).
    private func limpiarSesionRemota(client: APIClient?) async {
        guard let sessionId, let client else { return }
        self.sessionId = nil
        try? await client.terminarSesionVozGestionada(sessionId: sessionId)
    }

    private func actualizarActividad(_ state: ElevenLabs.AgentState) {
        switch state {
        case .listening: actividadAgente = "escuchando"
        case .thinking: actividadAgente = "pensando"
        case .speaking: actividadAgente = "hablando"
        default: actividadAgente = "escuchando"
        }
    }

    private func mensajeLegible(_ error: Error) -> String {
        if let apiError = error as? APIClient.APIError, case .servidor(let status, let mensaje) = apiError {
            if status == 403 {
                return "La voz gestionada no está activada para tu cuenta. Configúrala primero."
            }
            if mensaje.contains("paid_consent") || mensaje.contains("pagado") {
                return "Falta tu consentimiento para el proveedor pagado de voz."
            }
        }
        return "No se pudo iniciar la voz gestionada."
    }
}