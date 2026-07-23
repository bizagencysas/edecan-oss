package cc.edecan.app

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.remember
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.compose.LocalViewModelStoreOwner
import cc.edecan.app.nav.RootNav
import cc.edecan.app.ui.OnboardingScreen
import cc.edecan.app.ui.theme.EdecanTheme
import cc.edecan.app.vm.SessionViewModel
import cc.edecan.app.vm.SessionViewModelContainer
import cc.edecan.app.vm.SessionViewModelStoreOwner
import cc.edecan.app.notifications.EdecanNotifications
import cc.edecan.app.notifications.NotificationRoute
import androidx.compose.ui.platform.LocalContext
import cc.edecan.app.updates.AndroidUpdateManager
import kotlinx.coroutines.launch

/**
 * Punto de entrada composable de la app. Si el dispositivo no está
 * emparejado (`SessionUiState.isPaired == false` — ver el docstring de
 * `TokenStore`, en `shared`, para qué significa "emparejado" en este v1)
 * muestra [OnboardingScreen]; si ya lo está, va directo a [RootNav]. Mismo
 * criterio que `RaizDeLaApp` en `EdecanApp.swift` (iOS).
 *
 * SessionViewModel vive en la Activity; todos los ViewModels con datos del
 * usuario viven en un store hijo por sesión. Cerrar/cambiar cuenta limpia
 * ese store completo, mientras una rotación conserva la sesión actual.
 */
@Composable
fun App(
    pairingDeepLink: String? = null,
    onPairingDeepLinkConsumed: () -> Unit = {},
    notificationRoute: NotificationRoute? = null,
    onNotificationRouteConsumed: () -> Unit = {},
) {
    EdecanTheme {
        Surface(modifier = Modifier.fillMaxSize()) {
            val sessionViewModel: SessionViewModel = viewModel()
            val sessionViewModelContainer: SessionViewModelContainer = viewModel()
            val uiState by sessionViewModel.uiState.collectAsState()
            val context = LocalContext.current
            val lifecycleOwner = LocalLifecycleOwner.current
            val updateScope = rememberCoroutineScope()

            LaunchedEffect(Unit) { EdecanNotifications.initialize(context) }
            LaunchedEffect(uiState.isPaired, uiState.sessionGeneration) {
                if (uiState.isPaired) EdecanNotifications.refreshRemoteRegistration(context)
            }
            // Comprobación silenciosa al abrir y al volver al primer plano.
            // El manager aplica un intervalo de cuatro horas y un fallo aquí
            // nunca altera el chat ni la sesión con el computador.
            DisposableEffect(lifecycleOwner, context) {
                val observer = LifecycleEventObserver { _, event ->
                    if (event == Lifecycle.Event.ON_RESUME) {
                        updateScope.launch {
                            AndroidUpdateManager.check(context, force = false)
                        }
                    }
                }
                lifecycleOwner.lifecycle.addObserver(observer)
                onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
            }

            LaunchedEffect(pairingDeepLink) {
                pairingDeepLink?.let { raw ->
                    // Quita el secreto del estado de Activity antes de abrir
                    // red; SessionViewModel solo lo conserva en la coroutine.
                    onPairingDeepLinkConsumed()
                    sessionViewModel.procesarEnlaceEmparejamiento(raw)
                }
            }

            when {
                uiState.cargandoInicial -> Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator()
                }
                uiState.isPaired -> {
                    val parentOwner = checkNotNull(LocalViewModelStoreOwner.current)
                    val sessionStore = sessionViewModelContainer.storeFor(uiState.sessionGeneration)
                    val sessionOwner = remember(sessionStore, parentOwner) {
                        SessionViewModelStoreOwner(sessionStore, parentOwner)
                    }
                    CompositionLocalProvider(
                        LocalViewModelStoreOwner provides sessionOwner,
                    ) {
                        RootNav(
                            sessionKey = uiState.sessionGeneration,
                            notificationRoute = notificationRoute,
                            onNotificationRouteConsumed = onNotificationRouteConsumed,
                        )
                    }
                }
                else -> {
                    // Síncrono e idempotente: antes de mostrar login ya no
                    // queda ningún ViewModel ni dato de la cuenta anterior.
                    sessionViewModelContainer.clearSession()
                    OnboardingScreen(sessionViewModel)
                }
            }
        }
    }
}
