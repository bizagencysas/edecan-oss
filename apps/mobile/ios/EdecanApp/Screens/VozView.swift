import EdecanKit
import SwiftUI

/// Push-to-talk dentro del mismo hilo que el chat. Proveedor conectado cuando
/// existe; voz privada del dispositivo cuando no hay credenciales.
struct VozView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(SessionStore.self) private var session
    @State private var viewModel: VozViewModel
    @State private var presionando = false

    init(chat: ChatViewModel) {
        _viewModel = State(initialValue: VozViewModel(chat: chat))
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 24) {
                    if viewModel.usandoVozDelDispositivo {
                        Label("Voz privada del dispositivo", systemImage: "iphone.gen3")
                            .font(.caption.weight(.medium))
                            .foregroundStyle(.secondary)
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
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { Button("Cerrar") { dismiss() } }
            }
            .onDisappear {
                presionando = false
                viewModel.cancelarGrabacion()
            }
        }
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
                .font(.subheadline).foregroundStyle(.secondary)
        case .preparando:
            HStack(spacing: 8) { ProgressView(); Text("Preparando micrófono…") }
                .font(.subheadline).foregroundStyle(.secondary)
        case .grabando:
            Text("Escuchando…").font(.subheadline.weight(.semibold)).foregroundStyle(.red)
        case .transcribiendo:
            HStack(spacing: 8) { ProgressView(); Text("Transcribiendo…") }
                .font(.subheadline).foregroundStyle(.secondary)
        case .procesando:
            HStack(spacing: 8) {
                ProgressView()
                Text(viewModel.chat.herramientaActiva.map { "Usando \($0.nombre)…" } ?? "Edecán está pensando…")
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
            if viewModel.estado == .preparando || viewModel.estado == .transcribiendo || viewModel.estado == .procesando {
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
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(viewModel.estado == .grabando ? "Suelta para enviar" : "Mantén presionado para hablar")
        .accessibilityAddTraits(.isButton)
    }

    private var gestoDePresionar: some Gesture {
        DragGesture(minimumDistance: 0)
            .onChanged { _ in
                guard !presionando else { return }
                presionando = true
                viewModel.alPresionar()
            }
            .onEnded { valor in
                guard presionando else { return }
                presionando = false
                if abs(valor.translation.height) > 80 || abs(valor.translation.width) > 80 {
                    viewModel.cancelarGrabacion()
                } else {
                    viewModel.alSoltar(client: session.client)
                }
            }
    }

    private var botonDeshabilitado: Bool {
        switch viewModel.estado {
        case .inactivo, .preparando, .grabando: false
        case .transcribiendo, .procesando, .reproduciendo: true
        }
    }

    private var colorBoton: Color {
        switch viewModel.estado {
        case .grabando: .red
        case .reproduciendo: EdecanTheme.morado
        default: EdecanTheme.azul
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
