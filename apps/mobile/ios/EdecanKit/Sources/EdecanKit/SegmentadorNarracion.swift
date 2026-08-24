import Foundation

/// Parte la narración de un turno con herramientas en tarjetas cortas.
///
/// En vivo, Edecán va diciendo una frase por paso; al persistir, el servidor
/// guarda un solo `content`. Sin esto, al reabrir el chat se ve un muro.
public enum SegmentadorNarracion {
    public static func tarjetas(_ texto: String) -> [String] {
        let limpio = SpeechTags.ocultar(texto).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !limpio.isEmpty else { return [] }
        if limpio.contains("```") { return [limpio] }

        let parrafos = limpio
            .components(separatedBy: "\n\n")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        let fuente = parrafos.count >= 2 ? parrafos : [limpio]
        return fuente.flatMap(OracionesVoz.partir).filter { !$0.isEmpty }
    }

    /// El hilo de mini cards es para un relato de trabajo, no para chat.
    /// Quien llama debe haber comprobado que el mensaje trae `trabajo`.
    public static func debeMostrarHilo(tarjetas: [String]) -> Bool {
        tarjetas.count >= 2
    }
}
