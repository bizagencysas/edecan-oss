import Foundation

// MARK: - `/v1/gym` (contrato en paralelo del feature "gimnasio") — modelos
// Codable de la capa de datos, sin UI. Las claves JSON van en snake_case
// EXACTO, tal como las define el contrato que el backend implementa en
// paralelo contra ESTE cliente. Solo se usa un decoder manual donde un campo
// ausente debe caer a un default seguro (nunca fabricar éxito, ver
// ``GymCheckinOut``).

/// Un ejercicio del plan (`GymPlan.ejercicios`).
/// `repetitions` es un `String` a propósito: el backend puede mandar
/// "8-10", "hasta el fallo", etc. — no siempre un número.
public struct GymEjercicio: Codable, Sendable, Equatable {
    public let name: String
    public let muscle: String
    public let sets: Int
    public let repetitions: String
    public let restSeconds: Int
    public let notes: String

    enum CodingKeys: String, CodingKey {
        case name = "nombre"
        case muscle = "musculo"
        case sets = "series"
        case repetitions = "repeticiones"
        case restSeconds = "descanso_seg"
        case notes = "notas"
    }
}

/// El plan del día (`GET /v1/gym/plan/today` → `{"plan": GymPlan|null}`).
public struct GymPlan: Codable, Sendable, Equatable {
    public let title: String
    public let objective: String
    public let durationMinutes: Int
    public let imageURL: String?
    public let imageFileID: String?
    public let exercises: [GymEjercicio]

    enum CodingKeys: String, CodingKey {
        case title = "titulo"
        case objective = "objetivo"
        case durationMinutes = "duracion_min"
        case imageURL = "imagen_url"
        case imageFileID = "imagen_file_id"
        case exercises = "ejercicios"
    }
}

/// Una serie ya registrada dentro de `GymSession.series`.
/// `en` es la cadena opaca que manda el backend en la clave `"en"` — se
/// conserva tal cual, sin interpretarse en el cliente.
public struct GymSerie: Codable, Sendable, Equatable {
    public let exerciseIndex: Int
    public let repetitions: Int
    public let weightKg: Double?
    public let en: String

    enum CodingKeys: String, CodingKey {
        case exerciseIndex = "ejercicio_idx"
        case repetitions = "repeticiones"
        case weightKg = "peso_kg"
        case en
    }
}

/// Respuesta de `POST /v1/gym/plan/swap-ejercicio`: el ejercicio que la IA
/// propone para reemplazar el pedido, más alternativas para escojer de lista.
public struct GymSwapOut: Codable, Sendable, Equatable {
    public let ok: Bool
    public let interpreto: String?
    public let plan: GymPlan?
    public let ejercicioActual: GymEjercicio?
    public let ejercicioPropuesto: GymEjercicio?
    public let alternativas: [GymEjercicio]?

    enum CodingKeys: String, CodingKey {
        case ok, plan, alternativas
        case interpreto
        case ejercicioActual = "ejercicio_actual"
        case ejercicioPropuesto = "ejercicio_nuevo"
    }
}

/// Un elemento de `GymProgreso.exercises`.
public struct GymProgresoEjercicio: Codable, Sendable, Equatable {
    public let index: Int
    public let setsDone: Int
    public let setsTotal: Int

    enum CodingKeys: String, CodingKey {
        case index = "idx"
        case setsDone = "series_hechas"
        case setsTotal = "series_total"
    }
}

/// Avance de la sesión (`GymSession.progress`).
public struct GymProgreso: Codable, Sendable, Equatable {
    public let exercises: [GymProgresoEjercicio]

    enum CodingKeys: String, CodingKey {
        case exercises = "ejercicios"
    }
}

/// Lo último registrado por ejercicio en la sesión PREVIA (sobrecarga
/// progresiva): para mostrar "la semana pasada: 40kg × 10" y sugerir el
/// siguiente paso. `fecha` es "YYYY-MM-DD" de esa sesión.
public struct GymPrevioEjercicio: Codable, Sendable, Equatable {
    public let idx: Int
    public let weightKg: Double?
    public let repetitions: Int?
    public let fecha: String?

    enum CodingKeys: String, CodingKey {
        case idx
        case weightKg = "peso_kg"
        case repetitions = "repeticiones"
        case fecha
    }
}

/// Meta de sobrecarga progresiva por ejercicio (sugerida por el backend a
/// partir del `previo`): qué peso/reps intentar hoy.
public struct GymMetaEjercicio: Codable, Sendable, Equatable {
    public let idx: Int
    public let pesoObjetivo: Double?
    public let repeticionesObjetivo: Int?

    enum CodingKeys: String, CodingKey {
        case idx
        case pesoObjetivo = "peso_objetivo"
        case repeticionesObjetivo = "repeticiones_objetivo"
    }
}

/// Sesión de entrenamiento activa (o histórica en `gymHistorial`).
/// `status`/`estado` queda como `String` crudo (no un enum cerrado) — mismo
/// criterio que `MissionOut.status`: si el backend suma un estado nuevo,
/// decodificar no debe romperse.
public struct GymSession: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let status: String
    public let plan: GymPlan
    public let startedAt: String?
    public let series: [GymSerie]
    public let progress: GymProgreso
    /// Registros de la sesión anterior por `idx` de ejercicio (opcional).
    public let previo: [GymPrevioEjercicio]?
    /// Meta de sobrecarga progresiva por `idx` (opcional).
    public let meta: [GymMetaEjercicio]?

    enum CodingKeys: String, CodingKey {
        case id
        case status = "estado"
        case plan
        case startedAt = "started_at"
        case series
        case progress = "progreso"
        case previo
        case meta
    }
}

/// Respuesta de `POST /v1/gym/checkin`.
public struct GymCheckinOut: Codable, Sendable, Equatable {
    public let ok: Bool
    public let plan: GymPlan?
    public let session: GymSession?
    public let message: String

    enum CodingKeys: String, CodingKey {
        case ok, plan, session
        case message = "mensaje"
    }

    // Decoder a mano por una sola razón: si `ok` falta (o viene `null`), el
    // default seguro es `false` — "no me lo dijeron" es "no", nunca fabricar
    // éxito por la ausencia de la señal (mismo criterio que
    // `SocialContentPublishResult.verified` → `.unknown`).
    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        ok = try container.decodeIfPresent(Bool.self, forKey: .ok) ?? false
        plan = try container.decodeIfPresent(GymPlan.self, forKey: .plan)
        session = try container.decodeIfPresent(GymSession.self, forKey: .session)
        message = try container.decode(String.self, forKey: .message)
    }
}

/// Respuesta de `POST /v1/gym/sessions/{id}/sets` y `.../complete`.
public struct GymSetLogOut: Codable, Sendable, Equatable {
    public let session: GymSession
    public let message: String
    /// Resumen con IA de la sesión al terminarla (opcional, best-effort).
    public let resumen: String?

    enum CodingKeys: String, CodingKey {
        case session
        case message = "mensaje"
        case resumen
    }
}

/// Respuesta de `GET /v1/gym/history`: sesiones pasadas + racha en semanas.
public struct GymHistorialOut: Codable, Sendable, Equatable {
    public let sessions: [GymSession]
    public let streak: Int

    enum CodingKeys: String, CodingKey {
        case sessions
        case streak
    }
}

/// Respuesta de `GET /v1/gym/reporte_semanal`: resumen con IA de la semana.
public struct GymReporteSemanalOut: Codable, Sendable, Equatable {
    public let reporte: String

    enum CodingKeys: String, CodingKey {
        case reporte
    }
}

/// Respuesta de `POST /v1/gym/form/analizar`: feedback de técnica con IA.
public struct GymFormaOut: Codable, Sendable, Equatable {
    public let feedback: String

    enum CodingKeys: String, CodingKey {
        case feedback
    }
}

/// Respuesta de `POST /v1/gym/coach_voz`: una línea de coach con IA (el TTS lo
/// hace el cliente).
public struct GymCoachVozOut: Codable, Sendable, Equatable {
    public let linea: String?

    enum CodingKeys: String, CodingKey {
        case linea
    }
}

/// Body de `POST /v1/gym/checkin`.
public struct GymCheckinIn: Encodable, Sendable, Equatable {
    public let answer: String
    /// Estado de recuperación del usuario (readiness de HealthKit) para que el
    /// entrenador ajuste la rutina del día. Opcional.
    public let readiness: String?

    enum CodingKeys: String, CodingKey {
        case answer = "respuesta"
        case readiness
    }

    public init(answer: String, readiness: String? = nil) {
        self.answer = answer
        self.readiness = readiness
    }
}

/// Body de `POST /v1/gym/sessions/{id}/sets`.
public struct GymSetLogIn: Encodable, Sendable, Equatable {
    public let exerciseIndex: Int
    public let repetitions: Int
    public let weightKg: Double?

    enum CodingKeys: String, CodingKey {
        case exerciseIndex = "ejercicio_idx"
        case repetitions = "repeticiones"
        case weightKg = "peso_kg"
    }

    public init(exerciseIndex: Int, repetitions: Int, weightKg: Double? = nil) {
        self.exerciseIndex = exerciseIndex
        self.repetitions = repetitions
        self.weightKg = weightKg
    }

    // `encode(to:)` a mano para que `peso_kg` SIEMPRE viaje (como `null`
    // cuando no hay peso), exactamente el contrato del backend. El encode
    // sintetizado lo omitiría cuando es `nil` y un Pydantic que lo declare
    // requerido (aunque nullable) rechazaría el body.
    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(exerciseIndex, forKey: .exerciseIndex)
        try container.encode(repetitions, forKey: .repetitions)
        try container.encode(weightKg, forKey: .weightKg)
    }
}