import Foundation

/// Detector de voz con umbral ADAPTATIVO (puerta de ruido por software): mide
/// el piso de ruido del entorno (aire, calle, nevera) y solo declara "voz"
/// cuando el nivel sube claramente por encima de ese piso, con racha sostenida
/// para descartar golpes sueltos. Es el "aislador de ruido" de la llamada:
/// Edecán solo presta atención a la voz que le habla.
struct VadAdaptativo {
    /// Umbral mínimo absoluto (nunca baja de acá aunque el ambiente sea mudo:
    /// evita que un susurro de fondo arranque turnos en una habitación quieta).
    static let umbralMinimo: Float = 0.0005
    /// Factor sobre el piso de ruido: la voz debe superar 4× el ruido base.
    static let factorSobreRuido: Float = 2.5

    /// Chunks de 100 ms consecutivos con voz para dar por iniciado un turno.
    static let rachaInicio = 3
    static let ventanaVoz = 3
    static let minimosEnVentana = 2
    /// Chunks consecutivos con voz para aceptar un barge-in (cortar a Edecán).
    static let rachaBargeIn = 3
    /// Silencio máximo dentro del turno antes de cerrarlo (ms).
    static let silencioMaxMs = 1000
    /// Voz mínima real en un turno para considerarlo válido (ms); menos es ruido.
    static let vozMinimaMs = 300

    private(set) var umbral: Float
    private var ruidoBase: Float
    private var niveles: [Float] = []
    private var ultimos: [Bool] = []
    var calibrado: Bool { niveles.count >= 8 }
    var vozReciente: Bool { ultimos.filter { $0 }.count >= Self.minimosEnVentana }
    private(set) var racha = 0
    private(set) var vozMs = 0
    private(set) var silencioMs = 0

    init(ruidoBaseInicial: Float = 0.0002) {
        self.ruidoBase = ruidoBaseInicial
        self.umbral = max(Self.umbralMinimo, ruidoBaseInicial * Self.factorSobreRuido)
    }

    /// Alimenta el RMS de un chunk de 100 ms. Devuelve `true` si ese chunk se
    /// considera voz. Los chunks de silencio van bajando el piso de ruido y
    /// recalibrando el umbral.
    mutating func alimentar(rms: Float) -> Bool {
        niveles.append(max(0, rms))
        if niveles.count > 40 { niveles.removeFirst() }
        let ordenados = niveles.sorted()
        if !vozReciente {
            ruidoBase = ordenados[(ordenados.count - 1) / 5]
            umbral = max(Self.umbralMinimo, ruidoBase * Self.factorSobreRuido)
        }
        guard calibrado else { return false }
        let encima = rms > umbral
        ultimos.append(encima)
        if ultimos.count > Self.ventanaVoz { ultimos.removeFirst() }
        if vozReciente {
            racha += 1
            vozMs += 100
            silencioMs = 0
        } else {
            racha = 0
            silencioMs += 100
        }
        return encima
    }

    /// Reinicia el estado de un turno (sin perder el umbral aprendido).
    mutating func reiniciarTurno() {
        racha = 0
        vozMs = 0
        silencioMs = 0
        ultimos = []
    }

    /// Un turno es válido si acumuló al menos la voz mínima real.
    var turnoValido: Bool {
        vozMs >= Self.vozMinimaMs
    }
}
