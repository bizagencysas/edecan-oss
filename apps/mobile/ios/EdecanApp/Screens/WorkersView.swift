import SwiftUI
import EdecanKit

/// "Equipo" como roster de compañeros persistentes (workers) sobre
/// `GET /v1/agents/workers`. Cada compañero se arma con un perfil rico
/// (oficio, personalidad, instrucciones, autonomía) y se le encola trabajo
/// real vía `POST /v1/agents/workers/{id}/tasks`; no hay chat fingido.
struct WorkersView: View {
    @Environment(SessionStore.self) private var session
    @Environment(TabRouter.self) private var router
    @State private var workers: [PersistentWorker] = []
    @State private var handoffs: [WorkerHandoff] = []
    @State private var aprobaciones: [PendingApproval] = []
    @State private var busqueda = ""
    @State private var error: String?
    @State private var cargando = true
    @State private var ocupado = false
    @State private var creando: OficioEquipo?
    @State private var detalle: PersistentWorker?

    var body: some View {
        List {
            if let error {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.red)
                    .listRowSeparator(.hidden)
            }

            Section {
                NavigationLink {
                    ActivityView()
                } label: {
                    HStack(spacing: 12) {
                        Image(systemName: "list.bullet.rectangle")
                            .foregroundStyle(EdecanTheme.morado)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Registro de actividad")
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(.primary)
                            Text("Lo que han hecho los compañeros últimamente.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                NavigationLink {
                    AgentMessagesView()
                } label: {
                    HStack(spacing: 12) {
                        Image(systemName: "bubble.left.and.bubble.right.fill")
                            .foregroundStyle(EdecanTheme.morado)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Mensajes")
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(.primary)
                            Text("Lo que se dicen los compañeros entre sí.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }

            if !handoffs.isEmpty {
                Section("Necesitan tu OK") {
                    ForEach(handoffs) { handoff in
                        HStack(alignment: .top, spacing: 12) {
                            Circle()
                                .fill(.orange)
                                .frame(width: 8, height: 8)
                                .padding(.top, 6)
                            VStack(alignment: .leading, spacing: 4) {
                                Text(handoff.destinationName ?? "Compañero")
                                    .font(.subheadline.weight(.semibold))
                                Text(handoff.instruction ?? "Hay un paso esperando aprobación.")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(3)
                            }
                            Spacer(minLength: 8)
                            Button("Aprobar") {
                                Task { await aprobar(handoff.id) }
                            }
                            .buttonStyle(.borderedProminent)
                            .tint(EdecanTheme.morado)
                            .disabled(ocupado)
                        }
                    }
                }
            }

            if !aprobaciones.isEmpty {
                Section("Aprobaciones pendientes") {
                    ForEach(aprobaciones) { aprobacion in
                        FilaAprobacion(aprobacion: aprobacion, ocupado: ocupado) { aprobar in
                            Task { await decidirAprobacion(aprobacion, aprobar: aprobar) }
                        }
                    }
                }
            }

            Section {
                ForEach(visibles) { worker in
                    Button {
                        detalle = worker
                    } label: {
                        FilaWorker(worker: worker)
                    }
                    .foregroundStyle(.primary)
                }
                if !cargando && visibles.isEmpty {
                    if workers.isEmpty {
                        VStack(alignment: .leading, spacing: 10) {
                            Text("Crea tu primer compañero")
                                .font(.subheadline.weight(.semibold))
                            Text("Dale un oficio y encárgale trabajo; te avisa si necesita tu OK.")
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                            Button {
                                creando = .enBlanco
                            } label: {
                                Label("Crear compañero", systemImage: "person.badge.plus")
                            }
                            .buttonStyle(.bordered)
                            .tint(EdecanTheme.morado)
                        }
                        .padding(.vertical, 6)
                    } else {
                        Text("Nada coincide con “\(busqueda)”.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }
            } header: {
                Text("Compañeros")
            }

            Section("Oficios para empezar") {
                ForEach(OficioEquipo.catalogo) { oficio in
                    Button {
                        creando = oficio
                    } label: {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(oficio.nombre).font(.subheadline.weight(.semibold))
                            Text(oficio.resumen)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }

            Section {
                NavigationLink {
                    TeachTaskView()
                } label: {
                    HStack(spacing: 12) {
                        Image(systemName: "graduationcap.fill")
                            .foregroundStyle(EdecanTheme.morado)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Enseñar una tarea")
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(.primary)
                            Text("Captura los pasos de algo que repites y guárdalo como una habilidad.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            } header: {
                Text("Enseñar")
            }
        }
        .listStyle(.insetGrouped)
        .searchable(text: $busqueda, prompt: "Buscar compañeros")
        .navigationTitle("Compañeros")
        .navigationBarTitleDisplayMode(.large)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    creando = .enBlanco
                } label: {
                    Label("Nuevo compañero", systemImage: "person.badge.plus")
                }
            }
            ToolbarItem(placement: .topBarLeading) {
                Button {
                    router.mostrarRemoto()
                } label: {
                    Label("Computadora", systemImage: "desktopcomputer")
                }
            }
        }
        .sheet(item: $detalle) { worker in
            PerfilWorkerSheet(worker: worker) { actualizado in
                if let indice = workers.firstIndex(where: { $0.id == actualizado.id }) {
                    workers[indice] = actualizado
                }
            }
        }
        .sheet(item: $creando) { oficio in
            NavigationStack {
                NuevoCompaneroSheet(oficio: oficio) { borrador in
                    Task { await crear(borrador) }
                }
            }
        }
        .overlay {
            if cargando && workers.isEmpty {
                ProgressView()
            }
        }
        .task { await cargar() }
        .refreshable { await cargar() }
    }

    private var visibles: [PersistentWorker] {
        let q = busqueda.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !q.isEmpty else { return workers }
        return workers.filter {
            $0.nombreVisible.lowercased().contains(q)
            || $0.cargoVisible.lowercased().contains(q)
            || $0.purpose.lowercased().contains(q)
            || ($0.jobDescription?.lowercased().contains(q) ?? false)
        }
    }

    private func cargar() async {
        guard let client = session.client else {
            error = "No hay sesión activa."
            cargando = false
            return
        }
        cargando = workers.isEmpty
        error = nil
        defer { cargando = false }
        do {
            workers = try await client.listWorkers()
            handoffs = (try? await client.listWorkerHandoffs()) ?? []
            aprobaciones = (try? await client.listApprovals()) ?? []
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func crear(_ borrador: BorradorWorker) async {
        guard let client = session.client else { return }
        ocupado = true
        defer { ocupado = false }
        do {
            _ = try await client.createWorker(
                name: borrador.nombre,
                purpose: borrador.proposito,
                displayName: borrador.nombreVisible,
                avatarAccentHex: borrador.acentoHex,
                roleTitle: borrador.cargo,
                jobDescription: borrador.descripcion,
                autonomyLevel: borrador.autonomia.rawValue
            )
            creando = nil
            await cargar()
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func aprobar(_ id: String) async {
        guard let client = session.client else { return }
        ocupado = true
        defer { ocupado = false }
        do {
            try await client.approveWorkerHandoff(id: id)
            await cargar()
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func decidirAprobacion(_ aprobacion: PendingApproval, aprobar: Bool) async {
        guard let client = session.client else { return }
        ocupado = true
        defer { ocupado = false }
        do {
            if aprobar {
                try await client.approveApproval(id: aprobacion.id)
            } else {
                try await client.denyApproval(id: aprobacion.id)
            }
            await cargar()
        } catch {
            self.error = error.localizedDescription
        }
    }
}

// MARK: - Fila del roster

private struct FilaWorker: View {
    let worker: PersistentWorker

    var body: some View {
        HStack(spacing: 12) {
            AvatarWorker(worker: worker)
            VStack(alignment: .leading, spacing: 3) {
                Text(worker.nombreVisible)
                    .font(.subheadline.weight(.semibold))
                Text(worker.cargoVisible)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                FilaEstadoWorker(estado: estado)
            }
            Spacer(minLength: 8)
        }
        .padding(.vertical, 4)
    }

    private var estado: EstadoWorker {
        EstadoWorker.de(worker)
    }
}

// MARK: - Aprobaciones del chat

/// Reutilizada por ``SeguridadView`` — por eso no es `private`.
struct FilaAprobacion: View {
    let aprobacion: PendingApproval
    let ocupado: Bool
    let onDecidir: (Bool) -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Circle()
                .fill(.orange)
                .frame(width: 8, height: 8)
                .padding(.top, 6)
            VStack(alignment: .leading, spacing: 4) {
                Text(aprobacion.name ?? "Herramienta")
                    .font(.subheadline.weight(.semibold))
                if !aprobacion.argsPreview.isEmpty {
                    Text(aprobacion.argsPreview)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(3)
                }
                HStack(spacing: 8) {
                    Button("Aprobar") { onDecidir(true) }
                        .buttonStyle(.borderedProminent)
                        .tint(EdecanTheme.morado)
                        .controlSize(.small)
                    Button("Denegar") { onDecidir(false) }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                }
                .disabled(ocupado)
            }
            Spacer(minLength: 8)
        }
    }
}

// MARK: - Avatar

private struct AvatarWorker: View {
    let worker: PersistentWorker
    var tamanio: CGFloat = 40

    var body: some View {
        ZStack {
            Circle().fill(degradado)
            Text(iniciales)
                .font(.system(size: tamanio * 0.42, weight: .bold, design: .rounded))
                .foregroundStyle(.white)
        }
        .frame(width: tamanio, height: tamanio)
    }

    private var acento: Color {
        if let hex = worker.avatarAccentHex {
            return AcentoAvatar.color(hex: hex)
        }
        return AcentoAvatar.determinista(worker.nombreVisible).color
    }

    /// Degradado determinista derivado del acento: mismo compañero → mismo
    /// degradado, sin aleatoriedad entre renders ni entre dispositivos.
    private var degradado: LinearGradient {
        LinearGradient(
            colors: [acento, acento.opacity(0.74), acento.opacity(0.48)],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
    }

    /// `avatar.initials` si el backend lo trae; si no, iniciales del nombre.
    private var iniciales: String {
        if let letras = worker.avatarInitials,
           !letras.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return letras
        }
        return Iniciales.de(worker.nombreVisible)
    }
}

private enum Iniciales {
    static func de(_ nombre: String) -> String {
        let partes = nombre.split(separator: " ").prefix(2)
        let letras = partes.compactMap { $0.first }.map(String.init)
        return letras.joined().uppercased()
    }
}

// MARK: - Presencia / estado

private struct EstadoWorker {
    let texto: String
    let color: Color
    /// Última actividad, derivada de `updatedAt` ("Hace 5 min"), `nil` si no
    /// hay marca de tiempo.
    let actividad: String?
    /// `true` solo cuando el compañero está trabajando de verdad — es el
    /// único estado cuyo punto de presencia late.
    let animado: Bool

    /// La línea completa que se muestra bajo el nombre: para "Trabajando" se
    /// acompaña de la actividad ("Trabajando · hace 5 min"); para el resto,
    /// solo el estado, para no ensuciar el roster.
    var linea: String {
        if animado, let actividad { return "\(texto) · \(actividad)" }
        return texto
    }

    static func de(_ worker: PersistentWorker) -> EstadoWorker {
        let actividad = Self.actividadRelativa(worker.updatedAt)
        if !worker.enabled || worker.status == "disabled" {
            return EstadoWorker(texto: "Apagado", color: .secondary, actividad: actividad, animado: false)
        }
        switch worker.status {
        case "running":
            return EstadoWorker(texto: "Trabajando", color: .green, actividad: actividad, animado: true)
        case "paused":
            return EstadoWorker(texto: "En pausa", color: .orange, actividad: actividad, animado: false)
        case "failed", "error":
            return EstadoWorker(texto: "Falló", color: .red, actividad: actividad, animado: false)
        default:
            return EstadoWorker(texto: "Listo", color: EdecanTheme.morado, actividad: actividad, animado: false)
        }
    }

    private static func actividadRelativa(_ fecha: Date?) -> String? {
        guard let fecha else { return nil }
        let segundos = Date().timeIntervalSince(fecha)
        guard segundos >= 0 else { return nil }
        if segundos < 60 { return "Hace un momento" }
        let minutos = Int(segundos / 60)
        if minutos < 60 { return "Hace \(minutos) min" }
        let horas = minutos / 60
        if horas < 24 { return "Hace \(horas) h" }
        return "Hace \(horas / 24) d"
    }
}

/// Línea de estado semántico bajo el nombre del compañero ("● Trabajando ·
/// hace 5 min"). El punto late solo cuando `animado` es `true`; en el resto
/// de estados queda quieto, del color del estado, y el texto va en gris
/// para que la jerarquía la mande el nombre.
private struct FilaEstadoWorker: View {
    let estado: EstadoWorker

    var body: some View {
        HStack(spacing: 5) {
            PuntoPresencia(color: estado.color, animado: estado.animado)
            Text(estado.linea)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
    }
}

/// Punto de presencia: fijo cuando está quieto; latiendo suave cuando
/// `animado` y la persona no pidió reducir el movimiento (`Reduce Motion`).
private struct PuntoPresencia: View {
    let color: Color
    let animado: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var latiendo = false

    var body: some View {
        ZStack {
            if animado && !reduceMotion {
                Circle()
                    .fill(color.opacity(0.35))
                    .frame(width: 7, height: 7)
                    .scaleEffect(latiendo ? 1.9 : 0.6)
                    .opacity(latiendo ? 0 : 0.7)
            }
            Circle()
                .fill(color)
                .frame(width: 6, height: 6)
        }
        .onAppear {
            guard animado, !reduceMotion else { return }
            withAnimation(.easeOut(duration: 1.6).repeatForever(autoreverses: true)) {
                latiendo = true
            }
        }
    }
}

// MARK: - Perfil / detalle

private struct PerfilWorkerSheet: View {
    @Environment(SessionStore.self) private var session
    @Environment(\.dismiss) private var dismiss
    let onActualizado: (PersistentWorker) -> Void
    @State private var trabajador: PersistentWorker
    @State private var editando = false
    @State private var encargo = ""
    @State private var encolando = false
    @State private var aviso: String?
    @State private var error: String?
    @State private var memoria: [AgentMemoryEntry] = []
    @State private var memoriaError: String?

    init(worker: PersistentWorker, onActualizado: @escaping (PersistentWorker) -> Void) {
        self.onActualizado = onActualizado
        _trabajador = State(initialValue: worker)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    HStack(spacing: 14) {
                        AvatarWorker(worker: trabajador, tamanio: 56)
                        VStack(alignment: .leading, spacing: 3) {
                            Text(trabajador.nombreVisible).font(.headline)
                            if let corto = trabajador.roleShort,
                               !corto.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                                Text(corto).font(.subheadline).foregroundStyle(.secondary)
                            }
                            HStack(spacing: 6) {
                                Circle()
                                    .fill(EstadoWorker.de(trabajador).color)
                                    .frame(width: 7, height: 7)
                                Text(EstadoWorker.de(trabajador).texto)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                    .padding(.vertical, 4)
                }

                Section("Oficio") {
                    FilaDato(titulo: "Qué hace", contenido: trabajador.jobDescription ?? trabajador.purpose)
                    FilaDato(titulo: "Personalidad", contenido: trabajador.personality)
                    FilaDato(titulo: "Cómo se comunica", contenido: trabajador.communicationStyle)
                }

                Section("Reglas") {
                    FilaDato(titulo: "Instrucciones", contenido: trabajador.instructions)
                    FilaDato(titulo: "Límites", contenido: trabajador.constraints)
                    FilaDato(titulo: "Política de aprobación", contenido: textoJSON(trabajador.approvalPolicy))
                }

                Section("Autonomía") {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(nivel.titulo).font(.subheadline.weight(.semibold))
                        Text(nivel.descripcion)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }

                Section("Memoria") {
                    if let memoriaError {
                        Text(memoriaError).font(.footnote).foregroundStyle(.secondary)
                    } else if memoria.isEmpty {
                        Text("Este compañero todavía no recuerda nada específico.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(memoria) { entrada in
                            VStack(alignment: .leading, spacing: 3) {
                                Text(entrada.contentText)
                                    .font(.subheadline)
                                    .fixedSize(horizontal: false, vertical: true)
                                if let confianza = entrada.sourceTrustText {
                                    Text(confianza)
                                        .font(.caption2)
                                        .foregroundStyle(.secondary)
                                }
                            }
                            .padding(.vertical, 2)
                        }
                    }
                }

                Section("Encargar tarea") {
                    TextField("Encárgale algo, como a un colega…", text: $encargo, axis: .vertical)
                        .lineLimit(2...5)
                    Button {
                        Task { await encolar() }
                    } label: {
                        if encolando {
                            ProgressView()
                        } else {
                            Text("Encolar").frame(maxWidth: .infinity)
                        }
                    }
                    .disabled(encolando || encargo.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    if let aviso {
                        Text(aviso).font(.footnote).foregroundStyle(.secondary)
                    }
                    if let error {
                        Text(error).font(.footnote).foregroundStyle(.red)
                    }
                }
            }
            .navigationTitle("Compañero")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cerrar") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Editar") { editando = true }
                }
            }
            .task { await cargarMemoria() }
        }
        .sheet(isPresented: $editando) {
            EditarWorkerSheet(worker: trabajador) { actualizado in
                trabajador = actualizado
                onActualizado(actualizado)
            }
        }
    }

    private var nivel: AutonomiaNivel {
        AutonomiaNivel.desde(trabajador.autonomyLevel)
    }

    private func cargarMemoria() async {
        guard let client = session.client else { return }
        do {
            memoria = try await client.listAgentMemory(agentId: trabajador.id)
            memoriaError = nil
        } catch {
            memoriaError = "No se pudo leer la memoria de este compañero."
        }
    }

    private func encolar() async {
        let texto = encargo.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !texto.isEmpty else { return }
        guard let client = session.client else { return }
        encolando = true
        defer { encolando = false }
        error = nil
        aviso = nil
        do {
            _ = try await client.enqueueWorkerTask(workerId: trabajador.id, instruction: texto)
            aviso = "Encolado. Lo toma en la computadora y te avisa si necesita tu OK."
            encargo = ""
        } catch let error as APIClient.APIError {
            if case .servidor(let status, _) = error, status == 409 {
                self.error = "No está disponible ahora mismo. Intenta cuando esté libre."
            } else {
                self.error = error.localizedDescription
            }
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func textoJSON(_ dict: [String: JSONValue]?) -> String? {
        guard let dict, !dict.isEmpty else { return nil }
        return dict.map { "\($0.key): \($0.value.vistaPrevia)" }
            .sorted()
            .joined(separator: "\n")
    }
}

private struct FilaDato: View {
    let titulo: String
    let contenido: String?

    var body: some View {
        if let contenido, !contenido.isEmpty {
            VStack(alignment: .leading, spacing: 4) {
                Text(titulo)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text(contenido)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.vertical, 2)
        }
    }
}

// MARK: - Edición

private struct EditarWorkerSheet: View {
    @Environment(SessionStore.self) private var session
    @Environment(\.dismiss) private var dismiss
    let worker: PersistentWorker
    let onGuardado: (PersistentWorker) -> Void

    @State private var nombreVisible: String
    @State private var cargo: String
    @State private var cargoCorto: String
    @State private var descripcion: String
    @State private var personalidad: String
    @State private var comunicacion: String
    @State private var instrucciones: String
    @State private var limites: String
    @State private var autonomia: AutonomiaNivel
    @State private var guardando = false
    @State private var error: String?

    init(worker: PersistentWorker, onGuardado: @escaping (PersistentWorker) -> Void) {
        self.worker = worker
        self.onGuardado = onGuardado
        _nombreVisible = State(initialValue: worker.displayName ?? "")
        _cargo = State(initialValue: worker.roleTitle ?? "")
        _cargoCorto = State(initialValue: worker.roleShort ?? "")
        _descripcion = State(initialValue: worker.jobDescription ?? "")
        _personalidad = State(initialValue: worker.personality ?? "")
        _comunicacion = State(initialValue: worker.communicationStyle ?? "")
        _instrucciones = State(initialValue: worker.instructions ?? "")
        _limites = State(initialValue: worker.constraints ?? "")
        _autonomia = State(initialValue: AutonomiaNivel.desde(worker.autonomyLevel))
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Identidad") {
                    TextField("Nombre visible", text: $nombreVisible)
                    TextField("Cargo", text: $cargo)
                    TextField("Cargo corto", text: $cargoCorto)
                }
                Section("Perfil") {
                    TextField("Qué hace", text: $descripcion, axis: .vertical)
                        .lineLimit(3...8)
                    TextField("Personalidad", text: $personalidad, axis: .vertical)
                        .lineLimit(3...8)
                    TextField("Cómo se comunica", text: $comunicacion, axis: .vertical)
                        .lineLimit(3...8)
                    TextField("Instrucciones", text: $instrucciones, axis: .vertical)
                        .lineLimit(4...10)
                    TextField("Límites", text: $limites, axis: .vertical)
                        .lineLimit(4...10)
                }
                Section("Autonomía") {
                    Picker("Nivel", selection: $autonomia) {
                        ForEach(AutonomiaNivel.allCases) { nivel in
                            Text(nivel.titulo).tag(nivel)
                        }
                    }
                    Text(autonomia.descripcion)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                if let error {
                    Text(error).font(.footnote).foregroundStyle(.red)
                }
            }
            .navigationTitle("Editar compañero")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancelar") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Guardar") { Task { await guardar() } }
                        .disabled(guardando)
                }
            }
        }
    }

    private func guardar() async {
        guard let client = session.client else { return }
        guardando = true
        defer { guardando = false }
        error = nil
        do {
            let actualizado = try await client.patchWorker(
                id: worker.id,
                displayName: nombreVisible.trimmingCharacters(in: .whitespacesAndNewlines),
                roleTitle: cargo.trimmingCharacters(in: .whitespacesAndNewlines),
                roleShort: cargoCorto.trimmingCharacters(in: .whitespacesAndNewlines),
                jobDescription: descripcion.trimmingCharacters(in: .whitespacesAndNewlines),
                personality: personalidad.trimmingCharacters(in: .whitespacesAndNewlines),
                communicationStyle: comunicacion.trimmingCharacters(in: .whitespacesAndNewlines),
                instructions: instrucciones.trimmingCharacters(in: .whitespacesAndNewlines),
                constraints: limites.trimmingCharacters(in: .whitespacesAndNewlines),
                autonomyLevel: autonomia.rawValue
            )
            onGuardado(actualizado)
            dismiss()
        } catch {
            self.error = error.localizedDescription
        }
    }
}

// MARK: - Creación

struct BorradorWorker {
    var nombre = ""
    var nombreVisible = ""
    var cargo = ""
    var proposito = ""
    var descripcion = ""
    var acentoHex: String?
    var autonomia: AutonomiaNivel = .ask
}

private struct NuevoCompaneroSheet: View {
    @Environment(\.dismiss) private var dismiss
    let oficio: OficioEquipo
    let onCrear: (BorradorWorker) -> Void
    @State private var borrador: BorradorWorker

    init(oficio: OficioEquipo, onCrear: @escaping (BorradorWorker) -> Void) {
        self.oficio = oficio
        self.onCrear = onCrear
        _borrador = State(initialValue: oficio.borrador)
    }

    var body: some View {
        Form {
            Section("Identidad") {
                TextField("Nombre (identificador)", text: $borrador.nombre)
                TextField("Nombre visible", text: $borrador.nombreVisible)
                TextField("Cargo", text: $borrador.cargo)
            }
            Section("Oficio") {
                TextField("Propósito", text: $borrador.proposito, axis: .vertical)
                    .lineLimit(3...8)
                TextField("Qué hace", text: $borrador.descripcion, axis: .vertical)
                    .lineLimit(3...8)
            }
            Section("Autonomía") {
                Picker("Nivel", selection: $borrador.autonomia) {
                    ForEach(AutonomiaNivel.allCases) { nivel in
                        Text(nivel.titulo).tag(nivel)
                    }
                }
                Text(borrador.autonomia.descripcion)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            Section("Color del avatar") {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 12) {
                        ForEach(AcentoAvatar.tonos) { tono in
                            Button {
                                borrador.acentoHex = tono.hex
                            } label: {
                                Circle()
                                    .fill(tono.color)
                                    .frame(width: 32, height: 32)
                                    .overlay {
                                        if borrador.acentoHex == tono.hex {
                                            Circle().strokeBorder(.primary, lineWidth: 2)
                                                .padding(-4)
                                        }
                                    }
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .padding(.vertical, 6)
                }
                Text("Si no eliges uno, se asigna un color según el nombre.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
        .navigationTitle(oficio.id == "blank" ? "Nuevo compañero" : oficio.nombre)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button("Cancelar") { dismiss() }
            }
            ToolbarItem(placement: .confirmationAction) {
                Button("Crear") {
                    onCrear(borrador)
                    dismiss()
                }
                .disabled(nombreVacio || propositoVacio)
            }
        }
    }

    private var nombreVacio: Bool {
        borrador.nombre.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private var propositoVacio: Bool {
        borrador.proposito.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
}

// MARK: - Oficios (plantillas de quick-fill)

struct OficioEquipo: Identifiable, Hashable {
    let id: String
    let nombre: String
    let resumen: String
    let proposito: String
    let autonomia: AutonomiaNivel

    var borrador: BorradorWorker {
        if id == "blank" {
            return BorradorWorker()
        }
        return BorradorWorker(
            nombre: nombre,
            nombreVisible: nombre,
            cargo: nombre,
            proposito: proposito,
            descripcion: resumen,
            acentoHex: nil,
            autonomia: autonomia
        )
    }

    static let enBlanco = OficioEquipo(
        id: "blank",
        nombre: "Nuevo compañero",
        resumen: "Ponle un oficio y encárgale trabajo.",
        proposito: "",
        autonomia: .ask
    )

    static let catalogo: [OficioEquipo] = [
        OficioEquipo(
            id: "ventas",
            nombre: "Ventas outbound",
            resumen: "Investiga cuentas, redacta mail y LinkedIn en tu voz, y deja borradores para aprobar.",
            proposito: "Generar pipeline: investigar cuentas, puntuar contactos y dejar borradores de mail/LinkedIn para que el dueño apruebe. No envíes nada solo.",
            autonomia: .draft
        ),
        OficioEquipo(
            id: "talento",
            nombre: "Talento",
            resumen: "Busca perfiles y deja intros listas para tu OK.",
            proposito: "Scout de talento: buscar perfiles, redactar intros en la voz del dueño y no contactar a nadie sin aprobación.",
            autonomia: .draft
        ),
        OficioEquipo(
            id: "gastos",
            nombre: "Gastos",
            resumen: "Junta recibos y deja el reporte listo.",
            proposito: "Organizar gastos y recibos, armar el reporte y pedir OK antes de presentar o enviar nada.",
            autonomia: .draft
        ),
        OficioEquipo(
            id: "cuentas",
            nombre: "Cuentas",
            resumen: "Sigue hilos y deja el siguiente paso en borrador.",
            proposito: "Account health: seguir hilos, actualizar notas y dejar el siguiente paso en borrador. No mandar mensajes sin OK.",
            autonomia: .draft
        ),
        OficioEquipo(
            id: "jefe",
            nombre: "Jefe de staff",
            resumen: "Coordina a los demás y solo te jala para decisiones.",
            proposito: "Chief of staff: coordinar al resto del equipo, pasar trabajo entre compañeros y avisar al dueño solo cuando haga falta un juicio.",
            autonomia: .ask
        ),
        OficioEquipo(
            id: "hoteles",
            nombre: "Viajes",
            resumen: "Busca hoteles y vuelos reales y te muestra cards, sin pagar.",
            proposito: "Viajes: usar buscar_hoteles y buscar_vuelos. Mostrar cards con foto, precio y dirección. Nunca pagar ni abrir Booking en la Mac.",
            autonomia: .readOnly
        ),
        OficioEquipo(
            id: "pauta",
            nombre: "Pauta",
            resumen: "Revisa campañas y deja cambios en borrador.",
            proposito: "Paid media: revisar rendimiento de campañas, proponer cambios y dejar copys/ajustes en borrador. No publiques ni gastes sin OK.",
            autonomia: .draft
        ),
        OficioEquipo(
            id: "producto",
            nombre: "Producto",
            resumen: "Investiga métricas y deja el hallazgo con evidencia.",
            proposito: "Product performance: investigar preguntas de producto con evidencia, separar hecho de hipótesis y devolver el hallazgo de mayor impacto primero. No cambies producción.",
            autonomia: .readOnly
        ),
        OficioEquipo(
            id: "bugs",
            nombre: "Bugs",
            resumen: "Reproduce el fallo y deja el ticket listo.",
            proposito: "Bug reproduction: reproducir el fallo, guardar evidencia y dejar el ticket/handoff listo. No cambies producción ni despliegues nada.",
            autonomia: .draft
        ),
    ]
}

// MARK: - Autonomía

enum AutonomiaNivel: String, CaseIterable, Identifiable {
    case ask = "ask"
    case readOnly = "read_only"
    case draft = "draft"
    case full = "full"

    var id: String { rawValue }

    var titulo: String {
        switch self {
        case .ask: return "Preguntar siempre"
        case .readOnly: return "Solo lectura"
        case .draft: return "Borra y pide OK"
        case .full: return "Autonomía total"
        }
    }

    var descripcion: String {
        switch self {
        case .ask:
            return "Consulta antes de cada paso sensible."
        case .readOnly:
            return "Investiga y lee, pero no ejecuta acciones."
        case .draft:
            return "Deja borradores y pide tu OK antes de enviar o publicar."
        case .full:
            return "Ejecuta de punta a punta sin pedirte permiso."
        }
    }

    static func desde(_ raw: String?) -> AutonomiaNivel {
        AutonomiaNivel(rawValue: raw ?? "") ?? .ask
    }
}

// MARK: - Acentos de avatar

enum AcentoAvatar {
    struct Tono: Identifiable {
        let hex: String
        let color: Color
        var id: String { hex }
    }

    static let tonos: [Tono] = [
        Tono(hex: "#8257F5", color: EdecanTheme.morado),
        Tono(hex: "#4A7DFA", color: EdecanTheme.azul),
        Tono(hex: "#14B8A6", color: Color(red: 0.08, green: 0.72, blue: 0.65)),
        Tono(hex: "#10B981", color: Color(red: 0.06, green: 0.73, blue: 0.51)),
        Tono(hex: "#F59E0B", color: Color(red: 0.96, green: 0.62, blue: 0.04)),
        Tono(hex: "#F97316", color: Color(red: 0.98, green: 0.45, blue: 0.09)),
        Tono(hex: "#EC4899", color: Color(red: 0.93, green: 0.28, blue: 0.60)),
        Tono(hex: "#64748B", color: Color(red: 0.39, green: 0.45, blue: 0.55)),
    ]

    static func color(hex: String) -> Color {
        tonos.first { $0.hex.caseInsensitiveCompare(hex) == .orderedSame }?.color ?? EdecanTheme.morado
    }

    static func determinista(_ nombre: String) -> Tono {
        let hash = nombre.unicodeScalars.reduce(0) { ($0 &* 31) &+ Int($1.value) }
        return tonos[abs(hash) % tonos.count]
    }
}