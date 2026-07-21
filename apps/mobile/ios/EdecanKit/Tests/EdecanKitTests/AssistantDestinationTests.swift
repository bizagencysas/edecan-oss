import Testing
@testable import EdecanKit

@Suite("Navegación assistant-first")
struct AssistantDestinationTests {
    @Test("expone solo asistente, actividad y perfil")
    func primaryDestinations() {
        #expect(AssistantDestination.allCases == [.edecan, .activity, .settings])
    }
}
