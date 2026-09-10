import Foundation
import Testing
@testable import EdecanKit

/// Persistencia local del check-in del gym (`GymCheckinEstadoLocal`): clave
/// por usuario y día, respuestas "si"/"no", y aislamiento entre cuentas.
@Suite(.serialized)
struct GymCheckinEstadoLocalTests {
    private let usuarioA = "user-a"
    private let usuarioB = "user-b"

    private func storeVacio() -> GymCheckinEstadoLocal {
        guard let defaults = UserDefaults(suiteName: "gym-checkin-tests") else {
            fatalError("suite no disponible")
        }
        defaults.removePersistentDomain(forName: "gym-checkin-tests")
        return GymCheckinEstadoLocal(defaults: defaults)
    }

    @Test func diaISOFormateaCeroIzquierda() throws {
        let store = GymCheckinEstadoLocal(defaults: .standard)
        let calendario = Calendar(identifier: .gregorian)
        var componentes = DateComponents()
        componentes.year = 2026
        componentes.month = 3
        componentes.day = 5
        let fecha = try #require(calendario.date(from: componentes))
        #expect(store.diaISO(fecha, calendario: calendario) == "2026-03-05")
    }

    @Test func marcarYLeerRoundTrip() {
        let store = storeVacio()
        #expect(store.respuesta(dia: "2026-09-05", usuarioID: usuarioA) == nil)

        store.marcar(respuesta: "si", dia: "2026-09-05", usuarioID: usuarioA)
        #expect(store.respuesta(dia: "2026-09-05", usuarioID: usuarioA) == "si")
    }

    @Test func respuestaNoTambienPersiste() {
        let store = storeVacio()
        store.marcar(respuesta: "no", dia: "2026-09-05", usuarioID: usuarioA)
        #expect(store.respuesta(dia: "2026-09-05", usuarioID: usuarioA) == "no")
    }

    @Test func otroUsuarioNoHereadaLaRespuesta() {
        let store = storeVacio()
        store.marcar(respuesta: "si", dia: "2026-09-05", usuarioID: usuarioA)
        // Cambio de cuenta: la respuesta del dueño anterior NO cuenta.
        #expect(store.respuesta(dia: "2026-09-05", usuarioID: usuarioB) == nil)
    }

    @Test func otroDiaNoHereadaLaRespuesta() {
        let store = storeVacio()
        store.marcar(respuesta: "si", dia: "2026-09-05", usuarioID: usuarioA)
        // Un check-in de un día no esconde la tarjeta de OTRO día.
        #expect(store.respuesta(dia: "2026-09-06", usuarioID: usuarioA) == nil)
        #expect(store.respuesta(dia: "2026-09-04", usuarioID: usuarioA) == nil)
    }

    @Test func claveConPrefijoEstableParaLimpieza() {
        let store = storeVacio()
        store.marcar(respuesta: "si", dia: "2026-09-05", usuarioID: usuarioA)
        // La clave vive bajo el prefijo compartido del store (para limpieza
        // de cuenta, mismo criterio que ChatLocalStateStore).
        let defaults = UserDefaults(suiteName: "gym-checkin-tests")!
        let claves = defaults.dictionaryRepresentation().keys.filter {
            $0.hasPrefix(GymCheckinEstadoLocal.storagePrefix)
        }
        #expect(claves.contains("gym.checkin.\(usuarioA).2026-09-05"))
    }
}