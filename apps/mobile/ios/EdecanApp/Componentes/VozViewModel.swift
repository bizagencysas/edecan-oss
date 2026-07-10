import Foundation
import Observation
import AVFoundation
import EdecanKit

/// Orquesta el flujo completo de *push-to-talk* de ``VozView``: grabar
/// (``VozRecorder``) → `POST /v1/voice/transcribe` → mandar el texto por **el
/// mismo `ChatViewModel`** que usa la pestaña Chat (reutiliza su lógica de
/// conversación/SSE tal cual, en vez de reimplementar el turno del agente
/// aquí) → `POST /v1/voice/speak` con la respuesta del asistente →
/// reproducir con `AVAudioPlayer`.
@MainActor
@Observable
final class VozViewModel {
    enum Estado: Equatable {
        case inactivo
        case grabando
        case transcribiendo
        /// Esperando la respuesta del agente (el turno de chat completo,
        /// incluyendo herramientas) — delega en `chat.enviando`.
        case procesando
        case reproduciendo
    }

    /// El mismo `ChatViewModel` que ``ChatView`` — la conversación de voz
    /// vive aparte de la de texto (cada pestaña crea la suya), pero corre
    /// exactamente el mismo código de turno/confirmación/SSE.
    let chat = ChatViewModel()

    private let recorder = VozRecorder()
    private var reproductor: AVAudioPlayer?

    private(set) var estado: Estado = .inactivo
    var errorMensaje: String?
    /// Último texto transcrito del usuario — se muestra en pantalla mientras
    /// se procesa, para que la persona vea qué entendió Edecán.
    private(set) var ultimaTranscripcion: String?
    /// `true` si la última respuesta de voz vino del `StubTTS` offline (el
    /// tenant no conectó una credencial de voz propia) — `VozView` muestra un
    /// aviso con acceso directo a Perfil en vez de dejar sonar un beep sin
    /// explicación (`apps/api/edecan_api/routers/voice.py`: cae a stub,
    /// nunca reutiliza una credencial de plataforma compartida).
    private(set) var usandoVozDePrueba = false

    /// Error a mostrar en pantalla — mezcla los propios de esta clase
    /// (permiso de micrófono, transcripción, reproducción) con los del
    /// `ChatViewModel` reutilizado (fallos del turno/SSE, p. ej. "El
    /// proveedor LLM no respondió a tiempo"): sin esto, un error que ocurre
    /// DENTRO de `chat.enviar`/`chat.resolverConfirmacion` quedaría solo en
    /// `chat.errorMensaje` y `VozView` nunca lo mostraría.
    var errorParaMostrar: String? { errorMensaje ?? chat.errorMensaje }

    /// Botón presionado: pide permiso (si hace falta) y arranca a grabar.
    func alPresionar() async {
        guard estado == .inactivo else { return }
        errorMensaje = nil
        let permitido = await recorder.solicitarPermiso()
        guard permitido else {
            errorMensaje = VozRecorder.RecorderError.permisoDenegado.errorDescription
            return
        }
        do {
            try recorder.iniciar()
            estado = .grabando
        } catch {
            errorMensaje = error.localizedDescription
        }
    }

    /// Botón soltado: detiene la grabación y corre transcripción → chat →
    /// voz de vuelta.
    func alSoltar(client: APIClient?) async {
        guard estado == .grabando else { return }
        guard let client else {
            recorder.cancelar()
            estado = .inactivo
            errorMensaje = "No hay sesión activa."
            return
        }

        do {
            let audio = try recorder.detener()
            estado = .transcribiendo
            let texto = try await client.transcribir(audioData: audio, mimeType: "audio/wav", language: nil)
            let textoLimpio = texto.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !textoLimpio.isEmpty else {
                estado = .inactivo
                errorMensaje = "No se entendió nada — intenta de nuevo, más cerca del micrófono."
                return
            }
            ultimaTranscripcion = textoLimpio

            estado = .procesando
            await chat.enviar(texto: textoLimpio, client: client)
            await hablarRespuestaSiHay(client: client)
        } catch {
            errorMensaje = error.localizedDescription
            estado = .inactivo
        }
    }

    /// El usuario deslizó fuera del botón para cancelar sin enviar nada.
    func cancelarGrabacion() {
        recorder.cancelar()
        estado = .inactivo
    }

    /// Aprobar/rechazar una herramienta pendiente (``TarjetaConfirmacion``,
    /// compartida con ``ChatView``) — reproduce la respuesta final igual que
    /// un turno normal.
    func resolverConfirmacion(aprobado: Bool, client: APIClient) async {
        estado = .procesando
        await chat.resolverConfirmacion(aprobado: aprobado, client: client)
        await hablarRespuestaSiHay(client: client)
    }

    private func hablarRespuestaSiHay(client: APIClient) async {
        // Si el turno se detuvo pidiendo confirmación, `VozView` ya muestra
        // la tarjeta correspondiente — no hay todavía una respuesta final
        // que leer en voz alta.
        guard chat.confirmacionPendiente == nil,
              let respuesta = chat.ultimaRespuestaDelAsistente,
              !respuesta.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        else {
            estado = .inactivo
            return
        }
        await reproducir(texto: respuesta, client: client)
    }

    private func reproducir(texto: String, client: APIClient) async {
        estado = .reproduciendo
        do {
            let resultado = try await client.hablar(texto: texto, voiceId: nil)
            usandoVozDePrueba = resultado.contentType.localizedCaseInsensitiveContains("wav")
            let player = try AVAudioPlayer(data: resultado.audio)
            reproductor = player
            player.play()
            while player.isPlaying {
                try await Task.sleep(nanoseconds: 150_000_000)
            }
        } catch {
            errorMensaje = error.localizedDescription
        }
        reproductor = nil
        estado = .inactivo
    }
}
