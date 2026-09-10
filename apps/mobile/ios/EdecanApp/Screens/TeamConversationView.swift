import SwiftUI
import EdecanKit

/// Conversación multi-bot estilo Grok: turnos reales vía `POST /v1/teams/{id}/message`
/// + ``SSEClient`` (mismo pipeline que workers). Input nunca se bloquea; herramientas,
/// narración bot→bot y preguntas se reflejan en vivo sobre la Mac del dueño.
struct TeamConversationView: View {
    @Environment(SessionStore.self) private var session
    @Environment(\.scenePhase) private var scenePhase
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
    /// El primer mensaje de un grupo crea su conversación en el servidor.
    /// El `Team` recibido por navegación es un snapshot y puede traer nil;
    /// este estado conserva el id actualizado para confirmaciones posteriores.
    @State private var conversationIdActual: String?
    @State private var sugerenciasProactivas: [AutomationSuggestion] = []
    @State private var sugerenciasOcultas: Set<String> = []
    /// BOTS-11: tool_calls ya decididos en esta sesión. El backend NO marca
    /// `pending_approvals` como resuelta en la ruta SSE `/confirm` (solo lo
    /// hace `/v1/approvals/{id}/approve`), así que la reconciliación local
    /// evita re-mostrar la tarjeta de algo que acabamos de decidir.
    @State private var toolCallsDecididos: Set<String> = []
    /// AUD-12a: historial navegable por cursor (`before`/`next_cursor`), no por
    /// ventana creciente. `limiteHistorial` es el TAMAÑO DE PÁGINA fijo; el
    /// cursor opaco viaja en `cursorHistorial` (jamás reconstruido en cliente).
    @State private var limiteHistorial = 50
    @State private var cursorHistorial: String?
    @State private var cargandoMasHistorial = false
    /// Empieza en `false`: la primera carga confirma si hay historial previo
    /// (evita el parpadeo del botón en un hilo todavía vacío).
    @State private var hayMasHistorial = false

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
        .task {
            await cargar()
            await cargarAprobacionesPendientes()
        }
        .refreshable { await refrescarContenido() }
        .onChange(of: scenePhase) { _, fase in
            if fase == .active {
                Task { await cargarAprobacionesPendientes() }
            }
        }
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
                    if hayMasHistorial {
                        Button {
                            Task { await cargarMasHistorial() }
                        } label: {
                            if cargandoMasHistorial {
                                ProgressView()
                                    .controlSize(.small)
                            } else {
                                Text("Cargar mensajes anteriores")
                                    .font(.footnote.weight(.medium))
                            }
                        }
                        .buttonStyle(.plain)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 6)
                        .foregroundStyle(EdecanTheme.morado)
                        .accessibilityLabel("Cargar mensajes anteriores")
                    }
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
                        case .asignacion(let nombre, let workerId, let cara):
                            AsignadoAChipView(
                                nombreBot: nombre,
                                worker: worker(porId: workerId),
                                cara: cara
                            )
                            .id("asignacion-\(workerId)")
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
                // Al cargar mensajes anteriores no se baja el scroll: se
                // preserva la posición de lectura (BOTS-12).
                guard !cargandoMasHistorial else { return }
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
            if item.esAsignacion {
                resultado.append(
                    .asignacion(
                        item.asignacionNombre ?? "Bot",
                        item.asignacionWorkerId ?? item.botId ?? UUID().uuidString,
                        item.asignacionCara
                    )
                )
                continue
            }
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
                            tipo: ev.asignacion
                                ? "asignacion"
                                : (ev.escribioA ? "escribio" : "recibio"),
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
        if item.interrumpido {
            // F5: turno matado por un reinicio del servidor. No es error de red
            // ni turno «atascado»: el toque reenvía con clave NUEVA.
            Button {
                resenviarEquipoInterrumpido(id: item.id, texto: item.textoOriginal ?? "")
            } label: {
                HStack(alignment: .top, spacing: 8) {
                    if let bot = worker(porId: item.botId) {
                        GrokFaceAvatar(bot: bot, size: 30, showOnline: false, animado: false, activo: false)
                    } else {
                        avatarBotDesconocido(activo: false)
                    }
                    VStack(alignment: .leading, spacing: 4) {
                        Text(item.texto)
                            .font(.subheadline)
                            .fixedSize(horizontal: false, vertical: true)
                        Text("El turno se cortó por un reinicio del servidor. Tócala para reenviarlo.")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 40)
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .tarjetaVidrio(esquina: 18, tint: nil)
                .frame(maxWidth: 340, alignment: .leading)
            }
            .buttonStyle(.plain)
        } else if item.enProgreso && item.texto.isEmpty && item.textoApertura.isEmpty {
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
        } else if item.esBeat {
            HStack(alignment: .top, spacing: 8) {
                if let bot = worker(porId: item.botId) {
                    GrokFaceAvatar(bot: bot, size: 24, showOnline: false, animado: true, activo: false)
                } else {
                    avatarBotDesconocido(activo: false)
                }
                textoRico(item.texto)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 8)
                    .background(EdecanTheme.morado.opacity(0.08), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                    .overlay(
                        RoundedRectangle(cornerRadius: 14, style: .continuous)
                            .strokeBorder(EdecanTheme.morado.opacity(0.35), lineWidth: 0.5)
                    )
                Spacer(minLength: 40)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
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
                Text(
                    "Acción sensible: \(BeatHerramientaCopy.mensaje(nombre: pendiente.nombre, progreso: nil).replacingOccurrences(of: "…", with: ""))"
                )
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

        let idUsuario = UUID().uuidString
        items.append(ItemMensajeEquipo(id: idUsuario, esUsuario: true, texto: limpio))

        // Identidad del envío generada UNA vez (BOTS-10): reintentos reutilizan
        // la MISMA Idempotency-Key hasta el estado terminal.
        let clave = UUID().uuidString

        // Envío INMEDIATO (app.md L149–151): no cancelar el turno anterior; el
        // servidor secuencia con lock por conversación/equipo.
        let task = Task {
            await hacerEnvio(client: client, texto: limpio, idempotencyKey: clave)
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
        if conversationIdActual == nil {
            conversationIdActual = equipo.conversationId
        }
        defer { cargando = false }
        do {
            let pagina = try await client.listTeamMessagesPage(teamId: equipo.id, limit: limiteHistorial)
            let historial = pagina.messages
            workers = (try? await client.listWorkers()) ?? []
            _ = await actualizarConversationIdDesdeServidor(client: client)
            if items.isEmpty || !turnoEnCurso {
                items = historial.map { ItemMensajeEquipo.desde($0, workers: workersDelEquipo) }
            }
            hayMasHistorial = pagina.hasMore
            cursorHistorial = pagina.nextCursor
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

    /// AUD-12a: carga mensajes anteriores usando el CURSOR del último lote
    /// (`before` = `cursorHistorial`), nunca ensanchando `limit`. Prependiendo
    /// solo los que faltan (dedupe por id). No toca el scroll.
    private func cargarMasHistorial() async {
        guard !cargandoMasHistorial, hayMasHistorial,
              let cursor = cursorHistorial,
              let client = session.client else { return }
        cargandoMasHistorial = true
        defer {
            // Se conserva el flag un turno de run loop para que el
            // `.onChange(of: items.count)` NO auto-scrollee al fondo al
            // prepender (posición de lectura estable).
            Task { @MainActor in
                try? await Task.sleep(for: .milliseconds(60))
                cargandoMasHistorial = false
            }
        }
        do {
            let pagina = try await client.listTeamMessagesPage(
                teamId: equipo.id, limit: limiteHistorial, before: cursor
            )
            let fresco = pagina.messages.map { ItemMensajeEquipo.desde($0, workers: workersDelEquipo) }
            let idsExistentes = Set(items.map(\.id))
            let anteriores = fresco.filter { !idsExistentes.contains($0.id) }
            if !anteriores.isEmpty {
                items.insert(contentsOf: anteriores, at: 0)
            }
            cursorHistorial = pagina.nextCursor
            hayMasHistorial = pagina.hasMore
        } catch {
            // Degradación silenciosa: el botón sigue disponible para reintentar.
        }
    }

    /// BOTS-11: restaura la tarjeta de aprobación pendiente desde el servidor
    /// (`pending_approvals`, filtrado por conversation_id) al abrir o volver a
    /// primer plano. No pisa una confirmación que acaba de llegar por SSE.
    private func cargarAprobacionesPendientes(client: APIClient? = nil) async {
        let c = client ?? session.client
        guard let c else { return }
        guard confirmacionPendiente == nil else { return }
        let conversationId = conversationIdActual ?? equipo.conversationId
        guard let conversationId else { return }
        guard let pendientes = try? await c.listApprovals(conversationId: conversationId),
              let pendiente = pendientes.first(where: {
                  guard let tc = $0.toolCallId, !tc.isEmpty else { return false }
                  return !toolCallsDecididos.contains(tc)
              }) else { return }
        confirmacionPendiente = ConfirmacionEquipoPendiente(
            toolCallId: pendiente.toolCallId ?? "",
            nombre: pendiente.name ?? "",
            args: pendiente.args ?? [:],
            conversationId: pendiente.conversationId ?? conversationId
        )
    }

    private func hacerEnvio(
        client: APIClient,
        texto: String,
        idempotencyKey: String
    ) async {
        turnosEnVuelo += 1
        var estadoLocalFinalizado = false
        defer {
            if !estadoLocalFinalizado { finalizarEstadoLocalDelTurno() }
        }

        // Burbujas de ESTE turno (paridad E-IOS-2 de BotChatView): si la red
        // cae y se reconecta con la misma Idempotency-Key, el replay las
        // retira y pinta de cero — sin duplicar avisos ni respuestas. La
        // burbuja optimista del usuario NO se incluye (es un mensaje ya
        // persistido y con texto: nunca se retira).
        var burbujasDelTurno: Set<String> = []
        var estado = EstadoStreamEquipo(respuestaId: abrirBurbujaBot(responderDelEquipo))
        burbujasDelTurno.insert(estado.respuestaId)
        var resultado: ResultadoStreamEquipo = .continuar

        do {
            let request = try await client.peticionMensajeEquipo(
                teamId: equipo.id, text: texto, idempotencyKey: idempotencyKey
            )
            for try await evento in sseClient.stream(request) {
                guard items.firstIndex(where: { $0.id == estado.respuestaId }) != nil else { break }
                let actual = procesarEventoDelTurno(evento, estado: &estado, burbujasDelTurno: &burbujasDelTurno)
                if actual != .continuar { resultado = actual }
            }
quitarBurbujaVacia(estado.respuestaId)
            finalizarEstadoLocalDelTurno()
            estadoLocalFinalizado = true

            _ = await actualizarConversationIdDesdeServidor(client: client)
            if resultado == .terminado {
                // El contador ya bajó: `cargar()` puede reconciliar el
                // historial que el servidor acaba de persistir.
                await cargar()
            }
        } catch is CancellationError {
            if detenidoPorUsuario {
                cerrarBurbujaPorDetencion(estado.respuestaId)
            }
            herramientaActiva = nil
            idsBotsActivos.removeAll()
            detenidoPorUsuario = false
        } catch let apiError as APIClient.APIError {
            // Error local al armar la petición (token/URL): rechazo definitivo.
            marcarEnvioEquipoRechazado(
                respuestaId: estado.respuestaId,
                burbujasDelTurno,
                texto: texto,
                mensaje: apiError.localizedDescription
            )
        } catch let sseError as SSEClient.SSEError {
            if resultado == .esperandoConfirmacion {
                // `confirmation_required` cierra el stream sin `done`. Se
                // deja terminar la respuesta para que el servidor confirme
                // su transacción; el EOF no es una desconexión en este caso.
                quitarBurbujaVacia(estado.respuestaId)
                finalizarEstadoLocalDelTurno()
                estadoLocalFinalizado = true
                _ = await actualizarConversationIdDesdeServidor(client: client)
            } else if case .servidor(let status, _) = sseError,
                      (400..<500).contains(status), status != 409, status != 408, status != 429 {
                // Rechazo definitivo: no se reenviará en bucle (BOTS-10).
                marcarEnvioEquipoRechazado(
                    respuestaId: estado.respuestaId,
                    burbujasDelTurno,
                    texto: texto,
                    mensaje: mensajeRechazoEquipo(status: status, fallback: sseError.localizedDescription)
                )
            } else {
                // Resultado DESCONOCIDO (red caída, 5xx, 409/408/429): el turno
                // puede seguir en el servidor. Reconectar con la MISMA clave.
                await reconectarEnvioEquipo(
                    client: client,
                    texto: texto,
                    idempotencyKey: idempotencyKey,
                    estado: &estado,
                    burbujasDelTurno: &burbujasDelTurno,
                    resultado: &resultado,
                    estadoLocalFinalizado: &estadoLocalFinalizado
                )
            }
        } catch {
            // Resultado desconocido: reconectar con la misma clave.
            await reconectarEnvioEquipo(
                client: client,
                texto: texto,
                idempotencyKey: idempotencyKey,
                estado: &estado,
                burbujasDelTurno: &burbujasDelTurno,
                resultado: &resultado,
                estadoLocalFinalizado: &estadoLocalFinalizado
            )
        }
    }

    /// BOTS-10: reconexión con la MISMA Idempotency-Key. 202/409/429 = sigue
    /// en vuelo (esperar y reintentar); 200 = replay del turno completo;
    /// timeout honesto a los ~10 min (el push del servidor avisa igualmente).
    private func reconectarEnvioEquipo(
        client: APIClient,
        texto: String,
        idempotencyKey: String,
        estado: inout EstadoStreamEquipo,
        burbujasDelTurno: inout Set<String>,
        resultado: inout ResultadoStreamEquipo,
        estadoLocalFinalizado: inout Bool
    ) async {
        let limite = Date().addingTimeInterval(600)
        var burbujasTurno = burbujasDelTurno
        while Date() < limite, !Task.isCancelled {
            try? await Task.sleep(for: .seconds(2))
            if Task.isCancelled { return }
            do {
                let request = try await client.peticionMensajeEquipo(
                    teamId: equipo.id, text: texto, idempotencyKey: idempotencyKey
                )
                var limpiezaDeEsteIntento = false
                for try await evento in sseClient.stream(request) {
                    if !limpiezaDeEsteIntento {
                        // Primer frame de un replay real: retirar las burbujas
                        // de los intentos previos y empezar limpio (sin
                        // duplicar optimistas; BOTS-10).
                        items.removeAll { burbujasTurno.contains($0.id) }
                        estado.respuestaId = abrirBurbujaBot(responderDelEquipo)
                        burbujasTurno.insert(estado.respuestaId)
                        burbujasDelTurno = burbujasTurno
                        estado.textoParcial = ""
                        estado.ultimoPintado = .distantPast
                        limpiezaDeEsteIntento = true
                    }
                    let actual = procesarEventoDelTurno(evento, estado: &estado, burbujasDelTurno: &burbujasTurno)
                    if actual != .continuar { resultado = actual }
                }
                quitarBurbujaVacia(estado.respuestaId)
                finalizarEstadoLocalDelTurno()
                estadoLocalFinalizado = true
                burbujasDelTurno = burbujasTurno
                _ = await actualizarConversationIdDesdeServidor(client: client)
                if resultado == .terminado {
                    await cargar()
                }
                return
            } catch is CancellationError {
                return
            } catch let sseError as SSEClient.SSEError {
                if resultado == .esperandoConfirmacion {
                    // `confirmation_required` cierra el stream sin `done`:
                    // el EOF es esperado, no una desconexión.
                    quitarBurbujaVacia(estado.respuestaId)
                    finalizarEstadoLocalDelTurno()
                    estadoLocalFinalizado = true
                    _ = await actualizarConversationIdDesdeServidor(client: client)
                    return
                }
                // F5: el turno murió por un reinicio (x-run-state: interrupted).
                // Reintentar no sirve — se marca la burbuja y el toque reenvía
                // con una clave NUEVA.
                if case .interrumpido = sseError {
                    marcarBurbujaEquipoInterrumpida(estado.respuestaId, texto: texto)
                    finalizarEstadoLocalDelTurno()
                    estadoLocalFinalizado = true
                    return
                }
                if case .servidor(let status, _) = sseError,
                   (400..<500).contains(status), status != 409, status != 408, status != 429 {
                    marcarEnvioEquipoRechazado(
                        respuestaId: estado.respuestaId,
                        burbujasTurno,
                        texto: texto,
                        mensaje: mensajeRechazoEquipo(status: status, fallback: sseError.localizedDescription)
                    )
                    return
                }
                continue // sigue en vuelo o red caída: seguir esperando
            } catch {
                continue // red caída de nuevo: seguir esperando
            }
        }
        marcarBurbujaEquipoAtascada(estado.respuestaId)
    }

    /// BOTS-10: un 4xx (excepto 409/408/429) significa que el servidor NO
    /// aceptó el envío. Conserva el texto para reintentar y muestra el rechazo
    /// real, sin prometer que el turno sigue en segundo plano.
    private func marcarEnvioEquipoRechazado(
        respuestaId: String,
        _ burbujasDelTurno: Set<String>,
        texto: String,
        mensaje: String
    ) {
        for indice in items.indices where burbujasDelTurno.contains(items[indice].id) {
            items[indice].enProgreso = false
        }
        items.removeAll {
            burbujasDelTurno.contains($0.id) && $0.texto.isEmpty && $0.textoApertura.isEmpty && $0.bloques.isEmpty
        }
        if self.texto.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            self.texto = texto
        }
        error = mensaje
    }

    /// Tras el timeout de reconexión: honesto y accionable — el push del
    /// servidor avisa cuando el turno termine.
    private func marcarBurbujaEquipoAtascada(_ respuestaId: String) {
        if let indice = items.firstIndex(where: { $0.id == respuestaId }) {
            items[indice].enProgreso = false
            if items[indice].texto.isEmpty && items[indice].textoApertura.isEmpty {
                items[indice].texto = "Sigo trabajando en segundo plano; cuando termine te llega el aviso."
            }
        }
    }

    /// F5: el turno murió por un reinicio del servidor (`x-run-state:
    /// interrupted`). La burbuja NO queda «atascada»: se marca «Interrumpido» y
    /// se conserva el texto original para que el toque reenvíe con clave nueva.
    private func marcarBurbujaEquipoInterrumpida(_ respuestaId: String, texto: String) {
        if let indice = items.firstIndex(where: { $0.id == respuestaId }) {
            items[indice].enProgreso = false
            items[indice].interrumpido = true
            items[indice].textoOriginal = texto
            items[indice].texto = "Interrumpido — tócala para reenviar"
        }
    }

    /// F5: reenvío desde una burbuja interrumpida con una clave de idempotencia
    /// NUEVA (no la que el servidor ya marcó como interrumpida).
    private func resenviarEquipoInterrumpido(id: String, texto: String) {
        guard let client = session.client else { return }
        items.removeAll { $0.id == id }
        let clave = UUID().uuidString
        let task = Task {
            await hacerEnvio(client: client, texto: texto, idempotencyKey: clave)
        }
        tareaDetenible = task
    }

    private func mensajeRechazoEquipo(status: Int, fallback: String) -> String {
        switch status {
        case 422:
            return "El servidor rechazó el mensaje del equipo (422). Revisa el texto e intenta de nuevo."
        default:
            return fallback
        }
    }

    private func anexarBeatEquipoSiNuevo(
        _ texto: String?,
        botTurno: PersistentWorker?,
        burbujasDelTurno: inout Set<String>
    ) {
        let limpio = texto?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        guard !limpio.isEmpty else { return }
        let reciente = items.suffix(6).contains {
            !$0.esUsuario && $0.esBeat && $0.texto == limpio
        }
        guard !reciente else { return }
        let nuevaId = UUID().uuidString
        burbujasDelTurno.insert(nuevaId)
        items.append(
            ItemMensajeEquipo(
                id: nuevaId,
                esUsuario: false,
                texto: limpio,
                nombreRemitente: botTurno?.nombreVisible,
                botId: botTurno?.id,
                esBeat: true
            )
        )
    }

    /// Reductor único para mensajes normales y continuaciones de
    /// confirmación. Mantener ambos caminos aquí evita que el segundo descarte
    /// texto, límites de burbuja, bloques o una segunda confirmación.
    private func procesarEventoDelTurno(
        _ evento: ChatEvent,
        estado: inout EstadoStreamEquipo,
        burbujasDelTurno: inout Set<String>
    ) -> ResultadoStreamEquipo {
        switch evento {
        case .textDelta(let delta):
            estado.textoParcial += delta
            pintar(estado: &estado)
        case .messageStart:
            let bot = botDelTurno(estado.respuestaId)
            if let indice = items.firstIndex(where: { $0.id == estado.respuestaId }),
               !items[indice].texto.isEmpty || !items[indice].textoApertura.isEmpty
                    || !items[indice].bloques.isEmpty {
                items[indice].enProgreso = false
                estado.respuestaId = abrirBurbujaBot(bot)
                burbujasDelTurno.insert(estado.respuestaId)
                    } else if let indice = items.firstIndex(where: { $0.id == estado.respuestaId }) {
                items[indice].enProgreso = true
            }
            estado.textoParcial = ""
            estado.ultimoPintado = .distantPast
        case .messageEnd:
            pintar(estado: &estado, final: true)
            finalizarBurbuja(estado.respuestaId, texto: estado.textoParcial)
        case .toolStart(_, let nombre, let args):
            if nombre == "enviar_mensaje_bot" {
                let destino = cadenaEnArgs(args, clave: "bot") ?? ""
                let mensaje = cadenaEnArgs(args, clave: "mensaje")
                let emisorId = items.first(where: { $0.id == estado.respuestaId })?.botId
                if !destino.isEmpty {
                    registrarNarracionEntreBots(
                        emisorId: emisorId,
                        destinoNombre: destino,
                        mensaje: mensaje
                    )
                }
            }
            let botTurnoStart = botDelTurno(estado.respuestaId)
            if !BeatHerramientaCopy.silenciosas.contains(nombre) {
                herramientaActiva = HerramientaEquipoActiva(
                    id: UUID().uuidString,
                    nombre: nombre,
                    detalle: BeatHerramientaCopy.mensaje(nombre: nombre, progreso: nil),
                    nombreBot: nombreBotActivo(porId: botTurnoStart?.id)
                )
            }
            anexarBeatEquipoSiNuevo(
                BeatHerramientaCopy.beatParaToolStart(nombre: nombre),
                botTurno: botTurnoStart,
                burbujasDelTurno: &burbujasDelTurno
            )
        case .toolProgress(_, let nombre, _, let detalle):
            let botTurnoProg = botDelTurno(estado.respuestaId)
            if !BeatHerramientaCopy.silenciosas.contains(nombre) {
                herramientaActiva = HerramientaEquipoActiva(
                    id: herramientaActiva?.id ?? UUID().uuidString,
                    nombre: nombre,
                    detalle: BeatHerramientaCopy.mensaje(nombre: nombre, progreso: detalle),
                    nombreBot: nombreBotActivo(porId: botTurnoProg?.id)
                )
            }
        case .toolEnd(_, let nombre, let preview, _, _, let bloques, let missionId):
            if nombre != "avisar_avance" {
                herramientaActiva = nil
            }
            if let indice = items.firstIndex(where: { $0.id == estado.respuestaId }) {
                for bloque in bloques where !items[indice].bloques.contains(bloque) {
                    items[indice].bloques.append(bloque)
                }
            }
            let botTurno = botDelTurno(estado.respuestaId)
            if nombre == "delegar_mision", let missionId {
                let nuevaId = UUID().uuidString
                burbujasDelTurno.insert(nuevaId)
                items.append(
                    ItemMensajeEquipo(
                        id: nuevaId,
                        esUsuario: false,
                        texto: "Misión encolada (\(missionId.prefix(8))…). Sigue en segundo plano en tu Mac.",
                        nombreRemitente: botTurno?.nombreVisible,
                        botId: botTurno?.id
                    )
                )
            } else if let beat = BeatHerramientaCopy.beatParaToolEnd(nombre: nombre, preview: preview) {
                anexarBeatEquipoSiNuevo(beat, botTurno: botTurno, burbujasDelTurno: &burbujasDelTurno)
            } else if !preview.isEmpty, preview.lowercased().hasPrefix("error:") {
                let nuevaId = UUID().uuidString
                burbujasDelTurno.insert(nuevaId)
                items.append(
                    ItemMensajeEquipo(
                        id: nuevaId,
                        esUsuario: false,
                        texto: preview,
                        nombreRemitente: botTurno?.nombreVisible,
                        botId: botTurno?.id
                    )
                )
            }
        case .followUpTurn:
            pintar(estado: &estado, final: true)
            let bot = botDelTurno(estado.respuestaId)
            finalizarBurbuja(estado.respuestaId, texto: estado.textoParcial)
            estado.textoParcial = ""
            estado.ultimoPintado = .distantPast
            herramientaActiva = nil
            estado.respuestaId = abrirBurbujaBot(bot)
            burbujasDelTurno.insert(estado.respuestaId)
            case .confirmationRequired(let toolCallId, let nombre, let args):
            pintar(estado: &estado, final: true)
            finalizarBurbuja(estado.respuestaId, texto: estado.textoParcial)
            herramientaActiva = nil
            idsBotsActivos.removeAll()
            confirmacionPendiente = ConfirmacionEquipoPendiente(
                toolCallId: toolCallId,
                nombre: nombre,
                args: args,
                conversationId: conversationIdActual ?? equipo.conversationId
            )
            return .esperandoConfirmacion
        case .done:
            pintar(estado: &estado, final: true)
            finalizarBurbuja(estado.respuestaId, texto: estado.textoParcial)
            herramientaActiva = nil
            idsBotsActivos.removeAll()
            return .terminado
        case .error(let mensaje):
            pintar(estado: &estado, final: true)
            if let indice = items.firstIndex(where: { $0.id == estado.respuestaId }) {
                items[indice].enProgreso = false
                if items[indice].texto.isEmpty {
                    items[indice].texto = "Ups, se enredó a mitad de camino: \(mensaje)"
                }
            }
            herramientaActiva = nil
            idsBotsActivos.removeAll()
            return .fallo
        default:
            break
        }
        return .continuar
    }

    private func pintar(estado: inout EstadoStreamEquipo, final: Bool = false) {
        let ahora = Date()
        guard final || ahora.timeIntervalSince(estado.ultimoPintado) > 0.12 else { return }
        estado.ultimoPintado = ahora
        guard let indice = items.firstIndex(where: { $0.id == estado.respuestaId }) else { return }
        items[indice].texto = estado.textoParcial
    }

    private func botDelTurno(_ respuestaId: String) -> PersistentWorker? {
        worker(porId: items.first(where: { $0.id == respuestaId })?.botId) ?? responderDelEquipo
    }

    private func finalizarEstadoLocalDelTurno() {
        turnosEnVuelo = max(0, turnosEnVuelo - 1)
    }

    private func confirmar(_ pendiente: ConfirmacionEquipoPendiente, aprobado: Bool) {
        guard let client = session.client else {
            error = "No pude confirmar: falta sesión activa."
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
        var estadoLocalFinalizado = false
        defer {
            if !estadoLocalFinalizado { finalizarEstadoLocalDelTurno() }
        }
        var conversationId = pendiente.conversationId ?? conversationIdActual ?? equipo.conversationId
        if conversationId == nil {
            conversationId = await actualizarConversationIdDesdeServidor(client: client)
        }
        guard let conversationId else {
            error = "No pude confirmar: falta el id de conversación del equipo."
            confirmacionPendiente = pendiente
            return
        }

        conversationIdActual = conversationId
        var estado = EstadoStreamEquipo(respuestaId: abrirBurbujaBot(responderDelEquipo))
        var burbujasTurno: Set<String> = [estado.respuestaId]
        var resultado: ResultadoStreamEquipo = .continuar
        do {
            let request = try await client.peticionConfirmarConversacion(
                conversationId: conversationId,
                toolCallId: pendiente.toolCallId,
                approved: aprobado
            )
            for try await evento in sseClient.stream(request) {
                let actual = procesarEventoDelTurno(evento, estado: &estado, burbujasDelTurno: &burbujasTurno)
                if actual != .continuar { resultado = actual }
            }
            quitarBurbujaVacia(estado.respuestaId)
            finalizarEstadoLocalDelTurno()
            estadoLocalFinalizado = true
            _ = await actualizarConversationIdDesdeServidor(client: client)
            if resultado == .terminado {
                confirmacionPendiente = nil
                if !pendiente.toolCallId.isEmpty { toolCallsDecididos.insert(pendiente.toolCallId) }
                await cargar()
                // BOTS-11: reconciliar tras la decisión — recoger otra
                // aprobación pendiente o confirmar que no queda ninguna.
                await cargarAprobacionesPendientes(client: client)
            }
        } catch is CancellationError {
            if detenidoPorUsuario {
                confirmacionPendiente = pendiente
                cerrarBurbujaPorDetencion(estado.respuestaId)
            }
            detenidoPorUsuario = false
        } catch let apiError as APIClient.APIError {
            if apiError.esConfirmacionExpirada {
                // app.md L3087–3097: Detener + confirmar no debe colgar ni 500.
                confirmacionPendiente = nil
                error = nil
                if !pendiente.toolCallId.isEmpty { toolCallsDecididos.insert(pendiente.toolCallId) }
                finalizarEstadoLocalDelTurno()
                estadoLocalFinalizado = true
                await cargar()
                await cargarAprobacionesPendientes(client: client)
            } else {
                self.error = apiError.localizedDescription
                restaurarConfirmacionSiNoFueReemplazada(pendiente)
                cerrarBurbujaTrasFallo(estado.respuestaId)
            }
        } catch let sseError as SSEClient.SSEError {
            if resultado == .esperandoConfirmacion {
                quitarBurbujaVacia(estado.respuestaId)
                // La confirmación que acabamos de decidir quedó resuelta; la
                // nueva `confirmation_required` (si la hay) ya pintó su tarjeta.
                if !pendiente.toolCallId.isEmpty { toolCallsDecididos.insert(pendiente.toolCallId) }
                finalizarEstadoLocalDelTurno()
                estadoLocalFinalizado = true
                _ = await actualizarConversationIdDesdeServidor(client: client)
            } else if sseError.esConfirmacionExpirada {
                confirmacionPendiente = nil
                error = nil
                if !pendiente.toolCallId.isEmpty { toolCallsDecididos.insert(pendiente.toolCallId) }
                finalizarEstadoLocalDelTurno()
                estadoLocalFinalizado = true
                await cargar()
                await cargarAprobacionesPendientes(client: client)
            } else {
                self.error = "No pude registrar tu decisión. Intenta de nuevo."
                restaurarConfirmacionSiNoFueReemplazada(pendiente)
                cerrarBurbujaTrasFallo(estado.respuestaId)
            }
        } catch {
            self.error = "No pude registrar tu decisión. Intenta de nuevo."
            restaurarConfirmacionSiNoFueReemplazada(pendiente)
            cerrarBurbujaTrasFallo(estado.respuestaId)
        }
    }

    private func restaurarConfirmacionSiNoFueReemplazada(_ pendiente: ConfirmacionEquipoPendiente) {
        guard confirmacionPendiente == nil else { return }
        confirmacionPendiente = pendiente
    }

    /// Relee el equipo para capturar la conversación creada por su primer
    /// mensaje. Se conserva en estado local porque `equipo` es un snapshot.
    @discardableResult
    private func actualizarConversationIdDesdeServidor(client: APIClient) async -> String? {
        if let conversationIdActual { return conversationIdActual }
        for intento in 0..<2 {
            if let equipos = try? await client.listTeams(),
               let actualizado = equipos.first(where: { $0.id == equipo.id }),
               let conversationId = actualizado.conversationId {
                conversationIdActual = conversationId
                if let pendiente = confirmacionPendiente, pendiente.conversationId == nil {
                    confirmacionPendiente = ConfirmacionEquipoPendiente(
                        toolCallId: pendiente.toolCallId,
                        nombre: pendiente.nombre,
                        args: pendiente.args,
                        conversationId: conversationId
                    )
                }
                return conversationId
            }
            if intento == 0 { try? await Task.sleep(for: .milliseconds(250)) }
        }
        return nil
    }

    private func cerrarBurbujaPorDetencion(_ id: String) {
        guard let indice = items.firstIndex(where: { $0.id == id }) else { return }
        items[indice].enProgreso = false
        if items[indice].texto.isEmpty && items[indice].textoApertura.isEmpty {
            items[indice].texto = "Turno detenido."
        }
    }

    private func cerrarBurbujaTrasFallo(_ id: String) {
        if let indice = items.firstIndex(where: { $0.id == id }) {
            items[indice].enProgreso = false
        }
        quitarBurbujaVacia(id)
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
                    asignacion: false,
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

private struct EstadoStreamEquipo {
    var respuestaId: String
    var textoParcial = ""
    var ultimoPintado = Date.distantPast
}

private enum ResultadoStreamEquipo {
    case continuar
    case terminado
    case esperandoConfirmacion
    case fallo
}

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
    let asignacion: Bool
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
    var esAsignacion: Bool
    var asignacionWorkerId: String?
    var asignacionNombre: String?
    var asignacionCara: CaraSnapshot?
    var esBeat: Bool
    /// F5: turno matado por un reinicio del servidor. La burbuja muestra
    /// «Interrumpido — tócala para reenviar» y el toque reenvía con clave nueva.
    var interrumpido: Bool
    /// F5: texto original del turno interrumpido, para el reenvío.
    var textoOriginal: String?

    init(
        id: String,
        esUsuario: Bool,
        texto: String,
        textoApertura: String = "",
        nombreRemitente: String? = nil,
        botId: String? = nil,
        enProgreso: Bool = false,
        bloques: [ChatBlock] = [],
        evento: EventoNarracionEquipo? = nil,
        esAsignacion: Bool = false,
        asignacionWorkerId: String? = nil,
        asignacionNombre: String? = nil,
        asignacionCara: CaraSnapshot? = nil,
        esBeat: Bool = false,
        interrumpido: Bool = false,
        textoOriginal: String? = nil
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
        self.esAsignacion = esAsignacion
        self.asignacionWorkerId = asignacionWorkerId
        self.asignacionNombre = asignacionNombre
        self.asignacionCara = asignacionCara
        self.esBeat = esBeat
        self.interrumpido = interrumpido
        self.textoOriginal = textoOriginal
    }

    static func asignacion(workerId: String, nombre: String, cara: CaraSnapshot? = nil) -> ItemMensajeEquipo {
        ItemMensajeEquipo(
            id: UUID().uuidString,
            esUsuario: false,
            texto: "",
            esAsignacion: true,
            asignacionWorkerId: workerId,
            asignacionNombre: nombre,
            asignacionCara: cara
        )
    }

    static func desde(_ mensaje: TeamMessage, workers: [PersistentWorker]) -> ItemMensajeEquipo {
        if mensaje.esEventoAsignacion {
            let resuelto = mensaje.resolverAsignacionAuto(workers: workers)
            return ItemMensajeEquipo(
                id: mensaje.id,
                esUsuario: false,
                texto: mensaje.text,
                esAsignacion: true,
                asignacionWorkerId: resuelto.id,
                asignacionNombre: resuelto.nombre,
                asignacionCara: resuelto.cara
            )
        }
        var evento: EventoNarracionEquipo?
        if mensaje.kind == "evento", let ev = mensaje.evento, !ev.isEmpty, ev != "asignacion" {
            evento = EventoNarracionEquipo(
                de: mensaje.de ?? "",
                goal: mensaje.goal ?? "",
                escribioA: ev == "escribio_a",
                asignacion: ev == "asignacion",
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
            bloques: mensaje.bloquesDesdeToolCalls,
            evento: evento,
            esBeat: mensaje.kind == "aviso"
        )
    }
}

private func clavePreguntaEquipo(_ bloque: QuestionBlock) -> String {
    bloque.question + "|" + bloque.options.map(\.id).joined(separator: ",")
}

private enum FilaChatEquipo: Identifiable {
    case mensaje(ItemMensajeEquipo)
    case narracion(TeamNotaNarracion)
    case asignacion(String, String, CaraSnapshot?)
    case pregunta(QuestionBlock, String)

    var id: String {
        switch self {
        case .mensaje(let item): item.id
        case .narracion(let nota): nota.id
        case .asignacion(_, let workerId, _): "asignacion-\(workerId)"
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
