package cc.edecan.shared

import kotlin.test.Test
import kotlin.test.assertEquals

class AssistantDestinationTest {
    @Test
    fun `la navegacion primaria solo expone Edecan Actividad y Ajustes`() {
        assertEquals(
            listOf(
                AssistantDestination.EDECAN,
                AssistantDestination.ACTIVITY,
                AssistantDestination.SETTINGS,
            ),
            AssistantDestination.entries,
        )
    }
}
