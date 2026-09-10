import Sentry

/// Observabilidad de la app: Sentry (plan gratuito) captura crashes reales,
/// app hangs (watchdog/ANR de arranque) y terminations por watchdog — sin
/// necesitar el dispositivo conectado. El DSN es el endpoint público de
/// ingestion del proyecto; si queda vacío el SDK no arranca.
enum EdecanObservabilidad {
    static var dsn: String {
        Bundle.main.object(forInfoDictionaryKey: "EDECAN_SENTRY_DSN") as? String ?? ""
    }

    @MainActor
    static func arrancar() {
        guard !dsn.isEmpty else { return }
        SentrySDK.start { options in
            options.dsn = dsn
            options.tracesSampleRate = 0.1
            options.enableAppHangTracking = true
            options.appHangTimeoutInterval = 2.0
            options.enableWatchdogTerminationTracking = true
            options.attachStacktrace = true
            options.enableAutoBreadcrumbTracking = true
            options.enableNetworkTracking = true
        }
    }
}
