import Foundation

/// Identidad de un único mensaje lógico. Reintentar transporte conserva la
/// instancia; redactar/enviar otro mensaje crea una nueva.
public struct LogicalChatAttempt: Sendable, Equatable {
    public let idempotencyKey: UUID

    public init(idempotencyKey: UUID = UUID()) {
        self.idempotencyKey = idempotencyKey
    }

    public var headerValue: String {
        idempotencyKey.uuidString.lowercased()
    }
}

/// Guardia pequeña y determinista para una interacción push-to-talk.
/// Un permiso asíncrono solo puede iniciar audio si la misma presión continúa
/// activa; soltar, cancelar o comenzar otra presión invalida el token anterior.
public struct PushToTalkGate: Sendable, Equatable {
    public private(set) var generation: UInt64 = 0
    public private(set) var isPressed = false

    public init() {}

    @discardableResult
    public mutating func press() -> UInt64 {
        generation &+= 1
        isPressed = true
        return generation
    }

    public mutating func release() {
        isPressed = false
    }

    public mutating func cancel() {
        isPressed = false
        generation &+= 1
    }

    public func accepts(_ token: UInt64) -> Bool {
        isPressed && generation == token
    }

    public func isCurrent(_ token: UInt64) -> Bool {
        generation == token
    }
}

/// Único dueño de los borradores y del último hilo local. Todas sus claves
/// comparten prefijo para que un cierre/expiración pueda borrar el conjunto
/// completo sin tocar preferencias ajenas de la app.
public struct ChatLocalStateStore {
    public static let storagePrefix = "edecan.chat."

    private enum Key {
        static let currentConversation = "\(ChatLocalStateStore.storagePrefix)currentConversationId"
        static let draftPrefix = "\(ChatLocalStateStore.storagePrefix)draft."
    }

    private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    public var currentConversationId: String? {
        get {
            guard let value = defaults.string(forKey: Key.currentConversation), !value.isEmpty else { return nil }
            return value
        }
        nonmutating set {
            if let newValue, !newValue.isEmpty {
                defaults.set(newValue, forKey: Key.currentConversation)
            } else {
                defaults.removeObject(forKey: Key.currentConversation)
            }
        }
    }

    public func saveDraft(_ text: String, conversationId: String?) {
        let key = draftKey(conversationId)
        if text.isEmpty { defaults.removeObject(forKey: key) }
        else { defaults.set(text, forKey: key) }
    }

    public func draft(conversationId: String?) -> String {
        defaults.string(forKey: draftKey(conversationId)) ?? ""
    }

    public func clearAll() {
        for key in defaults.dictionaryRepresentation().keys where key.hasPrefix(Self.storagePrefix) {
            defaults.removeObject(forKey: key)
        }
    }

    private func draftKey(_ conversationId: String?) -> String {
        "\(Key.draftPrefix)\(conversationId ?? "new")"
    }
}
