import SwiftUI
import EdecanKit

/// Hilo de un mensaje del chat (`GET/POST /v1/messages/{id}/thread`). Se abre
/// con "Responder en hilo" desde el menú contextual de una burbuja. Contrato en
/// paralelo: si la ruta no aterrizó, degrada con "Próximamente".
struct ThreadView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(SessionStore.self) private var session
    let messageId: String
    let resumen: String

    @State private var mensajes: [ThreadMessage] = []
    @State private var texto = ""
    @State private var cargando = true
    @State private var enviando = false
    @State private var error: String?
    @State private var proximamente = false
    @FocusState private var campoEnfocado: Bool

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                lista
                if let error {
                    Text(error)
                        .font(.footnote)
                        .foregroundStyle(.red)
                        .padding(.horizontal)
                        .padding(.top, 6)
                }
                compositor
            }
            .background(EdecanTheme.degradado.opacity(0.05).ignoresSafeArea())
            .navigationTitle("Hilo")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cerrar") { dismiss() }
                }
            }
            .task { await cargar() }
        }
    }

    private var lista: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 12) {
                // El mensaje ancla, para que el hilo se entienda sin contexto.
                VStack(alignment: .leading, spacing: 4) {
                    Text("Sobre el mensaje")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(.secondary)
                    Text(resumen)
                        .font(.footnote)
                        .lineLimit(3)
                }
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(EdecanTheme.morado.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))

                if proximamente {
                    EmptyStateView(
                        icono: "hourglass",
                        titulo: "Próximamente",
                        descripcion: "Los hilos están llegando al servidor. Vuelve en un momento."
                    )
                    .padding(.top, 40)
                } else if cargando && mensajes.isEmpty {
                    ProgressView().frame(maxWidth: .infinity).padding(.top, 40)
                } else if mensajes.isEmpty {
                    Text("Este hilo está vacío. Responde para empezar.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .padding(.top, 40)
                }

                ForEach(mensajes) { mensaje in
                    burbuja(mensaje)
                }
            }
            .padding()
        }
        .scrollDismissesKeyboard(.interactively)
        .contentShape(Rectangle())
        .onTapGesture { campoEnfocado = false }
    }

    private func burbuja(_ mensaje: ThreadMessage) -> some View {
        let propio = mensaje.esDelDueno
        return HStack(alignment: .top, spacing: 0) {
            if propio { Spacer(minLength: 56) }
            Text(mensaje.text)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .foregroundStyle(propio ? .white : .primary)
                .background {
                    if propio {
                        RoundedRectangle(cornerRadius: 18, style: .continuous)
                            .fill(EdecanTheme.degradado)
                    } else {
                        RoundedRectangle(cornerRadius: 18, style: .continuous)
                            .fill(.ultraThinMaterial)
                    }
                }
                .frame(maxWidth: 340, alignment: propio ? .trailing : .leading)
            if !propio { Spacer(minLength: 56) }
        }
        .frame(maxWidth: .infinity, alignment: propio ? .trailing : .leading)
    }

    private var compositor: some View {
        HStack(alignment: .bottom, spacing: 8) {
            TextField("Responde en el hilo…", text: $texto, axis: .vertical)
                .lineLimit(1...5)
                .focused($campoEnfocado)
                .submitLabel(.send)
                .onSubmit {
                    guard botonHabilitado else { return }
                    Task { await enviar() }
                }
                .textFieldStyle(.plain)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .tarjetaVidrio(esquina: 18)
            Button {
                Task { await enviar() }
            } label: {
                if enviando {
                    ProgressView().frame(width: 34, height: 34)
                } else {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.system(size: 34))
                        .foregroundStyle(botonHabilitado ? AnyShapeStyle(EdecanTheme.degradado) : AnyShapeStyle(.tertiary))
                }
            }
            .disabled(!botonHabilitado)
            .accessibilityLabel("Enviar")
        }
        .padding()
    }

    private var botonHabilitado: Bool {
        !texto.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !enviando
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
            mensajes = try await client.listThread(messageId: messageId)
        } catch let apiError as APIClient.APIError {
            if apiError.esProximamente {
                proximamente = true
            } else {
                self.error = apiError.localizedDescription
            }
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func enviar() async {
        let limpio = texto.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !limpio.isEmpty, let client = session.client else { return }
        texto = ""
        campoEnfocado = false
        error = nil
        proximamente = false
        enviando = true
        defer { enviando = false }
        do {
            try await client.postThread(messageId: messageId, text: limpio)
            await cargar()
        } catch let apiError as APIClient.APIError {
            if apiError.esProximamente {
                proximamente = true
            } else {
                self.error = apiError.localizedDescription
            }
        } catch {
            self.error = error.localizedDescription
        }
    }
}