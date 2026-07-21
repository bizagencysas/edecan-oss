import SwiftUI
import EdecanKit
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
    @Environment(\.openURL) private var openURL
    @State private var viewModel = ChatViewModel()
    @State private var textoActual = ""
    @State private var mostrandoVoz = false
    @State private var mostrandoHistorial = false
    @State private var mostrandoSelectorArchivos = false
    @State private var adjuntosPendientes: [AdjuntoPendiente] = []
    @State private var tareasSubida: [UUID: Task<Void, Never>] = [:]
    @State private var artefactoDescargandoId: String?
    @State private var archivoCompartible: ArchivoCompartible?
    @State private var conversacionPersistida = ""
    private let estadoLocal = ChatLocalStateStore()
    @FocusState private var campoEnfocado: Bool

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                listaDeMensajes
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
            .navigationTitle(viewModel.tituloConversacionActual)
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
            .sheet(item: $archivoCompartible) { archivo in
                HojaCompartir(items: [archivo.url]) {
                    limpiarArchivoTemporal(archivo.url)
                    archivoCompartible = nil
                }
            }
            .fileImporter(
                isPresented: $mostrandoSelectorArchivos,
                allowedContentTypes: [.item],
                allowsMultipleSelection: true,
                onCompletion: recibirArchivos
            )
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button { mostrandoHistorial = true } label: {
                        Image(systemName: "text.bubble.fill")
                    }
                    .accessibilityLabel("Conversaciones")
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
                conversacionPersistida = estadoLocal.currentConversationId ?? ""
                if textoActual.isEmpty {
                    textoActual = cargarBorrador(conversationId: conversacionPersistida.vacioANil)
                }
                await viewModel.iniciar(
                    client: client,
                    preferredConversationId: conversacionPersistida.vacioANil
                )
            }
            .onChange(of: viewModel.conversacionId) { anterior, nueva in
                guardarBorrador(textoActual, conversationId: anterior)
                conversacionPersistida = nueva ?? ""
                estadoLocal.currentConversationId = nueva
                textoActual = cargarBorrador(conversationId: nueva)
                cancelarTodasLasSubidas()
                adjuntosPendientes = []
            }
            .onChange(of: textoActual) { _, nuevo in
                guardarBorrador(nuevo, conversationId: viewModel.conversacionId)
            }
            .onChange(of: tabRouter.solicitudPendiente?.id) { _, nuevaId in
                guard nuevaId != nil, let solicitud = tabRouter.consumirSolicitud() else { return }
                // Las solicitudes rapidas preparan el texto; el usuario sigue
                // teniendo la ultima palabra antes de enviarlo.
                textoActual = solicitud.texto
                campoEnfocado = true
            }
            .onDisappear {
                cancelarTodasLasSubidas()
            }
        }
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
                            icono: "sparkles",
                            titulo: "¿Que hacemos?",
                            descripcion: "Escribe, habla o toca + para crear un documento, PDF, presentacion, sitio, app o post."
                        )
                        .padding(.top, 60)
                    }
                    ForEach(viewModel.mensajes) { mensaje in
                        BurbujaMensaje(
                            mensaje: mensaje,
                            client: session.client,
                            artefactoDescargandoId: artefactoDescargandoId,
                            onAbrirArtefacto: descargarYCompartir,
                            onAction: ejecutarAccion,
                            onRetry: reintentarMensaje
                        )
                        .id(mensaje.id)
                    }
                    if let herramienta = viewModel.herramientaActiva {
                        IndicadorHerramienta(nombre: herramienta.nombre)
                    }
                    if let confirmacion = viewModel.confirmacionPendiente {
                        TarjetaConfirmacion(confirmacion: confirmacion, deshabilitada: viewModel.enviando) { aprobado in
                            resolverConfirmacion(aprobado: aprobado)
                        }
                    }
                }
                .padding()
            }
            .onChange(of: viewModel.mensajes.count) { _, _ in
                desplazarAlFinal(proxy)
            }
            .onChange(of: viewModel.mensajes.last?.texto) { _, _ in
                desplazarAlFinal(proxy)
            }
        }
    }

    private func desplazarAlFinal(_ proxy: ScrollViewProxy) {
        guard let ultimo = viewModel.mensajes.last else { return }
        withAnimation(.easeOut(duration: 0.2)) {
            proxy.scrollTo(ultimo.id, anchor: .bottom)
        }
    }

    private var barraDeEntrada: some View {
        VStack(alignment: .leading, spacing: 8) {
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

            HStack(alignment: .bottom, spacing: 10) {
                Menu {
                    Section("Crear") {
                        botonPreset("Documento", icono: "doc.text", texto: "Crea un documento Word sobre ")
                        botonPreset("PDF", icono: "doc.richtext", texto: "Crea un PDF sobre ")
                        botonPreset("Presentación", icono: "rectangle.on.rectangle", texto: "Crea una presentación sobre ")
                        botonPreset("Sitio web", icono: "globe", texto: "Crea un sitio web para ")
                        botonPreset("App", icono: "apps.iphone", texto: "Crea una app para ")
                        botonPreset("Post", icono: "text.bubble", texto: "Crea un post sobre ")
                    }
                    Section {
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
                .disabled(viewModel.enviando || viewModel.confirmacionPendiente != nil)
                .accessibilityLabel("Crear o adjuntar")

                TextField("Escríbele a Edecán…", text: $textoActual, axis: .vertical)
                    .lineLimit(1...5)
                    .focused($campoEnfocado)
                    .textFieldStyle(.plain)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .tarjetaVidrio(esquina: 18)

                if hayContenidoParaEnviar {
                    Button(action: enviarMensajeActual) {
                        Image(systemName: "arrow.up.circle.fill")
                            .font(.system(size: 34))
                            .foregroundStyle(botonHabilitado ? AnyShapeStyle(EdecanTheme.degradado) : AnyShapeStyle(.tertiary))
                    }
                    .disabled(!botonHabilitado)
                    .accessibilityLabel("Enviar")
                } else {
                    Button { mostrandoVoz = true } label: {
                        Image(systemName: "mic.fill")
                            .font(.system(size: 18, weight: .semibold))
                            .frame(width: 42, height: 42)
                            .foregroundStyle(EdecanTheme.morado)
                            .tarjetaVidrio(esquina: 18)
                    }
                    .disabled(viewModel.enviando || viewModel.confirmacionPendiente != nil)
                    .accessibilityLabel("Hablar con Edecan")
                }
            }
        }
        .padding()
    }

    private var hayContenidoParaEnviar: Bool {
        !textoActual.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !adjuntosPendientes.isEmpty
    }

    private var botonHabilitado: Bool {
        (textoActual.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false
            || adjuntosListos.isEmpty == false)
            && adjuntosPendientes.allSatisfy(\.estaListo)
            && !viewModel.enviando
            && viewModel.confirmacionPendiente == nil
    }

    private func enviarMensajeActual() {
        guard let client = session.client else {
            viewModel.errorMensaje = "No hay sesión activa."
            return
        }
        let texto = textoActual
        let adjuntos = adjuntosListos
        Task {
            let enviado = await viewModel.enviar(texto: texto, adjuntos: adjuntos, client: client)
            if enviado {
                textoActual = ""
                adjuntosPendientes = []
                guardarBorrador("", conversationId: viewModel.conversacionId)
            }
        }
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
        Task { await viewModel.resolverConfirmacion(aprobado: aprobado, client: client) }
    }

    /// Unica puerta de ejecucion para acciones venidas del servidor. Los
    /// mensajes solo rellenan el compositor, las URLs se revalidan como
    /// HTTP(S), y las pantallas pasan por un enum cerrado.
    private func ejecutarAccion(_ action: ChatAction) {
        switch action {
        case .openURL(_, _, let rawURL):
            guard let url = ChatAction.httpURLSegura(rawURL.absoluteString) else {
                viewModel.errorMensaje = "El enlace no es seguro y no se abrio."
                return
            }
            openURL(url)
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
        case .unsupported:
            break
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
            switch adjunto.estado {
            case .subiendo:
                ProgressView().controlSize(.small)
            case .listo:
                Image(systemName: "doc.fill").foregroundStyle(EdecanTheme.azul)
            case .fallido:
                Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.red)
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
        Task { _ = await viewModel.reintentar(mensajeId: id, client: client) }
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

private struct HistorialChatView: View {
    @Environment(\.dismiss) private var dismiss
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
                    List(viewModel.conversaciones) { conversacion in
                        Button {
                            onSeleccionar(conversacion.id)
                        } label: {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(titulo(conversacion)).font(.body.weight(.medium))
                                Text((conversacion.updatedAt ?? conversacion.createdAt), format: .dateTime.day().month().hour().minute())
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .buttonStyle(.plain)
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
        }
    }

    private func titulo(_ conversation: Conversation) -> String {
        let title = conversation.title?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return title.isEmpty ? "Conversación" : title
    }
}

private extension String {
    var vacioANil: String? { isEmpty ? nil : self }
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

private func limpiarArchivoTemporal(_ url: URL) {
    let temporales = FileManager.default.temporaryDirectory.standardizedFileURL.path
    let archivo = url.standardizedFileURL.path
    guard archivo.hasPrefix(temporales + "/") else { return }
    try? FileManager.default.removeItem(at: url.deletingLastPathComponent())
}
