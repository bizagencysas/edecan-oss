import Foundation

// MARK: - `/v1/automations` (`ARCHITECTURE.md` §11/`ROADMAP_V2.md`
// §7.4/§7.6/§7.10, `apps/api/edecan_api/routers/automations.py`) — reglas de
// agenda o webhook que corren una instrucción del agente sin supervisión.
// Este cliente móvil solo CREA disparadores `kind="schedule"` (agenda por
// `rrule` RFC 5545, ver `APIClient.createAutomation`) — dar de alta un
// webhook desde el teléfono queda fuera de alcance de esta app (sigue
// viviendo en el panel web), aunque sí decodifica automatizaciones
// existentes de tipo webhook para poder listarlas/mostrarlas.

/// `AutomationOut.trigger` — unión discriminada por `"kind"`, mismo criterio
/// que ``ChatEvent``: solo `Decodable` (este cliente nunca reenvía un
/// `AutomationOut.trigger` tal cual; para crear una automatización arma su
/// propio cuerpo `{"kind": "schedule", "rrule": ...}` a mano en
/// `APIClient.createAutomation`). `hasSecret`/`hookUrl` de `.webhook` son la
/// forma YA REDACTADA que devuelve `automations.py::_public_automation` — el
/// secreto real (si se acaba de generar) solo viaja en
/// `AutomationOut.hookSecret`, nunca aquí.
public enum AutomationTrigger: Decodable, Sendable, Equatable {
    /// `descripcion` es la `rrule` en cristiano ("Todos los días a las 4:00
    /// UTC"), armada por el SERVIDOR
    /// (`automations.py::_public_automation` → `edecan_automations.describe`)
    /// y nunca acá: la frase lleva HORA, y sólo el backend sabe en qué zona
    /// corre de verdad la regla. Por eso viene rotulada UTC y NO se convierte
    /// a la del teléfono — el instante concreto en hora local ya lo da
    /// ``AutomationOut/nextRunAt`` aparte, que sí es absoluto.
    ///
    /// `nil` cuando el servidor no pudo traducirla con certeza (`UNTIL`, "el
    /// último viernes del mes", …) o cuando es anterior a este campo: en ese
    /// caso se muestra la `rrule` cruda, nunca una hora aproximada — ver
    /// ``resumen``.
    case schedule(rrule: String, descripcion: String?)
    case webhook(hasSecret: Bool, hookUrl: String)

    enum CodingKeys: String, CodingKey {
        case kind, rrule, descripcion
        case hasSecret = "has_secret"
        case hookUrl = "hook_url"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let kind = try container.decode(String.self, forKey: .kind)
        switch kind {
        case "schedule":
            self = .schedule(
                rrule: try container.decode(String.self, forKey: .rrule),
                descripcion: try container.decodeIfPresent(String.self, forKey: .descripcion)
            )
        case "webhook":
            self = .webhook(
                hasSecret: try container.decodeIfPresent(Bool.self, forKey: .hasSecret) ?? false,
                hookUrl: try container.decodeIfPresent(String.self, forKey: .hookUrl) ?? ""
            )
        default:
            throw DecodingError.dataCorruptedError(
                forKey: .kind, in: container,
                debugDescription: "Trigger de automatización desconocido: \(kind)"
            )
        }
    }

    /// Fallback cuando el servidor no supo traducir la `rrule`: honesto y
    /// corto para una lista (el detalle además enseña la regla cruda).
    /// Espejo de `AGENDA_SIN_TRADUCIR` en
    /// `apps/web/src/lib/api-automatizaciones.ts`.
    public static let agendaSinTraducir = "Agenda personalizada"

    /// Una línea que dice cuándo corre esta automatización. NO arma la frase:
    /// para las de agenda usa la que ya vino del servidor (ver
    /// ``schedule(rrule:descripcion:)``), de modo que el teléfono y la web
    /// digan exactamente lo mismo.
    public var resumen: String {
        switch self {
        case .schedule(_, let descripcion):
            let texto = descripcion?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            return texto.isEmpty ? Self.agendaSinTraducir : texto
        case .webhook:
            return "Se dispara por webhook entrante"
        }
    }
}

/// `AutomationOut.accion` — única variante hoy (`kind == "agent_instruction"`,
/// `edecan_automations.engine.validate_accion`), así que a diferencia de
/// ``AutomationTrigger`` no hace falta una unión discriminada.
public struct AutomationAccion: Decodable, Sendable, Equatable {
    public let kind: String
    public let instruccion: String
    public let agente: String?
}

/// Fila pública de `automations` (`automations.py::_public_automation` — sin
/// un `BaseModel` de Pydantic pinned, el router arma un `dict` a mano, ver su
/// docstring; forma verificada campo por campo contra esa función).
/// `hookSecret` SOLO viene poblado en la respuesta puntual del `POST`/`PATCH`
/// que lo generó — nunca en `GET`/`list`.
///
/// `enabled` es el ÚNICO campo `var` (el resto son `let`, igual criterio que
/// el resto de este archivo): `AutomatizacionesViewModel.alternar` lo muta
/// in-place para el update optimista del toggle, revirtiéndolo si el
/// servidor rechaza el cambio — ver su docstring.
public struct AutomationOut: Decodable, Sendable, Equatable, Identifiable {
    public let id: String
    public let nombre: String
    public let descripcion: String
    public let trigger: AutomationTrigger
    public let accion: AutomationAccion
    public var enabled: Bool
    public let nextRunAt: Date?
    public let lastRunAt: Date?
    public let createdAt: Date
    public let updatedAt: Date
    public let hookSecret: String?

    enum CodingKeys: String, CodingKey {
        case id, nombre, descripcion, trigger, accion, enabled
        case nextRunAt = "next_run_at"
        case lastRunAt = "last_run_at"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case hookSecret = "hook_secret"
    }
}

/// Una corrida de `automation_runs`
/// (`automations.py::list_automation_runs`). `status` ∈
/// `running|done|error|waiting_confirmation` — `String` crudo, mismo
/// criterio que `MissionOut.status`. `detalle` es forma libre
/// (`jsonb`, escrita por `automation_scan.py`/`hooks.py`): puede traer
/// `"resultado"`/`"error"`/`"pendiente"` según cómo terminó la corrida.
public struct AutomationRunOut: Decodable, Sendable, Equatable, Identifiable {
    public let id: String
    public let status: String
    public let detalle: [String: JSONValue]
    public let startedAt: Date?
    public let finishedAt: Date?

    enum CodingKeys: String, CodingKey {
        case id, status, detalle
        case startedAt = "started_at"
        case finishedAt = "finished_at"
    }
}
