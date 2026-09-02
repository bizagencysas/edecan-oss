import SwiftUI
import EdecanKit

/// Conversación multi-bot estilo Grok: turnos reales vía `POST /v1/teams/{id}/message`
/// + ``SSEClient`` (mismo pipeline que workers). Input nunca se bloquea; herramientas,
/// narración bot→bot y preguntas se reflejan en vivo sobre la Mac del dueño.
struct TeamConversationView: View {
    @Environment(SessionStore.self) private var session
    let equipo: Team

    @State private var items: [ItemMensajeEquipo] = []
    @State private var texto = ""
    @State private var cargando = true
    @State private var error: String?
    @State private var proximamente = false
    @State private var workers: [PersistentWorker] = []
    @State private var idsBotsActivos: Set<String> = []
    @State private var herramientaActiva: HerramientaEquipoActiva?
    @State private var confirmacionPendiente: ConfirmacionEquipoPendiente?
    @FocusState private var campoEnfocado: Bool
    @State private var anclaFinal = "team-final"

    private let sseClient = SSEClient()
    /// Tarea SSE cancelable con «Detener» (la más reciente en vuelo).
    @State private var tareaDetenible: Task<Void, Never>?
    /// Turnos HTTP/SSE activos; el botón Detener visible mientras > 0.
    @State private var turnosEnVuelo = 0
    @State private var detenidoPorUsuario = false
    @State private var respuestaIdEnCurso: String?
    @State private var sugerenciasProactivas: [AutomationSuggestion] = []
    @State private var sugerenciasOcultas: Set<String> = []

    var body: some View {
        VStack(spacing: 0) {
            TeamParallelMacBar(workers: workersDelEquipo, idsActivos: idsBotsActivos)
            if !sugerenciasVisibles.isEmpty {
                TeamNeedsYouPanel(
                    sugerencias: sugerenciasVisibles,
                    onDelegar: { sugerencia in
                        encolarEnvio(textoForzado: TeamNeedsYouPanel.promptDelegacion(sugerencia))
                    },
                    onDescartar: { sugerencia in
                        sugerenciasOcultas.insert(sugerencia.id)
                    }
                )
            }
            listaDeMensajes
            if let confirmacionPendiente {
                bannerConfirmacion(confirmacionPendiente)
            }
            if let error {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal)
                    .padding(.top, 6)
            }
            barraDeEntrada
        }
        .estiloPantallaBots()
        .navigationTitle(equipo.name.isEmpty ? "Equipo" : equipo.name)
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(.hidden, for: .navigationBar)
        .task { await cargar() }
        .refreshable { await refrescarContenido() }
    }

    private var turnoEnCurso: Bool { turnosEnVuelo > 0 }

    private var workersDelEquipo: [PersistentWorker] {
        let ids = Set(equipo.members.map(\.agentId))
        return workers.filter { ids.contains($0.id) }
    }

    /// Sugerencias del motor `proactive_scan` filtradas al equipo (product design).
    private var sugerenciasVisibles: [AutomationSuggestion] {
        let idsEquipo = Set(equipo.members.map(\.agentId))
        return sugerenciasProactivas.filter { sugerencia in
            guard !sugerenciasOcultas.contains(sugerencia.id) else { return false }
            guard let agentId = sugerencia.agentId, !agentId.isEmpty else { return true }
            return idsEquipo.contains(agentId)
        }
    }

    /// Mismo criterio que el backend (`team_members` ordenado: coordinador primero).
    private var responderDelEquipo: PersistentWorker? {
        for miembro in equipo.members {
            if let bot = worker(porId: miembro.agentId) { return bot }
        }
        return workersDelEquipo.first
    }

    private var listaDeMensajes: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 14) {
                    if proximamente {
                        EmptyStateView(
                            icono: "hourglass",
                            titulo: "Próximamente",
                            descripcion: "Los chats de equipo están llegando al servidor. Vuelve en un momento."
                        )
                        .padding(.top, 60)
                    } else if cargando && items.isEmpty {
                        ProgressView("Cargando la conversación…")
                            .frame(maxWidth: .infinity)
                            .padding(.top, 60)
                    } else if items.isEmpty {
                        estadoVacio
                    }
                    ForEach(filas) { fila in
                        switch fila {
                        case .mensaje(let item):
                            burbuja(item).id(item.id)
                        case .narracion(let nota):
                            TeamNarracionRow(nota: nota).id(nota.id)
                        case .pregunta(let bloque, let mensajeId):
                            TeamQuestionCardView(
                                bloque: bloque,
                                respuestaPosterior: respuestaPosterior(para: bloque, mensajeId: mensajeId)
                            ) { respuesta in
                                encolarEnvio(textoForzado: respuesta)
                            }
                            .id("q-\(clavePreguntaEquipo(bloque))-\(mensajeId)")
                        }
                    }
                    if let herramientaActiva {
                        TeamToolActivityRow(
                            nombreBot: herramientaActiva.nombreBot,
                            herramienta: herramientaActiva.nombre,
                            detalle: herramientaActiva.detalle
                        )
                        .id("tool-active")
                    }
                    Color.clear.frame(height: 1).id(anclaFinal)
                }
                .padding()
            }
            .scrollDismissesKeyboard(.interactively)
            .contentShape(Rectangle())
            .onTapGesture { campoEnfocado = false }
            .onChange(of: items.count) { _, _ in
                withAnimation(.easeOut(duration: 0.2)) { proxy.scrollTo(anclaFinal, anchor: .bottom) }
            }
            .onChange(of: herramientaActiva?.id) { _, _ in
                withAnimation(.easeOut(duration: 0.2)) { proxy.scrollTo(anclaFinal, anchor: .bottom) }
            }
        }
    }

    private var estadoVacio: some View {
        VStack(spacing: 16) {
            HStack(spacing: -8) {
                ForEach(workersDelEquipo.prefix(4)) { bot in
                    GrokFaceAvatar(bot: bot, size: 44, showOnline: false, animado: true, activo: false)
                }
            }
            .padding(.top, 24)

            Text(equipo.name.isEmpty ? "Tu equipo" : equipo.name)
                .font(.title3.weight(.bold))

            Text("Varios bots en paralelo en tu Mac — escribe y ellos coordinan solos.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 320)

            VStack(spacing: 8) {
                sugerencia("Investiguen X y me resumen en puntos")
                sugerencia("Revisen el repo y propongan mejoras")
                sugerencia("Uno investiga, otro redacta — ustedes deciden")
            }

            Text("Toca una sugerencia o escribe abajo. Puedes mandar otro mensaje mientras trabajan.")
                .font(.caption)
                .foregroundStyle(.tertiary)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 20)
        .padding(.bottom, 12)
    }

    private func sugerencia(_ texto: String) -> some View {
        Button {
            self.texto = texto
            campoEnfocado = true
        } label: {
            Text(texto)
                .font(.footnote.weight(.medium))
                .foregroundStyle(.primary)
                .padding(.horizontal, 14)
                .padding(.vertical, 8)
                .capsulaVidrio()
        }
        .buttonStyle(.plain)
    }

    private var filas: [FilaChatEquipo] {
        var resultado: [FilaChatEquipo] = []
        for item in items {
            if let ev = item.evento {
                if case .narracion(var nota) = resultado.last, nota.de == ev.de {
                    nota.mensajes.append(ev.goal)
                    nota.ultimo = ev.goal
                    resultado[resultado.count - 1] = .narracion(nota)
                } else {
                    resultado.append(
                        .narracion(TeamNotaNarracion(
                            id: item.id,
                            de: ev.de,
                            tipo: ev.escribioA ? "escribio" : "recibio",
                            cara: ev.cara,
                            mensajes: [ev.goal],
                            ultimo: ev.goal
                        ))
                    )
                }
            } else {
                resultado.append(.mensaje(item))
                for bloque in item.bloques {
                    if case .question(let pregunta) = bloque {
                        resultado.append(.pregunta(pregunta, item.id))
                    }
                }
            }
        }
        return resultado
    }

    @ViewBuilder
    private func burbuja(_ item: ItemMensajeEquipo) -> some View {
        if item.enProgreso && item.texto.isEmpty && item.textoApertura.isEmpty {
            FilaEstadoTrabajandoEquipo(
                bot: worker(porId: item.botId) ?? workersDelEquipo.first,
                nombre: item.nombreRemitente ?? "Un bot"
            )
        } else if item.esUsuario {
            HStack(alignment: .top, spacing: 0) {
                Spacer(minLength: 56)
                Text(item.texto)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .foregroundStyle(.primary)
                    .tarjetaVidrio(esquina: 18, tint: EdecanTheme.morado)
                    .frame(maxWidth: 340, alignment: .trailing)
            }
            .frame(maxWidth: .infinity, alignment: .trailing)
        } else {
            HStack(alignment: .top, spacing: 8) {
                if let bot = worker(porId: item.botId) {
                    GrokFaceAvatar(
                        bot: bot, size: 30, showOnline: false, animado: true, activo: item.enProgreso
                    )
                } else {
                    avatarBotDesconocido(activo: item.enProgreso)
                }
                VStack(alignment: .leading, spacing: 6) {
                    if !item.nombreRemitente.isEmptyOrNil {
                        Text(item.nombreRemitente ?? "")
                            .font(.caption2.weight(.medium))
                            .foregroundStyle(.secondary)
                    }
                    if !item.textoApertura.isEmpty {
                        textoRico(item.textoApertura)
                            .foregroundStyle(.secondary)
                    }
                    if !item.texto.isEmpty {
                        textoRico(item.texto)
                    }
                }
                Spacer(minLength: 40)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func textoRico(_ texto: String) -> Text {
        let atribuido = (try? AttributedString(markdown: texto)) ?? AttributedString(texto)
        return Text(atribuido).font(.subheadline)
    }

    private func bannerConfirmacion(_ pendiente: ConfirmacionEquipoPendiente) -> some View {
        HStack(spacing: 10) {
            Image(systemName: "exclamationmark.shield.fill")
                .foregroundStyle(.orange)
            VStack(alignment: .leading, spacing: 2) {
                Text("Acción sensible: \(pendiente.nombre.replacingOccurrences(of: "_", with: " "))")
                    .font(.caption.weight(.semibold))
                Text("Confirma o rechaza para que el equipo continúe.")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
            Button("Rechazar") {
                confirmar(pendiente, aprobado: false)
            }
            .font(.caption.weight(.semibold))
            Button("Aprobar") {
                confirmar(pendiente, aprobado: true)
            }
            .font(.caption.weight(.bold))
            .buttonStyle(.borderedProminent)
            .tint(EdecanTheme.morado)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(.ultraThinMaterial)
    }

    private var barraDeEntrada: some View {
        ContenedorVidrioBots(spacing: 10) {
            HStack(alignment: .bottom, spacing: 10) {
                TextField("Escríbele al equipo…", text: $texto, axis: .vertical)
                    .lineLimit(1...5)
                    .focused($campoEnfocado)
                    .submitLabel(.send)
                    .onSubmit {
                        guard botonHabilitado else { return }
                        encolarEnvio()
                    }
                    .textFieldStyle(.plain)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 11)
                    .capsulaVidrio()

                TeamSendStopButton(
                    habilitadoEnviar: botonHabilitado,
                    turnoEnCurso: turnoEnCurso,
                    onEnviar: { encolarEnvio() },
                    onDetener: { detenerTurno() }
                )
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
        }
        .safeAreaPadding(.bottom, 4)
    }

    private var botonHabilitado: Bool {
        !texto.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private func encolarEnvio(textoForzado: String? = nil) {
        let limpio = (textoForzado ?? texto).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !limpio.isEmpty, let client = session.client else { return }
        if textoForzado == nil { texto = "" }
        campoEnfocado = false
        detenidoPorUsuario = false

        items.append(ItemMensajeEquipo(id: UUID().uuidString, esUsuario: true, texto: limpio))

        // Envío INMEDIATO (app.md L149–151): no cancelar el turno anterior; el
        // servidor secuencia con lock por conversación/equipo.
        let task = Task {
            await hacerEnvio(client: client, texto: limpio)
        }
        tareaDetenible = task
    }

    /// Cancela el stream SSE en curso (envío o confirmación). Paridad con ChatView.
    private func detenerTurno() {
        guard turnoEnCurso || tareaDetenible != nil else { return }
        detenidoPorUsuario = true
        tareaDetenible?.cancel()
        tareaDetenible = nil
    }

    private func refrescarContenido() async {
        guard let client = session.client else { return }
        await cargar()
        await cargarSugerenciasProactivas(client: client)
    }

    private func cargar() async {
        guard let client = session.client else {
            error = "No hay sesión activa."
            cargando = false
            return
        }
        cargando = items.isEmpty
        error = nil
        proximamente = false
        defer { cargando = false }
        do {
            let historial = try await client.listTeamMessages(teamId: equipo.id)
            if items.isEmpty || !turnoEnCurso {
                items = historial.map { ItemMensajeEquipo.desde($0, workers: workersDelEquipo) }
            }
            workers = (try? await client.listWorkers()) ?? []
            if items.isEmpty || !turnoEnCurso {
                items = historial.map { ItemMensajeEquipo.desde($0, workers: workersDelEquipo) }
            }
            await cargarSugerenciasProactivas(client: client)
        } catch let apiError as APIClient.APIError {
            if apiError.esProximamente {
                proximamente = true
            } else {
                self.error = apiError.localizedDescription
            }
        } catch {
            self.error = error.localizedDescription
        }
    }

    /// Motor de fondo `proactive_scan` → `GET /v1/automations/suggestions` (HANDOFF L394–398).
    private func cargarSugerenciasProactivas(client: APIClient) async {
        do {
            let todas = try await client.listAutomationSuggestions()
            sugerenciasProactivas = todas.filter { item in
                let etapa = SuggestionStage(item.stage)
                return etapa == .action || etapa == .suggestion || etapa == .draft
                    || item.failureCount != nil
            }
        } catch {
            // Degradación silenciosa: el chat sigue sin el panel Needs you.
        }
    }

    private func hacerEnvio(client: APIClient, texto: String) async {
        turnosEnVuelo += 1
        defer {
            turnosEnVuelo = max(0, turnosEnVuelo - 1)
            respuestaIdEnCurso = nil
        }

        var respuestaId = abrirBurbujaBot(responderDelEquipo)
        respuestaIdEnCurso = respuestaId

        var textoParcial = ""
        var ultimoPintado = Date.distantPast
        func pintar(final: Bool = false) {
            let ahora = Date()
            if final || ahora.timeIntervalSince(ultimoPintado) > 0.12 {
                ultimoPintado = ahora
                guard let indice = items.firstIndex(where: { $0.id == respuestaId }) else { return }
                items[indice].texto = textoParcial
            }
        }

        func botDelTurnoActual() -> PersistentWorker? {
            worker(porId: items.first(where: { $0.id == respuestaId })?.botId) ?? responderDelEquipo
        }

        do {
            let request = try await client.peticionMensajeEquipo(teamId: equipo.id, text: texto)
            for try await evento in sseClient.stream(request) {
                guard items.firstIndex(where: { $0.id == respuestaId }) != nil else { break }
                switch evento {
                case .textDelta(let delta):
                    textoParcial += delta
                    pintar()
                case .toolStart(_, let nombre, let args):
                    if nombre == "enviar_mensaje_bot" {
                        let destino = cadenaEnArgs(args, clave: "bot") ?? ""
                        let mensaje = cadenaEnArgs(args, clave: "mensaje")
                        let emisorId = items.first(where: { $0.id == respuestaId })?.botId
                        if !destino.isEmpty {
                            registrarNarracionEntreBots(
                                emisorId: emisorId,
                                destinoNombre: destino,
                                mensaje: mensaje
                            )
                        }
                    }
                    herramientaActiva = HerramientaEquipoActiva(
                        id: UUID().uuidString,
                        nombre: nombre,
                        detalle: "Ejecutando…",
                        nombreBot: nombreBotActivo(porId: botDelTurnoActual()?.id)
                    )
                    if let indice = items.firstIndex(where: { $0.id == respuestaId }),
                       !items[indice].texto.isEmpty {
                        let intermedio = items[indice].texto
                        items[indice].texto = ""
                        let apertura = items[indice].textoApertura
                        items[indice].textoApertura =
                            apertura.isEmpty ? intermedio : "\(apertura)\n\n\(intermedio)"
                        textoParcial = ""
                    }
                case .toolProgress(_, let nombre, _, let detalle):
                    herramientaActiva = HerramientaEquipoActiva(
                        id: herramientaActiva?.id ?? UUID().uuidString,
                        nombre: nombre,
                        detalle: detalle,
                        nombreBot: nombreBotActivo(porId: botDelTurnoActual()?.id)
                    )
                case .toolEnd(_, let nombre, let preview, _, _, let bloques, let missionId):
                    herramientaActiva = nil
                    if let indice = items.firstIndex(where: { $0.id == respuestaId }) {
                        for bloque in bloques where !items[indice].bloques.contains(bloque) {
                            items[indice].bloques.append(bloque)
                        }
                    }
                    let botTurno = botDelTurnoActual()
                    if nombre == "delegar_mision", let missionId {
                        items.append(
                            ItemMensajeEquipo(
                                id: UUID().uuidString,
                                esUsuario: false,
                                texto: "Misión encolada (\(missionId.prefix(8))…). Sigue en segundo plano en tu Mac.",
                                nombreRemitente: botTurno?.nombreVisible,
                                botId: botTurno?.id
                            )
                        )
                    } else if !preview.isEmpty, preview.lowercased().hasPrefix("error:") {
                        items.append(
                            ItemMensajeEquipo(
                                id: UUID().uuidString,
                                esUsuario: false,
                                texto: preview,
                                nombreRemitente: botTurno?.nombreVisible,
                                botId: botTurno?.id
                            )
                        )
                    }
                case .followUpTurn:
                    pintar(final: true)
                    finalizarBurbuja(respuestaId, texto: textoParcial)
                    textoParcial = ""
                    herramientaActiva = nil
                    respuestaId = abrirBurbujaBot(botDelTurnoActual())
                case .confirmationRequired(let toolCallId, let nombre, let args):
                    herramientaActiva = nil
                    confirmacionPendiente = ConfirmacionEquipoPendiente(
                        toolCallId: toolCallId,
                        nombre: nombre,
                        args: args,
                        conversationId: equipo.conversationId
                    )
                case .done:
                    pintar(final: true)
                    if let indice = items.firstIndex(where: { $0.id == respuestaId }) {
                        items[indice].enProgreso = false
                    }
                    herramientaActiva = nil
                    idsBotsActivos.removeAll()
                    await cargar()
                    await cargarSugerenciasProactivas(client: client)
                case .error(let mensaje):
                    pintar(final: true)
                    if let indice = items.firstIndex(where: { $0.id == respuestaId }) {
                        items[indice].enProgreso = false
                        if items[indice].texto.isEmpty {
                            items[indice].texto =
                                "Ups, se enredó a mitad de camino: \(mensaje)"
                        }
                    }
                    herramientaActiva = nil
                    idsBotsActivos.removeAll()
                default:
                    break
                }
            }
            quitarBurbujaVacia(respuestaId)
        } catch is CancellationError {
            if detenidoPorUsuario, let id = respuestaIdEnCurso {
                cerrarBurbujaPorDetencion(id)
            }
            herramientaActiva = nil
            idsBotsActivos.removeAll()
            detenidoPorUsuario = false
        } catch let apiError as APIClient.APIError {
            if apiError.esProximamente {
                proximamente = true
            } else {
                self.error = apiError.localizedDescription
            }
            quitarBurbujaVacia(respuestaId)
            herramientaActiva = nil
            idsBotsActivos.removeAll()
        } catch {
            self.error = error.localizedDescription
            quitarBurbujaVacia(respuestaId)
            herramientaActiva = nil
            idsBotsActivos.removeAll()
        }
    }

    private func confirmar(_ pendiente: ConfirmacionEquipoPendiente, aprobado: Bool) {
        guard let client = session.client else {
            error = "No pude confirmar: falta sesión activa."
            return
        }
        guard pendiente.conversationId ?? equipo.conversationId != nil else {
            error = "No pude confirmar: falta el id de conversación del equipo."
            return
        }
        confirmacionPendiente = nil
        detenidoPorUsuario = false
        let task = Task {
            await confirmarEnCadena(client: client, pendiente: pendiente, aprobado: aprobado)
        }
        tareaDetenible = task
    }

    private func confirmarEnCadena(
        client: APIClient,
        pendiente: ConfirmacionEquipoPendiente,
        aprobado: Bool
    ) async {
        turnosEnVuelo += 1
        defer { turnosEnVuelo = max(0, turnosEnVuelo - 1) }
        guard let conversationId = pendiente.conversationId ?? equipo.conversationId else {
            error = "No pude confirmar: falta el id de conversación del equipo."
            confirmacionPendiente = pendiente
            return
        }
        do {
            let request = try await client.peticionConfirmarConversacion(
                conversationId: conversationId,
                toolCallId: pendiente.toolCallId,
                approved: aprobado
            )
            for try await evento in sseClient.stream(request) {
                switch evento {
                case .done, .error:
                    break
                default:
                    continue
                }
            }
            confirmacionPendiente = nil
            await cargar()
            if let client = session.client {
                await cargarSugerenciasProactivas(client: client)
            }
        } catch is CancellationError {
            if detenidoPorUsuario {
                confirmacionPendiente = pendiente
            }
            detenidoPorUsuario = false
        } catch let apiError as APIClient.APIError {
            if apiError.esConfirmacionExpirada {
                // app.md L3087–3097: Detener + confirmar no debe colgar ni 500.
                confirmacionPendiente = nil
                error = nil
                await cargar()
            } else {
                self.error = apiError.localizedDescription
                confirmacionPendiente = pendiente
            }
        } catch let sseError as SSEClient.SSEError {
            if sseError.esConfirmacionExpirada {
                confirmacionPendiente = nil
                error = nil
                await cargar()
            } else {
                self.error = "No pude registrar tu decisión. Intenta de nuevo."
                confirmacionPendiente = pendiente
            }
        } catch {
            self.error = "No pude registrar tu decisión. Intenta de nuevo."
            confirmacionPendiente = pendiente
        }
    }

    private func cerrarBurbujaPorDetencion(_ id: String) {
        guard let indice = items.firstIndex(where: { $0.id == id }) else { return }
        items[indice].enProgreso = false
        if items[indice].texto.isEmpty && items[indice].textoApertura.isEmpty {
            items[indice].texto = "Turno detenido."
        }
    }

    @ViewBuilder
    private func avatarBotDesconocido(activo: Bool) -> some View {
        ZStack {
            Circle().fill(EdecanTheme.morado.opacity(0.18))
            Image(systemName: "sparkles")
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(EdecanTheme.morado)
        }
        .frame(width: 30, height: 30)
        .opacity(activo ? 1 : 0.85)
    }

    private func quitarBurbujaVacia(_ id: String) {
        guard let indice = items.firstIndex(where: { $0.id == id }),
              items[indice].texto.isEmpty,
              items[indice].textoApertura.isEmpty,
              items[indice].bloques.isEmpty
        else { return }
        items.remove(at: indice)
    }

    private func worker(porId id: String?) -> PersistentWorker? {
        guard let id else { return nil }
        return workers.first(where: { $0.id == id })
    }

    private func worker(porNombre nombre: String) -> PersistentWorker? {
        let q = nombre.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !q.isEmpty else { return nil }
        return workersDelEquipo.first { bot in
            let visible = bot.nombreVisible.lowercased()
            return visible.contains(q) || q.contains(visible)
        }
    }

    private func cadenaEnArgs(_ args: [String: JSONValue], clave: String) -> String? {
        guard case .string(let valor)? = args[clave] else { return nil }
        let limpio = valor.trimmingCharacters(in: .whitespacesAndNewlines)
        return limpio.isEmpty ? nil : limpio
    }

    private func nombreBotActivo(porId id: String?) -> String {
        worker(porId: id)?.nombreVisible ?? responderDelEquipo?.nombreVisible ?? "Bot"
    }

    @discardableResult
    private func abrirBurbujaBot(_ bot: PersistentWorker?) -> String {
        let id = UUID().uuidString
        items.append(
            ItemMensajeEquipo(
                id: id,
                esUsuario: false,
                texto: "",
                nombreRemitente: bot?.nombreVisible,
                botId: bot?.id,
                enProgreso: true
            )
        )
        if let bot { idsBotsActivos.insert(bot.id) }
        return id
    }

    private func finalizarBurbuja(_ id: String, texto: String) {
        guard let indice = items.firstIndex(where: { $0.id == id }) else { return }
        if !texto.isEmpty { items[indice].texto = texto }
        items[indice].enProgreso = false
    }

    /// Narración bot→bot en vivo (HANDOFF: `kind=evento`; product design: no simular).
    private func registrarNarracionEntreBots(
        emisorId: String?,
        destinoNombre: String,
        mensaje: String?
    ) {
        let emisor = worker(porId: emisorId)?.nombreVisible ?? "Un bot"
        let goal: String = {
            if let mensaje, !mensaje.isEmpty { return mensaje }
            return "Escribió a \(destinoNombre)"
        }()
        items.append(
            ItemMensajeEquipo(
                id: UUID().uuidString,
                esUsuario: false,
                texto: "",
                evento: EventoNarracionEquipo(
                    de: emisor,
                    goal: goal,
                    escribioA: true,
                    cara: nil
                )
            )
        )
        if let destino = worker(porNombre: destinoNombre) {
            idsBotsActivos.insert(destino.id)
        }
    }

    private func respuestaPosterior(para bloque: QuestionBlock, mensajeId: String) -> String? {
        guard let indice = items.firstIndex(where: { $0.id == mensajeId }) else { return nil }
        let siguientes = items[(indice + 1)...]
        return siguientes.first(where: { $0.esUsuario })?.texto
    }
}
// MARK: - Modelos locales

private struct HerramientaEquipoActiva: Identifiable {
    let id: String
    let nombre: String
    let detalle: String
    let nombreBot: String
}

private struct ConfirmacionEquipoPendiente {
    let toolCallId: String
    let nombre: String
    let args: [String: JSONValue]
    let conversationId: String?
}

private struct EventoNarracionEquipo {
    let de: String
    let goal: String
    let escribioA: Bool
    let cara: CaraSnapshot?
}

private struct ItemMensajeEquipo: Identifiable {
    let id: String
    var esUsuario: Bool
    var texto: String
    var textoApertura: String
    var nombreRemitente: String?
    var botId: String?
    var enProgreso: Bool
    var bloques: [ChatBlock]
    var evento: EventoNarracionEquipo?

    init(
        id: String,
        esUsuario: Bool,
        texto: String,
        textoApertura: String = "",
        nombreRemitente: String? = nil,
        botId: String? = nil,
        enProgreso: Bool = false,
        bloques: [ChatBlock] = [],
        evento: EventoNarracionEquipo? = nil
    ) {
        self.id = id
        self.esUsuario = esUsuario
        self.texto = texto
        self.textoApertura = textoApertura
        self.nombreRemitente = nombreRemitente
        self.botId = botId
        self.enProgreso = enProgreso
        self.bloques = bloques
        self.evento = evento
    }

    static func desde(_ mensaje: TeamMessage, workers: [PersistentWorker]) -> ItemMensajeEquipo {
        var evento: EventoNarracionEquipo?
        if mensaje.kind == "evento", let ev = mensaje.evento, !ev.isEmpty {
            evento = EventoNarracionEquipo(
                de: mensaje.de ?? "",
                goal: mensaje.goal ?? "",
                escribioA: ev == "escribio_a",
                cara: mensaje.cara
            )
        }
        let botId = mensaje.senderId
        let nombre = mensaje.esDelDueno
            ? nil
            : (mensaje.senderName ?? workers.first(where: { $0.id == botId })?.nombreVisible)
        return ItemMensajeEquipo(
            id: mensaje.id,
            esUsuario: mensaje.esDelDueno,
            texto: mensaje.text,
            nombreRemitente: nombre,
            botId: botId,
            evento: evento
        )
    }
}

private func clavePreguntaEquipo(_ bloque: QuestionBlock) -> String {
    bloque.question + "|" + bloque.options.map(\.id).joined(separator: ",")
}

private enum FilaChatEquipo: Identifiable {
    case mensaje(ItemMensajeEquipo)
    case narracion(TeamNotaNarracion)
    case pregunta(QuestionBlock, String)

    var id: String {
        switch self {
        case .mensaje(let item): item.id
        case .narracion(let nota): nota.id
        case .pregunta(let bloque, let mensajeId): "q-\(clavePreguntaEquipo(bloque))-\(mensajeId)"
        }
    }
}

private struct FilaEstadoTrabajandoEquipo: View {
    let bot: PersistentWorker?
    let nombre: String

    var body: some View {
        HStack(alignment: .center, spacing: 10) {
            if let bot {
                GrokFaceAvatar(bot: bot, size: 30, showOnline: false, animado: true, activo: true)
            } else {
                ZStack {
                    Circle().fill(EdecanTheme.morado.opacity(0.18))
                    Image(systemName: "sparkles")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(EdecanTheme.morado)
                }
                .frame(width: 30, height: 30)
            }
            HStack(spacing: 0) {
                Text("\(nombre) está ")
                    .foregroundStyle(.secondary)
                Text("trabajando")
                    .foregroundStyle(.primary.opacity(0.88))
                    .fontWeight(.medium)
            }
            .font(.subheadline)
            Spacer(minLength: 0)
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(EstadoTrabajandoChat.etiquetaAccesibilidad(nombreAgente: nombre))
    }
}

private extension Optional where Wrapped == String {
    var isEmptyOrNil: Bool {
        guard let self else { return true }
        return self.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
}

private extension APIClient.APIError {
    /// Confirmación ya consumida o expirada (`POST .../confirm` → 409).
    var esConfirmacionExpirada: Bool {
        guard case .servidor(let status, let mensaje) = self else { return false }
        if status == 409 { return true }
        let lower = mensaje.lowercased()
        return lower.contains("confirmación") && lower.contains("disponible")
    }
}

private extension SSEClient.SSEError {
    var esConfirmacionExpirada: Bool {
        guard case .servidor(let status, _) = self else { return false }
        return status == 409
    }
}
