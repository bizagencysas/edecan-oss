import Foundation

/// `POST /v1/devices` — fila creada en la tabla `devices`
/// (`packages/db/edecan_db/models.py::Device`, `ROADMAP_V2.md` §7.4).
///
/// **Contrato en paralelo (WP-V4-01), todavía sin router en disco al
/// escribir este cliente** (`ARCHITECTURE.md` §12.a solo pinnea
/// `credentials`/`setup`/`skills`/`smarthome` para v3; `devices` es v4). Solo
/// `id` es obligatorio para decodificar — el resto queda opcional a
/// propósito, así que si el shape real que aterriza difiere un poco de este
/// (más/menos campos) esta decodificación no se rompe: `APIClient.registrarDispositivo`
/// ya trata CUALQUIER fallo (404 porque el router no existe todavía, un shape
/// que no decodifica, sin red, etc.) como "no se pudo emparejar por
/// dispositivo" y sigue sin bloquear el login — ver su docstring.
public struct DeviceOut: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let nombre: String?
    public let plataforma: String?
    public let kind: String?
    public let status: String?
    public let fingerprint: String?
}
