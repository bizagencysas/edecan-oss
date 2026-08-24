import Foundation

/// Caché en memoria con tope duro de entradas y desalojo LRU (la más vieja
/// usada sale primero). Sin el tope, un caché "de una sola sesión" crece sin
/// límite mientras dura el hilo -- y un hilo largo es justo el caso que más
/// presión de memoria mete.
///
/// Nace del diagnóstico del crash por memoria del chat: `BurbujaMensaje`
/// (`EdecanApp`) reparseaba el Markdown de un mensaje EN CADA evaluación de
/// `body`, aunque el texto no hubiera cambiado -- medido, +92 MB de pico y
/// hasta 18 ms de bloqueo del hilo principal en un mensaje real de 32 kB.
/// Memoizar por `(id del mensaje, texto)` evita ese reparseo repetido.
///
/// Vive en `EdecanKit` (no en la vista) por lo mismo que ``TextoRicoParser``:
/// es lógica pura, sin SwiftUI, así que se puede probar sin simulador.
///
/// `@unchecked Sendable` porque el candado interno (`NSLock`) es la garantía
/// real de que es seguro compartir esta clase entre tareas -- el compilador
/// no puede verlo, pero el candado sí lo cumple.
public final class CacheAcotada<Clave: Hashable, Valor>: @unchecked Sendable {
    private var almacen: [Clave: Valor] = [:]
    /// Orden de uso más reciente al final; el candidato a desalojo es el primero.
    private var ordenDeUso: [Clave] = []
    private let capacidad: Int
    private let cerrojo = NSLock()

    public init(capacidad: Int) {
        precondition(capacidad > 0, "una caché de capacidad 0 no cachea nada")
        self.capacidad = capacidad
    }

    /// Cantidad de entradas vivas ahora mismo. Solo para pruebas/diagnóstico.
    public var conteo: Int {
        cerrojo.lock()
        defer { cerrojo.unlock() }
        return almacen.count
    }

    public func valor(_ clave: Clave) -> Valor? {
        cerrojo.lock()
        defer { cerrojo.unlock() }
        guard let encontrado = almacen[clave] else { return nil }
        marcarUsada(clave)
        return encontrado
    }

    public func guardar(_ valor: Valor, para clave: Clave) {
        cerrojo.lock()
        defer { cerrojo.unlock() }
        if almacen[clave] == nil, almacen.count >= capacidad {
            desalojarMasVieja()
        }
        almacen[clave] = valor
        marcarUsada(clave)
    }

    /// Devuelve el valor cacheado o lo calcula y lo guarda. Si dos llamadores
    /// piden la misma clave a la vez, las dos pueden calcular -- no hay
    /// candado más fino que eso -- pero el resultado final es el mismo y
    /// ninguna corrompe al almacén; para el caso de uso (Markdown ya
    /// parseado) recalcular una vez de más es barato comparado con el
    /// candado que haría falta para evitarlo del todo.
    public func valorOCalcular(_ clave: Clave, calcular: () -> Valor) -> Valor {
        if let existente = valor(clave) { return existente }
        let calculado = calcular()
        guardar(calculado, para: clave)
        return calculado
    }

    /// Vacía todo. Pensado para que quien integre esta caché la purgue en
    /// cuanto el sistema operativo avise presión de memoria (en iOS,
    /// `UIApplication.didReceiveMemoryWarningNotification`) -- mejor
    /// recalcular una vez más que dejar que la app muera por acumular.
    public func vaciar() {
        cerrojo.lock()
        defer { cerrojo.unlock() }
        almacen.removeAll()
        ordenDeUso.removeAll()
    }

    private func marcarUsada(_ clave: Clave) {
        ordenDeUso.removeAll { $0 == clave }
        ordenDeUso.append(clave)
    }

    private func desalojarMasVieja() {
        guard !ordenDeUso.isEmpty else { return }
        let vieja = ordenDeUso.removeFirst()
        almacen.removeValue(forKey: vieja)
    }
}
