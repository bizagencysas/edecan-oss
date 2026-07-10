package cc.edecan.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent

/**
 * Punto de entrada de la app — una sola Activity, sin fragments ni
 * `androidx.navigation` (ver `nav/RootNav.kt` para por qué). Todo el árbol
 * de UI es Compose desde acá para abajo, arrancando en [App].
 */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { App() }
    }
}
