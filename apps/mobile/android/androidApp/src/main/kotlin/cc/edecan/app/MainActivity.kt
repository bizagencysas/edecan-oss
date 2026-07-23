package cc.edecan.app

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import cc.edecan.app.notifications.EXTRA_NOTIFICATION_ROUTE
import cc.edecan.app.notifications.NotificationRoute

/**
 * Punto de entrada de la app — una sola Activity, sin fragments ni
 * `androidx.navigation` (ver `nav/RootNav.kt` para por qué). Todo el árbol
 * de UI es Compose desde acá para abajo, arrancando en [App].
 */
class MainActivity : ComponentActivity() {
    private var pairingDeepLink by mutableStateOf<String?>(null)
    private var notificationRoute by mutableStateOf<NotificationRoute?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        pairingDeepLink = extraerYSanitizar(intent)
        notificationRoute = extraerRutaNotificacion(intent)
        setContent {
            App(
                pairingDeepLink = pairingDeepLink,
                onPairingDeepLinkConsumed = { pairingDeepLink = null },
                notificationRoute = notificationRoute,
                onNotificationRouteConsumed = { notificationRoute = null },
            )
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        pairingDeepLink = extraerYSanitizar(intent)
        notificationRoute = extraerRutaNotificacion(intent)
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

    private fun extraerRutaNotificacion(incoming: Intent?): NotificationRoute? =
        (incoming?.getStringExtra(EXTRA_NOTIFICATION_ROUTE)
            ?: incoming?.getStringExtra("route")
            ?: incoming?.getStringExtra("screen"))
            ?.let(NotificationRoute::parse)
}
