"use client";

/**
 * Estado 2 de §3.1: Trabajando (mission control). "En cuanto hay trabajo en
 * marcha, el compositor se encoge a una fila arriba y el centro pasa a ser"
 * el hilo de la conversación, con el costo y los cambios del turno como
 * artefactos SIEMPRE a la vista -- no botones que abren un modal.
 *
 * Encargo "Coste por fila y cola de aprobaciones real" reescribió esta vista:
 *
 *  1. **Costo en vivo, sin modal.** Antes "Costo" era un botón que abría
 *     `CostSummaryModal`. Ahora `estado.openCostSummary()` se llama solo
 *     (al abrir la conversación y cada 8s mientras el agente sigue vivo) y
 *     el número vive en la cabecera, junto al título.
 *  2. **Artefacto de cambios inline.** `estado.openDiffReview()` igual: sin
 *     modal, el diff del turno (mismo `DiffReview` de siempre) vive debajo
 *     de la cabecera, con aceptar/rechazar POR LOTE (punto 2 del encargo:
 *     "aprobar por lote cuando son varias del mismo tipo").
 *  3. **Mission control no es un cajón lateral (punto 5).** Si otro agente
 *     de OTRA conversación está "esperando" (plan_pending) mientras se mira
 *     esta, aparece una franja arriba con un salto directo (`goToRun`, ya
 *     real) -- así no hace falta abrir el panel lateral para enterarse de
 *     que algo más reclama una decisión.
 *
 * El compositor de abajo es el MISMO que en Reposo (`./Reposo`, `Composer`):
 * dirigir al agente a mitad de turno es la razón de que siga ahí, no un
 * componente aparte.
 */

import { useEffect, useMemo } from "react";

import { formatCosto, tonoDe } from "@/components/ide/AgentActivityCenter";
import AgentThread from "@/components/ide/AgentThread";
import DiffReview, { type DiffReviewFile } from "@/components/ide/DiffReview";
import { Composer } from "@/components/ide/estado/Reposo";
import type { IdeEstado } from "@/components/ide/estado-ide";
import { isLive } from "@/components/ide/estado-ide";
import { ChevronRightIcon, PencilIcon, SquareIcon } from "@/components/icons";
import { Spinner } from "@/components/ui";

export function Trabajando({ estado }: { estado: IdeEstado }) {
  const { agent, activeConversationId, conversationSummaries } = estado;
  const agentId = agent?.id ?? null;
  const agentLive = agent ? isLive(agent) : false;

  const currentConversation = useMemo(
    () => (conversationSummaries || []).find((c) => c.id === activeConversationId),
    [conversationSummaries, activeConversationId],
  );

  const title = agent?.title || currentConversation?.title || "Conversación";
  const workspaceName = agent?.workspace_name || estado.workspace?.name || "fyminvest";
  const statusText = agent ? agent.status : "Sin sesión activa";

  // Costo y diff ya no son "algo que se pide con un clic": son artefactos que
  // se ven solos apenas hay una conversación abierta, y se refrescan solos
  // mientras el turno sigue vivo (8s: más lento que el latido de eventos de
  // 1.2s, porque ninguno de los dos cambia tan seguido como el texto).
  useEffect(() => {
    if (!agentId) return;
    void estado.openCostSummary();
    void estado.openDiffReview();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- una vez por conversación abierta, no en cada repintado.
  }, [agentId]);

  useEffect(() => {
    if (!agentId || !agentLive) return;
    const timer = window.setInterval(() => {
      void estado.openCostSummary();
      void estado.openDiffReview();
    }, 8_000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId, agentLive]);

  // Punto 5 del encargo: mission control es el centro de la pantalla cuando
  // hay trabajo, no un cajón lateral -- si otra conversación (no esta) tiene
  // un plan esperando decisión, se ve acá mismo, sin abrir el panel de
  // "Ejecuciones".
  const otrosEsperando = useMemo(
    () => estado.activityRuns.filter((run) => run.sessionId !== agentId && tonoDe(run) === "esperando"),
    [estado.activityRuns, agentId],
  );

  const sinResolver = (estado.diffData?.files ?? []).filter((file) => !estado.diffResolutions[file.path]);
  const mostrarCosto = estado.costData && (estado.costData.total_acciones > 0 || estado.costData.costo_usd !== null);
  const mostrarDiff = (estado.diffData?.files.length ?? 0) > 0 || (estado.diffLoading && !estado.diffData);

  return (
    <section className="relative flex min-h-0 flex-1 flex-col">
      {otrosEsperando.length > 0 && (
        <div className="shrink-0 border-b border-forja-borde-suave bg-brand-50/70 px-5 py-2 dark:border-forja-borde-oscuro-suave dark:bg-brand-950/20 sm:px-10">
          <div className="mx-auto flex max-w-4xl flex-wrap items-center gap-2">
            <span className="text-[11px] font-semibold text-brand-700 dark:text-brand-300">
              {otrosEsperando.length === 1 ? "Otro agente te espera" : `${otrosEsperando.length} agentes te esperan`}:
            </span>
            {otrosEsperando.map((run) => (
              <button
                key={run.sessionId}
                type="button"
                onClick={() => void estado.goToRun(run)}
                className="flex items-center gap-1 rounded-full border border-brand-200 bg-white px-2.5 py-1 text-[11px] font-semibold text-brand-700 hover:bg-brand-50 dark:border-brand-900/50 dark:bg-slate-900 dark:text-brand-300 dark:hover:bg-brand-950/40"
              >
                {run.titulo}
                <ChevronRightIcon className="h-3 w-3" />
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-6 sm:px-10">
        <div className="mx-auto max-w-4xl space-y-4">
          <div className="mb-1 flex items-start justify-between gap-3 border-b border-forja-borde-suave pb-4 dark:border-forja-borde-oscuro-suave">
            <div className="min-w-0">
              <h2 className="font-bold dark:text-slate-100">{title}</h2>
              <p className="text-xs text-slate-400 dark:text-slate-500">
                {workspaceName} · {statusText}{agent ? ` · cursor ${estado.agentCursor}` : ""}
              </p>
              {mostrarCosto && estado.costData && (
                <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-slate-500 dark:text-slate-400">
                  <span className="font-semibold text-slate-700 dark:text-slate-200">
                    {formatCosto(estado.costData)}
                  </span>
                  <span>· {estado.costData.duracion_humana}</span>
                  <span>· {estado.costData.total_acciones} acciones</span>
                  {estado.costData.comparacion?.fuera_de_lo_normal && (
                    <span className="font-semibold text-amber-600 dark:text-amber-400">
                      · {estado.costData.comparacion.motivo}
                    </span>
                  )}
                  {estado.costData.bucles.length > 0 && (
                    <span className="font-semibold text-rose-600 dark:text-rose-400">
                      · patrón repetido sin avanzar ({estado.costData.bucles[0].patron})
                    </span>
                  )}
                </p>
              )}
            </div>
            {agent && isLive(agent) && (
              <button
                type="button"
                onClick={() => void estado.stopAgent()}
                className="flex shrink-0 items-center gap-2 rounded-lg border border-red-200 bg-forja-superficie px-3 py-2 text-xs font-semibold text-red-600 hover:bg-red-50 dark:border-red-900/60 dark:bg-transparent dark:text-red-400 dark:hover:bg-red-950/30"
              >
                <SquareIcon className="h-3.5 w-3.5" /> Detener
              </button>
            )}
          </div>

          {mostrarDiff && (
            <div className="rounded-xl border border-forja-borde bg-forja-superficie dark:border-forja-borde-oscuro dark:bg-forja-superficie-oscura-elevada">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-forja-borde-suave px-4 py-2.5 dark:border-forja-borde-oscuro-suave">
                <h3 className="flex items-center gap-1.5 text-xs font-bold text-slate-700 dark:text-slate-200">
                  <PencilIcon className="h-3.5 w-3.5" /> Cambios de este turno
                  {estado.diffData?.sealed === false && (
                    <span className="font-normal text-slate-400">· el turno sigue en curso</span>
                  )}
                </h3>
                {sinResolver.length > 1 && (
                  <div className="flex items-center gap-1.5">
                    <button
                      type="button"
                      onClick={() => sinResolver.forEach((file) => estado.acceptDiffFile(file.path))}
                      className="rounded-md border border-emerald-200 px-2 py-0.5 text-[11px] font-semibold text-emerald-700 hover:bg-emerald-50 dark:border-emerald-900/60 dark:text-emerald-300 dark:hover:bg-emerald-950/30"
                    >
                      Aceptar los {sinResolver.length}
                    </button>
                    <button
                      type="button"
                      onClick={() => sinResolver.forEach((file) => void estado.rejectDiffFile(file.path))}
                      className="rounded-md border border-rose-200 px-2 py-0.5 text-[11px] font-semibold text-rose-600 hover:bg-rose-50 dark:border-rose-900/60 dark:text-rose-300 dark:hover:bg-rose-950/30"
                    >
                      Rechazar los {sinResolver.length}
                    </button>
                  </div>
                )}
              </div>
              <div className="max-h-72 overflow-y-auto p-2">
                {estado.diffLoading && !estado.diffData ? (
                  <div className="flex items-center justify-center py-6">
                    <Spinner className="h-4 w-4" />
                  </div>
                ) : (
                  <DiffReview
                    files={(estado.diffData?.files ?? []).map(
                      (file): DiffReviewFile => ({
                        path: file.path,
                        kind: file.kind,
                        beforeContent: file.before_content,
                        afterContent: file.after_content,
                        unavailableReason: file.unavailable_reason ?? undefined,
                      }),
                    )}
                    resolutions={estado.diffResolutions}
                    pendingPaths={estado.diffPending}
                    onAccept={estado.acceptDiffFile}
                    onReject={(path) => void estado.rejectDiffFile(path)}
                  />
                )}
              </div>
            </div>
          )}

          {estado.agentTurns.length > 0 ? (
            <AgentThread
              turns={estado.agentTurns}
              resolvedMcpCalls={estado.resolvedMcpCalls}
              onResolveMcp={(callId, approved) => void estado.resolveMcpCall(callId, approved)}
              scrollAnchorRef={estado.timelineEndRef}
            />
          ) : (
            <>
              <div className="border-y border-forja-borde-suave py-5 text-sm text-slate-500 dark:border-forja-borde-oscuro-suave dark:text-slate-400">
                {isLive(agent) ? "Iniciando el agente…" : "Esta sesión no tiene eventos visibles."}
              </div>
              <div ref={estado.timelineEndRef} />
            </>
          )}
        </div>
      </div>

      <Composer estado={estado} />
    </section>
  );
}
