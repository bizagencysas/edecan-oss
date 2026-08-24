import Foundation
import Testing
@testable import EdecanKit

@Suite(.serialized)
struct WidgetSnapshotStoreTests {
    @Test func snapshotEsCodableYNoAceptaConteosNegativos() throws {
        let snapshot = EdecanWidgetSnapshot(
            activeMissionCount: -2,
            pendingReminderCount: 3,
            nextReminderAt: Date(timeIntervalSince1970: 1_000),
            updatedAt: Date(timeIntervalSince1970: 2_000)
        )
        #expect(snapshot.activeMissionCount == 0)
        #expect(snapshot.pendingReminderCount == 3)

        let data = try JSONEncoder().encode(snapshot)
        let restored = try JSONDecoder().decode(EdecanWidgetSnapshot.self, from: data)
        #expect(restored == snapshot)
    }
}

