import UIKit
import UniformTypeIdentifiers
import EdecanKit

/// Share Extension deliberately supports text/URLs first. Binary attachments
/// need an App Group + security-scoped file handoff and are kept out of this
/// path until that storage contract is signed and tested.
@MainActor
final class ShareViewController: UIViewController {
    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        guard let items = extensionContext?.inputItems as? [NSExtensionItem] else {
            finish()
            return
        }
        extractText(from: items)
    }

    private func extractText(from items: [NSExtensionItem]) {
        let providers = items.flatMap { $0.attachments ?? [] }
        let textType = UTType.text.identifier
        let urlType = UTType.url.identifier
        let group = DispatchGroup()
        let accumulator = ExtractionAccumulator()

        for provider in providers {
            let type = provider.hasItemConformingToTypeIdentifier(urlType) ? urlType : textType
            if !provider.hasItemConformingToTypeIdentifier(type),
               provider.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier) {
                group.enter()
                provider.loadFileRepresentation(
                    forTypeIdentifier: UTType.fileURL.identifier
                ) { source, _ in
                    defer { group.leave() }
                    guard let source else { return }
                    if let payload = Self.copyFile(source) {
                        accumulator.append(payload: payload)
                    }
                }
                continue
            }
            guard provider.hasItemConformingToTypeIdentifier(type) else {
                if provider.hasItemConformingToTypeIdentifier(UTType.image.identifier) {
                    group.enter()
                    provider.loadFileRepresentation(
                        forTypeIdentifier: UTType.image.identifier
                    ) { source, _ in
                        defer { group.leave() }
                        guard let source else { return }
                        if let payload = Self.copyFile(source) {
                            accumulator.append(payload: payload)
                        }
                    }
                }
                continue
            }
            group.enter()
            provider.loadItem(forTypeIdentifier: type, options: nil) { item, _ in
                defer { group.leave() }
                let value: String?
                if let url = item as? URL {
                    value = url.absoluteString
                } else if let text = item as? String {
                    value = text
                } else if let data = item as? Data {
                    value = String(data: data, encoding: .utf8)
                } else {
                    value = nil
                }
                guard let value, !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                    return
                }
                accumulator.append(
                    value: value,
                    payload: SharedSharePayload(
                        kind: type == urlType ? "url" : "text", value: value
                    )
                )
            }
        }

        group.notify(queue: .main) { [accumulator] in
            let result = accumulator.snapshot()
            Task { @MainActor [weak self] in
                self?.finishExtraction(values: result.values, payloads: result.payloads)
            }
        }
    }

    @MainActor
    private func finishExtraction(values: [String], payloads: [SharedSharePayload]) {
        let text = values.joined(separator: "\n\n").trimmingCharacters(in: .whitespacesAndNewlines)
        SharePayloadStore.enqueue(payloads)
        guard !text.isEmpty || !payloads.isEmpty else {
            finish()
            return
        }
        var components = URLComponents()
        components.scheme = "edecan"
        components.host = "share"
        components.queryItems = [URLQueryItem(name: "text", value: String(text.prefix(10_000)))]
        guard let url = components.url else {
            finish()
            return
        }
        extensionContext?.open(url) { [weak self] _ in
            Task { @MainActor [weak self] in self?.finish() }
        }
    }

    private static func copyFile(_ source: URL) -> SharedSharePayload? {
        guard let container = SharePayloadStore.containerURL() else { return nil }
        let inbox = container.appendingPathComponent("ShareInbox", isDirectory: true)
        try? FileManager.default.createDirectory(at: inbox, withIntermediateDirectories: true)
        let filename = "\(UUID().uuidString)-\(source.lastPathComponent)"
        let destination = inbox.appendingPathComponent(filename)
        try? FileManager.default.removeItem(at: destination)
        guard (try? FileManager.default.copyItem(at: source, to: destination)) != nil else {
            return nil
        }
        let mime = UTType(filenameExtension: source.pathExtension)?.preferredMIMEType
        return SharedSharePayload(
            kind: "file",
            value: destination.path,
            filename: source.lastPathComponent,
            mimeType: mime
        )
    }

    private func finish() {
        extensionContext?.completeRequest(returningItems: nil)
    }
}

private final class ExtractionAccumulator: @unchecked Sendable {
    private let lock = NSLock()
    private var values: [String] = []
    private var payloads: [SharedSharePayload] = []

    func append(value: String, payload: SharedSharePayload) {
        lock.lock()
        values.append(value)
        payloads.append(payload)
        lock.unlock()
    }

    func append(payload: SharedSharePayload) {
        lock.lock()
        payloads.append(payload)
        lock.unlock()
    }

    func snapshot() -> (values: [String], payloads: [SharedSharePayload]) {
        lock.lock()
        defer { lock.unlock() }
        return (values, payloads)
    }
}
