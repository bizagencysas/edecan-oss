package cc.edecan.app.ui

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import java.io.ByteArrayOutputStream
import java.io.File
import kotlin.math.max
import kotlin.math.roundToInt

/** Calcula un `inSampleSize` potencia de dos para no expandir una foto grande
 * completa en memoria. Se mantiene pura para poder verificarla sin Android. */
internal fun calcularMuestraImagen(width: Int, height: Int, maxEdge: Int): Int {
    if (width <= 0 || height <= 0 || maxEdge <= 0) return 1
    val longestEdge = max(width, height)
    var sample = 1
    while (longestEdge / sample > maxEdge && sample <= Int.MAX_VALUE / 2) {
        sample *= 2
    }
    return sample
}

/** Decodifica una imagen privada con un límite de dimensión predecible. */
internal fun decodificarImagenAcotada(
    bytes: ByteArray,
    maxEdge: Int = 2048,
): ImageBitmap? {
    if (bytes.isEmpty()) return null
    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
    val sample = calcularMuestraImagen(bounds.outWidth, bounds.outHeight, maxEdge)
    return BitmapFactory.decodeByteArray(
        bytes,
        0,
        bytes.size,
        BitmapFactory.Options().apply { inSampleSize = sample },
    )?.asImageBitmap()
}

/** Conserva solo una miniatura pequeña para el composer. El archivo original
 * continúa en disco hasta terminar la subida y nunca se duplica en el heap. */
internal fun crearMiniaturaCodificada(
    file: File,
    mime: String,
    maxEdge: Int = 420,
): ByteArray? {
    if (!mime.startsWith("image/", ignoreCase = true) || !file.isFile) return null
    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeFile(file.absolutePath, bounds)
    val sample = calcularMuestraImagen(bounds.outWidth, bounds.outHeight, maxEdge)
    val decoded = BitmapFactory.decodeFile(
        file.absolutePath,
        BitmapFactory.Options().apply { inSampleSize = sample },
    ) ?: return null

    val longestEdge = max(decoded.width, decoded.height)
    val preview = if (longestEdge > maxEdge) {
        val scale = maxEdge.toFloat() / longestEdge.toFloat()
        Bitmap.createScaledBitmap(
            decoded,
            (decoded.width * scale).roundToInt().coerceAtLeast(1),
            (decoded.height * scale).roundToInt().coerceAtLeast(1),
            true,
        )
    } else {
        decoded
    }
    return try {
        ByteArrayOutputStream().use { output ->
            if (!preview.compress(Bitmap.CompressFormat.PNG, 100, output)) null
            else output.toByteArray()
        }
    } finally {
        if (preview !== decoded) preview.recycle()
        decoded.recycle()
    }
}
