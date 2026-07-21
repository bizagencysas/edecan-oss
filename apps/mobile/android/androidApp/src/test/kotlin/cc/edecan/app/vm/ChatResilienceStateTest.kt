package cc.edecan.app.vm

import cc.edecan.shared.Conversation
import cc.edecan.shared.edecanJson
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotEquals
import kotlin.test.assertNull

class ChatResilienceStateTest {
    @Test
    fun reintentarReutilizaLaClaveHastaExitoONuevoContenido() {
        var next = 0
        val keys = ClavesIntentoLogico { "key-${next++}" }

        val first = keys.nueva("m1")
        assertEquals(first, keys.reintentar("m1"))

        keys.completar("m1")
        assertNotEquals(first, keys.reintentar("m1"))

        val secondMessage = keys.nueva("m2")
        assertNotEquals(secondMessage, keys.reintentar("m1"))
    }

    @Test
    fun detallePersistidoReconstruyeLaTarjetaDeConfirmacion() {
        val conversation = edecanJson.decodeFromString(
            Conversation.serializer(),
            """{"id":"c1","pending_confirmation":{"tool_call_id":"call-1","name":"enviar_correo","args":{"to":"ana@example.com","subject":"Hola"}}}""",
        )

        val pending = confirmacionPersistida(conversation)

        assertEquals("call-1", pending?.toolCallId)
        assertEquals("enviar_correo", pending?.nombre)
        assertEquals("to: ana@example.com · subject: Hola", pending?.argumentos)
        assertNull(confirmacionPersistida(conversation.copy(pendingConfirmation = null)))
    }
}
