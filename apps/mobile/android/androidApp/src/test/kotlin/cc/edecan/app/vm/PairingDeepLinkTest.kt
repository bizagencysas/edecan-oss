package cc.edecan.app.vm

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class PairingDeepLinkTest {
    @Test
    fun httpAceptaIpLanDelPixelYRechazaHostPublico() {
        assertEquals(
            "http://192.168.58.105:9876",
            validarUrlServidor("http://192.168.58.105:9876/"),
        )
        assertNull(validarUrlServidor("http://evil.example"))
    }

    @Test
    fun politicaAceptaLocalesYHttpsPublico() {
        assertEquals("http://localhost:9876", validarUrlServidor("http://localhost:9876"))
        assertEquals("http://MacBook-Isacc:9876", validarUrlServidor("http://MacBook-Isacc:9876"))
        assertEquals("http://macbook-isacc.local:9876", validarUrlServidor("http://macbook-isacc.local:9876"))
        assertEquals("http://[::1]:9876", validarUrlServidor("http://[::1]:9876"))
        assertEquals("http://[fd12::10]:9876", validarUrlServidor("http://[fd12::10]:9876"))
        assertEquals("https://api.example.com", validarUrlServidor("https://api.example.com"))
    }

    @Test
    fun politicaRechazaCredencialesQueryFragmentoYHttpPublico() {
        assertNull(validarUrlServidor("http://user:pass@192.168.58.105:9876"))
        assertNull(validarUrlServidor("https://user:pass@api.example.com"))
        assertNull(validarUrlServidor("http://192.168.58.105:9876?token=x"))
        assertNull(validarUrlServidor("https://api.example.com?tenant=x"))
        assertNull(validarUrlServidor("http://192.168.58.105:9876#fragment"))
        assertNull(validarUrlServidor("http://8.8.8.8:9876"))
    }

    @Test
    fun parseaServidorYTokenOpacoCodificados() {
        val result = parsearEnlaceEmparejamiento(
            "edecan://pair?server=https%3A%2F%2Fedecan.example.com%2Fapi%2F&token=opaque%2Btoken%2F%3D",
        )

        assertEquals("https://edecan.example.com/api", result?.serverUrl)
        assertEquals("opaque+token/=", result?.pairingToken)

        val lan = parsearEnlaceEmparejamiento(
            "edecan://pair?server=http%3A%2F%2F192.168.58.105%3A9876&token=local-once",
        )
        assertEquals("http://192.168.58.105:9876", lan?.serverUrl)
    }

    @Test
    fun rechazaEsquemaHostParametrosDuplicadosYServidorConCredenciales() {
        assertNull(parsearEnlaceEmparejamiento("https://pair?server=https%3A%2F%2Fx.test&token=t"))
        assertNull(parsearEnlaceEmparejamiento("edecan://otro?server=https%3A%2F%2Fx.test&token=t"))
        assertNull(
            parsearEnlaceEmparejamiento(
                "edecan://pair?server=https%3A%2F%2Fx.test&token=uno&token=dos",
            ),
        )
        assertNull(
            parsearEnlaceEmparejamiento(
                "edecan://pair?server=https%3A%2F%2Fuser%3Apass%40x.test&token=t",
            ),
        )
    }

    @Test
    fun solicitudNoExponeElTokenEnToString() {
        val result = parsearEnlaceEmparejamiento(
            "edecan://pair?server=https%3A%2F%2Fx.test&token=secreto-que-no-debe-loguearse",
        )

        kotlin.test.assertFalse(result.toString().contains("secreto-que-no-debe-loguearse"))
    }
}
