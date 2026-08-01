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
 * Encargo "Coste por fila y cola de aprobaciones real" (§3.1 de la
 * especificación) añadió tres piezas sobre el reparto original:
 *
 *  1. **Coste por fila, en vivo, sin modal.** `useRunCosts` pide
 *     `GET /v1/ide/agents/{id}/cost` (ya wireado, `lib/api-ide.ts`) por cada
 *     fila visible -- una vez para las terminadas (el coste ya no cambia), a
 *     latido propio para las vivas. El viejo modal de costo seguía siendo el
 *     único lugar donde se veía este número; ahora vive en la fila.
 *  2. **La densidad la decide el estado del trabajo, no un clic.** Una fila
 *     "esperando" (plan_pending) o "falla" ya NO es una línea más: se expande
 *     sola con lo que de verdad hay para decidir -- ver `PlanPendingBody` y
 *     `FailureBody` más abajo.
 *  3. **Cola de aprobaciones honesta.** `PlanPendingBody` arma el artefacto
 *     real de un plan pendiente leyendo el propio stream de eventos de esa
 *     sesión (evento `plan_proposed`, que ya manda el companion con la meta,
 *     los pasos y qué archivos toca cada uno -- `ide_workers_agent.py`) más
 *     el diff real del turno (`GET .../diff`, `POST .../diff/reject`, ambos
 *     genéricos por `sessionId`, no solo para la conversación abierta). Lo
 *     que SÍ falta en el companion es un endpoint que resuelva el plan mismo
 *     (`ide_sessions.SessionManager.approve_plan`/`reject_plan` existen pero
 *     `routers/ide.py` no los expone -- verificado leyendo ambos archivos):
 *     por eso "Aprobar" queda deshabilitado con la explicación a la vista en
 *     vez de fingir que hace algo, y "Rechazar" reutiliza `onStop` (cancelar
 *     la sesión), que SÍ es real y dispersa el plan_pending de la misma forma
 *     que cualquier "Detener".
 *
 *     CAMINO MUERTO bajo el motor por defecto (mismo hallazgo que el
 *     docstring de `AgentThread.tsx` sobre `PlanPropuestoCard`, verificado
 *     ahí con el mismo método): con opencode (`EDECAN_IDE_MOTOR` sin fijar,
 *     o distinto de "viejo") una sesión nunca llega a `estado === "plan_pending"`
 *     -- ese estado solo lo pone `ide_sessions.py::_run_workers_agent`
 *     (el turno del motor VIEJO) al ver un `plan_proposed` que
 *     `ide_opencode_eventos.py` jamás emite. Así que `tonoDe` nunca resuelve
 *     "esperando" para una sesión de opencode y esta fila expandida
 *     (`PlanPendingBody`) no se monta. Sigue viva a propósito para
 *     `EDECAN_IDE_MOTOR=viejo`, donde `plan_pending` sí ocurre.
 *
 * Sigue sin hablar con la red para lo que ya traía por props (`runs`); lo
 * nuevo (coste, plan, diff) lo pide directo porque esos tres endpoints son
 * genéricos por `sessionId` -- no dependen de cuál conversación esté abierta,
 * así que no hacía falta subir ese estado a `estado-ide.ts` para tenerlos acá.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { ChevronRightIcon, CheckIcon, SendIcon, SparklesIcon, SquareIcon, XIcon } from "@/components/icons";
import { Spinner } from "@/components/ui";
import {
  ApiError,
  approveIdeAgentPlan,
  getIdeAgentCost,
  getIdeAgentDiff,
  readIdeAgent,
  rejectIdeAgentDiffFile,
  type IdeAgentCost,
  type IdeAgentDiff,
  type IdeDiffFile,
  type IdeSessionEvent,
} from "@/lib/api-ide";

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
  /** Corta el turno en curso de esa sesión (y, para un plan_pending, es lo
   * más cerca que hay hoy de "rechazar el plan": ver el docstring del módulo). */
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

export type Tono = "trabajando" | "esperando" | "listo" | "falla" | "quieto";

export function tonoDe(run: AgentRunSummary): Tono {
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

export function etiquetaDeEstado(run: AgentRunSummary): string {
  return ETIQUETA_DE_ESTADO[run.estado] ?? run.estado;
}

export const COLOR_DE_TONO: Record<Tono, { barra: string; punto: string; texto: string }> = {
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

export function transcurrido(desde: string, hasta: number): string {
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

// --- Coste por fila, en vivo (punto 1 del encargo) --------------------------

type CostRowState =
  | { estado: "cargando" }
  | { estado: "ok"; datos: IdeAgentCost }
  | { estado: "no_conectado" }
  | { estado: "error" };

/** `$0.84` si el companion ya sabe tarifar el modelo; si no, el conteo de
 * tokens -- nunca un número inventado cuando `costo_usd` viene `null`. */
export function formatCosto(datos: IdeAgentCost): string {
  if (datos.costo_usd !== null) {
    return datos.costo_usd < 0.01 ? `$${datos.costo_usd.toFixed(4)}` : `$${datos.costo_usd.toFixed(2)}`;
  }
  const tokens = datos.tokens.total;
  return tokens >= 1000 ? `${(tokens / 1000).toFixed(1)}k tok` : `${tokens} tok`;
}

function costoTexto(costo: CostRowState | undefined): string | null {
  return costo?.estado === "ok" ? formatCosto(costo.datos) : null;
}

/**
 * Pide `GET .../cost` por cada fila que se está pintando: una vez para las
 * que ya terminaron (el coste de un turno cerrado no cambia) y a latido
 * propio (6s, más lento que el de la torre) para las que siguen vivas. Vive
 * acá y no en `estado-ide.ts` porque el endpoint es genérico por `sessionId`
 * -- no hace falta que sea LA conversación abierta para pedirlo.
 */
function useRunCosts(runs: AgentRunSummary[]): Record<string, CostRowState> {
  const [costs, setCosts] = useState<Record<string, CostRowState>>({});
  const costsRef = useRef(costs);
  costsRef.current = costs;
  const runsRef = useRef(runs);
  runsRef.current = runs;
  const enVueloRef = useRef<Set<string>>(new Set());

  const pedirCosto = useCallback((sessionId: string) => {
    if (enVueloRef.current.has(sessionId)) return;
    enVueloRef.current.add(sessionId);
    getIdeAgentCost(sessionId)
      .then((datos) => setCosts((rows) => ({ ...rows, [sessionId]: { estado: "ok", datos } })))
      .catch((err) => {
        const noConectado = err instanceof ApiError && err.status === 404;
        setCosts((rows) => ({ ...rows, [sessionId]: { estado: noConectado ? "no_conectado" : "error" } }));
      })
      .finally(() => {
        enVueloRef.current.delete(sessionId);
      });
  }, []);

  // Primer vistazo de cada fila nueva (torre recién cargada, o un agente que
  // acaba de arrancar): sin esto se queda en blanco hasta el próximo latido.
  useEffect(() => {
    for (const run of runs) {
      if (!(run.sessionId in costsRef.current)) pedirCosto(run.sessionId);
    }
  }, [runs, pedirCosto]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      for (const run of runsRef.current) {
        if (run.viva) pedirCosto(run.sessionId);
      }
    }, 6_000);
    return () => window.clearInterval(timer);
  }, [pedirCosto]);

  return costs;
}

// --- Artefacto de un plan pendiente (punto 2 y 4 del encargo) ---------------

interface PlanArtifactStep {
  descripcion: string;
  estado: string;
  /** Rutas que ese paso declaró tocar (`rutas_por_paso`, §3.6: la partición). */
  archivos: string[];
}

interface PlanArtifact {
  /** `plan.public()["id"]` (ide_plan.py:209). Venía SIEMPRE en el evento y este
   * parser lo tiraba, así que la tarjeta no tenía con qué llamar a
   * `.../plan/{plan_id}/approve` y el botón se quedó pintado "no conectado". */
  id: string;
  meta: string;
  pasos: PlanArtifactStep[];
}

/**
 * Busca, de atrás hacia adelante, el último evento `plan_proposed` -- lo
 * manda `WorkersIDEAgent.run` con `plan.public()` + `rutas_por_paso` en JSON
 * (ver `ide_workers_agent.py`) justo antes de pausar el turno en
 * `plan_pending`. Nunca lanza: un evento viejo, sin ese tipo, o con un JSON
 * que no calza con lo esperado se ignora y esta vista se calla en vez de
 * mostrar un plan a medias.
 */
function extraerPlanDeEventos(events: IdeSessionEvent[]): PlanArtifact | null {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.type !== "plan_proposed") continue;
    try {
      const parsed = JSON.parse(event.text) as {
        plan?: { id?: unknown; goal?: unknown; steps?: unknown };
        rutas_por_paso?: unknown;
      };
      const plan = parsed.plan;
      if (!plan || typeof plan.goal !== "string" || !Array.isArray(plan.steps)) continue;
      // Sin `id` no se puede aprobar: mejor ignorar ese evento que pintar una
      // tarjeta con un botón que fallaría al pulsarlo.
      if (typeof plan.id !== "string" || !plan.id) continue;
      const rutas = Array.isArray(parsed.rutas_por_paso) ? parsed.rutas_por_paso : [];
      const pasos: PlanArtifactStep[] = plan.steps.map((paso, pasoIndex) => {
        const registro = paso as { description?: unknown; status?: unknown };
        const archivosPaso = rutas[pasoIndex];
        return {
          descripcion: typeof registro.description === "string" ? registro.description : `Paso ${pasoIndex + 1}`,
          estado: typeof registro.status === "string" ? registro.status : "pending",
          archivos: Array.isArray(archivosPaso) ? archivosPaso.filter((r): r is string => typeof r === "string") : [],
        };
      });
      return { id: plan.id, meta: plan.goal, pasos };
    } catch {
      continue;
    }
  }
  return null;
}

interface SessionArtifact {
  cargando: boolean;
  diff: IdeAgentDiff | null;
  plan: PlanArtifact | null;
}

/** Se activa solo para las filas que de verdad se están mostrando expandidas
 * (`activo`) -- pedir el diff y el historial de TODAS las filas "esperando"
 * de golpe, aunque hoy suelen ser pocas, sería trabajo que nadie pidió ver. */
function useSessionArtifact(sessionId: string, activo: boolean): SessionArtifact {
  const [state, setState] = useState<SessionArtifact>({ cargando: true, diff: null, plan: null });

  useEffect(() => {
    if (!activo) return;
    let cancelado = false;
    setState({ cargando: true, diff: null, plan: null });
    Promise.allSettled([getIdeAgentDiff(sessionId), readIdeAgent(sessionId, 0)]).then(([diffResult, eventsResult]) => {
      if (cancelado) return;
      setState({
        cargando: false,
        diff: diffResult.status === "fulfilled" ? diffResult.value : null,
        plan: eventsResult.status === "fulfilled" ? extraerPlanDeEventos(eventsResult.value.events) : null,
      });
    });
    return () => {
      cancelado = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- una vez por sesión que se vuelve visible, no en cada latido de la torre.
  }, [sessionId, activo]);

  return state;
}

type ResolucionLocal = "accepted" | "rejected";

const ETIQUETA_KIND: Record<IdeDiffFile["kind"], string> = {
  added: "Nuevo",
  modified: "Modificado",
  deleted: "Borrado",
  unavailable: "Sin capturar",
};

/**
 * El artefacto completo de un "esperando" (punto 4: plan + diff como UNA
 * cosa revisable, no modales sueltos): la meta y los pasos del plan que lo
 * pausó, más los archivos que el turno ya haya tocado antes de pausar.
 *
 * "Aprobar" ES real desde que `routers/ide.py` expone
 * `POST .../plan/{plan_id}/approve`. Antes estaba deshabilitado con el rótulo
 * "no conectado aún" y un `title` que afirmaba que ese endpoint no existía:
 * cuando se añadió, este archivo quedó fuera del reparto de trabajo y el texto
 * se quedó mintiendo. El dueño llegó a ver un plan de 6 pasos que no podía
 * aprobar mientras el backend lo esperaba. Si esto vuelve a deshabilitarse,
 * que sea por una razón medida, no por un rótulo viejo.
 */
function PlanPendingBody({ run, onReject }: { run: AgentRunSummary; onReject: () => void }) {
  const artifact = useSessionArtifact(run.sessionId, true);
  const [resoluciones, setResoluciones] = useState<Record<string, ResolucionLocal>>({});
  const [pendientes, setPendientes] = useState<Record<string, boolean>>({});
  const [confirmando, setConfirmando] = useState(false);
  // Aprobar reparte los pasos entre subagentes que escriben archivos de verdad:
  // se pide confirmación antes, igual que hace `AgentThread`.
  const [confirmandoAprobar, setConfirmandoAprobar] = useState(false);
  const [aprobando, setAprobando] = useState(false);
  const [aprobado, setAprobado] = useState(false);
  const [errorAprobar, setErrorAprobar] = useState<string | null>(null);

  const planId = artifact.plan?.id ?? null;

  async function aprobarPlan() {
    if (!planId) return;
    setAprobando(true);
    setErrorAprobar(null);
    try {
      await approveIdeAgentPlan(run.sessionId, planId);
      setAprobado(true);
    } catch (err) {
      setErrorAprobar(err instanceof Error && err.message ? err.message : "No se pudo aprobar el plan.");
    } finally {
      setAprobando(false);
      setConfirmandoAprobar(false);
    }
  }

  const rechazarArchivo = useCallback(
    async (path: string) => {
      setPendientes((rows) => ({ ...rows, [path]: true }));
      try {
        await rejectIdeAgentDiffFile(run.sessionId, path);
        setResoluciones((rows) => ({ ...rows, [path]: "rejected" }));
      } catch {
        // Mejor esfuerzo: el archivo se queda sin resolver y se puede reintentar.
      } finally {
        setPendientes((rows) => {
          const next = { ...rows };
          delete next[path];
          return next;
        });
      }
    },
    [run.sessionId],
  );

  const archivos = artifact.diff?.files ?? [];
  const sinResolver = archivos.filter((file) => !resoluciones[file.path]);

  async function rechazarLote(lista: IdeDiffFile[]) {
    for (const file of lista) await rechazarArchivo(file.path);
  }

  return (
    <div className="mt-2 space-y-2 rounded-lg border border-forja-borde bg-forja-superficie-elevada p-2.5 dark:border-forja-borde-oscuro dark:bg-slate-900/60">
      {artifact.cargando ? (
        <p className="flex items-center gap-1.5 text-[11px] text-slate-400">
          <Spinner className="h-3 w-3" /> Cargando el plan…
        </p>
      ) : artifact.plan ? (
        <div>
          <p className="text-[11px] font-semibold text-slate-600 dark:text-slate-300">{artifact.plan.meta}</p>
          <ol className="mt-1 space-y-1">
            {artifact.plan.pasos.map((paso, index) => (
              <li key={index} className="flex items-start gap-1.5 text-[11px] leading-4 text-slate-500 dark:text-slate-400">
                <span className="mt-0.5 shrink-0 tabular-nums text-slate-300 dark:text-slate-600">{index + 1}.</span>
                <span className="min-w-0 flex-1">
                  {paso.descripcion}
                  {paso.archivos.length > 0 && (
                    <span className="ml-1 text-slate-400 dark:text-slate-500">· {paso.archivos.join(", ")}</span>
                  )}
                </span>
              </li>
            ))}
          </ol>
        </div>
      ) : (
        <p className="text-[11px] italic text-slate-400">
          El agente pausó a esperar tu decisión, pero esta vista no encontró el detalle del plan en su historial
          reciente.
        </p>
      )}

      {archivos.length > 0 && (
        <div className="space-y-1 border-t border-forja-borde-suave pt-1.5 dark:border-forja-borde-oscuro-suave">
          <div className="flex items-center justify-between gap-2">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">
              {archivos.length} archivo{archivos.length === 1 ? "" : "s"} ya tocados
            </p>
            {sinResolver.length > 1 && (
              <button
                type="button"
                onClick={() => void rechazarLote(sinResolver)}
                className="shrink-0 text-[10px] font-semibold text-rose-600 hover:underline dark:text-rose-400"
              >
                Rechazar los {sinResolver.length}
              </button>
            )}
          </div>
          <ul className="space-y-1">
            {archivos.map((file) => (
              <li key={file.path} className="flex items-center justify-between gap-2 text-[11px]">
                <span className="flex min-w-0 flex-1 items-center gap-1 truncate" title={file.path}>
                  {file.kind === "deleted" && (
                    <span className="shrink-0 rounded bg-rose-50 px-1 py-0.5 text-[9px] font-bold uppercase text-rose-600 dark:bg-rose-950/40 dark:text-rose-300">
                      {ETIQUETA_KIND.deleted}
                    </span>
                  )}
                  <span className="truncate">{file.path}</span>
                </span>
                {resoluciones[file.path] ? (
                  <span
                    className={cx(
                      "shrink-0 text-[10px] font-semibold",
                      resoluciones[file.path] === "rejected"
                        ? "text-rose-500"
                        : "text-emerald-600 dark:text-emerald-400",
                    )}
                  >
                    {resoluciones[file.path] === "rejected" ? "Rechazado" : "Aceptado"}
                  </span>
                ) : pendientes[file.path] ? (
                  <Spinner className="h-3 w-3 shrink-0 text-slate-400" />
                ) : (
                  <span className="flex shrink-0 items-center gap-1">
                    <button
                      type="button"
                      aria-label={`Aceptar ${file.path}`}
                      onClick={() => setResoluciones((rows) => ({ ...rows, [file.path]: "accepted" }))}
                      className="rounded p-0.5 text-emerald-600 hover:bg-emerald-50 dark:text-emerald-400 dark:hover:bg-emerald-950/30"
                    >
                      <CheckIcon className="h-3 w-3" />
                    </button>
                    <button
                      type="button"
                      aria-label={`Rechazar ${file.path}`}
                      onClick={() => void rechazarArchivo(file.path)}
                      className="rounded p-0.5 text-rose-600 hover:bg-rose-50 dark:text-rose-400 dark:hover:bg-rose-950/30"
                    >
                      <XIcon className="h-3 w-3" />
                    </button>
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex items-center gap-2 border-t border-forja-borde-suave pt-1.5 dark:border-forja-borde-oscuro-suave">
        {aprobado ? (
          <span className="flex-1 rounded-md px-2 py-1 text-[11px] font-semibold text-emerald-600 dark:text-emerald-400">
            Plan aprobado — ejecutando
          </span>
        ) : confirmandoAprobar ? (
          <span className="flex flex-1 items-center gap-1.5 text-[11px]">
            <span className="text-slate-500 dark:text-slate-400">¿Ejecutar el plan?</span>
            <button
              type="button"
              disabled={aprobando}
              onClick={() => void aprobarPlan()}
              className="rounded bg-emerald-600 px-2 py-1 font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
            >
              {aprobando ? "Aprobando…" : "Sí, aprobar"}
            </button>
            <button
              type="button"
              onClick={() => setConfirmandoAprobar(false)}
              className="rounded px-2 py-1 font-semibold text-slate-500 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
            >
              No
            </button>
          </span>
        ) : (
          <button
            type="button"
            disabled={!planId}
            title={planId ? "Reparte los pasos entre subagentes que escriben archivos." : "Este plan llegó sin identificador; no se puede aprobar."}
            onClick={() => setConfirmandoAprobar(true)}
            className="flex-1 rounded-md border border-forja-borde px-2 py-1 text-[11px] font-semibold text-slate-700 hover:bg-forja-superficie-elevada disabled:cursor-not-allowed disabled:opacity-40 dark:border-forja-borde-oscuro dark:text-slate-200 dark:hover:bg-slate-800"
          >
            Aprobar el plan
          </button>
        )}
        {confirmando ? (
          <span className="flex flex-1 items-center justify-end gap-1.5 text-[11px]">
            <span className="text-slate-500 dark:text-slate-400">¿Descartar el plan?</span>
            <button
              type="button"
              onClick={onReject}
              className="rounded bg-rose-600 px-2 py-1 font-semibold text-white hover:bg-rose-700"
            >
              Sí, rechazar
            </button>
            <button
              type="button"
              onClick={() => setConfirmando(false)}
              className="rounded px-2 py-1 font-semibold text-slate-500 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
            >
              Cancelar
            </button>
          </span>
        ) : (
          <button
            type="button"
            onClick={() => setConfirmando(true)}
            className="flex-1 rounded-md border border-rose-200 px-2 py-1 text-[11px] font-semibold text-rose-600 hover:bg-rose-50 dark:border-rose-900/60 dark:text-rose-300 dark:hover:bg-rose-950/30"
          >
            Rechazar el plan
          </button>
        )}
      </div>

      {/* Si aprobar falla, se dice. Guardar el error y no pintarlo deja al
          dueño creyendo que el plan arrancó cuando no arrancó. */}
      {errorAprobar && (
        <p className="text-[11px] leading-4 text-rose-600 dark:text-rose-400" role="alert">
          {errorAprobar}
        </p>
      )}
    </div>
  );
}

function FailureBody({ run, onGo }: { run: AgentRunSummary; onGo: () => void }) {
  return (
    <div className="mt-2 space-y-1.5 rounded-lg border border-rose-200 bg-rose-50/60 p-2.5 text-[11px] leading-4 text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/20 dark:text-rose-300">
      <p>
        {run.estado === "interrupted"
          ? "El turno se cortó a mitad (el companion se desconectó o se cerró antes de terminar)."
          : "El turno terminó en falla. El motivo real queda en el hilo, no acá."}
      </p>
      <button type="button" onClick={onGo} className="font-semibold underline underline-offset-2 hover:no-underline">
        Abrir el hilo para ver qué pasó
      </button>
    </div>
  );
}

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
  const [confirmandoLote, setConfirmandoLote] = useState(false);

  const hayVivos = runs.some((run) => run.viva);
  const costs = useRunCosts(runs);

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

  // Cola de aprobaciones de primera clase (punto 2 del encargo): los
  // "esperando" no son una fila más entre las demás, son lo primero que
  // reclama una decisión -- por eso se cuentan aparte para el lote.
  const esperandoRuns = useMemo(() => runs.filter((run) => tonoDe(run) === "esperando"), [runs]);
  const costoVisibleTotal = useMemo(() => {
    let total = 0;
    let algunoConocido = false;
    for (const run of runs) {
      const costo = costs[run.sessionId];
      if (costo?.estado === "ok" && costo.datos.costo_usd !== null) {
        total += costo.datos.costo_usd;
        algunoConocido = true;
      }
    }
    return algunoConocido ? total : null;
  }, [runs, costs]);

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

  function rechazarLotePendientes() {
    for (const run of esperandoRuns) onStop(run);
    setConfirmandoLote(false);
  }

  return (
    <section
      aria-label="Ejecuciones en curso"
      className={cx(
        "flex h-full min-h-0 w-full flex-col overflow-hidden border-l border-forja-borde bg-forja-superficie-elevada dark:border-slate-800 dark:bg-slate-900",
        className,
      )}
    >
      <header className="shrink-0 border-b border-forja-borde px-4 pb-3 pt-3.5 dark:border-slate-800">
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
                className="rounded-md p-1.5 text-slate-400 hover:bg-forja-superficie-hundida hover:text-slate-700 dark:hover:bg-slate-800 dark:hover:text-slate-200"
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
          {costoVisibleTotal !== null && (
            <>
              <span aria-hidden="true">·</span>
              <span className="tabular-nums" title="Suma del costo conocido de las filas visibles">
                {costoVisibleTotal < 0.01 ? `$${costoVisibleTotal.toFixed(4)}` : `$${costoVisibleTotal.toFixed(2)}`}
              </span>
            </>
          )}
        </p>

        {esperandoRuns.length > 1 && (
          <div className="mt-2 flex items-center justify-between gap-2 rounded-lg border border-brand-200 bg-brand-50 px-2.5 py-1.5 dark:border-brand-900/50 dark:bg-brand-950/30">
            <span className="text-[11px] font-semibold text-brand-700 dark:text-brand-300">
              {esperandoRuns.length} planes esperando decisión
            </span>
            {confirmandoLote ? (
              <span className="flex items-center gap-1.5 text-[11px]">
                <button
                  type="button"
                  onClick={rechazarLotePendientes}
                  className="rounded bg-rose-600 px-2 py-0.5 font-semibold text-white hover:bg-rose-700"
                >
                  Rechazar los {esperandoRuns.length}
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmandoLote(false)}
                  className="rounded px-2 py-0.5 font-semibold text-brand-700 hover:bg-brand-100 dark:text-brand-300 dark:hover:bg-brand-900/40"
                >
                  Cancelar
                </button>
              </span>
            ) : (
              <button
                type="button"
                onClick={() => setConfirmandoLote(true)}
                className="shrink-0 text-[11px] font-semibold text-rose-600 hover:underline dark:text-rose-400"
              >
                Rechazar todos
              </button>
            )}
          </div>
        )}

        <div className="mt-3 flex rounded-lg border border-forja-borde-suave bg-white p-0.5 dark:border-slate-700 dark:bg-slate-800">
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
                        costo={costs[run.sessionId]}
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

      <footer className="shrink-0 border-t border-forja-borde px-4 py-2.5 text-[11px] leading-4 text-slate-400 dark:border-slate-800">
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
  costo,
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
  costo: CostRowState | undefined;
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
  const costoLabel = costoTexto(costo);
  // Regla de §3.1: un agente que va bien ocupa una línea; uno atascado o
  // esperando aprobación se expande solo -- la densidad la decide el estado,
  // no un clic de la persona.
  const expandidoPorEstado = tono === "esperando" || tono === "falla";

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
          : "border-forja-borde-suave hover:border-forja-borde-fuerte dark:border-slate-800 dark:hover:border-slate-700",
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
                {costoLabel && <span className="shrink-0 tabular-nums font-semibold text-slate-500 dark:text-slate-300">{costoLabel}</span>}
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
                    : "border-forja-borde-suave text-slate-500 hover:bg-forja-superficie-elevada dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800",
                )}
              >
                Dirigir
              </button>
            )}
            {run.viva && (
              <button
                type="button"
                onClick={onStop}
                className="flex items-center gap-1 rounded-md border border-forja-borde-suave px-2 py-0.5 text-[11px] font-semibold text-slate-500 hover:border-rose-200 hover:bg-rose-50 hover:text-rose-600 dark:border-slate-700 dark:text-slate-400 dark:hover:border-rose-900 dark:hover:bg-rose-950/40 dark:hover:text-rose-300"
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

          {expandidoPorEstado &&
            (tono === "esperando" ? (
              <PlanPendingBody run={run} onReject={onStop} />
            ) : (
              <FailureBody run={run} onGo={onGo} />
            ))}

          {dirigiendo && (
            <div className="mt-2 rounded-lg border border-forja-borde-suave bg-forja-superficie-elevada p-1.5 dark:border-slate-700 dark:bg-slate-900">
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
