import SwiftUI
import EdecanKit

/// Punto de entrada de la app. Si el dispositivo no está emparejado
/// (`PairingStore.isPaired == false` — ver el docstring de `PairingStore`
/// en EdecanKit para qué significa "emparejado" en este v1) muestra
/// ``OnboardingView``; si ya lo está, va directo a ``RootTabView``.
@main
struct EdecanApp: App {
    @UIApplicationDelegateAdaptor(EdecanAppDelegate.self) private var appDelegate
    @State private var pairingStore = PairingStore()
    @State private var session = SessionStore()
    @State private var push = PushNotificationCoordinator()
    @State private var updates = AppUpdateCoordinator()

    var body: some Scene {
        WindowGroup {
            RaizDeLaApp()
                .environment(pairingStore)
                .environment(session)
                .environment(push)
                .environment(updates)
        }
    }
}

/// Separado de `EdecanApp` (el `App` en sí no puede ser una `View` con
/// `@Environment`) solo para poder leer `pairingStore`/`session` ya
/// inyectados y decidir qué mostrar.
private struct RaizDeLaApp: View {
    @Environment(PairingStore.self) private var pairingStore
    @Environment(SessionStore.self) private var session
    @Environment(PushNotificationCoordinator.self) private var push
    @Environment(AppUpdateCoordinator.self) private var updates
    @Environment(\.openURL) private var openURL
    @Environment(\.scenePhase) private var scenePhase

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
        .onOpenURL { url in
            pairingStore.recibirEnlace(url)
        }
        .task(id: pairingStore.deviceId) {
            await push.configurar(client: session.client, deviceId: pairingStore.deviceId)
        }
        .onReceive(NotificationCenter.default.publisher(for: .edecanAPNsToken)) { notification in
            guard let token = notification.object as? String else { return }
            Task { await push.recibir(token: token) }
        }
        .onReceive(NotificationCenter.default.publisher(for: .edecanNotificationRoute)) { notification in
            guard let raw = notification.object as? String else { return }
            push.rutaPendiente = NotificationRoute(rawValue: raw) ?? .assistant
        }
        .onReceive(NotificationCenter.default.publisher(for: .edecanAPNsRegistrationFailed)) { _ in
            push.marcarRegistroRemotoNoDisponible()
        }
        .safeAreaInset(edge: .top, spacing: 0) {
            if pairingStore.isPaired,
               session.sesionValida,
               let update = updates.bannerUpdate {
                AppUpdateBanner(
                    update: update,
                    onDismiss: updates.dismissBanner,
                    onOpen: {
                        updates.markUpdateOpened(update)
                        openURL(update.installURL)
                    }
                )
            }
        }
        .onChange(of: scenePhase, initial: true) { _, phase in
            guard phase == .active else { return }
            Task { await updates.checkIfNeeded() }
        }
    }
}
