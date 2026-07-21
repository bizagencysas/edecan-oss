package cc.edecan.app

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.compose.LocalViewModelStoreOwner
import cc.edecan.app.nav.RootNav
import cc.edecan.app.ui.OnboardingScreen
import cc.edecan.app.ui.theme.EdecanTheme
import cc.edecan.app.vm.SessionViewModel
import cc.edecan.app.vm.SessionViewModelContainer
import cc.edecan.app.vm.SessionViewModelStoreOwner

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
fun App() {
    EdecanTheme {
        Surface(modifier = Modifier.fillMaxSize()) {
            val sessionViewModel: SessionViewModel = viewModel()
            val sessionViewModelContainer: SessionViewModelContainer = viewModel()
            val uiState by sessionViewModel.uiState.collectAsState()

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
                        RootNav(sessionKey = uiState.sessionGeneration)
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
