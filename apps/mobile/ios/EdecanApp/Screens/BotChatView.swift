import EdecanKit
import PhotosUI
import SwiftUI
import UIKit
import UniformTypeIdentifiers

/// Chat 1:1 con un bot persistente — SIEMPRE VIVO: puedes seguir escribiendo
/// mientras el bot trabaja (los mensajes se encolan y responde en orden),
/// las imágenes se ven y se amplían con zoom, y los mensajes se copian.
struct BotChatView: View {
    @Environment(SessionStore.self) private var session
    @Environment(TabRouter.self) private var tabRouter
    @Environment(\.scenePhase) private var scenePhase
    let bot: PersistentWorker
    var textoInicial: String? = nil

    @State private var items: [ItemMensajeBot] = []
    @State private var texto = ""
    @State private var cargando = true
    @State private var error: String?
    @FocusState private var campoEnfocado: Bool
    @State private var anclaFinal = "bot-final"
    /// Delegaciones al IDE en el hilo: cards mini Liquid Glass que se
    /// actualizan en vivo con los eventos del stream. Viven en el hilo de la
    /// sesión (no se persisten: el historial del servidor no trae events de
    /// tools) y se anclan al id del turno para quedar antes de su respuesta.
    @State private var delegacionesIDE: [ItemDelegacionIDE] = []

    private let sseClient = SSEClient()
    private let botSnapshotStore = BotChatSnapshotStore()
    /// Cadena de envíos: cada mensaje espera al turno anterior. Así el chat
    /// nunca se cierra — escribes aunque el bot esté trabajando y responde
    /// en orden (el modelo de Grok Bot).
    @State private var cadenaEnvio: Task<Void, Never>?
    /// E-IOS-1: antes era un BOOL — con dos turnos simultáneos, el primero
    /// que terminaba apagaba "Trabajando…" y la cara del bot mientras el
    /// segundo seguía. Contador de turnos activos: se apaga solo cuando
    /// llega a cero.
    @State private var turnosActivos = 0
    private var enviandoTurno: Bool { turnosActivos > 0 }
    /// Beats vivos (Grok-style): chat abierto sin SSE local → sondea historial
    /// para avisos proactivos (`avisar_avance`) y mensajes vía push.
    // FRONTI_BEATS_MARKER
    @State private var pollBeatsTask: Task<Void, Never>?

    // Adjuntos pendientes: fotos y archivos de cualquier tipo se suben a
    // /v1/files y viajan como ids al turno del bot.
    @State private var adjuntosPendientes: [AdjuntoBot] = []
    @State private var seleccionFotos: [PhotosPickerItem] = []
    @State private var mostrandoPhotosPicker = false
    @State private var mostrandoFileImporter = false
    @State private var mostrandoSelectorAdjuntos = false
    // Selector de modelo del chat del bot (el dueño elige con qué modelo
    // ejecuta el bot): catálogo del servidor + selección local del turno.
    @State private var catalogoModelos: ChatModelCatalog?
    @State private var modeloElegido: String?
    @State private var esfuerzoElegido: EsfuerzoChat?
    @State private var mostrandoSelectorModelo = false
    // Mini-menú del trabajo de Astra (misiones delegadas desde este chat):
    // tarjeta viva con los pasos, tocable y expandible, estilo OpenCode.
    @State private var misionesDelHilo: [ItemMisionBot] = []
    @State private var previewTarget: SecurePreviewTarget?
    /// Needs you (proactive_scan) visible en contexto Bots 1:1.
    @State private var sugerenciasProactivas: [AutomationSuggestion] = []
    @State private var sugerenciasOcultas: Set<String> = []
    @State private var pollNeedsYouTask: Task<Void, Never>?
    /// Ola full-power: conectores MCP/skills, community approvals, Ver Mac.
    @State private var mostrandoConectores = false
    @State private var mostrandoCommunity = false
    @State private var mostrandoPantalla = false
    @State private var aprobacionesPendientes: [PendingApproval] = []
    @State private var pollApprovalsTask: Task<Void, Never>?
    @State private var aprobacionesExternas: [PendingApproval] = []
    @State private var confirmacionEnVivo: ConfirmacionBotPendiente?
    @State private var destinoNavegador: DestinoNavegador?
    @State private var ocupadoAprobacion = false
    @State private var consumioTextoInicial = false
    /// Preguntas omitidas (dismiss) sin responder — clave mensajeId|question.
    @State private var preguntasOmitidas: Set<String> = []
    /// Beat humano de herramienta en curso («Revisando el código…»).
    @State private var herramientaActiva: HerramientaBotActiva?
    @Namespace private var composerNamespace
    /// AUD-12a: historial navegable por cursor (`before`/`next_cursor`), no por
    /// ventana creciente. `limiteHistorial` es el TAMAÑO DE PÁGINA fijo; el
    /// cursor opaco viaja en `cursorHistorial` y nunca se reconstruye en el
    /// cliente. Independiente de la ventana de contexto LLM del servidor.
    @State private var limiteHistorial = 50
    @State private var cursorHistorial: String?
    @State private var cargandoMasHistorial = false
    /// Empieza en `false`: la primera carga confirma si hay historial previo.
    /// Evita el parpadeo del botón «Cargar mensajes anteriores» en un chat
    /// todavía vacío.
    @State private var hayMasHistorial = false
    /// BOTS-09: el snapshot de disco de ESTE bot fue invalidado en esta sesión
    /// (tras `/clear` o DELETE exitoso). Mientras esté marcado, pintar el
    /// snapshot local no debe resucitar mensajes que el servidor ya borró.
    @State private var snapshotInvalidado = false
    /// BOTS-09: epoch de conversación más reciente visto desde el servidor.
    /// Un snapshot con epoch más viejo no se repinta (respuesta tardía pre-clear).
    @State private var epochConversacion: Int64?

    /// Tools cuyo efecto sale del tenant (publicar, mensajería, llamadas).
    private static let herramientasEnvioExterno: Set<String> = [
        "publicar_social", "publicar_linkedin", "crear_post_linkedin",
        "enviar_mensaje", "enviar_email", "llamar_contacto",
    ]

    private var sugerenciasVisibles: [AutomationSuggestion] {
        sugerenciasProactivas.filter { !sugerenciasOcultas.contains($0.id) }
    }

    /// Borradores sociales recientes en el hilo (cola community-manager).
    private var colaBorradoresSociales: [ColaBorradorSocial] {
        var vistos = Set<String>()
        var resultado: [ColaBorradorSocial] = []
        for item in items where !item.esUsuario {
            for (idx, bloque) in item.bloques.enumerated() {
                guard case .socialDraft(let social) = bloque else { continue }
                let key = "\(item.id)-\(idx)"
                guard vistos.insert(key).inserted else { continue }
                resultado.append(ColaBorradorSocial(id: key, bloque: social, mensajeId: item.id))
            }
        }
        return Array(resultado.suffix(6))
    }

    /// Confirmación en vivo (SSE) tiene prioridad sobre aprobaciones durables EXTERNAL.
    private var confirmacionVisible: ConfirmacionBotMostrada? {
        if let live = confirmacionEnVivo { return .enVivo(live) }
        if let durable = aprobacionesExternas.first { return .durable(durable) }
        return nil
    }

    /// Drafts sociales ya emitidos en el hilo (preview Community sheet).
    private var draftsSocialesDelHilo: [SocialDraftBlock] {
        items.flatMap { item in
            item.bloques.compactMap { bloque in
                if case .socialDraft(let draft) = bloque { return draft }
                return nil
            }
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            if !colaBorradoresSociales.isEmpty {
                colaBorradoresSocialesView
            }
            if let confirmacion = confirmacionVisible {
                tarjetaAprobacion(confirmacion)
                    .padding(.horizontal)
                    .padding(.top, 4)
            }
            if !sugerenciasVisibles.isEmpty {
                TeamNeedsYouPanel(
                    sugerencias: sugerenciasVisibles,
                    onDelegar: { sugerencia in
                        enviarTextoForzado(
                            TeamNeedsYouPanel.promptParaBot(
                                sugerencia, nombreBot: bot.nombreVisible
                            )
                        )
                    },
                    onDescartar: { sugerencia in
                        sugerenciasOcultas.insert(sugerencia.id)
                    },
                    etiquetaAccion: "Pedirle a \(bot.nombreVisible)",
                    pieSinDetalle: "Toca ↑ para que \(bot.nombreVisible) lo resuelva aquí"
                )
            }
            listaDeMensajes
            if !misionesDelHilo.isEmpty && franjaTareasHabilitada {
                franjaTareas
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
        .background(FondoGlowComposer(arriba: true), alignment: .top)
        .estiloPantallaBots()
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(.hidden, for: .navigationBar)
        .toolbar {
            ToolbarItem(placement: .principal) {
                cabeceraBot
            }
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Button {
                        mostrandoConectores = true
                    } label: {
                        Label("Conectores / skills", systemImage: "cable.connector")
                    }
                    Button {
                        mostrandoCommunity = true
                    } label: {
                        Label(
                            aprobacionesPendientes.isEmpty
                                ? "Community / envíos"
                                : "Community (\(aprobacionesPendientes.count))",
                            systemImage: "megaphone"
                        )
                    }
                    Button {
                        mostrandoPantalla = true
                    } label: {
                        Label("Ver VPS / pantalla", systemImage: "cloud")
                    }
                } label: {
                    Image(systemName: "ellipsis.circle")
                        .font(.body.weight(.semibold))
                        .symbolRenderingMode(.hierarchical)
                        .overlay(alignment: .topTrailing) {
                            if !aprobacionesPendientes.isEmpty {
                                Circle()
                                    .fill(EdecanTheme.morado)
                                    .frame(width: 7, height: 7)
                                    .offset(x: 2, y: -2)
                            }
                        }
                }
                .accessibilityLabel("Poderes del bot")
            }
        }
        .onAppear { pintarSnapshotLocalSiVacio() }
        .task {
            await cargar()
            await cargarCatalogoSiHaceFalta()
            if let client = session.client {
                await cargarSugerenciasProactivas(client: client)
                await cargarAprobaciones(client: client)
            }
            await consumirTextoInicialSiHay()
            iniciarPollBeatsVivos()
            iniciarPollNeedsYou()
            iniciarPollApprovals()
        }
        .onChange(of: seleccionFotos) { _, nuevos in
            Task { await subirFotos(nuevos) }
            seleccionFotos = []
        }
        .onChange(of: scenePhase) { _, fase in
            if fase == .active {
                Task {
                    await refrescarBeatsSiQuiet()
                    if let client = session.client {
                        await cargarSugerenciasProactivas(client: client)
                        await cargarAprobaciones(client: client)
                    }
                }
                iniciarPollBeatsVivos()
                iniciarPollNeedsYou()
                iniciarPollApprovals()
            } else {
                detenerPollBeatsVivos()
                detenerPollNeedsYou()
                detenerPollApprovals()
            }
        }
        .onChange(of: turnosActivos) { _, activos in
            if activos == 0 {
                limpiarTypingZombie()
                Task { await refrescarBeatsSiQuiet() }
                iniciarPollBeatsVivos()
            } else {
                detenerPollBeatsVivos()
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .edecanNotificationRoute)) { note in
            let ruta = (note.object as? String) ?? ""
            let chatId = note.userInfo?["conversationId"] as? String
            // BOTS-13: el push lleva `conversation_id`, no `worker_id`. El
            // worker lo resuelve del listado (`conversation_id`); comparar
            // contra él evita el falso negativo que dejaba el aviso del bot
            // abierto sin refrescar. Workers legacy sin el campo caen a `id`
            // para no perder el refresh.
            let conversationDelBot = bot.conversationId ?? bot.id
            guard ruta == NotificationRoute.botChat.rawValue,
                  chatId == conversationDelBot else { return }
            Task { await refrescarBeatsSiQuiet(forzar: true) }
        }
        .fullScreenCover(item: $previewTarget) { target in
            SecurePreviewSheet(target: target, client: session.client)
        }
        .onDisappear {
            detenerPollBeatsVivos()
            detenerPollNeedsYou()
            detenerPollApprovals()
            detenerPollingMisiones()
        }
        .sheet(isPresented: $mostrandoSelectorModelo) {
            HojaSelectorModeloBot(
                catalogo: catalogoModelos,
                seleccionado: modeloElegido,
                esfuerzo: esfuerzoElegido
            ) { modelo, esfuerzo in
                modeloElegido = modelo
                esfuerzoElegido = esfuerzo
            }
            .presentationDetents([.medium])
        }
        .sheet(isPresented: $mostrandoSelectorAdjuntos) {
            HojaAdjuntarBot(
                nombreBot: bot.nombreVisible,
                onFotos: { mostrandoPhotosPicker = true },
                onArchivos: { mostrandoFileImporter = true }
            )
            .presentationDetents([.height(330)])
            .presentationBackground(.clear)
            .presentationBackgroundInteraction(.enabled(upThrough: .height(330)))
            .presentationDragIndicator(.hidden)
        }
        .photosPicker(
            isPresented: $mostrandoPhotosPicker,
            selection: $seleccionFotos,
            maxSelectionCount: 4,
            matching: .images
        )
        .fileImporter(
            isPresented: $mostrandoFileImporter,
            allowedContentTypes: [.item],
            allowsMultipleSelection: true
        ) { resultado in
            switch resultado {
            case .success(let urls):
                Task { await subirArchivosGenericos(urls) }
            case .failure:
                error = "No pude abrir los archivos. Intenta de nuevo."
            }
        }
        .sheet(isPresented: $mostrandoConectores) {
            BotConectoresSheet(nombreBot: bot.nombreVisible) { prompt in
                enviarTextoForzado(prompt)
            }
        }
        .sheet(isPresented: $mostrandoCommunity) {
            BotCommunitySheet(
                workerId: bot.id,
                nombreBot: bot.nombreVisible,
                draftsEnChat: draftsSocialesDelHilo
            ) { prompt in
                enviarTextoForzado(prompt)
            }
        }
        .sheet(isPresented: $mostrandoPantalla) {
            BotPantallaSheet()
        }
        .sheet(item: $destinoNavegador) { destino in
            NavegadorEnApp(url: destino.url)
                .ignoresSafeArea()
        }
    }

    /// Cola horizontal de borradores sociales pendientes (community manager).
    private var colaBorradoresSocialesView: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Borradores para publicar")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
                .padding(.horizontal, 16)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 10) {
                    ForEach(colaBorradoresSociales) { entrada in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(entrada.bloque.draft.platform.label)
                                .font(.caption2.weight(.bold))
                                .foregroundStyle(EdecanTheme.morado)
                            Text(entrada.bloque.draft.copy)
                                .font(.caption)
                                .lineLimit(3)
                                .frame(width: 180, alignment: .leading)
                        }
                        .padding(10)
                        .tarjetaVidrio(esquina: 12)
                    }
                }
                .padding(.horizontal, 16)
            }
        }
        .padding(.vertical, 6)
    }

    @ViewBuilder
    private func tarjetaAprobacion(_ confirmacion: ConfirmacionBotMostrada) -> some View {
        switch confirmacion {
        case .enVivo(let pendiente):
            TarjetaConfirmacion(
                confirmacion: ChatViewModel.ConfirmacionPendiente(
                    toolCallId: pendiente.toolCallId,
                    nombre: pendiente.nombre,
                    args: pendiente.args,
                    indiceMensaje: nil
                ),
                deshabilitada: enviandoTurno,
                onVerComputadora: pendiente.nombre == "usar_computadora"
                    ? { tabRouter.mostrarRemoto() }
                    : nil
            ) { aprobado in
                confirmarTurnoBot(pendiente: pendiente, aprobado: aprobado)
            }
        case .durable(let aprobacion):
            VStack(alignment: .leading, spacing: 8) {
                Label("Envío externo pendiente", systemImage: "paperplane")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.orange)
                FilaAprobacion(aprobacion: aprobacion, ocupado: ocupadoAprobacion) { aprobar in
                    Task { await decidirAprobacionDurable(aprobacion, aprobar: aprobar) }
                }
            }
            .padding(12)
            .tarjetaVidrio(esquina: 16)
        }
    }

    /// Cabecera Liquid Glass: cara + nombre en pill flotante sobre el chat.
    private var cabeceraBot: some View {
        HStack(spacing: 10) {
            GrokFaceAvatar(bot: bot, size: 28, showOnline: false, animado: true, activo: enviandoTurno)
            VStack(alignment: .leading, spacing: 1) {
                Text(bot.nombreVisible)
                    .font(.subheadline.weight(.semibold))
                    .lineLimit(1)
                if enviandoTurno {
                    Text("Trabajando…")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .transition(.opacity.combined(with: .move(edge: .top)))
                }
            }
            // Sin spinner en la cabecera: la cara animada (habla + halo) ES el
            // indicador de trabajo — un spinner encima del nombre leía como
            // «app rota» en vez de «bot vivo».
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .capsulaVidrio()
        .animation(.spring(response: 0.32, dampingFraction: 0.82), value: enviandoTurno)
    }

    private var listaDeMensajes: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 18) {
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
                    if cargando && items.isEmpty {
                        ProgressView()
                            .controlSize(.large)
                            .frame(maxWidth: .infinity)
                            .padding(.top, 60)
                            .accessibilityLabel("Cargando el chat")
                    } else if items.isEmpty {
                        estadoVacio
                    }
                    ForEach(filas) { fila in
                        Group {
                            switch fila {
                            case .mensaje(let item):
                                burbuja(item)
                                    .id(item.id)
                            case .narracion(let nota):
                                filaNarracion(nota)
                                    .id(nota.id)
                            case .delegacion(let delegacion):
                                filaDelegacion(delegacion)
                                    .id(delegacion.id)
                            }
                        }
                        .transition(.asymmetric(
                            insertion: .opacity.combined(with: .move(edge: .bottom)),
                            removal: .opacity
                        ))
                    }
                    if let herramientaActiva {
                        TeamToolActivityRow(
                            nombreBot: bot.nombreVisible,
                            herramienta: herramientaActiva.nombre,
                            detalle: herramientaActiva.detalle
                        )
                        .id("tool-active")
                    }
                    Color.clear.frame(height: 1).id(anclaFinal)
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
            }
            .scrollDismissesKeyboard(.interactively)
            .onChange(of: items.count) { _, _ in
                // Al cargar mensajes anteriores no se baja el scroll: se
                // preserva la posición de lectura (BOTS-12).
                guard !cargandoMasHistorial else { return }
                withAnimation(.spring(response: 0.35, dampingFraction: 0.88)) {
                    proxy.scrollTo(anclaFinal, anchor: .bottom)
                }
            }
            .onChange(of: delegacionesIDE.count) { _, _ in
                withAnimation(.spring(response: 0.35, dampingFraction: 0.88)) {
                    proxy.scrollTo(anclaFinal, anchor: .bottom)
                }
            }
            .onChange(of: herramientaActiva?.id) { _, _ in
                withAnimation(.spring(response: 0.35, dampingFraction: 0.88)) {
                    proxy.scrollTo(anclaFinal, anchor: .bottom)
                }
            }
        }
    }

    /// Hero de chat vacío: el bot en grande, su identidad y sugerencias.
    private var estadoVacio: some View {
        VStack(spacing: 16) {
            GrokFaceAvatar(bot: bot, size: 96, showOnline: false)
                .padding(.top, 30)

            VStack(spacing: 6) {
                Text(bot.nombreVisible)
                    .font(.title2.weight(.bold))
                if !bot.purpose.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    Text(bot.purpose)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                        .lineLimit(3)
                        .frame(maxWidth: 320)
                }
            }

            HStack(spacing: 8) {
                sugerencia("Preséntate")
                sugerencia("¿En qué eres experto?")
                sugerencia("¿Qué sabes de mí?")
            }
            .padding(.horizontal, 4)

            Text("Escribe abajo, toca una sugerencia o mándale una imagen.")
                .font(.caption)
                .foregroundStyle(.tertiary)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 30)
        .padding(.bottom, 20)
    }

    private func sugerencia(_ texto: String) -> some View {
        Button {
            withAnimation(.easeOut(duration: 0.15)) {
                self.texto = texto
            }
            campoEnfocado = true
        } label: {
            Text(texto)
                .font(.footnote.weight(.medium))
                .foregroundStyle(.primary)
                .padding(.horizontal, 14)
                .padding(.vertical, 9)
                .capsulaVidrio()
        }
        .buttonStyle(.plain)
    }

    // MARK: - Mensajes

    /// Agrupa los eventos de narración consecutivos del MISMO bot en una
    /// sola nota: «N mensajes con [cara] X». El contenido es interno: la
    /// nota solo cuenta el hecho, nunca pega el mensaje literal.
    ///
    /// Las cards de delegación al IDE se intercalan antes de la respuesta del
    /// turno al que pertenecen (ancla = id del turno). Si el turno terminó sin
    /// texto y su burbuja vacía se eliminó, la card cae al final del hilo.
    private var filas: [FilaChatBot] {
        var resultado: [FilaChatBot] = []
        var anclasPresentes: Set<String> = []
        for item in items {
            anclasPresentes.insert(item.id)
            if let ev = item.evento {
                if case .narracion(var nota) = resultado.last, nota.de == ev.de {
                    nota.mensajes.append(ev.goal)
                    nota.ultimo = ev.goal
                    nacido(nota, en: &resultado)
                } else {
                    resultado.append(
                        .narracion(NotaNarracion(
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
            }
            // La card de delegación va DEBAJO de la respuesta de su turno
            // (como un recibo del trabajo), no encima — el dueño la pidió
            // abajo, con margen, alineada con la columna del bot.
            for delegacion in delegacionesIDE where delegacion.ancla == item.id {
                resultado.append(.delegacion(delegacion))
            }
        }
        for delegacion in delegacionesIDE where !anclasPresentes.contains(delegacion.ancla) {
            resultado.append(.delegacion(delegacion))
        }
        return resultado
    }

    private func nacido(_ nota: NotaNarracion, en resultado: inout [FilaChatBot]) {
        resultado[resultado.count - 1] = .narracion(nota)
    }

    /// La nota de narración estilo Grok: línea centrada con la carita +
    /// el nombre. El CONTENIDO entre bots es interno: se indica el hecho
    /// («Escribió a X» / «Mensaje de X»), nunca el mensaje literal.
    @ViewBuilder
    private func filaNarracion(_ nota: NotaNarracion) -> some View {
        HStack(spacing: 8) {
            if nota.tipo == "escribio" {
                Image(systemName: "arrow.right")
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(.tertiary)
            }
            Text(etiquetaNarracion(nota))
                .font(.caption)
                .foregroundStyle(.secondary)
            if let cara = nota.cara {
                caraSnapshot(cara, nombre: nota.de, size: 20)
            } else {
                Circle()
                    .fill(EdecanTheme.morado.opacity(0.25))
                    .frame(width: 20, height: 20)
            }
            Text(nota.de)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            if nota.mensajes.count > 1 {
                Text("· \(nota.mensajes.count)")
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(.tertiary)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 6)
        .capsulaVidrio()
        .frame(maxWidth: .infinity, alignment: .center)
        .padding(.vertical, 2)
        .transition(.opacity.combined(with: .move(edge: .top)))
    }

    private func etiquetaNarracion(_ nota: NotaNarracion) -> String {
        if nota.mensajes.count > 1 {
            return "\(nota.mensajes.count) mensajes con"
        }
        return nota.tipo == "escribio" ? "Escribió a" : "Mensaje de"
    }

    /// Card mini Liquid Glass de la delegación al IDE: icono + estado en vivo.
    /// Spinner pequeño mientras crea/procesa; checkmark al terminar. Grok
    /// style: la herramienta no se nombra, se ve el ESTADO del trabajo.
    /// Va debajo de la respuesta de su turno, alineada con la columna del bot.
    @ViewBuilder
    private func filaDelegacion(_ delegacion: ItemDelegacionIDE) -> some View {
        HStack(spacing: 10) {
            ZStack {
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(EdecanTheme.morado.opacity(0.13))
                    .frame(width: 26, height: 26)
                switch delegacion.fase {
                case .terminado:
                    Image(systemName: "checkmark")
                        .font(.system(size: 12, weight: .bold))
                        .foregroundStyle(EdecanTheme.morado)
                case .creando, .procesando:
                    ProgressView()
                        .controlSize(.mini)
                        .tint(EdecanTheme.morado)
                }
            }
            Text(delegacion.estado)
                .font(.system(size: 13, weight: .medium, design: .rounded))
                .foregroundStyle(.primary)
                .lineLimit(2)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .frame(maxWidth: 300, alignment: .leading)
        .tarjetaVidrio(esquina: 16, tint: EdecanTheme.morado)
        .padding(.leading, 52)
        .padding(.trailing, 40)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Delegación al IDE: \(delegacion.estado)")
    }

    /// Actualiza (o crea) la card de delegación del turno. Preferencia por el
    /// `toolCallId` del evento: es la identidad estable que sobrevive al
    /// replay de la reconexión sin duplicar cards. Si el backend no lo trae,
    /// cae a la delegación activa más reciente del turno.
    private func actualizarDelegacion(
        toolCallId: String?,
        ancla: String,
        fase: FaseDelegacionIDE,
        estado: String
    ) {
        if let id = toolCallId,
           let indice = delegacionesIDE.firstIndex(where: { $0.id == id }) {
            delegacionesIDE[indice].fase = fase
            delegacionesIDE[indice].estado = estado
            return
        }
        if let indice = delegacionesIDE.lastIndex(where: { $0.ancla == ancla && $0.fase != .terminado }) {
            delegacionesIDE[indice].fase = fase
            delegacionesIDE[indice].estado = estado
            return
        }
        delegacionesIDE.append(
            ItemDelegacionIDE(
                id: toolCallId ?? UUID().uuidString,
                ancla: ancla,
                fase: fase,
                estado: estado
            )
        )
    }

    /// Turno completado: ninguna card puede quedar con spinner — si el stream
    /// no trajo su `toolEnd`, se cierra con checkmark (el turno terminó).
    private func finalizarDelegaciones(_ respuestaId: String) {
        for indice in delegacionesIDE.indices
        where delegacionesIDE[indice].ancla == respuestaId
            && delegacionesIDE[indice].fase != .terminado {
            delegacionesIDE[indice].fase = .terminado
            delegacionesIDE[indice].estado = "Terminado"
        }
    }

    /// Turno fallido: las delegaciones que no terminaron no se muestran — un
    /// spinner vivo junto a «se me enredó» lee como app rota.
    private func quitarDelegacionesIncompletas(_ respuestaId: String) {
        delegacionesIDE.removeAll { $0.ancla == respuestaId && $0.fase != .terminado }
    }

    @ViewBuilder
    private func burbuja(_ item: ItemMensajeBot) -> some View {
        // AUD-5: los items con `evento` van por la fila .narracion en `filas`
        // (burbuja nunca los recibe) — la rama de evento que vivía aquí era
        // código muerto y se eliminó.
        if item.interrumpido {
            // F5: turno matado por un reinicio del servidor. No es un error de
            // red ni un turno «atascado»: el toque reenvía con clave NUEVA.
            Button {
                resenviarInterrumpido(
                    id: item.id,
                    texto: item.textoOriginal ?? "",
                    adjuntos: item.adjuntosOriginales
                )
            } label: {
                HStack(alignment: .top, spacing: 8) {
                    GrokFaceAvatar(bot: bot, size: 30, showOnline: false, animado: false, activo: false)
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
                .tarjetaVidrio(esquina: 16, tint: nil)
                .frame(maxWidth: 340, alignment: .leading)
            }
            .buttonStyle(.plain)
        } else if item.enProgreso && item.texto.isEmpty && enviandoTurno {
            FilaEstadoTrabajandoBot(bot: bot)
                .frame(maxWidth: .infinity, alignment: .leading)
                .id(item.id)
        } else if item.esUsuario {
            HStack(alignment: .top, spacing: 0) {
                Spacer(minLength: 48)
                VStack(alignment: .trailing, spacing: 6) {
                    if !item.adjuntos.isEmpty {
                        if item.adjuntosSoloReferencia {
                            indicadorAdjuntosPendientes()
                        } else {
                            adjuntosEnBurbuja(item.adjuntos)
                        }
                    }
                    if !item.texto.isEmpty {
                        Text(item.texto)
                            .font(.subheadline)
                            .fixedSize(horizontal: false, vertical: true)
                            .textSelection(.enabled)
                        if item.recortado {
                            Text("Texto recortado")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .foregroundStyle(.primary)
                .tarjetaVidrio(esquina: 16, tint: EdecanTheme.morado)
                .frame(maxWidth: 320, alignment: .trailing)
                .contextMenu {
                    if !item.texto.isEmpty {
                        Button {
                            UIPasteboard.general.string = item.texto
                        } label: {
                            Label("Copiar", systemImage: "doc.on.doc")
                        }
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .trailing)
        } else {
            // El bot: burbuja Liquid Glass (misma familia que la del dueño,
            // sin tint morado) — antes el texto iba directo sobre la
            // superficie y se veía pegado al fondo.
            HStack(alignment: .top, spacing: 8) {
                GrokFaceAvatar(
                    bot: bot,
                    size: 30,
                    showOnline: false,
                    animado: true,
                    activo: item.enProgreso
                )
                VStack(alignment: .leading, spacing: 6) {
                    if !item.adjuntos.isEmpty {
                        if item.adjuntosSoloReferencia {
                            indicadorAdjuntosPendientes()
                        } else {
                            adjuntosEnBurbuja(item.adjuntos)
                        }
                    }
                    if !item.texto.isEmpty {
                        VStack(alignment: .leading, spacing: 8) {
                            if item.esBeat {
                                HStack(spacing: 6) {
                                    Image(systemName: "bolt.fill")
                                        .font(.caption2.weight(.bold))
                                        .foregroundStyle(EdecanTheme.morado)
                                    Text("En vivo")
                                        .font(.caption2.weight(.bold))
                                        .foregroundStyle(EdecanTheme.morado)
                                    Spacer(minLength: 0)
                                }
                            }
                            ContenidoTextoView(
                                id: item.id,
                                campo: "bot",
                                texto: item.texto,
                                enBurbujaPropia: false,
                                estable: !item.enProgreso
                            )
                            .font(item.esBeat ? .footnote : .subheadline)
                            .textSelection(.enabled)
                            chipsReferenciasGit(item.texto)
                            evidenciaDe(item)
                            if item.recortado {
                                Text("Texto recortado")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .padding(.horizontal, 14)
                        .padding(.vertical, item.esBeat ? 8 : 10)
                        .tarjetaVidrio(
                            esquina: item.esBeat ? 14 : 16,
                            tint: item.esBeat ? EdecanTheme.morado : nil
                        )
                        .overlay(
                            RoundedRectangle(
                                cornerRadius: item.esBeat ? 14 : 16,
                                style: .continuous
                            )
                            .strokeBorder(
                                item.esBeat ? EdecanTheme.morado.opacity(0.35) : .clear,
                                lineWidth: 1
                            )
                        )
                        .frame(maxWidth: 320, alignment: .leading)
                    } else {
                        evidenciaDe(item)
                            .frame(maxWidth: 320, alignment: .leading)
                    }
                    ForEach(Array(preguntasDe(item).enumerated()), id: \.offset) { _, pregunta in
                        let clave = "\(item.id)|\(pregunta.question)"
                        if !preguntasOmitidas.contains(clave) {
                            TeamQuestionCardView(
                                bloque: pregunta,
                                respuestaPosterior: respuestaPosterior(para: pregunta, despuesDe: item.id),
                                onResponder: { respuesta in
                                    enviarTextoForzado(respuesta)
                                },
                                onDescartar: {
                                    withAnimation(.spring(response: 0.32, dampingFraction: 0.85)) {
                                        _ = preguntasOmitidas.insert(clave)
                                    }
                                }
                            )
                            .frame(maxWidth: 320, alignment: .leading)
                        }
                    }
                    evidenciaRica(item)
                    ForEach(Array(bloquesEntregablesDe(item).enumerated()), id: \.offset) { _, bloque in
                        BloqueChatView(
                            bloque: bloque,
                            client: session.client,
                            onAction: manejarAccionChat,
                            onResponder: { enviarTextoForzado($0) },
                            respuestaPosterior: nil,
                            onAbrirArtefacto: { ref in
                                abrirAdjunto(
                                    AdjuntoBot(
                                        fileId: ref.fileId,
                                        filename: ref.filename,
                                        mime: ref.mime
                                    )
                                )
                            },
                            fechaMensaje: nil
                        )
                        .frame(maxWidth: 320, alignment: .leading)
                    }

                }
                Spacer(minLength: 40)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contextMenu {
                if !item.texto.isEmpty {
                    Button {
                        UIPasteboard.general.string = item.texto
                    } label: {
                        Label("Copiar", systemImage: "doc.on.doc")
                    }
                }
            }
        }
    }

    /// BOTS-21: aviso honesto cuando un mensaje pintado desde el snapshot local
    /// tiene adjuntos que aún no se pueden descargar (sin conexión). Nunca
    /// aparenta que el entregable está completo.
    private func indicadorAdjuntosPendientes() -> some View {
        HStack(spacing: 6) {
            Image(systemName: "icloud.slash")
                .font(.caption)
                .foregroundStyle(.secondary)
            Text("Adjuntos disponibles al reconectar")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(
            Capsule().fill(Color.secondary.opacity(0.12))
        )
    }

    /// Imágenes adjuntas: miniatura autenticada; tocar abre el visor seguro
    /// con zoom (`SecurePreviewSheet`).
    @ViewBuilder
    private func adjuntosEnBurbuja(_ adjuntos: [AdjuntoBot]) -> some View {
        ForEach(adjuntos) { adjunto in
            if (adjunto.mime ?? "").lowercased().hasPrefix("image/") {
                ImagenAdjuntaBot(fileId: adjunto.fileId, client: session.client) {
                    abrirAdjunto(adjunto)
                }
            } else if esArchivoCodigo(adjunto.mime, filename: adjunto.filename) {
                BotCodigoCreadoPreview(
                    adjunto: adjunto,
                    client: session.client,
                    etiquetaTipo: nombreTipoDocumento(adjunto.mime, filename: adjunto.filename)
                ) {
                    abrirAdjunto(adjunto)
                }
            } else {
                // Documento generado (PDF, markdown, hoja de cálculo…): chip
                // tocable que abre el visor seguro (SecurePreviewSheet).
                Button {
                    abrirAdjunto(adjunto)
                } label: {
                    HStack(spacing: 8) {
                        Image(systemName: iconoDocumento(adjunto.mime, filename: adjunto.filename))
                            .font(.system(size: 16, weight: .medium))
                            .foregroundStyle(EdecanTheme.morado)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(adjunto.filename)
                                .font(.footnote.weight(.medium))
                                .foregroundStyle(.primary)
                                .lineLimit(1)
                            Text(nombreTipoDocumento(adjunto.mime, filename: adjunto.filename))
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                        Spacer(minLength: 6)
                        Text("Ver")
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(EdecanTheme.morado)
                        Image(systemName: "eye.fill")
                            .font(.caption)
                            .foregroundStyle(EdecanTheme.morado)
                    }
                    .padding(.horizontal, 12)
                    .padding(.vertical, 9)
                    .frame(maxWidth: 280, alignment: .leading)
                    .tarjetaVidrio(esquina: 12)
                }
                .buttonStyle(.plain)
            }
        }
    }

    private func esArchivoCodigo(_ mime: String?, filename: String) -> Bool {
        let name = filename.lowercased()
        let m = (mime ?? "").lowercased()
        let exts = [
            ".swift", ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".kt",
            ".c", ".h", ".cpp", ".hpp", ".m", ".mm", ".cs", ".rb", ".php", ".sh",
            ".css", ".scss", ".sql", ".toml", ".json", ".yml", ".yaml",
        ]
        if exts.contains(where: { name.hasSuffix($0) }) { return true }
        return m == "text/x-swift" || m == "text/x-python" || m.contains("javascript")
            || m.contains("typescript")
    }

    private func abrirAdjunto(_ adjunto: AdjuntoBot) {
        previewTarget = .artifact(
            ArtifactRef(fileId: adjunto.fileId, filename: adjunto.filename, mime: adjunto.mime)
        )
    }

    private func iconoDocumento(_ mime: String?, filename: String = "") -> String {
        let m = (mime ?? "").lowercased()
        let name = filename.lowercased()
        if m.contains("pdf") || name.hasSuffix(".pdf") { return "doc.richtext" }
        if m.contains("spreadsheet") || m.contains("excel") || m.contains("csv") || name.hasSuffix(".csv") {
            return "tablecells"
        }
        if m.contains("markdown") || name.hasSuffix(".md") || name.hasSuffix(".markdown") {
            return "doc.richtext.fill"
        }
        if [".swift", ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".kt",
            ".c", ".h", ".cpp", ".css", ".sh", ".sql"].contains(where: { name.hasSuffix($0) }) {
            return "chevron.left.forwardslash.chevron.right"
        }
        if m.contains("text/") || name.hasSuffix(".txt") { return "doc.text" }
        return "doc"
    }

    private func nombreTipoDocumento(_ mime: String?, filename: String = "") -> String {
        let m = (mime ?? "").lowercased()
        let name = filename.lowercased()
        if m.contains("pdf") || name.hasSuffix(".pdf") { return "PDF" }
        if m.contains("spreadsheet") || m.contains("excel") { return "Hoja de cálculo" }
        if m.contains("csv") || name.hasSuffix(".csv") { return "CSV" }
        if m.contains("markdown") || name.hasSuffix(".md") || name.hasSuffix(".markdown") {
            return "Markdown"
        }
        let code: [(String, String)] = [
            (".swift", "Swift"), (".py", "Python"), (".ts", "TypeScript"), (".tsx", "TSX"),
            (".js", "JavaScript"), (".go", "Go"), (".rs", "Rust"), (".java", "Java"),
            (".kt", "Kotlin"), (".css", "CSS"), (".html", "HTML"), (".json", "JSON"),
            (".sh", "Shell"), (".sql", "SQL"),
        ]
        if let hit = code.first(where: { name.hasSuffix($0.0) }) {
            return "Código · \(hit.1)"
        }
        if m.hasPrefix("text/") { return "Texto" }
        return "Documento"
    }

    /// Chips de color tipo Grok (SHA / main) debajo del texto del bot.
    @ViewBuilder
    private func chipsReferenciasGit(_ texto: String) -> some View {
        let refs = Self.extraerReferenciasGit(texto)
        if !refs.isEmpty {
            FlowLayoutChips(spacing: 6) {
                ForEach(refs, id: \.self) { ref in
                    let esMain = ref == "main"
                    let esPR = ref.hasPrefix("PR #")
                    Text(ref)
                        .font(.caption2.weight(.bold))
                        .monospaced(!esPR)
                        .padding(.horizontal, 9)
                        .padding(.vertical, 5)
                        .foregroundStyle(
                            esMain ? Color.white
                                : (esPR ? Color.white : EdecanTheme.azul)
                        )
                        .background(
                            Capsule(style: .continuous)
                                .fill(
                                    esMain ? EdecanTheme.morado
                                        : (esPR ? EdecanTheme.azul : EdecanTheme.azul.opacity(0.16))
                                )
                        )
                        .overlay(
                            Capsule(style: .continuous)
                                .strokeBorder(
                                    esMain || esPR
                                        ? Color.white.opacity(0.22)
                                        : EdecanTheme.azul.opacity(0.35),
                                    lineWidth: 1
                                )
                        )
                }
            }
        }
    }

    private static func extraerReferenciasGit(_ texto: String) -> [String] {
        var vistos = Set<String>()
        var orden: [String] = []
        let ns = texto as NSString
        let rango = NSRange(location: 0, length: ns.length)
        if let prs = try? NSRegularExpression(
            pattern: #"(?:PR\s*#|#)(\d{1,5})\b|github\.com/[^\s]+/pull/(\d+)"#,
            options: [.caseInsensitive]
        ) {
            for match in prs.matches(in: texto, range: rango) {
                for g in 1...match.numberOfRanges - 1 {
                    let r = match.range(at: g)
                    guard r.location != NSNotFound else { continue }
                    let n = "PR #\(ns.substring(with: r))"
                    if vistos.insert(n).inserted { orden.append(n) }
                }
            }
        }
        if let sha = try? NSRegularExpression(
            pattern: #"(?<![0-9a-fA-F])([0-9a-f]{7,40})(?![0-9a-fA-F])"#
        ) {
            for match in sha.matches(in: texto, range: rango) {
                let s = ns.substring(with: match.range(at: 1)).lowercased()
                guard s.contains(where: { $0.isLetter }) else { continue }
                let corto = s.count > 12 ? String(s.prefix(8)) : s
                if vistos.insert(corto).inserted {
                    orden.append(corto)
                }
            }
        }
        let lower = texto.lowercased()
        let hints = ["`main`", "/main", "branch main", "en main", " a main"]
        if hints.contains(where: { lower.contains($0) }), vistos.insert("main").inserted {
            orden.insert("main", at: 0)
        }
        return Array(orden.prefix(8))
    }


    /// Cara-orbe desde el snapshot del backend (sin worker completo).
    @ViewBuilder
    private func caraSnapshot(_ cara: CaraSnapshot, nombre: String, size: CGFloat) -> some View {
        CaraOrbe(
            nombre: nombre,
            formaBot: cara.shape ?? "circle",
            fillHex: cara.fill ?? "#6366f1",
            accentHex: cara.accent,
            ojoIzq: cara.eyes?.left.map {
                OjoDeCara(
                    x: CGFloat($0.x ?? 0.34), y: CGFloat($0.y ?? 0.38),
                    rx: CGFloat($0.rx ?? 0.07), ry: CGFloat($0.ry ?? 0.08),
                    rotation: CGFloat($0.rotation ?? 0)
                )
            },
            ojoDer: cara.eyes?.right.map {
                OjoDeCara(
                    x: CGFloat($0.x ?? 0.66), y: CGFloat($0.y ?? 0.38),
                    rx: CGFloat($0.rx ?? 0.07), ry: CGFloat($0.ry ?? 0.08),
                    rotation: CGFloat($0.rotation ?? 0)
                )
            },
            size: size,
            animado: true,
            activo: false
        )
    }

    // MARK: - Entrada

    private var barraDeEntrada: some View {
        VStack(spacing: 0) {
            ContenedorVidrioBots(spacing: 10) {
                if !adjuntosPendientes.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            ForEach(adjuntosPendientes) { adjunto in
                                chipAdjuntoPendiente(adjunto)
                            }
                        }
                        .padding(.horizontal, 2)
                    }
                    .padding(.horizontal, 14)
                    .padding(.top, 10)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
                }
                HStack(alignment: .bottom, spacing: 10) {
                    Button {
                        Haptico.ligero()
                        campoEnfocado = false
                        withAnimation(.spring(response: 0.35, dampingFraction: 0.85)) {
                            mostrandoSelectorAdjuntos = true
                        }
                    } label: {
                        Image(systemName: "plus")
                            .font(.system(size: 18, weight: .semibold))
                            .foregroundStyle(.primary)
                            .frame(width: 40, height: 40)
                    }
                    .accessibilityLabel("Adjuntar fotos o archivos")
                    .capsulaVidrio()
                    .vidrioMorphID("adjuntar", in: composerNamespace)

                    TextField("Escribe a \(bot.nombreVisible)…", text: $texto, axis: .vertical)
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
                        .vidrioMorphID("campo", in: composerNamespace)

                    // Sin spinner en el botón de enviar: escribir y encolar
                    // SIEMPRE está permitido mientras el bot trabaja (el
                    // servidor serializa los turnos). El estado «trabajando»
                    // vive en la cabecera y en la fila de estado.
                    Button {
                        Haptico.ligero()
                        mostrandoSelectorModelo = true
                    } label: {
                        IconoRobotModelo()
                            .frame(width: 42, height: 42)
                            .tarjetaVidrio(esquina: 18, tint: EdecanTheme.morado)
                    }
                    .accessibilityLabel("Modelo del bot: \(nombreModeloActivo)")
                    .vidrioMorphID("modelo", in: composerNamespace)

                    BotonEnviarNegro(habilitado: botonHabilitado) {
                        encolarEnvio()
                    }
                    .vidrioMorphID("enviar", in: composerNamespace)
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
            }
            .padding(.horizontal, 12)
            .padding(.top, 6)
            .shadow(color: Color.black.opacity(0.05), radius: 16, y: -4)
        }
        .background(FondoGlowComposer())
        .safeAreaPadding(.bottom, 6)
    }

    /// Escribir SIEMPRE está permitido, aunque el bot esté trabajando: el
    /// mensaje se encola y se envía cuando el turno anterior termina.
    private var botonHabilitado: Bool {
        guard confirmacionEnVivo == nil else { return false }
        let hayTexto = !texto.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        let hayAdjuntos = !adjuntosPendientes.isEmpty
        return hayTexto || hayAdjuntos
    }

    private func encolarEnvio() {
        let limpio = texto.trimmingCharacters(in: .whitespacesAndNewlines)
        let adjuntos = adjuntosPendientes
        guard !limpio.isEmpty || !adjuntos.isEmpty, let client = session.client else { return }
        error = nil
        adjuntosPendientes = []

        // Comandos locales: `/clear` (y sinónimos) reinician el chat del bot
        // sin pasarlo por el modelo — mismo criterio que el chat principal.
        let comando = limpio.lowercased()
        if ["/clear", "/reset", "/nuevo", "/new"].contains(comando) {
            texto = ""
            campoEnfocado = false
            Task { await limpiarChatBot(client: client) }
            return
        }

        items.append(ItemMensajeBot(id: UUID().uuidString, esUsuario: true, texto: limpio, adjuntos: adjuntos.map {
            AdjuntoBot(fileId: $0.fileId, filename: $0.filename, mime: $0.mime)
        }))
        // El texto viaja con el mensaje y la caja queda limpia: escribir de
        // nuevo no debe exigir borrar lo anterior.
        texto = ""

        // Envío INMEDIATO, como un chat con una persona: cada mensaje sale
        // en el instante en que lo mandas, sin esperar a que el bot termine
        // de responder el anterior. El ORDEN lo garantiza el servidor (lock
        // por worker en `send_worker_message`): los turnos corren en
        // secuencia aunque los envíos lleguen a la vez.
        cadenaEnvio = Task {
            await hacerEnvio(client: client, texto: limpio, adjuntos: adjuntos)
        }
    }

    private func limpiarChatBot(client: APIClient) async {
        do {
            try await client.clearWorkerMessages(workerId: bot.id)
            // BOTS-09: invalidar el snapshot de disco ANTES de limpiar la
            // memoria, y marcar la sesión para que `pintarSnapshotLocalSiVacio`
            // no repinte un chat que el servidor ya vació.
            botSnapshotStore.remove(workerId: bot.id)
            snapshotInvalidado = true
            epochConversacion = nil
            items.removeAll()
            delegacionesIDE.removeAll()
            // F2: la franja de misiones también se limpia (memoria + disco +
            // polling) — un chat nuevo no arrastra el trabajo viejo.
            detenerPollingMisiones()
            misionesDelHilo.removeAll()
            UserDefaults.standard.removeObject(forKey: claveMisionesLocales)
            let saludo = "¡Listo! Empezamos de cero, tú y yo. ¿Qué hacemos hoy?"
            items = [ItemMensajeBot(id: UUID().uuidString, esUsuario: false, texto: saludo, nombreRemitente: bot.nombreVisible)]
            error = nil
        } catch {
            self.error = "No pude limpiar el chat. Revisa que la Mac esté encendida."
        }
    }

    private func anexarBeatSiNuevo(
        _ texto: String?,
        burbujas: inout Set<String>
    ) {
        let limpio = texto?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        guard !limpio.isEmpty else { return }
        let reciente = items.suffix(6).contains {
            !$0.esUsuario && $0.esBeat && $0.texto == limpio
        }
        guard !reciente else { return }
        let beatId = UUID().uuidString
        items.append(
            ItemMensajeBot(
                id: beatId,
                esUsuario: false,
                texto: limpio,
                nombreRemitente: bot.nombreVisible,
                esBeat: true
            )
        )
        burbujas.insert(beatId)
    }

    private func registrarToolStart(
        toolCallId: String?,
        name: String,
        burbujas: inout Set<String>
    ) {
        if name == "delegar_al_ide" { return }
        guard !BeatHerramientaCopy.silenciosas.contains(name) else { return }
        herramientaActiva = HerramientaBotActiva(
            id: toolCallId ?? UUID().uuidString,
            nombre: name,
            detalle: BeatHerramientaCopy.mensaje(nombre: name, progreso: nil)
        )
        anexarBeatSiNuevo(BeatHerramientaCopy.beatParaToolStart(nombre: name), burbujas: &burbujas)
    }

    private func registrarToolProgress(
        toolCallId: String?,
        name: String,
        elapsedSeconds: Int,
        detalle: String,
        anclaDelegacion: String
    ) {
        if name == "delegar_al_ide" {
            actualizarDelegacion(
                toolCallId: toolCallId,
                ancla: anclaDelegacion,
                fase: .procesando,
                estado: "Procesando… · \(elapsedSeconds)s"
            )
            return
        }
        guard !BeatHerramientaCopy.silenciosas.contains(name) else { return }
        herramientaActiva = HerramientaBotActiva(
            id: toolCallId ?? herramientaActiva?.id ?? UUID().uuidString,
            nombre: name,
            detalle: BeatHerramientaCopy.mensaje(nombre: name, progreso: detalle)
        )
    }

    private func registrarToolEnd(
        toolCallId: String?,
        name: String,
        preview: String,
        burbujas: inout Set<String>,
        anclaDelegacion: String
    ) {
        if name == "delegar_al_ide" {
            actualizarDelegacion(
                toolCallId: toolCallId,
                ancla: anclaDelegacion,
                fase: .terminado,
                estado: "Terminado"
            )
            return
        }
        if let beat = BeatHerramientaCopy.beatParaToolEnd(nombre: name, preview: preview) {
            anexarBeatSiNuevo(beat, burbujas: &burbujas)
        }
        if name != "avisar_avance" {
            herramientaActiva = nil
        }
    }

    private func hacerEnvio(client: APIClient, texto: String, adjuntos: [AdjuntoBot]) async {
        turnosActivos += 1
        defer { turnosActivos -= 1 }
        let respuestaId = UUID().uuidString
        let claveIdempotencia = UUID().uuidString
        // Burbujas de ESTE turno (E-IOS-2): si la conexión cae y se
        // reconecta, el replay completo las volvería a pintar encima —
        // duplicando avisos y respuestas. La reconexión las retira y pinta
        // de cero desde el replay.
        var burbujasDelTurno: Set<String> = [respuestaId]
        items.append(
            ItemMensajeBot(
                id: respuestaId,
                esUsuario: false,
                texto: "",
                nombreRemitente: bot.nombreVisible,
                enProgreso: true
            )
        )

        do {
            let request = try await construirPeticion(
                client: client, texto: texto, adjuntos: adjuntos, clave: claveIdempotencia
            )
            // THROTTLE del streaming: antes cada delta de texto disparaba un
            // re-layout de SwiftUI (markdown incluido) — con respuestas largas
            // el main thread se saturaba y el watchdog de escena mataba la
            // app (0x8BADF00D, crash 29-ago 23:20). Acumulamos y pintamos
            // máximo ~8 veces por segundo; el texto final se pinta completo
            // al recibir .done.
            var textoParcial = ""
            var ultimoPintado = Date.distantPast
            // Burbuja en la que cae el texto AHORA. El servidor abre un
            // mensaje nuevo por cada tramo del bot (message_start), así cada
            // mensaje suyo es su propia burbuja, no un bloque pegado.
            var anclaActual = respuestaId
            func pintar(final: Bool = false) {
                let ahora = Date()
                if final || ahora.timeIntervalSince(ultimoPintado) > 0.12 {
                    ultimoPintado = ahora
                    guard let indice = items.firstIndex(where: { $0.id == anclaActual }) else { return }
                    items[indice].texto = textoParcial
                }
            }
            var falloDelTurno = false
            var esperandoConfirmacion = false
            for try await evento in sseClient.stream(request) {
                switch evento {
                case .textDelta(let delta):
                    textoParcial += delta
                    pintar()
                case .messageStart(let messageId):
                    aplicarMessageStart(
                        messageId: messageId,
                        anclaActual: &anclaActual,
                        burbujasDelTurno: &burbujasDelTurno
                    )
                    textoParcial = ""
                    ultimoPintado = Date.distantPast
                case .messageEnd(let messageId):
                    aplicarMessageEnd(messageId: messageId, anclaActual: anclaActual)
                    pintar(final: true)
                case .toolStart(let toolCallId, let name, _):
                    if name == "delegar_al_ide" {
                        actualizarDelegacion(
                            toolCallId: toolCallId, ancla: respuestaId, fase: .creando,
                            estado: "Creando…"
                        )
                    } else {
                        registrarToolStart(
                            toolCallId: toolCallId, name: name, burbujas: &burbujasDelTurno
                        )
                    }
                case .toolProgress(let toolCallId, let name, let elapsedSeconds, let detalle):
                    registrarToolProgress(
                        toolCallId: toolCallId,
                        name: name,
                        elapsedSeconds: elapsedSeconds,
                        detalle: detalle,
                        anclaDelegacion: respuestaId
                    )
                case .toolEnd(let toolCallId, let name, let preview, let artifacts, let blocksVersion, let bloques, let missionId):
                    registrarToolEnd(
                        toolCallId: toolCallId,
                        name: name,
                        preview: preview,
                        burbujas: &burbujasDelTurno,
                        anclaDelegacion: respuestaId
                    )
                    agregarArtefactos(artifacts, a: anclaActual)
                    if blocksVersion == 1, let indice = items.firstIndex(where: { $0.id == anclaActual }) {
                        for bloque in bloques where !items[indice].bloques.contains(bloque) {
                            items[indice].bloques.append(bloque)
                            if case .media(let media) = bloque {
                                agregarArtefactos([media.artifact], a: anclaActual)
                            }
                        }
                    }
                    if name == "delegar_mision", let missionId, !missionId.isEmpty {
                        // La misión de Astra queda VISIBLE en el chat: mini-
                        // menú con los pasos en curso, actualizado en vivo.
                        if !misionesDelHilo.contains(where: { $0.missionId == missionId }) {
                            misionesDelHilo.append(
                                ItemMisionBot(missionId: missionId, ancla: anclaActual)
                            )
                            persistirMisionesDelHilo()
                            iniciarSeguimientoMision(missionId)
                        }
                    }
                case .confirmationRequired(let toolCallId, let nombre, let args):
                    pintar(final: true)
                    if let indice = items.firstIndex(where: { $0.id == anclaActual }) {
                        items[indice].enProgreso = false
                    }
                    confirmacionEnVivo = ConfirmacionBotPendiente(
                        toolCallId: toolCallId,
                        nombre: nombre,
                        args: args
                    )
                    esperandoConfirmacion = true
                case .done:
                    pintar(final: true)
                    finalizarDelegaciones(respuestaId)
                    herramientaActiva = nil
                    if let indice = items.firstIndex(where: { $0.id == anclaActual }) {
                        items[indice].enProgreso = false
                    }
                case .error:
                    falloDelTurno = true
                    pintar(final: true)
                    herramientaActiva = nil
                    quitarDelegacionesIncompletas(respuestaId)
                    if let indice = items.firstIndex(where: { $0.id == respuestaId }) {
                        items[indice].enProgreso = false
                        if items[indice].texto.isEmpty {
                            items[indice].texto = "Ups, se me enredó a mitad de camino. Pídemelo de nuevo y lo intento por otra vía."
                        } else {
                            // C-3: entra al set del turno para que una
                            // reconexión exitosa no la deje junto al replay.
                            let perdonId = UUID().uuidString
                            items.append(
                                ItemMensajeBot(
                                    id: perdonId,
                                    esUsuario: false,
                                    texto: "…perdón, se me cortó justo ahí. Dime «continúa» y sigo.",
                                    nombreRemitente: bot.nombreVisible
                                )
                            )
                            burbujasDelTurno.insert(perdonId)
                        }
                    }
                default:
                    break
                }
            }
            if esperandoConfirmacion {
                return
            }
            if !falloDelTurno {
                quitarBurbujaVacia(anclaActual)
                return
            }
        } catch is CancellationError {
            // La vista se fue; el servidor conserva el turno y lo completa.
            // Al volver, `cargar()` trae la respuesta ya persistida.
            return
        } catch let sseError as SSEClient.SSEError {
            if case .servidor(let status, _) = sseError,
               (400..<500).contains(status), status != 409, status != 408, status != 429 {
                marcarEnvioRechazado(
                    respuestaId: respuestaId,
                    burbujasDelTurno,
                    texto: texto,
                    adjuntos: adjuntos,
                    mensaje: mensajeRechazo(status: status, fallback: sseError.localizedDescription)
                )
                return
            }
            // CONEXIÓN PERDIDA a mitad del turno. Nada de disculpas: el turno
            // puede seguir corriendo en el servidor y se recupera abajo con
            // la misma Idempotency-Key.
        } catch {
            // CONEXIÓN PERDIDA a mitad del turno. Nada de disculpas: el turno
            // SIGUE corriendo en la Mac (productor desacoplado del socket en
            // el backend). Reconectamos con la MISMA Idempotency-Key: el
            // backend responde 409 mientras sigue en vuelo y entrega el
            // replay exacto del turno completo en cuanto termina.
        }

        await reconectarTurno(
            client: client,
            texto: texto,
            adjuntos: adjuntos,
            clave: claveIdempotencia,
            respuestaId: respuestaId,
            burbujasDelTurno: burbujasDelTurno
        )
    }

    /// Reconexión con la misma Idempotency-Key: 202/409 = sigue en vuelo
    /// (esperar y reintentar); 200 = replay del turno completo; timeout
    /// honesto a los ~10 minutos (el push del servidor avisa igualmente).
    private func reconectarTurno(
        client: APIClient,
        texto: String,
        adjuntos: [AdjuntoBot],
        clave: String,
        respuestaId: String,
        burbujasDelTurno: Set<String>
    ) async {
        // E-IOS-2: el replay entrega el turno COMPLETO. La limpieza de las
        // burbujas del intento caído es PEREZOSA y por-intento: solo ocurre
        // cuando un intento RECIBE el primer evento del replay (un 202 de
        // "sigue en vuelo" no toca nada y el texto parcial pintado se
        // conserva; si un replay a su vez se corta, el siguiente intento
        // vuelve a limpiar — sin duplicados IOS-2A/2B/2C).
        var burbujasTurno = burbujasDelTurno
        let limite = Date().addingTimeInterval(600)
        while Date() < limite {
            try? await Task.sleep(for: .seconds(2))
            do {
                let request = try await construirPeticion(
                    client: client, texto: texto, adjuntos: adjuntos, clave: clave
                )
                var textoParcial = ""
                var ultimoPintado = Date.distantPast
                var anclaActual = respuestaId
                var limpiezaDeEsteIntento = false
                func pintar(final: Bool = false) {
                    let ahora = Date()
                    if final || ahora.timeIntervalSince(ultimoPintado) > 0.12 {
                        ultimoPintado = ahora
                        guard let indice = items.firstIndex(where: { $0.id == anclaActual }) else { return }
                        items[indice].texto = textoParcial
                    }
                }
                var termino = false
            var falloServidor = false
            var esperandoConfirmacion = false
            for try await evento in sseClient.stream(request) {
                if !limpiezaDeEsteIntento {
                    // Primer frame de un replay real: retirar las burbujas de
                    // los intentos previos y empezar limpio.
                    items.removeAll { burbujasTurno.contains($0.id) }
                    items.append(
                        ItemMensajeBot(
                            id: respuestaId,
                            esUsuario: false,
                            texto: "",
                            nombreRemitente: bot.nombreVisible,
                            enProgreso: true
                        )
                    )
                    burbujasTurno.insert(respuestaId)
                    limpiezaDeEsteIntento = true
                }
                switch evento {
                case .textDelta(let delta):
                    textoParcial += delta
                    pintar()
                case .messageStart(let messageId):
                    aplicarMessageStart(
                        messageId: messageId,
                        anclaActual: &anclaActual,
                        burbujasDelTurno: &burbujasTurno
                    )
                    textoParcial = ""
                    ultimoPintado = Date.distantPast
                case .messageEnd(let messageId):
                    aplicarMessageEnd(messageId: messageId, anclaActual: anclaActual)
                    pintar(final: true)
                case .toolStart(let toolCallId, let name, _):
                    if name == "delegar_al_ide" {
                        actualizarDelegacion(
                            toolCallId: toolCallId, ancla: respuestaId, fase: .creando,
                            estado: "Creando…"
                        )
                    } else {
                        registrarToolStart(
                            toolCallId: toolCallId, name: name, burbujas: &burbujasTurno
                        )
                    }
                case .toolProgress(let toolCallId, let name, let elapsedSeconds, let detalle):
                    registrarToolProgress(
                        toolCallId: toolCallId,
                        name: name,
                        elapsedSeconds: elapsedSeconds,
                        detalle: detalle,
                        anclaDelegacion: respuestaId
                    )
                case .toolEnd(let toolCallId, let name, let preview, let artifacts, let blocksVersion, let bloques, _):
                    registrarToolEnd(
                        toolCallId: toolCallId,
                        name: name,
                        preview: preview,
                        burbujas: &burbujasTurno,
                        anclaDelegacion: respuestaId
                    )
                    agregarArtefactos(artifacts, a: anclaActual)
                    if blocksVersion == 1, let indice = items.firstIndex(where: { $0.id == anclaActual }) {
                        for bloque in bloques where !items[indice].bloques.contains(bloque) {
                            items[indice].bloques.append(bloque)
                            if case .media(let media) = bloque {
                                agregarArtefactos([media.artifact], a: anclaActual)
                            }
                        }
                    }
                case .confirmationRequired(let toolCallId, let nombre, let args):
                    pintar(final: true)
                    if let indice = items.firstIndex(where: { $0.id == anclaActual }) {
                        items[indice].enProgreso = false
                    }
                    confirmacionEnVivo = ConfirmacionBotPendiente(
                        toolCallId: toolCallId,
                        nombre: nombre,
                        args: args
                    )
                    esperandoConfirmacion = true
                case .done:
                    pintar(final: true)
                    herramientaActiva = nil
                    termino = true
                case .error:
                    herramientaActiva = nil
                    falloServidor = true
                default:
                    break
                }
            }
            if esperandoConfirmacion {
                return
            }
            guard termino, !falloServidor else {
                quitarDelegacionesIncompletas(respuestaId)
                // C-2: sin texto ni spinner eterno en la burbuja del replay.
                if let indice = items.firstIndex(where: { $0.id == anclaActual }) {
                    items[indice].enProgreso = false
                    if items[indice].texto.isEmpty {
                        items[indice].texto = "El turno se cortó en el servidor. Pídemelo de nuevo y lo retomo."
                    }
                }
                return
            }
            finalizarDelegaciones(respuestaId)
            if let indice = items.firstIndex(where: { $0.id == anclaActual }) {
                items[indice].enProgreso = false
                if items[indice].texto.isEmpty && items[indice].adjuntos.isEmpty {
                    items[indice].texto = "Listo, terminé (terminé el trabajo en segundo plano)."
                }
            }
            quitarBurbujaVacia(anclaActual)
            return
            } catch is CancellationError {
                return
            } catch let apiError as APIClient.APIError where apiError == .sesionExpirada {
                // C-1: refresh local muerto — reintentar 10 min no sirve.
                if let indice = items.firstIndex(where: { $0.id == respuestaId }) {
                    items[indice].enProgreso = false
                    if items[indice].texto.isEmpty {
                        items[indice].texto = "Se cerró tu sesión mientras trabajaba. Vuelve a entrar y me pides continuar."
                    }
                }
                return
            } catch let sseError as SSEClient.SSEError {
                // F5: el turno murió por un restart (x-run-state: interrupted).
                // Reintentar con la misma clave no sirve — se marca la burbuja
                // y el dueño la toca para reenviar con una clave nueva.
                if case .interrumpido = sseError {
                    marcarBurbujaInterrumpida(respuestaId, texto: texto, adjuntos: adjuntos)
                    return
                }
                if case .servidor(let status, _) = sseError, status == 202 || status == 409 {
                    continue // sigue en vuelo: esperar y reintentar
                }
                if case .servidor(let status, _) = sseError, (400..<500).contains(status), status != 408, status != 429 {
                    // Rechazo definitivo (hash distinto, clave inválida…):
                    // reintentar no sirve. Nunca afirmar que el servidor
                    // sigue trabajando cuando rechazó la petición.
                    marcarEnvioRechazado(
                        respuestaId: respuestaId,
                        burbujasTurno,
                        texto: texto,
                        adjuntos: adjuntos,
                        mensaje: mensajeRechazo(status: status, fallback: sseError.localizedDescription)
                    )
                    return
                }
                continue // red caída de nuevo: seguir esperando
            } catch {
                continue // conexión de nuevo caída: seguir esperando
            }
        }
        marcarBurbujaAtascada(respuestaId)
    }

    /// Tras el timeout de reconexión: honesto y accionable — el push del
    /// servidor avisa igualmente cuando el turno termine.
    private func marcarBurbujaAtascada(_ respuestaId: String) {
        quitarDelegacionesIncompletas(respuestaId)
        if let indice = items.firstIndex(where: { $0.id == respuestaId }) {
            items[indice].enProgreso = false
            if items[indice].texto.isEmpty {
                items[indice].texto =
                    "Sigo trabajando en segundo plano; cuando termine te llega el aviso."
            }
        }
    }

    /// F5: el turno murió por un reinicio del servidor (`x-run-state:
    /// interrupted`). La burbuja NO queda «atascada» (el push no llegará): se
    /// marca «Interrumpido» y se guarda el texto/adjuntos originales para que
    /// el toque reenvíe con una clave NUEVA.
    private func marcarBurbujaInterrumpida(
        _ respuestaId: String,
        texto: String,
        adjuntos: [AdjuntoBot]
    ) {
        quitarDelegacionesIncompletas(respuestaId)
        if let indice = items.firstIndex(where: { $0.id == respuestaId }) {
            items[indice].enProgreso = false
            items[indice].interrumpido = true
            items[indice].textoOriginal = texto
            items[indice].adjuntosOriginales = adjuntos
            items[indice].texto = "Interrumpido — tócala para reenviar"
        }
    }

    /// F5: reenvío desde una burbuja interrumpida. `hacerEnvio` genera una
    /// clave de idempotencia NUEVA por turno — no se reutiliza la clave que el
    /// servidor ya marcó como interrumpida.
    private func resenviarInterrumpido(id: String, texto: String, adjuntos: [AdjuntoBot]) {
        guard let client = session.client else { return }
        items.removeAll { $0.id == id }
        cadenaEnvio = Task {
            await hacerEnvio(client: client, texto: texto, adjuntos: adjuntos)
        }
    }

    /// Un 4xx significa que el servidor NO aceptó el turno. Conserva el
    /// borrador/adjuntos para reintentar y muestra el rechazo real, en vez de
    /// prometer falsamente que el bot sigue trabajando en segundo plano.
    private func marcarEnvioRechazado(
        respuestaId: String,
        _ burbujasDelTurno: Set<String>,
        texto: String,
        adjuntos: [AdjuntoBot],
        mensaje: String
    ) {
        quitarDelegacionesIncompletas(respuestaId)
        for indice in items.indices where burbujasDelTurno.contains(items[indice].id) {
            items[indice].enProgreso = false
        }
        items.removeAll {
            burbujasDelTurno.contains($0.id) && $0.texto.isEmpty && $0.adjuntos.isEmpty
        }
        if self.texto.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            self.texto = texto
        }
        let idsPendientes = Set(adjuntosPendientes.map(\.fileId))
        adjuntosPendientes.append(contentsOf: adjuntos.filter { !idsPendientes.contains($0.fileId) })
        error = mensaje
    }

    private func mensajeRechazo(status: Int, fallback: String) -> String {
        switch status {
        case 413:
            return "El servidor rechazó el envío porque uno de los archivos es demasiado grande (413)."
        case 422:
            return "El servidor rechazó el mensaje o uno de sus adjuntos (422). Revisa los archivos e intenta de nuevo."
        default:
            return fallback
        }
    }

    /// Los artefactos de `tool_end` son entregables del bot, no telemetría.
    /// Se incorporan a la burbuja viva y se deduplican por `file_id` para que
    /// una repetición del evento no duplique miniaturas/documentos.
    private func agregarArtefactos(_ artifacts: [ArtifactRef], a mensajeId: String) {
        guard !artifacts.isEmpty,
              let indice = items.firstIndex(where: { $0.id == mensajeId })
        else { return }
        var existentes = Set(items[indice].adjuntos.map(\.fileId))
        for artifact in artifacts where existentes.insert(artifact.fileId).inserted {
            items[indice].adjuntos.append(AdjuntoBot(
                fileId: artifact.fileId,
                filename: artifact.filename,
                mime: artifact.mime
            ))
        }
    }

    private func quitarBurbujaVacia(_ id: String) {
        guard let indice = items.firstIndex(where: { $0.id == id }),
              items[indice].texto.isEmpty,
              items[indice].adjuntos.isEmpty
        else { return }
        items.remove(at: indice)
    }

    /// Franja Liquid Glass PEGADA al borde del composer (estática, no en el
    /// hilo): chips de las misiones de Astra con estado en vivo; tocar abre
    /// el panel expandible con los pasos.
    private var franjaTareas: some View {
        ContenedorVidrioBots(spacing: 8) {
            VStack(alignment: .leading, spacing: 8) {
                ForEach(misionesDelHilo) { mision in
                    chipMision(mision)
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
        }
        .padding(.horizontal, 12)
        .background(FondoGlowComposer().opacity(0.6))
    }

    @ViewBuilder
    private func chipMision(_ mision: ItemMisionBot) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Button {
                withAnimation(.spring(response: 0.32, dampingFraction: 0.82)) {
                    if let indice = misionesDelHilo.firstIndex(where: { $0.id == mision.id }) {
                        misionesDelHilo[indice].expandida.toggle()
                    }
                }
            } label: {
                HStack(spacing: 10) {
                    ZStack {
                        RoundedRectangle(cornerRadius: 8, style: .continuous)
                            .fill(EdecanTheme.morado.opacity(0.13))
                            .frame(width: 26, height: 26)
                        if mision.detalle?.mission.esTerminal == true {
                            Image(systemName: "checkmark")
                                .font(.system(size: 12, weight: .bold))
                                .foregroundStyle(EdecanTheme.morado)
                        } else {
                            ProgressView()
                                .controlSize(.mini)
                                .tint(EdecanTheme.morado)
                        }
                    }
                    Text(mision.titulo)
                        .font(.system(size: 13, weight: .medium, design: .rounded))
                        .foregroundStyle(.primary)
                        .lineLimit(1)
                    Spacer(minLength: 0)
                    Image(systemName: mision.expandida ? "chevron.up" : "chevron.down")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(.secondary)
                }
            }
            .buttonStyle(.plain)

            if mision.expandida {
                VStack(alignment: .leading, spacing: 6) {
                    if let pasos = mision.detalle?.steps, !pasos.isEmpty {
                        ForEach(pasos, id: \.seq) { paso in
                            HStack(alignment: .top, spacing: 8) {
                                Image(systemName: iconoPaso(paso.status))
                                    .font(.system(size: 11, weight: .semibold))
                                    .foregroundStyle(colorPaso(paso.status))
                                    .frame(width: 16)
                                VStack(alignment: .leading, spacing: 1) {
                                    Text(paso.agente)
                                        .font(.system(size: 12, weight: .semibold, design: .rounded))
                                    if !paso.instruccion.isEmpty {
                                        Text(paso.instruccion)
                                            .font(.system(size: 11.5, design: .rounded))
                                            .foregroundStyle(.secondary)
                                            .lineLimit(2)
                                    }
                                }
                            }
                        }
                    } else {
                        Text("Astra está planificando los pasos…")
                            .font(.system(size: 12, design: .rounded))
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(.leading, 36)
                .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .tarjetaVidrio(esquina: 16, tint: EdecanTheme.morado)
    }

    /// Server-driven: el servidor puede apagar la franja sin TestFlight
    /// (`ui.chat_bot.tareas_strip`). Por defecto, encendida.
    private var franjaTareasHabilitada: Bool {
        if case .object(let chatBot) = session.mobileConfig.ui["chat_bot"],
           case .bool(let activa) = chatBot["tareas_strip"] {
            return activa
        }
        return true
    }

    private var claveMisionesLocales: String { "misiones-hilo-\(bot.id)" }

    private func persistirMisionesDelHilo() {
        let guardables = misionesDelHilo.map { [ $0.missionId ] }
        if let datos = try? JSONEncoder().encode(guardables) {
            UserDefaults.standard.set(datos, forKey: claveMisionesLocales)
        }
    }

    private func restaurarMisionesDelHilo() {
        guard let datos = UserDefaults.standard.data(forKey: claveMisionesLocales),
              let ids = try? JSONDecoder().decode([String].self, from: datos)
        else { return }
        misionesDelHilo = ids.map { ItemMisionBot(missionId: $0, ancla: "") }
        for mision in misionesDelHilo {
            iniciarSeguimientoMision(mision.missionId)
        }
    }

    private var nombreModeloActivo: String {
        if let id = modeloElegido,
           let info = catalogoModelos?.modelos.first(where: { $0.id == id }) {
            return info.nombre
        }
        if let porDefecto = catalogoModelos?.porDefecto,
           let info = catalogoModelos?.modelos.first(where: { $0.id == porDefecto }) {
            return info.nombre
        }
        return "Modelo"
    }

    /// Tasks de polling por misión: se cancelan al re-lanzar la MISMA
    /// misión (sin duplicados) y al salir de la vista (sin zombies).
    @State private var pollingMisiones: [String: Task<Void, Never>] = [:]

    private func iniciarSeguimientoMision(_ missionId: String) {
        pollingMisiones[missionId]?.cancel()
        pollingMisiones[missionId] = Task {
            guard let client = session.client else { return }
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(4))
                guard !Task.isCancelled else { return }
                guard let indice = misionesDelHilo.firstIndex(where: { $0.missionId == missionId }) else { return }
                if let detalle = try? await client.getMission(id: missionId) {
                    misionesDelHilo[indice].detalle = detalle
                    if detalle.mission.esTerminal { return }
                }
            }
        }
    }

    private func detenerPollingMisiones() {
        for tarea in pollingMisiones.values { tarea.cancel() }
        pollingMisiones.removeAll()
    }


    private func iconoPaso(_ estado: String) -> String {
        switch estado {
        case "done": return "checkmark.circle.fill"
        case "running": return "arrow.triangle.2.circlepath"
        case "waiting_confirmation": return "clock.fill"
        case "failed", "error": return "xmark.circle.fill"
        default: return "circle.dotted"
        }
    }

    private func colorPaso(_ estado: String) -> Color {
        switch estado {
        case "done": return EdecanTheme.morado
        case "running": return EdecanTheme.azul
        case "failed", "error": return .red
        default: return .secondary
        }
    }

    private func construirPeticion(
        client: APIClient, texto: String, adjuntos: [AdjuntoBot], clave: String
    ) async throws -> URLRequest {
        let url = try await client.urlCompleta("/v1/agents/workers/\(bot.id)/message")
        let token = try await client.tokenDeAccesoValido()
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        // Idempotencia: con esta clave el backend desacopla el turno del
        // socket — si la app se suspende o la red cae, el mismo POST con la
        // misma clave entrega el replay exacto sin duplicar el trabajo.
        request.setValue(clave, forHTTPHeaderField: "Idempotency-Key")
        struct Cuerpo: Encodable {
            let text: String
            let attachments: [String]
            let model: String?
            let effort: String?
        }
        request.httpBody = try JSONEncoder().encode(
            Cuerpo(
                text: texto,
                attachments: adjuntos.map(\.fileId),
                model: modeloElegido,
                effort: esfuerzoElegido?.rawValue
            )
        )
        return request
    }


    @ViewBuilder
    private func evidenciaDe(_ item: ItemMensajeBot) -> some View {
        let enlaces: [LinkPreviewBlock] = item.bloques.compactMap { bloque in
            if case .linkPreview(let link) = bloque { return link }
            return nil
        }
        if !enlaces.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                ForEach(Array(enlaces.enumerated()), id: \.offset) { _, link in
                    BotLinkEvidenceCard(link: link)
                }
            }
        }
    }

    /// Drafts sociales + cards genéricas fuera de la burbuja de texto (Grok evidence).
    @ViewBuilder
    private func evidenciaRica(_ item: ItemMensajeBot) -> some View {
        ForEach(Array(item.bloques.enumerated()), id: \.offset) { _, bloque in
            switch bloque {
            case .socialDraft(let social):
                SocialDraftCardView(
                    bloque: social,
                    client: session.client,
                    onAction: { accion in
                        manejarAccionChat(accion)
                    }
                )
                .frame(maxWidth: 320, alignment: .leading)
            case .card(let card):
                CardGenericaView(
                    card: card,
                    client: session.client,
                    onAction: { accion in manejarAccionChat(accion) },
                    onResponder: { texto in enviarTextoForzado(texto) },
                    onAbrirImagen: { art in
                        previewTarget = .artifact(art)
                    }
                )
                .frame(maxWidth: 320, alignment: .leading)
            default:
                EmptyView()
            }
        }
    }

    private func manejarAccionChat(_ accion: ChatAction) {
        switch accion {
        case .openURL(_, _, let rawURL):
            guard let destino = DestinoNavegador(rawURL.absoluteString) else {
                error = "El enlace no es seguro y no se abrió."
                return
            }
            destinoNavegador = destino
        case .openScreen(_, _, let screen) where screen == .remote:
            tabRouter.mostrarRemoto()
        case .openScreen(_, _, let screen) where screen == .settings || screen == .skills:
            tabRouter.seleccion = .settings
        case .sendMessage(_, _, let message), .prefillMessage(_, _, let message):
            if case .prefillMessage = accion {
                texto = String(message.prefix(2_000))
                campoEnfocado = true
            } else {
                enviarTextoForzado(message)
            }
        case .copyText(_, _, let text):
            UIPasteboard.general.string = text
            Haptico.ligero()
        case .approveDraft(_, _, let draftId):
            Task {
                guard let client = session.client else {
                    error = "Inicia sesión para publicar."
                    return
                }
                do {
                    _ = try await ContentStudioService(client: client).publishDraft(draftId: draftId)
                    await cargar(fusionarSiEnviando: enviandoTurno)
                } catch {
                    self.error = "No se pudo publicar: \(error.localizedDescription)"
                }
            }
        case .saveArtifact(_, _, let fileId):
            abrirAdjunto(AdjuntoBot(fileId: fileId, filename: "archivo", mime: nil))
        case .openConversation(_, _, _), .openScreen(_, _, _):
            break
        case .unsupported:
            break
        }
    }

    /// Bloques ricos del mensaje excepto widgets con fila propia o duplicados en evidenciaRica.
    private func bloquesEntregablesDe(_ item: ItemMensajeBot) -> [ChatBlock] {
        item.bloques.filter { bloque in
            switch bloque {
            case .question, .linkPreview, .socialDraft, .card:
                return false
            default:
                return true
            }
        }
    }

    private func aplicarMessageStart(
        messageId: String,
        anclaActual: inout String,
        burbujasDelTurno: inout Set<String>
    ) {
        if let indice = items.firstIndex(where: { $0.id == messageId }) {
            items[indice].enProgreso = true
            anclaActual = messageId
            return
        }
        if let indiceActual = items.firstIndex(where: { $0.id == anclaActual }),
           items[indiceActual].texto.isEmpty,
           items[indiceActual].bloques.isEmpty {
            items[indiceActual].enProgreso = false
        } else if let indiceActual = items.firstIndex(where: { $0.id == anclaActual }) {
            items[indiceActual].enProgreso = false
        }
        items.append(
            ItemMensajeBot(
                id: messageId,
                esUsuario: false,
                texto: "",
                nombreRemitente: bot.nombreVisible,
                enProgreso: true
            )
        )
        burbujasDelTurno.insert(messageId)
        anclaActual = messageId
    }

    private func aplicarMessageEnd(messageId: String, anclaActual: String) {
        if let indice = items.firstIndex(where: { $0.id == messageId }) {
            items[indice].enProgreso = false
        } else if let indice = items.firstIndex(where: { $0.id == anclaActual }) {
            items[indice].enProgreso = false
        }
    }

    private func consumirTextoInicialSiHay() async {
        guard !consumioTextoInicial else { return }
        consumioTextoInicial = true
        guard let raw = textoInicial?.trimmingCharacters(in: .whitespacesAndNewlines),
              !raw.isEmpty else { return }
        try? await Task.sleep(for: .milliseconds(350))
        enviarTextoForzado(raw)
    }

    private func confirmarTurnoBot(pendiente: ConfirmacionBotPendiente, aprobado: Bool) {
        guard let client = session.client else {
            error = "No pude confirmar: falta sesión activa."
            return
        }
        let conversationId = bot.conversationId ?? bot.id
        confirmacionEnVivo = nil
        if aprobado { Haptico.medio() } else { Haptico.advertencia() }
        turnosActivos += 1
        Task {
            defer { turnosActivos -= 1 }
            let respuestaId = UUID().uuidString
            var burbujasDelTurno: Set<String> = [respuestaId]
            items.append(
                ItemMensajeBot(
                    id: respuestaId,
                    esUsuario: false,
                    texto: "",
                    nombreRemitente: bot.nombreVisible,
                    enProgreso: true
                )
            )
            do {
                let request = try await client.peticionConfirmarConversacion(
                    conversationId: conversationId,
                    toolCallId: pendiente.toolCallId,
                    approved: aprobado
                )
                var textoParcial = ""
                var ultimoPintado = Date.distantPast
                var anclaActual = respuestaId
                func pintar(final: Bool = false) {
                    let ahora = Date()
                    if final || ahora.timeIntervalSince(ultimoPintado) > 0.12 {
                        ultimoPintado = ahora
                        guard let indice = items.firstIndex(where: { $0.id == anclaActual }) else { return }
                        items[indice].texto = textoParcial
                    }
                }
                for try await evento in sseClient.stream(request) {
                    switch evento {
                    case .textDelta(let delta):
                        textoParcial += delta
                        pintar()
                    case .messageStart(let messageId):
                        aplicarMessageStart(
                            messageId: messageId,
                            anclaActual: &anclaActual,
                            burbujasDelTurno: &burbujasDelTurno
                        )
                        textoParcial = ""
                        ultimoPintado = Date.distantPast
                    case .messageEnd(let messageId):
                        aplicarMessageEnd(messageId: messageId, anclaActual: anclaActual)
                        pintar(final: true)
                    case .toolEnd(_, _, _, let artifacts, let blocksVersion, let bloques, _):
                        agregarArtefactos(artifacts, a: anclaActual)
                        if blocksVersion == 1, let indice = items.firstIndex(where: { $0.id == anclaActual }) {
                            for bloque in bloques where !items[indice].bloques.contains(bloque) {
                                items[indice].bloques.append(bloque)
                            }
                        }
                    case .confirmationRequired(let toolCallId, let nombre, let args):
                        pintar(final: true)
                        confirmacionEnVivo = ConfirmacionBotPendiente(
                            toolCallId: toolCallId,
                            nombre: nombre,
                            args: args
                        )
                        return
                    case .done:
                        pintar(final: true)
                        if let indice = items.firstIndex(where: { $0.id == anclaActual }) {
                            items[indice].enProgreso = false
                        }
                    case .error:
                        if let indice = items.firstIndex(where: { $0.id == anclaActual }) {
                            items[indice].enProgreso = false
                            if items[indice].texto.isEmpty {
                                items[indice].texto = "No pude completar la acción confirmada."
                            }
                        }
                    default:
                        break
                    }
                }
                quitarBurbujaVacia(anclaActual)
                await cargar(fusionarSiEnviando: enviandoTurno)
                if let client = session.client {
                    await cargarAprobaciones(client: client)
                }
            } catch {
                self.error = error.localizedDescription
                confirmacionEnVivo = pendiente
            }
        }
    }

    private func decidirAprobacionDurable(_ aprobacion: PendingApproval, aprobar: Bool) async {
        guard let client = session.client, !ocupadoAprobacion else { return }
        ocupadoAprobacion = true
        defer { ocupadoAprobacion = false }
        do {
            if aprobar {
                try await client.approveApproval(id: aprobacion.id)
            } else {
                try await client.denyApproval(id: aprobacion.id)
            }
            aprobacionesExternas.removeAll { $0.id == aprobacion.id }
            aprobacionesPendientes.removeAll { $0.id == aprobacion.id }
            await cargar(fusionarSiEnviando: enviandoTurno)
            await cargarAprobaciones(client: client)
        } catch {
            self.error = aprobar
                ? "No pude aprobar el envío externo."
                : "No pude rechazar el envío externo."
        }
    }

    private func preguntasDe(_ item: ItemMensajeBot) -> [QuestionBlock] {
        item.bloques.compactMap { bloque in
            if case .question(let pregunta) = bloque { return pregunta }
            return nil
        }
    }

    /// Primera respuesta del dueño posterior al mensaje de la tarjeta
    /// (dismissOnMoveOn / allowCustom: escribir también cierra el widget).
    private func respuestaPosterior(para _: QuestionBlock, despuesDe mensajeId: String) -> String? {
        let hilo: [MensajeDelHilo] = items.map { item in
            MensajeDelHilo(
                id: item.id,
                rol: item.esUsuario ? .usuario : .asistente,
                texto: item.texto,
                fecha: nil,
                entregable: true
            )
        }
        return HiloDePreguntas.respuestasPosteriores(en: hilo)[mensajeId]
    }

    /// Envío inmediato desde widget / Needs you (sin pasar por el TextField).
    private func enviarTextoForzado(_ textoForzado: String) {
        let limpio = textoForzado.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !limpio.isEmpty, let client = session.client else { return }
        error = nil
        texto = ""
        items.append(ItemMensajeBot(id: UUID().uuidString, esUsuario: true, texto: limpio))
        cadenaEnvio = Task {
            await hacerEnvio(client: client, texto: limpio, adjuntos: [])
        }
    }

    private func cargarSugerenciasProactivas(client: APIClient) async {
        do {
            let todas = try await client.listAutomationSuggestions(workerId: bot.id)
            sugerenciasProactivas = todas.filter { item in
                let etapa = SuggestionStage(item.stage)
                return etapa == .action || etapa == .suggestion || etapa == .draft
                    || item.failureCount != nil
            }
        } catch {
            // Degradación silenciosa: el chat 1:1 sigue sin Needs you.
        }
    }

    /// Chip + preview Grok-style encima del composer.
    private func chipAdjuntoPendiente(_ adjunto: AdjuntoBot) -> some View {
        let esImagen = (adjunto.mime ?? "").lowercased().hasPrefix("image/")
        return HStack(spacing: 8) {
            if esImagen {
                ImagenAdjuntaBotMini(fileId: adjunto.fileId, client: session.client) {
                    abrirAdjunto(adjunto)
                }
            } else {
                Image(systemName: iconoDocumento(adjunto.mime, filename: adjunto.filename))
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(EdecanTheme.morado)
                    .frame(width: 36, height: 36)
                    .background(Color.secondary.opacity(0.12), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                    .onTapGesture { abrirAdjunto(adjunto) }
            }
            VStack(alignment: .leading, spacing: 1) {
                Text(adjunto.filename)
                    .font(.caption.weight(.medium))
                    .lineLimit(1)
                Text(nombreTipoDocumento(adjunto.mime, filename: adjunto.filename))
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Button {
                Haptico.ligero()
                withAnimation(.spring(response: 0.32, dampingFraction: 0.85)) {
                    adjuntosPendientes.removeAll { $0.fileId == adjunto.fileId }
                }
            } label: {
                Image(systemName: "xmark.circle.fill")
                    .foregroundStyle(.secondary)
            }
            .accessibilityLabel("Quitar \(adjunto.filename)")
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 7)
        .tarjetaVidrio(esquina: 14)
    }

    private func iniciarPollBeatsVivos() {
        detenerPollBeatsVivos()
        guard scenePhase == .active else { return }
        pollBeatsTask = Task { @MainActor in
            while !Task.isCancelled {
                let ms = enviandoTurno ? 1100 : 2200
                try? await Task.sleep(for: .milliseconds(ms))
                guard !Task.isCancelled else { return }
                if !enviandoTurno {
                    limpiarTypingZombie()
                }
                await refrescarBeatsSiQuiet(forzar: enviandoTurno)
            }
        }
    }

    private func detenerPollBeatsVivos() {
        pollBeatsTask?.cancel()
        pollBeatsTask = nil
    }

    private func iniciarPollNeedsYou() {
        detenerPollNeedsYou()
        guard scenePhase == .active else { return }
        pollNeedsYouTask = Task { @MainActor in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(8))
                guard !Task.isCancelled, let client = session.client else { return }
                await cargarSugerenciasProactivas(client: client)
            }
        }
    }

    private func detenerPollNeedsYou() {
        pollNeedsYouTask?.cancel()
        pollNeedsYouTask = nil
    }

    private func iniciarPollApprovals() {
        detenerPollApprovals()
        guard scenePhase == .active else { return }
        pollApprovalsTask = Task { @MainActor in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(10))
                guard !Task.isCancelled, let client = session.client else { return }
                await cargarAprobaciones(client: client)
            }
        }
    }

    private func detenerPollApprovals() {
        pollApprovalsTask?.cancel()
        pollApprovalsTask = nil
    }

    private func cargarAprobaciones(client: APIClient) async {
        do {
            let todas = try await client.listApprovals(workerId: bot.id)
            aprobacionesPendientes = todas
            aprobacionesExternas = todas.filter { aprobacion in
                guard let nombre = aprobacion.name else { return false }
                return Self.herramientasEnvioExterno.contains(nombre)
            }
        } catch {
            // Silencioso: el menú Community degrada sin badge.
        }
    }

    /// Evita fila «trabajando» / cara viva huérfana cuando el SSE ya cerró.
    private func limpiarTypingZombie() {
        guard !enviandoTurno else { return }
        for i in items.indices where items[i].enProgreso {
            items[i].enProgreso = false
        }
    }

    private func refrescarBeatsSiQuiet(forzar: Bool = false) async {
        await cargar(fusionarSiEnviando: enviandoTurno || forzar)
    }

    private func cargarCatalogoSiHaceFalta() async {
        guard catalogoModelos == nil, let client = session.client else { return }
        if let fresco = try? await client.modelosDeChat() {
            catalogoModelos = fresco
        }
    }

    private func pintarSnapshotLocalSiVacio() {
        // BOTS-09: si esta sesión invalidó el snapshot (clear/delete), no
        // repintar lo que el servidor ya borró.
        guard items.isEmpty, !snapshotInvalidado else { return }
        guard let snap = botSnapshotStore.load(workerId: bot.id) else { return }
        // BOTS-09: el servidor ya nos contó un epoch más reciente que el del
        // snapshot → es una respuesta tardía pre-clear, no repintar.
        if let epochSnap = snap.conversationEpoch,
           let epochActual = epochConversacion,
           epochSnap < epochActual {
            return
        }
        items = snap.messages.map { mensaje in
            let adjuntos = mensaje.adjuntos.map {
                AdjuntoBot(fileId: $0.fileId, filename: $0.filename ?? "archivo", mime: $0.mime)
            }
            return ItemMensajeBot(
                id: mensaje.id,
                esUsuario: mensaje.esUsuario,
                texto: mensaje.texto,
                nombreRemitente: mensaje.nombreRemitente,
                adjuntos: adjuntos,
                // BOTS-21: pintado desde snapshot, aún sin reconectar → los
                // adjuntos son solo referencias; la burbuja lo dice honestamente.
                adjuntosSoloReferencia: !adjuntos.isEmpty,
                recortado: mensaje.recortado
            )
        }
        cargando = false
    }

    private func cargar(fusionarSiEnviando: Bool = false) async {
        guard let client = session.client else {
            error = "No hay sesión activa."
            cargando = false
            return
        }
        pintarSnapshotLocalSiVacio()
        error = nil
        defer { cargando = false }
        do {
            let pagina = try await client.listWorkerMessagesPage(workerId: bot.id, limit: limiteHistorial)
            let historial = pagina.messages
            let fresco = historial.map { ItemMensajeBot.desde($0, nombreBot: bot.nombreVisible) }
            // F3: descartar una respuesta tardía. (a) si esta sesión invalidó
            // el snapshot (un `cargar()` en vuelo durante `/clear` trae mensajes
            // que el servidor ya borró) o (b) si el epoch del servidor es más
            // viejo que el último conocido (respuesta fuera de orden), no se
            // guarda el snapshot ni se asignan `items`.
            if snapshotInvalidado { return }
            if let epoch = pagina.conversationEpoch,
               let ultimo = epochConversacion,
               epoch < ultimo {
                return
            }
            // BOTS-09: recordar el epoch más reciente que confirmó el servidor.
            if let epoch = pagina.conversationEpoch {
                epochConversacion = max(epochConversacion ?? epoch, epoch)
            }
            hayMasHistorial = pagina.hasMore
            cursorHistorial = pagina.nextCursor
            botSnapshotStore.save(
                CachedBotThread(
                    workerId: bot.id,
                    messages: fresco.map {
                        CachedBotMessage(
                            id: $0.id,
                            esUsuario: $0.esUsuario,
                            texto: $0.texto,
                            nombreRemitente: $0.nombreRemitente,
                            adjuntos: $0.adjuntos.map {
                                CachedBotAttachment(fileId: $0.fileId, filename: $0.filename, mime: $0.mime)
                            }
                        )
                    },
                    conversationEpoch: pagina.conversationEpoch
                )
            )
            if fusionarSiEnviando && enviandoTurno {
                let ids = Set(items.map { $0.id })
                let nuevos = fresco.filter { !ids.contains($0.id) && !$0.enProgreso }
                if !nuevos.isEmpty {
                    if let idx = items.lastIndex(where: { $0.enProgreso }) {
                        items.insert(contentsOf: nuevos, at: idx)
                    } else {
                        items.append(contentsOf: nuevos)
                    }
                }
            } else if items.isEmpty || !enviandoTurno {
                // Historial reemplazado: las cards de delegación no se
                // persisten en el servidor y sus anclas ya no existen — sin
                // esto caerían desplazadas al final del hilo.
                items = fresco.map { item in
                    var copia = item
                    if copia.enProgreso { copia.enProgreso = false }
                    return copia
                }
                delegacionesIDE.removeAll()
                // Las misiones del hilo se RESTAURAN del almacenamiento local
                // (el trabajo no se pierde al cerrar la app): se reabre la
                // franja y se reanuda el polling de las activas.
                restaurarMisionesDelHilo()
            }
        } catch {
            if items.isEmpty {
                self.error = "No pude cargar lo que hablamos. Revisa que la Mac esté encendida y vuelve a entrar."
            }
        }
    }

    /// AUD-12a: carga mensajes anteriores usando el CURSOR del último lote
    /// (`before` = `cursorHistorial`), nunca ensanchando `limit`. Prependiendo
    /// solo los que faltan (dedupe por id). No toca el scroll: los nuevos van
    /// AL PRINCIPIO y la posición de lectura se conserva.
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
            let pagina = try await client.listWorkerMessagesPage(
                workerId: bot.id, limit: limiteHistorial, before: cursor
            )
            let fresco = pagina.messages.map { ItemMensajeBot.desde($0, nombreBot: bot.nombreVisible) }
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

    private func subirFotos(_ seleccion: [PhotosPickerItem]) async {
        guard let client = session.client else { return }
        for item in seleccion.prefix(4) {
            guard let data = try? await item.loadTransferable(type: Data.self) else { continue }
            await subirImagen(data, client: client)
        }
    }

    /// Sube una foto redimensionada y comprimida: una de 12 MP (>5 MB)
    /// rompía la visión del bot y el turno colgaba. Bajarla a ~1280 px y
    /// JPEG 0.85 la hace analizable al instante.
    private func subirImagen(_ data: Data, client: APIClient) async {
        guard let imagen = UIImage(data: data) else { return }
        let tam = imagen.size
        let maxLado: CGFloat = 1280
        let escala = min(1, maxLado / max(tam.width, tam.height))
        let sizeMenor = CGSize(width: tam.width * escala, height: tam.height * escala)
        let formato = UIGraphicsImageRenderer(size: sizeMenor)
        let comprimida = formato.image { _ in
            imagen.draw(in: CGRect(origin: .zero, size: sizeMenor))
        }
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString + ".jpg")
        guard let jpg = comprimida.jpegData(compressionQuality: 0.85),
              (try? jpg.write(to: url)) != nil
        else { return }
        defer { try? FileManager.default.removeItem(at: url) }
        do {
            let archivo = try await client.subirArchivo(
                desde: url,
                filename: "foto-\(Int(Date().timeIntervalSince1970)).jpg",
                mimeType: "image/jpeg"
            )
            adjuntosPendientes.append(
                AdjuntoBot(fileId: archivo.id, filename: archivo.filename, mime: "image/jpeg")
            )
        } catch {
            self.error = "No pude subir la imagen. Intenta de nuevo."
        }
    }

    /// Sube archivos de CUALQUIER tipo (pdf, md, txt, zip, etc.): van crudos
    /// con su nombre y mime; el bot los lee con `leer_archivo`. Las imágenes
    /// que vengan por acá reciben el mismo tratamiento que las fotos.
    private func subirArchivosGenericos(_ urls: [URL]) async {
        guard let client = session.client else { return }
        for url in urls.prefix(4) {
            let acceso = url.startAccessingSecurityScopedResource()
            defer { if acceso { url.stopAccessingSecurityScopedResource() } }
            let filename = url.lastPathComponent
            let mime = UTType(filenameExtension: url.pathExtension)?.preferredMIMEType
                ?? "application/octet-stream"
            if mime.hasPrefix("image/") {
                if let data = try? Data(contentsOf: url) {
                    await subirImagen(data, client: client)
                }
                continue
            }
            do {
                let archivo = try await client.subirArchivo(
                    desde: url, filename: filename, mimeType: mime
                )
                adjuntosPendientes.append(
                    AdjuntoBot(
                        fileId: archivo.id,
                        filename: archivo.filename,
                        mime: archivo.mime ?? mime
                    )
                )
            } catch {
                self.error = "No pude subir \(filename). Intenta de nuevo."
            }
        }
    }
}

/// Hoja flotante de vidrio (Liquid Glass, iOS 26) para elegir qué adjuntar
/// al bot: Fotos de la galería o Archivos de cualquier tipo. Las tarjetas
/// llevan tile degradado con brillo, sombra de color y animación de presión.
private struct HojaAdjuntarBot: View {
    let nombreBot: String
    let onFotos: () -> Void
    let onArchivos: () -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(spacing: 20) {
            Capsule()
                .fill(.white.opacity(0.30))
                .frame(width: 38, height: 5)
                .padding(.top, 12)

            VStack(spacing: 3) {
                Text("Adjuntar")
                    .font(.system(size: 22, weight: .bold, design: .rounded))
                Text(nombreBot)
                    .font(.system(size: 13, weight: .medium, design: .rounded))
                    .foregroundStyle(.secondary)
            }

            HStack(spacing: 14) {
                tarjeta(
                    icono: "photo.on.rectangle.angled",
                    gradiente: [EdecanTheme.morado, EdecanTheme.azul],
                    titulo: "Fotos",
                    detalle: "Imágenes de tu galería",
                    accion: onFotos
                )
                tarjeta(
                    icono: "shippingbox.fill",
                    gradiente: [EdecanTheme.azul, Color(red: 0.16, green: 0.72, blue: 0.75)],
                    titulo: "Archivos",
                    detalle: "PDF · Markdown · texto · zip",
                    accion: onArchivos
                )
            }
            .padding(.horizontal, 18)
            .padding(.bottom, 26)
        }
        .padding(.horizontal, 8)
        .tarjetaVidrio(esquina: 36)
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }

    private func tarjeta(
        icono: String,
        gradiente: [Color],
        titulo: String,
        detalle: String,
        accion: @escaping () -> Void
    ) -> some View {
        Button {
            Haptico.ligero()
            dismiss()
            // Dar tiempo a que la hoja baje antes de abrir el picker del
            // sistema encima.
            Task { @MainActor in
                try? await Task.sleep(for: .milliseconds(420))
                accion()
            }
        } label: {
            VStack(spacing: 12) {
                ZStack {
                    RoundedRectangle(cornerRadius: 20, style: .continuous)
                        .fill(LinearGradient(
                            colors: gradiente,
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        ))
                        .frame(width: 66, height: 66)
                        .shadow(color: gradiente[0].opacity(0.42), radius: 16, y: 7)
                    Image(systemName: icono)
                        .font(.system(size: 26, weight: .semibold))
                        .foregroundStyle(.white)
                }
                Text(titulo)
                    .font(.system(size: 16, weight: .semibold, design: .rounded))
                    .foregroundStyle(.primary)
                Text(detalle)
                    .font(.system(size: 11.5, weight: .medium, design: .rounded))
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .lineLimit(2)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 22)
            .tarjetaVidrio(esquina: 26, flotante: true)
        }
        .buttonStyle(TarjetaAdjuntoButtonStyle())
    }
}

/// Presión suave con muelle para las tarjetas de la hoja de adjuntar.
private struct TarjetaAdjuntoButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.965 : 1)
            .opacity(configuration.isPressed ? 0.94 : 1)
            .animation(.spring(response: 0.28, dampingFraction: 0.72), value: configuration.isPressed)
    }
}

/// Un adjunto dentro del chat de bot: imagen ya subida a /v1/files.
struct AdjuntoBot: Identifiable, Sendable {
    let fileId: String
    let filename: String
    let mime: String?

    var id: String { fileId }
}

/// Imagen autenticada dentro del chat: miniatura del servidor; tocar abre el
/// visor seguro con zoom.
/// Preview inline cuando el bot CREA código/md: snippet + CTA Ver completo.
private struct BotCodigoCreadoPreview: View {
    let adjunto: AdjuntoBot
    let client: APIClient?
    let etiquetaTipo: String
    let onAbrir: () -> Void

    @State private var snippet: String?
    @State private var fallo = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button(action: onAbrir) {
                HStack(spacing: 8) {
                    Image(systemName: "chevron.left.forwardslash.chevron.right")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(EdecanTheme.azul)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(adjunto.filename)
                            .font(.footnote.weight(.semibold))
                            .foregroundStyle(.primary)
                            .lineLimit(1)
                        Text(etiquetaTipo)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 6)
                    Text("Ver")
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(EdecanTheme.azul)
                }
            }
            .buttonStyle(.plain)

            if let snippet, !snippet.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    Text(snippet)
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(.primary.opacity(0.92))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .padding(10)
                .background(Color.black.opacity(0.05), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                .onTapGesture(perform: onAbrir)
            } else if fallo {
                Text("No pude cargar el preview — toca Ver.")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            } else {
                ProgressView()
                    .scaleEffect(0.8)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .frame(maxWidth: 300, alignment: .leading)
        .tarjetaVidrio(esquina: 14, tint: EdecanTheme.azul)
        .task(id: adjunto.fileId) { await cargarSnippet() }
    }

    private func cargarSnippet() async {
        guard let client else {
            fallo = true
            return
        }
        do {
            let art = ArtifactRef(
                fileId: adjunto.fileId,
                filename: adjunto.filename,
                mime: adjunto.mime
            )
            let down = try await client.descargarArtefacto(art)
            let texto = String(decoding: down.data.prefix(12_000), as: UTF8.self)
            let lineas = texto.split(separator: "\n", omittingEmptySubsequences: false)
            let recorte = lineas.prefix(18).joined(separator: "\n")
            if recorte.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                fallo = true
            } else {
                snippet = recorte
            }
        } catch {
            fallo = true
        }
    }
}

private struct ImagenAdjuntaBot: View {
    let fileId: String
    let client: APIClient?
    let alTocar: () -> Void
    @State private var imagen: UIImage?

    var body: some View {
        Group {
            if let imagen {
                Image(uiImage: imagen)
                    .resizable()
                    .scaledToFit()
                    .frame(width: 240, height: 200)
                    .frame(maxWidth: 280, maxHeight: 240)
                    .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            } else {
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(Color.secondary.opacity(0.12))
                    .frame(width: 180, height: 130)
                    .overlay(ProgressView())
            }
        }
        .onTapGesture { alTocar() }
        .task(id: fileId) {
            guard let client else { return }
            let descarga = try? await client.descargarMiniatura(
                ArtifactRef(fileId: fileId, filename: "imagen", mime: "image/jpeg"),
                maxPixeles: 900
            )
            if let data = descarga?.data {
                imagen = UIImage(data: data)
            }
        }
    }
}

private struct BotLinkEvidenceCard: View {
    let link: LinkPreviewBlock

    var body: some View {
        Button {
            if let url = URL(string: link.url) {
                UIApplication.shared.open(url)
            }
        } label: {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Image(systemName: "link")
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(EdecanTheme.azul)
                    Text(link.siteName?.isEmpty == false ? link.siteName! : "Fuente")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(EdecanTheme.azul)
                    Spacer(minLength: 0)
                }
                Text(link.title)
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(.primary)
                    .multilineTextAlignment(.leading)
                    .lineLimit(2)
                if let desc = link.description, !desc.isEmpty {
                    Text(desc)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .tarjetaVidrio(esquina: 12, tint: EdecanTheme.azul)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Abrir \(link.title)")
    }
}

/// Miniatura cuadrada para chips del composer.
private struct ImagenAdjuntaBotMini: View {
    let fileId: String
    let client: APIClient?
    let alTocar: () -> Void
    @State private var imagen: UIImage?

    var body: some View {
        Group {
            if let imagen {
                Image(uiImage: imagen)
                    .resizable()
                    .scaledToFill()
            } else {
                Color.secondary.opacity(0.12)
                    .overlay(ProgressView().controlSize(.mini))
            }
        }
        .frame(width: 36, height: 36)
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        .onTapGesture { alTocar() }
        .task(id: fileId) {
            guard let client else { return }
            let descarga = try? await client.descargarMiniatura(
                ArtifactRef(fileId: fileId, filename: "imagen", mime: "image/jpeg"),
                maxPixeles: 240
            )
            if let data = descarga?.data {
                imagen = UIImage(data: data)
            }
        }
    }
}

/// Una nota de narración entre bots, agrupada por el mismo interlocutor.
private struct NotaNarracion: Identifiable {
    let de: String
    let tipo: String   // "escribio" | "recibio"
    let cara: CaraSnapshot?
    var mensajes: [String]
    var ultimo: String
    var id: String { "\(de)-\(mensajes.count)-\(ultimo)" }
}

/// Fila del chat: un mensaje normal, una nota de narración entre bots o una
/// card de delegación al IDE.
private enum FilaChatBot: Identifiable {
    case mensaje(ItemMensajeBot)
    case narracion(NotaNarracion)
    case delegacion(ItemDelegacionIDE)
    var id: String {
        switch self {
        case .mensaje(let item): item.id
        case .narracion(let nota): nota.id
        case .delegacion(let delegacion): delegacion.id
        }
    }
}

/// Fase de una delegación al IDE: define el icono de la card (spinner o
/// checkmark) mientras `estado` lleva el texto que se muestra.
private enum FaseDelegacionIDE: Sendable {
    case creando
    case procesando
    case terminado
}

/// Card mini Liquid Glass que cuenta la delegación al IDE dentro del hilo.
/// El `id` es ESTABLE (el `toolCallId` del stream o un UUID creado UNA vez
/// en `toolStart`): nunca se regenera por frame, así SwiftUI conserva la
/// identidad de la fila mientras `estado` cambia en vivo.
private struct ItemDelegacionIDE: Identifiable, Sendable {
    let id: String
    /// Id del turno (burbuja de respuesta) al que pertenece: la card se
    /// intercala justo antes de esa respuesta en el hilo.
    let ancla: String
    var fase: FaseDelegacionIDE
    var estado: String
}

private struct HerramientaBotActiva: Identifiable, Sendable {
    let id: String
    let nombre: String
    let detalle: String
}

/// Fila «{Nombre} está trabajando» del chat 1:1: cara del bot ENCIENDIDA.
private struct FilaEstadoTrabajandoBot: View {
    let bot: PersistentWorker

    var body: some View {
        HStack(alignment: .center, spacing: 10) {
            GrokFaceAvatar(bot: bot, size: 30, showOnline: false, animado: true, activo: true)
            HStack(spacing: 0) {
                Text("\(bot.nombreVisible) está ")
                    .foregroundStyle(.secondary)
                Text("trabajando")
                    .foregroundStyle(.primary.opacity(0.88))
                    .fontWeight(.medium)
            }
            .font(.subheadline)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .capsulaVidrio()
        .accessibilityElement(children: .combine)
        .accessibilityLabel(EstadoTrabajandoChat.etiquetaAccesibilidad(nombreAgente: bot.nombreVisible))
    }
}

private struct EventoNarracion {
    let texto: String
    let de: String
    let goal: String
    let escribioA: Bool
    let cara: CaraSnapshot?
}

private struct ConfirmacionBotPendiente: Equatable {
    let toolCallId: String
    let nombre: String
    let args: [String: JSONValue]
}

private enum ConfirmacionBotMostrada {
    case enVivo(ConfirmacionBotPendiente)
    case durable(PendingApproval)
}

private struct ColaBorradorSocial: Identifiable {
    let id: String
    let bloque: SocialDraftBlock
    let mensajeId: String
}

private struct ItemMensajeBot: Identifiable {
    let id: String
    var esUsuario: Bool
    var texto: String
    var nombreRemitente: String?
    var enProgreso: Bool
    var adjuntos: [AdjuntoBot]
    var bloques: [ChatBlock]
    var esBeat: Bool
    var evento: EventoNarracion?
    /// BOTS-21: adjuntos pintados desde el snapshot local, aún sin reconectar.
    /// La burbuja muestra el aviso honesto en vez de aparentar el entregable.
    var adjuntosSoloReferencia: Bool
    /// BOTS-21: el texto se recortó al guardarse (entregable parcial).
    var recortado: Bool
    /// F5: turno matado por un reinicio del servidor. La burbuja muestra
    /// «Interrumpido — tócala para reenviar» y el toque reenvía con clave nueva.
    var interrumpido: Bool
    /// F5: texto/adjuntos originales del turno interrumpido, para el reenvío.
    var textoOriginal: String?
    var adjuntosOriginales: [AdjuntoBot]

    init(
        id: String,
        esUsuario: Bool,
        texto: String,
        nombreRemitente: String? = nil,
        enProgreso: Bool = false,
        adjuntos: [AdjuntoBot] = [],
        bloques: [ChatBlock] = [],
        esBeat: Bool = false,
        evento: EventoNarracion? = nil,
        adjuntosSoloReferencia: Bool = false,
        recortado: Bool = false,
        interrumpido: Bool = false,
        textoOriginal: String? = nil,
        adjuntosOriginales: [AdjuntoBot] = []
    ) {
        self.id = id
        self.esUsuario = esUsuario
        self.texto = texto
        self.nombreRemitente = nombreRemitente
        self.enProgreso = enProgreso
        self.adjuntos = adjuntos
        self.bloques = bloques
        self.esBeat = esBeat
        self.evento = evento
        self.adjuntosSoloReferencia = adjuntosSoloReferencia
        self.recortado = recortado
        self.interrumpido = interrumpido
        self.textoOriginal = textoOriginal
        self.adjuntosOriginales = adjuntosOriginales
    }

    static func desde(_ mensaje: TeamMessage, nombreBot: String) -> ItemMensajeBot {
        var evento: EventoNarracion?
        var adjuntos: [AdjuntoBot] = []
        if mensaje.kind == "evento", let ev = mensaje.evento, !ev.isEmpty {
            evento = EventoNarracion(
                texto: mensaje.text,
                de: mensaje.de ?? "",
                goal: mensaje.goal ?? "",
                escribioA: ev == "escribio_a",
                cara: mensaje.cara
            )
        }
        if let refs = mensaje.adjuntos {
            adjuntos = refs.map {
                AdjuntoBot(fileId: $0.fileId, filename: $0.filename ?? "archivo", mime: $0.mime)
            }
        }
        var vistos = Set(adjuntos.map(\.fileId))
        for art in mensaje.artefactosDesdeToolCalls where vistos.insert(art.fileId).inserted {
            adjuntos.append(
                AdjuntoBot(fileId: art.fileId, filename: art.filename, mime: art.mime)
            )
        }
        let bloques = mensaje.bloquesDesdeToolCalls
        return ItemMensajeBot(
            id: mensaje.id,
            esUsuario: mensaje.esDelDueno,
            texto: mensaje.text,
            nombreRemitente: mensaje.esDelDueno ? nil : (mensaje.senderName ?? nombreBot),
            enProgreso: false,
            adjuntos: adjuntos,
            bloques: bloques,
            esBeat: mensaje.kind == "aviso",
            evento: evento
        )
    }
}

/// Tarjeta viva de una misión delegada desde el chat (el trabajo de Astra):
/// pasos con estado, expandible como un mini-menú estilo OpenCode.
struct ItemMisionBot: Identifiable, Equatable {
    let missionId: String
    let ancla: String
    var expandida = false
    var detalle: MissionDetailOut?

    var id: String { missionId }

    var titulo: String {
        if let estado = detalle?.mission.status {
            switch estado {
            case "done": return "Astra terminó la misión"
            case "error", "cancelled": return "La misión no se completó"
            case "planning": return "Astra está planificando…"
            case "waiting_confirmation": return "Astra necesita tu aprobación"
            default: return "Astra está trabajando"
            }
        }
        return "Astra está trabajando"
    }
}

/// Hoja del selector de modelo del chat del bot: catálogo del servidor con
/// los nombres reales (Luna, Workers AI…), sin hardcodear nada.
private struct HojaSelectorModeloBot: View {
    let catalogo: ChatModelCatalog?
    let seleccionado: String?
    let esfuerzo: EsfuerzoChat?
    let onSelect: (String?, EsfuerzoChat?) -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section("Modelo para este chat del bot") {
                    Button {
                        onSelect(nil, esfuerzo)
                        dismiss()
                    } label: {
                        HStack {
                            Text("Automático (política de costos)")
                                .foregroundStyle(.primary)
                            Spacer()
                            if seleccionado == nil {
                                Image(systemName: "checkmark")
                                    .foregroundStyle(EdecanTheme.morado)
                            }
                        }
                    }
                    if let catalogo {
                        ForEach(catalogo.modelos, id: \.id) { modelo in
                            Button {
                                onSelect(modelo.id, esfuerzo)
                                dismiss()
                            } label: {
                                HStack {
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(modelo.nombre)
                                            .foregroundStyle(.primary)
                                        Text(modelo.descripcion)
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                    }
                                    Spacer()
                                    if seleccionado == modelo.id {
                                        Image(systemName: "checkmark")
                                            .foregroundStyle(EdecanTheme.morado)
                                    }
                                }
                            }
                        }
                    } else {
                        HStack(spacing: 10) {
                            ProgressView()
                            Text("Cargando modelos…")
                        }
                    }
                }
                Section("Esfuerzo") {
                    ForEach(catalogo?.esfuerzos ?? EsfuerzoChat.allCases) { nivel in
                        Button {
                            onSelect(seleccionado, nivel)
                        } label: {
                            HStack {
                                Text(nivel.nombreLegible)
                                    .foregroundStyle(.primary)
                                Spacer()
                                if esfuerzo == nivel {
                                    Image(systemName: "checkmark")
                                        .foregroundStyle(EdecanTheme.morado)
                                }
                            }
                        }
                    }
                }
            }
            .navigationTitle("Modelo del bot")
            .navigationBarTitleDisplayMode(.inline)
        }
    }
}

/// Robot dibujado en Swift (sin SF Symbols): cabecita redondeada con
/// antena, dos ojos vivos y boca de línea — el ícono del selector de modelo
/// del bot. Usa los colores de marca.
struct IconoRobotModelo: View {
    var body: some View {
        ZStack {
            // Antena
            Capsule()
                .fill(EdecanTheme.morado)
                .frame(width: 2, height: 6)
                .offset(y: -19)
            Circle()
                .fill(EdecanTheme.morado)
                .frame(width: 5, height: 5)
                .offset(y: -24)

            // Cabeza
            RoundedRectangle(cornerRadius: 9, style: .continuous)
                .stroke(
                    LinearGradient(
                        colors: [EdecanTheme.morado, EdecanTheme.azul],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    ),
                    lineWidth: 2
                )
                .frame(width: 26, height: 26)
                .background(
                    RoundedRectangle(cornerRadius: 9, style: .continuous)
                        .fill(EdecanTheme.morado.opacity(0.10))
                )

            // Ojos
            HStack(spacing: 6) {
                Circle()
                    .fill(EdecanTheme.morado)
                    .frame(width: 4.5, height: 4.5)
                Circle()
                    .fill(EdecanTheme.morado)
                    .frame(width: 4.5, height: 4.5)
            }
            .offset(y: -3)

            // Boca
            Capsule()
                .fill(EdecanTheme.morado.opacity(0.55))
                .frame(width: 10, height: 2)
                .offset(y: 7)
        }
    }
}

/// Layout compacto para chips SHA/main en el chat de bots.
private struct FlowLayoutChips: Layout {
    var spacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let maxWidth = proposal.width ?? .infinity
        var x: CGFloat = 0
        var y: CGFloat = 0
        var rowH: CGFloat = 0
        var height: CGFloat = 0
        var width: CGFloat = 0
        for sub in subviews {
            let size = sub.sizeThatFits(.unspecified)
            if x + size.width > maxWidth, x > 0 {
                y += rowH + spacing
                x = 0
                rowH = 0
            }
            rowH = max(rowH, size.height)
            width = max(width, x + size.width)
            height = max(height, y + size.height)
            x += size.width + spacing
        }
        return CGSize(width: width, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX
        var y = bounds.minY
        var rowH: CGFloat = 0
        for sub in subviews {
            let size = sub.sizeThatFits(.unspecified)
            if x + size.width > bounds.maxX, x > bounds.minX {
                y += rowH + spacing
                x = bounds.minX
                rowH = 0
            }
            sub.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
            rowH = max(rowH, size.height)
            x += size.width + spacing
        }
    }
}
