import Foundation

// MARK: - Server-driven mobile config (`GET /v1/mobile/config`)

public struct MobileTabConfig: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let title: String
    public let systemIcon: String
    public let enabled: Bool
    public let order: Int
    public let badge: String?

    enum CodingKeys: String, CodingKey {
        case id, title, enabled, order, badge
        case systemIcon = "system_icon"
    }

    public init(
        id: String,
        title: String,
        systemIcon: String,
        enabled: Bool = true,
        order: Int,
        badge: String? = nil
    ) {
        self.id = id
        self.title = title
        self.systemIcon = systemIcon
        self.enabled = enabled
        self.order = order
        self.badge = badge
    }
}

public struct MobileCopyConfig: Codable, Sendable, Equatable {
    public let assistantTitle: String
    public let chatPlaceholder: String
    public let idePlaceholder: String
    public let activityEmpty: String
    public let profileTitle: String

    enum CodingKeys: String, CodingKey {
        case assistantTitle = "assistant_title"
        case chatPlaceholder = "chat_placeholder"
        case idePlaceholder = "ide_placeholder"
        case activityEmpty = "activity_empty"
        case profileTitle = "profile_title"
    }

    public init(
        assistantTitle: String = "Edecán",
        chatPlaceholder: String = "Escríbele a Edecán...",
        idePlaceholder: String = "Dile qué construir, revisar o arreglar...",
        activityEmpty: String = "No hay nada en curso ahora.",
        profileTitle: String = "Tú"
    ) {
        self.assistantTitle = assistantTitle
        self.chatPlaceholder = chatPlaceholder
        self.idePlaceholder = idePlaceholder
        self.activityEmpty = activityEmpty
        self.profileTitle = profileTitle
    }
}

public struct MobileFeatureFlags: Codable, Sendable, Equatable {
    public let attachments: Bool
    public let camera: Bool
    public let photoPicker: Bool
    public let richCards: Bool
    public let chatStreaming: Bool
    public let ideRemote: Bool
    public let voice: Bool
    public let calls: Bool
    public let activity: Bool
    public let profile: Bool
    public let serverDrivenUI: Bool

    enum CodingKeys: String, CodingKey {
        case attachments, camera, voice, calls, activity, profile
        case photoPicker = "photo_picker"
        case richCards = "rich_cards"
        case chatStreaming = "chat_streaming"
        case ideRemote = "ide_remote"
        case serverDrivenUI = "server_driven_ui"
    }

    public init(
        attachments: Bool = true,
        camera: Bool = true,
        photoPicker: Bool = true,
        richCards: Bool = true,
        chatStreaming: Bool = true,
        ideRemote: Bool = true,
        voice: Bool = true,
        calls: Bool = true,
        activity: Bool = true,
        profile: Bool = true,
        serverDrivenUI: Bool = true
    ) {
        self.attachments = attachments
        self.camera = camera
        self.photoPicker = photoPicker
        self.richCards = richCards
        self.chatStreaming = chatStreaming
        self.ideRemote = ideRemote
        self.voice = voice
        self.calls = calls
        self.activity = activity
        self.profile = profile
        self.serverDrivenUI = serverDrivenUI
    }
}

public struct MobileActionConfig: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let title: String
    public let kind: String
    public let value: String
    public let enabled: Bool
}

public struct MobileServerConfig: Codable, Sendable, Equatable {
    public let schemaVersion: Int
    public let configVersion: Int
    public let updatedAt: Date
    public let minSupportedBuild: Int
    public let platform: String
    public let tabs: [MobileTabConfig]
    public let copy: MobileCopyConfig
    public let flags: MobileFeatureFlags
    public let quickActions: [MobileActionConfig]

    enum CodingKeys: String, CodingKey {
        case platform, tabs, copy, flags
        case schemaVersion = "schema_version"
        case configVersion = "config_version"
        case updatedAt = "updated_at"
        case minSupportedBuild = "min_supported_build"
        case quickActions = "quick_actions"
    }

    public static let fallback = MobileServerConfig(
        schemaVersion: 1,
        configVersion: 1,
        updatedAt: Date(timeIntervalSince1970: 0),
        minSupportedBuild: 1,
        platform: "ios",
        tabs: [
            MobileTabConfig(id: "assistant", title: "Edecán", systemIcon: "bubble.left.and.bubble.right.fill", order: 0),
            MobileTabConfig(id: "equipo", title: "Bots", systemIcon: "sparkles", order: 1),
            MobileTabConfig(id: "activity", title: "Actividad", systemIcon: "clock.arrow.circlepath", order: 2),
            MobileTabConfig(id: "ide", title: "IDE", systemIcon: "chevron.left.forwardslash.chevron.right", order: 3),
            MobileTabConfig(id: "profile", title: "Tú", systemIcon: "person.crop.circle.fill", order: 4),
        ],
        copy: MobileCopyConfig(),
        flags: MobileFeatureFlags(),
        quickActions: []
    )
}
