import Foundation
import Observation
import UserNotifications
import WatchConnectivity
import WatchKit

struct WatchAviso: Identifiable, Equatable {
    let id: String
    let mensaje: String
    let vence: Date
    let pendiente: Bool
}

struct WatchAprobacion: Identifiable, Equatable {
    let id: String
    let nombre: String
    let detalle: String
}

struct WatchMision: Identifiable, Equatable {
    let id: String
    let objetivo: String
    let status: String
}

struct WatchRutina: Identifiable, Equatable {
    let id: String
    let nombre: String
    let enabled: Bool
}

struct WatchCompanero: Identifiable, Equatable {
    let id: String
    let nombre: String
    let status: String
}

@MainActor
@Observable
final class WatchSessionManager: NSObject, WCSessionDelegate {
    var sesionActiva = false
    var pausada = false
    var tienePlan = false
    var tituloPlan: String?
    var cronometro: TimeInterval?
    var frecuenciaCardiaca: Double?
    var calorias: Double?
    var descansoRestante: Int?
    var descansoEjercicio: String?
    var descansoFin: Date?
    var racha = 0
    var misionesActivas = 0
    var avisos: [WatchAviso] = []
    var ejercicioActual: String?
    var seriesHechas = 0
    var seriesTotales = 0
    var aprobaciones: [WatchAprobacion] = []
    var misiones: [WatchMision] = []
    var enLlamada = false
    var nombreLlamada: String?
    var idLlamada: String?
    var rutinasActivas = 0
    var rutinas: [WatchRutina] = []
    var equipo: [WatchCompanero] = []
    var ultimaRespuesta: String?
    var pesoSugerido: Double?
    var repsSugeridas: Int?
    var turnoLlamada: String?
    var turnoRol: String?

    static weak var activo: WatchSessionManager?

    var avisosPendientes: [WatchAviso] { avisos.filter(\.pendiente) }
    var proximoAviso: WatchAviso? {
        avisosPendientes.sorted { $0.vence < $1.vence }.first
    }

    /// Pantalla viva: sesión activa, HK en curso o descanso — sin exigir título de plan.
    var mostrarLiveEntrenamiento: Bool {
        if let restante = descansoRestante, restante > 0 { return true }
        return sesionActiva || pausada || WatchEntrenamiento.compartido.activo
    }

    private let sesion: WCSession

    override init() {
        sesion = WCSession.default
        super.init()
        sesion.delegate = self
        sesion.activate()
        Self.activo = self
    }

    nonisolated func session(_ session: WCSession, didReceiveMessage message: [String: Any]) {
        aplicar(extraer(message))
    }

    nonisolated func session(
        _ session: WCSession,
        didReceiveMessage message: [String: Any],
        replyHandler: @escaping ([String: Any]) -> Void,
    ) {
        aplicar(extraer(message))
        replyHandler(["ok": true])
    }

    nonisolated func session(
        _ session: WCSession,
        didReceiveApplicationContext applicationContext: [String: Any],
    ) {
        aplicar(extraer(applicationContext))
    }

    nonisolated func session(
        _ session: WCSession,
        activationDidCompleteWith activationState: WCSessionActivationState,
        error: Error?,
    ) {
        aplicar(extraer(session.receivedApplicationContext))
    }

    private struct Inbound: Sendable {
        var activa: Bool?
        var pausada: Bool?
        var tienePlan: Bool?
        var titulo: String?
        var segundos: TimeInterval?
        var bpm: Double?
        var kcal: Double?
        var descanso: Int?
        var ejercicioDescanso: String?
        var descansoTerminado: Bool?
        var racha: Int?
        var misionesActivas: Int?
        var avisos: [WatchAviso]
        var avisosIncluidos: Bool
        var ejercicio: String?
        var seriesHechas: Int?
        var seriesTotales: Int?
        var aprobaciones: [WatchAprobacion]
        var aprobacionesIncluidas: Bool
        var misiones: [WatchMision]
        var misionesIncluidas: Bool
        var enLlamada: Bool?
        var nombreLlamada: String?
        var idLlamada: String?
        var rutinas: Int?
        var listaRutinas: [WatchRutina]
        var rutinasIncluidas: Bool
        var equipo: [WatchCompanero]
        var equipoIncluido: Bool
        var respuesta: String?
        var pesoSugerido: Double?
        var repsSugeridas: Int?
        var turno: String?
        var turnoRol: String?
        var sumarAgua: Int?
        var aguaEnSalud: Bool?
    }

    private nonisolated func extraer(_ message: [String: Any]) -> Inbound {
        func entero(_ clave: String) -> Int? {
            (message[clave] as? Int) ?? (message[clave] as? NSNumber)?.intValue
        }
        func real(_ clave: String) -> Double? {
            (message[clave] as? Double) ?? (message[clave] as? NSNumber)?.doubleValue
        }
        func flag(_ clave: String) -> Bool? {
            (message[clave] as? Bool) ?? (message[clave] as? NSNumber)?.boolValue
        }
        func filas(_ clave: String) -> [[String: Any]] {
            message[clave] as? [[String: Any]] ?? []
        }
        let avisos = filas("recordatorios").compactMap { fila -> WatchAviso? in
            guard let id = fila["id"] as? String, let mensaje = fila["mensaje"] as? String else { return nil }
            let vence = (fila["vence"] as? TimeInterval).map { Date(timeIntervalSince1970: $0) } ?? Date()
            let pendiente = (fila["pendiente"] as? Bool) ?? (fila["pendiente"] as? NSNumber)?.boolValue ?? true
            return WatchAviso(id: id, mensaje: mensaje, vence: vence, pendiente: pendiente)
        }
        let aprobaciones = filas("aprobaciones").compactMap { fila -> WatchAprobacion? in
            guard let id = fila["id"] as? String else { return nil }
            return WatchAprobacion(
                id: id,
                nombre: fila["nombre"] as? String ?? "Aprobación",
                detalle: fila["detalle"] as? String ?? ""
            )
        }
        let misiones = filas("misiones").compactMap { fila -> WatchMision? in
            guard let id = fila["id"] as? String else { return nil }
            return WatchMision(
                id: id,
                objetivo: fila["objetivo"] as? String ?? "",
                status: fila["status"] as? String ?? ""
            )
        }
        let listaRutinas = filas("rutinas").compactMap { fila -> WatchRutina? in
            guard let id = fila["id"] as? String else { return nil }
            let on = (fila["enabled"] as? Bool) ?? (fila["enabled"] as? NSNumber)?.boolValue ?? false
            return WatchRutina(id: id, nombre: fila["nombre"] as? String ?? "Rutina", enabled: on)
        }
        let equipo = filas("equipo").compactMap { fila -> WatchCompanero? in
            guard let id = fila["id"] as? String else { return nil }
            return WatchCompanero(
                id: id,
                nombre: fila["nombre"] as? String ?? "Compañero",
                status: fila["status"] as? String ?? ""
            )
        }
        return Inbound(
            activa: flag("sesionActiva"),
            pausada: flag("pausada"),
            tienePlan: flag("tienePlan"),
            titulo: message["tituloPlan"] as? String,
            segundos: real("cronometro"),
            bpm: real("frecuenciaCardiaca"),
            kcal: real("calorias"),
            descanso: entero("descansoRestante"),
            ejercicioDescanso: message["descansoEjercicio"] as? String,
            descansoTerminado: flag("descansoTerminado"),
            racha: entero("racha"),
            misionesActivas: entero("misionesActivas"),
            avisos: avisos,
            avisosIncluidos: message["recordatorios"] != nil,
            ejercicio: message["ejercicioActual"] as? String,
            seriesHechas: entero("seriesHechas"),
            seriesTotales: entero("seriesTotales"),
            aprobaciones: aprobaciones,
            aprobacionesIncluidas: message["aprobaciones"] != nil,
            misiones: misiones,
            misionesIncluidas: message["misiones"] != nil,
            enLlamada: flag("enLlamada"),
            nombreLlamada: message["nombreLlamada"] as? String,
            idLlamada: message["idLlamada"] as? String,
            rutinas: entero("rutinasActivas"),
            listaRutinas: listaRutinas,
            rutinasIncluidas: message["rutinas"] != nil,
            equipo: equipo,
            equipoIncluido: message["equipo"] != nil,
            respuesta: message["respuesta"] as? String,
            pesoSugerido: real("pesoSugerido"),
            repsSugeridas: entero("repsSugeridas"),
            turno: message["turnoLlamada"] as? String,
            turnoRol: message["turnoRol"] as? String,
            sumarAgua: entero("sumarAgua"),
            aguaEnSalud: flag("aguaEnSalud"),
        )
    }

    private nonisolated func aplicar(_ inbound: Inbound) {
        Task { @MainActor [weak self] in
            self?.aplicarEnMain(inbound)
        }
    }

    private func aplicarEnMain(_ inbound: Inbound) {
        let estabaActiva = sesionActiva
        if let activa = inbound.activa { sesionActiva = activa }
        if let pausada = inbound.pausada { self.pausada = pausada }
        if let tiene = inbound.tienePlan { tienePlan = tiene }
        if let titulo = inbound.titulo { tituloPlan = titulo.isEmpty ? nil : titulo }
        if inbound.tienePlan == false {
            tituloPlan = nil
            sesionActiva = false
            pausada = false
        }
        if let segundos = inbound.segundos { cronometro = segundos }
        if let bpm = inbound.bpm { frecuenciaCardiaca = bpm }
        if let kcal = inbound.kcal { calorias = kcal }
        if let racha = inbound.racha { self.racha = racha }
        if let n = inbound.misionesActivas { misionesActivas = n }
        if inbound.avisosIncluidos { avisos = inbound.avisos }
        if let ej = inbound.ejercicio { ejercicioActual = ej.isEmpty ? nil : ej }
        if let n = inbound.seriesHechas { seriesHechas = n }
        if let n = inbound.seriesTotales { seriesTotales = n }
        if inbound.aprobacionesIncluidas {
            let previas = Set(aprobaciones.map(\.id))
            let nuevas = inbound.aprobaciones.filter { !previas.contains($0.id) }
            aprobaciones = inbound.aprobaciones
            for item in nuevas { avisarAprobacion(item) }
        }
        if inbound.misionesIncluidas {
            let previas = Set(misiones.map(\.id))
            let nuevas = inbound.misiones.filter {
                $0.status == "waiting_confirmation" && !previas.contains($0.id)
            }
            misiones = inbound.misiones
            for m in nuevas { avisarMision(m) }
        }
        if let v = inbound.enLlamada { enLlamada = v }
        if let v = inbound.nombreLlamada { nombreLlamada = v.isEmpty ? nil : v }
        if let v = inbound.idLlamada { idLlamada = v.isEmpty ? nil : v }
        if let n = inbound.rutinas { rutinasActivas = n }
        if inbound.rutinasIncluidas { rutinas = inbound.listaRutinas }
        if inbound.equipoIncluido { equipo = inbound.equipo }
        if let r = inbound.respuesta, !r.isEmpty { ultimaRespuesta = r }
        if let n = inbound.pesoSugerido { pesoSugerido = n > 0 ? n : nil }
        if let n = inbound.repsSugeridas { repsSugeridas = n > 0 ? n : nil }
        if let t = inbound.turno { turnoLlamada = t.isEmpty ? nil : t }
        if let r = inbound.turnoRol { turnoRol = r.isEmpty ? nil : r }
        if let ml = inbound.sumarAgua, ml > 0 {
            AguaStore.compartido.registrar(ml, enSalud: inbound.aguaEnSalud != true)
        }

        WatchEntrenamiento.compartido.alinear(activo: sesionActiva, pausada: pausada, cronometro: cronometro)
        WatchSnapshotStore.guardarAviso(mensaje: proximoAviso?.mensaje, vence: proximoAviso?.vence)

        if sesionActiva, !estabaActiva, inbound.activa == true {
            NotificationCenter.default.post(name: .edecanWatchMostrarGymLive, object: nil)
        }

        let anterior = descansoRestante ?? 0
        if let descanso = inbound.descanso { descansoRestante = descanso }
        if let ejercicioDescanso = inbound.ejercicioDescanso {
            self.descansoEjercicio = ejercicioDescanso
        }
        let actual = descansoRestante ?? 0
        if anterior <= 0 && actual > 0 {
            descansoFin = Date.now.addingTimeInterval(TimeInterval(actual))
        }
        if inbound.descansoTerminado == true || (anterior > 0 && actual == 0 && inbound.descanso != nil) {
            descansoRestante = 0
            descansoEjercicio = nil
            descansoFin = nil
            WKInterfaceDevice.current().play(.notification)
            avisarSerieLista()
        }
    }

    private func avisarSerieLista() {
        let contenido = UNMutableNotificationContent()
        contenido.title = "Descanso listo"
        contenido.body = ejercicioActual.map { "Siguiente: \($0). Marca la serie." } ?? "Marca la serie."
        contenido.sound = .default
        contenido.categoryIdentifier = "EDECAN_SERIE"
        UNUserNotificationCenter.current().add(
            UNNotificationRequest(
                identifier: "gym-serie",
                content: contenido,
                trigger: UNTimeIntervalNotificationTrigger(timeInterval: 1, repeats: false)
            )
        )
    }

    private func avisarAprobacion(_ item: WatchAprobacion) {
        let contenido = UNMutableNotificationContent()
        contenido.title = "Edecán pide tu sí"
        contenido.body = item.nombre
        contenido.sound = .default
        contenido.categoryIdentifier = "EDECAN_APROBAR"
        contenido.userInfo = ["approvalId": item.id]
        UNUserNotificationCenter.current().add(
            UNNotificationRequest(
                identifier: "aprobar-\(item.id)",
                content: contenido,
                trigger: UNTimeIntervalNotificationTrigger(timeInterval: 1, repeats: false)
            )
        )
    }

    private func avisarMision(_ mision: WatchMision) {
        let contenido = UNMutableNotificationContent()
        contenido.title = "Misión espera tu sí"
        contenido.body = mision.objetivo
        contenido.sound = .default
        contenido.categoryIdentifier = "EDECAN_MISION"
        contenido.userInfo = ["missionId": mision.id]
        UNUserNotificationCenter.current().add(
            UNNotificationRequest(
                identifier: "mision-\(mision.id)",
                content: contenido,
                trigger: UNTimeIntervalNotificationTrigger(timeInterval: 1, repeats: false)
            )
        )
    }

    func alternar() { enviar(["comando": "alternar"]) }
    func terminarGym() { enviar(["comando": "terminarGym"]) }

    /// Respuesta a la push «¿Vas a entrenar hoy?» tocada en el reloj.
    /// Arranca HK al instante y avisa al iPhone para persistir check-in + sesión.
    func confirmarCheckin(respuesta: String) {
        if respuesta == "si" {
            sesionActiva = true
            pausada = false
            tienePlan = true
            if cronometro == nil { cronometro = 0 }
            WatchEntrenamiento.compartido.alinear(activo: true, pausada: false, cronometro: cronometro)
            WKInterfaceDevice.current().play(.start)
            NotificationCenter.default.post(name: .edecanWatchMostrarGymLive, object: nil)
            enviar(["comando": "checkinSi"])
        } else {
            enviar(["comando": "checkinNo"])
        }
    }

    func completarAviso(_ id: String) {
        avisos.removeAll { $0.id == id }
        enviar(["comando": "completarRecordatorio", "id": id])
    }
    func crearAvisoEnIPhone(mensaje: String, minutos: Int) {
        enviar(["comando": "crearRecordatorio", "mensaje": mensaje, "minutos": minutos])
    }
    func hablar(_ texto: String) {
        enviar(["comando": "hablar", "mensaje": texto])
        WKInterfaceDevice.current().play(.success)
    }
    func decidirAprobacion(id: String, ok: Bool) {
        aprobaciones.removeAll { $0.id == id }
        enviar(["comando": "aprobacion", "id": id, "ok": ok])
    }
    func susurrar(_ texto: String) {
        guard let idLlamada else { return }
        enviar(["comando": "susurrar", "id": idLlamada, "mensaje": texto])
    }
    func registrarSerie(reps: Int? = nil, peso: Double? = nil) {
        var payload: [String: Any] = ["comando": "registrarSerie"]
        if let reps { payload["entero"] = reps }
        if let peso { payload["peso"] = peso }
        enviar(payload)
        WKInterfaceDevice.current().play(.click)
    }
    func saltarDescanso() {
        descansoRestante = 0
        descansoEjercicio = nil
        descansoFin = nil
        enviar(["comando": "saltarDescanso"])
    }
    func confirmarMision(id: String, ok: Bool) {
        enviar(["comando": "confirmarMision", "id": id, "ok": ok])
    }
    func cancelarMision(_ id: String) {
        misiones.removeAll { $0.id == id }
        enviar(["comando": "cancelarMision", "id": id])
    }
    func pausarMision(_ id: String) {
        enviar(["comando": "pausarMision", "id": id])
    }
    func reanudarMision(_ id: String) {
        enviar(["comando": "reanudarMision", "id": id])
    }
    func dirigirMision(id: String, texto: String) {
        enviar(["comando": "steering", "id": id, "mensaje": texto])
    }
    func crearMision(_ objetivo: String) {
        enviar(["comando": "crearMision", "mensaje": objetivo])
    }
    func toggleRutina(id: String, enabled: Bool) {
        if let i = rutinas.firstIndex(where: { $0.id == id }) {
            rutinas[i] = WatchRutina(id: id, nombre: rutinas[i].nombre, enabled: enabled)
        }
        enviar(["comando": "toggleRutina", "id": id, "ok": enabled])
    }
    func deshacer() {
        enviar(["comando": "deshacer"])
    }

    private func enviar(_ payload: [String: Any]) {
        guard sesion.activationState == .activated else { return }
        if sesion.isReachable {
            sesion.sendMessage(payload, replyHandler: { _ in }, errorHandler: { [weak self] _ in
                self?.sesion.transferUserInfo(payload)
            })
        } else {
            sesion.transferUserInfo(payload)
        }
    }
}

extension Notification.Name {
    /// El Hub abre la pantalla viva de gym cuando la sesión arranca (push Sí o sync).
    static let edecanWatchMostrarGymLive = Notification.Name("cc.edecan.watch.mostrar-gym-live")
}
