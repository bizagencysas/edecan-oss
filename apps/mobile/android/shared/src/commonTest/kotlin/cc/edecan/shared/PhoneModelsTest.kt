package cc.edecan.shared

import io.ktor.client.HttpClient
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.respond
import io.ktor.client.plugins.contentnegotiation.ContentNegotiation
import io.ktor.http.HttpHeaders
import io.ktor.http.HttpStatusCode
import io.ktor.http.headersOf
import io.ktor.serialization.kotlinx.json.json
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

class PhoneModelsTest {
    @Test
    fun phoneCall_decodifica_agente_resumen_y_transcripcion() {
        val json = """
            {
              "id":"call-1","conversation_id":"phone-thread","direction":"outgoing",
              "from_e164":"+12025550100","to_e164":"+573001112233",
              "goal":"Confirmar la cita","status":"completed",
              "agent":{"template_id":"sales","template_name":"Seguimiento comercial","name":"Sofía"},
              "duration_seconds":73,
              "summary":{
                "version":1,"status":"completed","direction":"outgoing",
                "participants":[{"role":"contact","name":"Ana","phone_e164":"+573001112233"}],
                "duration_seconds":73,"key_points":["La cita sigue en pie"],
                "commitments":["Ana enviará la dirección"],
                "next_steps":["Revisar el mensaje antes de las 5"],
                "transcript":{"available":true,"turn_count":6}
              },
              "summary_generated_at":"2026-07-01T10:02:00Z"
            }
        """.trimIndent()

        val call = edecanJson.decodeFromString(PhoneCall.serializer(), json)
        val summary = assertNotNull(call.summary)

        assertEquals("+573001112233", call.contactNumber)
        assertEquals("Sofía · Seguimiento comercial", call.agentLabel)
        assertEquals("Ana", summary.participants.single().name)
        assertEquals(listOf("La cita sigue en pie"), summary.keyPoints)
        assertEquals(listOf("Ana enviará la dirección"), summary.commitments)
        assertEquals(listOf("Revisar el mensaje antes de las 5"), summary.nextSteps)
        assertTrue(summary.transcript.available)
        assertEquals(6, summary.transcript.turnCount)
        assertTrue(call.isTerminal)
    }

    @Test
    fun phoneCall_tolera_resumen_parcial_y_llamada_antigua_sin_resumen() {
        val partial = edecanJson.decodeFromString(
            PhoneCall.serializer(),
            """{"id":"call-2","direction":"incoming","from_e164":"+57","summary":{"status":"failed"}}""",
        )
        val legacy = edecanJson.decodeFromString(PhoneCall.serializer(), """{"id":"call-3"}""")
        val summary = assertNotNull(partial.summary)

        assertEquals("+57", partial.contactNumber)
        assertTrue(summary.keyPoints.isEmpty())
        assertFalse(summary.transcript.available)
        assertEquals(0, summary.transcript.turnCount)
        assertNull(legacy.summary)
        assertNull(legacy.agent)
    }

    @Test
    fun phoneCalls_usa_get_autenticado_y_decodifica_la_lista() = runTest {
        val http = HttpClient(MockEngine { request ->
            assertEquals("/v1/phone/calls", request.url.encodedPath)
            assertEquals("Bearer phone-access", request.headers[HttpHeaders.Authorization])
            respond(
                """[{"id":"call-4","direction":"incoming","from_e164":"+57300","status":"ringing"}]""",
                HttpStatusCode.OK,
                headersOf(HttpHeaders.ContentType, "application/json"),
            )
        }) {
            install(ContentNegotiation) { json(edecanJson) }
            expectSuccess = false
        }
        val api = EdecanApi.paraPruebas(
            "https://edecan.test",
            PhoneTokenStore(access = "phone-access", refresh = "phone-refresh"),
            http,
        )

        val calls = api.phoneCalls()

        assertEquals("call-4", calls.single().id)
        assertEquals("+57300", calls.single().contactNumber)
    }
}

private class PhoneTokenStore(
    private var access: String? = null,
    private var refresh: String? = null,
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
