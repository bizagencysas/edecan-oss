package cc.edecan.app.vm

import androidx.lifecycle.SavedStateHandle
import cc.edecan.shared.EdecanApi
import cc.edecan.shared.TokenStore
import cc.edecan.shared.UploadedFile
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.withContext
import java.io.File
import kotlin.test.Test
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class ChatAttachmentUploadRaceTest {
    @OptIn(ExperimentalCoroutinesApi::class)
    @Test
    fun quitarCancelaLaSubidaYUnResultadoTardioNoResucitaElAdjunto() = runTest {
        val dispatcher = StandardTestDispatcher(testScheduler)
        Dispatchers.setMain(dispatcher)
        val staged = File.createTempFile("edecan-upload-race-", ".tmp").apply { writeText("contenido") }
        val started = CompletableDeferred<Unit>()
        val cancelled = CompletableDeferred<Unit>()
        val releaseLateResult = CompletableDeferred<Unit>()
        val uploader = AdjuntoUploader { _, _ ->
            started.complete(Unit)
            try {
                releaseLateResult.await()
            } catch (_: CancellationException) {
                cancelled.complete(Unit)
                withContext(NonCancellable) { releaseLateResult.await() }
            }
            UploadedFile("file-tardio", "doc.txt", "text/plain", 9)
        }
        val viewModel = ChatViewModel(SavedStateHandle(), uploader)
        val api = EdecanApi("https://edecan.test", EmptyTokenStore())

        try {
            viewModel.subirAdjunto(ArchivoSubidaLocal(staged, "doc.txt", "text/plain"), api)
            runCurrent()
            started.await()
            val localId = viewModel.uiState.value.adjuntosComposer.single().localId

            viewModel.quitarAdjunto(localId)
            runCurrent()
            cancelled.await()
            assertTrue(viewModel.uiState.value.adjuntosComposer.isEmpty())
            assertFalse(staged.exists())

            releaseLateResult.complete(Unit)
            advanceUntilIdle()

            assertTrue(viewModel.uiState.value.adjuntosComposer.isEmpty())
        } finally {
            staged.delete()
            Dispatchers.resetMain()
        }
    }
}

private class EmptyTokenStore : TokenStore {
    override suspend fun getServerUrl(): String? = null
    override suspend fun saveServerUrl(url: String) = Unit
    override suspend fun getAccessToken(): String? = null
    override suspend fun getRefreshToken(): String? = null
    override suspend fun saveTokens(accessToken: String, refreshToken: String) = Unit
    override suspend fun clearTokens() = Unit
    override suspend fun getDeviceId(): String? = null
    override suspend fun saveDeviceId(deviceId: String) = Unit
    override suspend fun clearDeviceId() = Unit
}
