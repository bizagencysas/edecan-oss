import Foundation

/// Utilidades compartidas para refrescar la pantalla de entrenamiento: ignorar
/// cancelaciones cooperativas y sondear el collage cuando el backend lo genera
/// en segundo plano (~30–60 s después del check-in).
public enum GymRefreshSupport {
    /// `true` para cancelación cooperativa de Swift (`CancellationError`) o de
    /// URLSession (`URLError.cancelled`). Ninguna debe llegar a `errorMensaje`.
    public static func esCancelacion(_ error: Error) -> Bool {
        if error is CancellationError { return true }
        return (error as? URLError)?.code == .cancelled
    }

    /// Asigna `errorMensaje` solo si el error es real. Devuelve `false` cuando
    /// la cancelación se ignora a propósito.
    @discardableResult
    public static func asignarError(
        _ error: Error,
        a errorMensaje: inout String?
    ) -> Bool {
        guard !esCancelacion(error) else { return false }
        errorMensaje = error.localizedDescription
        return true
    }
}

/// Sondeo best-effort del `imagen_file_id` del plan: el collage se genera en
/// background en el backend y puede tardar decenas de segundos.
public struct GymCollagePoller: Sendable {
    public let maxAttempts: Int
    public let intervalNanoseconds: UInt64

    public init(maxAttempts: Int = 5, intervalNanoseconds: UInt64 = 6_000_000_000) {
        self.maxAttempts = max(1, maxAttempts)
        self.intervalNanoseconds = intervalNanoseconds
    }

    /// Espera `intervalNanoseconds` entre intentos y devuelve el primer
    /// `fileId` no vacío, o `nil` si se agotan los intentos o hay cancelación.
    public func poll(
        fetchFileID: @Sendable () async throws -> String?,
        sleep: @Sendable (_ nanoseconds: UInt64) async throws -> Void = {
            try await Task.sleep(nanoseconds: $0)
        },
        isCancelled: @Sendable () -> Bool = { Task.isCancelled }
    ) async -> String? {
        for _ in 0..<maxAttempts {
            if isCancelled() { return nil }
            do {
                try await sleep(intervalNanoseconds)
            } catch {
                if GymRefreshSupport.esCancelacion(error) { return nil }
            }
            if isCancelled() { return nil }
            do {
                if let fileId = try await fetchFileID()?.trimmingCharacters(in: .whitespacesAndNewlines),
                   !fileId.isEmpty {
                    return fileId
                }
            } catch {
                if GymRefreshSupport.esCancelacion(error) { return nil }
            }
        }
        return nil
    }
}
