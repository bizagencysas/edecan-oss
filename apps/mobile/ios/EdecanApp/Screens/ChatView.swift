import SwiftUI
import EdecanKit
import UIKit

/// Superficie principal "Edecan" — lista de mensajes, input y envío hacia
/// `POST /v1/conversations/{id}/messages` (SSE) apendeando `text_delta` a
/// medida que llega, más un indicador mientras el agente usa una
/// herramienta (`tool_start`/`tool_end`). Lógica real en ``ChatViewModel``;
/// esta vista solo dibuja su estado.
struct ChatView: View {
    @Environment(SessionStore.self) private var session
    @Environment(TabRouter.self) private var tabRouter
    @State private var viewModel = ChatViewModel()
    @State private var textoActual = ""
    @State private var mostrandoVoz = false
    @State private var artefactoDescargandoId: String?
    @State private var archivoCompartible: ArchivoCompartible?
    @FocusState private var campoEnfocado: Bool

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                listaDeMensajes
                if let error = viewModel.errorMensaje {
                    Text(error)
                        .font(.footnote)
                        .foregroundStyle(.red)
                        .padding(.horizontal)
                        .padding(.top, 6)
                }
                barraDeEntrada
            }
            .background(EdecanTheme.degradado.opacity(0.05).ignoresSafeArea())
            .navigationTitle("Edecan")
            .navigationBarTitleDisplayMode(.inline)
            .sheet(isPresented: $mostrandoVoz) {
                VozView()
            }
            .sheet(item: $archivoCompartible) { archivo in
                HojaCompartir(items: [archivo.url])
            }
            .onChange(of: tabRouter.solicitudPendiente?.id) { _, nuevaId in
                guard nuevaId != nil, let solicitud = tabRouter.consumirSolicitud() else { return }
                guard let client = session.client else {
                    viewModel.errorMensaje = "No hay sesión activa."
                    return
                }
                Task { await viewModel.enviar(texto: solicitud.texto, client: client) }
            }
        }
    }

    private var listaDeMensajes: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    if viewModel.mensajes.isEmpty {
                        EmptyStateView(
                            icono: "bubble.left.and.bubble.right",
                            titulo: "Empieza una conversación",
                            descripcion: "Escríbele a Edecán abajo — puede agendar, buscar en tu correo, resumir finanzas y más, con tu confirmación cuando haga falta."
                        )
                        .padding(.top, 60)
                    }
                    ForEach(viewModel.mensajes) { mensaje in
                        BurbujaMensaje(
                            mensaje: mensaje,
                            artefactoDescargandoId: artefactoDescargandoId,
                            onAbrirArtefacto: descargarYCompartir
                        )
                        .id(mensaje.id)
                    }
                    if let herramienta = viewModel.herramientaActiva {
                        IndicadorHerramienta(nombre: herramienta)
                    }
                    if let confirmacion = viewModel.confirmacionPendiente {
                        TarjetaConfirmacion(confirmacion: confirmacion, deshabilitada: viewModel.enviando) { aprobado in
                            resolverConfirmacion(aprobado: aprobado)
                        }
                    }
                }
                .padding()
            }
            .onChange(of: viewModel.mensajes.count) { _, _ in
                desplazarAlFinal(proxy)
            }
            .onChange(of: viewModel.mensajes.last?.texto) { _, _ in
                desplazarAlFinal(proxy)
            }
        }
    }

    private func desplazarAlFinal(_ proxy: ScrollViewProxy) {
        guard let ultimo = viewModel.mensajes.last else { return }
        withAnimation(.easeOut(duration: 0.2)) {
            proxy.scrollTo(ultimo.id, anchor: .bottom)
        }
    }

    private var barraDeEntrada: some View {
        HStack(alignment: .bottom, spacing: 12) {
            Button {
                mostrandoVoz = true
            } label: {
                Image(systemName: "mic.fill")
                    .font(.system(size: 18, weight: .semibold))
                    .frame(width: 42, height: 42)
                    .foregroundStyle(EdecanTheme.morado)
                    .tarjetaVidrio(esquina: 18)
            }
            .accessibilityLabel("Hablar con Edecan")

            TextField("Escríbele a Edecán…", text: $textoActual, axis: .vertical)
                .lineLimit(1...4)
                .focused($campoEnfocado)
                .textFieldStyle(.plain)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .tarjetaVidrio(esquina: 18)

            Button(action: enviarMensajeActual) {
                Image(systemName: "arrow.up.circle.fill")
                    .font(.system(size: 32))
                    .foregroundStyle(botonHabilitado ? AnyShapeStyle(EdecanTheme.degradado) : AnyShapeStyle(.tertiary))
            }
            .disabled(!botonHabilitado)
        }
        .padding()
    }

    private var botonHabilitado: Bool {
        !textoActual.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !viewModel.enviando
            && viewModel.confirmacionPendiente == nil
    }

    private func enviarMensajeActual() {
        guard let client = session.client else {
            viewModel.errorMensaje = "No hay sesión activa."
            return
        }
        let texto = textoActual
        textoActual = ""
        Task { await viewModel.enviar(texto: texto, client: client) }
    }

    private func resolverConfirmacion(aprobado: Bool) {
        guard let client = session.client else {
            viewModel.errorMensaje = "No hay sesión activa."
            return
        }
        Task { await viewModel.resolverConfirmacion(aprobado: aprobado, client: client) }
    }

    /// Descarga los bytes con Bearer mediante `APIClient`, los guarda solo
    /// en el directorio temporal privado de la app y abre la hoja nativa.
    /// Desde ahi la persona elige Guardar en Archivos, AirDrop u otra app.
    private func descargarYCompartir(_ artefacto: ArtifactRef) {
        guard let client = session.client, artefactoDescargandoId == nil else {
            if session.client == nil { viewModel.errorMensaje = "No hay sesión activa." }
            return
        }
        artefactoDescargandoId = artefacto.fileId
        viewModel.errorMensaje = nil
        Task {
            defer { artefactoDescargandoId = nil }
            do {
                let descarga = try await client.descargarArtefacto(artefacto)
                let url = try await Task.detached(priority: .userInitiated) {
                    try guardarArtefactoTemporal(descarga)
                }.value
                archivoCompartible = ArchivoCompartible(url: url)
            } catch {
                viewModel.errorMensaje = "No se pudo descargar \(artefacto.filename): \(error.localizedDescription)"
            }
        }
    }

}

private func guardarArtefactoTemporal(_ descarga: DownloadedArtifact) throws -> URL {
    let fileManager = FileManager.default
    let idSeguro = descarga.artifact.fileId
        .filter { $0.isLetter || $0.isNumber || $0 == "-" || $0 == "_" }
        .prefix(80)
    let carpetaId = idSeguro.isEmpty ? "archivo" : String(idSeguro)
    let raiz = fileManager.temporaryDirectory
        .appendingPathComponent("edecan-artifacts", isDirectory: true)
        .appendingPathComponent(carpetaId, isDirectory: true)
    try fileManager.createDirectory(at: raiz, withIntermediateDirectories: true)
    let url = raiz.appendingPathComponent(nombreSeguro(descarga.artifact.filename), isDirectory: false)
    try descarga.data.write(to: url, options: .atomic)
    return url
}

private func nombreSeguro(_ filename: String) -> String {
    let normalizado = filename.replacingOccurrences(of: "\\", with: "/")
    let ultimo = normalizado.split(separator: "/").last.map(String.init) ?? "archivo"
    let limpio = ultimo
        .replacingOccurrences(of: ":", with: "-")
        .trimmingCharacters(in: .whitespacesAndNewlines)
    return limpio.isEmpty ? "archivo" : String(limpio.prefix(180))
}

private struct ArchivoCompartible: Identifiable {
    let id = UUID()
    let url: URL
}

private struct HojaCompartir: UIViewControllerRepresentable {
    let items: [Any]

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }

    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {}
}
