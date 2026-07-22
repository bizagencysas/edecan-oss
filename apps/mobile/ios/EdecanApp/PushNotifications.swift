import EdecanKit
import Foundation
import Observation
import UIKit
import UserNotifications

extension Notification.Name {
    static let edecanAPNsToken = Notification.Name("cc.edecan.apns-token")
    static let edecanNotificationRoute = Notification.Name("cc.edecan.notification-route")
    static let edecanAPNsRegistrationFailed = Notification.Name("cc.edecan.apns-registration-failed")
}

/// Puente mínimo de UIApplicationDelegate. El token solo vive en memoria el
/// tiempo necesario para sincronizarlo y jamás aparece en logs.
@MainActor
final class EdecanAppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate, @unchecked Sendable {
    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        return true
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken token: Data) {
        let hexadecimal = token.map { String(format: "%02x", $0) }.joined()
        NotificationCenter.default.post(name: .edecanAPNsToken, object: hexadecimal)
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        // No se registra el error porque algunos entornos de firma incluyen
        // información de aprovisionamiento. La UI conserva el fallback local.
        NotificationCenter.default.post(name: .edecanAPNsRegistrationFailed, object: nil)
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner, .sound, .badge])
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        let route = NotificationRoute.parse(userInfo: response.notification.request.content.userInfo)
        NotificationCenter.default.post(name: .edecanNotificationRoute, object: route.rawValue)
        completionHandler()
    }
}

@MainActor
@Observable
final class PushNotificationCoordinator {
    enum Estado: Equatable {
        case comprobando
        case sinPedir
        case activo
        case denegado
        case noDisponible

        var texto: String {
            switch self {
            case .comprobando: "Comprobando…"
            case .sinPedir: "Activa avisos de recordatorios y trabajos"
            case .activo: "Avisos activados"
            case .denegado: "Avisos desactivados en Ajustes de iOS"
            case .noDisponible: "Avisos locales disponibles; push remoto requiere tu firma"
            }
        }
    }

    private(set) var estado: Estado = .comprobando
    var rutaPendiente: NotificationRoute?
    private var client: APIClient?
    private var deviceId: String?

    func configurar(client: APIClient?, deviceId: String?) async {
        self.client = client
        self.deviceId = deviceId
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        actualizarEstado(settings.authorizationStatus)
        if settings.authorizationStatus == .authorized || settings.authorizationStatus == .provisional {
            UIApplication.shared.registerForRemoteNotifications()
        }
    }

    func pedirPermiso() async {
        if estado == .denegado {
            if let settings = URL(string: UIApplication.openSettingsURLString) {
            await UIApplication.shared.open(settings)
            }
            return
        }
        do {
            let permitido = try await UNUserNotificationCenter.current().requestAuthorization(
                options: [.alert, .sound, .badge]
            )
            estado = permitido ? .activo : .denegado
            if permitido { UIApplication.shared.registerForRemoteNotifications() }
        } catch {
            estado = .noDisponible
        }
    }

    func marcarRegistroRemotoNoDisponible() {
        if estado == .activo { estado = .noDisponible }
    }

    func recibir(token: String) async {
        guard let client, let deviceId, !token.isEmpty else { return }
        do {
            try await client.registrarPushToken(deviceId: deviceId, token: token)
            estado = .activo
        } catch {
            // El registro remoto es best-effort: las notificaciones locales
            // siguen funcionando y un siguiente launch vuelve a intentarlo.
        }
    }

    func revocar() async {
        guard let client, let deviceId else { return }
        try? await client.revocarPushToken(deviceId: deviceId)
    }

    private func actualizarEstado(_ status: UNAuthorizationStatus) {
        switch status {
        case .authorized, .provisional, .ephemeral: estado = .activo
        case .denied: estado = .denegado
        case .notDetermined: estado = .sinPedir
        @unknown default: estado = .noDisponible
        }
    }
}

enum LocalNotificationScheduler {
    static func reminder(id: String, message: String, dueAt: Date) async {
        let content = UNMutableNotificationContent()
        content.title = "Recordatorio de Edecán"
        content.body = message
        content.sound = .default
        content.userInfo = ["route": NotificationRoute.activity.rawValue]

        let delay = max(dueAt.timeIntervalSinceNow, 1)
        let trigger = UNTimeIntervalNotificationTrigger(timeInterval: delay, repeats: false)
        let request = UNNotificationRequest(identifier: "reminder-\(id)", content: content, trigger: trigger)
        try? await UNUserNotificationCenter.current().add(request)
    }

    static func completed(kind: String, id: String, title: String, body: String, route: NotificationRoute) async {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.sound = .default
        content.userInfo = ["route": route.rawValue]
        let request = UNNotificationRequest(
            identifier: "\(kind)-\(id)",
            content: content,
            trigger: UNTimeIntervalNotificationTrigger(timeInterval: 1, repeats: false)
        )
        try? await UNUserNotificationCenter.current().add(request)
    }
}
