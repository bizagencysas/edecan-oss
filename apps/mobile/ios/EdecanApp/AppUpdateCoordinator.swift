import EdecanKit
import Foundation
import Observation
import SwiftUI

/// Consulta un manifiesto público de distribución y expone únicamente una
/// actualización que avance la versión instalada.
///
/// iOS decide la instalación: Edecán abre App Store, TestFlight,
/// AltStore/SideStore o la página HTTPS configurada. Este coordinador jamás
/// descarga ni instala ejecutables, no usa credenciales y falla en silencio
/// cuando el teléfono está sin conexión.
@MainActor
@Observable
final class AppUpdateCoordinator {
    private struct Configuration {
        let channel: IOSUpdateChannel
        let manifestURL: URL
    }

    private enum StorageKey {
        static let prefix = "cc.edecan.ios-updates."
        static let cachedManifest = "\(prefix)manifest"
        static let cachedSource = "\(prefix)source"
        static let cachedChannel = "\(prefix)channel"
        static let etag = "\(prefix)etag"
        static let lastSuccessfulCheck = "\(prefix)last-successful-check"
        static let lastPresentedUpdate = "\(prefix)last-presented-update"
    }

    private static let checkInterval: TimeInterval = 4 * 60 * 60
    private static let failedAttemptInterval: TimeInterval = 5 * 60

    private(set) var availableUpdate: IOSUpdateManifest?
    private(set) var bannerUpdate: IOSUpdateManifest?

    private let configuration: Configuration?
    private let defaults: UserDefaults
    private let urlSession: URLSession
    private let installedVersion: String
    private let installedBuild: Int?
    private var checking = false
    private var lastFailedAttempt: Date?

    init(
        bundle: Bundle = .main,
        defaults: UserDefaults = .standard
    ) {
        self.defaults = defaults
        installedVersion = bundle.object(
            forInfoDictionaryKey: "CFBundleShortVersionString"
        ) as? String ?? "0.0.0"
        if let rawBuild = bundle.object(
            forInfoDictionaryKey: "CFBundleVersion"
        ) as? String {
            installedBuild = Int(rawBuild)
        } else {
            installedBuild = nil
        }

        let sessionConfiguration = URLSessionConfiguration.ephemeral
        sessionConfiguration.urlCache = nil
        sessionConfiguration.httpCookieStorage = nil
        sessionConfiguration.requestCachePolicy = .reloadIgnoringLocalCacheData
        sessionConfiguration.timeoutIntervalForRequest = 12
        sessionConfiguration.timeoutIntervalForResource = 20
        urlSession = URLSession(configuration: sessionConfiguration)

        let rawChannel = bundle.object(
            forInfoDictionaryKey: "EdecanUpdateChannel"
        ) as? String
        let rawManifestURL = bundle.object(
            forInfoDictionaryKey: "EdecanUpdateManifestURL"
        ) as? String

        if let rawChannel,
           let channel = IOSUpdateChannel(rawValue: rawChannel.lowercased()),
           let rawManifestURL,
           let manifestURL = try? IOSUpdateManifestParser.validatedManifestURL(rawManifestURL) {
            configuration = Configuration(channel: channel, manifestURL: manifestURL)
        } else {
            configuration = nil
        }
    }

    /// Se invoca al volver a foreground. Respeta el intervalo de cuatro horas
    /// y usa la última respuesta válida si la red no está disponible.
    func checkIfNeeded(force: Bool = false) async {
        guard let configuration, !checking else { return }

        let cached = cachedManifest(for: configuration)
        if !force, !shouldCheckNetwork(for: configuration) {
            evaluate(cached, configuration: configuration)
            return
        }

        checking = true
        defer { checking = false }

        do {
            let manifest = try await fetchManifest(
                configuration: configuration,
                cachedManifest: cached
            )
            defaults.set(
                Date().timeIntervalSince1970,
                forKey: StorageKey.lastSuccessfulCheck
            )
            lastFailedAttempt = nil
            evaluate(manifest, configuration: configuration)
        } catch is CancellationError {
            evaluate(cached, configuration: configuration)
        } catch {
            lastFailedAttempt = Date()
            evaluate(cached, configuration: configuration)
        }
    }

    func dismissBanner() {
        guard let bannerUpdate else { return }
        defaults.set(
            bannerUpdate.presentationID,
            forKey: StorageKey.lastPresentedUpdate
        )
        self.bannerUpdate = nil
    }

    func markUpdateOpened(_ update: IOSUpdateManifest) {
        defaults.set(
            update.presentationID,
            forKey: StorageKey.lastPresentedUpdate
        )
        if bannerUpdate?.presentationID == update.presentationID {
            bannerUpdate = nil
        }
    }

    private func shouldCheckNetwork(for configuration: Configuration) -> Bool {
        if let lastFailedAttempt,
           Date().timeIntervalSince(lastFailedAttempt) < Self.failedAttemptInterval {
            return false
        }
        guard cacheBelongs(to: configuration) else { return true }
        let lastCheck = defaults.double(forKey: StorageKey.lastSuccessfulCheck)
        guard lastCheck > 0 else { return true }
        return Date().timeIntervalSince1970 - lastCheck >= Self.checkInterval
    }

    private func fetchManifest(
        configuration: Configuration,
        cachedManifest: IOSUpdateManifest?
    ) async throws -> IOSUpdateManifest? {
        var request = URLRequest(url: configuration.manifestURL)
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("Edecan-iOS/\(installedVersion)", forHTTPHeaderField: "User-Agent")
        if cachedManifest != nil,
           cacheBelongs(to: configuration),
           let etag = defaults.string(forKey: StorageKey.etag),
           !etag.isEmpty {
            request.setValue(etag, forHTTPHeaderField: "If-None-Match")
        }

        let (data, response) = try await urlSession.data(for: request)
        guard let response = response as? HTTPURLResponse else {
            throw URLError(.badServerResponse)
        }

        if response.statusCode == 304, let cachedManifest {
            return cachedManifest
        }
        guard (200 ... 299).contains(response.statusCode) else {
            throw URLError(.badServerResponse)
        }

        let manifest = try IOSUpdateManifestParser.parse(data)
        store(
            data: data,
            response: response,
            configuration: configuration
        )
        return manifest
    }

    private func store(
        data: Data,
        response: HTTPURLResponse,
        configuration: Configuration
    ) {
        defaults.set(data, forKey: StorageKey.cachedManifest)
        defaults.set(
            configuration.manifestURL.absoluteString,
            forKey: StorageKey.cachedSource
        )
        defaults.set(
            configuration.channel.rawValue,
            forKey: StorageKey.cachedChannel
        )
        if let etag = response.value(forHTTPHeaderField: "ETag"), !etag.isEmpty {
            defaults.set(etag, forKey: StorageKey.etag)
        } else {
            defaults.removeObject(forKey: StorageKey.etag)
        }
    }

    private func cachedManifest(
        for configuration: Configuration
    ) -> IOSUpdateManifest? {
        guard cacheBelongs(to: configuration),
              let data = defaults.data(forKey: StorageKey.cachedManifest)
        else { return nil }
        return try? IOSUpdateManifestParser.parse(data)
    }

    private func cacheBelongs(to configuration: Configuration) -> Bool {
        defaults.string(forKey: StorageKey.cachedSource)
            == configuration.manifestURL.absoluteString
            && defaults.string(forKey: StorageKey.cachedChannel)
            == configuration.channel.rawValue
    }

    private func evaluate(
        _ manifest: IOSUpdateManifest?,
        configuration: Configuration
    ) {
        guard let manifest,
              IOSUpdatePolicy.isAvailable(
                manifest,
                currentVersion: installedVersion,
                currentBuild: installedBuild,
                configuredChannel: configuration.channel
              )
        else {
            availableUpdate = nil
            bannerUpdate = nil
            return
        }

        availableUpdate = manifest
        let alreadyPresented = defaults.string(
            forKey: StorageKey.lastPresentedUpdate
        ) == manifest.presentationID
        bannerUpdate = alreadyPresented ? nil : manifest
    }

}

/// Aviso compacto; no bloquea el chat ni reaparece para la misma versión una
/// vez descartado. La ficha persistente sigue disponible en “Tú”.
struct AppUpdateBanner: View {
    let update: IOSUpdateManifest
    let onDismiss: () -> Void
    let onOpen: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "arrow.down.app.fill")
                .font(.title3)
                .foregroundStyle(EdecanTheme.morado)

            VStack(alignment: .leading, spacing: 2) {
                Text("Edecán \(update.versionText) disponible")
                    .font(.subheadline.weight(.semibold))
                if !update.releaseNotes.isEmpty {
                    Text(update.releaseNotes)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }

            Spacer(minLength: 4)

            Button(update.installKind.actionTitle, action: onOpen)
                .font(.caption.weight(.semibold))
                .buttonStyle(.borderedProminent)
                .tint(EdecanTheme.morado)

            Button(action: onDismiss) {
                Image(systemName: "xmark")
                    .font(.caption.weight(.bold))
            }
            .buttonStyle(.plain)
            .foregroundStyle(.secondary)
            .accessibilityLabel("Recordar después")
        }
        .padding(12)
        .tarjetaVidrio(esquina: 18)
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .accessibilityElement(children: .contain)
    }
}
