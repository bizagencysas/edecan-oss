import SwiftUI
import EdecanKit

/// Formulario para `PUT /v1/mcp/servers` — HTTP o stdio, con validación de handshake.
struct RegistrarMCPSheet: View {
    @Environment(SessionStore.self) private var session
    @Environment(\.dismiss) private var dismiss

    var onRegistrado: (() -> Void)?

    @State private var nombre = ""
    @State private var transporte = "http"
    @State private var url = ""
    @State private var comando = ""
    @State private var headerClave = ""
    @State private var headerValor = ""
    @State private var headers: [String: String] = [:]
    @State private var validar = true
    @State private var guardando = false
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Nombre (único)", text: $nombre)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    Picker("Transporte", selection: $transporte) {
                        Text("HTTP").tag("http")
                        Text("stdio (comando local)").tag("stdio")
                    }
                }

                if transporte == "http" {
                    Section("HTTP") {
                        TextField("URL del servidor MCP", text: $url)
                            .textInputAutocapitalization(.never)
                            .keyboardType(.URL)
                            .autocorrectionDisabled()
                    }
                } else {
                    Section("stdio") {
                        TextField("Comando (ej. npx -y @modelcontextprotocol/server-filesystem /tmp)", text: $comando)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                    }
                }

                Section("Headers (HTTP, opcional)") {
                    HStack {
                        TextField("Clave", text: $headerClave)
                            .textInputAutocapitalization(.never)
                        TextField("Valor", text: $headerValor)
                            .textInputAutocapitalization(.never)
                    }
                    Button("Añadir header") {
                        let k = headerClave.trimmingCharacters(in: .whitespacesAndNewlines)
                        let v = headerValor.trimmingCharacters(in: .whitespacesAndNewlines)
                        guard !k.isEmpty, !v.isEmpty else { return }
                        headers[k] = v
                        headerClave = ""
                        headerValor = ""
                    }
                    .disabled(headerClave.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                              || headerValor.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    if !headers.isEmpty {
                        ForEach(headers.keys.sorted(), id: \.self) { clave in
                            HStack {
                                Text(clave).font(.caption.monospaced())
                                Spacer()
                                Text("•••").font(.caption2).foregroundStyle(.secondary)
                            }
                        }
                    }
                }

                Section {
                    Toggle("Validar handshake antes de guardar", isOn: $validar)
                } footer: {
                    Text("Si la validación falla, el servidor no se guarda y verás el error exacto del VPS.")
                }

                if let error {
                    Section {
                        VStack(alignment: .leading, spacing: 8) {
                            Text(error).font(.footnote).foregroundStyle(.red)
                            Button("Reintentar") {
                                Task { await guardar() }
                            }
                            .font(.footnote.weight(.semibold))
                            .disabled(guardando)
                        }
                    }
                }
            }
            .navigationTitle("Registrar MCP")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancelar") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Guardar") {
                        Task { await guardar() }
                    }
                    .disabled(guardando || nombre.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
            .overlay {
                if guardando { ProgressView() }
            }
        }
    }

    private func guardar() async {
        guard let client = session.client else { return }
        let nombreLimpio = nombre.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !nombreLimpio.isEmpty else { return }

        guardando = true
        error = nil
        defer { guardando = false }

        let input = MCPServerInput(
            nombre: nombreLimpio,
            transporte: transporte,
            url: transporte == "http" ? url.trimmingCharacters(in: .whitespacesAndNewlines) : nil,
            comando: transporte == "stdio" ? comando.trimmingCharacters(in: .whitespacesAndNewlines) : nil,
            headers: headers.isEmpty ? nil : headers,
            validate: validar
        )

        do {
            try await client.putMCPServer(input)
            onRegistrado?()
            dismiss()
        } catch {
            self.error = error.localizedDescription
        }
    }
}
