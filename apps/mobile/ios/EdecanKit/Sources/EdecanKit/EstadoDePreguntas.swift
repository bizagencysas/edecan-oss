import Foundation

/// Un mensaje del hilo visto por el resolvedor de ``QuestionBlock``: lo mínimo
/// para saber si una tarjeta de pregunta ya se contestó.
///
/// Es un tipo propio y no el `Mensaje` de `EdecanApp` porque aquel arrastra
/// SwiftUI y `@Observable`; acá la lógica queda testeable con `swift test`, sin
/// simulador, igual que ``ConfirmacionFormato``.
public struct MensajeDelHilo: Sendable, Equatable, Identifiable {
    public enum Rol: Sendable, Equatable { case usuario, asistente }

    public let id: String
    public let rol: Rol
    /// Texto tal como se envió (no el redactado de la burbuja), porque es
    /// contra esto que se compara el `value` de una opción.
    public let texto: String
    /// Fecha del servidor. `nil` en la burbuja optimista que todavía va en
    /// vuelo: esa nunca tiene fecha y siempre es la más nueva del hilo.
    public let fecha: Date?
    /// `false` cuando el envío falló y la burbuja quedó con "Reintentar". Un
    /// mensaje que no llegó no contestó nada: si contara, la pregunta se
    /// quedaría trabada sin manera de volver a responderla.
    public let entregable: Bool

    public init(id: String, rol: Rol, texto: String, fecha: Date?, entregable: Bool = true) {
        self.id = id
        self.rol = rol
        self.texto = texto
        self.fecha = fecha
        self.entregable = entregable
    }
}

/// Estado de una tarjeta de ``QuestionBlock``, derivado del hilo.
public struct EstadoDePregunta: Sendable, Equatable {
    /// Ya hay una respuesta del usuario después del mensaje que trae la
    /// tarjeta: se deshabilita y se marca como respondida.
    public let respondida: Bool
    /// `QuestionOption.id` de las opciones cuyo texto coincide con lo que se
    /// contestó. Vacío cuando contestó con sus propias palabras — la tarjeta
    /// igual queda cerrada, solo que sin ninguna opción marcada.
    public let opcionesMarcadas: Set<String>

    public static let sinResponder = EstadoDePregunta(respondida: false, opcionesMarcadas: [])

    public init(respondida: Bool, opcionesMarcadas: Set<String>) {
        self.respondida = respondida
        self.opcionesMarcadas = opcionesMarcadas
    }
}

/// Deriva del HILO si una tarjeta de pregunta ya se contestó.
///
/// El estado no puede vivir en la vista. Una tarjeta que recordaba en un
/// `@State` que ya la habían tocado se rompía de dos maneras reales: si la
/// persona contestaba escribiendo en vez de tocar, la tarjeta nunca se
/// enteraba; y como SwiftUI recrea las vistas del `LazyVStack` al desplazar o
/// al reabrir la conversación, ese `@State` volvía a `false` y una pregunta
/// contestada hacía rato quedaba tocable otra vez — tocarla mandaba una
/// segunda respuesta a algo ya resuelto.
///
/// La regla es del hilo, no de la vista: una pregunta está contestada si
/// existe CUALQUIER mensaje del usuario posterior al mensaje que trae la
/// tarjeta. Es derivable en el cliente (el servidor no cambia) y sobrevive a
/// que la vista se recree, porque la vista ya no recuerda nada.
public enum HiloDePreguntas {
    /// Texto de la PRIMERA respuesta del usuario posterior a cada mensaje,
    /// indexado por id de mensaje. Un mensaje sin entrada es un mensaje que
    /// todavía nadie contestó — ahí es donde la tarjeta sigue tocable.
    ///
    /// Devolver un diccionario y no un booleano por tarjeta permite calcularlo
    /// una sola vez por hilo (una pasada) en lugar de recorrerlo entero por
    /// cada burbuja que se dibuja.
    public static func respuestasPosteriores(en mensajes: [MensajeDelHilo]) -> [String: String] {
        var respuestas: [String: String] = [:]
        var siguienteRespuesta: String?
        for mensaje in ordenadoPorFecha(mensajes).reversed() {
            if let siguienteRespuesta { respuestas[mensaje.id] = siguienteRespuesta }
            if mensaje.rol == .usuario, mensaje.entregable {
                siguienteRespuesta = mensaje.texto
            }
        }
        return respuestas
    }

    /// Estado de la tarjeta a partir de esa respuesta. `nil` (nadie contestó
    /// todavía) deja la tarjeta activa, que es justo lo que necesita la
    /// pregunta más reciente: la que el usuario tiene que contestar AHORA.
    public static func estado(de bloque: QuestionBlock, respuesta: String?) -> EstadoDePregunta {
        guard let respuesta else { return .sinResponder }
        return EstadoDePregunta(
            respondida: true,
            opcionesMarcadas: opcionesQueCoinciden(bloque, respuesta: respuesta)
        )
    }

    /// El orden que importa es el real, no el de llegada al array.
    ///
    /// Hoy el backend ya devuelve `created_at ASC` (`repo.list_messages`) y la
    /// app agrega las burbujas optimistas al final, así que el array suele
    /// coincidir con la realidad. Se ordena igual, de forma ESTABLE, por dos
    /// motivos: `ORDER BY created_at` no desempata fechas idénticas, y así el
    /// resolvedor no depende de que el servidor nunca cambie ese orden.
    ///
    /// Los mensajes sin fecha van al final a propósito: son las burbujas
    /// optimistas recién creadas. Fecharlas con el reloj del teléfono sería
    /// peor — un teléfono atrasado ordenaría la respuesta ANTES de la pregunta
    /// y la tarjeta parpadearía de vuelta a tocable mientras se envía.
    static func ordenadoPorFecha(_ mensajes: [MensajeDelHilo]) -> [MensajeDelHilo] {
        mensajes.enumerated()
            .sorted { izquierda, derecha in
                let fechaIzquierda = izquierda.element.fecha ?? .distantFuture
                let fechaDerecha = derecha.element.fecha ?? .distantFuture
                if fechaIzquierda == fechaDerecha { return izquierda.offset < derecha.offset }
                return fechaIzquierda < fechaDerecha
            }
            .map(\.element)
    }

    private static func opcionesQueCoinciden(
        _ bloque: QuestionBlock,
        respuesta: String
    ) -> Set<String> {
        let objetivo = normalizado(respuesta)
        guard !objetivo.isEmpty else { return [] }

        var marcadas: Set<String> = []
        for opcion in bloque.options {
            let texto = normalizado(opcion.messageText)
            if !texto.isEmpty, texto == objetivo { marcadas.insert(opcion.id) }
        }
        // Con `multiSelect` la respuesta es la unión de varias opciones
        // ("A, B"), así que la igualdad completa solo acierta cuando eligió
        // una sola. Partir por comas es exclusivo de este caso: en selección
        // simple haría que "Sí, hazlo" marcara la opción "Sí" que la persona
        // nunca tocó.
        guard marcadas.isEmpty, bloque.multiSelect else { return marcadas }
        let partes = Set(respuesta.split(separator: ",").map { normalizado(String($0)) })
        for opcion in bloque.options {
            let texto = normalizado(opcion.messageText)
            if !texto.isEmpty, partes.contains(texto) { marcadas.insert(opcion.id) }
        }
        return marcadas
    }

    /// Tolerante de más a propósito: quien escribe la respuesta a mano teclea
    /// "personal" o "publicalo", no la frase con mayúscula y tilde exactas.
    /// Fallar el emparejamiento solo cuesta la marca visual — la tarjeta queda
    /// contestada igual —, así que conviene errar hacia reconocerla.
    private static func normalizado(_ texto: String) -> String {
        texto
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .folding(
                options: [.caseInsensitive, .diacriticInsensitive, .widthInsensitive],
                locale: nil
            )
    }
}
