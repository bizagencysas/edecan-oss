package cc.edecan.shared

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class MobileTabConfig(
    val id: String,
    val title: String,
    @SerialName("system_icon") val systemIcon: String,
    val enabled: Boolean = true,
    val order: Int,
    val badge: String? = null,
)

@Serializable
data class MobileCopyConfig(
    @SerialName("assistant_title") val assistantTitle: String = "Edecán",
    @SerialName("chat_placeholder") val chatPlaceholder: String = "Escríbele a Edecán...",
    @SerialName("ide_placeholder") val idePlaceholder: String = "Dile qué construir, revisar o arreglar...",
    @SerialName("activity_empty") val activityEmpty: String = "No hay nada en curso ahora.",
    @SerialName("profile_title") val profileTitle: String = "Tú",
)

@Serializable
data class MobileFeatureFlags(
    val attachments: Boolean = true,
    val camera: Boolean = true,
    @SerialName("photo_picker") val photoPicker: Boolean = true,
    @SerialName("rich_cards") val richCards: Boolean = true,
    @SerialName("chat_streaming") val chatStreaming: Boolean = true,
    @SerialName("ide_remote") val ideRemote: Boolean = true,
    val voice: Boolean = true,
    val calls: Boolean = true,
    val activity: Boolean = true,
    val profile: Boolean = true,
    @SerialName("server_driven_ui") val serverDrivenUi: Boolean = true,
)

@Serializable
data class MobileActionConfig(
    val id: String,
    val title: String,
    val kind: String,
    val value: String,
    val enabled: Boolean = true,
)

@Serializable
data class MobileServerConfig(
    @SerialName("schema_version") val schemaVersion: Int = 1,
    @SerialName("config_version") val configVersion: Int = 1,
    @SerialName("updated_at") val updatedAt: String = "1970-01-01T00:00:00Z",
    @SerialName("min_supported_build") val minSupportedBuild: Int = 1,
    val platform: String = "android",
    val tabs: List<MobileTabConfig> = emptyList(),
    val copy: MobileCopyConfig = MobileCopyConfig(),
    val flags: MobileFeatureFlags = MobileFeatureFlags(),
    @SerialName("quick_actions") val quickActions: List<MobileActionConfig> = emptyList(),
) {
    companion object {
        val fallbackTabs = listOf(
            MobileTabConfig(
                id = "assistant",
                title = "Edecán",
                systemIcon = "bubble.left.and.bubble.right.fill",
                order = 0,
            ),
            MobileTabConfig(
                id = "activity",
                title = "Actividad",
                systemIcon = "clock.arrow.circlepath",
                order = 1,
            ),
            MobileTabConfig(
                id = "ide",
                title = "IDE",
                systemIcon = "chevron.left.forwardslash.chevron.right",
                order = 2,
            ),
            MobileTabConfig(
                id = "profile",
                title = "Tú",
                systemIcon = "person.crop.circle.fill",
                order = 3,
            ),
        )

        val fallback = MobileServerConfig(tabs = fallbackTabs)
    }
}
