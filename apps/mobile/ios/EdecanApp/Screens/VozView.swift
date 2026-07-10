import SwiftUI
import EdecanKit

/// Pestaña "Voz" (antes "Llamadas" en el esqueleto original): *push-to-talk*
/// nativo con `AVAudioEngine` — mantén presionado para hablar, Edecán
/// transcribe (`POST /v1/voice/transcribe`), corre el turno de chat completo
/// y responde en voz (`POST /v1/voice/speak`). El historial de llamadas de
/// telefonía premium por tenant sigue pendiente (`docs/voz-telefonia.md`) —
/// ver `docs/movil-ios.md`.
struct VozView: View {
    @Environment(SessionStore.self) private var session
    @Environment(TabRouter.self) private var tabRouter
    @State private var viewModel = VozViewModel()
    @State private var presionando = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 24) {
                    if viewModel.usandoVozDePrueba {
                        avisoVozDePrueba
                    }

                    if let transcripcion = viewModel.ultimaTranscripcion {
                        burbujaTexto(titulo: "Dijiste", texto: transcripcion, alineacionDerecha: true)
                    }

                    if let confirmacion = viewModel.chat.confirmacionPendiente {
                        TarjetaConfirmacion(confirmacion: confirmacion, deshabilitada: viewModel.estado == .procesando) { aprobado in
                            resolverConfirmacion(aprobado: aprobado)
                        }
                    } else if let respuesta = viewModel.chat.ultimaRespuestaDelAsistente, !respuesta.isEmpty {
                        burbujaTexto(titulo: "Edecán", texto: respuesta, alineacionDerecha: false)
                    }

                    if let error = viewModel.errorParaMostrar {
                        Text(error).font(.footnote).foregroundStyle(.red)
                    }

                    Spacer(minLength: 40)

                    textoDeEstado
                    botonMicrofono
                    Text("Mantén presionado para hablar")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .padding()
                .frame(maxWidth: .infinity, minHeight: 500)
            }
            .background(EdecanTheme.degradado.opacity(0.05).ignoresSafeArea())
            .navigationTitle("Voz")
            .navigationBarTitleDisplayMode(.inline)
        }
    }

    private var avisoVozDePrueba: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "info.circle.fill").foregroundStyle(EdecanTheme.azul)
            VStack(alignment: .leading, spacing: 6) {
                Text("Estás escuchando una voz de prueba")
                    .font(.subheadline.weight(.semibold))
                Text("Tu tenant no tiene una credencial de voz conectada — conecta ElevenLabs o Polly en Perfil para una voz real.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                Button("Ir a Perfil") { tabRouter.seleccion = .perfil }
                    .font(.footnote.weight(.semibold))
            }
        }
        .padding(14)
        .tarjetaVidrio(esquina: 16)
    }

    private func burbujaTexto(titulo: String, texto: String, alineacionDerecha: Bool) -> some View {
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
    }

    @ViewBuilder
    private var textoDeEstado: some View {
        switch viewModel.estado {
        case .inactivo:
            Text(viewModel.ultimaTranscripcion == nil ? "Toca y mantén para empezar" : "Listo para otra pregunta")
                .font(.subheadline)
                .foregroundStyle(.secondary)
        case .grabando:
            Text("Escuchando…").font(.subheadline.weight(.semibold)).foregroundStyle(.red)
        case .transcribiendo:
            HStack(spacing: 8) { ProgressView(); Text("Transcribiendo…") }
                .font(.subheadline).foregroundStyle(.secondary)
        case .procesando:
            HStack(spacing: 8) {
                ProgressView()
                Text(viewModel.chat.herramientaActiva.map { "Usando \($0)…" } ?? "Edecán está pensando…")
            }
            .font(.subheadline).foregroundStyle(.secondary)
        case .reproduciendo:
            HStack(spacing: 8) { Image(systemName: "speaker.wave.2.fill"); Text("Respondiendo…") }
                .font(.subheadline.weight(.semibold)).foregroundStyle(EdecanTheme.morado)
        }
    }

    private var botonMicrofono: some View {
        ZStack {
            Circle()
                .fill(colorBoton)
                .frame(width: 96, height: 96)
                .shadow(color: colorBoton.opacity(0.5), radius: viewModel.estado == .grabando ? 18 : 6)
            if viewModel.estado == .transcribiendo || viewModel.estado == .procesando {
                ProgressView().tint(.white)
            } else {
                Image(systemName: viewModel.estado == .reproduciendo ? "speaker.wave.2.fill" : "mic.fill")
                    .font(.system(size: 34))
                    .foregroundStyle(.white)
            }
        }
        .scaleEffect(viewModel.estado == .grabando ? 1.1 : 1.0)
        .animation(.spring(response: 0.25), value: viewModel.estado)
        .contentShape(Circle())
        .gesture(gestoDePresionar)
        .disabled(botonDeshabilitado)
        .opacity(botonDeshabilitado ? 0.5 : 1)
    }

    private var gestoDePresionar: some Gesture {
        DragGesture(minimumDistance: 0)
            .onChanged { _ in
                guard !presionando else { return }
                presionando = true
                Task { await viewModel.alPresionar() }
            }
            .onEnded { valor in
                guard presionando else { return }
                presionando = false
                // Si el dedo se deslizó lejos del botón antes de soltar, se
                // interpreta como "cancelar" (mismo gesto que WhatsApp/iMessage
                // para cancelar un audio) en vez de enviar la grabación.
                if abs(valor.translation.height) > 80 || abs(valor.translation.width) > 80 {
                    viewModel.cancelarGrabacion()
                } else {
                    Task { await viewModel.alSoltar(client: session.client) }
                }
            }
    }

    private var botonDeshabilitado: Bool {
        switch viewModel.estado {
        case .inactivo, .grabando: return false
        case .transcribiendo, .procesando, .reproduciendo: return true
        }
    }

    private var colorBoton: Color {
        switch viewModel.estado {
        case .grabando: return .red
        case .reproduciendo: return EdecanTheme.morado
        default: return EdecanTheme.azul
        }
    }

    private func resolverConfirmacion(aprobado: Bool) {
        guard let client = session.client else {
            viewModel.errorMensaje = "No hay sesión activa."
            return
        }
        Task { await viewModel.resolverConfirmacion(aprobado: aprobado, client: client) }
    }
}
