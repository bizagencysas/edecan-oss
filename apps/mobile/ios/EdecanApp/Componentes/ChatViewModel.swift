import EdecanKit
import Foundation
import Observation

/// Estado unico del asistente conversacional. Texto, voz, adjuntos y bloques
/// ricos comparten la misma instancia y el mismo `conversationId`.
@MainActor
@Observable
final class ChatViewModel {
    struct Trabajo: Equatable {
        struct Paso: Identifiable, Equatable {
            enum Estado: Equatable { case ejecutando, completado, error }

            let id: String
            let nombre: String
            var estado: Estado
            var detalle: String?
            var segundos: Int
        }

        let iniciadoEn: Date
        var terminadoEn: Date?
        var pasos: [Paso]
        var missionId: String? = nil
        var estadoMision: String? = nil
        var errorMision: String? = nil

        var estaActivo: Bool {
            if let estadoMision { return estadoMision == "planning" || estadoMision == "running" }
            return terminadoEn == nil
        }
        var tituloEstado: String {
            switch estadoMision {
            case "waiting_confirmation": return "Necesita tu aprobación"
            case "error": return "El trabajo encontró un error"
            case "cancelled": return "Trabajo cancelado"
            case "done": return "Trabajo completado"
            default: return estaActivo ? "Edecán está trabajando" : "Trabajo completado"
            }
        }
        var segundosTranscurridos: Int {
            if let reportado = pasos.last?.segundos, reportado > 0 { return reportado }
            return max(0, Int((terminadoEn ?? Date()).timeIntervalSince(iniciadoEn)))
        }

        mutating func iniciar(toolCallId: String?, nombre: String) {
            let id = toolCallId ?? "\(nombre)-\(pasos.count)"
            if pasos.contains(where: { $0.id == id }) { return }
            pasos.append(Paso(id: id, nombre: nombre, estado: .ejecutando, segundos: 0))
        }

        mutating func actualizar(
            toolCallId: String?, nombre: String, segundos: Int, detalle: String
        ) {
            guard let index = indice(toolCallId: toolCallId, nombre: nombre) else {
                iniciar(toolCallId: toolCallId, nombre: nombre)
                actualizar(
                    toolCallId: toolCallId,
                    nombre: nombre,
                    segundos: segundos,
                    detalle: detalle
                )
                return
            }
            pasos[index].segundos = max(pasos[index].segundos, segundos)
            pasos[index].detalle = detalle
        }

        mutating func completar(
            toolCallId: String?, nombre: String, resultado: String
        ) {
            guard let index = indice(toolCallId: toolCallId, nombre: nombre) else { return }
            let fallo = resultado.trimmingCharacters(in: .whitespacesAndNewlines)
                .lowercased().hasPrefix("error:")
            pasos[index].estado = fallo ? .error : .completado
            let limpio = resultado.trimmingCharacters(in: .whitespacesAndNewlines)
            pasos[index].detalle = limpio.isEmpty ? nil : limpio
        }

        mutating func finalizar() {
            guard missionId == nil else { return }
            terminadoEn = Date()
        }

        mutating func vincularMision(_ missionId: String) {
            self.missionId = missionId
            estadoMision = "planning"
            terminadoEn = nil
        }

        mutating func actualizarMision(_ detail: MissionDetailOut) {
            missionId = detail.mission.id
            estadoMision = detail.mission.status
            errorMision = detail.mission.error
            for step in detail.steps {
                let id = "mission:\(step.seq)"
                let state: Paso.Estado
                switch step.status {
                case "done", "skipped": state = .completado
                case "error": state = .error
                default: state = .ejecutando
                }
                let item = Paso(
                    id: id,
                    nombre: step.instruccion,
                    estado: state,
                    detalle: step.resultado,
                    segundos: 0
                )
                if let index = pasos.firstIndex(where: { $0.id == id }) { pasos[index] = item }
                else { pasos.append(item) }
            }
            if !detail.mission.estaActiva { terminadoEn = Date() }
        }

        private func indice(toolCallId: String?, nombre: String) -> Int? {
            if let toolCallId, let exacto = pasos.lastIndex(where: { $0.id == toolCallId }) {
                return exacto
            }
            return pasos.lastIndex(where: { $0.nombre == nombre && $0.estado == .ejecutando })
        }
    }

    struct Mensaje: Identifiable, Equatable {
        enum Rol: Equatable { case usuario, asistente }

        let id: String
        var rol: Rol
        var texto: String
        /// Solo vive en memoria mientras el intento puede reintentarse. La UI
        /// muestra `texto`, ya redactado, y nunca persiste este valor crudo.
        var textoTransporte: String? = nil
        var enProgreso: Bool = false
        var artefactos: [ArtifactRef] = []
        var bloques: [ChatBlock] = []
        var adjuntos: [ChatAttachment] = []
        var trabajo: Trabajo?
        /// UUID estable del intento lógico. Solo sobrevive mientras el envío
        /// puede reintentarse; nunca se reconstruye para mensajes históricos.
        var logicalAttempt: LogicalChatAttempt?
        /// Mantiene visible y reintentable una orden cuyo transporte fallo.
        var falloEnvio = false

        init(
            id: String = UUID().uuidString,
            rol: Rol,
            texto: String,
            textoTransporte: String? = nil,
            enProgreso: Bool = false,
            artefactos: [ArtifactRef] = [],
            bloques: [ChatBlock] = [],
            adjuntos: [ChatAttachment] = [],
            trabajo: Trabajo? = nil,
            logicalAttempt: LogicalChatAttempt? = nil,
            falloEnvio: Bool = false
        ) {
            self.id = id
            self.rol = rol
            self.texto = texto
            self.textoTransporte = textoTransporte
            self.enProgreso = enProgreso
            self.artefactos = artefactos
            self.bloques = bloques
            self.adjuntos = adjuntos
            self.trabajo = trabajo
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
    private var seguimientoMisiones: [String: Task<Void, Never>] = [:]

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
            iniciarSeguimientosPersistidos(client: client)
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

    func renombrarConversacion(id: String, titulo: String, client: APIClient) async {
        let limpio = titulo.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !limpio.isEmpty else { return }
        do {
            let actualizada = try await client.renombrarConversacion(id: id, titulo: limpio)
            if let index = conversaciones.firstIndex(where: { $0.id == id }) {
                conversaciones[index] = actualizada
            }
        } catch {
            errorMensaje = error.localizedDescription
        }
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
            texto: ChatSecretRedaction.redact(textoLimpio),
            textoTransporte: textoLimpio,
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
        let texto = mensajes[indiceUsuario].textoTransporte ?? mensajes[indiceUsuario].texto
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
                mensajes[indiceUsuario].textoTransporte = nil
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
                    if let missionId = aplicar(evento, indiceRespuesta: indiceRespuesta) {
                        iniciarSeguimientoMision(
                            missionId: missionId,
                            mensajeId: mensajes[indiceRespuesta].id,
                            client: client
                        )
                    }
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

    @discardableResult
    private func aplicar(_ evento: ChatEvent, indiceRespuesta: Int) -> String? {
        guard mensajes.indices.contains(indiceRespuesta) else { return nil }
        switch evento {
        case .textDelta(let texto):
            mensajes[indiceRespuesta].texto += texto
        case .toolStart(let toolCallId, let nombre, _):
            herramientaActiva = HerramientaActiva(toolCallId: toolCallId, nombre: nombre)
            if mensajes[indiceRespuesta].trabajo == nil {
                mensajes[indiceRespuesta].trabajo = Trabajo(iniciadoEn: Date(), pasos: [])
            }
            mensajes[indiceRespuesta].trabajo?.iniciar(toolCallId: toolCallId, nombre: nombre)
        case .toolProgress(let toolCallId, let nombre, let segundos, let detalle):
            if mensajes[indiceRespuesta].trabajo == nil {
                mensajes[indiceRespuesta].trabajo = Trabajo(iniciadoEn: Date(), pasos: [])
            }
            mensajes[indiceRespuesta].trabajo?.actualizar(
                toolCallId: toolCallId,
                nombre: nombre,
                segundos: segundos,
                detalle: detalle
            )
        case .toolEnd(
            let toolCallId,
            let nombre,
            let resultado,
            let artefactos,
            let blocksVersion,
            let bloques,
            let missionId
        ):
            if herramientaActiva?.toolCallId == nil
                || toolCallId == nil
                || herramientaActiva?.toolCallId == toolCallId {
                herramientaActiva = nil
            }
            mensajes[indiceRespuesta].trabajo?.completar(
                toolCallId: toolCallId,
                nombre: nombre,
                resultado: resultado
            )
            for artefacto in artefactos
            where !mensajes[indiceRespuesta].artefactos.contains(where: { $0.fileId == artefacto.fileId }) {
                mensajes[indiceRespuesta].artefactos.append(artefacto)
            }
            if blocksVersion == 1 {
                for bloque in bloques where !mensajes[indiceRespuesta].bloques.contains(bloque) {
                    mensajes[indiceRespuesta].bloques.append(bloque)
                }
            }
            if let missionId {
                if mensajes[indiceRespuesta].trabajo == nil {
                    mensajes[indiceRespuesta].trabajo = Trabajo(iniciadoEn: Date(), pasos: [])
                }
                mensajes[indiceRespuesta].trabajo?.vincularMision(missionId)
                return missionId
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
            mensajes[indiceRespuesta].trabajo?.finalizar()
        case .error(let mensaje):
            errorMensaje = mensaje
        case .unknown:
            break
        }
        return nil
    }

    private func iniciarSeguimientosPersistidos(client: APIClient) {
        for mensaje in mensajes {
            guard let missionId = mensaje.trabajo?.missionId else { continue }
            iniciarSeguimientoMision(missionId: missionId, mensajeId: mensaje.id, client: client)
        }
    }

    private func iniciarSeguimientoMision(
        missionId: String,
        mensajeId: String,
        client: APIClient
    ) {
        guard seguimientoMisiones[missionId] == nil else { return }
        seguimientoMisiones[missionId] = Task { [weak self] in
            guard let self else { return }
            defer { self.seguimientoMisiones[missionId] = nil }
            var fallosConsecutivos = 0
            while !Task.isCancelled {
                do {
                    let detail = try await client.getMission(id: missionId)
                    fallosConsecutivos = 0
                    if let index = self.mensajes.firstIndex(where: { $0.id == mensajeId }) {
                        if self.mensajes[index].trabajo == nil {
                            self.mensajes[index].trabajo = Trabajo(iniciadoEn: Date(), pasos: [])
                        }
                        self.mensajes[index].trabajo?.actualizarMision(detail)
                    }
                    if !detail.mission.estaActiva { break }
                } catch {
                    fallosConsecutivos += 1
                    if fallosConsecutivos >= 3 {
                        if let index = self.mensajes.firstIndex(where: { $0.id == mensajeId }) {
                            self.mensajes[index].trabajo?.errorMision =
                                "No pude actualizar el progreso. Sigue disponible en Actividad."
                        }
                        break
                    }
                }
                try? await Task.sleep(for: .seconds(3))
            }
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
            var trabajo: Trabajo?
            for event in row.toolCalls {
                switch event {
                case .toolStart(let toolCallId, let nombre, _):
                    if trabajo == nil { trabajo = Trabajo(iniciadoEn: row.createdAt, pasos: []) }
                    trabajo?.iniciar(toolCallId: toolCallId, nombre: nombre)
                case .toolProgress(let toolCallId, let nombre, let segundos, let detalle):
                    if trabajo == nil { trabajo = Trabajo(iniciadoEn: row.createdAt, pasos: []) }
                    trabajo?.actualizar(
                        toolCallId: toolCallId,
                        nombre: nombre,
                        segundos: segundos,
                        detalle: detalle
                    )
                case .toolEnd(
                    let toolCallId,
                    let nombre,
                    let resultado,
                    let eventArtifacts,
                    let version,
                    let eventBlocks,
                    let missionId
                ):
                    trabajo?.completar(
                        toolCallId: toolCallId,
                        nombre: nombre,
                        resultado: resultado
                    )
                    for artifact in eventArtifacts
                    where !artifacts.contains(where: { $0.fileId == artifact.fileId }) {
                        artifacts.append(artifact)
                    }
                    if version == 1 {
                        for block in eventBlocks where !blocks.contains(block) { blocks.append(block) }
                    }
                    if let missionId {
                        if trabajo == nil { trabajo = Trabajo(iniciadoEn: row.createdAt, pasos: []) }
                        trabajo?.vincularMision(missionId)
                    }
                default:
                    continue
                }
            }
            trabajo?.finalizar()
            return Mensaje(
                id: row.id,
                rol: role,
                texto: row.text,
                artefactos: artifacts,
                bloques: blocks,
                adjuntos: row.attachments,
                trabajo: trabajo
            )
        }
    }
}
