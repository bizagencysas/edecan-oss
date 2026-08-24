import XCTest
@testable import EdecanKit

final class ChatSecretRedactionTests: XCTestCase {
    func testRedactsOpenAIProjectKeyBeforeRendering() {
        let secret = "sk-proj-example-secret-1234567890"
        let result = ChatSecretRedaction.redact("Configura OpenAI con \(secret)")

        XCTAssertEqual(result, "Configura OpenAI con [credencial protegida]")
        XCTAssertFalse(result.contains(secret))
    }

    func testLeavesOrdinaryConversationUntouched() {
        let text = "Crea una imagen de una chica en un balcón."
        XCTAssertEqual(ChatSecretRedaction.redact(text), text)
    }
}
