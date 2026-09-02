import Foundation
import UserNotifications
import WatchKit

/// Único delegate de notificaciones en el Watch. `AguaStore` ya no se queda
/// con el centro: antes tragaba acciones gym porque no conocía `GYM_YES`/`GYM_NO`.
@MainActor
final class WatchNotificationRouter: NSObject, UNUserNotificationCenterDelegate {
    static let compartido = WatchNotificationRouter()

    private enum Identifiers {
        static let categoriaGym = "GYM_CHECKIN"
        static let accionSi = "GYM_YES"
        static let accionNo = "GYM_NO"
    }

    func configurar() {
        UNUserNotificationCenter.current().delegate = self
    }

    /// Registra todas las categorías del reloj, incluido gym check-in.
    func registrarCategorias() {
        let tome = UNNotificationAction(identifier: "AGUA_250", title: "Ya tomé 250 ml")
        let agua = UNNotificationCategory(identifier: "AGUA", actions: [tome], intentIdentifiers: [])
        let hecho = UNNotificationAction(identifier: "AVISO_HECHO", title: "Hecho")
        let aviso = UNNotificationCategory(identifier: "EDECAN_AVISO", actions: [hecho], intentIdentifiers: [])
        let si = UNNotificationAction(
            identifier: "APROBAR_SI",
            title: "Sí",
            options: [.authenticationRequired]
        )
        let no = UNNotificationAction(
            identifier: "APROBAR_NO",
            title: "No",
            options: [.authenticationRequired]
        )
        let aprobar = UNNotificationCategory(identifier: "EDECAN_APROBAR", actions: [si, no], intentIdentifiers: [])
        let serie = UNNotificationAction(identifier: "GYM_SERIE", title: "Serie hecha")
        let gymSerie = UNNotificationCategory(identifier: "EDECAN_SERIE", actions: [serie], intentIdentifiers: [])
        let gymSi = UNNotificationAction(identifier: Identifiers.accionSi, title: "Sí")
        let gymNo = UNNotificationAction(identifier: Identifiers.accionNo, title: "No")
        let gymCheckin = UNNotificationCategory(
            identifier: Identifiers.categoriaGym,
            actions: [gymSi, gymNo],
            intentIdentifiers: [],
            options: []
        )
        let misionSi = UNNotificationAction(
            identifier: "MISION_SI",
            title: "Sí",
            options: [.authenticationRequired]
        )
        let misionNo = UNNotificationAction(
            identifier: "MISION_NO",
            title: "No",
            options: [.authenticationRequired]
        )
        let mision = UNNotificationCategory(
            identifier: "EDECAN_MISION",
            actions: [misionSi, misionNo],
            intentIdentifiers: []
        )
        let ping = UNNotificationCategory(identifier: "PING", actions: [], intentIdentifiers: [])
        UNUserNotificationCenter.current().setNotificationCategories([
            agua, aviso, aprobar, gymSerie, gymCheckin, mision, ping,
        ])
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
    ) async -> UNNotificationPresentationOptions {
        let esAgua = notification.request.identifier.hasPrefix("agua-")
        let metaAlcanzada = await MainActor.run { AguaStore.compartido.completo }
        if esAgua, metaAlcanzada { return [] }
        return [.banner, .sound]
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
    ) async {
        let accion = response.actionIdentifier
        let info = response.notification.request.content.userInfo
        let reminderId = info["reminderId"] as? String
        let approvalId = info["approvalId"] as? String
        let missionId = info["missionId"] as? String

        if accion == "AGUA_250" {
            await MainActor.run { AguaStore.compartido.registrar(250) }
            return
        }

        if accion == Identifiers.accionSi {
            await MainActor.run {
                WatchSessionManager.activo?.confirmarCheckin(respuesta: "si")
            }
            return
        }

        if accion == Identifiers.accionNo {
            await MainActor.run {
                WatchSessionManager.activo?.confirmarCheckin(respuesta: "no")
            }
            return
        }

        await MainActor.run {
            let manager = WatchSessionManager.activo
            if accion == "AVISO_HECHO", let avisoId = reminderId {
                manager?.completarAviso(avisoId)
            } else if accion == "APROBAR_SI", let aid = approvalId {
                manager?.decidirAprobacion(id: aid, ok: true)
            } else if accion == "APROBAR_NO", let aid = approvalId {
                manager?.decidirAprobacion(id: aid, ok: false)
            } else if accion == "GYM_SERIE" {
                manager?.registrarSerie()
            } else if accion == "MISION_SI", let mid = missionId {
                manager?.confirmarMision(id: mid, ok: true)
            } else if accion == "MISION_NO", let mid = missionId {
                manager?.confirmarMision(id: mid, ok: false)
            }
        }
    }
}
