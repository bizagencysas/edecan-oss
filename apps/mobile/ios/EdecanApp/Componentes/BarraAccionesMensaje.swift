import AVFoundation
import EdecanKit
import SwiftUI
import UIKit

/// Barra compacta de acciones bajo cada burbuja de Edecán (asistente):
/// copiar, escuchar (TTS ElevenLabs via backend, fallback a dispositivo) y
/// compartir. Solo se renderiza para mensajes del asistente; el llamador
/// decide si la muestra. Sin tarjeta de fondo: son solo los iconos flotando
/// debajo de la burbuja, alineados a la izquierda como los mensajes de Edecán.
struct BarraAccionesMensaje: View {
    let texto: String
    var client: APIClient?
    /// `true` solo en la ultima respuesta del asistente del hilo: ahi se
    /// muestra el boton de regenerar. En las demas burbujas no aparece, para
    /// que no se ofrezca "regenerar" una respuesta que ya tiene otra debajo.
    var esUltima: Bool = false
    /// Tocar el boton de regenerar. `nil` cuando no corresponde mostrarlo
    /// (mensaje historico sin turno reenviable, etc.).
    var onRegenerar: (() -> Void)? = nil
    var pinned: Bool = false
    var bookmarked: Bool = false
    var onTogglePin: (() -> Void)? = nil
    var onToggleBookmark: (() -> Void)? = nil
    var onReply: (() -> Void)? = nil

    @State private var mostrarCopiado = false
    @State private var mostrarShareSheet = false
    @State private var mostrarMas = false
    @StateObject private var voz = ReproductorVoz()

    /// Texto sin speech tags ni efectos — para copiar y compartir.
    /// El original (con tags) se manda a Escuchar.
    private var textoSinTags: String { SpeechTags.ocultar(texto) }

    var body: some View {
        HStack(spacing: 14) {
            botonCopiar
            botonEscuchar
            if hayAccionesExtra {
                if mostrarMas {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 14) {
                            accionesExtra
                        }
                    }
                    .frame(maxWidth: 168)
                }
                botonMas
            }
        }
        .fixedSize(horizontal: true, vertical: true)
        .font(.system(size: 15))
        .foregroundStyle(.secondary)
        .overlay(alignment: .topLeading) {
            if mostrarCopiado {
                Text("Copiado")
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(.ultraThinMaterial, in: Capsule())
                    .offset(y: -18)
                    .transition(.opacity)
                    .accessibilityHidden(true)
            }
        }
        .sheet(isPresented: $mostrarShareSheet) {
            HojaCompartir(items: [textoSinTags])
                .presentationDetents([.medium])
        }
    }

    private var botonCopiar: some View {
        Button {
            hapticSuccess()
            UIPasteboard.general.string = textoSinTags
            withAnimation(.easeInOut(duration: 0.18)) { mostrarCopiado = true }
            Task {
                try? await Task.sleep(nanoseconds: 1_400_000_000)
                await MainActor.run {
                    withAnimation(.easeInOut(duration: 0.25)) { mostrarCopiado = false }
                }
            }
        } label: {
            Image(systemName: "doc.on.doc")
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Copiar mensaje")
    }

    private var botonEscuchar: some View {
        Button {
            hapticSuccess()
            voz.alternar(texto: textoSinTags, client: client)
        } label: {
            Image(systemName: voz.hablando ? "stop.fill" : "speaker.wave.2")
        }
        .buttonStyle(.plain)
        .accessibilityLabel(voz.hablando ? "Detener lectura" : "Escuchar mensaje")
    }

    private var botonCompartir: some View {
        Button {
            hapticSuccess()
            mostrarShareSheet = true
        } label: {
            Image(systemName: "square.and.arrow.up")
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Compartir mensaje")
    }

    private var hayAccionesExtra: Bool {
        true // compartir siempre; regenerar/responder/fijar/guardar se suman al abrir >>
    }

    @ViewBuilder
    private var accionesExtra: some View {
        botonCompartir
        if esUltima, let onRegenerar {
            botonRegenerar(onRegenerar)
        }
        if let onReply {
            Button {
                Haptico.ligero()
                onReply()
            } label: {
                Image(systemName: "arrowshape.turn.up.left")
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Responder")
        }
        if let onTogglePin {
            Button {
                Haptico.ligero()
                onTogglePin()
            } label: {
                Image(systemName: pinned ? "pin.fill" : "pin")
            }
            .buttonStyle(.plain)
            .accessibilityLabel(pinned ? "Quitar fijo" : "Fijar")
        }
        if let onToggleBookmark {
            Button {
                Haptico.ligero()
                onToggleBookmark()
            } label: {
                Image(systemName: bookmarked ? "bookmark.fill" : "bookmark")
            }
            .buttonStyle(.plain)
            .accessibilityLabel(bookmarked ? "Quitar guardado" : "Guardar")
        }
    }

    private var botonMas: some View {
        Button {
            Haptico.ligero()
            withAnimation(.snappy(duration: 0.2)) { mostrarMas.toggle() }
        } label: {
            Image(systemName: mostrarMas ? "chevron.backward.2" : "chevron.forward.2")
                .font(.system(size: 13, weight: .semibold))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(mostrarMas ? "Ocultar acciones" : "Más acciones")
        .accessibilityHint("Muestra compartir, responder, fijar y guardar")
    }

    private func botonRegenerar(_ accion: @escaping () -> Void) -> some View {
        Button {
            Haptico.ligero()
            accion()
        } label: {
            Image(systemName: "arrow.clockwise")
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Regenerar respuesta")
        .accessibilityHint("Vuelve a generar esta respuesta")
    }

    /// Quita el Markdown que no se debe oír: los bloques de código cercados se
    /// leen como "código", los enlaces se quedan con su texto visible y se
    /// borran las URLs sueltas. Sin esto el sintetizador deletrea corchetes,
    /// paréntesis y URLs enteras.
    private var textoSanitizado: String {
        var s = texto
        // Bloques cercados: ```…``` -> "código".
        let bloqueCodigo = try? NSRegularExpression(
            pattern: "```[\\s\\S]*?```",
            options: []
        )
        s = bloqueCodigo?.stringByReplacingMatches(
            in: s, range: NSRange(location: 0, length: s.utf16.count),
            withTemplate: "código"
        ) ?? s
        // Enlaces Markdown: [texto](url) -> texto.
        let enlaces = try? NSRegularExpression(
            pattern: "\\[([^\\]]*)\\]\\([^)]*\\)",
            options: []
        )
        s = enlaces?.stringByReplacingMatches(
            in: s, range: NSRange(location: 0, length: s.utf16.count),
            withTemplate: "$1"
        ) ?? s
        // URLs sueltas que hayan quedado.
        let urls = try? NSRegularExpression(
            pattern: "https?://[^\\s)]+",
            options: []
        )
        s = urls?.stringByReplacingMatches(
            in: s, range: NSRange(location: 0, length: s.utf16.count),
            withTemplate: ""
        ) ?? s
        // Negritas/cursivas/tachado: **x**, *x*, _x_, ~~x~~ -> x.
        for patron in ["\\*\\*(.+?)\\*\\*", "\\*(.+?)\\*", "_(.+?)_", "~~(.+?)~~"] {
            let re = try? NSRegularExpression(pattern: patron, options: [])
            s = re?.stringByReplacingMatches(
                in: s, range: NSRange(location: 0, length: s.utf16.count),
                withTemplate: "$1"
            ) ?? s
        }
        // Código inline: `x` -> x.
        let codigoInline = try? NSRegularExpression(pattern: "`([^`]*)`", options: [])
        s = codigoInline?.stringByReplacingMatches(
            in: s, range: NSRange(location: 0, length: s.utf16.count),
            withTemplate: "$1"
        ) ?? s
        return s
    }

    private func hapticSuccess() {
        let g = UINotificationFeedbackGenerator()
        g.prepare()
        g.notificationOccurred(.success)
    }
}

/// Reproductor de voz que pide `POST /v1/voice/speak/stream` con `eleven_turbo_v2_5`
/// y cae a `AVSpeechSynthesizer` si no hay cliente o si el stream falla.
///
/// Configura `AVAudioSession` a `.playback` antes de reproducir para que el
/// modo silencio del iPhone NO bloquee el audio.
@MainActor
private final class ReproductorVoz: NSObject, ObservableObject, AVSpeechSynthesizerDelegate {
    @Published var hablando = false
    private let sintetizador = AVSpeechSynthesizer()
    private let streamPlayer = ReproductorMPEGStream()
    private var tarea: Task<Void, Never>?

    /// Voice ID de ElevenLabs para el altavoz del chat. Modelo eleven_turbo_v2_5.
    private let voiceId = "0uHpKhb0ymsdvmCtPV8y"
    private let modelId = "eleven_turbo_v2_5"

    override init() {
        super.init()
        sintetizador.delegate = self
    }

    private func configurarSesionAudio() {
        let sesion = AVAudioSession.sharedInstance()
        try? sesion.setCategory(.playback, mode: .spokenAudio, options: [.duckOthers])
        try? sesion.setActive(true, options: .notifyOthersOnDeactivation)
    }

    func alternar(texto: String, client: APIClient?) {
        if hablando {
            detener()
            return
        }
        configurarSesionAudio()
        let textoLimpio = SpeechTags.ocultar(texto).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !textoLimpio.isEmpty else { return }
        guard let client else {
            hablarLocalmente(textoLimpio)
            return
        }
        hablando = true
        tarea = Task {
            defer {
                if !Task.isCancelled { hablando = false }
            }
            do {
                let stream = try await client.hablarStream(
                    texto: textoLimpio, voiceId: voiceId, modelId: modelId
                )
                try await streamPlayer.reproducir(stream: stream)
            } catch is CancellationError {
                return
            } catch {
                hablarLocalmente(textoLimpio)
            }
        }
    }

    private func detener() {
        tarea?.cancel()
        tarea = nil
        streamPlayer.detener()
        sintetizador.stopSpeaking(at: .immediate)
        hablando = false
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    private func hablarLocalmente(_ texto: String) {
        let frase = AVSpeechUtterance(string: texto)
        frase.voice = AVSpeechSynthesisVoice(language: "es-ES")
        frase.rate = AVSpeechUtteranceDefaultSpeechRate
        sintetizador.speak(frase)
    }

    nonisolated func speechSynthesizer(_ s: AVSpeechSynthesizer, didStart u: AVSpeechUtterance) {
        Task { @MainActor in hablando = true }
    }

    nonisolated func speechSynthesizer(_ s: AVSpeechSynthesizer, didFinish u: AVSpeechUtterance) {
        Task { @MainActor in hablando = false }
    }

    nonisolated func speechSynthesizer(_ s: AVSpeechSynthesizer, didCancel u: AVSpeechUtterance) {
        Task { @MainActor in hablando = false }
    }
}

/// `UIActivityViewController` envuelto para usarlo dentro de un `.sheet` de
/// SwiftUI.
private struct HojaCompartir: UIViewControllerRepresentable {
    let items: [Any]

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }

    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}
