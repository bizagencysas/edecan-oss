package cc.edecan.shared

import kotlinx.serialization.DeserializationStrategy
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonContentPolymorphicSerializer
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonPrimitive

/**
 * Modelos serializables contra las rutas `/v1/...` (`docs/api.md`, `ARCHITECTURE.md`
 * §10.12) — el equivalente Kotlin de
 * `apps/mobile/ios/EdecanKit/Sources/EdecanKit/Models.swift`. Misma fuente
 * de verdad (la API HTTP real), dos implementaciones nativas independientes;
 * ver "Por qué no React Native" en `docs/movil-android.md`.
 */

/** Instancia [Json] compartida por [EdecanApi] y [SseClient]: tolerante a
 * campos nuevos que el backend agregue mañana (`ignoreUnknownKeys`) y a
 * campos opcionales ausentes en la respuesta (`explicitNulls = false`). */
val edecanJson: Json = Json {
    ignoreUnknownKeys = true
    explicitNulls = false
    encodeDefaults = true
}

// ---------------------------------------------------------------------------
// Autenticación
// ---------------------------------------------------------------------------

/** Respuesta de `POST /v1/auth/login`, `/register` y `/refresh`
 * (`docs/api.md` §"Autenticación y sesión"; `TokenPairOut` en el backend). */
@Serializable
data class TokenPair(
    @SerialName("access_token") val accessToken: String,
    @SerialName("refresh_token") val refreshToken: String,
    @SerialName("token_type") val tokenType: String = "bearer",
)

// ---------------------------------------------------------------------------
// Perfil (`GET /v1/me`)
// ---------------------------------------------------------------------------

@Serializable
data class UserInfo(
    val id: String,
    val email: String,
    @SerialName("is_superadmin") val isSuperadmin: Boolean = false,
    @SerialName("created_at") val createdAt: String,
)

@Serializable
data class TenantInfo(
    val id: String,
    val name: String,
    val slug: String,
    @SerialName("plan_key") val planKey: String,
    val status: String,
    @SerialName("created_at") val createdAt: String,
)

/** `GET /v1/me`. `flags` mezcla banderas booleanas (`"voice.web"`) y límites
 * numéricos (`"limits.messages_per_day"`, `-1` = ilimitado por convención del
 * backend, `ARCHITECTURE.md` §10.13) bajo el mismo diccionario — igual que
 * lo manda el servidor — por eso queda como `JsonElement` crudo en vez de un
 * tipo fijo; usa [boolFlag]/[intFlag] para leerlo. */
@Serializable
data class Me(
    val user: UserInfo,
    val tenant: TenantInfo,
    val flags: Map<String, JsonElement> = emptyMap(),
)

/** Nombre "de pila" para el saludo de Inicio (`"Hola, {nombre} 👋"`) — la API
 * no manda un nombre propio (solo `email`), así que se toma lo que hay antes
 * de la arroba, igual que hace el frontend web y `EdecanKit` en iOS. */
val Me.nombrePila: String
    get() = user.email.substringBefore('@').ifBlank { user.email }

fun Map<String, JsonElement>.boolFlag(key: String, default: Boolean = false): Boolean =
    this[key]?.jsonPrimitive?.let { runCatching { it.boolean }.getOrNull() } ?: default

fun Map<String, JsonElement>.intFlag(key: String, default: Int = 0): Int =
    this[key]?.jsonPrimitive?.let { runCatching { it.int }.getOrNull() } ?: default

// ---------------------------------------------------------------------------
// Conversaciones y mensajes
// ---------------------------------------------------------------------------

/** Un mensaje persistido (`_message_out` en
 * `apps/api/edecan_api/routers/conversations.py`). `content` llega como
 * `{"text": "..."}` (o `null` para turnos que solo ejecutaron una tool) — usa
 * [texto] en vez de leer el campo crudo. */
@Serializable
data class Message(
    val id: String,
    val role: String,
    val content: JsonElement? = null,
    @SerialName("tool_calls") val toolCalls: JsonElement? = null,
    @SerialName("tokens_in") val tokensIn: Int = 0,
    @SerialName("tokens_out") val tokensOut: Int = 0,
    @SerialName("created_at") val createdAt: String = "",
)

val Message.texto: String
    get() = (content as? JsonObject)?.get("text")?.jsonPrimitive?.contentOrNull ?: ""

/** Un elemento de `GET /v1/conversations`, la respuesta de
 * `POST /v1/conversations`, o (con `messages` poblado) `GET
 * /v1/conversations/{id}` — las tres comparten forma
 * (`_conversation_out` + `out["messages"] = ...`). `channel` se deja como
 * `String` en vez de un enum cerrado a propósito: si el backend suma un
 * canal nuevo, decodificar no debe romperse solo porque el cliente todavía
 * no lo conoce. */
@Serializable
data class Conversation(
    val id: String,
    val title: String? = null,
    val channel: String = "web",
    @SerialName("created_at") val createdAt: String = "",
    @SerialName("updated_at") val updatedAt: String? = null,
    val messages: List<Message> = emptyList(),
)

/** Body de `POST /v1/conversations/{id}/messages` (`ChatMessageIn` en
 * `edecan_schemas`, mismo nombre a propósito). */
@Serializable
data class ChatMessageIn(val text: String)

/** Body de `POST /v1/conversations/{id}/confirm` (`ConfirmIn` en
 * `edecan_api.routers.conversations`, mismo nombre a propósito). */
@Serializable
data class ConfirmIn(
    @SerialName("tool_call_id") val toolCallId: String,
    val approved: Boolean,
)

// ---------------------------------------------------------------------------
// Eventos del turno del agente (SSE) — docs/api.md §"Conversaciones y chat (SSE)"
// ---------------------------------------------------------------------------

/** Métricas de tokens del evento `message.done`. */
@Serializable
data class Usage(
    @SerialName("input_tokens") val inputTokens: Int = 0,
    @SerialName("output_tokens") val outputTokens: Int = 0,
)

/**
 * Un evento del stream SSE de `POST /v1/conversations/{id}/messages` (y de
 * `POST .../confirm`), decodificado por el campo `"type"` de cada bloque
 * `data:` — exactamente el `AgentEvent` de `ARCHITECTURE.md` §10.7 / la
 * tabla de `docs/api.md`. El nombre del `event:` de SSE (p. ej.
 * `message.delta`) es redundante con `"type"` (`text_delta`) y no hace falta
 * para decodificar; [SseClient] lo guarda solo para mensajes de error más
 * claros.
 */
@Serializable(with = ChatEventSerializer::class)
sealed interface ChatEvent {
    @Serializable
    data class TextDelta(val text: String) : ChatEvent

    @Serializable
    data class ToolStart(
        val name: String,
        val args: JsonElement = JsonObject(emptyMap()),
    ) : ChatEvent

    @Serializable
    data class ToolEnd(
        val name: String,
        @SerialName("result_preview") val resultPreview: String = "",
    ) : ChatEvent

    @Serializable
    data class ConfirmationRequired(
        @SerialName("tool_call_id") val toolCallId: String,
        val name: String,
        val args: JsonElement = JsonObject(emptyMap()),
    ) : ChatEvent

    @Serializable
    data class Done(val usage: Usage? = null) : ChatEvent

    @Serializable
    data class ErrorEvent(val message: String) : ChatEvent

    /** Cualquier `"type"` que este cliente todavía no conoce. Deserializador
     * TOLERANTE a propósito (pedido del work package): un evento SSE nuevo
     * que el backend agregue mañana no debe tumbar el stream de una app
     * móvil que todavía no se actualizó — se decodifica en esta variante
     * "ignorable" en vez de lanzar. */
    @Serializable
    data class Unknown(val type: String = "desconocido") : ChatEvent
}

/** Ver el doc de [ChatEvent]: elige el `KSerializer` concreto mirando solo el
 * campo `"type"` del JSON crudo, sin usar el mecanismo de polimorfismo con
 * discriminador de kotlinx.serialization (que por defecto exige registrar
 * cada subtipo en un `SerializersModule`) — más simple y, sobre todo,
 * tolerante: un `"type"` desconocido cae en [ChatEvent.Unknown] en vez de
 * lanzar `SerializationException`. */
object ChatEventSerializer : JsonContentPolymorphicSerializer<ChatEvent>(ChatEvent::class) {
    override fun selectDeserializer(element: JsonElement): DeserializationStrategy<ChatEvent> =
        when ((element as? JsonObject)?.get("type")?.jsonPrimitive?.contentOrNull) {
            "text_delta" -> ChatEvent.TextDelta.serializer()
            "tool_start" -> ChatEvent.ToolStart.serializer()
            "tool_end" -> ChatEvent.ToolEnd.serializer()
            "confirmation_required" -> ChatEvent.ConfirmationRequired.serializer()
            "done" -> ChatEvent.Done.serializer()
            "error" -> ChatEvent.ErrorEvent.serializer()
            else -> ChatEvent.Unknown.serializer()
        }
}

// ---------------------------------------------------------------------------
// Negocios (`/v1/negocios/*` — facturación ligera + KPIs, `docs/negocios.md`)
// ---------------------------------------------------------------------------

/** Un canal de la dona "ventas por canal" de `NegociosKpis.porCanal`. */
@Serializable
data class CanalVenta(val canal: String, val total: Double = 0.0)

/** Un evento de `NegociosKpis.actividad` (factura o transacción reciente). */
@Serializable
data class ActividadItem(
    val tipo: String = "",
    val id: String = "",
    val fecha: String = "",
    val descripcion: String = "",
    val monto: Double = 0.0,
    val moneda: String = "USD",
    val status: String? = null,
)

/** `GET /v1/negocios/kpis` (`edecan_business.kpis.kpis_mes`) — a diferencia
 * de [Invoice], estos campos SIEMPRE son `float` del lado del servidor
 * (`kpis.py` hace `float(...)` explícito antes de devolver), así que
 * `Double` directo es seguro, sin necesitar la tolerancia de [montoDouble]. */
@Serializable
data class NegociosKpis(
    val mes: String = "",
    val ingresos: Double = 0.0,
    val gastos: Double = 0.0,
    val beneficio: Double = 0.0,
    @SerialName("nuevos_clientes") val nuevosClientes: Int = 0,
    val facturado: Double = 0.0,
    val cobrado: Double = 0.0,
    @SerialName("por_canal") val porCanal: List<CanalVenta> = emptyList(),
    val actividad: List<ActividadItem> = emptyList(),
)

/** Una fila de `GET /v1/negocios/facturas` (`edecan_business.invoices`,
 * `SELECT * FROM invoices` tal cual — el router no recorta columnas).
 * `subtotal`/`impuestos`/`total` quedan como [JsonElement] a propósito: ver
 * [montoDouble]. */
@Serializable
data class Invoice(
    val id: String,
    val numero: String = "",
    @SerialName("cliente_nombre") val clienteNombre: String = "",
    @SerialName("cliente_email") val clienteEmail: String? = null,
    val moneda: String = "USD",
    val subtotal: JsonElement? = null,
    val impuestos: JsonElement? = null,
    val total: JsonElement? = null,
    val status: String = "draft",
    @SerialName("due_date") val dueDate: String? = null,
    @SerialName("created_at") val createdAt: String = "",
)

/** `subtotal`/`impuestos`/`total` de [Invoice] llegan como número JSON en la
 * vida real (`Decimal` de Postgres vía `fastapi.encoders.jsonable_encoder`,
 * que SIEMPRE produce `float` porque `edecan_business.invoices._round2`
 * cuantiza a 2 decimales, nunca a un entero exacto) — se modelan como
 * [JsonElement] de todas formas, tolerantes también a un string
 * (`"100.00"`), para no depender de ese detalle interno de serialización del
 * backend. `JsonPrimitive.content` es el texto crudo tanto si el valor
 * llegó como número (`100.0`) como si llegó entre comillas (`"100.0"`), así
 * que un solo `toDoubleOrNull()` cubre ambos casos sin necesitar
 * `isLenient` en [edecanJson]. */
fun JsonElement?.montoDouble(): Double =
    (this as? JsonPrimitive)?.content?.toDoubleOrNull() ?: 0.0

// ---------------------------------------------------------------------------
// Credenciales bring-your-own (`/v1/credentials/*`, `ARCHITECTURE.md` §12.b)
// y wizard de arranque (`/v1/setup/*`) — pantalla Perfil.
// ---------------------------------------------------------------------------

@Serializable
data class LlmCredentialOut(
    val kind: String? = null,
    @SerialName("model_principal") val modelPrincipal: String? = null,
    @SerialName("model_rapido") val modelRapido: String? = null,
    @SerialName("base_url") val baseUrl: String? = null,
    val masked: String? = null,
)

/** Forma compartida de `voice_stt`/`voice_tts`/`search` en `GET
 * /v1/credentials` — las tres son `{"provider": ..., "masked": ...}`. */
@Serializable
data class ProviderCredentialOut(val provider: String? = null, val masked: String? = null)

/** `GET /v1/credentials` (`edecan_api.routers.credentials`). `images` no se
 * modela (forma distinta, `{base_url, model, masked}`, y esta app todavía no
 * la usa) — `edecanJson.ignoreUnknownKeys` la descarta sin romper nada. */
@Serializable
data class CredentialsOut(
    val llm: LlmCredentialOut? = null,
    @SerialName("voice_stt") val voiceStt: ProviderCredentialOut? = null,
    @SerialName("voice_tts") val voiceTts: ProviderCredentialOut? = null,
    val search: ProviderCredentialOut? = null,
)

/** Body de `PUT /v1/credentials/llm` (`LLMCredentialsIn` en
 * `edecan_api.routers.credentials`, mismos nombres de campo). */
@Serializable
data class LlmCredentialsIn(
    val kind: String,
    @SerialName("api_key") val apiKey: String? = null,
    @SerialName("base_url") val baseUrl: String? = null,
    @SerialName("model_principal") val modelPrincipal: String? = null,
    @SerialName("model_rapido") val modelRapido: String? = null,
    val extra: Map<String, String> = emptyMap(),
    val validate: Boolean = true,
)

/** `GET /v1/setup/status` (`edecan_api.routers.setup`). */
@Serializable
data class SetupStatusOut(
    @SerialName("local_mode") val localMode: Boolean = false,
    @SerialName("llm_configured") val llmConfigured: Boolean = false,
    val version: String = "",
)

// ---------------------------------------------------------------------------
// Voz web (`/v1/voice/*`, `docs/api.md` §"Voz web") — micrófono de Chat.
// ---------------------------------------------------------------------------

/** Respuesta ya leída de `POST /v1/voice/speak`: el binario de audio (mp3
 * salvo que el tenant no conectó voz propia, en cuyo caso el servidor cae al
 * `StubTTS` offline y manda wav — `contentType` es la señal real, nunca se
 * asume una extensión fija) más el `Content-Type` tal cual lo mandó el
 * servidor. No es un tipo `@Serializable`: el cuerpo es binario, no JSON. */
data class VoiceAudio(val bytes: ByteArray, val contentType: String)

// ---------------------------------------------------------------------------
// IDE embebido, solo lectura (`/v1/ide/*`, `docs/api.md` §"/v1/ide") —
// IDE accesible desde Modo avanzado.
// ---------------------------------------------------------------------------

@Serializable
data class IdeStatusOut(val connected: Boolean = false)

/** Un nodo del árbol de `GET /v1/ide/tree` (`edecan_companion.actions._list_tree`).
 * `children == null` para un archivo, o para una carpeta que llegó al tope
 * de profundidad; `children` vacío (`[]`) es una carpeta vacía de verdad. */
@Serializable
data class IdeTreeNode(
    val name: String,
    @SerialName("is_dir") val isDir: Boolean = false,
    val children: List<IdeTreeNode>? = null,
    @SerialName("size_bytes") val sizeBytes: Long? = null,
)

@Serializable
data class IdeTreeOut(
    val path: String = ".",
    val entries: List<IdeTreeNode> = emptyList(),
    val truncated: Boolean = false,
)

/** `GET /v1/ide/file?path=`. `encoding` es `"utf-8"` o `"base64"` (un
 * archivo binario que no decodifica como UTF-8) — ver [IdeFileOut.esBinario]. */
@Serializable
data class IdeFileOut(
    val path: String = "",
    val content: String = "",
    val encoding: String = "utf-8",
    @SerialName("size_bytes") val sizeBytes: Long = 0,
)

val IdeFileOut.esBinario: Boolean get() = encoding == "base64"
