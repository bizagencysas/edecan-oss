package cc.edecan.shared

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Contrato móvil de `GET /v1/phone/calls`. El vocabulario de estados y
 * roles permanece abierto: un valor nuevo del proveedor nunca debe impedir
 * que la persona abra su historial.
 */

@Serializable
data class PhoneCallAgent(
    @SerialName("template_id") val templateId: String? = null,
    @SerialName("template_name") val templateName: String? = null,
    val name: String? = null,
)

@Serializable
data class PhoneCallParticipant(
    val role: String = "",
    val name: String? = null,
    @SerialName("phone_e164") val phoneE164: String? = null,
)

@Serializable
data class PhoneCallTranscript(
    val available: Boolean = false,
    @SerialName("turn_count") val turnCount: Int = 0,
)

/** Resumen estructurado que genera el servidor al terminar la llamada. */
@Serializable
data class PhoneCallSummary(
    val version: Int = 1,
    val status: String = "",
    val direction: String = "",
    val participants: List<PhoneCallParticipant> = emptyList(),
    @SerialName("duration_seconds") val durationSeconds: Int? = null,
    @SerialName("key_points") val keyPoints: List<String> = emptyList(),
    val commitments: List<String> = emptyList(),
    @SerialName("next_steps") val nextSteps: List<String> = emptyList(),
    val transcript: PhoneCallTranscript = PhoneCallTranscript(),
)

@Serializable
data class PhoneCall(
    val id: String,
    @SerialName("conversation_id") val conversationId: String = "",
    val direction: String = "outgoing",
    @SerialName("from_e164") val fromE164: String = "",
    @SerialName("to_e164") val toE164: String = "",
    val goal: String = "",
    val agent: PhoneCallAgent? = null,
    val status: String = "draft",
    @SerialName("confirmed_at") val confirmedAt: String? = null,
    @SerialName("started_at") val startedAt: String? = null,
    @SerialName("ended_at") val endedAt: String? = null,
    @SerialName("duration_seconds") val durationSeconds: Int? = null,
    val error: String? = null,
    val summary: PhoneCallSummary? = null,
    @SerialName("summary_generated_at") val summaryGeneratedAt: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("updated_at") val updatedAt: String? = null,
)

/** El otro extremo de la llamada, independientemente de su dirección. */
val PhoneCall.contactNumber: String
    get() = if (direction == "incoming") fromE164 else toE164

/** Nombre humano sin mostrar ids de plantilla ni detalles técnicos. */
val PhoneCall.agentLabel: String?
    get() {
        val agentName = agent?.name?.trim().orEmpty()
        val templateName = agent?.templateName?.trim().orEmpty()
        return when {
            agentName.isNotEmpty() && templateName.isNotEmpty() -> "$agentName · $templateName"
            agentName.isNotEmpty() -> agentName
            templateName.isNotEmpty() -> templateName
            else -> null
        }
    }

val PhoneCall.isTerminal: Boolean
    get() = status in setOf("completed", "failed", "busy", "no_answer", "cancelled")
