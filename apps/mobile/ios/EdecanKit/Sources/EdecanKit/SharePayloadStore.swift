import Foundation

public struct SharedSharePayload: Codable, Sendable, Equatable {
    public let kind: String
    public let value: String
    public let filename: String?
    public let mimeType: String?

    public init(kind: String, value: String, filename: String? = nil, mimeType: String? = nil) {
        self.kind = kind
        self.value = value
        self.filename = filename
        self.mimeType = mimeType
    }
}

/// Contrato pequeño compartido por la app y la Share Extension. La extensión
/// nunca escribe en el sandbox privado de Edecán: solo en este App Group.
public enum SharePayloadStore {
    public static let appGroup = "group.cc.edecan.app"
    private static let manifestKey = "cc.edecan.share.payloads.v1"

    public static func enqueue(_ payloads: [SharedSharePayload]) {
        guard !payloads.isEmpty, let defaults = UserDefaults(suiteName: appGroup) else { return }
        var current = read(from: defaults)
        current.append(contentsOf: payloads)
        let bounded = Array(current.suffix(20))
        guard let data = try? JSONEncoder().encode(bounded) else { return }
        defaults.set(data, forKey: manifestKey)
    }

    public static func consume() -> [SharedSharePayload] {
        guard let defaults = UserDefaults(suiteName: appGroup) else { return [] }
        let payloads = read(from: defaults)
        defaults.removeObject(forKey: manifestKey)
        return payloads
    }

    public static func pendingCount() -> Int {
        guard let defaults = UserDefaults(suiteName: appGroup) else { return 0 }
        return read(from: defaults).count
    }

    public static func containerURL() -> URL? {
        FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: appGroup)
    }

    private static func read(from defaults: UserDefaults) -> [SharedSharePayload] {
        guard let data = defaults.data(forKey: manifestKey),
              let payloads = try? JSONDecoder().decode([SharedSharePayload].self, from: data)
        else { return [] }
        return payloads
    }
}
