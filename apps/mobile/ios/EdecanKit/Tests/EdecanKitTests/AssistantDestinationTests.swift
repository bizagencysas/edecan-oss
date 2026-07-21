import Testing
@testable import EdecanKit

@Suite("Navegación assistant-first")
struct AssistantDestinationTests {
    @Test("expone chat, estudio, remoto, actividad y ajustes")
    func primaryDestinations() {
        #expect(AssistantDestination.allCases == [.edecan, .studio, .remote, .activity, .settings])
    }
}
