import Foundation

/// Un botón de la tarjeta `gym_checkin` (`{"label":String,"accion":String}`).
/// `accion` es `"gym_yes"` o `"gym_no"`; cualquier otra cosa se ignora en la
/// UI — nunca se dispara una acción arbitraria venida del servidor.
public struct GymCheckinBoton: Decodable, Sendable, Equatable {
    public let label: String
    public let accion: String

    enum CodingKeys: String, CodingKey { case label, accion }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        label = (try? container.decode(String.self, forKey: .label)) ?? ""
        accion = (try? container.decode(String.self, forKey: .accion)) ?? ""
    }
}

/// Bloque de chat `gym_checkin`: la tarjeta "¿Vas a ir al gym hoy?" con
/// botones Sí/No. Decodificación defensiva — si falta `titulo` o `botones`,
/// se cae a vacíos en vez de tumbar el hilo (mismo criterio forward-compatible
/// que el resto de bloques de ``ChatBlock``).
public struct GymCheckinBlock: Decodable, Sendable, Equatable {
    public let titulo: String
    public let botones: [GymCheckinBoton]

    enum CodingKeys: String, CodingKey {
        case titulo
        case botones
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        titulo = (try? container.decode(String.self, forKey: .titulo)) ?? ""
        botones = (try? container.decode([GymCheckinBoton].self, forKey: .botones)) ?? []
    }
}