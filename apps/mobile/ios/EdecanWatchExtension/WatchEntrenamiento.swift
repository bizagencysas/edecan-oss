import Foundation
import HealthKit
import Observation
import WatchConnectivity

/// El reloj es quien debe medir el gym: `HKWorkoutSession` + builder en vivo.
/// El iPhone no abre otro workout si este reloj está reachable.
@MainActor
@Observable
final class WatchEntrenamiento: NSObject, HKWorkoutSessionDelegate, HKLiveWorkoutBuilderDelegate {
    static let compartido = WatchEntrenamiento()

    var bpm: Double?
    var kcal: Double?
    var activo = false

    private let store = HKHealthStore()
    @ObservationIgnored private var sesion: HKWorkoutSession?
    @ObservationIgnored private var builder: HKLiveWorkoutBuilder?
    @ObservationIgnored private var ultimoEnvio = Date.distantPast

    func alinear(activo: Bool, pausada: Bool, cronometro: TimeInterval? = nil) {
        if activo {
            arrancarSiHaceFalta(cronometro: cronometro)
            sesion?.resume()
        } else if pausada {
            arrancarSiHaceFalta(cronometro: cronometro)
            sesion?.pause()
        } else if sesion != nil {
            Task { await terminar() }
        }
    }

    func arrancarSiHaceFalta(cronometro: TimeInterval? = nil) {
        guard sesion == nil, HKHealthStore.isHealthDataAvailable() else { return }
        let config = HKWorkoutConfiguration()
        config.activityType = .traditionalStrengthTraining
        config.locationType = .indoor
        guard let nueva = try? HKWorkoutSession(healthStore: store, configuration: config) else { return }
        let vivo = nueva.associatedWorkoutBuilder()
        vivo.dataSource = HKLiveWorkoutDataSource(healthStore: store, workoutConfiguration: config)
        vivo.dataSource?.enableCollection(for: HKQuantityType(.heartRate), predicate: nil)
        vivo.dataSource?.enableCollection(for: HKQuantityType(.activeEnergyBurned), predicate: nil)
        nueva.delegate = self
        vivo.delegate = self
        sesion = nueva
        builder = vivo
        // Inicio REAL: si el reloj despierta a mitad de la sesión, el
        // cronómetro del backend dice cuánto lleva — el workout de Salud
        // arranca en el instante verdadero, no en el del despertar.
        let inicio = Date().addingTimeInterval(-(cronometro ?? 0))
        nueva.startActivity(with: inicio)
        vivo.beginCollection(withStart: inicio) { [weak self] _, _ in
            Task { @MainActor [weak self] in
                self?.activo = true
            }
        }
    }

    func terminar() async {
        guard let sesion, let builder else {
            limpiar()
            return
        }
        let fin = Date()
        sesion.end()
        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            builder.endCollection(withEnd: fin) { _, _ in
                builder.finishWorkout { _, _ in
                    cont.resume()
                }
            }
        }
        limpiar()
    }

    private func limpiar() {
        sesion = nil
        builder = nil
        bpm = nil
        kcal = nil
        activo = false
    }

    private func empujarAlIPhone() {
        guard Date().timeIntervalSince(ultimoEnvio) >= 5 else { return }
        ultimoEnvio = Date()
        var payload: [String: Any] = ["comando": "metricasGym"]
        if let bpm { payload["bpm"] = bpm }
        if let kcal { payload["kcal"] = kcal }
        let wc = WCSession.default
        guard wc.activationState == .activated else { return }
        if wc.isReachable {
            wc.sendMessage(payload, replyHandler: { _ in }, errorHandler: { _ in
                wc.transferUserInfo(payload)
            })
        } else {
            wc.transferUserInfo(payload)
        }
    }

    nonisolated func workoutBuilder(
        _ workoutBuilder: HKLiveWorkoutBuilder,
        didCollectDataOf collectedTypes: Set<HKSampleType>,
    ) {
        let frecuencia = workoutBuilder.statistics(for: HKQuantityType(.heartRate))?
            .mostRecentQuantity()?.doubleValue(for: HKUnit.count().unitDivided(by: .minute()))
        let calorias = workoutBuilder.statistics(for: HKQuantityType(.activeEnergyBurned))?
            .sumQuantity()?.doubleValue(for: .kilocalorie())
        Task { @MainActor [weak self] in
            self?.bpm = frecuencia
            if let calorias { self?.kcal = calorias }
            self?.empujarAlIPhone()
        }
    }

    nonisolated func workoutBuilderDidCollectEvent(_ workoutBuilder: HKLiveWorkoutBuilder) {}

    nonisolated func workoutSession(
        _ workoutSession: HKWorkoutSession,
        didChangeTo toState: HKWorkoutSessionState,
        from fromState: HKWorkoutSessionState,
        date: Date,
    ) {}

    nonisolated func workoutSession(_ workoutSession: HKWorkoutSession, didFailWithError error: Error) {
        Task { @MainActor [weak self] in
            self?.limpiar()
        }
    }
}
