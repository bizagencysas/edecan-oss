package cc.edecan.app.notifications

import kotlin.test.Test
import kotlin.test.assertEquals

class NotificationRouteTest {
    @Test
    fun soloAceptaRutasConocidas() {
        assertEquals(NotificationRoute.ACTIVITY, NotificationRoute.parse("activity"))
        assertEquals(NotificationRoute.CREATE, NotificationRoute.parse("CREATE"))
        assertEquals(NotificationRoute.ASSISTANT, NotificationRoute.parse("https://evil.test"))
        assertEquals(NotificationRoute.ASSISTANT, NotificationRoute.parse(null))
    }
}
