import SwiftUI
import EdecanKit

/// Selección de pestaña compartida vía `@Environment` — permite que una
/// pantalla cambie la pestaña activa de otra (p. ej. ``VozView`` llevando al
/// usuario a Perfil para conectar una credencial de voz real). Vive fuera de
/// ``RootTabView`` para que cualquier pantalla pueda leerla/escribirla sin
/// pasar bindings a mano por cada nivel.
@MainActor
@Observable
final class TabRouter {
    enum Pestana: Hashable {
        case inicio, chat, ide, negocios, voz, perfil
    }

    var seleccion: Pestana = .inicio
}

/// Las 6 pestañas del mockup del panel web: Inicio, Chat, IDE, Negocios, Voz,
/// Perfil (`DIRECCION_ACTUAL.md` "Apps móviles", `ROADMAP_V2.md` §6.1). La
/// pestaña "Llamadas" del esqueleto original se renombró a "Voz" al aterrizar
/// *push-to-talk* nativo (WP-V4-03) — el historial de llamadas de telefonía
/// premium sigue pendiente, ver `docs/movil-ios.md`.
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
            InicioView()
                .tabItem { Label("Inicio", systemImage: "house.fill") }
                .tag(TabRouter.Pestana.inicio)

            ChatView()
                .tabItem { Label("Chat", systemImage: "bubble.left.and.bubble.right.fill") }
                .tag(TabRouter.Pestana.chat)

            IDEView()
                .tabItem { Label("IDE", systemImage: "chevron.left.forwardslash.chevron.right") }
                .tag(TabRouter.Pestana.ide)

            NegociosView()
                .tabItem { Label("Negocios", systemImage: "chart.pie.fill") }
                .tag(TabRouter.Pestana.negocios)

            VozView()
                .tabItem { Label("Voz", systemImage: "waveform") }
                .tag(TabRouter.Pestana.voz)

            PerfilView()
                .tabItem { Label("Perfil", systemImage: "person.crop.circle.fill") }
                .tag(TabRouter.Pestana.perfil)
        }
        .tint(EdecanTheme.morado)
        .environment(router)
    }
}
