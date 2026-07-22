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
private data class ContentStudioErrorBody(val detail: String? = null)

/** Crea un borrador y sus artefactos privados. El request puede incluir una
 * imagen generada, por eso amplía solo aquí el timeout REST normal. */
suspend fun EdecanApi.createSocialContent(input: SocialContentRequest): SocialContentDraft =
    createSocialContent(input, canRefresh = true)

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
