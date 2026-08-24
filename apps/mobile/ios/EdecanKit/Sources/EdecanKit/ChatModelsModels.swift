import Foundation

// MARK: - Catálogo del selector de modelos del chat

/// Un modelo del selector, tal cual lo sirve `GET /v1/models/chat`.
///
/// La lista NO se duplica en el cliente a propósito: la autoridad es
/// `config/modelos.yml` -> `modelos_chat`, que el backend lee en runtime y
/// expone por ese endpoint. Agregar o quitar un modelo del selector no
/// requiere tocar la app.
public struct ChatModelInfo: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let nombre: String
    public let descripcion: String
    /// Posición en la portada de la hoja (1..4 para los `principal`).
    public let orden: Int
    /// `true` = va en la tarjeta de arriba; `false` = detrás de "Más modelos".
    public let principal: Bool
    /// Si es `false`, la hoja lo etiqueta y el composer avisa cuando hay una
    /// imagen en el turno: el dueño manda capturas constantemente y un modelo
    /// ciego en silencio se siente como un Edecán incapaz.
    public let veImagenes: Bool
    /// Decide si la fila "Esfuerzo" aparece para este modelo. Un control que
    /// no cambia nada es peor que no tenerlo.
    public let soportaEsfuerzo: Bool
    public let contextoVentana: Int

    enum CodingKeys: String, CodingKey {
        case id, nombre, descripcion, orden, principal
        case veImagenes = "ve_imagenes"
        case soportaEsfuerzo = "soporta_esfuerzo"
        case contextoVentana = "contexto_ventana"
    }

    public init(
        id: String,
        nombre: String,
        descripcion: String,
        orden: Int,
        principal: Bool,
        veImagenes: Bool,
        soportaEsfuerzo: Bool,
        contextoVentana: Int
    ) {
        self.id = id
        self.nombre = nombre
        self.descripcion = descripcion
        self.orden = orden
        self.principal = principal
        self.veImagenes = veImagenes
        self.soportaEsfuerzo = soportaEsfuerzo
        self.contextoVentana = contextoVentana
    }
}

/// Nivel de Esfuerzo del turno. En este backend se traduce en el presupuesto
/// de tokens POR VUELTA del ciclo agente↔herramientas (bajo 2048 / medio 4096
/// / alto 8192), no en un flag decorativo de "reasoning".
public enum EsfuerzoChat: String, Codable, Sendable, Equatable, CaseIterable, Identifiable {
    case bajo
    case medio
    case alto

    public var id: String { rawValue }

    public var nombreLegible: String {
        switch self {
        case .bajo: return "Bajo"
        case .medio: return "Medio"
        case .alto: return "Alto"
        }
    }

    /// Una línea honesta de qué cambia, para que la fila no parezca magia.
    public var descripcion: String {
        switch self {
        case .bajo: return "Responde con menos vueltas de pensamiento."
        case .medio: return "El equilibrio de siempre."
        case .alto: return "Le da más aire para razonar antes de responder."
        }
    }
}

/// Respuesta completa de `GET /v1/models/chat`.
public struct ChatModelCatalog: Codable, Sendable, Equatable {
    /// Modelo que corre cuando nadie eligió nada (la pastilla dice
    /// "Automático", no este nombre: quién decide sigue siendo el backend).
    public let porDefecto: String
    public let esfuerzos: [EsfuerzoChat]
    public let esfuerzoPorDefecto: EsfuerzoChat
    public let modelos: [ChatModelInfo]

    enum CodingKeys: String, CodingKey {
        case porDefecto = "default"
        case esfuerzos
        case esfuerzoPorDefecto = "esfuerzo_default"
        case modelos
    }

    public init(
        porDefecto: String,
        esfuerzos: [EsfuerzoChat],
        esfuerzoPorDefecto: EsfuerzoChat,
        modelos: [ChatModelInfo]
    ) {
        self.porDefecto = porDefecto
        self.esfuerzos = esfuerzos
        self.esfuerzoPorDefecto = esfuerzoPorDefecto
        self.modelos = modelos
    }

    /// Decodifica los enums por `rawValue` en vez de dejar que Swift lance:
    /// si el backend agrega un cuarto nivel de Esfuerzo, la hoja debe seguir
    /// abriendo con los tres que sí entiende, no quedarse vacía.
    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        porDefecto = try container.decode(String.self, forKey: .porDefecto)
        let crudos = (try? container.decode([String].self, forKey: .esfuerzos)) ?? []
        let conocidos = crudos.compactMap(EsfuerzoChat.init(rawValue:))
        esfuerzos = conocidos.isEmpty ? EsfuerzoChat.allCases : conocidos
        let defecto = try? container.decode(String.self, forKey: .esfuerzoPorDefecto)
        esfuerzoPorDefecto = defecto.flatMap(EsfuerzoChat.init(rawValue:)) ?? .medio
        modelos = try container.decode([ChatModelInfo].self, forKey: .modelos)
    }

    /// Portada de la hoja, en el orden que fijó el backend.
    public var principales: [ChatModelInfo] {
        modelos.filter(\.principal).sorted { $0.orden < $1.orden }
    }

    /// Los de "Más modelos".
    public var secundarios: [ChatModelInfo] {
        modelos.filter { !$0.principal }.sorted { $0.orden < $1.orden }
    }

    public func modelo(id: String?) -> ChatModelInfo? {
        guard let id else { return nil }
        return modelos.first { $0.id == id }
    }
}

// MARK: - Selección persistida (`PUT /v1/conversations/{id}/model`)

/// Estado que devuelve el backend después de fijar el modelo. `nil` en
/// cualquiera de los dos = automático.
public struct SeleccionModeloChatOut: Sendable, Equatable, Decodable {
    public let model: String?
    public let effort: EsfuerzoChat?

    enum CodingKeys: String, CodingKey {
        case model, effort
    }

    public init(model: String?, effort: EsfuerzoChat?) {
        self.model = model
        self.effort = effort
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        model = try container.decodeIfPresent(String.self, forKey: .model)
        // Igual que en el catálogo: un nivel desconocido degrada a "sin
        // esfuerzo elegido" en lugar de tumbar la respuesta entera.
        let crudo = try container.decodeIfPresent(String.self, forKey: .effort)
        effort = crudo.flatMap(EsfuerzoChat.init(rawValue:))
    }
}

/// Cuerpo de `PUT /v1/conversations/{id}/model`.
///
/// Codifica las DOS claves siempre, incluso en `null`: para ese endpoint
/// `null` significa "volver a automático", que es un valor legítimo y no
/// "no cambiar". Con `encodeIfPresent` (lo que sintetizaría Swift) la clave
/// desaparecería del JSON y el cliente perdería la forma de volver atrás.
public struct SeleccionModeloChatIn: Sendable, Equatable, Encodable {
    public let model: String?
    public let effort: EsfuerzoChat?

    enum CodingKeys: String, CodingKey {
        case model, effort
    }

    public init(model: String?, effort: EsfuerzoChat?) {
        self.model = model
        self.effort = effort
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(model, forKey: .model)
        try container.encode(effort?.rawValue, forKey: .effort)
    }
}

// MARK: - Visión

/// MIMEs que el chat inserta directo en el turno como imagen
/// (`_DIRECT_VISION_MIMES` en `apps/api/edecan_api/routers/conversations.py`).
/// La app las conoce solo para poder avisar ANTES de enviar que el modelo
/// elegido es ciego; la autoridad sigue siendo el backend.
public enum MimesConVisionDirecta {
    public static let soportados: Set<String> = [
        "image/jpeg", "image/png", "image/gif", "image/webp",
    ]

    public static func esImagen(_ mime: String?) -> Bool {
        guard let mime else { return false }
        let limpio = mime.split(separator: ";").first.map(String.init) ?? mime
        return soportados.contains(
            limpio.trimmingCharacters(in: .whitespaces).lowercased()
        )
    }
}

// MARK: - Lo que ve el dueño

/// Lo que la pastilla del composer y la hoja necesitan saber, junto. Vive en
/// el paquete (y no en la vista) para poder probar la etiqueta y el aviso sin
/// red ni simulador.
public struct SeleccionDeModeloChat: Sendable, Equatable {
    /// Id persistido en la conversación. `nil` = automático.
    public let modeloId: String?
    /// Ficha del catálogo, si ya se cargó.
    public let info: ChatModelInfo?
    public let esfuerzo: EsfuerzoChat?

    public init(modeloId: String?, info: ChatModelInfo?, esfuerzo: EsfuerzoChat?) {
        self.modeloId = modeloId
        self.info = info
        self.esfuerzo = esfuerzo
    }

    /// Nombre para mostrar. Si el catálogo todavía no llegó pero la
    /// conversación ya trae un id, se usa el último tramo del id
    /// (`@cf/moonshotai/kimi-k2.6` -> `kimi-k2.6`) en vez de mentir diciendo
    /// "Automático" sobre un chat que sí tiene modelo fijado.
    public var nombreVisible: String? {
        if let info { return info.nombre }
        guard let modeloId, !modeloId.isEmpty else { return nil }
        return modeloId.split(separator: "/").last.map(String.init) ?? modeloId
    }

    /// La fila "Esfuerzo" y el sufijo de la pastilla solo aparecen donde el
    /// nivel de verdad cambia el turno.
    public var muestraEsfuerzo: Bool { info?.soportaEsfuerzo ?? false }

    /// Texto de la pastilla: "Oda · Alto", "Scout", "Automático".
    public var etiqueta: String {
        guard let nombreVisible else { return "Automático" }
        guard muestraEsfuerzo, let esfuerzo else { return nombreVisible }
        return "\(nombreVisible) · \(esfuerzo.nombreLegible)"
    }

    /// Aviso para cuando hay una imagen en el turno y el modelo elegido no
    /// ve. El backend NO falla: ese turno lo atiende un modelo con visión y
    /// la selección persistida no cambia — el aviso explica exactamente eso,
    /// para que la degradación no parezca un error silencioso.
    public func avisoDeCeguera(hayImagenEnElTurno: Bool) -> String? {
        guard hayImagenEnElTurno, let info, !info.veImagenes else { return nil }
        return "\(info.nombre) no ve imágenes: este mensaje lo atiende un modelo con visión."
    }
}
