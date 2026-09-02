import EdecanKit
import Foundation
import Observation
import UIKit
import UserNotifications

extension Notification.Name {
    static let edecanAPNsToken = Notification.Name("cc.edecan.apns-token")
    static let edecanNotificationRoute = Notification.Name("cc.edecan.notification-route")
    static let edecanAPNsRegistrationFailed = Notification.Name("cc.edecan.apns-registration-failed")
    static let edecanShareText = Notification.Name("cc.edecan.share-text")
    static let edecanSharePayloads = Notification.Name("cc.edecan.share-payloads")
}

/// Categoría de notificación "¿Vas a ir al gym hoy?" con acciones Sí/No, y el
/// puente que el delegate necesita para ejecutar el checkin. `EdecanAppDelegate`
/// lo instancia el sistema (`@UIApplicationDelegateAdaptor`), así que no puede
/// recibir el `SessionStore` por inyección: guarda acá una referencia al
/// `APIClient` que `PushNotificationCoordinator` ya tiene en cada `configurar`.
@MainActor
enum GymCheckinNotifications {
    static var client: APIClient?

    /// Registra la categoría ANTES de que pueda llegar un aviso. Se llama en
    /// `didFinishLaunchingWithOptions`, junto al `delegate = self`.
    static func registrar() {
        let si = UNNotificationAction(
            identifier: GymCheckinNotificationSupport.accionSi,
            title: "Sí"
        )
        let no = UNNotificationAction(
            identifier: GymCheckinNotificationSupport.accionNo,
            title: "No"
        )
        let gym = UNNotificationCategory(
            identifier: GymCheckinNotificationSupport.categoriaIdentifier,
            actions: [si, no],
            intentIdentifiers: [],
            options: []
        )
        let hecho = UNNotificationAction(identifier: "AVISO_HECHO", title: "Hecho")
        let aviso = UNNotificationCategory(
            identifier: "EDECAN_AVISO",
            actions: [hecho],
            intentIdentifiers: []
        )
        let aprobarSi = UNNotificationAction(
            identifier: "APROBAR_SI",
            title: "Sí",
            options: [.authenticationRequired]
        )
        let aprobarNo = UNNotificationAction(
            identifier: "APROBAR_NO",
            title: "No",
            options: [.authenticationRequired]
        )
        let aprobar = UNNotificationCategory(
            identifier: "EDECAN_APROBAR",
            actions: [aprobarSi, aprobarNo],
            intentIdentifiers: []
        )
        let serie = UNNotificationAction(identifier: "GYM_SERIE", title: "Serie hecha")
        let gymSerie = UNNotificationCategory(
            identifier: "EDECAN_SERIE",
            actions: [serie],
            intentIdentifiers: []
        )
        let agua = UNNotificationAction(identifier: "AGUA_250", title: "Ya tomé 250 ml")
        let aguaCat = UNNotificationCategory(
            identifier: "AGUA",
            actions: [agua],
            intentIdentifiers: []
        )
        UNUserNotificationCenter.current().setNotificationCategories([
            gym, aviso, aprobar, gymSerie, aguaCat,
        ])
    }

    /// Ejecuta el checkin. Si la respuesta es «si», también inicia la sesión gym
    /// y sincroniza el reloj (HK + UI viva). Best-effort ante fallos de red.
    static func responder(respuesta: String) async {
        if respuesta == "si" {
            await WatchCompanion.compartido.accionCheckinConEntrenamiento(respuesta: respuesta)
            return
        }
        if client != nil {
            _ = try? await client?.gymCheckin(respuesta: respuesta)
            await WatchCompanion.compartido.sincronizar()
            return
        }
        await WatchCompanion.compartido.accionCheckin(respuesta)
        await WatchCompanion.compartido.sincronizar()
    }
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
        GymCheckinNotifications.registrar()
        // App matada y abierta tocando el push (no `didReceive response`,
        // que solo dispara con la app ya viva): `launchOptions` trae el
        // mismo payload. Se posterga un turno de run loop porque en este
        // punto `RaizDeLaApp` (EdecanApp.swift) todavía no suscribió sus
        // `.onReceive` — publicarlo ahora mismo se perdería en el vacío.
        if let userInfo = launchOptions?[.remoteNotification] as? [AnyHashable: Any] {
            let destino = NotificationDestino.parse(userInfo: userInfo)
            DispatchQueue.main.async {
                publicarRuta(destino)
            }
        }
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
        // `.list` es lo que mete la notificación en el Centro de
        // Notificaciones. Sin ella, un aviso que llega con la app ABIERTA se
        // muestra como banner efímero y desaparece para siempre: no queda en
        // el historial que uno baja desde arriba, y como el `aps` tampoco trae
        // `badge`, no queda globito en el icono. Resultado: Apple responde 200,
        // el servidor no tiene nada que reprocharse, y la persona dice con
        // razón "no me llegó nada". Este es el estado en que estaba el teléfono
        // cuando se creó el borrador de LinkedIn: la app en primer plano,
        // transmitiendo la pantalla del Mac.
        completionHandler([.banner, .list, .sound, .badge])
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        let userInfo = response.notification.request.content.userInfo
        let destino = NotificationDestino.parse(userInfo: userInfo)
        let accion = response.actionIdentifier
        let reminderId = userInfo["reminderId"] as? String
        let approvalId = userInfo["approvalId"] as? String
        Task { @MainActor in
            var ejecutoAccion = false
            if accion == GymCheckinNotificationSupport.accionSi {
                await GymCheckinNotifications.responder(respuesta: "si")
                ejecutoAccion = true
            } else if accion == GymCheckinNotificationSupport.accionNo {
                await GymCheckinNotifications.responder(respuesta: "no")
                ejecutoAccion = true
            } else if accion == "AVISO_HECHO", let id = reminderId {
                await WatchCompanion.compartido.accionCompletarAviso(id)
                ejecutoAccion = true
            } else if accion == "APROBAR_SI", let id = approvalId {
                _ = await WatchCompanion.compartido.accionDecidirAprobacion(id: id, ok: true)
                ejecutoAccion = true
            } else if accion == "APROBAR_NO", let id = approvalId {
                _ = await WatchCompanion.compartido.accionDecidirAprobacion(id: id, ok: false)
                ejecutoAccion = true
            } else if accion == "GYM_SERIE" {
                await WatchCompanion.compartido.accionSerie()
                ejecutoAccion = true
            } else if accion == "AGUA_250" {
                await WatchCompanion.compartido.accionAgua(250)
                ejecutoAccion = true
            }
            if !ejecutoAccion {
                publicarRuta(destino)
            } else if accion != GymCheckinNotificationSupport.accionSi
                && accion != GymCheckinNotificationSupport.accionNo {
                await WatchCompanion.compartido.sincronizar()
            }
        }
        completionHandler()
    }
}

/// Único punto que publica `.edecanNotificationRoute`, para que la app fría
/// (`didFinishLaunchingWithOptions`) y la app viva (`didReceive response`)
/// terminen en el mismo lugar: `RaizDeLaApp.body` en EdecanApp.swift, que es
/// quien de verdad decide a dónde navegar.
@MainActor
func publicarRuta(_ destino: NotificationDestino) {
    var datos: [AnyHashable: Any] = [:]
    if let conversationId = destino.conversationId {
        datos["conversationId"] = conversationId
    }
    if let callId = destino.callId {
        datos["callId"] = callId
    }
    if let missionId = destino.missionId {
        datos["missionId"] = missionId
    }
    NotificationCenter.default.post(
        name: .edecanNotificationRoute,
        object: destino.route.rawValue,
        userInfo: datos
    )
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
    /// Conversación que traía el push que se acaba de tocar (frente 3,
    /// deeplink). Solo tiene valor cuando `rutaPendiente == .assistant` y el
    /// evento venía con `chat_id` — ver ``NotificationDestino``. `RootTabView`
    /// la consume junto con `rutaPendiente` y la deja en `nil`.
    var conversacionPendiente: String?
    /// Llamada ENTRANTE que traía el push que se acaba de tocar (frente 6,
    /// deeplink). Solo tiene valor cuando `rutaPendiente == .activity` y el
    /// evento era `phone_call_incoming` — ver ``NotificationDestino``.
    /// `RootTabView` la consume junto con `rutaPendiente` y la deja en `nil`.
    var llamadaPendiente: String?
    /// Misión de `work_failed` / `work_completed` que traía el push. Solo
    /// tiene valor cuando no hay `chat_id` (si lo hay, se abre el chat).
    var misionPendiente: String?
    private var client: APIClient?
    private var deviceId: String?
    /// Último token APNs entregado por iOS. Se guarda incluso cuando aún no
    /// tenemos `client` o `deviceId` — el `.task(id: pairingStore.deviceId)`
    /// de `RaizDeLaApp` puede correr con `session.client` todavía en `nil`
    /// (carrera contra el `.onChange` que lo construye), y sin este cache el
    /// token se perdía en silencio hasta el próximo cold-start. `configurar`
    /// lo reintenta en cuanto client y deviceId aparecen.
    private var tokenPendiente: String?

    func configurar(client: APIClient?, deviceId: String?) async {
        self.client = client
        self.deviceId = deviceId
        GymCheckinNotifications.client = client
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        actualizarEstado(settings.authorizationStatus)
        if settings.authorizationStatus == .authorized || settings.authorizationStatus == .provisional {
            UIApplication.shared.registerForRemoteNotifications()
        }
        // Si el token llegó antes de que tuvieramos client/deviceId, lo
        // reintentamos ahora que ya estamos listos.
        if let pendiente = tokenPendiente, client != nil, deviceId != nil {
            await recibir(token: pendiente)
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
        guard !token.isEmpty else { return }
        // Guardamos SIEMPRE el token para reintentos posteriores (ver
        // `tokenPendiente`). Si client/deviceId aún no están, `configurar`
        // llamará a esta función de nuevo cuando lo estén.
        tokenPendiente = token
        guard let client, let deviceId else { return }
        do {
            try await client.registrarPushToken(deviceId: deviceId, token: token)
            estado = .activo
            tokenPendiente = nil
        } catch {
            // El registro remoto es best-effort: dejamos `tokenPendiente`
            // seteado para que el próximo `configurar` (cambio de deviceId,
            // cold restart, o reconstrucción de client) vuelva a intentarlo.
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
        content.categoryIdentifier = "EDECAN_AVISO"
        content.userInfo = [
            "route": NotificationRoute.activity.rawValue,
            "reminderId": id,
        ]

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

    static func approval(id: String, nombre: String, detalle: String) {
        let content = UNMutableNotificationContent()
        content.title = "Edecán pide tu sí"
        content.body = detalle.isEmpty ? nombre : "\(nombre): \(detalle)"
        content.sound = .default
        content.categoryIdentifier = "EDECAN_APROBAR"
        content.userInfo = ["approvalId": id]
        let request = UNNotificationRequest(
            identifier: "aprobar-\(id)",
            content: content,
            trigger: UNTimeIntervalNotificationTrigger(timeInterval: 1, repeats: false)
        )
        UNUserNotificationCenter.current().add(request)
    }

    static func gymSerieLista(ejercicio: String?) {
        let content = UNMutableNotificationContent()
        content.title = "Descanso listo"
        content.body = ejercicio.map { "Siguiente: \($0). Marca la serie." } ?? "Marca la serie."
        content.sound = .default
        content.categoryIdentifier = "EDECAN_SERIE"
        let request = UNNotificationRequest(
            identifier: "gym-serie",
            content: content,
            trigger: UNTimeIntervalNotificationTrigger(timeInterval: 1, repeats: false)
        )
        UNUserNotificationCenter.current().add(request)
    }
}
