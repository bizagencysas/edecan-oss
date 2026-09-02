import EdecanKit
import Foundation
import Observation

/// Estado de la pantalla de entrenamiento. Carga la sesión activa o el plan
/// de hoy, y maneja el ciclo completo: empezar, registrar series, pausar/
/// reanudar y terminar (con el `HKWorkout` correspondiente).
///
/// Sigue la convención del repo: el `APIClient` llega por parámetro en cada
/// método (igual que ``MisionesViewModel``), nunca se guarda en el view model.
@MainActor
@Observable
final class GymViewModel {
    let health = HealthKitManager.compartido
    private let liveActivity = GymLiveActivityController()
    /// Puente al Apple Watch: manda el estado REAL de la sesión (generada por
    /// IA en el backend) y las métricas REALES de HealthKit. Cero mocks.
    let watchBridge = PhoneWatchBridge.compartido

    private(set) var sesion: GymSession?
    private(set) var plan: GymPlan?
    private(set) var cargando = false
    private(set) var terminada = false
    /// La sesión de HOY ya está `completed`. `/plan/today` sigue devolviendo
    /// el plan aunque ya se entrenó (no sabe de sesiones), así que se averigua
    /// contra el historial — sin esto, la pantalla ofrecía "Iniciar
    /// entrenamiento" otra vez después de haber terminado.
    private(set) var sesionCompletadaHoy: GymSession?
    /// Racha en días (la manda `/history` junto al historial).
    private(set) var rachaDias = 0
    var yaEntrenoHoy: Bool { sesionCompletadaHoy != nil }
    var errorMensaje: String?
    /// Mensaje de seguimiento que devuelve el backend (`gymRegistrarSerie`/
    /// `gymTerminarSesion`). Se muestra tal cual, sin interpretarse.
    var mensajeBackend: String?
    /// Resumen con IA de la sesión que devuelve `gymTerminarSesion`
    /// (opcional, best-effort). Se muestra en la vista de sesión terminada.
    private(set) var resumenIA: String?

    /// Sondeo del collage cuando el backend aún no escribió `imagen_file_id`.
    private var tareaPollingCollage: Task<Void, Never>?

    /// Campos de texto por ejercicio (índice del plan → valor editable).
    /// `pesos` es el campo opcional de kilos; `repeticiones` viene prefijado
    /// desde el plan ("8-10" → "8") y el usuario puede ajustarlo.
    var pesos: [Int: String] = [:]
    var repeticiones: [Int: String] = [:]

    /// Instante de arranque del cronómetro. Preferimos el `started_at` del
    /// backend; si falta o no parsea, usamos el momento en que se cargó.
    private(set) var fechaInicio: Date?

    private static let iso: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        // El backend manda `started_at` con microsegundos
        // ("2026-08-24T17:03:23.113017+00:00"); sin `.withFractionalSeconds`
        // el parseo devuelve `nil` y `fechaInicio` cae a `Date()`, así que el
        // cronómetro se REINICIABA en cada `.task`/refresco al volver a la
        // pantalla. Este es el fix de "minimizo la app / salgo al chat y el
        // tiempo vuelve a cero".
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    /// Fallback sin fracción de segundo (algunos orígenes mandan el ISO pelado).
    private static let isoSimple = ISO8601DateFormatter()

    var tieneSesion: Bool { sesion != nil }

    var ejercicios: [GymEjercicio] {
        sesion?.plan.exercises ?? plan?.exercises ?? []
    }

    var tituloPlan: String {
        sesion?.plan.title ?? plan?.title ?? ""
    }

    var imageURL: String? {
        sesion?.plan.imageURL ?? plan?.imageURL
    }

    /// `fileId` del collage para descargarlo con el Bearer del tenant vía
    /// `descargarArtefacto` — el camino autenticado, no la URL pública.
    var imageFileID: String? {
        sesion?.plan.imageFileID ?? plan?.imageFileID
    }

    var pausada: Bool {
        sesion?.status == "paused"
    }

    /// Sesión recién creada por el check-in, todavía sin cronómetro: muestra
    /// el botón "Iniciar" y no corre el contador hasta que el señor lo toque.
    var esPlaneada: Bool {
        sesion?.status == "planned"
    }

    func cargar(client: APIClient?) async {
        guard let client else {
            errorMensaje = "No hay sesión activa."
            return
        }
        tareaPollingCollage?.cancel()
        let yaTeniaContenido = sesion != nil || plan != nil
        cargando = !yaTeniaContenido
        defer { cargando = false }
        do {
            if let sesion = try await client.gymSesionActual() {
                sesionCompletadaHoy = nil
                await aplicarSesion(sesion)
                errorMensaje = nil
                if sesion.plan.imageFileID == nil {
                    iniciarPollingCollage(client: client, origenSesion: true)
                }
            } else if let plan = try await client.gymPlanDeHoy() {
                self.sesion = nil
                self.plan = plan
                prefijarCampos(plan)
                errorMensaje = nil
                (sesionCompletadaHoy, rachaDias) = await Self.sesionCompletadaDeHoy(client: client)
                if plan.imageFileID == nil {
                    iniciarPollingCollage(client: client, origenSesion: false)
                }
            } else {
                sesionCompletadaHoy = nil
                if !yaTeniaContenido {
                    errorMensaje = "No hay plan de entrenamiento para hoy."
                } else {
                    errorMensaje = nil
                }
            }
        } catch {
            registrarError(error)
        }
    }

    /// La sesión `completed` iniciada HOY (si existe) y la racha en días.
    /// Busca en el historial porque el backend no cruza "plan de hoy" con
    /// "sesión completada": `/plan/today` y `/session` no lo dicen.
    private static func sesionCompletadaDeHoy(client: APIClient) async -> (GymSession?, Int) {
        guard let historial = try? await client.gymHistorial(limit: 5) else { return (nil, 0) }
        let calendario = Calendar.current
        for sesion in historial.sessions where sesion.status == "completed" {
            if let raw = sesion.startedAt,
                let fecha = Self.iso.date(from: raw) ?? Self.isoSimple.date(from: raw),
                calendario.isDateInToday(fecha) {
                return (sesion, historial.streak)
            }
        }
        return (nil, historial.streak)
    }

    /// Botón "Empezar" cuando no hay sesión: responde "sí" al checkin y el
    /// backend devuelve la sesión recién creada (o el plan si todavía no la
    /// abre).
    func empezar(client: APIClient?) async {
        guard let client else { return }
        cargando = true
        defer { cargando = false }
        do {
            let readiness = await health.readinessResumen()
            let out = try await client.gymCheckin(respuesta: "si", readiness: readiness)
            if let sesion = out.session {
                await aplicarSesion(sesion)
                if sesion.plan.imageFileID == nil {
                    iniciarPollingCollage(client: client, origenSesion: true)
                }
            } else if let plan = out.plan {
                self.sesion = nil
                self.plan = plan
                prefijarCampos(plan)
                if plan.imageFileID == nil {
                    iniciarPollingCollage(client: client, origenSesion: false)
                }
            }
            if !out.message.isEmpty { mensajeBackend = out.message }
            errorMensaje = nil
        } catch {
            registrarError(error)
        }
    }

    func registrarSerie(ejercicio: GymEjercicio, indice: Int, client: APIClient?) async {
        guard let client, let sesion else { return }
        let peso = parsearPeso(pesos[indice])
        let reps = parsearReps(repeticiones[indice], ejercicio: ejercicio)
        do {
            let out = try await client.gymRegistrarSerie(
                sessionId: sesion.id,
                ejercicioIdx: indice,
                repeticiones: reps,
                pesoKg: peso
            )
            await aplicarSesion(out.session)
            mensajeBackend = out.message.isEmpty ? nil : out.message
            await coachVoz(tipo: "serie_completada", ejercicio: ejercicio.name, client: client)
        } catch {
            registrarError(error)
        }
    }

    func iniciar(client: APIClient?) async {
        guard let client, let sesion else { return }
        do {
            await aplicarSesion(try await client.gymIniciarSesion(sessionId: sesion.id))
            if PhoneWatchBridge.compartido.relojAlcanzable {
                health.abandonarSeguimientoSinGuardar()
            } else {
                // Inicio real de la sesión (backend), no el instante del tap:
                // si el seguimiento arranca tarde, el workout guardado en
                // Salud conserva la duración verdadera del entrenamiento.
                health.iniciarSeguimiento(inicio: fechaInicio ?? Date())
            }
            await coachVoz(tipo: "sesion_inicio", client: client)
        } catch {
            registrarError(error)
        }
    }

    func pausar(client: APIClient?) async {
        guard let client, let sesion else { return }
        do {
            if !PhoneWatchBridge.compartido.relojAlcanzable {
                health.pausarSeguimiento()
            }
            await aplicarSesion(try await client.gymPausarSesion(sessionId: sesion.id))
        } catch {
            registrarError(error)
        }
    }

    func reanudar(client: APIClient?) async {
        guard let client, let sesion else { return }
        do {
            if !PhoneWatchBridge.compartido.relojAlcanzable {
                health.reanudarSeguimiento()
            }
            await aplicarSesion(try await client.gymReanudarSesion(sessionId: sesion.id))
        } catch {
            registrarError(error)
        }
    }

    func terminar(client: APIClient?) async {
        guard let client, let sesion else { return }
        errorMensaje = nil
        tareaPollingCollage?.cancel()
        do {
            let out = try await client.gymTerminarSesion(sessionId: sesion.id)
            mensajeBackend = out.message.isEmpty ? nil : out.message
            resumenIA = out.resumen
            plan = sesion.plan
            terminada = true
            self.sesion = nil
            await liveActivity.terminar()
            // HealthKit: cierra el seguimiento en vivo y guarda el workout con
            // las muestras reales (frecuencia + calorías) que se recolectaron.
            await health.detenerSeguimiento(fin: Date())
            enviarEstadoAlWatch()
        } catch {
            registrarError(error)
        }
    }

    /// Series hechas/totales de un ejercicio de la sesión activa. `nil`
    /// cuando el backend todavía no mandó progreso para ese índice.
    func progreso(para indice: Int) -> GymProgresoEjercicio? {
        sesion?.progress.exercises.first(where: { $0.index == indice })
    }

    private func registrarError(_ error: Error) {
        GymRefreshSupport.asignarError(error, a: &errorMensaje)
    }

    private func iniciarPollingCollage(client: APIClient, origenSesion: Bool) {
        tareaPollingCollage?.cancel()
        tareaPollingCollage = Task { [weak self] in
            let poller = GymCollagePoller()
            let fileId = await poller.poll {
                if origenSesion {
                    try await client.gymSesionActual()?.plan.imageFileID
                } else {
                    try await client.gymPlanDeHoy()?.imageFileID
                }
            }
            guard !Task.isCancelled, fileId != nil, let self else { return }
            await self.refrescarCollage(client: client, origenSesion: origenSesion)
        }
    }

    private func refrescarCollage(client: APIClient, origenSesion: Bool) async {
        do {
            if origenSesion, let sesion = try await client.gymSesionActual() {
                await aplicarSesion(sesion)
            } else if let plan = try await client.gymPlanDeHoy() {
                self.plan = plan
                prefijarCampos(plan)
            }
        } catch {
            registrarError(error)
        }
    }

    private func aplicarSesion(_ nueva: GymSession) async {
        sesion = nueva
        // Si la sesión ya está en curso (p. ej. se reabre la app a mitad del
        // entrenamiento), arranca el seguimiento en vivo de HealthKit si no
        // estaba corriendo. El reloj es dueño de la sesión cuando está reachable.
        if nueva.status == "active" && !health.siguiendo && !PhoneWatchBridge.compartido.relojAlcanzable {
            health.iniciarSeguimiento(inicio: fechaInicio ?? Date())
        }
        // El cronómetro solo corre cuando el backend devuelve `started_at`
        // (sesión active/paused). Una sesión "planned" todavía no lo tiene:
        // no hay contador hasta tocar "Iniciar".
        if let raw = nueva.startedAt,
            let fecha = Self.iso.date(from: raw) ?? Self.isoSimple.date(from: raw) {
            fechaInicio = fecha
        } else if nueva.status != "planned" {
            fechaInicio = Date()
        } else {
            fechaInicio = nil
        }
        prefijarCampos(nueva.plan)
        await actualizarLiveActivity()
        enviarEstadoAlWatch()
    }

    // MARK: - Apple Watch (datos reales, cero mocks)

    /// Manda al reloj el estado REAL de la sesión: título del plan (del
    /// backend/IA), segundos transcurridos desde `started_at` y las métricas
    /// de HealthKit en vivo. Sin sesión, manda "no activo".
    func enviarEstadoAlWatch() {
        let titulo = sesion?.plan.title ?? plan?.title
        let cronometro: TimeInterval? = fechaInicio.map { Date().timeIntervalSince($0) }
        watchBridge.enviar(
            sesionActiva: sesion?.status == "active",
            titulo: sesion == nil && plan == nil ? nil : titulo,
            cronometro: cronometro,
            frecuenciaCardiaca: health.frecuenciaCardiaca,
            calorias: health.caloriasActivas,
            tienePlan: sesion != nil || plan != nil,
        )
        var extra: [String: Any] = ["pausada": sesion?.status == "paused"]
        if let sesion {
            let (ejercicio, hechas, totales) = ejercicioActual(sesion)
            extra["ejercicioActual"] = ejercicio
            extra["seriesHechas"] = hechas
            extra["seriesTotales"] = totales
        }
        watchBridge.enviarTablero(extra)
    }

    /// Solo las métricas en vivo (cuando la UI detecta un cambio de frecuencia
    /// o calorías), sin tocar el estado de la sesión.
    func enviarMetricasAlWatch() {
        guard sesion?.status == "active" || sesion?.status == "paused" else { return }
        watchBridge.enviar(
            titulo: sesion?.plan.title ?? plan?.title,
            frecuenciaCardiaca: health.frecuenciaCardiaca,
            calorias: health.caloriasActivas
        )
    }

    /// El reloj tocó "Entrenar"/"Pausar": inicia, pausa o reanuda según el
    /// estado real del backend (nunca un toggle local).
    func alternarDesdeWatch(client: APIClient?) async {
        if esPlaneada {
            await iniciar(client: client)
        } else if pausada {
            await reanudar(client: client)
        } else {
            await pausar(client: client)
        }
    }

    /// Manda al reloj el descanso en curso (segundos restantes + ejercicio).
    func enviarDescansoAlWatch(restante: Int?, ejercicio: String?) {
        watchBridge.enviarDescanso(restante: restante, ejercicio: ejercicio)
    }

    /// Índice cuyo nombre se tocó: GymView abre la hoja de cambio.
    var swapIndice: Int?

    func swapIndicePedir(_ indice: Int) {
        swapIndice = indice
    }

    /// Reemplaza el plan en memoria con el que devuelve el swap (la IA ya
    /// aplicó el cambio en el backend). La sesión activa conserva series hechas.
    /// Round-trip JSON en vez de memberwise init: GymSession se decodifica a
    /// mano y su init explícito no acepta reconstrucción por parámetros.
    func aplicarPlan(_ planNuevo: GymPlan) {
        plan = planNuevo
        guard let sesionViva = sesion else { return }
        do {
            let dataViva = try JSONEncoder().encode(sesionViva)
            var obj = try JSONSerialization.jsonObject(with: dataViva) as? [String: Any] ?? [:]
            obj["plan"] = try JSONSerialization.jsonObject(with: JSONEncoder().encode(planNuevo))
            let dataNueva = try JSONSerialization.data(withJSONObject: obj)
            sesion = try JSONDecoder().decode(GymSession.self, from: dataNueva)
        } catch {
            // Best-effort: el plan ya quedó actualizado en `plan`; si la
            // reconstrucción de la sesión falla, el refresh del backend la
            // traerá igual en el próximo cargar().
        }
    }

    // MARK: - Coach de voz (línea de Sol + voz de Edecán, cero mocks)

    private let coachPlayer = ReproductorMPEGStream()
    // Misma voz que el altavoz del chat (ElevenLabs turbo v2.5).
    private let coachVoiceId = "0uHpKhb0ymsdvmCtPV8y"
    private let coachModelId = "eleven_turbo_v2_5"
    /// Single-flight: si una línea ya está hablando, la siguiente se ignora.
    /// Antes 4 toques de «+1 serie» lanzaban 4 voces superpuestas a la vez.
    private var vozCoachActiva = false

    /// Pide una línea de coach a Sol (xhigh) y la reproduce con la voz de
    /// Edecán. Best-effort: un fallo nunca bloquea el entrenamiento.
    func coachVoz(tipo: String, ejercicio: String? = nil, client: APIClient?) async {
        guard let client, !vozCoachActiva else { return }
        vozCoachActiva = true
        defer { vozCoachActiva = false }
        do {
            let out = try await client.gymCoachVoz(tipo: tipo, ejercicio: ejercicio)
            guard let linea = out.linea, !linea.isEmpty else { return }
            let stream = try await client.hablarStream(
                texto: linea, voiceId: coachVoiceId, modelId: coachModelId
            )
            try await coachPlayer.reproducir(stream: stream)
        } catch {
            // Best-effort: el coach de voz es un extra, nunca tumba la sesión.
        }
    }

    private func actualizarLiveActivity() async {
        guard let sesion, sesion.status != "planned" else { return }
        let (ejercicio, hechas, totales) = ejercicioActual(sesion)
        if liveActivity.activa {
            await liveActivity.actualizar(ejercicio: ejercicio, seriesHechas: hechas, seriesTotales: totales)
        } else {
            liveActivity.iniciar(
                ejercicio: ejercicio,
                seriesHechas: hechas,
                seriesTotales: totales,
                startedAt: fechaInicio ?? Date()
            )
        }
    }

    /// El ejercicio "en curso" para el Live Activity: el primero que todavía
    /// no completó sus series; si todos van completos, el primero del plan.
    private func ejercicioActual(_ sesion: GymSession) -> (String, Int, Int) {
        let progreso = sesion.progress.exercises
        let ejercicios = sesion.plan.exercises
        if let actual = progreso.first(where: { $0.setsDone < $0.setsTotal }) {
            let nombre = ejercicios.indices.contains(actual.index)
                ? ejercicios[actual.index].name
                : "Ejercicio \(actual.index + 1)"
            return (nombre, actual.setsDone, actual.setsTotal)
        }
        let nombre = ejercicios.first?.name ?? "Entrenamiento"
        return (nombre, 0, 0)
    }

    private func prefijarCampos(_ plan: GymPlan) {
        for (indice, ejercicio) in plan.exercises.enumerated() {
            if repeticiones[indice] == nil {
                repeticiones[indice] = repsPorDefecto(ejercicio.repetitions)
            }
        }
    }

    /// Primer entero del texto de repeticiones ("8-10" → "8"); vacío si no
    /// hay ninguno ("hasta el fallo").
    private func repsPorDefecto(_ texto: String) -> String {
        texto.components(separatedBy: CharacterSet.decimalDigits.inverted)
            .filter { !$0.isEmpty }
            .first ?? ""
    }

    private func parsearReps(_ texto: String?, ejercicio: GymEjercicio) -> Int {
        if let texto, let valor = Int(texto.trimmingCharacters(in: .whitespaces)) {
            return valor
        }
        if let valor = Int(repsPorDefecto(ejercicio.repetitions)) {
            return valor
        }
        return 0
    }

    private func parsearPeso(_ texto: String?) -> Double? {
        guard let texto else { return nil }
        let limpio = texto.trimmingCharacters(in: .whitespaces)
            .replacingOccurrences(of: ",", with: ".")
        guard let valor = Double(limpio), valor > 0 else { return nil }
        return valor
    }
}