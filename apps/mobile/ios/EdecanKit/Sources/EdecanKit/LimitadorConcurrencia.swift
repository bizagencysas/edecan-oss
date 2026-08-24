import Foundation

/// Puerta que deja pasar como máximo `maximo` tareas async a la vez; el resto
/// espera en fila (FIFO) hasta que alguna libere su turno.
///
/// Nace del diagnóstico del crash por memoria del chat: abrir un hilo largo
/// con muchos adjuntos de imagen dispara, uno por fila, un `.task` que
/// descarga y decodifica -- si el hilo trae 35 imágenes y las 35 filas
/// entran en el layout inicial a la vez (por ejemplo al desplazar de golpe
/// hasta el fondo), eso son 35 descargas+decodificaciones concurrentes sin
/// ningún tope. Esa vía de memoria quedó explícitamente sin medir en el
/// diagnóstico; este limitador es la mitigación aunque no haya medición
/// exacta del pico que evita.
///
/// `actor` en vez de un semáforo con `NSLock`: todo el estado (`enCurso`,
/// `esperando`) solo se toca desde dentro, así que el aislamiento del actor
/// ya da exclusión mutua sin candados explícitos.
public actor LimitadorConcurrencia {
    private let maximo: Int
    private var enCurso = 0
    private var esperando: [CheckedContinuation<Void, Never>] = []

    /// Mayor cantidad de tareas que llegaron a correr a la vez desde que se
    /// creó. Solo para pruebas: confirma que el tope de verdad se respeta.
    public private(set) var picoObservado = 0

    public init(maximo: Int) {
        precondition(maximo > 0, "un limitador de 0 nunca dejaría pasar nada")
        self.maximo = maximo
    }

    /// Corre `trabajo` en cuanto haya un cupo libre; si no hay, espera en
    /// fila. Libera el cupo apenas `trabajo` termina (con éxito o con error),
    /// para que el siguiente en la fila pueda arrancar.
    public func ejecutar<T: Sendable>(_ trabajo: @Sendable () async throws -> T) async rethrows -> T {
        await adquirir()
        do {
            let resultado = try await trabajo()
            liberar()
            return resultado
        } catch {
            liberar()
            throw error
        }
    }

    private func adquirir() async {
        if enCurso < maximo {
            enCurso += 1
            picoObservado = max(picoObservado, enCurso)
            return
        }
        await withCheckedContinuation { continuacion in
            esperando.append(continuacion)
        }
        // Quien nos despertó (`liberar`) ya nos dejó "contados" adentro:
        // pasó la posta sin bajar `enCurso`, así que acá solo falta anotar
        // el pico -- sumar de nuevo contaría el mismo cupo dos veces.
        picoObservado = max(picoObservado, enCurso)
    }

    private func liberar() {
        if !esperando.isEmpty {
            // El cupo se lo queda quien esperaba, pasado de mano en mano
            // DENTRO de esta misma llamada (sin `await` de por medio): así
            // ningún `adquirir()` que llegue después puede colarse y tomar
            // un cupo que ya tiene dueño.
            let siguiente = esperando.removeFirst()
            siguiente.resume()
        } else {
            enCurso -= 1
        }
    }
}
