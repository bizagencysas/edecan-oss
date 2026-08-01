import SwiftUI
import EdecanKit

/// "Recordatorios" (`ARCHITECTURE.md` §10.3/§10.12) — alcanzable desde el
/// acceso directo en ``InicioView`` (no es una pestaña propia). Lista
/// separada en pendientes/completados, alta con texto + fecha (`DatePicker`),
/// completar con swipe (`APIClient.completeReminder`, que reutiliza el
/// status `"sent"` — ver su docstring).
struct RecordatoriosView: View {
    @Environment(SessionStore.self) private var session
    @State private var viewModel = RecordatoriosViewModel()
    @State private var texto = ""
    @State private var fecha = Date().addingTimeInterval(3600)

    var body: some View {
        Group {
            if viewModel.cargando && viewModel.recordatorios.isEmpty {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                lista
            }
        }
        .background(EdecanTheme.degradado.opacity(0.05).ignoresSafeArea())
        .navigationTitle("Recordatorios")
        .navigationBarTitleDisplayMode(.inline)
        .task { await viewModel.cargar(client: session.client) }
        .refreshable { await viewModel.cargar(client: session.client) }
    }

    private var lista: some View {
        List {
            Section("Nuevo recordatorio") {
                TextField("Ej: Llamar al contador", text: $texto)
                DatePicker("Fecha y hora", selection: $fecha)
                if let error = viewModel.errorCreacion {
                    Text(error).font(.footnote).foregroundStyle(.red)
                }
                Button {
                    Task { await crear() }
                } label: {
                    if viewModel.creando {
                        ProgressView().frame(maxWidth: .infinity)
                    } else {
                        Text("Crear recordatorio").frame(maxWidth: .infinity)
                    }
                }
                .disabled(viewModel.creando || texto.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            .listRowBackground(Color.clear)

            if let error = viewModel.errorLista {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.red)
                    .listRowSeparator(.hidden)
                    .listRowBackground(Color.clear)
            }

            if viewModel.pendientes.isEmpty && viewModel.completados.isEmpty {
                Section {
                    EmptyStateView(
                        icono: "bell.badge.fill",
                        titulo: "Sin recordatorios",
                        descripcion: "Crea uno arriba, o pídeselo a Edecán en el chat."
                    )
                    .frame(minHeight: 160)
                }
                .listRowBackground(Color.clear)
            } else {
                if !viewModel.pendientes.isEmpty {
                    Section("Pendientes") {
                        ForEach(viewModel.pendientes) { recordatorio in
                            FilaRecordatorio(recordatorio: recordatorio)
                                .swipeActions(edge: .trailing) {
                                    Button {
                                        Task { await viewModel.completar(id: recordatorio.id, client: session.client) }
                                    } label: {
                                        Label("Completar", systemImage: "checkmark")
                                    }
                                    .tint(.green)
                                }
                        }
                    }
                    .listRowBackground(Color.clear)
                }
                if !viewModel.completados.isEmpty {
                    Section("Completados") {
                        ForEach(viewModel.completados) { recordatorio in
                            FilaRecordatorio(recordatorio: recordatorio)
                        }
                    }
                    .listRowBackground(Color.clear)
                }
            }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
    }

    private func crear() async {
        guard await viewModel.crear(texto: texto, fecha: fecha, client: session.client) != nil else { return }
        texto = ""
        fecha = Date().addingTimeInterval(3600)
    }
}

private struct FilaRecordatorio: View {
    let recordatorio: ReminderOut

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: recordatorio.completado ? "checkmark.circle.fill" : "circle")
                .foregroundStyle(recordatorio.completado ? .green : .secondary)
            VStack(alignment: .leading, spacing: 2) {
                Text(recordatorio.message)
                    .font(.subheadline)
                    .strikethrough(recordatorio.completado)
                    .foregroundStyle(recordatorio.completado ? .secondary : .primary)
                Text(recordatorio.dueAt.formatted(date: .abbreviated, time: .shortened))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
        }
        .padding(.vertical, 2)
    }
}
