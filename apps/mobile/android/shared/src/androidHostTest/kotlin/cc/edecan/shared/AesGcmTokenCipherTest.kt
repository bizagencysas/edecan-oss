package cc.edecan.shared

import java.security.GeneralSecurityException
import javax.crypto.KeyGenerator
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertNotEquals

class AesGcmTokenCipherTest {
    private val key = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()

    @Test
    fun roundTripConservaTokenCompleto() {
        val token = "cabecera.payload.firma\ncon unicode áéí"

        val encrypted = AesGcmTokenCipher.encrypt(token, "access_token", key)

        assertEquals(token, AesGcmTokenCipher.decrypt(encrypted, "access_token", key))
    }

    @Test
    fun cadaCifradoUsaIvAleatorio() {
        val first = AesGcmTokenCipher.encrypt("mismo-token", "access_token", key)
        val second = AesGcmTokenCipher.encrypt("mismo-token", "access_token", key)

        assertNotEquals(first, second)
    }

    @Test
    fun associatedDataImpideIntercambiarTiposDeToken() {
        val encrypted = AesGcmTokenCipher.encrypt("secreto", "access_token", key)

        assertFailsWith<GeneralSecurityException> {
            AesGcmTokenCipher.decrypt(encrypted, "refresh_token", key)
        }
    }

    @Test
    fun payloadCorruptoSeRechaza() {
        assertFailsWith<IllegalArgumentException> {
            EncryptedTokenPayloadCodec.decode("v1:no-es-base64:tampoco")
        }
    }

    @Test
    fun servidorIdYTokenDurableUsanCifradoSeparadoPorTipo() {
        val values = mapOf(
            "server_url" to "https://edecan.example.com",
            "device_id" to "device-123",
            "device_token" to "durable-super-secret",
        )

        val encrypted = values.mapValues { (name, value) ->
            AesGcmTokenCipher.encrypt(value, name, key)
        }

        values.forEach { (name, value) ->
            assertNotEquals(value, encrypted.getValue(name))
            assertEquals(value, AesGcmTokenCipher.decrypt(encrypted.getValue(name), name, key))
        }
        assertFailsWith<GeneralSecurityException> {
            AesGcmTokenCipher.decrypt(encrypted.getValue("device_token"), "device_id", key)
        }
    }
}
