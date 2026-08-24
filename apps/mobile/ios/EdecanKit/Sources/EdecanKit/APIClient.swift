import Foundation

protocol AuthTokenStoring: Sendable {
    func accessToken() -> String?
    func refreshToken() -> String?
    func save(accessToken: String, refreshToken: String)
    func clear()
}

struct KeychainAuthTokenStore: AuthTokenStoring {
    func accessToken() -> String? { Keychain.get(KeychainKeys.accessToken) }
    func refreshToken() -> String? { Keychain.get(KeychainKeys.refreshToken) }
    func save(accessToken: String, refreshToken: String) {
        Keychain.set(accessToken, for: KeychainKeys.accessToken)
        Keychain.set(refreshToken, for: KeychainKeys.refreshToken)
    }
    func clear() {
        Keychain.delete(KeychainKeys.accessToken)
        Keychain.delete(KeychainKeys.refreshToken)
    }
}

protocol DevicePairingCredentialStoring: Sendable {
    func deviceId() -> String?
    func deviceToken() -> String?
    func save(deviceId: String, deviceToken: String)
    func clear()
}

struct KeychainDevicePairingCredentialStore: DevicePairingCredentialStoring {
    func deviceId() -> String? { Keychain.get(KeychainKeys.deviceId) }
    func deviceToken() -> String? { Keychain.get(KeychainKeys.deviceToken) }
    func save(deviceId: String, deviceToken: String) {
        Keychain.set(deviceId, for: KeychainKeys.deviceId)
        Keychain.setDeviceBound(deviceToken, for: KeychainKeys.deviceToken)
    }
    func clear() {
        Keychain.delete(KeychainKeys.deviceId)
        Keychain.delete(KeychainKeys.deviceToken)
    }
}

struct EmptyDevicePairingCredentialStore: DevicePairingCredentialStoring {
    func deviceId() -> String? { nil }
    func deviceToken() -> String? { nil }
    func save(deviceId: String, deviceToken: String) {}
    func clear() {}
}

/// Cliente tipado a mano contra `/v1/*` (`docs/api.md`, `ARCHITECTURE.md`
/// §10.12) usando `URLSession` + `async/await` — sin generar código, sin
/// dependencias externas, para que quede claro exactamente qué pide y qué
/// espera cada endpoint. Un `actor` porque guarda estado mutable (los
/// tokens en memoria) que puede tocarse desde varias tareas a la vez (p.
/// ej. una pantalla pidiendo `/v1/me` mientras otra manda un mensaje).
///
/// El envío de mensajes de chat (`POST /v1/conversations/{id}/messages`,
/// SSE) NO vive aquí — ver ``SSEClient``. Este cliente sí sabe construir la
/// URL/cabecera de autenticación que ese stream necesita
/// (``tokenDeAccesoValido()`` / ``urlCompleta(_:)``), pero la lectura
/// línea-por-línea del stream es responsabilidad de `SSEClient`.
public actor APIClient {
    /// Errores tipados en español — se muestran tal cual en la UI
    /// (`OnboardingView`, `ChatView`), así que el texto ya viene listo para
    /// una persona, no para un log.
    public enum APIError: Error, LocalizedError, Sendable, Equatable {
        case urlInvalida
        case sinConexion(detalle: String)
        case credencialesInvalidas
        case sesionExpirada
        case servidor(status: Int, mensaje: String)
        case respuestaInvalida

        public var errorDescription: String? {
            switch self {
            case .urlInvalida:
                return "La URL del servidor no es válida."
            case .sinConexion(let detalle):
                return "No se pudo conectar con el servidor: \(detalle)"
            case .credencialesInvalidas:
                return "Correo, contraseña o código de verificación incorrectos."
            case .sesionExpirada:
                return "La sesión expiró. Vuelve a iniciar sesión."
            case .servidor(let status, let mensaje):
                return "El servidor respondió con un error (\(status)): \(mensaje)"
            case .respuestaInvalida:
                return "El servidor envió una respuesta que no se pudo interpretar."
            }
        }
    }

    private var baseURL: URL
    private var accessToken: String?
    private var refreshToken: String?
    private var refreshTask: (epoch: UInt64, task: Task<TokenPair, Error>)?
    private var sessionEpoch: UInt64 = 0
    private let urlSession: URLSession
    private let tokenStore: any AuthTokenStoring
    private let devicePairingStore: any DevicePairingCredentialStoring
    private let onSessionExpired: (@Sendable () async -> Void)?
    private let decoder: JSONDecoder
    private let encoder: JSONEncoder

    public init(
        baseURL: URL,
        urlSession: URLSession = .shared,
        onSessionExpired: (@Sendable () async -> Void)? = nil
    ) {
        let store = KeychainAuthTokenStore()
        self.baseURL = baseURL
        self.urlSession = urlSession
        self.tokenStore = store
        self.devicePairingStore = KeychainDevicePairingCredentialStore()
        self.onSessionExpired = onSessionExpired
        self.decoder = APIClient.crearDecoder()
        self.encoder = APIClient.crearEncoder()
        self.accessToken = store.accessToken()
        self.refreshToken = store.refreshToken()
    }

    init(
        baseURL: URL,
        urlSession: URLSession,
        tokenStore: any AuthTokenStoring,
        devicePairingStore: any DevicePairingCredentialStoring = EmptyDevicePairingCredentialStore(),
        onSessionExpired: (@Sendable () async -> Void)? = nil
    ) {
        self.baseURL = baseURL
        self.urlSession = urlSession
        self.tokenStore = tokenStore
        self.devicePairingStore = devicePairingStore
        self.onSessionExpired = onSessionExpired
        self.decoder = APIClient.crearDecoder()
        self.encoder = APIClient.crearEncoder()
        self.accessToken = tokenStore.accessToken()
        self.refreshToken = tokenStore.refreshToken()
    }

    /// Cambia el servidor contra el que habla este cliente (p. ej. si el
    /// onboarding vuelve atrás y el usuario corrige la URL).
    public func actualizarBaseURL(_ url: URL) {
        baseURL = url
    }

    public var haySesion: Bool {
        refreshToken != nil
            || (devicePairingStore.deviceId() != nil && devicePairingStore.deviceToken() != nil)
    }

    // MARK: - Autenticación

    /// `POST /v1/auth/register`. Crea tenant + usuario `owner` + persona por
    /// defecto y deja la sesión iniciada (mismos tokens que `login`) —
    /// `docs/api.md` §"Autenticación y sesión". `409` si ya existe una cuenta
    /// con ese correo (`APIError.servidor(status: 409, ...)`, mensaje del
    /// backend tal cual).
    @discardableResult
    public func registrar(email: String, password: String, tenantName: String) async throws -> TokenPair {
        let epoch = comenzarNuevaSesion()
        struct Body: Encodable {
            let email: String
            let password: String
            let tenantName: String
            enum CodingKeys: String, CodingKey {
                case email, password
                case tenantName = "tenant_name"
            }
        }
        let body = Body(email: email, password: password, tenantName: tenantName)
        let tokens: TokenPair = try await peticionSinSesion(path: "/v1/auth/register", body: body)
        try guardarTokens(tokens, expectedEpoch: epoch)
        return tokens
    }

    /// `POST /v1/auth/login`. `totpCode` solo hace falta si la cuenta tiene
    /// 2FA activado — se manda solo si no viene vacío.
    @discardableResult
    public func login(email: String, password: String, totpCode: String? = nil) async throws -> TokenPair {
        let epoch = comenzarNuevaSesion()
        struct Body: Encodable {
            let email: String
            let password: String
            let totpCode: String?
            enum CodingKeys: String, CodingKey {
                case email, password
                case totpCode = "totp_code"
            }
        }
        let body = Body(email: email, password: password, totpCode: totpCodeOVacio(totpCode))
        let tokens: TokenPair = try await peticionSinSesion(path: "/v1/auth/login", body: body)
        try guardarTokens(tokens, expectedEpoch: epoch)
        return tokens
    }

    /// Claim sin autenticación del QR emitido por una sesión ya autorizada.
    /// Persiste JWT y credencial durable antes de devolver éxito, de modo que
    /// un cierre de la app entre la respuesta y la UI no pierda el pairing.
    @discardableResult
    public func reclamarEmparejamiento(
        pairingToken: String,
        nombre: String,
        fingerprint: String?
    ) async throws -> PairingClaimOut {
        let epoch = comenzarNuevaSesion()
        struct Body: Encodable {
            let pairingToken: String
            let nombre: String
            let plataforma = "ios"
            let kind = "mobile"
            let fingerprint: String?

            enum CodingKeys: String, CodingKey {
                case pairingToken = "pairing_token"
                case nombre, plataforma, kind, fingerprint
            }
        }
        let claim: PairingClaimOut = try await peticionSinSesion(
            path: "/v1/devices/pairing/claim",
            body: Body(pairingToken: pairingToken, nombre: nombre, fingerprint: fingerprint)
        )
        try guardarTokens(claim.tokens, expectedEpoch: epoch)
        guard sessionEpoch == epoch else { throw APIError.sesionExpirada }
        devicePairingStore.save(deviceId: claim.deviceId, deviceToken: claim.deviceToken)
        return claim
    }

    /// `POST /v1/auth/refresh`. El backend rota el refresh token en cada uso,
    /// por lo que las renovaciones concurrentes comparten una sola tarea. Un
    /// `401` invalida la sesión persistida; no representa credenciales de
    /// login mal escritas.
    @discardableResult
    public func refrescar(totpCode: String? = nil) async throws -> TokenPair {
        let epoch = sessionEpoch
        if let refreshTask, refreshTask.epoch == epoch {
            do {
                let tokens = try await refreshTask.task.value
                try guardarTokens(tokens, expectedEpoch: epoch)
                return tokens
            } catch APIError.credencialesInvalidas {
                await expirarSesionSiVigente(epoch)
                throw APIError.sesionExpirada
            }
        }
        let refreshTokenActual = refreshToken
        let codigo = totpCodeOVacio(totpCode)
        let task = Task {
            try await self.ejecutarRefreshConRecuperacionDurable(
                refreshToken: refreshTokenActual,
                totpCode: codigo
            )
        }
        refreshTask = (epoch, task)
        defer {
            if sessionEpoch == epoch {
                refreshTask = nil
            }
        }
        do {
            let tokens = try await task.value
            try guardarTokens(tokens, expectedEpoch: epoch)
            return tokens
        } catch APIError.credencialesInvalidas {
            await expirarSesionSiVigente(epoch)
            throw APIError.sesionExpirada
        }
    }

    private func ejecutarRefresh(refreshToken: String, totpCode: String?) async throws -> TokenPair {
        struct Body: Encodable {
            let refreshToken: String
            let totpCode: String?
            enum CodingKeys: String, CodingKey {
                case refreshToken = "refresh_token"
                case totpCode = "totp_code"
            }
        }
        return try await peticionSinSesion(
            path: "/v1/auth/refresh",
            body: Body(refreshToken: refreshToken, totpCode: totpCode)
        )
    }

    private func ejecutarRefreshConRecuperacionDurable(
        refreshToken: String?,
        totpCode: String?
    ) async throws -> TokenPair {
        if let refreshToken {
            do {
                return try await ejecutarRefresh(refreshToken: refreshToken, totpCode: totpCode)
            } catch APIError.credencialesInvalidas {
                // Solo un rechazo definitivo activa la vía durable. Un error
                // de red conserva el refresh JWT y se propaga sin expirar.
            }
        }
        return try await ejecutarRefreshDurable()
    }

    private func ejecutarRefreshDurable() async throws -> TokenPair {
        guard let deviceId = devicePairingStore.deviceId(),
              let deviceToken = devicePairingStore.deviceToken()
        else { throw APIError.credencialesInvalidas }
        struct Body: Encodable {
            let deviceId: String
            let deviceToken: String
            enum CodingKeys: String, CodingKey {
                case deviceId = "device_id"
                case deviceToken = "device_token"
            }
        }
        return try await peticionSinSesion(
            path: "/v1/devices/pairing/refresh",
            body: Body(deviceId: deviceId, deviceToken: deviceToken)
        )
    }

    /// Invalida Keychain y memoria de inmediato. Después intenta revocar el
    /// dispositivo y el refresh token con copias capturadas de las
    /// credenciales; estar offline nunca conserva una sesión local.
    public func cerrarSesion(deviceId: String? = nil) async {
        struct Body: Encodable {
            let refreshToken: String
            enum CodingKeys: String, CodingKey { case refreshToken = "refresh_token" }
        }
        let capturedAccessToken = accessToken
        let capturedRefreshToken = refreshToken
        sessionEpoch &+= 1
        refreshTask?.task.cancel()
        refreshTask = nil
        invalidarSesionLocal()

        if let deviceId, let capturedAccessToken {
            do {
                var request = URLRequest(url: try urlCompleta("/v1/devices/\(deviceId)/revoke"))
                request.httpMethod = "POST"
                request.setValue("Bearer \(capturedAccessToken)", forHTTPHeaderField: "Authorization")
                _ = try await realizar(request)
            } catch {
                // Best-effort: la sesión local ya está invalidada.
            }
        }
        if let capturedRefreshToken {
            do {
                var request = URLRequest(url: try urlCompleta("/v1/auth/logout"))
                request.httpMethod = "POST"
                request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                request.httpBody = try encoder.encode(Body(refreshToken: capturedRefreshToken))
                _ = try await realizar(request)
            } catch {
                // Best-effort: la sesión local ya está invalidada.
            }
        }
    }

    private func invalidarSesionLocal() {
        accessToken = nil
        refreshToken = nil
        tokenStore.clear()
    }

    /// Un access token utilizable ahora mismo, refrescando una vez si hace
    /// falta. Pensado para quien construye su propia petición fuera de este
    /// cliente (``SSEClient``, que abre su propio stream para el chat).
    public func tokenDeAccesoValido() async throws -> String {
        if let accessToken {
            return accessToken
        }
        try await refrescar()
        guard let accessToken else { throw APIError.sesionExpirada }
        return accessToken
    }

    /// Resuelve `path` (p. ej. `"/v1/conversations/\(id)/messages"`) contra
    /// la `baseURL` configurada. Público para que `SSEClient` arme su propia
    /// `URLRequest` con la misma base que usa el resto del cliente.
    public func urlCompleta(_ path: String) throws -> URL {
        guard let url = URL(string: path, relativeTo: baseURL) else { throw APIError.urlInvalida }
        return url
    }

    // MARK: - Perfil y conversaciones

    /// `GET /v1/me`.
    public func me() async throws -> Me {
        try await conAutoRefresh { try await self.obtener("/v1/me") }
    }

    /// Configuración server-driven segura para navegación, textos y flags
    /// móviles. Si esta petición falla, la app debe conservar su fallback
    /// local para no bloquear el arranque.
    public func mobileConfig(platform: String = "ios", build: Int = 1) async throws -> MobileServerConfig {
        try await conAutoRefresh {
            try await self.obtenerConQuery(
                "/v1/mobile/config",
                [("platform", platform), ("build", String(build))]
            )
        }
    }

    /// Perfil personal compartido por computador, iOS, Android y el agente.
    public func perfilVivo() async throws -> LiveProfile {
        try await conAutoRefresh { try await self.obtener("/v1/perfil") }
    }

    /// Export portable de datos propios; devuelve el JSON crudo para que la
    /// app pueda compartirlo sin convertirlo en un modelo que pierda campos.
    public func exportarDatosPrivacidad() async throws -> Data {
        let url = try urlCompleta("/v1/privacy/export")
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        let stableRequest = request
        return try await conAutoRefresh {
            let (data, _) = try await self.ejecutarAutenticadoData(stableRequest)
            return data
        }
    }

    /// `DELETE /v1/memory` — borra solo la memoria del usuario; no elimina
    /// conversaciones ni la cuenta.
    public func borrarMemoriaCompleta() async throws {
        let url = try urlCompleta("/v1/memory")
        var request = URLRequest(url: url)
        request.httpMethod = "DELETE"
        let stableRequest = request
        _ = try await conAutoRefresh {
            try await self.ejecutarAutenticadoData(stableRequest)
        }
    }

    /// Guarda de forma atómica la identidad declarada y el resumen editable.
    public func actualizarPerfilVivo(
        identidad: ProfileIdentity,
        resumen: String
    ) async throws -> LiveProfile {
        struct Datos: Encodable { let identidad: ProfileIdentity }
        struct Body: Encodable { let resumen: String; let datos: Datos }
        return try await conAutoRefresh {
            try await self.enviar(
                "/v1/perfil",
                method: "PUT",
                body: Body(resumen: resumen, datos: Datos(identidad: identidad))
            )
        }
    }

    /// `GET /v1/conversations` — más recientes primero (orden que ya
    /// aplica el backend).
    public func listarConversaciones() async throws -> [Conversation] {
        try await conAutoRefresh { try await self.obtener("/v1/conversations") }
    }

    /// `GET /v1/conversations/main` -- resuelve (o crea si no existe) la
    /// conversación "principal" del tenant+usuario actual: ahí aterrizan los
    /// eventos automáticos que el dueño no pidió (llamada recibida,
    /// automatización ejecutada, recordatorio disparado). `HistorialChatView`
    /// la fija arriba del historial.
    public func conversacionPrincipal() async throws -> Conversation {
        try await conAutoRefresh { try await self.obtener("/v1/conversations/main") }
    }

    /// `GET /v1/conversations/{id}` con mensajes y tool logs persistidos.
    public func obtenerConversacion(id: String) async throws -> ConversationDetail {
        try await conAutoRefresh { try await self.obtener("/v1/conversations/\(id)") }
    }

    /// `POST /v1/conversations`. `titulo` es opcional, igual que en la API.
    @discardableResult
    public func crearConversacion(titulo: String? = nil) async throws -> Conversation {
        struct Body: Encodable { let title: String? }
        return try await conAutoRefresh {
            try await self.enviar("/v1/conversations", method: "POST", body: Body(title: titulo))
        }
    }

    /// Cambia el título visible del chat en todos los dispositivos conectados.
    @discardableResult
    public func renombrarConversacion(id: String, titulo: String) async throws -> Conversation {
        struct Body: Encodable { let title: String }
        return try await conAutoRefresh {
            try await self.enviar(
                "/v1/conversations/\(id)", method: "PATCH", body: Body(title: titulo)
            )
        }
    }

    /// `GET /v1/models/chat` — catálogo del selector de modelos del chat.
    /// Es la ÚNICA autoridad de la lista: la app no hardcodea modelos ni sus
    /// capacidades, así agregar uno en `config/modelos.yml` lo hace aparecer
    /// en la hoja sin publicar una versión nueva de iOS.
    public func modelosDeChat() async throws -> ChatModelCatalog {
        try await conAutoRefresh { try await self.obtener("/v1/models/chat") }
    }

    /// `PUT /v1/conversations/{id}/model` — fija modelo y Esfuerzo de ESA
    /// conversación. `modelo: nil` vuelve a automático.
    ///
    /// Es la vía que usa iOS en vez de mandar el modelo en cada mensaje:
    /// permite elegir sin escribir nada, sobrevive reinicios y hace que el
    /// flujo de confirmación (`POST /confirm`, otro request) corra con el
    /// mismo modelo del turno original porque relee estas columnas.
    @discardableResult
    public func fijarModeloDeChat(
        conversacionId: String,
        modelo: String?,
        esfuerzo: EsfuerzoChat?
    ) async throws -> SeleccionModeloChatOut {
        try await conAutoRefresh {
            try await self.enviar(
                "/v1/conversations/\(conversacionId)/model",
                method: "PUT",
                body: SeleccionModeloChatIn(model: modelo, effort: esfuerzo)
            )
        }
    }

    /// `POST /v1/conversations/{id}/clear` -- comando local `/clear`: reinicia
    /// el contexto que arma el próximo turno SIN borrar el historial. Ningún
    /// mensaje se pierde -- solo deja de mandarse al modelo y de listarse por
    /// defecto (ver el docstring del endpoint). Es lo que ejecuta
    /// `ChatViewModel` cuando `LocalChatCommand.parse` reconoce `/clear`
    /// escrito en el composer, en vez de mandarlo como un mensaje normal.
    @discardableResult
    public func limpiarContextoDeConversacion(id: String) async throws -> Conversation {
        try await conAutoRefresh {
            try await self.enviarSinCuerpoConRespuesta(
                "/v1/conversations/\(id)/clear", method: "POST"
            )
        }
    }

    @discardableResult
    public func ramificarConversacion(
        id: String,
        afterMessageId: String? = nil,
        untilMessageId: String? = nil
    ) async throws -> Conversation {
        struct Body: Encodable {
            let after_message_id: String?
            let until_message_id: String?
        }
        return try await conAutoRefresh {
            try await self.enviar(
                "/v1/conversations/\(id)/branch",
                method: "POST",
                body: Body(after_message_id: afterMessageId, until_message_id: untilMessageId)
            )
        }
    }

    @discardableResult
    public func rebobinarConversacion(id: String) async throws -> Conversation {
        try await conAutoRefresh {
            try await self.enviarSinCuerpoConRespuesta(
                "/v1/conversations/\(id)/rewind", method: "POST"
            )
        }
    }

    public func marcarBanderasDeMensaje(
        conversacionId: String,
        mensajeId: String,
        pinned: Bool? = nil,
        bookmark: Bool? = nil
    ) async throws -> ConversationMessage {
        struct Body: Encodable {
            let pinned: Bool?
            let bookmark: Bool?
        }
        return try await conAutoRefresh {
            try await self.enviar(
                "/v1/conversations/\(conversacionId)/messages/\(mensajeId)/flags",
                method: "POST",
                body: Body(pinned: pinned, bookmark: bookmark)
            )
        }
    }

    /// Elimina la conversación (irreversible). El backend ya lo expone
    /// (`DELETE /v1/conversations/{id}` -> 204); solo faltaba en iOS, que
    /// hasta ahora únicamente ofrecía renombrar.
    public func eliminarConversacion(id: String) async throws {
        try await conAutoRefresh {
            try await self.enviarSinCuerpo("/v1/conversations/\(id)", method: "DELETE")
        }
    }

    /// `GET /v1/files/{id}/download` — descarga privada, autenticada y
    /// limitada al tenant del usuario. Devuelve bytes en memoria para que la
    /// app decida si abrir la hoja de compartir o guardarlos en Archivos;
    /// nunca expone una URL publica ni escribe fuera del sandbox por si sola.
    public func descargarArtefacto(_ artifact: ArtifactRef) async throws -> DownloadedArtifact {
        let data = try await conAutoRefresh {
            var request = URLRequest(url: try await self.urlCompleta("/v1/files/\(artifact.fileId)/download"))
            request.httpMethod = "GET"
            return try await self.ejecutarAutenticadoData(request).data
        }
        return DownloadedArtifact(artifact: artifact, data: data)
    }

    /// `GET /v1/files/{id}/thumbnail` — la MISMA imagen, reducida en el
    /// servidor y cacheable.
    ///
    /// Por qué existe: las cards del chat pedían el original por `/download`
    /// —un PNG de 2160x2160, de 3 a 5 MB— solo para dibujarlo a 1280 px. Con
    /// veinte imágenes en una conversación eso son decenas de MB por el túnel
    /// en cada apertura, y a los pocos segundos las descargas se abandonan.
    ///
    /// Cae al original si el servidor todavía no conoce la ruta: la app de
    /// TestFlight y la instancia del Mac se actualizan por separado, y un
    /// servidor viejo NO puede dejar al teléfono sin imágenes.
    public func descargarMiniatura(
        _ artifact: ArtifactRef,
        maxPixeles: Int = 1_280
    ) async throws -> DownloadedArtifact {
        do {
            let data = try await conAutoRefresh {
                var request = URLRequest(
                    url: try await self.urlCompleta(
                        "/v1/files/\(artifact.fileId)/thumbnail?max=\(maxPixeles)"
                    )
                )
                request.httpMethod = "GET"
                return try await self.ejecutarAutenticadoData(request).data
            }
            return DownloadedArtifact(artifact: artifact, data: data)
        } catch let error as APIError {
            // Cualquier "esta ruta no te sirve para este archivo" cae al original en vez
            // de dejar la card sin imagen: 404/405 = servidor viejo sin `/thumbnail`
            // (la instancia del Mac y la app de TestFlight se actualizan por separado),
            // y 415 = mime fuera de `_THUMBNAIL_SOURCE_MIMES`, que el propio endpoint
            // levanta y que igual se puede pintar bajando el archivo entero.
            guard case .servidor(let status, _) = error,
                  status == 404 || status == 405 || status == 415
            else {
                throw error
            }
            return try await descargarArtefacto(artifact)
        }
    }

    /// Registra o reemplaza el token APNs de este dispositivo. El token no
    /// se persiste ni se registra en logs dentro de la app; APNs puede
    /// rotarlo y el cliente vuelve a enviarlo cada vez que iOS lo entrega.
    public func registrarPushToken(deviceId: String, token: String) async throws {
        struct Body: Encodable {
            let pushToken: String
            let pushPlatform = "apns"
            enum CodingKeys: String, CodingKey {
                case pushToken = "push_token"
                case pushPlatform = "push_platform"
            }
        }
        try await conAutoRefresh {
            try await self.enviarSinRespuesta(
                "/v1/devices/\(deviceId)/push-token",
                method: "POST",
                body: Body(pushToken: token)
            )
        }
    }

    /// Elimina el destino push antes de desvincular el dispositivo. Es
    /// independiente de revocar la sesión para que ambos pasos puedan ser
    /// reintentados de forma segura.
    public func revocarPushToken(deviceId: String) async throws {
        try await conAutoRefresh {
            try await self.enviarSinCuerpo(
                "/v1/devices/\(deviceId)/push-token",
                method: "DELETE"
            )
        }
    }

    public func estadoPush() async throws -> PushStatus {
        try await conAutoRefresh { try await self.obtener("/v1/devices/push/status") }
    }

    /// `POST /v1/files` desde un archivo local mediante multipart transmitido
    /// desde disco. No duplica el archivo completo en memoria y conserva el
    /// cuerpo temporal para un unico auto-refresh/reintento de autenticacion.
    public func subirArchivo(
        desde localURL: URL,
        filename: String,
        mimeType: String = "application/octet-stream"
    ) async throws -> UploadedFile {
        // Debe ocurrir antes de `crearArchivoMultipart`: un archivo fuera de
        // política nunca se copia a otro temporal ni abre una petición.
        try FileUploadPolicy.validate(localURL)
        let boundary = "Edecan-File-\(UUID().uuidString)"
        let multipartURL = try Self.crearArchivoMultipart(
            sourceURL: localURL,
            filename: filename,
            mimeType: mimeType,
            boundary: boundary
        )
        defer { try? FileManager.default.removeItem(at: multipartURL) }

        return try await conAutoRefresh {
            var request = URLRequest(url: try await self.urlCompleta("/v1/files"))
            request.httpMethod = "POST"
            request.setValue(
                "multipart/form-data; boundary=\(boundary)",
                forHTTPHeaderField: "Content-Type"
            )
            return try await self.ejecutarUploadAutenticado(request, fromFile: multipartURL)
        }
    }

    // MARK: - Negocios (`ARCHITECTURE.md` §11 ROADMAP_V2.md §7.4/§7.7 WP-V2-12,
    // `docs/negocios.md` — pantalla 4 del mockup)

    /// `GET /v1/negocios/kpis?mes=YYYY-MM` (default del propio servidor: mes
    /// actual UTC — se omite el query param si `mes` es `nil`).
    public func negociosKPIs(mes: String? = nil) async throws -> NegocioKPIs {
        try await conAutoRefresh {
            try await self.obtenerConQuery("/v1/negocios/kpis", [("mes", mes)])
        }
    }

    /// `GET /v1/negocios/facturas?status=` — más recientes primero. `status`
    /// filtra por uno de `draft|sent|paid|void`; `nil` trae todas.
    public func listarFacturas(status: String? = nil) async throws -> [Factura] {
        try await conAutoRefresh {
            try await self.obtenerConQuery("/v1/negocios/facturas", [("status", status)])
        }
    }

    // MARK: - Estado de capacidades

    /// `GET /v1/credentials` — nunca trae el secreto completo, solo `masked`.
    public func credenciales() async throws -> CredentialsOut {
        try await conAutoRefresh { try await self.obtener("/v1/credentials") }
    }

    // MARK: - Setup / wizard de primer arranque

    /// `GET /v1/setup/status`.
    public func setupStatus() async throws -> SetupStatus {
        try await conAutoRefresh { try await self.obtener("/v1/setup/status") }
    }

    // MARK: - IDE embebido, solo lectura (`ARCHITECTURE.md` §11 ROADMAP_V2.md
    // §7.6/§7.8, `apps/api/edecan_api/routers/ide.py`)

    /// `GET /v1/ide/status` — `{"connected": bool}`; si es `false`, el resto
    /// de rutas de IDE responden `503` (sin companion emparejado/conectado).
    public func ideStatus() async throws -> IDEStatusOut {
        try await conAutoRefresh { try await self.obtener("/v1/ide/status") }
    }

    /// `GET /v1/ide/tree?path=&max_depth=&max_entries=` — árbol recursivo del
    /// sandbox del companion. Sin parámetros, el companion usa su raíz y sus
    /// propios topes por defecto (`MAX_TREE_DEPTH`/`MAX_TREE_ENTRIES`).
    public func ideTree(path: String? = nil, maxDepth: Int? = nil, maxEntries: Int? = nil) async throws -> IDETree {
        try await conAutoRefresh {
            try await self.obtenerConQuery("/v1/ide/tree", [
                ("path", path),
                ("max_depth", maxDepth.map(String.init)),
                ("max_entries", maxEntries.map(String.init)),
            ])
        }
    }

    /// `GET /v1/ide/file?path=` — lee un archivo completo del sandbox.
    public func ideFile(path: String) async throws -> IDEFileOut {
        try await conAutoRefresh { try await self.obtenerConQuery("/v1/ide/file", [("path", path)]) }
    }

    public func ideWrite(path: String, content: String) async throws {
        struct Body: Encodable { let path: String; let content: String }
        try await conAutoRefresh {
            try await self.enviarSinRespuesta(
                "/v1/ide/file", method: "PUT", body: Body(path: path, content: content)
            )
        }
    }

    public func ideRun(command: String) async throws -> IDERunOut {
        struct Body: Encodable { let command: String }
        return try await conAutoRefresh {
            try await self.enviar("/v1/ide/run", method: "POST", body: Body(command: command))
        }
    }

    // MARK: Proyectos autorizados y sesiones durables del IDE

    public func ideWorkspaces() async throws -> [IDEWorkspace] {
        let out: IDEWorkspacesOut = try await conAutoRefresh {
            try await self.obtener("/v1/ide/workspaces")
        }
        return out.workspaces
    }

    public func ideCreateWorkspace(path: String, name: String? = nil) async throws -> IDEWorkspace {
        struct Body: Encodable { let path: String; let name: String? }
        return try await conAutoRefresh {
            try await self.enviar(
                "/v1/ide/workspaces",
                method: "POST",
                body: Body(path: path, name: name)
            )
        }
    }

    public func ideActivateWorkspace(id: String) async throws -> IDEWorkspace {
        return try await conAutoRefresh {
            try await self.enviarSinCuerpoConRespuesta(
                "/v1/ide/workspaces/\(id)/activate",
                method: "POST"
            )
        }
    }

    public func ideTree(
        workspaceId: String,
        path: String? = nil,
        maxDepth: Int? = nil,
        maxEntries: Int? = nil
    ) async throws -> IDETree {
        try await conAutoRefresh {
            try await self.obtenerConQuery(
                "/v1/ide/workspaces/\(workspaceId)/tree",
                [
                    ("path", path),
                    ("max_depth", maxDepth.map(String.init)),
                    ("max_entries", maxEntries.map(String.init)),
                ]
            )
        }
    }

    public func ideFile(workspaceId: String, path: String) async throws -> IDEFileOut {
        try await conAutoRefresh {
            try await self.obtenerConQuery(
                "/v1/ide/workspaces/\(workspaceId)/file",
                [("path", path)]
            )
        }
    }

    public func ideWrite(workspaceId: String, path: String, content: String) async throws {
        struct Body: Encodable { let path: String; let content: String }
        try await conAutoRefresh {
            try await self.enviarSinRespuesta(
                "/v1/ide/workspaces/\(workspaceId)/file",
                method: "PUT",
                body: Body(path: path, content: content)
            )
        }
    }

    public func ideCreateTerminal(
        workspaceId: String,
        argv: [String]? = nil,
        title: String? = nil
    ) async throws -> IDESession {
        struct Body: Encodable {
            let workspaceId: String
            let argv: [String]?
            let title: String?
            enum CodingKeys: String, CodingKey {
                case workspaceId = "workspace_id"
                case argv, title
            }
        }
        return try await conAutoRefresh {
            try await self.enviar(
                "/v1/ide/terminals",
                method: "POST",
                body: Body(workspaceId: workspaceId, argv: argv, title: title)
            )
        }
    }

    public func ideTerminals() async throws -> [IDESession] {
        let out: IDESessionsOut = try await conAutoRefresh {
            try await self.obtener("/v1/ide/terminals")
        }
        return out.sessions
    }

    public func ideReadTerminal(id: String, cursor: Int) async throws -> IDESessionReadOut {
        try await conAutoRefresh {
            try await self.obtenerConQuery(
                "/v1/ide/terminals/\(id)",
                [("cursor", String(max(0, cursor)))]
            )
        }
    }

    public func ideTerminalInput(id: String, data: String) async throws {
        struct Body: Encodable { let data: String }
        try await conAutoRefresh {
            try await self.enviarSinRespuesta(
                "/v1/ide/terminals/\(id)/input",
                method: "POST",
                body: Body(data: data)
            )
        }
    }

    public func ideDeleteTerminal(id: String) async throws {
        try await conAutoRefresh {
            try await self.enviarSinCuerpo("/v1/ide/terminals/\(id)", method: "DELETE")
        }
    }

    public func ideCreateAgent(
        workspaceId: String,
        prompt: String,
        provider: IDEAgentProvider,
        title: String? = nil,
        model: String? = nil,
        conversationId: String? = nil,
        attachments: [IDEAgentAttachment] = []
    ) async throws -> IDESession {
        struct Body: Encodable {
            let workspaceId: String
            let prompt: String
            let provider: IDEAgentProvider
            let title: String?
            let model: String?
            let conversationId: String?
            let attachments: [IDEAgentAttachment]
            enum CodingKeys: String, CodingKey {
                case workspaceId = "workspace_id"
                case conversationId = "conversation_id"
                case prompt, provider, title, model, attachments
            }
        }
        return try await conAutoRefresh {
            try await self.enviar(
                "/v1/ide/agents",
                method: "POST",
                body: Body(
                    workspaceId: workspaceId,
                    prompt: prompt,
                    provider: provider,
                    title: title,
                    model: model,
                    conversationId: conversationId,
                    attachments: attachments
                )
            )
        }
    }

    public func ideAgents() async throws -> [IDESession] {
        let out: IDESessionsOut = try await conAutoRefresh {
            try await self.obtener("/v1/ide/agents")
        }
        return out.sessions
    }

    public func ideReadAgent(id: String, cursor: Int) async throws -> IDESessionReadOut {
        try await conAutoRefresh {
            try await self.obtenerConQuery(
                "/v1/ide/agents/\(id)",
                [("cursor", String(max(0, cursor)))]
            )
        }
    }

    public func ideDeleteAgent(id: String) async throws {
        try await conAutoRefresh {
            try await self.enviarSinCuerpo("/v1/ide/agents/\(id)", method: "DELETE")
        }
    }

    public func ideConfirmAgentMCP(
        sessionId: String,
        callId: String,
        approved: Bool
    ) async throws {
        struct Body: Encodable { let approved: Bool }
        try await conAutoRefresh {
            try await self.enviarSinRespuesta(
                "/v1/ide/agents/\(sessionId)/mcp/\(callId)/confirm",
                method: "POST",
                body: Body(approved: approved)
            )
        }
    }

    // MARK: Git por proyecto

    public func ideGitStatus(workspaceId: String) async throws -> IDEGitStatus {
        try await conAutoRefresh {
            try await self.obtener("/v1/ide/workspaces/\(workspaceId)/git/status")
        }
    }

    public func ideGitDiff(workspaceId: String) async throws -> IDEGitDiff {
        try await conAutoRefresh {
            try await self.obtener("/v1/ide/workspaces/\(workspaceId)/git/diff")
        }
    }

    public func ideGitLog(workspaceId: String) async throws -> IDEGitLog {
        try await conAutoRefresh {
            try await self.obtener("/v1/ide/workspaces/\(workspaceId)/git/log")
        }
    }

    public func ideGitStage(workspaceId: String, paths: [String]) async throws {
        struct Body: Encodable { let paths: [String] }
        try await conAutoRefresh {
            try await self.enviarSinRespuesta(
                "/v1/ide/workspaces/\(workspaceId)/git/stage",
                method: "POST",
                body: Body(paths: paths)
            )
        }
    }

    public func ideGitUnstage(workspaceId: String, paths: [String]) async throws {
        struct Body: Encodable { let paths: [String] }
        try await conAutoRefresh {
            try await self.enviarSinRespuesta(
                "/v1/ide/workspaces/\(workspaceId)/git/unstage",
                method: "POST",
                body: Body(paths: paths)
            )
        }
    }

    public func ideGitCommit(workspaceId: String, message: String) async throws {
        struct Body: Encodable { let message: String }
        try await conAutoRefresh {
            try await self.enviarSinRespuesta(
                "/v1/ide/workspaces/\(workspaceId)/git/commit",
                method: "POST",
                body: Body(message: message)
            )
        }
    }

    public func ideGitCreateBranch(
        workspaceId: String,
        name: String,
        checkout: Bool
    ) async throws {
        struct Body: Encodable { let name: String; let checkout: Bool }
        try await conAutoRefresh {
            try await self.enviarSinRespuesta(
                "/v1/ide/workspaces/\(workspaceId)/git/branch",
                method: "POST",
                body: Body(name: name, checkout: checkout)
            )
        }
    }

    public func ideGitCheckout(
        workspaceId: String,
        name: String,
        create: Bool = false
    ) async throws {
        struct Body: Encodable { let name: String; let create: Bool }
        try await conAutoRefresh {
            try await self.enviarSinRespuesta(
                "/v1/ide/workspaces/\(workspaceId)/git/checkout",
                method: "POST",
                body: Body(name: name, create: create)
            )
        }
    }

    public func ideGitPush(
        workspaceId: String,
        remote: String? = nil,
        branch: String? = nil,
        setUpstream: Bool = false
    ) async throws {
        struct Body: Encodable {
            let remote: String?
            let branch: String?
            let setUpstream: Bool
            enum CodingKeys: String, CodingKey {
                case remote, branch
                case setUpstream = "set_upstream"
            }
        }
        try await conAutoRefresh {
            try await self.enviarSinRespuesta(
                "/v1/ide/workspaces/\(workspaceId)/git/push",
                method: "POST",
                body: Body(remote: remote, branch: branch, setUpstream: setUpstream)
            )
        }
    }

    // MARK: - Voz web (`ARCHITECTURE.md` §10.9, `apps/api/edecan_api/routers/voice.py`)

    /// `POST /v1/voice/transcribe` — sube `audioData` como `multipart/form-data`
    /// (campo `audio`, más `language` opcional como campo de formulario) y
    /// devuelve el texto transcrito. Requiere flag de plan `voice.web` y
    /// cuota de minutos disponible — un plan/tenant sin ninguno de los dos
    /// llega acá como `APIError.servidor(status: 403|429, ...)`.
    public func transcribir(audioData: Data, mimeType: String, language: String? = nil) async throws -> String {
        try await conAutoRefresh {
            let out: TranscribeOut = try await self.subirAudioMultipart(
                audioData: audioData, mimeType: mimeType, language: language
            )
            return out.text
        }
    }

    /// `POST /v1/voice/speak {text, voice_id?}` — devuelve bytes de audio
    /// crudos (`audio/wav` si el tenant no conectó una credencial de voz
    /// propia — cae al `StubTTS`; `audio/mpeg` con un proveedor real).
    public func hablar(texto: String, voiceId: String? = nil, modelId: String? = nil) async throws -> HablarResultado {
        try await conAutoRefresh { try await self.pedirAudio(texto: texto, voiceId: voiceId, modelId: modelId) }
    }

    /// `POST /v1/voice/speak/stream` — el body de audio llega por chunks.
    /// El texto viaja en el POST y el audio se recibe en streaming.
    public func hablarStream(
        texto: String,
        voiceId: String? = nil,
        modelId: String? = nil
    ) async throws -> AsyncThrowingStream<TrozoHablar, Error> {
        try await conAutoRefresh {
            try await self.abrirStreamAudio(texto: texto, voiceId: voiceId, modelId: modelId)
        }
    }

    /// Abre el transporte realtime autenticado; el caller debe llamar
    /// `connect()` y cerrar el cliente cuando termine.
    public func abrirVozRealtime(conversationId: String? = nil) async throws -> RealtimeVoiceClient {
        let token = try await tokenDeAccesoValido()
        let httpURL = try urlCompleta("/v1/voice/realtime")
        guard var components = URLComponents(url: httpURL, resolvingAgainstBaseURL: true),
              components.scheme != nil,
              components.host != nil
        else { throw APIError.urlInvalida }
        components.scheme = components.scheme == "https" ? "wss" : "ws"
        guard let websocketURL = components.url else { throw APIError.urlInvalida }
        return RealtimeVoiceClient(
            url: websocketURL,
            token: token,
            conversationId: conversationId,
            urlSession: urlSession
        )
    }

    /// `GET /v1/phone/calls` — feed real de llamadas del usuario actual.
    /// El servidor aplica tanto el aislamiento por tenant como el flag de
    /// telefonía; un plan sin esa capacidad recibe 403 tipado.
    public func listarLlamadas() async throws -> [PhoneCallOut] {
        try await conAutoRefresh { try await self.obtener("/v1/phone/calls") }
    }

    /// `GET /v1/phone/calls/{id}` — detalle de una llamada CON `events`
    /// crudos (`listarLlamadas()` no los trae). La pantalla de llamada en
    /// vivo (frente 6c, ``EdecanApp/LlamadaEnVivoView``) sondea este endpoint
    /// para ir armando los turnos según se registran y para saber cuándo la
    /// llamada terminó.
    public func obtenerLlamada(id: String) async throws -> PhoneCallOut {
        try await conAutoRefresh { try await self.obtener("/v1/phone/calls/\(id)") }
    }

    /// `POST /v1/phone/calls/{id}/susurro` — encola una instrucción del
    /// dueño para el siguiente turno de una llamada EN CURSO (frente 6a,
    /// paridad con REFERENCIA). A diferencia de REFERENCIA (Pipecat en tiempo real),
    /// Edecán conversa por turnos vía Twilio: el susurro no se incorpora al
    /// instante, entra recién cuando `_phone_reply` arme la siguiente
    /// respuesta — puede tardar hasta un turno completo. El servidor
    /// responde 409 si la llamada no está `in_progress` y 404 si no es de
    /// este tenant+usuario.
    public func susurrarLlamada(id: String, texto: String) async throws -> PhoneCallWhisperOut {
        struct Body: Encodable { let text: String }
        return try await conAutoRefresh {
            try await self.enviar(
                "/v1/phone/calls/\(id)/susurro", method: "POST", body: Body(text: texto)
            )
        }
    }

    // MARK: - Dispositivos (WP-V4-01, contrato en paralelo — ver ``DeviceOut``)

    /// `POST /v1/devices {nombre, plataforma, kind, fingerprint}` — registra
    /// ESTE teléfono como dispositivo emparejado. Devuelve `nil` (nunca
    /// lanza) si el servidor responde `404`: el router de WP-V4-01 puede no
    /// haber aterrizado todavía en el servidor contra el que habla esta app,
    /// y el emparejamiento v1 (sesión = emparejamiento, ``PairingStore``)
    /// sigue funcionando exactamente igual sin esto. Cualquier OTRO error
    /// (red, 401, 500...) sí se propaga — quien llama (`SessionStore`) lo
    /// trata igual de best-effort, nunca bloquea login/registro por esto.
    public func registrarDispositivo(
        nombre: String, plataforma: String, kind: String, fingerprint: String?
    ) async throws -> DeviceOut? {
        struct Body: Encodable { let nombre: String; let plataforma: String; let kind: String; let fingerprint: String? }
        let body = Body(nombre: nombre, plataforma: plataforma, kind: kind, fingerprint: fingerprint)
        do {
            return try await conAutoRefresh {
                try await self.enviar("/v1/devices", method: "POST", body: body)
            }
        } catch APIError.servidor(let status, _) where status == 404 {
            return nil
        }
    }

    /// `POST /v1/devices/{id}/revoke` — mismo criterio de degradación que
    /// ``registrarDispositivo``: un `404` (router todavía no aterrizado) se
    /// ignora en silencio.
    public func revocarDispositivo(id: String) async throws {
        do {
            try await conAutoRefresh {
                try await self.enviarSinCuerpo("/v1/devices/\(id)/revoke", method: "POST")
            }
        } catch APIError.servidor(let status, _) where status == 404 {
            return
        }
    }

    // MARK: - Misiones (`ARCHITECTURE.md` §11/`ROADMAP_V2.md` §7.4/§7.9,
    // `apps/api/edecan_api/routers/missions.py`) — sin SSE (ver
    // ``MissionOut``): ``MisionesViewModel`` hace *polling* sobre estos
    // métodos en vez de abrir un stream.

    /// `GET /v1/missions` — más recientes primero (orden que ya aplica el backend).
    public func listMissions() async throws -> [MissionOut] {
        try await conAutoRefresh { try await self.obtener("/v1/missions") }
    }

    /// `POST /v1/missions {objetivo}` — encola el job `run_mission` y
    /// devuelve la misión recién creada en `status="planning"`.
    @discardableResult
    public func createMission(objetivo: String) async throws -> MissionOut {
        struct Body: Encodable { let objetivo: String }
        return try await conAutoRefresh {
            try await self.enviar("/v1/missions", method: "POST", body: Body(objetivo: objetivo))
        }
    }

    /// `GET /v1/missions/{id}` — la misión + sus pasos (`agent_steps`) en orden.
    public func getMission(id: String) async throws -> MissionDetailOut {
        try await conAutoRefresh { try await self.obtener("/v1/missions/\(id)") }
    }

    /// `POST /v1/missions/{id}/confirm {approved}` — aprueba o rechaza el
    /// paso `waiting_confirmation` pendiente. Si `approve` es `false`, el
    /// servidor CANCELA la misión entera (no solo el paso); si es `true`,
    /// reanuda ejecutando DIRECTO la herramienta aprobada, sin volver a
    /// llamar al LLM (`missions.py::confirm_mission`).
    @discardableResult
    public func confirmMission(id: String, approve: Bool) async throws -> MissionOut {
        struct Body: Encodable { let approved: Bool }
        return try await conAutoRefresh {
            try await self.enviar("/v1/missions/\(id)/confirm", method: "POST", body: Body(approved: approve))
        }
    }

    /// `POST /v1/missions/{id}/cancel` — sin cuerpo de petición. `409` si la
    /// misión ya está en un status terminal (`done`/`error`/`cancelled`,
    /// ``MissionOut/esTerminal``).
    @discardableResult
    public func cancelMission(id: String) async throws -> MissionOut {
        try await conAutoRefresh {
            try await self.enviarSinCuerpoConRespuesta("/v1/missions/\(id)/cancel", method: "POST")
        }
    }

    @discardableResult
    public func steerMission(id: String, instruction: String) async throws -> MissionOut {
        struct Body: Encodable { let instruction: String }
        return try await conAutoRefresh {
            try await self.enviar("/v1/missions/\(id)/steer", method: "POST", body: Body(instruction: instruction))
        }
    }

    public func listWorkers() async throws -> [PersistentWorker] {
        try await conAutoRefresh { try await self.obtener("/v1/agents/workers") }
    }

    @discardableResult
    public func createWorker(name: String, purpose: String) async throws -> PersistentWorker {
        struct Body: Encodable { let name: String; let purpose: String }
        return try await conAutoRefresh {
            try await self.enviar("/v1/agents/workers", method: "POST", body: Body(name: name, purpose: purpose))
        }
    }

    public func listWorkerHandoffs() async throws -> [WorkerHandoff] {
        try await conAutoRefresh { try await self.obtener("/v1/agents/workers/handoffs") }
    }

    public func approveWorkerHandoff(id: String) async throws {
        try await conAutoRefresh {
            try await self.enviarSinCuerpo("/v1/agents/workers/handoffs/\(id)/approve", method: "POST")
        }
    }

    @discardableResult
    public func enqueueWorkerTask(workerId: String, instruction: String) async throws -> WorkerTaskQueued {
        struct Body: Encodable { let instruction: String }
        return try await conAutoRefresh {
            try await self.enviar(
                "/v1/agents/workers/\(workerId)/tasks",
                method: "POST",
                body: Body(instruction: instruction)
            )
        }
    }

    public struct UndoActionOut: Codable, Sendable {
        public let toolName: String
        enum CodingKeys: String, CodingKey { case toolName = "tool_name" }
    }

    @discardableResult
    public func undoLastAction() async throws -> UndoActionOut {
        try await conAutoRefresh {
            try await self.enviarSinCuerpoConRespuesta("/v1/me/actions/undo", method: "POST")
        }
    }

    // MARK: - Automatizaciones (`ARCHITECTURE.md` §11/`ROADMAP_V2.md`
    // §7.4/§7.6/§7.10, `apps/api/edecan_api/routers/automations.py`)

    /// `GET /v1/automations` — más recientes primero.
    public func listAutomations() async throws -> [AutomationOut] {
        try await conAutoRefresh { try await self.obtener("/v1/automations") }
    }

    /// `PATCH /v1/automations/{id} {enabled}` — activa/desactiva sin tocar
    /// el resto de la regla. `403` si se intenta activar por encima del
    /// límite de automatizaciones activas del plan (`automations.py::_check_limit`).
    @discardableResult
    public func toggleAutomation(id: String, enabled: Bool) async throws -> AutomationOut {
        struct Body: Encodable { let enabled: Bool }
        return try await conAutoRefresh {
            try await self.enviar("/v1/automations/\(id)", method: "PATCH", body: Body(enabled: enabled))
        }
    }

    /// `POST /v1/automations` — "alta simple": SIEMPRE crea un disparador
    /// `kind="schedule"` (agenda por `rrule` RFC 5545) con una acción
    /// `kind="agent_instruction"` (la única que reconoce el backend hoy,
    /// `edecan_automations.engine.validate_accion` — el servidor la fuerza
    /// igual aunque no se mande, `automations.py::_normalize_accion_in`).
    /// Crear un disparador `webhook` desde el teléfono queda fuera de
    /// alcance de este cliente — sigue siendo terreno del panel web.
    @discardableResult
    public func createAutomation(
        nombre: String, descripcion: String, rrule: String, instruccion: String, agente: String? = nil
    ) async throws -> AutomationOut {
        struct TriggerBody: Encodable { let kind: String; let rrule: String }
        struct AccionBody: Encodable { let instruccion: String; let agente: String? }
        struct Body: Encodable {
            let nombre: String
            let descripcion: String
            let trigger: TriggerBody
            let accion: AccionBody
            let enabled: Bool
        }
        let body = Body(
            nombre: nombre,
            descripcion: descripcion,
            trigger: TriggerBody(kind: "schedule", rrule: rrule),
            accion: AccionBody(instruccion: instruccion, agente: agente),
            enabled: true
        )
        return try await conAutoRefresh {
            try await self.enviar("/v1/automations", method: "POST", body: body)
        }
    }

    /// `GET /v1/automations/{id}/runs` — hasta 50 corridas, más recientes primero.
    public func listAutomationRuns(id: String) async throws -> [AutomationRunOut] {
        try await conAutoRefresh { try await self.obtener("/v1/automations/\(id)/runs") }
    }

    // MARK: - Recordatorios (`ARCHITECTURE.md` §10.3/§10.12,
    // `apps/api/edecan_api/routers/reminders.py`)

    /// `GET /v1/reminders` — ordenados por `due_at` ascendente (orden que ya
    /// aplica `Repo.list_reminders`).
    public func listReminders() async throws -> [ReminderOut] {
        try await conAutoRefresh { try await self.obtener("/v1/reminders") }
    }

    /// `POST /v1/reminders {message, due_at, channel}`. La app usa `mobile`
    /// para que el worker intente APNs y conserva una notificación local como
    /// respaldo cuando el despliegue OSS no tiene credenciales push.
    @discardableResult
    public func createReminder(texto: String, fecha: Date, canal: String = "mobile") async throws -> ReminderOut {
        struct Body: Encodable {
            let message: String
            let dueAt: Date
            let channel: String
            enum CodingKeys: String, CodingKey {
                case message
                case dueAt = "due_at"
                case channel
            }
        }
        let body = Body(message: texto, dueAt: fecha, channel: canal)
        return try await conAutoRefresh {
            try await self.enviar("/v1/reminders", method: "POST", body: body)
        }
    }

    /// `PUT /v1/reminders/{id} {status: "sent"}` — el backend no tiene un
    /// status "completado" propio (solo `pending|sent|cancelled`,
    /// `ARCHITECTURE.md` §10.3); esta app reutiliza `"sent"` (el mismo que
    /// pone `send_reminder_scan` cuando el recordatorio vence solo) para "lo
    /// marqué como hecho a mano" desde el swipe de ``RecordatoriosView`` —
    /// ver ``ReminderOut/completado``.
    @discardableResult
    public func completeReminder(id: String) async throws -> ReminderOut {
        struct Body: Encodable { let status: String }
        return try await conAutoRefresh {
            try await self.enviar("/v1/reminders/\(id)", method: "PUT", body: Body(status: "sent"))
        }
    }

    // MARK: - Control remoto (WP-V6-08, `ARCHITECTURE.md` §13.c/§14,
    // `apps/api/edecan_api/routers/remote.py`, `docs/control-remoto.md`) —
    // vista (`kind="view"`) y control (`kind="control"`, input real de
    // teclado/mouse) del Mac/PC companion. Sigue siendo *polling* HTTP
    // (nunca WebRTC, ver §1.1 de ese documento): sin SSE ni socket propio en
    // este cliente, cada frame/comando es una petición suelta.

    /// `POST /v1/remote/sessions {consent: true, kind}`. `consent` SIEMPRE
    /// `true` — el `422` que el backend devolvería si no lo fuera no aplica
    /// aquí: este método solo se llama después de que ``RemotoView`` ya
    /// mostró el diálogo de consentimiento explícito y el usuario lo aceptó,
    /// nunca antes. `kind` default `"view"`; pasa `"control"` para además
    /// poder mandar input de teclado/mouse más adelante (exige el flag de
    /// plan `companion.remote_input` — `403` con mensaje claro si el plan no
    /// lo trae, ver ``APIError/servidor(status:mensaje:)``).
    @discardableResult
    public func createRemoteSession(kind: String = "view") async throws -> RemoteSession {
        struct Body: Encodable { let consent: Bool; let kind: String }
        return try await conAutoRefresh {
            try await self.enviar(
                "/v1/remote/sessions", method: "POST", body: Body(consent: true, kind: kind)
            )
        }
    }

    /// `GET /v1/remote/sessions` — todas las sesiones del tenant (cualquier
    /// estado), más recientes primero (orden que ya aplica el backend).
    public func listRemoteSessions() async throws -> [RemoteSession] {
        try await conAutoRefresh { try await self.obtener("/v1/remote/sessions") }
    }

    /// `GET /v1/remote/sessions/{id}`.
    public func getRemoteSession(id: String) async throws -> RemoteSession {
        try await conAutoRefresh { try await self.obtener("/v1/remote/sessions/\(id)") }
    }

    /// `GET /v1/remote/sessions/{id}/frame` — pide un frame nuevo al
    /// companion; en la primera llamada de una sesión, esto es lo que
    /// dispara la aprobación LOCAL en el companion y puede tardar hasta ~30s
    /// (`docs/control-remoto.md`). Puede lanzar `APIError.servidor` con
    /// `status`: `429` (pediste uno antes de `REMOTE_FRAME_MIN_INTERVAL_SECONDS`,
    /// 0.25s — el *polling* de ``RemotoViewModel`` usa 0.35s, ya lo respeta),
    /// `403` (el usuario denegó la sesión en su companion), `409` (la sesión
    /// ya `ended`), `501` (companion sin soporte de captura o con el IDE
    /// deshabilitado), `502`/`503` (companion sin responder a tiempo o
    /// desconectado) — todos con un `mensaje` en español ya listo para
    /// mostrar (`routers/remote.py`, ver sus traducciones exactas).
    public func getRemoteFrame(sessionId: String) async throws -> RemoteFrame {
        try await conAutoRefresh { try await self.obtener("/v1/remote/sessions/\(sessionId)/frame") }
    }

    /// `POST /v1/remote/sessions/{id}/end` — idempotente, siempre `200`
    /// incluso si la sesión ya estaba `ended`.
    @discardableResult
    public func endRemoteSession(id: String) async throws -> RemoteSession {
        try await conAutoRefresh {
            try await self.enviarSinCuerpoConRespuesta("/v1/remote/sessions/\(id)/end", method: "POST")
        }
    }

    /// `POST /v1/remote/sessions/{id}/input` — solo sesiones `kind="control"`
    /// ya `active`. Puede lanzar `APIError.servidor` con `status`: `403` (la
    /// sesión no es de control, o el usuario denegó ESTE comando en su
    /// companion — deniega la SESIÓN completa, no solo el comando), `409`
    /// (todavía no `active`, o ya `ended`), `429` (rate limit propio, más
    /// laxo que el de frames), `501` (companion sin soporte/deshabilitado, o
    /// plataforma sin soporte), `502`/`503` (companion sin responder) — ver
    /// el docstring de `routers/remote.py::send_input` para el detalle
    /// completo de cada caso.
    @discardableResult
    public func sendRemoteInput(sessionId: String, input: RemoteInput) async throws -> RemoteInputResult {
        try await conAutoRefresh {
            try await self.enviar("/v1/remote/sessions/\(sessionId)/input", method: "POST", body: input)
        }
    }

    // Portapapeles y transferencia de archivos compartidos (WP-V7). Mismos
    // candados que el input (`kind="control"` ya `active`): mismos `status`
    // de error (403/409/429/502/503) con `mensaje` en español listo para
    // mostrar (`routers/remote.py`).

    /// `GET /v1/remote/sessions/{id}/clipboard` — trae el portapapeles (texto)
    /// de la computadora al teléfono.
    public func getRemoteClipboard(sessionId: String) async throws -> String {
        let respuesta: RemoteClipboard = try await conAutoRefresh {
            try await self.obtener("/v1/remote/sessions/\(sessionId)/clipboard")
        }
        return respuesta.text
    }

    /// `POST /v1/remote/sessions/{id}/clipboard {text}` — escribe el
    /// portapapeles de la computadora con el texto del teléfono.
    public func setRemoteClipboard(sessionId: String, text: String) async throws {
        struct Body: Encodable { let text: String }
        try await conAutoRefresh {
            try await self.enviarSinRespuesta(
                "/v1/remote/sessions/\(sessionId)/clipboard", method: "POST", body: Body(text: text)
            )
        }
    }

    /// `GET /v1/remote/sessions/{id}/files` — lista la carpeta compartida de
    /// la computadora (para elegir qué traer).
    public func listRemoteFiles(sessionId: String) async throws -> RemoteSharedFiles {
        try await conAutoRefresh {
            try await self.obtener("/v1/remote/sessions/\(sessionId)/files")
        }
    }

    /// `POST /v1/remote/sessions/{id}/files {name, content_b64}` — teléfono →
    /// computadora. Devuelve el nombre FINAL con el que quedó guardado.
    @discardableResult
    public func pushRemoteFile(
        sessionId: String, name: String, contentB64: String
    ) async throws -> RemotePushedFile {
        struct Body: Encodable {
            let name: String
            let content_b64: String
        }
        return try await conAutoRefresh {
            try await self.enviar(
                "/v1/remote/sessions/\(sessionId)/files", method: "POST",
                body: Body(name: name, content_b64: contentB64)
            )
        }
    }

    /// `GET /v1/remote/sessions/{id}/files/content?name=…` — computadora →
    /// teléfono. Devuelve el contenido en base64 más nombre y tipo MIME.
    public func pullRemoteFile(sessionId: String, name: String) async throws -> RemotePulledFile {
        try await conAutoRefresh {
            try await self.obtenerConQuery(
                "/v1/remote/sessions/\(sessionId)/files/content", [("name", name)]
            )
        }
    }

    // MARK: - Capacidades (Skills + MCP)

    public func listSkills() async throws -> [SkillSummary] {
        let envelope: SkillsEnvelope = try await conAutoRefresh {
            try await self.obtener("/v1/skills")
        }
        return envelope.skills
    }

    public func listMCPServers() async throws -> [MCPServerSummary] {
        try await conAutoRefresh { try await self.obtener("/v1/mcp/servers") }
    }

    // MARK: - Gimnasio (`/v1/gym`, contrato en paralelo — ver ``GymModels``)

    /// `POST /v1/gym/checkin {respuesta: "si"|"no"}` — el dueño responde si
    /// quiere entrenar hoy; el backend devuelve `ok` y, según el caso, el
    /// `plan`/`session` resultantes (ambos pueden venir en `null`).
    public func gymCheckin(respuesta: String) async throws -> GymCheckinOut {
        try await conAutoRefresh {
            try await self.enviar(
                "/v1/gym/checkin", method: "POST", body: GymCheckinIn(answer: respuesta)
            )
        }
    }

    /// `GET /v1/gym/plan/today` — el plan de hoy, o `nil` si el servidor
    /// todavía no expone esta ruta (`404`). Degrada con gracia, igual que
    /// ``registrarDispositivo``; cualquier otro error se propaga tal cual.
    public func gymPlanDeHoy() async throws -> GymPlan? {
        struct PlanDeHoyOut: Decodable, Sendable { let plan: GymPlan? }
        do {
            let out: PlanDeHoyOut = try await conAutoRefresh {
                try await self.obtener("/v1/gym/plan/today")
            }
            return out.plan
        } catch APIError.servidor(let status, _) where status == 404 {
            return nil
        }
    }

    /// `GET /v1/gym/session` — solo la sesión activa; `nil` si no hay (o si
    /// el router todavía no aterrizó: `404`).
    public func gymSesionActual() async throws -> GymSession? {
        struct SesionOut: Decodable, Sendable { let session: GymSession? }
        do {
            let out: SesionOut = try await conAutoRefresh {
                try await self.obtener("/v1/gym/session")
            }
            return out.session
        } catch APIError.servidor(let status, _) where status == 404 {
            return nil
        }
    }

    /// `POST /v1/gym/sessions/{id}/sets` — registra una serie hecha.
    public func gymRegistrarSerie(
        sessionId: String, ejercicioIdx: Int, repeticiones: Int, pesoKg: Double?
    ) async throws -> GymSetLogOut {
        try await conAutoRefresh {
            try await self.enviar(
                "/v1/gym/sessions/\(sessionId)/sets",
                method: "POST",
                body: GymSetLogIn(exerciseIndex: ejercicioIdx, repetitions: repeticiones, weightKg: pesoKg)
            )
        }
    }

    /// `POST /v1/gym/sessions/{id}/complete` — termina la sesión activa.
    public func gymTerminarSesion(sessionId: String) async throws -> GymSetLogOut {
        try await conAutoRefresh {
            try await self.enviarSinCuerpoConRespuesta(
                "/v1/gym/sessions/\(sessionId)/complete", method: "POST"
            )
        }
    }

    /// `POST /v1/gym/sessions/{id}/pause` — pausa la sesión activa.
    public func gymPausarSesion(sessionId: String) async throws -> GymSession {
        struct SesionOut: Decodable, Sendable { let session: GymSession }
        let out: SesionOut = try await conAutoRefresh {
            try await self.enviarSinCuerpoConRespuesta(
                "/v1/gym/sessions/\(sessionId)/pause", method: "POST"
            )
        }
        return out.session
    }

    /// `POST /v1/gym/sessions/{id}/resume` — reanuda la sesión pausada.
    public func gymReanudarSesion(sessionId: String) async throws -> GymSession {
        struct SesionOut: Decodable, Sendable { let session: GymSession }
        let out: SesionOut = try await conAutoRefresh {
            try await self.enviarSinCuerpoConRespuesta(
                "/v1/gym/sessions/\(sessionId)/resume", method: "POST"
            )
        }
        return out.session
    }

    /// `POST /v1/gym/sessions/{id}/start` — arranca el cronómetro de una
    /// sesión `planned` (fija `started_at` y pasa a `active`).
    public func gymIniciarSesion(sessionId: String) async throws -> GymSession {
        struct SesionOut: Decodable, Sendable { let session: GymSession }
        let out: SesionOut = try await conAutoRefresh {
            try await self.enviarSinCuerpoConRespuesta(
                "/v1/gym/sessions/\(sessionId)/start", method: "POST"
            )
        }
        return out.session
    }

    /// `GET /v1/gym/history?limit=30` — historial de sesiones terminadas.
    public func gymHistorial(limit: Int = 30) async throws -> [GymSession] {
        struct HistorialOut: Decodable, Sendable { let sessions: [GymSession] }
        let out: HistorialOut = try await conAutoRefresh {
            try await self.obtenerConQuery("/v1/gym/history", [("limit", String(limit))])
        }
        return out.sessions
    }

    // MARK: - Internals: HTTP

    private func totpCodeOVacio(_ code: String?) -> String? {
        guard let code, !code.trimmingCharacters(in: .whitespaces).isEmpty else { return nil }
        return code
    }

    private func comenzarNuevaSesion() -> UInt64 {
        sessionEpoch &+= 1
        refreshTask?.task.cancel()
        refreshTask = nil
        invalidarSesionLocal()
        return sessionEpoch
    }

    private func guardarTokens(_ tokens: TokenPair, expectedEpoch: UInt64) throws {
        guard sessionEpoch == expectedEpoch else { throw APIError.sesionExpirada }
        tokenStore.save(accessToken: tokens.accessToken, refreshToken: tokens.refreshToken)
        accessToken = tokens.accessToken
        refreshToken = tokens.refreshToken
    }

    private func expirarSesionSiVigente(_ expectedEpoch: UInt64) async {
        guard sessionEpoch == expectedEpoch else { return }
        sessionEpoch &+= 1
        refreshTask?.task.cancel()
        refreshTask = nil
        invalidarSesionLocal()
        // La expiración del JWT no equivale a olvidar el emparejamiento del
        // iPhone. El secreto durable es precisamente la vía de recuperación
        // que evita pedir el QR otra vez; solo `cerrarSesion`/`olvidarEmparejamiento`
        // deben borrarlo de forma explícita. Si el servidor también rechazó
        // el secreto durable, conservarlo permite mostrar recuperación manual
        // con la URL guardada en vez de convertir una sesión vencida en un
        // nuevo emparejamiento obligatorio.
        await onSessionExpired?()
    }

    /// Ejecuta `operacion`; si falla porque el access token expiró,
    /// refresca UNA vez y reintenta. Si el refresh también falla, propaga
    /// el error del refresh (normalmente `.sesionExpirada`).
    private func conAutoRefresh<T: Sendable>(_ operacion: @Sendable () async throws -> T) async throws -> T {
        let epoch = sessionEpoch
        let tokenQueFallo = accessToken
        do {
            let resultado = try await operacion()
            guard sessionEpoch == epoch else { throw APIError.sesionExpirada }
            return resultado
        } catch APIError.sesionExpirada {
            guard sessionEpoch == epoch else { throw APIError.sesionExpirada }
            // Otra petición puede haber renovado mientras esta esperaba el
            // 401. Solo quien aún vea el token fallido consume el refresh
            // token one-time; las demás reutilizan el par recién guardado.
            if accessToken == tokenQueFallo {
                try await refrescar()
            }
            guard sessionEpoch == epoch else { throw APIError.sesionExpirada }
            let resultado = try await operacion()
            guard sessionEpoch == epoch else { throw APIError.sesionExpirada }
            return resultado
        }
    }

    private func obtener<Respuesta: Decodable>(_ path: String) async throws -> Respuesta {
        var request = URLRequest(url: try urlCompleta(path))
        request.httpMethod = "GET"
        return try await ejecutarAutenticado(request)
    }

    /// Igual que ``obtener(_:)`` pero con query params — cada par de `items`
    /// se omite si el valor es `nil`/vacío (mismo criterio que usan los
    /// endpoints opcionales del backend, p. ej. `mes`/`status`/`path`).
    private func obtenerConQuery<Respuesta: Decodable>(
        _ path: String, _ items: [(String, String?)]
    ) async throws -> Respuesta {
        var request = URLRequest(url: try urlConQuery(path, items))
        request.httpMethod = "GET"
        return try await ejecutarAutenticado(request)
    }

    private func enviar<Cuerpo: Encodable, Respuesta: Decodable>(
        _ path: String, method: String, body: Cuerpo
    ) async throws -> Respuesta {
        var request = URLRequest(url: try urlCompleta(path))
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try encoder.encode(body)
        return try await ejecutarAutenticado(request)
    }

    /// Igual que ``enviar(_:method:body:)`` pero para endpoints `204 No
    /// Content` — no hay nada que decodificar, solo confirmar que el status
    /// fue exitoso (o lanzar el error tal cual si no).
    private func enviarSinRespuesta<Cuerpo: Encodable>(_ path: String, method: String, body: Cuerpo) async throws {
        var request = URLRequest(url: try urlCompleta(path))
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try encoder.encode(body)
        _ = try await ejecutarAutenticadoData(request)
    }

    /// POST/DELETE sin cuerpo y sin respuesta que decodificar (p. ej.
    /// `DELETE /v1/credentials/llm`, `POST /v1/devices/{id}/revoke`).
    private func enviarSinCuerpo(_ path: String, method: String) async throws {
        var request = URLRequest(url: try urlCompleta(path))
        request.httpMethod = method
        _ = try await ejecutarAutenticadoData(request)
    }

    /// Igual que ``enviarSinCuerpo(_:method:)`` pero decodificando la
    /// respuesta JSON (p. ej. `POST /v1/missions/{id}/cancel`, que no lleva
    /// cuerpo de petición pero sí devuelve la misión actualizada).
    private func enviarSinCuerpoConRespuesta<Respuesta: Decodable>(_ path: String, method: String) async throws -> Respuesta {
        var request = URLRequest(url: try urlCompleta(path))
        request.httpMethod = method
        let (data, _) = try await ejecutarAutenticadoData(request)
        return try decodificar(Respuesta.self, data)
    }

    /// Variante para `/login` y `/refresh`: no llevan `Authorization`, y un
    /// 401 significa credenciales inválidas, no sesión expirada.
    private func peticionSinSesion<Cuerpo: Encodable, Respuesta: Decodable>(
        path: String, body: Cuerpo
    ) async throws -> Respuesta {
        var request = URLRequest(url: try urlCompleta(path))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try encoder.encode(body)
        let (data, response) = try await realizar(request)
        let http = try httpResponse(response)
        if http.statusCode == 401 {
            throw APIError.credencialesInvalidas
        }
        try validarStatus(http, data: data)
        return try decodificar(Respuesta.self, data)
    }

    /// Agrega `Authorization`, ejecuta la petición y valida el status —
    /// devuelve el `Data` crudo de la respuesta sin decodificar nada, para
    /// que tanto ``ejecutarAutenticado(_:)`` (JSON) como los endpoints que
    /// devuelven bytes crudos (``pedirAudio(texto:voiceId:)``) o `204 No
    /// Content` (``enviarSinRespuesta(_:method:body:)``) compartan la MISMA
    /// lógica de auto-refresh/401/status — nunca duplicada.
    private func ejecutarAutenticadoData(_ request: URLRequest) async throws -> (data: Data, response: HTTPURLResponse) {
        guard let accessToken else { throw APIError.sesionExpirada }
        var request = request
        request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
        if Self.esRutaIDEAvanzada(request.url?.path) {
            guard let deviceId = devicePairingStore.deviceId(),
                  let deviceToken = devicePairingStore.deviceToken()
            else {
                throw APIError.servidor(
                    status: 403,
                    mensaje: "Vuelve a conectar este iPhone con el QR de Edecán."
                )
            }
            request.setValue(deviceId, forHTTPHeaderField: "X-Edecan-Device-Id")
            request.setValue(deviceToken, forHTTPHeaderField: "X-Edecan-Device-Token")
        }
        let (data, response) = try await realizar(request)
        let http = try httpResponse(response)
        if http.statusCode == 401 {
            throw APIError.sesionExpirada
        }
        try validarStatus(http, data: data)
        return (data, http)
    }

    /// Las credenciales durables del dispositivo solo salen hacia las rutas
    /// que pueden iniciar procesos o mutar un workspace. El IDE legacy y el
    /// resto de la API reciben únicamente el Bearer normal.
    private static func esRutaIDEAvanzada(_ path: String?) -> Bool {
        guard let path else { return false }
        return path == "/v1/ide/workspaces"
            || path.hasPrefix("/v1/ide/workspaces/")
            || path == "/v1/ide/terminals"
            || path.hasPrefix("/v1/ide/terminals/")
            || path == "/v1/ide/agents"
            || path.hasPrefix("/v1/ide/agents/")
    }

    private func ejecutarAutenticado<Respuesta: Decodable>(_ request: URLRequest) async throws -> Respuesta {
        let (data, _) = try await ejecutarAutenticadoData(request)
        return try decodificar(Respuesta.self, data)
    }

    private func ejecutarUploadAutenticado<Respuesta: Decodable>(
        _ request: URLRequest, fromFile fileURL: URL
    ) async throws -> Respuesta {
        guard let accessToken else { throw APIError.sesionExpirada }
        var request = request
        request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await urlSession.upload(for: request, fromFile: fileURL)
        } catch is CancellationError {
            throw CancellationError()
        } catch let error as URLError where error.code == .cancelled {
            throw CancellationError()
        } catch {
            throw APIError.sinConexion(detalle: error.localizedDescription)
        }
        let http = try httpResponse(response)
        if http.statusCode == 401 { throw APIError.sesionExpirada }
        try validarStatus(http, data: data)
        return try decodificar(Respuesta.self, data)
    }

    /// Query params codificados correctamente (`URLComponents`, no
    /// concatenación a mano) sobre la URL que ya arma ``urlCompleta(_:)``.
    /// `items` con valor `nil`/vacío se omiten.
    private func urlConQuery(_ path: String, _ items: [(String, String?)]) throws -> URL {
        let base = try urlCompleta(path)
        guard var components = URLComponents(url: base, resolvingAgainstBaseURL: true) else {
            throw APIError.urlInvalida
        }
        let queryItems = items.compactMap { clave, valor -> URLQueryItem? in
            guard let valor, !valor.isEmpty else { return nil }
            return URLQueryItem(name: clave, value: valor)
        }
        if !queryItems.isEmpty {
            components.queryItems = queryItems
        }
        guard let url = components.url else { throw APIError.urlInvalida }
        return url
    }

    // MARK: - Internals: multipart (voz)

    /// Materializa solo el envoltorio multipart en un archivo temporal y
    /// copia el documento por chunks. Asi `URLSession.upload(fromFile:)`
    /// puede transmitir archivos grandes sin crear otro `Data` gigante.
    private static func crearArchivoMultipart(
        sourceURL: URL,
        filename: String,
        mimeType: String,
        boundary: String
    ) throws -> URL {
        let safeName = nombreMultipartSeguro(filename)
        let safeMime = mimeType.unicodeScalars.allSatisfy {
            $0.value >= 32 && $0.value != 127 && $0 != "\r" && $0 != "\n"
        } ? mimeType : "application/octet-stream"
        let destination = FileManager.default.temporaryDirectory
            .appendingPathComponent("edecan-upload-\(UUID().uuidString)")
            .appendingPathExtension("multipart")
        guard FileManager.default.createFile(atPath: destination.path, contents: nil) else {
            throw APIError.respuestaInvalida
        }

        do {
            let input = try FileHandle(forReadingFrom: sourceURL)
            let output = try FileHandle(forWritingTo: destination)
            defer {
                try? input.close()
                try? output.close()
            }
            let header = "--\(boundary)\r\n"
                + "Content-Disposition: form-data; name=\"file\"; filename=\"\(safeName)\"\r\n"
                + "Content-Type: \(safeMime)\r\n\r\n"
            try output.write(contentsOf: Data(header.utf8))
            while let chunk = try input.read(upToCount: 64 * 1024), !chunk.isEmpty {
                try output.write(contentsOf: chunk)
            }
            try output.write(contentsOf: Data("\r\n--\(boundary)--\r\n".utf8))
            return destination
        } catch {
            try? FileManager.default.removeItem(at: destination)
            throw error
        }
    }

    private static func nombreMultipartSeguro(_ raw: String) -> String {
        let last = raw.replacingOccurrences(of: "\\", with: "/")
            .split(separator: "/").last.map(String.init) ?? "archivo"
        let clean = last.unicodeScalars.filter {
            $0.value >= 32 && $0.value != 127 && $0 != "\"" && $0 != "\\"
        }
        let value = String(String.UnicodeScalarView(clean)).trimmingCharacters(in: .whitespacesAndNewlines)
        return value.isEmpty ? "archivo" : String(value.prefix(180))
    }

    /// `POST /v1/voice/transcribe` — `multipart/form-data` a mano (sin
    /// dependencias externas): un campo de archivo `audio` + un campo de
    /// texto `language` opcional, exactamente el contrato de
    /// `apps/api/edecan_api/routers/voice.py::transcribe`
    /// (`UploadFile` campo `audio` + `Form` campo `language`).
    private func subirAudioMultipart<Respuesta: Decodable>(
        audioData: Data, mimeType: String, language: String?
    ) async throws -> Respuesta {
        let boundary = "Edecan-\(UUID().uuidString)"
        var body = Data()
        body.appendCampoDeArchivo(nombre: "audio", filename: "voz.wav", mimeType: mimeType, contenido: audioData, boundary: boundary)
        if let language, !language.trimmingCharacters(in: .whitespaces).isEmpty {
            body.appendCampoDeTexto(nombre: "language", valor: language, boundary: boundary)
        }
        body.append("--\(boundary)--\r\n")

        var request = URLRequest(url: try urlCompleta("/v1/voice/transcribe"))
        request.httpMethod = "POST"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        request.httpBody = body
        return try await ejecutarAutenticado(request)
    }

    /// `POST /v1/voice/speak` — a diferencia del resto de este cliente, la
    /// respuesta exitosa NO es JSON (es audio binario), así que no puede
    /// pasar por ``ejecutarAutenticado(_:)``/``decodificar(_:_:)``: usa
    /// ``ejecutarAutenticadoData(_:)`` directo y arma ``HablarResultado`` con
    /// los bytes crudos + el `Content-Type` real de la respuesta.
    private func pedirAudio(texto: String, voiceId: String?, modelId: String?) async throws -> HablarResultado {
        struct Body: Encodable {
            let text: String
            let voiceId: String?
            let modelId: String?
            enum CodingKeys: String, CodingKey {
                case text
                case voiceId = "voice_id"
                case modelId = "model_id"
            }
        }
        var request = URLRequest(url: try urlCompleta("/v1/voice/speak"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try encoder.encode(Body(text: texto, voiceId: voiceId, modelId: modelId))
        let (data, http) = try await ejecutarAutenticadoData(request)
        let contentType = http.value(forHTTPHeaderField: "Content-Type") ?? "audio/mpeg"
        return HablarResultado(audio: data, contentType: contentType)
    }

    private func abrirStreamAudio(
        texto: String, voiceId: String?, modelId: String?
    ) async throws -> AsyncThrowingStream<TrozoHablar, Error> {
        struct Body: Encodable {
            let text: String
            let voiceId: String?
            let modelId: String?
            enum CodingKeys: String, CodingKey {
                case text
                case voiceId = "voice_id"
                case modelId = "model_id"
            }
        }
        guard let accessToken else { throw APIError.sesionExpirada }
        var request = URLRequest(url: try urlCompleta("/v1/voice/speak/stream"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
        request.httpBody = try encoder.encode(Body(text: texto, voiceId: voiceId, modelId: modelId))

        let (bytes, response): (URLSession.AsyncBytes, URLResponse)
        do {
            (bytes, response) = try await urlSession.bytes(for: request)
        } catch is CancellationError {
            throw CancellationError()
        } catch let error as URLError where error.code == .cancelled {
            throw CancellationError()
        } catch {
            throw APIError.sinConexion(detalle: error.localizedDescription)
        }
        let http = try httpResponse(response)
        if http.statusCode == 401 {
            throw APIError.sesionExpirada
        }
        if !(200..<300).contains(http.statusCode) {
            var errorBody = Data()
            for try await byte in bytes {
                errorBody.append(byte)
                if errorBody.count > 4_096 { break }
            }
            throw APIError.servidor(status: http.statusCode, mensaje: Self.extraerMensajeDeError(errorBody))
        }
        let mime = http.value(forHTTPHeaderField: "Content-Type")?.split(separator: ";").first.map(String.init)
            ?? "audio/mpeg"
        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    var buffer = Data()
                    buffer.reserveCapacity(4_096)
                    for try await byte in bytes {
                        buffer.append(byte)
                        if buffer.count >= 4_096 {
                            continuation.yield(TrozoHablar(chunk: buffer, mime: mime))
                            buffer.removeAll(keepingCapacity: true)
                        }
                    }
                    if !buffer.isEmpty {
                        continuation.yield(TrozoHablar(chunk: buffer, mime: mime))
                    }
                    continuation.finish()
                } catch is CancellationError {
                    continuation.finish(throwing: CancellationError())
                } catch {
                    continuation.finish(throwing: APIError.sinConexion(detalle: error.localizedDescription))
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    private func realizar(_ request: URLRequest) async throws -> (Data, URLResponse) {
        do {
            return try await urlSession.data(for: request)
        } catch is CancellationError {
            // SwiftUI cancela las tareas de una pantalla cuando cambia su
            // identidad o deja de estar visible. Eso no es una caída de red
            // y nunca debe terminar convertido en un aviso rojo al usuario.
            throw CancellationError()
        } catch let error as URLError where error.code == .cancelled {
            throw CancellationError()
        } catch let error as APIError {
            throw error
        } catch {
            throw APIError.sinConexion(detalle: error.localizedDescription)
        }
    }

    private func httpResponse(_ response: URLResponse) throws -> HTTPURLResponse {
        guard let http = response as? HTTPURLResponse else { throw APIError.respuestaInvalida }
        return http
    }

    private func validarStatus(_ http: HTTPURLResponse, data: Data) throws {
        guard (200..<300).contains(http.statusCode) else {
            throw APIError.servidor(status: http.statusCode, mensaje: Self.extraerMensajeDeError(data))
        }
    }

    private func decodificar<T: Decodable>(_ type: T.Type, _ data: Data) throws -> T {
        do {
            return try decoder.decode(T.self, from: data)
        } catch {
            throw APIError.respuestaInvalida
        }
    }

    /// FastAPI manda `{"detail": "..."}` en sus errores — se usa tal cual
    /// si existe; si no, un texto genérico en vez de fallar decodificando.
    private static func extraerMensajeDeError(_ data: Data) -> String {
        if let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            if let detail = object["detail"] as? String, !detail.isEmpty {
                return detail
            }
            if let details = object["detail"] as? [[String: Any]] {
                let mensajes = details.compactMap { $0["msg"] as? String }.filter { !$0.isEmpty }
                if !mensajes.isEmpty { return mensajes.joined(separator: " ") }
            }
        }
        return "sin detalle"
    }

    // MARK: - Codificación

    /// Decoder compartido: fechas ISO 8601, con o sin fracción de segundo
    /// (Postgres `timestamptz` puede mandar cualquiera de las dos formas).
    static func crearDecoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let raw = try container.decode(String.self)
            let conFraccion = ISO8601DateFormatter()
            conFraccion.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let date = conFraccion.date(from: raw) { return date }
            let sinFraccion = ISO8601DateFormatter()
            sinFraccion.formatOptions = [.withInternetDateTime]
            if let date = sinFraccion.date(from: raw) { return date }
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Fecha ISO 8601 inválida: \(raw)")
        }
        return decoder
    }

    private static func crearEncoder() -> JSONEncoder {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        return encoder
    }
}
