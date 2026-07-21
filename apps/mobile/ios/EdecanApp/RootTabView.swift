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

    struct SolicitudRapida: Identifiable, Equatable {
        let id = UUID()
        let texto: String
    }

    func pedir(_ texto: String) {
        solicitudPendiente = SolicitudRapida(texto: texto)
        seleccion = .edecan
    }

    func consumirSolicitud() -> SolicitudRapida? {
        defer { solicitudPendiente = nil }
        return solicitudPendiente
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

    var body: some View {
        @Bindable var router = router
        TabView(selection: $router.seleccion) {
            ChatView()
                .tabItem { Label("Edecan", systemImage: "bubble.left.and.bubble.right.fill") }
                .tag(AssistantDestination.edecan)

            ContentStudioView()
                .tabItem { Label("Crear", systemImage: "wand.and.stars") }
                .tag(AssistantDestination.studio)

            NavigationStack { RemotoView() }
                .tabItem { Label("Remoto", systemImage: "display.and.arrow.down") }
                .tag(AssistantDestination.remote)

            InicioView()
                .tabItem { Label("Actividad", systemImage: "clock.arrow.circlepath") }
                .tag(AssistantDestination.activity)

            PerfilView()
                .tabItem { Label("Ajustes", systemImage: "gearshape.fill") }
                .tag(AssistantDestination.settings)
        }
        .tint(EdecanTheme.morado)
        .environment(router)
    }
}
