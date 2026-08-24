import Foundation
import Testing
@testable import EdecanKit

@Suite(.serialized)
struct HablarStreamTests {
    @Test func hablarStreamPosteaSpeakStreamYRindeChunks() async throws {
        let tokens = VoiceLockedAuthTokenStore(access: "access-voz", refresh: "refresh-voz")
        let session = voiceStubSession { request in
            #expect(request.httpMethod == "POST")
            #expect(request.url?.path == "/v1/voice/speak/stream")
            #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer access-voz")
            let json = try #require(
                JSONSerialization.jsonObject(with: voiceRequestBody(request)) as? [String: Any]
            )
            #expect(json["text"] as? String == "Hola.")
            #expect(json["voice_id"] as? String == "voice-1")
            #expect(json["model_id"] as? String == "eleven_turbo_v2_5")
            return (200, "audio/mpeg", Data("AAAA".utf8) + Data("BBBB".utf8))
        }
        let api = APIClient(
            baseURL: try #require(URL(string: "https://edecan.test")),
            urlSession: session,
            tokenStore: tokens
        )

        let stream = try await api.hablarStream(
            texto: "Hola.", voiceId: "voice-1", modelId: "eleven_turbo_v2_5"
        )
        var juntos = Data()
        var mime: String?
        for try await trozo in stream {
            juntos.append(trozo.chunk)
            mime = trozo.mime
        }
        #expect(mime == "audio/mpeg")
        #expect(juntos == Data("AAAABBBB".utf8))
    }

    @Test func hablarStreamPropagaErrorHTTP() async throws {
        let tokens = VoiceLockedAuthTokenStore(access: "access-voz", refresh: "refresh-voz")
        let session = voiceStubSession { _ in
            (502, "application/json", Data(#"{"detail":"ElevenLabs caído"}"#.utf8))
        }
        let api = APIClient(
            baseURL: try #require(URL(string: "https://edecan.test")),
            urlSession: session,
            tokenStore: tokens
        )

        await #expect(throws: APIClient.APIError.self) {
            _ = try await api.hablarStream(texto: "Hola.")
        }
    }
}

private final class VoiceStubURLProtocol: URLProtocol, @unchecked Sendable {
    nonisolated(unsafe) static var handler: (@Sendable (URLRequest) async throws -> (Int, String, Data))?
    private var work: Task<Void, Never>?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        work = Task {
            do {
                guard let handler = Self.handler, let url = request.url else {
                    throw URLError(.badURL)
                }
                let (status, mime, data) = try await handler(request)
                let response = HTTPURLResponse(
                    url: url,
                    statusCode: status,
                    httpVersion: "HTTP/1.1",
                    headerFields: ["Content-Type": mime]
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

private final class VoiceLockedAuthTokenStore: AuthTokenStoring, @unchecked Sendable {
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
}

private func voiceStubSession(
    handler: @escaping @Sendable (URLRequest) async throws -> (Int, String, Data)
) -> URLSession {
    VoiceStubURLProtocol.handler = handler
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [VoiceStubURLProtocol.self]
    return URLSession(configuration: configuration)
}

private func voiceRequestBody(_ request: URLRequest) throws -> Data {
    if let body = request.httpBody {
        return body
    }
    guard let stream = request.httpBodyStream else {
        throw URLError(.cannotDecodeContentData)
    }
    stream.open()
    defer { stream.close() }
    var body = Data()
    var buffer = [UInt8](repeating: 0, count: 4_096)
    while true {
        let count = stream.read(&buffer, maxLength: buffer.count)
        if count < 0 {
            throw stream.streamError ?? URLError(.cannotDecodeContentData)
        }
        if count == 0 {
            return body
        }
        body.append(contentsOf: buffer.prefix(count))
    }
}
