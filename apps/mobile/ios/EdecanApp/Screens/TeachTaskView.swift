import SwiftUI
import EdecanKit

/// "Enseñar una tarea" (`skills.py` §teach): captura los pasos de algo que la
/// persona repite y lo compila en una skill `draft`, que recién se activa al
/// aprobarla. Flujo mínimo: nombre/descripción → agregar pasos → terminar →
/// aprobar.
struct TeachTaskView: View {
    @Environment(SessionStore.self) private var sessionStore
    @State private var nombre = ""
    @State private var descripcion = ""
    @State private var sesion: TeachSession?
    @State private var pasos: [TeachStep] = []
    @State private var pasoAction = ""
    @State private var pasoSelector = ""
    @State private var pasoDecision = ""
    @State private var pasoInput = ""
    @State private var pasoOutput = ""
    @State private var draft: TeachSkillDetail?
    @State private var aprobado = false
    @State private var ocupado = false
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Form {
                if let error {
                    Section { Text(error).font(.footnote).foregroundStyle(.red) }
                }
                contenido
            }
            .navigationTitle("Enseñar una tarea")
            .navigationBarTitleDisplayMode(.inline)
        }
    }

    @ViewBuilder
    private var contenido: some View {
        if aprobado {
            Section {
                VStack(alignment: .leading, spacing: 8) {
                    Label("Lista", systemImage: "checkmark.seal.fill")
                        .font(.headline)
                        .foregroundStyle(.green)
                    Text("“\(draft?.nombre ?? nombre)” quedó activa y disponible para Edecán.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                .padding(.vertical, 4)
            }
        } else if let draft {
            Section {
                VStack(alignment: .leading, spacing: 8) {
                    Text(draft.nombre).font(.headline)
                    Text(draft.status == "draft"
                         ? "Guardada como borrador: no se activa hasta que la apruebes."
                         : "Lista para usar.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                .padding(.vertical, 4)
                Button {
                    Task { await aprobar(draft) }
                } label: {
                    if ocupado { ProgressView() } else { Text("Aprobar y activar").frame(maxWidth: .infinity) }
                }
                .disabled(ocupado || draft.status != "draft")
            }
        } else if sesion == nil {
            Section {
                TextField("Nombre de la tarea", text: $nombre)
                TextField("Descripción (opcional)", text: $descripcion, axis: .vertical)
                    .lineLimit(2...4)
            } footer: {
                Text("Por ejemplo: “Preparar el reporte de gastos”. Después capturas los pasos uno a uno.")
            }
            Section {
                Button {
                    Task { await comenzar() }
                } label: {
                    if ocupado { ProgressView() } else { Text("Comenzar").frame(maxWidth: .infinity) }
                }
                .disabled(ocupado || nombre.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        } else {
            Section {
                if pasos.isEmpty {
                    Text("Todavía no hay pasos. Agrega el primero abajo.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(Array(pasos.enumerated()), id: \.offset) { indice, paso in
                        VStack(alignment: .leading, spacing: 3) {
                            Text("\(indice + 1). \(paso.action.isEmpty ? "(sin acción)" : paso.action)")
                                .font(.subheadline.weight(.medium))
                            Text(detallePaso(paso))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        .padding(.vertical, 2)
                    }
                }
            } header: {
                Text("Pasos capturados")
            }

            Section {
                TextField("Acción (qué hace)", text: $pasoAction, axis: .vertical)
                    .lineLimit(1...3)
                TextField("Selector (opcional)", text: $pasoSelector)
                TextField("Decisión (opcional)", text: $pasoDecision)
                TextField("Input (opcional)", text: $pasoInput, axis: .vertical)
                    .lineLimit(1...3)
                TextField("Output (opcional)", text: $pasoOutput, axis: .vertical)
                    .lineLimit(1...3)
                Button {
                    Task { await agregarPaso() }
                } label: {
                    if ocupado { ProgressView() } else { Text("Agregar paso") }
                }
                .disabled(ocupado || pasoAction.trimmingCharacters(in: .whitespaces).isEmpty)
            } header: {
                Text("Nuevo paso")
            }

            Section {
                Button {
                    Task { await terminar() }
                } label: {
                    if ocupado { ProgressView() } else { Text("Terminar").frame(maxWidth: .infinity) }
                }
                .disabled(ocupado || pasos.isEmpty)
            } footer: {
                Text("Al terminar se arma una habilidad en borrador. Nada se activa sin tu aprobación.")
            }
        }
    }

    private func detallePaso(_ paso: TeachStep) -> String {
        var partes: [String] = []
        if !paso.selector.isEmpty { partes.append("Selector: \(paso.selector)") }
        if !paso.decision.isEmpty { partes.append("Decisión: \(paso.decision)") }
        if !paso.input.isEmpty { partes.append("Input: \(paso.input)") }
        if !paso.output.isEmpty { partes.append("Output: \(paso.output)") }
        return partes.isEmpty ? "" : partes.joined(separator: " · ")
    }

    private func comenzar() async {
        guard let client = sessionStore.client else { return }
        ocupado = true
        error = nil
        defer { ocupado = false }
        do {
            sesion = try await client.startTeach(
                nombre: nombre.trimmingCharacters(in: .whitespacesAndNewlines),
                descripcion: descripcion.trimmingCharacters(in: .whitespacesAndNewlines)
            )
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func agregarPaso() async {
        guard let client = sessionStore.client, let sesion else { return }
        ocupado = true
        error = nil
        defer { ocupado = false }
        do {
            let actualizada = try await client.addTeachStep(
                sessionId: sesion.id,
                step: TeachStep(
                    action: pasoAction.trimmingCharacters(in: .whitespacesAndNewlines),
                    selector: pasoSelector.trimmingCharacters(in: .whitespacesAndNewlines),
                    decision: pasoDecision.trimmingCharacters(in: .whitespacesAndNewlines),
                    input: pasoInput.trimmingCharacters(in: .whitespacesAndNewlines),
                    output: pasoOutput.trimmingCharacters(in: .whitespacesAndNewlines)
                )
            )
            pasos = actualizada.pasos
            pasoAction = ""
            pasoSelector = ""
            pasoDecision = ""
            pasoInput = ""
            pasoOutput = ""
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func terminar() async {
        guard let client = sessionStore.client, let sesion else { return }
        ocupado = true
        error = nil
        defer { ocupado = false }
        do {
            draft = try await client.finishTeach(sessionId: sesion.id)
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func aprobar(_ borrador: TeachSkillDetail) async {
        guard let client = sessionStore.client else { return }
        ocupado = true
        error = nil
        defer { ocupado = false }
        do {
            draft = try await client.approveSkill(id: borrador.id)
            aprobado = true
        } catch {
            self.error = error.localizedDescription
        }
    }
}