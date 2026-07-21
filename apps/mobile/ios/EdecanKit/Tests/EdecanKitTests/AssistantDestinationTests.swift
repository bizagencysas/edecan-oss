import Testing
@testable import EdecanKit

@Suite("Navegación assistant-first")
struct AssistantDestinationTests {
    @Test("expone solo Edecan, Actividad y Ajustes")
    func primaryDestinations() {
        #expect(AssistantDestination.allCases == [.edecan, .activity, .settings])
    }
}
