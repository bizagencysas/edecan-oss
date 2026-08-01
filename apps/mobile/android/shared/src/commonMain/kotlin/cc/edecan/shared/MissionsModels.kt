package cc.edecan.shared

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

/**
 * Modelos de `/v1/missions` (`ARCHITECTURE.md` §11 / `ROADMAP_V2.md`
 * §7.4, §7.6, §7.9; `edecan_schemas.missions.MissionOut`/`MissionStepOut`;
 * `apps/api/edecan_api/routers/missions.py`) — pestaña "Misiones" (WP-V5-07).
 * Mismo criterio que el resto de [Models.kt]: solo `id` queda sin default,
 * el resto tolera un campo ausente/nuevo (`edecanJson.ignoreUnknownKeys`,
 * `explicitNulls = false`) para no romper si el backend cambia de forma.
 */

/** Estados "en curso" de `Mission.status` (`edecan_schemas.missions.
 * MISSION_STATUSES`, mismo conjunto que `ACTIVE_MISSION_STATUSES` en
 * `apps/web/src/lib/api-misiones.ts`) — `MisionesViewModel` hace *polling*
 * mientras la misión seleccionada (o alguna de la lista) siga en uno de
 * estos dos. `status` se deja `String` suelto en [Mission]/[MissionStep] en
 * vez de un enum cerrado (mismo criterio que `Conversation.channel`/
 * `Invoice.status`): un estado nuevo del backend no debe tumbar la
 * decodificación de un cliente que todavía no se actualizó. */
val ACTIVE_MISSION_STATUSES: Set<String> = setOf("planning", "running")

/** `edecan_schemas.plans.FLAG_AGENTS_MISSIONS` — gate de plan de TODO
 * `/v1/missions` (`missions.py::_require_agents_missions`). */
const val FLAG_AGENTS_MISSIONS = "agents.missions"

/**
 * Fila pública de `agent_missions` (`GET/POST /v1/missions`,
 * `GET .../{id}/confirm`, `MissionOut`). `plan`/`presupuesto` quedan como
 * [JsonElement] crudo a propósito: `ui/MisionesScreen.kt` (WP-V5-07) solo
 * necesita `objetivo`/`status`/`resultado`/`error` tipados — los pasos
 * reales viven en [MissionStep] vía [MissionDetail], no en `plan` — mismo
 * criterio que `Message.content`/`Message.toolCalls` en `Models.kt`.
 */
@Serializable
data class Mission(
    val id: String,
    @SerialName("tenant_id") val tenantId: String = "",
    @SerialName("user_id") val userId: String = "",
    val objetivo: String = "",
    val status: String = "planning",
    val plan: JsonElement? = null,
    val resultado: String? = null,
    val presupuesto: JsonElement? = null,
    val error: String? = null,
    @SerialName("created_at") val createdAt: String = "",
    @SerialName("updated_at") val updatedAt: String = "",
)

/**
 * Fila pública de `agent_steps` (dentro de `MissionDetailOut.steps`,
 * `GET /v1/missions/{id}`, `MissionStepOut`). `usage` queda como
 * [JsonElement] crudo: cuando `status == "waiting_confirmation"` puede traer
 * `{"pending_tool_call": {"id","name","args"}}` (mismo shape que
 * `MissionStep.usage` en `apps/web/src/lib/api-misiones.ts`) — `ui/
 * MisionesScreen.kt` lo lee a mano igual que `ChatViewModel.formatearArgumentos`
 * lee `ChatEvent.ConfirmationRequired.args`.
 */
@Serializable
data class MissionStep(
    val id: String,
    @SerialName("tenant_id") val tenantId: String = "",
    @SerialName("mission_id") val missionId: String = "",
    val seq: Int = 0,
    val agente: String = "",
    val instruccion: String = "",
    val status: String = "pending",
    val resultado: String? = null,
    val usage: JsonElement? = null,
    @SerialName("created_at") val createdAt: String = "",
    @SerialName("updated_at") val updatedAt: String = "",
)

/** `GET /v1/missions/{id}` (`MissionDetailOut`). */
@Serializable
data class MissionDetail(
    val mission: Mission,
    val steps: List<MissionStep> = emptyList(),
)
