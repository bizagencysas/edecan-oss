import SwiftUI
import EdecanKit
import Photos
import PhotosUI
import UIKit
import UniformTypeIdentifiers

/// Superficie principal "Edecan" — lista de mensajes, input y envío hacia
/// `POST /v1/conversations/{id}/messages` (SSE) apendeando `text_delta` a
/// medida que llega, más un indicador mientras el agente usa una
/// herramienta (`tool_start`/`tool_end`). Lógica real en ``ChatViewModel``;
/// esta vista solo dibuja su estado.
struct ChatView: View {
    @Environment(SessionStore.self) private var session
    @Environment(TabRouter.self) private var tabRouter
    @Environment(\.scenePhase) private var scenePhase
    @State private var viewModel = ChatViewModel()
    @State private var textoActual = ""
    @State private var mostrandoVoz = false
    @State private var mostrandoHistorial = false
    @State private var mostrandoSelectorArchivos = false
    @State private var mostrandoSelectorFotos = false
    @State private var mostrandoCamara = false
    @State private var mostrandoSelectorModelo = false
    @State private var adjuntosPendientes: [AdjuntoPendiente] = []
    @State private var tareasSubida: [UUID: Task<Void, Never>] = [:]
    @State private var artefactoDescargandoId: String?
    @State private var archivoCompartible: ArchivoCompartible?
    @State private var previewTarget: SecurePreviewTarget?
    /// Enlaces que el usuario abre a mano: van al navegador de verdad
    /// (`NavegadorEnApp`), no al visor capado de artefactos.
    @State private var destinoNavegador: DestinoNavegador?
    /// Pulso sutil del botón Detener mientras Edecán está generando.
    @State private var pulsandoDetener = false
    @State private var steerMision = ""
    /// Monitoreo de red del dispositivo para el banner de conectividad.
    @State private var monitorRed = MonitorRed()
    /// Marca que el servidor resultó inalcanzable en el último intento de
    /// API (no confundir con "sin red del dispositivo": si no hay red local,
    /// el banner de "Sin conexión" ya cubre la causa). Se borra al volver a
    /// tener red o cuando `errorMensaje` se limpia.
    @State private var servidorInaccesible = false
    /// Mensaje del usuario que se está editando desde el menú contextual
    /// ("Editar"). Mientras no sea `nil`, el compositor viene precargado con
    /// su texto y se muestra un indicador discreto arriba del campo. Al
    /// enviar se limpia: editar NO reemplaza el historial, solo manda un
    /// mensaje nuevo con el texto corregido.
    @State private var editandoMensajeId: String?
    /// Mensaje ancla del hilo abierto desde "Responder en hilo".
    @State private var mensajeEnHilo: ChatViewModel.Mensaje?
    /// Datos fuente del autocompletado `@`/`/` del composer, cargados una vez.
    @State private var datosMenciones = DatosMenciones()
    private let estadoLocal = ChatLocalStateStore()
    private let anclaFinal = "chat-final"
    @FocusState private var campoEnfocado: Bool

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                listaDeMensajes
                    .overlay(alignment: .top) {
                        bannerEstadoRed
                            .animation(.easeInOut(duration: 0.25), value: monitorRed.estaConectado)
                            .animation(.easeInOut(duration: 0.25), value: servidorInaccesible)
                    }
                if let error = viewModel.errorMensaje {
                    Text(error)
                        .font(.footnote)
                        .foregroundStyle(.red)
                        .padding(.horizontal)
                        .padding(.top, 6)
                }
                barraDeEntrada
            }
            .background(EdecanTheme.degradado.opacity(0.05).ignoresSafeArea())
            .navigationTitle("")
            .navigationBarTitleDisplayMode(.inline)
            .sheet(isPresented: $mostrandoVoz) {
                VozView(chat: viewModel)
            }
            .sheet(isPresented: $mostrandoHistorial) {
                HistorialChatView(
                    viewModel: viewModel,
                    client: session.client,
                    onNueva: crearChatNuevo,
                    onSeleccionar: abrirConversacion
                )
            }
            .sheet(isPresented: $mostrandoSelectorModelo) {
                SelectorModeloView(viewModel: viewModel)
            }
            .sheet(item: $archivoCompartible) { archivo in
                HojaCompartir(items: [archivo.url]) {
                    limpiarArchivoTemporal(archivo.url)
                    archivoCompartible = nil
                }
            }
            .sheet(item: $destinoNavegador) { destino in
                NavegadorEnApp(url: destino.url)
                    .ignoresSafeArea()
            }
            .sheet(item: $previewTarget) { target in
                SecurePreviewSheet(target: target, client: session.client)
            }
            .sheet(item: $mensajeEnHilo) { mensaje in
                ThreadView(
                    messageId: mensaje.id,
                    resumen: mensaje.texto.isEmpty ? mensaje.textoApertura : mensaje.texto
                )
            }
            .sheet(isPresented: $mostrandoCamara) {
                CapturadorFotoChat { resultado in
                    mostrandoCamara = false
                    if case .success(let imagen) = resultado {
                        prepararFotoCapturada(imagen)
                    } else if case .failure(let error) = resultado {
                        viewModel.errorMensaje = "No se pudo tomar la foto: \(error.localizedDescription)"
                    }
                }
                .ignoresSafeArea()
            }
            .sheet(isPresented: $mostrandoSelectorFotos) {
                SelectorFotosModerno(
                    limite: max(1, 10 - adjuntosPendientes.count)
                ) { fotos in
                    mostrandoSelectorFotos = false
                    Task { await recibirFotos(fotos) }
                }
            }
            .fileImporter(
                isPresented: $mostrandoSelectorArchivos,
                allowedContentTypes: [.item],
                allowsMultipleSelection: true,
                onCompletion: recibirArchivos
            )
            .toolbar {
                ToolbarItem(placement: .principal) {
                    cabeceraDeConversacion
                }
                ToolbarItem(placement: .topBarLeading) {
                    Button {
                        campoEnfocado = false
                        mostrandoHistorial = true
                    } label: {
                        Image(systemName: "text.bubble.fill")
                    }
                    .accessibilityLabel("Conversaciones")
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        Task { await deshacerUltimaAccion() }
                    } label: {
                        Image(systemName: "arrow.uturn.backward")
                    }
                    .accessibilityLabel("Deshacer última acción")
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button(action: crearChatNuevo) {
                        Image(systemName: "square.and.pencil")
                    }
                    .disabled(viewModel.enviando || viewModel.confirmacionPendiente != nil)
                    .accessibilityLabel("Nuevo chat")
                }
            }
            .task {
                guard let client = session.client else { return }
                // Frente 5: en frío la pestaña aterriza en la conversación
                // PRINCIPAL persistente, no en un chat nuevo y vacío (ver
                // `ChatViewModel.iniciar`). El borrador que se recupera acá
                // es el del "chat nuevo" (clave "new") por si `iniciar`
                // termina dejando la vista sin conversación (sin red, backend
                // viejo); en cuanto `conversacionId` cambie, el
                // `.onChange` de abajo lo reemplaza por el borrador de esa
                // conversación. Un envío a medias sí se recupera aparte: eso
                // lo maneja `iniciar` con el intento pendiente, que no
                // depende de este borrador.
                if textoActual.isEmpty {
                    textoActual = cargarBorrador(conversationId: nil)
                }
                // Si un push con `chat_id` (frente 3) ya dejó lista una
                // conversación puntual en el router ANTES de que este `.task`
                // corra, se la pasamos derecho: `iniciar` ya sabe preferirla
                // sobre la principal (ver su propio `preferredId`). Evita la
                // carrera de abrir la principal para, un instante después,
                // pisarla con la del deeplink.
                await viewModel.iniciar(
                    client: client,
                    preferredConversationId: tabRouter.conversacionPendiente?.conversationId
                )
            }
            .onChange(of: viewModel.conversacionId) { anterior, nueva in
                guardarBorrador(textoActual, conversationId: anterior)
                estadoLocal.currentConversationId = nueva
                textoActual = cargarBorrador(conversationId: nueva)
                cancelarTodasLasSubidas()
                adjuntosPendientes = []
            }
            .onChange(of: textoActual) { _, nuevo in
                guardarBorrador(nuevo, conversationId: viewModel.conversacionId)
                cargarDatosMenciones()
            }
            .onChange(of: viewModel.errorMensaje) { _, nuevo in
                if nuevo != nil { Haptico.error() }
                // Una llamada al backend que falla mientras SÍ hay red del
                // dispositivo apunta al servidor, no al WiFi: sube el flag del
                // banner "No se puede conectar con Edecán". Si `errorMensaje`
                // se limpia (envío exitoso) o se corta la red local, el banner
                // de "Sin conexión" gana y este flag se resetea con él.
                servidorInaccesible = nuevo != nil
            }
            .onChange(of: monitorRed.estaConectado) { _, conectado in
                if conectado { servidorInaccesible = false }
            }
            .onChange(of: scenePhase, initial: true) { _, nuevaFase in
                let activa = nuevaFase == .active
                viewModel.actualizarEstadoAplicacion(activa: activa)
                guard activa, let client = session.client else { return }
                Task {
                    await viewModel.reanudarIntentoPendienteSiNecesario(client: client)
                    await viewModel.refrescarConversacionAbierta(client: client)
                }
            }
            .onChange(of: tabRouter.solicitudPendiente?.id) { _, nuevaId in
                guard nuevaId != nil, let solicitud = tabRouter.consumirSolicitud() else { return }
                // Las solicitudes rapidas preparan el texto; el usuario sigue
                // teniendo la ultima palabra antes de enviarlo.
                textoActual = solicitud.texto
                campoEnfocado = true
                if tabRouter.abrirVozPendiente {
                    tabRouter.abrirVozPendiente = false
                    mostrandoVoz = true
                }
            }
            .onChange(of: tabRouter.compartidoPendiente?.id) { _, nuevaId in
                guard nuevaId != nil, let compartido = tabRouter.consumirCompartido() else { return }
                if !compartido.texto.isEmpty { textoActual = compartido.texto }
                if !compartido.archivos.isEmpty {
                    recibirArchivos(.success(compartido.archivos))
                }
                campoEnfocado = true
            }
            .onChange(of: tabRouter.conversacionPendiente?.id) { _, nuevaId in
                // Frente 3 (deeplink): un push con `chat_id` deja la
                // conversacion pedida en `tabRouter.conversacionPendiente`.
                // La consumimos aca y la abrimos igual que `abrirConversacion(_:)`.
                guard nuevaId != nil,
                      let solicitada = tabRouter.consumirConversacionPendiente(),
                      let client = session.client else { return }
                Task { await viewModel.abrirConversacion(id: solicitada.conversationId, client: client) }
            }
            .onDisappear {
                cancelarTodasLasSubidas()
            }
        }
    }

    private var idUltimaRespuestaAsistente: String? {
        viewModel.mensajes.last(where: { $0.rol == .asistente })?.id
    }

    private var listaDeMensajes: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    if viewModel.cargandoConversacion {
                        ProgressView("Cargando conversacion…")
                            .frame(maxWidth: .infinity)
                            .padding(.top, 60)
                    } else if viewModel.mensajes.isEmpty {
                        EmptyStateView(
                            icono: "bubble.left.and.bubble.right.fill",
                            titulo: "Escríbele a Edecán",
                            descripcion: "Como un chat con alguien de confianza. También puede escribirte primero con avisos o resultados."
                        )
                        .padding(.top, 48)
                        .accessibilityIdentifier("chat-empty-state")
                        MissionInboxChips(onOpenActivity: { tabRouter.seleccion = .activity })
                    }
                    // Una sola pasada por el hilo para todas las burbujas: qué
                    // tarjeta de pregunta ya se contestó sale del hilo, no de
                    // un `@State` que SwiftUI descarta al reciclar la vista.
                    let respuestas = viewModel.respuestasAPreguntas
                    let idUltimaRespuesta = idUltimaRespuestaAsistente
                    ForEach(viewModel.mensajes) { mensaje in
                        burbujaDelHilo(
                            mensaje,
                            respuestaPosterior: respuestas[mensaje.id],
                            esUltimaRespuesta: mensaje.id == idUltimaRespuesta
                        )
                    }
                    if let mision = viewModel.misionViva {
                        TarjetaMisionEnHilo(
                            mision: mision,
                            steerMision: $steerMision,
                            onSteer: { instruction in
                                guard let client = session.client else { return }
                                Task {
                                    await viewModel.redirigirMisionViva(instruction: instruction, client: client)
                                    steerMision = ""
                                }
                            },
                            onOpenActivity: { tabRouter.seleccion = .activity }
                        )
                    }
                    if let confirmacion = viewModel.confirmacionPendiente {
                        TarjetaConfirmacion(
                            confirmacion: confirmacion,
                            deshabilitada: viewModel.enviando,
                            onVerComputadora: confirmacion.nombre == "usar_computadora"
                                ? { tabRouter.mostrarRemoto() }
                                : nil
                        ) { aprobado in
                            resolverConfirmacion(aprobado: aprobado)
                        }
                    }
                    Color.clear
                        .frame(height: 1)
                        .id(anclaFinal)
                }
                .padding()
            }
            .scrollDismissesKeyboard(.interactively)
            .contentShape(Rectangle())
            .onTapGesture {
                campoEnfocado = false
            }
            .onChange(of: viewModel.mensajes.last) { _, _ in
                desplazarAlFinal(proxy)
            }
            .onChange(of: viewModel.confirmacionPendiente?.id) { _, _ in
                desplazarAlFinal(proxy)
            }
            .onAppear { sincronizarLecturaYBadge() }
            .onChange(of: viewModel.mensajes.count) { _, _ in sincronizarLecturaYBadge() }
            .onChange(of: tabRouter.seleccion) { _, _ in sincronizarLecturaYBadge() }
            .onChange(of: scenePhase) { _, _ in sincronizarLecturaYBadge() }
        }
    }


    private func burbujaDelHilo(
        _ mensaje: ChatViewModel.Mensaje,
        respuestaPosterior: String?,
        esUltimaRespuesta: Bool
    ) -> some View {
        BurbujaMensaje(
            mensaje: mensaje,
            client: session.client,
            artefactoDescargandoId: artefactoDescargandoId,
            onAbrirArtefacto: { previewTarget = .artifact($0) },
            onAction: ejecutarAccion,
            onRetry: reintentarMensaje,
            onResponder: responderPregunta,
            respuestaPosterior: respuestaPosterior,
            esUltimaRespuesta: esUltimaRespuesta,
            onRegenerar: regenerarRespuesta,
            onEditar: empezarEdicion,
            onTogglePin: {
                Task {
                    guard let client = session.client else { return }
                    await viewModel.marcarBanderas(
                        mensajeId: mensaje.id,
                        pinned: !mensaje.pinned,
                        client: client
                    )
                }
            },
            onToggleBookmark: {
                Task {
                    guard let client = session.client else { return }
                    await viewModel.marcarBanderas(
                        mensajeId: mensaje.id,
                        bookmark: !mensaje.bookmark,
                        client: client
                    )
                }
            },
            onReply: { textoActual = "> \(mensaje.texto)\n\n" },
            onReaccionar: { emoji in
                guard let client = session.client else { return }
                Task {
                    await viewModel.alternarReaccion(
                        mensajeId: mensaje.id, emoji: emoji, client: client
                    )
                }
            },
            onResponderEnHilo: { mensajeEnHilo = mensaje },
            nombreAgente: viewModel.tituloConversacionActual
        )
        .id(mensaje.id)
    }

    /// Marca como leído cuando la persona está en el chat; si no, actualiza
    /// el badge de la pestaña Edecán con avisos del compañero.
    private func sincronizarLecturaYBadge() {
        guard let conversationId = viewModel.conversacionId else {
            tabRouter.mensajesNoLeidosEnChat = 0
            return
        }
        let lastRead = estadoLocal.lastReadAt(conversationId: conversationId)
        let noLeidos = viewModel.contarNoLeidos(lastReadAt: lastRead)
        let chatVisible = tabRouter.seleccion == .edecan && scenePhase == .active
        if chatVisible {
            if let ultimo = viewModel.instanteUltimoMensajeLeible() {
                estadoLocal.markRead(conversationId: conversationId, at: ultimo)
            }
            tabRouter.mensajesNoLeidosEnChat = 0
        } else {
            tabRouter.mensajesNoLeidosEnChat = noLeidos
        }
    }

    /// Banner discreto de estado de red en la parte superior del hilo. No
    /// bloquea la interacción — es solo un indicador visual que aparece y
    /// desaparece con animación. "Sin conexión" (ámbar) cuando el dispositivo
    /// no tiene red; "No se puede conectar con Edecán" (ámbar) cuando la hay
    /// pero el último intento de API falló.
    @ViewBuilder
    private var bannerEstadoRed: some View {
        let sinRed = !monitorRed.estaConectado
        let servidor = monitorRed.estaConectado && servidorInaccesible
        if sinRed || servidor {
            HStack(spacing: 8) {
                Image(systemName: sinRed ? "wifi.slash" : "exclamationmark.triangle.fill")
                    .font(.subheadline.weight(.semibold))
                Text(sinRed ? "Sin conexión" : "No se puede conectar con Edecán")
                    .font(.subheadline.weight(.semibold))
            }
            .foregroundStyle(Color(red: 0.40, green: 0.28, blue: 0.05))
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .background(
                Color(red: 1.0, green: 0.82, blue: 0.34).opacity(0.85),
                in: Capsule()
            )
            .padding(.top, 8)
            .transition(.move(edge: .top).combined(with: .opacity))
            .accessibilityElement(children: .combine)
            .accessibilityLabel(sinRed ? "Sin conexión" : "No se puede conectar con Edecán")
        }
    }

    @ViewBuilder
    private var cabeceraDeConversacion: some View {
        if let conversationId = viewModel.conversacionId {
            VStack(spacing: 2) {
                HStack(spacing: 6) {
                    Text(viewModel.tituloConversacionActual)
                        .font(.headline)
                        .lineLimit(1)
                    if tabRouter.mensajesNoLeidosEnChat > 0 {
                        Text("\(tabRouter.mensajesNoLeidosEnChat)")
                            .font(.caption2.weight(.bold))
                            .foregroundStyle(.white)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(EdecanTheme.morado, in: Capsule())
                            .accessibilityLabel("\(tabRouter.mensajesNoLeidosEnChat) mensajes nuevos")
                    }
                }
                HStack(spacing: 4) {
                    Circle()
                        .fill(Color.green)
                        .frame(width: 6, height: 6)
                    Text("En línea")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
            .accessibilityElement(children: .combine)
            .accessibilityLabel(viewModel.tituloConversacionActual)
        } else {
            Text("Nuevo chat")
                .font(.headline)
        }
    }

    private func desplazarAlFinal(_ proxy: ScrollViewProxy) {
        withAnimation(.easeOut(duration: 0.2)) {
            proxy.scrollTo(anclaFinal, anchor: .bottom)
        }
    }

    private var barraDeEntrada: some View {
        VStack(alignment: .leading, spacing: 8) {
            if editandoMensajeId != nil {
                indicadorEdicion
            }
            if !adjuntosPendientes.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(adjuntosPendientes) { adjunto in
                            fichaAdjunto(adjunto)
                        }
                    }
                    .padding(.horizontal, 2)
                }
            }

            if let token = tokenActivo, campoEnfocado {
                let sugerencias = AutocompletadoComposer.sugerencias(
                    prefijo: token.prefijo, query: token.query, datos: datosMenciones
                )
                if !sugerencias.isEmpty {
                    PanelMenciones(sugerencias: sugerencias) { elegida in
                        insertar(sugerencia: elegida)
                    }
                    .transition(.move(edge: .bottom).combined(with: .opacity))
                }
            }

            HStack(alignment: .bottom, spacing: 8) {
                Menu {
                    Section("Crear") {
                        botonPreset("Documento", icono: "doc.text", texto: "Crea un documento Word sobre ")
                        botonPreset("PDF", icono: "doc.richtext", texto: "Crea un PDF sobre ")
                        botonPreset("Presentación", icono: "rectangle.on.rectangle", texto: "Crea una presentación sobre ")
                        botonPreset("Sitio web", icono: "globe", texto: "Crea un sitio web para ")
                        botonPreset("App", icono: "apps.iphone", texto: "Crea una app para ")
                        botonPreset("Post", icono: "text.bubble", texto: "Crea un post sobre ")
                    }
                    Section("Adjuntar") {
                        Button {
                            Task { @MainActor in
                                await Task.yield()
                                mostrandoSelectorFotos = true
                            }
                        } label: {
                            Label("Elegir fotos", systemImage: "photo.on.rectangle")
                        }
                        Button {
                            guard UIImagePickerController.isSourceTypeAvailable(.camera) else {
                                viewModel.errorMensaje = "La cámara no está disponible en este dispositivo."
                                return
                            }
                            mostrandoCamara = true
                        } label: {
                            Label("Tomar foto", systemImage: "camera")
                        }
                        Button {
                            mostrandoSelectorArchivos = true
                        } label: {
                            Label("Subir archivo", systemImage: "paperclip")
                        }
                        Button {
                            tabRouter.mostrarRemoto()
                        } label: {
                            Label("Usar mi computadora", systemImage: "display")
                        }
                    }
                } label: {
                    Image(systemName: "plus")
                        .font(.system(size: 17, weight: .semibold))
                        .frame(width: 42, height: 42)
                        .foregroundStyle(EdecanTheme.morado)
                        .tarjetaVidrio(esquina: 18)
                }
                .disabled(viewModel.confirmacionPendiente != nil)
                .accessibilityLabel("Crear o adjuntar")

                TextField("Escríbele a Edecán…", text: $textoActual, axis: .vertical)
                    .lineLimit(1...5)
                    .focused($campoEnfocado)
                    .submitLabel(.send)
                    .onSubmit {
                        guard botonHabilitado else { return }
                        enviarMensajeActual()
                    }
                    .textFieldStyle(.plain)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .tarjetaVidrio(esquina: 18)

                Button {
                    viewModel.modoTrabajar.toggle()
                } label: {
                    Image(systemName: viewModel.modoTrabajar ? "briefcase.fill" : "briefcase")
                        .font(.system(size: 17, weight: .semibold))
                        .frame(width: 42, height: 42)
                        .foregroundStyle(viewModel.modoTrabajar ? EdecanTheme.morado : .secondary)
                        .tarjetaVidrio(esquina: 18)
                }
                .accessibilityLabel(viewModel.modoTrabajar ? "Modo trabajar activo" : "Modo trabajar")

                botonModeloIA

                if viewModel.estaGenerando {
                    Button {
                        viewModel.detenerGeneracion()
                    } label: {
                        Image(systemName: "stop.circle.fill")
                            .font(.system(size: 34))
                            .foregroundStyle(.red)
                            .scaleEffect(pulsandoDetener ? 1.12 : 1.0)
                    }
                    .accessibilityLabel("Detener generación")
                    .onAppear {
                        withAnimation(
                            .easeInOut(duration: 0.7)
                            .repeatForever(autoreverses: true)
                        ) {
                            pulsandoDetener = true
                        }
                    }
                    .onDisappear {
                        withAnimation(.easeOut(duration: 0.15)) {
                            pulsandoDetener = false
                        }
                    }
                }

                if hayContenidoParaEnviar {
                    Button(action: enviarMensajeActual) {
                        Image(systemName: "arrow.up.circle.fill")
                            .font(.system(size: 34))
                            .foregroundStyle(botonHabilitado ? AnyShapeStyle(EdecanTheme.degradado) : AnyShapeStyle(.tertiary))
                    }
                    .disabled(!botonHabilitado)
                    .accessibilityLabel("Enviar")
                } else if !viewModel.estaGenerando {
                    Button { mostrandoVoz = true } label: {
                        Image(systemName: "mic.fill")
                            .font(.system(size: 18, weight: .semibold))
                            .frame(width: 42, height: 42)
                            .foregroundStyle(EdecanTheme.morado)
                            .tarjetaVidrio(esquina: 18)
                    }
                    .disabled(viewModel.confirmacionPendiente != nil)
                    .accessibilityLabel("Hablar con Edecan")
                }
            }
        }
        .padding()
    }

    /// Botón de IA (un solo icono `sparkles` con el degradado de marca) que abre
    /// el selector de modelos. Va en la fila del composer, al lado del micrófono
    /// y del mismo tamaño (42×42), en vez de la pastilla que antes ocupaba una
    /// fila entera encima del campo — que se sentía incómoda y le comía altura.
    /// El modelo activo y su Esfuerzo ahora se ven DENTRO del selector, no aquí.
    @ViewBuilder
    private var botonModeloIA: some View {
        Button {
            campoEnfocado = false
            mostrandoSelectorModelo = true
        } label: {
            Image(systemName: "sparkles")
                .font(.system(size: 18, weight: .semibold))
                .frame(width: 42, height: 42)
                .foregroundStyle(EdecanTheme.degradado)
                .tarjetaVidrio(esquina: 18)
        }
        .disabled(viewModel.confirmacionPendiente != nil)
        .accessibilityLabel("Modelo de IA: \(viewModel.etiquetaPastilla)")
        .accessibilityHint("Toca para elegir otro modelo o su esfuerzo")
    }

    /// Indicador discreto que aparece sobre el composer mientras se edita un
    /// mensaje enviado ("Editar" del menú contextual). Una pastilla con el
    /// texto "Editando" y una X para cancelar: no toca colores ni diseño
    /// existentes, solo añade esta pista encima del campo.
    private var indicadorEdicion: some View {
        HStack(spacing: 7) {
            Image(systemName: "pencil")
                .font(.caption.weight(.semibold))
            Text("Editando mensaje")
                .font(.caption.weight(.semibold))
            Spacer(minLength: 0)
            Button {
                cancelarEdicion()
            } label: {
                Image(systemName: "xmark.circle.fill")
                    .font(.body)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Cancelar edición")
        }
        .foregroundStyle(.primary)
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(.quaternary.opacity(0.35), in: Capsule())
    }

    private func empezarEdicion(_ texto: String) {
        textoActual = texto
        editandoMensajeId = UUID().uuidString
        campoEnfocado = true
    }

    private func cancelarEdicion() {
        editandoMensajeId = nil
        textoActual = ""
        campoEnfocado = false
    }

    /// Token activo de autocompletado (`@` o `/`) al final del texto, si lo hay.
    private var tokenActivo: (prefijo: String, query: String)? {
        AutocompletadoComposer.tokenActivo(en: textoActual)
    }

    /// Reemplaza el segmento en curso con la mención/comando elegido.
    private func insertar(sugerencia: SugerenciaComposer) {
        textoActual.replaceSubrange(
            AutocompletadoComposer.rangoUltimoSegmento(en: textoActual),
            with: sugerencia.insercion
        )
        campoEnfocado = true
    }

    /// Carga de forma perezosa los datos del autocompletado (`@`/`/`).
    private func cargarDatosMenciones() {
        guard tokenActivo != nil, !datosMenciones.cargados,
              let client = session.client else { return }
        Task {
            var datos = DatosMenciones()
            datos.agentes = (try? await client.listWorkers()) ?? []
            datos.equipos = (try? await client.listTeams()) ?? []
            datos.workspaces = (try? await client.listWorkspaces()) ?? []
            datos.conectores = (try? await client.listMCPServers()) ?? []
            datos.skills = (try? await client.listSkills()) ?? []
            datos.cargados = true
            datosMenciones = datos
        }
    }

    private func regenerarRespuesta() {
        guard let client = session.client else {
            viewModel.errorMensaje = "No hay sesión activa."
            return
        }
        Haptico.ligero()
        viewModel.regenerarDesdeVista(client: client)
    }

    private var hayContenidoParaEnviar: Bool {
        !textoActual.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !adjuntosPendientes.isEmpty
    }

    private var botonHabilitado: Bool {
        (textoActual.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false
            || adjuntosListos.isEmpty == false)
            && adjuntosPendientes.allSatisfy(\.estaListo)
            && viewModel.confirmacionPendiente == nil
    }

    /// Manda la respuesta de un modal de pregunta (``QuestionBlock``) como un
    /// mensaje normal del usuario.
    ///
    /// No pasa por `textoActual` a propósito: si el usuario ya había empezado a
    /// escribir otra cosa en el campo, tocar una opción no debe pisarle ni
    /// mandarle ese borrador. Por eso tampoco lo limpia.
    private func responderPregunta(_ respuesta: String) {
        guard let client = session.client else {
            viewModel.errorMensaje = "No hay sesión activa."
            return
        }
        let texto = respuesta.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !texto.isEmpty else { return }
        viewModel.enviarDesdeVista(
            texto: texto,
            adjuntos: [],
            alAceptar: { campoEnfocado = false },
            client: client
        )
    }

    private func enviarMensajeActual() {
        guard let client = session.client else {
            viewModel.errorMensaje = "No hay sesión activa."
            return
        }
        let texto = textoActual
        let adjuntos = adjuntosListos
        Haptico.ligero()
        viewModel.enviarDesdeVista(
            texto: texto,
            adjuntos: adjuntos,
            alAceptar: {
                // Se limpia al crear la burbuja optimista, no al terminar
                // el stream. Un fallo posterior queda en esa burbuja con
                // Reintentar y no duplica la orden en el composer.
                textoActual = ""
                adjuntosPendientes = []
                editandoMensajeId = nil
                campoEnfocado = false
                guardarBorrador("", conversationId: viewModel.conversacionId)
            },
            client: client
        )
    }

    private var adjuntosListos: [ChatAttachment] {
        adjuntosPendientes.compactMap { adjunto in
            guard case .listo(let archivo) = adjunto.estado else { return nil }
            return ChatAttachment(fileId: archivo.id, filename: archivo.filename, mime: archivo.mime)
        }
    }

    private func resolverConfirmacion(aprobado: Bool) {
        guard let client = session.client else {
            viewModel.errorMensaje = "No hay sesión activa."
            return
        }
        if aprobado { Haptico.medio() } else { Haptico.advertencia() }
        viewModel.resolverConfirmacionDesdeVista(aprobado: aprobado, client: client)
    }

    /// Unica puerta de ejecucion para acciones venidas del servidor. Los
    /// mensajes solo rellenan el compositor, las URLs se revalidan como
    /// HTTP(S), y las pantallas pasan por un enum cerrado.
    private func ejecutarAccion(_ action: ChatAction) {
        switch action {
        case .openURL(_, _, let rawURL):
            guard let destino = DestinoNavegador(rawURL.absoluteString) else {
                viewModel.errorMensaje = "El enlace no es seguro y no se abrio."
                return
            }
            destinoNavegador = destino
        case .openScreen(_, _, let screen):
            switch screen {
            case .assistant:
                tabRouter.seleccion = .edecan
            case .create:
                tabRouter.seleccion = .edecan
                if textoActual.isEmpty { textoActual = "Crea " }
                campoEnfocado = true
            case .remote:
                tabRouter.mostrarRemoto()
            case .activity, .travel, .orders, .files:
                // Viajes, pedidos y archivos viven hoy dentro del trabajo
                // del asistente, no son rutas arbitrarias independientes.
                tabRouter.seleccion = .activity
            case .settings, .skills:
                tabRouter.seleccion = .settings
            }
        case .prefillMessage(_, _, let message):
            textoActual = String(message.prefix(2_000))
            tabRouter.seleccion = .edecan
            campoEnfocado = true
        case .copyText(_, _, let texto):
            // `CardGenericaView` ya maneja `copy_text` en el propio botón (igual
            // que `SocialDraftCardView.botonesLectura`); este caso queda para
            // que el switch sea exhaustivo si algún día otro bloque despacha
            // la misma acción hasta aquí.
            UIPasteboard.general.string = texto
        case .saveArtifact(_, _, let fileId):
            // Idem: la resolución rica (filename/mime desde los artifacts de
            // la MISMA card) vive en `CardGenericaView`. Esta ruta es solo la
            // red de respaldo si la acción llega hasta acá sin resolverse antes.
            guardarArtefactoPorId(fileId)
        case .sendMessage(_, _, let message):
            responderPregunta(message)
        case .approveDraft(_, _, let draftId):
            // Ya existe el endpoint que publica SOLO por `draft_id`
            // (`POST /v1/content/social/drafts/{draft_id}/publish`, respaldado por
            // la tabla `social_drafts`), así que el botón por fin hace lo que
            // promete. Antes mostraba un error porque `POST /v1/content/social/publish`
            // exigía `target`/`text`/`image_file_id` en mano, y una card llegada
            // por push solo trae la referencia.
            //
            // La app NO reenvía el texto: manda el id y el servidor decide qué
            // publica. Y la idempotencia vive del lado del servidor, no acá --
            // por eso un segundo toque es seguro aunque el primero se haya
            // quedado sin respuesta por señal mala.
            publicarBorrador(draftId)
        case .openConversation(_, _, let conversationId):
            abrirConversacion(conversationId)
        case .unsupported:
            break
        }
    }

    /// Publica el borrador que la card trae referenciado por `draft_id`.
    ///
    /// Es el "un toque y publicado": el servidor ya tiene el texto, la imagen y
    /// la cuenta de destino guardados en `social_drafts`, así que acá solo viaja
    /// el id. Deliberadamente NO se manda el texto de vuelta -- si el teléfono
    /// pudiera reenviarlo, lo que se publica dejaría de ser lo que se generó y
    /// se auditó.
    ///
    /// El resultado se cuenta en el chat en vez de en un cartel que se va solo:
    /// publicar es irreversible de cara al público, y merece quedar por escrito
    /// en el hilo. Un fallo también se dice con todas sus letras (por ejemplo
    /// "conecta tu cuenta"), nunca en silencio: quedarse callado tras tocar
    /// Publicar es peor que el error mismo, porque no se sabe si salió o no.
    private func publicarBorrador(_ draftId: String) {
        guard let client = session.client else {
            viewModel.errorMensaje = "Inicia sesión para publicar."
            return
        }
        Task {
            do {
                _ = try await ContentStudioService(client: client).publishDraft(draftId: draftId)
                viewModel.errorMensaje = nil
                // La confirmación la ESCRIBE el servidor en el hilo (igual que
                // hace con el borrador), así que acá solo se recarga en vez de
                // fabricar un mensaje local: si lo inventara la app, al recargar
                // saldría dos veces.
                if let conversacionId = viewModel.conversacionId {
                    await viewModel.abrirConversacion(id: conversacionId, client: client)
                }
            } catch is CancellationError {
                // Salir de la pantalla no es un fallo que valga la pena contar.
            } catch {
                // `APIClient.APIError` conforma `LocalizedError`, así que esto ya
                // trae el detalle del servidor (por ejemplo "conecta tu cuenta")
                // en vez de un código pelado.
                viewModel.errorMensaje = "No se pudo publicar: \(error.localizedDescription)"
            }
        }
    }

    /// Red de respaldo de `save_artifact` sin el `ArtifactRef` completo (sin
    /// filename/mime): el endpoint de descarga solo necesita el `fileId`, así
    /// que basta un valor mínimo para poder guardar la imagen en Fotos.
    private func guardarArtefactoPorId(_ fileId: String) {
        guard let client = session.client else {
            viewModel.errorMensaje = "Inicia sesión para guardar."
            return
        }
        Task {
            let artifact = ArtifactRef(fileId: fileId, filename: "archivo")
            guard let descargado = try? await client.descargarArtefacto(artifact) else {
                viewModel.errorMensaje = "No se pudo descargar el archivo."
                return
            }
            guard let imagen = UIImage(data: descargado.data) else {
                viewModel.errorMensaje = "El archivo no es una imagen válida."
                return
            }
            UIImageWriteToSavedPhotosAlbum(imagen, nil, nil, nil)
        }
    }

    @ViewBuilder
    private func botonPreset(_ titulo: String, icono: String, texto: String) -> some View {
        Button {
            textoActual = texto
            campoEnfocado = true
        } label: {
            Label(titulo, systemImage: icono)
        }
    }

    private func fichaAdjunto(_ adjunto: AdjuntoPendiente) -> some View {
        HStack(spacing: 7) {
            if adjunto.mimeType.lowercased().hasPrefix("image/") {
                MiniaturaAdjuntoLocal(
                    url: adjunto.localURL,
                    filename: adjunto.filename,
                    estado: adjunto.estado
                )
            } else {
                switch adjunto.estado {
                case .subiendo:
                    ProgressView().controlSize(.small)
                case .listo:
                    Image(systemName: "doc.fill").foregroundStyle(EdecanTheme.azul)
                case .fallido:
                    Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.red)
                }
            }
            Text(adjunto.filename)
                .font(.caption)
                .lineLimit(1)
            if case .fallido = adjunto.estado {
                Button {
                    reintentarSubida(adjunto)
                } label: {
                    Image(systemName: "arrow.clockwise.circle.fill")
                        .foregroundStyle(EdecanTheme.azul)
                }
                .accessibilityLabel("Reintentar subida de \(adjunto.filename)")
            }
            Button {
                quitarAdjunto(adjunto.id)
            } label: {
                Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary)
            }
            .accessibilityLabel("Quitar \(adjunto.filename)")
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 7)
        .tarjetaVidrio(esquina: 14)
    }

    private func recibirArchivos(_ resultado: Result<[URL], Error>) {
        guard let client = session.client else {
            viewModel.errorMensaje = "No hay sesión activa."
            return
        }
        do {
            let disponibles = max(0, 10 - adjuntosPendientes.count)
            let urls = Array(try resultado.get().prefix(disponibles))
            guard !urls.isEmpty else {
                if disponibles == 0 { viewModel.errorMensaje = "Puedes adjuntar hasta 10 archivos por mensaje." }
                return
            }
            for url in urls {
                let tieneAcceso = url.startAccessingSecurityScopedResource()
                defer { if tieneAcceso { url.stopAccessingSecurityScopedResource() } }
                do {
                    try FileUploadPolicy.validate(url)
                    let tipo = UTType(filenameExtension: url.pathExtension)
                    let pendiente = AdjuntoPendiente(
                        localURL: url,
                        filename: url.lastPathComponent,
                        mimeType: tipo?.preferredMIMEType ?? "application/octet-stream"
                    )
                    adjuntosPendientes.append(pendiente)
                    iniciarSubida(pendiente, client: client)
                } catch {
                    viewModel.errorMensaje = "No se puede adjuntar \(url.lastPathComponent): \(error.localizedDescription)"
                }
            }
        } catch {
            viewModel.errorMensaje = "No se pudieron seleccionar los archivos: \(error.localizedDescription)"
        }
    }

    @MainActor
    private func recibirFotos(_ fotos: [GaleriaFoto]) async {
        guard let client = session.client else {
            viewModel.errorMensaje = "No hay sesión activa."
            return
        }
        let disponibles = max(0, 10 - adjuntosPendientes.count)
        guard disponibles > 0 else {
            viewModel.errorMensaje = "Puedes adjuntar hasta 10 archivos por mensaje."
            return
        }
        for (indice, foto) in fotos.prefix(disponibles).enumerated() {
            do {
                let url = FileManager.default.temporaryDirectory
                    .appendingPathComponent("edecan-foto-\(UUID().uuidString).\(foto.extensionArchivo)")
                try foto.datos.write(to: url, options: .atomic)
                let pendiente = AdjuntoPendiente(
                    localURL: url,
                    filename: "foto-\(indice + 1).\(foto.extensionArchivo)",
                    mimeType: foto.mimeType
                )
                adjuntosPendientes.append(pendiente)
                iniciarSubida(pendiente, client: client)
            } catch {
                viewModel.errorMensaje = "No se pudo preparar una de las fotos: \(error.localizedDescription)"
            }
        }
    }

    @MainActor
    private func prepararFotoCapturada(_ imagen: UIImage) {
        guard let client = session.client else {
            viewModel.errorMensaje = "No hay sesión activa."
            return
        }
        guard adjuntosPendientes.count < 10 else {
            viewModel.errorMensaje = "Puedes adjuntar hasta 10 archivos por mensaje."
            return
        }
        guard let datos = imagen.jpegData(compressionQuality: 0.9) else {
            viewModel.errorMensaje = "No se pudo preparar la foto."
            return
        }
        do {
            let url = FileManager.default.temporaryDirectory
                .appendingPathComponent("edecan-camara-\(UUID().uuidString).jpg")
            try datos.write(to: url, options: .atomic)
            let pendiente = AdjuntoPendiente(
                localURL: url,
                filename: "foto-camara.jpg",
                mimeType: "image/jpeg"
            )
            adjuntosPendientes.append(pendiente)
            iniciarSubida(pendiente, client: client)
        } catch {
            viewModel.errorMensaje = "No se pudo preparar la foto: \(error.localizedDescription)"
        }
    }

    private func iniciarSubida(_ pendiente: AdjuntoPendiente, client: APIClient) {
        tareasSubida[pendiente.id]?.cancel()
        tareasSubida[pendiente.id] = Task {
            await subirArchivo(pendiente.localURL, pendiente: pendiente, client: client)
        }
    }

    private func subirArchivo(_ url: URL, pendiente: AdjuntoPendiente, client: APIClient) async {
        let tieneAcceso = url.startAccessingSecurityScopedResource()
        defer {
            if tieneAcceso { url.stopAccessingSecurityScopedResource() }
            tareasSubida[pendiente.id] = nil
        }
        do {
            let archivo = try await client.subirArchivo(
                desde: url,
                filename: pendiente.filename,
                mimeType: pendiente.mimeType
            )
            guard !Task.isCancelled else { return }
            guard let index = adjuntosPendientes.firstIndex(where: { $0.id == pendiente.id }) else { return }
            adjuntosPendientes[index].estado = .listo(archivo)
        } catch {
            guard !Task.isCancelled else { return }
            guard let index = adjuntosPendientes.firstIndex(where: { $0.id == pendiente.id }) else { return }
            adjuntosPendientes[index].estado = .fallido(error.localizedDescription)
            viewModel.errorMensaje = "No se pudo subir \(pendiente.filename): \(error.localizedDescription)"
        }
    }

    private func quitarAdjunto(_ id: UUID) {
        tareasSubida[id]?.cancel()
        tareasSubida[id] = nil
        adjuntosPendientes.removeAll { $0.id == id }
    }

    private func reintentarSubida(_ adjunto: AdjuntoPendiente) {
        guard let client = session.client,
              let index = adjuntosPendientes.firstIndex(where: { $0.id == adjunto.id })
        else { return }
        adjuntosPendientes[index].estado = .subiendo
        viewModel.errorMensaje = nil
        iniciarSubida(adjuntosPendientes[index], client: client)
    }

    private func cancelarTodasLasSubidas() {
        for task in tareasSubida.values { task.cancel() }
        tareasSubida.removeAll()
        for index in adjuntosPendientes.indices {
            if case .subiendo = adjuntosPendientes[index].estado {
                adjuntosPendientes[index].estado = .fallido("La subida se canceló.")
            }
        }
    }

    private func crearChatNuevo() {
        guardarBorrador(textoActual, conversationId: viewModel.conversacionId)
        viewModel.nuevaConversacion()
        textoActual = cargarBorrador(conversationId: nil)
        cancelarTodasLasSubidas()
        adjuntosPendientes = []
        mostrandoHistorial = false
        campoEnfocado = true
    }

    private func deshacerUltimaAccion() async {
        guard let client = session.client else { return }
        do {
            _ = try await client.undoLastAction()
            viewModel.errorMensaje = nil
        } catch {
            viewModel.errorMensaje = error.localizedDescription
        }
    }

    private func abrirConversacion(_ id: String) {
        guard let client = session.client else { return }
        mostrandoHistorial = false
        Task { await viewModel.abrirConversacion(id: id, client: client) }
    }

    private func reintentarMensaje(_ id: String) {
        guard let client = session.client else {
            viewModel.errorMensaje = "No hay sesión activa."
            return
        }
        viewModel.reintentarDesdeVista(mensajeId: id, client: client)
    }

    private func guardarBorrador(_ texto: String, conversationId: String?) {
        estadoLocal.saveDraft(texto, conversationId: conversationId)
    }

    private func cargarBorrador(conversationId: String?) -> String {
        estadoLocal.draft(conversationId: conversationId)
    }

    /// Descarga los bytes con Bearer mediante `APIClient`, los guarda solo
    /// en el directorio temporal privado de la app y abre la hoja nativa.
    /// Desde ahi la persona elige Guardar en Archivos, AirDrop u otra app.
    private func descargarYCompartir(_ artefacto: ArtifactRef) {
        guard let client = session.client, artefactoDescargandoId == nil else {
            if session.client == nil { viewModel.errorMensaje = "No hay sesión activa." }
            return
        }
        artefactoDescargandoId = artefacto.fileId
        viewModel.errorMensaje = nil
        Task {
            defer { artefactoDescargandoId = nil }
            do {
                let descarga = try await client.descargarArtefacto(artefacto)
                let url = try await Task.detached(priority: .userInitiated) {
                    try guardarArtefactoTemporal(descarga)
                }.value
                archivoCompartible = ArchivoCompartible(url: url)
            } catch {
                viewModel.errorMensaje = "No se pudo descargar \(artefacto.filename): \(error.localizedDescription)"
            }
        }
    }

}

struct GaleriaFoto: Sendable {
    let datos: Data
    let extensionArchivo: String
    let mimeType: String
}

/// Selector de fotos moderno basado en `PHPickerViewController` (PhotosUI).
/// A diferencia de `GaleriaEdecanPicker`, no exige permiso para toda la
/// biblioteca: PHPicker es privacy-first y solo entrega los elementos que la
/// persona confirma. La conversión HEIC→JPEG se mantiene para que el backend
/// siempre reciba un formato que el modelo puede ver.
struct SelectorFotosModerno: UIViewControllerRepresentable {
    let limite: Int
    let onConfirmar: ([GaleriaFoto]) -> Void

    func makeUIViewController(context: Context) -> SelectorFotosHostController {
        let host = SelectorFotosHostController()
        host.limite = limite
        host.onConfirmar = onConfirmar
        return host
    }

    func updateUIViewController(_ uiViewController: SelectorFotosHostController, context: Context) {}
}

/// Contenedor que incrusta el `PHPickerViewController` como hijo y muestra un
/// indicador de carga mientras se convierten las fotos a JPEG antes de cerrar.
final class SelectorFotosHostController: UIViewController, PHPickerViewControllerDelegate {
    var limite: Int = 1
    var onConfirmar: (([GaleriaFoto]) -> Void)?

    private let capaCarga = UIView()
    private let indicador = UIActivityIndicatorView(style: .large)
    private let etiqueta = UILabel()

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemBackground
        incrustarPicker()
        prepararCapaCarga()
    }

    private func incrustarPicker() {
        var config = PHPickerConfiguration()
        config.filter = .images
        config.selectionLimit = max(1, limite)
        config.preferredAssetRepresentationMode = .compatible
        let picker = PHPickerViewController(configuration: config)
        picker.delegate = self
        addChild(picker)
        picker.view.frame = view.bounds
        picker.view.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        view.addSubview(picker.view)
        picker.didMove(toParent: self)
    }

    private func prepararCapaCarga() {
        capaCarga.translatesAutoresizingMaskIntoConstraints = false
        capaCarga.backgroundColor = UIColor.black.withAlphaComponent(0.35)
        capaCarga.alpha = 0
        view.addSubview(capaCarga)

        let contenedor = UIView()
        contenedor.translatesAutoresizingMaskIntoConstraints = false
        contenedor.backgroundColor = UIColor.systemBackground.withAlphaComponent(0.92)
        contenedor.layer.cornerRadius = 18
        contenedor.clipsToBounds = true
        capaCarga.addSubview(contenedor)

        indicador.translatesAutoresizingMaskIntoConstraints = false
        indicador.startAnimating()
        etiqueta.translatesAutoresizingMaskIntoConstraints = false
        etiqueta.text = "Preparando fotos…"
        etiqueta.font = .systemFont(ofSize: 14, weight: .medium)
        etiqueta.textColor = .label
        etiqueta.textAlignment = .center

        let pila = UIStackView(arrangedSubviews: [indicador, etiqueta])
        pila.translatesAutoresizingMaskIntoConstraints = false
        pila.axis = .horizontal
        pila.spacing = 12
        pila.alignment = .center
        contenedor.addSubview(pila)

        NSLayoutConstraint.activate([
            capaCarga.topAnchor.constraint(equalTo: view.topAnchor),
            capaCarga.bottomAnchor.constraint(equalTo: view.bottomAnchor),
            capaCarga.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            capaCarga.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            contenedor.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            contenedor.centerYAnchor.constraint(equalTo: view.centerYAnchor),
            pila.topAnchor.constraint(equalTo: contenedor.topAnchor, constant: 16),
            pila.bottomAnchor.constraint(equalTo: contenedor.bottomAnchor, constant: -16),
            pila.leadingAnchor.constraint(equalTo: contenedor.leadingAnchor, constant: 20),
            pila.trailingAnchor.constraint(equalTo: contenedor.trailingAnchor, constant: -20)
        ])
    }

    private func mostrarCarga(_ mostrar: Bool) {
        UIView.animate(withDuration: 0.2) {
            self.capaCarga.alpha = mostrar ? 1 : 0
        }
    }

    // MARK: - PHPickerViewControllerDelegate

    func picker(_ picker: PHPickerViewController, didFinishPicking results: [PHPickerResult]) {
        if results.isEmpty {
            // Cancelar: devolvemos vacío para que el sheet se cierre.
            onConfirmar?([])
            return
        }
        mostrarCarga(true)
        let confirmar = onConfirmar
        Task { @MainActor in
            let fotos = await Self.convertir(results)
            confirmar?(fotos)
        }
    }

    private static func convertir(_ results: [PHPickerResult]) async -> [GaleriaFoto] {
        var fotos: [GaleriaFoto] = []
        for resultado in results {
            if let foto = await convertir(resultado) {
                fotos.append(foto)
            }
        }
        return fotos
    }

    private static func convertir(_ resultado: PHPickerResult) async -> GaleriaFoto? {
        await withCheckedContinuation { continuation in
            resultado.itemProvider.loadObject(ofClass: UIImage.self) { objeto, _ in
                guard let imagen = objeto as? UIImage else {
                    continuation.resume(returning: nil)
                    return
                }
                // HEIC no lo procesa ni Workers AI ni el pipeline de visión
                // directo del backend (solo JPEG/PNG/GIF/WEBP). Convertimos a
                // JPEG aquí para que las fotos de la galería siempre lleguen
                // en un formato que el modelo puede ver.
                guard let jpeg = imagen.jpegData(compressionQuality: 0.9) else {
                    continuation.resume(returning: nil)
                    return
                }
                continuation.resume(returning: GaleriaFoto(
                    datos: jpeg,
                    extensionArchivo: "jpg",
                    mimeType: "image/jpeg"
                ))
            }
        }
    }
}

/// Galería visual propia de Edecán. Mantiene la selección y la lectura de
/// originales dentro del dispositivo; el chat solo recibe los elementos que
/// la persona confirma explícitamente.
struct GaleriaEdecanPicker: View {
    @Environment(\.dismiss) private var dismiss
    @State private var autorizacion = PHPhotoLibrary.authorizationStatus(for: .readWrite)
    @State private var recursos: [PHAsset] = []
    @State private var seleccion: [String] = []
    @State private var cargando = false
    @State private var error: String?

    let limite: Int
    let onConfirmar: ([GaleriaFoto]) -> Void

    private let columnas = [
        GridItem(.flexible(), spacing: 10),
        GridItem(.flexible(), spacing: 10)
    ]

    var body: some View {
        NavigationStack {
            ZStack {
                Color(uiColor: .systemBackground).ignoresSafeArea()

                switch autorizacion {
                case .authorized, .limited:
                    galeria
                case .notDetermined:
                    ProgressView("Preparando tus fotos…")
                case .denied, .restricted:
                    accesoDenegado
                @unknown default:
                    accesoDenegado
                }
            }
            .safeAreaInset(edge: .top, spacing: 0) {
                cabecera
            }
            .safeAreaInset(edge: .bottom, spacing: 0) {
                if autorizacion == .authorized || autorizacion == .limited {
                    barraConfirmacion
                }
            }
            .toolbar(.hidden, for: .navigationBar)
            .task {
                await solicitarAccesoSiHaceFalta()
            }
        }
        .interactiveDismissDisabled(cargando)
    }

    private var cabecera: some View {
        HStack(spacing: 14) {
            Button {
                dismiss()
            } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 15, weight: .bold))
                    .frame(width: 38, height: 38)
                    .background(.ultraThinMaterial, in: Circle())
            }
            .disabled(cargando)
            .accessibilityLabel("Cerrar galería")

            VStack(alignment: .leading, spacing: 2) {
                Text("Tus momentos")
                    .font(.title3.bold())
                Text(seleccion.isEmpty ? "Elige lo que quieras compartir" : "\(seleccion.count) de \(limite) seleccionadas")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Spacer()

            Image(systemName: "sparkles")
                .font(.system(size: 18, weight: .semibold))
                .foregroundStyle(EdecanTheme.degradado)
                .frame(width: 38, height: 38)
                .background(EdecanTheme.degradado.opacity(0.12), in: Circle())
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(.ultraThinMaterial)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(.separator.opacity(0.35))
                .frame(height: 0.5)
        }
    }

    private var galeria: some View {
        ScrollView {
            if recursos.isEmpty {
                ContentUnavailableView(
                    "No hay fotos disponibles",
                    systemImage: "photo.on.rectangle.angled",
                    description: Text("Cuando tengas fotos en este iPhone aparecerán aquí.")
                )
                .padding(.top, 100)
            } else {
                LazyVGrid(columns: columnas, spacing: 10) {
                    ForEach(recursos, id: \.localIdentifier) { recurso in
                        celda(recurso)
                    }
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 12)
            }
        }
        .scrollIndicators(.hidden)
    }

    private func celda(_ recurso: PHAsset) -> some View {
        let posicion = seleccion.firstIndex(of: recurso.localIdentifier).map { $0 + 1 }
        return Button {
            alternar(recurso.localIdentifier)
        } label: {
            MiniaturaGaleria(asset: recurso)
                .aspectRatio(0.82, contentMode: .fit)
                .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
                .overlay {
                    if posicion != nil {
                        EdecanTheme.degradado.opacity(0.16)
                    }
                }
                .overlay(alignment: .topTrailing) {
                    ZStack {
                        Circle()
                            .fill(posicion == nil ? .black.opacity(0.28) : EdecanTheme.morado)
                            .stroke(.white, lineWidth: 2)
                        if let posicion {
                            Text("\(posicion)")
                                .font(.caption.bold())
                                .foregroundStyle(.white)
                        }
                    }
                    .frame(width: 28, height: 28)
                    .padding(8)
                }
                .overlay {
                    RoundedRectangle(cornerRadius: 20, style: .continuous)
                        .stroke(
                            posicion == nil
                                ? AnyShapeStyle(.white.opacity(0.18))
                                : AnyShapeStyle(EdecanTheme.degradado),
                            lineWidth: posicion == nil ? 1 : 3
                        )
                }
                .shadow(color: .black.opacity(0.12), radius: 12, y: 5)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(posicion == nil ? "Seleccionar foto" : "Foto seleccionada número \(posicion!)")
    }

    private var barraConfirmacion: some View {
        VStack(spacing: 9) {
            if let error {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .multilineTextAlignment(.center)
            }

            Button {
                confirmar()
            } label: {
                HStack(spacing: 10) {
                    if cargando {
                        ProgressView()
                            .tint(.white)
                    } else {
                        Image(systemName: seleccion.count == 1 ? "photo.fill" : "photo.stack.fill")
                    }
                    Text(textoConfirmacion)
                        .font(.headline)
                }
                .foregroundStyle(.white)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 15)
                .background(EdecanTheme.degradado, in: Capsule())
                .shadow(color: EdecanTheme.morado.opacity(0.3), radius: 18, y: 8)
            }
            .disabled(seleccion.isEmpty || cargando)
            .opacity(seleccion.isEmpty ? 0.45 : 1)
        }
        .padding(.horizontal, 18)
        .padding(.top, 12)
        .padding(.bottom, 8)
        .background(.ultraThinMaterial)
    }

    private var textoConfirmacion: String {
        if cargando { return "Preparando fotos…" }
        if seleccion.isEmpty { return "Selecciona tus fotos" }
        if seleccion.count == 1 { return "Adjuntar una foto" }
        return "Adjuntar \(seleccion.count) fotos"
    }

    private var accesoDenegado: some View {
        VStack(spacing: 18) {
            ZStack {
                Circle()
                    .fill(EdecanTheme.degradado.opacity(0.13))
                    .frame(width: 92, height: 92)
                Image(systemName: "photo.badge.exclamationmark")
                    .font(.system(size: 36, weight: .medium))
                    .foregroundStyle(EdecanTheme.degradado)
            }
            Text("Tus fotos siguen siendo tuyas")
                .font(.title2.bold())
            Text("Permite el acceso para elegir imágenes. Edecán solo prepara las que selecciones y nunca revisa el resto.")
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
                .padding(.horizontal, 28)
            Button("Abrir configuración") {
                guard let url = URL(string: UIApplication.openSettingsURLString) else { return }
                UIApplication.shared.open(url)
            }
            .buttonStyle(.borderedProminent)
            .tint(EdecanTheme.morado)
        }
        .padding()
    }

    @MainActor
    private func solicitarAccesoSiHaceFalta() async {
        if autorizacion == .notDetermined {
            autorizacion = await PHPhotoLibrary.requestAuthorization(for: .readWrite)
        }
        if autorizacion == .authorized || autorizacion == .limited {
            cargarRecursos()
        }
    }

    @MainActor
    private func cargarRecursos() {
        let opciones = PHFetchOptions()
        opciones.sortDescriptors = [NSSortDescriptor(key: "creationDate", ascending: false)]
        opciones.fetchLimit = 1_000
        let resultado = PHAsset.fetchAssets(with: .image, options: opciones)
        var nuevos: [PHAsset] = []
        nuevos.reserveCapacity(resultado.count)
        resultado.enumerateObjects { recurso, _, _ in
            nuevos.append(recurso)
        }
        recursos = nuevos
    }

    private func alternar(_ id: String) {
        error = nil
        if let indice = seleccion.firstIndex(of: id) {
            seleccion.remove(at: indice)
        } else if seleccion.count < limite {
            seleccion.append(id)
            Haptico.ligero()
        } else {
            error = "Puedes elegir hasta \(limite) fotos para este mensaje."
            Haptico.advertencia()
        }
    }

    private func confirmar() {
        guard !seleccion.isEmpty else { return }
        cargando = true
        error = nil
        let ids = seleccion
        Task {
            do {
                let fotos = try await cargarOriginales(ids)
                await MainActor.run {
                    cargando = false
                    onConfirmar(fotos)
                }
            } catch {
                await MainActor.run {
                    cargando = false
                    self.error = "No pudimos preparar una foto. Inténtalo de nuevo."
                }
            }
        }
    }

    private func cargarOriginales(_ ids: [String]) async throws -> [GaleriaFoto] {
        let resultado = PHAsset.fetchAssets(withLocalIdentifiers: ids, options: nil)
        var porId: [String: PHAsset] = [:]
        resultado.enumerateObjects { recurso, _, _ in
            porId[recurso.localIdentifier] = recurso
        }

        var fotos: [GaleriaFoto] = []
        for id in ids {
            guard let recurso = porId[id] else { continue }
            fotos.append(try await Self.datosOriginales(de: recurso))
        }
        return fotos
    }

    private static func datosOriginales(de recurso: PHAsset) async throws -> GaleriaFoto {
        try await withCheckedThrowingContinuation { continuation in
            let opciones = PHImageRequestOptions()
            opciones.isNetworkAccessAllowed = true
            opciones.deliveryMode = .highQualityFormat
            opciones.version = .current
            PHImageManager.default().requestImageDataAndOrientation(
                for: recurso,
                options: opciones
            ) { datos, identificadorTipo, _, info in
                if let error = info?[PHImageErrorKey] as? Error {
                    continuation.resume(throwing: error)
                    return
                }
                guard let datos else {
                    continuation.resume(throwing: CocoaError(.fileReadUnknown))
                    return
                }
                let tipo = identificadorTipo.flatMap(UTType.init) ?? .jpeg
                // HEIC no lo procesa ni Workers AI ni el pipeline de visión
                // directa del backend (solo JPEG/PNG/GIF/WEBP). Convertimos a
                // JPEG aquí para que las fotos de la galería siempre lleguen
                // en un formato que el modelo puede ver.
                if tipo == .heic || tipo.preferredMIMEType == "image/heic",
                   let imagen = UIImage(data: datos),
                   let jpeg = imagen.jpegData(compressionQuality: 0.9) {
                    continuation.resume(returning: GaleriaFoto(
                        datos: jpeg,
                        extensionArchivo: "jpg",
                        mimeType: "image/jpeg"
                    ))
                } else {
                    continuation.resume(returning: GaleriaFoto(
                        datos: datos,
                        extensionArchivo: tipo.preferredFilenameExtension ?? "jpg",
                        mimeType: tipo.preferredMIMEType ?? "image/jpeg"
                    ))
                }
            }
        }
    }
}

private struct MiniaturaGaleria: View {
    @Environment(\.displayScale) private var displayScale
    let asset: PHAsset
    @State private var imagen: UIImage?
    @State private var solicitud: PHImageRequestID?

    var body: some View {
        GeometryReader { proxy in
            ZStack {
                Rectangle()
                    .fill(Color(uiColor: .secondarySystemBackground))
                if let imagen {
                    // El fondo ocupa la tarjeta sin dejar bandas duras, pero
                    // la imagen principal siempre se muestra completa.
                    Image(uiImage: imagen)
                        .resizable()
                        .scaledToFill()
                        .frame(width: proxy.size.width, height: proxy.size.height)
                        .blur(radius: 20)
                        .scaleEffect(1.15)
                        .opacity(0.48)
                        .clipped()

                    Image(uiImage: imagen)
                        .resizable()
                        .scaledToFit()
                        .frame(
                            width: proxy.size.width - 10,
                            height: proxy.size.height - 10
                        )
                        .shadow(color: .black.opacity(0.18), radius: 8, y: 3)
                        .transition(.opacity)
                } else {
                    ProgressView()
                        .controlSize(.small)
                }
            }
        }
        .task(id: asset.localIdentifier) {
            cargar()
        }
        .onDisappear {
            if let solicitud {
                PHImageManager.default().cancelImageRequest(solicitud)
            }
        }
    }

    private func cargar() {
        // 180 puntos cubren incluso celdas amplias de iPad sin leer el
        // original completo. displayScale evita depender de UIScreen.main.
        let lado = 180 * displayScale
        let opciones = PHImageRequestOptions()
        opciones.deliveryMode = .opportunistic
        opciones.resizeMode = .fast
        opciones.isNetworkAccessAllowed = true
        solicitud = PHCachingImageManager.default().requestImage(
            for: asset,
            targetSize: CGSize(width: lado, height: lado),
            contentMode: .aspectFill,
            options: opciones
        ) { resultado, _ in
            guard let resultado else { return }
            withAnimation(.easeOut(duration: 0.18)) {
                imagen = resultado
            }
        }
    }
}

private struct AdjuntoPendiente: Identifiable, Equatable {
    enum Estado: Equatable {
        case subiendo
        case listo(UploadedFile)
        case fallido(String)
    }

    let id = UUID()
    let localURL: URL
    let filename: String
    let mimeType: String
    var estado: Estado = .subiendo

    var estaListo: Bool {
        if case .listo = estado { return true }
        return false
    }
}

private struct MiniaturaAdjuntoLocal: View {
    let url: URL
    let filename: String
    let estado: AdjuntoPendiente.Estado
    @State private var image: UIImage?

    var body: some View {
        ZStack {
            if let image {
                Image(uiImage: image)
                    .resizable()
                    .scaledToFill()
            } else {
                Color.secondary.opacity(0.10)
                Image(systemName: "photo")
                    .foregroundStyle(.secondary)
            }

            switch estado {
            case .subiendo:
                Color.black.opacity(0.22)
                ProgressView()
                    .controlSize(.small)
                    .tint(.white)
            case .fallido:
                Color.black.opacity(0.18)
                Image(systemName: "exclamationmark.triangle.fill")
                    .foregroundStyle(.white, .red)
            case .listo:
                EmptyView()
            }
        }
        .frame(width: 48, height: 48)
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .task(id: url) {
            guard image == nil else { return }
            let tieneAcceso = url.startAccessingSecurityScopedResource()
            defer {
                if tieneAcceso { url.stopAccessingSecurityScopedResource() }
            }
            let thumbnail = await Task.detached(priority: .userInitiated) {
                cgImagePreviaAcotada(url: url, maxPixelSize: 360)
            }.value
            guard !Task.isCancelled else { return }
            image = thumbnail.map(UIImage.init(cgImage:))
        }
        .accessibilityLabel("Vista previa de \(filename)")
    }
}

private struct HistorialChatView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var conversacionARenombrar: Conversation?
    @State private var tituloEditado = ""
    @State private var conversacionAEliminar: Conversation?
    let viewModel: ChatViewModel
    let client: APIClient?
    let onNueva: () -> Void
    let onSeleccionar: (String) -> Void

    var body: some View {
        NavigationStack {
            Group {
                if viewModel.cargandoHistorial && viewModel.conversaciones.isEmpty {
                    ProgressView("Cargando conversaciones…")
                } else if viewModel.conversaciones.isEmpty {
                    EmptyStateView(
                        icono: "bubble.left.and.bubble.right",
                        titulo: "Todavía no hay conversaciones",
                        descripcion: "Empieza un nuevo chat y Edecan guardará el contexto por ti."
                    )
                    .padding()
                } else {
                    // La conversación "principal" (frente 5, paridad REFERENCIA —
                    // ahí aterrizan los avisos automáticos que el dueño no
                    // pidió) siempre va primero, sin importar `updatedAt`:
                    // es un ancla fija, no otro chat más reciente que otro.
                    List(conversacionesOrdenadas) { conversacion in
                        Button {
                            onSeleccionar(conversacion.id)
                        } label: {
                            HStack(spacing: 12) {
                                if conversacion.isMain {
                                    ZStack {
                                        Circle().fill(EdecanTheme.degradado)
                                        Image(systemName: "bell.badge.fill")
                                            .font(.system(size: 14, weight: .semibold))
                                            .foregroundStyle(.white)
                                    }
                                    .frame(width: 32, height: 32)
                                }
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(titulo(conversacion)).font(.body.weight(.medium))
                                    Text(subtitulo(conversacion))
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                            }
                        }
                        .buttonStyle(.plain)
                        .listRowBackground(
                            conversacion.isMain ? EdecanTheme.morado.opacity(0.10) : Color.clear
                        )
                        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                            Button(role: .destructive) {
                                conversacionAEliminar = conversacion
                            } label: {
                                Label("Eliminar", systemImage: "trash")
                            }
                            Button {
                                conversacionARenombrar = conversacion
                                tituloEditado = titulo(conversacion)
                            } label: {
                                Label("Renombrar", systemImage: "pencil")
                            }
                            .tint(.indigo)
                        }
                    }
                }
            }
            .navigationTitle("Conversaciones")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { Button("Cerrar") { dismiss() } }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        onNueva()
                    } label: {
                        Image(systemName: "square.and.pencil")
                    }
                    .accessibilityLabel("Nuevo chat")
                }
            }
            .refreshable {
                guard let client else { return }
                await viewModel.cargarConversaciones(client: client)
            }
            .alert("Renombrar conversación", isPresented: Binding(
                get: { conversacionARenombrar != nil },
                set: { if !$0 { conversacionARenombrar = nil } }
            )) {
                TextField("Nombre", text: $tituloEditado)
                Button("Cancelar", role: .cancel) { conversacionARenombrar = nil }
                Button("Guardar") {
                    guard let client, let id = conversacionARenombrar?.id else { return }
                    let titulo = tituloEditado
                    conversacionARenombrar = nil
                    Task { await viewModel.renombrarConversacion(id: id, titulo: titulo, client: client) }
                }
            }
            .alert("¿Eliminar esta conversación?", isPresented: Binding(
                get: { conversacionAEliminar != nil },
                set: { if !$0 { conversacionAEliminar = nil } }
            )) {
                Button("Cancelar", role: .cancel) { conversacionAEliminar = nil }
                Button("Eliminar", role: .destructive) {
                    guard let client, let id = conversacionAEliminar?.id else { return }
                    conversacionAEliminar = nil
                    Task { await viewModel.eliminarConversacion(id: id, client: client) }
                }
            } message: {
                Text("Esta acción no se puede deshacer.")
            }
        }
    }

    private func titulo(_ conversation: Conversation) -> String {
        let title = conversation.title?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return title.isEmpty ? "Conversación" : title
    }

    private func subtitulo(_ conversation: Conversation) -> String {
        let fecha = (conversation.updatedAt ?? conversation.createdAt)
            .formatted(.dateTime.day().month().hour().minute())
        return conversation.isMain ? "Avisos automáticos · \(fecha)" : fecha
    }

    /// `viewModel.conversaciones` ya llega ordenada por `updatedAt` desde el
    /// backend (`GET /v1/conversations`); acá solo se saca la principal (si
    /// existe -- se crea perezosamente al primer aviso automático, así que
    /// puede no haber ninguna todavía) y se fija arriba de las demás.
    private var conversacionesOrdenadas: [Conversation] {
        let principal = viewModel.conversaciones.filter(\.isMain)
        let resto = viewModel.conversaciones.filter { !$0.isMain }
        return principal + resto
    }
}

private func guardarArtefactoTemporal(_ descarga: DownloadedArtifact) throws -> URL {
    let fileManager = FileManager.default
    let idSeguro = descarga.artifact.fileId
        .filter { $0.isLetter || $0.isNumber || $0 == "-" || $0 == "_" }
        .prefix(80)
    let carpetaId = idSeguro.isEmpty ? "archivo" : String(idSeguro)
    let raiz = fileManager.temporaryDirectory
        .appendingPathComponent("edecan-artifacts", isDirectory: true)
        .appendingPathComponent(carpetaId, isDirectory: true)
    try fileManager.createDirectory(at: raiz, withIntermediateDirectories: true)
    let url = raiz.appendingPathComponent(nombreSeguro(descarga.artifact.filename), isDirectory: false)
    try descarga.data.write(to: url, options: .atomic)
    return url
}

private func nombreSeguro(_ filename: String) -> String {
    let normalizado = filename.replacingOccurrences(of: "\\", with: "/")
    let ultimo = normalizado.split(separator: "/").last.map(String.init) ?? "archivo"
    let limpio = ultimo
        .replacingOccurrences(of: ":", with: "-")
        .trimmingCharacters(in: .whitespacesAndNewlines)
    return limpio.isEmpty ? "archivo" : String(limpio.prefix(180))
}

private struct ArchivoCompartible: Identifiable {
    let id = UUID()
    let url: URL
}

private struct HojaCompartir: UIViewControllerRepresentable {
    let items: [Any]
    let onFinish: () -> Void

    func makeUIViewController(context: Context) -> UIActivityViewController {
        let controller = UIActivityViewController(activityItems: items, applicationActivities: nil)
        controller.completionWithItemsHandler = { _, _, _, _ in
            Task { @MainActor in onFinish() }
        }
        return controller
    }

    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {}
}

private struct CapturadorFotoChat: UIViewControllerRepresentable {
    let onResult: (Result<UIImage, Error>) -> Void

    final class Coordinator: NSObject, UINavigationControllerDelegate, UIImagePickerControllerDelegate {
        let onResult: (Result<UIImage, Error>) -> Void

        init(onResult: @escaping (Result<UIImage, Error>) -> Void) {
            self.onResult = onResult
        }

        func imagePickerController(
            _ picker: UIImagePickerController,
            didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]
        ) {
            guard let image = info[.originalImage] as? UIImage else {
                onResult(.failure(CocoaError(.fileReadUnknown)))
                return
            }
            onResult(.success(image))
        }

        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) {
            picker.dismiss(animated: true)
            // Sin binding aquí: el Coordinator no conoce mostrandoCamara; el
            // dismiss cierra el picker y el sheet se actualiza por sí mismo.
        }
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(onResult: onResult)
    }

    func makeUIViewController(context: Context) -> UIImagePickerController {
        let controller = UIImagePickerController()
        controller.sourceType = .camera
        controller.cameraCaptureMode = .photo
        controller.delegate = context.coordinator
        return controller
    }

    func updateUIViewController(_ uiViewController: UIImagePickerController, context: Context) {}
}

/// Tarjeta compacta de misión en el hilo — el detalle pesado vive en Actividad.
private struct TarjetaMisionEnHilo: View {
    let mision: MissionOut
    @Binding var steerMision: String
    let onSteer: (String) -> Void
    let onOpenActivity: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: "briefcase.fill")
                    .foregroundStyle(EdecanTheme.morado)
                Text("Trabajo en curso")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                Spacer(minLength: 0)
                Button("Ver en Actividad", action: onOpenActivity)
                    .font(.caption.weight(.semibold))
            }
            Text(mision.objetivo)
                .font(.subheadline.weight(.medium))
                .fixedSize(horizontal: false, vertical: true)
            if let resultado = mision.resultado, !resultado.isEmpty {
                Text(resultado)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .lineLimit(3)
            }
            if !mision.esTerminal {
                HStack(spacing: 8) {
                    TextField("Redirigir…", text: $steerMision, axis: .vertical)
                        .lineLimit(1...2)
                        .textFieldStyle(.roundedBorder)
                    Button("Enviar") {
                        let instruction = steerMision.trimmingCharacters(in: .whitespacesAndNewlines)
                        guard !instruction.isEmpty else { return }
                        onSteer(instruction)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(EdecanTheme.morado)
                    .controlSize(.small)
                    .disabled(steerMision.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
        }
        .padding(12)
        .frame(maxWidth: 340, alignment: .leading)
        .tarjetaVidrio(esquina: 14)
    }
}

private struct MissionInboxChips: View {
    let onOpenActivity: () -> Void
    @Environment(SessionStore.self) private var session
    @State private var misiones: [MissionOut] = []

    var body: some View {
        let activas = Array(misiones.filter(\.estaActiva).prefix(2))
        Group {
            if !activas.isEmpty {
                VStack(spacing: 8) {
                    ForEach(activas) { mision in
                        Button(action: onOpenActivity) {
                            Text("Seguir: \(mision.objetivo)")
                                .font(.caption)
                                .multilineTextAlignment(.center)
                                .padding(.horizontal, 12)
                                .padding(.vertical, 8)
                                .background(EdecanTheme.morado.opacity(0.12), in: Capsule())
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.top, 8)
            }
        }
        .task {
            guard let client = session.client else { return }
            misiones = (try? await client.listMissions()) ?? []
        }
    }
}

private func limpiarArchivoTemporal(_ url: URL) {
    let temporales = FileManager.default.temporaryDirectory.standardizedFileURL.path
    let archivo = url.standardizedFileURL.path
    guard archivo.hasPrefix(temporales + "/") else { return }
    try? FileManager.default.removeItem(at: url.deletingLastPathComponent())
}
