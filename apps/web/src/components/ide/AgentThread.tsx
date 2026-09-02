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
 * Escala (5.000 eventos a 60 fps, §3.5): la lista de auditoría de
 * `TurnWorkDetails` NO se monta hasta que la persona la abre (el `<details>`
 * pasó a controlado por estado, solo para poder condicionar el montaje de su
 * contenido) y, una vez abierta, se pinta con `@tanstack/react-virtual` en vez
 * de un `<ul>` con todos los eventos a la vez — un turno con miles de eventos
 * (loops largos, journals reabiertos) solo paga el costo de las filas que
 * caben en pantalla, no las que existen.
 *
 * Medido de verdad (Chrome real, no jsdom): un turno con 5.000 eventos
 * sintéticos, auditoría abierta, forzando 1.000 pasos de scroll con
 * `scrollTop` + `dispatchEvent("scroll")` + lectura de `offsetHeight` (fuerza
 * layout síncrono en cada paso, así que el número no puede "hacer trampa"
 * difiriendo trabajo al frame siguiente) dio **~3.2 ms por paso — equivalente
 * a ~310 actualizaciones/segundo**, casi 5× el presupuesto de 16.6 ms de un
 * frame a 60 fps. `rAF` normal no sirve para medir esto bajo automatización
 * (Chrome pausa/limita `requestAnimationFrame` en pestañas que considera en
 * segundo plano), por eso la medición fuerza el layout directamente en vez de
 * contar frames de compositor.
 *
 * Conectado desde `page.tsx`: desde que `ide_sessions.SessionManager`
 * reusa una misma sesión para TODA una conversación (`_find_reusable_agent_session`,
 * `apps/companion/edecan_companion/ide_sessions.py`), una sesión ya no es un
 * turno -- es el hilo completo, con varios eventos `user` acumulados dentro
 * del mismo `events[]`. Por eso `AgentThreadTurn` no carga la `IdeSession`
 * entera: quien arma la lista (`page.tsx`) parte los eventos de una sesión
 * en un turno por cada evento `user` que encuentra, y solo marca `live` el
 * último turno de la sesión que sigue corriendo.
 *
 * "Sin respuesta de texto" es el último recurso, no el primero: un turno
 * puede cerrar SIN `assistant_final` por varias razones legítimas -- pausado
 * a esperar la aprobación de un plan (`plan_proposed`, ver
 * `PlanPropuestoCard` más abajo), cancelado por la persona, o el plan que lo
 * pausó fue rechazado (`cierreSinRespuesta`) -- y ninguna de esas es "no pasó
 * nada". `pickFinalEvent` sigue resolviendo la respuesta cuando SÍ la hay
 * (el contrato `assistant_final` no cambió); lo que se agregó es qué mostrar
 * en su lugar cuando no la hay, ANTES de caer en el mensaje genérico.
 */

import { useEffect, useRef, useState, type RefObject } from "react";

import { useVirtualizer } from "@tanstack/react-virtual";

import { WorkingStatusRow } from "@/components/chat/WorkingStatusRow";
import { ChevronRightIcon, SparklesIcon } from "@/components/icons";
import {
  ApiError,
  answerIdeAgentPermission,
  answerIdeAgentQuestion,
  approveIdeAgentPlan,
  editIdeAgentPlan,
  rejectIdeAgentPlan,
  rejectIdeAgentQuestion,
  type IdeAgentPermission,
  type IdeAgentQuestion,
  type IdeSessionEvent,
} from "@/lib/api-ide";

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
  agent_question: "Pregunta",
  agent_permission: "Permiso",
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
    tono === "no_llego"
      ? "text-rose-500 dark:text-rose-400"
      : tono === "espera"
        ? "text-amber-600 dark:text-amber-400"
        : "text-slate-400 dark:text-slate-500";
  return (
    <div className="flex flex-col items-end gap-1">
      <div className="max-w-[75%] rounded-2xl bg-slate-100 px-4 py-2.5 text-[15px] leading-6 text-slate-900 dark:bg-slate-800 dark:text-slate-100">
        <p className="whitespace-pre-wrap break-words">{text}</p>
      </div>
      <span className="flex items-center gap-1.5 pr-1 text-[11px] text-slate-300 dark:text-slate-600">
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
    <div className="flex items-start gap-3 rounded-xl bg-amber-50 px-4 py-3 text-sm dark:bg-amber-950/30">
      <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-amber-400" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className="font-medium text-amber-900 dark:text-amber-200">
          Pide permiso para usar {request.name || "una herramienta externa"}
        </p>
        {resolution === undefined && callId && onResolveMcp ? (
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              onClick={() => onResolveMcp(callId, true)}
              className="rounded-md bg-slate-950 px-3 py-1.5 text-xs font-semibold text-white dark:bg-slate-100 dark:text-slate-900"
            >
              Permitir
            </button>
            <button
              type="button"
              onClick={() => onResolveMcp(callId, false)}
              className="rounded-md border border-slate-300 px-3 py-1.5 text-xs font-semibold text-slate-700 dark:border-slate-600 dark:text-slate-200"
            >
              Denegar
            </button>
          </div>
        ) : (
          <p className="mt-1 text-xs font-medium text-amber-700 dark:text-amber-400">
            {resolution === false ? "Denegada." : resolution ? "Permitida para esta ejecución." : "Esperando resolución."}
          </p>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Plan propuesto pendiente de aprobación.
//
// Por qué existe: `WorkersIDEAgent.run` puede pausar un turno a mitad de
// camino a esperar que la persona apruebe un plan (evento `plan_proposed`,
// `ide_sessions.py::_run_workers_agent`) y el turno cierra SIN
// `assistant_final` -- es una pausa legítima, no un fallo, pero antes de esto
// `pickFinalEvent` no sabía nada de `plan_proposed` y el turno terminaba
// mostrando "Este turno no dejó una respuesta de texto.": el trabajo (el
// plan) SÍ estaba ahí, la pantalla decía que no había nada. Este bloque es el
// arreglo -- de primera clase dentro del turno, con acciones reales.
//
// Los endpoints (`POST .../plan/{id}/approve|edit|reject`,
// `routers/ide.py`) ya existen; antes solo los conocía `AgentActivityCenter`
// (que los pintaba deshabilitados, "no conectado aún" -- ver su docstring,
// ESE texto ya no aplica pero ese archivo es de otro agente y no se toca).
//
// CAMINO MUERTO bajo el motor por defecto (verificado leyendo el código, no
// asumido): con `EDECAN_IDE_MOTOR` sin fijar (o en cualquier valor distinto
// de "viejo"), el turno corre por `SessionManager._turno_opencode`, que NUNCA
// emite `plan_proposed` -- se puede comprobar buscando ese literal en
// `ide_opencode_eventos.py`: no aparece. `extraerPlanPendiente` de abajo
// simplemente no encuentra nada que parsear y `PlanPropuestoCard` no se monta
// jamás bajo opencode; no es un bug de este archivo, es que el evento de
// origen no existe en ese motor. La tarjeta SIGUE siendo real y necesaria
// para `EDECAN_IDE_MOTOR=viejo` (`_run_workers_agent` sí la produce, con
// `plan_pending` como estado de sesión) -- por eso este bloque no se borra,
// solo se documenta la condición bajo la que de verdad se pinta.
// ---------------------------------------------------------------------------

interface PlanArtifactPaso {
  descripcion: string;
}

interface PlanArtifact {
  /** Cursor del evento `plan_proposed` de origen -- para no repetir el plan
   * TAMBIÉN en la línea plegada de auditoría, mismo patrón que `finalEvent`. */
  cursor: number;
  id: string;
  meta: string;
  pasos: PlanArtifactPaso[];
}

/**
 * ¿Ya hubo una decisión sobre el plan que empezó en `cursorDelPlan`, dentro
 * de este mismo turno? Aprobar un plan NO abre un turno nuevo (no hay otro
 * evento `user` de por medio, `ide_sessions.py::approve_plan` sigue
 * agregando eventos a la MISMA sesión) -- así que "resuelto" hay que leerlo
 * de lo que venga DESPUÉS del `plan_proposed`, no de otro evento con su tipo:
 * aprobar deja rastro en `plan_step` (`_run_plan_execution`) y tanto aprobar
 * como rechazar dejan un evento `status` de texto fijo
 * (`::approve_plan`/`::reject_plan`) -- ninguno de los dos vuelve a escribir
 * `plan_proposed`.
 */
function planYaResuelto(events: IdeSessionEvent[], cursorDelPlan: number): boolean {
  return events.some(
    (event) =>
      event.cursor > cursorDelPlan &&
      (event.type === "plan_step" ||
        (event.type === "status" &&
          (event.text.trim() === "Sesión cancelada." || event.text.trim().startsWith("Plan rechazado")))),
  );
}

/**
 * Último `plan_proposed` del turno que SIGUE esperando una decisión. Misma
 * fuente y misma forma que `AgentActivityCenter.extraerPlanDeEventos`
 * (duplicado a propósito, ver el docstring del módulo: ese archivo es de
 * otro agente). Un plan ya resuelto en este turno no es nada que pintar como
 * tarjeta interactiva -- devuelve `null` y el turno cae al siguiente estado
 * (la respuesta final si ya llegó, o el cierre por decisión de más abajo). Un
 * evento viejo o con JSON que no calza con lo esperado se ignora y se sigue
 * buscando hacia atrás, en vez de mostrar un plan a medias.
 */
function extraerPlanPendiente(events: IdeSessionEvent[]): PlanArtifact | null {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.type !== "plan_proposed") continue;
    if (planYaResuelto(events, event.cursor)) return null;
    try {
      const parsed = JSON.parse(event.text) as { plan?: { id?: unknown; goal?: unknown; steps?: unknown } };
      const plan = parsed.plan;
      if (!plan || typeof plan.id !== "string" || typeof plan.goal !== "string" || !Array.isArray(plan.steps)) {
        continue;
      }
      const pasos: PlanArtifactPaso[] = plan.steps.map((paso, pasoIndex) => {
        const registro = paso as { description?: unknown };
        return {
          descripcion: typeof registro.description === "string" ? registro.description : `Paso ${pasoIndex + 1}`,
        };
      });
      return { cursor: event.cursor, id: plan.id, meta: plan.goal, pasos };
    } catch {
      continue;
    }
  }
  return null;
}

/**
 * Otros cierres SIN `assistant_final` que son una decisión de la persona, no
 * un fallo: cancelar el turno entero (`ide_sessions.py::close()`, "Sesión
 * cancelada.") o rechazar el plan que lo pausó (`::reject_plan`, "Plan
 * rechazado..."). Igual que arriba, viajan como un evento `status` común,
 * sin ningún `type` propio que los distinga -- comparar el texto literal es
 * lo único que hay. Sin esto, cualquiera de los dos caía en el mismo mensaje
 * genérico de "no dejó respuesta" que el plan pendiente.
 */
function cierreSinRespuesta(events: IdeSessionEvent[]): { cursor: number; texto: string } | null {
  const cierre = [...events]
    .reverse()
    .find(
      (event) =>
        event.type === "status" &&
        (event.text.trim() === "Sesión cancelada." || event.text.trim().startsWith("Plan rechazado")),
    );
  return cierre ? { cursor: cierre.cursor, texto: cierre.text.trim() } : null;
}

function textoDeCierreLegible(texto: string): string {
  if (texto === "Sesión cancelada.") return "Detuviste este turno antes de que terminara.";
  if (texto.startsWith("Plan rechazado")) return "Rechazaste el plan propuesto: no se ejecutó ningún paso.";
  return texto;
}

/**
 * La tarjeta interactiva. Estilo "esperando" en vez de "terminado"/"fallido"
 * (encargo punto 4): borde y acento de marca, no el gris neutro de una
 * respuesta ya cerrada ni el rojo de un error.
 *
 * "Aprobar" arranca trabajo real -- reparte los pasos entre subagentes que
 * ESCRIBEN archivos (`ide_sessions.approve_plan`) -- así que pide
 * confirmación aparte antes de mandarlo, mismo patrón de dos pasos que
 * "Rechazar". El resultado de cada acción no se refleja optimistamente sobre
 * el plan (el próximo refresco del hilo trae los eventos reales -- `running`
 * de vuelta si se aprobó, o `cancelled` si se rechazó): acá solo se apagan
 * los botones y se dice qué se mandó, para que no se pueda mandar dos veces
 * mientras ese refresco llega.
 */
function PlanPropuestoCard({ sessionId, plan }: { sessionId: string; plan: PlanArtifact }) {
  const [pasos, setPasos] = useState(() => plan.pasos.map((paso) => paso.descripcion));
  const [editando, setEditando] = useState(false);
  const [confirmando, setConfirmando] = useState<"aprobar" | "rechazar" | null>(null);
  const [enVuelo, setEnVuelo] = useState<"aprobar" | "editar" | "rechazar" | null>(null);
  const [resuelto, setResuelto] = useState<"aprobado" | "rechazado" | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Si el plan que llega cambia (turno distinto, plan distinto), el borrador
  // de edición no puede seguir mostrando los pasos del plan anterior.
  useEffect(() => {
    setPasos(plan.pasos.map((paso) => paso.descripcion));
    setEditando(false);
    setResuelto(null);
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- solo `plan.id` dispara el reinicio: `plan.pasos` es fijo mientras el plan sigue siendo el mismo, sumarlo reiniciaría el borrador de edición en cada re-render sin que el plan real haya cambiado.
  }, [plan.id]);

  async function mensajeDeError(err: unknown, fallback: string): Promise<string> {
    return err instanceof ApiError ? err.message : fallback;
  }

  async function aprobar() {
    setEnVuelo("aprobar");
    setError(null);
    try {
      await approveIdeAgentPlan(sessionId, plan.id);
      setResuelto("aprobado");
    } catch (err) {
      setError(await mensajeDeError(err, "No se pudo aprobar el plan."));
    } finally {
      setEnVuelo(null);
      setConfirmando(null);
    }
  }

  async function rechazar() {
    setEnVuelo("rechazar");
    setError(null);
    try {
      await rejectIdeAgentPlan(sessionId, plan.id);
      setResuelto("rechazado");
    } catch (err) {
      setError(await mensajeDeError(err, "No se pudo rechazar el plan."));
    } finally {
      setEnVuelo(null);
      setConfirmando(null);
    }
  }

  async function guardarEdicion() {
    const limpios = pasos.map((paso) => paso.trim()).filter(Boolean);
    if (!limpios.length) {
      setError("El plan necesita al menos un paso.");
      return;
    }
    setEnVuelo("editar");
    setError(null);
    try {
      await editIdeAgentPlan(sessionId, plan.id, limpios);
      setEditando(false);
    } catch (err) {
      setError(await mensajeDeError(err, "No se pudo guardar la edición."));
    } finally {
      setEnVuelo(null);
    }
  }

  if (resuelto) {
    return (
      <p className="text-sm text-slate-400 dark:text-slate-500">
        {resuelto === "aprobado"
          ? "Aprobaste el plan: Edecán ya está repartiendo los pasos."
          : "Rechazaste el plan: no se ejecutó ningún paso."}
      </p>
    );
  }

  return (
    <div className="rounded-xl border border-brand-200 bg-brand-50/60 px-4 py-3 dark:border-brand-900/50 dark:bg-brand-950/20">
      <p className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-brand-700 dark:text-brand-300">
        <span className="h-1.5 w-1.5 rounded-full bg-brand-500" aria-hidden="true" />
        Te espera: aprobar este plan
      </p>
      <p className="mt-1.5 text-[15px] font-medium text-slate-800 dark:text-slate-100">{plan.meta}</p>
      {editando ? (
        <ol className="mt-2 space-y-1.5">
          {pasos.map((texto, index) => (
            <li key={index} className="flex items-start gap-1.5">
              <span className="mt-2 shrink-0 text-xs tabular-nums text-slate-400 dark:text-slate-500">{index + 1}.</span>
              <textarea
                value={texto}
                onChange={(event) =>
                  setPasos((rows) => rows.map((row, rowIndex) => (rowIndex === index ? event.target.value : row)))
                }
                rows={2}
                className="min-w-0 flex-1 rounded-md border border-forja-borde bg-forja-superficie px-2 py-1 text-sm text-slate-800 dark:border-forja-borde-oscuro dark:bg-forja-superficie-oscura dark:text-slate-100"
              />
            </li>
          ))}
        </ol>
      ) : (
        <ol className="mt-2 space-y-1">
          {plan.pasos.map((paso, index) => (
            <li key={index} className="flex items-start gap-1.5 text-sm leading-6 text-slate-600 dark:text-slate-300">
              <span className="mt-0.5 shrink-0 tabular-nums text-slate-400 dark:text-slate-500">{index + 1}.</span>
              <span>{paso.descripcion}</span>
            </li>
          ))}
        </ol>
      )}

      {error && <p className="mt-2 text-xs text-rose-600 dark:text-rose-400">{error}</p>}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {editando ? (
          <>
            <button
              type="button"
              onClick={() => void guardarEdicion()}
              disabled={enVuelo !== null}
              className="rounded-md bg-slate-950 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900"
            >
              {enVuelo === "editar" ? "Guardando…" : "Guardar cambios"}
            </button>
            <button
              type="button"
              onClick={() => {
                setEditando(false);
                setPasos(plan.pasos.map((paso) => paso.descripcion));
              }}
              disabled={enVuelo !== null}
              className="rounded-md border border-slate-300 px-3 py-1.5 text-xs font-semibold text-slate-700 dark:border-slate-600 dark:text-slate-200"
            >
              Cancelar
            </button>
          </>
        ) : confirmando === "aprobar" ? (
          <>
            <span className="text-xs text-slate-500 dark:text-slate-400">
              Esto reparte los pasos entre subagentes que van a escribir archivos. ¿Aprobar?
            </span>
            <button
              type="button"
              onClick={() => void aprobar()}
              disabled={enVuelo !== null}
              className="rounded-md bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50 hover:bg-brand-700"
            >
              {enVuelo === "aprobar" ? "Aprobando…" : "Sí, aprobar y ejecutar"}
            </button>
            <button
              type="button"
              onClick={() => setConfirmando(null)}
              disabled={enVuelo !== null}
              className="rounded-md px-3 py-1.5 text-xs font-semibold text-slate-500 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
            >
              Cancelar
            </button>
          </>
        ) : confirmando === "rechazar" ? (
          <>
            <span className="text-xs text-slate-500 dark:text-slate-400">¿Descartar el plan?</span>
            <button
              type="button"
              onClick={() => void rechazar()}
              disabled={enVuelo !== null}
              className="rounded-md bg-rose-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50 hover:bg-rose-700"
            >
              {enVuelo === "rechazar" ? "Rechazando…" : "Sí, rechazar"}
            </button>
            <button
              type="button"
              onClick={() => setConfirmando(null)}
              disabled={enVuelo !== null}
              className="rounded-md px-3 py-1.5 text-xs font-semibold text-slate-500 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
            >
              Cancelar
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              onClick={() => setConfirmando("aprobar")}
              className="rounded-md bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700"
            >
              Aprobar
            </button>
            <button
              type="button"
              onClick={() => setEditando(true)}
              className="rounded-md border border-slate-300 px-3 py-1.5 text-xs font-semibold text-slate-700 dark:border-slate-600 dark:text-slate-200"
            >
              Editar
            </button>
            <button
              type="button"
              onClick={() => setConfirmando("rechazar")}
              className="rounded-md border border-rose-200 px-3 py-1.5 text-xs font-semibold text-rose-600 hover:bg-rose-50 dark:border-rose-900/60 dark:text-rose-300 dark:hover:bg-rose-950/30"
            >
              Rechazar
            </button>
          </>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pregunta del agente a la persona ("que la IA me hable", encargo punto 5).
//
// Por qué existe: opencode puede pausar un turno para preguntar algo en vez
// de asumirlo (`question.v2.asked`, ver `ide_opencode_permisos.py` en el
// companion) -- hasta esta ronda ese canal no llegaba a la interfaz: el
// turno cerraba sin `assistant_final` y caía en el mismo mensaje genérico
// que un plan pendiente ANTES de que `PlanPropuestoCard` existiera. Mismo
// arreglo, mismo patrón: de primera clase dentro del turno, no una línea más
// de auditoría.
//
// El companion ya traduce la pregunta cruda a un evento de sesión
// `agent_question` (`ide_opencode_eventos.py::traducir_pregunta`) con el
// payload que `IdeAgentQuestion` espeja 1:1. Lo que TODAVÍA no existe es el
// endpoint REST que este archivo llama para responder o rechazar
// (`routers/ide.py` no tiene `.../question/{id}/reply|reject` -- ver el
// comentario en `lib/api-ide.ts`, mismo hallazgo). Ese cableado es de otro
// workflow (companion/API, fuera de mis archivos); mientras no exista, el
// botón manda la petición real igual -- el error real (capturado por
// `mensajeDeError`, mismo camino que el plan) se ve en la tarjeta en vez de
// fingir una respuesta que nunca llegó.
// ---------------------------------------------------------------------------

/**
 * Último `agent_question` del turno. A diferencia de `extraerPlanPendiente`,
 * NO hay forma de saber si ya se respondió/rechazó leyendo eventos
 * posteriores: `traducir_pregunta` documenta a propósito que
 * `question.v2.replied`/`question.v2.rejected` no se traducen a ningún
 * evento de sesión (ver el docstring de `ide_opencode_eventos.py`). Por eso
 * esta función siempre devuelve la ÚLTIMA pregunta del turno si el turno no
 * cerró con una respuesta de texto -- si de verdad ya se resolvió y el
 * agente siguió trabajando, `pickFinalEvent`/`live` ya lo habrían capturado
 * antes de llegar acá (ver `TurnRow`), así que el caso que queda sin cubrir
 * es acotado: una pregunta respondida desde OTRA pestaña/dispositivo justo
 * antes de que el turno cierre sin más eventos.
 */
function extraerPreguntaPendiente(events: IdeSessionEvent[]): { cursor: number; pregunta: IdeAgentQuestion } | null {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.type !== "agent_question") continue;
    try {
      const parsed = JSON.parse(event.text) as Partial<IdeAgentQuestion>;
      if (
        typeof parsed.request_id !== "string" ||
        typeof parsed.session_id !== "string" ||
        !Array.isArray(parsed.questions) ||
        parsed.questions.length === 0
      ) {
        continue;
      }
      return { cursor: event.cursor, pregunta: parsed as IdeAgentQuestion };
    } catch {
      continue;
    }
  }
  return null;
}

/**
 * La tarjeta interactiva. Mismo estilo "esperando" que `PlanPropuestoCard`
 * (acento de marca, no gris ni rojo) y mismo criterio de dos pasos para
 * rechazar -- responder no lo pide porque no es destructivo, es la acción
 * esperada.
 *
 * Una pregunta trae una o más `questions`; cada una se responde por
 * separado y las respuestas viajan en el mismo orden (`answers[i]`
 * corresponde a `questions[i]`). Si trae `options`, responder es elegir una
 * (o varias, si `multiple`) -- la persona nunca teclea lo que el agente ya
 * ofreció como alternativa. Si además (o en vez de eso) trae `custom`, hay
 * un campo de texto libre. Sin ninguna de las dos cosas (`options` vacío y
 * `custom` en `false`/ausente) se ofrece el texto libre igual -- una
 * pregunta sin ninguna forma de responder no sería una pregunta, sería un
 * callejón sin salida.
 */
function AgentQuestionCard({ sessionId, pregunta }: { sessionId: string; pregunta: IdeAgentQuestion }) {
  const [respuestas, setRespuestas] = useState<string[]>(() => pregunta.questions.map(() => ""));
  const [libres, setLibres] = useState<string[]>(() => pregunta.questions.map(() => ""));
  const [confirmandoRechazo, setConfirmandoRechazo] = useState(false);
  const [enVuelo, setEnVuelo] = useState<"responder" | "rechazar" | null>(null);
  const [resuelto, setResuelto] = useState<"respondida" | "rechazada" | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setRespuestas(pregunta.questions.map(() => ""));
    setLibres(pregunta.questions.map(() => ""));
    setConfirmandoRechazo(false);
    setResuelto(null);
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- solo `request_id` dispara el reinicio, mismo criterio que `PlanPropuestoCard` con `plan.id`.
  }, [pregunta.request_id]);

  function elegirOpcion(indice: number, valor: string) {
    setRespuestas((filas) => {
      const siguiente = [...filas];
      const item = pregunta.questions[indice];
      if (item?.multiple) {
        const actuales = siguiente[indice] ? siguiente[indice].split(", ") : [];
        siguiente[indice] = actuales.includes(valor)
          ? actuales.filter((x) => x !== valor).join(", ")
          : [...actuales, valor].join(", ");
      } else {
        siguiente[indice] = valor;
      }
      return siguiente;
    });
  }

  async function mensajeDeError(err: unknown, fallback: string): Promise<string> {
    return err instanceof ApiError ? err.message : fallback;
  }

  async function responder() {
    const finales = pregunta.questions.map((_, indice) => libres[indice]?.trim() || respuestas[indice] || "");
    if (finales.some((valor) => !valor)) {
      setError("Responde todas las preguntas antes de mandar.");
      return;
    }
    setEnVuelo("responder");
    setError(null);
    try {
      await answerIdeAgentQuestion(sessionId, pregunta.request_id, finales);
      setResuelto("respondida");
    } catch (err) {
      setError(await mensajeDeError(err, "No se pudo mandar la respuesta."));
    } finally {
      setEnVuelo(null);
    }
  }

  async function rechazar() {
    setEnVuelo("rechazar");
    setError(null);
    try {
      await rejectIdeAgentQuestion(sessionId, pregunta.request_id);
      setResuelto("rechazada");
    } catch (err) {
      setError(await mensajeDeError(err, "No se pudo rechazar la pregunta."));
    } finally {
      setEnVuelo(null);
      setConfirmandoRechazo(false);
    }
  }

  if (resuelto) {
    return (
      <p className="text-sm text-slate-400 dark:text-slate-500">
        {resuelto === "respondida"
          ? "Mandaste tu respuesta: Edecán sigue trabajando con ella."
          : "Rechazaste la pregunta: el turno queda libre para el próximo mensaje."}
      </p>
    );
  }

  return (
    <div className="rounded-xl border border-brand-200 bg-brand-50/60 px-4 py-3 dark:border-brand-900/50 dark:bg-brand-950/20">
      <p className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-brand-700 dark:text-brand-300">
        <span className="h-1.5 w-1.5 rounded-full bg-brand-500" aria-hidden="true" />
        Te espera: responder una pregunta
      </p>

      <div className="mt-2 space-y-4">
        {pregunta.questions.map((item, indice) => (
          <div key={indice}>
            {item.header && (
              <p className="text-[11px] font-semibold text-slate-500 dark:text-slate-400">{item.header}</p>
            )}
            <p className="mt-0.5 text-[15px] font-medium text-slate-800 dark:text-slate-100">{item.question}</p>
            {item.options.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {item.options.map((opcion) => {
                  const elegida = (respuestas[indice] ?? "").split(", ").includes(opcion.label);
                  return (
                    <button
                      key={opcion.label}
                      type="button"
                      title={opcion.description || undefined}
                      onClick={() => elegirOpcion(indice, opcion.label)}
                      className={`rounded-md border px-2.5 py-1 text-xs font-semibold ${
                        elegida
                          ? "border-brand-400 bg-brand-100 text-brand-800 dark:border-brand-700 dark:bg-brand-900/40 dark:text-brand-200"
                          : "border-forja-borde bg-forja-superficie text-slate-600 hover:bg-forja-superficie-elevada dark:border-forja-borde-oscuro dark:bg-forja-superficie-oscura-elevada dark:text-slate-300"
                      }`}
                    >
                      {opcion.label}
                    </button>
                  );
                })}
              </div>
            )}
            {(item.custom || item.options.length === 0) && (
              <input
                type="text"
                value={libres[indice] ?? ""}
                onChange={(event) =>
                  setLibres((filas) => filas.map((fila, filaIndice) => (filaIndice === indice ? event.target.value : fila)))
                }
                placeholder={item.options.length > 0 ? "O escribe tu propia respuesta…" : "Tu respuesta…"}
                className="mt-2 w-full min-w-0 rounded-md border border-forja-borde bg-forja-superficie px-2.5 py-1.5 text-sm text-slate-800 dark:border-forja-borde-oscuro dark:bg-forja-superficie-oscura dark:text-slate-100"
              />
            )}
          </div>
        ))}
      </div>

      {error && <p className="mt-2 text-xs text-rose-600 dark:text-rose-400">{error}</p>}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {confirmandoRechazo ? (
          <>
            <span className="text-xs text-slate-500 dark:text-slate-400">¿Rechazar sin responder?</span>
            <button
              type="button"
              onClick={() => void rechazar()}
              disabled={enVuelo !== null}
              className="rounded-md bg-rose-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50 hover:bg-rose-700"
            >
              {enVuelo === "rechazar" ? "Rechazando…" : "Sí, rechazar"}
            </button>
            <button
              type="button"
              onClick={() => setConfirmandoRechazo(false)}
              disabled={enVuelo !== null}
              className="rounded-md px-3 py-1.5 text-xs font-semibold text-slate-500 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
            >
              Cancelar
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              onClick={() => void responder()}
              disabled={enVuelo !== null}
              className="rounded-md bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50 hover:bg-brand-700"
            >
              {enVuelo === "responder" ? "Mandando…" : "Responder"}
            </button>
            <button
              type="button"
              onClick={() => setConfirmandoRechazo(true)}
              disabled={enVuelo !== null}
              className="rounded-md border border-rose-200 px-3 py-1.5 text-xs font-semibold text-rose-600 hover:bg-rose-50 dark:border-rose-900/60 dark:text-rose-300 dark:hover:bg-rose-950/30"
            >
              Rechazar
            </button>
          </>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Permisos: el freno de los modos Manual / Aceptar ediciones, con su salida
//
// En esos modos opencode PAUSA de verdad antes de escribir un archivo o correr
// un comando, y espera un `reply`. El companion lo traduce a un evento
// `agent_permission` (`ide_opencode_eventos.py::traducir_permiso`).
//
// Sin esta tarjeta el freno sería una trampa: la persona ve que el turno se
// detuvo y no tiene con qué dejarlo seguir. Es el mismo fallo que este
// proyecto arrastró todo el día -- capacidad real en el motor, sin forma de
// llegar a ella desde la pantalla.
// ---------------------------------------------------------------------------

/** Último `agent_permission` del turno, por el mismo criterio que
 * `extraerPreguntaPendiente`: opencode no emite un evento de "ya resuelto"
 * que se pueda leer después, así que se muestra el último si el turno no
 * cerró con texto. */
function extraerPermisoPendiente(
  events: IdeSessionEvent[],
): { cursor: number; permiso: IdeAgentPermission } | null {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.type !== "agent_permission") continue;
    try {
      const parsed = JSON.parse(event.text) as Partial<IdeAgentPermission>;
      if (typeof parsed.request_id !== "string" || typeof parsed.session_id !== "string") continue;
      if (typeof parsed.action !== "string") continue;
      return {
        cursor: event.cursor,
        permiso: { ...parsed, resources: parsed.resources ?? [] } as IdeAgentPermission,
      };
    } catch {
      continue;
    }
  }
  return null;
}

/** Traduce el nombre técnico de la acción de opencode a algo que se entienda
 * sin saber cómo se llama la herramienta por dentro. Lo que no esté en la
 * tabla se muestra tal cual: inventar un nombre bonito para una acción
 * desconocida es peor que enseñar el real. */
const ACCION_LEGIBLE: Record<string, string> = {
  edit: "modificar un archivo",
  write: "escribir un archivo",
  bash: "ejecutar un comando",
  webfetch: "consultar algo en internet",
  websearch: "buscar en internet",
  external_directory: "salir de la carpeta del proyecto",
};

function AgentPermissionCard({
  sessionId,
  permiso,
}: {
  sessionId: string;
  permiso: IdeAgentPermission;
}) {
  const [enVuelo, setEnVuelo] = useState<"conceder" | "denegar" | null>(null);
  const [recordar, setRecordar] = useState(false);
  const [resuelto, setResuelto] = useState<"concedido" | "denegado" | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Mismo criterio que las otras dos tarjetas de este archivo: el mensaje real
  // del backend cuando lo hay, y solo si no, uno genérico. Un permiso que no
  // se puede conceder tiene siempre un motivo concreto que conviene leer.
  function mensajeDeError(err: unknown, fallback: string): string {
    return err instanceof ApiError ? err.message : fallback;
  }

  async function responder(conceder: boolean) {
    setEnVuelo(conceder ? "conceder" : "denegar");
    setError(null);
    try {
      await answerIdeAgentPermission(sessionId, permiso.request_id, {
        conceder,
        // Recordar solo se manda si opencode declara que ESTA solicitud lo
        // admite: para lo irreversible, el companion lo rechaza a propósito.
        recordar: conceder && recordar && permiso.puede_recordar === true,
      });
      setResuelto(conceder ? "concedido" : "denegado");
    } catch (err) {
      setError(mensajeDeError(err, "No se pudo responder al permiso."));
    } finally {
      setEnVuelo(null);
    }
  }

  if (resuelto) {
    return (
      <p className="text-sm text-slate-400 dark:text-slate-500">
        {resuelto === "concedido"
          ? "Permiso concedido: el mismo turno sigue desde donde se paró."
          : "Permiso denegado: Edecán buscará otra forma o te lo dirá."}
      </p>
    );
  }

  const accion = ACCION_LEGIBLE[permiso.action] ?? permiso.action;

  return (
    <div className="rounded-xl border border-amber-200 bg-amber-50/60 px-4 py-3 dark:border-amber-900/50 dark:bg-amber-950/20">
      <p className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-300">
        <span className="h-1.5 w-1.5 rounded-full bg-amber-500" aria-hidden="true" />
        Te espera: dar permiso
      </p>

      <p className="mt-1.5 text-[15px] font-medium text-slate-800 dark:text-slate-100">
        Edecán quiere {accion}.
      </p>

      {permiso.resources.length > 0 && (
        <ul className="mt-1.5 space-y-0.5">
          {permiso.resources.slice(0, 8).map((recurso) => (
            <li key={recurso} className="truncate font-mono text-xs text-slate-500 dark:text-slate-400">
              {recurso}
            </li>
          ))}
          {permiso.resources.length > 8 && (
            <li className="text-xs text-slate-400 dark:text-slate-500">
              y {permiso.resources.length - 8} más
            </li>
          )}
        </ul>
      )}

      {permiso.puede_recordar === true && (
        <label className="mt-2 flex items-center gap-1.5 text-xs text-slate-600 dark:text-slate-400">
          <input
            type="checkbox"
            checked={recordar}
            onChange={(event) => setRecordar(event.target.checked)}
            className="h-3.5 w-3.5 rounded border-forja-borde dark:border-forja-borde-oscuro"
          />
          No volver a preguntarme por esto en este proyecto
        </label>
      )}

      {error && (
        <p className="mt-2 text-xs text-rose-600 dark:text-rose-400" role="alert">
          {error}
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => void responder(true)}
          disabled={enVuelo !== null}
          className="rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
        >
          {enVuelo === "conceder" ? "Concediendo…" : "Permitir"}
        </button>
        <button
          type="button"
          onClick={() => void responder(false)}
          disabled={enVuelo !== null}
          className="rounded-md border border-rose-200 px-3 py-1.5 text-xs font-semibold text-rose-600 hover:bg-rose-50 disabled:opacity-50 dark:border-rose-900/60 dark:text-rose-300 dark:hover:bg-rose-950/30"
        >
          {enVuelo === "denegar" ? "Denegando…" : "No permitir"}
        </button>
      </div>
    </div>
  );
}

/** Alto estimado de una fila colapsada (etiqueta + resumen de una línea); el
 * alto real —sobre todo si la persona expande el `<pre>` con el texto
 * completo del evento— lo corrige `virtualizer.measureElement` fila por fila. */
const EVENT_ROW_ESTIMATED_HEIGHT = 56;

/** Cuerpo virtualizado de la lista de auditoría: la única parte cara con
 * turnos largos (miles de eventos). Solo existe mientras `TurnWorkDetails`
 * está abierto (ver ahí abajo), así que un turno con 5.000 eventos que nadie
 * despliega nunca paga este costo. */
function EventList({ events }: { events: IdeSessionEvent[] }) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const virtualizer = useVirtualizer({
    count: events.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => EVENT_ROW_ESTIMATED_HEIGHT,
    overscan: 8,
  });

  return (
    <div
      ref={scrollRef}
      role="list"
      className="mt-2 max-h-80 overflow-y-auto border-l border-slate-100 pl-4 dark:border-slate-800"
    >
      <div style={{ height: virtualizer.getTotalSize(), width: "100%", position: "relative" }}>
        {virtualizer.getVirtualItems().map((virtualRow) => {
          const event = events[virtualRow.index];
          return (
            <div
              key={event.cursor}
              ref={virtualizer.measureElement}
              data-index={virtualRow.index}
              role="listitem"
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                width: "100%",
                transform: `translateY(${virtualRow.start}px)`,
              }}
              className="border-b border-slate-50 py-2 last:border-b-0 dark:border-slate-800/60"
            >
              <div className="flex items-baseline justify-between gap-3">
                <span className="text-[11px] font-medium uppercase tracking-wide text-slate-400 dark:text-slate-500">
                  {eventLabel(event)}
                </span>
                <span className="shrink-0 text-[11px] text-slate-300 dark:text-slate-600">{shortClock(event.timestamp)}</span>
              </div>
              <details className="mt-1">
                <summary className="cursor-pointer select-none text-xs text-slate-500 marker:hidden [&::-webkit-details-marker]:hidden dark:text-slate-400">
                  {summarize(event.text)}
                </summary>
                <pre className="mt-1.5 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md bg-slate-50 p-2.5 font-mono text-[11px] leading-5 text-slate-600 dark:bg-slate-900 dark:text-slate-300">
                  {event.text}
                </pre>
              </details>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** La línea plegada de auditoría: "N eventos · M acciones", sin ícono, sin caja — solo texto discreto
 * que se expande. Vive DESPUÉS de la respuesta a propósito, para no competir con ella.
 *
 * El `<details>` pasó de no controlado a controlado por un solo motivo: poder condicionar el montaje
 * de `EventList` a `open` (`{open && <EventList .../>}`). Sin eso, un turno con miles de eventos
 * pagaría el costo de crear el virtualizador y su `ResizeObserver` aunque la persona nunca abra la
 * auditoría — el patrón es el mismo que "contenido que se monta solo al expandir" de `DiffReview.tsx`. */
function TurnWorkDetails({
  events,
  live,
  hasError,
}: {
  events: IdeSessionEvent[];
  live: boolean;
  hasError: boolean;
}) {
  const [open, setOpen] = useState(false);
  if (!events.length) return null;
  const actionCount = events.filter(isActionEvent).length;
  const eventWord = events.length === 1 ? "evento" : "eventos";
  const actionWord = actionCount === 1 ? "acción" : "acciones";
  return (
    <details className="group" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary
        className={`flex w-fit cursor-pointer list-none items-center gap-1.5 text-xs select-none marker:hidden [&::-webkit-details-marker]:hidden ${
          hasError
            ? "text-red-500/80 hover:text-red-600 dark:text-red-400/80 dark:hover:text-red-400"
            : "text-slate-400 hover:text-slate-600 dark:text-slate-500 dark:hover:text-slate-300"
        }`}
      >
        <ChevronRightIcon className="h-3 w-3 shrink-0 transition-transform duration-150 group-open:rotate-90" />
        <span>
          {events.length} {eventWord} · {actionCount} {actionWord}
          {live ? " · en curso" : ""}
          {hasError ? " · con errores" : ""}
        </span>
      </summary>
      {open && <EventList events={events} />}
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
  // `turn.id` es `${sessionId}:${cursorDelPrimerEvento}` (ver el docstring de
  // `AgentThreadTurn`); el cursor es siempre numérico, así que el ÚLTIMO ":"
  // es el separador real aunque `sessionId` alguna vez llevara uno propio.
  const sessionId = turn.id.slice(0, turn.id.lastIndexOf(":"));
  const userEvent = events.find((event) => event.type === "user");
  const confirmations = events.filter((event) => event.type === "mcp_confirmation");
  const dirigidos = mensajesDirigidos(events);
  const finalEvent = pickFinalEvent(events, live);
  const { bloques, representados } = bloquesDelTurno(events);
  // Solo tiene sentido buscar un cierre "sin respuesta" cuando de verdad no
  // hay una (`assistant_final` ya ganó arriba) y el turno no sigue vivo -- un
  // turno en curso todavía puede llegar a la respuesta, no hay nada que
  // explicar todavía.
  const planPendiente = !finalEvent && !live ? extraerPlanPendiente(events) : null;
  // Misma prioridad que el plan: una pregunta pendiente compite por el mismo
  // sitio (el contenido principal del turno cuando no hay `assistant_final`)
  // y el plan gana si por alguna razón coinciden -- no debería pasar en la
  // práctica (un turno pausa por una cosa u otra, no las dos), pero el orden
  // deja explícito cuál manda.
  const preguntaPendiente = !finalEvent && !live && !planPendiente ? extraerPreguntaPendiente(events) : null;
  // Un permiso pendiente entra en la misma competencia por el contenido
  // principal del turno. Va después de la pregunta por prioridad, no por
  // importancia: si coincidieran, responder la pregunta suele desbloquear
  // también lo demás.
  const permisoPendiente =
    !finalEvent && !live && !planPendiente && !preguntaPendiente
      ? extraerPermisoPendiente(events)
      : null;
  const cierre =
    !finalEvent && !live && !planPendiente && !preguntaPendiente && !permisoPendiente
      ? cierreSinRespuesta(events)
      : null;
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
      event.cursor !== finalEvent?.cursor &&
      // Mismo trato que `finalEvent`: el plan pendiente, la pregunta
      // pendiente y el cierre por decisión ya se pintan arriba como el
      // contenido principal del turno, no como una línea más de auditoría.
      event.cursor !== planPendiente?.cursor &&
      event.cursor !== preguntaPendiente?.cursor &&
      event.cursor !== cierre?.cursor,
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
        <div className="text-[15px] leading-7 text-slate-800 dark:text-slate-200">
          <AgentRichText text={finalEvent.text.trim()} />
        </div>
      ) : live ? (
        <WorkingStatusRow />
      ) : planPendiente ? (
        <PlanPropuestoCard sessionId={sessionId} plan={planPendiente} />
      ) : preguntaPendiente ? (
        <AgentQuestionCard sessionId={sessionId} pregunta={preguntaPendiente.pregunta} />
      ) : permisoPendiente ? (
        <AgentPermissionCard sessionId={sessionId} permiso={permisoPendiente.permiso} />
      ) : cierre ? (
        <p className="text-sm text-slate-400 dark:text-slate-500">{textoDeCierreLegible(cierre.texto)}</p>
      ) : workEvents.length > 0 && bloques.length === 0 ? (
        <p className="text-sm italic text-slate-400 dark:text-slate-500">Este turno no dejó una respuesta de texto.</p>
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
        <SparklesIcon className="h-5 w-5 text-slate-300 dark:text-slate-600" />
        <p className="text-sm text-slate-400 dark:text-slate-500">Aún no hay mensajes en esta conversación.</p>
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
