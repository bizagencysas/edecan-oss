import EdecanKit
import SwiftUI
import UIKit
import UniformTypeIdentifiers

/// Studio completo de proyectos, deliberadamente presentado como una acción
/// sencilla: describe, revisa y pide cambios. El motor, las credenciales y el
/// historial local permanecen detrás de la API privada de Edecán.
struct ContentStudioView: View {
    private enum Pantalla { case biblioteca, crear, proyecto }

    @Environment(\.dismiss) private var dismiss
    @Environment(SessionStore.self) private var session
    @Environment(TabRouter.self) private var router

    @State private var pantalla: Pantalla = .biblioteca
    @State private var proyectos: [StudioProjectSummary] = []
    @State private var proyecto: StudioProjectSummary?
    @State private var revisiones: [StudioRevision] = []
    @State private var revisionSeleccionada: String?
    @State private var artefactos: [ArtifactRef] = []
    @State private var previewTarget: SecurePreviewTarget?

    @State private var pedido = ""
    @State private var nombreProyecto = ""
    @State private var marca = ""
    @State private var modo: StudioProjectMode = .general
    @State private var calidad: StudioProjectQuality = .balanced
    @State private var cantidad = 2
    @State private var referencias: [UploadedFile] = []
    @State private var mostrarSelectorArchivos = false
    @State private var solicitudUniversal = ""

    @State private var instruccion = ""
    @State private var trabajando = false
    @State private var etapa = ""
    @State private var errorMensaje: String?
    @State private var avisoMensaje: String?
    @State private var tarea: Task<Void, Never>?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    switch pantalla {
                    case .biblioteca: biblioteca
                    case .crear: formulario
                    case .proyecto: detalleProyecto
                    }
                }
                .padding()
            }
            .background(EdecanTheme.degradado.opacity(0.05).ignoresSafeArea())
            .navigationTitle(pantalla == .proyecto ? (proyecto?.name ?? "Proyecto") : "Crear")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { barra }
            .safeAreaInset(edge: .top) {
                if pantalla != .proyecto {
                    Picker("Studio", selection: $pantalla) {
                        Text("Mis proyectos").tag(Pantalla.biblioteca)
                        Text("Crear algo").tag(Pantalla.crear)
                    }
                    .pickerStyle(.segmented)
                    .padding(.horizontal)
                    .padding(.vertical, 8)
                    .background(.bar)
                }
            }
            .fileImporter(
                isPresented: $mostrarSelectorArchivos,
                allowedContentTypes: [.data],
                allowsMultipleSelection: true,
                onCompletion: recibirReferencias
            )
            .sheet(item: $previewTarget) { target in
                SecurePreviewSheet(target: target, client: session.client)
            }
            .task { await cargarProyectos() }
            .onDisappear { tarea?.cancel() }
        }
    }

    @ToolbarContentBuilder
    private var barra: some ToolbarContent {
        ToolbarItem(placement: .topBarLeading) {
            if pantalla == .proyecto {
                Button {
                    pantalla = .biblioteca
                    proyecto = nil
                    revisiones = []
                    artefactos = []
                    Task { await cargarProyectos() }
                } label: {
                    Label("Proyectos", systemImage: "chevron.left")
                }
            } else {
                Button("Cerrar") { dismiss() }
            }
        }
    }

    private var biblioteca: some View {
        Group {
            VStack(alignment: .leading, spacing: 6) {
                Text("Todo lo que has creado")
                    .font(.title2.bold())
                Text("Cada cambio queda guardado. Abre un proyecto y continúa con una frase.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }

            mensajes

            if trabajando && proyectos.isEmpty {
                progreso
            } else if proyectos.isEmpty {
                ContentUnavailableView(
                    "Tu Studio está listo",
                    systemImage: "wand.and.stars",
                    description: Text("Puedes crear una página, una app, un post o una presentación desde una sola frase.")
                )
                Button {
                    pantalla = .crear
                } label: {
                    Label("Crear mi primer proyecto", systemImage: "plus")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .tint(EdecanTheme.morado)
            } else {
                ForEach(proyectos) { item in
                    Button { abrir(item) } label: {
                        HStack(spacing: 14) {
                            Image(systemName: icono(modo: item.mode))
                                .font(.title2)
                                .foregroundStyle(EdecanTheme.morado)
                                .frame(width: 42, height: 42)
                                .background(EdecanTheme.morado.opacity(0.1), in: RoundedRectangle(cornerRadius: 12))
                            VStack(alignment: .leading, spacing: 4) {
                                Text(item.name).font(.headline).foregroundStyle(.primary)
                                Text(subtitulo(item))
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                            Image(systemName: "chevron.right").foregroundStyle(.tertiary)
                        }
                        .padding(14)
                        .tarjetaVidrio(esquina: 16)
                    }
                    .buttonStyle(.plain)
                    .disabled(trabajando)
                }
            }

            Button { Task { await cargarProyectos() } } label: {
                Label("Actualizar", systemImage: "arrow.clockwise")
            }
            .buttonStyle(.bordered)
            .disabled(trabajando)
        }
    }

    private var formulario: some View {
        Group {
            VStack(alignment: .leading, spacing: 6) {
                Text("¿Qué quieres crear?")
                    .font(.title2.bold())
                Text("Dilo como se lo dirías a una persona. Edecán se encarga del resto.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }

            TextField(
                "Ej. Crea una página elegante para mi cafetería, con menú y reservas",
                text: $pedido,
                axis: .vertical
            )
            .lineLimit(4...9)
            .padding(14)
            .tarjetaVidrio(esquina: 16)
            .disabled(trabajando)

            VStack(alignment: .leading, spacing: 12) {
                Text("Tipo").font(.headline)
                Picker("Tipo", selection: $modo) {
                    ForEach(StudioProjectMode.allCases) { Text($0.label).tag($0) }
                }
                .pickerStyle(.menu)

                Divider()
                TextField("Nombre del proyecto (opcional)", text: $nombreProyecto)
                TextField("Marca (opcional)", text: $marca)
            }
            .padding(14)
            .tarjetaVidrio(esquina: 16)
            .disabled(trabajando)

            VStack(alignment: .leading, spacing: 12) {
                Picker("Calidad", selection: $calidad) {
                    ForEach(StudioProjectQuality.allCases) { Text($0.label).tag($0) }
                }
                .pickerStyle(.segmented)
                Stepper("Propuestas distintas: \(cantidad)", value: $cantidad, in: 1...4)
            }
            .padding(14)
            .tarjetaVidrio(esquina: 16)
            .disabled(trabajando)

            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Referencias").font(.headline)
                        Text("Imágenes, logos o documentos privados")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button {
                        mostrarSelectorArchivos = true
                    } label: {
                        Label("Añadir", systemImage: "paperclip")
                    }
                    .disabled(trabajando || referencias.count >= 12)
                }
                ForEach(referencias) { archivo in
                    HStack {
                        Image(systemName: "doc")
                        Text(archivo.filename).lineLimit(1)
                        Spacer()
                        Button(role: .destructive) {
                            referencias.removeAll { $0.id == archivo.id }
                        } label: {
                            Image(systemName: "xmark.circle.fill")
                        }
                        .disabled(trabajando)
                    }
                    .font(.callout)
                }
            }
            .padding(14)
            .tarjetaVidrio(esquina: 16)

            mensajes
            if trabajando { progreso }

            Button(action: crearProyecto) {
                Label(trabajando ? "Creando…" : "Crear proyecto", systemImage: "sparkles")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(EdecanTheme.morado)
            .disabled(trabajando || pedido.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)

            creacionUniversal

            VStack(alignment: .leading, spacing: 10) {
                Text("Puedes empezar así").font(.headline)
                ForEach([
                    "Una landing premium para presentar mi negocio y recibir clientes",
                    "Una app móvil sencilla para organizar hábitos diarios",
                    "Un carrusel educativo claro, humano y visual",
                    "Una presentación de venta moderna para explicar mi producto",
                ], id: \.self) { idea in
                    Button(idea) { pedido = idea }
                        .buttonStyle(.bordered)
                        .disabled(trabajando)
                }
            }
        }
    }

    private var creacionUniversal: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("¿Necesitas algo diferente?").font(.headline)
            Text("Imagen, video, campaña, producto, personaje o análisis: pídelo con naturalidad y continúa en el chat.")
                .font(.caption)
                .foregroundStyle(.secondary)

            LazyVGrid(columns: [GridItem(.adaptive(minimum: 92))], spacing: 7) {
                ForEach([
                    ("Imagen", "Crea una imagen original para "),
                    ("Video", "Crea y planifica un video para "),
                    ("Campaña", "Crea una campaña completa para "),
                    ("Producto", "Diseña un producto completo para "),
                    ("Personaje", "Crea un personaje coherente para "),
                    ("Analizar", "Analiza esto a fondo y entrégame conclusiones accionables: "),
                ], id: \.0) { item in
                    Button(item.0) { solicitudUniversal = item.1 }
                        .buttonStyle(.bordered)
                }
            }

            TextField("Describe lo que necesitas", text: $solicitudUniversal, axis: .vertical)
                .lineLimit(2...5)
                .textFieldStyle(.roundedBorder)
            Button {
                let clean = solicitudUniversal.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !clean.isEmpty else { return }
                router.pedir(clean)
                dismiss()
            } label: {
                Label("Continuar en el chat", systemImage: "bubble.left.and.bubble.right")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .disabled(solicitudUniversal.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)

            Text("Allí verás el progreso y los archivos a medida que Edecán trabaja.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(14)
        .tarjetaVidrio(esquina: 16)
    }

    private var detalleProyecto: some View {
        Group {
            if let proyecto {
                VStack(alignment: .leading, spacing: 6) {
                    Text(proyecto.name).font(.title2.bold())
                    Text("\(label(modo: proyecto.mode)) · \(revisiones.filter { $0.archivedAt == nil }.count) versiones guardadas")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }

                mensajes
                if trabajando { progreso }

                VStack(alignment: .leading, spacing: 12) {
                    HStack {
                        Text("Vista previa").font(.headline)
                        Spacer()
                        if let image = artefactos.first(where: { $0.mime?.hasPrefix("image/") == true }) {
                            Button("Abrir") { previewTarget = .artifact(image) }
                        }
                    }
                    Text("Ábrelo en el formato que necesitas. Siempre se descarga de forma privada.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    HStack(spacing: 9) {
                        botonFormato(.html, icono: "chevron.left.forwardslash.chevron.right")
                        botonFormato(.png, icono: "photo")
                        botonFormato(.pdf, icono: "doc.richtext")
                    }
                }
                .padding(14)
                .tarjetaVidrio(esquina: 16)

                VStack(alignment: .leading, spacing: 10) {
                    Text("Pídele un cambio").font(.headline)
                    TextField(
                        "Ej. Haz el título más elegante y agrega una sección de testimonios",
                        text: $instruccion,
                        axis: .vertical
                    )
                    .lineLimit(3...7)
                    .textFieldStyle(.roundedBorder)
                    Button(action: editarProyecto) {
                        Label("Aplicar y guardar nueva versión", systemImage: "wand.and.stars")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(EdecanTheme.morado)
                    .disabled(trabajando || instruccion.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
                .padding(14)
                .tarjetaVidrio(esquina: 16)

                VStack(alignment: .leading, spacing: 10) {
                    Text("Variantes e historial").font(.headline)
                    if revisiones.isEmpty {
                        Text("Todavía no hay versiones disponibles.")
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(revisiones.reversed()) { revision in
                            Button {
                                revisionSeleccionada = revision.id
                                abrirFormato(.png)
                            } label: {
                                HStack {
                                    Image(systemName: revisionSeleccionada == revision.id ? "checkmark.circle.fill" : "circle")
                                        .foregroundStyle(EdecanTheme.morado)
                                    VStack(alignment: .leading, spacing: 3) {
                                        Text(revision.label).font(.headline).foregroundStyle(.primary)
                                        Text(detalle(revision))
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                    }
                                    Spacer()
                                }
                                .padding(12)
                                .background(.background.opacity(0.55), in: RoundedRectangle(cornerRadius: 12))
                            }
                            .buttonStyle(.plain)
                            .disabled(trabajando || revision.archivedAt != nil)
                        }
                    }
                }
                .padding(14)
                .tarjetaVidrio(esquina: 16)
            } else {
                progreso
            }
        }
    }

    private var progreso: some View {
        HStack(spacing: 12) {
            ProgressView()
            VStack(alignment: .leading, spacing: 3) {
                Text("Edecán está trabajando…").font(.headline)
                Text(etapa).font(.caption).foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .tarjetaVidrio(esquina: 16)
    }

    @ViewBuilder
    private var mensajes: some View {
        if let errorMensaje {
            Text(errorMensaje)
                .font(.callout)
                .foregroundStyle(.red)
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.red.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
        }
        if let avisoMensaje {
            Text(avisoMensaje)
                .font(.callout)
                .foregroundStyle(.secondary)
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(EdecanTheme.morado.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
        }
    }

    private func botonFormato(_ formato: StudioExportFormat, icono: String) -> some View {
        Button { abrirFormato(formato) } label: {
            Label(formato.label, systemImage: icono).frame(maxWidth: .infinity)
        }
        .buttonStyle(.bordered)
        .disabled(trabajando)
    }

    private func cargarProyectos() async {
        guard pantalla != .proyecto, let client = session.client else { return }
        trabajando = true
        etapa = "Buscando tus proyectos"
        errorMensaje = nil
        do {
            let response = try await ContentStudioService(client: client).perform(
                StudioActionRequest(action: "list")
            )
            try Task.checkCancellation()
            proyectos = response.projects
        } catch is CancellationError {
            return
        } catch {
            errorMensaje = error.localizedDescription
        }
        trabajando = false
        etapa = ""
    }

    private func crearProyecto() {
        guard let client = session.client else {
            errorMensaje = "No hay una sesión lista. Vuelve a conectar Edecán."
            return
        }
        iniciar(etapa: "Creando propuestas y preparando la vista previa") { @MainActor in
            let request = StudioActionRequest(
                action: "create",
                prompt: pedido.trimmingCharacters(in: .whitespacesAndNewlines),
                projectName: nombreProyecto.nilSiVacio,
                brandName: marca.nilSiVacio,
                mode: modo,
                count: cantidad,
                quality: calidad,
                files: referencias.map(\.id)
            )
            let response = try await ContentStudioService(client: client).perform(request)
            guard let created = response.project else {
                throw APIClient.APIError.respuestaInvalida
            }
            artefactos = response.artifacts
            proyecto = created
            pantalla = .proyecto
            try await cargarDetalle(created, client: client, preferida: response.revisionId)
            avisoMensaje = response.message
            await LocalNotificationScheduler.completed(
                kind: "studio",
                id: created.id,
                title: "Proyecto listo",
                body: "Tu creación ya está lista para revisar.",
                route: .create
            )
        }
    }

    private func abrir(_ item: StudioProjectSummary) {
        guard let client = session.client else { return }
        proyecto = item
        pantalla = .proyecto
        iniciar(etapa: "Abriendo el proyecto") { @MainActor in
            try await cargarDetalle(item, client: client, preferida: nil)
        }
    }

    private func cargarDetalle(
        _ item: StudioProjectSummary,
        client: APIClient,
        preferida: String?
    ) async throws {
        let history = try await ContentStudioService(client: client).perform(
            StudioActionRequest(action: "history", projectId: item.id)
        )
        revisiones = history.revisions
        proyecto = history.project ?? item
        revisionSeleccionada = preferida
            ?? history.revisions.last(where: { $0.archivedAt == nil })?.id
    }

    private func editarProyecto() {
        guard let client = session.client, let proyecto else { return }
        iniciar(etapa: "Aplicando el cambio sin perder la versión anterior") { @MainActor in
            let response = try await ContentStudioService(client: client).perform(
                StudioActionRequest(
                    action: "edit",
                    projectId: proyecto.id,
                    revisionId: revisionSeleccionada,
                    instruction: instruccion.trimmingCharacters(in: .whitespacesAndNewlines),
                    quality: calidad
                )
            )
            artefactos = response.artifacts
            try await cargarDetalle(proyecto, client: client, preferida: response.revisionId)
            instruccion = ""
            avisoMensaje = response.message
        }
    }

    private func abrirFormato(_ formato: StudioExportFormat) {
        guard let client = session.client, let proyecto else { return }
        iniciar(etapa: "Preparando \(formato.label) de forma privada") { @MainActor in
            let action = switch formato {
            case .html: "read"
            case .png: "render"
            case .pdf: "export"
            }
            let response = try await ContentStudioService(client: client).perform(
                StudioActionRequest(
                    action: action,
                    projectId: proyecto.id,
                    revisionId: revisionSeleccionada,
                    exportFormat: formato == .pdf ? formato : nil
                )
            )
            artefactos = combinar(artefactos, response.artifacts)
            guard let artifact = response.artifacts.first(where: { coincide($0, formato: formato) })
                    ?? response.artifacts.first else {
                throw APIClient.APIError.respuestaInvalida
            }
            previewTarget = .artifact(artifact)
        }
    }

    private func recibirReferencias(_ result: Result<[URL], Error>) {
        guard let client = session.client else { return }
        switch result {
        case .failure(let error):
            errorMensaje = error.localizedDescription
        case .success(let urls):
            iniciar(etapa: "Guardando referencias de forma privada") { @MainActor in
                for url in urls.prefix(max(0, 12 - referencias.count)) {
                    let acceso = url.startAccessingSecurityScopedResource()
                    defer { if acceso { url.stopAccessingSecurityScopedResource() } }
                    let mime = UTType(filenameExtension: url.pathExtension)?.preferredMIMEType
                        ?? "application/octet-stream"
                    let uploaded = try await client.subirArchivo(
                        desde: url,
                        filename: url.lastPathComponent,
                        mimeType: mime
                    )
                    referencias.append(uploaded)
                }
            }
        }
    }

    private func iniciar(
        etapa nuevaEtapa: String,
        operacion: @escaping @MainActor () async throws -> Void
    ) {
        tarea?.cancel()
        trabajando = true
        etapa = nuevaEtapa
        errorMensaje = nil
        avisoMensaje = nil
        tarea = Task { @MainActor in
            do {
                try await operacion()
            } catch is CancellationError {
                return
            } catch {
                errorMensaje = error.localizedDescription
            }
            trabajando = false
            etapa = ""
        }
    }

    private func combinar(_ current: [ArtifactRef], _ incoming: [ArtifactRef]) -> [ArtifactRef] {
        var values = Dictionary(uniqueKeysWithValues: current.map { ($0.fileId, $0) })
        incoming.forEach { values[$0.fileId] = $0 }
        return Array(values.values)
    }

    private func coincide(_ artifact: ArtifactRef, formato: StudioExportFormat) -> Bool {
        let filename = artifact.filename.lowercased()
        switch formato {
        case .html: return filename.hasSuffix(".html") || artifact.mime == "text/html"
        case .png: return filename.hasSuffix(".png") || artifact.mime == "image/png"
        case .pdf: return filename.hasSuffix(".pdf") || artifact.mime == "application/pdf"
        }
    }

    private func subtitulo(_ project: StudioProjectSummary) -> String {
        let date = project.updatedAt.map { String($0.prefix(10)) }
        return [label(modo: project.mode), "\(project.revisionCount) versiones", date]
            .compactMap { $0 }.joined(separator: " · ")
    }

    private func detalle(_ revision: StudioRevision) -> String {
        let dimensions = revision.width > 0 ? "\(revision.width) × \(revision.height)" : nil
        let date = revision.createdAt.map { String($0.prefix(10)) }
        return [dimensions, date, revision.instruction.nilSiVacio]
            .compactMap { $0 }.joined(separator: " · ")
    }

    private func label(modo raw: String) -> String {
        StudioProjectMode(rawValue: raw)?.label ?? "Proyecto"
    }

    private func icono(modo raw: String) -> String {
        switch StudioProjectMode(rawValue: raw) {
        case .landing: "globe"
        case .mockup: "apps.iphone"
        case .post, .carousel, .ad: "photo.on.rectangle"
        case .email: "envelope"
        case .deck: "rectangle.on.rectangle.angled"
        case .general, .none: "wand.and.stars"
        }
    }
}

private extension String {
    var nilSiVacio: String? {
        let clean = trimmingCharacters(in: .whitespacesAndNewlines)
        return clean.isEmpty ? nil : clean
    }
}

struct LinkedInStudioView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(SessionStore.self) private var session

    @State private var target: SocialContentTarget = .personal
    @State private var idea = ""
    @State private var objetivo = "Aportar una idea útil"
    @State private var tono = "Claro, humano y con criterio"
    @State private var crearImagen = true
    @State private var draft: SocialContentDraft?
    @State private var textoEditable = ""
    @State private var cambios = ""
    @State private var trabajando = false
    @State private var etapa = ""
    @State private var errorMensaje: String?
    @State private var avisoMensaje: String?
    // Aviso NEUTRO para `estado == .unverified` -- a propósito separado de
    // `avisoMensaje` (verde) y `errorMensaje` (rojo): se envió al servidor y
    // no reventó, pero no se pudo confirmar en LinkedIn. Ver
    // `SocialApprovalStatus` (EdecanKit) para el porqué de los tres casos.
    @State private var avisoSinConfirmar: String?
    // Igual que en `SocialDraftCardView` (`BurbujaMensaje.swift`) -- misma
    // card, mismo patrón, mismo motivo: un booleano no alcanza para
    // distinguir "no se sabe si se publicó" de "sí se publicó" o "no se
    // publicó nada".
    @State private var estado: SocialApprovalStatus = .pending
    @State private var previewTarget: SecurePreviewTarget?
    @State private var tarea: Task<Void, Never>?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    encabezado
                    formulario
                    mensajes
                    if trabajando { progreso }
                    if let draft { preview(draft) }
                }
                .padding()
            }
            .background(EdecanTheme.degradado.opacity(0.06).ignoresSafeArea())
            .navigationTitle("LinkedIn")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cerrar") { dismiss() }
                }
            }
            .sheet(item: $previewTarget) { target in
                SecurePreviewSheet(target: target, client: session.client)
            }
            .onDisappear { tarea?.cancel() }
        }
    }

    private var encabezado: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Crea, revisa y decide")
                .font(.title2.bold())
            Text(target.isPersonal
                 ? "Para tu LinkedIn personal: copia el texto y guarda la imagen."
                 : "Para una página de organización: aprueba o pide cambios antes de publicar.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
    }

    private var formulario: some View {
        VStack(alignment: .leading, spacing: 14) {
            Picker("Destino", selection: $target) {
                // Solo dos opciones por ahora: el perfil personal (siempre disponible) y
                // un placeholder genérico de "página de organización" -- esta app todavía
                // no tiene un selector dinámico de los destinos que el tenant configuró en
                // el backend (ver `SocialContentTarget.organization`).
                ForEach([SocialContentTarget.personal, .organization]) { item in
                    Text(item.label).tag(item)
                }
            }
            .pickerStyle(.segmented)
            .onChange(of: target) { _, _ in
                draft = nil
                textoEditable = ""
                avisoMensaje = nil
                avisoSinConfirmar = nil
                errorMensaje = nil
                estado = .pending
            }

            TextField(
                "Ej. Post sobre por qué la educación financiera importa",
                text: $idea,
                axis: .vertical
            )
            .lineLimit(4...8)
            .padding(14)
            .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 18, style: .continuous))

            VStack(spacing: 10) {
                TextField("Objetivo", text: $objetivo)
                Divider()
                TextField("Tono", text: $tono)
                Toggle("Crear también una imagen", isOn: $crearImagen)
            }
            .padding(14)
            .tarjetaVidrio(esquina: 18)

            Button(action: crear) {
                Label(trabajando ? "Creando…" : "Crear borrador", systemImage: "sparkles")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(EdecanTheme.morado)
            .disabled(trabajando || idea.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
    }

    @ViewBuilder
    private var mensajes: some View {
        if let errorMensaje {
            Text(errorMensaje)
                .font(.footnote)
                .foregroundStyle(.red)
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.red.opacity(0.08), in: RoundedRectangle(cornerRadius: 14))
        }
        if let avisoMensaje {
            Text(avisoMensaje)
                .font(.footnote)
                .foregroundStyle(.green)
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.green.opacity(0.1), in: RoundedRectangle(cornerRadius: 14))
        }
        if let avisoSinConfirmar {
            // Naranja: ni el verde de un éxito confirmado ni el rojo de un
            // error real -- "se envió, pero no lo sabemos con certeza".
            Text(avisoSinConfirmar)
                .font(.footnote)
                .foregroundStyle(.orange)
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.orange.opacity(0.1), in: RoundedRectangle(cornerRadius: 14))
        }
    }

    private var progreso: some View {
        HStack(spacing: 10) {
            ProgressView()
            Text(etapa.isEmpty ? "Trabajando…" : etapa)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.secondary)
        }
        .padding(14)
        .tarjetaVidrio(esquina: 16)
    }

    private func preview(_ draft: SocialContentDraft) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text("Borrador")
                    .font(.headline)
                Spacer()
                Text(target.label)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(EdecanTheme.morado)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 5)
                    .background(EdecanTheme.morado.opacity(0.12), in: Capsule())
            }

            TextEditor(text: $textoEditable)
                .frame(minHeight: 220)
                .scrollContentBackground(.hidden)
                .padding(10)
                .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 16))

            if let warning = draft.visualWarning, !warning.isEmpty {
                Text(warning)
                    .font(.caption)
                    .foregroundStyle(.orange)
            }

            if let image = draft.imageArtifact {
                Button {
                    previewTarget = .artifact(image)
                } label: {
                    HStack(spacing: 12) {
                        Image(systemName: "photo.on.rectangle.angled")
                            .foregroundStyle(EdecanTheme.morado)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Imagen lista")
                                .font(.subheadline.weight(.semibold))
                            Text(image.filename)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(1)
                        }
                        Spacer()
                        Image(systemName: "square.and.arrow.up")
                            .foregroundStyle(.secondary)
                    }
                    .padding(14)
                    .tarjetaVidrio(esquina: 16)
                }
                .buttonStyle(.plain)
            }

            acciones(draft)
        }
        .padding(16)
        .tarjetaVidrio(esquina: 22)
    }

    @ViewBuilder
    private func acciones(_ draft: SocialContentDraft) -> some View {
        if target.isPersonal {
            HStack(spacing: 10) {
                Button {
                    UIPasteboard.general.string = textoEditable
                    avisoMensaje = "Texto copiado."
                } label: {
                    Label("Copiar texto", systemImage: "doc.on.doc")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .tint(EdecanTheme.morado)

                Button {
                    if let image = draft.imageArtifact {
                        previewTarget = .artifact(image)
                    } else {
                        avisoMensaje = "Este borrador no tiene imagen."
                    }
                } label: {
                    Label("Guardar imagen", systemImage: "photo")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
            }
        } else {
            VStack(spacing: 10) {
                Button(action: aprobarDestino) {
                    Label(tituloBotonAprobar, systemImage: iconoBotonAprobar)
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .tint(estado == .unverified ? .gray : .green)
                // `estado != .pending` deshabilita tanto tras un éxito
                // confirmado (nada que repetir) como tras uno sin confirmar
                // (no se sabe si ya salió -- reintentar podría duplicarlo).
                // Ver `SocialApprovalStatus` para el detalle de cada caso.
                .disabled(trabajando || estado != .pending)

                TextField(
                    "Qué quieres cambiar antes de aprobar",
                    text: $cambios,
                    axis: .vertical
                )
                .lineLimit(2...5)
                .padding(12)
                .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 14))

                Button(action: pedirCambios) {
                    Label("Hacer cambios", systemImage: "wand.and.stars")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .disabled(trabajando || cambios.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
    }

    private var tituloBotonAprobar: String {
        if trabajando { return "Publicando…" }
        switch estado {
        case .pending: return "Aprobar"
        case .confirmed: return "Publicado"
        // A propósito NO dice "Publicado": afirmarlo sin haberlo comprobado
        // es justo el error que originó todo esto.
        case .unverified: return "No se pudo confirmar"
        }
    }

    private var iconoBotonAprobar: String {
        switch estado {
        case .pending: "checkmark.circle.fill"
        case .confirmed: "checkmark.seal.fill"
        case .unverified: "questionmark.circle.fill"
        }
    }

    private func crear() {
        iniciar(etapa: "Creando copy e imagen…") {
            let client = try cliente()
            let result = try await ContentStudioService(client: client).create(
                SocialContentRequest(
                    platform: .linkedin,
                    target: target,
                    topic: idea.trimmingCharacters(in: .whitespacesAndNewlines),
                    objective: objetivo,
                    tone: tono,
                    withImage: crearImagen
                )
            )
            draft = result
            textoEditable = result.copy
            cambios = ""
            avisoMensaje = "Borrador listo."
        }
    }

    private func pedirCambios() {
        guard let draft else { return }
        iniciar(etapa: "Aplicando cambios…") {
            let client = try cliente()
            let feedback = cambios.trimmingCharacters(in: .whitespacesAndNewlines)
            let result = try await ContentStudioService(client: client).create(
                SocialContentRequest(
                    platform: .linkedin,
                    target: target,
                    topic: "\(idea)\n\nTexto actual:\n\(draft.copy)\n\nCambios pedidos:\n\(feedback)",
                    objective: objetivo,
                    tone: tono,
                    withImage: crearImagen
                )
            )
            self.draft = result
            textoEditable = result.copy
            cambios = ""
            avisoMensaje = "Cambios aplicados."
        }
    }

    private func aprobarDestino() {
        guard let draft else { return }
        iniciar(etapa: "Publicando en LinkedIn…") {
            let client = try cliente()
            let resultado = try await ContentStudioService(client: client).publishLinkedIn(
                SocialContentPublishRequest(
                    target: target,
                    text: textoEditable,
                    imageFileId: draft.imageArtifact?.fileId,
                    altText: draft.altText,
                    confirmed: true
                )
            )
            estado = SocialApprovalStatus(resultado.verified)
            switch estado {
            case .confirmed:
                avisoMensaje = "Publicado en LinkedIn (\(target.label))."
            case .unverified:
                // Sin `resultado.verificationNote`: trae detalle técnico
                // (códigos HTTP, etc. -- ver `verify_post()` en el conector)
                // que no le sirve a la persona; lo que necesita es saber qué
                // hacer.
                avisoSinConfirmar =
                    "Se envió a LinkedIn (\(target.label)), pero no pudimos " +
                    "confirmar que quedó publicado. Entra a LinkedIn y " +
                    "revísalo tú mismo antes de intentarlo de nuevo."
            case .pending:
                break  // `SocialApprovalStatus.init` nunca produce este caso.
            }
        }
    }

    private func iniciar(
        etapa nuevaEtapa: String,
        operacion: @escaping @MainActor () async throws -> Void
    ) {
        tarea?.cancel()
        trabajando = true
        etapa = nuevaEtapa
        errorMensaje = nil
        avisoMensaje = nil
        avisoSinConfirmar = nil
        // Reset a `.pending` al ARRANCAR cualquier operación (crear borrador,
        // pedir cambios o aprobar): un borrador nuevo o recién editado
        // todavía no se intentó publicar. Cuando esta llamada es
        // `aprobarDestino()`, el botón ya estaba deshabilitado salvo que
        // `estado` fuera `.pending` de entrada (ver `acciones(_:)`), así que
        // este reset no pisa un `.confirmed`/`.unverified` real -- solo
        // confirma lo que ya era cierto antes de empezar.
        estado = .pending
        tarea = Task { @MainActor in
            do {
                try await operacion()
            } catch is CancellationError {
                return
            } catch {
                errorMensaje = error.localizedDescription
            }
            trabajando = false
            etapa = ""
        }
    }

    private func cliente() throws -> APIClient {
        guard let client = session.client else {
            throw APIClient.APIError.sinConexion(detalle: "Este iPhone no está conectado a tu Edecán.")
        }
        return client
    }
}
