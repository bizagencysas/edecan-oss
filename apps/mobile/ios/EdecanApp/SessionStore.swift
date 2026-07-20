import Foundation
import Observation
import UIKit
import EdecanKit

/// Punto único de acceso a la sesión activa de la app. `APIClient` (en
/// EdecanKit) es un `actor` sin URL fija hasta que se construye — este
/// store lo crea/recrea cuando cambia la URL del servidor
/// (`PairingStore.serverURL`, paso 1 del onboarding) y cachea el último
/// `Me` cargado para que Inicio/Perfil no tengan que volver a pedirlo cada
/// vez que se muestra la pestaña.
///
/// Vive en `EdecanApp` (no en `EdecanKit`) a propósito: es estado de
/// PANTALLA (qué mostrar mientras carga, el último error legible), no un
/// contrato de red — `EdecanWidgets` no lo necesita.
@MainActor
@Observable
public final class SessionStore {
    public private(set) var client: APIClient?
    public private(set) var me: Me?
    public var errorMensaje: String?
    public private(set) var cargandoMe = false
    public private(set) var sesionValida = true
    private var clientGeneration = 0
    private var contentEpoch = 0

    public init() {}

    /// Se llama al arrancar la app y cada vez que el onboarding guarda o
    /// cambia la URL del servidor. `nil` (sin servidor todavía) deja
    /// `client` en `nil` — las pantallas que lo necesiten deben tratarlo
    /// como "sin sesión utilizable".
    public func actualizarBaseURL(_ url: URL?) {
        clientGeneration += 1
        contentEpoch += 1
        me = nil
        guard let url else {
            client = nil
            return
        }
        let generation = clientGeneration
        client = APIClient(
            baseURL: url,
            onSessionExpired: { [weak self] in
                await self?.manejarExpiracionGlobal(clientGeneration: generation)
            }
        )
    }

    /// `GET /v1/me` — se llama justo tras el login y cada vez que Inicio o
    /// Perfil aparecen en pantalla (`ChatView`/`RootTabView` no lo
    /// necesitan). Silencioso ante error de red: deja `errorMensaje` para
    /// que la pantalla decida cómo mostrarlo, nunca lanza.
    @discardableResult
    public func cargarMe() async -> Me? {
        guard let client else { return nil }
        let generation = clientGeneration
        let requestEpoch = contentEpoch
        cargandoMe = true
        defer { cargandoMe = false }
        do {
            let me = try await client.me()
            guard clientGeneration == generation, contentEpoch == requestEpoch, self.client === client else {
                return nil
            }
            self.me = me
            sesionValida = true
            errorMensaje = nil
            return me
        } catch APIClient.APIError.sesionExpirada {
            guard clientGeneration == generation, contentEpoch == requestEpoch, self.client === client else {
                return nil
            }
            manejarExpiracionGlobal(clientGeneration: generation)
            return nil
        } catch {
            guard clientGeneration == generation, contentEpoch == requestEpoch, self.client === client else {
                return nil
            }
            errorMensaje = error.localizedDescription
            return nil
        }
    }

    public func marcarSesionValida() {
        contentEpoch += 1
        me = nil
        sesionValida = true
    }

    private func manejarExpiracionGlobal(clientGeneration generation: Int) {
        guard generation == clientGeneration else { return }
        contentEpoch += 1
        me = nil
        sesionValida = false
        errorMensaje = APIClient.APIError.sesionExpirada.localizedDescription
    }

    /// Cierra la sesión en memoria y en el Keychain (vía `APIClient`). NO
    /// toca `PairingStore` — quien llama (`PerfilView`) es responsable de
    /// también llamar a `PairingStore.olvidarEmparejamiento()` para volver
    /// a mostrar el onboarding; se mantienen separados porque
    /// `PairingStore` vive en `EdecanKit` (lo comparte `EdecanWidgets`) y
    /// este store es específico de `EdecanApp`.
    ///
    /// `deviceId`, si no es `nil`, dispara además `POST /v1/devices/{id}/revoke`
    /// en segundo plano — best-effort (nunca bloquea ni falla el cierre de
    /// sesión: WP-V4-01 puede no haber aterrizado, o puede fallar por red).
    public func cerrarSesion(deviceId: String? = nil) async {
        contentEpoch += 1
        me = nil
        errorMensaje = nil
        sesionValida = false
        guard let client else { return }
        await client.cerrarSesion(deviceId: deviceId)
    }

    // MARK: - Emparejamiento por dispositivo (WP-V4-01, contrato en paralelo)

    /// Se llama justo tras un login/registro exitoso — registra ESTE
    /// teléfono en `devices` (`POST /v1/devices`) y guarda el `id` devuelto
    /// en `PairingStore` para poder revocarlo luego sin cambiar la
    /// contraseña. Íntegramente best-effort: CUALQUIER falla (endpoint
    /// todavía no existe, sin red, lo que sea) se ignora en silencio — el
    /// emparejamiento v1 (sesión = emparejamiento) ya quedó completo antes
    /// de llegar aquí y nunca debe bloquearse por esto.
    public func emparejarDispositivo(pairingStore: PairingStore) async {
        guard let client else { return }
        do {
            let dispositivo = UIDevice.current
            let nombre = dispositivo.name
            let fingerprint = dispositivo.identifierForVendor?.uuidString
            if let registrado = try await client.registrarDispositivo(
                nombre: nombre, plataforma: "ios", kind: "mobile", fingerprint: fingerprint
            ) {
                pairingStore.guardarDeviceId(registrado.id)
            }
        } catch {
            // Best-effort a propósito — ver el docstring de este método.
        }
    }
}
