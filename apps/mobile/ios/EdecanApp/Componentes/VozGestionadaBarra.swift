import EdecanKit
import SwiftUI

/// Orb de voz GESTIONADA dentro del chat (modo Speech Engine, ver
/// `docs/speech-engine.md`). No reemplaza la barra legacy de
/// `LlamadaViewModel` — son modos distintos y nunca corren a la vez.
///
/// - Inactivo: un orb discreto para arrancar la voz gestionada.
/// - Activo: muestra el enunciado en vivo, la respuesta que va llegando y la
///   X que detiene micrófono/audio dejándote en el MISMO chat (lo hablado ya
///   quedó en la conversación canónica).
/// - Sin configuración: aviso + acceso a la hoja de configuración. NUNCA se
///   etiqueta el pipeline legacy como "voz gestionada".
struct VozGestionadaBarra: View {
    let gestionada: VozGestionadaViewModel
    let client: APIClient?
    /// Conversación canónica abierta: la voz gestionada se ata a ESTA, no a
    /// la principal del backend (continuidad texto→voz en cualquier chat).
    var conversationIdActual: String? = nil
    var onConfigurar: () -> Void = {}

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(SessionStore.self) private var session

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 10) {
                orb
                if gestionada.estaActivo {
                    VStack(alignment: .leading, spacing: 2) {
                        if !gestionada.textoUsuario.isEmpty {
                            Text("Tú: \(gestionada.textoUsuario)")
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                                .lineLimit(2)
                        } else if !gestionada.textoAgente.isEmpty {
                            Text(gestionada.textoAgente)
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                                .lineLimit(2)
                        } else {
                            Text("Edecán: \(gestionada.actividadAgente)")
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        }
                    }
                    Spacer(minLength: 0)
                    Button {
                        Task { await gestionada.detener(client: session.client ?? client) }
                    } label: {
                        Image(systemName: "xmark").frame(width: 44, height: 44)
                    }
                    .accessibilityLabel("Terminar voz gestionada")
                    .accessibilityIdentifier("voice-managed-stop")
                } else {
                    Text(textoInactivo)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                    Spacer(minLength: 0)
                    Button(action: onConfigurar) {
                        Image(systemName: "slider.horizontal.3").frame(width: 44, height: 44)
                    }
                    .accessibilityLabel("Configurar voz gestionada")
                }
            }
            .buttonStyle(.plain)
        }
    }

    private var orb: some View {
        Button {
            Task {
                await gestionada.iniciar(
                    client: session.client ?? client, conversationId: conversationIdActual
                )
            }
        } label: {
            ZStack {
                Circle().fill(EdecanTheme.degradado)
                Image(systemName: gestionada.estaActivo ? "waveform" : "mic.fill")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(.white)
            }
            .frame(width: 32, height: 32)
            .scaleEffect(
                reduceMotion || !gestionada.estaActivo
                    ? 1
                    : 1 + CGFloat(min(gestionada.nivelEntrada * 8, 0.12))
            )
            .animation(.easeOut(duration: 0.1), value: gestionada.nivelEntrada)
            .frame(width: 44, height: 44)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(gestionada.estaActivo ? "Voz gestionada activa" : "Activar voz gestionada")
        .accessibilityIdentifier("voice-managed-orb")
    }

    private var textoInactivo: String {
        switch gestionada.estado {
        case .inactivo: return "Voz gestionada desactivada"
        case .activando: return "Activando voz gestionada…"
        case .activo: return "Conectada"
        case .error(let mensaje): return mensaje
        }
    }
}