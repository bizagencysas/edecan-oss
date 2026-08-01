import Foundation

public enum SocialContentPlatform: String, Codable, CaseIterable, Sendable, Identifiable {
    case linkedin
    case x

    public var id: String { rawValue }
    public var label: String { self == .linkedin ? "LinkedIn" : "X" }
    public var characterLimit: Int { self == .linkedin ? 3_000 : 280 }
}

/// Identificador ABIERTO de un destino de publicación de LinkedIn: el perfil
/// personal (siempre disponible, valor por defecto) o una página de
/// organización configurada por el tenant en el backend (ver
/// `edecan_creative.marcas.BrandDestination` en el monorepo Edecán). Ya no es
/// un enum cerrado a dos marcas fijas -- cualquier id que no sea `personal`
/// se trata como una organización genérica, y su etiqueta se deriva del
/// propio id que envía el backend en vez de un nombre de marca fijo aquí.
public struct SocialContentTarget: RawRepresentable, Codable, Sendable, Equatable, Hashable, Identifiable {
    public let rawValue: String

    public init(rawValue: String) {
        self.rawValue = rawValue.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        self.init(rawValue: try container.decode(String.self))
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(rawValue)
    }

    public var id: String { rawValue }

    /// El destino implícito de siempre: mismo id que ya tenía el literal fijo
    /// `"personal"` (compatibilidad con perfiles ya guardados con esa clave).
    public static let personal = SocialContentTarget(rawValue: "personal")

    /// Placeholder genérico de "una página de organización" mientras esta app
    /// no tiene un selector dinámico de los destinos que el tenant configuró
    /// en el backend. Un self-host real debe configurar su página de
    /// organización con este mismo id (o adaptar este valor al que haya
    /// elegido) -- ver `edecan_creative.marcas.BrandDestinationConfig`.
    public static let organization = SocialContentTarget(rawValue: "organizacion")

    public var isPersonal: Bool { self == .personal }

    public var label: String {
        isPersonal ? "LinkedIn personal" : "LinkedIn: \(rawValue.capitalized)"
    }
}

public struct SocialContentRequest: Encodable, Sendable, Equatable {
    public let platform: SocialContentPlatform
    public let target: SocialContentTarget?
    public let topic: String
    public let objective: String
    public let tone: String
    public let withImage: Bool

    enum CodingKeys: String, CodingKey {
        case platform, target, topic, objective, tone
        case withImage = "with_image"
    }

    public init(
        platform: SocialContentPlatform,
        target: SocialContentTarget? = nil,
        topic: String,
        objective: String,
        tone: String,
        withImage: Bool
    ) {
        self.platform = platform
        self.target = target
        self.topic = topic
        self.objective = objective
        self.tone = tone
        self.withImage = withImage
    }
}

public struct SocialContentDraft: Decodable, Sendable, Equatable {
    public let status: String
    public let platform: SocialContentPlatform
    public let target: SocialContentTarget?
    public let copy: String
    public let parts: [String]
    public let altText: String
    public let offlineVisual: Bool
    public let visualWarning: String?
    public let artifacts: [ArtifactRef]
    public let requiresHumanConfirmation: Bool

    enum CodingKeys: String, CodingKey {
        case status, platform, target, copy, parts, artifacts
        case altText = "alt_text"
        case offlineVisual = "offline_visual"
        case visualWarning = "visual_warning"
        case requiresHumanConfirmation = "requires_human_confirmation"
    }

    public var imageArtifact: ArtifactRef? {
        artifacts.first { $0.mime?.lowercased().hasPrefix("image/") == true }
    }
}

public struct SocialContentPublishRequest: Encodable, Sendable, Equatable {
    public let platform: SocialContentPlatform
    public let target: SocialContentTarget
    public let text: String
    public let imageFileId: String?
    public let altText: String
    public let confirmed: Bool

    enum CodingKeys: String, CodingKey {
        case platform, target, text, confirmed
        case imageFileId = "image_file_id"
        case altText = "alt_text"
    }

    public init(
        platform: SocialContentPlatform = .linkedin,
        target: SocialContentTarget,
        text: String,
        imageFileId: String?,
        altText: String,
        confirmed: Bool
    ) {
        self.platform = platform
        self.target = target
        self.text = text
        self.imageFileId = imageFileId
        self.altText = altText
        self.confirmed = confirmed
    }
}

/// Espejo de `verified` en `SocialContentPublishOut`
/// (`apps/api/edecan_api/routers/content_studio.py`): el conector de LinkedIn
/// relee el post recién creado antes de darlo por publicado, porque un `2xx`
/// de LinkedIn no prueba que el post exista -- es EXACTAMENTE lo que pasó el
/// día que esto se descubrió: un token sin el scope de organización hizo que
/// LinkedIn respondiera 201 con un `x-restli-id` válido sin crear nada.
///
/// Solo dos casos llegan hasta acá porque el tercero (`"not_found"`, la
/// relectura confirmó que el post NO existe) nunca se convierte en una
/// respuesta 2xx: el backend levanta un error ahí mismo, así que el cliente
/// lo recibe como una excepción de verdad, no como este campo.
public enum SocialPublishVerification: String, Decodable, Sendable, Equatable {
    case confirmed
    case unknown
}

/// Estado de aprobación de una card o formulario social tras un intento de
/// publicar. Es un tipo de TRES casos, no un booleano, para que
/// `SocialDraftCardView` (la card del chat) y `LinkedInStudioView` (el
/// Studio) muestren exactamente lo mismo ante cada resultado posible del
/// backend -- ver `SocialPublishVerification` arriba para el porqué de cada
/// caso.
public enum SocialApprovalStatus: Equatable, Sendable {
    /// Sin intento todavía, o el intento anterior fue un error DE VERDAD
    /// (LinkedIn confirmó que el post no existe) -- no hay nada publicado,
    /// así que reintentar es seguro y el botón sigue disponible.
    case pending
    /// Se releyó el post en LinkedIn y existe. Éxito real y comprobado.
    case confirmed
    /// Se envió pero no se pudo releer (típicamente sin permiso de lectura,
    /// o un problema de red). PUEDE que sí se haya publicado -- por eso el
    /// botón se deshabilita en vez de volver a "Aprobar": invitar a tocar de
    /// nuevo podría duplicar un post que en realidad sí salió. La persona
    /// tiene que comprobarlo ella misma en LinkedIn.
    case unverified

    public init(_ verified: SocialPublishVerification) {
        self = verified == .confirmed ? .confirmed : .unverified
    }
}

public struct SocialContentPublishResult: Decodable, Sendable, Equatable {
    public let status: String
    public let platform: SocialContentPlatform
    public let providerId: String?
    public let verified: SocialPublishVerification
    public let verificationNote: String

    enum CodingKeys: String, CodingKey {
        case status, platform
        case providerId = "provider_id"
        case verified
        case verificationNote = "verification_note"
    }

    // Decoder a mano (no el sintetizado) por una sola razón: el default de
    // `verified` tiene que ser `.unknown`, NUNCA `.confirmed`, cuando la
    // clave falta. Un backend viejo (de antes de la verificación real)
    // simplemente no manda `verified` -- y asumir éxito por la ausencia de
    // este campo repetiría exactamente el bug que originó todo esto: un 2xx
    // de LinkedIn (o, aquí, una respuesta sin este campo) no prueba que el
    // post exista. "No lo sé" es la lectura segura de "no me lo dijeron".
    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        status = try container.decode(String.self, forKey: .status)
        platform = try container.decode(SocialContentPlatform.self, forKey: .platform)
        providerId = try container.decodeIfPresent(String.self, forKey: .providerId)
        verified = try container.decodeIfPresent(SocialPublishVerification.self, forKey: .verified)
            ?? .unknown
        verificationNote = try container.decodeIfPresent(String.self, forKey: .verificationNote)
            ?? ""
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

    public func publishLinkedIn(
        _ input: SocialContentPublishRequest
    ) async throws -> SocialContentPublishResult {
        try await sendPublish(input, canRefresh: true)
    }

    public func perform(_ input: StudioActionRequest) async throws -> StudioActionResponse {
        try await sendStudio(input, canRefresh: true)
    }

    /// Publica un borrador que YA vive en el servidor, referenciado solo por su
    /// `draft_id` — el que viaja en el botón "Aprobar y publicar" de una card
    /// (`ApproveDraftAction`).
    ///
    /// Es distinto de `publishLinkedIn`, que exige `target`/`text`/`image_file_id`
    /// explícitos porque nace del Studio, donde la app TIENE el contenido en
    /// mano. Una card llegada por push no lo tiene: trae una referencia, y el
    /// texto y la imagen viven en el servidor. Esa asimetría es la razón por la
    /// que el botón mostraba un error en vez de publicar — no existía este
    /// camino (ver `ChatView.ejecutarAccion`, caso `.approveDraft`).
    ///
    /// El servidor es quien decide qué se publica; la app nunca reenvía el texto
    /// de vuelta. Así un borrador no se puede alterar desde el teléfono entre que
    /// se generó y se aprobó, y la publicación es idempotente del lado del
    /// servidor: tocar dos veces devuelve el mismo resultado en vez de publicar
    /// dos veces (que en LinkedIn se ve y da pena).
    public func publishDraft(draftId: String) async throws -> SocialContentPublishResult {
        try await sendPublishDraft(draftId: draftId, canRefresh: true)
    }

    private func sendPublishDraft(
        draftId: String,
        canRefresh: Bool
    ) async throws -> SocialContentPublishResult {
        // El `draft_id` va en la RUTA, así que se codifica: lo arma el servidor
        // (`linkedin-<hex>`), pero un id con caracteres raros no puede romper la
        // URL ni colarse como otro segmento de ruta.
        let idCodificado =
            draftId.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? draftId
        let token = try await client.tokenDeAccesoValido()
        var request = URLRequest(
            url: try await client.urlCompleta("/v1/content/social/drafts/\(idCodificado)/publish")
        )
        request.httpMethod = "POST"
        // Mismo margen que `sendPublish`: publicar habla con LinkedIn y, si el
        // borrador lleva imagen, sube el archivo antes.
        request.timeoutInterval = 90
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")

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
            return try await sendPublishDraft(draftId: draftId, canRefresh: false)
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
            return try JSONDecoder().decode(SocialContentPublishResult.self, from: data)
        } catch {
            throw APIClient.APIError.respuestaInvalida
        }
    }

    private func sendPublish(
        _ input: SocialContentPublishRequest,
        canRefresh: Bool
    ) async throws -> SocialContentPublishResult {
        let token = try await client.tokenDeAccesoValido()
        var request = URLRequest(url: try await client.urlCompleta("/v1/content/social/publish"))
        request.httpMethod = "POST"
        request.timeoutInterval = 90
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
            return try await sendPublish(input, canRefresh: false)
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
            return try JSONDecoder().decode(SocialContentPublishResult.self, from: data)
        } catch {
            throw APIClient.APIError.respuestaInvalida
        }
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
