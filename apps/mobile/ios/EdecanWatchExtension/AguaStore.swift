import Foundation
import Observation
import HealthKit
import UserNotifications
import WatchKit
import WidgetKit

/// Agua del día en el reloj: litros locales + Salud si hay permiso, y avisos
/// de hidratación a horas fijas. No depende del iPhone.
/// Las notificaciones las enruta ``WatchNotificationRouter`` (gym, avisos, agua).
@MainActor
@Observable
final class AguaStore: NSObject {
    static let compartido = AguaStore()
    private let mlKey = "cc.edecan.watch.agua.ml"
    private let diaKey = "cc.edecan.watch.agua.dia"
    private let metaKey = "cc.edecan.watch.agua.meta"
    private let avisosKey = "cc.edecan.watch.agua.avisos"

    var mlHoy = 0
    var metaMl = 2500
    var avisosActivos = true
    var ultimoTrago: Date?
    private var ultimoSipMl = 0

    var litros: Double { Double(mlHoy) / 1000 }
    var progreso: Double { min(1, Double(mlHoy) / Double(max(metaMl, 1))) }
    var completo: Bool { mlHoy >= metaMl }

    private let salud = HKHealthStore()

    override init() {
        super.init()
        let defaults = UserDefaults.standard
        metaMl = defaults.object(forKey: metaKey) as? Int ?? 2500
        avisosActivos = defaults.object(forKey: avisosKey) as? Bool ?? true
        restaurarDia()
    }

    func arrancar() async {
        restaurarDia()
        WatchNotificationRouter.compartido.configurar()
        await pedirPermisos()
        if avisosActivos { programarAvisosAgua() }
    }

    func registrar(_ ml: Int, enSalud: Bool = true) {
        restaurarDia()
        mlHoy += max(0, ml)
        ultimoSipMl = ml
        ultimoTrago = Date()
        persistir()
        WKInterfaceDevice.current().play(.success)
        if enSalud {
            Task { await guardarEnSalud(ml) }
        }
    }

    func deshacerUltimo() {
        guard ultimoSipMl > 0, mlHoy > 0 else { return }
        mlHoy = max(0, mlHoy - ultimoSipMl)
        ultimoSipMl = 0
        persistir()
        WKInterfaceDevice.current().play(.click)
    }

    func cambiarAvisos(_ activos: Bool) {
        avisosActivos = activos
        UserDefaults.standard.set(activos, forKey: avisosKey)
        if activos {
            programarAvisosAgua()
        } else {
            UNUserNotificationCenter.current().removePendingNotificationRequests(
                withIdentifiers: Self.idsAgua
            )
        }
    }

    func avisarALas(hora: Int, minuto: Int, id: String, titulo: String, cuerpo: String) {
        var partes = DateComponents()
        partes.hour = ((hora % 24) + 24) % 24
        partes.minute = minuto
        let contenido = UNMutableNotificationContent()
        contenido.title = titulo
        contenido.body = cuerpo
        contenido.sound = .default
        contenido.categoryIdentifier = "PING"
        let dispara = UNCalendarNotificationTrigger(dateMatching: partes, repeats: true)
        UNUserNotificationCenter.current().add(
            UNNotificationRequest(identifier: "habito-\(id)", content: contenido, trigger: dispara)
        )
        WKInterfaceDevice.current().play(.click)
    }

    func avisarEn(minutos: Int, mensaje: String) {
        let contenido = UNMutableNotificationContent()
        contenido.title = "Edecán"
        contenido.body = mensaje
        contenido.sound = .default
        contenido.categoryIdentifier = "PING"
        let dispara = UNTimeIntervalNotificationTrigger(
            timeInterval: TimeInterval(max(60, minutos * 60)),
            repeats: false
        )
        let id = "ping-\(UUID().uuidString)"
        UNUserNotificationCenter.current().add(
            UNNotificationRequest(identifier: id, content: contenido, trigger: dispara)
        )
        WKInterfaceDevice.current().play(.click)
    }

    private func restaurarDia() {
        let hoy = Self.claveDia(Date())
        let defaults = UserDefaults.standard
        if defaults.string(forKey: diaKey) != hoy {
            defaults.set(hoy, forKey: diaKey)
            defaults.set(0, forKey: mlKey)
            mlHoy = 0
        } else {
            mlHoy = defaults.integer(forKey: mlKey)
        }
    }

    private func persistir() {
        UserDefaults.standard.set(mlHoy, forKey: mlKey)
        UserDefaults.standard.set(Self.claveDia(Date()), forKey: diaKey)
        UserDefaults.standard.set(metaMl, forKey: metaKey)
        WatchSnapshotStore.guardarAgua(ml: mlHoy, meta: metaMl)
    }

    private func pedirPermisos() async {
        _ = try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound])
        WatchNotificationRouter.compartido.registrarCategorias()
        guard HKHealthStore.isHealthDataAvailable() else { return }
        let agua = HKQuantityType(.dietaryWater)
        try? await salud.requestAuthorization(toShare: [agua], read: [agua])
    }

    private static let idsAgua = (0..<7).map { "agua-\($0)" }
    private static let horasAgua = [9, 11, 13, 15, 17, 19, 21]

    private func programarAvisosAgua() {
        let centro = UNUserNotificationCenter.current()
        centro.removePendingNotificationRequests(withIdentifiers: Self.idsAgua)
        for (indice, hora) in Self.horasAgua.enumerated() {
            var partes = DateComponents()
            partes.hour = hora
            partes.minute = 0
            let contenido = UNMutableNotificationContent()
            contenido.title = "Un trago de agua"
            contenido.body = "Edecán te recuerda hidratarte. Meta de hoy: \(metaMl / 1000) L."
            contenido.sound = .default
            contenido.categoryIdentifier = "AGUA"
            let dispara = UNCalendarNotificationTrigger(dateMatching: partes, repeats: true)
            centro.add(
                UNNotificationRequest(
                    identifier: Self.idsAgua[indice],
                    content: contenido,
                    trigger: dispara
                )
            )
        }
    }

    private func guardarEnSalud(_ ml: Int) async {
        guard HKHealthStore.isHealthDataAvailable() else { return }
        let tipo = HKQuantityType(.dietaryWater)
        let cantidad = HKQuantity(unit: .literUnit(with: .milli), doubleValue: Double(ml))
        let muestra = HKQuantitySample(type: tipo, quantity: cantidad, start: Date(), end: Date())
        try? await salud.save(muestra)
    }

    private static func claveDia(_ fecha: Date) -> String {
        let f = DateFormatter()
        f.calendar = Calendar.current
        f.dateFormat = "yyyy-MM-dd"
        return f.string(from: fecha)
    }
}

enum WatchSnapshotStore {
    private static var defaults: UserDefaults {
        UserDefaults(suiteName: "group.cc.edecan.app") ?? .standard
    }

    static func guardarAgua(ml: Int, meta: Int) {
        defaults.set(ml, forKey: "cc.edecan.watch.snap.ml")
        defaults.set(meta, forKey: "cc.edecan.watch.snap.meta")
        recargarComplications()
    }

    static func guardarAviso(mensaje: String?, vence: Date?) {
        defaults.set(mensaje, forKey: "cc.edecan.watch.snap.aviso")
        defaults.set(vence?.timeIntervalSince1970, forKey: "cc.edecan.watch.snap.vence")
        recargarComplications()
    }

    static func guardarPasos(_ pasos: Int, meta: Int) {
        defaults.set(pasos, forKey: "cc.edecan.watch.snap.pasos")
        defaults.set(meta, forKey: "cc.edecan.watch.snap.pasosMeta")
        recargarComplications()
    }

    private static func recargarComplications() {
        WidgetCenter.shared.reloadAllTimelines()
    }
}
