import SwiftUI

/// Controles de voz del hilo actual, sin una pantalla de llamada paralela.
struct VozEnChatBarra: View {
    let llamada: LlamadaViewModel
    let onVoces: () -> Void
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(SessionStore.self) private var session

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            controles
            if llamada.puedeReintentar {
                HStack(spacing: 16) {
                    Button("Reintentar") { llamada.reintentarTurno(client: session.client) }
                        .accessibilityIdentifier("voice-retry-turn")
                    Button("Descartar") { llamada.descartarTurno() }
                        .accessibilityIdentifier("voice-discard-turn")
                }
                .font(.subheadline)
                .padding(.leading, 54)
                .padding(.bottom, 8)
            }
        }
    }

    private var controles: some View {
        HStack(spacing: 10) {
            Button {
                llamada.interrumpirDesdeChat()
            } label: {
                ZStack {
                    Circle().fill(EdecanTheme.degradado)
                    Image(systemName: llamada.estado == .hablando ? "waveform" : "mic.fill")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(.white)
                }
                .frame(width: 32, height: 32)
                .scaleEffect(reduceMotion ? 1 : 1 + CGFloat(min(llamada.nivelEntrada * 8, 0.12)))
                .animation(.easeOut(duration: 0.1), value: llamada.nivelEntrada)
                .frame(width: 44, height: 44)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(llamada.estado == .hablando ? "Interrumpir respuesta" : "Voz activa")
            .accessibilityIdentifier("voice-inline-orb")
            Text(estadoTexto)
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .lineLimit(2)
            Spacer(minLength: 0)
            Button(action: onVoces) {
                Image(systemName: "slider.horizontal.3").frame(width: 44, height: 44)
            }
            .disabled(llamada.estado != .escuchando)
            .accessibilityLabel("Elegir voz")
            Button { llamada.terminarLlamada() } label: {
                Image(systemName: "xmark").frame(width: 44, height: 44)
            }
            .accessibilityLabel("Terminar escucha")
            .accessibilityIdentifier("voice-inline-stop")
        }
        .buttonStyle(.plain)
    }

    private var estadoTexto: String {
        if llamada.chat?.confirmacionPendiente != nil { return "Esperando tu aprobación" }
        if let error = llamada.errorMensaje { return error }
        switch llamada.estado {
        case .inactivo: return "Voz desactivada"
        case .inicializando: return "Activando voz…"
        case .escuchando: return "Te escucho"
        case .transcribiendo: return "Transcribiendo…"
        case .pensando: return llamada.chat?.herramientaActiva.map { "Usando \($0.nombre)…" } ?? "Preparando respuesta…"
        case .hablando: return "Hablando · toca el orb para interrumpir"
        }
    }
}
