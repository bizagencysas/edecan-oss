package cc.edecan.shared

import io.ktor.client.HttpClient
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.respond
import io.ktor.client.plugins.contentnegotiation.ContentNegotiation
import io.ktor.http.HttpHeaders
import io.ktor.http.HttpStatusCode
import io.ktor.http.headersOf
import io.ktor.http.content.TextContent
import io.ktor.serialization.kotlinx.json.json
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

class ContentStudioTest {
    @Test
    fun createsPrivateEditableSocialPackage() = runTest {
        val http = HttpClient(MockEngine { request ->
            assertEquals("/v1/content/social", request.url.encodedPath)
            assertEquals("Bearer access-content", request.headers[HttpHeaders.Authorization])
            respond(
                """
                {
                  "status":"ready",
                  "platform":"linkedin",
                  "copy":"Una idea clara.",
                  "parts":["Una idea clara."],
                  "alt_text":"Una mesa con un cuaderno.",
                  "offline_visual":true,
                  "artifacts":[
                    {"file_id":"copy-1","filename":"post.md","mime":"text/markdown"},
                    {"file_id":"image-1","filename":"post.png","mime":"image/png"}
                  ],
                  "requires_human_confirmation":true
                }
                """.trimIndent(),
                HttpStatusCode.OK,
                headersOf(HttpHeaders.ContentType, "application/json"),
            )
        }) {
            install(ContentNegotiation) { json(edecanJson) }
            expectSuccess = false
        }
        val api = EdecanApi.paraPruebas(
            "https://edecan.test",
            ContentTokenStore(access = "access-content", refresh = "refresh-content"),
            http,
        )

        val draft = api.createSocialContent(
            SocialContentRequest(
                platform = SocialContentPlatform.LINKEDIN,
                topic = "Claridad",
                objective = "Enseñar",
                tone = "Humano",
                withImage = true,
            ),
        )

        assertEquals("Una idea clara.", draft.copy)
        assertEquals("image-1", draft.imageArtifact?.fileId)
        assertTrue(draft.requiresHumanConfirmation)
        assertEquals(3_000, draft.platform.characterLimit)
    }

    @Test
    fun exposesHumanServerError() = runTest {
        val http = HttpClient(MockEngine {
            respond(
                """{"detail":"Conecta un modelo antes de crear."}""",
                HttpStatusCode.BadRequest,
                headersOf(HttpHeaders.ContentType, "application/json"),
            )
        }) {
            install(ContentNegotiation) { json(edecanJson) }
            expectSuccess = false
        }
        val api = EdecanApi.paraPruebas(
            "https://edecan.test",
            ContentTokenStore(access = "access", refresh = "refresh"),
            http,
        )

        val error = assertFailsWith<ApiException.Servidor> {
            api.createSocialContent(
                SocialContentRequest(
                    SocialContentPlatform.X,
                    "Una idea",
                    "Conversar",
                    "Directo",
                    false,
                ),
            )
        }

        assertEquals(400, error.status)
        assertEquals("Conecta un modelo antes de crear.", error.detalle)
        assertEquals(280, SocialContentPlatform.X.characterLimit)
    }

    @Test
    fun createsVersionedStudioProjectThroughPrivateFacade() = runTest {
        val http = HttpClient(MockEngine { request ->
            assertEquals("/v1/content/studio/actions", request.url.encodedPath)
            assertEquals("Bearer studio-access", request.headers[HttpHeaders.Authorization])
            val body = (request.body as TextContent).text
            val encoded = edecanJson.parseToJsonElement(body).jsonObject
            assertEquals("create", encoded["action"]?.jsonPrimitive?.content)
            assertEquals("landing", encoded["mode"]?.jsonPrimitive?.content)
            assertEquals("file-1", encoded["files"]?.jsonArray?.first()?.jsonPrimitive?.content)
            respond(
                """
                {
                  "status":"ready","action":"create","message":"Proyecto listo",
                  "result":{
                    "project":{"id":"proj_1","name":"Café Norte","mode":"landing"},
                    "revision":"rev_1","revisions":["rev_1","rev_2"]
                  },
                  "artifacts":[
                    {"file_id":"html-1","filename":"proj_1.html","mime":"text/html"},
                    {"file_id":"png-1","filename":"proj_1.png","mime":"image/png"}
                  ],
                  "presentation":[]
                }
                """.trimIndent(),
                HttpStatusCode.OK,
                headersOf(HttpHeaders.ContentType, "application/json"),
            )
        }) {
            install(ContentNegotiation) { json(edecanJson) }
            expectSuccess = false
        }
        val api = EdecanApi.paraPruebas(
            "https://edecan.test",
            ContentTokenStore(access = "studio-access", refresh = "studio-refresh"),
            http,
        )

        val response = api.studioAction(
            StudioActionRequest(
                action = "create",
                prompt = "Una landing humana",
                projectName = "Café Norte",
                mode = StudioProjectMode.LANDING,
                quality = StudioProjectQuality.MAX,
                count = 2,
                files = listOf("file-1"),
            ),
        )

        assertEquals("proj_1", response.project?.id)
        assertEquals("rev_1", response.revisionId)
        assertEquals("png-1", response.artifacts.last().fileId)
    }

    @Test
    fun decodesProjectHistoryForMobileParity() {
        val response = edecanJson.decodeFromString<StudioActionResponse>(
            """
            {
              "status":"ready","action":"history","message":"Listo",
              "result":{
                "project":{"id":"proj_2","name":"App","mode":"mockup"},
                "revisions":[
                  {"id":"rev_a","label":"Principal","width":390,"height":844,
                   "instruction":"Inicial","createdAt":"2026-07-22T09:00:00Z"},
                  {"id":"rev_b","label":"Revisión","width":390,"height":844,
                   "instruction":"Más clara","createdAt":"2026-07-22T10:00:00Z"}
                ]
              },"artifacts":[],"presentation":[]
            }
            """.trimIndent(),
        )

        assertEquals(2, response.project?.revisionCount)
        assertEquals("Más clara", response.revisions.last().instruction)
    }
}

private class ContentTokenStore(
    private var access: String?,
    private var refresh: String?,
) : TokenStore {
    override suspend fun getServerUrl(): String? = "https://edecan.test"
    override suspend fun saveServerUrl(url: String) = Unit
    override suspend fun getAccessToken(): String? = access
    override suspend fun getRefreshToken(): String? = refresh
    override suspend fun saveTokens(accessToken: String, refreshToken: String) {
        access = accessToken
        refresh = refreshToken
    }
    override suspend fun clearTokens() {
        access = null
        refresh = null
    }
    override suspend fun getDeviceId(): String? = null
    override suspend fun saveDeviceId(deviceId: String) = Unit
    override suspend fun clearDeviceId() = Unit
}
