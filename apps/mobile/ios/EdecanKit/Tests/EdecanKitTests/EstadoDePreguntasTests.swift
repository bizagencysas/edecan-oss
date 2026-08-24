import Foundation
import Testing
@testable import EdecanKit

/// Una tarjeta de pregunta está contestada si el HILO dice que lo está, no si
/// la vista se acuerda. Estos casos son los dos defectos reales que se vieron
/// en el teléfono (contestar escribiendo, y la tarjeta reviviendo al desplazar
/// o al reabrir el chat) más las trampas de alrededor: el mensaje optimista en
/// vuelo, el envío fallido y la pregunta más nueva, que tiene que seguir
/// tocable o la persona queda trabada.
@Suite("Estado de las tarjetas de pregunta, derivado del hilo")
struct EstadoDePreguntasTests {
    /// Mismo payload que emite `preguntar_al_usuario`, con `value` explícito:
    /// las frases completas son justamente lo que se compara para saber qué
    /// opción se eligió.
    static func bloque(
        multiSelect: Bool = false,
        valores: [(String, String?)] = [
            ("Personal", "Publicalo con el destino 'personal'."),
            ("Acme", "Publicalo con el destino 'organization'."),
        ]
    ) throws -> QuestionBlock {
        let opciones = valores
            .map { label, value in
                let valorJSON = value.map { "\"\($0)\"" } ?? "null"
                return "{\"label\":\"\(label)\",\"description\":null,\"value\":\(valorJSON)}"
            }
            .joined(separator: ",")
        let json = """
        {"schema_version":1,"type":"question","question":"¿A qué cuenta publico este post?",
         "header":"Destino","options":[\(opciones)],
         "multi_select":\(multiSelect),"allow_free_text":true}
        """
        return try JSONDecoder().decode(QuestionBlock.self, from: Data(json.utf8))
    }

    static let base = Date(timeIntervalSince1970: 1_700_000_000)

    static func mensaje(
        _ id: String,
        _ rol: MensajeDelHilo.Rol,
        _ texto: String,
        segundos: Double?,
        entregable: Bool = true
    ) -> MensajeDelHilo {
        MensajeDelHilo(
            id: id,
            rol: rol,
            texto: texto,
            fecha: segundos.map { base.addingTimeInterval($0) },
            entregable: entregable
        )
    }

    /// El hilo de la captura: el usuario pide algo, Edecán pregunta.
    static var hiloSinContestar: [MensajeDelHilo] {
        [
            mensaje("m1", .usuario, "Publica un post sobre crédito.", segundos: 0),
            mensaje("m2", .asistente, "¿A qué cuenta?", segundos: 10),
        ]
    }

    @Test("sin respuesta después, la tarjeta sigue activa")
    func sinResponderQuedaActiva() throws {
        let estado = HiloDePreguntas.estado(
            de: try Self.bloque(),
            respuesta: HiloDePreguntas.respuestasPosteriores(en: Self.hiloSinContestar)["m2"]
        )
        #expect(estado.respondida == false)
        #expect(estado.opcionesMarcadas.isEmpty)
    }

    @Test("un mensaje del usuario después la deja contestada")
    func conRespuestaQuedaContestada() throws {
        var hilo = Self.hiloSinContestar
        hilo.append(Self.mensaje("m3", .usuario, "Personal", segundos: 20))
        let estado = HiloDePreguntas.estado(
            de: try Self.bloque(),
            respuesta: HiloDePreguntas.respuestasPosteriores(en: hilo)["m2"]
        )
        #expect(estado.respondida)
    }

    @Test("marca la opción cuando el texto coincide con su value")
    func marcaLaOpcionElegida() throws {
        var hilo = Self.hiloSinContestar
        hilo.append(Self.mensaje("m3", .usuario, "Publicalo con el destino 'organization'.", segundos: 20))
        let estado = HiloDePreguntas.estado(
            de: try Self.bloque(),
            respuesta: HiloDePreguntas.respuestasPosteriores(en: hilo)["m2"]
        )
        #expect(estado.respondida)
        #expect(estado.opcionesMarcadas == ["Acme"])
    }

    /// El defecto 1 de la captura: escribió "Personal" en el campo de texto en
    /// vez de tocar la opción, y la tarjeta se quedó tocable como si nada.
    @Test("contestar con palabras propias la cierra igual, sin marca")
    func palabrasPropiasCierranSinMarca() throws {
        var hilo = Self.hiloSinContestar
        hilo.append(Self.mensaje("m3", .usuario, "mejor mándalo a la cuenta de la empresa", segundos: 20))
        let estado = HiloDePreguntas.estado(
            de: try Self.bloque(),
            respuesta: HiloDePreguntas.respuestasPosteriores(en: hilo)["m2"]
        )
        #expect(estado.respondida)
        #expect(estado.opcionesMarcadas.isEmpty)
    }

    /// El defecto 2: al desplazar o reabrir la conversación, SwiftUI recrea la
    /// vista. Antes ahí se perdía el `@State` y la pregunta revivía. Ahora el
    /// cálculo no guarda nada entre llamadas, así que recrear la tarjeta —o el
    /// hilo entero, como al reabrir el chat— da exactamente lo mismo.
    @Test("recrear la vista no revive una pregunta ya contestada")
    func recrearLaVistaNoLaRevive() throws {
        let bloque = try Self.bloque()
        var hilo = Self.hiloSinContestar
        hilo.append(Self.mensaje("m3", .usuario, "Personal", segundos: 20))

        let primera = HiloDePreguntas.estado(
            de: bloque,
            respuesta: HiloDePreguntas.respuestasPosteriores(en: hilo)["m2"]
        )
        // Reabrir el chat reconstruye los mensajes desde el historial: valores
        // nuevos, mismos datos.
        let recargado = hilo.map {
            MensajeDelHilo(id: $0.id, rol: $0.rol, texto: $0.texto, fecha: $0.fecha)
        }
        let segunda = HiloDePreguntas.estado(
            de: bloque,
            respuesta: HiloDePreguntas.respuestasPosteriores(en: recargado)["m2"]
        )

        #expect(primera.respondida)
        #expect(segunda == primera)
    }

    /// Si la burbuja optimista no contara, la tarjeta parpadearía de vuelta a
    /// tocable durante todo el envío.
    @Test("el mensaje optimista todavía en vuelo ya cuenta como respuesta")
    func elOptimistaEnVueloCuenta() throws {
        var hilo = Self.hiloSinContestar
        // Sin fecha del servidor todavía, y con el texto exacto que manda el
        // toque en la opción.
        hilo.append(Self.mensaje("m3", .usuario, "Publicalo con el destino 'personal'.", segundos: nil))
        let estado = HiloDePreguntas.estado(
            de: try Self.bloque(),
            respuesta: HiloDePreguntas.respuestasPosteriores(en: hilo)["m2"]
        )
        #expect(estado.respondida)
        #expect(estado.opcionesMarcadas == ["Personal"])
    }

    @Test("un envío que falló no cuenta: la tarjeta vuelve a estar tocable")
    func elEnvioFallidoNoCuenta() throws {
        var hilo = Self.hiloSinContestar
        hilo.append(Self.mensaje("m3", .usuario, "Personal", segundos: 20, entregable: false))
        let estado = HiloDePreguntas.estado(
            de: try Self.bloque(),
            respuesta: HiloDePreguntas.respuestasPosteriores(en: hilo)["m2"]
        )
        #expect(estado.respondida == false)
    }

    /// Pasarse de estricto sería peor que el bug: desactivaría la pregunta que
    /// la persona tiene que contestar ahora mismo.
    @Test("la pregunta más reciente sigue activa aunque haya otra vieja contestada")
    func laMasRecienteSigueActiva() throws {
        let bloque = try Self.bloque()
        let hilo = [
            Self.mensaje("m1", .asistente, "¿A qué cuenta?", segundos: 0),
            Self.mensaje("m2", .usuario, "Personal", segundos: 10),
            Self.mensaje("m3", .asistente, "¿Con imagen o sin imagen?", segundos: 20),
        ]
        let respuestas = HiloDePreguntas.respuestasPosteriores(en: hilo)
        #expect(HiloDePreguntas.estado(de: bloque, respuesta: respuestas["m1"]).respondida)
        #expect(HiloDePreguntas.estado(de: bloque, respuesta: respuestas["m3"]).respondida == false)
    }

    /// El caso mixto, que es el que se da de verdad en el teléfono: el
    /// historial ya viene fechado del servidor y encima quedan las burbujas
    /// nuevas sin fecha (la respuesta optimista y la contestación en
    /// streaming, que trae OTRA pregunta). Sin el `.distantFuture` para las
    /// que no tienen fecha, la pregunta recién llegada quedaría ordenada
    /// ANTES de la respuesta anterior y saldría desactivada — justo la que la
    /// persona tiene que contestar.
    @Test("una pregunta nueva sin fecha, sobre historial fechado, sigue activa")
    func preguntaNuevaSobreHistorialFechadoSigueActiva() throws {
        let bloque = try Self.bloque()
        let hilo = [
            Self.mensaje("m1", .usuario, "Publica un post sobre crédito.", segundos: 0),
            Self.mensaje("m2", .asistente, "¿A qué cuenta?", segundos: 10),
            Self.mensaje("m3", .usuario, "Publicalo con el destino 'personal'.", segundos: nil),
            Self.mensaje("m4", .asistente, "¿Con imagen o sin imagen?", segundos: nil),
        ]
        let respuestas = HiloDePreguntas.respuestasPosteriores(en: hilo)
        #expect(HiloDePreguntas.estado(de: bloque, respuesta: respuestas["m2"]).respondida)
        #expect(HiloDePreguntas.estado(de: bloque, respuesta: respuestas["m4"]).respondida == false)
    }

    /// La que marca es la respuesta INMEDIATA, no la última del hilo: si no,
    /// una pregunta vieja se marcaría con lo que se eligió en otra pregunta.
    @Test("cada pregunta se marca con su respuesta, no con la última del hilo")
    func cadaPreguntaUsaSuPropiaRespuesta() throws {
        let bloque = try Self.bloque()
        let hilo = [
            Self.mensaje("m1", .asistente, "¿A qué cuenta?", segundos: 0),
            Self.mensaje("m2", .usuario, "Publicalo con el destino 'personal'.", segundos: 10),
            Self.mensaje("m3", .asistente, "¿Y esta otra?", segundos: 20),
            Self.mensaje("m4", .usuario, "Publicalo con el destino 'organization'.", segundos: 30),
        ]
        let respuestas = HiloDePreguntas.respuestasPosteriores(en: hilo)
        #expect(HiloDePreguntas.estado(de: bloque, respuesta: respuestas["m1"]).opcionesMarcadas == ["Personal"])
        #expect(HiloDePreguntas.estado(de: bloque, respuesta: respuestas["m3"]).opcionesMarcadas == ["Acme"])
    }

    /// Un mensaje del usuario ANTERIOR a la pregunta es la orden que la
    /// provocó, no su respuesta.
    @Test("el mensaje que provocó la pregunta no la contesta")
    func elMensajeAnteriorNoCuenta() throws {
        let respuestas = HiloDePreguntas.respuestasPosteriores(en: Self.hiloSinContestar)
        #expect(respuestas["m2"] == nil)
        #expect(respuestas["m1"] == nil)
    }

    /// El orden que decide es el de las fechas, no el del array.
    @Test("ordena por fecha real aunque el array venga al revés")
    func ordenaPorFechaNoPorLlegada() throws {
        let hilo = [
            Self.mensaje("m3", .usuario, "Personal", segundos: 20),
            Self.mensaje("m2", .asistente, "¿A qué cuenta?", segundos: 10),
        ]
        let respuestas = HiloDePreguntas.respuestasPosteriores(en: hilo)
        #expect(respuestas["m2"] == "Personal")
        #expect(respuestas["m3"] == nil)
    }

    /// Fechas iguales (`ORDER BY created_at` no desempata) conservan el orden
    /// en que vinieron, que es lo mejor que se puede saber.
    @Test("con fechas idénticas manda el orden del array")
    func fechasIdenticasConservanElOrden() throws {
        let hilo = [
            Self.mensaje("m1", .asistente, "¿A qué cuenta?", segundos: 10),
            Self.mensaje("m2", .usuario, "Personal", segundos: 10),
        ]
        let respuestas = HiloDePreguntas.respuestasPosteriores(en: hilo)
        #expect(respuestas["m1"] == "Personal")
        #expect(respuestas["m2"] == nil)
    }

    @Test("multiSelect marca todas las opciones que se enviaron juntas")
    func multiSelectMarcaVarias() throws {
        let bloque = try Self.bloque(
            multiSelect: true,
            valores: [("LinkedIn", nil), ("X", nil), ("Instagram", nil)]
        )
        var hilo = Self.hiloSinContestar
        // Exactamente lo que arma "Enviar respuesta" en la tarjeta.
        hilo.append(Self.mensaje("m3", .usuario, "LinkedIn, Instagram", segundos: 20))
        let estado = HiloDePreguntas.estado(
            de: bloque,
            respuesta: HiloDePreguntas.respuestasPosteriores(en: hilo)["m2"]
        )
        #expect(estado.respondida)
        #expect(estado.opcionesMarcadas == ["LinkedIn", "Instagram"])
    }

    /// Quien escribe a mano no teclea la frase con la mayúscula y la tilde
    /// exactas. Sin esto la tarjeta quedaría cerrada pero sin marcar nada.
    @Test("empareja sin importar mayúsculas ni tildes")
    func emparejaSinTildesNiMayusculas() throws {
        var hilo = Self.hiloSinContestar
        hilo.append(Self.mensaje("m3", .usuario, "  publícalo con el destino 'PERSONAL'.  ", segundos: 20))
        let estado = HiloDePreguntas.estado(
            de: try Self.bloque(),
            respuesta: HiloDePreguntas.respuestasPosteriores(en: hilo)["m2"]
        )
        #expect(estado.opcionesMarcadas == ["Personal"])
    }

    /// En selección simple partir por comas marcaría una opción que nadie tocó.
    @Test("en selección simple, una respuesta libre con coma no marca nada")
    func seleccionSimpleNoParteEnComas() throws {
        let bloque = try Self.bloque(valores: [("Personal", nil), ("Acme", nil)])
        var hilo = Self.hiloSinContestar
        hilo.append(Self.mensaje("m3", .usuario, "Personal, pero sin la imagen", segundos: 20))
        let estado = HiloDePreguntas.estado(
            de: bloque,
            respuesta: HiloDePreguntas.respuestasPosteriores(en: hilo)["m2"]
        )
        #expect(estado.respondida)
        #expect(estado.opcionesMarcadas.isEmpty)
    }

    /// Un mensaje solo con adjuntos no tiene texto, pero la conversación ya
    /// siguió: la tarjeta se cierra igual, sin marcar nada.
    @Test("un mensaje sin texto también cierra la pregunta")
    func mensajeSinTextoTambienCierra() throws {
        var hilo = Self.hiloSinContestar
        hilo.append(Self.mensaje("m3", .usuario, "", segundos: 20))
        let estado = HiloDePreguntas.estado(
            de: try Self.bloque(),
            respuesta: HiloDePreguntas.respuestasPosteriores(en: hilo)["m2"]
        )
        #expect(estado.respondida)
        #expect(estado.opcionesMarcadas.isEmpty)
    }
}
