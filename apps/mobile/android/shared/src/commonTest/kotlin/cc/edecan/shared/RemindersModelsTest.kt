package cc.edecan.shared

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

/**
 * Tests de serialización de `RemindersModels.kt` contra ejemplos reales de
 * `apps/api/edecan_api/routers/reminders.py` (WP-V5-07).
 */
class RemindersModelsTest {

    @Test
    fun reminder_decodifica_una_fila_real_de_v1_reminders() {
        val json = """
            {"id":"rem1","tenant_id":"t1","user_id":"u1",
             "due_at":"2026-07-09T15:00:00Z","rrule":null,"message":"Llamar al contador",
             "channel":"web","status":"pending",
             "created_at":"2026-07-01T10:00:00Z","updated_at":"2026-07-01T10:00:00Z"}
        """.trimIndent()
        val reminder = edecanJson.decodeFromString(Reminder.serializer(), json)
        assertEquals("Llamar al contador", reminder.message)
        assertEquals("pending", reminder.status)
        assertEquals("web", reminder.channel)
        assertNull(reminder.rrule)
    }

    @Test
    fun reminder_status_desconocido_no_rompe_la_decodificacion() {
        // `status` se deja `String` suelto a propósito (ver KDoc de
        // `Reminder`) — un valor que este cliente todavía no conoce no debe
        // tumbar la decodificación.
        val json = """
            {"id":"rem2","tenant_id":"t1","user_id":"u1",
             "due_at":"2026-07-09T15:00:00Z","rrule":"FREQ=DAILY","message":"Tomar la pastilla",
             "channel":"web","status":"algo_nuevo_del_backend",
             "created_at":"2026-07-01T10:00:00Z","updated_at":"2026-07-01T10:00:00Z"}
        """.trimIndent()
        val reminder = edecanJson.decodeFromString(Reminder.serializer(), json)
        assertEquals("algo_nuevo_del_backend", reminder.status)
        assertEquals("FREQ=DAILY", reminder.rrule)
    }
}
