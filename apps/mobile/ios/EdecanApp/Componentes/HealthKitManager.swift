import Foundation
import HealthKit
import Observation

/// Puente de HealthKit del feature gimnasio: pide autorización, hace
/// SEGUIMIENTO EN VIVO durante la sesión (frecuencia cardíaca + calorías
/// activas vía `HKLiveWorkoutBuilder`) y guarda el `HKWorkout` con esas
/// muestras al terminar. Además lee los workouts del día.
///
/// Diseño deliberado:
/// - Si HealthKit no está disponible (o el usuario lo niega), la app sigue
///   funcionando exactamente igual: cada método es best-effort y no lanza.
/// - NUNCA se inventan datos de salud: si HealthKit no devuelve nada, se
///   devuelve vacío — no un número por defecto.
@MainActor
@Observable
final class HealthKitManager: NSObject, HKLiveWorkoutBuilderDelegate, HKWorkoutSessionDelegate {
    static let compartido = HealthKitManager()

    private let store = HKHealthStore()

    /// `true` cuando el usuario ya autorizó lectura/escritura. Se usa solo
    /// para decidir si vale la pena leer/guardar; las lecturas degradan a
    /// vacío y las escrituras se omiten si no hay permiso.
    private(set) var autorizado = false
    /// `true` cuando este dispositivo no tiene HealthKit disponible en absoluto.
    private(set) var noDisponible = false

    // MARK: Métricas en vivo (publicadas a la UI)

    /// Última frecuencia cardíaca en bpm, o `nil` si HealthKit aún no reporta.
    private(set) var frecuenciaCardiaca: Double?
    /// Calorías activas acumuladas en kcal durante la sesión en curso.
    private(set) var caloriasActivas: Double?
    /// `true` mientras el seguimiento en vivo está corriendo.
    private(set) var siguiendo = false

    @ObservationIgnored private var builder: HKLiveWorkoutBuilder?
    @ObservationIgnored private var sesionHealth: HKWorkoutSession?
    @ObservationIgnored private var inicio: Date?

    private let tiposLectura: Set<HKObjectType> = [
        HKObjectType.workoutType(),
        HKQuantityType(.stepCount),
        HKQuantityType(.activeEnergyBurned),
        HKQuantityType(.heartRate),
        HKQuantityType(.bodyMass),
        HKQuantityType(.heartRateVariabilitySDNN),
        HKQuantityType(.dietaryWater),
        HKCategoryType(.sleepAnalysis),
    ]
    private let tiposEscritura: Set<HKSampleType> = [
        HKObjectType.workoutType(),
        HKQuantityType(.dietaryWater),
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

    // MARK: Lecturas de bienestar (best-effort)

    /// El peso corporal (kg) más reciente registrado en HealthKit. Best-effort:
    /// `nil` si HealthKit no está disponible, no hay permiso o no hay datos.
    /// NUNCA devuelve un peso inventado.
    func pesoReciente() async -> Double? {
        guard HKHealthStore.isHealthDataAvailable(), autorizado else { return nil }
        let muestras = await consultarMuestras(
            HKQuantityType(.bodyMass),
            desde: Date.distantPast,
            hasta: Date(),
            limite: 1
        )
        guard let muestra = muestras.first as? HKQuantitySample else { return nil }
        return muestra.quantity.doubleValue(for: .gramUnit(with: .kilo))
    }

    /// Serie de pesos corporales (kg) de los últimos 90 días, en orden
    /// cronológico (el más antiguo primero). Best-effort: `[]` si HealthKit
    /// no está disponible, no hay permiso o no hay datos. NUNCA inventa pesos.
    func pesoHistorico() async -> [(fecha: Date, kg: Double)] {
        guard HKHealthStore.isHealthDataAvailable(), autorizado else { return [] }
        let fin = Date()
        guard let inicio = Calendar.current.date(byAdding: .day, value: -90, to: fin) else { return [] }
        let muestras = await consultarMuestras(
            HKQuantityType(.bodyMass),
            desde: inicio,
            hasta: fin,
            limite: HKObjectQueryNoLimit
        )
        let puntos = muestras.compactMap { muestra -> (fecha: Date, kg: Double)? in
            guard let cantidad = muestra as? HKQuantitySample else { return nil }
            let kg = cantidad.quantity.doubleValue(for: .gramUnit(with: .kilo))
            return (fecha: cantidad.startDate, kg: kg)
        }
        return puntos.sorted { $0.fecha < $1.fecha }
    }

    /// Sugerencia corta en español sobre qué tan listo está el cuerpo para
    /// entrenar hoy: combina la duración de sueño de la última noche con el
    /// HRV SDNN más reciente de esta mañana. Best-effort: `nil` cuando
    /// faltan los datos que una rama requiere — NUNCA inventa números, solo
    /// aplica umbrales a lo que HealthKit devuelva de verdad.
    func readinessResumen() async -> String? {
        guard HKHealthStore.isHealthDataAvailable(), autorizado else { return nil }
        let suenoHoras = await duracionSuenoUltimaNoche()
        let hrv = await hrvSDNNManana()

        if let suenoHoras, suenoHoras < 6 {
            return "Dormiste poco; hoy entrena suave o descansa."
        }
        if let hrv, hrv < 40 {
            return "Tu recuperación está baja; mejor una sesión ligera."
        }
        if let suenoHoras, suenoHoras >= 7, let hrv, hrv >= 60 {
            return "Bien descansado, hoy puedes exigirte."
        }
        return nil
    }

    /// Horas de sueño de la última noche (ventana desde el mediodía de ayer).
    /// Suma las muestras de `sleepAnalysis` de tipo "dormido" (asleep,
    /// core/deep/REM), que representan sueño real; si solo existe una muestra
    /// `inBed`, usa su duración como aproximación. `nil` si no hay nada.
    private func duracionSuenoUltimaNoche() async -> Double? {
        let calendario = Calendar.current
        let hoy = calendario.startOfDay(for: Date())
        guard let inicioNoche = calendario.date(byAdding: .hour, value: -12, to: hoy) else { return nil }

        let muestras = await consultarMuestras(
            HKCategoryType(.sleepAnalysis),
            desde: inicioNoche,
            hasta: Date(),
            limite: HKObjectQueryNoLimit
        )
        let categorias = muestras.compactMap { $0 as? HKCategorySample }
        let dormidos = categorias.filter { esCategoriaDormido($0.value) }
        let enCama = categorias.filter { $0.value == HKCategoryValueSleepAnalysis.inBed.rawValue }

        let segmentos = dormidos.isEmpty ? enCama : dormidos
        guard !segmentos.isEmpty else { return nil }

        let segundos = segmentos.reduce(0.0) { total, muestra in
            total + max(0, muestra.endDate.timeIntervalSince(muestra.startDate))
        }
        return segundos / 3600
    }

    private func esCategoriaDormido(_ valor: Int) -> Bool {
        valor == HKCategoryValueSleepAnalysis.asleepUnspecified.rawValue
            || valor == HKCategoryValueSleepAnalysis.asleepCore.rawValue
            || valor == HKCategoryValueSleepAnalysis.asleepDeep.rawValue
            || valor == HKCategoryValueSleepAnalysis.asleepREM.rawValue
    }

    /// HRV SDNN (ms) más reciente de esta mañana (antes del mediodía). El
    /// HRV se mide al despertar, así que se ignora cualquier muestra posterior.
    private func hrvSDNNManana() async -> Double? {
        let calendario = Calendar.current
        let hoy = calendario.startOfDay(for: Date())
        guard let mediodia = calendario.date(bySettingHour: 12, minute: 0, second: 0, of: Date()) else { return nil }
        let fin = min(mediodia, Date())
        guard fin > hoy else { return nil }

        let muestras = await consultarMuestras(
            HKQuantityType(.heartRateVariabilitySDNN),
            desde: hoy,
            hasta: fin,
            limite: 1
        )
        guard let muestra = muestras.first as? HKQuantitySample else { return nil }
        return muestra.quantity.doubleValue(for: HKUnit.secondUnit(with: .milli))
    }

    /// Consulta genérica de muestras HealthKit ordenadas de más reciente a más
    /// antigua. El `HKSampleQuery` se dispara desde el hilo principal (esta
    /// clase es `@MainActor`); la continuación se resuelve cuando el store
    /// conteste, venga de donde venga.
    private func consultarMuestras(_ tipo: HKSampleType, desde inicio: Date, hasta fin: Date, limite: Int) async -> [HKSample] {
        await withCheckedContinuation { continuacion in
            let predicado = HKQuery.predicateForSamples(
                withStart: inicio, end: fin, options: .strictStartDate
            )
            let orden = NSSortDescriptor(key: HKSampleSortIdentifierStartDate, ascending: false)
            let query = HKSampleQuery(
                sampleType: tipo,
                predicate: predicado,
                limit: limite,
                sortDescriptors: [orden]
            ) { _, muestras, _ in
                continuacion.resume(returning: muestras ?? [])
            }
            store.execute(query)
        }
    }

    // MARK: Seguimiento en vivo

    /// Arranca el `HKLiveWorkoutBuilder`: mientras la sesión esté activa,
    /// HealthKit recolecta frecuencia cardíaca y calorías y la UI las ve en
    /// `frecuenciaCardiaca`/`caloriasActivas`. No lanza: sin permiso, degrada
    /// a `siguiendo=false` y la sesión de gym sigue normal.
    /// `inicio` es el instante REAL en que arrancó la sesión de gym (del
    /// backend), no el momento en que esta vista empieza a rastrear: así un
    /// entrenamiento de 52 min guardado al reabrir la app a mitad de sesión
    /// no queda registrado como "2 minutos".
    func iniciarSeguimiento(inicio: Date = Date()) {
        guard HKHealthStore.isHealthDataAvailable(), autorizado, builder == nil else { return }
        let configuracion = HKWorkoutConfiguration()
        configuracion.activityType = .traditionalStrengthTraining
        configuracion.locationType = .indoor

        // `HKLiveWorkoutBuilder` no se instancia directo (init NS_UNAVAILABLE):
        // el builder se obtiene de su `HKWorkoutSession` asociada.
        guard let sesion = try? HKWorkoutSession(healthStore: store, configuration: configuracion)
        else { return }
        let nuevoBuilder = sesion.associatedWorkoutBuilder()

        sesion.delegate = self
        nuevoBuilder.delegate = self
        sesionHealth = sesion
        builder = nuevoBuilder

        let fuente = HKLiveWorkoutDataSource(healthStore: store, workoutConfiguration: configuracion)
        nuevoBuilder.dataSource = fuente
        nuevoBuilder.dataSource?.enableCollection(
            for: HKQuantityType(.heartRate), predicate: nil
        )
        nuevoBuilder.dataSource?.enableCollection(
            for: HKQuantityType(.activeEnergyBurned), predicate: nil
        )

        self.inicio = inicio
        sesion.startActivity(with: inicio)
        nuevoBuilder.beginCollection(withStart: inicio) { [weak self] _, _ in
            Task { @MainActor [weak self] in
                self?.siguiendo = true
            }
        }
    }

    /// Termina el seguimiento y GUARDA el workout con las muestras reales
    /// recolectadas (frecuencia, calorías). `fin` es el cierre de la sesión.
    func detenerSeguimiento(fin: Date) async {
        guard let sesion = sesionHealth, let builder, let inicio else {
            guardarWorkoutMinimo(fin: fin)
            return
        }
        sesion.end()
        builder.endCollection(withEnd: fin) { [weak self] _, _ in
            Task { @MainActor [weak self] in
                _ = try? await builder.finishWorkout()
                self?.limpiarSeguimiento()
            }
        }
        // Fallback determinista si el finish asíncrono no reportara.
        await detenerYLimpiar()
    }

    /// Pausa la recolección (el workout queda a medio recolectar; al reanudar
    /// se retoma). Se llama al pausar la sesión de gym.
    func pausarSeguimiento() {
        sesionHealth?.pause()
    }

    /// Reanuda la recolección tras una pausa.
    func reanudarSeguimiento() {
        sesionHealth?.resume()
    }

    /// El Watch está midiendo: el iPhone suelta su sesión sin guardar otro
    /// workout (si no, Salud duplica el entrenamiento).
    func abandonarSeguimientoSinGuardar() {
        builder?.discardWorkout()
        sesionHealth?.end()
        limpiarSeguimiento()
    }

    /// Métricas que manda el reloj mientras él es dueño del `HKWorkoutSession`.
    func aplicarMetricasExternas(bpm: Double?, kcal: Double?) {
        if let bpm { frecuenciaCardiaca = bpm }
        if let kcal { caloriasActivas = kcal }
    }

    /// Agua que Siri o el iPhone registran. `false` si no hay permiso o Salud rechaza.
    @discardableResult
    func guardarAgua(ml: Int) async -> Bool {
        guard HKHealthStore.isHealthDataAvailable(), autorizado, ml > 0 else { return false }
        let tipo = HKQuantityType(.dietaryWater)
        let cantidad = HKQuantity(unit: .literUnit(with: .milli), doubleValue: Double(ml))
        let muestra = HKQuantitySample(type: tipo, quantity: cantidad, start: Date(), end: Date())
        do {
            try await store.save(muestra)
            return true
        } catch {
            return false
        }
    }

    private func detenerYLimpiar() async {
        sesionHealth?.end()
        sesionHealth = nil
        builder = nil
        inicio = nil
        frecuenciaCardiaca = nil
        caloriasActivas = nil
        siguiendo = false
    }

    private func limpiarSeguimiento() {
        sesionHealth = nil
        builder = nil
        inicio = nil
        frecuenciaCardiaca = nil
        caloriasActivas = nil
        siguiendo = false
    }

    /// Guarda un workout de fuerza con cero muestras (fallback cuando no hubo
    /// seguimiento en vivo, p. ej. sin permiso de HealthKit).
    private func guardarWorkoutMinimo(fin: Date) {
        guard HKHealthStore.isHealthDataAvailable(), autorizado else { return }
        guard let inicio = inicio, fin > inicio else { return }
        let configuracion = HKWorkoutConfiguration()
        configuracion.activityType = .traditionalStrengthTraining
        configuracion.locationType = .indoor
        let builder = HKWorkoutBuilder(healthStore: store, configuration: configuracion, device: .local())
        // Completions encadenados (sin variantes async de los labels en este SDK):
        // best-effort, nunca bloquea ni lanza.
        builder.beginCollection(withStart: inicio) { _, _ in
            builder.endCollection(withEnd: fin) { _, _ in
                builder.finishWorkout { _, _ in }
            }
        }
    }

    // MARK: Delegados

    nonisolated func workoutBuilder(
        _ workoutBuilder: HKLiveWorkoutBuilder, didCollectDataOf collectedTypes: Set<HKSampleType>
    ) {
        let frecuencia = workoutBuilder.statistics(for: HKQuantityType(.heartRate))?
            .mostRecentQuantity()?.doubleValue(for: HKUnit.count().unitDivided(by: .minute()))
        let calorias = workoutBuilder.statistics(for: HKQuantityType(.activeEnergyBurned))?
            .sumQuantity()?.doubleValue(for: .kilocalorie())
        Task { @MainActor [weak self] in
            self?.frecuenciaCardiaca = frecuencia
            if let calorias {
                self?.caloriasActivas = calorias
            }
        }
    }

    nonisolated func workoutBuilderDidCollectEvent(_ workoutBuilder: HKLiveWorkoutBuilder) {}

    nonisolated func workoutSession(
        _ workoutSession: HKWorkoutSession,
        didChangeTo toState: HKWorkoutSessionState,
        from fromState: HKWorkoutSessionState,
        date: Date
    ) {}

    nonisolated func workoutSession(_ workoutSession: HKWorkoutSession, didFailWithError error: Error) {
        Task { @MainActor [weak self] in
            self?.limpiarSeguimiento()
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