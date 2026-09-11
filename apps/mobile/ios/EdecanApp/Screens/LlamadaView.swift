import EdecanKit
import SwiftUI

/// Llamada continua con Edecán a pantalla completa: entra escuchando sin parar
/// hasta tocar la X. La transcripción del usuario aparece EN VIVO (parciales)
/// y la respuesta de Edecán se va reproduciendo en cuanto empieza a llegar
/// (PCM en streaming). El cerebro es el chat real (tools incluidas); la voz es
/// la que el usuario eligió en ``VocesView``.
struct LlamadaView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(SessionStore.self) private var session
    @AppStorage("vozElegidaId") private var vozElegidaId = "0uHpKhb0ymsdvmCtPV8y"
    @AppStorage("vozElegidaNombre") private var vozElegidaNombre = "Edecán"

    let chat: ChatViewModel
    @State private var viewModel = LlamadaViewModel()
    @State private var mostrandoVoces = false
    @State private var pulso = false

    var body: some View {
        ZStack {
            LinearGradient(
                colors: [EdecanTheme.morado.opacity(0.25), EdecanTheme.morado.opacity(0.06)],
                startPoint: .top,
                endPoint: .bottom
            )
            .ignoresSafeArea()

            VStack(spacing: 24) {
                Spacer(minLength: 12)

                Text("Llamada con Edecán")
                    .font(.title3.weight(.semibold))
                Text("Voz: \(vozElegidaNombre)")
                    .font(.caption)
                    .foregroundStyle(.secondary)

                textoDeEstado
                    .padding(.horizontal, 24)

                ondaDeVoz

                // Confirmación de tool pendiente: la tarjeta manda — el
                // micrófono no abre turnos hasta que el usuario decida.
                if let confirmacion = chat.confirmacionPendiente {
                    TarjetaConfirmacion(
                        confirmacion: confirmacion,
                        deshabilitada: viewModel.estado == .pensando
                    ) { aprobado in
                        guard let client = session.client else { return }
                        Task {
                            await viewModel.resolverConfirmacion(
                                aprobado: aprobado, client: client
                            )
                        }
                    }
                    .frame(maxWidth: 340)
                    .padding(.horizontal, 8)
                } else {
                    // Burbuja EN VIVO del usuario: el parcial mientras habla y,
                    // si ya cerró el turno, la última transcripción confirmada.
                    if let textoUsuario = textoUsuarioVisible {
                        burbuja(titulo: "Tú", texto: textoUsuario, alineacionDerecha: true)
                    }
                    if let respuesta = respuestaVisible {
                        burbuja(titulo: "Edecán", texto: respuesta, alineacionDerecha: false)
                    }
                }

                if let error = viewModel.errorMensaje {
                    Text(error)
                        .font(.footnote)
                        .foregroundStyle(.red)
                        .padding(.horizontal, 24)
                }

                Spacer(minLength: 12)

                HStack(spacing: 40) {
                    Button {
                        viewModel.terminarLlamada()
                        dismiss()
                    } label: {
                        Image(systemName: "phone.down.fill")
                            .font(.system(size: 22, weight: .semibold))
                            .frame(width: 64, height: 64)
                            .foregroundStyle(.white)
                            .background(Circle().fill(.red))
                    }
                    .accessibilityLabel("Colgar")

                    Button {
                        mostrandoVoces = true
                    } label: {
                        Image(systemName: "waveform.badge.mic")
                            .font(.system(size: 20, weight: .semibold))
                            .frame(width: 56, height: 56)
                            .foregroundStyle(EdecanTheme.morado)
                            .tarjetaVidrio(esquina: 28)
                    }
                    .accessibilityLabel("Elegir voz")
                }
                .padding(.bottom, 36)
            }
            .padding(.horizontal, 20)
        }
        .sheet(isPresented: $mostrandoVoces) {
            VocesView(client: session.client)
        }
        .onAppear {
            viewModel.chat = chat
            viewModel.vozId = vozElegidaId
            viewModel.iniciar(client: session.client)
        }
        .onChange(of: vozElegidaId) { _, nueva in
            viewModel.vozId = nueva
        }
        .onDisappear {
            viewModel.terminarLlamada()
        }
    }

    private var textoUsuarioVisible: String? {
        let candidato = viewModel.textoParcial ?? viewModel.ultimaTranscripcion
        guard let candidato, !candidato.isEmpty else { return nil }
        return candidato
    }

    private var respuestaVisible: String? {
        guard let respuesta = viewModel.ultimaRespuesta, !respuesta.isEmpty else { return nil }
        return respuesta
    }

    @ViewBuilder
    private var textoDeEstado: some View {
        switch viewModel.estado {
        case .inactivo:
            Text("Colgando…")
                .font(.subheadline)
                .foregroundStyle(.secondary)
        case .inicializando:
            HStack(spacing: 8) { ProgressView(); Text("Preparando micrófono…") }
                .font(.subheadline)
                .foregroundStyle(.secondary)
        case .escuchando:
            HStack(spacing: 8) {
                puntoPulsante
                Text("Escuchando…")
                Text(String(format: "· nivel %.3f", viewModel.nivelEntrada))
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(EdecanTheme.azul)
        case .transcribiendo:
            HStack(spacing: 8) { ProgressView(); Text("Transcribiendo…") }
                .font(.subheadline)
                .foregroundStyle(.secondary)
        case .pensando:
            HStack(spacing: 8) {
                ProgressView()
                Text(chat.herramientaActiva.map { "Usando \($0.nombre)…" } ?? "Edecán está pensando…")
            }
            .font(.subheadline)
            .foregroundStyle(.secondary)
        case .hablando:
            HStack(spacing: 8) { Image(systemName: "speaker.wave.2.fill"); Text("Hablando…") }
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(EdecanTheme.morado)
        }
    }

    /// Punto rojo/azul que pulsa mientras Edecán escucha.
    private var puntoPulsante: some View {
        Circle()
            .fill(EdecanTheme.azul)
            .frame(width: 10, height: 10)
            .opacity(pulso ? 1 : 0.35)
            .animation(.easeInOut(duration: 0.6).repeatForever(autoreverses: true), value: pulso)
            .onAppear { pulso = true }
            .onDisappear { pulso = false }
    }

    /// Onda animada cuando alguien habla (escuchando o respondiendo); la
    /// altura de las barras sube con el RMS del micrófono para dar feedback
    /// en vivo.
    private var ondaDeVoz: some View {
        HStack(spacing: 5) {
            ForEach(0..<5, id: \.self) { indice in
                RoundedRectangle(cornerRadius: 3)
                    .fill(colorOnda())
                    .frame(width: 6, height: alturaBarra(indice))
                    .animation(
                        .easeInOut(duration: 0.5).repeatForever(autoreverses: true).delay(Double(indice) * 0.08),
                        value: pulso
                    )
            }
        }
        .frame(height: 48)
        .onAppear { pulso = true }
    }

    private func colorOnda() -> Color {
        switch viewModel.estado {
        case .hablando: return EdecanTheme.morado
        case .escuchando: return EdecanTheme.azul
        default: return Color.secondary.opacity(0.35)
        }
    }

    private func alturaBarra(_ indice: Int) -> CGFloat {
        let activo = viewModel.estado == .escuchando || viewModel.estado == .hablando
        guard activo else { return 10 }
        let base: [CGFloat] = [26, 40, 30, 44, 24]
        let aporteRMS = CGFloat(min(1.0, max(0.0, viewModel.nivelEntrada * 10)))
        let factor: CGFloat = max(0.4, (0.5 + aporteRMS) * (pulso ? 1.0 : 0.8))
        return base[indice] * factor
    }

    private func burbuja(titulo: String, texto: String, alineacionDerecha: Bool) -> some View {
        VStack(alignment: alineacionDerecha ? .trailing : .leading, spacing: 4) {
            Text(titulo).font(.caption).foregroundStyle(.secondary)
            Text(texto)
                .font(.body)
                .multilineTextAlignment(alineacionDerecha ? .trailing : .leading)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .tarjetaVidrio(esquina: 16)
        }
        .frame(maxWidth: .infinity, alignment: alineacionDerecha ? .trailing : .leading)
        .padding(.horizontal, 8)
    }
}