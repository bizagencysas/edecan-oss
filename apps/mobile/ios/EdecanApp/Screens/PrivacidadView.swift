import SwiftUI
import EdecanKit

/// Controles de privacidad disponibles en iOS. El borrado de cuenta completo
/// permanece en el flujo web reforzado porque necesita contraseña/TOTP y
/// preflight de dependencias externas.
struct PrivacidadView: View {
    @Environment(SessionStore.self) private var session
    @State private var exportURL: URL?
    @State private var busy = false
    @State private var mensaje: String?
    @State private var error: String?
    @State private var confirmandoMemoria = false

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                Text("Controla lo que Edecán conserva y comparte. Estos controles no incluyen credenciales operativas.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)

                Button {
                    Task { await exportar() }
                } label: {
                    fila(icono: "arrow.down.doc.fill", titulo: "Exportar mis datos", subtitulo: "Genera un JSON para guardarlo o compartirlo")
                }
                .buttonStyle(.plain)
                .disabled(busy)

                if let exportURL {
                    ShareLink(item: exportURL) {
                        Label("Compartir exportación", systemImage: "square.and.arrow.up")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                }

                Button(role: .destructive) { confirmandoMemoria = true } label: {
                    fila(icono: "trash.fill", titulo: "Eliminar mi memoria", subtitulo: "Borra recuerdos personales, no conversaciones ni cuenta")
                }
                .buttonStyle(.plain)
                .disabled(busy)

                if let mensaje { Text(mensaje).font(.footnote).foregroundStyle(.green) }
                if let error { Text(error).font(.footnote).foregroundStyle(.red) }
            }
            .padding()
        }
        .background(EdecanTheme.degradado.opacity(0.05).ignoresSafeArea())
        .navigationTitle("Privacidad")
        .navigationBarTitleDisplayMode(.inline)
        .confirmationDialog("¿Eliminar toda tu memoria?", isPresented: $confirmandoMemoria, titleVisibility: .visible) {
            Button("Eliminar memoria", role: .destructive) { Task { await borrarMemoria() } }
            Button("Cancelar", role: .cancel) {}
        } message: {
            Text("Se borran los recuerdos guardados para personalizar respuestas. Tus conversaciones permanecen.")
        }
    }

    private func exportar() async {
        guard let client = session.client else { return }
        busy = true; error = nil; mensaje = nil
        defer { busy = false }
        do {
            let data = try await client.exportarDatosPrivacidad()
            let url = FileManager.default.temporaryDirectory
                .appendingPathComponent("edecan-datos-\(UUID().uuidString).json")
            try data.write(to: url, options: .atomic)
            exportURL = url
            mensaje = "Exportación lista para compartir."
        } catch let caught { error = caught.localizedDescription }
    }

    private func borrarMemoria() async {
        guard let client = session.client else { return }
        busy = true; error = nil; mensaje = nil
        defer { busy = false }
        do {
            try await client.borrarMemoriaCompleta()
            mensaje = "Tu memoria fue eliminada."
        } catch let caught { error = caught.localizedDescription }
    }

    private func fila(icono: String, titulo: String, subtitulo: String) -> some View {
        HStack(spacing: 14) {
            Image(systemName: icono)
                .foregroundStyle(EdecanTheme.morado)
                .frame(width: 42, height: 42)
                .background(EdecanTheme.morado.opacity(0.12), in: RoundedRectangle(cornerRadius: 11))
            VStack(alignment: .leading, spacing: 3) {
                Text(titulo).font(.headline).foregroundStyle(.primary)
                Text(subtitulo).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Image(systemName: "chevron.right").font(.caption).foregroundStyle(.tertiary)
        }
        .padding(16)
        .tarjetaVidrio(esquina: 20)
    }
}
