import Foundation
import Observation

/// Estado de emparejamiento de ESTE dispositivo con un tenant de Edecán.
///
/// **v1: el "emparejamiento" es, literalmente, tener sesión iniciada.** No
/// hay un protocolo de emparejamiento propio para el móvil todavía (como el
/// código corto que usa el companion de escritorio,
/// `POST /v1/companion/pair-code` — ver `docs/api.md` §"Companion de
/// escritorio"): el par de tokens JWT que devuelve `POST /v1/auth/login`
/// YA identifica de forma única a este usuario+tenant en este teléfono
/// (`ARCHITECTURE.md` §10.12). Mientras el refresh token viva en el
/// Keychain de este dispositivo, el dispositivo cuenta como "emparejado".
///
/// Esto es intencional para este esqueleto, no un descuido: `ROADMAP_V2.md`
/// §6.1/§7.4 y `docs/control-remoto.md` §9/§4 ya dejan la tabla `devices`
/// (`kind = "mobile"`) y el diseño de emparejamiento por QR + fingerprint
/// como el camino real cuando haga falta identificar "este iPhone" más allá
/// de "esta sesión" (p. ej. para revocar un dispositivo sin cambiar la
/// contraseña, o para push notifications por dispositivo). Esta clase es el
/// punto de extensión natural cuando eso se construya — ver
/// `docs/movil-ios.md` sección "Qué queda pendiente".
@MainActor
@Observable
public final class PairingStore {
    public private(set) var serverURL: URL?
    public private(set) var isPaired: Bool
    /// `id` de la fila `devices` de este teléfono, si `APIClient.registrarDispositivo`
    /// logró crear una (WP-V4-01 en paralelo — ver su docstring). `nil` si
    /// nunca se pudo registrar (endpoint 404 todavía, sin red, etc.): el resto
    /// de la app debe seguir funcionando exactamente igual sin este valor,
    /// nunca es un requisito para poder chatear.
    public private(set) var deviceId: String?

    public init() {
        // SIN valor por defecto: si nunca se guardó nada, `serverURL`
        // queda `nil` y el onboarding lo pide — nunca se sugiere un
        // servidor real "de fábrica" (ver DIRECCION_ACTUAL.md, modelo de
        // credenciales bring-your-own).
        if let raw = Keychain.get(KeychainKeys.serverURL), let url = URL(string: raw) {
            serverURL = url
        } else {
            serverURL = nil
        }
        isPaired = Keychain.get(KeychainKeys.refreshToken) != nil
        deviceId = Keychain.get(KeychainKeys.deviceId)
    }

    /// Guarda la URL del servidor propio del tenant (paso 1 del onboarding).
    public func guardarServidor(_ url: URL) {
        Keychain.set(url.absoluteString, for: KeychainKeys.serverURL)
        serverURL = url
    }

    /// Se llama justo después de un login exitoso (`APIClient.login`, que ya
    /// dejó los tokens en el Keychain) — a partir de aquí el dispositivo
    /// queda emparejado con ese tenant hasta que se cierre sesión.
    public func marcarEmparejado() {
        isPaired = true
    }

    /// Guarda el `id` de `devices` que devolvió `POST /v1/devices`
    /// (`APIClient.registrarDispositivo`, WP-V4-01 en paralelo) — best-effort,
    /// se llama solo si esa llamada tuvo éxito. Permite luego revocar este
    /// dispositivo puntual al cerrar sesión (`APIClient.revocarDispositivo`)
    /// sin tener que cambiar la contraseña de la cuenta.
    public func guardarDeviceId(_ id: String) {
        Keychain.set(id, for: KeychainKeys.deviceId)
        deviceId = id
    }

    /// Cierra sesión en este dispositivo: borra los tokens del Keychain y
    /// vuelve a mostrar el onboarding. Deliberadamente NO borra
    /// `serverURL` — la próxima vez que este usuario (o cualquier otro
    /// tenant en el mismo servidor) abra la app, no debería tener que
    /// volver a escribir la URL del servidor.
    ///
    /// Sí borra `deviceId`: quien llama es responsable de revocarlo en el
    /// servidor ANTES de invocar este método (`APIClient.revocarDispositivo`,
    /// mientras el valor todavía esté disponible) — una vez emparejado un
    /// dispositivo nuevo (mismo u otro tenant), este teléfono necesita una
    /// fila `devices` nueva, no reutilizar la de la sesión anterior.
    public func olvidarEmparejamiento() {
        Keychain.delete(KeychainKeys.accessToken)
        Keychain.delete(KeychainKeys.refreshToken)
        Keychain.delete(KeychainKeys.deviceId)
        isPaired = false
        deviceId = nil
    }
}
