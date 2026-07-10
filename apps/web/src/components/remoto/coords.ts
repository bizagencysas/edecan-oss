/**
 * Mapea un clic sobre el `<img>` del visor (que usa `object-contain`, así
 * que puede tener franjas vacías tipo "letterbox" si la proporción del
 * frame no coincide con la del elemento) a coordenadas REALES de la pantalla
 * remota (`frame.width`/`frame.height`) — WP-V4-10, control remoto fase 2.
 *
 * `null` cuando el clic cayó en una franja vacía (fuera del contenido real
 * de la imagen): el llamador debe ignorarlo, nunca mandar un `input_pointer`
 * con coordenadas inventadas.
 */

export interface RemoteFrameSize {
  width: number;
  height: number;
}

export interface ContainedRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** El rectángulo que de verdad ocupa la imagen dentro de su elemento con
 * `object-fit: contain` — puede ser más chico que el elemento si las
 * proporciones no coinciden (de ahí las franjas vacías arriba/abajo o a los
 * lados). */
export function containedImageRect(
  elementWidth: number,
  elementHeight: number,
  naturalWidth: number,
  naturalHeight: number,
): ContainedRect {
  if (elementWidth <= 0 || elementHeight <= 0 || naturalWidth <= 0 || naturalHeight <= 0) {
    return { left: 0, top: 0, width: elementWidth, height: elementHeight };
  }

  const elementAspect = elementWidth / elementHeight;
  const naturalAspect = naturalWidth / naturalHeight;

  if (naturalAspect > elementAspect) {
    // La imagen llena el ancho completo; franjas vacías arriba/abajo.
    const width = elementWidth;
    const height = width / naturalAspect;
    return { left: 0, top: (elementHeight - height) / 2, width, height };
  }

  // La imagen llena el alto completo; franjas vacías a los lados.
  const height = elementHeight;
  const width = height * naturalAspect;
  return { left: (elementWidth - width) / 2, top: 0, width, height };
}

/**
 * `clientX`/`clientY` (de un evento de mouse) + el `getBoundingClientRect()`
 * del `<img>` + el tamaño real del frame → coordenadas enteras dentro de
 * `[0, frame.width) x [0, frame.height)`, o `null` si el clic cayó fuera del
 * contenido real de la imagen (franja de letterbox).
 */
export function mapClientPointToRemoteCoords(
  clientX: number,
  clientY: number,
  elementRect: { left: number; top: number; width: number; height: number },
  frame: RemoteFrameSize,
): { x: number; y: number } | null {
  if (frame.width <= 0 || frame.height <= 0) return null;

  const contained = containedImageRect(
    elementRect.width,
    elementRect.height,
    frame.width,
    frame.height,
  );
  if (contained.width <= 0 || contained.height <= 0) return null;

  const relX = clientX - elementRect.left - contained.left;
  const relY = clientY - elementRect.top - contained.top;
  if (relX < 0 || relY < 0 || relX > contained.width || relY > contained.height) {
    return null; // cayó en la franja vacía del letterbox, no en la imagen real
  }

  const x = Math.round((relX / contained.width) * frame.width);
  const y = Math.round((relY / contained.height) * frame.height);
  return {
    x: Math.min(Math.max(x, 0), frame.width - 1),
    y: Math.min(Math.max(y, 0), frame.height - 1),
  };
}
