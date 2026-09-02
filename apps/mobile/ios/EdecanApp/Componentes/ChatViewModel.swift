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

        /// Una misión en curso sí mantiene el reloj. Si no hay misión, el
        /// reloj vive solo mientras el asistente sigue generando: un turno
        /// ya escrito no puede seguir sumando minutos.
        var misionEnCurso: Bool {
            estadoMision == "planning" || estadoMision == "running"
        }
        var estaActivo: Bool {
            if misionEnCurso { return true }
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
            if let fin = terminadoEn {
                return max(0, Int(fin.timeIntervalSince(iniciadoEn)))
            }
            if estaActivo {
                return max(0, Int(Date().timeIntervalSince(iniciadoEn)))
            }
            return max(0, pasos.map(\.segundos).max() ?? 0)
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
            // Una misión que sigue planning/running tiene su propio seguimiento.
            // Cualquier otro caso (tools del chat, misión ya terminal, o un
            // `mission_id` huérfano) tiene que congelar el reloj: si no, el
            // ticker sigue vivo con `Date()` y un turno de dos minutos llega
            // a marcar 50.
            guard !misionEnCurso else { return }
            if terminadoEn == nil { terminadoEn = Date() }
        }

        /// El poll de la misión falló o el hilo ya no está generando: no
        /// dejamos el reloj en `planning` eterno.
        mutating func finalizarForzado() {
            if misionEnCurso { estadoMision = "done" }
            if terminadoEn == nil { terminadoEn = Date() }
        }

        /// Al reconstruir desde el GET, `Date()` ahora mismo convertiría un
        /// turno de hace una hora en "60m y contando". Congelamos contra el
        /// instante de creación más lo que reportaron las tools.
        mutating func finalizarDesdeHistorial() {
            guard !misionEnCurso else { return }
            if terminadoEn != nil { return }
            let extra = TimeInterval(pasos.map(\.segundos).max() ?? 0)
            terminadoEn = iniciadoEn.addingTimeInterval(extra)
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

    /// Una fuente (cita) de una búsqueda web, extraída del `tool_end` de
    /// `buscar_web`. Se muestra como un chip compacto y tappable debajo del
    /// texto de la respuesta, no como una tarjeta completa (eso ya lo hacen
    /// los bloques `link_preview`): es la referencia discreta, no el resumen.
    struct Fuente: Identifiable, Equatable {
        let id: String
        let titulo: String
        let url: String
        let dominio: String
        let fragmento: String?

        init(titulo: String, url: String, dominio: String? = nil, fragmento: String? = nil) {
            self.id = url
            self.titulo = titulo
            self.url = url
            self.dominio = dominio ?? Self.extraerDominio(url)
            self.fragmento = fragmento
        }

        static func extraerDominio(_ urlString: String) -> String {
            guard let host = URL(string: urlString)?.host else { return urlString }
            return host.replacingOccurrences(of: "www.", with: "")
        }
    }

    struct Mensaje: Identifiable, Equatable {
        /// `.sistema` es un aviso LOCAL del hilo (p. ej. la confirmación de
        /// `/clear`): no lo dijo ni el dueño ni Edecán, así que no es ni
        /// burbuja de usuario ni de asistente (ver `BurbujaMensaje`). Nunca
        /// se persiste -- `mensajesDesdeHistorial` solo reconstruye
        /// `usuario`/`asistente` desde el servidor, así que reabrir el chat
        /// (u otro dispositivo) no lo vuelve a mostrar. Eso es intencional:
        /// la prueba de que `/clear` funcionó, tras un relanzamiento, es que
        /// la pantalla sigue limpia -- no un aviso viejo repitiéndose.
        enum Rol: Equatable { case usuario, asistente, sistema }

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
        /// Fuentes (citas) de `buscar_web` — filas tappables bajo la respuesta.
        var fuentes: [Fuente] = []
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
        var pinned = false
        var bookmark = false
        /// Reacciones del mensaje (emojis). Vive solo en memoria/sesión: el
        /// `GET` de la conversación todavía no devuelve reacciones, así que no
        /// se reconstruyen del historial (contrato en paralelo).
        var reactions: [String] = []

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
            fuentes: [Fuente] = [],
            logicalAttempt: LogicalChatAttempt? = nil,
            falloEnvio: Bool = false,
            createdAt: Date? = nil,
            pinned: Bool = false,
            bookmark: Bool = false,
            reactions: [String] = []
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
            self.fuentes = fuentes
            self.logicalAttempt = logicalAttempt
            self.falloEnvio = falloEnvio
            self.createdAt = createdAt
            self.pinned = pinned
            self.bookmark = bookmark
            self.reactions = reactions
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
    var modoTrabajar = false
    private(set) var misionViva: MissionOut?

    /// `true` mientras un turno del asistente se está transmitiendo por SSE
    /// (el stream del POST o de la confirmación). La vista lo usa para
    /// reemplazar el botón de envío por uno de Detener. No cubre el replay de
    /// recuperación (que no pasa por ``consumirStreamConRefresh``): ese flujo
    /// es automático y no tiene sentido cancelarlo a mano.
    private(set) var estaGenerando = false

    /// Task que envuelve el envío/reintento/confirmación en curso, para que
    /// ``detenerGeneracion`` pueda cancelar el stream SSE. Vive solo mientras
    /// hay un stream activo iniciado por el usuario; el replay de
    /// recuperación no lo toca.
    private var tareaGeneracion: Task<Void, Never>?

    /// Marca una cancelación iniciada por el usuario (botón Detener) para que
    /// el `catch is CancellationError` de ``ejecutarEnvio`` cierre la burbuja
    /// en vez de dejarla `enProgreso` (comportamiento pensado para la
    /// suspensión de la vista, no para una parada a mano).
    private var detenidoPorUsuario = false

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
    /// Nº de polls de misiones que han arrancado en esta conversación. Al
    /// abrir un hilo con N misiones activas, cada una espera ~600 ms más
    /// que la anterior antes de su primer GET: así NO disparan todas a la
    /// vez por el túnel (el pico de "carga muchas cosas a la vez" que
    /// acompañaba al crash). El poll es de 3 s y una misión ya terminada
    /// se queda en su turno sin tocar el servidor hasta salir de la fila.
    private var misionesArrancadas = 0

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

    /// Mensajes del asistente posteriores a la última marca de lectura local.
    func contarNoLeidos(lastReadAt: Date?) -> Int {
        mensajes.filter { mensaje in
            guard mensaje.rol == .asistente, !mensaje.enProgreso else { return false }
            let tieneContenido = !mensaje.texto.isEmpty
                || !mensaje.textoApertura.isEmpty
                || !mensaje.bloques.isEmpty
                || !mensaje.artefactos.isEmpty
            guard tieneContenido else { return false }
            guard let createdAt = mensaje.createdAt else { return lastReadAt == nil }
            guard let lastReadAt else { return true }
            return createdAt > lastReadAt
        }.count
    }

    /// Marca como leídos todos los mensajes persistidos del hilo abierto.
    func instanteUltimoMensajeLeible() -> Date? {
        mensajes.compactMap(\.createdAt).max()
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

    /// Texto de la pastilla: "Oda · Alto", "Scout", "Automático".
    var etiquetaPastilla: String { seleccionDeModelo.etiqueta }

    /// Aviso a mostrar bajo el composer cuando hay una imagen lista para
    /// enviar y el modelo elegido no ve. La vista es la que sabe qué adjuntos
    /// hay pendientes, así que el dato entra por parámetro.
    func avisoModeloCiego(hayImagenPendiente: Bool) -> String? {
        seleccionDeModelo.avisoDeCeguera(hayImagenEnElTurno: hayImagenPendiente)
    }

    /// Pide el catálogo. Al arrancar el chat se llama sin forzar (para que
    /// la pastilla muestre el NOMBRE y no el id crudo). La hoja lo pide
    /// otra vez con `forzar` para no quedarse con un catálogo viejo del
    /// sidecar (Copla / GPT-OSS) después de un rebuild.
    func cargarCatalogoModelos(client: APIClient?, forzar: Bool = false) async {
        guard let client else { return }
        if !forzar, catalogoModelos != nil { return }
        guard !cargandoCatalogoModelos else { return }
        cargandoCatalogoModelos = true
        defer { cargandoCatalogoModelos = false }
        // Un fallo aquí NO es un error del chat: sin catálogo la pastilla
        // sigue diciendo algo honesto y el hilo funciona igual, así que no se
        // pinta el banner rojo por esto.
        if let fresco = try? await client.modelosDeChat() {
            catalogoModelos = fresco
        }
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
    /// PRINCIPAL persistente (Frente 5, paridad REFERENCIA) en vez de un chat
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

    /// Recarga el hilo abierto al volver al frente. iOS no mantiene un
    /// socket de chat: un mensaje que Edecán escribe solo (resumen de
    /// llamada, aviso de misión) ya está en el servidor, pero la pantalla
    /// se queda con lo que cargó al abrir. Mac sí lo ve porque al
    /// reiniciar el escritorio vuelve a pedir el GET. Sin esto, el dueño
    /// abre iOS y el chat "no tiene" lo que Mac sí.
    func refrescarConversacionAbierta(client: APIClient) async {
        guard let conversacionId,
              !enviando,
              !cargandoConversacion,
              confirmacionPendiente == nil
        else { return }
        do {
            let detail = try await client.obtenerConversacion(id: conversacionId)
            guard self.conversacionId == detail.id, !enviando else { return }
            mensajes = Self.mensajesDesdeHistorial(detail.messages)
            restaurarConfirmacion(detail.pendingConfirmation)
            iniciarSeguimientosPersistidos(client: client)
            modeloElegido = detail.model
            esfuerzoElegido = detail.effort
        } catch {
            // Sync en silencio: un fallo de red aquí no debe tapar el hilo
            // que el dueño ya está leyendo.
        }
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
            errorMensaje = Self.mensajeErrorUsuario(error.localizedDescription)
        }
    }

    func cargarConversaciones(client: APIClient) async {
        cargandoHistorial = true
        defer { cargandoHistorial = false }
        do {
            // Mismo historial que Mac: los hilos `phone` ("Llamada de +57…")
            // son la transcripción real. Si se ocultan, iOS solo ve el
            // resumen de Actividad — y si ese resumen se escribió antes de
            // la transcripción, parece que "no hubo llamada".
            conversaciones = try await client.listarConversaciones()
        } catch {
            errorMensaje = Self.mensajeErrorUsuario(error.localizedDescription)
        }
    }

    func abrirConversacion(id: String, client: APIClient) async {
        guard !enviando else { return }
        if conversacionId != id {
            soltarMisionViva()
        }
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
            errorMensaje = Self.mensajeErrorUsuario(error.localizedDescription)
        }
    }

    func nuevaConversacion() {
        guard !enviando, confirmacionPendiente == nil else { return }
        conversacionId = nil
        mensajes = []
        herramientaActiva = nil
        errorMensaje = nil
        soltarMisionViva()
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
            errorMensaje = Self.mensajeErrorUsuario(error.localizedDescription)
        }
    }

    /// Irreversible: la lista se actualiza solo si el backend confirma el borrado.
    func eliminarConversacion(id: String, client: APIClient) async {
        do {
            try await client.eliminarConversacion(id: id)
            conversaciones.removeAll { $0.id == id }
        } catch {
            errorMensaje = Self.mensajeErrorUsuario(error.localizedDescription)
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
              confirmacionPendiente == nil
        else {
            if textoLimpio.count > 50_000 { errorMensaje = "El mensaje es demasiado largo." }
            return false
        }

        // Comando local (ver `LocalChatCommand`): se intercepta ACÁ, antes de
        // crear la burbuja optimista de usuario, y nunca se manda como
        // mensaje normal. Un adjunto junto al texto rompe el match a
        // propósito -- eso ya no es "solo /clear", es un mensaje real que
        // menciona el comando.
        if adjuntos.isEmpty, let comando = LocalChatCommand.parse(textoLimpio) {
            alAceptar?()
            switch comando {
            case .clear:
                return await ejecutarComandoClear(client: client)
            case .branch:
                return await ejecutarComandoBranch(client: client)
            case .rewind:
                return await ejecutarComandoRewind(client: client)
            }
        }
        if modoTrabajar, adjuntos.isEmpty {
            alAceptar?()
            return await ejecutarModoTrabajar(objetivo: textoLimpio, client: client)
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

    /// Ejecuta `/clear`: por defecto NO ES DESTRUCTIVO. Nada se borra del
    /// historial del dueño -- solo se le pide al backend que mueva el límite
    /// desde el que el PRÓXIMO turno arma el contexto que ve el modelo
    /// (`POST /{id}/clear`, ver su docstring en `conversations.py` para la
    /// receta exacta). Sin conversación todavía (chat nuevo, vacío) no hay
    /// nada que limpiar en el servidor -- solo se vacía la pantalla.
    @discardableResult
    private func ejecutarComandoClear(client: APIClient) async -> Bool {
        guard !enviando, confirmacionPendiente == nil else { return false }
        errorMensaje = nil
        if let conversacionId {
            do {
                _ = try await client.limpiarContextoDeConversacion(id: conversacionId)
            } catch {
                errorMensaje = Self.mensajeErrorUsuario(error.localizedDescription)
                return false
            }
        }
        mensajes = []
        herramientaActiva = nil
        // La tarjeta "Misión · done" vive FUERA de `mensajes` (`misionViva`).
        // Si no se suelta aquí, /clear deja la pantalla "limpia" con esa
        // ficha todavía clavada al final -- exactamente lo que el dueño
        // reportó. La misión sigue en Actividad; solo deja de perseguir el
        // hilo que acaba de reiniciar.
        soltarMisionViva()
        // Aviso LOCAL y efímero (`Mensaje.Rol.sistema`): el dueño pidió
        // explícitamente "que vea que pasó, no un silencio" -- sin esta
        // burbuja /clear se vería igual que el bug que vino a arreglar (algo
        // cambia, pero no se nota). No se persiste ni sobrevive a un
        // relanzamiento a propósito: ver el comentario de `Rol.sistema`.
        mensajes.append(
            Mensaje(
                rol: .sistema,
                texto: "Contexto reiniciado. Lo anterior sigue en tu historial; desde aquí empiezas de cero."
            )
        )
        return true
    }

    @discardableResult
    private func ejecutarComandoBranch(client: APIClient) async -> Bool {
        guard let conversacionId else {
            errorMensaje = "Abre un chat antes de ramificar."
            return false
        }
        do {
            let branched = try await client.ramificarConversacion(id: conversacionId)
            conversaciones.insert(branched, at: 0)
            await abrirConversacion(id: branched.id, client: client)
            mensajes.insert(
                Mensaje(rol: .sistema, texto: "Rama nueva. El chat original sigue intacto."),
                at: 0
            )
            return true
        } catch {
            errorMensaje = Self.mensajeErrorUsuario(error.localizedDescription)
            return false
        }
    }

    @discardableResult
    private func ejecutarComandoRewind(client: APIClient) async -> Bool {
        guard let conversacionId else {
            errorMensaje = "Abre un chat antes de rebobinar."
            return false
        }
        do {
            let rewound = try await client.rebobinarConversacion(id: conversacionId)
            conversaciones.insert(rewound, at: 0)
            await abrirConversacion(id: rewound.id, client: client)
            mensajes.insert(
                Mensaje(rol: .sistema, texto: "Rebobiné el último turno. Puedes escribir de nuevo."),
                at: 0
            )
            return true
        } catch {
            errorMensaje = Self.mensajeErrorUsuario(error.localizedDescription)
            return false
        }
    }

    @discardableResult
    private func ejecutarModoTrabajar(objetivo: String, client: APIClient) async -> Bool {
        do {
            let mission = try await client.createMission(objetivo: objetivo)
            misionViva = mission
            iniciarPollingMisionViva(client: client)
            mensajes.append(Mensaje(rol: .usuario, texto: objetivo))
            mensajes.append(
                Mensaje(
                    rol: .asistente,
                    texto: "Lanzé la misión «\(mission.objetivo)». La sigo aquí y también en Actividad."
                )
            )
            return true
        } catch {
            errorMensaje = Self.mensajeErrorUsuario(error.localizedDescription)
            return false
        }
    }

    func marcarBanderas(
        mensajeId: String,
        pinned: Bool? = nil,
        bookmark: Bool? = nil,
        client: APIClient
    ) async {
        guard let conversacionId, !mensajeId.isEmpty else { return }
        do {
            let updated = try await client.marcarBanderasDeMensaje(
                conversacionId: conversacionId,
                mensajeId: mensajeId,
                pinned: pinned,
                bookmark: bookmark
            )
            if let index = mensajes.firstIndex(where: { $0.id == mensajeId }) {
                mensajes[index].pinned = updated.pinned
                mensajes[index].bookmark = updated.bookmark
            }
        } catch {
            errorMensaje = Self.mensajeErrorUsuario(error.localizedDescription)
        }
    }

    /// Alterna una reacción (👍👎✅👀❤️🔥) sobre un mensaje del chat. Optimista:
    /// se aplica de inmediato y se revierte si el endpoint falla. El id debe
    /// ser el persistido (`createdAt != nil`); un id local haría 404 y se
    /// revertiría sin dejar la burbuja mintiendo.
    func alternarReaccion(mensajeId: String, emoji: String, client: APIClient) async {
        guard let index = mensajes.firstIndex(where: { $0.id == mensajeId }) else { return }
        let previas = mensajes[index].reactions
        let agregar = !previas.contains(emoji)
        if agregar {
            mensajes[index].reactions.append(emoji)
        } else {
            mensajes[index].reactions.removeAll { $0 == emoji }
        }
        do {
            if agregar {
                try await client.addReaction(messageId: mensajeId, emoji: emoji)
            } else {
                try await client.removeReaction(messageId: mensajeId, emoji: emoji)
            }
        } catch let apiError as APIClient.APIError {
            mensajes[index].reactions = previas
            errorMensaje = apiError.esProximamente
                ? "Las reacciones están llegando al servidor."
                : Self.mensajeErrorUsuario(apiError.localizedDescription)
        } catch {
            mensajes[index].reactions = previas
            errorMensaje = Self.mensajeErrorUsuario(error.localizedDescription)
        }
    }

    private var tareaMisionViva: Task<Void, Never>?

    private func soltarMisionViva() {
        tareaMisionViva?.cancel()
        tareaMisionViva = nil
        misionViva = nil
        for tarea in seguimientoMisiones.values { tarea.cancel() }
        seguimientoMisiones.removeAll()
    }

    private func iniciarPollingMisionViva(client: APIClient) {
        tareaMisionViva?.cancel()
        tareaMisionViva = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(4))
                guard let self, let id = self.misionViva?.id else { return }
                if let detail = try? await client.getMission(id: id) {
                    self.misionViva = detail.mission
                    if detail.mission.esTerminal { break }
                }
            }
        }
    }

    func redirigirMisionViva(instruction: String, client: APIClient) async {
        guard let misionViva else { return }
        do {
            self.misionViva = try await client.steerMission(id: misionViva.id, instruction: instruction)
        } catch {
            errorMensaje = Self.mensajeErrorUsuario(error.localizedDescription)
        }
    }

    @discardableResult
    func reintentar(mensajeId: String, client: APIClient) async -> Bool {
        guard let index = mensajes.firstIndex(where: {
            $0.id == mensajeId && $0.rol == .usuario && $0.falloEnvio
        }) else { return false }
        mensajes[index].falloEnvio = false
        return await ejecutarEnvio(mensajeId: mensajeId, client: client)
    }

    // MARK: - Detención de generación

    /// Punto único para que la vista arranque un envío y pueda cancelarlo.
    /// Crea el `Task` y lo guarda en ``tareaGeneracion``; al terminar lo
    /// limpia. Si había uno previo (p. ej. un doble toque), se cancela primero.
    func enviarDesdeVista(
        texto: String,
        adjuntos: [ChatAttachment],
        alAceptar: (() -> Void)?,
        client: APIClient
    ) {
        if estaGenerando {
            Task { [weak self] in
                _ = await self?.enviar(
                    texto: texto,
                    adjuntos: adjuntos,
                    alAceptar: alAceptar,
                    client: client
                )
            }
            return
        }
        tareaGeneracion?.cancel()
        let task = Task { [weak self] in
            _ = await self?.enviar(
                texto: texto,
                adjuntos: adjuntos,
                alAceptar: alAceptar,
                client: client
            )
            self?.tareaGeneracion = nil
        }
        tareaGeneracion = task
    }

    func reintentarDesdeVista(mensajeId: String, client: APIClient) {
        tareaGeneracion?.cancel()
        let task = Task { [weak self] in
            _ = await self?.reintentar(mensajeId: mensajeId, client: client)
            self?.tareaGeneracion = nil
        }
        tareaGeneracion = task
    }

    /// Regenera la ultima respuesta del asistente: localiza el ultimo turno
    /// del usuario, descarta la respuesta del asistente que lo sigue y
    /// reenvia el mismo texto. No crea una burbuja nueva de usuario -- reusa
    /// la que ya esta en el hilo -- asi el resultado es un unico turno
    /// reescrito, no un duplicado. Los adjuntos del turno original ya fueron
    /// consumidos por el backend, asi que no se reenvian: el servidor los
    /// conserva en el mensaje que ya tiene.
    func regenerar(client: APIClient) async {
        guard !enviando, confirmacionPendiente == nil,
              let ultimoUsuarioIndex = mensajes.lastIndex(where: { $0.rol == .usuario })
        else { return }
        // Descarta la respuesta del asistente (lo que sigue al ultimo turno
        // del usuario). Solo se quitan mensajes de asistente: un posible
        // aviso `.sistema` intermedio se conserva.
        while mensajes.count > ultimoUsuarioIndex + 1,
              mensajes.last?.rol == .asistente {
            mensajes.removeLast()
        }
        // `ejecutarEnvio` reusa la burbuja de usuario (no crea otra) y genera
        // un `LogicalChatAttempt` fresco si el anterior ya se completo, asi
        // el backend ve un idempotency key nuevo en vez de rechazarlo como
        // ya procesado.
        _ = await ejecutarEnvio(
            mensajeId: mensajes[ultimoUsuarioIndex].id,
            client: client
        )
    }

    func regenerarDesdeVista(client: APIClient) {
        tareaGeneracion?.cancel()
        let task = Task { [weak self] in
            await self?.regenerar(client: client)
            self?.tareaGeneracion = nil
        }
        tareaGeneracion = task
    }

    func resolverConfirmacionDesdeVista(aprobado: Bool, client: APIClient) {
        tareaGeneracion?.cancel()
        let task = Task { [weak self] in
            await self?.resolverConfirmacion(aprobado: aprobado, client: client)
            self?.tareaGeneracion = nil
        }
        tareaGeneracion = task
    }

    /// Cancela el stream SSE en curso (envío, reintento o confirmación).
    /// La cancelación propaga el `CancellationError` por el `for await`, que
    /// los caminos existentes ya manejan sin pintar fallo. Como fue una parada
    /// explícita, marca ``detenidoPorUsuario`` para que la burbuja en curso se
    /// cierre en vez de quedar con el indicador de "escribiendo" colgado.
    func detenerGeneracion() {
        guard tareaGeneracion != nil else { return }
        detenidoPorUsuario = true
        tareaGeneracion?.cancel()
        tareaGeneracion = nil
    }

    private func ejecutarEnvio(mensajeId: String, client: APIClient) async -> Bool {
        guard confirmacionPendiente == nil,
              let indiceUsuario = mensajes.firstIndex(where: { $0.id == mensajeId })
        else { return false }
        let texto = mensajes[indiceUsuario].textoTransporte ?? mensajes[indiceUsuario].texto
        let attachmentIds = mensajes[indiceUsuario].adjuntos.map(\.fileId)
        let logicalAttempt = mensajes[indiceUsuario].logicalAttempt ?? LogicalChatAttempt()
        mensajes[indiceUsuario].logicalAttempt = logicalAttempt

        errorMensaje = nil

        if estaGenerando {
            return await encolarMensajeDuranteTurno(
                mensajeId: mensajeId,
                indiceUsuario: indiceUsuario,
                texto: texto,
                attachmentIds: attachmentIds,
                logicalAttempt: logicalAttempt,
                client: client
            )
        }

        enviando = true
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
                cerrarBurbujaAsistente(indiceRespuesta)
            }
            await cargarConversaciones(client: client)
            return true
        } catch is CancellationError {
            // Suspensión/cierre de la vista no es un fallo del trabajo. El
            // registro protegido queda listo para `scenePhase == .active` o
            // para el siguiente arranque.
            if let responseIndex = indiceRespuestaCreada,
               mensajes.indices.contains(responseIndex) {
                if detenidoPorUsuario {
                    // Parada explícita (botón Detener): cerramos la burbuja para
                    // que no quede con el indicador de "escribiendo" colgado.
                    // No se toca el registro de intento pendiente: el servidor
                    // puede seguir procesando y el resultado se recupera al
                    // volver a primer plano, igual que cualquier otra
                    // interrupción de red.
                    cerrarBurbujaAsistente(responseIndex)
                } else {
                    mensajes[responseIndex].enProgreso = true
                }
            }
            detenidoPorUsuario = false
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
            errorMensaje = Self.mensajeErrorUsuario(error.localizedDescription)
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
                cerrarBurbujaAsistente(indiceRespuesta)
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
        } catch is CancellationError {
            // Parada explícita (botón Detener) durante una confirmación: no es
            // un error ni un resultado ambiguo. El defer ya cerró la burbuja.
            if detenidoPorUsuario { detenidoPorUsuario = false }
        } catch {
            // Resultado ambiguo: conserva la tarjeta hasta consultar la
            // fuente de verdad. Si el backend ya la consumió, el GET devuelve
            // nil y el historial refleja el resultado; si sigue pendiente,
            // vuelve a mostrar exactamente la misma confirmación pública.
            confirmacionPendiente = pendienteDuranteEnvio
            errorMensaje = Self.mensajeErrorUsuario(error.localizedDescription)
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

    /// Encola un mensaje mientras el SSE del turno activo sigue abierto. El
    /// backend responde `202` con `{status:"queued"}`; el follow-up corre al
    /// cerrar el stream del turno en curso (evento `follow_up_turn`).
    private func encolarMensajeDuranteTurno(
        mensajeId: String,
        indiceUsuario: Int,
        texto: String,
        attachmentIds: [String],
        logicalAttempt: LogicalChatAttempt,
        client: APIClient
    ) async -> Bool {
        do {
            let conversationId = try await asegurarConversacion(client: client)
            let pendingAttempt = PendingChatAttempt(
                idempotencyKey: logicalAttempt.idempotencyKey,
                conversationId: conversationId,
                localMessageId: mensajeId
            )
            try? pendingAttemptStore.save(pendingAttempt)

            struct Body: Encodable {
                let text: String
                let attachments: [String]
            }
            let body = Body(text: texto, attachments: attachmentIds)
            let request = try await construirPeticionSSE(
                client: client,
                path: "/v1/conversations/\(conversationId)/messages",
                body: body,
                idempotencyKey: logicalAttempt.idempotencyKey
            )
            let encolado = try await postMensajeEncolado(request: request)
            guard encolado else {
                mensajes[indiceUsuario].falloEnvio = true
                errorMensaje = "No se pudo encolar el mensaje."
                return false
            }
            limpiarIntentoPendiente(siCoincide: logicalAttempt.idempotencyKey)
            marcarIntentoCompletado(mensajeId: mensajeId)
            return true
        } catch {
            mensajes[indiceUsuario].falloEnvio = true
            mensajes[indiceUsuario].logicalAttempt = nil
            errorMensaje = Self.mensajeErrorUsuario(error.localizedDescription)
            return false
        }
    }

    private func postMensajeEncolado(request: URLRequest) async throws -> Bool {
        var request = request
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw SSEClient.SSEError.respuestaInvalida
        }
        if http.statusCode == 202 {
            struct Queued: Decodable { let status: String }
            if let parsed = try? JSONDecoder().decode(Queued.self, from: data),
               parsed.status == "queued" {
                return true
            }
            return true
        }
        if http.statusCode == 409 {
            throw SSEClient.SSEError.servidor(status: http.statusCode, retryAfter: nil)
        }
        throw SSEClient.SSEError.servidor(
            status: http.statusCode,
            retryAfter: http.value(forHTTPHeaderField: "Retry-After").flatMap(TimeInterval.init)
        )
    }

    private func consumirStreamConRefresh<Cuerpo: Encodable>(
        client: APIClient,
        path: String,
        body: Cuerpo,
        idempotencyKey: UUID?,
        indiceRespuesta: Int
    ) async throws {
        estaGenerando = true
        defer { estaGenerando = false }
        var yaRefresco = false
        while true {
            let request = try await construirPeticionSSE(
                client: client,
                path: path,
                body: body,
                idempotencyKey: idempotencyKey
            )
            do {
                var indiceActual = indiceRespuesta
                for try await evento in sseClient.stream(request) {
                    if case .followUpTurn = evento {
                        if mensajes.indices.contains(indiceActual) {
                            cerrarBurbujaAsistente(indiceActual)
                        }
                        mensajes.append(Mensaje(rol: .asistente, texto: "", enProgreso: true))
                        indiceActual = mensajes.count - 1
                        herramientaActiva = nil
                        continue
                    }
                    if let missionId = aplicar(evento, indiceRespuesta: indiceActual) {
                        iniciarSeguimientoMision(
                            missionId: missionId,
                            mensajeId: mensajes[indiceActual].id,
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

    /// El turno ya no está escribiendo: congela el reloj del trabajo. Sin
    /// esto, `ProgresoTrabajoView` sigue usando `Date()` y un par de minutos
    /// de tools se convierten en 50.
    private func cerrarBurbujaAsistente(_ index: Int) {
        guard mensajes.indices.contains(index) else { return }
        mensajes[index].enProgreso = false
        mensajes[index].trabajo?.finalizar()
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
        mensajes[index].fuentes = []
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

    static func mensajeErrorUsuario(_ raw: String) -> String {
        let texto = raw.lowercased()
        if texto.contains("maximum context length") || texto.contains("input_tokens") {
            return "Este chat ya es demasiado largo para este modelo. Abre un chat nuevo o cambia de modelo."
        }
        if texto.contains("workers_ai") && (texto.contains("400") || texto.contains("8007")) {
            return "El modelo rechazó este turno. Prueba de nuevo o cambia de modelo."
        }
        return raw
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
            let falloHerramienta = resultado
                .trimmingCharacters(in: .whitespacesAndNewlines)
                .lowercased()
                .hasPrefix("error:")
            if !falloHerramienta { Haptico.exito() }
            for artefacto in artefactos
            where !mensajes[indiceRespuesta].artefactos.contains(where: { $0.fileId == artefacto.fileId }) {
                mensajes[indiceRespuesta].artefactos.append(artefacto)
            }
            if blocksVersion == 1 {
                for bloque in bloques where !mensajes[indiceRespuesta].bloques.contains(bloque) {
                    mensajes[indiceRespuesta].bloques.append(bloque)
                }
            }
            // Fuentes (citas) de `buscar_web`: `VistaFuentes` consolida todos los
            // hits; los bloques `link_preview` duplicados se filtran en la vista.
            if nombre == "buscar_web" {
                let nuevas = Self.fuentesDesdeToolEnd(resultado: resultado, bloques: bloques)
                for fuente in nuevas
                where !mensajes[indiceRespuesta].fuentes.contains(where: { $0.url == fuente.url }) {
                    mensajes[indiceRespuesta].fuentes.append(fuente)
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
            let apertura = mensajes[indiceRespuesta].textoApertura
            if !apertura.isEmpty {
                mensajes[indiceRespuesta].textoApertura = SpeechTags.ocultar(apertura)
            }
            let cuerpo = mensajes[indiceRespuesta].texto
            if !cuerpo.isEmpty {
                mensajes[indiceRespuesta].texto = SpeechTags.ocultar(cuerpo)
            }
        case .error(let mensaje):
            errorMensaje = Self.mensajeErrorUsuario(mensaje)
        case .followUpTurn:
            break
        case .unknown:
            break
        }
        return nil
    }

    private func iniciarSeguimientosPersistidos(client: APIClient) {
        // UN Task por misión, pero bajo la puerta de `limitadorMisionesChat`
        // (máximo 2): un hilo con N misiones activas lanzaría N peticiones
        // simultáneas por el túnel al abrir la conversación. Se acompañan en
        // fila y cada Task espera 3s entre polls.
        let pendientes = mensajes.compactMap { mensaje -> (String, String)? in
            guard let missionId = mensaje.trabajo?.missionId else { return nil }
            return (missionId, mensaje.id)
        }
        for (missionId, mensajeId) in pendientes {
            iniciarSeguimientoMision(missionId: missionId, mensajeId: mensajeId, client: client)
        }
    }

    private func iniciarSeguimientoMision(
        missionId: String,
        mensajeId: String,
        client: APIClient
    ) {
        guard seguimientoMisiones[missionId] == nil else { return }
        let miTurno = misionesArrancadas
        misionesArrancadas += 1
        seguimientoMisiones[missionId] = Task { [weak self] in
            guard let self else { return }
            // Momento frágil: un hilo con N misiones activas disparaba sus
            // polls a la vez. Cada misión espera su turno en la fila
            // (~600 ms por misión, máx 3 s de arranque): el pico de
            // peticiones queda escalonado sin tocar el MainActor.
            if miTurno > 0 {
                try? await Task.sleep(
                    for: .milliseconds(UInt64(miTurno) * 600)
                )
            }
            var fallosConsecutivos: UInt8 = 0
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
                            self.mensajes[index].trabajo?.finalizarForzado()
                        }
                        break
                    }
                }
                try? await Task.sleep(for: .seconds(3))
            }
            // El dict se limpia cuando la misión deja de ser activa, para
            // que un id nuevo con el mismo missionId pueda re-suscribirse.
            self.seguimientoMisiones[missionId] = nil
        }
    }

    /// Extrae fuentes de un `tool_end` de `buscar_web`. Combina los bloques
    /// `link_preview` (estructurados, hasta 3, con `site_name`) con el texto
    /// de `result_preview` (que trae TODOS los hits, no solo los 3 que se
    /// convirtieron en bloques) para reunir la lista completa sin duplicar
    /// por URL.
    ///
    /// El formato del `result_preview` de `buscar_web` es regular:
    /// `N. Title — URL\n   snippet` — ver `research.py:BuscarWebTool.run`.
    /// Puede venir truncado a 400 caracteres, así que el último hit del texto
    /// puede estar incompleto; se descarta si la URL no termina limpia.
    private static func fuentesDesdeToolEnd(resultado: String, bloques: [ChatBlock]) -> [Fuente] {
        var fuentes: [Fuente] = []
        var vistas: Set<String> = []

        // Primero los bloques estructurados: hasta 3, con dominio confiable
        // (`site_name` ya viene parseado del backend).
        for bloque in bloques {
            guard case .linkPreview(let link) = bloque else { continue }
            guard !vistas.contains(link.url) else { continue }
            vistas.insert(link.url)
            fuentes.append(Fuente(
                titulo: link.title,
                url: link.url,
                dominio: link.siteName,
                fragmento: link.description
            ))
        }

        // Después el texto: completa los hits que no llegaron como bloques
        // (el backend solo acuña hasta 3 `link_preview`; el resto vive solo
        // en el `content`/`result_preview`).
        let lineas = resultado.components(separatedBy: "\n")
        var i = 0
        while i < lineas.count {
            let linea = lineas[i]
            // Cabecera de hit: "N. Title — URL"
            guard let sepRange = linea.range(of: " — ") else { i += 1; continue }
            let headerPart = String(linea[..<sepRange.lowerBound])
            let urlPart = String(linea[sepRange.upperBound...]).trimmingCharacters(in: .whitespaces)
            guard let dotRange = headerPart.range(of: ". "),
                  headerPart[..<dotRange.lowerBound].allSatisfy(\.isNumber),
                  urlPart.hasPrefix("http"),
                  !vistas.contains(urlPart)
            else { i += 1; continue }
            let titulo = String(headerPart[dotRange.upperBound...])
            var fragmento: String? = nil
            if i + 1 < lineas.count {
                let siguiente = lineas[i + 1].trimmingCharacters(in: .whitespaces)
                // La línea siguiente es un snippet si no está vacía y no es
                // la cabecera del siguiente hit (que también empieza con "N. ").
                if !siguiente.isEmpty,
                   siguiente.range(of: #"^\d+\.\s"#, options: .regularExpression) == nil {
                    fragmento = siguiente
                }
            }
            vistas.insert(urlPart)
            fuentes.append(Fuente(titulo: titulo, url: urlPart, fragmento: fragmento))
            i += 1
        }

        return fuentes
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
            var fuentes: [Fuente] = []
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
                    if nombre == "buscar_web" {
                        let nuevas = Self.fuentesDesdeToolEnd(resultado: resultado, bloques: eventBlocks)
                        for fuente in nuevas
                        where !fuentes.contains(where: { $0.url == fuente.url }) {
                            fuentes.append(fuente)
                        }
                    }
                    if let missionId {
                        if trabajo == nil { trabajo = Trabajo(iniciadoEn: row.createdAt, pasos: []) }
                        trabajo?.vincularMision(missionId)
                    }
                default:
                    continue
                }
            }
            trabajo?.finalizarDesdeHistorial()
            return Mensaje(
                id: row.id,
                rol: role,
                texto: role == .asistente ? SpeechTags.ocultar(row.text) : row.text,
                artefactos: artifacts,
                bloques: blocks,
                adjuntos: row.attachments,
                trabajo: trabajo,
                fuentes: fuentes,
                createdAt: row.createdAt,
                pinned: row.pinned,
                bookmark: row.bookmark
            )
        }
    }
}
