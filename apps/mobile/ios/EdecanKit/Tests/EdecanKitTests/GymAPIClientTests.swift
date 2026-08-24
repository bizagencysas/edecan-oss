import Foundation
import Testing
@testable import EdecanKit

/// Métodos de `APIClient` del feature gimnasio: método HTTP + ruta + body +
/// decodificación, con la red falseada (mismo patrón que
/// `APISessionRaceTests`). Verifica también la degradación `404 → nil`.
@Suite(.serialized)
struct GymAPIClientTests {
    private static let sessionJSON = """
    {
      "id": "gym-s1", "estado": "active",
      "plan": {"titulo": "Empuje", "objetivo": "Fuerza", "duracion_min": 45, "imagen_url": null,
               "ejercicios": [{"nombre": "Press banca", "musculo": "Pecho", "series": 4,
                               "repeticiones": "8-10", "descanso_seg": 90, "notas": ""}]},
      "started_at": "2026-08-16T10:00:00Z",
      "series": [{"ejercicio_idx": 0, "repeticiones": 10, "peso_kg": 60.5, "en": "x"}],
      "progreso": {"ejercicios": [{"idx": 0, "series_hechas": 1, "series_total": 4}]}
    }
    """

    @Test func gymCheckinMandaRespuestaYDecodifica() async throws {
        let tokens = GymLockedAuthTokenStore(access: "access-gym", refresh: "refresh-gym")
        let session = gymStubSession { request in
            #expect(request.httpMethod == "POST")
            #expect(request.url?.path == "/v1/gym/checkin")
            #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer access-gym")
            let json = try #require(
                JSONSerialization.jsonObject(with: requestBody(request)) as? [String: Any]
            )
            #expect(json["respuesta"] as? String == "si")
            return (200, Data("""
            {"ok": true, "plan": null, "session": \(Self.sessionJSON), "mensaje": "¡A entrenar!"}
            """.utf8))
        }
        let api = APIClient(
            baseURL: try #require(URL(string: "https://edecan.test")),
            urlSession: session,
            tokenStore: tokens
        )

        let out = try await api.gymCheckin(respuesta: "si")

        #expect(out.ok == true)
        #expect(out.plan == nil)
        #expect(out.session?.id == "gym-s1")
        #expect(out.message == "¡A entrenar!")
    }

    @Test func gymPlanDeHoyDecodificaPlan() async throws {
        let tokens = GymLockedAuthTokenStore(access: "access-gym", refresh: "refresh-gym")
        let session = gymStubSession { request in
            #expect(request.httpMethod == "GET")
            #expect(request.url?.path == "/v1/gym/plan/today")
            return (200, Data("""
            {"plan": {"titulo": "Empuje", "objetivo": "Fuerza", "duracion_min": 45,
                      "imagen_url": null, "ejercicios": []}}
            """.utf8))
        }
        let api = APIClient(
            baseURL: try #require(URL(string: "https://edecan.test")),
            urlSession: session,
            tokenStore: tokens
        )

        let plan = try await api.gymPlanDeHoy()

        #expect(plan?.title == "Empuje")
        #expect(plan?.durationMinutes == 45)
    }

    @Test func gymPlanDeHoyDevuelveNilEn404() async throws {
        let tokens = GymLockedAuthTokenStore(access: "access-gym", refresh: "refresh-gym")
        let session = gymStubSession { _ in
            (404, Data(#"{"detail": "router no desplegado"}"#.utf8))
        }
        let api = APIClient(
            baseURL: try #require(URL(string: "https://edecan.test")),
            urlSession: session,
            tokenStore: tokens
        )

        let plan = try await api.gymPlanDeHoy()

        #expect(plan == nil)
    }

    @Test func gymSesionActualDevuelveNilEn404YDecodificaEn200() async throws {
        let tokens = GymLockedAuthTokenStore(access: "access-gym", refresh: "refresh-gym")
        let calls = GymLockedCounter()
        let session = gymStubSession { request in
            #expect(request.httpMethod == "GET")
            #expect(request.url?.path == "/v1/gym/session")
            if calls.increment() == 1 {
                return (404, Data(#"{"detail": "sin sesión"}"#.utf8))
            }
            return (200, Data(#"{"session": \#(Self.sessionJSON)}"#.utf8))
        }
        let api = APIClient(
            baseURL: try #require(URL(string: "https://edecan.test")),
            urlSession: session,
            tokenStore: tokens
        )

        let ausente = try await api.gymSesionActual()
        #expect(ausente == nil)

        let activa = try await api.gymSesionActual()
        #expect(activa?.id == "gym-s1")
    }

    @Test func gymRegistrarSerieMandaBodyCorrecto() async throws {
        let tokens = GymLockedAuthTokenStore(access: "access-gym", refresh: "refresh-gym")
        let session = gymStubSession { request in
            #expect(request.httpMethod == "POST")
            #expect(request.url?.path == "/v1/gym/sessions/gym-s1/sets")
            let json = try #require(
                JSONSerialization.jsonObject(with: requestBody(request)) as? [String: Any]
            )
            #expect(json["ejercicio_idx"] as? Int == 2)
            #expect(json["repeticiones"] as? Int == 12)
            #expect(json["peso_kg"] as? Double == 70)
            return (200, Data(#"{"session": \#(Self.sessionJSON), "mensaje": "Serie registrada"}"#.utf8))
        }
        let api = APIClient(
            baseURL: try #require(URL(string: "https://edecan.test")),
            urlSession: session,
            tokenStore: tokens
        )

        let out = try await api.gymRegistrarSerie(
            sessionId: "gym-s1", ejercicioIdx: 2, repeticiones: 12, pesoKg: 70
        )

        #expect(out.session.id == "gym-s1")
        #expect(out.message == "Serie registrada")
    }

    @Test func gymTerminarSesionSinCuerpoDecodificaSessionYMensaje() async throws {
        let tokens = GymLockedAuthTokenStore(access: "access-gym", refresh: "refresh-gym")
        let session = gymStubSession { request in
            #expect(request.httpMethod == "POST")
            #expect(request.url?.path == "/v1/gym/sessions/gym-s1/complete")
            #expect(request.httpBody == nil)
            return (200, Data(#"{"session": \#(Self.sessionJSON), "mensaje": "¡Sesión completada!"}"#.utf8))
        }
        let api = APIClient(
            baseURL: try #require(URL(string: "https://edecan.test")),
            urlSession: session,
            tokenStore: tokens
        )

        let out = try await api.gymTerminarSesion(sessionId: "gym-s1")

        #expect(out.session.id == "gym-s1")
        #expect(out.message == "¡Sesión completada!")
    }

    @Test func gymPausarYReanudarDecodificanEnvoltorioSession() async throws {
        let tokens = GymLockedAuthTokenStore(access: "access-gym", refresh: "refresh-gym")
        let calls = GymLockedCounter()
        let session = gymStubSession { request in
            #expect(request.httpMethod == "POST")
            if calls.increment() == 1 {
                #expect(request.url?.path == "/v1/gym/sessions/gym-s1/pause")
            } else {
                #expect(request.url?.path == "/v1/gym/sessions/gym-s1/resume")
            }
            return (200, Data(#"{"session": \#(Self.sessionJSON)}"#.utf8))
        }
        let api = APIClient(
            baseURL: try #require(URL(string: "https://edecan.test")),
            urlSession: session,
            tokenStore: tokens
        )

        let pausada = try await api.gymPausarSesion(sessionId: "gym-s1")
        let reanudada = try await api.gymReanudarSesion(sessionId: "gym-s1")

        #expect(pausada.id == "gym-s1")
        #expect(reanudada.id == "gym-s1")
        #expect(calls.value == 2)
    }

    @Test func gymHistorialUsaLimitYDecodificaLista() async throws {
        let tokens = GymLockedAuthTokenStore(access: "access-gym", refresh: "refresh-gym")
        let session = gymStubSession { request in
            #expect(request.httpMethod == "GET")
            #expect(request.url?.path == "/v1/gym/history")
            let url = try #require(request.url)
            let components = try #require(URLComponents(url: url, resolvingAgainstBaseURL: false))
            #expect(
                components.queryItems?.first(where: { $0.name == "limit" })?.value == "30"
            )
            return (200, Data(#"{"sessions": [\#(Self.sessionJSON)]}"#.utf8))
        }
        let api = APIClient(
            baseURL: try #require(URL(string: "https://edecan.test")),
            urlSession: session,
            tokenStore: tokens
        )

        let historial = try await api.gymHistorial()

        #expect(historial.count == 1)
        #expect(historial[0].id == "gym-s1")
    }

    private func gymStubSession(
        handler: @escaping @Sendable (URLRequest) async throws -> (Int, Data)
    ) -> URLSession {
        GymStubURLProtocol.handler = handler
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [GymStubURLProtocol.self]
        return URLSession(configuration: configuration)
    }
}

private struct UncheckedSendable<Value>: @unchecked Sendable {
    let value: Value
}

private final class GymStubURLProtocol: URLProtocol, @unchecked Sendable {
    nonisolated(unsafe) static var handler: (@Sendable (URLRequest) async throws -> (Int, Data))?
    private var work: Task<Void, Never>?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let request = UncheckedSendable(value: request)
        let client = UncheckedSendable(value: client)
        let protocolObject = UncheckedSendable(value: self)
        work = Task.detached { [request, client, protocolObject] in
            do {
                guard let handler = Self.handler, let url = request.value.url else {
                    throw URLError(.badURL)
                }
                let (status, data) = try await handler(request.value)
                let response = HTTPURLResponse(
                    url: url,
                    statusCode: status,
                    httpVersion: "HTTP/1.1",
                    headerFields: ["Content-Type": "application/json"]
                )!
                client.value?.urlProtocol(protocolObject.value, didReceive: response, cacheStoragePolicy: .notAllowed)
                client.value?.urlProtocol(protocolObject.value, didLoad: data)
                client.value?.urlProtocolDidFinishLoading(protocolObject.value)
            } catch {
                client.value?.urlProtocol(protocolObject.value, didFailWithError: error)
            }
        }
    }

    override func stopLoading() {
        work?.cancel()
        work = nil
    }
}

private final class GymLockedCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var count = 0
    func increment() -> Int {
        lock.withLock {
            count += 1
            return count
        }
    }
    var value: Int { lock.withLock { count } }
}

private final class GymLockedAuthTokenStore: AuthTokenStoring, @unchecked Sendable {
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

private func requestBody(_ request: URLRequest) throws -> Data {
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
