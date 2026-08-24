import Testing
@testable import EdecanKit

@Suite("Navegación assistant-first")
struct AssistantDestinationTests {
    @Test("expone asistente, actividad, IDE y perfil")
    func primaryDestinations() {
        #expect(AssistantDestination.allCases == [.edecan, .activity, .equipo, .ide, .settings])
    }
}
