import EdecanKit
import Foundation
import Observation

/// Estado unico del asistente conversacional. Texto, voz, adjuntos y bloques
/// ricos comparten la misma instancia y el mismo `conversationId`.
@MainActor
@Observable
final class ChatViewModel {
    struct Mensaje: Identifiable, Equatable {
        enum Rol: Equatable { case usuario, asistente }

        let id: String
        var rol: Rol
        var texto: String
        var enProgreso: Bool = false
        var artefactos: [ArtifactRef] = []
        var bloques: [ChatBlock] = []
        var adjuntos: [ChatAttachment] = []
        /// UUID estable del intento lógico. Solo sobrevive mientras el envío
        /// puede reintentarse; nunca se reconstruye para mensajes históricos.
        var logicalAttempt: LogicalChatAttempt?
        /// Mantiene visible y reintentable una orden cuyo transporte fallo.
        var falloEnvio = false

        init(
            id: String = UUID().uuidString,
            rol: Rol,
            texto: String,
            enProgreso: Bool = false,
            artefactos: [ArtifactRef] = [],
            bloques: [ChatBlock] = [],
            adjuntos: [ChatAttachment] = [],
            logicalAttempt: LogicalChatAttempt? = nil,
            falloEnvio: Bool = false
        ) {
            self.id = id
            self.rol = rol
            self.texto = texto
            self.enProgreso = enProgreso
            self.artefactos = artefactos
            self.bloques = bloques
            self.adjuntos = adjuntos
            self.logicalAttempt = logicalAttempt
            self.falloEnvio = falloEnvio
        }
    }

    struct HerramientaActiva: Equatable {
        let toolCallId: String?
        let nombre: String
    }

    struct ConfirmacionPendiente: Identifiable, Equatable {
        var id: String { toolCallId }
        let toolCallId: String
        let nombre: String
        let args: [String: JSONValue]
        let indiceMensaje: Int?
    }

    private(set) var mensajes: [Mensaje] = []
    private(set) var conversaciones: [Conversation] = []
    private(set) var conversacionId: String?
    private(set) var herramientaActiva: HerramientaActiva?
    private(set) var enviando = false
    private(set) var cargandoHistorial = false
    private(set) var cargandoConversacion = false
    private(set) var confirmacionPendiente: ConfirmacionPendiente?
    var errorMensaje: String?

    private let sseClient = SSEClient()
    private var inicializado = false

    var tituloConversacionActual: String {
        guard let conversacionId,
              let conversation = conversaciones.first(where: { $0.id == conversacionId })
        else { return "Nuevo chat" }
        let title = conversation.title?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return title.isEmpty ? "Conversación" : title
    }

    var ultimaRespuestaDelAsistente: String? {
        mensajes.last(where: { $0.rol == .asistente })?.texto
    }

    func iniciar(client: APIClient, preferredConversationId: String?) async {
        guard !inicializado else { return }
        inicializado = true
        await cargarConversaciones(client: client)

        if let preferredConversationId,
           conversaciones.contains(where: { $0.id == preferredConversationId }) {
            await abrirConversacion(id: preferredConversationId, client: client)
        } else if let first = conversaciones.first {
            await abrirConversacion(id: first.id, client: client)
        }
    }

    func cargarConversaciones(client: APIClient) async {
        cargandoHistorial = true
        defer { cargandoHistorial = false }
        do {
            // Las llamadas tienen su feed propio en Actividad. El selector del
            // chat solo ofrece hilos donde escribir de forma natural.
            conversaciones = try await client.listarConversaciones()
                .filter { $0.channel != "phone" }
        } catch {
            errorMensaje = error.localizedDescription
        }
    }

    func abrirConversacion(id: String, client: APIClient) async {
        guard !enviando else { return }
        cargandoConversacion = true
        errorMensaje = nil
        defer { cargandoConversacion = false }
        do {
            let detail = try await client.obtenerConversacion(id: id)
            conversacionId = detail.id
            mensajes = Self.mensajesDesdeHistorial(detail.messages)
            herramientaActiva = nil
            restaurarConfirmacion(detail.pendingConfirmation)
        } catch {
            errorMensaje = error.localizedDescription
        }
    }

    func nuevaConversacion() {
        guard !enviando, confirmacionPendiente == nil else { return }
        conversacionId = nil
        mensajes = []
        herramientaActiva = nil
        errorMensaje = nil
    }

    @discardableResult
    func enviar(
        texto: String,
        adjuntos: [ChatAttachment] = [],
        alAceptar: (() -> Void)? = nil,
        client: APIClient
    ) async -> Bool {
        let textoLimpio = texto.trimmingCharacters(in: .whitespacesAndNewlines)
        guard (!textoLimpio.isEmpty || !adjuntos.isEmpty),
              textoLimpio.count <= 50_000,
              adjuntos.count <= 10,
              !enviando,
              confirmacionPendiente == nil
        else {
            if textoLimpio.count > 50_000 { errorMensaje = "El mensaje es demasiado largo." }
            return false
        }

        let mensaje = Mensaje(
            rol: .usuario,
            texto: textoLimpio,
            adjuntos: adjuntos,
            logicalAttempt: LogicalChatAttempt()
        )
        mensajes.append(mensaje)
        // La orden ya tiene una burbuja optimista y un intento lógico estable.
        // La vista puede vaciar su composer ahora; si el transporte falla, la
        // misma burbuja ofrece Reintentar sin duplicar el texto en el input.
        alAceptar?()
        return await ejecutarEnvio(mensajeId: mensaje.id, client: client)
    }

    @discardableResult
    func reintentar(mensajeId: String, client: APIClient) async -> Bool {
        guard let index = mensajes.firstIndex(where: {
            $0.id == mensajeId && $0.rol == .usuario && $0.falloEnvio
        }) else { return false }
        mensajes[index].falloEnvio = false
        return await ejecutarEnvio(mensajeId: mensajeId, client: client)
    }

    private func ejecutarEnvio(mensajeId: String, client: APIClient) async -> Bool {
        guard !enviando,
              confirmacionPendiente == nil,
              let indiceUsuario = mensajes.firstIndex(where: { $0.id == mensajeId })
        else { return false }
        let texto = mensajes[indiceUsuario].texto
        let attachmentIds = mensajes[indiceUsuario].adjuntos.map(\.fileId)
        let logicalAttempt = mensajes[indiceUsuario].logicalAttempt ?? LogicalChatAttempt()
        mensajes[indiceUsuario].logicalAttempt = logicalAttempt

        enviando = true
        errorMensaje = nil
        herramientaActiva = nil
        defer {
            enviando = false
            herramientaActiva = nil
        }

        var indiceRespuestaCreada: Int?
        do {
            let conversationId = try await asegurarConversacion(client: client)
            let indiceRespuesta = mensajes.count
            indiceRespuestaCreada = indiceRespuesta
            mensajes.append(Mensaje(rol: .asistente, texto: "", enProgreso: true))
            defer {
                if mensajes.indices.contains(indiceRespuesta) {
                    mensajes[indiceRespuesta].enProgreso = false
                }
            }

            struct Body: Encodable {
                let text: String
                let attachments: [String]
            }
            try await consumirStreamConRefresh(
                client: client,
                path: "/v1/conversations/\(conversationId)/messages",
                body: Body(text: texto, attachments: attachmentIds),
                idempotencyKey: logicalAttempt.idempotencyKey,
                indiceRespuesta: indiceRespuesta
            )
            if errorMensaje != nil {
                if mensajes.indices.contains(indiceUsuario) { mensajes[indiceUsuario].falloEnvio = true }
                removerRespuestaFallida(indiceRespuesta)
                return false
            }
            if mensajes.indices.contains(indiceUsuario) {
                mensajes[indiceUsuario].falloEnvio = false
                mensajes[indiceUsuario].logicalAttempt = nil
            }
            await cargarConversaciones(client: client)
            return true
        } catch {
            if mensajes.indices.contains(indiceUsuario) { mensajes[indiceUsuario].falloEnvio = true }
            if let responseIndex = indiceRespuestaCreada {
                removerRespuestaFallida(responseIndex)
            }
            errorMensaje = error.localizedDescription
            return false
        }
    }

    private func asegurarConversacion(client: APIClient) async throws -> String {
        if let conversacionId { return conversacionId }
        let conversation = try await client.crearConversacion(titulo: nil)
        conversacionId = conversation.id
        conversaciones.insert(conversation, at: 0)
        return conversation.id
    }

    func resolverConfirmacion(aprobado: Bool, client: APIClient) async {
        guard let pendiente = confirmacionPendiente, let conversacionId else { return }
        let indiceRespuesta: Int
        if let existente = pendiente.indiceMensaje, mensajes.indices.contains(existente) {
            indiceRespuesta = existente
            mensajes[indiceRespuesta].enProgreso = true
        } else {
            indiceRespuesta = mensajes.count
            mensajes.append(Mensaje(rol: .asistente, texto: "", enProgreso: true))
        }
        let pendienteDuranteEnvio = ConfirmacionPendiente(
            toolCallId: pendiente.toolCallId,
            nombre: pendiente.nombre,
            args: pendiente.args,
            indiceMensaje: indiceRespuesta
        )
        confirmacionPendiente = pendienteDuranteEnvio

        enviando = true
        errorMensaje = nil
        defer {
            enviando = false
            herramientaActiva = nil
            if mensajes.indices.contains(indiceRespuesta) {
                mensajes[indiceRespuesta].enProgreso = false
            }
        }

        do {
            struct Body: Encodable {
                let toolCallId: String
                let approved: Bool
                enum CodingKeys: String, CodingKey {
                    case toolCallId = "tool_call_id"
                    case approved
                }
            }
            try await consumirStreamConRefresh(
                client: client,
                path: "/v1/conversations/\(conversacionId)/confirm",
                body: Body(toolCallId: pendiente.toolCallId, approved: aprobado),
                idempotencyKey: nil,
                indiceRespuesta: indiceRespuesta
            )
            confirmacionPendiente = nil
            await cargarConversaciones(client: client)
        } catch {
            // Resultado ambiguo: conserva la tarjeta hasta consultar la
            // fuente de verdad. Si el backend ya la consumió, el GET devuelve
            // nil y el historial refleja el resultado; si sigue pendiente,
            // vuelve a mostrar exactamente la misma confirmación pública.
            confirmacionPendiente = pendienteDuranteEnvio
            errorMensaje = error.localizedDescription
            await recargarTrasConfirmacionAmbigua(
                conversationId: conversacionId,
                pendienteOriginal: pendienteDuranteEnvio,
                client: client
            )
        }
    }

    private func construirPeticionSSE<Cuerpo: Encodable>(
        client: APIClient, path: String, body: Cuerpo, idempotencyKey: UUID?
    ) async throws -> URLRequest {
        let url = try await client.urlCompleta(path)
        let token = try await client.tokenDeAccesoValido()
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        if let idempotencyKey {
            request.setValue(
                LogicalChatAttempt(idempotencyKey: idempotencyKey).headerValue,
                forHTTPHeaderField: "Idempotency-Key"
            )
        }
        request.httpBody = try JSONEncoder().encode(body)
        return request
    }

    private func consumirStreamConRefresh<Cuerpo: Encodable>(
        client: APIClient,
        path: String,
        body: Cuerpo,
        idempotencyKey: UUID?,
        indiceRespuesta: Int
    ) async throws {
        var yaRefresco = false
        while true {
            let request = try await construirPeticionSSE(
                client: client,
                path: path,
                body: body,
                idempotencyKey: idempotencyKey
            )
            do {
                for try await evento in sseClient.stream(request) {
                    aplicar(evento, indiceRespuesta: indiceRespuesta)
                }
                return
            } catch SSEClient.SSEError.servidor(let status) where status == 401 && !yaRefresco {
                yaRefresco = true
                try await client.refrescar()
            }
        }
    }

    private func removerRespuestaFallida(_ index: Int) {
        guard mensajes.indices.contains(index), mensajes[index].rol == .asistente else { return }
        mensajes.remove(at: index)
    }

    private func restaurarConfirmacion(_ pending: PendingConfirmationOut?) {
        guard let pending else {
            confirmacionPendiente = nil
            return
        }
        confirmacionPendiente = ConfirmacionPendiente(
            toolCallId: pending.toolCallId,
            nombre: pending.name,
            args: pending.args,
            indiceMensaje: nil
        )
    }

    private func recargarTrasConfirmacionAmbigua(
        conversationId: String,
        pendienteOriginal: ConfirmacionPendiente,
        client: APIClient
    ) async {
        do {
            let detail = try await client.obtenerConversacion(id: conversationId)
            guard self.conversacionId == conversationId else { return }
            mensajes = Self.mensajesDesdeHistorial(detail.messages)
            restaurarConfirmacion(detail.pendingConfirmation)
        } catch {
            // Sin una lectura confirmada se conserva la tarjeta original. Un
            // segundo POST es seguro: Redis ya consumido responderá 409 y no
            // vuelve a ejecutar; si no salió, todavía permite decidir.
            confirmacionPendiente = pendienteOriginal
            errorMensaje = "No se pudo verificar el resultado. \(error.localizedDescription)"
        }
    }

    private func aplicar(_ evento: ChatEvent, indiceRespuesta: Int) {
        guard mensajes.indices.contains(indiceRespuesta) else { return }
        switch evento {
        case .textDelta(let texto):
            mensajes[indiceRespuesta].texto += texto
        case .toolStart(let toolCallId, let nombre, _):
            herramientaActiva = HerramientaActiva(toolCallId: toolCallId, nombre: nombre)
        case .toolEnd(let toolCallId, _, _, let artefactos, let blocksVersion, let bloques):
            if herramientaActiva?.toolCallId == nil
                || toolCallId == nil
                || herramientaActiva?.toolCallId == toolCallId {
                herramientaActiva = nil
            }
            for artefacto in artefactos
            where !mensajes[indiceRespuesta].artefactos.contains(where: { $0.fileId == artefacto.fileId }) {
                mensajes[indiceRespuesta].artefactos.append(artefacto)
            }
            if blocksVersion == 1 {
                for bloque in bloques where !mensajes[indiceRespuesta].bloques.contains(bloque) {
                    mensajes[indiceRespuesta].bloques.append(bloque)
                }
            }
        case .confirmationRequired(let toolCallId, let nombre, let args):
            herramientaActiva = nil
            confirmacionPendiente = ConfirmacionPendiente(
                toolCallId: toolCallId,
                nombre: nombre,
                args: args,
                indiceMensaje: indiceRespuesta
            )
        case .done:
            break
        case .error(let mensaje):
            errorMensaje = mensaje
        case .unknown:
            break
        }
    }

    private static func mensajesDesdeHistorial(_ rows: [ConversationMessage]) -> [Mensaje] {
        rows.compactMap { row in
            let role: Mensaje.Rol
            switch row.role {
            case "user": role = .usuario
            case "assistant": role = .asistente
            default: return nil
            }

            var artifacts: [ArtifactRef] = []
            var blocks: [ChatBlock] = []
            for event in row.toolCalls {
                guard case .toolEnd(_, _, _, let eventArtifacts, let version, let eventBlocks) = event
                else { continue }
                for artifact in eventArtifacts
                where !artifacts.contains(where: { $0.fileId == artifact.fileId }) {
                    artifacts.append(artifact)
                }
                if version == 1 {
                    for block in eventBlocks where !blocks.contains(block) { blocks.append(block) }
                }
            }
            return Mensaje(
                id: row.id,
                rol: role,
                texto: row.text,
                artefactos: artifacts,
                bloques: blocks,
                adjuntos: row.attachments
            )
        }
    }
}
