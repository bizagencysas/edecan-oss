import XCTest
@testable import EdecanKit

/// Antes de que existiera ``CacheAcotada``, `BurbujaMensaje` reparseaba el
/// Markdown de un mensaje en cada evaluación de `body` -- estas pruebas
/// cubren exactamente las dos garantías de las que depende ese arreglo:
/// que un mismo par (clave, texto) SOLO se calcule una vez, y que la caché
/// tenga un tope duro para no crecer sin límite en un hilo largo.
final class CacheAcotadaTests: XCTestCase {
    func testValorOCalcularSoloInvocaElCalculoUnaVezPorClave() {
        let cache = CacheAcotada<String, Int>(capacidad: 10)
        var invocaciones = 0

        let primero = cache.valorOCalcular("a") { invocaciones += 1; return 42 }
        let segundo = cache.valorOCalcular("a") { invocaciones += 1; return 99 }

        XCTAssertEqual(primero, 42)
        XCTAssertEqual(segundo, 42, "la segunda llamada debe devolver el valor cacheado, no recalcular")
        XCTAssertEqual(invocaciones, 1)
    }

    func testClavesDistintasNoComparteValor() {
        let cache = CacheAcotada<String, Int>(capacidad: 10)
        cache.guardar(1, para: "a")
        cache.guardar(2, para: "b")

        XCTAssertEqual(cache.valor("a"), 1)
        XCTAssertEqual(cache.valor("b"), 2)
        XCTAssertNil(cache.valor("c"))
    }

    func testDesalojaLaEntradaMenosUsadaAlSuperarLaCapacidad() {
        let cache = CacheAcotada<Int, String>(capacidad: 2)
        cache.guardar("uno", para: 1)
        cache.guardar("dos", para: 2)
        // Tocar `1` la vuelve la más recientemente usada -- `2` queda como
        // la candidata a salir cuando entre una tercera clave.
        _ = cache.valor(1)
        cache.guardar("tres", para: 3)

        XCTAssertEqual(cache.valor(1), "uno", "la usada hace poco no debía desalojarse")
        XCTAssertNil(cache.valor(2), "la menos usada debía salir para hacerle sitio a la nueva")
        XCTAssertEqual(cache.valor(3), "tres")
        XCTAssertEqual(cache.conteo, 2)
    }

    func testVaciarDejaLaCacheEnCero() {
        let cache = CacheAcotada<String, Int>(capacidad: 10)
        cache.guardar(1, para: "a")
        cache.guardar(2, para: "b")
        XCTAssertEqual(cache.conteo, 2)

        cache.vaciar()

        XCTAssertEqual(cache.conteo, 0)
        XCTAssertNil(cache.valor("a"))
    }
}
