import Foundation
import Security

/// Claves fijas usadas para guardar valores en el Keychain de este
/// dispositivo. Un solo lugar para no repetir strings sueltos entre
/// ``APIClient`` y ``PairingStore``.
public enum KeychainKeys {
    public static let serverURL = "cc.edecan.app.serverURL"
    public static let accessToken = "cc.edecan.app.accessToken"
    public static let refreshToken = "cc.edecan.app.refreshToken"
    /// `id` de la fila `devices` que devolvió `POST /v1/devices` al emparejar
    /// este teléfono (WP-V4-01, contrato en paralelo — ver `PairingStore`).
    /// `nil` si ese endpoint todavía no existe en el servidor o la llamada
    /// falló: el emparejamiento v1 (sesión = emparejamiento) sigue
    /// funcionando igual sin este valor.
    public static let deviceId = "cc.edecan.app.deviceId"
}

/// Envoltorio mínimo sobre el Keychain de iOS (`Security.framework`) para
/// guardar valores sensibles: la URL del servidor propio del tenant y el
/// par de tokens JWT de la sesión. Cada valor se guarda como
/// `kSecClassGenericPassword` con su propio `account`, todos bajo el mismo
/// `service` de la app.
///
/// Deliberadamente NO usa un `kSecAttrAccessGroup` (App Group) — eso haría
/// falta para que `EdecanWidgets` pudiera leer estos valores directamente,
/// algo que hoy no necesita (el widget de este WP es un placeholder sin
/// datos reales, ver `EdecanWidgets/EdecanWidgets.swift` y
/// "Qué queda pendiente" en `docs/movil-ios.md`).
public enum Keychain {
    private static let service = "cc.edecan.app.keychain"

    /// Guarda `value` bajo `key`, reemplazando cualquier valor previo.
    public static func set(_ value: String, for key: String) {
        let data = Data(value.utf8)
        // SecItemAdd falla con errSecDuplicateItem si la clave ya existe —
        // se borra primero para que `set` siempre sea "upsert".
        delete(key)
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock,
        ]
        SecItemAdd(query as CFDictionary, nil)
    }

    /// Lee el valor guardado bajo `key`, o `nil` si no existe.
    public static func get(_ key: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess, let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    /// Borra el valor guardado bajo `key`. No falla si no existía.
    @discardableResult
    public static func delete(_ key: String) -> Bool {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
        ]
        let status = SecItemDelete(query as CFDictionary)
        return status == errSecSuccess || status == errSecItemNotFound
    }
}
