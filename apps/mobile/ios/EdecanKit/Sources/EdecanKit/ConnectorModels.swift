import Foundation

/// Cuenta OAuth ya vinculada (`GET /v1/connectors` → `accounts[]`).
public struct ConnectorAccountSummary: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let connectorKey: String?
    public let externalAccountId: String?
    public let displayName: String?
    public let status: String?
    public let scopes: [String]?
    /// `GET /v1/connectors` — BotBeta: única señal fiable de OAuth operativo (vault con `access_token`).
    /// Si el campo falta en el JSON, tratamos como `nil` → no conectada (conservador).
    public let hasAccessToken: Bool?

    enum CodingKeys: String, CodingKey {
        case id, status, scopes
        case connectorKey = "connector_key"
        case externalAccountId = "external_account_id"
        case displayName = "display_name"
        case hasAccessToken = "has_access_token"
    }

    public var etiqueta: String {
        if let displayName, !displayName.isEmpty { return displayName }
        if let externalAccountId, !externalAccountId.isEmpty { return externalAccountId }
        return id
    }

    /// Verdad de vault: solo cuando el VPS expone `has_access_token: true`.
    /// No usar `status` ni la mera presencia en `accounts[]` — filas huérfanas pueden venir `active`.
    public var conectada: Bool {
        hasAccessToken == true
    }

    /// Cuenta listada sin token operativo (huérfana o OAuth incompleto).
    public var esPendiente: Bool {
        hasAccessToken != true
    }
}

/// Fila de `GET /v1/connectors` (OAuth + Twilio/etc.).
public struct ConnectorListItem: Codable, Sendable, Equatable, Identifiable {
    public var id: String { key }
    public let key: String
    public let displayName: String
    public let accounts: [ConnectorAccountSummary]
    public let appConfigured: Bool?
    public let appClientIdMasked: String?
    public let oauthRedirectUri: String?

    enum CodingKeys: String, CodingKey {
        case key, accounts
        case displayName = "display_name"
        case appConfigured = "app_configured"
        case appClientIdMasked = "app_client_id_masked"
        case oauthRedirectUri = "oauth_redirect_uri"
    }

    public init(
        key: String,
        displayName: String,
        accounts: [ConnectorAccountSummary] = [],
        appConfigured: Bool? = nil,
        appClientIdMasked: String? = nil,
        oauthRedirectUri: String? = nil
    ) {
        self.key = key
        self.displayName = displayName
        self.accounts = accounts
        self.appConfigured = appConfigured
        self.appClientIdMasked = appClientIdMasked
        self.oauthRedirectUri = oauthRedirectUri
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        key = try c.decode(String.self, forKey: .key)
        displayName = try c.decodeIfPresent(String.self, forKey: .displayName) ?? key
        accounts = try c.decodeIfPresent([ConnectorAccountSummary].self, forKey: .accounts) ?? []
        appConfigured = try c.decodeIfPresent(Bool.self, forKey: .appConfigured)
        appClientIdMasked = try c.decodeIfPresent(String.self, forKey: .appClientIdMasked)
        oauthRedirectUri = try c.decodeIfPresent(String.self, forKey: .oauthRedirectUri)
    }

    /// Fila de catálogo fija (LinkedIn/X/Meta/YouTube) sin datos del VPS hasta que
    /// `GET /v1/connectors` devuelva el ítem real. Cuentas vacías → Pendiente/Sin conectar.
    public static func filaCatalogo(_ key: SocialOAuthConnectorKey) -> ConnectorListItem {
        ConnectorListItem(key: key.rawValue, displayName: key.titulo)
    }

    public var tieneCuenta: Bool { accounts.contains(where: \.conectada) }

    public var esOAuthSocial: Bool {
        ["linkedin", "x", "meta", "youtube"].contains(key)
    }

    public var esOAuthGenerico: Bool {
        appConfigured != nil || oauthRedirectUri != nil
    }
}

public struct ConnectorAuthorizeOut: Codable, Sendable, Equatable {
    public let url: String
}

/// Cuerpo de `PUT /v1/connectors/{key}/app-credentials` — la app OAuth PROPIA
/// del tenant (BYO). `client_secret` opcional (X con PKCE puro no lo exige).
public struct OAuthAppCredentialsBody: Encodable, Sendable {
    public let clientId: String
    public let clientSecret: String?

    enum CodingKeys: String, CodingKey {
        case clientId = "client_id"
        case clientSecret = "client_secret"
    }

    public init(clientId: String, clientSecret: String?) {
        self.clientId = clientId
        self.clientSecret = clientSecret
    }
}

/// Redes OAuth que los bots consultan con `estado_conectores_sociales`.
public enum SocialOAuthConnectorKey: String, CaseIterable, Sendable {
    case linkedin, x, meta, youtube

    public var titulo: String {
        switch self {
        case .linkedin: return "LinkedIn"
        case .x: return "X"
        case .meta: return "Meta"
        case .youtube: return "YouTube"
        }
    }

    public var iconoSistema: String {
        switch self {
        case .linkedin: return "briefcase.fill"
        case .x: return "at"
        case .meta: return "person.2.fill"
        case .youtube: return "play.rectangle.fill"
        }
    }
}
