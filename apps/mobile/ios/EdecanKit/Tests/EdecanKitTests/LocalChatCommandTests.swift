import XCTest
@testable import EdecanKit

final class LocalChatCommandTests: XCTestCase {
    func testRecognizesExactSlashClear() {
        XCTAssertEqual(LocalChatCommand.parse("/clear"), .clear)
    }

    func testIsCaseInsensitive() {
        XCTAssertEqual(LocalChatCommand.parse("/CLEAR"), .clear)
        XCTAssertEqual(LocalChatCommand.parse("/Clear"), .clear)
    }

    func testToleratesSurroundingWhitespaceAndNewlines() {
        XCTAssertEqual(LocalChatCommand.parse("  /clear  "), .clear)
        XCTAssertEqual(LocalChatCommand.parse("\n/clear\n"), .clear)
    }

    /// El disparador tiene que ser el mensaje COMPLETO: una frase que solo
    /// menciona el comando no debe reiniciar nada por accidente.
    func testDoesNotTriggerWhenSlashClearIsPartOfALongerMessage() {
        XCTAssertNil(LocalChatCommand.parse("¿qué hace /clear?"))
        XCTAssertNil(LocalChatCommand.parse("/clear por favor"))
        XCTAssertNil(LocalChatCommand.parse("porfa /clear"))
    }

    func testRecognizesBranchAndRewind() {
        XCTAssertEqual(LocalChatCommand.parse("/branch"), .branch)
        XCTAssertEqual(LocalChatCommand.parse("/rewind"), .rewind)
        XCTAssertEqual(LocalChatCommand.parse("  /BRANCH  "), .branch)
        XCTAssertNil(LocalChatCommand.parse("/branch ahora"))
    }

    func testOrdinaryMessagesAreNotCommands() {
        XCTAssertNil(LocalChatCommand.parse("Hola, ¿cómo estás?"))
        XCTAssertNil(LocalChatCommand.parse(""))
        XCTAssertNil(LocalChatCommand.parse("/fix el creador de PDF"))
    }
}
