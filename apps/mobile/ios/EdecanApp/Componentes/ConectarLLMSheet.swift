import SwiftUI
import EdecanKit

/// Estado informativo de la inteligencia administrada. Se conserva el nombre
/// del componente para mantener compatibilidad con las pantallas que ya lo
/// presentan, pero no permite escoger proveedores ni modelos.
struct ConectarLLMSheet: View {
    @Bindable var viewModel: CredencialesViewModel
    @Environment(\.dismiss) private var dismiss
    @Environment(SessionStore.self) private var session

    var body: some View {
        NavigationStack {
            List {
                Section {
                    HStack(spacing: 14) {
                        Image(systemName: "brain.head.profile")
                            .font(.title2)
                            .foregroundStyle(.purple)
                            .frame(width: 42, height: 42)
                            .background(.purple.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))

                        VStack(alignment: .leading, spacing: 4) {
                            Text("Workers AI")
                                .font(.headline)
                            Text(viewModel.inteligenciaDisponible ? "Conectado" : "No disponible")
                                .font(.subheadline)
                                .foregroundStyle(
                                    viewModel.inteligenciaDisponible ? Color.green : Color.secondary
                                )
                        }
                    }
                }

                Section("Conversación y llamadas") {
                    // Esta pantalla ya NO nombra un modelo concreto: el del
                    // chat se elige por conversación en la hoja "Seleccionar
                    // modelo" del composer (`GET /v1/models/chat`), y repetir
                    // aquí un nombre fijo fue justo el tipo de dato que quedó
                    // mintiendo la última vez.
                    LabeledContent("Modelo del chat", value: "Lo eliges en el chat")
                    Text("La pastilla debajo del campo de texto abre el selector: ahí escoges el modelo y su esfuerzo para esa conversación, o lo dejas en automático.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                Section("Ingeniería") {
                    Text("El IDE tiene su propio motor especializado. Esta pantalla no cambia ni acopla su configuración.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                Section {
                    Label(
                        "No hay que guardar una API key aquí. En automático el router decide según la tarea; si prefieres mandar tú, el selector del chat manda.",
                        systemImage: "wand.and.stars"
                    )
                    .font(.footnote)
                }

                if let error = viewModel.errorMensaje {
                    Section {
                        Text(error)
                            .font(.footnote)
                            .foregroundStyle(.red)
                    }
                }
            }
            .navigationTitle("Inteligencia")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Cerrar") { dismiss() }
                }
            }
            .task {
                await viewModel.cargar(client: session.client)
            }
            .refreshable {
                await viewModel.cargar(client: session.client)
            }
        }
    }
}
