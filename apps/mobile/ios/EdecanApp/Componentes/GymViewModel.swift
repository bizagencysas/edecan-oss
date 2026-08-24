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
    let health = HealthKitManager()
    private let liveActivity = GymLiveActivityController()

    private(set) var sesion: GymSession?
    private(set) var plan: GymPlan?
    private(set) var cargando = false
    private(set) var terminada = false
    var errorMensaje: String?
    /// Mensaje de seguimiento que devuelve el backend (`gymRegistrarSerie`/
    /// `gymTerminarSesion`). Se muestra tal cual, sin interpretarse.
    var mensajeBackend: String?

    /// Campos de texto por ejercicio (índice del plan → valor editable).
    /// `pesos` es el campo opcional de kilos; `repeticiones` viene prefijado
    /// desde el plan ("8-10" → "8") y el usuario puede ajustarlo.
    var pesos: [Int: String] = [:]
    var repeticiones: [Int: String] = [:]

    /// Instante de arranque del cronómetro. Preferimos el `started_at` del
    /// backend; si falta o no parsea, usamos el momento en que se cargó.
    private(set) var fechaInicio: Date?

    private static let iso = ISO8601DateFormatter()

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
        cargando = true
        defer { cargando = false }
        do {
            if let sesion = try await client.gymSesionActual() {
                await aplicarSesion(sesion)
            } else if let plan = try await client.gymPlanDeHoy() {
                self.plan = plan
                prefijarCampos(plan)
            } else {
                errorMensaje = "No hay plan de entrenamiento para hoy."
            }
        } catch {
            errorMensaje = error.localizedDescription
        }
    }

    /// Botón "Empezar" cuando no hay sesión: responde "sí" al checkin y el
    /// backend devuelve la sesión recién creada (o el plan si todavía no la
    /// abre).
    func empezar(client: APIClient?) async {
        guard let client else { return }
        cargando = true
        defer { cargando = false }
        do {
            let out = try await client.gymCheckin(respuesta: "si")
            if let sesion = out.session {
                await aplicarSesion(sesion)
            } else if let plan = out.plan {
                self.plan = plan
                prefijarCampos(plan)
            }
            if !out.message.isEmpty { mensajeBackend = out.message }
        } catch {
            errorMensaje = error.localizedDescription
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
        } catch {
            errorMensaje = error.localizedDescription
        }
    }

    func iniciar(client: APIClient?) async {
        guard let client, let sesion else { return }
        do {
            await aplicarSesion(try await client.gymIniciarSesion(sessionId: sesion.id))
        } catch {
            errorMensaje = error.localizedDescription
        }
    }

    func pausar(client: APIClient?) async {
        guard let client, let sesion else { return }
        do {
            await aplicarSesion(try await client.gymPausarSesion(sessionId: sesion.id))
        } catch {
            errorMensaje = error.localizedDescription
        }
    }

    func reanudar(client: APIClient?) async {
        guard let client, let sesion else { return }
        do {
            await aplicarSesion(try await client.gymReanudarSesion(sessionId: sesion.id))
        } catch {
            errorMensaje = error.localizedDescription
        }
    }

    func terminar(client: APIClient?) async {
        guard let client, let sesion else { return }
        do {
            let out = try await client.gymTerminarSesion(sessionId: sesion.id)
            mensajeBackend = out.message.isEmpty ? nil : out.message
            plan = sesion.plan
            terminada = true
            self.sesion = nil
            await liveActivity.terminar()
            // HealthKit: guardar el workout de la sesión que acaba de cerrar.
            if let inicio = fechaInicio {
                await health.guardarWorkout(inicio: inicio, fin: Date())
            }
        } catch {
            errorMensaje = error.localizedDescription
        }
    }

    /// Series hechas/totales de un ejercicio de la sesión activa. `nil`
    /// cuando el backend todavía no mandó progreso para ese índice.
    func progreso(para indice: Int) -> GymProgresoEjercicio? {
        sesion?.progress.exercises.first(where: { $0.index == indice })
    }

    private func aplicarSesion(_ nueva: GymSession) async {
        sesion = nueva
        // El cronómetro solo corre cuando el backend devuelve `started_at`
        // (sesión active/paused). Una sesión "planned" todavía no lo tiene:
        // no hay contador hasta tocar "Iniciar".
        if let raw = nueva.startedAt, let fecha = Self.iso.date(from: raw) {
            fechaInicio = fecha
        } else if nueva.status != "planned" {
            fechaInicio = Date()
        } else {
            fechaInicio = nil
        }
        prefijarCampos(nueva.plan)
        await actualizarLiveActivity()
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