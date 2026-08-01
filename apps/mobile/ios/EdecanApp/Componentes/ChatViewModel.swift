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
        /// Respuesta FINAL: lo que el asistente dice cuando ya terminó de trabajar.
        var texto: String
        /// Lo que dijo ANTES de usar la primera herramienta (el "claro que sí, ya voy").
        ///
        /// Se guarda aparte para poder dibujar la burbuja en el orden que el usuario espera:
        /// saludo → resumen de trabajo plegado → respuesta. Mezclarlos en un solo campo era
        /// lo que producía frases pegadas ("…con tu voz.Tengo datos…") y enterraba la
        /// respuesta real bajo la narración del plan.
        var textoApertura: String = ""
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
        /// Fecha del servidor (`created_at`). `nil` en la burbuja optimista que
        /// aún no volvió del backend. Se conserva porque el orden REAL del hilo
        /// es lo que decide si una tarjeta de pregunta ya se contestó
        /// (``HiloDePreguntas``), y el orden de llegada al array no es una
        /// garantía que valga la pena asumir.
        var createdAt: Date?

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
            falloEnvio: Bool = false,
            createdAt: Date? = nil
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
            self.createdAt = createdAt
        }

        /// Lo que se mandó de verdad. `texto` viene redactado para la burbuja
        /// (``ChatSecretRedaction``), así que compararlo contra el `value` de
        /// una opción sería comparar contra algo que nadie escribió.
        var textoEnviado: String { textoTransporte ?? texto }
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

    // MARK: - Selector de modelos (`GET /v1/models/chat`, `PUT .../model`)

    /// Catálogo servido por el backend. Se cachea por sesión de la vista: la
    /// lista casi nunca cambia y volver a pedirla al abrir la hoja solo
    /// añadiría un spinner.
    private(set) var catalogoModelos: ChatModelCatalog?
    private(set) var cargandoCatalogoModelos = false
    /// Id del modelo fijado en la conversación abierta. `nil` = automático.
    private(set) var modeloElegido: String?
    private(set) var esfuerzoElegido: EsfuerzoChat?

    private let sseClient = SSEClient()
    private let pendingAttemptStore: PendingChatAttemptStore
    private var inicializado = false
    private var inicioCompleto = false
    private var seguimientoMisiones: [String: Task<Void, Never>] = [:]
    private var aplicacionActiva = true
    private var recuperacionEnCurso = false

    init(pendingAttemptStore: PendingChatAttemptStore = PendingChatAttemptStore()) {
        self.pendingAttemptStore = pendingAttemptStore
    }

    var tituloConversacionActual: String {
        guard let conversacionId,
              let conversation = conversaciones.first(where: { $0.id == conversacionId })
        else { return "Nuevo chat" }
        // La conversación "principal" se titula "Actividad" en el backend
        // (ahí aterrizan los avisos automáticos que el dueño no pidió), pero
        // ese título leído en la cabecera del chat parece el nombre de un
        // feed que no es. Un rótulo neutro evita esa confusión.
        if conversation.isMain { return "Edecán" }
        let title = conversation.title?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return title.isEmpty ? "Conversación" : title
    }

    var ultimaRespuestaDelAsistente: String? {
        mensajes.last(where: { $0.rol == .asistente })?.texto
    }

    /// Ficha del modelo activo. `nil` mientras el catálogo no haya llegado o
    /// si la conversación está en automático.
    var modeloActivo: ChatModelInfo? { catalogoModelos?.modelo(id: modeloElegido) }

    /// Lo que necesitan la pastilla del composer y la hoja, en un solo valor.
    var seleccionDeModelo: SeleccionDeModeloChat {
        SeleccionDeModeloChat(
            modeloId: modeloElegido,
            info: modeloActivo,
            esfuerzo: esfuerzoElegido
        )
    }

    /// Texto de la pastilla: "Oda · Alto", "Copla", "Automático".
    var etiquetaPastilla: String { seleccionDeModelo.etiqueta }

    /// Aviso a mostrar bajo el composer cuando hay una imagen lista para
    /// enviar y el modelo elegido no ve. La vista es la que sabe qué adjuntos
    /// hay pendientes, así que el dato entra por parámetro.
    func avisoModeloCiego(hayImagenPendiente: Bool) -> String? {
        seleccionDeModelo.avisoDeCeguera(hayImagenEnElTurno: hayImagenPendiente)
    }

    /// Pide el catálogo una sola vez. Se llama al arrancar el chat (para que
    /// la pastilla muestre el NOMBRE del modelo y no el id crudo) y otra vez
    /// al abrir la hoja, por si el primer intento falló sin red.
    func cargarCatalogoModelos(client: APIClient?) async {
        guard catalogoModelos == nil, !cargandoCatalogoModelos, let client else { return }
        cargandoCatalogoModelos = true
        defer { cargandoCatalogoModelos = false }
        // Un fallo aquí NO es un error del chat: sin catálogo la pastilla
        // sigue diciendo algo honesto y el hilo funciona igual, así que no se
        // pinta el banner rojo por esto.
        catalogoModelos = try? await client.modelosDeChat()
    }

    /// Fija modelo y Esfuerzo de la conversación abierta. Actualización
    /// optimista (la hoja se cierra al instante) y reversión si el PUT falla:
    /// dejar la pastilla mintiendo sobre qué modelo corre sería peor que el
    /// error.
    func fijarModelo(_ modeloId: String?, esfuerzo: EsfuerzoChat?, client: APIClient?) async {
        let modeloAnterior = modeloElegido
        let esfuerzoAnterior = esfuerzoElegido
        modeloElegido = modeloId
        esfuerzoElegido = esfuerzo

        // En un chat nuevo todavía no hay fila que actualizar. La selección
        // viaja en memoria y se persiste al crear la conversación, justo antes
        // del primer POST (ver `asegurarConversacion`).
        guard let conversacionId, let client else { return }
        do {
            let persistido = try await client.fijarModeloDeChat(
                conversacionId: conversacionId,
                modelo: modeloId,
                esfuerzo: esfuerzo
            )
            modeloElegido = persistido.model
            esfuerzoElegido = persistido.effort
        } catch {
            modeloElegido = modeloAnterior
            esfuerzoElegido = esfuerzoAnterior
            errorMensaje = "No se pudo cambiar el modelo. \(error.localizedDescription)"
        }
    }

    /// Por id de mensaje, la primera respuesta del usuario que vino después.
    /// Es lo que cierra las tarjetas de ``QuestionBlock``: el hilo es la fuente
    /// de verdad, no un `@State` dentro de la tarjeta que SwiftUI descarta al
    /// reciclar la vista. Se calcula una vez por hilo, no una por burbuja.
    var respuestasAPreguntas: [String: String] {
        HiloDePreguntas.respuestasPosteriores(
            en: mensajes.map { mensaje in
                MensajeDelHilo(
                    id: mensaje.id,
                    rol: mensaje.rol == .usuario ? .usuario : .asistente,
                    texto: mensaje.textoEnviado,
                    fecha: mensaje.createdAt,
                    entregable: !mensaje.falloEnvio
                )
            }
        )
    }

    /// Arranca el chat. Un intento pendiente por recuperar o un
    /// `preferredConversationId` explícito (p. ej. un deeplink) SIEMPRE
    /// ganan. Sin ninguno de los dos, la pestaña aterriza en la conversación
    /// PRINCIPAL persistente (Frente 5, paridad Aria) en vez de un chat
    /// nuevo y vacío: es la misma conversación en todos los dispositivos, no
    /// otro hilo más que el dueño tiene que encontrar. El botón de lápiz
    /// (``nuevaConversacion``) sigue abriendo chats nuevos aparte.
    func iniciar(client: APIClient, preferredConversationId: String?) async {
        guard !inicializado else { return }
        inicializado = true
        // No se espera: el catálogo solo alimenta la pastilla y la hoja, y
        // bloquear el arranque del chat por él sería cambiar una etiqueta por
        // un retraso en lo único que el dueño vino a hacer, escribir.
        Task { await cargarCatalogoModelos(client: client) }
        await cargarConversaciones(client: client)

        let pending = cargarIntentoPendienteValido()
        // Un envío a medias SIEMPRE gana: perder esa burbuja sería perder un
        // mensaje que ya salió hacia el servidor.
        let preferredId = pending?.conversationId ?? preferredConversationId
        if let preferredId,
           conversaciones.contains(where: { $0.id == preferredId }) {
            await abrirConversacion(id: preferredId, client: client)
        } else if preferredId == nil {
            await abrirConversacionPrincipalSiEsPosible(client: client)
        }

        inicioCompleto = true
        if pending != nil {
            await reanudarIntentoPendienteSiNecesario(client: client)
        }
    }

    /// Resuelve (o crea, si es la primera vez) la conversación principal del
    /// dueño y la deja abierta. Un fallo aquí (sin red, backend viejo sin el
    /// endpoint) no debe bloquear el arranque: se cae al chat nuevo de
    /// siempre, que sigue siendo un estado válido.
    private func abrirConversacionPrincipalSiEsPosible(client: APIClient) async {
        guard let principal = try? await client.conversacionPrincipal() else { return }
        // La lista ya se cargó arriba (`cargarConversaciones`); si esta es la
        // primerísima vez que se crea la principal, todavía no aparece ahí.
        // Se inserta a mano para que `tituloConversacionActual` la encuentre
        // de inmediato en vez de mostrar "Nuevo chat" por un instante.
        if !conversaciones.contains(where: { $0.id == principal.id }) {
            conversaciones.insert(principal, at: 0)
        }
        await abrirConversacion(id: principal.id, client: client)
    }

    /// SwiftUI informa la fase de la escena. No intentamos mantener un socket
    /// artificialmente vivo en background, algo que iOS no garantiza: el
    /// servidor continúa el turno y, al volver, se recupera su replay.
    func actualizarEstadoAplicacion(activa: Bool) {
        aplicacionActiva = activa
    }

    /// Punto idempotente para foreground. Si el proceso siguió vivo, evita una
    /// segunda recuperación mientras el envío original está activo. Si iOS lo
    /// recreó, reconstruye la burbuja y consulta la operación persistida.
    func reanudarIntentoPendienteSiNecesario(client: APIClient) async {
        guard inicioCompleto, aplicacionActiva, !enviando, !recuperacionEnCurso,
              let pending = cargarIntentoPendienteValido()
        else { return }

        recuperacionEnCurso = true
        errorMensaje = nil
        defer {
            enviando = false
            recuperacionEnCurso = false
        }

        do {
            if conversacionId != pending.conversationId {
                await abrirConversacion(id: pending.conversationId, client: client)
                guard conversacionId == pending.conversationId else { return }
            }

            enviando = true
            let responseIndex = prepararBurbujasParaReanudacion()
            try await recuperarReplay(
                pending,
                indiceRespuesta: responseIndex,
                client: client
            )
            limpiarIntentoPendiente(siCoincide: pending.idempotencyKey)
            await cargarConversaciones(client: client)
        } catch is CancellationError {
            // Conserva el estado. El próximo foreground vuelve por la misma UUID.
        } catch {
            // Solo los errores definitivos llegan aquí. Una pérdida temporal de
            // red permanece silenciosa dentro de `recuperarReplay`.
            limpiarIntentoPendiente(siCoincide: pending.idempotencyKey)
            if let userIndex = mensajes.firstIndex(where: { $0.id == pending.localMessageId }) {
                mensajes[userIndex].falloEnvio = true
                mensajes[userIndex].logicalAttempt = nil
            }
            mensajes.removeAll {
                $0.rol == .asistente && $0.enProgreso && $0.texto.isEmpty
            }
            errorMensaje = error.localizedDescription
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
            // La selección es propiedad de la conversación: abrirla restaura
            // la pastilla exactamente como quedó, en este y en cualquier otro
            // dispositivo.
            modeloElegido = detail.model
            esfuerzoElegido = detail.effort
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
        // `modeloElegido`/`esfuerzoElegido` se conservan a propósito: quien
        // acaba de elegir Oda para trabajar espera seguir en Oda al abrir el
        // siguiente chat, no volver a automático. Se persiste cuando la
        // conversación nueva nace (`asegurarConversacion`).
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

    /// Irreversible: la lista se actualiza solo si el backend confirma el borrado.
    func eliminarConversacion(id: String, client: APIClient) async {
        do {
            try await client.eliminarConversacion(id: id)
            conversaciones.removeAll { $0.id == id }
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
        var pending: PendingChatAttempt?
        do {
            let conversationId = try await asegurarConversacion(client: client)
            let pendingAttempt = PendingChatAttempt(
                idempotencyKey: logicalAttempt.idempotencyKey,
                conversationId: conversationId,
                localMessageId: mensajeId
            )
            // Este registro se escribe antes del POST. Si iOS suspende o mata
            // el proceso después de entregar la petición, el próximo arranque
            // recupera el replay por UUID sin volver a enviar el prompt.
            // Recovery persistence must not block the foreground turn. The
            // request is idempotent and the server remains the source of truth;
            // if iOS temporarily refuses Application Support (common during
            // first launch, restore, or simulator transitions), continue the
            // send and let the normal transport/retry path handle it.
            try? pendingAttemptStore.save(pendingAttempt)
            pending = pendingAttempt

            let indiceRespuesta = mensajes.count
            indiceRespuestaCreada = indiceRespuesta
            mensajes.append(Mensaje(rol: .asistente, texto: "", enProgreso: true))

            struct Body: Encodable {
                let text: String
                let attachments: [String]
            }
            let body = Body(text: texto, attachments: attachmentIds)
            do {
                try await consumirStreamConRefresh(
                    client: client,
                    path: "/v1/conversations/\(conversationId)/messages",
                    body: body,
                    idempotencyKey: logicalAttempt.idempotencyKey,
                    indiceRespuesta: indiceRespuesta
                )
            } catch {
                guard Self.esInterrupcionRecuperable(error) else { throw error }
                // El mismo POST/key es seguro y cubre el caso en que la red
                // cayó antes de que el servidor reclamara el turno. Si ya lo
                // reclamó, responde 409 y pasamos al GET de estado/replay.
                try await esperarAplicacionActiva()
                restablecerRespuestaParaReplay(indiceRespuesta)
                do {
                    try await consumirStreamConRefresh(
                        client: client,
                        path: "/v1/conversations/\(conversationId)/messages",
                        body: body,
                        idempotencyKey: logicalAttempt.idempotencyKey,
                        indiceRespuesta: indiceRespuesta
                    )
                } catch {
                    guard Self.esInterrupcionRecuperable(error) else { throw error }
                    try await recuperarReplay(
                        pendingAttempt,
                        indiceRespuesta: indiceRespuesta,
                        client: client
                    )
                }
            }
            if errorMensaje != nil {
                if mensajes.indices.contains(indiceUsuario) { mensajes[indiceUsuario].falloEnvio = true }
                limpiarIntentoPendiente(siCoincide: logicalAttempt.idempotencyKey)
                removerRespuestaFallida(indiceRespuesta)
                return false
            }
            limpiarIntentoPendiente(siCoincide: logicalAttempt.idempotencyKey)
            marcarIntentoCompletado(mensajeId: mensajeId)
            if mensajes.indices.contains(indiceRespuesta) {
                mensajes[indiceRespuesta].enProgreso = false
            }
            await cargarConversaciones(client: client)
            return true
        } catch is CancellationError {
            // Suspensión/cierre de la vista no es un fallo del trabajo. El
            // registro protegido queda listo para `scenePhase == .active` o
            // para el siguiente arranque.
            if let responseIndex = indiceRespuestaCreada,
               mensajes.indices.contains(responseIndex) {
                mensajes[responseIndex].enProgreso = true
            }
            return false
        } catch {
            if let pending {
                limpiarIntentoPendiente(siCoincide: pending.idempotencyKey)
            }
            if mensajes.indices.contains(indiceUsuario) {
                mensajes[indiceUsuario].falloEnvio = true
                mensajes[indiceUsuario].logicalAttempt = nil
            }
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
        // Lo que el dueño eligió en un chat que todavía no existía se persiste
        // AQUÍ, antes del POST del mensaje: así el primer turno ya corre con
        // ese modelo en vez de estrenar la conversación en automático.
        if modeloElegido != nil || esfuerzoElegido != nil {
            // Un fallo no debe abortar el envío: el turno corre en automático
            // (peor etiqueta, mensaje entregado) y la pastilla vuelve a la
            // verdad tras el `cargarConversaciones` del final.
            if let persistido = try? await client.fijarModeloDeChat(
                conversacionId: conversation.id,
                modelo: modeloElegido,
                esfuerzo: esfuerzoElegido
            ) {
                modeloElegido = persistido.model
                esfuerzoElegido = persistido.effort
            }
        }
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
            } catch SSEClient.SSEError.servidor(let status, _) where status == 401 && !yaRefresco {
                yaRefresco = true
                try await client.refrescar()
            }
        }
    }

    /// Consume el replay sin aplicarlo sobre deltas parciales, porque el replay
    /// contiene el turno completo. Al terminar recarga historial, artefactos,
    /// progreso y confirmaciones desde la fuente de verdad.
    private func recuperarReplay(
        _ pending: PendingChatAttempt,
        indiceRespuesta: Int,
        client: APIClient
    ) async throws {
        var refrescoAutenticacionDisponible = true
        var demoraConexion: TimeInterval = 1

        while true {
            try Task.checkCancellation()
            try await esperarAplicacionActiva()
            let request = try await construirPeticionDeReplay(pending, client: client)
            do {
                // Antes del replay completo se descarta cualquier fragmento
                // que alcanzó a llegar por el socket original.
                restablecerRespuestaParaReplay(indiceRespuesta)
                for try await _ in sseClient.stream(request) {}

                let detail = try await client.obtenerConversacion(id: pending.conversationId)
                guard conversacionId == pending.conversationId else {
                    throw CancellationError()
                }
                mensajes = Self.mensajesDesdeHistorial(detail.messages)
                restaurarConfirmacion(detail.pendingConfirmation)
                iniciarSeguimientosPersistidos(client: client)
                return
            } catch SSEClient.SSEError.servidor(let status, let retryAfter)
                where status == 202 {
                // El backend conserva la ejecución aunque el socket móvil haya
                // desaparecido. No se presenta como error ni se vuelve a crear
                // el mensaje.
                try await Task.sleep(
                    for: .seconds(max(0.5, min(retryAfter ?? 1, 10)))
                )
                demoraConexion = 1
            } catch SSEClient.SSEError.servidor(let status, _)
                where status == 401 && refrescoAutenticacionDisponible {
                refrescoAutenticacionDisponible = false
                try await client.refrescar()
            } catch let error where Self.esErrorDeConexion(error) {
                // Backoff acotado y sin banner rojo. En background iOS suele
                // suspender este Task; en foreground retoma con la misma UUID.
                try await Task.sleep(for: .seconds(demoraConexion))
                demoraConexion = min(demoraConexion * 2, 10)
            }
        }
    }

    private func construirPeticionDeReplay(
        _ pending: PendingChatAttempt,
        client: APIClient
    ) async throws -> URLRequest {
        let path = "/v1/conversations/\(pending.conversationId)/message-attempts/"
            + pending.idempotencyKey.uuidString.lowercased()
        let url = try await client.urlCompleta(path)
        let token = try await client.tokenDeAccesoValido()
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        return request
    }

    private func esperarAplicacionActiva() async throws {
        while !aplicacionActiva {
            try Task.checkCancellation()
            try await Task.sleep(for: .milliseconds(500))
        }
    }

    private func cargarIntentoPendienteValido() -> PendingChatAttempt? {
        do {
            guard let pending = try pendingAttemptStore.load() else { return nil }
            guard pending.isRecoverable() else {
                pendingAttemptStore.clear()
                return nil
            }
            return pending
        } catch {
            pendingAttemptStore.clear()
            return nil
        }
    }

    private func limpiarIntentoPendiente(siCoincide idempotencyKey: UUID) {
        guard let pending = try? pendingAttemptStore.load(),
              pending.idempotencyKey == idempotencyKey
        else { return }
        pendingAttemptStore.clear()
    }

    private func marcarIntentoCompletado(mensajeId: String) {
        guard let index = mensajes.firstIndex(where: { $0.id == mensajeId }) else { return }
        mensajes[index].falloEnvio = false
        mensajes[index].logicalAttempt = nil
        mensajes[index].textoTransporte = nil
    }

    private func restablecerRespuestaParaReplay(_ index: Int) {
        guard mensajes.indices.contains(index), mensajes[index].rol == .asistente else { return }
        mensajes[index].texto = ""
        mensajes[index].artefactos = []
        mensajes[index].bloques = []
        mensajes[index].trabajo = nil
        mensajes[index].enProgreso = true
        herramientaActiva = nil
        confirmacionPendiente = nil
        errorMensaje = nil
    }

    private func prepararBurbujasParaReanudacion() -> Int {
        if let index = mensajes.lastIndex(where: {
            $0.rol == .asistente && $0.enProgreso
        }) {
            restablecerRespuestaParaReplay(index)
            return index
        }
        let index = mensajes.count
        mensajes.append(Mensaje(rol: .asistente, texto: "", enProgreso: true))
        return index
    }

    private static func esInterrupcionRecuperable(_ error: Error) -> Bool {
        if error is CancellationError { return true }
        if esErrorDeConexion(error) { return true }
        if case SSEClient.SSEError.servidor(let status, _) = error {
            return status == 409 || status == 502 || status == 503 || status == 504
        }
        return false
    }

    private static func esErrorDeConexion(_ error: Error) -> Bool {
        if case SSEClient.SSEError.conexion = error { return true }
        if let urlError = error as? URLError {
            switch urlError.code {
            case .networkConnectionLost, .notConnectedToInternet, .timedOut,
                 .cannotConnectToHost, .cannotFindHost, .dnsLookupFailed,
                 .internationalRoamingOff, .dataNotAllowed:
                return true
            default:
                return false
            }
        }
        return false
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
            // El texto que llega ANTES de la primera herramienta es el acuse ("claro que
            // sí, ya voy"); el de después es la respuesta de verdad. El corte lo marca la
            // existencia de `trabajo`, que nace en el primer `toolStart`.
            if mensajes[indiceRespuesta].trabajo == nil {
                mensajes[indiceRespuesta].textoApertura += texto
            } else {
                mensajes[indiceRespuesta].texto += texto
            }
        case .toolStart(let toolCallId, let nombre, _):
            herramientaActiva = HerramientaActiva(toolCallId: toolCallId, nombre: nombre)
            // Si ya había texto "final" y llega OTRA herramienta, no era la respuesta: era
            // narración entre pasos ("ahora voy a buscar…"). Se recicla como apertura para
            // que la respuesta de verdad quede sola al final, que es el punto de separarlas.
            if !mensajes[indiceRespuesta].texto.isEmpty {
                let intermedio = mensajes[indiceRespuesta].texto
                mensajes[indiceRespuesta].texto = ""
                let apertura = mensajes[indiceRespuesta].textoApertura
                mensajes[indiceRespuesta].textoApertura =
                    apertura.isEmpty ? intermedio : "\(apertura)\n\n\(intermedio)"
            }
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
                trabajo: trabajo,
                createdAt: row.createdAt
            )
        }
    }
}
