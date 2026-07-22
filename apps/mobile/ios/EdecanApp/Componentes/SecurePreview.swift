import EdecanKit
import PDFKit
import SwiftUI
import UIKit
import WebKit

enum SecurePreviewTarget: Identifiable {
    case artifact(ArtifactRef)
    case publicURL(URL)

    var id: String {
        switch self {
        case .artifact(let artifact): "artifact-\(artifact.fileId)"
        case .publicURL(let url): "url-\(url.absoluteString)"
        }
    }
}

/// Visor interno: archivos privados siempre llegan por el endpoint Bearer y
/// las URLs públicas navegan en un WKWebView efímero, sin JavaScript ni acceso
/// a hosts locales. Compartir sigue disponible como una acción explícita.
struct SecurePreviewSheet: View {
    @Environment(\.dismiss) private var dismiss
    let target: SecurePreviewTarget
    let client: APIClient?

    var body: some View {
        NavigationStack {
            Group {
                switch target {
                case .artifact(let artifact):
                    ArtifactPreview(artifact: artifact, client: client)
                case .publicURL(let url):
                    SafeWebPreview(url: url)
                }
            }
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cerrar") { dismiss() }
                }
            }
        }
    }

    private var title: String {
        switch target {
        case .artifact(let artifact): artifact.filename
        case .publicURL(let url): url.host ?? "Enlace"
        }
    }
}

private struct ArtifactPreview: View {
    enum State {
        case loading
        case ready(DownloadedArtifact)
        case failed(String)
    }

    let artifact: ArtifactRef
    let client: APIClient?
    @State private var state: State = .loading
    @State private var shareURL: URL?

    var body: some View {
        Group {
            switch state {
            case .loading:
                ProgressView("Abriendo de forma privada…")
            case .failed(let message):
                ContentUnavailableView(
                    "No se pudo abrir",
                    systemImage: "exclamationmark.triangle",
                    description: Text(message)
                )
            case .ready(let download):
                preview(download)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .safeAreaInset(edge: .bottom) {
            if let shareURL {
                ShareLink(item: shareURL) {
                    Label("Compartir o guardar", systemImage: "square.and.arrow.up")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .padding()
                .background(.bar)
            }
        }
        .task(id: artifact.fileId) { await load() }
        .onDisappear { cleanupShareFile() }
    }

    @ViewBuilder
    private func preview(_ download: DownloadedArtifact) -> some View {
        let mime = artifact.mime?.lowercased() ?? ""
        if mime.hasPrefix("image/"), let image = UIImage(data: download.data) {
            ScrollView([.horizontal, .vertical]) {
                Image(uiImage: image).resizable().scaledToFit().padding()
            }
        } else if mime == "application/pdf" || artifact.filename.lowercased().hasSuffix(".pdf") {
            PDFPreview(data: download.data)
        } else if isText(mime: mime, filename: artifact.filename) {
            ScrollView {
                Text(String(decoding: download.data.prefix(2_000_000), as: UTF8.self))
                    .font(.system(.body, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding()
            }
        } else {
            ContentUnavailableView(
                "Vista previa no disponible",
                systemImage: "doc",
                description: Text("Puedes guardar o compartir este archivo desde el botón inferior.")
            )
        }
    }

    private func load() async {
        guard let client else {
            state = .failed("No hay una sesión activa.")
            return
        }
        do {
            let download = try await client.descargarArtefacto(artifact)
            try Task.checkCancellation()
            state = .ready(download)
            let directory = FileManager.default.temporaryDirectory
                .appendingPathComponent("edecan-preview-\(UUID().uuidString)", isDirectory: true)
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let safeName = artifact.filename.replacingOccurrences(of: "/", with: "-")
                .replacingOccurrences(of: "\\", with: "-")
            let url = directory.appendingPathComponent(String(safeName.prefix(180)).isEmpty ? "archivo" : String(safeName.prefix(180)))
            try download.data.write(to: url, options: .atomic)
            shareURL = url
        } catch is CancellationError {
            return
        } catch {
            state = .failed(error.localizedDescription)
        }
    }

    private func cleanupShareFile() {
        guard let shareURL else { return }
        try? FileManager.default.removeItem(at: shareURL.deletingLastPathComponent())
        self.shareURL = nil
    }

    private func isText(mime: String, filename: String) -> Bool {
        mime.hasPrefix("text/") || mime == "application/json" || mime.hasSuffix("+json") ||
            mime == "application/xml" || mime.hasSuffix("+xml") ||
            [".md", ".txt", ".csv", ".json", ".xml", ".yaml", ".yml"].contains {
                filename.lowercased().hasSuffix($0)
            }
    }
}

private struct PDFPreview: UIViewRepresentable {
    let data: Data

    func makeUIView(context: Context) -> PDFView {
        let view = PDFView()
        view.autoScales = true
        view.displayMode = .singlePageContinuous
        view.displayDirection = .vertical
        view.document = PDFDocument(data: data)
        return view
    }

    func updateUIView(_ uiView: PDFView, context: Context) {}
}

private struct SafeWebPreview: UIViewRepresentable {
    let url: URL

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = false
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = false
        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = context.coordinator
        webView.allowsLinkPreview = false
        if let safe = ChatAction.httpURLSegura(url.absoluteString) {
            // El delegate protege cada navegación principal. La lista de
            // contenido aplica la misma frontera a imágenes/iframes y demás
            // subrecursos para que una página pública no sondee la LAN.
            Task { @MainActor in
                guard let rules = try? await WKContentRuleListStore.default().compileContentRuleList(
                    forIdentifier: "cc.edecan.block-private-network.v1",
                    encodedContentRuleList: Self.privateNetworkRules
                ) else { return }
                configuration.userContentController.add(rules)
                webView.load(URLRequest(url: safe, cachePolicy: .reloadIgnoringLocalCacheData))
            }
        }
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {}

    @MainActor
    final class Coordinator: NSObject, WKNavigationDelegate {
        func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationAction: WKNavigationAction
        ) async -> WKNavigationActionPolicy {
            guard let candidate = navigationAction.request.url,
                  ChatAction.httpURLSegura(candidate.absoluteString) != nil
            else {
                return .cancel
            }
            return .allow
        }
    }

    private static let privateNetworkRules = #"""
    [
      {"trigger":{"url-filter":"^https?://localhost","url-filter-is-case-sensitive":false},"action":{"type":"block"}},
      {"trigger":{"url-filter":"^https?://[^/]+\\.localhost","url-filter-is-case-sensitive":false},"action":{"type":"block"}},
      {"trigger":{"url-filter":"^https?://[^/]+\\.local","url-filter-is-case-sensitive":false},"action":{"type":"block"}},
      {"trigger":{"url-filter":"^https?://[^/]+\\.internal","url-filter-is-case-sensitive":false},"action":{"type":"block"}},
      {"trigger":{"url-filter":"^https?://0\\.","url-filter-is-case-sensitive":false},"action":{"type":"block"}},
      {"trigger":{"url-filter":"^https?://10\\.","url-filter-is-case-sensitive":false},"action":{"type":"block"}},
      {"trigger":{"url-filter":"^https?://127\\.","url-filter-is-case-sensitive":false},"action":{"type":"block"}},
      {"trigger":{"url-filter":"^https?://169\\.254\\.","url-filter-is-case-sensitive":false},"action":{"type":"block"}},
      {"trigger":{"url-filter":"^https?://192\\.168\\.","url-filter-is-case-sensitive":false},"action":{"type":"block"}},
      {"trigger":{"url-filter":"^https?://172\\.1[6-9]\\.","url-filter-is-case-sensitive":false},"action":{"type":"block"}},
      {"trigger":{"url-filter":"^https?://172\\.2[0-9]\\.","url-filter-is-case-sensitive":false},"action":{"type":"block"}},
      {"trigger":{"url-filter":"^https?://172\\.3[01]\\.","url-filter-is-case-sensitive":false},"action":{"type":"block"}},
      {"trigger":{"url-filter":"^https?://100\\.6[4-9]\\.","url-filter-is-case-sensitive":false},"action":{"type":"block"}},
      {"trigger":{"url-filter":"^https?://100\\.[7-9][0-9]\\.","url-filter-is-case-sensitive":false},"action":{"type":"block"}},
      {"trigger":{"url-filter":"^https?://100\\.1[01][0-9]\\.","url-filter-is-case-sensitive":false},"action":{"type":"block"}},
      {"trigger":{"url-filter":"^https?://100\\.12[0-7]\\.","url-filter-is-case-sensitive":false},"action":{"type":"block"}},
      {"trigger":{"url-filter":"^https?://198\\.1[89]\\.","url-filter-is-case-sensitive":false},"action":{"type":"block"}},
      {"trigger":{"url-filter":"^https?://\\[::1\\]","url-filter-is-case-sensitive":false},"action":{"type":"block"}},
      {"trigger":{"url-filter":"^https?://\\[fc","url-filter-is-case-sensitive":false},"action":{"type":"block"}},
      {"trigger":{"url-filter":"^https?://\\[fd","url-filter-is-case-sensitive":false},"action":{"type":"block"}},
      {"trigger":{"url-filter":"^https?://\\[fe[89ab]","url-filter-is-case-sensitive":false},"action":{"type":"block"}},
      {"trigger":{"url-filter":"^https?://\\[ff","url-filter-is-case-sensitive":false},"action":{"type":"block"}}
    ]
    """#
}
