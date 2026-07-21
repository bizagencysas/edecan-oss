import Foundation
import Testing
@testable import EdecanKit

@Suite(.serialized)
struct APISessionRaceTests {
    @Test func subidaDeArchivoUsaMultipartAutenticadoYDevuelveUUID() async throws {
        let tokens = LockedAuthTokenStore(access: "access-upload", refresh: "refresh-upload")
        let session = stubSession { request in
            #expect(request.url?.path == "/v1/files")
            #expect(request.httpMethod == "POST")
            #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer access-upload")
            #expect(request.value(forHTTPHeaderField: "Content-Type")?.hasPrefix("multipart/form-data; boundary=Edecan-File-") == true)
            return (201, Data("""
            {"id":"018f7f4c-07f4-7ed0-93c8-cf0525d1092b","filename":"brief.pdf","mime":"application/pdf","size_bytes":4,"status":"uploaded","created_at":"2026-07-01T10:00:00Z"}
            """.utf8))
        }
        let api = APIClient(
            baseURL: try #require(URL(string: "https://edecan.test")),
            urlSession: session,
            tokenStore: tokens
        )
        let source = FileManager.default.temporaryDirectory.appendingPathComponent("edecan-upload-test-\(UUID().uuidString).pdf")
        try Data("test".utf8).write(to: source)
        defer { try? FileManager.default.removeItem(at: source) }

        let result = try await api.subirArchivo(desde: source, filename: "brief.pdf", mimeType: "application/pdf")

        #expect(result.id == "018f7f4c-07f4-7ed0-93c8-cf0525d1092b")
        #expect(result.filename == "brief.pdf")
    }

    @Test func archivoMayorA25MBSeRechazaAntesDeMultipartYRed() async throws {
        let tokens = LockedAuthTokenStore(access: "access-upload", refresh: "refresh-upload")
        let session = stubSession { _ in
            Issue.record("Un archivo fuera de política no debía tocar la red")
            return (500, Data())
        }
        let api = APIClient(
            baseURL: try #require(URL(string: "https://edecan.test")),
            urlSession: session,
            tokenStore: tokens
        )
        let source = FileManager.default.temporaryDirectory.appendingPathComponent("edecan-oversize-\(UUID().uuidString).bin")
        #expect(FileManager.default.createFile(atPath: source.path, contents: nil))
        let handle = try FileHandle(forWritingTo: source)
        try handle.truncate(atOffset: UInt64(FileUploadPolicy.maximumBytes + 1))
        try handle.close()
        defer { try? FileManager.default.removeItem(at: source) }

        do {
            _ = try await api.subirArchivo(desde: source, filename: "grande.bin")
            Issue.record("Debía rechazar el archivo antes del multipart")
        } catch let error as FileUploadPolicy.ValidationError {
            #expect(error == .tooLarge(
                actualBytes: FileUploadPolicy.maximumBytes + 1,
                maximumBytes: FileUploadPolicy.maximumBytes
            ))
        }
    }

    @Test func obtieneDetalleDeConversacionConRutaAutenticada() async throws {
        let tokens = LockedAuthTokenStore(access: "access-chat", refresh: "refresh-chat")
        let session = stubSession { request in
            #expect(request.url?.path == "/v1/conversations/conversation-1")
            #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer access-chat")
            return (200, Data("""
            {"id":"conversation-1","title":null,"channel":"web","created_at":"2026-07-01T10:00:00Z","messages":[]}
            """.utf8))
        }
        let api = APIClient(
            baseURL: try #require(URL(string: "https://edecan.test")),
            urlSession: session,
            tokenStore: tokens
        )

        let detail = try await api.obtenerConversacion(id: "conversation-1")

        #expect(detail.id == "conversation-1")
        #expect(detail.messages.isEmpty)
    }

    @Test func descargaArtefactoUsaRutaPrivadaYBearerSinRedReal() async throws {
        let tokens = LockedAuthTokenStore(access: "access-privado", refresh: "refresh-privado")
        let bytes = Data("contenido-pdf-sintetico".utf8)
        let session = stubSession { request in
            #expect(request.url?.path == "/v1/files/018f7f4c-07f4-7ed0-93c8-cf0525d1092b/download")
            #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer access-privado")
            return (200, bytes)
        }
        let api = APIClient(
            baseURL: try #require(URL(string: "https://edecan.test")),
            urlSession: session,
            tokenStore: tokens
        )
        let artifact = ArtifactRef(
            fileId: "018f7f4c-07f4-7ed0-93c8-cf0525d1092b",
            filename: "propuesta.pdf",
            mime: "application/pdf"
        )

        let downloaded = try await api.descargarArtefacto(artifact)

        #expect(downloaded == DownloadedArtifact(artifact: artifact, data: bytes))
    }

    @Test func refreshAusenteTambienNotificaExpiracionSinTocarLaRed() async throws {
        let tokens = LockedAuthTokenStore(access: "access-sin-refresh", refresh: nil)
        let expiration = AsyncFlag()
        let session = stubSession { _ in
            Issue.record("No debía intentar una petición sin refresh token")
            throw URLError(.badServerResponse)
        }
        let api = APIClient(
            baseURL: try #require(URL(string: "https://edecan.test")),
            urlSession: session,
            tokenStore: tokens,
            onSessionExpired: { await expiration.set() }
        )

        do {
            _ = try await api.refrescar()
            Issue.record("La ausencia de refresh debía expirar la sesión")
        } catch let error as APIClient.APIError {
            #expect(error == .sesionExpirada)
        }

        #expect(tokens.snapshot() == .init(access: nil, refresh: nil))
        #expect(await expiration.value)
    }

    @Test func refreshRechazadoLimpiaTokensYNotificaExpiracionGlobal() async throws {
        let tokens = LockedAuthTokenStore(access: "access-viejo", refresh: "refresh-revocado")
        let expiration = AsyncFlag()
        let session = stubSession { request in
            #expect(request.url?.path == "/v1/auth/refresh")
            return (401, Data(#"{"detail":"revocado"}"#.utf8))
        }
        let api = APIClient(
            baseURL: try #require(URL(string: "https://edecan.test")),
            urlSession: session,
            tokenStore: tokens,
            onSessionExpired: { await expiration.set() }
        )

        do {
            _ = try await api.refrescar()
            Issue.record("El refresh revocado debía expirar la sesión")
        } catch let error as APIClient.APIError {
            #expect(error == .sesionExpirada)
        }

        #expect(tokens.snapshot() == .init(access: nil, refresh: nil))
        #expect(await expiration.value)
    }

    @Test func refreshEnVueloNoPuedeReponerTokensDespuesDeLogout() async throws {
        let tokens = LockedAuthTokenStore(access: "access-viejo", refresh: "refresh-viejo")
        let refreshStarted = AsyncGate()
        let allowRefresh = AsyncGate()
        let session = stubSession { request in
            switch request.url?.path {
            case "/v1/auth/refresh":
                await refreshStarted.open()
                await allowRefresh.wait()
                return (200, Data(#"{"access_token":"access-huerfano","refresh_token":"refresh-huerfano"}"#.utf8))
            case "/v1/auth/logout":
                return (200, Data(#"{"revoked":true}"#.utf8))
            default:
                throw URLError(.badURL)
            }
        }
        let api = APIClient(
            baseURL: try #require(URL(string: "https://edecan.test")),
            urlSession: session,
            tokenStore: tokens
        )

        let refresh = Task { try await api.refrescar() }
        await refreshStarted.wait()
        await api.cerrarSesion()
        await allowRefresh.open()
        _ = try? await refresh.value

        #expect(tokens.snapshot() == .init(access: nil, refresh: nil))
        #expect(await api.haySesion == false)
    }

    @Test func respuestaMeAnteriorAlLogoutSeDescartaPorEpoch() async throws {
        let tokens = LockedAuthTokenStore(access: "access-viejo", refresh: "refresh-viejo")
        let meStarted = AsyncGate()
        let allowMe = AsyncGate()
        let session = stubSession { request in
            switch request.url?.path {
            case "/v1/me":
                await meStarted.open()
                await allowMe.wait()
                return (200, Data("""
                {
                  "user":{"id":"u1","email":"anterior@edecan.test","is_superadmin":false,"created_at":"2026-01-01T00:00:00Z"},
                  "tenant":{"id":"t1","name":"Tenant anterior","slug":"anterior","plan_key":"free_selfhost","status":"active","created_at":"2026-01-01T00:00:00Z"},
                  "flags":{}
                }
                """.utf8))
            case "/v1/auth/logout":
                return (200, Data(#"{"revoked":true}"#.utf8))
            default:
                throw URLError(.badURL)
            }
        }
        let api = APIClient(
            baseURL: try #require(URL(string: "https://edecan.test")),
            urlSession: session,
            tokenStore: tokens
        )

        let oldMe = Task { try await api.me() }
        await meStarted.wait()
        await api.cerrarSesion()
        await allowMe.open()
        do {
            _ = try await oldMe.value
            Issue.record("La respuesta de /me del epoch anterior debía descartarse")
        } catch let error as APIClient.APIError {
            #expect(error == .sesionExpirada)
        }

        #expect(tokens.snapshot() == .init(access: nil, refresh: nil))
    }

    private func stubSession(
        handler: @escaping @Sendable (URLRequest) async throws -> (Int, Data)
    ) -> URLSession {
        StubURLProtocol.handler = handler
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubURLProtocol.self]
        return URLSession(configuration: configuration)
    }
}

private final class StubURLProtocol: URLProtocol, @unchecked Sendable {
    nonisolated(unsafe) static var handler: (@Sendable (URLRequest) async throws -> (Int, Data))?
    private var work: Task<Void, Never>?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        work = Task {
            do {
                guard let handler = Self.handler, let url = request.url else {
                    throw URLError(.badURL)
                }
                let (status, data) = try await handler(request)
                let response = HTTPURLResponse(
                    url: url,
                    statusCode: status,
                    httpVersion: "HTTP/1.1",
                    headerFields: ["Content-Type": "application/json"]
                )!
                client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
                client?.urlProtocol(self, didLoad: data)
                client?.urlProtocolDidFinishLoading(self)
            } catch {
                client?.urlProtocol(self, didFailWithError: error)
            }
        }
    }

    override func stopLoading() {
        work?.cancel()
        work = nil
    }
}

private final class LockedAuthTokenStore: AuthTokenStoring, @unchecked Sendable {
    struct Snapshot: Equatable {
        let access: String?
        let refresh: String?
    }

    private let lock = NSLock()
    private var access: String?
    private var refresh: String?

    init(access: String?, refresh: String?) {
        self.access = access
        self.refresh = refresh
    }

    func accessToken() -> String? { lock.withLock { access } }
    func refreshToken() -> String? { lock.withLock { refresh } }
    func save(accessToken: String, refreshToken: String) {
        lock.withLock {
            access = accessToken
            refresh = refreshToken
        }
    }
    func clear() {
        lock.withLock {
            access = nil
            refresh = nil
        }
    }
    func snapshot() -> Snapshot { lock.withLock { Snapshot(access: access, refresh: refresh) } }
}

private actor AsyncFlag {
    private(set) var value = false
    func set() { value = true }
}

private actor AsyncGate {
    private var isOpen = false
    private var continuations: [CheckedContinuation<Void, Never>] = []

    func wait() async {
        if isOpen { return }
        await withCheckedContinuation { continuations.append($0) }
    }

    func open() {
        guard !isOpen else { return }
        isOpen = true
        let pending = continuations
        continuations.removeAll()
        pending.forEach { $0.resume() }
    }
}
