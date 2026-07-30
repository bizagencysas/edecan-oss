package cc.edecan.shared

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class SkillSummary(
    val id: String,
    val nombre: String,
    val descripcion: String = "",
    val version: String = "",
    val enabled: Boolean = false,
    @SerialName("trust_tier") val trustTier: String = "",
    val capabilities: List<String> = emptyList(),
)

@Serializable
data class SkillsEnvelope(val skills: List<SkillSummary> = emptyList())

@Serializable
data class McpServerSummary(
    val nombre: String,
    val transporte: String,
    val url: String? = null,
    val comando: String? = null,
    val estado: String = "active",
)
