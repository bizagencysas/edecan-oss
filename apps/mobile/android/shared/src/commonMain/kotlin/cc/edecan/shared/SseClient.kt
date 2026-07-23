package cc.edecan.shared

import io.ktor.client.HttpClient
import io.ktor.client.request.headers
import io.ktor.client.request.prepareGet
import io.ktor.client.request.preparePost
import io.ktor.client.request.setBody
import io.ktor.client.statement.HttpResponse
import io.ktor.client.statement.bodyAsChannel
import io.ktor.http.ContentType
import io.ktor.http.HttpHeaders
import io.ktor.http.contentType
import io.ktor.utils.io.ByteReadChannel
import io.ktor.utils.io.readLine
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.serialization.json.Json

/**
 * Cliente de Server-Sent Events para el turno del agente
 * (`POST /v1/conversations/{id}/messages` y `POST .../confirm`, ambos
 * `text/event-stream` — `docs/api.md` §"Conversaciones y chat (SSE)") — el
 * equivalente Kotlin de `SSEClient.swift` en `EdecanKit` (iOS).
 *
 * Usa `HttpClient.preparePost` + `bodyAsChannel()` de Ktor para leer el
 * stream línea por línea a medida que llega, en vez de esperar la
 * respuesta completa — así `ChatScreen` puede ir mostrando el texto según
 * llega, no todo de golpe al final. La nota "Ktor prepareGet" del work
 * package que originó este archivo es shorthand genérico por la familia
 * `prepare*`/`HttpStatement` de Ktor: el endpoint real es **POST** con
 * cuerpo JSON (`docs/api.md`), así que este cliente usa [preparePost], no
 * `prepareGet` — usar GET aquí sería un bug real contra el backend.
 *
 * Este cliente SOLO entiende el framing de SSE (`event:` / `data:` /
 * líneas en blanco que cierran un bloque, más el sentinel `[DONE]` de
 * estilo OpenAI por si algún día hay un proxy/relay compatible delante —
 * el backend propio de Edecán hoy simplemente cierra la conexión tras
 * `message.done`, sin mandar ese sentinel). No sabe nada de autenticación
 * ni de la forma de la URL — quien llama arma el `accessToken`/`url`/
 * `bodyJson` completos (ver `EdecanApi.tokenDeAccesoValido()` /
 * `EdecanApi.urlCompleta(_:)`, usados desde `vm/ChatViewModel.kt`).
 */
class SseClient(private val json: Json = edecanJson) {

    /** Errores tipados en español, mismo criterio que [ApiException] —
     * listos para pintar tal cual en `ChatScreen`. */
    sealed class SseException(message: String) : Exception(message) {
        class RespuestaInvalida :
            SseException("El servidor envió una respuesta que no se pudo interpretar.")

        class Servidor(val status: Int, val retryAfterSeconds: Long? = null) :
            SseException("El servidor rechazó la conexión de chat ($status).")

        class IntentoEnCurso(val retryAfterSeconds: Long = 1) :
            SseException("Edecán sigue trabajando.")

        class EventoInvalido(detalle: String) :
            SseException("Edecán envió un evento de chat que no se pudo leer: $detalle")

        class Conexion(detalle: String) :
            SseException("Se perdió la conexión con Edecán: $detalle")
    }

    /**
     * Abre `POST url` (Content-Type `application/json`, Accept
     * `text/event-stream`, `Authorization: Bearer accessToken`, cuerpo
     * `bodyJson` ya serializado) y emite un [ChatEvent] por cada bloque
     * `data:` completo. El stream termina normalmente cuando el servidor
     * cierra la conexión (después de `message.done`) o con un
     * [SseException] si la conexión falla o un evento no se puede
     * decodificar.
     */
    fun stream(
        client: HttpClient,
        url: String,
        accessToken: String,
        bodyJson: String,
        idempotencyKey: String? = null,
    ): Flow<ChatEvent> = flow {
        try {
            client.preparePost(url) {
                headers {
                    append(HttpHeaders.Authorization, "Bearer $accessToken")
                    append(HttpHeaders.Accept, "text/event-stream")
                    idempotencyKey?.let { append("Idempotency-Key", it) }
                }
                contentType(ContentType.Application.Json)
                setBody(bodyJson)
            }.execute { response ->
                validarRespuesta(response)
                leerBloques(response.bodyAsChannel()) { evento -> emit(evento) }
            }
        } catch (e: SseException) {
            throw e
        } catch (e: Exception) {
            throw SseException.Conexion(e.message ?: e::class.simpleName ?: "error desconocido")
        }
    }

    /**
     * Recupera un turno idempotente que ya fue aceptado por el servidor.
     *
     * Este GET no vuelve a enviar el texto del usuario. Por eso Android puede
     * persistir únicamente la UUID del intento y reanudar después de que el
     * sistema mate el proceso, sin guardar mensajes ni credenciales en un
     * Bundle. El servidor responde 202 mientras el agente sigue trabajando y
     * 200 con el replay SSE exacto cuando termina.
     */
    fun resume(
        client: HttpClient,
        url: String,
        accessToken: String,
    ): Flow<ChatEvent> = flow {
        try {
            client.prepareGet(url) {
                headers {
                    append(HttpHeaders.Authorization, "Bearer $accessToken")
                    append(HttpHeaders.Accept, "text/event-stream")
                }
            }.execute { response ->
                if (response.status.value == 202) {
                    throw SseException.IntentoEnCurso(response.retryAfterSeconds() ?: 1)
                }
                validarRespuesta(response)
                leerBloques(response.bodyAsChannel()) { evento -> emit(evento) }
            }
        } catch (e: SseException) {
            throw e
        } catch (e: Exception) {
            throw SseException.Conexion(e.message ?: e::class.simpleName ?: "error desconocido")
        }
    }

    private fun validarRespuesta(response: HttpResponse) {
        if (response.status.value !in 200..299) {
            throw SseException.Servidor(response.status.value, response.retryAfterSeconds())
        }
    }

    private fun HttpResponse.retryAfterSeconds(): Long? =
        headers[HttpHeaders.RetryAfter]?.trim()?.toLongOrNull()?.coerceIn(1, 30)

    /** Lee `canal` línea por línea y alimenta [SseFrameParser] — el framing
     * SSE en sí (acumular `data:`, decidir cuándo un bloque cierra,
     * decodificar el payload) vive todo en esa clase, separada a propósito
     * para poder testearla con líneas de texto plano
     * (`shared/src/commonTest`) sin abrir ningún canal/conexión real. */
    private suspend fun leerBloques(canal: ByteReadChannel, despachar: suspend (ChatEvent) -> Unit) {
        val parser = SseFrameParser(json)
        val terminal = SseTerminalState()
        while (true) {
            val linea = canal.readLine() ?: break // cierre normal de conexión.
            parser.procesarLinea(linea)?.let { evento ->
                if (terminal.aceptar(evento)) despachar(evento)
            }
            if (terminal.finalizado) break
        }
        // Por si el servidor cierra el stream sin una última línea en
        // blanco tras el bloque final.
        if (!terminal.finalizado) {
            parser.finalizar()?.let { evento ->
                if (terminal.aceptar(evento)) despachar(evento)
            }
        }
        terminal.validarCierre()
    }
}

/**
 * Máquina de estados PURA del framing SSE que usa [SseClient] — acumula
 * líneas `data:` hasta que la línea en blanco que cierra el bloque llega, y
 * en ese momento decodifica el payload acumulado como [ChatEvent]. Ignora
 * comentarios/keep-alive (líneas que empiezan con `:`) y campos que este
 * cliente no necesita (`id`, `retry`).
 *
 * Separada de `SseClient.leerBloques` (que sí depende de un
 * `ByteReadChannel` real) específicamente para poder testearla en
 * `commonTest` alimentándola con líneas de texto plano, sin tocar red ni
 * coroutines — un `SseFrameParser` es de un solo uso por conexión (guarda
 * el bloque `data:`/`event:` en curso como estado mutable interno).
 */
internal class SseFrameParser(private val json: Json = edecanJson) {
    private var nombreEvento: String? = null
    private val lineasDeDatos = mutableListOf<String>()

    /** Procesa una línea del stream (sin el `\n` final). Devuelve el
     * [ChatEvent] que esa línea termina de cerrar (la línea en blanco que
     * sigue a un bloque `data:` no vacío), o `null` si la línea todavía no
     * cierra ningún bloque. Lanza [SseClient.SseException.EventoInvalido]
     * si el payload acumulado no decodifica como un [ChatEvent] válido. */
    fun procesarLinea(lineaCruda: String): ChatEvent? {
        val linea = lineaCruda.removeSuffix("\r")
        return when {
        linea.isEmpty() -> despacharBloque()
        linea.startsWith(":") -> null // comentario/keep-alive de SSE.
        else -> procesarCampo(linea)
        }
    }

    /** Cierra el bloque acumulado aunque nunca haya llegado una línea en
     * blanco final — se llama una vez cuando el canal se cierra. */
    fun finalizar(): ChatEvent? = despacharBloque()

    private fun procesarCampo(linea: String): ChatEvent? {
        val indiceDosPuntos = linea.indexOf(':')
        if (indiceDosPuntos < 0) return null
        val campo = linea.substring(0, indiceDosPuntos)
        var valor = linea.substring(indiceDosPuntos + 1)
        if (valor.startsWith(" ")) valor = valor.substring(1)
        if (campo == "event") {
            // Un relay puede perder la línea vacía entre eventos. Un nuevo
            // `event:` cierra inequívocamente el bloque anterior.
            val pendiente = despacharBloque()
            nombreEvento = valor
            return pendiente
        }
        if (campo == "data") lineasDeDatos.add(valor)
        return null
    }

    private fun despacharBloque(): ChatEvent? {
        if (lineasDeDatos.isEmpty()) return null
        val payload = lineasDeDatos.joinToString("\n")
        lineasDeDatos.clear()
        val evento = nombreEvento?.trim()
        nombreEvento = null
        if (payload.trim() == "[DONE]") return ChatEvent.Done()
        return try {
            json.decodeFromString(ChatEvent.serializer(), payload)
        } catch (e: Exception) {
            if (evento == "message.done") return ChatEvent.Done()
            throw SseClient.SseException.EventoInvalido("evento '${evento ?: "?"}': ${e.message}")
        }
    }
}

/** Contrato terminal de un turno SSE: el primer `done`/`error` gana y
 * cualquier evento posterior se ignora. Un EOF sin marcador terminal es una
 * respuesta truncada, no un éxito. */
internal class SseTerminalState {
    var finalizado: Boolean = false
        private set

    fun aceptar(evento: ChatEvent): Boolean {
        if (finalizado) return false
        if (evento is ChatEvent.Done || evento is ChatEvent.ErrorEvent) finalizado = true
        return true
    }

    fun validarCierre() {
        if (!finalizado) {
            throw SseClient.SseException.Conexion("la respuesta terminó sin confirmación final")
        }
    }
}
