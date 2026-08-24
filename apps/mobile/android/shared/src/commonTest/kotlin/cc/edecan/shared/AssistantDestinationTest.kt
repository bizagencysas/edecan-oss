package cc.edecan.shared

import kotlin.test.Test
import kotlin.test.assertEquals

class AssistantDestinationTest {
    @Test
    fun `la navegacion primaria solo expone tres espacios humanos`() {
        assertEquals(
            listOf(
                AssistantDestination.EDECAN,
                AssistantDestination.ACTIVITY,
                AssistantDestination.YOU,
            ),
            AssistantDestination.entries,
        )
    }
}
