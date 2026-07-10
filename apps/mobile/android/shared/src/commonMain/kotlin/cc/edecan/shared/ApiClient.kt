package cc.edecan.shared

import io.ktor.client.HttpClient
import io.ktor.client.call.body
import io.ktor.client.plugins.HttpTimeout
import io.ktor.client.plugins.contentnegotiation.ContentNegotiation
import io.ktor.client.request.delete
import io.ktor.client.request.forms.formData
import io.ktor.client.request.forms.submitFormWithBinaryData
import io.ktor.client.request.get
import io.ktor.client.request.header
import io.ktor.client.request.patch
import io.ktor.client.request.post
import io.ktor.client.request.put
import io.ktor.client.request.setBody
import io.ktor.client.statement.HttpResponse
import io.ktor.client.statement.bodyAsText
import io.ktor.http.ContentType
import io.ktor.http.Headers
import io.ktor.http.HttpHeaders
import io.ktor.http.contentType
import io.ktor.http.encodeURLParameter
import io.ktor.serialization.kotlinx.json.json
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Errores tipados de [EdecanApi] — mensajes en español listos para pintar
 * tal cual en la UI (`OnboardingScreen`, `ChatScreen`), mismo criterio que
 * `APIClient.APIError` en `apps/mobile/ios/EdecanKit/Sources/EdecanKit/APIClient.swift`.
 */
sealed class ApiException(message: String) : Exception(message) {
    class UrlInvalida : ApiException("La URL del servidor no es válida.")

    class SinConexion(detalle: String) :
        ApiException("No se pudo conectar con el servidor: $detalle")

    class CredencialesInvalidas :
        ApiException("Correo, contraseña o código de verificación incorrectos.")

    class SesionExpirada : ApiException("La sesión expiró. Vuelve a iniciar sesión.")

    class Servidor(val status: Int, val detalle: String) :
        ApiException("El servidor respondió con un error ($status): $detalle")

    class RespuestaInvalida :
        ApiException("El servidor envió una respuesta que no se pudo interpretar.")
}

@Serializable
private data class LoginBody(
    val email: String,
    val password: String,
    @SerialName("totp_code") val totpCode: String? = null,
)

@Serializable
private data class RefreshBody(
    @SerialName("refresh_token") val refreshToken: String,
    @SerialName("totp_code") val totpCode: String? = null,
)

@Serializable
private data class CrearConversacionBody(val title: String? = null)

@Serializable
private data class ErrorBody(val detail: String? = null)

@Serializable
private data class RegisterBody(
    val email: String,
    val password: String,
    @SerialName("tenant_name") val tenantName: String,
)

@Serializable
private data class SpeakBody(val text: String, @SerialName("voice_id") val voiceId: String? = null)

@Serializable
private data class TranscribeOut(val text: String)

@Serializable
private data class DeviceRegisterBody(
    val nombre: String,
    val plataforma: String = "android",
    val kind: String = "mobile",
    val fingerprint: String? = null,
)

@Serializable
private data class DeviceOut(val id: String = "")

// --- Misiones (`/v1/missions/*`, WP-V5-07) ---------------------------------

@Serializable
private data class MisionCrearBody(val objetivo: String)

@Serializable
private data class MisionConfirmarBody(val approved: Boolean)

// --- Automatizaciones (`/v1/automations/*`, WP-V5-07) ----------------------

@Serializable
private data class AutomationTriggerBody(val kind: String, val rrule: String? = null)

@Serializable
private data class AutomationAccionBody(val instruccion: String, val agente: String? = null)

@Serializable
private data class AutomationCrearBody(
    val nombre: String,
    val descripcion: String = "",
    val trigger: AutomationTriggerBody,
    val accion: AutomationAccionBody,
    val enabled: Boolean = true,
)

@Serializable
private data class AutomationTogglePatchBody(val enabled: Boolean)

// --- Recordatorios (`/v1/reminders/*`, WP-V5-07) ----------------------------

@Serializable
private data class RecordatorioCrearBody(
    @SerialName("due_at") val dueAt: String,
    val message: String,
    val rrule: String? = null,
    val channel: String = "web",
)

@Serializable
private data class RecordatorioCompletarBody(val status: String = "sent")

// --- Control remoto (`/v1/remote/*`, WP-V6-09) ------------------------------

@Serializable
private data class RemoteSessionCreateBody(val consent: Boolean, val kind: String = REMOTE_KIND_VIEW)

@Serializable
private data class RemotePointerInputBody(
    val tipo: String = "pointer",
    val x: Int,
    val y: Int,
    val accion: String,
    val button: String? = null,
)

@Serializable
private data class RemoteKeyInputBody(
    val tipo: String = "key",
    val texto: String? = null,
    val tecla: String? = null,
)

/** `commonMain` es compartido con los targets iOS declarados (ver
 * `shared/build.gradle.kts`) — nada de `java.net.*` aquí, que solo existe en
 * el target JVM/Android. `io.ktor.http.encodeURLParameter` (`CodecsKt`, del
 * propio `ktor-client-core` ya declarado) es la codificación de porcentaje
 * multiplataforma real que ya usa Ktor puertas adentro para construir URLs. */
private fun urlEncode(value: String): String = value.encodeURLParameter()

/** Corre `bloque`; ante cualquier error (de red, `404` porque el endpoint
 * todavía no aterrizó del lado del servidor, etc.) devuelve `null` en vez de
 * propagar — para llamadas "mejor esfuerzo" que nunca deben bloquear un
 * flujo principal (login, logout). SIEMPRE relanza [CancellationException]:
 * swallowing esa excepción específica rompería la cancelación cooperativa de
 * la coroutine que llama esto (p. ej. si el `ViewModel` se destruye a mitad
 * de la llamada) — solo se atrapan errores "reales". */
private suspend fun <T> mejorEsfuerzo(bloque: suspend () -> T): T? =
    try {
        bloque()
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        null
    }

/**
 * Cliente tipado a mano contra las rutas `/v1/...` (`docs/api.md`, `ARCHITECTURE.md`
 * §10.12) usando Ktor `HttpClient` + coroutines — el equivalente Kotlin de
 * `APIClient.swift` en `EdecanKit` (iOS). Mismo contrato de red, dos
 * implementaciones nativas independientes (ver "Por qué no React Native" en
 * `docs/movil-android.md`).
 *
 * El envío de mensajes de chat (`POST /v1/conversations/{id}/messages`,
 * SSE) NO vive aquí — ver [SseClient]. Esta clase sí sabe construir la
 * URL/token de autenticación que ese stream necesita ([tokenDeAccesoValido]
 * / [urlCompleta]); quien conecta ambas piezas es `vm/ChatViewModel.kt` en
 * `androidApp` — igual división de responsabilidades que `ChatViewModel` +
 * `APIClient` + `SSEClient` en EdecanApp/iOS.
 *
 * No es un `actor` (Kotlin no tiene ese concepto): el [Mutex] en
 * [tokenDeAccesoValido] evita que dos 401 concurrentes disparen dos
 * refreshes a la vez; el resto asume el uso normal de un solo ViewModel
 * (llamadas secuenciales desde un `viewModelScope`).
 *
 * Diferencia deliberada con la versión iOS: `APIClient.init` de Swift carga
 * el access token del Keychain de forma SÍNCRONA en el constructor; el
 * [TokenStore] de Kotlin es 100% `suspend` (DataStore/`EncryptedSharedPreferences`
 * vía coroutines), así que no hay equivalente síncrono — en su lugar,
 * [accessTokenVigente] carga el access token persistido de forma perezosa
 * en la primera llamada autenticada tras crear una instancia nueva (p. ej.
 * tras reabrir la app). Si ese token quedó vencido, el servidor responde
 * `401` y [conAutoRefresh] dispara el refresh normal con el refresh token
 * del [TokenStore] — mismo resultado neto, sin bloquear el constructor.
 */
class EdecanApi(
    baseUrl: String,
    private val tokenStore: TokenStore,
) {
    private var baseUrl: String = baseUrl.trimEnd('/')
    private var accessTokenEnMemoria: String? = null
    private val refreshMutex = Mutex()

    private val http: HttpClient = HttpClient {
        install(ContentNegotiation) { json(edecanJson) }
        install(HttpTimeout) {
            requestTimeoutMillis = 30_000
            connectTimeoutMillis = 15_000
        }
        expectSuccess = false // los status code se revisan a mano, ver ejecutar().
    }

    /** El mismo `HttpClient` Ktor, para que `ChatViewModel` se lo pase a
     * [SseClient] y reutilice el connection pool en vez de abrir uno nuevo
     * — ese cliente igual aplica SU PROPIO timeout, mucho más largo (una
     * SSE de chat puede tardar minutos; las llamadas REST de esta clase,
     * no). */
    val httpClientParaStream: HttpClient get() = http

    /** Cambia el servidor contra el que habla este cliente (p. ej. si el
     * onboarding vuelve atrás y el usuario corrige la URL). */
    fun actualizarBaseUrl(url: String) {
        baseUrl = url.trimEnd('/')
    }

    suspend fun haySesion(): Boolean = tokenStore.isPaired()

    // -------------------------------------------------------------------
    // Autenticación
    // -------------------------------------------------------------------

    /** `POST /v1/auth/login`. `totpCode` solo hace falta si la cuenta tiene
     * 2FA activado — se manda solo si no viene vacío. */
    suspend fun login(email: String, password: String, totpCode: String? = null): TokenPair {
        val tokens: TokenPair =
            peticionSinSesion("/v1/auth/login", LoginBody(email, password, totpCode.aNuloSiVacio()))
        guardarTokens(tokens)
        return tokens
    }

    /** `POST /v1/auth/register`. Crea tenant + usuario (owner) + persona por
     * defecto, y deja la sesión iniciada de una — mismo `TokenPair` que
     * [login], mismo `409` (`ApiException.Servidor`) si el correo ya existe. */
    suspend fun registrar(email: String, password: String, tenantName: String): TokenPair {
        val tokens: TokenPair =
            peticionSinSesion("/v1/auth/register", RegisterBody(email, password, tenantName))
        guardarTokens(tokens)
        return tokens
    }

    /** `POST /v1/auth/refresh`. Igual que [login], exige `totp_code` si la
     * cuenta tiene 2FA — quien llame con una cuenta 2FA y no lo pase recibe
     * [ApiException.CredencialesInvalidas] del servidor. */
    suspend fun refrescar(totpCode: String? = null): TokenPair {
        val refreshToken = tokenStore.getRefreshToken() ?: throw ApiException.SesionExpirada()
        val tokens: TokenPair =
            peticionSinSesion("/v1/auth/refresh", RefreshBody(refreshToken, totpCode.aNuloSiVacio()))
        guardarTokens(tokens)
        return tokens
    }

    /** Cierra sesión EN ESTE DISPOSITIVO: borra los tokens de memoria y del
     * [TokenStore]. No es una llamada de red — la API v1 no tiene un
     * endpoint de logout, los refresh tokens simplemente expiran solos
     * (`ARCHITECTURE.md` §10.12). */
    suspend fun cerrarSesion() {
        accessTokenEnMemoria = null
        tokenStore.clearTokens()
    }

    /** Un access token utilizable ahora mismo, refrescando una vez si hace
     * falta. Pensado para quien construye su propia petición fuera de esta
     * clase ([SseClient], vía `ChatViewModel`). */
    suspend fun tokenDeAccesoValido(): String {
        accessTokenVigente()?.let { return it }
        return refreshMutex.withLock {
            accessTokenVigente() ?: run {
                refrescar()
                accessTokenEnMemoria ?: throw ApiException.SesionExpirada()
            }
        }
    }

    /** Resuelve `path` (p. ej. `"/v1/conversations/$id/messages"`) contra
     * la `baseUrl` configurada. Público para que `ChatViewModel` arme su
     * propia petición SSE con la misma base que usa el resto del cliente. */
    fun urlCompleta(path: String): String {
        if (baseUrl.isBlank()) throw ApiException.UrlInvalida()
        return baseUrl + path
    }

    // -------------------------------------------------------------------
    // Perfil y conversaciones
    // -------------------------------------------------------------------

    /** `GET /v1/me`. */
    suspend fun me(): Me = conAutoRefresh { obtener("/v1/me") }

    /** `GET /v1/conversations` — más recientes primero (orden que ya
     * aplica el backend). */
    suspend fun conversations(): List<Conversation> = conAutoRefresh { obtener("/v1/conversations") }

    /** `POST /v1/conversations`. `titulo` es opcional, igual que en la API. */
    suspend fun createConversation(titulo: String? = null): Conversation =
        conAutoRefresh { enviar("/v1/conversations", CrearConversacionBody(titulo)) }

    // -------------------------------------------------------------------
    // Negocios (`/v1/negocios/*` — pestaña Negocios)
    // -------------------------------------------------------------------

    /** `GET /v1/negocios/kpis?mes=YYYY-MM` — `mes` `null` deja que el
     * servidor decida el mes actual UTC (`edecan_business.kpis.kpis_mes`). */
    suspend fun negociosKpis(mes: String? = null): NegociosKpis =
        conAutoRefresh { obtener("/v1/negocios/kpis" + (mes?.let { "?mes=${urlEncode(it)}" } ?: "")) }

    /** `GET /v1/negocios/facturas?status=` — más recientes primero. */
    suspend fun negociosFacturas(status: String? = null): List<Invoice> =
        conAutoRefresh {
            obtener("/v1/negocios/facturas" + (status?.let { "?status=${urlEncode(it)}" } ?: ""))
        }

    // -------------------------------------------------------------------
    // Credenciales bring-your-own y wizard de arranque (pestaña Perfil)
    // -------------------------------------------------------------------

    /** `GET /v1/credentials` — nunca trae el secreto completo, solo lo
     * enmascarado (`LlmCredentialOut.masked`, etc.). */
    suspend fun credentials(): CredentialsOut = conAutoRefresh { obtener("/v1/credentials") }

    /** `GET /v1/setup/status` — `localMode` decide si esta app debe ofrecer
     * los `kind` que solo tienen sentido con el backend corriendo en la
     * máquina del propio usuario (`claude_cli`/`codex_cli`/`ollama`). */
    suspend fun setupStatus(): SetupStatusOut = conAutoRefresh { obtener("/v1/setup/status") }

    /** `PUT /v1/credentials/llm` — "pegar y validar" (`DIRECCION_ACTUAL.md`):
     * `204` si el servidor pudo validar `payload` contra el proveedor real
     * (o `payload.validate == false`); lanza [ApiException.Servidor] con el
     * detalle EXACTO del proveedor si la rechazó (`400`). */
    suspend fun conectarLlm(payload: LlmCredentialsIn) {
        conAutoRefresh { actualizarSinCuerpo("/v1/credentials/llm", payload) }
    }

    // -------------------------------------------------------------------
    // Voz web (`/v1/voice/*` — pestaña Voz)
    // -------------------------------------------------------------------

    /** `POST /v1/voice/transcribe` — sube `audioBytes` como
     * `multipart/form-data` (campo `audio`, más `language` si no es nulo) y
     * devuelve el texto transcrito. Si el tenant no conectó su propio
     * proveedor de voz (`PUT /v1/credentials/voice/stt`), el servidor cae al
     * `StubSTT` offline: SIEMPRE el mismo texto fijo, sin importar el audio
     * real (ver `docs/api.md` §"Voz web") — la pantalla de Voz avisa de esto
     * por separado leyendo [credentials]. */
    suspend fun transcribirVoz(
        audioBytes: ByteArray,
        filename: String,
        mime: String,
        language: String? = null,
    ): String =
        conAutoRefresh {
            val token = accessTokenVigente() ?: throw ApiException.SesionExpirada()
            val response = ejecutar {
                http.submitFormWithBinaryData(
                    url = urlCompleta("/v1/voice/transcribe"),
                    formData = formData {
                        append(
                            "audio",
                            audioBytes,
                            Headers.build {
                                append(HttpHeaders.ContentType, mime)
                                append(HttpHeaders.ContentDisposition, "filename=\"$filename\"")
                            },
                        )
                        if (!language.isNullOrBlank()) append("language", language)
                    },
                ) { header(HttpHeaders.Authorization, "Bearer $token") }
            }
            val out: TranscribeOut = manejarRespuestaAutenticada(response)
            out.text
        }

    /** `POST /v1/voice/speak` — el `Content-Type` de la respuesta decide el
     * formato real (`audio/mpeg` normalmente, `audio/wav` si cayó al
     * `StubTTS` offline, ver `docs/api.md` §"Voz web"); nunca se asume uno
     * fijo. */
    suspend fun hablar(texto: String, vozId: String? = null): VoiceAudio =
        conAutoRefresh {
            val token = accessTokenVigente() ?: throw ApiException.SesionExpirada()
            val response = ejecutar {
                http.post(urlCompleta("/v1/voice/speak")) {
                    header(HttpHeaders.Authorization, "Bearer $token")
                    contentType(ContentType.Application.Json)
                    setBody(SpeakBody(texto, vozId))
                }
            }
            if (response.status.value == 401) throw ApiException.SesionExpirada()
            validarStatus(response)
            val bytes: ByteArray = response.body()
            val tipo = response.headers[HttpHeaders.ContentType] ?: "audio/mpeg"
            VoiceAudio(bytes, tipo)
        }

    // -------------------------------------------------------------------
    // IDE embebido, solo lectura (`/v1/ide/*` — pestaña IDE)
    // -------------------------------------------------------------------

    /** `GET /v1/ide/status` — `false`/`503` en el resto de rutas de `/v1/ide`
     * si no hay companion de escritorio conectado. */
    suspend fun ideStatus(): IdeStatusOut = conAutoRefresh { obtener("/v1/ide/status") }

    /** `GET /v1/ide/tree?path=` — árbol recursivo del sandbox del companion;
     * `path` nulo pide la raíz. */
    suspend fun ideTree(path: String? = null): IdeTreeOut =
        conAutoRefresh { obtener("/v1/ide/tree" + (path?.let { "?path=${urlEncode(it)}" } ?: "")) }

    /** `GET /v1/ide/file?path=` — contenido completo de un archivo del
     * sandbox del companion. */
    suspend fun ideFile(path: String): IdeFileOut =
        conAutoRefresh { obtener("/v1/ide/file?path=${urlEncode(path)}") }

    // -------------------------------------------------------------------
    // Emparejamiento de dispositivo (`/v1/devices`, contrato de WP-V4-01 —
    // en paralelo a este work package, ver docstring de [mejorEsfuerzo]).
    // -------------------------------------------------------------------

    /** Registra este dispositivo al iniciar sesión/registrarse. "Mejor
     * esfuerzo" a propósito (nunca lanza, ver [mejorEsfuerzo]): si
     * `/v1/devices` todavía no existe del lado del servidor (`404`) u ocurre
     * cualquier otro error, devuelve `null` en silencio — emparejar el
     * dispositivo nunca debe bloquear ni fallar visiblemente un login. */
    suspend fun emparejarDispositivo(nombre: String, fingerprint: String?): String? =
        mejorEsfuerzo {
            val out: DeviceOut = conAutoRefresh {
                enviar("/v1/devices", DeviceRegisterBody(nombre = nombre, fingerprint = fingerprint))
            }
            out.id.ifBlank { null }
        }

    /** `DELETE /v1/devices/{deviceId}` al cerrar sesión — mismo criterio
     * "mejor esfuerzo" que [emparejarDispositivo]: nunca lanza, para que
     * cerrar sesión en este dispositivo nunca falle por esto. */
    suspend fun revocarDispositivo(deviceId: String) {
        mejorEsfuerzo { conAutoRefresh { eliminarSinCuerpo("/v1/devices/$deviceId") } }
    }

    // -------------------------------------------------------------------
    // Misiones (`/v1/missions/*` — pestaña Misiones, WP-V5-07)
    // -------------------------------------------------------------------

    /** `GET /v1/missions` — más recientes primero (orden que ya aplica el
     * backend). `403` (`ApiException.Servidor`) si el plan del tenant no
     * trae el flag `agents.missions` ([FLAG_AGENTS_MISSIONS]). */
    suspend fun listMissions(): List<Mission> = conAutoRefresh { obtener("/v1/missions") }

    /** `POST /v1/missions {objetivo}` — encola el job `run_mission`; la
     * planificación/ejecución real corre asíncrona en el worker
     * (`missions.py`, docstring del módulo), así que esta misión vuelve en
     * `status = "planning"`. */
    suspend fun createMission(objetivo: String): Mission =
        conAutoRefresh { enviar("/v1/missions", MisionCrearBody(objetivo)) }

    /** `GET /v1/missions/{id}` — la misión más sus `agent_steps`
     * (`MissionDetailOut`). */
    suspend fun getMission(missionId: String): MissionDetail =
        conAutoRefresh { obtener("/v1/missions/$missionId") }

    /** `POST /v1/missions/{id}/confirm {approved}` — resuelve el paso
     * `waiting_confirmation` pendiente: `approve = true` lo aprueba y
     * reencola `run_mission` para que siga corriendo; `approve = false`
     * cancela la misión entera (`missions.py::confirm_mission`). `409` si
     * la misión no tiene una confirmación pendiente en este momento. */
    suspend fun confirmMission(missionId: String, approve: Boolean): Mission =
        conAutoRefresh { enviar("/v1/missions/$missionId/confirm", MisionConfirmarBody(approve)) }

    // -------------------------------------------------------------------
    // Automatizaciones (`/v1/automations/*` — pestaña Automatizaciones, WP-V5-07)
    // -------------------------------------------------------------------

    /** `GET /v1/automations` — más recientes primero. `403` si el plan no
     * trae el flag `automations.rules` ([FLAG_AUTOMATIONS_RULES]). */
    suspend fun listAutomations(): List<Automation> = conAutoRefresh { obtener("/v1/automations") }

    /** `POST /v1/automations` — alta simple (`AutomatizacionesScreen`):
     * siempre un disparador `kind = "schedule"` (`rrule` RFC 5545, mismos
     * presets que `AutomationForm.tsx` en el panel web) más una acción
     * `agent_instruction`. Este cliente no ofrece crear automatizaciones
     * `kind = "webhook"` — esas solo se crean desde el panel web, que sí
     * muestra el secreto generado una sola vez. */
    suspend fun createAutomation(
        nombre: String,
        rrule: String,
        instruccion: String,
        agente: String? = null,
        descripcion: String = "",
        enabled: Boolean = true,
    ): Automation =
        conAutoRefresh {
            enviar(
                "/v1/automations",
                AutomationCrearBody(
                    nombre = nombre,
                    descripcion = descripcion,
                    trigger = AutomationTriggerBody(kind = "schedule", rrule = rrule),
                    accion = AutomationAccionBody(instruccion = instruccion, agente = agente),
                    enabled = enabled,
                ),
            )
        }

    /** `PATCH /v1/automations/{id} {enabled}` — `AutomatizacionesScreen`
     * actualiza su Switch de forma optimista ANTES de llamar esto y revierte
     * a mano si lanza (`AutomatizacionesViewModel.alternar`); también puede
     * lanzar `403` si al reactivar (`enabled = true`) el tenant ya alcanzó
     * `limits.automations_active` de su plan. */
    suspend fun toggleAutomation(automationId: String, enabled: Boolean): Automation =
        conAutoRefresh {
            actualizarParcial("/v1/automations/$automationId", AutomationTogglePatchBody(enabled))
        }

    /** `GET /v1/automations/{id}/runs` — más recientes primero, hasta 50
     * (`_RUNS_LIMIT_DEFAULT` en `automations.py`). */
    suspend fun listAutomationRuns(automationId: String): List<AutomationRun> =
        conAutoRefresh { obtener("/v1/automations/$automationId/runs") }

    // -------------------------------------------------------------------
    // Recordatorios (`/v1/reminders/*` — pestaña Recordatorios, WP-V5-07)
    // -------------------------------------------------------------------

    /** `GET /v1/reminders` — sin flag de plan (disponible en los 4 planes,
     * `ARCHITECTURE.md` §10.13). */
    suspend fun listReminders(): List<Reminder> = conAutoRefresh { obtener("/v1/reminders") }

    /** `POST /v1/reminders {due_at, message, channel}`. `canal` SIEMPRE
     * `"web"` desde este cliente a propósito, NUNCA `"mobile"`: `reminders.py`
     * acepta cualquier string en `channel`, pero todavía no hay una ruta de
     * entrega push nativa (llega en otra ola con FCM — ver
     * `docs/movil-android.md` "Roadmap") y `send_reminder.py` hoy solo sabe
     * entregar `web`/`api` como mensaje de chat (`voice`/`phone` degradan
     * con una advertencia); mandar `"mobile"` no cambiaría nada del lado del
     * servidor todavía y sí confundiría a un futuro lector de `reminders`. */
    suspend fun createReminder(texto: String, fecha: String, canal: String = "web"): Reminder =
        conAutoRefresh {
            enviar("/v1/reminders", RecordatorioCrearBody(dueAt = fecha, message = texto, channel = canal))
        }

    /** `PUT /v1/reminders/{id} {status: "sent"}` — "completa" un recordatorio
     * pendiente a mano, antes de que `send_reminder_scan` lo alcance por su
     * cuenta (`ARCHITECTURE.md` §10.11). El vocabulario real de
     * `ReminderPatch.status` es solo `pending`/`sent`/`cancelled` — no existe
     * un status `"done"` propio; `"sent"` es el que representa "ya se
     * entregó/atendió", el mismo que usa el job automático
     * (`repo.mark_reminder_sent`). */
    suspend fun completeReminder(reminderId: String): Reminder =
        conAutoRefresh { actualizar("/v1/reminders/$reminderId", RecordatorioCompletarBody()) }

    // -------------------------------------------------------------------
    // Control remoto (`/v1/remote/*` — pantalla Remoto, WP-V6-09). Errores
    // propios de este grupo (403 plan/flag/denegado, 404, 409, 422, 429, 501,
    // 502, 503) llegan todos como [ApiException.Servidor] con su `status`
    // real — ver el docstring de `edecan_api.routers.remote` para el detalle
    // línea por línea de cada código.
    // -------------------------------------------------------------------

    /** `POST /v1/remote/sessions`. `consent` DEBE ser `true` (el servidor
     * responde `422` si no) — `RemotoScreen` solo llama esto tras el
     * checkbox de consentimiento explícito, mismo criterio que
     * `ConsentGate.tsx` en el panel web. `kind` = [REMOTE_KIND_CONTROL]
     * exige además el flag de plan `companion.remote_input`
     * ([FLAG_COMPANION_REMOTE_INPUT]) — `403` si no. */
    suspend fun createRemoteSession(consent: Boolean, kind: String = REMOTE_KIND_VIEW): RemoteSession =
        conAutoRefresh {
            enviar("/v1/remote/sessions", RemoteSessionCreateBody(consent = consent, kind = kind))
        }

    /** `GET /v1/remote/sessions` — todas las sesiones del tenant (cualquier
     * estado), más recientes primero (orden que ya aplica el backend). */
    suspend fun listRemoteSessions(): List<RemoteSession> =
        conAutoRefresh { obtener("/v1/remote/sessions") }

    suspend fun getRemoteSession(sessionId: String): RemoteSession =
        conAutoRefresh { obtener("/v1/remote/sessions/$sessionId") }

    /** `GET /v1/remote/sessions/{id}/frame` — pide un frame nuevo al
     * companion. El primer frame exitoso pasa la sesión a `active` (es lo
     * que dispara la aprobación LOCAL en el companion — `RemotoViewModel`
     * llama esto de inmediato tras [createRemoteSession]). Puede lanzar
     * `429` (pediste un frame antes de que pasara
     * `REMOTE_FRAME_MIN_INTERVAL_SECONDS`, ver [remoteFramePollDelayMillis]),
     * `501` (el companion no soporta/tiene deshabilitada la captura, o
     * corre en una plataforma sin soporte), `403` (el usuario lo denegó en
     * el companion, o la sesión ya estaba `denied`), `409` (sesión ya
     * `ended`) o `503` (companion no conectado o sin respuesta a tiempo). */
    suspend fun getRemoteFrame(sessionId: String): RemoteFrame =
        conAutoRefresh { obtener("/v1/remote/sessions/$sessionId/frame") }

    /** `POST /v1/remote/sessions/{id}/end` — termina la sesión (idempotente,
     * repetirlo no falla). Devuelve la sesión ya actualizada. */
    suspend fun endRemoteSession(sessionId: String): RemoteSession =
        conAutoRefresh { enviarSinCuerpo("/v1/remote/sessions/$sessionId/end") }

    /** `POST /v1/remote/sessions/{id}/input {tipo:"pointer",...}` — solo
     * sesiones `kind` = [REMOTE_KIND_CONTROL] ya `active`. [accion] debe
     * ser una de [REMOTE_POINTER_ACCIONES]; [button] (default `"left"` del
     * lado del servidor si se omite) una de [REMOTE_MOUSE_BUTTONS]. Puede
     * lanzar, además de los genéricos de arriba, `403` (sesión no es de
     * control, o el companion/usuario denegó el comando), `409` (todavía
     * `pending`, o ya `ended`) y `429` (rate limit propio, mucho más laxo
     * que el de [getRemoteFrame]). */
    suspend fun sendRemotePointerInput(
        sessionId: String,
        x: Int,
        y: Int,
        accion: String,
        button: String? = null,
    ): RemoteInputResult =
        conAutoRefresh {
            enviar(
                "/v1/remote/sessions/$sessionId/input",
                RemotePointerInputBody(x = x, y = y, accion = accion, button = button),
            )
        }

    /** `POST .../input {tipo:"key", texto}` — escribe [texto] tal cual,
     * carácter por carácter, en el equipo remoto (Unicode, sin depender del
     * layout de teclado — ver `edecan_companion.actions._input_key`). */
    suspend fun sendRemoteKeyTexto(sessionId: String, texto: String): RemoteInputResult =
        conAutoRefresh {
            enviar("/v1/remote/sessions/$sessionId/input", RemoteKeyInputBody(texto = texto))
        }

    /** `POST .../input {tipo:"key", tecla}` — [tecla] debe ser una de
     * [REMOTE_SPECIAL_KEYS]. */
    suspend fun sendRemoteKeyTecla(sessionId: String, tecla: String): RemoteInputResult =
        conAutoRefresh {
            enviar("/v1/remote/sessions/$sessionId/input", RemoteKeyInputBody(tecla = tecla))
        }

    // -------------------------------------------------------------------
    // Internals: HTTP
    // -------------------------------------------------------------------

    private fun String?.aNuloSiVacio(): String? = this?.trim()?.ifEmpty { null }

    private suspend fun accessTokenVigente(): String? =
        accessTokenEnMemoria ?: tokenStore.getAccessToken()?.also { accessTokenEnMemoria = it }

    private suspend fun guardarTokens(tokens: TokenPair) {
        accessTokenEnMemoria = tokens.accessToken
        tokenStore.saveTokens(tokens.accessToken, tokens.refreshToken)
    }

    /** Ejecuta `operacion`; si falla porque el access token expiró,
     * refresca UNA vez y reintenta. Si el refresh también falla, propaga
     * el error del refresh (normalmente [ApiException.SesionExpirada]). */
    private suspend fun <T> conAutoRefresh(operacion: suspend () -> T): T =
        try {
            operacion()
        } catch (e: ApiException.SesionExpirada) {
            refrescar()
            operacion()
        }

    private suspend inline fun <reified R> obtener(path: String): R {
        val token = accessTokenVigente() ?: throw ApiException.SesionExpirada()
        val response = ejecutar {
            http.get(urlCompleta(path)) { header(HttpHeaders.Authorization, "Bearer $token") }
        }
        return manejarRespuestaAutenticada(response)
    }

    private suspend inline fun <reified B, reified R> enviar(path: String, body: B): R {
        val token = accessTokenVigente() ?: throw ApiException.SesionExpirada()
        val response = ejecutar {
            http.post(urlCompleta(path)) {
                header(HttpHeaders.Authorization, "Bearer $token")
                contentType(ContentType.Application.Json)
                setBody(body)
            }
        }
        return manejarRespuestaAutenticada(response)
    }

    /** `POST` sin cuerpo que SÍ decodifica una respuesta (`POST
     * /v1/remote/sessions/{id}/end`, [endRemoteSession]) — a diferencia de
     * [enviar], no manda `Content-Type` ni cuerpo alguno. */
    private suspend inline fun <reified R> enviarSinCuerpo(path: String): R {
        val token = accessTokenVigente() ?: throw ApiException.SesionExpirada()
        val response = ejecutar {
            http.post(urlCompleta(path)) { header(HttpHeaders.Authorization, "Bearer $token") }
        }
        return manejarRespuestaAutenticada(response)
    }

    /** `PUT` cuya respuesta exitosa es `204 No Content` (`conectarLlm`): a
     * diferencia de [enviar], no intenta decodificar ningún cuerpo. */
    private suspend inline fun <reified B> actualizarSinCuerpo(path: String, body: B) {
        val token = accessTokenVigente() ?: throw ApiException.SesionExpirada()
        val response = ejecutar {
            http.put(urlCompleta(path)) {
                header(HttpHeaders.Authorization, "Bearer $token")
                contentType(ContentType.Application.Json)
                setBody(body)
            }
        }
        if (response.status.value == 401) throw ApiException.SesionExpirada()
        validarStatus(response)
    }

    /** `PUT` que SÍ decodifica un cuerpo de respuesta — a diferencia de
     * [actualizarSinCuerpo] (pensado para `204`), úsalo cuando el endpoint
     * devuelve la fila ya actualizada (p. ej. `PUT /v1/reminders/{id}`,
     * [completeReminder]). */
    private suspend inline fun <reified B, reified R> actualizar(path: String, body: B): R {
        val token = accessTokenVigente() ?: throw ApiException.SesionExpirada()
        val response = ejecutar {
            http.put(urlCompleta(path)) {
                header(HttpHeaders.Authorization, "Bearer $token")
                contentType(ContentType.Application.Json)
                setBody(body)
            }
        }
        return manejarRespuestaAutenticada(response)
    }

    /** `PATCH` con cuerpo, decodifica la respuesta (p. ej. `PATCH
     * /v1/automations/{id}`, [toggleAutomation] — devuelve la fila ya
     * actualizada). */
    private suspend inline fun <reified B, reified R> actualizarParcial(path: String, body: B): R {
        val token = accessTokenVigente() ?: throw ApiException.SesionExpirada()
        val response = ejecutar {
            http.patch(urlCompleta(path)) {
                header(HttpHeaders.Authorization, "Bearer $token")
                contentType(ContentType.Application.Json)
                setBody(body)
            }
        }
        return manejarRespuestaAutenticada(response)
    }

    /** `DELETE` "mejor esfuerzo" (`revocarDispositivo`): un `404` se trata
     * como éxito silencioso (idempotente — el recurso ya no está, o el
     * endpoint todavía no aterrizó del lado del servidor), no como error. */
    private suspend fun eliminarSinCuerpo(path: String) {
        val token = accessTokenVigente() ?: throw ApiException.SesionExpirada()
        val response = ejecutar {
            http.delete(urlCompleta(path)) { header(HttpHeaders.Authorization, "Bearer $token") }
        }
        if (response.status.value == 401) throw ApiException.SesionExpirada()
        if (response.status.value == 404) return
        validarStatus(response)
    }

    /** Variante para `/login` y `/refresh`: no llevan `Authorization`, y un
     * `401` significa credenciales inválidas, no sesión expirada. */
    private suspend inline fun <reified B, reified R> peticionSinSesion(path: String, body: B): R {
        val response = ejecutar {
            http.post(urlCompleta(path)) {
                contentType(ContentType.Application.Json)
                setBody(body)
            }
        }
        if (response.status.value == 401) throw ApiException.CredencialesInvalidas()
        validarStatus(response)
        return decodificar(response)
    }

    private suspend inline fun <reified R> manejarRespuestaAutenticada(response: HttpResponse): R {
        if (response.status.value == 401) throw ApiException.SesionExpirada()
        validarStatus(response)
        return decodificar(response)
    }

    private suspend fun ejecutar(bloque: suspend () -> HttpResponse): HttpResponse =
        try {
            bloque()
        } catch (e: ApiException) {
            throw e
        } catch (e: Exception) {
            throw ApiException.SinConexion(e.message ?: e::class.simpleName ?: "error desconocido")
        }

    private suspend fun validarStatus(response: HttpResponse) {
        if (response.status.value !in 200..299) {
            throw ApiException.Servidor(response.status.value, extraerMensajeDeError(response))
        }
    }

    private suspend inline fun <reified R> decodificar(response: HttpResponse): R =
        try {
            response.body()
        } catch (e: Exception) {
            throw ApiException.RespuestaInvalida()
        }

    /** FastAPI manda `{"detail": "..."}` en sus errores — se usa tal cual
     * si existe; si no, un texto genérico en vez de fallar decodificando. */
    private suspend fun extraerMensajeDeError(response: HttpResponse): String =
        try {
            edecanJson.decodeFromString(ErrorBody.serializer(), response.bodyAsText()).detail ?: "sin detalle"
        } catch (e: Exception) {
            "sin detalle"
        }
}
