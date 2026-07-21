package cc.edecan.app.vm

import cc.edecan.shared.ArtifactRef
import cc.edecan.shared.ChatBlock
import cc.edecan.shared.Conversation
import cc.edecan.shared.Message
import cc.edecan.shared.edecanJson
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class ChatArtifactStateTest {
    @Test
    fun historialReconstruyeAdjuntoArtefactoYBloqueRicoPersistidos() {
        val conversation = Conversation(
            id = "c1",
            messages = listOf(
                edecanJson.decodeFromString(
                    Message.serializer(),
                    """{"id":"u1","role":"user","content":{"text":"Mira","attachments":[{"file_id":"f-in","filename":"entrada.pdf","mime":"application/pdf"}]}}""",
                ),
                edecanJson.decodeFromString(
                    Message.serializer(),
                    """{"id":"a1","role":"assistant","content":{"text":"Listo"},"tool_calls":[{"type":"tool_end","name":"buscar","artifacts":[{"file_id":"f-out","filename":"salida.png","mime":"image/png"}],"blocks_version":1,"blocks":[{"schema_version":1,"type":"link_preview","url":"https://example.com","title":"Fuente"}]}]}""",
                ),
            ),
        )

        val messages = mensajesPersistidos(conversation)

        assertEquals("f-in", messages.first().adjuntos.single().fileId)
        assertEquals("f-out", messages.last().artefactos.single().fileId)
        assertEquals("Fuente", (messages.last().bloques.single() as ChatBlock.LinkPreview).title)
    }

    @Test
    fun toolEndConservaArtefactosEnElMensajeCorrectoSinDuplicarlos() {
        val artifact = ArtifactRef(
            fileId = "018f7f4c-07f4-7ed0-93c8-cf0525d1092b",
            filename = "propuesta.pdf",
            mime = "application/pdf",
        )
        val initial = ChatUiState(
            mensajes = listOf(
                MensajeUi("usuario", MensajeUi.Rol.USUARIO, "Crea un PDF"),
                MensajeUi("respuesta", MensajeUi.Rol.ASISTENTE, "Listo"),
            ),
            herramientaActiva = "crear_pdf",
        )

        val once = aplicarFinDeHerramienta(initial, "respuesta", listOf(artifact))
        val repeated = aplicarFinDeHerramienta(once, "respuesta", listOf(artifact))

        assertNull(repeated.herramientaActiva)
        assertEquals(emptyList(), repeated.mensajes.first().artefactos)
        assertEquals(listOf(artifact), repeated.mensajes.last().artefactos)
    }

    @Test
    fun toolEndConservaBloques_y_solo_cierra_la_llamada_correspondiente() {
        val block = ChatBlock.LinkPreview(
            url = "https://example.com",
            title = "Resultado",
            fallbackText = "Resultado en example.com",
        )
        val initial = ChatUiState(
            mensajes = listOf(MensajeUi("respuesta", MensajeUi.Rol.ASISTENTE)),
            herramientaActiva = "buscar_web",
            herramientaActivaCallId = "call-a",
        )

        val otroFin = aplicarFinDeHerramienta(
            initial,
            "respuesta",
            emptyList(),
            listOf(block),
            toolCallId = "call-b",
        )
        val finCorrecto = aplicarFinDeHerramienta(
            otroFin,
            "respuesta",
            emptyList(),
            listOf(block),
            toolCallId = "call-a",
        )

        assertEquals("buscar_web", otroFin.herramientaActiva)
        assertEquals("call-a", otroFin.herramientaActivaCallId)
        assertNull(finCorrecto.herramientaActiva)
        assertNull(finCorrecto.herramientaActivaCallId)
        assertEquals(listOf(block), finCorrecto.mensajes.single().bloques)
    }
}
