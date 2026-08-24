import AppIntents
import Foundation

/// Intent seguro: abre Edecán con la pregunta escrita, pero nunca la envía
/// ni ejecuta una tool sin que la persona vea y confirme el composer.
struct AskEdecanIntent: AppIntent {
    static let title: LocalizedStringResource = "Preguntarle a Edecán"
    static let description = IntentDescription("Abre Edecán con una pregunta prellenada.")
    static let openAppWhenRun = true

    @Parameter(title: "Pregunta")
    var question: String

    func perform() async throws -> some IntentResult {
        let texto = String(question.trimmingCharacters(in: .whitespacesAndNewlines).prefix(10_000))
        guard !texto.isEmpty else { return .result() }
        var components = URLComponents()
        components.scheme = "edecan"
        components.host = "share"
        components.queryItems = [URLQueryItem(name: "text", value: texto)]
        guard let url = components.url else { return .result() }
        return .result(opensIntent: OpenURLIntent(url))
    }
}

struct EdecanAppShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: AskEdecanIntent(),
            phrases: ["Pregúntale a \(.applicationName)"],
            shortTitle: "Preguntar a Edecán",
            systemImageName: "sparkles"
        )
        AppShortcut(
            intent: CreateEdecanTaskIntent(),
            phrases: ["Crea una tarea en \(.applicationName)"],
            shortTitle: "Crear tarea",
            systemImageName: "checklist"
        )
        AppShortcut(
            intent: SearchEdecanConversationsIntent(),
            phrases: ["Busca en mis conversaciones de \(.applicationName)"],
            shortTitle: "Buscar conversaciones",
            systemImageName: "magnifyingglass"
        )
    }
}

struct CreateEdecanTaskIntent: AppIntent {
    static let title: LocalizedStringResource = "Crear tarea en Edecán"
    static let openAppWhenRun = true

    @Parameter(title: "Tarea")
    var task: String

    func perform() async throws -> some IntentResult {
        let text = "Crea una tarea: \(task)"
        return .result(opensIntent: OpenURLIntent(DeepLink.share(text)))
    }
}

struct SearchEdecanConversationsIntent: AppIntent {
    static let title: LocalizedStringResource = "Buscar conversaciones en Edecán"
    static let openAppWhenRun = true

    @Parameter(title: "Búsqueda")
    var query: String

    func perform() async throws -> some IntentResult {
        let text = "Busca en mis conversaciones: \(query)"
        return .result(opensIntent: OpenURLIntent(DeepLink.share(text)))
    }
}

private enum DeepLink {
    static func share(_ text: String) -> URL {
        var components = URLComponents()
        components.scheme = "edecan"
        components.host = "share"
        components.queryItems = [URLQueryItem(name: "text", value: String(text.prefix(10_000)))]
        return components.url ?? URL(string: "edecan://share")!
    }
}
