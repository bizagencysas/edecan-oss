import Testing
@testable import EdecanKit

@Test func notificationRouteUsaAllowlist() {
    #expect(NotificationRoute.parse(userInfo: ["route": "activity"]) == .activity)
    #expect(NotificationRoute.parse(userInfo: ["screen": "settings"]) == .settings)
    #expect(NotificationRoute.parse(userInfo: ["route": "https://evil.test"]) == .assistant)
    #expect(NotificationRoute.parse(userInfo: [:]) == .assistant)
}
