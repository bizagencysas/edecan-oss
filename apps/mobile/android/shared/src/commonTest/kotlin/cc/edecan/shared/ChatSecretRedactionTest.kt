package cc.edecan.shared

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse

class ChatSecretRedactionTest {
    @Test
    fun redactsOpenAiProjectKeyBeforeRendering() {
        val secret = "sk-proj-example-secret-1234567890"
        val result = ChatSecretRedaction.redact("Configura OpenAI con $secret")

        assertEquals("Configura OpenAI con [credencial protegida]", result)
        assertFalse(result.contains(secret))
    }

    @Test
    fun leavesOrdinaryConversationUntouched() {
        val text = "Crea una imagen de una chica en un balcón."
        assertEquals(text, ChatSecretRedaction.redact(text))
    }
}
