package cc.edecan.shared

import kotlin.test.Test
import kotlin.test.assertEquals

class AssistantDestinationTest {
    @Test
    fun `la navegacion primaria expone los cuatro espacios humanos`() {
        assertEquals(
            listOf(
                AssistantDestination.EDECAN,
                AssistantDestination.ACTIVITY,
                AssistantDestination.IDE,
                AssistantDestination.YOU,
            ),
            AssistantDestination.entries,
        )
    }
}
