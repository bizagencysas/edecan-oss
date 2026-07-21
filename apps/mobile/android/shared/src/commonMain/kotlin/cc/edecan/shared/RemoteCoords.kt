package cc.edecan.shared

import kotlin.math.roundToInt

/**
 * Mapea un toque sobre el visor de la pantalla remota a coordenadas REALES
 * de esa pantalla (`RemoteFrame.width`/`.height`) — puerto directo de
 * `apps/web/src/components/remoto/coords.ts` (WP-V4-10) a Kotlin puro, SIN
 * ninguna dependencia de Compose/Android, para poder testearlo en
 * `commonTest` igual que el resto de `shared` (`RemoteCoordsTest.kt`).
 *
 * El visor dibuja el frame con "contain" (mismo criterio que
 * `object-fit: contain` en CSS — `RemotoScreen`, `androidApp`, usa
 * `ContentScale.Fit` dentro de un contenedor de tamaño fijo): si la
 * proporción del frame no coincide con la del contenedor quedan franjas
 * vacías tipo "letterbox" arriba/abajo o a los lados, y un toque ahí NO debe
 * traducirse a ningún `input_pointer` (coordenadas inventadas).
 *
 * Diferencia deliberada con la versión web: `coords.ts` parte de
 * `clientX`/`clientY` (relativos a toda la ventana) y necesita restar
 * `getBoundingClientRect()` para volverlos locales al `<img>`. Compose ya
 * entrega el offset de un toque en el sistema de coordenadas LOCAL del nodo
 * que lo escucha (`Modifier.pointerInput`/`detectTapGestures`) — así que
 * este puerto recibe [pointX]/[pointY] ya locales al elemento, sin
 * `elementLeft`/`elementTop`.
 */

data class RemoteContainedRect(val left: Double, val top: Double, val width: Double, val height: Double)

data class RemotePoint(val x: Int, val y: Int)

/**
 * El rectángulo que de verdad ocupa la imagen dentro de su contenedor con
 * "contain" — puede ser más chico que el contenedor si las proporciones no
 * coinciden (de ahí las franjas vacías). Idéntico a `containedImageRect` en
 * `coords.ts`, con los mismos cuatro nombres de parámetro traducidos.
 */
fun remoteContainedImageRect(
    elementWidth: Double,
    elementHeight: Double,
    naturalWidth: Double,
    naturalHeight: Double,
): RemoteContainedRect {
    if (elementWidth <= 0 || elementHeight <= 0 || naturalWidth <= 0 || naturalHeight <= 0) {
        return RemoteContainedRect(0.0, 0.0, elementWidth, elementHeight)
    }

    val elementAspect = elementWidth / elementHeight
    val naturalAspect = naturalWidth / naturalHeight

    if (naturalAspect > elementAspect) {
        // La imagen llena el ancho completo; franjas vacías arriba/abajo.
        val width = elementWidth
        val height = width / naturalAspect
        return RemoteContainedRect(left = 0.0, top = (elementHeight - height) / 2, width = width, height = height)
    }

    // La imagen llena el alto completo; franjas vacías a los lados.
    val height = elementHeight
    val width = height * naturalAspect
    return RemoteContainedRect(left = (elementWidth - width) / 2, top = 0.0, width = width, height = height)
}

/**
 * [pointX]/[pointY] (offset LOCAL de un toque, ya relativo al elemento que lo
 * recibió) + el tamaño del elemento ([elementWidth]/[elementHeight]) + el
 * tamaño real del frame ([frameWidth]/[frameHeight]) -> coordenadas enteras
 * dentro de `[0, frameWidth) x [0, frameHeight)`, o `null` si el toque cayó
 * fuera del contenido real de la imagen (franja de letterbox) — el llamador
 * (`RemotoViewModel`) debe ignorarlo, NUNCA mandar un `input_pointer` con
 * coordenadas inventadas (mismo contrato que `mapClientPointToRemoteCoords`
 * en `coords.ts`).
 */
fun mapPointToRemoteCoords(
    pointX: Double,
    pointY: Double,
    elementWidth: Double,
    elementHeight: Double,
    frameWidth: Int,
    frameHeight: Int,
    originX: Int = 0,
    originY: Int = 0,
): RemotePoint? {
    if (frameWidth <= 0 || frameHeight <= 0) return null

    val contained = remoteContainedImageRect(elementWidth, elementHeight, frameWidth.toDouble(), frameHeight.toDouble())
    if (contained.width <= 0 || contained.height <= 0) return null

    val relX = pointX - contained.left
    val relY = pointY - contained.top
    if (relX < 0 || relY < 0 || relX > contained.width || relY > contained.height) {
        return null // cayó en la franja vacía del letterbox, no en la imagen real
    }

    val x = ((relX / contained.width) * frameWidth).roundToInt()
    val y = ((relY / contained.height) * frameHeight).roundToInt()
    return RemotePoint(
        x = x.coerceIn(0, frameWidth - 1) + originX,
        y = y.coerceIn(0, frameHeight - 1) + originY,
    )
}
