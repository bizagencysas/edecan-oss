package cc.edecan.shared

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

/**
 * Modelos de `/v1/remote` (control remoto de pantalla + teclado/mouse,
 * `ARCHITECTURE.md` §13.c/§14, `docs/control-remoto.md` §7bis/§10;
 * `apps/api/edecan_api/routers/remote.py`) — pestaña "Remoto" (WP-V6-09,
 * espejo Android de WP-V6-08 en iOS). Mismo criterio tolerante que el resto
 * de `shared` (`MissionsModels.kt`/`AutomationsModels.kt`): solo `id` queda
 * sin default, el resto tolera un campo ausente/nuevo
 * (`edecanJson.ignoreUnknownKeys`, `explicitNulls = false`).
 */

/** `edecan_schemas.plans.FLAG_COMPANION_REMOTE_VIEW` — gate de plan de TODO
 * `/v1/remote` (`remote.py::_require_remote_view`). */
const val FLAG_COMPANION_REMOTE_VIEW = "companion.remote_view"

/** `edecan_schemas.plans.FLAG_COMPANION_REMOTE_INPUT` — gate ADICIONAL,
 * ADEMÁS de [FLAG_COMPANION_REMOTE_VIEW], de `kind = "control"` en
 * `POST /v1/remote/sessions` y de TODO `POST .../input`
 * (`remote.py::_require_remote_control`). */
const val FLAG_COMPANION_REMOTE_INPUT = "companion.remote_input"

// ---------------------------------------------------------------------------
// Vocabulario EXACTO de `edecan_api.routers.remote` (`kind`, `status`,
// `PointerAccion`, `MouseButton`, `SpecialKey`) — `remote_sessions.kind`/
// `.status` no tienen defaults tipados en el backend salvo por CHECK
// constraint (`status`) o texto abierto (`kind`), así que estos quedan como
// `const val`/`Set`/`List` en vez de un `enum class` cerrado: mismo criterio
// que `Conversation.channel`/`Automation.trigger.kind` en el resto de
// `shared` — un valor nuevo del backend no debe romper la decodificación de
// un cliente que todavía no se actualizó.
// ---------------------------------------------------------------------------

const val REMOTE_KIND_VIEW = "view"
const val REMOTE_KIND_CONTROL = "control"

const val REMOTE_STATUS_PENDING = "pending"
const val REMOTE_STATUS_ACTIVE = "active"
const val REMOTE_STATUS_ENDED = "ended"
const val REMOTE_STATUS_DENIED = "denied"

/** `edecan_api.routers.remote.PointerAccion` — usado por `RemotoViewModel`
 * para validar antes de mandar, y por los tests de este módulo. */
val REMOTE_POINTER_ACCIONES: Set<String> = setOf("move", "click", "double_click", "right_click")

/** `edecan_api.routers.remote.MouseButton`. */
val REMOTE_MOUSE_BUTTONS: Set<String> = setOf("left", "right", "middle")

/** `edecan_api.routers.remote.SpecialKey` / `edecan_companion.actions._SPECIAL_KEYS`
 * — en ORDEN (no `Set`): es el mismo orden que pinta la barra de teclas
 * especiales de `RemotoScreen` (mismo orden que `RemoteControlPanel.tsx` en
 * el panel web). */
val REMOTE_SPECIAL_KEYS: List<String> = listOf(
    "enter",
    "tab",
    "escape",
    "backspace",
    "arrow_up",
    "arrow_down",
    "arrow_left",
    "arrow_right",
)

/**
 * Fila pública de `remote_sessions` (`RemoteSessionOut` en
 * `edecan_schemas.devices`; `GET/POST /v1/remote/sessions*`). `kind`/`status`
 * quedan `String` sueltos a propósito (ver el comentario de la sección de
 * arriba) — usa [isControl]/[haTerminado] en vez de comparar a mano.
 */
@Serializable
data class RemoteSession(
    val id: String,
    @SerialName("tenant_id") val tenantId: String = "",
    @SerialName("user_id") val userId: String = "",
    @SerialName("device_id") val deviceId: String? = null,
    val kind: String = REMOTE_KIND_VIEW,
    val status: String = REMOTE_STATUS_PENDING,
    @SerialName("started_at") val startedAt: String? = null,
    @SerialName("ended_at") val endedAt: String? = null,
    @SerialName("frames_count") val framesCount: Int = 0,
    @SerialName("created_at") val createdAt: String = "",
    @SerialName("updated_at") val updatedAt: String = "",
)

/** `session.kind == "control"` — solo esas sesiones aceptan
 * `POST .../input` (ver el docstring de `remote.py::send_input`). */
val RemoteSession.isControl: Boolean get() = kind == REMOTE_KIND_CONTROL

/** `true` si la sesión ya no puede recibir más frames/input (`ended` o
 * `denied`) — `RemotoViewModel` la usa para saber cuándo detener el
 * *polling*. */
val RemoteSession.haTerminado: Boolean get() = status == REMOTE_STATUS_ENDED || status == REMOTE_STATUS_DENIED

/** `GET /v1/remote/sessions/{id}/frame` — el frame más reciente. `imageB64`
 * es un PNG en base64, listo para `android.graphics.BitmapFactory.decodeByteArray`
 * tras un `android.util.Base64.decode` (ver `RemotoViewModel`, `androidApp`:
 * este módulo `commonMain` no depende de `android.*`, así que el decode a
 * `ImageBitmap` vive en la capa de UI, no acá). */
@Serializable
data class RemoteFrame(
    @SerialName("image_b64") val imageB64: String = "",
    val width: Int = 0,
    val height: Int = 0,
    val seq: Int = 0,
)

/** `POST /v1/remote/sessions/{id}/input` — `result` queda como [JsonElement]
 * crudo a propósito (su forma depende de la acción: `{x,y,accion,button}`
 * para `pointer`, `{tipo,length}`/`{tipo,tecla}` para `key` — ver
 * `edecan_companion.actions._input_pointer`/`_input_key`); `RemotoScreen` no
 * necesita leerlo, solo que la llamada haya sido exitosa (`ok`). */
@Serializable
data class RemoteInputResult(val ok: Boolean = true, val result: JsonElement? = null)

// ---------------------------------------------------------------------------
// Intervalo de *polling* del visor — función PURA (sin coroutines/Android),
// testeada en `RemoteModelsTest.kt`.
// ---------------------------------------------------------------------------

/** Default real de `apps/api/edecan_api/config.py::Settings.REMOTE_FRAME_MIN_INTERVAL_SECONDS`
 * (`.env.example`) — el backend lo lee con `getattr(settings, ..., DEFAULT_FRAME_MIN_INTERVAL_SECONDS)`
 * así que puede no existir; este cliente no tiene forma de consultarlo (no
 * hay endpoint que lo exponga), así que asume el default real documentado,
 * igual que hace el panel web (`AUTO_REFRESH_INTERVAL_MS` en
 * `apps/web/src/app/(app)/app/remoto/page.tsx`). */
const val DEFAULT_REMOTE_FRAME_MIN_INTERVAL_SECONDS: Double = 1.0

private const val MIN_REMOTE_POLL_DELAY_MILLIS = 500L

/**
 * Intervalo real (ms) que debe usar el *polling* automático del visor entre
 * cada `GET /v1/remote/sessions/{id}/frame` — SIEMPRE por encima de
 * [minIntervalSeconds] (el `REMOTE_FRAME_MIN_INTERVAL_SECONDS` real del
 * backend, `apps/api/edecan_api/routers/remote.py::DEFAULT_FRAME_MIN_INTERVAL_SECONDS`,
 * default 1.0s) para no pisar su rate limit (`429`) con el *polling* normal
 * — mismo margen 2× que ya usa el panel web
 * (`apps/web/src/app/(app)/app/remoto/page.tsx::AUTO_REFRESH_INTERVAL_MS`,
 * fijo en 2000ms = 2 × 1.0s). Un [minIntervalSeconds] de 0 (o negativo, por
 * si algún día llega mal configurado) nunca produce un *polling* a ráfaga
 * cerrada: cae al piso de [MIN_REMOTE_POLL_DELAY_MILLIS].
 */
fun remoteFramePollDelayMillis(
    minIntervalSeconds: Double = DEFAULT_REMOTE_FRAME_MIN_INTERVAL_SECONDS,
): Long {
    val minMillis = (minIntervalSeconds * 1000).toLong().coerceAtLeast(0L)
    return (minMillis * 2).coerceAtLeast(MIN_REMOTE_POLL_DELAY_MILLIS)
}
