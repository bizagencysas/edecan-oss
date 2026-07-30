"use client";

/**
 * Torre de control de Forge Studio: qué está corriendo AHORA en todos los
 * proyectos a la vez, y desde dónde dirigir a cualquiera de ellos sin perder
 * de vista a los demás.
 *
 * POR QUÉ EXISTE: el estudio ya sostiene varias sesiones de agente en paralelo
 * (`ide_sessions.SessionManager` las guarda todas, y `GET /v1/ide/agents` sin
 * `workspace_id` las devuelve todas), pero la pantalla solo sabía mostrar UNA:
 * la de la conversación abierta. Con cuatro o siete agentes trabajando al
 * mismo tiempo, eso obliga a entrar a cada conversación para saber si sigue
 * viva — y entrar a una es dejar de ver las otras. Este panel es la vista que
 * faltaba.
 *
 * Tres decisiones de diseño, todas por el mismo motivo (que se pueda leer de
 * un vistazo con siete tarjetas en pantalla):
 *  1. Lo que bloquea va primero. Un agente esperando el visto bueno de alguien
 *     es el único que de verdad reclama atención; corriendo, después; lo ya
 *     terminado, al final.
 *  2. El texto para dirigir vive DENTRO de la tarjeta. Mandar una idea a otro
 *     agente no debe costar cambiar de conversación (y perder de vista el
 *     resto), que es exactamente lo que se pidió evitar.
 *  3. Sin tablas ni menús desplegables: una fila = un agente, con su estado en
 *     color y su reloj corriendo.
 *
 * Este componente no habla con la red: recibe filas ya armadas y avisa hacia
 * arriba. Quien lo usa (`app/(app)/app/ide/page.tsx`) es quien consulta,
 * encola y detiene.
 */

import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { ChevronRightIcon, SendIcon, SparklesIcon, SquareIcon, XIcon } from "@/components/icons";
import { Spinner } from "@/components/ui";

function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

/** Una ejecución de agente, ya resuelta contra proyectos y conversaciones. */
export interface AgentRunSummary {
  sessionId: string;
  conversationId: string | null;
  /** Título de la conversación (o de la sesión, si la conversación no tiene). */
  titulo: string;
  /** `IdeSession.status` tal cual lo manda el companion. */
  estado: string;
  /** Sigue viva: ni terminó ni la cerraron. */
  viva: boolean;
  /** Nombre del proyecto, o `null` para las conversaciones sin proyecto. */
  proyecto: string | null;
  /** Carpeta autorizada sobre la que trabaja. */
  carpeta: string;
  modelo: string | null;
  startedAt: string;
  endedAt: string | null;
  /** Mensajes míos esperando turno en esta conversación. */
  enCola: number;
  /** Es la conversación abierta ahora mismo en el panel principal. */
  activa: boolean;
}

export interface AgentActivityCenterProps {
  runs: AgentRunSummary[];
  /** Primera carga: todavía no se sabe si hay algo corriendo. */
  loading?: boolean;
  /** Abre esa conversación en el panel principal. */
  onGo: (run: AgentRunSummary) => void;
  /** Corta el turno en curso de esa sesión. */
  onStop: (run: AgentRunSummary) => void;
  /** Manda un mensaje a esa conversación sin salir de este panel. */
  onDirect: (run: AgentRunSummary, texto: string) => void;
  /** Cierra el panel (en pantallas anchas queda fijo y esto lo esconde). */
  onClose?: () => void;
  className?: string;
}

// --- Estado -> lenguaje humano ---------------------------------------------

/** Espejo de `_AGENT_BUSY_STATUSES` (`ide_sessions.py`): lo que todavía no terminó. */
const ESTADOS_TRABAJANDO = new Set(["starting", "running"]);
/** El agente se detuvo a pedir permiso: es el único estado que reclama a una persona. */
const ESTADOS_ESPERANDO = new Set(["plan_pending"]);
const ESTADOS_FALLIDOS = new Set(["failed", "interrupted"]);

type Tono = "trabajando" | "esperando" | "listo" | "falla" | "quieto";

function tonoDe(run: AgentRunSummary): Tono {
  if (ESTADOS_ESPERANDO.has(run.estado)) return "esperando";
  if (ESTADOS_FALLIDOS.has(run.estado)) return "falla";
  if (run.viva || ESTADOS_TRABAJANDO.has(run.estado)) return "trabajando";
  if (run.estado === "completed") return "listo";
  return "quieto";
}

const ETIQUETA_DE_ESTADO: Record<string, string> = {
  starting: "Arrancando",
  running: "Trabajando",
  plan_pending: "Te espera",
  completed: "Listo",
  failed: "Falló",
  cancelled: "Detenido",
  closed: "Cerrado",
  interrupted: "Se cortó",
};

function etiquetaDeEstado(run: AgentRunSummary): string {
  return ETIQUETA_DE_ESTADO[run.estado] ?? run.estado;
}

const COLOR_DE_TONO: Record<Tono, { barra: string; punto: string; texto: string }> = {
  trabajando: {
    barra: "bg-amber-400",
    punto: "bg-amber-500",
    texto: "text-amber-700 dark:text-amber-300",
  },
  esperando: {
    barra: "bg-brand-500",
    punto: "bg-brand-500",
    texto: "text-brand-700 dark:text-brand-300",
  },
  listo: {
    barra: "bg-emerald-400",
    punto: "bg-emerald-500",
    texto: "text-emerald-700 dark:text-emerald-300",
  },
  falla: {
    barra: "bg-rose-400",
    punto: "bg-rose-500",
    texto: "text-rose-700 dark:text-rose-300",
  },
  quieto: {
    barra: "bg-slate-200 dark:bg-slate-700",
    punto: "bg-slate-300 dark:bg-slate-600",
    texto: "text-slate-400",
  },
};

/** Orden de urgencia: lo que me necesita, lo que corre, y al final lo que ya pasó. */
const PESO_DE_TONO: Record<Tono, number> = {
  esperando: 0,
  trabajando: 1,
  falla: 2,
  listo: 3,
  quieto: 4,
};

function transcurrido(desde: string, hasta: number): string {
  const inicio = Date.parse(desde);
  if (!Number.isFinite(inicio)) return "";
  const segundos = Math.max(0, Math.floor((hasta - inicio) / 1000));
  if (segundos < 60) return `${segundos}s`;
  const minutos = Math.floor(segundos / 60);
  if (minutos < 60) return `${minutos}m ${String(segundos % 60).padStart(2, "0")}s`;
  const horas = Math.floor(minutos / 60);
  return `${horas}h ${String(minutos % 60).padStart(2, "0")}m`;
}

const SIN_PROYECTO = "Sin proyecto";

// --- Panel ------------------------------------------------------------------

export function AgentActivityCenter({
  runs,
  loading = false,
  onGo,
  onStop,
  onDirect,
  onClose,
  className,
}: AgentActivityCenterProps) {
  const [soloAhora, setSoloAhora] = useState(true);
  const [dirigiendo, setDirigiendo] = useState<string | null>(null);
  const [borrador, setBorrador] = useState("");
  const [ahora, setAhora] = useState(() => Date.now());

  const hayVivos = runs.some((run) => run.viva);

  // El reloj solo corre si hay algo vivo que contar: con todo terminado, el
  // tiempo transcurrido ya es un número fijo y no hace falta repintar.
  useEffect(() => {
    if (!hayVivos) return;
    const timer = window.setInterval(() => setAhora(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [hayVivos]);

  const resumen = useMemo(() => {
    let trabajando = 0;
    let esperando = 0;
    let enCola = 0;
    for (const run of runs) {
      const tono = tonoDe(run);
      if (tono === "trabajando") trabajando += 1;
      if (tono === "esperando") esperando += 1;
      enCola += run.enCola;
    }
    return { trabajando, esperando, enCola };
  }, [runs]);

  const grupos = useMemo(() => {
    const visibles = soloAhora ? runs.filter((run) => run.viva || run.enCola > 0) : runs;
    const ordenados = [...visibles].sort((a, b) => {
      const peso = PESO_DE_TONO[tonoDe(a)] - PESO_DE_TONO[tonoDe(b)];
      if (peso !== 0) return peso;
      return (Date.parse(b.startedAt) || 0) - (Date.parse(a.startedAt) || 0);
    });
    const mapa = new Map<string, AgentRunSummary[]>();
    for (const run of ordenados) {
      const clave = run.proyecto ?? SIN_PROYECTO;
      const lista = mapa.get(clave);
      if (lista) lista.push(run);
      else mapa.set(clave, [run]);
    }
    return [...mapa.entries()];
  }, [runs, soloAhora]);

  function enviarDesdeTarjeta(run: AgentRunSummary) {
    const texto = borrador.trim();
    if (!texto) return;
    onDirect(run, texto);
    setBorrador("");
    setDirigiendo(null);
  }

  return (
    <section
      aria-label="Ejecuciones en curso"
      className={cx(
        "flex h-full min-h-0 w-full flex-col overflow-hidden border-l border-[#dededc] bg-[#fbfbfa] dark:border-slate-800 dark:bg-slate-900",
        className,
      )}
    >
      <header className="shrink-0 border-b border-[#dededc] px-4 pb-3 pt-3.5 dark:border-slate-800">
        <div className="flex items-center justify-between gap-2">
          <h2 className="flex items-center gap-2 text-sm font-bold text-slate-900 dark:text-slate-100">
            <SparklesIcon className="h-4 w-4 text-slate-400" />
            Ejecuciones
          </h2>
          <div className="flex items-center gap-1">
            {loading && <Spinner className="h-3.5 w-3.5 text-slate-400" />}
            {onClose && (
              <button
                type="button"
                onClick={onClose}
                aria-label="Cerrar el panel de ejecuciones"
                className="rounded-md p-1.5 text-slate-400 hover:bg-[#efefed] hover:text-slate-700 dark:hover:bg-slate-800 dark:hover:text-slate-200"
              >
                <XIcon className="h-4 w-4" />
              </button>
            )}
          </div>
        </div>

        <p className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-500 dark:text-slate-400">
          <span className="inline-flex items-center gap-1.5 font-semibold text-slate-700 dark:text-slate-200">
            <span
              className={cx(
                "h-1.5 w-1.5 rounded-full",
                resumen.trabajando > 0 ? "animate-pulse bg-amber-500" : "bg-slate-300 dark:bg-slate-600",
              )}
              aria-hidden="true"
            />
            {resumen.trabajando} trabajando
          </span>
          {resumen.esperando > 0 && (
            <>
              <span aria-hidden="true">·</span>
              <span className="font-semibold text-brand-600 dark:text-brand-300">
                {resumen.esperando} te espera{resumen.esperando === 1 ? "" : "n"}
              </span>
            </>
          )}
          {resumen.enCola > 0 && (
            <>
              <span aria-hidden="true">·</span>
              <span>
                {resumen.enCola} mensaje{resumen.enCola === 1 ? "" : "s"} en cola
              </span>
            </>
          )}
        </p>

        <div className="mt-3 flex rounded-lg border border-[#e2e2e0] bg-white p-0.5 dark:border-slate-700 dark:bg-slate-800">
          {([
            [true, "Ahora"],
            [false, "Todo"],
          ] as const).map(([valor, etiqueta]) => (
            <button
              key={etiqueta}
              type="button"
              onClick={() => setSoloAhora(valor)}
              aria-pressed={soloAhora === valor}
              className={cx(
                "flex-1 rounded-md px-3 py-1 text-xs font-semibold transition-colors",
                soloAhora === valor
                  ? "bg-slate-950 text-white dark:bg-slate-100 dark:text-slate-900"
                  : "text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-100",
              )}
            >
              {etiqueta}
            </button>
          ))}
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-3 py-3 thin-scrollbar">
        {grupos.length === 0 ? (
          <EstadoVacio soloAhora={soloAhora} loading={loading} />
        ) : (
          <div className="space-y-4">
            {grupos.map(([proyecto, filas]) => (
              <div key={proyecto}>
                <p className="px-1 pb-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                  {proyecto}
                </p>
                <ul className="space-y-1.5">
                  {filas.map((run) => (
                    <li key={run.sessionId}>
                      <RunCard
                        run={run}
                        ahora={ahora}
                        dirigiendo={dirigiendo === run.sessionId}
                        borrador={borrador}
                        onBorrador={setBorrador}
                        onAbrirDirigir={() => {
                          setDirigiendo((actual) => (actual === run.sessionId ? null : run.sessionId));
                          setBorrador("");
                        }}
                        onEnviar={() => enviarDesdeTarjeta(run)}
                        onGo={() => onGo(run)}
                        onStop={() => onStop(run)}
                      />
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
      </div>

      <footer className="shrink-0 border-t border-[#dededc] px-4 py-2.5 text-[11px] leading-4 text-slate-400 dark:border-slate-800">
        Escribe en cualquier tarjeta para dirigir a ese agente: el mensaje entra
        cuando termine su vuelta, sin cortarle el trabajo.
      </footer>
    </section>
  );
}

function EstadoVacio({ soloAhora, loading }: { soloAhora: boolean; loading: boolean }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-14 text-center">
      <SparklesIcon className="h-5 w-5 text-slate-300" />
      <p className="text-xs leading-5 text-slate-400">
        {loading
          ? "Buscando trabajos en curso…"
          : soloAhora
            ? "Nadie está trabajando ahora mismo. Manda un mensaje y esta lista se llena sola."
            : "Todavía no hay ninguna ejecución registrada."}
      </p>
    </div>
  );
}

function RunCard({
  run,
  ahora,
  dirigiendo,
  borrador,
  onBorrador,
  onAbrirDirigir,
  onEnviar,
  onGo,
  onStop,
}: {
  run: AgentRunSummary;
  ahora: number;
  dirigiendo: boolean;
  borrador: string;
  onBorrador: (valor: string) => void;
  onAbrirDirigir: () => void;
  onEnviar: () => void;
  onGo: () => void;
  onStop: () => void;
}) {
  const tono = tonoDe(run);
  const color = COLOR_DE_TONO[tono];
  const campoRef = useRef<HTMLTextAreaElement | null>(null);
  const reloj = transcurrido(run.startedAt, run.endedAt ? Date.parse(run.endedAt) : ahora);

  useEffect(() => {
    if (dirigiendo) campoRef.current?.focus();
  }, [dirigiendo]);

  function alTeclear(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      onEnviar();
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      onAbrirDirigir();
    }
  }

  return (
    <article
      className={cx(
        "group overflow-hidden rounded-lg border bg-white transition-shadow dark:bg-slate-950/40",
        run.activa
          ? "border-slate-400 shadow-panel dark:border-slate-500"
          : "border-[#e2e2e0] hover:border-[#cfcfcc] dark:border-slate-800 dark:hover:border-slate-700",
      )}
    >
      <div className="flex">
        <span className={cx("w-0.5 shrink-0", color.barra)} aria-hidden="true" />
        <div className="min-w-0 flex-1 px-2.5 py-2">
          <button
            type="button"
            onClick={onGo}
            className="flex w-full min-w-0 items-start gap-2 text-left"
            title={`Abrir «${run.titulo}»`}
          >
            <span className="min-w-0 flex-1">
              <span className="flex items-center gap-1.5">
                <span className="truncate text-xs font-semibold text-slate-800 dark:text-slate-100">
                  {run.titulo}
                </span>
                {run.activa && (
                  <span className="shrink-0 rounded bg-slate-100 px-1 py-0.5 text-[9px] font-bold uppercase tracking-wide text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                    Abierta
                  </span>
                )}
              </span>
              <span className="mt-0.5 flex items-center gap-1.5 text-[11px] leading-4">
                <span
                  className={cx(
                    "h-1.5 w-1.5 shrink-0 rounded-full",
                    color.punto,
                    tono === "trabajando" && "animate-pulse",
                  )}
                  aria-hidden="true"
                />
                <span className={cx("shrink-0 font-semibold", color.texto)}>
                  {etiquetaDeEstado(run)}
                </span>
                {reloj && <span className="shrink-0 tabular-nums text-slate-400">{reloj}</span>}
                <span className="truncate text-slate-400" title={run.carpeta}>
                  · {run.carpeta}
                </span>
              </span>
            </span>
            <ChevronRightIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-300 group-hover:text-slate-500" />
          </button>

          <div className="mt-1.5 flex items-center gap-1.5">
            {/* Sin conversación no se puede dirigir sin romper la continuidad
                del hilo: mandar sin `conversation_id` abriría uno nuevo en vez
                de sumarse a este (ver `_find_reusable_agent_session`). */}
            {run.conversationId && (
              <button
                type="button"
                onClick={onAbrirDirigir}
                aria-expanded={dirigiendo}
                className={cx(
                  "rounded-md border px-2 py-0.5 text-[11px] font-semibold transition-colors",
                  dirigiendo
                    ? "border-slate-900 bg-slate-900 text-white dark:border-slate-100 dark:bg-slate-100 dark:text-slate-900"
                    : "border-[#e2e2e0] text-slate-500 hover:bg-[#f7f7f6] dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800",
                )}
              >
                Dirigir
              </button>
            )}
            {run.viva && (
              <button
                type="button"
                onClick={onStop}
                className="flex items-center gap-1 rounded-md border border-[#e2e2e0] px-2 py-0.5 text-[11px] font-semibold text-slate-500 hover:border-rose-200 hover:bg-rose-50 hover:text-rose-600 dark:border-slate-700 dark:text-slate-400 dark:hover:border-rose-900 dark:hover:bg-rose-950/40 dark:hover:text-rose-300"
              >
                <SquareIcon className="h-2.5 w-2.5" />
                Detener
              </button>
            )}
            {run.enCola > 0 && (
              <span className="ml-auto shrink-0 rounded-md bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700 dark:bg-amber-950/40 dark:text-amber-300">
                {run.enCola} en cola
              </span>
            )}
            {run.modelo && run.enCola === 0 && (
              <span className="ml-auto truncate text-[10px] text-slate-400" title={run.modelo}>
                {run.modelo.split("/").at(-1)}
              </span>
            )}
          </div>

          {dirigiendo && (
            <div className="mt-2 rounded-lg border border-[#e2e2e0] bg-[#fbfbfa] p-1.5 dark:border-slate-700 dark:bg-slate-900">
              <textarea
                ref={campoRef}
                value={borrador}
                onChange={(event) => onBorrador(event.target.value)}
                onKeyDown={alTeclear}
                rows={2}
                placeholder="Dile qué cambiar, sin cortarle el trabajo…"
                className="w-full resize-none bg-transparent px-1 py-0.5 text-xs leading-5 text-slate-800 outline-none placeholder:text-slate-400 dark:text-slate-100"
              />
              <div className="flex items-center justify-between gap-2 pl-1">
                <span className="text-[10px] text-slate-400">Entra en la vuelta siguiente</span>
                <button
                  type="button"
                  onClick={onEnviar}
                  disabled={!borrador.trim()}
                  aria-label="Mandar a este agente"
                  className="grid h-6 w-6 place-items-center rounded-md bg-slate-900 text-white hover:bg-slate-700 disabled:opacity-30 dark:bg-slate-100 dark:text-slate-900"
                >
                  <SendIcon className="h-3 w-3" />
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </article>
  );
}

export default AgentActivityCenter;
