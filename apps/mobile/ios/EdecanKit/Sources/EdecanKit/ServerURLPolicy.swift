import Darwin
import Foundation

/// Política única para todas las direcciones de servidor que acepta iOS.
///
/// HTTPS puede apuntar a un host público. HTTP se limita deliberadamente a
/// destinos locales para que un QR o un valor antiguo de Keychain no rebaje
/// una conexión remota a texto plano.
public enum ServerURLPolicy {
    public enum ValidationError: Error, LocalizedError, Sendable, Equatable {
        case invalidURL
        case embeddedCredentialsOrParameters
        case insecureRemoteHTTP

        public var errorDescription: String? {
            switch self {
            case .invalidURL:
                return "Escribe una dirección completa con http:// o https://."
            case .embeddedCredentialsOrParameters:
                return "La dirección del servidor no puede incluir usuario, contraseña, consulta ni fragmento."
            case .insecureRemoteHTTP:
                return "Los servidores públicos deben usar HTTPS. HTTP solo está permitido dentro de tu red local."
            }
        }
    }

    public static func parseAndValidate(_ rawValue: String) throws -> URL {
        guard let url = URL(string: rawValue) else { throw ValidationError.invalidURL }
        return try validate(url)
    }

    @discardableResult
    public static func validate(_ url: URL) throws -> URL {
        guard let scheme = url.scheme?.lowercased(),
              scheme == "http" || scheme == "https",
              let host = url.host, !host.isEmpty
        else { throw ValidationError.invalidURL }

        guard url.user == nil,
              url.password == nil,
              url.query == nil,
              url.fragment == nil
        else { throw ValidationError.embeddedCredentialsOrParameters }

        if scheme == "https" || isLocalHTTPHost(host) {
            return url
        }
        throw ValidationError.insecureRemoteHTTP
    }

    private static func isLocalHTTPHost(_ rawHost: String) -> Bool {
        let host = rawHost.lowercased()

        if let ipv4 = parseIPv4(host) {
            return isLocalIPv4(ipv4)
        }
        if host.contains(":") {
            return isLocalIPv6(host)
        }
        if host == "localhost" {
            return true
        }
        if host.hasSuffix(".local") {
            return isValidHostname(host)
        }
        // Los hosts de una sola etiqueta son nombres de la LAN. Se excluyen
        // formas solo numéricas para no aceptar representaciones IPv4
        // abreviadas que el resolver podría interpretar como una IP pública.
        return !host.contains(".")
            && host.contains(where: { $0.isLetter })
            && isValidHostname(host)
    }

    private static func parseIPv4(_ host: String) -> [UInt8]? {
        let parts = host.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 4 else { return nil }
        var bytes: [UInt8] = []
        bytes.reserveCapacity(4)
        for part in parts {
            guard !part.isEmpty,
                  !(part.count > 1 && part.first == "0"),
                  part.allSatisfy(\.isNumber),
                  let byte = UInt8(part)
            else { return nil }
            bytes.append(byte)
        }
        return bytes
    }

    private static func isLocalIPv4(_ bytes: [UInt8]) -> Bool {
        bytes[0] == 10
            || bytes[0] == 127
            || (bytes[0] == 169 && bytes[1] == 254)
            || (bytes[0] == 172 && (16...31).contains(bytes[1]))
            || (bytes[0] == 192 && bytes[1] == 168)
    }

    private static func isLocalIPv6(_ hostWithOptionalZone: String) -> Bool {
        let addressText = hostWithOptionalZone.split(separator: "%", maxSplits: 1).first.map(String.init) ?? ""
        var address = in6_addr()
        guard addressText.withCString({ inet_pton(AF_INET6, $0, &address) }) == 1 else {
            return false
        }
        let bytes = withUnsafeBytes(of: address) { Array($0) }

        let loopback = bytes.dropLast().allSatisfy { $0 == 0 } && bytes.last == 1
        let uniqueLocal = (bytes[0] & 0xFE) == 0xFC // fc00::/7
        let linkLocal = bytes[0] == 0xFE && (bytes[1] & 0xC0) == 0x80 // fe80::/10
        let ipv4Mapped = bytes.prefix(10).allSatisfy { $0 == 0 }
            && bytes[10] == 0xFF
            && bytes[11] == 0xFF
            && isLocalIPv4(Array(bytes[12...15]))
        return loopback || uniqueLocal || linkLocal || ipv4Mapped
    }

    private static func isValidHostname(_ host: String) -> Bool {
        guard host.utf8.count <= 253 else { return false }
        let labels = host.split(separator: ".", omittingEmptySubsequences: false)
        return labels.allSatisfy { label in
            guard !label.isEmpty,
                  label.utf8.count <= 63,
                  label.first?.isLetter == true || label.first?.isNumber == true,
                  label.last?.isLetter == true || label.last?.isNumber == true
            else { return false }
            return label.allSatisfy { $0.isLetter || $0.isNumber || $0 == "-" }
        }
    }
}
