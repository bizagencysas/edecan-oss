import SwiftUI
import Observation
import Foundation
import EdecanKit
import UIKit

@MainActor
private func prepareIDEAttachments(
    _ photos: [GaleriaFoto],
    remaining: Int
) -> [IDEAgentAttachment] {
    guard remaining > 0 else { return [] }
    return photos.prefix(remaining).compactMap { photo in
        autoreleasepool {
            guard let image = UIImage(data: photo.datos) else { return nil }
            let maxDimension: CGFloat = 2_048
            let largest = max(image.size.width, image.size.height)
            let scale = largest > maxDimension ? maxDimension / largest : 1
            let target = CGSize(
                width: max(1, image.size.width * scale),
                height: max(1, image.size.height * scale)
            )
            let format = UIGraphicsImageRendererFormat.preferred()
            format.scale = 1
            let normalized = UIGraphicsImageRenderer(size: target, format: format)
                .image { _ in
                    image.draw(in: CGRect(origin: .zero, size: target))
                }
            guard let data = normalized.jpegData(compressionQuality: 0.82),
                  data.count <= 10_000_000
            else { return nil }
            return IDEAgentAttachment(
                name: "vision-\(UUID().uuidString.prefix(8)).jpg",
                mediaType: "image/jpeg",
                data: data.base64EncodedString()
            )
        }
    }
}

// MARK: - Pantalla principal

/// Cliente móvil del IDE remoto.
///
/// La primera pantalla es un historial persistente de trabajos, no un
/// explorador de archivos. Cada conversación agrupa una o más ejecuciones
/// remotas del agente y recompone sus eventos al volver del background.
struct IDERemoteSessionsView: View {
    @Environment(SessionStore.self) private var session
    @Environment(\.scenePhase) private var scenePhase

    @State private var viewModel = IDEConversationListViewModel()
    @State private var mostrandoNuevaSesion = false
    @State private var conversacionSeleccionada: IDEConversationReference?
    @State private var conversacionParaRenombrar: IDEConversationReference?
    @State private var busqueda = ""

    var body: some View {
        NavigationStack {
            Group {
                if viewModel.loading && viewModel.conversations.isEmpty {
                    ProgressView("Conectando con tu estudio…")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if !viewModel.connected {
                    disconnectedView
                } else {
                    sessionsView
                }
            }
            .background(ideBackground)
            .navigationTitle("IDE")
            .navigationBarTitleDisplayMode(.large)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        mostrandoNuevaSesion = true
                    } label: {
                        Image(systemName: "square.and.pencil")
                    }
                    .disabled(!viewModel.connected || viewModel.workspaces.isEmpty)
                    .accessibilityLabel("Nueva sesión")
                }
            }
            .searchable(
                text: $busqueda,
                placement: .navigationBarDrawer(displayMode: .automatic),
                prompt: "Buscar sesiones"
            )
            .task {
                await viewModel.load(client: session.client)
            }
            .onChange(of: scenePhase) { _, phase in
                guard phase == .active else { return }
                Task { await viewModel.load(client: session.client, quietly: true) }
            }
            .refreshable {
                await viewModel.load(client: session.client, quietly: true)
            }
            .sheet(isPresented: $mostrandoNuevaSesion) {
                IDENewConversationSheet(
                    workspaces: viewModel.workspaces
                ) { conversation in
                    viewModel.insert(conversation)
                    conversacionSeleccionada = conversation
                    Task {
                        await viewModel.load(client: session.client, quietly: true)
                    }
                }
            }
            .sheet(item: $conversacionParaRenombrar) { conversation in
                IDERenameConversationSheet(conversation: conversation) { title in
                    viewModel.rename(conversation, to: title)
                }
                .presentationDetents([.height(230)])
            }
            .navigationDestination(item: $conversacionSeleccionada) { conversation in
                IDEConversationDetailView(conversation: conversation)
            }
        }
    }

    private var sessionsView: some View {
        ScrollView {
            LazyVStack(spacing: 18) {
                hero

                if let error = viewModel.errorMessage {
                    IDEErrorBanner(message: error) {
                        viewModel.dismissError()
                    }
                }

                let filtered = filteredConversations
                let active = filtered.filter(viewModel.isActive)
                let recent = filtered.filter { !viewModel.isActive($0) }

                if !active.isEmpty {
                    conversationSection(
                        title: "En curso",
                        icon: "waveform.path.ecg",
                        conversations: active
                    )
                }

                if !recent.isEmpty {
                    conversationSection(
                        title: active.isEmpty ? "Sesiones" : "Recientes",
                        icon: "clock.arrow.circlepath",
                        conversations: recent
                    )
                }

                if filtered.isEmpty {
                    emptyState
                }
            }
            .padding(.horizontal, 18)
            .padding(.bottom, 30)
        }
    }

    private var hero: some View {
        HStack(spacing: 15) {
            Image(systemName: "terminal.fill")
                .font(.system(size: 25, weight: .semibold))
                .foregroundStyle(.white)
                .frame(width: 54, height: 54)
                .background(EdecanTheme.degradado, in: RoundedRectangle(cornerRadius: 17))

            VStack(alignment: .leading, spacing: 4) {
                Text("Tu estudio remoto")
                    .font(.headline)
                Text("Agente, archivos, Terminal y Git continúan en tu computadora.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
            Circle()
                .fill(.green)
                .frame(width: 10, height: 10)
                .accessibilityLabel("Computadora conectada")
        }
        .padding(16)
        .tarjetaVidrio(esquina: 22)
    }

    private func conversationSection(
        title: String,
        icon: String,
        conversations: [IDEConversationReference]
    ) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(title, systemImage: icon)
                .font(.headline)
                .padding(.horizontal, 3)

            ForEach(conversations) { conversation in
                Button {
                    conversacionSeleccionada = conversation
                } label: {
                    IDEConversationCard(
                        conversation: conversation,
                        session: viewModel.latestSession(for: conversation)
                    )
                }
                .buttonStyle(.plain)
                .contextMenu {
                    Button {
                        conversacionParaRenombrar = conversation
                    } label: {
                        Label("Renombrar", systemImage: "pencil")
                    }
                    Button(role: .destructive) {
                        viewModel.remove(conversation)
                    } label: {
                        Label("Quitar del historial", systemImage: "trash")
                    }
                }
            }
        }
    }

    private var emptyState: some View {
        VStack(spacing: 18) {
            Image(systemName: "sparkles.rectangle.stack")
                .font(.system(size: 48, weight: .light))
                .foregroundStyle(EdecanTheme.degradado)
            VStack(spacing: 7) {
                Text(busqueda.isEmpty ? "Crea tu primera sesión" : "No encontramos esa sesión")
                    .font(.title3.bold())
                Text(
                    busqueda.isEmpty
                        ? "Pídele a Edecán que construya, audite o repare un proyecto. Verás cada avance en vivo."
                        : "Prueba con otro nombre o proyecto."
                )
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            }
            if busqueda.isEmpty {
                Button("Nueva sesión") {
                    mostrandoNuevaSesion = true
                }
                .buttonStyle(.borderedProminent)
                .tint(EdecanTheme.morado)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 52)
        .padding(.horizontal, 28)
        .tarjetaVidrio(esquina: 26)
    }

    private var disconnectedView: some View {
        VStack(spacing: 18) {
            Image(systemName: "desktopcomputer.trianglebadge.exclamationmark")
                .font(.system(size: 52, weight: .light))
                .foregroundStyle(EdecanTheme.degradado)
            Text("Tu computadora no está disponible")
                .font(.title2.bold())
            Text("Las sesiones permanecen guardadas. Cuando Edecán vuelva a estar en línea, podrás continuar exactamente donde quedaste.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 30)
            Button("Volver a intentar") {
                Task { await viewModel.load(client: session.client) }
            }
            .buttonStyle(.borderedProminent)
            .tint(EdecanTheme.morado)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding()
    }

    private var filteredConversations: [IDEConversationReference] {
        let query = busqueda.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return viewModel.conversations }
        return viewModel.conversations.filter {
            $0.title.localizedCaseInsensitiveContains(query)
                || $0.workspaceName.localizedCaseInsensitiveContains(query)
        }
    }

    private var ideBackground: some View {
        ZStack {
            Color(uiColor: .systemBackground)
            EdecanTheme.degradado
                .opacity(0.08)
                .blur(radius: 65)
                .offset(y: -240)
        }
        .ignoresSafeArea()
    }
}

// MARK: - Detalle de conversación

private struct IDEConversationDetailView: View {
    @Environment(SessionStore.self) private var session
    @Environment(\.scenePhase) private var scenePhase

    @State private var viewModel: IDEConversationDetailViewModel
    @State private var selectedTool: IDEStudioSection?
    @State private var showingGallery = false

    init(conversation: IDEConversationReference) {
        _viewModel = State(
            initialValue: IDEConversationDetailViewModel(conversation: conversation)
        )
    }

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 18) {
                    conversationHeader

                    if let error = viewModel.errorMessage {
                        IDEErrorBanner(message: error) {
                            viewModel.dismissError()
                        }
                    }

                    if viewModel.loading && viewModel.turns.isEmpty {
                        ProgressView("Recuperando el trabajo…")
                            .padding(.vertical, 70)
                    } else if viewModel.turns.isEmpty {
                        Text("Esta sesión todavía no tiene eventos.")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                            .padding(.vertical, 70)
                    } else {
                        ForEach(viewModel.turns) { turn in
                            IDEConversationTurnView(
                                turn: turn,
                                resolvedMCPCalls: viewModel.resolvedMCPCalls
                            ) { sessionId, callId, approved in
                                Task {
                                    await viewModel.resolveMCP(
                                        client: session.client,
                                        sessionId: sessionId,
                                        callId: callId,
                                        approved: approved
                                    )
                                }
                            }
                        }
                    }

                    Color.clear
                        .frame(height: 1)
                        .id("ide-conversation-bottom")
                }
                .padding(.horizontal, 16)
                .padding(.top, 10)
                .padding(.bottom, 18)
            }
            .onChange(of: viewModel.timelineVersion) { _, _ in
                withAnimation(.easeOut(duration: 0.25)) {
                    proxy.scrollTo("ide-conversation-bottom", anchor: .bottom)
                }
            }
        }
        .background(
            EdecanTheme.degradado
                .opacity(0.055)
                .ignoresSafeArea()
        )
        .navigationTitle(viewModel.conversation.title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                if viewModel.isWorking {
                    ProgressView()
                        .controlSize(.small)
                        .accessibilityLabel("Trabajo en curso")
                } else {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                        .accessibilityLabel("Trabajo actualizado")
                }
            }
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            composer
        }
        .task {
            await viewModel.load(client: session.client)
            viewModel.startPolling(client: session.client)
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                Task {
                    await viewModel.resume(client: session.client)
                    viewModel.startPolling(client: session.client)
                }
            } else {
                viewModel.stopPolling()
            }
        }
        .onDisappear {
            viewModel.stopPolling()
        }
        .refreshable {
            await viewModel.resume(client: session.client)
        }
        .fullScreenCover(item: $selectedTool) { section in
            IDEStudioSheet(section: section)
        }
        .fullScreenCover(isPresented: $showingGallery) {
            GaleriaEdecanPicker(
                limite: max(1, 5 - viewModel.attachments.count)
            ) { photos in
                viewModel.attachments.append(
                    contentsOf: prepareIDEAttachments(
                        photos,
                        remaining: 5 - viewModel.attachments.count
                    )
                )
                showingGallery = false
            }
        }
    }

    private var conversationHeader: some View {
        VStack(spacing: 13) {
            HStack(spacing: 12) {
                Image(systemName: "folder.fill")
                    .foregroundStyle(EdecanTheme.morado)
                    .frame(width: 34, height: 34)
                    .background(EdecanTheme.morado.opacity(0.12), in: RoundedRectangle(cornerRadius: 10))
                VStack(alignment: .leading, spacing: 2) {
                    Text(viewModel.conversation.workspaceName)
                        .font(.subheadline.bold())
                    Text(viewModel.isWorking ? "Edecán está trabajando" : "Listo para continuar")
                        .font(.caption)
                        .foregroundStyle(viewModel.isWorking ? .orange : .secondary)
                }
                Spacer()
                if let updatedAt = viewModel.turns.last?.session.endedAt
                    ?? viewModel.turns.last?.session.startedAt {
                    Text(updatedAt, style: .relative)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }

            HStack(spacing: 9) {
                toolButton("Archivos", icon: "folder", section: .archivos)
                toolButton("Terminal", icon: "terminal", section: .terminal)
                toolButton("Git", icon: "arrow.triangle.branch", section: .git)
            }
        }
        .padding(14)
        .tarjetaVidrio(esquina: 20)
    }

    private func toolButton(
        _ title: String,
        icon: String,
        section: IDEStudioSection
    ) -> some View {
        Button {
            IDELocalStateStore().selectedWorkspaceId = viewModel.conversation.workspaceId
            selectedTool = section
        } label: {
            Label(title, systemImage: icon)
                .font(.caption.bold())
                .frame(maxWidth: .infinity)
                .padding(.vertical, 9)
                .background(
                    Color.secondary.opacity(0.08),
                    in: RoundedRectangle(cornerRadius: 12)
                )
        }
        .buttonStyle(.plain)
    }

    private var composer: some View {
        VStack(spacing: 7) {
            if viewModel.isWorking {
                HStack(spacing: 7) {
                    ProgressView().controlSize(.small)
                    Text("Sigue trabajando en tu computadora. Puedes salir de la app.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }

            if !viewModel.attachments.isEmpty {
                ScrollView(.horizontal) {
                    HStack(spacing: 8) {
                        ForEach(viewModel.attachments) { attachment in
                            IDEAttachmentChip(attachment: attachment) {
                                viewModel.attachments.removeAll {
                                    $0.id == attachment.id
                                }
                            }
                        }
                    }
                }
                .scrollIndicators(.hidden)
            }

            HStack(alignment: .bottom, spacing: 10) {
                Button {
                    showingGallery = true
                } label: {
                    Image(systemName: "plus")
                        .font(.headline)
                        .frame(width: 42, height: 42)
                        .background(
                            Color.secondary.opacity(0.09),
                            in: Circle()
                        )
                }
                .disabled(
                    viewModel.isWorking
                        || viewModel.sending
                        || viewModel.attachments.count >= 5
                )
                .accessibilityLabel("Adjuntar imágenes")

                TextField(
                    viewModel.isWorking ? "Trabajo en curso…" : "Continúa esta sesión…",
                    text: $viewModel.composerText,
                    axis: .vertical
                )
                .lineLimit(1...5)
                .textFieldStyle(.plain)
                .padding(.horizontal, 14)
                .padding(.vertical, 12)
                .background(
                    Color.secondary.opacity(0.08),
                    in: RoundedRectangle(cornerRadius: 18)
                )
                .disabled(viewModel.isWorking || viewModel.sending)

                Button {
                    Task { await viewModel.send(client: session.client) }
                } label: {
                    Group {
                        if viewModel.sending {
                            ProgressView().tint(.white)
                        } else {
                            Image(systemName: "arrow.up")
                                .font(.headline.bold())
                        }
                    }
                    .foregroundStyle(.white)
                    .frame(width: 45, height: 45)
                    .background(EdecanTheme.degradado, in: Circle())
                }
                .disabled(
                    viewModel.isWorking
                        || viewModel.sending
                        || (
                            viewModel.composerText
                                .trimmingCharacters(in: .whitespacesAndNewlines)
                                .isEmpty
                            && viewModel.attachments.isEmpty
                        )
                )
                .opacity(viewModel.isWorking ? 0.45 : 1)
                .accessibilityLabel("Enviar")
            }
        }
        .padding(.horizontal, 14)
        .padding(.top, 10)
        .padding(.bottom, 8)
        .background(.ultraThinMaterial)
    }
}

// MARK: - Crear y renombrar

private struct IDENewConversationSheet: View {
    @Environment(SessionStore.self) private var session
    @Environment(\.dismiss) private var dismiss

    let workspaces: [IDEWorkspace]
    let onCreated: (IDEConversationReference) -> Void

    @State private var prompt = ""
    @State private var workspaceId = ""
    @State private var creating = false
    @State private var errorMessage: String?
    @State private var attachments: [IDEAgentAttachment] = []
    @State private var showingGallery = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("¿Qué quieres construir?")
                            .font(.title2.bold())
                        Text("Edecán lo ejecutará en tu computadora y mostrará cada avance aquí.")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }

                    if let errorMessage {
                        IDEErrorBanner(message: errorMessage) {
                            self.errorMessage = nil
                        }
                    }

                    TextField(
                        "Ejemplo: audita el login y corrige cualquier error",
                        text: $prompt,
                        axis: .vertical
                    )
                    .lineLimit(5...10)
                    .padding(14)
                    .background(
                        Color.secondary.opacity(0.08),
                        in: RoundedRectangle(cornerRadius: 18)
                    )

                    if !attachments.isEmpty {
                        ScrollView(.horizontal) {
                            HStack(spacing: 8) {
                                ForEach(attachments) { attachment in
                                    IDEAttachmentChip(attachment: attachment) {
                                        attachments.removeAll {
                                            $0.id == attachment.id
                                        }
                                    }
                                }
                            }
                        }
                        .scrollIndicators(.hidden)
                    }

                    Button {
                        showingGallery = true
                    } label: {
                        Label(
                            attachments.isEmpty
                                ? "Añadir imágenes"
                                : "Añadir otra imagen",
                            systemImage: "photo.on.rectangle.angled"
                        )
                    }
                    .buttonStyle(.bordered)
                    .disabled(creating || attachments.count >= 5)

                    VStack(alignment: .leading, spacing: 8) {
                        Text("Proyecto")
                            .font(.subheadline.bold())
                        Picker("Proyecto", selection: $workspaceId) {
                            ForEach(workspaces) { workspace in
                                Text(workspace.name).tag(workspace.id)
                            }
                        }
                        .pickerStyle(.menu)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }

                    Button {
                        Task { await create() }
                    } label: {
                        HStack {
                            if creating {
                                ProgressView().tint(.white)
                            } else {
                                Image(systemName: "sparkles")
                            }
                            Text(creating ? "Preparando…" : "Iniciar sesión")
                        }
                        .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(EdecanTheme.morado)
                    .controlSize(.large)
                    .disabled(
                        creating
                            || workspaceId.isEmpty
                            || (
                                prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                                && attachments.isEmpty
                            )
                    )
                }
                .padding(20)
            }
            .background(EdecanTheme.degradado.opacity(0.06).ignoresSafeArea())
            .navigationTitle("Nueva sesión")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancelar") { dismiss() }
                }
            }
            .onAppear {
                if workspaceId.isEmpty {
                    workspaceId = workspaces.first(where: \.active)?.id
                        ?? workspaces.first?.id
                        ?? ""
                }
            }
            .fullScreenCover(isPresented: $showingGallery) {
                GaleriaEdecanPicker(
                    limite: max(1, 5 - attachments.count)
                ) { photos in
                    attachments.append(
                        contentsOf: prepareIDEAttachments(
                            photos,
                            remaining: 5 - attachments.count
                        )
                    )
                    showingGallery = false
                }
            }
        }
    }

    private func create() async {
        guard let client = session.client,
              let workspace = workspaces.first(where: { $0.id == workspaceId })
        else {
            errorMessage = "No hay una computadora o proyecto disponible."
            return
        }
        let request = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !request.isEmpty || !attachments.isEmpty else { return }
        let finalPrompt = request.isEmpty ? "Analiza estas imágenes." : request

        creating = true
        errorMessage = nil
        defer { creating = false }
        do {
            let remote = try await client.ideCreateAgent(
                workspaceId: workspace.id,
                prompt: finalPrompt,
                provider: .workersAI,
                attachments: attachments
            )
            let conversation = IDEConversationLocalStore().append(
                session: remote,
                prompt: finalPrompt
            )
            onCreated(conversation)
            dismiss()
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

private struct IDERenameConversationSheet: View {
    @Environment(\.dismiss) private var dismiss
    let conversation: IDEConversationReference
    let onSave: (String) -> Void
    @State private var title: String

    init(
        conversation: IDEConversationReference,
        onSave: @escaping (String) -> Void
    ) {
        self.conversation = conversation
        self.onSave = onSave
        _title = State(initialValue: conversation.title)
    }

    var body: some View {
        NavigationStack {
            Form {
                TextField("Nombre de la sesión", text: $title)
            }
            .navigationTitle("Renombrar")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancelar") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Guardar") {
                        onSave(title)
                        dismiss()
                    }
                    .disabled(title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
        }
    }
}

// MARK: - Componentes de timeline

private struct IDEAttachmentChip: View {
    let attachment: IDEAgentAttachment
    let onRemove: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            if let data = Data(base64Encoded: attachment.data),
               let image = UIImage(data: data) {
                Image(uiImage: image)
                    .resizable()
                    .scaledToFill()
                    .frame(width: 42, height: 42)
                    .clipShape(RoundedRectangle(cornerRadius: 11))
            } else {
                Image(systemName: "photo")
                    .frame(width: 42, height: 42)
                    .background(
                        EdecanTheme.morado.opacity(0.1),
                        in: RoundedRectangle(cornerRadius: 11)
                    )
            }
            Text("Imagen")
                .font(.caption.bold())
            Button(action: onRemove) {
                Image(systemName: "xmark.circle.fill")
                    .foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Quitar imagen")
        }
        .padding(6)
        .padding(.trailing, 3)
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 15))
    }
}

private struct IDEConversationCard: View {
    let conversation: IDEConversationReference
    let session: IDESession?

    var body: some View {
        HStack(spacing: 13) {
            Image(systemName: session?.isActive == true ? "sparkles" : "chevron.left.forwardslash.chevron.right")
                .font(.system(size: 17, weight: .semibold))
                .foregroundStyle(session?.isActive == true ? .white : EdecanTheme.morado)
                .frame(width: 42, height: 42)
                .background(
                    session?.isActive == true
                        ? AnyShapeStyle(EdecanTheme.degradado)
                        : AnyShapeStyle(EdecanTheme.morado.opacity(0.12)),
                    in: RoundedRectangle(cornerRadius: 13)
                )

            VStack(alignment: .leading, spacing: 4) {
                Text(conversation.title)
                    .font(.subheadline.bold())
                    .foregroundStyle(.primary)
                    .lineLimit(2)
                HStack(spacing: 6) {
                    Text(conversation.workspaceName)
                        .lineLimit(1)
                    Text("·")
                    Text(conversation.updatedAt, style: .relative)
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }
            Spacer(minLength: 4)
            VStack(alignment: .trailing, spacing: 6) {
                if session?.isActive == true {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: "chevron.right")
                        .font(.caption.bold())
                        .foregroundStyle(.tertiary)
                }
                if conversation.sessionIds.count > 1 {
                    Text("\(conversation.sessionIds.count) pasos")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .padding(14)
        .tarjetaVidrio(esquina: 19)
    }
}

private struct IDEConversationTurnView: View {
    let turn: IDEConversationTurn
    let resolvedMCPCalls: [String: Bool]
    let resolveMCP: (String, String, Bool) -> Void

    var body: some View {
        VStack(spacing: 12) {
            HStack {
                Spacer(minLength: 52)
                Text(turn.userText ?? turn.session.title ?? "Solicitud")
                    .font(.subheadline)
                    .foregroundStyle(.white)
                    .textSelection(.enabled)
                    .padding(.horizontal, 15)
                    .padding(.vertical, 11)
                    .background(EdecanTheme.degradado, in: RoundedRectangle(cornerRadius: 19))
            }

            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 8) {
                    Circle()
                        .fill(turn.session.isActive ? .orange : turn.session.status == "completed" ? .green : .secondary)
                        .frame(width: 8, height: 8)
                    Text(statusLabel(turn.session))
                        .font(.caption.bold())
                        .foregroundStyle(.secondary)
                    Spacer()
                    Text(turn.session.startedAt, style: .time)
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }

                if turn.blocks.isEmpty && turn.richBlocks.isEmpty {
                    HStack(spacing: 9) {
                        if turn.session.isActive {
                            ProgressView().controlSize(.small)
                        }
                        Text(turn.session.isActive ? "Preparando el entorno…" : "Sin salida registrada.")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }
                } else {
                    ForEach(turn.blocks) { block in
                        IDEEventBlockView(
                            block: block,
                            resolution: block.mcpCallId.flatMap { resolvedMCPCalls[$0] }
                        ) { approved in
                            guard let callId = block.mcpCallId else { return }
                            resolveMCP(turn.session.id, callId, approved)
                        }
                    }
                    if !turn.richBlocks.isEmpty {
                        BloquesIDEView(bloques: turn.richBlocks)
                    }
                }
            }
            .padding(14)
            .tarjetaVidrio(esquina: 20)
        }
    }

    private func statusLabel(_ session: IDESession) -> String {
        switch session.status {
        case "starting": "Preparando"
        case "running": "Trabajando en vivo"
        case "completed": "Trabajo terminado"
        case "failed": "Necesita atención"
        case "cancelled", "closed": "Sesión detenida"
        case "interrupted": "Interrumpida al reiniciar"
        default: session.status.capitalized
        }
    }
}

private struct IDEEventBlockView: View {
    let block: IDEEventBlock
    let resolution: Bool?
    let resolveMCP: (Bool) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: block.icon)
                    .foregroundStyle(block.color)
                    .frame(width: 19)
                VStack(alignment: .leading, spacing: 4) {
                    Text(block.label)
                        .font(.caption.bold())
                        .foregroundStyle(.secondary)
                    if block.esRespuestaDelAgente {
                        TextoRicoIDE(texto: block.displayText)
                    } else {
                        Text(block.displayText)
                            .font(block.isTechnical ? .system(.caption, design: .monospaced) : .subheadline)
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
            if block.mcpCallId != nil {
                if let resolution {
                    Label(
                        resolution ? "Autorizada para esta ejecución" : "Acción denegada",
                        systemImage: resolution ? "checkmark.shield.fill" : "xmark.shield.fill"
                    )
                    .font(.caption.bold())
                    .foregroundStyle(resolution ? .green : .secondary)
                } else {
                    HStack(spacing: 10) {
                        Button("Permitir una vez") {
                            resolveMCP(true)
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(EdecanTheme.morado)
                        Button("Denegar") {
                            resolveMCP(false)
                        }
                        .buttonStyle(.bordered)
                    }
                    .controlSize(.small)
                }
            }
        }
    }
}

private struct IDEErrorBanner: View {
    let message: String
    let dismiss: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill")
            Text(message)
                .font(.footnote)
                .frame(maxWidth: .infinity, alignment: .leading)
            Button(action: dismiss) {
                Image(systemName: "xmark")
            }
            .buttonStyle(.plain)
        }
        .foregroundStyle(.red)
        .padding(12)
        .background(.red.opacity(0.09), in: RoundedRectangle(cornerRadius: 14))
    }
}

private struct IDEStudioSheet: View {
    @Environment(\.dismiss) private var dismiss
    let section: IDEStudioSection

    var body: some View {
        ZStack(alignment: .topTrailing) {
            IDEView(initialSection: section)
            Button {
                dismiss()
            } label: {
                Image(systemName: "xmark")
                    .font(.headline)
                    .frame(width: 38, height: 38)
                    .background(.ultraThinMaterial, in: Circle())
            }
            .buttonStyle(.plain)
            .padding(.top, 10)
            .padding(.trailing, 12)
            .accessibilityLabel("Cerrar")
        }
    }
}

// MARK: - View models

@MainActor
@Observable
private final class IDEConversationListViewModel {
    private(set) var connected = false
    private(set) var loading = false
    private(set) var workspaces: [IDEWorkspace] = []
    private(set) var sessions: [IDESession] = []
    private(set) var conversations: [IDEConversationReference] = []
    private(set) var errorMessage: String?

    private let store = IDEConversationLocalStore()

    func load(client: APIClient?, quietly: Bool = false) async {
        guard let client else {
            connected = false
            errorMessage = "No hay una sesión activa."
            return
        }
        if !quietly { loading = true }
        defer { loading = false }
        do {
            let status = try await client.ideStatus()
            connected = status.connected
            guard connected else {
                conversations = store.load()
                return
            }
            workspaces = try await client.ideWorkspaces()
            sessions = try await client.ideAgents()
                .sorted { $0.startedAt > $1.startedAt }
            conversations = store.reconcile(remoteSessions: sessions)
            errorMessage = nil
        } catch is CancellationError {
            return
        } catch {
            connected = false
            conversations = store.load()
            errorMessage = error.localizedDescription
        }
    }

    func latestSession(for conversation: IDEConversationReference) -> IDESession? {
        let sessionIds = Set(conversation.sessionIds)
        return sessions
            .filter { sessionIds.contains($0.id) }
            .max { $0.startedAt < $1.startedAt }
    }

    func isActive(_ conversation: IDEConversationReference) -> Bool {
        let sessionIds = Set(conversation.sessionIds)
        return sessions.contains { sessionIds.contains($0.id) && $0.isActive }
    }

    func insert(_ conversation: IDEConversationReference) {
        conversations.removeAll { $0.id == conversation.id }
        conversations.insert(conversation, at: 0)
    }

    func rename(_ conversation: IDEConversationReference, to title: String) {
        store.rename(id: conversation.id, title: title)
        conversations = store.load()
    }

    func remove(_ conversation: IDEConversationReference) {
        store.remove(id: conversation.id)
        conversations.removeAll { $0.id == conversation.id }
    }

    func dismissError() {
        errorMessage = nil
    }
}

private struct IDEConversationTurn: Identifiable {
    let session: IDESession
    let events: [IDESessionEvent]
    let blocks: [IDEEventBlock]
    /// Bloques tipados del IDE (``IDEBlock``) que trajo este turno.
    let richBlocks: [IDEBlock]
    let userText: String?
    var id: String { session.id }
}

/// Evento que transporta bloques ricos del IDE.
///
/// Los bloques viajan en el canal `presentation` del evento (ver
/// ``IDESessionEvent``), nunca en el texto. El `text` de un evento `blocks` es
/// siempre el equivalente en texto de lo dibujado, así que si los bloques no se
/// pueden pintar el evento no se pierde: se lee como cualquier otro.
private let tipoDeBloqueIDE = "blocks"

/// Tope de tarjetas por turno. Más que esto es un volcado, no una respuesta.
private let maxBloquesPorTurno = 12

/// Los bloques que se dibujan y los eventos que quedaron representados por ellos.
///
/// El tope se aplica por EVENTO COMPLETO, y quién lo alcanzó sale de acá junto
/// con los bloques, porque las dos decisiones tienen que venir del mismo corte:
/// la de dibujar y la de ocultar el texto equivalente en ``merge``. Recortando
/// la lista ya aplanada, un evento pasado el tope no se dibujaba Y su texto se
/// ocultaba igual (``merge`` solo miraba si traía `presentation`), así que sus
/// datos desaparecían de la pantalla sin que nada lo dijera.
///
/// Mismo criterio que `bloquesDelTurno` en `apps/web/src/components/ide/AgentThread.tsx`.
private func bloquesRicos(
    en events: [IDESessionEvent]
) -> (bloques: [IDEBlock], representados: Set<Int>) {
    var bloques: [IDEBlock] = []
    var representados: Set<Int> = []
    for event in events where event.type == tipoDeBloqueIDE && !event.presentation.isEmpty {
        // Se corta antes de partir un evento a la mitad: o se dibujan todos sus
        // bloques, o se lee entero como texto.
        if bloques.count + event.presentation.count > maxBloquesPorTurno { break }
        bloques.append(contentsOf: event.presentation)
        representados.insert(event.cursor)
    }
    return (bloques, representados)
}

private struct IDEEventBlock: Identifiable {
    let id: String
    let type: String
    let stream: String?
    let text: String

    var isTechnical: Bool {
        stream == "stderr" || type == "error" || type == "mcp_confirmation"
    }

    /// Solo la respuesta del agente pasa por el render rico. La salida de un
    /// comando es un log: interpretarle Markdown a un `git log` con barras
    /// verticales sería inventarle una tabla que nadie escribió.
    var esRespuestaDelAgente: Bool {
        !isTechnical && (type == "assistant" || type == "assistant_final")
    }

    var label: String {
        if stream == "stderr" || type == "error" { return "Aviso" }
        if type == "mcp_confirmation" { return "Confirmación MCP" }
        if type == "status" { return "Progreso" }
        if type == "exit" { return "Finalizado" }
        return "Edecán"
    }

    var icon: String {
        if stream == "stderr" || type == "error" {
            return "exclamationmark.triangle.fill"
        }
        if type == "mcp_confirmation" { return "externaldrive.badge.questionmark" }
        if type == "exit" { return "checkmark.circle.fill" }
        if type == "status" { return "bolt.fill" }
        return "sparkles"
    }

    var color: Color {
        if stream == "stderr" || type == "error" { return .orange }
        if type == "mcp_confirmation" { return .orange }
        if type == "exit" { return .green }
        return EdecanTheme.morado
    }

    var displayText: String {
        if let request = mcpRequest {
            let arguments = request.arguments
                .map { "\n\($0)" } ?? ""
            return "\(request.name)\(arguments)"
        }
        let clean = text.strippingANSI
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard clean.count > 12_000 else { return clean }
        return "…\n" + clean.suffix(12_000)
    }

    var mcpCallId: String? { mcpRequest?.callId }

    private var mcpRequest: (callId: String, name: String, arguments: String?)? {
        guard type == "mcp_confirmation",
              let data = text.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let callId = object["call_id"] as? String,
              let name = object["name"] as? String
        else { return nil }
        let arguments: String?
        if let raw = object["arguments"],
           JSONSerialization.isValidJSONObject(raw),
           let encoded = try? JSONSerialization.data(
               withJSONObject: raw,
               options: [.prettyPrinted, .sortedKeys]
           ) {
            arguments = String(data: encoded, encoding: .utf8)
        } else {
            arguments = nil
        }
        return (callId, name, arguments)
    }
}

@MainActor
@Observable
private final class IDEConversationDetailViewModel {
    private(set) var conversation: IDEConversationReference
    private(set) var sessions: [IDESession] = []
    private(set) var eventsBySession: [String: [IDESessionEvent]] = [:]
    private(set) var loading = false
    private(set) var sending = false
    private(set) var errorMessage: String?
    private(set) var timelineVersion = 0
    private(set) var resolvedMCPCalls: [String: Bool] = [:]
    var composerText = ""
    var attachments: [IDEAgentAttachment] = []

    private let store = IDEConversationLocalStore()
    private var cursors: [String: Int] = [:]
    private var pollingTask: Task<Void, Never>?

    init(conversation: IDEConversationReference) {
        self.conversation = conversation
    }

    var isWorking: Bool {
        sessions.contains(where: \.isActive)
    }

    var turns: [IDEConversationTurn] {
        let order = Dictionary(
            uniqueKeysWithValues: conversation.sessionIds.enumerated().map { ($1, $0) }
        )
        return sessions
            .sorted {
                (order[$0.id] ?? .max) < (order[$1.id] ?? .max)
            }
            .map { session in
                let events = eventsBySession[session.id] ?? []
                let ricos = bloquesRicos(en: events)
                return IDEConversationTurn(
                    session: session,
                    events: events,
                    blocks: merge(
                        events: events,
                        sessionId: session.id,
                        representados: ricos.representados
                    ),
                    richBlocks: ricos.bloques,
                    userText: events.first(where: { $0.type == "user" })?.text
                )
            }
    }

    func load(client: APIClient?) async {
        guard let client else {
            errorMessage = "No hay una sesión activa."
            return
        }
        loading = true
        defer { loading = false }
        do {
            let remote = try await client.ideAgents()
            let wanted = Set(conversation.sessionIds)
            sessions = remote.filter { wanted.contains($0.id) }
            for session in sessions {
                try await readAllAvailable(
                    client: client,
                    sessionId: session.id,
                    cursor: 0
                )
            }
            errorMessage = nil
        } catch is CancellationError {
            return
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func resume(client: APIClient?) async {
        guard let client else { return }
        do {
            let remote = try await client.ideAgents()
            let wanted = Set(conversation.sessionIds)
            let currentById = Dictionary(uniqueKeysWithValues: sessions.map { ($0.id, $0) })
            sessions = remote
                .filter { wanted.contains($0.id) }
                .map { currentById[$0.id] ?? $0 }
            for session in sessions {
                try await readAllAvailable(
                    client: client,
                    sessionId: session.id,
                    cursor: cursors[session.id] ?? 0
                )
            }
            errorMessage = nil
        } catch is CancellationError {
            return
        } catch {
            errorMessage = "No pudimos actualizar el progreso. Reintentaremos automáticamente."
        }
    }

    func send(client: APIClient?) async {
        guard let client, !sending, !isWorking else { return }
        let request = composerText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !request.isEmpty || !attachments.isEmpty else { return }
        let finalPrompt = request.isEmpty ? "Analiza estas imágenes." : request

        sending = true
        errorMessage = nil
        defer { sending = false }
        do {
            let provider = preferredProvider
            let remote = try await client.ideCreateAgent(
                workspaceId: conversation.workspaceId,
                prompt: finalPrompt,
                provider: provider,
                title: conversation.title,
                conversationId: conversation.id,
                attachments: attachments
            )
            conversation = store.append(
                session: remote,
                prompt: finalPrompt,
                to: conversation.id
            )
            sessions.append(remote)
            eventsBySession[remote.id] = []
            cursors[remote.id] = 0
            composerText = ""
            attachments = []
            timelineVersion += 1
            startPolling(client: client)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func startPolling(client: APIClient?) {
        stopPolling()
        guard let client else { return }
        pollingTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                await self.poll(client: client)
                try? await Task.sleep(for: .seconds(1))
            }
        }
    }

    func stopPolling() {
        pollingTask?.cancel()
        pollingTask = nil
    }

    func dismissError() {
        errorMessage = nil
    }

    func resolveMCP(
        client: APIClient?,
        sessionId: String,
        callId: String,
        approved: Bool
    ) async {
        guard let client, resolvedMCPCalls[callId] == nil else { return }
        resolvedMCPCalls[callId] = approved
        do {
            try await client.ideConfirmAgentMCP(
                sessionId: sessionId,
                callId: callId,
                approved: approved
            )
            errorMessage = nil
        } catch {
            resolvedMCPCalls.removeValue(forKey: callId)
            errorMessage = "No pudimos responder la confirmación MCP."
        }
    }

    private var preferredProvider: IDEAgentProvider {
        guard let raw = sessions.last?.provider,
              let provider = IDEAgentProvider(rawValue: raw)
        else { return .workersAI }
        return provider
    }

    private func poll(client: APIClient) async {
        for session in sessions where session.isActive {
            do {
                try await readAllAvailable(
                    client: client,
                    sessionId: session.id,
                    cursor: cursors[session.id] ?? 0
                )
            } catch is CancellationError {
                return
            } catch {
                // Una interrupción de red no cancela la tarea remota. El
                // siguiente ciclo o `resume` reconstruirá los eventos.
            }
        }
    }

    private func update(_ out: IDESessionReadOut) {
        if let index = sessions.firstIndex(where: { $0.id == out.session.id }) {
            sessions[index] = out.session
        } else {
            sessions.append(out.session)
        }

        var current = eventsBySession[out.session.id] ?? []
        let known = Set(current.map(\.cursor))
        current.append(contentsOf: out.events.filter { !known.contains($0.cursor) })
        current.sort { $0.cursor < $1.cursor }
        eventsBySession[out.session.id] = current
        cursors[out.session.id] = max(cursors[out.session.id] ?? 0, out.nextCursor)
        timelineVersion += 1
    }

    private func readAllAvailable(
        client: APIClient,
        sessionId: String,
        cursor: Int
    ) async throws {
        var next = cursor
        repeat {
            let out = try await client.ideReadAgent(id: sessionId, cursor: next)
            update(out)
            let previous = next
            next = out.nextCursor
            if out.hasMore != true || next <= previous { break }
        } while !Task.isCancelled
    }

    private func merge(
        events: [IDESessionEvent],
        sessionId: String,
        representados: Set<Int>
    ) -> [IDEEventBlock] {
        var blocks: [IDEEventBlock] = []
        for event in events
        where event.type != "user"
            // Un evento `blocks` que SÍ se dibujó no se repite acá con su texto
            // equivalente. Se compara contra lo que de verdad se dibujó: si no
            // se pudo —malformado o pasado el tope del turno— su texto tiene que
            // seguir viéndose, para que la persona lea lo que había en vez de nada.
            && !(event.type == tipoDeBloqueIDE && representados.contains(event.cursor))
            && !event.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            if let last = blocks.last,
               last.type == event.type,
               last.stream == event.stream,
               ["output", "assistant"].contains(event.type) {
                blocks[blocks.count - 1] = IDEEventBlock(
                    id: last.id,
                    type: last.type,
                    stream: last.stream,
                    text: last.text + event.text
                )
            } else {
                blocks.append(
                    IDEEventBlock(
                        id: "\(sessionId)-\(event.cursor)",
                        type: event.type,
                        stream: event.stream,
                        text: event.text
                    )
                )
            }
        }
        return blocks
    }
}

private extension String {
    var nilIfEmpty: String? { isEmpty ? nil : self }

    var strippingANSI: String {
        replacingOccurrences(
            of: "\u{001B}\\[[0-9;?]*[ -/]*[@-~]",
            with: "",
            options: .regularExpression
        )
    }
}
