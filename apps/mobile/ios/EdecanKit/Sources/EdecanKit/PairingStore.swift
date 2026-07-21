import Foundation
import Observation

/// Contenido validado de `edecan://pair`. URLComponents ya entrega los query
/// items percent-decoded; el token se conserva opaco y nunca se registra.
public struct PairingLink: Sendable, Equatable, Identifiable {
    public enum ParseError: Error, LocalizedError, Sendable, Equatable {
        case invalidScheme
        case invalidRoute
        case missingServer
        case unsafeServer
        case missingToken
        case invalidToken

        public var errorDescription: String? {
            switch self {
            case .invalidScheme, .invalidRoute:
                return "Este enlace no es un enlace de emparejamiento de Edecan."
            case .missingServer:
                return "El enlace no incluye la dirección del servidor."
            case .unsafeServer:
                return "La dirección del servidor del QR no es válida."
            case .missingToken:
                return "El enlace no incluye el token de emparejamiento."
            case .invalidToken:
                return "El token de emparejamiento está dañado. Genera un QR nuevo."
            }
        }
    }

    public let serverURL: URL
    public let token: String
    /// Identidad efímera para que SwiftUI procese enlaces consecutivos sin
    /// incorporar el secreto opaco al estado visible o a descripciones.
    public let id: UUID

    public init(url: URL) throws {
        guard url.scheme?.lowercased() == "edecan" else { throw ParseError.invalidScheme }
        guard url.host?.lowercased() == "pair", url.path.isEmpty || url.path == "/" else {
            throw ParseError.invalidRoute
        }
        guard let components = URLComponents(url: url, resolvingAgainstBaseURL: false) else {
            throw ParseError.invalidRoute
        }
        let servers = components.queryItems?.filter { $0.name == "server" }.compactMap(\.value) ?? []
        let tokens = components.queryItems?.filter { $0.name == "token" }.compactMap(\.value) ?? []
        guard servers.count == 1, !servers[0].isEmpty else { throw ParseError.missingServer }
        guard tokens.count == 1, !tokens[0].isEmpty else { throw ParseError.missingToken }
        guard let server = try? ServerURLPolicy.parseAndValidate(servers[0]) else {
            throw ParseError.unsafeServer
        }
        let opaque = tokens[0]
        guard opaque.count <= 4_096,
              opaque.unicodeScalars.allSatisfy({ scalar in
                  !CharacterSet.whitespacesAndNewlines.contains(scalar)
                      && !CharacterSet.controlCharacters.contains(scalar)
              })
        else { throw ParseError.invalidToken }
        serverURL = server
        token = opaque
        id = UUID()
    }
}

/// Estado durable de ESTE iPhone. Un JWT existente o la pareja durable
/// `deviceId/deviceToken` bastan para intentar recuperar la sesión.
@MainActor
@Observable
public final class PairingStore {
    public private(set) var serverURL: URL?
    public private(set) var isPaired: Bool
    public private(set) var deviceId: String?
    public private(set) var pendingPairingLink: PairingLink?
    public private(set) var pairingLinkError: String?

    public init() {
        let persistedServerURL: URL?
        if let raw = Keychain.get(KeychainKeys.serverURL),
           let url = try? ServerURLPolicy.parseAndValidate(raw) {
            persistedServerURL = url
        } else {
            persistedServerURL = nil
            Keychain.delete(KeychainKeys.serverURL)
        }
        serverURL = persistedServerURL
        let durableId = Keychain.get(KeychainKeys.deviceId)
        let durableToken = Keychain.get(KeychainKeys.deviceToken)
        deviceId = durableId
        isPaired = persistedServerURL != nil
            && (Keychain.get(KeychainKeys.refreshToken) != nil
                || (durableId != nil && durableToken != nil))
    }

    public func guardarServidor(_ url: URL) throws {
        let safeURL = try ServerURLPolicy.validate(url)
        Keychain.set(safeURL.absoluteString, for: KeychainKeys.serverURL)
        serverURL = safeURL
    }

    public func marcarEmparejado() {
        isPaired = true
    }

    public func guardarDeviceId(_ id: String) {
        Keychain.set(id, for: KeychainKeys.deviceId)
        deviceId = id
    }

    public func completarEmparejamientoQR(
        serverURL: URL,
        deviceId: String,
        deviceToken: String
    ) throws {
        let safeURL = try ServerURLPolicy.validate(serverURL)
        Keychain.set(safeURL.absoluteString, for: KeychainKeys.serverURL)
        Keychain.set(deviceId, for: KeychainKeys.deviceId)
        Keychain.setDeviceBound(deviceToken, for: KeychainKeys.deviceToken)
        self.serverURL = safeURL
        self.deviceId = deviceId
        isPaired = true
        pendingPairingLink = nil
        pairingLinkError = nil
    }

    public func recibirEnlace(_ url: URL) {
        do {
            pendingPairingLink = try PairingLink(url: url)
            pairingLinkError = nil
        } catch {
            pendingPairingLink = nil
            pairingLinkError = error.localizedDescription
        }
    }

    public func consumirEnlacePendiente() -> PairingLink? {
        defer { pendingPairingLink = nil }
        return pendingPairingLink
    }

    public func limpiarErrorDeEnlace() {
        pairingLinkError = nil
    }

    public func olvidarEmparejamiento() {
        Keychain.delete(KeychainKeys.accessToken)
        Keychain.delete(KeychainKeys.refreshToken)
        Keychain.delete(KeychainKeys.deviceId)
        Keychain.delete(KeychainKeys.deviceToken)
        isPaired = false
        deviceId = nil
        pendingPairingLink = nil
    }
}
