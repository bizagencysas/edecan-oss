import EdecanKit
import SwiftUI

/// Catálogo de voces del tenant (`GET /v1/voz/voces`): voces de stock de
/// ElevenLabs, clones propios y los stubs offline cuando no hay credencial.
/// Tocar una voz la elige (persistida en el dispositivo) y la previsualiza
/// con una frase corta. Es la voz que usan la llamada y el altavoz del chat.
struct VocesView: View {
    @Environment(\.dismiss) private var dismiss
    @AppStorage("vozElegidaId") private var vozElegidaId = "0uHpKhb0ymsdvmCtPV8y"
    @AppStorage("vozElegidaNombre") private var vozElegidaNombre = "Edecán"

    let client: APIClient?

    @State private var voces: [VozElegible] = []
    @State private var cargando = true
    @State private var errorMensaje: String?
    @State private var reproduciendoId: String?
    @State private var reproductor: ReproductorMPEGStream?

    var body: some View {
        NavigationStack {
            Group {
                if cargando {
                    HStack(spacing: 10) { ProgressView(); Text("Cargando voces…") }
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if let errorMensaje {
                    VStack(spacing: 10) {
                        Image(systemName: "waveform.badge.exclamationmark")
                            .font(.largeTitle)
                            .foregroundStyle(.secondary)
                        Text(errorMensaje)
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.center)
                            .padding(.horizontal, 30)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    List(voces) { voz in
                        fila(voz)
                    }
                    .listStyle(.plain)
                }
            }
            .navigationTitle("Elegir voz")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Listo") { dismiss() }
                        .fontWeight(.semibold)
                }
            }
            .task {
                await cargar()
            }
        }
    }

    private func fila(_ voz: VozElegible) -> some View {
        let elegida = voz.voiceId == vozElegidaId
        let reproduciendo = reproduciendoId == voz.voiceId
        return Button {
            seleccionar(voz)
        } label: {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(voz.nombre)
                        .font(.body.weight(elegida ? .semibold : .regular))
                        .foregroundStyle(elegida ? EdecanTheme.morado : .primary)
                    Text(categoriaLegible(voz.categoria))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if reproduciendo {
                    ProgressView()
                } else {
                    Image(systemName: elegida ? "checkmark.circle.fill" : "play.circle")
                        .font(.title3)
                        .foregroundStyle(elegida ? EdecanTheme.morado : .secondary)
                }
            }
            .padding(.vertical, 4)
        }
        .buttonStyle(.plain)
    }

    private func seleccionar(_ voz: VozElegible) {
        vozElegidaId = voz.voiceId
        vozElegidaNombre = voz.nombre
        Task { await previsualizar(voz) }
    }

    private func previsualizar(_ voz: VozElegible) async {
        guard let client else { return }
        reproductor?.detener()
        let player = ReproductorMPEGStream()
        reproductor = player
        reproduciendoId = voz.voiceId
        do {
            let stream = try await client.hablarStream(
                texto: "Hola, soy Edecán.", voiceId: voz.voiceId, modelId: "eleven_turbo_v2_5"
            )
            _ = try await player.reproducir(stream: stream)
        } catch {
            // La elección ya quedó guardada; el preview es solo un extra.
        }
        reproduciendoId = nil
    }

    private func cargar() async {
        cargando = true
        errorMensaje = nil
        guard let client else {
            cargando = false
            errorMensaje = "No hay sesión activa."
            return
        }
        do {
            voces = try await client.listarVoces()
        } catch {
            errorMensaje = "No se pudieron cargar las voces: \(error.localizedDescription)"
        }
        cargando = false
    }

    private func categoriaLegible(_ categoria: String) -> String {
        switch categoria {
        case "cloned": return "Tu clon de voz"
        case "generated": return "Generada"
        case "professional": return "Profesional"
        default: return "Voz de stock"
        }
    }
}