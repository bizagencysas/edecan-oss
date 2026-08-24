import Foundation

// MARK: - `/v1/missions` (`ARCHITECTURE.md` §11/`ROADMAP_V2.md` §7.4/§7.9,
// `edecan_schemas.missions`, `apps/api/edecan_api/routers/missions.py`) — el
// Orchestrator multi-agente: una misión por objetivo, planificada y
// ejecutada de forma ASÍNCRONA por el worker
// (`apps/worker/edecan_worker/handlers/run_mission.py`), nunca en el turno
// de la petición HTTP. A diferencia del chat, este router NO expone SSE
// (ver su docstring: "deliberadamente delgado... la ejecución real ocurre de
// forma asíncrona en el worker") — por eso ``MisionesViewModel`` hace
// *polling* sobre estos endpoints en vez de abrir un stream, mismo criterio
// que ya usa `apps/web/src/app/(app)/app/misiones/page.tsx`.

/// Un paso propuesto en `MissionOut.plan` — siempre `{"seq", "agente",
/// "instruccion"}` (`packages/agents/edecan_agents/orchestrator.py::Orchestrator.plan`,
/// verificado contra el código fuente: nunca una lista vacía, y esos 3 campos
/// son los únicos que escribe). Distinto de ``MissionStepOut``: este es solo
/// el plan propuesto al crear la misión, sin `status`/`resultado` todavía —
/// para eso está `MissionDetailOut.steps`, la fuente de verdad en vivo.
public struct MissionPlanStep: Codable, Sendable, Equatable {
    public let seq: Int
    public let agente: String
    public let instruccion: String
}

/// Fila pública de `agent_missions` — espejo EXACTO de
/// `edecan_schemas.missions.MissionOut` (mismo nombre, a propósito: es el
/// `response_model` real de `missions.py`). `plan`/`resultado` quedan `nil`
/// hasta que el Orchestrator los produce; `presupuesto` siempre trae al
/// menos `max_steps` (nunca vacío de verdad, `Field(default_factory=dict)`
/// solo cubre el caso límite de decodificación).
public struct MissionOut: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let tenantId: String
    public let userId: String
    public let objetivo: String
    /// `edecan_schemas.missions.MISSION_STATUSES`: `planning`, `running`,
    /// `waiting_confirmation`, `done`, `error`, `cancelled`. `String` crudo
    /// (no un enum Swift cerrado) — mismo criterio que `Factura.status`: si
    /// el backend suma un estado nuevo, decodificar no debe romperse.
    public let status: String
    public let plan: [MissionPlanStep]?
    public let resultado: String?
    public let presupuesto: [String: JSONValue]
    public let error: String?
    public let createdAt: Date
    public let updatedAt: Date

    enum CodingKeys: String, CodingKey {
        case id, objetivo, status, plan, resultado, presupuesto, error
        case tenantId = "tenant_id"
        case userId = "user_id"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }

    /// `presupuesto["max_steps"]` (`ARCHITECTURE.md`/`ROADMAP_V2.md` §7.5,
    /// `MISSIONS_MAX_STEPS`) — el único campo de `presupuesto` que la UI lee hoy.
    public var maxSteps: Int? {
        if case .number(let value)? = presupuesto["max_steps"] { return Int(value) }
        return nil
    }

    /// Misiones aún en curso: la UI hace *polling* mientras el status esté
    /// en este conjunto — mismo criterio que `ACTIVE_MISSION_STATUSES` en
    /// `apps/web/src/lib/api-misiones.ts`.
    public var estaActiva: Bool { status == "planning" || status == "running" }

    /// `true` si la misión ya terminó — no admite `confirm`/`cancel`.
    public var esTerminal: Bool { status == "done" || status == "error" || status == "cancelled" }
}

/// Fila pública de `agent_steps` — espejo EXACTO de
/// `edecan_schemas.missions.MissionStepOut`, un paso ejecutado por uno de
/// los perfiles de `edecan_agents.profiles`.
public struct MissionStepOut: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let tenantId: String
    public let missionId: String
    public let seq: Int
    public let agente: String
    public let instruccion: String
    /// `edecan_schemas.missions.MISSION_STEP_STATUSES`: `pending`, `running`,
    /// `waiting_confirmation`, `done`, `error`, `skipped`.
    public let status: String
    public let resultado: String?
    /// Forma libre (`dict[str, Any] | None` en el backend,
    /// `packages/agents/edecan_agents/orchestrator.py::_run_step`): tokens de
    /// uso (`input_tokens`/`output_tokens`) cuando `status == "done"`, o
    /// `{"pending_tool_call": {"id", "name", "args"}}` cuando `status ==
    /// "waiting_confirmation"` — ver ``pendingToolCall``.
    public let usage: [String: JSONValue]?
    public let createdAt: Date
    public let updatedAt: Date

    enum CodingKeys: String, CodingKey {
        case id, seq, agente, instruccion, status, resultado, usage
        case tenantId = "tenant_id"
        case missionId = "mission_id"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }

    /// La herramienta `dangerous` pendiente de aprobar/rechazar cuando
    /// `status == "waiting_confirmation"`. A diferencia del chat
    /// (`ChatViewModel.ConfirmacionPendiente`, poblado desde un evento SSE en
    /// vivo), este valor viene de la fila `agent_steps` ya guardada —
    /// `POST /v1/missions/{id}/confirm` no usa SSE (ver docstring del módulo).
    public var pendingToolCall: PendingToolCall? {
        guard case .object(let obj)? = usage?["pending_tool_call"] else { return nil }
        guard case .string(let id)? = obj["id"], case .string(let name)? = obj["name"] else { return nil }
        var args: [String: JSONValue] = [:]
        if case .object(let a)? = obj["args"] { args = a }
        return PendingToolCall(id: id, name: name, args: args)
    }

    public struct PendingToolCall: Sendable, Equatable {
        public let id: String
        public let name: String
        public let args: [String: JSONValue]
    }
}

/// `GET /v1/missions/{id}` — espejo EXACTO de
/// `apps/api/edecan_api/routers/missions.py::MissionDetailOut` (misma
/// intención de nombre: `{mission, steps}`).
public struct MissionDetailOut: Codable, Sendable, Equatable {
    public let mission: MissionOut
    public let steps: [MissionStepOut]
}

public struct PersistentWorker: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let name: String
    public let purpose: String
    public let status: String
    public let enabled: Bool
    public let workspace: String?
    public let updatedAt: Date?

    enum CodingKeys: String, CodingKey {
        case id, name, purpose, status, enabled, workspace
        case updatedAt = "updated_at"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try Self.decodeIdentificador(container, key: .id)
        name = try container.decode(String.self, forKey: .name)
        purpose = try container.decode(String.self, forKey: .purpose)
        status = try container.decode(String.self, forKey: .status)
        enabled = try container.decodeIfPresent(Bool.self, forKey: .enabled) ?? true
        workspace = try container.decodeIfPresent(String.self, forKey: .workspace)
        updatedAt = Self.decodeFecha(container, key: .updatedAt)
    }

    private static func decodeIdentificador(_ container: KeyedDecodingContainer<CodingKeys>, key: CodingKeys) throws -> String {
        if let value = try? container.decode(String.self, forKey: key) { return value }
        throw DecodingError.dataCorruptedError(forKey: key, in: container, debugDescription: "id inválido")
    }

    private static func decodeFecha(_ container: KeyedDecodingContainer<CodingKeys>, key: CodingKeys) -> Date? {
        if let date = try? container.decode(Date.self, forKey: key) { return date }
        return nil
    }
}

public struct WorkerHandoff: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let destinationWorkerId: String
    public let destinationName: String?
    public let taskId: String
    public let envelope: JSONValue?
    public let status: String
    public let createdAt: Date?

    enum CodingKeys: String, CodingKey {
        case id, status, envelope
        case destinationWorkerId = "destination_worker_id"
        case destinationName = "destination_name"
        case taskId = "task_id"
        case createdAt = "created_at"
    }

    public var instruction: String? {
        guard case .object(let object)? = envelope, case .string(let value)? = object["instruction"] else {
            if case .string(let value)? = envelope { return value }
            return nil
        }
        return value
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(String.self, forKey: .id)
        destinationWorkerId = try container.decode(String.self, forKey: .destinationWorkerId)
        destinationName = try container.decodeIfPresent(String.self, forKey: .destinationName)
        taskId = try container.decode(String.self, forKey: .taskId)
        envelope = try container.decodeIfPresent(JSONValue.self, forKey: .envelope)
        status = try container.decode(String.self, forKey: .status)
        createdAt = try? container.decode(Date.self, forKey: .createdAt)
    }
}

public struct WorkerTaskQueued: Codable, Sendable, Equatable {
    public let taskId: String
    enum CodingKeys: String, CodingKey { case taskId = "task_id" }
}
