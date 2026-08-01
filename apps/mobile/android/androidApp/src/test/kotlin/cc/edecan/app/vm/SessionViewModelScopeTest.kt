package cc.edecan.app.vm

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.ViewModelStore
import androidx.lifecycle.ViewModelStoreOwner
import kotlin.test.Test
import kotlin.test.assertNotSame
import kotlin.test.assertNull
import kotlin.test.assertSame
import kotlin.test.assertTrue

class SessionViewModelScopeTest {
    @Test
    fun cambiarSesionDestruyeViewModelsAnterioresYEntregaEstadoNuevo() {
        val parent = object : ViewModelStoreOwner {
            override val viewModelStore = ViewModelStore()
        }
        val container = SessionViewModelContainer()
        val factory = object : ViewModelProvider.Factory {
            @Suppress("UNCHECKED_CAST")
            override fun <T : ViewModel> create(modelClass: Class<T>): T = SensitiveViewModel() as T
        }

        val firstOwner = SessionViewModelStoreOwner(container.storeFor(10), parent)
        val old = ViewModelProvider(firstOwner, factory)[SensitiveViewModel::class.java]
        old.secret = "conversación de cuenta A"
        assertSame(old, ViewModelProvider(firstOwner, factory)[SensitiveViewModel::class.java])

        val secondOwner = SessionViewModelStoreOwner(container.storeFor(11), parent)
        val fresh = ViewModelProvider(secondOwner, factory)[SensitiveViewModel::class.java]

        assertTrue(old.cleared)
        assertNotSame(old, fresh)
        assertNull(fresh.secret)

        container.clearSession()
        assertTrue(fresh.cleared)
    }
}

private class SensitiveViewModel : ViewModel() {
    var secret: String? = null
    var cleared: Boolean = false

    override fun onCleared() {
        secret = null
        cleared = true
    }
}
