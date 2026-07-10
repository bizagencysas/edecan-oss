package cc.edecan.app

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.lifecycle.viewmodel.compose.viewModel
import cc.edecan.app.nav.RootNav
import cc.edecan.app.ui.OnboardingScreen
import cc.edecan.app.ui.theme.EdecanTheme
import cc.edecan.app.vm.SessionViewModel

/**
 * Punto de entrada composable de la app. Si el dispositivo no está
 * emparejado (`SessionUiState.isPaired == false` — ver el docstring de
 * `TokenStore`, en `shared`, para qué significa "emparejado" en este v1)
 * muestra [OnboardingScreen]; si ya lo está, va directo a [RootNav]. Mismo
 * criterio que `RaizDeLaApp` en `EdecanApp.swift` (iOS).
 *
 * `sessionViewModel` se resuelve UNA vez aquí vía `viewModel()` — Compose
 * cachea las instancias de `ViewModel` por clase en el `ViewModelStore` del
 * `ViewModelStoreOwner` más cercano (la `Activity`, sin
 * `androidx.navigation` de por medio en este esqueleto), así que cada
 * pantalla que también pide `SessionViewModel` con `= viewModel()` como
 * valor por defecto (`InicioScreen`, `ChatScreen`, `PerfilScreen`,
 * `OnboardingScreen`) recibe automáticamente esta MISMA instancia — el
 * equivalente práctico a inyectar `@Environment(SessionStore.self)` una
 * vez en la raíz de `EdecanApp.swift` (iOS).
 */
@Composable
fun App() {
    EdecanTheme {
        Surface(modifier = Modifier.fillMaxSize()) {
            val sessionViewModel: SessionViewModel = viewModel()
            val uiState by sessionViewModel.uiState.collectAsState()

            when {
                uiState.cargandoInicial -> Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator()
                }
                uiState.isPaired -> RootNav()
                else -> OnboardingScreen(sessionViewModel)
            }
        }
    }
}
