package cc.edecan.app

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue

/**
 * Punto de entrada de la app — una sola Activity, sin fragments ni
 * `androidx.navigation` (ver `nav/RootNav.kt` para por qué). Todo el árbol
 * de UI es Compose desde acá para abajo, arrancando en [App].
 */
class MainActivity : ComponentActivity() {
    private var pairingDeepLink by mutableStateOf<String?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        pairingDeepLink = extraerYSanitizar(intent)
        setContent {
            App(
                pairingDeepLink = pairingDeepLink,
                onPairingDeepLinkConsumed = { pairingDeepLink = null },
            )
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        pairingDeepLink = extraerYSanitizar(intent)
    }

    /** No conserva el token de un solo uso en `Activity.intent` después de
     * entregarlo a Compose. */
    private fun extraerYSanitizar(incoming: Intent?): String? {
        val raw = incoming?.takeIf { it.action == Intent.ACTION_VIEW }?.dataString
        if (raw != null) {
            setIntent(Intent(this, MainActivity::class.java).setAction(Intent.ACTION_MAIN))
        } else if (incoming != null) {
            setIntent(incoming)
        }
        return raw
    }
}
