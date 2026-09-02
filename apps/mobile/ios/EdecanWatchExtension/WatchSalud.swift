import Foundation
import HealthKit
import Observation

/// Salud del Apple Watch: lee **este** reloj (pasos, movimiento, ejercicio,
/// pie, sueño, ritmo, distancia). Nada inventado: si Salud no da número, queda vacío.
@MainActor
@Observable
final class WatchSalud {
    static let compartido = WatchSalud()

    var pasos: Int?
    var kcal: Double?
    var minutosEjercicio: Int?
    var horasPie: Int?
    var distanciaKm: Double?
    var pisos: Int?
    var bpm: Int?
    var suenoHoras: Double?
    var aguaMl: Int?
    var hrv: Double?
    var reposo: Int?
    var oxigeno: Int?
    var listo = false
    var sinPermiso = false

    var metaPasos = 8_000
    var metaKcal = 400.0
    var metaEjercicio = 30
    var metaPie = 12

    var progresoPasos: Double { fraccion(pasos, metaPasos) }
    var progresoKcal: Double { fraccion(kcal, metaKcal) }
    var progresoEjercicio: Double { fraccion(minutosEjercicio, metaEjercicio) }
    var progresoPie: Double { fraccion(horasPie, metaPie) }

    private let store = HKHealthStore()
    private var loop: Task<Void, Never>?

    func arrancar() async {
        await pedirPermiso()
        await refrescar()
        loop?.cancel()
        loop = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(25))
                await self?.refrescar()
            }
        }
    }

    func refrescar() async {
        guard HKHealthStore.isHealthDataAvailable(), !sinPermiso else { return }
        let hoy = Calendar.current.startOfDay(for: Date())
        async let p = suma(.stepCount, unidad: .count(), desde: hoy)
        async let k = suma(.activeEnergyBurned, unidad: .kilocalorie(), desde: hoy)
        async let e = suma(.appleExerciseTime, unidad: .minute(), desde: hoy)
        async let pie = suma(.appleStandTime, unidad: .hour(), desde: hoy)
        async let d = suma(.distanceWalkingRunning, unidad: .meter(), desde: hoy)
        async let f = suma(.flightsClimbed, unidad: .count(), desde: hoy)
        async let agua = suma(.dietaryWater, unidad: .literUnit(with: .milli), desde: hoy)
        async let ritmo = ultimoRitmo()
        async let sueno = suenoNoche()
        async let h = ultimo(.heartRateVariabilitySDNN, unidad: HKUnit.secondUnit(with: .milli))
        async let r = ultimo(.restingHeartRate, unidad: HKUnit.count().unitDivided(by: .minute()))
        async let o = ultimo(.oxygenSaturation, unidad: .percent())

        if let v = await p { pasos = Int(v.rounded()) }
        if let v = await k { kcal = v }
        if let v = await e { minutosEjercicio = Int(v.rounded()) }
        if let v = await pie { horasPie = Int(v.rounded()) }
        if let v = await d { distanciaKm = v / 1000 }
        if let v = await f { pisos = Int(v.rounded()) }
        if let v = await agua { aguaMl = Int(v.rounded()) }
        if let v = await ritmo { bpm = Int(v.rounded()) }
        suenoHoras = await sueno
        if let v = await h { hrv = v }
        if let v = await r { reposo = Int(v.rounded()) }
        if let v = await o { oxigeno = Int((v * 100).rounded()) }
        listo = true
        WatchSnapshotStore.guardarPasos(pasos ?? 0, meta: metaPasos)
    }

    /// Texto del día: salud real + lo que llegó de Edecán. Frases concretas.
    func resumenDelDia(manager: WatchSessionManager, agua: AguaStore) -> String {
        var partes: [String] = []

        if let pasos {
            let pct = Int((Double(pasos) / Double(metaPasos) * 100).rounded())
            partes.append("Llevas \(pasos.formatted()) pasos (\(min(pct, 999))% de \(metaPasos.formatted())).")
        } else {
            partes.append("Aún no hay pasos de hoy en Salud — el reloj los suma al caminar con permiso.")
        }

        if let kcal, let minutosEjercicio, let horasPie {
            partes.append("Movimiento \(Int(kcal.rounded())) kcal, \(minutosEjercicio) min de ejercicio, \(horasPie) h de pie.")
        } else if let kcal {
            partes.append("Has quemado \(Int(kcal.rounded())) kcal activas.")
        }

        if let distanciaKm, distanciaKm >= 0.1 {
            partes.append(String(format: "%.1f km caminando.", distanciaKm))
        }
        if let pisos, pisos > 0 {
            partes.append("\(pisos) pisos.")
        }
        if let bpm {
            partes.append("Ritmo ahora \(bpm) lpm.")
        }
        if let suenoHoras {
            if suenoHoras < 6 {
                partes.append(String(format: "Dormiste %.1f h: corto, hoy suave.", suenoHoras))
            } else if suenoHoras >= 7 {
                partes.append(String(format: "Dormiste %.1f h: bien recuperado.", suenoHoras))
            } else {
                partes.append(String(format: "Dormiste %.1f h.", suenoHoras))
            }
        }
        if let hrv {
            partes.append(String(format: "HRV %.0f ms.", hrv))
        }
        if let reposo {
            partes.append("Reposo \(reposo) lpm.")
        }
        let ml = max(agua.mlHoy, aguaMl ?? 0)
        partes.append(String(format: "Agua %.1f L de %.1f L.", Double(ml) / 1000, Double(agua.metaMl) / 1000))

        if manager.enLlamada {
            partes.append("Edecán está en una llamada.")
        }
        if !manager.aprobaciones.isEmpty {
            partes.append("\(manager.aprobaciones.count) aprobación(es) pendiente(s).")
        }
        if manager.misionesActivas > 0 {
            partes.append("\(manager.misionesActivas) misión(es) en curso.")
        }
        if manager.sesionActiva {
            if let ej = manager.ejercicioActual {
                partes.append("Entrenando: \(ej) (\(manager.seriesHechas)/\(manager.seriesTotales)).")
            } else {
                partes.append("Sesión de gym en curso: \(manager.tituloPlan ?? "entrenamiento").")
            }
        } else if manager.tienePlan, let titulo = manager.tituloPlan {
            partes.append("Plan de hoy: \(titulo).")
        }
        if let aviso = manager.proximoAviso {
            partes.append("Siguiente aviso: \(aviso.mensaje).")
        }
        if manager.racha > 0 {
            partes.append("Racha de gym: \(manager.racha) semana(s).")
        }
        return partes.joined(separator: " ")
    }

    private func pedirPermiso() async {
        guard HKHealthStore.isHealthDataAvailable() else {
            sinPermiso = true
            return
        }
        var lectura: Set<HKObjectType> = [
            HKQuantityType(.stepCount),
            HKQuantityType(.activeEnergyBurned),
            HKQuantityType(.appleExerciseTime),
            HKQuantityType(.appleStandTime),
            HKQuantityType(.distanceWalkingRunning),
            HKQuantityType(.flightsClimbed),
            HKQuantityType(.heartRate),
            HKQuantityType(.dietaryWater),
            HKCategoryType(.sleepAnalysis),
        ]
        if let hrv = HKQuantityType.quantityType(forIdentifier: .heartRateVariabilitySDNN) {
            lectura.insert(hrv)
        }
        if let rest = HKQuantityType.quantityType(forIdentifier: .restingHeartRate) {
            lectura.insert(rest)
        }
        if let ox = HKQuantityType.quantityType(forIdentifier: .oxygenSaturation) {
            lectura.insert(ox)
        }
        lectura.insert(HKObjectType.workoutType())
        var escritura: Set<HKSampleType> = [
            HKQuantityType(.dietaryWater),
            HKObjectType.workoutType(),
        ]
        if let mindful = HKObjectType.categoryType(forIdentifier: .mindfulSession) {
            escritura.insert(mindful)
            lectura.insert(mindful)
        }
        do {
            try await store.requestAuthorization(toShare: escritura, read: lectura)
        } catch {
            sinPermiso = true
        }
    }

    private func ultimo(_ id: HKQuantityTypeIdentifier, unidad: HKUnit) async -> Double? {
        guard let tipo = HKQuantityType.quantityType(forIdentifier: id) else { return nil }
        let pred = HKQuery.predicateForSamples(
            withStart: Date().addingTimeInterval(-24 * 60 * 60),
            end: Date()
        )
        let orden = NSSortDescriptor(key: HKSampleSortIdentifierEndDate, ascending: false)
        return await withCheckedContinuation { cont in
            let q = HKSampleQuery(sampleType: tipo, predicate: pred, limit: 1, sortDescriptors: [orden]) { _, muestras, _ in
                let muestra = muestras?.first as? HKQuantitySample
                cont.resume(returning: muestra?.quantity.doubleValue(for: unidad))
            }
            store.execute(q)
        }
    }

    func guardarMindful(desde inicio: Date, hasta fin: Date) async {
        guard let tipo = HKObjectType.categoryType(forIdentifier: .mindfulSession) else { return }
        let muestra = HKCategorySample(type: tipo, value: 0, start: inicio, end: fin)
        try? await store.save(muestra)
    }

    private func suma(_ id: HKQuantityTypeIdentifier, unidad: HKUnit, desde: Date) async -> Double? {
        guard let tipo = HKQuantityType.quantityType(forIdentifier: id) else { return nil }
        let pred = HKQuery.predicateForSamples(withStart: desde, end: Date())
        return await withCheckedContinuation { cont in
            let q = HKStatisticsQuery(quantityType: tipo, quantitySamplePredicate: pred, options: .cumulativeSum) { _, stats, _ in
                let v = stats?.sumQuantity()?.doubleValue(for: unidad)
                cont.resume(returning: v)
            }
            store.execute(q)
        }
    }

    private func ultimoRitmo() async -> Double? {
        let tipo = HKQuantityType(.heartRate)
        let pred = HKQuery.predicateForSamples(
            withStart: Date().addingTimeInterval(-15 * 60),
            end: Date()
        )
        let orden = NSSortDescriptor(key: HKSampleSortIdentifierEndDate, ascending: false)
        return await withCheckedContinuation { cont in
            let q = HKSampleQuery(sampleType: tipo, predicate: pred, limit: 1, sortDescriptors: [orden]) { _, muestras, _ in
                let muestra = muestras?.first as? HKQuantitySample
                let bpm = muestra?.quantity.doubleValue(for: HKUnit.count().unitDivided(by: .minute()))
                cont.resume(returning: bpm)
            }
            store.execute(q)
        }
    }

    private func suenoNoche() async -> Double? {
        let cal = Calendar.current
        let hoy = cal.startOfDay(for: Date())
        guard let inicio = cal.date(byAdding: .hour, value: -12, to: hoy) else { return nil }
        let tipo = HKCategoryType(.sleepAnalysis)
        let pred = HKQuery.predicateForSamples(withStart: inicio, end: Date())
        return await withCheckedContinuation { cont in
            let q = HKSampleQuery(sampleType: tipo, predicate: pred, limit: HKObjectQueryNoLimit, sortDescriptors: nil) { _, muestras, _ in
                let dormido = muestras?.compactMap { $0 as? HKCategorySample }.filter { muestra in
                    let v = HKCategoryValueSleepAnalysis(rawValue: muestra.value)
                    switch v {
                    case .asleepCore, .asleepDeep, .asleepREM, .asleepUnspecified:
                        return true
                    case .inBed, .awake, .none:
                        return false
                    @unknown default:
                        return muestra.value >= HKCategoryValueSleepAnalysis.asleepUnspecified.rawValue
                    }
                } ?? []
                let segundos = dormido.reduce(0.0) { $0 + $1.endDate.timeIntervalSince($1.startDate) }
                if segundos > 0 {
                    cont.resume(returning: segundos / 3600)
                    return
                }
                let cama = muestras?.compactMap { $0 as? HKCategorySample }.filter {
                    HKCategoryValueSleepAnalysis(rawValue: $0.value) == .inBed
                } ?? []
                let camaSeg = cama.reduce(0.0) { $0 + $1.endDate.timeIntervalSince($1.startDate) }
                cont.resume(returning: camaSeg > 0 ? camaSeg / 3600 : nil)
            }
            store.execute(q)
        }
    }

    private func fraccion(_ valor: Int?, _ meta: Int) -> Double {
        guard let valor, meta > 0 else { return 0 }
        return min(1, Double(valor) / Double(meta))
    }

    private func fraccion(_ valor: Double?, _ meta: Double) -> Double {
        guard let valor, meta > 0 else { return 0 }
        return min(1, valor / meta)
    }
}
