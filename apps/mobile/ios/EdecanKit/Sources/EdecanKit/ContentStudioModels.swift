import Foundation

public enum SocialContentPlatform: String, Codable, CaseIterable, Sendable, Identifiable {
    case linkedin
    case x

    public var id: String { rawValue }
    public var label: String { self == .linkedin ? "LinkedIn" : "X" }
    public var characterLimit: Int { self == .linkedin ? 3_000 : 280 }
}

public struct SocialContentRequest: Encodable, Sendable, Equatable {
    public let platform: SocialContentPlatform
    public let topic: String
    public let objective: String
    public let tone: String
    public let withImage: Bool

    enum CodingKeys: String, CodingKey {
        case platform, topic, objective, tone
        case withImage = "with_image"
    }

    public init(
        platform: SocialContentPlatform,
        topic: String,
        objective: String,
        tone: String,
        withImage: Bool
    ) {
        self.platform = platform
        self.topic = topic
        self.objective = objective
        self.tone = tone
        self.withImage = withImage
    }
}

public struct SocialContentDraft: Decodable, Sendable, Equatable {
    public let status: String
    public let platform: SocialContentPlatform
    public let copy: String
    public let parts: [String]
    public let altText: String
    public let offlineVisual: Bool
    public let artifacts: [ArtifactRef]
    public let requiresHumanConfirmation: Bool

    enum CodingKeys: String, CodingKey {
        case status, platform, copy, parts, artifacts
        case altText = "alt_text"
        case offlineVisual = "offline_visual"
        case requiresHumanConfirmation = "requires_human_confirmation"
    }

    public var imageArtifact: ArtifactRef? {
        artifacts.first { $0.mime?.lowercased().hasPrefix("image/") == true }
    }
}

// MARK: - Studio de proyectos

public enum StudioProjectMode: String, Codable, CaseIterable, Sendable, Identifiable {
    case general, landing, mockup, post, carousel, ad, email, deck

    public var id: String { rawValue }

    public var label: String {
        switch self {
        case .general: "Cualquier cosa"
        case .landing: "Página web"
        case .mockup: "App o producto"
        case .post: "Post"
        case .carousel: "Carrusel"
        case .ad: "Anuncio"
        case .email: "Email"
        case .deck: "Presentación"
        }
    }
}

public enum StudioProjectQuality: String, Codable, CaseIterable, Sendable, Identifiable {
    case fast, balanced, max

    public var id: String { rawValue }
    public var label: String {
        switch self {
        case .fast: "Rápida"
        case .balanced: "Equilibrada"
        case .max: "Máxima"
        }
    }
}

public enum StudioExportFormat: String, Codable, CaseIterable, Sendable, Identifiable {
    case html, png, pdf

    public var id: String { rawValue }
    public var label: String { rawValue.uppercased() }
}

/// Petición única del Studio privado. Los campos opcionales se omiten y los
/// adjuntos son UUIDs ya subidos a Edecán; el cliente nunca conoce rutas del
/// motor creativo ni credenciales de sus proveedores.
public struct StudioActionRequest: Encodable, Sendable, Equatable {
    public let action: String
    public let projectId: String?
    public let revisionId: String?
    public let prompt: String?
    public let instruction: String?
    public let projectName: String?
    public let brandName: String?
    public let mode: StudioProjectMode?
    public let width: Int?
    public let height: Int?
    public let count: Int?
    public let quality: StudioProjectQuality?
    public let files: [String]
    public let exportFormat: StudioExportFormat?
    public let includeArchived: Bool?
    public let confirmed: Bool

    enum CodingKeys: String, CodingKey {
        case action, prompt, instruction, mode, width, height, count, quality, files, confirmed
        case projectId, revisionId, projectName, brandName, exportFormat, includeArchived
    }

    public init(
        action: String,
        projectId: String? = nil,
        revisionId: String? = nil,
        prompt: String? = nil,
        instruction: String? = nil,
        projectName: String? = nil,
        brandName: String? = nil,
        mode: StudioProjectMode? = nil,
        width: Int? = nil,
        height: Int? = nil,
        count: Int? = nil,
        quality: StudioProjectQuality? = nil,
        files: [String] = [],
        exportFormat: StudioExportFormat? = nil,
        includeArchived: Bool? = nil,
        confirmed: Bool = false
    ) {
        self.action = action
        self.projectId = projectId
        self.revisionId = revisionId
        self.prompt = prompt
        self.instruction = instruction
        self.projectName = projectName
        self.brandName = brandName
        self.mode = mode
        self.width = width
        self.height = height
        self.count = count
        self.quality = quality
        self.files = Array(files.prefix(12))
        self.exportFormat = exportFormat
        self.includeArchived = includeArchived
        self.confirmed = confirmed
    }
}

public struct StudioProjectSummary: Sendable, Equatable, Identifiable {
    public let id: String
    public let name: String
    public let mode: String
    public let revisionCount: Int
    public let updatedAt: String?
    public let brandName: String?
    public let archivedAt: String?

    public init(
        id: String,
        name: String,
        mode: String,
        revisionCount: Int = 0,
        updatedAt: String? = nil,
        brandName: String? = nil,
        archivedAt: String? = nil
    ) {
        self.id = id
        self.name = name
        self.mode = mode
        self.revisionCount = revisionCount
        self.updatedAt = updatedAt
        self.brandName = brandName
        self.archivedAt = archivedAt
    }
}

public struct StudioRevision: Sendable, Equatable, Identifiable {
    public let id: String
    public let label: String
    public let width: Int
    public let height: Int
    public let instruction: String
    public let createdAt: String?
    public let archivedAt: String?

    public init(
        id: String,
        label: String,
        width: Int,
        height: Int,
        instruction: String,
        createdAt: String? = nil,
        archivedAt: String? = nil
    ) {
        self.id = id
        self.label = label
        self.width = width
        self.height = height
        self.instruction = instruction
        self.createdAt = createdAt
        self.archivedAt = archivedAt
    }
}

public struct StudioActionResponse: Decodable, Sendable, Equatable {
    public let status: String
    public let action: String
    public let message: String
    public let result: [String: JSONValue]
    public let artifacts: [ArtifactRef]
    public let presentation: [[String: JSONValue]]

    public var projects: [StudioProjectSummary] {
        guard case .array(let values) = result["projects"] else { return [] }
        return values.compactMap { value in
            guard case .object(let object) = value else { return nil }
            return Self.project(from: object)
        }
    }

    public var project: StudioProjectSummary? {
        guard case .object(let value) = result["project"] else { return nil }
        let parsed = Self.project(from: value)
        guard let parsed else { return nil }
        return StudioProjectSummary(
            id: parsed.id,
            name: parsed.name,
            mode: parsed.mode,
            revisionCount: revisions.isEmpty ? parsed.revisionCount : revisions.count,
            updatedAt: parsed.updatedAt,
            brandName: parsed.brandName,
            archivedAt: parsed.archivedAt
        )
    }

    public var revisions: [StudioRevision] {
        guard case .array(let values) = result["revisions"] else { return [] }
        return values.compactMap { value in
            guard case .object(let object) = value,
                  let id = object["id"]?.stringValue else { return nil }
            return StudioRevision(
                id: id,
                label: object["label"]?.stringValue ?? "Revisión",
                width: object["width"]?.intValue ?? 0,
                height: object["height"]?.intValue ?? 0,
                instruction: object["instruction"]?.stringValue ?? "",
                createdAt: object["createdAt"]?.stringValue,
                archivedAt: object["archivedAt"]?.stringValue
            )
        }
    }

    public var revisionId: String? { result["revision"]?.stringValue }

    private static func project(from object: [String: JSONValue]) -> StudioProjectSummary? {
        guard let id = object["id"]?.stringValue else { return nil }
        return StudioProjectSummary(
            id: id,
            name: object["name"]?.stringValue ?? "Proyecto sin nombre",
            mode: object["mode"]?.stringValue ?? "general",
            revisionCount: object["revisions"]?.intValue ?? 0,
            updatedAt: object["updatedAt"]?.stringValue,
            brandName: object["brandName"]?.stringValue,
            archivedAt: object["archivedAt"]?.stringValue
        )
    }
}

private extension JSONValue {
    var stringValue: String? {
        guard case .string(let value) = self else { return nil }
        return value
    }

    var intValue: Int? {
        guard case .number(let value) = self else { return nil }
        return Int(value)
    }
}

/// Cliente del Studio creativo completo. Se mantiene separado de `APIClient`
/// para que la nueva superficie no compita con cambios del chat; reutiliza
/// su URL, sesión, renovación de token y descarga privada de artefactos.
public struct ContentStudioService: Sendable {
    private let client: APIClient
    private let urlSession: URLSession

    public init(client: APIClient, urlSession: URLSession = .shared) {
        self.client = client
        self.urlSession = urlSession
    }

    public func create(_ input: SocialContentRequest) async throws -> SocialContentDraft {
        try await send(input, canRefresh: true)
    }

    public func perform(_ input: StudioActionRequest) async throws -> StudioActionResponse {
        try await sendStudio(input, canRefresh: true)
    }

    private func sendStudio(
        _ input: StudioActionRequest,
        canRefresh: Bool
    ) async throws -> StudioActionResponse {
        let token = try await client.tokenDeAccesoValido()
        var request = URLRequest(url: try await client.urlCompleta("/v1/content/studio/actions"))
        request.httpMethod = "POST"
        request.timeoutInterval = 1_230
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.httpBody = try JSONEncoder().encode(input)

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await urlSession.data(for: request)
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            throw APIClient.APIError.sinConexion(detalle: error.localizedDescription)
        }
        guard let http = response as? HTTPURLResponse else {
            throw APIClient.APIError.respuestaInvalida
        }
        if http.statusCode == 401, canRefresh {
            _ = try await client.refrescar()
            return try await sendStudio(input, canRefresh: false)
        }
        if http.statusCode == 401 {
            throw APIClient.APIError.sesionExpirada
        }
        guard (200..<300).contains(http.statusCode) else {
            struct ErrorBody: Decodable { let detail: String? }
            let detail = (try? JSONDecoder().decode(ErrorBody.self, from: data).detail)
                ?? "sin detalle"
            throw APIClient.APIError.servidor(status: http.statusCode, mensaje: detail)
        }
        do {
            return try JSONDecoder().decode(StudioActionResponse.self, from: data)
        } catch {
            throw APIClient.APIError.respuestaInvalida
        }
    }

    private func send(
        _ input: SocialContentRequest,
        canRefresh: Bool
    ) async throws -> SocialContentDraft {
        let token = try await client.tokenDeAccesoValido()
        var request = URLRequest(url: try await client.urlCompleta("/v1/content/social"))
        request.httpMethod = "POST"
        request.timeoutInterval = 180
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.httpBody = try JSONEncoder().encode(input)

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await urlSession.data(for: request)
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            throw APIClient.APIError.sinConexion(detalle: error.localizedDescription)
        }
        guard let http = response as? HTTPURLResponse else {
            throw APIClient.APIError.respuestaInvalida
        }
        if http.statusCode == 401, canRefresh {
            _ = try await client.refrescar()
            return try await send(input, canRefresh: false)
        }
        if http.statusCode == 401 {
            throw APIClient.APIError.sesionExpirada
        }
        guard (200..<300).contains(http.statusCode) else {
            struct ErrorBody: Decodable { let detail: String? }
            let detail = (try? JSONDecoder().decode(ErrorBody.self, from: data).detail)
                ?? "sin detalle"
            throw APIClient.APIError.servidor(status: http.statusCode, mensaje: detail)
        }
        do {
            return try JSONDecoder().decode(SocialContentDraft.self, from: data)
        } catch {
            throw APIClient.APIError.respuestaInvalida
        }
    }
}
