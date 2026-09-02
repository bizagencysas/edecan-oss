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

    /// `true` si la misión sigue pidiendo atención en el Watch: en curso,
    /// esperando confirmación o pausada.
    public var visibleEnWatch: Bool {
        estaActiva || status == "waiting_confirmation" || status == "paused"
    }

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

    // Perfil rico (migración 0048, `persistent_agents.py`). Todos opcionales:
    // un worker viejo (o una respuesta incompleta) no debe romper el decode.
    public let displayName: String?
    /// `avatar` es un jsonb libre (`dict[str, Any]`). El cliente escribe
    /// `{"accent": "#RRGGBB"}`; se decodifica como objeto para tolerar
    /// cualquier forma que el backend devuelva.
    public let avatar: [String: JSONValue]?
    public let roleTitle: String?
    public let roleShort: String?
    public let jobDescription: String?
    public let personality: String?
    public let communicationStyle: String?
    public let instructions: String?
    public let constraints: String?
    public let approvalPolicy: [String: JSONValue]?
    public let autonomyLevel: String?
    public let modelPolicy: [String: JSONValue]?

    enum CodingKeys: String, CodingKey {
        case id, name, purpose, status, enabled, workspace, avatar, personality, instructions, constraints
        case displayName = "display_name"
        case roleTitle = "role_title"
        case roleShort = "role_short"
        case jobDescription = "job_description"
        case communicationStyle = "communication_style"
        case approvalPolicy = "approval_policy"
        case autonomyLevel = "autonomy_level"
        case modelPolicy = "model_policy"
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
        displayName = try container.decodeIfPresent(String.self, forKey: .displayName)
        avatar = try container.decodeIfPresent([String: JSONValue].self, forKey: .avatar)
        roleTitle = try container.decodeIfPresent(String.self, forKey: .roleTitle)
        roleShort = try container.decodeIfPresent(String.self, forKey: .roleShort)
        jobDescription = try container.decodeIfPresent(String.self, forKey: .jobDescription)
        personality = try container.decodeIfPresent(String.self, forKey: .personality)
        communicationStyle = try container.decodeIfPresent(String.self, forKey: .communicationStyle)
        instructions = try container.decodeIfPresent(String.self, forKey: .instructions)
        constraints = try container.decodeIfPresent(String.self, forKey: .constraints)
        approvalPolicy = try container.decodeIfPresent([String: JSONValue].self, forKey: .approvalPolicy)
        autonomyLevel = try container.decodeIfPresent(String.self, forKey: .autonomyLevel)
        modelPolicy = try container.decodeIfPresent([String: JSONValue].self, forKey: .modelPolicy)
    }

    /// Nombre para mostrar en el roster: `display_name` si existe y no está
    /// vacío; si no, `name`.
    public var nombreVisible: String {
        let candidato = displayName?.trimmingCharacters(in: .whitespacesAndNewlines)
        if let candidato, !candidato.isEmpty { return candidato }
        return name
    }

    /// Cargo para mostrar: `role_title` si existe; si no, cae a `purpose`.
    public var cargoVisible: String {
        let candidato = roleTitle?.trimmingCharacters(in: .whitespacesAndNewlines)
        if let candidato, !candidato.isEmpty { return candidato }
        return purpose
    }

    /// Hex (`#RRGGBB`) del acento del avatar, si el backend lo trae.
    public var avatarAccentHex: String? {
        if let avatar, case .string(let hex)? = avatar["fill"] { return hex }
        guard let avatar, case .string(let hex)? = avatar["accent"] else { return nil }
        return hex
    }

    /// Estilo del descriptor (`grok_face`, `geometric`, …).
    public var avatarStyle: String? {
        guard let avatar, case .string(let style)? = avatar["style"] else { return nil }
        return style
    }

    /// Forma geométrica (`circle`, `rounded_square`, `oval`, …) para `grok_face`.
    public var avatarShape: String? {
        guard let avatar, case .string(let shape)? = avatar["shape"] else { return nil }
        return shape
    }

    /// Relleno sólido del avatar Grok Bot.
    public var avatarFillHex: String? {
        guard let avatar, case .string(let hex)? = avatar["fill"] else { return nil }
        return hex
    }

    /// Ojos inclinados del descriptor `grok_face`.
    public struct AvatarEye: Sendable, Equatable {
        public let x: Double
        public let y: Double
        public let rx: Double
        public let ry: Double
        public let rotation: Double
    }

    public var avatarEyes: (left: AvatarEye?, right: AvatarEye?) {
        guard let avatar, case .object(let eyes)? = avatar["eyes"] else {
            return (nil, nil)
        }
        func parseEye(_ key: String) -> AvatarEye? {
            guard case .object(let eye)? = eyes[key] else { return nil }
            func num(_ k: String, default d: Double) -> Double {
                if case .number(let v)? = eye[k] { return v }
                return d
            }
            return AvatarEye(
                x: num("x", default: 0.5),
                y: num("y", default: 0.4),
                rx: num("rx", default: 0.055),
                ry: num("ry", default: 0.075),
                rotation: num("rotation", default: -22)
            )
        }
        return (parseEye("left"), parseEye("right"))
    }

    /// Iniciales explícitas del avatar (`avatar.initials`), si el backend las
    /// trae; `nil` si hay que derivarlas del nombre.
    public var avatarInitials: String? {
        guard let avatar, case .string(let letras)? = avatar["initials"] else { return nil }
        return letras
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
