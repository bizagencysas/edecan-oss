import Foundation

@main
enum VoiceVADRegression {
    static func main() {
        var vad = VadAdaptativo()
        for _ in 0..<20 { _ = vad.alimentar(rms: 0.0001) }
        precondition(!vad.vozReciente, "Silence must not start a turn")
        let threshold = vad.umbral
        _ = vad.alimentar(rms: 0.002)
        precondition(vad.umbral == threshold, "Do not learn the first syllable as noise")
        for _ in 0..<8 { _ = vad.alimentar(rms: 0.002) }
        precondition(vad.vozReciente && vad.turnoValido, "Soft speech must open a valid turn")
        for _ in 0..<12 { _ = vad.alimentar(rms: 0.0001) }
        precondition(vad.silencioMs >= VadAdaptativo.silencioMaxMs, "A pause must close the turn")
        vad.reiniciarTurno()
        _ = vad.alimentar(rms: 0.2)
        for _ in 0..<5 { _ = vad.alimentar(rms: 0.0001) }
        precondition(!vad.turnoValido && !vad.vozReciente, "An isolated impact is not a turn")
        for _ in 0..<8 { _ = vad.alimentar(rms: 0.002) }
        precondition(vad.turnoValido, "The next quiet turn must still work")
        var ambiente = VadAdaptativo()
        for _ in 0..<30 { _ = ambiente.alimentar(rms: 0.003) }
        precondition(!ambiente.turnoValido && !ambiente.vozReciente, "Learn steady room noise above the initial floor")
        for _ in 0..<8 { _ = ambiente.alimentar(rms: 0.012) }
        precondition(ambiente.turnoValido, "Speech above learned room noise must pass")
        print("PASS: silence, soft speech, first syllable, end-of-turn, impact rejection, second turn")
    }
}
