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
import kotlinx.serialization.json.decodeFromJsonElement
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

// ---------------------------------------------------------------------------
// Perfil personal (`GET/PUT /v1/perfil`)
// ---------------------------------------------------------------------------

@Serializable
data class ProfileIdentity(
    @SerialName("nombre_preferido") val nombrePreferido: String = "",
    @SerialName("nombre_completo") val nombreCompleto: String = "",
    val pronombres: String = "",
    @SerialName("fecha_nacimiento") val fechaNacimiento: String = "",
    val pais: String = "",
    val ciudad: String = "",
    @SerialName("zona_horaria") val zonaHoraria: String = "",
    val ocupacion: String = "",
    @SerialName("idioma_preferido") val idiomaPreferido: String = "",
    @SerialName("forma_de_trato") val formaDeTrato: String = "",
    val biografia: String = "",
)

@Serializable
data class ProfileData(
    val identidad: ProfileIdentity = ProfileIdentity(),
    val gustos: List<String> = emptyList(),
    val proyectos: List<String> = emptyList(),
    val metas: List<String> = emptyList(),
    val relaciones: List<String> = emptyList(),
    val empresas: List<String> = emptyList(),
    val habitos: List<String> = emptyList(),
)

@Serializable
data class LiveProfile(
    val resumen: String = "",
    val datos: ProfileData = ProfileData(),
    val version: Int = 0,
    @SerialName("updated_at") val updatedAt: String? = null,
)

@Serializable
data class ProfileDataPatch(val identidad: ProfileIdentity)

@Serializable
data class LiveProfilePatch(val resumen: String, val datos: ProfileDataPatch)

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

/** Metadata privada de un archivo adjunto persistida dentro de
 * `Message.content.attachments`. Nunca contiene URL ni clave de S3. */
@Serializable
data class MessageAttachment(
    @SerialName("file_id") val fileId: String,
    val filename: String = "archivo",
    val mime: String? = null,
)

val Message.adjuntos: List<MessageAttachment>
    get() = runCatching {
        val values = (content as? JsonObject)?.get("attachments") ?: return emptyList()
        edecanJson.decodeFromJsonElement(
            kotlinx.serialization.builtins.ListSerializer(MessageAttachment.serializer()),
            values,
        )
    }.getOrDefault(emptyList())

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
    @SerialName("pending_confirmation") val pendingConfirmation: PendingConfirmation? = null,
)

/** Parte pública y restaurable de una confirmación que sigue pendiente en
 * el servidor. Nunca incluye el estado interno serializado del agente. */
@Serializable
data class PendingConfirmation(
    @SerialName("tool_call_id") val toolCallId: String,
    val name: String,
    val args: JsonElement = JsonObject(emptyMap()),
)

/** Body de `POST /v1/conversations/{id}/messages` (`ChatMessageIn` en
 * `edecan_schemas`, mismo nombre a propósito). */
@Serializable
data class ChatMessageIn(
    val text: String = "",
    /** UUIDs ya subidos por `POST /v1/files`; máximo y pertenencia al tenant
     * vuelven a validarse en el servidor. */
    val attachments: List<String> = emptyList(),
)

/** Respuesta segura de `POST /v1/files`. La ubicación interna del objeto no
 * forma parte del contrato móvil. */
@Serializable
data class UploadedFile(
    val id: String,
    val filename: String = "archivo",
    val mime: String? = null,
    @SerialName("size_bytes") val sizeBytes: Long = 0,
    val status: String = "uploaded",
    @SerialName("created_at") val createdAt: String = "",
)

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

/** Referencia privada a un archivo creado por una tool. No contiene una URL
 * publica: [EdecanApi.downloadArtifact] usa [fileId] contra el endpoint
 * autenticado y limitado al tenant actual. */
@Serializable
data class ArtifactRef(
    @SerialName("file_id") val fileId: String,
    val filename: String,
    val mime: String? = null,
)

/** Acción sugerida por un bloque rico. Son intenciones de UI, no permisos:
 * `prefill_message` solo rellena el compositor y nunca envía por sí sola. */
@Serializable(with = ChatActionSerializer::class)
sealed interface ChatAction {
    val id: String
    val label: String

    @Serializable
    data class OpenUrl(
        override val id: String,
        override val label: String,
        val url: String,
        val action: String = "open_url",
    ) : ChatAction

    @Serializable
    data class OpenScreen(
        override val id: String,
        override val label: String,
        val screen: String,
        val action: String = "open_screen",
    ) : ChatAction

    @Serializable
    data class PrefillMessage(
        override val id: String,
        override val label: String,
        val message: String,
        val action: String = "prefill_message",
    ) : ChatAction

    /** Una acción futura no debe tumbar todo el `tool_end`; simplemente no se
     * renderiza hasta que esta app la conozca. */
    @Serializable
    data class Unknown(
        override val id: String = "unknown",
        override val label: String = "",
        val action: String = "unknown",
    ) : ChatAction
}

object ChatActionSerializer : JsonContentPolymorphicSerializer<ChatAction>(ChatAction::class) {
    override fun selectDeserializer(element: JsonElement): DeserializationStrategy<ChatAction> =
        when ((element as? JsonObject)?.get("action")?.jsonPrimitive?.contentOrNull) {
            "open_url" -> ChatAction.OpenUrl.serializer()
            "open_screen" -> ChatAction.OpenScreen.serializer()
            "prefill_message" -> ChatAction.PrefillMessage.serializer()
            else -> ChatAction.Unknown.serializer()
        }
}

/** Bloques visuales producidos por herramientas. Cada variante conserva
 * `fallbackText`; los tipos futuros caen en [Unknown] sin romper el stream. */
@Serializable(with = ChatBlockSerializer::class)
sealed interface ChatBlock {
    val schemaVersion: Int
    val fallbackText: String?

    @Serializable
    data class Media(
        @SerialName("media_kind") val mediaKind: String,
        val artifact: ArtifactRef,
        val alt: String = "",
        val caption: String? = null,
        @SerialName("schema_version") override val schemaVersion: Int = 1,
        @SerialName("fallback_text") override val fallbackText: String? = null,
        val type: String = "media",
    ) : ChatBlock

    @Serializable
    data class LinkPreview(
        val url: String,
        val title: String,
        val description: String? = null,
        @SerialName("site_name") val siteName: String? = null,
        @SerialName("observed_at") val observedAt: String? = null,
        @SerialName("source_mode") val sourceMode: String = "unknown",
        val actions: List<ChatAction> = emptyList(),
        @SerialName("schema_version") override val schemaVersion: Int = 1,
        @SerialName("fallback_text") override val fallbackText: String? = null,
        val type: String = "link_preview",
    ) : ChatBlock

    @Serializable
    data class Flight(
        @SerialName("offer_id") val offerId: String,
        val airline: String,
        val origin: String,
        val destination: String,
        val departure: String? = null,
        val arrival: String? = null,
        val stops: Int = 0,
        val price: String,
        val currency: String,
        @SerialName("source_mode") val sourceMode: String = "unknown",
        val provider: String? = null,
        @SerialName("observed_at") val observedAt: String? = null,
        @SerialName("expires_at") val expiresAt: String? = null,
        val taxes: String? = null,
        val cancellation: String? = null,
        val actions: List<ChatAction> = emptyList(),
        @SerialName("schema_version") override val schemaVersion: Int = 1,
        @SerialName("fallback_text") override val fallbackText: String? = null,
        val type: String = "flight",
    ) : ChatBlock

    @Serializable
    data class Hotel(
        @SerialName("offer_id") val offerId: String,
        val name: String,
        val city: String,
        val checkin: String? = null,
        val checkout: String? = null,
        val rating: String? = null,
        val price: String,
        val currency: String,
        @SerialName("source_mode") val sourceMode: String = "unknown",
        val provider: String? = null,
        @SerialName("observed_at") val observedAt: String? = null,
        @SerialName("expires_at") val expiresAt: String? = null,
        val taxes: String? = null,
        val cancellation: String? = null,
        val actions: List<ChatAction> = emptyList(),
        @SerialName("schema_version") override val schemaVersion: Int = 1,
        @SerialName("fallback_text") override val fallbackText: String? = null,
        val type: String = "hotel",
    ) : ChatBlock

    @Serializable
    data class Unknown(
        val type: String = "unknown",
        @SerialName("schema_version") override val schemaVersion: Int = 1,
        @SerialName("fallback_text") override val fallbackText: String? = null,
    ) : ChatBlock
}

object ChatBlockSerializer : JsonContentPolymorphicSerializer<ChatBlock>(ChatBlock::class) {
    override fun selectDeserializer(element: JsonElement): DeserializationStrategy<ChatBlock> {
        val objectValue = element as? JsonObject
        val version = objectValue?.get("schema_version")?.jsonPrimitive?.contentOrNull?.toIntOrNull() ?: 1
        if (version != 1) return ChatBlock.Unknown.serializer()
        return when (objectValue?.get("type")?.jsonPrimitive?.contentOrNull) {
            "media" -> ChatBlock.Media.serializer()
            "link_preview" -> ChatBlock.LinkPreview.serializer()
            "flight" -> ChatBlock.Flight.serializer()
            "hotel" -> ChatBlock.Hotel.serializer()
            else -> ChatBlock.Unknown.serializer()
        }
    }
}

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
        @SerialName("tool_call_id") val toolCallId: String? = null,
    ) : ChatEvent

    @Serializable
    data class ToolProgress(
        val name: String,
        @SerialName("elapsed_seconds") val elapsedSeconds: Int = 0,
        val message: String = "Trabajando",
        @SerialName("tool_call_id") val toolCallId: String? = null,
    ) : ChatEvent

    @Serializable
    data class ToolEnd(
        val name: String,
        @SerialName("result_preview") val resultPreview: String = "",
        val artifacts: List<ArtifactRef> = emptyList(),
        @SerialName("blocks_version") val blocksVersion: Int = 1,
        val blocks: List<ChatBlock> = emptyList(),
        @SerialName("tool_call_id") val toolCallId: String? = null,
        @SerialName("mission_id") val missionId: String? = null,
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
            "tool_progress" -> ChatEvent.ToolProgress.serializer()
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
    @SerialName("model_profundo") val modelProfundo: String? = null,
    @SerialName("reasoning_effort_profundo") val reasoningEffortProfundo: String? = null,
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
    @SerialName("model_profundo") val modelProfundo: String? = null,
    @SerialName("reasoning_effort_profundo") val reasoningEffortProfundo: String? = null,
    val extra: Map<String, String> = emptyMap(),
    val validate: Boolean = true,
)

@Serializable
data class LlmModelsOut(
    val kind: String,
    @SerialName("model_principal") val modelPrincipal: String? = null,
    @SerialName("model_rapido") val modelRapido: String? = null,
    @SerialName("model_profundo") val modelProfundo: String? = null,
    @SerialName("reasoning_effort_profundo") val reasoningEffortProfundo: String? = null,
    val models: List<String> = emptyList(),
    @SerialName("manual_allowed") val manualAllowed: Boolean = true,
    @SerialName("capabilities_managed_by_edecan")
    val capabilitiesManagedByEdecan: Boolean = true,
    @SerialName("discovery_error") val discoveryError: String? = null,
)

@Serializable
data class LlmModelsIn(
    @SerialName("model_principal") val modelPrincipal: String,
    @SerialName("model_rapido") val modelRapido: String? = null,
    @SerialName("model_profundo") val modelProfundo: String? = null,
    @SerialName("reasoning_effort_profundo") val reasoningEffortProfundo: String? = "xhigh",
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
    /** Ruta canónica relativa al workspace. Los companions antiguos no la
     * enviaban, por eso sigue siendo opcional y el cliente conserva un
     * fallback construido desde [name]. */
    val path: String? = null,
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

val IdeFileOut.esBinario: Boolean get() = encoding != "utf-8"

@Serializable
data class IdeRunOut(
    val stdout: String = "",
    val stderr: String = "",
    @SerialName("exit_code") val exitCode: Int = -1,
    val truncated: Boolean = false,
)

/** Proyecto que la persona autorizó explícitamente en su computadora.
 *
 * El teléfono conserva únicamente [id]. La ruta real y todo el contenido
 * siguen viviendo en el companion de escritorio. */
@Serializable
data class IdeWorkspace(
    val id: String,
    val name: String,
    val path: String,
    val active: Boolean = false,
    @SerialName("created_at") val createdAt: String = "",
)

@Serializable
data class IdeWorkspacesOut(val workspaces: List<IdeWorkspace> = emptyList())

/** Sesión durable de terminal o agente que continúa en la computadora
 * aunque la app móvil se minimice o pierda momentáneamente la red. */
@Serializable
data class IdeSession(
    val id: String,
    val kind: String,
    @SerialName("workspace_id") val workspaceId: String,
    @SerialName("workspace_name") val workspaceName: String = "",
    val status: String = "starting",
    @SerialName("started_at") val startedAt: String = "",
    @SerialName("ended_at") val endedAt: String? = null,
    @SerialName("exit_code") val exitCode: Int? = null,
    val command: List<String>? = null,
    val provider: String? = null,
    val title: String = "",
) {
    val activa: Boolean
        get() = endedAt == null && status.lowercase() !in setOf(
            "completed", "failed", "closed", "cancelled", "interrupted",
        )
}

@Serializable
data class IdeSessionsOut(val sessions: List<IdeSession> = emptyList())

@Serializable
data class IdeSessionEvent(
    val cursor: Int,
    val type: String,
    val text: String = "",
    val stream: String? = null,
    val timestamp: String = "",
)

@Serializable
data class IdeSessionReadOut(
    val session: IdeSession,
    val events: List<IdeSessionEvent> = emptyList(),
    @SerialName("next_cursor") val nextCursor: Int = 0,
)

/** Respuesta de cerrar una terminal o cancelar un agente. A diferencia de
 * los endpoints de creación, estas rutas conservan el wrapper `session`. */
@Serializable
data class IdeSessionActionOut(val session: IdeSession)

// Git se expone como acciones tipadas; nunca como una shell construida con
// texto recibido desde el teléfono.

@Serializable
data class IdeGitFile(
    val path: String,
    @SerialName("index_status") val indexStatus: String = " ",
    @SerialName("worktree_status") val worktreeStatus: String = " ",
    @SerialName("original_path") val originalPath: String? = null,
) {
    val staged: Boolean get() = indexStatus != " " && indexStatus != "?"
}

@Serializable
data class IdeGitStatus(
    val branch: String? = null,
    val upstream: String? = null,
    val ahead: Int = 0,
    val behind: Int = 0,
    val files: List<IdeGitFile> = emptyList(),
)

@Serializable
data class IdeGitDiff(
    val text: String = "",
    val truncated: Boolean = false,
)

@Serializable
data class IdeGitCommit(
    val hash: String,
    @SerialName("short_hash") val shortHash: String = "",
    val author: String = "",
    val email: String = "",
    val timestamp: String = "",
    val subject: String = "",
)

@Serializable
data class IdeGitLog(val commits: List<IdeGitCommit> = emptyList())
