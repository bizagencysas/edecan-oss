import SwiftUI
import EdecanKit

/// Punto de entrada de la app. Si el dispositivo no está emparejado
/// (`PairingStore.isPaired == false` — ver el docstring de `PairingStore`
/// en EdecanKit para qué significa "emparejado" en este v1) muestra
/// ``OnboardingView``; si ya lo está, va directo a ``RootTabView``.
@main
struct EdecanApp: App {
    @State private var pairingStore = PairingStore()
    @State private var session = SessionStore()

    var body: some Scene {
        WindowGroup {
            RaizDeLaApp()
                .environment(pairingStore)
                .environment(session)
        }
    }
}

/// Separado de `EdecanApp` (el `App` en sí no puede ser una `View` con
/// `@Environment`) solo para poder leer `pairingStore`/`session` ya
/// inyectados y decidir qué mostrar.
private struct RaizDeLaApp: View {
    @Environment(PairingStore.self) private var pairingStore
    @Environment(SessionStore.self) private var session

    var body: some View {
        Group {
            if pairingStore.isPaired && session.sesionValida {
                RootTabView()
            } else {
                OnboardingView()
            }
        }
        // `initial: true` para que el primer arranque, con un `serverURL`
        // ya guardado del onboarding de una sesión anterior, deje el
        // `APIClient` listo antes de que `RootTabView` intente usarlo.
        .onChange(of: pairingStore.serverURL, initial: true) { _, nuevaURL in
            session.actualizarBaseURL(nuevaURL)
        }
    }
}
