package cc.edecan.app.ui

import android.annotation.SuppressLint
import android.content.ClipData
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.pdf.PdfRenderer
import android.os.ParcelFileDescriptor
import android.webkit.CookieManager
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.core.content.FileProvider
import cc.edecan.shared.ArtifactRef
import cc.edecan.shared.DownloadedArtifact
import cc.edecan.shared.EdecanApi
import java.io.File
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

sealed interface SecurePreviewTarget {
    data class Artifact(val value: ArtifactRef) : SecurePreviewTarget
    data class PublicUrl(val value: String) : SecurePreviewTarget
}

@Composable
fun SecurePreviewDialog(target: SecurePreviewTarget, api: EdecanApi?, onDismiss: () -> Unit) {
    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false),
    ) {
        Surface(modifier = Modifier.fillMaxSize()) {
            Column {
                Row(
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        when (target) {
                            is SecurePreviewTarget.Artifact -> target.value.filename
                            is SecurePreviewTarget.PublicUrl -> "Vista segura"
                        },
                        style = MaterialTheme.typography.titleMedium,
                        modifier = Modifier.weight(1f),
                        maxLines = 1,
                    )
                    TextButton(onClick = onDismiss) { Text("Cerrar") }
                }
                when (target) {
                    is SecurePreviewTarget.Artifact -> ArtifactPreview(target.value, api)
                    is SecurePreviewTarget.PublicUrl -> SafeWebPreview(target.value)
                }
            }
        }
    }
}

@Composable
private fun ArtifactPreview(artifact: ArtifactRef, api: EdecanApi?) {
    val context = LocalContext.current
    var download by remember(artifact.fileId) { mutableStateOf<DownloadedArtifact?>(null) }
    var error by remember(artifact.fileId) { mutableStateOf<String?>(null) }

    LaunchedEffect(artifact.fileId, api) {
        if (api == null) {
            error = "No hay una sesión activa."
            return@LaunchedEffect
        }
        try {
            download = api.downloadArtifact(artifact)
        } catch (failure: Throwable) {
            error = failure.message ?: "No se pudo descargar el archivo."
        }
    }

    val current = download
    when {
        error != null -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            Text(error.orEmpty(), color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(24.dp))
        }
        current == null -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            CircularProgressIndicator()
        }
        else -> {
            Column(modifier = Modifier.fillMaxSize()) {
                Box(modifier = Modifier.weight(1f).fillMaxWidth()) {
                    ArtifactBody(current)
                }
                Button(
                    onClick = { shareArtifact(context, current) },
                    modifier = Modifier.fillMaxWidth().padding(12.dp),
                ) { Text("Compartir o guardar") }
            }
        }
    }
}

@Composable
private fun ArtifactBody(download: DownloadedArtifact) {
    val artifact = download.artifact
    val mime = artifact.mime?.lowercase().orEmpty()
    when {
        mime.startsWith("image/") -> {
            val bitmap = remember(download.bytes) { BitmapFactory.decodeByteArray(download.bytes, 0, download.bytes.size) }
            if (bitmap != null) {
                Image(
                    bitmap = bitmap.asImageBitmap(),
                    contentDescription = artifact.filename,
                    contentScale = ContentScale.Fit,
                    modifier = Modifier.fillMaxSize().padding(12.dp),
                )
            } else PreviewUnavailable()
        }
        mime == "application/pdf" || artifact.filename.lowercase().endsWith(".pdf") -> PdfPreview(download)
        isHtmlArtifact(mime, artifact.filename) -> LocalHtmlPreview(download.bytes)
        isTextArtifact(mime, artifact.filename) -> Text(
            text = download.bytes.copyOfRange(0, minOf(download.bytes.size, 2_000_000)).toString(Charsets.UTF_8),
            fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace,
            modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        )
        else -> PreviewUnavailable()
    }
}

@SuppressLint("SetJavaScriptEnabled")
@Composable
private fun LocalHtmlPreview(bytes: ByteArray) {
    val html = remember(bytes) {
        bytes.copyOfRange(0, minOf(bytes.size, 8_000_000)).toString(Charsets.UTF_8)
    }
    AndroidView(
        factory = { context ->
            CookieManager.getInstance().setAcceptCookie(false)
            WebView(context).apply {
                settings.javaScriptEnabled = false
                settings.domStorageEnabled = false
                settings.allowFileAccess = false
                settings.allowContentAccess = false
                settings.blockNetworkLoads = true
                settings.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
                webViewClient = object : WebViewClient() {
                    override fun shouldOverrideUrlLoading(
                        view: WebView,
                        request: WebResourceRequest,
                    ): Boolean = true

                    override fun shouldInterceptRequest(
                        view: WebView,
                        request: WebResourceRequest,
                    ): WebResourceResponse? {
                        val scheme = request.url.scheme?.lowercase()
                        if (scheme == "about" || scheme == "data") return null
                        return WebResourceResponse(
                            "text/plain",
                            "UTF-8",
                            403,
                            "Blocked",
                            emptyMap(),
                            null,
                        )
                    }
                }
                tag = html.hashCode()
                loadDataWithBaseURL(null, html, "text/html", "UTF-8", null)
            }
        },
        update = { view ->
            val revision = html.hashCode()
            if (view.tag != revision) {
                view.tag = revision
                view.loadDataWithBaseURL(null, html, "text/html", "UTF-8", null)
            }
        },
        modifier = Modifier.fillMaxSize(),
        onRelease = { webView ->
            webView.stopLoading()
            webView.loadUrl("about:blank")
            webView.clearHistory()
            webView.clearCache(true)
            webView.destroy()
        },
    )
}

@Composable
private fun PreviewUnavailable() {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Text("Este tipo no tiene vista previa interna. Puedes guardarlo o compartirlo.", modifier = Modifier.padding(24.dp))
    }
}

@Composable
private fun PdfPreview(download: DownloadedArtifact) {
    val context = LocalContext.current
    var pages by remember(download.artifact.fileId) { mutableStateOf<List<Bitmap>>(emptyList()) }
    var tempFile by remember(download.artifact.fileId) { mutableStateOf<File?>(null) }

    LaunchedEffect(download.artifact.fileId) {
        val rendered = withContext(Dispatchers.IO) {
            val file = File.createTempFile("edecan-preview-", ".pdf", context.cacheDir)
            file.writeBytes(download.bytes)
            val bitmaps = mutableListOf<Bitmap>()
            ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY).use { descriptor ->
                PdfRenderer(descriptor).use { renderer ->
                    repeat(minOf(renderer.pageCount, 20)) { index ->
                        renderer.openPage(index).use { page ->
                            val width = 1_200
                            val height = (width.toFloat() / page.width * page.height).toInt().coerceAtLeast(1)
                            Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888).also { bitmap ->
                                bitmap.eraseColor(android.graphics.Color.WHITE)
                                page.render(bitmap, null, null, PdfRenderer.Page.RENDER_MODE_FOR_DISPLAY)
                                bitmaps += bitmap
                            }
                        }
                    }
                }
            }
            file to bitmaps
        }
        tempFile = rendered.first
        pages = rendered.second
    }
    DisposableEffect(download.artifact.fileId) {
        onDispose {
            pages.forEach(Bitmap::recycle)
            tempFile?.delete()
        }
    }
    if (pages.isEmpty()) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
    } else {
        LazyColumn(modifier = Modifier.fillMaxSize(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            itemsIndexed(pages) { index, bitmap ->
                Image(
                    bitmap.asImageBitmap(),
                    contentDescription = "Página ${index + 1}",
                    contentScale = ContentScale.FillWidth,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        }
    }
}

@SuppressLint("SetJavaScriptEnabled")
@Composable
private fun SafeWebPreview(rawUrl: String) {
    if (!esUrlPublicaSegura(rawUrl)) {
        PreviewUnavailable()
        return
    }
    AndroidView(
        factory = { context ->
            CookieManager.getInstance().setAcceptCookie(false)
            WebView(context).apply {
                settings.javaScriptEnabled = false
                settings.domStorageEnabled = false
                settings.allowFileAccess = false
                settings.allowContentAccess = false
                settings.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
                webViewClient = object : WebViewClient() {
                    override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                        return if (esUrlPublicaSegura(request.url.toString())) false else true
                    }

                    override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? {
                        if (esUrlPublicaSegura(request.url.toString())) return null
                        return WebResourceResponse("text/plain", "UTF-8", 403, "Blocked", emptyMap(), null)
                    }
                }
                loadUrl(rawUrl)
            }
        },
        update = {},
        modifier = Modifier.fillMaxSize(),
        onRelease = { webView ->
            webView.stopLoading()
            webView.clearHistory()
            webView.clearCache(true)
            webView.destroy()
        },
    )
}

private fun isTextArtifact(mime: String, filename: String): Boolean =
    mime.startsWith("text/") || mime == "application/json" || mime.endsWith("+json") ||
        mime == "application/xml" || mime.endsWith("+xml") ||
        listOf(".md", ".txt", ".csv", ".json", ".xml", ".yaml", ".yml").any(filename.lowercase()::endsWith)

private fun isHtmlArtifact(mime: String, filename: String): Boolean =
    mime == "text/html" || mime == "application/xhtml+xml" ||
        listOf(".html", ".htm").any(filename.lowercase()::endsWith)

private fun shareArtifact(context: Context, download: DownloadedArtifact) {
    val safeName = download.artifact.filename.replace('\\', '-').replace('/', '-').take(180).ifBlank { "archivo" }
    val directory = File(context.cacheDir, "shared_artifacts/${download.artifact.fileId.filter(Char::isLetterOrDigit)}").apply { mkdirs() }
    val file = File(directory, safeName).apply { writeBytes(download.bytes) }
    val uri = FileProvider.getUriForFile(context, "${context.packageName}.files", file)
    val intent = Intent(Intent.ACTION_SEND).apply {
        type = download.artifact.mime ?: "*/*"
        putExtra(Intent.EXTRA_STREAM, uri)
        clipData = ClipData.newRawUri(safeName, uri)
        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    }
    context.startActivity(Intent.createChooser(intent, "Compartir $safeName"))
}
