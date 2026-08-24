package cc.edecan.app.vm

import androidx.lifecycle.SavedStateHandle
import cc.edecan.shared.ChatEvent
import cc.edecan.shared.Conversation
import cc.edecan.shared.edecanJson
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNotEquals
import kotlin.test.assertNull
import kotlin.test.assertTrue

class ChatResilienceStateTest {
    @Test
    fun errorDelAgenteEsUnCierreTerminalNoUnaPerdidaDeConexion() {
        val error = ChatEvent.ErrorEvent("  El proveedor no respondió.  ")

        assertEquals("El proveedor no respondió.", error.mensajeDeFalloTerminal())
        assertNull(ChatEvent.Done().mensajeDeFalloTerminal())
        assertNull(ChatEvent.TextDelta("parcial").mensajeDeFalloTerminal())
    }

    @Test
    fun errorTerminalRetiraLaClaveParaQueReintentarCreeOtroIntento() {
        var next = 0
        val keys = ClavesIntentoLogico { "key-${next++}" }
        val fallido = keys.nueva("m1")

        keys.completar("m1")

        assertNotEquals(fallido, keys.reintentar("m1"))
    }

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

    @Test
    fun intentoPersistidoSoloConservaIdsNoElContenidoPrivado() {
        val savedState = SavedStateHandle(
            mapOf(
                "chat_pending_conversation_id" to "conversation-1",
                "chat_pending_idempotency_key" to "attempt-1",
            ),
        )

        assertEquals(
            TurnoPendientePersistido("conversation-1", "attempt-1"),
            savedState.turnoPendientePersistido(),
        )
        assertFalse(savedState.keys().any { it.contains("body") || it.contains("text") || it.contains("attachment") })
    }

    @Test
    fun replayLimpiaFragmentosSinMarcarElMensajeComoFallido() {
        val state = ChatUiState(
            mensajes = listOf(
                MensajeUi(
                    id = "user-1",
                    rol = MensajeUi.Rol.USUARIO,
                    texto = "Haz un trabajo largo",
                    estadoEntrega = EstadoEntrega.ENTREGADO,
                ),
                MensajeUi(
                    id = "assistant-1",
                    rol = MensajeUi.Rol.ASISTENTE,
                    texto = "Respuesta par",
                    enProgreso = true,
                    trabajo = TrabajoUi(),
                ),
            ),
            enviando = true,
            errorMensaje = "Se perdió la conexión con Edecán.",
        )

        val resumed = state.prepararReanudacion("assistant-1")

        assertTrue(resumed.recuperandoTurno)
        assertNull(resumed.errorMensaje)
        assertEquals("", resumed.mensajes.last().texto)
        assertTrue(resumed.mensajes.last().enProgreso)
        assertNull(resumed.mensajes.last().trabajo)
        assertEquals(EstadoEntrega.ENTREGADO, resumed.mensajes.first().estadoEntrega)
    }
}
