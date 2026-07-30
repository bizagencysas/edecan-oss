import SwiftUI
import EdecanKit

/// Selección de pestaña compartida vía `@Environment` — permite que una
/// pantalla cambie la pestaña activa de otra (p. ej. ``VozView`` llevando a
/// Ajustes para conectar una credencial de voz real). Vive fuera de
/// ``RootTabView`` para que cualquier pantalla pueda leerla/escribirla sin
/// pasar bindings a mano por cada nivel.
@MainActor
@Observable
final class TabRouter {
    var seleccion: AssistantDestination = .edecan
    var solicitudPendiente: SolicitudRapida?
    /// Frente 3 (deeplink): conversación que pidió abrir el último push
    /// tocado. `ChatView` la consume con el mismo patrón que
    /// `solicitudPendiente` — un `.onChange(of: conversacionPendiente?.id)`
    /// que llama a `consumirConversacionPendiente()` y abre esa conversación
    /// con `ChatViewModel.abrirConversacion(id:client:)`.
    var conversacionPendiente: ConversacionSolicitada?
    var presentacion: Presentacion?

    enum Presentacion: String, Identifiable {
        case remote
        var id: String { rawValue }
    }

    struct SolicitudRapida: Identifiable, Equatable {
        let id = UUID()
        let texto: String
    }

    struct ConversacionSolicitada: Identifiable, Equatable {
        let id = UUID()
        let conversationId: String
    }

    func pedir(_ texto: String) {
        solicitudPendiente = SolicitudRapida(texto: texto)
        seleccion = .edecan
    }

    func consumirSolicitud() -> SolicitudRapida? {
        defer { solicitudPendiente = nil }
        return solicitudPendiente
    }

    /// Deja lista la conversación a abrir y cambia a la pestaña del
    /// asistente. Igual que `pedir(_:)`, no abre nada por sí sola — quien
    /// dibuja `ChatView` decide cuándo consumirla.
    func abrirConversacionDesdeNotificacion(_ conversationId: String) {
        conversacionPendiente = ConversacionSolicitada(conversationId: conversationId)
        seleccion = .edecan
    }

    func consumirConversacionPendiente() -> ConversacionSolicitada? {
        defer { conversacionPendiente = nil }
        return conversacionPendiente
    }

    func mostrarRemoto() {
        presentacion = .remote
    }
}

/// Navegación assistant-first: Edecan es la conversación universal,
/// Actividad concentra el trabajo delegado y Ajustes guarda configuración y
/// herramientas avanzadas. IDE, Negocios y Voz siguen existiendo, pero ya no
/// compiten con el asistente como pestañas independientes.
///
/// Nota Liquid Glass: un `TabView` estándar en iOS 26 YA adopta
/// automáticamente la barra flotante translúcida del sistema — no hace
/// falta ningún modifier extra para eso (es el comportamiento por defecto
/// del framework en este deployment target). El `if #available(iOS 26, *)`
/// con fallback a `.ultraThinMaterial` que pide la especificación de este
/// WP vive en ``TarjetaVidrio`` (`Theme.swift`), que sí se aplica a mano en
/// tarjetas/burbujas propias de esta app (`OnboardingView`, `ChatView`,
/// `EmptyStateView`) — ahí es donde el código realmente elige entre
/// `glassEffect` real y el fallback, no en la tab bar misma.
struct RootTabView: View {
    @State private var router = TabRouter()
    @Environment(SessionStore.self) private var session
    @Environment(PushNotificationCoordinator.self) private var push

    var body: some View {
        @Bindable var router = router
        TabView(selection: $router.seleccion) {
            ForEach(tabsVisibles) { tab in
                vista(tab.destino)
                    .tabItem { Label(tab.title, systemImage: tab.systemIcon) }
                    .tag(tab.destino)
            }
        }
        .tint(EdecanTheme.morado)
        .environment(router)
        .task { await session.cargarMobileConfig() }
        .sheet(item: $router.presentacion) { presentacion in
            switch presentacion {
            case .remote:
                NavigationStack { RemotoView() }
                    .environment(router)
            }
        }
        .onChange(of: push.rutaPendiente) { _, route in
            guard let route else { return }
            switch route {
            case .assistant:
                // Frente 3 (deeplink): si el push traía `chat_id`, abre esa
                // conversación puntual; si no, comportamiento de siempre
                // (solo cambia a la pestaña del asistente).
                if let conversationId = push.conversacionPendiente {
                    router.abrirConversacionDesdeNotificacion(conversationId)
                } else {
                    router.seleccion = .edecan
                }
            case .activity: router.seleccion = .activity
            case .settings: router.seleccion = .settings
            case .create:
                router.pedir("Crea ")
            case .remote:
                router.mostrarRemoto()
            }
            push.rutaPendiente = nil
            push.conversacionPendiente = nil
        }
    }

    private var tabsVisibles: [ResolvedMobileTab] {
        let tabs = session.mobileConfig.tabs
            .filter(\.enabled)
            .compactMap(ResolvedMobileTab.init(config:))
            .sorted { $0.order < $1.order }
        return tabs.isEmpty ? ResolvedMobileTab.fallback : tabs
    }

    @ViewBuilder
    private func vista(_ destino: AssistantDestination) -> some View {
        switch destino {
        case .edecan:
            ChatView()
        case .activity:
            InicioView()
        case .ide:
            IDERemoteSessionsView()
        case .settings:
            PerfilView()
        }
    }
}

private struct ResolvedMobileTab: Identifiable {
    let id: String
    let destino: AssistantDestination
    let title: String
    let systemIcon: String
    let order: Int

    init?(config: MobileTabConfig) {
        guard let destino = Self.destino(for: config.id) else { return nil }
        self.id = config.id
        self.destino = destino
        self.title = config.title
        self.systemIcon = config.systemIcon
        self.order = config.order
    }

    init(id: String, destino: AssistantDestination, title: String, systemIcon: String, order: Int) {
        self.id = id
        self.destino = destino
        self.title = title
        self.systemIcon = systemIcon
        self.order = order
    }

    static let fallback = [
        ResolvedMobileTab(id: "assistant", destino: .edecan, title: "Edecán", systemIcon: "bubble.left.and.bubble.right.fill", order: 0),
        ResolvedMobileTab(id: "activity", destino: .activity, title: "Actividad", systemIcon: "clock.arrow.circlepath", order: 1),
        ResolvedMobileTab(id: "ide", destino: .ide, title: "IDE", systemIcon: "chevron.left.forwardslash.chevron.right", order: 2),
        ResolvedMobileTab(id: "profile", destino: .settings, title: "Tú", systemIcon: "person.crop.circle.fill", order: 3),
    ]

    private static func destino(for id: String) -> AssistantDestination? {
        switch id {
        case "assistant", "chat", "edecan":
            return .edecan
        case "activity", "inicio":
            return .activity
        case "ide", "studio":
            return .ide
        case "profile", "settings", "you":
            return .settings
        default:
            return nil
        }
    }
}
