package cc.edecan.app.ui

import kotlin.test.Test
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class ChatUrlSecurityTest {
    @Test
    fun solo_admite_http_publico_sin_credenciales() {
        assertTrue(esUrlPublicaSegura("https://example.com/oferta?id=1"))
        assertFalse(esUrlPublicaSegura("javascript:alert(1)"))
        assertFalse(esUrlPublicaSegura("file:///etc/passwd"))
        assertFalse(esUrlPublicaSegura("https://usuario:clave@example.com"))
        assertFalse(esUrlPublicaSegura("http://localhost/admin"))
        assertFalse(esUrlPublicaSegura("http://127.0.0.1/admin"))
        assertFalse(esUrlPublicaSegura("http://10.0.0.8/metadata"))
        assertFalse(esUrlPublicaSegura("http://169.254.169.254/latest/meta-data"))
        assertFalse(esUrlPublicaSegura("http://[::1]/"))
    }
}
