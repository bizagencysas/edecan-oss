package cc.edecan.shared

import io.ktor.client.HttpClient
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.respond
import io.ktor.client.plugins.contentnegotiation.ContentNegotiation
import io.ktor.client.request.HttpRequestData
import io.ktor.client.request.HttpResponseData
import io.ktor.http.HttpHeaders
import io.ktor.http.HttpStatusCode
import io.ktor.http.content.OutgoingContent
import io.ktor.http.headersOf
import io.ktor.serialization.kotlinx.json.json
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.async
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

class ApiSessionTest {
    @Test
    fun obtieneConversacionCompletaConBearer() = runTest {
        val store = FakeTokenStore(access = "access-chat", refresh = "refresh-chat")
        val api = apiConMock(store) { request ->
            assertEquals("/v1/conversations/c1", request.url.encodedPath)
            assertEquals("Bearer access-chat", request.headers[HttpHeaders.Authorization])
            respond(
                """{"id":"c1","title":"Viaje","messages":[{"id":"m1","role":"user","content":{"text":"Hola"}}]}""",
                HttpStatusCode.OK,
                headersOf(HttpHeaders.ContentType, "application/json"),
            )
        }

        val conversation = api.conversation("c1")

        assertEquals("Viaje", conversation.title)
        assertEquals("Hola", conversation.messages.single().texto)
    }

    @Test
    fun subidaPrivadaUsaMultipartBearerYDecodificaMetadata() = runTest {
        val store = FakeTokenStore(access = "access-upload", refresh = "refresh-upload")
        val api = apiConMock(store) { request ->
            assertEquals("/v1/files", request.url.encodedPath)
            assertEquals("Bearer access-upload", request.headers[HttpHeaders.Authorization])
            assertTrue(request.body::class.simpleName?.contains("MultiPartFormDataContent") == true)
            respond(
                """{"id":"f1","filename":"brief.pdf","mime":"application/pdf","size_bytes":4,"status":"uploaded"}""",
                HttpStatusCode.Created,
                headersOf(HttpHeaders.ContentType, "application/json"),
            )
        }

        val uploaded = api.uploadFile(byteArrayOf(1, 2, 3, 4), "brief.pdf", "application/pdf")

        assertEquals(UploadedFile("f1", "brief.pdf", "application/pdf", 4, "uploaded"), uploaded)
    }

    @Test
    fun descargaArtefactoUsaRutaPrivadaYBearerSinRedReal() = runTest {
        val store = FakeTokenStore(access = "access-privado", refresh = "refresh-privado")
        val bytes = "contenido-pdf-sintetico".encodeToByteArray()
        val api = apiConMock(store) { request ->
            assertEquals(
                "/v1/files/018f7f4c-07f4-7ed0-93c8-cf0525d1092b/download",
                request.url.encodedPath,
            )
            assertEquals("Bearer access-privado", request.headers[HttpHeaders.Authorization])
            respond(bytes, HttpStatusCode.OK, headersOf(HttpHeaders.ContentType, "application/pdf"))
        }
        val artifact = ArtifactRef(
            fileId = "018f7f4c-07f4-7ed0-93c8-cf0525d1092b",
            filename = "propuesta.pdf",
            mime = "application/pdf",
        )

        val download = api.downloadArtifact(artifact)

        assertEquals(artifact, download.artifact)
        assertTrue(download.bytes.contentEquals(bytes))
    }

    @Test
    fun previewDeImagenUsaContentPrivadoYBearer() = runTest {
        val store = FakeTokenStore(access = "access-preview", refresh = "refresh-preview")
        val bytes = byteArrayOf(1, 2, 3, 4)
        val api = apiConMock(store) { request ->
            assertEquals("/v1/files/image-id/content", request.url.encodedPath)
            assertEquals("Bearer access-preview", request.headers[HttpHeaders.Authorization])
            respond(bytes, HttpStatusCode.OK, headersOf(HttpHeaders.ContentType, "image/png"))
        }
        val artifact = ArtifactRef("image-id", "preview.png", "image/png")

        val preview = api.previewArtifact(artifact)

        assertEquals(artifact, preview.artifact)
        assertTrue(preview.bytes.contentEquals(bytes))
    }

    @Test
    fun rangoMultimediaRepiteLaMismaVentanaConTokenRenovado() = runTest {
        val store = FakeTokenStore(access = "access-viejo", refresh = "refresh-viejo")
        var contentRequests = 0
        val api = apiConMock(store) { request ->
            when (request.url.encodedPath) {
                "/v1/files/video-id/content" -> {
                    contentRequests += 1
                    assertEquals("bytes=1024-1027", request.headers[HttpHeaders.Range])
                    if (contentRequests == 1) {
                        assertEquals("Bearer access-viejo", request.headers[HttpHeaders.Authorization])
                        respond("""{"detail":"expirado"}""", HttpStatusCode.Unauthorized)
                    } else {
                        assertEquals("Bearer access-renovado", request.headers[HttpHeaders.Authorization])
                        respond(
                            byteArrayOf(1, 2, 3, 4),
                            HttpStatusCode.PartialContent,
                            headersOf(
                                HttpHeaders.ContentRange to listOf("bytes 1024-1027/4096"),
                                HttpHeaders.ContentType to listOf("video/mp4"),
                            ),
                        )
                    }
                }
                "/v1/auth/refresh" -> respond(
                    """{"access_token":"access-renovado","refresh_token":"refresh-renovado"}""",
                    HttpStatusCode.OK,
                    headersOf(HttpHeaders.ContentType, "application/json"),
                )
                else -> error("Ruta inesperada: ${request.url.encodedPath}")
            }
        }

        val window = api.privateMediaRange(ArtifactRef("video-id", "demo.mp4", "video/mp4"), 1024, 4)

        assertEquals(2, contentRequests)
        assertTrue(window.bytes.contentEquals(byteArrayOf(1, 2, 3, 4)))
        assertEquals(1024L, window.offset)
        assertEquals(4096L, window.totalSize)
        assertEquals("access-renovado", store.access)
        assertEquals("refresh-renovado", store.refresh)
    }

    @Test
    fun logoutRevocaElDispositivoConElContratoRealDespuesDeLimpiarLocalmente() = runTest {
        val store = FakeTokenStore(access = "access-viejo", refresh = "refresh-viejo")
        val requests = mutableListOf<Pair<String, String?>>()
        val api = apiConMock(store) { request ->
            // La red se toca solo después de invalidar el almacenamiento local.
            assertNull(store.access)
            assertNull(store.refresh)
            requests += request.url.encodedPath to request.headers[HttpHeaders.Authorization]
            respond("", HttpStatusCode.NoContent)
        }

        api.cerrarSesion(deviceId = "device-123")

        assertEquals(
            listOf(
                "/v1/devices/device-123/revoke" to "Bearer access-viejo",
                "/v1/auth/logout" to null,
            ),
            requests,
        )
    }

    @Test
    fun logoutRevocaElRefreshTokenYSiempreLimpiaLaSesionLocal() = runTest {
        val store = FakeTokenStore(access = "access-viejo", refresh = "refresh-viejo")
        var path: String? = null
        var body: String? = null
        val api = apiConMock(store) { request ->
            path = request.url.encodedPath
            body = (request.body as OutgoingContent.ByteArrayContent).bytes().decodeToString()
            respond("", HttpStatusCode.NoContent)
        }

        api.cerrarSesion()

        assertEquals("/v1/auth/logout", path)
        assertEquals("""{"refresh_token":"refresh-viejo"}""", body)
        assertNull(store.access)
        assertNull(store.refresh)
        assertFalse(api.haySesion())
    }

    @Test
    fun logoutOfflineNoDejaTokensReutilizablesEnElDispositivo() = runTest {
        val store = FakeTokenStore(access = "access", refresh = "refresh")
        val api = apiConMock(store) { error("sin red") }

        api.cerrarSesion()

        assertNull(store.access)
        assertNull(store.refresh)
        assertEquals(1, store.clearCount)
    }

    @Test
    fun refreshRechazadoExpiraLaSesionEnVezDeReportarLoginIncorrecto() = runTest {
        val store = FakeTokenStore(access = "access-expirado", refresh = "refresh-revocado")
        var callbackInvocado = false
        val api = apiConMock(store, onSessionExpired = { callbackInvocado = true }) {
            respond(
                """{"detail":"Refresh token inválido o revocado"}""",
                HttpStatusCode.Unauthorized,
                headersOf(HttpHeaders.ContentType, "application/json"),
            )
        }

        assertFailsWith<ApiException.SesionExpirada> { api.refrescar() }

        assertNull(store.access)
        assertNull(store.refresh)
        assertTrue(callbackInvocado)
    }

    @Test
    fun refreshAusenteTambienNotificaExpiracionSinTocarLaRed() = runTest {
        val store = FakeTokenStore(access = "access-sin-refresh", refresh = null)
        var callbackInvocado = false
        var redInvocada = false
        val api = apiConMock(store, onSessionExpired = { callbackInvocado = true }) {
            redInvocada = true
            error("No debía intentar una petición sin refresh token")
        }

        assertFailsWith<ApiException.SesionExpirada> { api.refrescar() }

        assertTrue(callbackInvocado)
        assertFalse(redInvocada)
        assertNull(store.access)
        assertNull(store.refresh)
    }

    @Test
    fun refreshEnVueloNoPuedeResucitarTokensDespuesDeLogout() = runTest {
        val store = FakeTokenStore(access = "access-viejo", refresh = "refresh-viejo")
        val refreshInicio = CompletableDeferred<Unit>()
        val permitirRefresh = CompletableDeferred<Unit>()
        val api = apiConMock(store) { request ->
            when (request.url.encodedPath) {
                "/v1/auth/refresh" -> {
                    refreshInicio.complete(Unit)
                    permitirRefresh.await()
                    respond(
                        """{"access_token":"access-huerfano","refresh_token":"refresh-huerfano"}""",
                        HttpStatusCode.OK,
                        headersOf(HttpHeaders.ContentType, "application/json"),
                    )
                }
                "/v1/auth/logout" -> respond("{}", HttpStatusCode.OK)
                else -> error("Ruta inesperada: ${request.url.encodedPath}")
            }
        }

        val refresh = async { runCatching { api.refrescar() } }
        refreshInicio.await()
        api.cerrarSesion()
        permitirRefresh.complete(Unit)

        assertTrue(refresh.await().exceptionOrNull() is ApiException.SesionExpirada)
        assertNull(store.access)
        assertNull(store.refresh)
    }

    @Test
    fun refreshViejoRechazadoNoBorraUnLoginNuevo() = runTest {
        val store = FakeTokenStore(access = "access-viejo", refresh = "refresh-viejo")
        val refreshInicio = CompletableDeferred<Unit>()
        val permitirRespuestaVieja = CompletableDeferred<Unit>()
        val api = apiConMock(store) { request ->
            when (request.url.encodedPath) {
                "/v1/auth/refresh" -> {
                    refreshInicio.complete(Unit)
                    permitirRespuestaVieja.await()
                    respond("""{"detail":"revocado"}""", HttpStatusCode.Unauthorized)
                }
                "/v1/auth/login" -> respond(
                    """{"access_token":"access-nuevo","refresh_token":"refresh-nuevo"}""",
                    HttpStatusCode.OK,
                    headersOf(HttpHeaders.ContentType, "application/json"),
                )
                else -> error("Ruta inesperada: ${request.url.encodedPath}")
            }
        }

        val refreshViejo = async { runCatching { api.refrescar() } }
        refreshInicio.await()
        api.login("nuevo@edecan.test", "password-seguro")
        permitirRespuestaVieja.complete(Unit)

        assertTrue(refreshViejo.await().exceptionOrNull() is ApiException.SesionExpirada)
        assertEquals("access-nuevo", store.access)
        assertEquals("refresh-nuevo", store.refresh)
    }

    @Test
    fun cancelacionDeRedNoSeConvierteEnErrorOffline() = runTest {
        val store = FakeTokenStore(access = "access", refresh = "refresh")
        val api = apiConMock(store) { throw CancellationException("cancelado por lifecycle") }

        assertFailsWith<CancellationException> { api.me() }
        assertEquals("access", store.access)
        assertEquals("refresh", store.refresh)
    }

    @Test
    fun respuestaMeAnteriorAlLogoutSeDescartaPorEpoch() = runTest {
        val store = FakeTokenStore(access = "access-viejo", refresh = "refresh-viejo")
        val meInicio = CompletableDeferred<Unit>()
        val permitirMe = CompletableDeferred<Unit>()
        val api = apiConMock(store) { request ->
            when (request.url.encodedPath) {
                "/v1/me" -> {
                    meInicio.complete(Unit)
                    permitirMe.await()
                    respond(
                        """{
                          "user":{"id":"u1","email":"anterior@edecan.test","created_at":"2026-01-01T00:00:00Z"},
                          "tenant":{"id":"t1","name":"Tenant anterior","slug":"anterior","plan_key":"free_selfhost","status":"active","created_at":"2026-01-01T00:00:00Z"},
                          "flags":{}
                        }""",
                        HttpStatusCode.OK,
                        headersOf(HttpHeaders.ContentType, "application/json"),
                    )
                }
                "/v1/auth/logout" -> respond("{}", HttpStatusCode.OK)
                else -> error("Ruta inesperada: ${request.url.encodedPath}")
            }
        }

        val meViejo = async { runCatching { api.me() } }
        meInicio.await()
        api.cerrarSesion()
        permitirMe.complete(Unit)

        assertTrue(meViejo.await().exceptionOrNull() is ApiException.SesionExpirada)
        assertNull(store.access)
        assertNull(store.refresh)
    }

    @Test
    fun respuestaDelRetryPosteriorAlRefreshTambienValidaEpoch() = runTest {
        val store = FakeTokenStore(access = "access-viejo", refresh = "refresh-viejo")
        val retryInicio = CompletableDeferred<Unit>()
        val permitirRetry = CompletableDeferred<Unit>()
        var meRequests = 0
        val api = apiConMock(store) { request ->
            when (request.url.encodedPath) {
                "/v1/me" -> {
                    meRequests += 1
                    if (meRequests == 1) {
                        respond("""{"detail":"access expirado"}""", HttpStatusCode.Unauthorized)
                    } else {
                        retryInicio.complete(Unit)
                        permitirRetry.await()
                        respond(
                            """{
                              "user":{"id":"u1","email":"anterior@edecan.test","created_at":"2026-01-01T00:00:00Z"},
                              "tenant":{"id":"t1","name":"Tenant anterior","slug":"anterior","plan_key":"free_selfhost","status":"active","created_at":"2026-01-01T00:00:00Z"},
                              "flags":{}
                            }""",
                            HttpStatusCode.OK,
                            headersOf(HttpHeaders.ContentType, "application/json"),
                        )
                    }
                }
                "/v1/auth/refresh" -> respond(
                    """{"access_token":"access-renovado","refresh_token":"refresh-renovado"}""",
                    HttpStatusCode.OK,
                    headersOf(HttpHeaders.ContentType, "application/json"),
                )
                "/v1/auth/logout" -> respond("{}", HttpStatusCode.OK)
                else -> error("Ruta inesperada: ${request.url.encodedPath}")
            }
        }

        val meViejo = async { runCatching { api.me() } }
        retryInicio.await()
        api.cerrarSesion()
        permitirRetry.complete(Unit)

        assertTrue(meViejo.await().exceptionOrNull() is ApiException.SesionExpirada)
        assertNull(store.access)
        assertNull(store.refresh)
    }

    private fun apiConMock(
        store: TokenStore,
        onSessionExpired: (() -> Unit)? = null,
        handler: suspend io.ktor.client.engine.mock.MockRequestHandleScope.(HttpRequestData) -> HttpResponseData,
    ): EdecanApi {
        val http = HttpClient(MockEngine(handler)) {
            install(ContentNegotiation) { json(edecanJson) }
            expectSuccess = false
        }
        return EdecanApi.paraPruebas("https://edecan.test", store, http, onSessionExpired)
    }
}

private class FakeTokenStore(
    var access: String? = null,
    var refresh: String? = null,
) : TokenStore {
    private var serverUrl: String? = null
    private var deviceId: String? = null
    var clearCount = 0
        private set

    override suspend fun getServerUrl(): String? = serverUrl
    override suspend fun saveServerUrl(url: String) { serverUrl = url }
    override suspend fun getAccessToken(): String? = access
    override suspend fun getRefreshToken(): String? = refresh
    override suspend fun saveTokens(accessToken: String, refreshToken: String) {
        access = accessToken
        refresh = refreshToken
    }
    override suspend fun clearTokens() {
        clearCount += 1
        access = null
        refresh = null
    }
    override suspend fun getDeviceId(): String? = deviceId
    override suspend fun saveDeviceId(deviceId: String) { this.deviceId = deviceId }
    override suspend fun clearDeviceId() { deviceId = null }
}
