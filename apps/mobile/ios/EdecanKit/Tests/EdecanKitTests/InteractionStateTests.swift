import Foundation
import Testing
@testable import EdecanKit

struct InteractionStateTests {
    @Test func intentoLogicoConservaLaMismaClaveSoloAlReintentar() throws {
        let uuid = try #require(UUID(uuidString: "95E4E7C5-61F6-4B35-86B8-255224623E25"))
        let attempt = LogicalChatAttempt(idempotencyKey: uuid)
        let retry = attempt

        #expect(retry == attempt)
        #expect(retry.headerValue == "95e4e7c5-61f6-4b35-86b8-255224623e25")
        #expect(LogicalChatAttempt().idempotencyKey != attempt.idempotencyKey)
    }

    @Test func toqueCortoConPermisoTardioNoIniciaGrabacion() async throws {
        var gate = PushToTalkGate()
        let token = gate.press()

        // El dedo sale antes de que el diálogo del sistema responda.
        gate.release()
        try await Task.sleep(for: .milliseconds(5))

        #expect(gate.accepts(token) == false)
        #expect(gate.isPressed == false)
    }

    @Test func permisoDePresionAnteriorNoAfectaUnaNueva() async throws {
        var gate = PushToTalkGate()
        let oldToken = gate.press()
        gate.release()
        let currentToken = gate.press()

        try await Task.sleep(for: .milliseconds(5))

        #expect(gate.accepts(oldToken) == false)
        #expect(gate.accepts(currentToken))
    }

    @Test func cierreDeVistaInvalidaPermisoEnVuelo() {
        var gate = PushToTalkGate()
        let token = gate.press()
        gate.cancel()

        #expect(gate.accepts(token) == false)
        #expect(gate.isCurrent(token) == false)
    }

    @Test func limpiezaDeSesionEliminaSoloEstadoLocalDelChat() throws {
        let suiteName = "InteractionStateTests.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suiteName))
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let store = ChatLocalStateStore(defaults: defaults)

        store.currentConversationId = "tenant-a-conversation"
        store.saveDraft("dato privado", conversationId: "tenant-a-conversation")
        store.saveDraft("nuevo privado", conversationId: nil)
        defaults.set("conservar", forKey: "edecan.appearance.theme")

        store.clearAll()

        #expect(store.currentConversationId == nil)
        #expect(store.draft(conversationId: "tenant-a-conversation").isEmpty)
        #expect(store.draft(conversationId: nil).isEmpty)
        #expect(defaults.string(forKey: "edecan.appearance.theme") == "conservar")
    }

    @Test func intentoPendienteSePersisteSinPromptCrudoYSeEliminaAlCompletar() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("PendingChatAttemptStore.\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = PendingChatAttemptStore(directoryURL: directory)
        let key = try #require(UUID(uuidString: "DE09B042-756A-44BB-A9F0-D95FB1B4A140"))
        let pending = PendingChatAttempt(
            idempotencyKey: key,
            conversationId: "conversation-1",
            localMessageId: "local-message-1"
        )

        try store.save(pending)

        #expect(try store.load() == pending)
        let restored = try #require(try store.load())
        #expect(restored.isRecoverable())

        store.clear()
        #expect(try store.load() == nil)
    }

    @Test func intentoPendienteExpiradoNoSeReanuda() {
        let pending = PendingChatAttempt(
            idempotencyKey: UUID(),
            conversationId: "conversation-1",
            localMessageId: "local-message-1",
            createdAt: Date(timeIntervalSinceNow: -(26 * 60 * 60))
        )

        #expect(pending.isRecoverable() == false)
    }
}
