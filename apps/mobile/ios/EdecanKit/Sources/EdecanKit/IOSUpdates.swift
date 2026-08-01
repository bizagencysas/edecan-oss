import Foundation

/// Canal de distribución elegido al compilar la app. Un build estable nunca
/// ofrece una versión preliminar; un build preview puede avanzar hacia otra
/// preview o hacia una versión final más nueva.
public enum IOSUpdateChannel: String, Codable, Sendable, CaseIterable {
    case stable
    case preview
}

/// SemVer 2.0.0 estricto para comparar la versión publicada con
/// `CFBundleShortVersionString`. Se acepta un prefijo `v` únicamente como
/// comodidad para manifiestos derivados de tags de Git.
public struct SemanticVersion: Sendable, Hashable, Comparable, CustomStringConvertible {
    public let major: Int
    public let minor: Int
    public let patch: Int
    public let prerelease: [String]
    public let buildMetadata: [String]

    public var isPrerelease: Bool { !prerelease.isEmpty }

    public var description: String {
        var value = "\(major).\(minor).\(patch)"
        if !prerelease.isEmpty { value += "-\(prerelease.joined(separator: "."))" }
        if !buildMetadata.isEmpty { value += "+\(buildMetadata.joined(separator: "."))" }
        return value
    }

    public init?(_ rawValue: String) {
        var value = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        if value.first == "v" || value.first == "V" {
            value.removeFirst()
        }
        guard !value.isEmpty else { return nil }

        let buildSplit = value.split(separator: "+", omittingEmptySubsequences: false)
        guard buildSplit.count <= 2 else { return nil }
        let versionAndPrerelease = String(buildSplit[0])
        let build = buildSplit.count == 2 ? String(buildSplit[1]) : nil

        let prereleaseSplit = versionAndPrerelease.split(
            separator: "-",
            maxSplits: 1,
            omittingEmptySubsequences: false
        )
        guard !prereleaseSplit.isEmpty else { return nil }
        let core = prereleaseSplit[0].split(separator: ".", omittingEmptySubsequences: false)
        guard core.count == 3,
              let major = Self.parseCoreNumber(core[0]),
              let minor = Self.parseCoreNumber(core[1]),
              let patch = Self.parseCoreNumber(core[2])
        else { return nil }

        let prerelease = prereleaseSplit.count == 2
            ? String(prereleaseSplit[1]).split(separator: ".", omittingEmptySubsequences: false)
                .map(String.init)
            : []
        let buildMetadata = build?
            .split(separator: ".", omittingEmptySubsequences: false)
            .map(String.init) ?? []

        guard prerelease.allSatisfy(Self.isValidPrereleaseIdentifier),
              buildMetadata.allSatisfy(Self.isValidIdentifier)
        else { return nil }

        self.major = major
        self.minor = minor
        self.patch = patch
        self.prerelease = prerelease
        self.buildMetadata = buildMetadata
    }

    public static func < (lhs: SemanticVersion, rhs: SemanticVersion) -> Bool {
        if lhs.major != rhs.major { return lhs.major < rhs.major }
        if lhs.minor != rhs.minor { return lhs.minor < rhs.minor }
        if lhs.patch != rhs.patch { return lhs.patch < rhs.patch }

        if lhs.prerelease.isEmpty { return false }
        if rhs.prerelease.isEmpty { return true }

        for (left, right) in zip(lhs.prerelease, rhs.prerelease) {
            if left == right { continue }
            let leftNumeric = left.allSatisfy(\.isNumber)
            let rightNumeric = right.allSatisfy(\.isNumber)
            switch (leftNumeric, rightNumeric) {
            case (true, true):
                if left.count != right.count { return left.count < right.count }
                return left < right
            case (true, false):
                return true
            case (false, true):
                return false
            case (false, false):
                return left < right
            }
        }
        return lhs.prerelease.count < rhs.prerelease.count
    }

    /// SemVer excluye los metadatos de build al calcular precedencia.
    /// Implementar también `==` de esa forma mantiene coherente el contrato
    /// de `Comparable`: dos versiones que no ordenan una antes de la otra son
    /// la misma para la política de actualización.
    public static func == (lhs: SemanticVersion, rhs: SemanticVersion) -> Bool {
        lhs.major == rhs.major
            && lhs.minor == rhs.minor
            && lhs.patch == rhs.patch
            && lhs.prerelease == rhs.prerelease
    }

    public func hash(into hasher: inout Hasher) {
        hasher.combine(major)
        hasher.combine(minor)
        hasher.combine(patch)
        hasher.combine(prerelease)
    }

    private static func parseCoreNumber(_ value: Substring) -> Int? {
        guard !value.isEmpty,
              value.allSatisfy(\.isNumber),
              value == "0" || value.first != "0"
        else { return nil }
        return Int(value)
    }

    private static func isValidPrereleaseIdentifier(_ value: String) -> Bool {
        guard isValidIdentifier(value) else { return false }
        return !value.allSatisfy(\.isNumber) || value == "0" || value.first != "0"
    }

    private static func isValidIdentifier(_ value: String) -> Bool {
        !value.isEmpty
            && value.unicodeScalars.allSatisfy { scalar in
                scalar.isASCII
                    && (CharacterSet.alphanumerics.contains(scalar) || scalar == "-")
            }
    }
}

public enum IOSUpdateInstallKind: String, Sendable, Equatable {
    case appStore
    case testFlight
    case altStore
    case sideStore
    case web

    public var actionTitle: String {
        switch self {
        case .appStore: "Abrir App Store"
        case .testFlight: "Abrir TestFlight"
        case .altStore: "Abrir AltStore"
        case .sideStore: "Abrir SideStore"
        case .web: "Ver actualización"
        }
    }
}

public struct IOSUpdateManifest: Sendable, Equatable {
    public static let schemaVersion = 1

    public let channel: IOSUpdateChannel
    public let version: SemanticVersion
    public let versionText: String
    public let buildNumber: Int?
    public let publishedAt: String?
    public let releaseNotes: String
    public let installURL: URL
    public let installKind: IOSUpdateInstallKind

    public var presentationID: String {
        if let buildNumber { return "\(versionText)-\(buildNumber)" }
        return versionText
    }
}

public enum IOSUpdateManifestError: Error, LocalizedError, Sendable, Equatable {
    case tooLarge
    case malformed
    case unsupportedSchema
    case unsupportedChannel
    case invalidVersion
    case invalidBuildNumber
    case invalidInstallURL
    case insecureManifestURL

    public var errorDescription: String? {
        switch self {
        case .tooLarge: "El manifiesto de actualización es demasiado grande."
        case .malformed: "El manifiesto de actualización no se pudo interpretar."
        case .unsupportedSchema: "La versión del manifiesto no es compatible."
        case .unsupportedChannel: "El canal de actualización no es compatible."
        case .invalidVersion: "La versión publicada no es válida."
        case .invalidBuildNumber: "La compilación publicada no es válida."
        case .invalidInstallURL: "El enlace oficial de instalación no es válido."
        case .insecureManifestURL: "El canal de actualización debe usar HTTPS."
        }
    }
}

public enum IOSUpdateManifestParser {
    public static let maximumBytes = 256 * 1024

    private struct RawManifest: Decodable {
        let schemaVersion: Int
        let channel: String
        let version: String
        let buildNumber: Int?
        let publishedAt: String?
        let releaseNotes: String?
        let installURL: String

        enum CodingKeys: String, CodingKey {
            case schemaVersion = "schema_version"
            case channel, version
            case buildNumber = "build_number"
            case publishedAt = "published_at"
            case releaseNotes = "release_notes"
            case installURL = "install_url"
        }
    }

    public static func parse(_ data: Data) throws -> IOSUpdateManifest {
        guard data.count <= maximumBytes else { throw IOSUpdateManifestError.tooLarge }
        let raw: RawManifest
        do {
            raw = try JSONDecoder().decode(RawManifest.self, from: data)
        } catch {
            throw IOSUpdateManifestError.malformed
        }
        guard raw.schemaVersion == IOSUpdateManifest.schemaVersion else {
            throw IOSUpdateManifestError.unsupportedSchema
        }
        guard let channel = IOSUpdateChannel(rawValue: raw.channel) else {
            throw IOSUpdateManifestError.unsupportedChannel
        }
        guard raw.version.count <= 80, let version = SemanticVersion(raw.version) else {
            throw IOSUpdateManifestError.invalidVersion
        }
        if let buildNumber = raw.buildNumber, buildNumber <= 0 {
            throw IOSUpdateManifestError.invalidBuildNumber
        }
        let (installURL, installKind) = try validatedInstallURL(raw.installURL)
        let notes = raw.releaseNotes?
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .prefix(20_000) ?? ""
        let publishedAt = raw.publishedAt?
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .prefix(100)

        return IOSUpdateManifest(
            channel: channel,
            version: version,
            versionText: version.description,
            buildNumber: raw.buildNumber,
            publishedAt: publishedAt.map(String.init),
            releaseNotes: String(notes),
            installURL: installURL,
            installKind: installKind
        )
    }

    public static func validatedManifestURL(_ rawValue: String) throws -> URL {
        guard rawValue.count <= 2_048,
              let components = URLComponents(string: rawValue),
              components.scheme?.lowercased() == "https",
              components.host?.isEmpty == false,
              components.user == nil,
              components.password == nil,
              components.fragment == nil,
              let url = components.url
        else { throw IOSUpdateManifestError.insecureManifestURL }
        return url
    }

    public static func validatedInstallURL(
        _ rawValue: String
    ) throws -> (URL, IOSUpdateInstallKind) {
        guard rawValue.count <= 2_048,
              let components = URLComponents(string: rawValue),
              let scheme = components.scheme?.lowercased(),
              components.user == nil,
              components.password == nil,
              components.fragment == nil,
              let url = components.url
        else { throw IOSUpdateManifestError.invalidInstallURL }

        let kind: IOSUpdateInstallKind
        switch scheme {
        case "https":
            guard components.host?.isEmpty == false else {
                throw IOSUpdateManifestError.invalidInstallURL
            }
            switch components.host?.lowercased() {
            case "apps.apple.com": kind = .appStore
            case "testflight.apple.com": kind = .testFlight
            default: kind = .web
            }
        case "itms-apps":
            guard components.host?.isEmpty == false else {
                throw IOSUpdateManifestError.invalidInstallURL
            }
            kind = .appStore
        case "itms-beta":
            guard components.host?.isEmpty == false else {
                throw IOSUpdateManifestError.invalidInstallURL
            }
            kind = .testFlight
        case "altstore":
            guard components.host?.isEmpty == false
                    || !components.path.isEmpty
                    || components.query?.isEmpty == false
            else {
                throw IOSUpdateManifestError.invalidInstallURL
            }
            kind = .altStore
        case "sidestore":
            guard components.host?.isEmpty == false
                    || !components.path.isEmpty
                    || components.query?.isEmpty == false
            else {
                throw IOSUpdateManifestError.invalidInstallURL
            }
            kind = .sideStore
        default:
            throw IOSUpdateManifestError.invalidInstallURL
        }
        return (url, kind)
    }
}

public enum IOSUpdatePolicy {
    /// Devuelve `true` solo para un avance real. Nunca ofrece downgrade, una
    /// preview a un build estable ni un manifiesto de otro canal.
    public static func isAvailable(
        _ manifest: IOSUpdateManifest,
        currentVersion: String,
        currentBuild: Int?,
        configuredChannel: IOSUpdateChannel
    ) -> Bool {
        guard manifest.channel == configuredChannel,
              let installed = SemanticVersion(currentVersion)
        else { return false }
        if configuredChannel == .stable && manifest.version.isPrerelease {
            return false
        }
        if manifest.version > installed { return true }
        guard manifest.version == installed,
              let publishedBuild = manifest.buildNumber,
              let currentBuild
        else { return false }
        return publishedBuild > currentBuild
    }
}
