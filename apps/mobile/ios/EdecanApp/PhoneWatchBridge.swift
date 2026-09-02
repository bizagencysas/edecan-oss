import Foundation
import WatchConnectivity

/// Comando que llega del Apple Watch. El iPhone es el cerebro (API + HealthKit);
/// el reloj solo dispara acciones y pinta el tablero.
struct WatchComando: Sendable {
    let nombre: String
    let id: String?
    let mensaje: String?
    let minutos: Int?
    let entero: Int?
    let ok: Bool?
    let bpm: Double?
    let kcal: Double?
    let peso: Double?
}

/// Puente iPhone ↔ Apple Watch vía ``WatchConnectivity``.
///
/// Publica un tablero (gym, racha, recordatorios, misiones) por
/// `updateApplicationContext` siempre, y por `sendMessage` cuando el reloj
/// está reachable. Recibe comandos con o sin `replyHandler` (WatchConnectivity
/// elige el callback según cómo mandó el reloj).
@MainActor
final class PhoneWatchBridge: NSObject, WCSessionDelegate {
    static let compartido = PhoneWatchBridge()

    var onComando: ((WatchComando) -> Void)?

    /// Se conserva para no romper ``GymViewModel``; reenvía a ``onComando``.
    var onComandoAlternar: (() -> Void)? {
        didSet {
            if onComandoAlternar != nil, onComando == nil {
                onComando = { [weak self] cmd in
                    if cmd.nombre == "alternar" { self?.onComandoAlternar?() }
                }
            }
        }
    }

    private var sesion: WCSession?
    private var contexto: [String: Any] = [:]

    override private init() {
        super.init()
        guard WCSession.isSupported() else { return }
        let activa = WCSession.default
        sesion = activa
        activa.delegate = self
        activa.activate()
    }

    var relojAlcanzable: Bool { sesion?.isReachable == true }

    func activarSiHaceFalta() {
        guard WCSession.isSupported(), sesion == nil else { return }
        let activa = WCSession.default
        sesion = activa
        activa.delegate = self
        activa.activate()
    }

    // MARK: iPhone → Watch

    func enviar(
        sesionActiva: Bool,
        titulo: String?,
        cronometro: TimeInterval?,
        frecuenciaCardiaca: Double?,
        calorias: Double? = nil,
        tienePlan: Bool? = nil,
    ) {
        var payload: [String: Any] = ["sesionActiva": sesionActiva]
        if let tienePlan { payload["tienePlan"] = tienePlan }
        if let titulo, !titulo.isEmpty { payload["tituloPlan"] = titulo }
        if tienePlan == false { payload["tituloPlan"] = "" }
        if let cronometro { payload["cronometro"] = cronometro }
        if let frecuenciaCardiaca { payload["frecuenciaCardiaca"] = frecuenciaCardiaca }
        if let calorias { payload["calorias"] = calorias }
        publicar(payload)
    }

    func enviar(titulo: String?, frecuenciaCardiaca: Double?, calorias: Double?) {
        var payload: [String: Any] = ["tipo": "metricas"]
        if let titulo { payload["tituloPlan"] = titulo }
        if let frecuenciaCardiaca { payload["frecuenciaCardiaca"] = frecuenciaCardiaca }
        if let calorias { payload["calorias"] = calorias }
        publicar(payload)
    }

    func enviarDescanso(restante: Int?, ejercicio: String?) {
        var payload: [String: Any] = ["tipo": "descanso"]
        payload["descansoRestante"] = max(0, restante ?? 0)
        if let ejercicio { payload["descansoEjercicio"] = ejercicio }
        if max(0, restante ?? 0) == 0 { payload["descansoTerminado"] = true }
        publicar(payload)
        contexto.removeValue(forKey: "descansoTerminado")
    }

    func enviarTablero(_ payload: [String: Any]) {
        var cuerpo = payload
        cuerpo["tipo"] = "tablero"
        publicar(cuerpo)
    }

    private func publicar(_ delta: [String: Any]) {
        for (clave, valor) in delta {
            contexto[clave] = valor
        }
        guard let sesion, sesion.activationState == .activated else { return }
        try? sesion.updateApplicationContext(contexto)
        if sesion.isReachable {
            let bloque: @Sendable (Error) -> Void = { _ in }
            sesion.sendMessage(delta, replyHandler: nil, errorHandler: bloque)
        }
    }

    // MARK: Watch → iPhone

    nonisolated func session(_ session: WCSession, didReceiveMessage message: [String: Any]) {
        recibir(message)
    }

    nonisolated func session(
        _ session: WCSession,
        didReceiveMessage message: [String: Any],
        replyHandler: @escaping ([String: Any]) -> Void,
    ) {
        recibir(message)
        replyHandler(["ok": true])
    }

    private nonisolated func recibir(_ message: [String: Any]) {
        let nombre = message["comando"] as? String
        let id = message["id"] as? String
        let texto = message["mensaje"] as? String
        let minutos = (message["minutos"] as? Int) ?? (message["minutos"] as? NSNumber)?.intValue
        let entero = (message["entero"] as? Int) ?? (message["entero"] as? NSNumber)?.intValue
        let ok = (message["ok"] as? Bool) ?? (message["ok"] as? NSNumber)?.boolValue
        let bpm = (message["bpm"] as? Double) ?? (message["bpm"] as? NSNumber)?.doubleValue
        let kcal = (message["kcal"] as? Double) ?? (message["kcal"] as? NSNumber)?.doubleValue
        let peso = (message["peso"] as? Double) ?? (message["peso"] as? NSNumber)?.doubleValue
        guard let nombre else { return }
        Task { @MainActor [weak self] in
            let comando = WatchComando(
                nombre: nombre, id: id, mensaje: texto, minutos: minutos, entero: entero, ok: ok,
                bpm: bpm, kcal: kcal, peso: peso
            )
            if let handler = self?.onComando {
                handler(comando)
            } else if comando.nombre == "alternar" {
                self?.onComandoAlternar?()
            }
        }
    }

    // MARK: WCSessionDelegate

    nonisolated func session(
        _ session: WCSession,
        activationDidCompleteWith activationState: WCSessionActivationState,
        error: Error?,
    ) {}

    nonisolated func sessionDidBecomeInactive(_ session: WCSession) {}

    nonisolated func sessionDidDeactivate(_ session: WCSession) {
        session.activate()
    }

    nonisolated func session(_ session: WCSession, didReceiveUserInfo userInfo: [String: Any] = [:]) {
        recibir(userInfo)
    }

    nonisolated func sessionReachabilityDidChange(_ session: WCSession) {
        let reachable = session.isReachable
        Task { @MainActor [weak self] in
            guard let self, reachable, !self.contexto.isEmpty else { return }
            self.publicar(self.contexto)
        }
    }
}
