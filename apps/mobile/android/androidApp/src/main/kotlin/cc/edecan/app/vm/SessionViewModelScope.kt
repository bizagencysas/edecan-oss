package cc.edecan.app.vm

import androidx.lifecycle.HasDefaultViewModelProviderFactory
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.ViewModelStore
import androidx.lifecycle.ViewModelStoreOwner
import androidx.lifecycle.viewmodel.CreationExtras

/** Contenedor Activity-scoped que posee UN store hijo por sesión autenticada.
 * Sobrevive rotaciones, pero reemplaza y limpia el store completo cuando
 * cambia [sessionKey] o se cierra sesión. */
internal class SessionViewModelContainer : ViewModel() {
    private var activeSessionKey: Long? = null
    private var sessionStore = ViewModelStore()

    fun storeFor(sessionKey: Long): ViewModelStore {
        if (activeSessionKey != sessionKey) {
            sessionStore.clear()
            sessionStore = ViewModelStore()
            activeSessionKey = sessionKey
        }
        return sessionStore
    }

    fun clearSession() {
        if (activeSessionKey == null) return
        sessionStore.clear()
        sessionStore = ViewModelStore()
        activeSessionKey = null
    }

    override fun onCleared() {
        sessionStore.clear()
    }
}

/** Hace que todos los `viewModel()` bajo `RootNav` usen el store de sesión,
 * conservando la factory/extras del Activity para AndroidViewModel y
 * SavedStateHandle. */
internal class SessionViewModelStoreOwner(
    override val viewModelStore: ViewModelStore,
    parent: ViewModelStoreOwner,
) : ViewModelStoreOwner, HasDefaultViewModelProviderFactory {
    private val parentDefaults = parent as? HasDefaultViewModelProviderFactory

    override val defaultViewModelProviderFactory: ViewModelProvider.Factory =
        parentDefaults?.defaultViewModelProviderFactory ?: ViewModelProvider.NewInstanceFactory()

    override val defaultViewModelCreationExtras: CreationExtras =
        parentDefaults?.defaultViewModelCreationExtras ?: CreationExtras.Empty
}
