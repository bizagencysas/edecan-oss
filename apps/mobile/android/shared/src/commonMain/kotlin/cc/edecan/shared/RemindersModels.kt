package cc.edecan.shared

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Modelo de `/v1/reminders` (`ARCHITECTURE.md` §10.3, §10.12;
 * `apps/api/edecan_api/routers/reminders.py`) — pestaña "Recordatorios"
 * (WP-V5-07). Mismo criterio de defaults tolerantes que
 * [MissionsModels.kt]/[AutomationsModels.kt]/`Models.kt`.
 */

/**
 * Fila de `reminders` (`GET/POST/PUT /v1/reminders*`). `status` se deja
 * `String` suelto — vocabulario real `"pending"|"sent"|"cancelled"`
 * (`ReminderPatch.status` en el backend) — mismo criterio que
 * `Conversation.channel`/`Mission.status`: no hay un valor `"done"` propio,
 * "completar" desde la app manda `status = "sent"` (ver
 * `EdecanApi.completeReminder`).
 */
@Serializable
data class Reminder(
    val id: String,
    @SerialName("tenant_id") val tenantId: String = "",
    @SerialName("user_id") val userId: String = "",
    @SerialName("due_at") val dueAt: String = "",
    val rrule: String? = null,
    val message: String = "",
    val channel: String = "web",
    val status: String = "pending",
    @SerialName("created_at") val createdAt: String = "",
    @SerialName("updated_at") val updatedAt: String = "",
)
