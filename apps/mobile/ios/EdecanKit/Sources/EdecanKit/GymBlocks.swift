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

/// Persistencia local del check-in del gym: qué día quedó respondido y con qué
/// respuesta. El servidor es la fuente de verdad (`/plan/today` →
/// `checkin_hoy`), pero este flag evita que la tarjeta Sí/No reaparezca al
/// reabrir la app sin red. La clave va namespaced por usuario para que un
/// cambio de cuenta NO herede la respuesta del dueño anterior.
///
/// Solo se escribe DESPUÉS de un check-in aceptado por el servidor (o
/// confirmado por `checkin_hoy`): nunca esconde la tarjeta antes de responder.
/// Mismo patrón de `defaults` inyectable que ``ChatLocalStateStore`` para
/// poder probarlo sin tocar las preferencias reales del dispositivo.
public struct GymCheckinEstadoLocal {
    public static let storagePrefix = "gym.checkin."

    private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    /// `"yyyy-MM-dd"` en el calendario local — el mismo día que el backend
    /// guarda con `date.today()` del servidor.
    public func diaISO(
        _ fecha: Date = Date(),
        calendario: Calendar = .current
    ) -> String {
        let componentes = calendario.dateComponents([.year, .month, .day], from: fecha)
        return String(
            format: "%04d-%02d-%02d",
            componentes.year ?? 0, componentes.month ?? 0, componentes.day ?? 0
        )
    }

    /// Respuesta ya persistida para `dia` y `usuarioID` (`"si"`/`"no"`), o
    /// `nil` si ese día no se respondió desde este dispositivo/cuenta.
    public func respuesta(dia: String, usuarioID: String) -> String? {
        defaults.string(forKey: Self.clave(dia: dia, usuarioID: usuarioID))
    }

    /// Marca `dia` como respondido para `usuarioID`.
    public func marcar(respuesta: String, dia: String, usuarioID: String) {
        defaults.set(respuesta, forKey: Self.clave(dia: dia, usuarioID: usuarioID))
    }

    private static func clave(dia: String, usuarioID: String) -> String {
        "\(storagePrefix)\(usuarioID).\(dia)"
    }
}