package cc.edecan.app.vm

import cc.edecan.shared.ArtifactRef
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class ChatArtifactStateTest {
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
}
