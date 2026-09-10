import SwiftUI
import EdecanKit

/// Hoja Liquid Glass: skills + MCP reales (salud/auth/tools) en el chat 1:1.
struct BotConectoresSheet: View {
    @Environment(SessionStore.self) private var session
    let nombreBot: String
    let onPedirAlBot: (String) -> Void

    @State private var skills: [SkillSummary] = []
    @State private var servers: [MCPServerSummary] = []
    @State private var mcpHealth: MCPHealthSummary?
    @State private var toolsPorServidor: [String: [MCPToolSummary]] = [:]
    @State private var expandido: String?
    @State private var loading = true
    @State private var cargandoTools: String?
    @State private var skillsError: String?
    @State private var mcpError: String?
    @State private var mostrarRegistrarMCP = false
    @State private var servidorAEliminar: String?
    @State private var eliminandoMCP = false
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Text("Instalar/quitar MCP: formulario PUT/DELETE del Kit. Auth conversacional: pide a \(nombreBot) con `conectar_mcp` cuando diga Auth requerida.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .listRowBackground(Color.clear)
                }

                SocialConnectorsSection(presentation: .listSection(compact: true))

                Section("Habilidades") {
                    if let skillsError {
                        VStack(alignment: .leading, spacing: 8) {
                            Text(skillsError).foregroundStyle(.secondary)
                            Button("Reintentar") { Task { await cargar() } }
                                .font(.footnote.weight(.semibold))
                        }
                    } else if skills.isEmpty && !loading {
                        Text("Sin skills instaladas.").foregroundStyle(.secondary)
                    } else {
                        ForEach(skills) { skill in
                            Button {
                                Haptico.ligero()
                                onPedirAlBot(
                                    "Usa o explica la skill «\(skill.nombre)» (\(skill.enabled ? "activa" : "pausada")). Si hace falta activarla, hazlo."
                                )
                                dismiss()
                            } label: {
                                HStack(spacing: 12) {
                                    Image(systemName: skill.enabled ? "checkmark.seal.fill" : "pause.circle")
                                        .foregroundStyle(skill.enabled ? Color.green : Color.secondary)
                                    VStack(alignment: .leading, spacing: 3) {
                                        Text(skill.nombre).font(.subheadline.weight(.semibold))
                                            .foregroundStyle(.primary)
                                        Text(skill.descripcion)
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                            .lineLimit(2)
                                    }
                                    Spacer()
                                    Text(skill.enabled ? "Activa" : "Pausada")
                                        .font(.caption2.weight(.semibold))
                                        .foregroundStyle(skill.enabled ? Color.green : .secondary)
                                }
                            }
                        }
                    }
                }

                Section("Conectores MCP") {
                    if let mcpHealth {
                        MCPHealthBanner(health: mcpHealth)
                            .listRowBackground(Color.clear)
                    }
                    if let mcpError {
                        VStack(alignment: .leading, spacing: 8) {
                            Text(mcpError).foregroundStyle(.secondary)
                            Button("Reintentar") { Task { await cargar() } }
                                .font(.footnote.weight(.semibold))
                        }
                    } else if servers.isEmpty && !loading {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Ningún MCP configurado todavía (lista vacía del servidor, no un placeholder).")
                                .foregroundStyle(.secondary)
                            Button("Pedir a \(nombreBot) que conecte un MCP") {
                                onPedirAlBot(
                                    "No hay MCP configurados. Guíame para conectar uno con conectar_mcp (URL HTTP o comando local) y valida el handshake."
                                )
                                dismiss()
                            }
                        }
                    } else {
                        ForEach(servers) { server in
                            VStack(alignment: .leading, spacing: 10) {
                                Button {
                                    Haptico.ligero()
                                    withAnimation {
                                        expandido = expandido == server.nombre ? nil : server.nombre
                                    }
                                    if expandido == server.nombre {
                                        Task { await cargarTools(server.nombre) }
                                    }
                                } label: {
                                    HStack(spacing: 12) {
                                        Image(systemName: server.transporte == "http" ? "network" : "cable.connector")
                                            .foregroundStyle(EdecanTheme.morado)
                                            .frame(width: 28, height: 28)
                                            .background(EdecanTheme.morado.opacity(0.12), in: Circle())
                                        VStack(alignment: .leading, spacing: 2) {
                                            Text(server.nombre)
                                                .font(.subheadline.weight(.semibold))
                                                .foregroundStyle(.primary)
                                            Text(subtitulo(server))
                                                .font(.caption)
                                                .foregroundStyle(.secondary)
                                                .lineLimit(2)
                                        }
                                        Spacer()
                                        Text(server.etiquetaSalud)
                                            .font(.caption2.weight(.bold))
                                            .foregroundStyle(colorSalud(server))
                                            .padding(.horizontal, 8)
                                            .padding(.vertical, 3)
                                            .background(colorSalud(server).opacity(0.14), in: Capsule())
                                    }
                                }
                                .buttonStyle(.plain)

                                if server.necesitaAuth {
                                    Button {
                                        Haptico.ligero()
                                        onPedirAlBot(promptAuth(server))
                                        dismiss()
                                    } label: {
                                        Label("Configurar auth con \(nombreBot)", systemImage: "key.fill")
                                            .font(.caption.weight(.semibold))
                                            .frame(maxWidth: .infinity)
                                            .padding(.vertical, 8)
                                            .foregroundStyle(.white)
                                            .background(EdecanTheme.morado, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                                    }
                                    .buttonStyle(.plain)
                                } else {
                                    HStack(spacing: 8) {
                                        Button {
                                            onPedirAlBot(
                                                "Revisa el MCP «\(server.nombre)» (salud: \(server.etiquetaSalud)). Si algo falla, reconéctalo o lista sus tools."
                                            )
                                            dismiss()
                                        } label: {
                                            Text("Pedir revisión")
                                                .font(.caption.weight(.semibold))
                                                .frame(maxWidth: .infinity)
                                                .padding(.vertical, 8)
                                                .foregroundStyle(EdecanTheme.morado)
                                                .background(EdecanTheme.morado.opacity(0.12), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                                        }
                                        .buttonStyle(.plain)

                                        Button {
                                            servidorAEliminar = server.nombre
                                        } label: {
                                            Text(eliminandoMCP && servidorAEliminar == server.nombre ? "Quitando…" : "Quitar del VPS")
                                                .font(.caption.weight(.semibold))
                                                .frame(maxWidth: .infinity)
                                                .padding(.vertical, 8)
                                                .foregroundStyle(.red)
                                                .background(Color.red.opacity(0.1), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                                        }
                                        .buttonStyle(.plain)
                                        .disabled(eliminandoMCP)
                                    }
                                }

                                if expandido == server.nombre {
                                    if cargandoTools == server.nombre {
                                        ProgressView().padding(.vertical, 4)
                                    } else if let tools = toolsPorServidor[server.nombre] {
                                        if tools.isEmpty {
                                            Text("Sin tools expuestas (handshake OK o vacío).")
                                                .font(.caption2)
                                                .foregroundStyle(.secondary)
                                        } else {
                                            ForEach(tools.prefix(12)) { tool in
                                                VStack(alignment: .leading, spacing: 2) {
                                                    Text(tool.name)
                                                        .font(.caption.weight(.semibold))
                                                        .monospaced()
                                                    if !tool.description.isEmpty {
                                                        Text(tool.description)
                                                            .font(.caption2)
                                                            .foregroundStyle(.secondary)
                                                            .lineLimit(2)
                                                    }
                                                }
                                                .padding(.vertical, 2)
                                            }
                                            if tools.count > 12 {
                                                Text("+\(tools.count - 12) tools más")
                                                    .font(.caption2)
                                                    .foregroundStyle(.secondary)
                                            }
                                        }
                                    }
                                }

                                if let err = server.lastError, !err.isEmpty {
                                    Text(err)
                                        .font(.caption2)
                                        .foregroundStyle(.orange)
                                        .lineLimit(3)
                                }
                            }
                            .padding(.vertical, 4)
                        }
                    }
                    Button {
                        mostrarRegistrarMCP = true
                    } label: {
                        Label("Registrar MCP en el VPS", systemImage: "plus.circle")
                    }
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle("Conectores")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cerrar") { dismiss() }
                }
            }
            .overlay { if loading { ProgressView() } }
            .task { await cargar() }
            .refreshable { await cargar() }
            .sheet(isPresented: $mostrarRegistrarMCP) {
                RegistrarMCPSheet {
                    Task { await cargar() }
                }
                .environment(session)
            }
            .confirmationDialog(
                "¿Quitar «\(servidorAEliminar ?? "")» del VPS?",
                isPresented: Binding(
                    get: { servidorAEliminar != nil },
                    set: { if !$0 { servidorAEliminar = nil } }
                ),
                titleVisibility: .visible
            ) {
                Button("Quitar del VPS", role: .destructive) {
                    guard let nombre = servidorAEliminar else { return }
                    Task { await eliminarServidor(nombre) }
                }
                Button("Cancelar", role: .cancel) { servidorAEliminar = nil }
            } message: {
                Text("Elimina la configuración en el servidor. \(nombreBot) sigue pudiendo reconectar con conectar_mcp si lo necesitas.")
            }
        }
        .presentationDetents([.medium, .large])
    }

    private func subtitulo(_ server: MCPServerSummary) -> String {
        var partes: [String] = [
            server.transporte == "http" ? "HTTP" : "stdio",
        ]
        if let url = server.url, !url.isEmpty {
            partes.append(url)
        } else if let cmd = server.comando, !cmd.isEmpty {
            partes.append(cmd)
        }
        if server.autenticacionConfigurada {
            partes.append("auth OK")
        }
        if let ms = server.latencyMs {
            partes.append("\(ms) ms")
        }
        return partes.joined(separator: " · ")
    }

    private func colorSalud(_ server: MCPServerSummary) -> Color {
        switch server.colorSalud {
        case "green": return .green
        case "orange": return .orange
        case "red": return .red
        case "purple": return EdecanTheme.morado
        default: return .secondary
        }
    }

    private func promptAuth(_ server: MCPServerSummary) -> String {
        """
        El MCP «\(server.nombre)» necesita autenticación (salud: \(server.etiquetaSalud)\
        \(server.lastError.map { "; error: \($0)" } ?? "")). \
        Usa conectar_mcp / actualiza headers o env con las credenciales que te dé. \
        No inventes tokens: guíame paso a paso.
        """
    }

    private func cargar() async {
        guard let client = session.client else {
            loading = false
            return
        }
        loading = true
        skillsError = nil
        mcpError = nil
        do { skills = try await client.listSkills() }
        catch { skillsError = "No se pudieron cargar las skills." }
        // Secuencial: `withTaskGroup` mutando estado de la vista golpea una
        // limitación del region-based isolation checker de Swift 6.
        do {
            servers = try await client.listMCPServers()
        } catch APIClient.APIError.servidor(let status, _) where status == 403 {
            mcpError = "MCP no habilitado en este plan (403 real del API)."
            servers = []
        } catch APIClient.APIError.servidor(let status, _) where status == 404 {
            mcpError = "El servidor aún no expone /v1/mcp/servers."
            servers = []
        } catch {
            mcpError = "No se pudieron cargar los conectores MCP."
            servers = []
        }
        do {
            mcpHealth = try await client.getMCPHealth()
        } catch {
            mcpHealth = nil
        }
        loading = false
    }

    private func eliminarServidor(_ nombre: String) async {
        guard let client = session.client else { return }
        eliminandoMCP = true
        defer {
            eliminandoMCP = false
            servidorAEliminar = nil
        }
        do {
            try await client.deleteMCPServer(nombre: nombre)
            Haptico.exito()
            toolsPorServidor.removeValue(forKey: nombre)
            if expandido == nombre { expandido = nil }
            await cargar()
        } catch {
            mcpError = "No se pudo quitar «\(nombre)» del VPS."
        }
    }

    private func cargarTools(_ nombre: String) async {
        guard let client = session.client else { return }
        if toolsPorServidor[nombre] != nil { return }
        cargandoTools = nombre
        defer { cargandoTools = nil }
        do {
            toolsPorServidor[nombre] = try await client.listMCPServerTools(nombre: nombre)
        } catch {
            toolsPorServidor[nombre] = []
        }
    }
}

/// Community: drafts del hilo + approvals durables filtradas por worker_id.
struct BotCommunitySheet: View {
    @Environment(SessionStore.self) private var session
    let workerId: String
    let nombreBot: String
    var draftsEnChat: [SocialDraftBlock] = []
    let onPedirAlBot: (String) -> Void

    @State private var approvals: [PendingApproval] = []
    @State private var loading = true
    @State private var error: String?
    @State private var ocupado: String?
    @State private var aviso: String?
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Text("Aprobaciones con `worker_id=\(String(workerId.prefix(8)))…`. Drafts = cards ya creadas en este chat.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .listRowBackground(Color.clear)
                }

                if let aviso {
                    Section {
                        Text(aviso).font(.footnote).foregroundStyle(.green)
                    }
                }

                SocialConnectorsSection(presentation: .listSection(compact: true))

                Section {
                    Text("Community manager publica vía approvals durables en el VPS; no hace falta companion Mac.")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .listRowBackground(Color.clear)
                }

                Section("Drafts en este chat") {
                    if draftsEnChat.isEmpty {
                        Text("Todavía no hay borradores sociales en el hilo.")
                            .foregroundStyle(.secondary)
                        Button {
                            onPedirAlBot(
                                "Crea un borrador social (LinkedIn o X) con card de preview para que yo lo apruebe aquí."
                            )
                            dismiss()
                        } label: {
                            Label("Pedir draft con preview", systemImage: "square.text.square")
                        }
                    } else {
                        ForEach(Array(draftsEnChat.enumerated()), id: \.offset) { _, bloque in
                            SocialDraftCardView(
                                bloque: bloque,
                                client: session.client,
                                onAction: { _ in }
                            )
                            .listRowInsets(EdgeInsets(top: 8, leading: 12, bottom: 8, trailing: 12))
                        }
                    }
                }

                Section("Aprobaciones pendientes") {
                    if let error {
                        VStack(alignment: .leading, spacing: 8) {
                            Text(error).foregroundStyle(.secondary)
                            Button("Reintentar") { Task { await cargar() } }
                                .font(.footnote.weight(.semibold))
                        }
                    } else if approvals.isEmpty && !loading {
                        Text("Nada pendiente de aprobar para este bot.")
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(approvals) { ap in
                            VStack(alignment: .leading, spacing: 8) {
                                HStack {
                                    Text(ap.name ?? "Acción externa")
                                        .font(.subheadline.weight(.semibold))
                                    Spacer()
                                    if let wid = ap.workerId, !wid.isEmpty {
                                        Text("bot")
                                            .font(.caption2.weight(.bold))
                                            .foregroundStyle(EdecanTheme.morado)
                                            .padding(.horizontal, 6)
                                            .padding(.vertical, 2)
                                            .background(EdecanTheme.morado.opacity(0.12), in: Capsule())
                                    }
                                }
                                if let preview = previewArgs(ap) {
                                    Text(preview)
                                        .font(.caption)
                                        .foregroundStyle(.primary)
                                        .padding(8)
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                        .background(Color.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                                        .lineLimit(8)
                                } else if !ap.argsPreview.isEmpty {
                                    Text(ap.argsPreview)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                        .lineLimit(4)
                                }
                                HStack(spacing: 10) {
                                    Button {
                                        Task { await decidir(ap, aprobar: true) }
                                    } label: {
                                        Text("Aprobar")
                                            .font(.caption.weight(.bold))
                                            .frame(maxWidth: .infinity)
                                    }
                                    .buttonStyle(.borderedProminent)
                                    .tint(EdecanTheme.morado)
                                    .disabled(ocupado != nil)

                                    Button(role: .destructive) {
                                        Task { await decidir(ap, aprobar: false) }
                                    } label: {
                                        Text("Rechazar")
                                            .font(.caption.weight(.semibold))
                                            .frame(maxWidth: .infinity)
                                    }
                                    .buttonStyle(.bordered)
                                    .disabled(ocupado != nil)
                                }
                            }
                            .padding(.vertical, 4)
                        }
                    }
                }

                Section("Cola") {
                    Button {
                        onPedirAlBot(
                            "Resume la cola de community manager: drafts listos, posts programados y lo que necesita mi OK."
                        )
                        dismiss()
                    } label: {
                        Label("Pedir cola de posts", systemImage: "calendar.badge.clock")
                    }
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle("Community")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cerrar") { dismiss() }
                }
            }
            .overlay { if loading { ProgressView() } }
            .task { await cargar() }
            .refreshable { await cargar() }
        }
        .presentationDetents([.medium, .large])
    }

    private func previewArgs(_ ap: PendingApproval) -> String? {
        guard let args = ap.args else { return nil }
        for key in ["copy", "text", "body", "caption", "mensaje", "content"] {
            guard let valor = args[key] else { continue }
            if case .string(let s) = valor, !s.isEmpty {
                return s
            }
        }
        return nil
    }

    private func cargar() async {
        guard let client = session.client else {
            loading = false
            return
        }
        loading = true
        error = nil
        defer { loading = false }
        do {
            var lista = try await client.listApprovals(workerId: workerId)
            // Defensa: si el filtro del server falla/vacío raro, no mezclar otros bots.
            lista = lista.filter { ap in
                guard let wid = ap.workerId, !wid.isEmpty else { return true }
                return wid == workerId
            }
            approvals = lista
        } catch {
            self.error = "No se pudieron cargar las aprobaciones (worker_id)."
        }
    }

    private func decidir(_ ap: PendingApproval, aprobar: Bool) async {
        guard let client = session.client else { return }
        if let wid = ap.workerId, !wid.isEmpty, wid != workerId {
            error = "Esta aprobación es de otro bot (worker_id no coincide)."
            return
        }
        ocupado = ap.id
        defer { ocupado = nil }
        do {
            if aprobar {
                try await client.approveApproval(id: ap.id)
                aviso = "Aprobado — \(nombreBot) puede reanudar."
            } else {
                try await client.denyApproval(id: ap.id)
                aviso = "Rechazado."
            }
            Haptico.exito()
            await cargar()
        } catch {
            self.error = aprobar ? "No se pudo aprobar." : "No se pudo rechazar."
        }
    }
}

/// Ver pantalla: VPS = default (casa). Mac companion opcional.
struct BotPantallaSheet: View {
    @Environment(SessionStore.self) private var session
    @Environment(\.dismiss) private var dismiss

    @State private var maquinas: [RemoteMachine] = []
    @State private var cargando = true
    @State private var errorCarga: String?

    private var companions: [RemoteMachine] {
        maquinas.filter { !$0.esLocal }
    }
    private var locales: [RemoteMachine] {
        maquinas.filter(\.esLocal)
    }
    private var companionOnline: Bool {
        companions.contains(where: \.connected)
    }
    private var vpsDisponible: Bool {
        locales.contains(where: \.connected) || !locales.isEmpty
    }

    var body: some View {
        NavigationStack {
            List {
                Section {
                    if cargando {
                        ProgressView("Consultando máquinas…")
                    } else if let errorCarga {
                        VStack(alignment: .leading, spacing: 8) {
                            Text(errorCarga)
                                .font(.footnote)
                                .foregroundStyle(.orange)
                            Button("Reintentar") {
                                Task { await cargar() }
                            }
                            .font(.footnote.weight(.semibold))
                        }
                    } else {
                        Text(resumenEstado)
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }
                .listRowBackground(Color.clear)

                Section("Casa = VPS (default)") {
                    if locales.isEmpty && !cargando {
                        Text("El runtime del servidor no listó máquinas locales; Remoto igual puede abrir el box del VPS.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(locales) { m in
                            filaMaquina(m)
                        }
                    }
                    NavigationLink {
                        RemotoView()
                    } label: {
                        Label("Ver pantalla del VPS", systemImage: "cloud.fill")
                    }
                    NavigationLink {
                        ComputerView()
                    } label: {
                        Label("Sesiones de computadora (VPS)", systemImage: "desktopcomputer")
                    }
                }

                Section("Mac companion (opcional)") {
                    if companions.isEmpty {
                        Text("Ningún companion Mac conectado. No hace falta para OAuth ni community: eso vive en el VPS.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(companions) { m in
                            filaMaquina(m)
                        }
                        if companionOnline {
                            NavigationLink {
                                RemotoView()
                            } label: {
                                Label("Ver Mac companion", systemImage: "laptopcomputer")
                            }
                        }
                    }
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle("Ver Mac")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cerrar") { dismiss() }
                }
            }
            .task { await cargar() }
            .refreshable { await cargar() }
        }
        .presentationDetents([.medium, .large])
    }

    private var resumenEstado: String {
        if vpsDisponible {
            if companionOnline {
                return "VPS en línea (casa). Mac companion también online — opcional."
            }
            return "VPS en línea (casa). Mac companion opcional y ahora offline — OAuth/community no la necesitan."
        }
        if companionOnline {
            return "Solo ves companion Mac; el VPS no listó runtime local. Sigue pudiendo abrir Remoto."
        }
        if maquinas.isEmpty {
            return "Sin máquinas listadas. Default honesto: usa Remoto hacia el VPS/box del servidor. Mac no es requisito."
        }
        return "Máquinas registradas sin WS activo. Prueba Remoto al VPS antes de asumir caída total."
    }

    private func filaMaquina(_ m: RemoteMachine) -> some View {
        HStack(spacing: 12) {
            Image(systemName: m.esLocal ? "cloud.fill" : "laptopcomputer")
                .foregroundStyle(EdecanTheme.morado)
            VStack(alignment: .leading, spacing: 2) {
                Text(m.label)
                    .font(.subheadline.weight(.semibold))
                Text(m.esLocal ? "Runtime local / VPS" : "Companion remoto")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Text(m.connected ? "En línea" : "Sin WS")
                .font(.caption2.weight(.bold))
                .foregroundStyle(m.connected ? EdecanTheme.morado : .secondary)
        }
    }

    private func cargar() async {
        guard let client = session.client else {
            cargando = false
            errorCarga = "Sin sesión."
            return
        }
        cargando = true
        errorCarga = nil
        defer { cargando = false }
        do {
            maquinas = try await client.listRemoteMachines()
        } catch {
            errorCarga = "No pude listar máquinas; Remoto igual puede abrir el fallback del servidor."
            maquinas = []
        }
    }
}
