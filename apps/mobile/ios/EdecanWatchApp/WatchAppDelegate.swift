import WatchKit

final class WatchAppDelegate: NSObject, WKApplicationDelegate {
    func handle(_ backgroundTasks: Set<WKRefreshBackgroundTask>) {
        for tarea in backgroundTasks {
            switch tarea {
            case let refresco as WKApplicationRefreshBackgroundTask:
                Task { await WatchSalud.compartido.refrescar() }
                programarRefresco()
                refresco.setTaskCompletedWithSnapshot(false)
            default:
                tarea.setTaskCompletedWithSnapshot(false)
            }
        }
    }

    func applicationDidFinishLaunching() {
        programarRefresco()
    }

    private func programarRefresco() {
        WKApplication.shared().scheduleBackgroundRefresh(
            withPreferredDate: Date().addingTimeInterval(15 * 60),
            userInfo: nil
        ) { _ in }
    }
}
