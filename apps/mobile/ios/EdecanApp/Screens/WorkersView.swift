import SwiftUI
import EdecanKit

/// Equipo al estilo Grok Bot: compañeros con nombre, se les escribe como a
/// un colega, trabajan en paralelo y solo vuelven cuando necesitan un OK.
/// Encima de `POST /v1/agents/workers` — no es un formulario de admin.
struct WorkersView: View {
    @Environment(SessionStore.self) private var session
    @Environment(TabRouter.self) private var router
    @State private var workers: [PersistentWorker] = []
    @State private var handoffs: [WorkerHandoff] = []
    @State private var busqueda = ""
    @State private var error: String?
    @State private var cargando = true
    @State private var ocupado = false
    @State private var creando: OficioEquipo?
    @State private var mostrandoGrupo = false

    var body: some View {
        List {
            if let error {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.red)
                    .listRowSeparator(.hidden)
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

            Section {
                ForEach(visibles) { worker in
                    NavigationLink {
                        ConversacionEquipoView(worker: worker)
                    } label: {
                        FilaCompanero(worker: worker)
                    }
                }
                if !cargando && visibles.isEmpty {
                    Text(workers.isEmpty
                         ? "Crea un compañero, dale un oficio y escríbele como a alguien del equipo."
                         : "Nada coincide con “\(busqueda)”.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            } header: {
                Text("Equipo")
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
        }
        .listStyle(.insetGrouped)
        .searchable(text: $busqueda, prompt: "Buscar compañeros")
        .navigationTitle("Equipo")
        .navigationBarTitleDisplayMode(.large)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Button {
                        creando = .enBlanco
                    } label: {
                        Label("Nuevo compañero", systemImage: "person.badge.plus")
                    }
                    Button {
                        mostrandoGrupo = true
                    } label: {
                        Label("Nuevo grupo", systemImage: "person.3")
                    }
                    .disabled(workers.count < 2)
                } label: {
                    Label("Nuevo", systemImage: "plus")
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
        .sheet(item: $creando) { oficio in
            NavigationStack {
                NuevoCompaneroSheet(oficio: oficio) { nombre, proposito in
                    Task { await crear(nombre: nombre, proposito: proposito) }
                }
            }
        }
        .sheet(isPresented: $mostrandoGrupo) {
            NavigationStack {
                NuevoGrupoSheet(workers: workers) { nombres, proposito, tarea in
                    router.encargarACompanero(
                        nombre: nombres,
                        proposito: proposito,
                        tarea: tarea
                    )
                    mostrandoGrupo = false
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
            $0.name.lowercased().contains(q) || $0.purpose.lowercased().contains(q)
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
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func crear(nombre: String, proposito: String) async {
        guard let client = session.client else { return }
        ocupado = true
        defer { ocupado = false }
        do {
            _ = try await client.createWorker(name: nombre, purpose: proposito)
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
}

private struct FilaCompanero: View {
    let worker: PersistentWorker

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            ZStack {
                Circle().fill(EdecanTheme.morado.opacity(0.16))
                Text(iniciales)
                    .font(.caption.weight(.bold))
                    .foregroundStyle(EdecanTheme.morado)
            }
            .frame(width: 36, height: 36)
            VStack(alignment: .leading, spacing: 3) {
                HStack {
                    Text(worker.name)
                        .font(.subheadline.weight(.semibold))
                    Spacer()
                    Text(hora)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                Text(MemoriaEquipo.ultimo(worker.id) ?? worker.purpose)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
            Text(etiquetaEstado)
                .font(.caption2.weight(.semibold))
                .foregroundStyle(colorEstado)
                .padding(.horizontal, 7)
                .padding(.vertical, 3)
                .background(colorEstado.opacity(0.12), in: Capsule())
        }
        .padding(.vertical, 4)
    }

    private var iniciales: String {
        let partes = worker.name.split(separator: " ").prefix(2)
        let letras = partes.compactMap { $0.first }.map(String.init)
        return letras.joined().uppercased()
    }

    private var hora: String {
        guard let fecha = worker.updatedAt else { return "" }
        return fecha.formatted(date: .omitted, time: .shortened)
    }

    private var etiquetaEstado: String {
        switch worker.status {
        case "running": "Trabajando"
        case "paused": "En pausa"
        case "disabled": "Apagado"
        default: "Listo"
        }
    }

    private var colorEstado: Color {
        switch worker.status {
        case "running": .green
        case "paused": .orange
        case "disabled": .secondary
        default: EdecanTheme.morado
        }
    }
}

/// Hilo con un compañero: se le escribe el encargo, él sigue en el Mac/sidecar
/// y solo pide OK. También se puede abrir en el chat (tools) o ver la Mac.
struct ConversacionEquipoView: View {
    @Environment(SessionStore.self) private var session
    @Environment(TabRouter.self) private var router
    let worker: PersistentWorker
    @State private var mensaje = ""
    @State private var bitacora: [EntradaEquipo]
    @State private var ocupado = false
    @State private var error: String?

    init(worker: PersistentWorker) {
        self.worker = worker
        _bitacora = State(initialValue: MemoriaEquipo.bitacora(worker.id))
    }

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    Text(worker.purpose)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)

                    ForEach(bitacora) { entrada in
                        VStack(alignment: entrada.propia ? .trailing : .leading, spacing: 4) {
                            Text(entrada.texto)
                                .font(.body)
                                .padding(12)
                                .background(
                                    entrada.propia
                                        ? EdecanTheme.morado.opacity(0.16)
                                        : Color.primary.opacity(0.06),
                                    in: RoundedRectangle(cornerRadius: 16, style: .continuous)
                                )
                            Text(entrada.cuando.formatted(date: .omitted, time: .shortened))
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                        .frame(maxWidth: .infinity, alignment: entrada.propia ? .trailing : .leading)
                    }
                    if let error {
                        Text(error).font(.footnote).foregroundStyle(.red)
                    }
                }
                .padding()
            }

            HStack(spacing: 10) {
                TextField("Encárgale algo, como a un colega…", text: $mensaje, axis: .vertical)
                    .textFieldStyle(.roundedBorder)
                    .lineLimit(1...4)
                Button {
                    Task { await enviar(modo: .voz) }
                } label: {
                    Image(systemName: "mic.circle.fill")
                        .font(.title)
                        .foregroundStyle(EdecanTheme.morado)
                }
                .disabled(ocupado || mensaje.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                Button {
                    Task { await enviar(modo: .cola) }
                } label: {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.title)
                        .foregroundStyle(EdecanTheme.morado)
                }
                .disabled(ocupado || mensaje.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            .padding(.horizontal)
            .padding(.vertical, 10)
            .background(.bar)
        }
        .navigationTitle(worker.name)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("Computadora") { router.mostrarRemoto() }
            }
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if !mensaje.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                Button("También en el chat") {
                    Task { await enviar(modo: .chat) }
                }
                .font(.caption.weight(.semibold))
                .padding(.bottom, 4)
            }
        }
    }

    private enum ModoEnvio { case cola, chat, voz }

    private func enviar(modo: ModoEnvio) async {
        let texto = mensaje.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !texto.isEmpty else { return }
        ocupado = true
        defer { ocupado = false }
        var encolado = false
        do {
            if let client = session.client {
                _ = try await client.enqueueWorkerTask(workerId: worker.id, instruction: texto)
                encolado = true
            }
        } catch let error as APIClient.APIError {
            if case .servidor(let status, _) = error, status == 409 {
                encolado = false
            } else {
                self.error = error.localizedDescription
                return
            }
        } catch {
            self.error = error.localizedDescription
            return
        }
        let propia = EntradaEquipo(texto: texto, propia: true, cuando: Date())
        let eco = EntradaEquipo(
            texto: encolado
                ? "Lo tomo. Sigo en la computadora y te aviso si necesito tu OK."
                : "Ya estaba en algo. Te lo pasé al chat para que siga ahí.",
            propia: false,
            cuando: Date()
        )
        bitacora.append(contentsOf: [propia, eco])
        MemoriaEquipo.guardar(workerId: worker.id, bitacora: bitacora, ultimo: texto)
        mensaje = ""
        if modo == .voz {
            router.hablarConCompanero(nombre: worker.name, proposito: worker.purpose, tarea: texto)
        } else if modo == .chat || !encolado {
            router.encargarACompanero(nombre: worker.name, proposito: worker.purpose, tarea: texto)
        }
    }
}

private struct NuevoCompaneroSheet: View {
    @Environment(\.dismiss) private var dismiss
    let oficio: OficioEquipo
    let onCrear: (String, String) -> Void
    @State private var nombre: String
    @State private var proposito: String

    init(oficio: OficioEquipo, onCrear: @escaping (String, String) -> Void) {
        self.oficio = oficio
        self.onCrear = onCrear
        _nombre = State(initialValue: oficio.nombre == "Nuevo compañero" ? "" : oficio.nombre)
        _proposito = State(initialValue: oficio.proposito)
    }

    var body: some View {
        Form {
            TextField("Nombre", text: $nombre)
            TextField("Oficio", text: $proposito, axis: .vertical)
                .lineLimit(3...8)
            Text("Después le escribes como a un colega. Él usa la Mac y las tools; tú solo apruebas lo sensible.")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
        .navigationTitle(oficio.nombre)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button("Cancelar") { dismiss() }
            }
            ToolbarItem(placement: .confirmationAction) {
                Button("Crear") {
                    onCrear(
                        nombre.trimmingCharacters(in: .whitespacesAndNewlines),
                        proposito.trimmingCharacters(in: .whitespacesAndNewlines)
                    )
                    dismiss()
                }
                .disabled(nombre.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                          || proposito.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
    }
}

struct OficioEquipo: Identifiable, Hashable {
    let id: String
    let nombre: String
    let resumen: String
    let proposito: String

    static let enBlanco = OficioEquipo(
        id: "blank",
        nombre: "Nuevo compañero",
        resumen: "Ponle un oficio y escríbele.",
        proposito: ""
    )

    static let catalogo: [OficioEquipo] = [
        OficioEquipo(
            id: "ventas",
            nombre: "Ventas outbound",
            resumen: "Investiga cuentas, redacta mail y LinkedIn en tu voz, y deja borradores para aprobar.",
            proposito: "Generar pipeline: investigar cuentas, puntuar contactos y dejar borradores de mail/LinkedIn para que el dueño apruebe. No envíes nada solo."
        ),
        OficioEquipo(
            id: "talento",
            nombre: "Talento",
            resumen: "Busca perfiles y deja intros listas para tu OK.",
            proposito: "Scout de talento: buscar perfiles, redactar intros en la voz del dueño y no contactar a nadie sin aprobación."
        ),
        OficioEquipo(
            id: "gastos",
            nombre: "Gastos",
            resumen: "Junta recibos y deja el reporte listo.",
            proposito: "Organizar gastos y recibos, armar el reporte y pedir OK antes de presentar o enviar nada."
        ),
        OficioEquipo(
            id: "cuentas",
            nombre: "Cuentas",
            resumen: "Sigue hilos y deja el siguiente paso en borrador.",
            proposito: "Account health: seguir hilos, actualizar notas y dejar el siguiente paso en borrador. No mandar mensajes sin OK."
        ),
        OficioEquipo(
            id: "jefe",
            nombre: "Jefe de staff",
            resumen: "Coordina a los demás y solo te jala para decisiones.",
            proposito: "Chief of staff: coordinar al resto del equipo, pasar trabajo entre compañeros y avisar al dueño solo cuando haga falta un juicio."
        ),
        OficioEquipo(
            id: "hoteles",
            nombre: "Viajes",
            resumen: "Busca hoteles y vuelos reales y te muestra cards, sin pagar.",
            proposito: "Viajes: usar buscar_hoteles y buscar_vuelos. Mostrar cards con foto, precio y dirección. Nunca pagar ni abrir Booking en la Mac."
        ),
        OficioEquipo(
            id: "pauta",
            nombre: "Pauta",
            resumen: "Revisa campañas y deja cambios en borrador.",
            proposito: "Paid media: revisar rendimiento de campañas, proponer cambios y dejar copys/ajustes en borrador. No publiques ni gastes sin OK."
        ),
        OficioEquipo(
            id: "producto",
            nombre: "Producto",
            resumen: "Investiga métricas y deja el hallazgo con evidencia.",
            proposito: "Product performance: investigar preguntas de producto con evidencia, separar hecho de hipótesis y devolver el hallazgo de mayor impacto primero. No cambies producción."
        ),
        OficioEquipo(
            id: "bugs",
            nombre: "Bugs",
            resumen: "Reproduce el fallo y deja el ticket listo.",
            proposito: "Bug reproduction: reproducir el fallo, guardar evidencia y dejar el ticket/handoff listo. No cambies producción ni despliegues nada."
        ),
    ]
}

private struct NuevoGrupoSheet: View {
    @Environment(\.dismiss) private var dismiss
    let workers: [PersistentWorker]
    let onCrear: (String, String, String) -> Void
    @State private var elegidos: Set<String> = []
    @State private var tarea = ""

    var body: some View {
        Form {
            Section("Quién entra") {
                ForEach(workers) { worker in
                    Button {
                        if elegidos.contains(worker.id) {
                            elegidos.remove(worker.id)
                        } else {
                            elegidos.insert(worker.id)
                        }
                    } label: {
                        HStack {
                            Text(worker.name)
                            Spacer()
                            if elegidos.contains(worker.id) {
                                Image(systemName: "checkmark")
                                    .foregroundStyle(EdecanTheme.morado)
                            }
                        }
                    }
                }
            }
            Section("Encargo compartido") {
                TextField("Qué tienen que terminar juntos", text: $tarea, axis: .vertical)
                    .lineLimit(3...8)
                Text("Ellos se pasan el trabajo. Tú solo entras si hace falta un juicio.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
        .navigationTitle("Nuevo grupo")
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button("Cancelar") { dismiss() }
            }
            ToolbarItem(placement: .confirmationAction) {
                Button("Encargar") {
                    let nombres = workers.filter { elegidos.contains($0.id) }.map(\.name)
                    onCrear(
                        nombres.joined(separator: ", "),
                        "Grupo: \(nombres.joined(separator: " + ")). Coordínense; un solo dueño por paso.",
                        tarea.trimmingCharacters(in: .whitespacesAndNewlines)
                    )
                    dismiss()
                }
                .disabled(elegidos.count < 2 || tarea.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
    }
}

private struct EntradaEquipo: Identifiable, Codable, Equatable {
    var id = UUID()
    let texto: String
    let propia: Bool
    let cuando: Date
}

private enum MemoriaEquipo {
    private static let clave = "cc.edecan.equipo.bitacora.v1"

    static func ultimo(_ workerId: String) -> String? {
        bitacora(workerId).last(where: \.propia)?.texto
    }

    static func bitacora(_ workerId: String) -> [EntradaEquipo] {
        guard let data = UserDefaults.standard.data(forKey: clave),
              let mapa = try? JSONDecoder().decode([String: [EntradaEquipo]].self, from: data)
        else { return [] }
        return mapa[workerId] ?? []
    }

    static func guardar(workerId: String, bitacora: [EntradaEquipo], ultimo: String) {
        var mapa: [String: [EntradaEquipo]] = [:]
        if let data = UserDefaults.standard.data(forKey: clave),
           let existente = try? JSONDecoder().decode([String: [EntradaEquipo]].self, from: data) {
            mapa = existente
        }
        mapa[workerId] = Array(bitacora.suffix(40))
        if let data = try? JSONEncoder().encode(mapa) {
            UserDefaults.standard.set(data, forKey: clave)
        }
        _ = ultimo
    }
}
