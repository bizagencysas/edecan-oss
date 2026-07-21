import Foundation
import Observation
import EdecanKit

/// Estado y lógica de una conversación de ``ChatView`` (y, reutilizada tal
/// cual, de ``VozView`` — ver `VozViewModel`). Arma a mano la petición SSE de
/// `POST /v1/conversations/{id}/messages` (`docs/api.md`) usando
/// `APIClient.urlCompleta`/`tokenDeAccesoValido` (la autenticación) +
/// `SSEClient` (el framing del stream) — exactamente la división de
/// responsabilidades que documenta `EdecanKit/Sources/EdecanKit/SSEClient.swift`.
@MainActor
@Observable
final class ChatViewModel {
    /// Un mensaje en pantalla. `id` estable para `ForEach`/`ScrollViewReader`.
    struct Mensaje: Identifiable, Equatable {
        enum Rol: Equatable { case usuario, asistente }
        let id = UUID()
        var rol: Rol
        var texto: String
        var enProgreso: Bool = false
        var artefactos: [ArtifactRef] = []
    }

    /// Una confirmación de herramienta `dangerous` pendiente de aprobar o
    /// rechazar (evento SSE `confirmation_required`,
    /// `POST /v1/conversations/{id}/confirm` — `ARCHITECTURE.md` §10.7/§10.12).
    /// `nil` si no hay ninguna en curso. `ChatView`/`VozView` la muestran como
    /// una tarjeta inline (``TarjetaConfirmacion``) con botones Aprobar/Rechazar.
    struct ConfirmacionPendiente: Identifiable, Equatable {
        var id: String { toolCallId }
        let toolCallId: String
        let nombre: String
        let args: [String: JSONValue]
        /// Índice en `mensajes` del mensaje del asistente al que pertenece
        /// esta confirmación — para seguir apendeando texto ahí mismo cuando
        /// se resuelva (aprobada o rechazada).
        let indiceMensaje: Int
    }

    private(set) var mensajes: [Mensaje] = []
    private(set) var conversacionId: String?
    /// Nombre de la herramienta que el agente está usando ahora mismo
    /// (evento `tool_start` sin su `tool_end` correspondiente todavía),
    /// o `nil` si no hay ninguna en curso — se muestra como un banner corto
    /// encima del campo de texto.
    private(set) var herramientaActiva: String?
    private(set) var enviando = false
    private(set) var confirmacionPendiente: ConfirmacionPendiente?
    var errorMensaje: String?

    private let sseClient = SSEClient()

    /// `POST /v1/conversations` una sola vez por instancia — las siguientes
    /// llamadas a `enviar` reutilizan `conversacionId`.
    private func asegurarConversacion(client: APIClient) async {
        guard conversacionId == nil else { return }
        do {
            let conversacion = try await client.crearConversacion(titulo: nil)
            conversacionId = conversacion.id
        } catch {
            errorMensaje = error.localizedDescription
        }
    }

    /// Texto plano del último mensaje del asistente ya completo — lo usa
    /// `VozViewModel` para mandarlo a `POST /v1/voice/speak` tras un turno.
    var ultimaRespuestaDelAsistente: String? {
        mensajes.last(where: { $0.rol == .asistente })?.texto
    }

    func enviar(texto: String, client: APIClient) async {
        let textoLimpio = texto.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !textoLimpio.isEmpty, !enviando, confirmacionPendiente == nil else { return }

        await asegurarConversacion(client: client)
        guard let conversacionId else { return }

        mensajes.append(Mensaje(rol: .usuario, texto: textoLimpio))
        let indiceRespuesta = mensajes.count
        mensajes.append(Mensaje(rol: .asistente, texto: "", enProgreso: true))

        enviando = true
        errorMensaje = nil
        herramientaActiva = nil
        defer {
            enviando = false
            herramientaActiva = nil
            if mensajes.indices.contains(indiceRespuesta) {
                mensajes[indiceRespuesta].enProgreso = false
            }
        }

        do {
            struct Body: Encodable { let text: String }
            try await consumirStreamConRefresh(
                client: client,
                path: "/v1/conversations/\(conversacionId)/messages",
                body: Body(text: textoLimpio),
                indiceRespuesta: indiceRespuesta
            )
        } catch {
            errorMensaje = error.localizedDescription
        }
    }

    /// Aprueba (`aprobado: true`) o rechaza (`false`) la herramienta que
    /// quedó pendiente en ``confirmacionPendiente`` — `POST
    /// /v1/conversations/{id}/confirm`. Si se aprueba, el servidor la
    /// ejecuta DIRECTO (nunca vuelve a llamar al LLM, ver
    /// `apps/api/edecan_api/routers/conversations.py`); si se rechaza, el
    /// stream cierra con un mensaje corto sin ejecutar nada — ambos casos
    /// llegan aquí como los mismos eventos SSE de siempre.
    func resolverConfirmacion(aprobado: Bool, client: APIClient) async {
        guard let pendiente = confirmacionPendiente, let conversacionId else { return }
        confirmacionPendiente = nil
        let indiceRespuesta = pendiente.indiceMensaje

        enviando = true
        errorMensaje = nil
        defer {
            enviando = false
            herramientaActiva = nil
            if mensajes.indices.contains(indiceRespuesta) {
                mensajes[indiceRespuesta].enProgreso = false
            }
        }

        do {
            struct Body: Encodable {
                let toolCallId: String
                let approved: Bool
                enum CodingKeys: String, CodingKey {
                    case toolCallId = "tool_call_id"
                    case approved
                }
            }
            try await consumirStreamConRefresh(
                client: client,
                path: "/v1/conversations/\(conversacionId)/confirm",
                body: Body(toolCallId: pendiente.toolCallId, approved: aprobado),
                indiceRespuesta: indiceRespuesta
            )
        } catch {
            errorMensaje = error.localizedDescription
        }
    }

    private func construirPeticionSSE<Cuerpo: Encodable>(
        client: APIClient, path: String, body: Cuerpo
    ) async throws -> URLRequest {
        let url = try await client.urlCompleta(path)
        let token = try await client.tokenDeAccesoValido()
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.httpBody = try JSONEncoder().encode(body)
        return request
    }

    /// SSE no atraviesa `APIClient.conAutoRefresh`. Si el servidor rechaza
    /// la conexión inicial con 401, rota el refresh token una sola vez y
    /// reconstruye la petición completa con el nuevo access token.
    private func consumirStreamConRefresh<Cuerpo: Encodable>(
        client: APIClient,
        path: String,
        body: Cuerpo,
        indiceRespuesta: Int
    ) async throws {
        var yaRefresco = false
        while true {
            let request = try await construirPeticionSSE(client: client, path: path, body: body)
            do {
                for try await evento in sseClient.stream(request) {
                    aplicar(evento, indiceRespuesta: indiceRespuesta)
                }
                return
            } catch SSEClient.SSEError.servidor(let status) where status == 401 && !yaRefresco {
                yaRefresco = true
                try await client.refrescar()
            }
        }
    }

    private func aplicar(_ evento: ChatEvent, indiceRespuesta: Int) {
        guard mensajes.indices.contains(indiceRespuesta) else { return }
        switch evento {
        case .textDelta(let texto):
            mensajes[indiceRespuesta].texto += texto
        case .toolStart(let nombre, _):
            herramientaActiva = nombre
        case .toolEnd(_, _, let artefactos):
            herramientaActiva = nil
            for artefacto in artefactos
            where !mensajes[indiceRespuesta].artefactos.contains(where: { $0.fileId == artefacto.fileId }) {
                mensajes[indiceRespuesta].artefactos.append(artefacto)
            }
        case .confirmationRequired(let toolCallId, let nombre, let args):
            herramientaActiva = nil
            confirmacionPendiente = ConfirmacionPendiente(
                toolCallId: toolCallId, nombre: nombre, args: args, indiceMensaje: indiceRespuesta
            )
        case .done:
            break
        case .error(let mensaje):
            errorMensaje = mensaje
        }
    }
}
