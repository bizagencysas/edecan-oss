/**
 * Lógica pura de `PanelRedimensionable` -- separada del componente porque
 * es TSX (JSX) y el runner de pruebas del repo (`node --test`, ver
 * `test-register.mjs`) usa el *type stripping* nativo de Node, que no
 * transforma JSX. Sacando el cálculo de aquí (clamp de límites, la decisión
 * de colapsar, y la serialización de `localStorage`) queda un módulo `.ts`
 * plano que sí se puede importar directo en un `.test.mjs`, igual que
 * `ide-cola.ts` o `chat-attachments.ts` en este mismo paquete.
 *
 * Nada de esto toca el DOM ni `window`: el componente decide cuándo leer
 * `window.innerWidth` o `localStorage` y le pasa los números a estas
 * funciones.
 */

export interface LimitesPanel {
  min: number;
  max: number;
}

export interface EstadoPanel {
  ancho: number;
  colapsado: boolean;
}

/** Confina `valor` a `[min, max]`. Si `max < min` (ventana angosta), gana `min`. */
export function clamp(valor: number, min: number, max: number): number {
  return Math.min(Math.max(valor, min), Math.max(min, max));
}

/**
 * Límites del encargo: mínimo fijo (~200px) y máximo ~40% del ancho de la
 * ventana. En ventanas angostas el 40% puede caer por debajo del mínimo --
 * en ese caso el máximo cede y se iguala al mínimo, nunca al revés.
 */
export function limitesPanel(
  anchoVentana: number,
  anchoMinimo: number,
  fraccionMaxima: number,
): LimitesPanel {
  const max = Math.round(anchoVentana * fraccionMaxima);
  return { min: anchoMinimo, max: Math.max(anchoMinimo, max) };
}

/**
 * Decide el ancho final al SOLTAR el arrastre (o al restaurar por teclado
 * de forma explícita). Por debajo del mínimo, el panel se colapsa y el
 * ancho que se recuerda para la próxima vez que se reabra es el mínimo --
 * no el valor a medio arrastrar, que podría ser negativo.
 */
export function resolverAnchoArrastre(anchoPropuesto: number, limites: LimitesPanel): EstadoPanel {
  if (anchoPropuesto < limites.min) {
    return { ancho: limites.min, colapsado: true };
  }
  return { ancho: clamp(anchoPropuesto, limites.min, limites.max), colapsado: false };
}

/**
 * Ancho "en vivo" durante el arrastre (antes de soltar): con resistencia en
 * el mínimo -- no se deja ver negativo ni saltar al colapso a medio gesto,
 * eso solo se decide al soltar (`resolverAnchoArrastre`).
 */
export function anchoEnVivo(anchoPropuesto: number, limites: LimitesPanel): number {
  return clamp(anchoPropuesto, limites.min, limites.max);
}

/**
 * Traduce un desplazamiento de pantalla (positivo = hacia la derecha) al
 * delta de ancho del panel, según de qué lado del layout vive: el panel
 * izquierdo crece cuando el divisor se mueve a la derecha; el derecho
 * crece cuando se mueve a la izquierda (su borde arrastrable es el
 * interior, no el que toca el borde de la ventana).
 */
export function anchoConDelta(ladoIzquierda: boolean, anchoBase: number, deltaPantalla: number): number {
  return anchoBase + (ladoIzquierda ? deltaPantalla : -deltaPantalla);
}

export type TeclaFlecha = "ArrowLeft" | "ArrowRight";

/** Un paso de teclado (16px por convención del encargo), clamped -- nunca colapsa por teclado. */
export function siguienteAnchoTeclado(
  ladoIzquierda: boolean,
  anchoActual: number,
  tecla: TeclaFlecha,
  limites: LimitesPanel,
  paso = 16,
): number {
  const deltaPantalla = tecla === "ArrowRight" ? paso : -paso;
  return clamp(anchoConDelta(ladoIzquierda, anchoActual, deltaPantalla), limites.min, limites.max);
}

/** Prefijo pedido por el encargo: `forge.panel.*`. */
export function claveAlmacenamiento(nombre: string): string {
  return `forge.panel.${nombre}`;
}

export function serializarEstadoPanel(estado: EstadoPanel): string {
  return JSON.stringify(estado);
}

/**
 * Lee lo guardado en `localStorage` con desconfianza: JSON roto, un valor
 * de otra versión del esquema, un ancho no numérico o negativo -- cualquiera
 * de esos casos cae de vuelta al ancho por defecto en vez de romper el
 * render.
 */
export function analizarEstadoPanel(valorGuardado: string | null, anchoPorDefecto: number): EstadoPanel {
  if (!valorGuardado) return { ancho: anchoPorDefecto, colapsado: false };
  try {
    const datos = JSON.parse(valorGuardado) as Partial<EstadoPanel> | null;
    const ancho =
      datos && typeof datos.ancho === "number" && Number.isFinite(datos.ancho) && datos.ancho > 0
        ? datos.ancho
        : anchoPorDefecto;
    const colapsado = Boolean(datos && datos.colapsado === true);
    return { ancho, colapsado };
  } catch {
    return { ancho: anchoPorDefecto, colapsado: false };
  }
}
