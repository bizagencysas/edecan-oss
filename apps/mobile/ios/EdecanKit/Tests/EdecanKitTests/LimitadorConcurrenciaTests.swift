import XCTest
@testable import EdecanKit

/// Cubre la garantía de la que depende el arreglo de memoria del chat: abrir
/// un hilo con muchos adjuntos NUNCA debe decodificar más de `maximo`
/// imágenes a la vez, sin importar cuántas filas pidan su imagen de golpe.
final class LimitadorConcurrenciaTests: XCTestCase {
    func testNuncaDejaCorrerMasDeMaximoALaVez() async {
        let limitador = LimitadorConcurrencia(maximo: 3)
        let contador = ContadorEnCurso()

        await withTaskGroup(of: Void.self) { grupo in
            for _ in 0..<20 {
                grupo.addTask {
                    await limitador.ejecutar {
                        await contador.entrar()
                        // Cede el hilo para que, si el tope estuviera roto,
                        // varias tareas alcancen a solaparse de verdad.
                        try? await Task.sleep(nanoseconds: 2_000_000)
                        await contador.salir()
                    }
                }
            }
        }

        let pico = await contador.picoObservado
        XCTAssertLessThanOrEqual(pico, 3, "nunca debieron correr más de 3 tareas a la vez")
        let picoDelLimitador = await limitador.picoObservado
        XCTAssertLessThanOrEqual(picoDelLimitador, 3)
        XCTAssertGreaterThan(picoDelLimitador, 0, "con 20 tareas y tope 3, el limitador sí debió llegar al tope")
    }

    func testDejaCorrerATodasEventualmente() async {
        let limitador = LimitadorConcurrencia(maximo: 2)
        let terminadas = ContadorSimple()

        await withTaskGroup(of: Void.self) { grupo in
            for _ in 0..<10 {
                grupo.addTask {
                    await limitador.ejecutar {
                        await terminadas.incrementar()
                    }
                }
            }
        }

        let total = await terminadas.valor
        XCTAssertEqual(total, 10, "nadie debía quedarse esperando para siempre")
    }

    func testPropagaElErrorYLiberaElCupo() async {
        struct ErrorDePrueba: Error {}
        let limitador = LimitadorConcurrencia(maximo: 1)

        do {
            try await limitador.ejecutar { throw ErrorDePrueba() }
            XCTFail("debía relanzar el error")
        } catch is ErrorDePrueba {
            // esperado
        } catch {
            XCTFail("relanzó un error distinto: \(error)")
        }

        // Si el cupo no se hubiera liberado tras el error, esto colgaría.
        let resultado = await limitador.ejecutar { 7 }
        XCTAssertEqual(resultado, 7)
    }
}

private actor ContadorEnCurso {
    private var enCurso = 0
    private(set) var picoObservado = 0

    func entrar() {
        enCurso += 1
        picoObservado = max(picoObservado, enCurso)
    }

    func salir() {
        enCurso -= 1
    }
}

private actor ContadorSimple {
    private(set) var valor = 0
    func incrementar() { valor += 1 }
}
