import Foundation

/// No intenta distinguir voces por volumen: descarta la entrada durante la
/// salida del altavoz y su cola acustica, incluidos buffers capturados antes.
struct PuertaEcoVoz {
    private var reproduciendo = false
    private var aceptarDesde: TimeInterval = 0
    private let colaAcustica: TimeInterval = 0.45

    mutating func comenzarSalida() {
        reproduciendo = true
    }

    mutating func terminarSalida(ahora: TimeInterval) {
        reproduciendo = false
        aceptarDesde = ahora + colaAcustica
    }

    func acepta(capturadoEn: TimeInterval, ahora: TimeInterval) -> Bool {
        !reproduciendo && ahora >= aceptarDesde && capturadoEn >= aceptarDesde
    }
}
