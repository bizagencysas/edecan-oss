"use client";

/**
 * Hilo de conversación del agente de Forge Studio.
 *
 * Por qué existe: hoy cada mensaje del usuario crea una SESIÓN nueva en el
 * companion (ver `apps/companion/edecan_companion/ide_sessions.py`), así que
 * la UI histórica mostraba una lista de sesiones sueltas y el dueño creía
 * que "el chat desaparecía". La sesión ya trae `conversation_id` (se manda
 * al crearla desde `page.tsx`), lo que permite agrupar todas las sesiones de
 * una misma conversación en turnos de UNA sola charla continua. Este
 * componente es esa vista: no decide el agrupamiento (eso lo hace quien lo
 * use, armando la lista de `turns` a partir de las sesiones con el mismo
 * `conversation_id`) — solo la pinta.
 *
 * Diseño: el mensaje del usuario y la respuesta del agente son el contenido
 * — texto suelto, sin cajas, con aire de sobra entre turnos. El trabajo
 * técnico de cada turno (comandos, archivos tocados, salida de procesos)
 * se pliega en UNA línea discreta ("53 eventos · 44 acciones") con una
 * flecha: es rastro de auditoría, no la respuesta, y por eso no compite
 * visualmente con el texto. Las solicitudes de permiso MCP son la única
 * excepción: si necesitan una decisión, se muestran siempre, sin plegar.
 *
 * Conectado desde `page.tsx`: desde que `ide_sessions.SessionManager`
 * reusa una misma sesión para TODA una conversación (`_find_reusable_agent_session`,
 * `apps/companion/edecan_companion/ide_sessions.py`), una sesión ya no es un
 * turno -- es el hilo completo, con varios eventos `user` acumulados dentro
 * del mismo `events[]`. Por eso `AgentThreadTurn` no carga la `IdeSession`
 * entera: quien arma la lista (`page.tsx`) parte los eventos de una sesión
 * en un turno por cada evento `user` que encuentra, y solo marca `live` el
 * último turno de la sesión que sigue corriendo.
 */

import type { RefObject } from "react";

import { ChevronRightIcon, SparklesIcon } from "@/components/icons";
import type { IdeSessionEvent } from "@/lib/api-ide";

import AgentRichText, { IdeBlockCards } from "./AgentRichText";
import { parseIdeBlocks, type IdeBlock } from "./ide-blocks";

/** Un turno = un mensaje del usuario + todo lo que el agente hizo para responderlo, dentro de una sesión. */
export interface AgentThreadTurn {
  /** Único dentro del hilo (p. ej. `${sessionId}:${cursorDelPrimerEvento}`); no es el id de una sesión. */
  id: string;
  events: IdeSessionEvent[];
  /** Solo el turno más reciente de la sesión activa puede seguir en curso. */
  live: boolean;
}

export interface AgentThreadProps {
  /** Turnos en orden cronológico (el más viejo primero). */
  turns: AgentThreadTurn[];
  /** Resoluciones ya conocidas de confirmaciones MCP, por `call_id`. */
  resolvedMcpCalls?: Record<string, boolean>;
  /** Se llama al permitir/denegar una herramienta pedida por el agente. */
  onResolveMcp?: (callId: string, approved: boolean) => void;
  /** Ref opcional para hacer scroll al final del hilo tras un mensaje nuevo. */
  scrollAnchorRef?: RefObject<HTMLDivElement | null>;
  className?: string;
}

function shortClock(value: string): string {
  try {
    return new Intl.DateTimeFormat("es", { hour: "numeric", minute: "2-digit" }).format(
      new Date(value),
    );
  } catch {
    return "";
  }
}

/** Heurística compartida con `page.tsx::looksLikeTechnicalText` (duplicada a propósito: ese archivo no se toca). */
function looksLikeTechnicalText(text: string): boolean {
  return (
    /\b(Traceback|Error:|Exception|SELECT|INSERT|UPDATE|DELETE|FROM|WHERE|pytest|npm|pnpm|yarn|cargo|uv |python|node|exit code|stderr|stdout|warning|failed|BUILD FAILED)\b/i.test(
      text,
    ) || /[{};]|(^|\n)\s*(File |at |\$ |>|\+ |-|@@)/.test(text)
  );
}

function isActionEvent(event: IdeSessionEvent): boolean {
  const type = event.type.toLowerCase();
  const stream = (event.stream ?? "").toLowerCase();
  return (
    type === "command" ||
    type === "tool" ||
    type === "file" ||
    type.startsWith("mcp_") ||
    stream === "command"
  );
}

const EVENT_LABELS: Record<string, string> = {
  status: "Estado",
  progress: "Progreso",
  tool: "Herramienta",
  command: "Comando",
  output: "Salida",
  error: "Error",
  file: "Archivo",
  exit: "Cierre",
};

function eventLabel(event: IdeSessionEvent): string {
  return EVENT_LABELS[event.type.toLowerCase()] ?? event.type.replaceAll("_", " ");
}

function summarize(text: string): string {
  const trimmed = text.trim();
  if (!trimmed) return "(sin contenido)";
  const firstLine = trimmed.split("\n").find(Boolean) ?? trimmed;
  return firstLine.length > 140 ? `${firstLine.slice(0, 137)}…` : firstLine;
}

/** Elige la respuesta final del turno: el contrato explícito `assistant_final` gana siempre;
 * si el turno ya terminó y ninguno llegó (sesiones viejas, sin ese contrato), recupera el mejor
 * texto conversacional disponible sin convertir comandos o logs en respuesta. */
function pickFinalEvent(events: IdeSessionEvent[], live: boolean): IdeSessionEvent | undefined {
  const explicit = [...events].reverse().find((event) => event.type === "assistant_final");
  if (explicit) return explicit;
  if (live) return undefined;
  const candidates = events.filter((event) => {
    if (event.type === "user" || event.type === "mcp_confirmation") return false;
    const type = event.type.toLowerCase();
    const text = event.text.trim();
    if (!text || looksLikeTechnicalText(text)) return false;
    return type === "assistant" || type.includes("completed") || type.includes("done");
  });
  if (!candidates.length) return undefined;
  return [...candidates].sort((a, b) => b.text.trim().length - a.text.trim().length)[0];
}

function ThinkingRow() {
  return (
    <div className="flex items-center gap-2 text-sm text-slate-400">
      <span className="flex gap-1">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-slate-300" />
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-slate-300 [animation-delay:150ms]" />
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-slate-300 [animation-delay:300ms]" />
      </span>
      <span>Edecán está trabajando…</span>
    </div>
  );
}

function UserBubble({
  text,
  timestamp,
  nota,
  tono,
}: {
  text: string;
  timestamp: string;
  /** Estado del mensaje dirigido a mitad de turno; ausente en el que abrió el turno. */
  nota?: string;
  tono?: "espera" | "llego" | "no_llego";
}) {
  const colorDeNota =
    tono === "no_llego" ? "text-rose-500" : tono === "espera" ? "text-amber-600" : "text-slate-400";
  return (
    <div className="flex flex-col items-end gap-1">
      <div className="max-w-[75%] rounded-2xl bg-slate-100 px-4 py-2.5 text-[15px] leading-6 text-slate-900">
        <p className="whitespace-pre-wrap break-words">{text}</p>
      </div>
      <span className="flex items-center gap-1.5 pr-1 text-[11px] text-slate-300">
        {nota && <span className={`font-semibold ${colorDeNota}`}>{nota}</span>}
        {shortClock(timestamp)}
      </span>
    </div>
  );
}

/**
 * Los mensajes que la persona mandó MIENTRAS el turno corría. El motor los
 * anuncia con tres eventos que llevan el texto del mensaje (`user_queued` al
 * recibirlo, `user_delivered` cuando el agente lo lee, `user_undelivered`
 * cuando el turno cerró sin leerlo), no con un evento `user` — así que sin
 * esto quedaban enterrados en la línea plegada de auditoría, junto a la salida
 * de los comandos.
 *
 * Y ahí está el problema que resuelven: la ficha del compositor se va a los
 * pocos segundos, y si el hilo tampoco los muestra, la persona termina sin
 * ninguna prueba de haberlos mandado. Se pinta uno por mensaje (los tres
 * eventos hablan del mismo texto) con el último estado que se sepa de él.
 */
const TIPOS_DIRIGIDOS = new Set(["user_queued", "user_delivered", "user_undelivered"]);

const NOTA_DIRIGIDA = {
  user_queued: { nota: "En cola", tono: "espera" as const },
  user_delivered: { nota: "Lo leyó a mitad del trabajo", tono: "llego" as const },
  user_undelivered: { nota: "No llegó: vuelve a mandarlo", tono: "no_llego" as const },
};

function mensajesDirigidos(events: IdeSessionEvent[]) {
  const filas: Array<{ clave: number; text: string; timestamp: string; tipo: string }> = [];
  const indicePorTexto = new Map<string, number>();
  for (const event of events) {
    if (!TIPOS_DIRIGIDOS.has(event.type)) continue;
    const clave = event.text.trim();
    const existente = indicePorTexto.get(clave);
    if (existente === undefined) {
      indicePorTexto.set(clave, filas.length);
      filas.push({ clave: event.cursor, text: event.text, timestamp: event.timestamp, tipo: event.type });
      continue;
    }
    // El estado más reciente manda: "en cola" se convierte en "lo leyó".
    filas[existente] = { ...filas[existente], timestamp: event.timestamp, tipo: event.type };
  }
  return filas;
}

/**
 * Bloques ricos del turno (`edecan_schemas.ide_blocks`).
 *
 * Llegan por el canal `presentation` del evento — el único que puede acuñar UI
 * — y su `text` es siempre el equivalente en texto. Por eso un evento `blocks`
 * cuyos bloques no validen no se pierde: cae a la línea de auditoría con su
 * texto, que es exactamente lo que la persona habría leído antes.
 */
const TIPO_BLOQUE = "blocks";

function bloquesDelEvento(event: IdeSessionEvent): IdeBlock[] {
  return event.type === TIPO_BLOQUE ? parseIdeBlocks(event.presentation) : [];
}

/** Tope de tarjetas por turno. Más que esto es un volcado, no una respuesta. */
const MAX_BLOQUES_POR_TURNO = 12;

/**
 * Los bloques que se dibujan y los eventos que quedaron representados por ellos.
 *
 * El tope se aplica por EVENTO COMPLETO, y quién lo alcanzó se devuelve junto
 * con los bloques, porque las dos decisiones tienen que salir del mismo corte:
 * la de dibujar y la de ocultar el texto equivalente. Recortando la lista ya
 * aplanada, un evento pasado el tope no se dibujaba Y su texto se ocultaba
 * igual (el filtro de abajo solo miraba si sus bloques eran válidos), así que
 * sus datos desaparecían de la pantalla sin que nada lo dijera.
 */
function bloquesDelTurno(events: IdeSessionEvent[]): {
  bloques: IdeBlock[];
  representados: Set<number>;
} {
  const bloques: IdeBlock[] = [];
  const representados = new Set<number>();
  for (const event of events) {
    const propios = bloquesDelEvento(event);
    if (propios.length === 0) continue;
    // Se corta antes de partir un evento a la mitad: o se dibujan todos sus
    // bloques, o se lee entero como texto.
    if (bloques.length + propios.length > MAX_BLOQUES_POR_TURNO) break;
    bloques.push(...propios);
    representados.add(event.cursor);
  }
  return { bloques, representados };
}

function McpConfirmationCard({
  event,
  resolvedMcpCalls,
  onResolveMcp,
}: {
  event: IdeSessionEvent;
  resolvedMcpCalls: Record<string, boolean>;
  onResolveMcp?: (callId: string, approved: boolean) => void;
}) {
  let request: { call_id?: string; name?: string; arguments?: unknown } = {};
  try {
    request = JSON.parse(event.text) as typeof request;
  } catch {
    return (
      <p role="alert" className="text-sm text-red-600">
        Edecán pidió permiso para usar una herramienta, pero la solicitud llegó dañada.
      </p>
    );
  }
  const callId = request.call_id;
  const resolution = callId ? resolvedMcpCalls[callId] : undefined;
  return (
    <div className="flex items-start gap-3 rounded-xl bg-amber-50 px-4 py-3 text-sm">
      <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-amber-400" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className="font-medium text-amber-900">
          Pide permiso para usar {request.name || "una herramienta externa"}
        </p>
        {resolution === undefined && callId && onResolveMcp ? (
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              onClick={() => onResolveMcp(callId, true)}
              className="rounded-md bg-slate-950 px-3 py-1.5 text-xs font-semibold text-white"
            >
              Permitir
            </button>
            <button
              type="button"
              onClick={() => onResolveMcp(callId, false)}
              className="rounded-md border border-slate-300 px-3 py-1.5 text-xs font-semibold text-slate-700"
            >
              Denegar
            </button>
          </div>
        ) : (
          <p className="mt-1 text-xs font-medium text-amber-700">
            {resolution === false ? "Denegada." : resolution ? "Permitida para esta ejecución." : "Esperando resolución."}
          </p>
        )}
      </div>
    </div>
  );
}

/** La línea plegada de auditoría: "N eventos · M acciones", sin ícono, sin caja — solo texto discreto
 * que se expande. Vive DESPUÉS de la respuesta a propósito, para no competir con ella. */
function TurnWorkDetails({
  events,
  live,
  hasError,
}: {
  events: IdeSessionEvent[];
  live: boolean;
  hasError: boolean;
}) {
  if (!events.length) return null;
  const actionCount = events.filter(isActionEvent).length;
  const eventWord = events.length === 1 ? "evento" : "eventos";
  const actionWord = actionCount === 1 ? "acción" : "acciones";
  return (
    <details className="group">
      <summary
        className={`flex w-fit cursor-pointer list-none items-center gap-1.5 text-xs select-none marker:hidden [&::-webkit-details-marker]:hidden ${
          hasError ? "text-red-500/80 hover:text-red-600" : "text-slate-400 hover:text-slate-600"
        }`}
      >
        <ChevronRightIcon className="h-3 w-3 shrink-0 transition-transform duration-150 group-open:rotate-90" />
        <span>
          {events.length} {eventWord} · {actionCount} {actionWord}
          {live ? " · en curso" : ""}
          {hasError ? " · con errores" : ""}
        </span>
      </summary>
      <ul className="mt-2 max-h-80 overflow-y-auto border-l border-slate-100 pl-4">
        {events.map((event) => (
          <li key={event.cursor} className="border-b border-slate-50 py-2 last:border-b-0">
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-[11px] font-medium uppercase tracking-wide text-slate-400">
                {eventLabel(event)}
              </span>
              <span className="shrink-0 text-[11px] text-slate-300">{shortClock(event.timestamp)}</span>
            </div>
            <details className="mt-1">
              <summary className="cursor-pointer select-none text-xs text-slate-500 marker:hidden [&::-webkit-details-marker]:hidden">
                {summarize(event.text)}
              </summary>
              <pre className="mt-1.5 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md bg-slate-50 p-2.5 font-mono text-[11px] leading-5 text-slate-600">
                {event.text}
              </pre>
            </details>
          </li>
        ))}
      </ul>
    </details>
  );
}

function TurnRow({
  turn,
  resolvedMcpCalls,
  onResolveMcp,
}: {
  turn: AgentThreadTurn;
  resolvedMcpCalls: Record<string, boolean>;
  onResolveMcp?: (callId: string, approved: boolean) => void;
}) {
  const { events, live } = turn;
  const userEvent = events.find((event) => event.type === "user");
  const confirmations = events.filter((event) => event.type === "mcp_confirmation");
  const dirigidos = mensajesDirigidos(events);
  const finalEvent = pickFinalEvent(events, live);
  const { bloques, representados } = bloquesDelTurno(events);
  const workEvents = events.filter(
    (event) =>
      event.type !== "user" &&
      event.type !== "mcp_confirmation" &&
      !TIPOS_DIRIGIDOS.has(event.type) &&
      // Un evento `blocks` que SÍ se dibujó no se repite abajo en la línea de
      // auditoría con su texto equivalente. Se mira evento por evento y contra
      // lo que de verdad se dibujó: si uno no se pudo dibujar —malformado o
      // pasado el tope del turno— ESE tiene que seguir viéndose como texto.
      !(event.type === TIPO_BLOQUE && representados.has(event.cursor)) &&
      event.cursor !== finalEvent?.cursor,
  );
  const hasError = workEvents.some(
    (event) => event.type.toLowerCase() === "error" || (event.stream ?? "").toLowerCase() === "stderr",
  );

  return (
    <div className="flex flex-col gap-4">
      {userEvent ? <UserBubble text={userEvent.text} timestamp={userEvent.timestamp} /> : null}
      {dirigidos.map((fila) => {
        const marca = NOTA_DIRIGIDA[fila.tipo as keyof typeof NOTA_DIRIGIDA];
        return (
          <UserBubble
            key={fila.clave}
            text={fila.text}
            timestamp={fila.timestamp}
            nota={marca?.nota}
            tono={marca?.tono}
          />
        );
      })}
      {confirmations.map((event) => (
        <McpConfirmationCard
          key={event.cursor}
          event={event}
          resolvedMcpCalls={resolvedMcpCalls}
          onResolveMcp={onResolveMcp}
        />
      ))}
      {finalEvent ? (
        <div className="text-[15px] leading-7 text-slate-800">
          <AgentRichText text={finalEvent.text.trim()} />
        </div>
      ) : live ? (
        <ThinkingRow />
      ) : workEvents.length > 0 && bloques.length === 0 ? (
        <p className="text-sm italic text-slate-400">Este turno no dejó una respuesta de texto.</p>
      ) : null}
      {bloques.length > 0 && <IdeBlockCards blocks={bloques} />}
      <TurnWorkDetails events={workEvents} live={live} hasError={hasError} />
    </div>
  );
}

export default function AgentThread({
  turns,
  resolvedMcpCalls = {},
  onResolveMcp,
  scrollAnchorRef,
  className,
}: AgentThreadProps) {
  if (turns.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-2 py-20 text-center">
        <SparklesIcon className="h-5 w-5 text-slate-300" />
        <p className="text-sm text-slate-400">Aún no hay mensajes en esta conversación.</p>
      </div>
    );
  }
  return (
    <div className={`flex flex-col gap-10 ${className ?? ""}`}>
      {turns.map((turn) => (
        <TurnRow
          key={turn.id}
          turn={turn}
          resolvedMcpCalls={resolvedMcpCalls}
          onResolveMcp={onResolveMcp}
        />
      ))}
      {scrollAnchorRef ? <div ref={scrollAnchorRef} /> : null}
    </div>
  );
}
