import AuthenticationServices
import UIKit

/// Abre el flujo OAuth del proveedor en `ASWebAuthenticationSession`.
/// El callback HTTPS del VPS redirige a `edecan://conectores?...` y cierra la sesión.
@MainActor
enum SocialOAuthSession {
    private static var activeSession: ASWebAuthenticationSession?
    private static let anchorProvider = PresentationAnchorProvider()

    enum OAuthResult: Sendable {
        case success(key: String)
        case failure(key: String, error: String)
        case cancelled
    }

    static func start(providerURL: URL) async -> OAuthResult {
        await withCheckedContinuation { continuation in
            activeSession?.cancel()
            let session = ASWebAuthenticationSession(
                url: providerURL,
                callbackURLScheme: "edecan"
            ) { callbackURL, error in
                activeSession = nil
                if let error = error as NSError?, error.domain == ASWebAuthenticationSessionErrorDomain,
                   error.code == ASWebAuthenticationSessionError.canceledLogin.rawValue {
                    continuation.resume(returning: .cancelled)
                    return
                }
                if let error {
                    continuation.resume(returning: .failure(key: "", error: error.localizedDescription))
                    return
                }
                guard let callbackURL else {
                    continuation.resume(returning: .failure(key: "", error: "Callback vacío"))
                    return
                }
                continuation.resume(returning: parseCallback(callbackURL))
            }
            session.presentationContextProvider = anchorProvider
            session.prefersEphemeralWebBrowserSession = false
            activeSession = session
            if !session.start() {
                continuation.resume(returning: .failure(key: "", error: "No se pudo abrir la sesión OAuth"))
            }
        }
    }

    static func parseCallback(_ url: URL) -> OAuthResult {
        guard url.scheme == "edecan", url.host == "conectores" else {
            return .failure(key: "", error: "Deep link inesperado: \(url.absoluteString)")
        }
        let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
        let dict = Dictionary(uniqueKeysWithValues: items.compactMap { item in
            item.value.map { (item.name, $0) }
        })
        let key = dict["key"] ?? ""
        if dict["ok"] == "1" {
            return .success(key: key)
        }
        return .failure(key: key, error: dict["error"] ?? "sin_codigo")
    }

    private final class PresentationAnchorProvider: NSObject, ASWebAuthenticationPresentationContextProviding {
        func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
            UIApplication.shared.connectedScenes
                .compactMap { $0 as? UIWindowScene }
                .flatMap(\.windows)
                .first { $0.isKeyWindow } ?? ASPresentationAnchor()
        }
    }
}
