"use client";

/**
 * Pantalla de preparación de Windows -- encargo del dueño, literal: "antes
 * de abrir la app completamente exija descargar por CLI las cosas que
 * falten, que detecte lo que falte y salgan los comandos con solo darle ▶
 * play, se conecte con el CMD y los ejecute, y cuando termine, esos
 * comandos vayan desapareciendo hasta desaparecer la pantalla."
 *
 * Se engancha en `(app)/layout.tsx`, DESPUÉS de resolver autenticación y
 * SOLO cuando `isLocalDesktop` (la app de escritorio) -- nunca en el móvil
 * ni en el hosted multi-tenant. `GET /v1/preparacion` ya devuelve lista
 * vacía fuera de Windows (`edecan_companion.preparacion.detectar`), así que
 * en macOS/Linux esta pantalla se resuelve sola en el primer fetch y nunca
 * llega a pintar nada -- doble cinturón, no solo el flag del cliente.
 *
 * Comportamiento pedido, uno a uno:
 * - Lista de lo que falta, con nombre y POR QUÉ hace falta.
 * - Un solo botón ▶ que instala en orden (`ejecutarTodo`).
 * - La fila que se está instalando muestra su salida en vivo -- se reusa
 *   `@xterm/xterm` (mismo criterio que `paneles/TerminalPanel.tsx`: nunca
 *   limpiar ANSI, dejar que el propio terminal lo interprete) en
 *   `<SalidaInstalacion>`.
 * - Al terminar bien, la fila se desvanece y desaparece de la lista.
 * - Cuando no queda ninguna, la pantalla se desvanece y se llama `onListo`.
 * - Si una falla: NO desaparece, se queda con el error visible, con
 *   "Reintentar" siempre y "Continuar de todas formas" solo si no era
 *   obligatoria (`puedeContinuarSinResolver`).
 * - Lo que necesita administrador se marca ANTES de pulsar play
 *   (`requiere_admin` + `elevado` ya vienen en la primera lectura): una fila
 *   así nunca llega a intentarse sola, para no fallar a medias.
 *
 * Fallo al CONSULTAR el estado (red, companion no conectado todavía) no
 * bloquea la entrada a la app -- un preflight roto es peor que uno ausente
 * (`onListo` se llama igual, con el error solo en consola).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ITheme, Terminal as XTerm } from "@xterm/xterm";
import type { FitAddon } from "@xterm/addon-fit";

import "@xterm/xterm/css/xterm.css";

import { KeyIcon, PlayIcon, RetryIcon } from "@/components/icons";
import {
  getPreparacion,
  postPreparacionInstalar,
  readPreparacion,
  type RequisitoPreparacion,
} from "@/lib/api-preparacion";

// Cuánto se espera entre cada lectura de salida de una instalación en curso.
// Más lento que el sondeo de una terminal interactiva (`TERMINAL_POLL_MS` en
// `TerminalPanel.tsx`, 120ms): acá nadie teclea en vivo, es un instalador
// corriendo solo, así que no hay percepción de "input atascado" que cuidar.
const POLL_MS = 300;
// Duración de la transición de salida de una fila (debe calzar con
// `duration-500` de las clases de abajo) y de la pantalla completa.
const FADE_MS = 500;

type EstadoFila =
  | "pendiente"
  | "sin_admin"
  | "ejecutando"
  | "completado"
  | "error"
  | "saliendo";

interface FilaPreparacion extends RequisitoPreparacion {
  estadoFila: EstadoFila;
  error: string | null;
  salida: string;
  cursor: number;
}

/** Una fila instalable con `requiere_admin` sin que ESTE proceso esté
 * elevado nunca se intenta sola -- es la regla "se marca antes de pulsar
 * play, no después de fallar" hecha función, nombrada en vez de una
 * expresión booleana suelta en `filaInicial`. */
function puedeIntentarseAhora(
  requisito: Pick<RequisitoPreparacion, "instalable" | "requiere_admin">,
  elevado: boolean,
): boolean {
  return requisito.instalable && !(requisito.requiere_admin && !elevado);
}

function filaInicial(requisito: RequisitoPreparacion, elevado: boolean): FilaPreparacion {
  const sinAdmin = requisito.instalable && !puedeIntentarseAhora(requisito, elevado);
  return {
    ...requisito,
    estadoFila: sinAdmin ? "sin_admin" : "pendiente",
    error: null,
    salida: "",
    cursor: 0,
  };
}

export function PantallaPreparacion({ onListo }: { onListo: () => void }) {
  const [cargando, setCargando] = useState(true);
  const [elevado, setElevado] = useState(false);
  const [filas, setFilas] = useState<FilaPreparacion[]>([]);
  const [corriendo, setCorriendo] = useState(false);
  const [saliendoPantalla, setSaliendoPantalla] = useState(false);
  const filasRef = useRef(filas);
  filasRef.current = filas;

  const cargarInicial = useCallback(async () => {
    try {
      const estado = await getPreparacion();
      const pendientes = estado.requisitos.filter((r) => r.estado !== "cumplido");
      if (pendientes.length === 0) {
        onListo();
        return;
      }
      setElevado(estado.elevado);
      setFilas(pendientes.map((r) => filaInicial(r, estado.elevado)));
    } catch (err) {
      // Un preflight que no se pudo ni consultar no debe dejar a la persona
      // sin poder entrar a su propia app -- fallo visible solo en consola.
      console.error("No se pudo consultar la preparación del equipo.", err);
      onListo();
      return;
    } finally {
      setCargando(false);
    }
  }, [onListo]);

  useEffect(() => {
    void cargarInicial();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** "Ya lo hice, revisar de nuevo" (fila sin instalación automática):
   * vuelve a consultar el manifiesto, pero MEZCLA el resultado en vez de
   * reemplazar `filas` entera -- una fila que en este mismo instante está
   * "ejecutando" (otra instalación en curso, o un "Reintentar" suelto) se
   * preserva intacta. `cargarInicial` sí puede reemplazar todo porque corre
   * una sola vez, antes de que exista ninguna fila en vuelo. */
  const revisarPendientes = useCallback(async () => {
    try {
      const estado = await getPreparacion();
      const porId = new Map(estado.requisitos.map((r) => [r.id, r]));
      setFilas((actuales) =>
        actuales
          .filter((f) => f.estadoFila === "ejecutando" || porId.get(f.id)?.estado !== "cumplido")
          .map((f) => {
            if (f.estadoFila === "ejecutando") return f;
            const fresco = porId.get(f.id);
            return fresco ? filaInicial(fresco, estado.elevado) : f;
          }),
      );
      setElevado(estado.elevado);
    } catch (err) {
      console.error("No se pudo revisar el estado del equipo.", err);
    }
  }, []);

  // Cuando la lista queda vacía (todo se desvaneció o se descartó), la
  // pantalla completa se desvanece y recién ENTONCES se entra a la app --
  // nunca antes de que la persona vea la última fila desaparecer.
  useEffect(() => {
    if (cargando || filas.length > 0 || saliendoPantalla) return;
    setSaliendoPantalla(true);
    const temporizador = window.setTimeout(onListo, FADE_MS);
    return () => window.clearTimeout(temporizador);
  }, [cargando, filas.length, saliendoPantalla, onListo]);

  const actualizarFila = useCallback((id: string, cambios: Partial<FilaPreparacion>) => {
    setFilas((actuales) => actuales.map((f) => (f.id === id ? { ...f, ...cambios } : f)));
  }, []);

  const quitarFila = useCallback((id: string) => {
    setFilas((actuales) => actuales.filter((f) => f.id !== id));
  }, []);

  /** Corre UNA fila de punta a punta: arranca la instalación y sondea su
   * salida hasta que el companion la reporta terminada (bien o mal). */
  const ejecutarFila = useCallback(
    async (id: string) => {
      actualizarFila(id, { estadoFila: "ejecutando", error: null, salida: "", cursor: 0 });
      try {
        await postPreparacionInstalar(id);
      } catch (err) {
        actualizarFila(id, {
          estadoFila: "error",
          error: err instanceof Error ? err.message : "No se pudo iniciar la instalación.",
        });
        return;
      }

      for (;;) {
        const cursorActual = filasRef.current.find((f) => f.id === id)?.cursor ?? 0;
        let lectura;
        try {
          lectura = await readPreparacion(id, cursorActual);
        } catch (err) {
          actualizarFila(id, {
            estadoFila: "error",
            error: err instanceof Error ? err.message : "Se perdió la conexión con el companion.",
          });
          return;
        }
        const textoNuevo = lectura.events.map((evento) => evento.text).join("");
        setFilas((actuales) =>
          actuales.map((f) =>
            f.id === id
              ? { ...f, salida: f.salida + textoNuevo, cursor: lectura.next_cursor }
              : f,
          ),
        );

        if (lectura.estado === "completado") {
          actualizarFila(id, { estadoFila: "saliendo" });
          await new Promise((resolve) => window.setTimeout(resolve, FADE_MS));
          quitarFila(id);
          return;
        }
        if (lectura.estado === "error") {
          actualizarFila(id, {
            estadoFila: "error",
            error: lectura.error || "La instalación terminó con un error.",
          });
          return;
        }
        await new Promise((resolve) => window.setTimeout(resolve, POLL_MS));
      }
    },
    [actualizarFila, quitarFila],
  );

  /** El único botón ▶: recorre las filas pendientes EN ORDEN, una a la vez.
   * Una fila que necesita administrador y no lo tiene se salta -- ya está
   * marcada desde antes de pulsar play, reintentarla no cambiaría nada sin
   * reabrir Edecán elevado. */
  const ejecutarTodo = useCallback(async () => {
    setCorriendo(true);
    try {
      const objetivo = filasRef.current
        .filter((f) => f.estadoFila === "pendiente" || f.estadoFila === "error")
        .map((f) => f.id);
      for (const id of objetivo) {
        if (!filasRef.current.some((f) => f.id === id)) continue; // ya se resolvió/quitó
        await ejecutarFila(id);
      }
    } finally {
      setCorriendo(false);
    }
  }, [ejecutarFila]);

  const hayAlgoAccionable = useMemo(
    () => filas.some((f) => f.estadoFila === "pendiente" || f.estadoFila === "error"),
    [filas],
  );

  if (cargando) {
    return (
      <div className="flex min-h-dvh flex-col items-center justify-center gap-3 bg-forja-superficie text-forja-tenue dark:bg-forja-superficie-oscura dark:text-forja-tenue-oscuro">
        <span className="h-5 w-5 animate-spin rounded-full border-2 border-forja-borde border-t-transparent dark:border-forja-borde-oscuro" />
        <p className="text-sm">Comprobando tu equipo…</p>
      </div>
    );
  }

  if (filas.length === 0 && !saliendoPantalla) return null;

  return (
    <div
      className={`flex min-h-dvh items-center justify-center bg-forja-superficie px-4 py-10 transition-opacity duration-500 dark:bg-forja-superficie-oscura ${
        saliendoPantalla ? "pointer-events-none opacity-0" : "opacity-100"
      }`}
    >
      <div className="w-full max-w-xl rounded-2xl border border-forja-borde bg-forja-superficie-elevada p-6 shadow-panel dark:border-forja-borde-oscuro dark:bg-forja-superficie-oscura-elevada">
        <h1 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
          Preparando tu equipo
        </h1>
        <p className="mt-1 text-sm text-forja-tenue dark:text-forja-tenue-oscuro">
          A Edecán le falta esto para funcionar bien en tu PC. Revísalo y pulsa ▶ una vez --
          instala todo en orden.
        </p>

        <ul className="mt-5 space-y-3">
          {filas.map((fila) => (
            <FilaRequisito
              key={fila.id}
              fila={fila}
              elevado={elevado}
              onReintentar={() => void ejecutarFila(fila.id)}
              onContinuarSinResolver={() => quitarFila(fila.id)}
              onRevisarDeNuevo={() => void revisarPendientes()}
              deshabilitado={corriendo}
            />
          ))}
        </ul>

        <div className="mt-6 flex items-center justify-between gap-3">
          <p className="text-xs text-forja-tenue dark:text-forja-tenue-oscuro">
            {corriendo
              ? "Instalando, no cierres Edecán…"
              : hayAlgoAccionable
                ? `${filas.filter((f) => f.estadoFila === "pendiente" || f.estadoFila === "error").length} paso(s) por hacer.`
                : "Nada más que este proceso pueda resolver solo."}
          </p>
          {hayAlgoAccionable && (
            <button
              type="button"
              onClick={() => void ejecutarTodo()}
              disabled={corriendo}
              className="flex shrink-0 items-center gap-1.5 rounded-lg bg-slate-950 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-white"
            >
              {corriendo ? (
                <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white/60 border-t-transparent dark:border-slate-900/60" />
              ) : (
                <PlayIcon className="h-3.5 w-3.5" />
              )}
              {corriendo ? "Instalando…" : "Preparar equipo"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function FilaRequisito({
  fila,
  elevado,
  deshabilitado,
  onReintentar,
  onContinuarSinResolver,
  onRevisarDeNuevo,
}: {
  fila: FilaPreparacion;
  elevado: boolean;
  deshabilitado: boolean;
  onReintentar: () => void;
  onContinuarSinResolver: () => void;
  onRevisarDeNuevo: () => void;
}) {
  const puedeContinuarSinResolver = !fila.obligatorio;

  return (
    <li
      className={`rounded-xl border border-forja-borde-suave bg-forja-superficie p-3 transition-all duration-500 dark:border-forja-borde-oscuro-suave dark:bg-forja-superficie-oscura ${
        fila.estadoFila === "saliendo" ? "pointer-events-none scale-95 opacity-0" : "opacity-100"
      }`}
    >
      <div className="flex items-start gap-3">
        <EstadoIcono estado={fila.estadoFila} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <p className="text-sm font-medium text-slate-800 dark:text-slate-100">{fila.nombre}</p>
            {fila.requiere_admin && (
              <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-medium text-amber-700 dark:bg-amber-900/50 dark:text-amber-300">
                <KeyIcon className="h-2.5 w-2.5" /> Administrador
              </span>
            )}
            {!fila.obligatorio && (
              <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                Opcional
              </span>
            )}
          </div>
          <p className="mt-0.5 text-xs text-forja-tenue dark:text-forja-tenue-oscuro">
            {fila.por_que}
          </p>

          {fila.estadoFila === "sin_admin" && (
            <p className="mt-2 rounded-md bg-amber-50 px-2.5 py-1.5 text-xs text-amber-700 dark:bg-amber-950/40 dark:text-amber-300">
              Necesita permisos de administrador. Cierra Edecán y vuelve a abrirlo con clic
              derecho → «Ejecutar como administrador»
              {!elevado ? " -- este proceso no los tiene ahora mismo." : "."}
            </p>
          )}

          {!fila.instalable && fila.estadoFila !== "completado" && (
            <div className="mt-2 flex items-center justify-between gap-2 rounded-md bg-forja-superficie-hundida px-2.5 py-1.5 text-xs text-forja-tenue dark:bg-forja-superficie-oscura-hundida dark:text-forja-tenue-oscuro">
              <span>Esto se resuelve fuera de Edecán -- revisa arriba cómo.</span>
              <button
                type="button"
                onClick={onRevisarDeNuevo}
                className="shrink-0 font-medium text-slate-700 underline decoration-dotted hover:text-slate-900 dark:text-slate-300 dark:hover:text-white"
              >
                Ya lo hice, revisar de nuevo
              </button>
            </div>
          )}

          {fila.estadoFila === "ejecutando" && <SalidaInstalacion id={fila.id} texto={fila.salida} />}

          {fila.estadoFila === "error" && (
            <div className="mt-2 space-y-2">
              {fila.salida && <SalidaInstalacion id={fila.id} texto={fila.salida} />}
              <p className="rounded-md bg-rose-50 px-2.5 py-1.5 text-xs text-rose-700 dark:bg-rose-950/40 dark:text-rose-300">
                {fila.error}
              </p>
              <div className="flex gap-3">
                <button
                  type="button"
                  onClick={onReintentar}
                  disabled={deshabilitado}
                  className="inline-flex items-center gap-1 text-xs font-semibold text-slate-700 hover:text-slate-950 disabled:cursor-not-allowed disabled:opacity-50 dark:text-slate-300 dark:hover:text-white"
                >
                  <RetryIcon className="h-3 w-3" /> Reintentar
                </button>
                {puedeContinuarSinResolver && (
                  <button
                    type="button"
                    onClick={onContinuarSinResolver}
                    disabled={deshabilitado}
                    className="text-xs font-medium text-forja-tenue underline decoration-dotted hover:text-slate-700 disabled:cursor-not-allowed disabled:opacity-50 dark:text-forja-tenue-oscuro dark:hover:text-slate-200"
                  >
                    Continuar de todas formas
                  </button>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </li>
  );
}

function EstadoIcono({ estado }: { estado: EstadoFila }) {
  if (estado === "ejecutando") {
    return (
      <span className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin rounded-full border-2 border-slate-300 border-t-slate-700 dark:border-slate-600 dark:border-t-slate-200" />
    );
  }
  if (estado === "error") {
    return <span className="mt-0.5 h-2.5 w-2.5 shrink-0 rounded-full bg-rose-500" />;
  }
  if (estado === "sin_admin") {
    return <span className="mt-0.5 h-2.5 w-2.5 shrink-0 rounded-full bg-amber-500" />;
  }
  return (
    <span className="mt-0.5 h-2.5 w-2.5 shrink-0 rounded-full border border-forja-borde-fuerte dark:border-forja-borde-oscuro" />
  );
}

// ---------------------------------------------------------------------------
// Salida en vivo -- xterm.js reusado (nunca limpiar ANSI, ver el docstring
// del archivo), un instancia por fila que está corriendo. Carga diferida
// igual que `paneles/TerminalPanel.tsx::loadXtermModules`.
// ---------------------------------------------------------------------------

interface XtermModules {
  Terminal: (typeof import("@xterm/xterm"))["Terminal"];
  FitAddon: (typeof import("@xterm/addon-fit"))["FitAddon"];
}

let modulesPromise: Promise<XtermModules> | null = null;

function loadXtermModules(): Promise<XtermModules> {
  if (!modulesPromise) {
    modulesPromise = Promise.all([import("@xterm/xterm"), import("@xterm/addon-fit")]).then(
      ([xterm, fit]) => ({ Terminal: xterm.Terminal, FitAddon: fit.FitAddon }),
    );
  }
  return modulesPromise;
}

// Mismo mapa de colores que `paneles/TerminalPanel.tsx` (`FORJA_TERMINAL_THEME`,
// copiado ahí de la escala `forja.*` porque xterm.js pinta fuera de la
// cascada de Tailwind y necesita hex reales) -- se repite acá en vez de
// exportarlo desde ese archivo porque `paneles/` es del reparto de otro
// encargo en curso (`docs/edecan-windows.md`), y esta pantalla no depende de
// él.
const TEMA_SALIDA: ITheme = {
  background: "#0b0b0c",
  foreground: "#d6d6d4",
  cursor: "#0b0b0c",
  selectionBackground: "#232326",
  black: "#0b0b0c",
  red: "#f87171",
  green: "#4ade80",
  yellow: "#facc15",
  blue: "#60a5fa",
  magenta: "#c084fc",
  cyan: "#22d3ee",
  white: "#8f8f96",
  brightBlack: "#2b2b2f",
  brightRed: "#fca5a5",
  brightGreen: "#86efac",
  brightYellow: "#fde047",
  brightBlue: "#93c5fd",
  brightMagenta: "#d8b4fe",
  brightCyan: "#67e8f9",
  brightWhite: "#ffffff",
};

function SalidaInstalacion({ id, texto }: { id: string; texto: string }) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<XTerm | null>(null);
  const escritoRef = useRef(0);
  const textoRef = useRef(texto);
  textoRef.current = texto;

  useEffect(() => {
    let cancelado = false;
    escritoRef.current = 0;
    void (async () => {
      const { Terminal, FitAddon: FitAddonCtor } = await loadXtermModules();
      if (cancelado || !hostRef.current) return;
      const term = new Terminal({
        convertEol: false,
        cursorBlink: false,
        disableStdin: true,
        fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
        fontSize: 11.5,
        scrollback: 2000,
        theme: TEMA_SALIDA,
      });
      const fit = new FitAddonCtor();
      term.loadAddon(fit);
      term.open(hostRef.current);
      fit.fit();
      termRef.current = term;
      // Escribe lo que ya se haya acumulado mientras cargaban los módulos --
      // sin esto, la salida que llegó antes de que xterm terminara de cargar
      // se perdería en silencio.
      if (textoRef.current) {
        term.write(textoRef.current);
        escritoRef.current = textoRef.current.length;
      }
    })();
    return () => {
      cancelado = true;
      termRef.current?.dispose();
      termRef.current = null;
    };
    // Nueva instancia de xterm por cada fila que EMPIEZA a correr -- `id`
    // identifica el requisito, no cambia mientras la misma fila sigue viva.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  useEffect(() => {
    const term = termRef.current;
    if (!term || texto.length <= escritoRef.current) return;
    term.write(texto.slice(escritoRef.current));
    escritoRef.current = texto.length;
  }, [texto]);

  return (
    <div
      ref={hostRef}
      className="mt-2 h-32 overflow-hidden rounded-md border border-forja-borde-oscuro p-1"
    />
  );
}
