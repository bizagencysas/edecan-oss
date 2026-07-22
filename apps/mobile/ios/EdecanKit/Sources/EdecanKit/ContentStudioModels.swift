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

/// Cliente pequeño del mini estudio. Se mantiene separado de `APIClient`
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
