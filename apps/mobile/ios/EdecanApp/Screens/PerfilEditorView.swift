import EdecanKit
import SwiftUI

/// Editor nativo de la identidad que Edecán inyecta en cada conversación.
/// No contiene credenciales ni configuración técnica: es información de la
/// persona, compartida con Android y el panel de computador por `/v1/perfil`.
struct PerfilEditorView: View {
    @Environment(SessionStore.self) private var session
    @Environment(\.dismiss) private var dismiss
    @State private var identidad = ProfileIdentity()
    @State private var resumen = ""
    @State private var cargando = true
    @State private var guardando = false
    @State private var error: String?

    let onSaved: (LiveProfile) -> Void

    var body: some View {
        Form {
            Section {
                TextField("Nombre preferido", text: $identidad.nombrePreferido)
                    .textContentType(.nickname)
                TextField("Nombre completo", text: $identidad.nombreCompleto)
                    .textContentType(.name)
                TextField("Pronombres", text: $identidad.pronombres)
                TextField("Fecha de nacimiento", text: $identidad.fechaNacimiento)
            } header: {
                Text("Cómo identificarte")
            } footer: {
                Text("Edecán usará tu nombre preferido al hablar contigo.")
            }

            Section("Tu contexto") {
                TextField("País", text: $identidad.pais)
                    .textContentType(.countryName)
                TextField("Ciudad", text: $identidad.ciudad)
                    .textContentType(.addressCity)
                TextField("Zona horaria", text: $identidad.zonaHoraria)
                    .textInputAutocapitalization(.never)
                TextField("A qué te dedicas", text: $identidad.ocupacion)
                TextField("Idioma preferido", text: $identidad.idiomaPreferido)
                TextField("Cómo quieres que te hable", text: $identidad.formaDeTrato, axis: .vertical)
                    .lineLimit(2...4)
            }

            Section("Sobre ti") {
                TextField(
                    "Lo que Edecán debería saber para ayudarte mejor",
                    text: $identidad.biografia,
                    axis: .vertical
                )
                .lineLimit(4...8)

                TextField(
                    "Síntesis de preferencias, proyectos y objetivos",
                    text: $resumen,
                    axis: .vertical
                )
                .lineLimit(3...7)
            }

            if let error {
                Section {
                    Text(error)
                        .font(.footnote)
                        .foregroundStyle(.red)
                }
            }
        }
        .scrollDismissesKeyboard(.interactively)
        .navigationTitle("Perfil")
        .navigationBarTitleDisplayMode(.inline)
        .overlay {
            if cargando {
                ProgressView("Cargando tu perfil…")
                    .padding(20)
                    .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16))
            }
        }
        .toolbar {
            ToolbarItem(placement: .confirmationAction) {
                Button("Guardar") {
                    Task { await guardar() }
                }
                .disabled(cargando || guardando)
            }
        }
        .task { await cargar() }
    }

    @MainActor
    private func cargar() async {
        guard let client = session.client else {
            error = "Edecán no está conectado."
            cargando = false
            return
        }
        do {
            let perfil = try await client.perfilVivo()
            identidad = perfil.datos.identidad
            resumen = perfil.resumen
        } catch is CancellationError {
            return
        } catch {
            self.error = error.localizedDescription
        }
        cargando = false
    }

    @MainActor
    private func guardar() async {
        guard let client = session.client, !guardando else { return }
        guardando = true
        error = nil
        defer { guardando = false }
        do {
            let actualizado = try await client.actualizarPerfilVivo(
                identidad: identidad,
                resumen: resumen.trimmingCharacters(in: .whitespacesAndNewlines)
            )
            onSaved(actualizado)
            dismiss()
        } catch is CancellationError {
            return
        } catch {
            self.error = error.localizedDescription
        }
    }
}
