"use client";

/**
 * Envoltorio redimensionable para los dos paneles laterales del IDE (barra de
 * proyectos a la izquierda, torre de control "Ejecuciones" a la derecha):
 * encargo del dueño, "haz que esas dos ventanas se puedan encoger con el
 * mouse". Antes ambos tenían ancho fijo (`w-[21rem]` cableado en cada sitio).
 *
 * El cálculo (límites, colapso, lectura/escritura de `localStorage`) vive en
 * `panel-redimensionable-logica.ts` -- puro, sin JSX, para poder importarlo
 * directo desde un `.test.mjs` (ver ese archivo para el porqué). Este
 * componente solo conecta esa lógica con el DOM.
 *
 * Fluidez durante el arrastre: el ancho se escribe directo en la variable
 * CSS `--panel-ancho` del contenedor en cada `pointermove` (sin `setState`,
 * sin volver a renderizar nada) y el estado de React solo se confirma una
 * vez, al soltar -- así lo pide el encargo ("nada de re-renderizar el árbol
 * entero en cada pointermove").
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";

import { ChevronLeftIcon, ChevronRightIcon } from "@/components/icons";
import {
  analizarEstadoPanel,
  anchoConDelta,
  anchoEnVivo,
  clamp,
  claveAlmacenamiento,
  limitesPanel,
  resolverAnchoArrastre,
  serializarEstadoPanel,
  siguienteAnchoTeclado,
  type TeclaFlecha,
} from "@/components/ide/panel-redimensionable-logica";

function cx(...partes: Array<string | false | null | undefined>): string {
  return partes.filter(Boolean).join(" ");
}

/**
 * La pestaña fina que reabre un panel colapsado (encargo: cuando el panel de
 * Ejecuciones se cierra del todo con la X, "hay que adivinar dónde darle
 * click de nuevo"). Antes vivía inline dentro de este componente, solo para
 * el caso "colapsado arrastrando el divisor" -- se exporta acá para que
 * `page.tsx` reutilice el MISMO patrón visual cuando el panel se cierra
 * entero (deja de montarse `PanelRedimensionable`), en vez de inventar una
 * pestaña distinta.
 */
export interface PanelTabProps {
  /** De qué lado vive el panel que reabre: decide hacia qué lado apunta la flecha. */
  lado: "izquierda" | "derecha";
  etiqueta: string;
  onClick: () => void;
  /** Punto o número superpuesto (p.ej. "2 te esperan") -- ver `page.tsx`. */
  indicador?: ReactNode;
  className?: string;
}

export function PanelTab({ lado, etiqueta, onClick, indicador, className }: PanelTabProps) {
  const Chevron = lado === "izquierda" ? ChevronRightIcon : ChevronLeftIcon;
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={etiqueta}
      title={etiqueta}
      className={cx(
        "relative flex h-full w-3 shrink-0 items-center justify-center text-forja-tenue transition-colors hover:bg-forja-superficie-elevada hover:text-slate-600 dark:text-forja-tenue-oscuro dark:hover:bg-slate-800 dark:hover:text-slate-300",
        className,
      )}
    >
      <Chevron className="h-3 w-3" />
      {indicador}
    </button>
  );
}

const ANCHO_VENTANA_POR_DEFECTO = 1280; // Fallback razonable para el primer render en el servidor (sin `window`).

function anchoDeVentana(): number {
  return typeof window === "undefined" ? ANCHO_VENTANA_POR_DEFECTO : window.innerWidth;
}

export interface PanelRedimensionableProps {
  /** De qué lado del layout vive: decide hacia dónde crece al arrastrar y dónde va el divisor. */
  lado: "izquierda" | "derecha";
  /** Sufijo de la clave de `localStorage` (`forge.panel.<nombre>`). */
  nombre: string;
  /** Nombre legible para los `aria-label` ("panel de proyectos", "panel de ejecuciones"). */
  etiqueta: string;
  anchoPorDefecto: number;
  anchoMinimo?: number;
  /** Fracción del ancho de la ventana que marca el máximo (encargo: ~40%). */
  fraccionMaxima?: number;
  /**
   * El panel también se usa como cajón de ancho completo en pantallas chicas
   * (`lg:` es el punto de corte del resto del IDE). Con esto en `true`, el
   * arrastre/colapso solo aplica en escritorio: en móvil el contenido
   * siempre se ve completo, sin importar lo que quedó guardado la última
   * vez que se usó en escritorio.
   */
  soloEscritorio?: boolean;
  className?: string;
  children: ReactNode;
}

export function PanelRedimensionable({
  lado,
  nombre,
  etiqueta,
  anchoPorDefecto,
  anchoMinimo = 200,
  fraccionMaxima = 0.4,
  soloEscritorio = false,
  className,
  children,
}: PanelRedimensionableProps) {
  const ladoIzquierda = lado === "izquierda";
  const clave = claveAlmacenamiento(nombre);

  const [estado, setEstado] = useState<{ ancho: number; colapsado: boolean }>(() => {
    if (typeof window === "undefined") return { ancho: anchoPorDefecto, colapsado: false };
    const guardado = analizarEstadoPanel(window.localStorage.getItem(clave), anchoPorDefecto);
    const limites = limitesPanel(window.innerWidth, anchoMinimo, fraccionMaxima);
    return { ancho: Math.round(clamp(guardado.ancho, limites.min, limites.max)), colapsado: guardado.colapsado };
  });

  // En el cajón móvil el panel siempre se ve completo -- lo guardado en
  // escritorio no debe colapsar la vista en un teléfono.
  const [esEscritorio, setEsEscritorio] = useState(() => !soloEscritorio || typeof window === "undefined" || window.matchMedia("(min-width: 1024px)").matches);
  useEffect(() => {
    if (!soloEscritorio || typeof window === "undefined") return;
    const consulta = window.matchMedia("(min-width: 1024px)");
    const actualizar = () => setEsEscritorio(consulta.matches);
    actualizar();
    consulta.addEventListener("change", actualizar);
    return () => consulta.removeEventListener("change", actualizar);
  }, [soloEscritorio]);

  const colapsado = soloEscritorio && !esEscritorio ? false : estado.colapsado;

  const contenedorRef = useRef<HTMLDivElement | null>(null);
  const divisorRef = useRef<HTMLDivElement | null>(null);
  const arrastreRef = useRef<{ anchoInicial: number; xInicial: number } | null>(null);

  const persistir = useCallback(
    (siguiente: { ancho: number; colapsado: boolean }) => {
      setEstado(siguiente);
      if (typeof window !== "undefined") {
        window.localStorage.setItem(clave, serializarEstadoPanel(siguiente));
      }
    },
    [clave],
  );

  const aplicarAnchoEnVivo = useCallback((ancho: number) => {
    contenedorRef.current?.style.setProperty("--panel-ancho", `${ancho}px`);
  }, []);

  const manejarPointerDown = useCallback(
    (evento: ReactPointerEvent<HTMLDivElement>) => {
      if (evento.button !== 0 && evento.pointerType === "mouse") return;
      arrastreRef.current = { anchoInicial: estado.ancho, xInicial: evento.clientX };
      divisorRef.current?.setPointerCapture(evento.pointerId);
      divisorRef.current?.setAttribute("data-arrastrando", "true");
      document.body.style.userSelect = "none";
      document.body.style.cursor = "col-resize";
    },
    [estado.ancho],
  );

  const manejarPointerMove = useCallback(
    (evento: ReactPointerEvent<HTMLDivElement>) => {
      const arrastre = arrastreRef.current;
      if (!arrastre) return;
      const delta = evento.clientX - arrastre.xInicial;
      const propuesto = anchoConDelta(ladoIzquierda, arrastre.anchoInicial, delta);
      const limites = limitesPanel(anchoDeVentana(), anchoMinimo, fraccionMaxima);
      aplicarAnchoEnVivo(anchoEnVivo(propuesto, limites));
    },
    [ladoIzquierda, anchoMinimo, fraccionMaxima, aplicarAnchoEnVivo],
  );

  const terminarArrastre = useCallback(
    (evento: ReactPointerEvent<HTMLDivElement>) => {
      const arrastre = arrastreRef.current;
      arrastreRef.current = null;
      divisorRef.current?.removeAttribute("data-arrastrando");
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
      if (divisorRef.current?.hasPointerCapture(evento.pointerId)) {
        divisorRef.current.releasePointerCapture(evento.pointerId);
      }
      if (!arrastre) return;
      const delta = evento.clientX - arrastre.xInicial;
      const propuesto = anchoConDelta(ladoIzquierda, arrastre.anchoInicial, delta);
      const limites = limitesPanel(anchoDeVentana(), anchoMinimo, fraccionMaxima);
      persistir(resolverAnchoArrastre(propuesto, limites));
    },
    [ladoIzquierda, anchoMinimo, fraccionMaxima, persistir],
  );

  const restaurarPorDefecto = useCallback(() => {
    persistir({ ancho: anchoPorDefecto, colapsado: false });
  }, [anchoPorDefecto, persistir]);

  const reabrir = useCallback(() => {
    persistir({ ancho: estado.ancho, colapsado: false });
  }, [estado.ancho, persistir]);

  const manejarTeclado = useCallback(
    (evento: ReactKeyboardEvent<HTMLDivElement>) => {
      if (evento.key !== "ArrowLeft" && evento.key !== "ArrowRight") return;
      evento.preventDefault();
      const limites = limitesPanel(anchoDeVentana(), anchoMinimo, fraccionMaxima);
      const siguiente = siguienteAnchoTeclado(ladoIzquierda, estado.ancho, evento.key as TeclaFlecha, limites);
      persistir({ ancho: siguiente, colapsado: false });
    },
    [ladoIzquierda, anchoMinimo, fraccionMaxima, estado.ancho, persistir],
  );

  const estiloContenedor = { "--panel-ancho": `${estado.ancho}px` } as CSSProperties;

  const divisor = !colapsado && (
    <div
      ref={divisorRef}
      role="separator"
      aria-orientation="vertical"
      aria-label={`Redimensionar ${etiqueta}`}
      aria-valuenow={Math.round(estado.ancho)}
      aria-valuemin={anchoMinimo}
      aria-valuemax={Math.round(limitesPanel(anchoDeVentana(), anchoMinimo, fraccionMaxima).max)}
      tabIndex={0}
      onPointerDown={manejarPointerDown}
      onPointerMove={manejarPointerMove}
      onPointerUp={terminarArrastre}
      onPointerCancel={terminarArrastre}
      onDoubleClick={restaurarPorDefecto}
      onKeyDown={manejarTeclado}
      className={cx(
        "group relative z-10 w-1.5 shrink-0 touch-none select-none outline-none",
        "cursor-col-resize",
        soloEscritorio && "hidden lg:block",
      )}
    >
      <span
        aria-hidden="true"
        className={cx(
          "pointer-events-none absolute inset-y-0 left-1/2 w-px -translate-x-1/2 rounded-full bg-transparent transition-colors duration-150",
          "group-hover:bg-forja-tenue/60 group-focus-visible:bg-forja-tenue/60",
          "group-data-[arrastrando=true]:w-0.5 group-data-[arrastrando=true]:bg-forja-tenue",
          "dark:group-hover:bg-forja-tenue-oscuro/60 dark:group-focus-visible:bg-forja-tenue-oscuro/60 dark:group-data-[arrastrando=true]:bg-forja-tenue-oscuro",
        )}
      />
    </div>
  );

  const pestana = colapsado && <PanelTab lado={lado} etiqueta={`Mostrar ${etiqueta}`} onClick={reabrir} />;

  const contenido = !colapsado && (
    <div
      ref={contenedorRef}
      style={estiloContenedor}
      className={cx(
        "h-full min-w-0 flex-1 overflow-hidden",
        soloEscritorio ? "lg:w-[var(--panel-ancho)] lg:flex-none" : "w-[var(--panel-ancho)] flex-none",
      )}
    >
      {children}
    </div>
  );

  return (
    <div className={cx("flex h-full min-w-0 flex-1 lg:flex-none lg:shrink-0", className)}>
      {!ladoIzquierda && (divisor || pestana)}
      {contenido}
      {ladoIzquierda && (divisor || pestana)}
    </div>
  );
}
