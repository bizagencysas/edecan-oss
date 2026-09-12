import Foundation

@main
enum VoiceEchoGateRegression {
    static func main() {
        var gate = PuertaEcoVoz()
        precondition(gate.acepta(capturadoEn: 1, ahora: 1))
        gate.comenzarSalida()
        // Da igual cuan fuerte sea el eco: el audio no llega al VAD ni al WS.
        for tick in 0..<100 {
            let now = 2 + Double(tick) * 0.1
            precondition(!gate.acepta(capturadoEn: now, ahora: now))
        }
        gate.terminarSalida(ahora: 12)
        precondition(!gate.acepta(capturadoEn: 12.2, ahora: 12.2))
        precondition(!gate.acepta(capturadoEn: 11, ahora: 13), "Buffered speaker audio must not leak into the next turn")
        precondition(!gate.acepta(capturadoEn: 12.2, ahora: 13), "Buffered acoustic tail must be discarded")
        precondition(gate.acepta(capturadoEn: 12.5, ahora: 12.5))
        gate.comenzarSalida()
        precondition(!gate.acepta(capturadoEn: 15, ahora: 15))
        gate.terminarSalida(ahora: 15) // manual orb interruption
        precondition(gate.acepta(capturadoEn: 15.5, ahora: 15.5))
        print("PASS: input, long playback, acoustic tail, queued echo, manual interrupt, second turn")
    }
}
