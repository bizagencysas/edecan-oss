import AVFoundation
import EdecanKit
import SwiftUI
import UIKit

/// Push-to-talk dentro del mismo hilo que el chat. Proveedor conectado cuando
/// existe; voz privada del dispositivo cuando no hay credenciales.
struct VozView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(SessionStore.self) private var session
    @State private var viewModel: VozViewModel
    @State private var camara = LiveCameraCapture()
    @State private var presionando = false
    @State private var camaraActiva = false
    @AppStorage("vozVelocidad") private var velocidadGuardada: Double = 1.0

    init(chat: ChatViewModel) {
        _viewModel = State(initialValue: VozViewModel(chat: chat))
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 24) {
                    Label(viewModel.chat.tituloConversacionActual, systemImage: "bubble.left.and.bubble.right.fill")
                        .font(.caption.weight(.medium))
                        .foregroundStyle(.secondary)

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
                    selectorVelocidad
                    Toggle(isOn: $camaraActiva) {
                        Label("Cámara en vivo", systemImage: "camera.viewfinder")
                    }
                    .onChange(of: camaraActiva) { _, activa in
                        if activa {
                            Task {
                                await camara.iniciar()
                                viewModel.actualizarFrameCamara(camara.ultimoFrameJPEG)
                            }
                        } else {
                            camara.detener()
                            viewModel.actualizarFrameCamara(nil)
                        }
                    }
                    if camaraActiva {
                        LiveCameraPreview(session: camara.session)
                            .frame(height: 180)
                            .clipShape(RoundedRectangle(cornerRadius: 16))
                            .onChange(of: camara.ultimoFrameJPEG) { _, frame in
                                viewModel.actualizarFrameCamara(frame)
                            }
                        if let error = camara.errorMensaje {
                            Text(error).font(.footnote).foregroundStyle(.red)
                        }
                    }
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
            .onAppear {
                viewModel.velocidad = Float(velocidadGuardada)
            }
            .onChange(of: velocidadGuardada) { _, nuevo in
                viewModel.velocidad = Float(nuevo)
            }
            .onDisappear {
                presionando = false
                viewModel.cancelarGrabacion()
                camara.detener()
                camaraActiva = false
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
        case .transcribiendo, .procesando: true
        case .reproduciendo: false
        }
    }

    private var colorBoton: Color {
        switch viewModel.estado {
        case .grabando: .red
        case .reproduciendo: EdecanTheme.morado
        default: EdecanTheme.azul
        }
    }

    private struct OpcionVelocidad {
        let valor: Double
        let etiqueta: String
    }

    private static let opcionesVelocidad: [OpcionVelocidad] = [
        OpcionVelocidad(valor: 0.75, etiqueta: "0,75×"),
        OpcionVelocidad(valor: 1.0, etiqueta: "1×"),
        OpcionVelocidad(valor: 1.25, etiqueta: "1,25×"),
        OpcionVelocidad(valor: 1.5, etiqueta: "1,5×"),
        OpcionVelocidad(valor: 2.0, etiqueta: "2×"),
    ]

    private var selectorVelocidad: some View {
        Menu {
            Picker("Velocidad", selection: $velocidadGuardada) {
                ForEach(Self.opcionesVelocidad, id: \.valor) { opcion in
                    Text(opcion.etiqueta).tag(opcion.valor)
                }
            }
        } label: {
            Label(etiquetaVelocidad(velocidadGuardada), systemImage: "speedometer")
                .font(.caption.weight(.medium))
                .foregroundStyle(EdecanTheme.morado)
                .padding(.horizontal, 12)
                .padding(.vertical, 6)
                .tarjetaVidrio(esquina: 12)
        }
    }

    private func etiquetaVelocidad(_ valor: Double) -> String {
        Self.opcionesVelocidad.first { $0.valor == valor }?.etiqueta ?? "1×"
    }

    private struct LiveCameraPreview: UIViewRepresentable {
        let session: AVCaptureSession

        func makeUIView(context: Context) -> PreviewView {
            let view = PreviewView()
            view.previewLayer.session = session
            view.previewLayer.videoGravity = .resizeAspectFill
            return view
        }

        func updateUIView(_ uiView: PreviewView, context: Context) {
            uiView.previewLayer.session = session
        }
    }

    private final class PreviewView: UIView {
        override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
        var previewLayer: AVCaptureVideoPreviewLayer {
            layer as! AVCaptureVideoPreviewLayer
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
