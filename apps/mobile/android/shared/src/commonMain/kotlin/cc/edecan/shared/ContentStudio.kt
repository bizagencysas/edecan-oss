package cc.edecan.shared

import io.ktor.client.plugins.timeout
import io.ktor.client.request.header
import io.ktor.client.request.post
import io.ktor.client.request.setBody
import io.ktor.client.statement.bodyAsText
import io.ktor.http.ContentType
import io.ktor.http.HttpHeaders
import io.ktor.http.contentType
import kotlinx.coroutines.CancellationException
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

@Serializable
enum class SocialContentPlatform {
    @SerialName("linkedin")
    LINKEDIN,

    @SerialName("x")
    X,
    ;

    val label: String get() = if (this == LINKEDIN) "LinkedIn" else "X"
    val characterLimit: Int get() = if (this == LINKEDIN) 3_000 else 280
}

@Serializable
data class SocialContentRequest(
    val platform: SocialContentPlatform,
    val topic: String,
    val objective: String,
    val tone: String,
    @SerialName("with_image") val withImage: Boolean,
)

@Serializable
data class SocialContentDraft(
    val status: String,
    val platform: SocialContentPlatform,
    val copy: String,
    val parts: List<String>,
    @SerialName("alt_text") val altText: String = "",
    @SerialName("offline_visual") val offlineVisual: Boolean = false,
    val artifacts: List<ArtifactRef> = emptyList(),
    @SerialName("requires_human_confirmation") val requiresHumanConfirmation: Boolean = true,
) {
    val imageArtifact: ArtifactRef?
        get() = artifacts.firstOrNull { it.mime?.lowercase()?.startsWith("image/") == true }
}

@Serializable
enum class StudioProjectMode {
    @SerialName("general") GENERAL,
    @SerialName("landing") LANDING,
    @SerialName("mockup") MOCKUP,
    @SerialName("post") POST,
    @SerialName("carousel") CAROUSEL,
    @SerialName("ad") AD,
    @SerialName("email") EMAIL,
    @SerialName("deck") DECK,
    ;

    val label: String
        get() = when (this) {
            GENERAL -> "Cualquier cosa"
            LANDING -> "Página web"
            MOCKUP -> "App o producto"
            POST -> "Post"
            CAROUSEL -> "Carrusel"
            AD -> "Anuncio"
            EMAIL -> "Email"
            DECK -> "Presentación"
        }
}

@Serializable
enum class StudioProjectQuality {
    @SerialName("fast") FAST,
    @SerialName("balanced") BALANCED,
    @SerialName("max") MAX,
    ;

    val label: String
        get() = when (this) {
            FAST -> "Rápida"
            BALANCED -> "Equilibrada"
            MAX -> "Máxima"
        }
}

@Serializable
enum class StudioExportFormat {
    @SerialName("html") HTML,
    @SerialName("png") PNG,
    @SerialName("pdf") PDF,
}

/** Contrato estable de la fachada privada de Studio. Los archivos son UUIDs
 * ya subidos a Edecán: nunca se filtran rutas ni configuración del motor. */
@Serializable
data class StudioActionRequest(
    val action: String,
    @SerialName("projectId") val projectId: String? = null,
    @SerialName("revisionId") val revisionId: String? = null,
    val prompt: String? = null,
    val instruction: String? = null,
    @SerialName("projectName") val projectName: String? = null,
    @SerialName("brandName") val brandName: String? = null,
    val mode: StudioProjectMode? = null,
    val width: Int? = null,
    val height: Int? = null,
    val count: Int? = null,
    val quality: StudioProjectQuality? = null,
    val files: List<String> = emptyList(),
    @SerialName("exportFormat") val exportFormat: StudioExportFormat? = null,
    @SerialName("includeArchived") val includeArchived: Boolean? = null,
    val confirmed: Boolean = false,
)

data class StudioProjectSummary(
    val id: String,
    val name: String,
    val mode: String,
    val revisionCount: Int = 0,
    val updatedAt: String? = null,
    val brandName: String? = null,
    val archivedAt: String? = null,
)

data class StudioRevision(
    val id: String,
    val label: String,
    val width: Int,
    val height: Int,
    val instruction: String,
    val createdAt: String? = null,
    val archivedAt: String? = null,
)

@Serializable
data class StudioActionResponse(
    val status: String = "ready",
    val action: String,
    val message: String,
    val result: JsonObject = JsonObject(emptyMap()),
    val artifacts: List<ArtifactRef> = emptyList(),
    val presentation: List<JsonObject> = emptyList(),
) {
    val projects: List<StudioProjectSummary>
        get() = (result["projects"] as? JsonArray).orEmpty().mapNotNull(::parseProject)

    val revisions: List<StudioRevision>
        get() = (result["revisions"] as? JsonArray).orEmpty().mapNotNull { raw ->
            val value = raw as? JsonObject ?: return@mapNotNull null
            val id = value.string("id") ?: return@mapNotNull null
            StudioRevision(
                id = id,
                label = value.string("label") ?: "Revisión",
                width = value.integer("width") ?: 0,
                height = value.integer("height") ?: 0,
                instruction = value.string("instruction").orEmpty(),
                createdAt = value.string("createdAt"),
                archivedAt = value.string("archivedAt"),
            )
        }

    val project: StudioProjectSummary?
        get() {
            val parsed = parseProject(result["project"] ?: return null) ?: return null
            return parsed.copy(
                revisionCount = if (revisions.isEmpty()) parsed.revisionCount else revisions.size,
            )
        }

    val revisionId: String? get() = result.string("revision")

    private fun parseProject(raw: kotlinx.serialization.json.JsonElement): StudioProjectSummary? {
        val value = raw as? JsonObject ?: return null
        return StudioProjectSummary(
            id = value.string("id") ?: return null,
            name = value.string("name") ?: "Proyecto sin nombre",
            mode = value.string("mode") ?: "general",
            revisionCount = value.integer("revisions") ?: 0,
            updatedAt = value.string("updatedAt"),
            brandName = value.string("brandName"),
            archivedAt = value.string("archivedAt"),
        )
    }
}

private fun JsonObject.string(key: String): String? =
    (this[key] as? JsonPrimitive)?.takeUnless { it.isString.not() }?.content

private fun JsonObject.integer(key: String): Int? =
    (this[key] as? JsonPrimitive)?.content?.toIntOrNull()

@Serializable
private data class ContentStudioErrorBody(val detail: String? = null)

/** Crea un borrador y sus artefactos privados. El request puede incluir una
 * imagen generada, por eso amplía solo aquí el timeout REST normal. */
suspend fun EdecanApi.createSocialContent(input: SocialContentRequest): SocialContentDraft =
    createSocialContent(input, canRefresh = true)

suspend fun EdecanApi.studioAction(input: StudioActionRequest): StudioActionResponse =
    studioAction(input.copy(files = input.files.take(12)), canRefresh = true)

private suspend fun EdecanApi.studioAction(
    input: StudioActionRequest,
    canRefresh: Boolean,
): StudioActionResponse {
    val token = tokenDeAccesoValido()
    val response = try {
        httpClientParaStream.post(urlCompleta("/v1/content/studio/actions")) {
            timeout { requestTimeoutMillis = 1_230_000 }
            header(HttpHeaders.Authorization, "Bearer $token")
            contentType(ContentType.Application.Json)
            setBody(input)
        }
    } catch (error: CancellationException) {
        throw error
    } catch (error: Throwable) {
        throw ApiException.SinConexion(error.message ?: error::class.simpleName.orEmpty())
    }

    if (response.status.value == 401 && canRefresh) {
        refrescar()
        return studioAction(input, canRefresh = false)
    }
    if (response.status.value == 401) throw ApiException.SesionExpirada()

    val body = response.bodyAsText()
    if (response.status.value !in 200..299) {
        val detail = runCatching {
            edecanJson.decodeFromString<ContentStudioErrorBody>(body).detail
        }.getOrNull() ?: "sin detalle"
        throw ApiException.Servidor(response.status.value, detail)
    }
    return runCatching { edecanJson.decodeFromString<StudioActionResponse>(body) }
        .getOrElse { throw ApiException.RespuestaInvalida() }
}

private suspend fun EdecanApi.createSocialContent(
    input: SocialContentRequest,
    canRefresh: Boolean,
): SocialContentDraft {
    val token = tokenDeAccesoValido()
    val response = try {
        httpClientParaStream.post(urlCompleta("/v1/content/social")) {
            timeout { requestTimeoutMillis = 180_000 }
            header(HttpHeaders.Authorization, "Bearer $token")
            contentType(ContentType.Application.Json)
            setBody(input)
        }
    } catch (error: CancellationException) {
        throw error
    } catch (error: Throwable) {
        throw ApiException.SinConexion(error.message ?: error::class.simpleName.orEmpty())
    }

    if (response.status.value == 401 && canRefresh) {
        refrescar()
        return createSocialContent(input, canRefresh = false)
    }
    if (response.status.value == 401) throw ApiException.SesionExpirada()

    val body = response.bodyAsText()
    if (response.status.value !in 200..299) {
        val detail = runCatching {
            edecanJson.decodeFromString<ContentStudioErrorBody>(body).detail
        }.getOrNull() ?: "sin detalle"
        throw ApiException.Servidor(response.status.value, detail)
    }
    return runCatching { edecanJson.decodeFromString<SocialContentDraft>(body) }
        .getOrElse { throw ApiException.RespuestaInvalida() }
}
