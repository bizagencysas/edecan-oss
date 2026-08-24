"use client";

/**
 * Terminal real del Mac, embebida. Vive dentro del Estado 3 (`estado/Editor.tsx`,
 * §3.1: "terminal abajo") -- ya no es una pestaña propia: el acceso total al
 * shell (§3.5) sigue intacto, solo cambia dónde se monta en pantalla.
 *
 * §3.5 es literal: "un shell real... PTY de verdad, no tuberías... salida
 * completa, ANSI incluido". El backend ya lo hace bien
 * (`apps/companion/edecan_companion/ide_sessions.py::start_terminal` abre un
 * `pty.openpty()` de verdad y `input_terminal` escribe bytes crudos al master
 * fd, sin interpretarlos). La interfaz anterior lo destruía en dos puntos:
 *
 *   1. `estado-ide.ts::cleanTerminalOutput` le pasa la salida por una cadena
 *      de `.replace(...)` que BORRA los códigos ANSI antes de pintar --
 *      literalmente el dato que hace que `vim`/`top`/una barra de progreso
 *      sean legibles. Esta pieza NO puede dejar de llamar a esa función
 *      (sigue viva para quien todavía la use en otra vista); en vez de eso,
 *      este archivo directamente no la importa: lee los eventos crudos con su
 *      propio `readIdeTerminal` y escribe `event.text` sin tocarlo en un
 *      terminal de verdad (`@xterm/xterm`), que es quien sabe interpretar ANSI.
 *   2. Un `<input>` de una sola línea que mandaba con `\n` al soltar Enter --
 *      sin Ctrl+C, sin flechas, sin Tab, sin tecla por tecla. Se reemplaza por
 *      `term.onData`, que entrega cada pulsación (o el pegado completo) tal
 *      cual `xterm.js` la interpreta del teclado, y se manda cruda a
 *      `sendIdeTerminalInput` (mismo transporte que ya existía en
 *      `lib/api-ide.ts`, sin negociar un protocolo nuevo).
 *
 * Carga diferida: `@xterm/xterm` (v6) + `@xterm/addon-fit` (+ intento de
 * `@xterm/addon-webgl`, con caída silenciosa al renderer DOM si no hay WebGL)
 * se piden con `import()` dentro de un efecto -- nunca en el cuerpo del
 * módulo. Dos razones para NO envolver esto con `next/dynamic` como sugiere
 * el encargo al pie de la letra: (a) lo que hay que diferir es una LIBRERÍA,
 * no un componente de React con export default, y `next/dynamic` está
 * pensado para lo segundo -- forzarlo exigiría un segundo archivo solo para
 * el wrapper, y el reparto de archivos de este encargo es justo este uno; (b)
 * un `import()` dentro de un `useEffect` nunca corre en el paso de
 * renderizado en servidor (los efectos son client-only por definición) y el
 * bundler igual lo separa en su propio chunk -- el mismo resultado que pide
 * el encargo ("no pagarlos en el bundle base") sin el archivo extra.
 *
 * Varias terminales a la vez (§3.5, "cada una con su estado y su
 * directorio"): la API sí lo permite -- `ide_sessions.py` registra tantas
 * sesiones `kind="terminal"` concurrentes como se pidan, cada una con su
 * propio `master_fd`, y el hilo `_read_pty` sigue drenando la salida al
 * journal de esa sesión SIGA O NO alguien leyéndola por HTTP. Por eso, cada
 * `IdeSession.id` que este panel llega a mostrar se queda con su propia
 * instancia de `xterm.Terminal` (scrollback, cursor de lectura, proceso) en
 * `instancesRef`, oculta con CSS en vez de destruirse al cambiar de pestaña:
 * volver a una terminal no la resetea, hace un sondeo de alcance inmediato
 * desde su propio cursor y sigue exactamente donde iba. Lo que este archivo
 * NO hace es sondear en vivo terminales que no están a la vista: el layout de
 * `Editor.tsx` (fuera de mi reparto) solo muestra UNA a la vez, así que
 * mantener el resto sondeando de fondo gastaría red sin que nadie lo viera, y
 * no hay pérdida de datos -- el companion journaliza esa salida igual, con o
 * sin sondeo, así que el alcance inmediato al reseleccionar la recupera
 * completa.
 *
 * GAP DECLARADO, no simulado (regla del encargo, mismo criterio que
 * `MemoryKnowledgePanel.tsx:23-38`): el redimensionado real del PTY (`ioctl`
 * `TIOCSWINSZ` / `SIGWINCH`) no existe en el backend. `ide_sessions.py` jamás
 * llama a eso, y no hay endpoint `/v1/ide/terminals/{id}/resize` en
 * `lib/api-ide.ts`. `FitAddon.fit()` aquí solo ajusta cuántas filas/columnas
 * MUESTRA xterm.js en el cliente -- el proceso remoto (`vim`, `top`) sigue
 * creyendo que su pty mide lo que medía al arrancar (`start_terminal`), así
 * que puede pintarse mal si el panel no calza con ese tamaño original.
 * Arreglarlo de verdad requiere tocar `ide_sessions.py` (prohibido para este
 * encargo), `routers/ide.py` y `lib/api-ide.ts` (fuera de mi reparto de
 * archivos) -- queda declarado acá, en la interfaz (aviso con `title` junto
 * al selector) y en el resumen final, no fingido.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { ITheme, Terminal as XTerm } from "@xterm/xterm";
import type { FitAddon } from "@xterm/addon-fit";
import type { WebglAddon } from "@xterm/addon-webgl";

// Next.js permite (y en este caso es lo correcto) importar el CSS de un
// paquete de terceros desde cualquier componente, no solo desde el layout
// raíz -- es una excepción documentada a la regla de "CSS global solo en
// _app", pensada justo para bibliotecas como esta.
import "@xterm/xterm/css/xterm.css";

import { isLive } from "@/components/ide/estado-ide";
import { PlusIcon } from "@/components/icons";
import { readIdeTerminal, sendIdeTerminalInput, type IdeSession, type IdeSessionEvent } from "@/lib/api-ide";

// Cuánto tarda un ciclo de sondeo de la terminal. Deliberadamente mucho más
// corto que los 1200/2500 ms del agente y la torre de control en
// `estado-ide.ts`: ahí el usuario lee texto que llega en bloques; acá teclea
// en vivo sobre `vim` o el autocompletado de su shell, y cualquier demora
// perceptible se siente como una terminal rota. El companion corre en la
// misma máquina, así que el costo de sondear rápido es insignificante frente
// al beneficio de latencia percibida.
const TERMINAL_POLL_MS = 120;

/**
 * Colores literales, a propósito y solo acá: `xterm.js` pinta sobre su propio
 * lienzo (canvas/DOM), fuera de la cascada de Tailwind, así que su `ITheme`
 * necesita valores de color reales, no clases utilitarias -- no hay forma de
 * pasarle `bg-forja-superficie`. Estos hex son una copia literal (no
 * inventada) de la escala `forja.*` de `tailwind.config.ts`: si esa escala
 * cambia, este mapa hay que actualizarlo a mano. Los 16 colores ANSI se
 * mantienen desaturados y contenidos, mismo espíritu "sereno" de §8 (el color
 * es presupuesto escaso) en vez de la paleta por defecto, chillona, de xterm.
 */
const FORJA_TERMINAL_THEME: { light: ITheme; dark: ITheme } = {
  light: {
    background: "#ffffff", // forja.superficie
    foreground: "#3f3f3d",
    cursor: "#3f3f3d",
    cursorAccent: "#ffffff",
    selectionBackground: "#e6e6e4", // forja.borde-suave
    black: "#1c1c1e",
    red: "#c0392b",
    green: "#2f9e57",
    yellow: "#b8860b",
    blue: "#2563eb",
    magenta: "#9333ea",
    cyan: "#0891b2",
    white: "#d9d9d7", // forja.borde
    brightBlack: "#8a8a86", // forja.tenue
    brightRed: "#e11d48",
    brightGreen: "#22c55e",
    brightYellow: "#eab308",
    brightBlue: "#3b82f6",
    brightMagenta: "#a855f7",
    brightCyan: "#06b6d4",
    brightWhite: "#0b0b0c",
  },
  dark: {
    background: "#0b0b0c", // forja.superficie-oscura
    foreground: "#d6d6d4",
    cursor: "#d6d6d4",
    cursorAccent: "#0b0b0c",
    selectionBackground: "#232326", // forja.borde-oscuro-suave
    black: "#0b0b0c",
    red: "#f87171",
    green: "#4ade80",
    yellow: "#facc15",
    blue: "#60a5fa",
    magenta: "#c084fc",
    cyan: "#22d3ee",
    white: "#8f8f96", // forja.tenue-oscuro
    brightBlack: "#2b2b2f", // forja.borde-oscuro
    brightRed: "#fca5a5",
    brightGreen: "#86efac",
    brightYellow: "#fde047",
    brightBlue: "#93c5fd",
    brightMagenta: "#d8b4fe",
    brightCyan: "#67e8f9",
    brightWhite: "#ffffff",
  },
};

/** Firma de los tres módulos de xterm ya cargados, sin volver a pedirlos. */
interface XtermModules {
  Terminal: (typeof import("@xterm/xterm"))["Terminal"];
  FitAddon: (typeof import("@xterm/addon-fit"))["FitAddon"];
  WebglAddon: (typeof import("@xterm/addon-webgl"))["WebglAddon"];
}

let modulesPromise: Promise<XtermModules> | null = null;

/** `import()` real (no `next/dynamic`): ver el docstring del archivo para el porqué. */
function loadXtermModules(): Promise<XtermModules> {
  if (!modulesPromise) {
    modulesPromise = Promise.all([
      import("@xterm/xterm"),
      import("@xterm/addon-fit"),
      import("@xterm/addon-webgl"),
    ]).then(([xterm, fit, webgl]) => ({
      Terminal: xterm.Terminal,
      FitAddon: fit.FitAddon,
      WebglAddon: webgl.WebglAddon,
    }));
  }
  return modulesPromise;
}

interface TerminalInstance {
  term: XTerm;
  fit: FitAddon;
  webgl: WebglAddon | null;
  container: HTMLDivElement;
  resizeObserver: ResizeObserver;
  /** Cursor de lectura PROPIO de este panel -- independiente del `terminalCursor`
   * que `estado-ide.ts` sigue manteniendo para su propia copia (ya limpia de
   * ANSI) del texto. Sondear dos veces la misma sesión es redundante pero
   * inofensivo: son sondeos de solo lectura, cada uno con su propio cursor. */
  nextCursor: number;
}

/**
 * Mismo filtro de eventos que `estado-ide.ts::cleanTerminalOutput` (qué
 * eventos SON salida de proceso, no un evento de estado/sistema) -- pero sin
 * la cadena de `.replace(...)` que le sigue ahí. Mantenerlo como función
 * propia (no importada) es intencional: `cleanTerminalOutput` seguirá
 * limpiando ANSI para quien la use en otra vista, y este panel nunca debe
 * llamarla.
 */
function isTerminalOutputEvent(event: IdeSessionEvent): boolean {
  const type = event.type.toLowerCase();
  const stream = (event.stream ?? "").toLowerCase();
  return (
    type === "output" ||
    type.includes("stdout") ||
    type.includes("stderr") ||
    stream === "stdout" ||
    stream === "stderr"
  );
}

function readIsDark(): boolean {
  if (typeof document === "undefined") return true; // el tema por defecto del producto es oscuro (ver lib/theme.ts)
  return document.documentElement.classList.contains("dark");
}

export function TerminalPanel({
  terminals,
  terminal,
  onSelect,
  onCreate,
}: {
  terminals: IdeSession[];
  terminal: IdeSession | null;
  output: string;
  input: string;
  cursor: number;
  onSelect: (session: IdeSession) => void;
  onCreate: () => void;
  onInput: (value: string) => void;
  onSend: () => void;
}) {
  // `output`/`input`/`cursor`/`onInput`/`onSend` se mantienen en la firma
  // solo para no romper `estado/Editor.tsx` (fuera de mi reparto de
  // archivos, no lo toco): ese contrato de props ya existía y seguirlo
  // exactamente evita tocar un archivo ajeno. Quedan sin usar a propósito --
  // pertenecían al `<input>` de una sola línea que este archivo reemplaza por
  // entrada tecla-por-tecla directa al PTY (`term.onData` más abajo).
  const selectableTerminals = terminals.filter((row) => isLive(row) || row.id === terminal?.id);

  const hostRef = useRef<HTMLDivElement | null>(null);
  const instancesRef = useRef<Map<string, TerminalInstance>>(new Map());
  const [isDark, setIsDark] = useState(readIsDark);
  const isDarkRef = useRef(isDark);
  isDarkRef.current = isDark;

  const terminalId = terminal?.id ?? null;
  const terminalLive = isLive(terminal);

  // Sigue el tema oscuro/claro (`darkMode: "class"` en `tailwind.config.ts`,
  // ver `lib/theme.ts::applyTheme`) para repintar TODAS las terminales ya
  // abiertas cuando la persona cambia de tema a mitad de sesión.
  useEffect(() => {
    if (typeof document === "undefined") return;
    const root = document.documentElement;
    const observer = new MutationObserver(() => setIsDark(root.classList.contains("dark")));
    observer.observe(root, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const theme = isDark ? FORJA_TERMINAL_THEME.dark : FORJA_TERMINAL_THEME.light;
    for (const instance of instancesRef.current.values()) {
      instance.term.options.theme = { ...theme };
    }
  }, [isDark]);

  /** Sondeo de solo-lectura de UNA sesión ya abierta: escribe la salida cruda,
   * ANSI incluido, sin pasar por ningún `.replace(...)` en el camino. */
  const pollOnce = useCallback(async (id: string) => {
    const instance = instancesRef.current.get(id);
    if (!instance) return;
    try {
      const result = await readIdeTerminal(id, instance.nextCursor);
      for (const event of result.events) {
        if (isTerminalOutputEvent(event)) instance.term.write(event.text);
      }
      instance.nextCursor = result.next_cursor;
    } catch {
      // Mejor esfuerzo: un fallo de red puntual se reintenta solo en el
      // siguiente ciclo del intervalo, sin tumbar la terminal ya pintada.
    }
  }, []);

  /** Crea (o reutiliza) la instancia de `xterm.Terminal` de una sesión. */
  const ensureInstance = useCallback(
    async (id: string): Promise<TerminalInstance | null> => {
      const existing = instancesRef.current.get(id);
      if (existing) return existing;

      const host = hostRef.current;
      if (!host) return null;

      const container = document.createElement("div");
      container.style.position = "absolute";
      container.style.inset = "0";
      container.style.display = "none";
      host.appendChild(container);

      const { Terminal, FitAddon: FitAddonCtor, WebglAddon: WebglAddonCtor } = await loadXtermModules();
      // El host pudo haberse desmontado mientras esperábamos la carga diferida.
      if (!hostRef.current) {
        container.remove();
        return null;
      }

      const term = new Terminal({
        // El PTY ya manda `\r\n`/ANSI real (`ide_sessions.py::_read_pty` no
        // reescribe nada); convertir el fin de línea aparte duplicaría saltos.
        convertEol: false,
        cursorBlink: true,
        fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
        fontSize: 12.5,
        scrollback: 5000,
        theme: { ...(isDarkRef.current ? FORJA_TERMINAL_THEME.dark : FORJA_TERMINAL_THEME.light) },
      });

      const fit = new FitAddonCtor();
      term.loadAddon(fit);

      let webgl: WebglAddon | null = null;
      try {
        webgl = new WebglAddonCtor();
        term.loadAddon(webgl);
        const activeWebgl = webgl;
        activeWebgl.onContextLoss(() => {
          // Si el WebView pierde el contexto (p. ej. GPU deshabilitada en
          // Tauri), se desecha el addon y xterm.js vuelve solo al renderer
          // DOM por defecto -- sigue siendo correcto y legible, solo más lento.
          activeWebgl.dispose();
          if (instancesRef.current.get(id)?.webgl === activeWebgl) {
            const current = instancesRef.current.get(id);
            if (current) current.webgl = null;
          }
        });
      } catch {
        // WebGL no disponible en este entorno: se sigue con el renderer DOM
        // por defecto, que ya interpreta ANSI correctamente.
        webgl = null;
      }

      term.open(container);
      fit.fit();

      // Entrada tecla-por-tecla real al PTY (§3.5): `term.onData` entrega lo
      // que xterm.js ya tradujo del teclado -- Ctrl+C -> "\x03", flechas ->
      // "\x1b[A/B/C/D", Tab -> "\t", Escape -> "\x1b", pegado completo tal
      // cual -- y se manda crudo por el MISMO transporte que ya existía
      // (`sendIdeTerminalInput`, `lib/api-ide.ts`), sin negociar un protocolo
      // nuevo. Nunca junta líneas ni espera Enter.
      term.onData((data) => {
        void sendIdeTerminalInput(id, data)
          .then(() => pollOnce(id)) // sondeo inmediato: la tecla se ve al toque, no en el próximo tick de 120 ms
          .catch(() => {
            // Mejor esfuerzo: si un byte no llega, el siguiente sondeo regular
            // ya refleja el estado real del PTY -- no hay nada seguro que
            // "reintentar" sin arriesgar mandar la misma tecla dos veces.
          });
      });

      const resizeObserver = new ResizeObserver(() => fit.fit());
      resizeObserver.observe(container);

      const instance: TerminalInstance = {
        term,
        fit,
        webgl,
        container,
        resizeObserver,
        nextCursor: 0,
      };
      instancesRef.current.set(id, instance);
      return instance;
    },
    [pollOnce],
  );

  /** Muestra la instancia de `id` y oculta el resto -- ninguna se destruye, así
   * que cada terminal conserva su scrollback al volver a ella (§3.5, "varias
   * terminales a la vez, cada una con su estado"). */
  const showOnly = useCallback((id: string | null) => {
    for (const [key, instance] of instancesRef.current) {
      const visible = key === id;
      instance.container.style.display = visible ? "block" : "none";
      if (visible) {
        // El contenedor pudo haber estado en `display:none` (0x0) mientras no
        // era la pestaña activa; sin este `fit()` de nuevo, xterm mide 0
        // filas y pinta un terminal vacío hasta el próximo resize real.
        requestAnimationFrame(() => instance.fit.fit());
      }
    }
  }, []);

  // Instancia (si hace falta), muestra y sondea la terminal seleccionada.
  useEffect(() => {
    if (!terminalId) {
      showOnly(null);
      return;
    }
    let stopped = false;
    void (async () => {
      const instance = await ensureInstance(terminalId);
      if (stopped || !instance) return;
      showOnly(terminalId);
      await pollOnce(terminalId);
    })();

    // Solo la terminal a la vista se sondea en vivo -- ver el docstring del
    // archivo ("varias terminales a la vez") para por qué esto no pierde
    // salida de las que quedan de fondo.
    let timer: number | undefined;
    if (terminalLive) {
      timer = window.setInterval(() => void pollOnce(terminalId), TERMINAL_POLL_MS);
    }
    return () => {
      stopped = true;
      if (timer !== undefined) window.clearInterval(timer);
    };
  }, [terminalId, terminalLive, ensureInstance, showOnly, pollOnce]);

  // Al desmontar el panel entero (p. ej. se colapsa la terminal en
  // `Editor.tsx`, que deja de renderizar este componente), se liberan de
  // verdad los `ResizeObserver`/instancias de xterm -- el scrollback SÍ se
  // pierde en ese caso, porque `Editor.tsx` (fuera de mi reparto) desmonta el
  // árbol entero en vez de solo ocultarlo; nada que este archivo pueda evitar.
  useEffect(() => {
    return () => {
      // `instancesRef` no es un ref de nodo DOM (es un `Map` mutable propio):
      // leer `.current` recién en el cleanup es deliberado, porque ahí es
      // cuando el mapa tiene TODAS las instancias creadas durante la vida del
      // componente. Copiarlo antes (que es lo que pide la regla genérica de
      // `exhaustive-deps`) lo capturaría vacío, en el momento del montaje.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      for (const instance of instancesRef.current.values()) {
        instance.resizeObserver.disconnect();
        instance.webgl?.dispose();
        instance.term.dispose();
        instance.container.remove();
      }
      instancesRef.current.clear();
    };
  }, []);

  return (
    <section className="flex min-h-0 flex-1 flex-col bg-forja-superficie text-slate-800 dark:bg-forja-superficie-oscura dark:text-slate-200">
      <div className="flex h-12 items-center gap-2 border-b border-forja-borde-suave bg-forja-superficie px-3 dark:border-forja-borde-oscuro-suave dark:bg-forja-superficie-oscura">
        <select
          value={terminal?.id ?? ""}
          onChange={(event) => {
            const selected = terminals.find((row) => row.id === event.target.value);
            if (selected) onSelect(selected);
          }}
          className="rounded-md border border-forja-borde bg-forja-superficie px-3 py-1.5 text-xs text-slate-700 outline-none dark:border-forja-borde-oscuro dark:bg-forja-superficie-oscura-elevada dark:text-slate-200"
        >
          <option value="">Sin terminal</option>
          {selectableTerminals.map((row) => (
            <option key={row.id} value={row.id}>
              {row.title || "Terminal"} · {row.status}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={onCreate}
          className="flex items-center gap-1 rounded-md bg-slate-950 px-3 py-1.5 text-xs font-semibold text-white dark:bg-slate-100 dark:text-slate-900"
        >
          <PlusIcon className="h-3 w-3" /> Terminal
        </button>
        <span className="ml-auto flex items-center gap-2 text-[10px] text-forja-tenue dark:text-forja-tenue-oscuro">
          {terminal && isLive(terminal) ? "Conectada · shell total" : terminal ? "Terminal cerrada" : "Sin terminal activa"}
          {terminal ? (
            <span
              className="cursor-help underline decoration-dotted"
              title="El companion todavía no expone un endpoint de resize del PTY (falta ioctl TIOCSWINSZ en ide_sessions.py): este panel ajusta cuánto SE VE, pero el proceso remoto puede seguir creyendo el tamaño con el que arrancó."
            >
              tamaño no sincronizado
            </span>
          ) : null}
        </span>
      </div>
      <div className="relative min-h-0 flex-1 bg-forja-superficie dark:bg-forja-superficie-oscura">
        {/* Contenedor deliberadamente vacío en JSX -- React nunca reconcilia
            hijos suyos acá adentro, así que las instancias de xterm.js que se
            cuelgan a mano con `appendChild` (ver `ensureInstance`) nunca
            chocan con el reconciliador. */}
        <div ref={hostRef} className="absolute inset-0 p-2" onClick={() => instancesRef.current.get(terminalId ?? "")?.term.focus()} />
        {!terminal && (
          <p className="pointer-events-none absolute inset-0 flex items-center justify-center p-5 text-center text-xs text-forja-tenue dark:text-forja-tenue-oscuro">
            Abre una terminal para tener shell total: tu $SHELL, tu $PATH, sin lista blanca de comandos.
          </p>
        )}
      </div>
    </section>
  );
}
