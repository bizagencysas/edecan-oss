import Foundation
import HealthKit
import Observation

/// Puente de HealthKit del feature gimnasio: pide autorización, lee los
/// workouts del día y guarda un `HKWorkout` al terminar la sesión.
///
/// Diseño deliberado:
/// - Si HealthKit no está disponible (o el usuario lo niega), la app sigue
///   funcionando exactamente igual: cada método es best-effort y no lanza.
/// - NUNCA se inventan datos de salud: si HealthKit no devuelve nada, se
///   devuelve vacío — no un número por defecto.
@MainActor
@Observable
final class HealthKitManager {
    private let store = HKHealthStore()

    /// `true` cuando el usuario ya autorizó lectura/escritura. Se usa solo
    /// para decidir si vale la pena leer/guardar; las lecturas degradan a
    /// vacío y las escrituras se omiten si no hay permiso.
    private(set) var autorizado = false
    /// `true` cuando este dispositivo no tiene HealthKit disponible en absoluto.
    private(set) var noDisponible = false

    private let tiposLectura: Set<HKObjectType> = [
        HKObjectType.workoutType(),
        HKQuantityType(.stepCount),
        HKQuantityType(.activeEnergyBurned),
    ]
    private let tiposEscritura: Set<HKSampleType> = [
        HKObjectType.workoutType(),
    ]

    /// Pide permiso de lectura/escritura. Nunca lanza: sin HealthKit o con
    /// rechazo, deja `noDisponible = true` y la app sigue sin él.
    func solicitarAutorizacion() async {
        guard HKHealthStore.isHealthDataAvailable() else {
            noDisponible = true
            return
        }
        do {
            try await store.requestAuthorization(toShare: tiposEscritura, read: tiposLectura)
            autorizado = true
        } catch {
            // Negado o error del sistema: la app sigue sin HealthKit.
            noDisponible = true
        }
    }

    /// Los workouts de hoy. Si no hay HealthKit, no hay permisos o no hay
    /// datos, devuelve `[]` — nunca un valor inventado.
    func workoutsDeHoy() async -> [HKWorkout] {
        guard HKHealthStore.isHealthDataAvailable(), autorizado else { return [] }
        let inicio = Calendar.current.startOfDay(for: Date())
        return await consultarWorkouts(desde: inicio, hasta: Date())
    }

    /// Guarda un workout de fuerza al terminar la sesión. Sin muestras de
    /// energía: el builder termina con cero calorías (no se inventan). Si no
    /// hay HealthKit, no está autorizado o el intervalo es inválido, no hace
    /// nada y no bloquea el cierre de la sesión en el backend.
    func guardarWorkout(inicio: Date, fin: Date) async {
        guard HKHealthStore.isHealthDataAvailable(), autorizado, fin > inicio else { return }
        let configuracion = HKWorkoutConfiguration()
        configuracion.activityType = .traditionalStrengthTraining
        configuracion.locationType = .indoor

        let builder = HKWorkoutBuilder(healthStore: store, configuration: configuracion, device: .local())
        do {
            try await builder.beginCollection(at: inicio)
            try await builder.endCollection(at: fin)
            _ = try await builder.finishWorkout()
        } catch {
            // Best-effort: un fallo de escritura jamás bloquea el cierre.
        }
    }

    private func consultarWorkouts(desde inicio: Date, hasta fin: Date) async -> [HKWorkout] {
        await withCheckedContinuation { continuacion in
            let predicado = HKQuery.predicateForSamples(
                withStart: inicio, end: fin, options: .strictStartDate
            )
            let orden = NSSortDescriptor(key: HKSampleSortIdentifierStartDate, ascending: false)
            let query = HKSampleQuery(
                sampleType: HKObjectType.workoutType(),
                predicate: predicado,
                limit: HKObjectQueryNoLimit,
                sortDescriptors: [orden]
            ) { _, muestras, _ in
                continuacion.resume(returning: (muestras as? [HKWorkout]) ?? [])
            }
            store.execute(query)
        }
    }
}