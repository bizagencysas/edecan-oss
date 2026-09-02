import SwiftUI
import EdecanKit

/// "Mensajes" entre compañeros (`GET/POST /v1/agents/messages`, contrato en
/// paralelo). Muestra lo que los workers se dicen entre sí y permite dejar un
/// mensaje dirigido a uno de ellos — sin chat fingido: todo pasa por el
/// servidor. Una ruta que todavía no aterrizó degrada con "Próximamente"
/// (directiva §153).
struct AgentMessagesView: View {
    @Environment(SessionStore.self) private var session
    @State private var mensajes: [AgentMessage] = []
    @State private var workers: [PersistentWorker] = []
    @State private var cargando = true
    @State private var error: String?
    @State private var proximamente = false
    @State private var redactando = false

    var body: some View {
        List {
            if let error {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.red)
                    .listRowSeparator(.hidden)
            }
            if proximamente {
                filaProximamente
            }
            if mensajes.isEmpty && !cargando && !proximamente {
                Text("Todavía no hay mensajes entre compañeros.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .listRowSeparator(.hidden)
            }
            ForEach(mensajes) { mensaje in
                FilaAgentMessage(mensaje: mensaje)
                    .listRowSeparator(.hidden)
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Mensajes")
        .navigationBarTitleDisplayMode(.large)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    redactando = true
                } label: {
                    Image(systemName: "square.and.pencil")
                }
            }
        }
        .overlay {
            if cargando && mensajes.isEmpty {
                ProgressView()
            }
        }
        .sheet(isPresented: $redactando) {
            NuevoAgentMessageSheet(workers: workers) {
                Task { await cargar() }
            }
            .environment(session)
        }
        .task { await cargar() }
        .refreshable { await cargar() }
    }

    private var filaProximamente: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("Próximamente", systemImage: "hourglass")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(EdecanTheme.morado)
            Text("Los mensajes entre compañeros están llegando al servidor. Vuelve en un momento.")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
        .padding(.vertical, 4)
        .listRowSeparator(.hidden)
    }

    private func cargar() async {
        guard let client = session.client else {
            error = "No hay sesión activa."
            cargando = false
            return
        }
        cargando = mensajes.isEmpty
        error = nil
        proximamente = false
        defer { cargando = false }
        do {
            mensajes = try await client.listAgentMessages()
        } catch let apiError as APIClient.APIError {
            if apiError.esProximamente {
                proximamente = true
            } else {
                self.error = apiError.localizedDescription
            }
        } catch {
            self.error = error.localizedDescription
        }
        // La lista de destinatarios se usa para el compositor; un fallo aquí
        // no debe esconder los mensajes ya cargados.
        workers = (try? await client.listWorkers()) ?? []
    }
}

private struct FilaAgentMessage: View {
    let mensaje: AgentMessage

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Circle()
                .fill(mensaje.esDelDueno ? EdecanTheme.morado : EdecanTheme.azul)
                .frame(width: 8, height: 8)
                .padding(.top, 6)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 4) {
                    Text(remitente)
                        .font(.caption.weight(.semibold))
                    if let destinatario = destinatario {
                        Text("→ \(destinatario)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                Text(texto)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                if let at = mensaje.createdAt {
                    Text(at.formatted(date: .abbreviated, time: .shortened))
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
            Spacer(minLength: 8)
        }
        .padding(.vertical, 2)
    }

    private var texto: String {
        if let goal = mensaje.goal?.trimmingCharacters(in: .whitespacesAndNewlines),
           !goal.isEmpty {
            return goal
        }
        return mensaje.messageType ?? "Mensaje"
    }

    private var remitente: String {
        if mensaje.esDelDueno { return "Tú" }
        if let nombre = mensaje.senderName?.trimmingCharacters(in: .whitespacesAndNewlines),
           !nombre.isEmpty {
            return nombre
        }
        return "Compañero"
    }

    private var destinatario: String? {
        if let nombre = mensaje.recipientName?.trimmingCharacters(in: .whitespacesAndNewlines),
           !nombre.isEmpty {
            return nombre
        }
        return nil
    }
}

/// Compositor: elige a qué compañero va dirigido el mensaje y lo encola.
private struct NuevoAgentMessageSheet: View {
    @Environment(SessionStore.self) private var session
    @Environment(\.dismiss) private var dismiss
    let workers: [PersistentWorker]
    let onEnviado: () -> Void

    @State private var destinatarioId: String?
    @State private var texto = ""
    @State private var enviando = false
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("Para") {
                    if workers.isEmpty {
                        Text("No hay compañeros para recibir el mensaje.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    } else {
                        Picker("Compañero", selection: $destinatarioId) {
                            Text("Elige uno").tag(String?.none)
                            ForEach(workers) { worker in
                                Text(worker.nombreVisible).tag(String?.some(worker.id))
                            }
                        }
                    }
                }
                Section("Mensaje") {
                    TextField("Escríbele algo…", text: $texto, axis: .vertical)
                        .lineLimit(3...8)
                }
                if let error {
                    Section { Text(error).font(.footnote).foregroundStyle(.red) }
                }
            }
            .navigationTitle("Nuevo mensaje")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancelar") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button {
                        Task { await enviar() }
                    } label: {
                        if enviando { ProgressView() } else { Text("Enviar") }
                    }
                    .disabled(enviando || !valido)
                }
            }
        }
    }

    private var valido: Bool {
        destinatarioId != nil
            && !texto.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private func enviar() async {
        guard let client = session.client,
              let destinatarioId,
              !texto.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        else { return }
        enviando = true
        error = nil
        defer { enviando = false }
        do {
            _ = try await client.sendAgentMessage(
                toAgentId: destinatarioId,
                text: texto.trimmingCharacters(in: .whitespacesAndNewlines)
            )
            onEnviado()
            dismiss()
        } catch {
            self.error = error.localizedDescription
        }
    }
}
