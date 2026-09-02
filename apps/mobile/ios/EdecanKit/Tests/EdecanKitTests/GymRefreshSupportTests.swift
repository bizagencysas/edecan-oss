import Foundation
import Testing
@testable import EdecanKit

/// Cancelación cooperativa y sondeo del collage del gimnasio — la misma lógica
/// que usa ``GymViewModel`` vía ``GymRefreshSupport``/``GymCollagePoller``.
struct GymRefreshSupportTests {
    @Test func cancellationErrorNoAsignaErrorMensaje() {
        var mensaje: String? = "previo"
        let asignado = GymRefreshSupport.asignarError(CancellationError(), a: &mensaje)
        #expect(asignado == false)
        #expect(mensaje == "previo")
    }

    @Test func urlErrorCancelledNoAsignaErrorMensaje() {
        var mensaje: String?
        let asignado = GymRefreshSupport.asignarError(
            URLError(.cancelled),
            a: &mensaje
        )
        #expect(asignado == false)
        #expect(mensaje == nil)
    }

    @Test func errorRealSiAsignaMensaje() {
        var mensaje: String?
        let asignado = GymRefreshSupport.asignarError(
            URLError(.notConnectedToInternet),
            a: &mensaje
        )
        #expect(asignado == true)
        #expect(mensaje == URLError(.notConnectedToInternet).localizedDescription)
    }

    @Test func refreshExitosoLimpiaErrorAnterior() {
        var mensaje: String? = "The operation couldn't be completed."
        _ = GymRefreshSupport.asignarError(URLError(.timedOut), a: &mensaje)
        #expect(mensaje != nil)
        mensaje = nil
        #expect(mensaje == nil)
    }

    @Test func pollEncuentraFileIdEnSegundoIntento() async {
        let calls = LockedCounter()
        let poller = GymCollagePoller(maxAttempts: 4, intervalNanoseconds: 1)
        let fileId = await poller.poll(
            fetchFileID: {
                let n = calls.increment()
                return n >= 2 ? "collage-file-abc" : nil
            },
            sleep: { _ in }
        )
        #expect(fileId == "collage-file-abc")
        #expect(calls.value == 2)
    }

    @Test func pollRespetaCancelacionCooperativa() async {
        let poller = GymCollagePoller(maxAttempts: 4, intervalNanoseconds: 1)
        let fileId = await poller.poll(
            fetchFileID: { "nunca-deberia-usarse" },
            sleep: { _ in },
            isCancelled: { true }
        )
        #expect(fileId == nil)
    }

    @Test func pollIgnoraFileIdVacio() async {
        let calls = LockedCounter()
        let poller = GymCollagePoller(maxAttempts: 3, intervalNanoseconds: 1)
        let fileId = await poller.poll(
            fetchFileID: {
                _ = calls.increment()
                return "   "
            },
            sleep: { _ in }
        )
        #expect(fileId == nil)
        #expect(calls.value == 3)
    }
}

private final class LockedCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var count = 0

    func increment() -> Int {
        lock.withLock {
            count += 1
            return count
        }
    }

    var value: Int { lock.withLock { count } }
}
