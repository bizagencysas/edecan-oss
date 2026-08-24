import Foundation
import Network
import Observation

/// Monitor de conectividad de red basado en `NWPathMonitor`.
///
/// Publica `estaConectado` (true cuando hay una ruta de red utilizable,
/// false cuando no). Vive como `@State`/`@Environment` en la vista que quiera
/// mostrar el indicador de "Sin conexión" — no en `SessionStore` a propósito:
/// la red del dispositivo es una señal de UI, no de sesión, y `EdecanKit` no
/// debe depender de `Network.framework`.
///
/// El monitor arranca en `true` y solo baja a `false` cuando el sistema
/// confirma que NO hay ruta. Así evitamos el parpadeo inicial durante el
/// primer `update` síncrono, que siempre llega `satisfied = false` antes de
/// la primera medición real.
@MainActor
@Observable
final class MonitorRed {
    private(set) var estaConectado = true
    private let monitor = NWPathMonitor()
    private let cola = DispatchQueue(label: "cc.edecan.monitor-red")

    init() {
        monitor.pathUpdateHandler = { [weak self] path in
            let conectado = path.status == .satisfied
            Task { @MainActor [weak self] in self?.estaConectado = conectado }
        }
        monitor.start(queue: cola)
    }

    deinit {
        monitor.cancel()
    }
}
