package cc.edecan.shared

import io.ktor.client.HttpClient
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.respond
import io.ktor.http.HttpHeaders
import io.ktor.http.HttpStatusCode
import io.ktor.http.headersOf
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals

class SseClientRequestTest {
    @Test
    fun mensajeIncluyeLaClaveIdempotenteSinAlterarElCuerpo() = runTest {
        val body = """{"text":"Organiza mis pendientes","attachments":[]}"""
        val client = HttpClient(MockEngine { request ->
            assertEquals("Bearer access-chat", request.headers[HttpHeaders.Authorization])
            assertEquals("018f7f4c-07f4-7ed0-93c8-cf0525d1092b", request.headers["Idempotency-Key"])
            respond(
                "data: {\"type\":\"done\"}\n\n",
                HttpStatusCode.OK,
                headersOf(HttpHeaders.ContentType, "text/event-stream"),
            )
        })

        val events = SseClient().stream(
            client = client,
            url = "https://edecan.test/v1/conversations/c1/messages",
            accessToken = "access-chat",
            bodyJson = body,
            idempotencyKey = "018f7f4c-07f4-7ed0-93c8-cf0525d1092b",
        ).toList()

        assertEquals(listOf(ChatEvent.Done()), events)
    }
}
