package cc.edecan.app.vm

import cc.edecan.shared.Conversation
import kotlin.test.Test
import kotlin.test.assertEquals

class ChatConversationTitleRefreshTest {
    @Test
    fun aplicaTituloResumidoDelServidorSinReemplazarLosMensajesLocales() {
        val mensaje = MensajeUi("assistant-1", MensajeUi.Rol.ASISTENTE, texto = "Listo")
        val estado = ChatUiState(
            mensajes = listOf(mensaje),
            conversationId = "conversation-1",
            tituloConversacion = null,
            conversaciones = listOf(Conversation(id = "conversation-1")),
        )

        val actualizado = estado.conConversacionesActualizadas(
            listOf(
                Conversation(
                    id = "conversation-1",
                    title = "Configurar API Key de X",
                ),
            ),
        )

        assertEquals("Configurar API Key de X", actualizado.tituloConversacion)
        assertEquals(listOf(mensaje), actualizado.mensajes)
        assertEquals("Configurar API Key de X", actualizado.conversaciones.single().title)
    }
}
