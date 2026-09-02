import Foundation

// MARK: - Memoria (`apps/api/edecan_api/routers/memory.py`)
//
// `GET /v1/memory` admite dos espacios: `namespace=user` (fila plana de
// `memory_items`, con `id` y `source_trust`, borrable una a una) y
// `namespace=agent:<id>` (dict `persistent_agents.memory`, sin `id` por ítem:
// el router lo devuelve como pares `key`/`value`, no es borrable por
// `/v1/memory/{id}`). Dos modelos distintos, mismo criterio: formas
// verificadas campo por campo contra `memory.py`.

/// Una fila de `memory_items` (`namespace=user`) — espejo de
/// `memory.py::_memory_out`. Es la ÚNICA que trae `id`, así que solo estas
/// soportan `DELETE /v1/memory/{id}`.
public struct MemoryItem: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let kind: String?
    public let content: String?
    public let importance: Double?
    public let confidence: Double?
    public let source: String?
    public let namespace: String?
    public let sourceTrust: String?
    public let expiresAt: Date?
    public let createdAt: Date?

    enum CodingKeys: String, CodingKey {
        case id, kind, content, importance, confidence, source, namespace
        case sourceTrust = "source_trust"
        case expiresAt = "expires_at"
        case createdAt = "created_at"
    }
}

/// Un par `key`/`value` de la memoria de un worker persistente
/// (`namespace=agent:<id>`, `memory.py::list_memory` rama agente). `value` es
/// forma libre (`JSONValue`) — puede ser texto o un objeto; no hay `id` que
/// borrar, por eso esta lista no ofrece "olvidar".
public struct AgentMemoryEntry: Codable, Sendable, Equatable, Identifiable {
    public let key: String
    public let value: JSONValue
    public let namespace: String?

    public var id: String { key }

    /// Texto legible del valor para mostrar en una fila. Si `value` es un
    /// objeto con claves `content`/`source_trust`, se las aprovecha igual que
    /// el espacio `user`; si no, se muestra el valor tal cual.
    public var contentText: String {
        if case .object(let obj) = value {
            if case .string(let contenido)? = obj["content"], !contenido.isEmpty {
                return contenido
            }
            if case .string(let texto)? = obj["value"], !texto.isEmpty {
                return texto
            }
            return value.vistaPrevia
        }
        if case .string(let texto) = value { return texto }
        return value.vistaPrevia
    }

    public var sourceTrustText: String? {
        guard case .object(let obj) = value else { return nil }
        if case .string(let confianza)? = obj["source_trust"] { return confianza }
        if case .string(let fuente)? = obj["source"] { return fuente }
        return nil
    }
}

// MARK: - Aprobaciones (`apps/api/edecan_api/routers/approvals.py`)

/// Fila pública de `pending_approvals` (`approvals.py::_public_row`) — la
/// acción `dangerous` que el chat dejó esperando el OK de la persona.
/// `args` es forma libre (`JSONValue`) porque cada herramienta declara sus
/// propios argumentos. `name` es el nombre de la tool a aprobar.
public struct PendingApproval: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let conversationId: String?
    public let toolCallId: String?
    public let name: String?
    public let args: [String: JSONValue]?
    public let status: String?
    public let createdAt: Date?
    public let decidedAt: Date?

    enum CodingKeys: String, CodingKey {
        case id, name, args, status
        case conversationId = "conversation_id"
        case toolCallId = "tool_call_id"
        case createdAt = "created_at"
        case decidedAt = "decided_at"
    }

    /// Vista corta de los argumentos para pintar en una fila sin volcar
    /// secretos ni JSON crudo — solo `clave: valor` legible.
    public var argsPreview: String {
        guard let args, !args.isEmpty else { return "" }
        return args.map { "\($0.key): \($0.value.vistaPrevia)" }
            .sorted()
            .joined(separator: " · ")
    }
}

// MARK: - Sugerencias de rutinas (`automations.py::list_automation_suggestions`)

/// Una sugerencia de revisión devuelta por `GET /v1/automations/suggestions` —
/// nunca crea ni activa nada. Dos variantes (`kind`):
/// - `automation_suggestion`: una automatización con fallas consecutivas
///   (`automation_id`, `failure_count`, `nombre`, `enabled`).
/// - `routine_suggestion`: una tarea repetida en las misiones recientes
///   (`task`, `repetitions`).
///
/// `stage` (contrato en paralelo) clasifica la madurez de cada ítem:
/// `observation` (hallazgo pasivo), `suggestion` (propuesta accionable),
/// `draft` (vista previa de lo que se armaría) y `action` (requiere un paso
/// tuyo). Se decodifica opcional: un servidor que todavía no lo manda no debe
/// romper la lista.
public struct AutomationSuggestion: Codable, Sendable, Equatable, Identifiable {
    public let kind: String?
    public let action: String?
    public let reason: String?
    public let automationId: String?
    public let failureCount: Int?
    public let nombre: String?
    public let enabled: Bool?
    public let task: String?
    public let repetitions: Int?
    public let stage: String?
    public let agentId: String?

    enum CodingKeys: String, CodingKey {
        case kind, action, reason, nombre, enabled, task, stage
        case automationId = "automation_id"
        case failureCount = "failure_count"
        case repetitions = "repetitions"
        case agentId = "agent_id"
    }

    public var id: String {
        automationId ?? task ?? reason ?? "sugerencia"
    }

    /// Título legible para una fila de la lista.
    public var titulo: String {
        if let task, !task.isEmpty { return task }
        if let nombre, !nombre.isEmpty { return nombre }
        return "Rutina"
    }
}

/// Clasifica el `stage` de una ``AutomationSuggestion`` para pintarla con la
/// intensidad visual que le corresponde: una `observation` es sutil, un
/// `action` llama la atención. `nil` (servidor sin el campo) cae a `suggestion`.
public enum SuggestionStage: String, Sendable {
    case observation
    case suggestion
    case draft
    case action

    public init(_ raw: String?) {
        self = SuggestionStage(rawValue: raw?.lowercased() ?? "") ?? .suggestion
    }
}

// MARK: - Sugerencias de memoria (`memory.py`, contrato en paralelo)

/// Una sugerencia de memoria devuelta por `GET /v1/memory/suggestions` —
/// solo lectura, nunca guarda nada. El usuario decide con Guardar (→
/// `POST /v1/memory`) o Ignorar. `scope` es el espacio al que apunta la
/// sugerencia (`user` por defecto); `confidence` la confianza declarada.
public struct MemorySuggestion: Codable, Sendable, Equatable, Identifiable {
    public let text: String
    public let source: String?
    public let scope: String?
    public let confidence: Double?

    enum CodingKeys: String, CodingKey {
        case text, source, scope, confidence
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        text = try container.decode(String.self, forKey: .text)
        source = try container.decodeIfPresent(String.self, forKey: .source)
        scope = try container.decodeIfPresent(String.self, forKey: .scope)
        confidence = try container.decodeIfPresent(Double.self, forKey: .confidence)
    }

    /// Identidad estable para `ForEach` sin `id` real del backend: el propio
    /// texto + fuente, únicos campos que definen de qué se habla.
    public var id: String {
        "\(source ?? "user")|\(scope ?? "user")|\(text)"
    }
}

// MARK: - Mensajes entre agentes (`/v1/agents/messages`, contrato en paralelo)

/// Un mensaje persistido entre agentes (`GET/POST /v1/agents/messages`). Se
/// decodifica todo tolerante (salvo `id`): el router está aterrizando y una
/// respuesta parcial no debe tumbar la lista. `sender_agent_id` vacío o
/// `"user"`/`"owner"` se dibuja como la persona dueña.
public struct AgentMessage: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let senderAgentId: String?
    public let senderName: String?
    public let receiverAgentId: String?
    public let recipientName: String?
    public let messageType: String?
    public let goal: String?
    public let expectedOutput: String?
    public let status: String?
    public let createdAt: Date?

    enum CodingKeys: String, CodingKey {
        case id
        case senderAgentId = "sender_agent_id"
        case senderName = "sender_name"
        case receiverAgentId = "receiver_agent_id"
        case recipientName = "recipient_name"
        case messageType = "message_type"
        case goal = "goal"
        case expectedOutput = "expected_output"
        case status
        case createdAt = "created_at"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(String.self, forKey: .id)
        senderAgentId = try container.decodeIfPresent(String.self, forKey: .senderAgentId)
        senderName = try container.decodeIfPresent(String.self, forKey: .senderName)
        receiverAgentId = try container.decodeIfPresent(String.self, forKey: .receiverAgentId)
        recipientName = try container.decodeIfPresent(String.self, forKey: .recipientName)
        messageType = try container.decodeIfPresent(String.self, forKey: .messageType)
        goal = try container.decodeIfPresent(String.self, forKey: .goal)
        expectedOutput = try container.decodeIfPresent(String.self, forKey: .expectedOutput)
        status = try container.decodeIfPresent(String.self, forKey: .status)
        createdAt = try? container.decode(Date.self, forKey: .createdAt)
    }

    /// `true` si este mensaje lo escribió la persona dueña (no un agente).
    public var esDelDueno: Bool {
        guard let senderAgentId else { return false }
        return senderAgentId == "user" || senderAgentId == "owner" || senderAgentId == "human"
    }
}

// MARK: - "Enseñar una tarea" (`apps/api/edecan_api/routers/skills.py` §teach)

/// Un paso capturado de una tarea (`skills.py::TeachStepIn`). Mismo struct
/// para codificar (al agregar un paso) y decodificar (al leer `pasos`).
public struct TeachStep: Codable, Sendable, Equatable, Identifiable {
    public var action: String
    public var selector: String
    public var decision: String
    public var input: String
    public var output: String

    public var id: String { action + selector + decision + input + output }

    public init(
        action: String = "",
        selector: String = "",
        decision: String = "",
        input: String = "",
        output: String = ""
    ) {
        self.action = action
        self.selector = selector
        self.decision = decision
        self.input = input
        self.output = output
    }
}

/// Sesión de enseñanza (`skills.py::_session_out`) — `pasos` es la lista
/// acumulada de pasos; `draftSkillId` se puebla recién al terminar.
public struct TeachSession: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let nombre: String?
    public let descripcion: String?
    public let status: String?
    public let pasos: [TeachStep]
    public let draftSkillId: String?
    public let createdAt: Date?
    public let updatedAt: Date?

    enum CodingKeys: String, CodingKey {
        case id, nombre, descripcion, status, pasos
        case draftSkillId = "draft_skill_id"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(String.self, forKey: .id)
        nombre = try container.decodeIfPresent(String.self, forKey: .nombre)
        descripcion = try container.decodeIfPresent(String.self, forKey: .descripcion) ?? ""
        status = try container.decodeIfPresent(String.self, forKey: .status)
        pasos = try container.decodeIfPresent([TeachStep].self, forKey: .pasos) ?? []
        draftSkillId = try container.decodeIfPresent(String.self, forKey: .draftSkillId)
        createdAt = try container.decodeIfPresent(Date.self, forKey: .createdAt)
        updatedAt = try container.decodeIfPresent(Date.self, forKey: .updatedAt)
    }
}

/// La skill `draft` que devuelven `POST /v1/skills/teach/{id}/finish` y
/// `POST /v1/skills/{id}/approve` (`skills.py::_detail`) — solo los campos
/// que este flujo necesita mostrar; el resto se ignora al decodificar.
public struct TeachSkillDetail: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let nombre: String
    public let descripcion: String
    public let status: String
    public let enabled: Bool

    enum CodingKeys: String, CodingKey {
        case id, nombre, descripcion, status, enabled
    }
}