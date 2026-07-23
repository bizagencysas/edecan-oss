package cc.edecan.app.updates

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

class AndroidUpdateManifestTest {
    @Test
    fun parsesStableManifest() {
        val parsed = AndroidUpdateManifestParser.parse(validManifest())

        assertEquals("stable", parsed.channel)
        assertEquals(9, parsed.versionCode)
        assertEquals("0.7.4", parsed.versionName)
        assertEquals(12_345_678L, parsed.apkSizeBytes)
        assertEquals("a".repeat(64), parsed.apkSha256)
    }

    @Test
    fun rejectsPlainHttpForManifestAndApk() {
        assertFailsWith<IllegalArgumentException> {
            AndroidUpdateManifestParser.requireHttps("http://updates.example/estable.json")
        }
        assertFailsWith<IllegalArgumentException> {
            AndroidUpdateManifestParser.parse(
                validManifest().replace(
                    "https://github.com/example/edecan.apk",
                    "http://github.com/example/edecan.apk",
                ),
            )
        }
    }

    @Test
    fun rejectsInvalidHashAndOversizedPayload() {
        assertFailsWith<IllegalArgumentException> {
            AndroidUpdateManifestParser.parse(
                validManifest().replace("a".repeat(64), "not-a-hash"),
            )
        }
        assertFailsWith<IllegalArgumentException> {
            AndroidUpdateManifestParser.parse(
                validManifest().replace(
                    "\"size_bytes\": 12345678",
                    "\"size_bytes\": ${MAX_ANDROID_UPDATE_BYTES + 1}",
                ),
            )
        }
    }

    @Test
    fun rejectsUnknownSchemaOrChannel() {
        val schemaFailure = assertFailsWith<IllegalArgumentException> {
            AndroidUpdateManifestParser.parse(
                validManifest().replace("\"schema_version\": 1", "\"schema_version\": 2"),
            )
        }
        assertTrue(schemaFailure.message.orEmpty().contains("no compatible"))

        assertFailsWith<IllegalArgumentException> {
            AndroidUpdateManifestParser.parse(
                validManifest().replace("\"channel\": \"stable\"", "\"channel\": \"nightly\""),
            )
        }
    }

    private fun validManifest(): String =
        """
        {
          "schema_version": 1,
          "channel": "stable",
          "version_code": 9,
          "version_name": "0.7.4",
          "published_at": "2026-07-23T12:00:00Z",
          "release_notes": "Chat más resistente.",
          "apk": {
            "url": "https://github.com/example/edecan.apk",
            "sha256": "${"a".repeat(64)}",
            "size_bytes": 12345678
          }
        }
        """.trimIndent()
}
