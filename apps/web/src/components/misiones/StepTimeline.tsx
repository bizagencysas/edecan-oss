"use client";

import { useState } from "react";

import { ChevronDownIcon, CheckIcon, XIcon } from "@/components/icons";
import { Button, Spinner } from "@/components/ui";
import type { MissionStepDetalle } from "@/lib/api-misiones";

import { calcularOlas } from "./olas";
import { StepStatusBadge } from "./MissionStatusBadge";

/** Cap de la previsualización de un resultado antes de mostrar "Ver más" —
 * `resultado_truncado` ya viene recortado por el servidor (~2000 caracteres,
 * WP-V6-10), esto es solo la previsualización colapsada del lado del
 * cliente. */
const PREVISUALIZACION_LIMITE = 220;

function formatAgentName(agente: string): string {
  return agente
    .split("_")
    .map((part) => (part ? part[0].toUpperCase() + part.slice(1) : part))
    .join(" ");
}

function formatTokenLabel(clave: string): string {
  const base = clave.endsWith("_tokens") ? clave.slice(0, -"_tokens".length) : clave;
  const CONOCIDAS: Record<string, string> = { input: "entrada", output: "salida" };
  return CONOCIDAS[base] ?? base.replace(/_/g, " ");
}

function formatDuracion(inicioIso: string, finIso: string): string | null {
  const inicio = Date.parse(inicioIso);
  const fin = Date.parse(finIso);
  if (!Number.isFinite(inicio) || !Number.isFinite(fin) || fin < inicio) return null;
  const segundos = (fin - inicio) / 1000;
  if (segundos < 60) return `${segundos.toFixed(1)}s`;
  const minutos = Math.floor(segundos / 60);
  const resto = Math.round(segundos % 60);
  return `${minutos}m ${resto}s`;
}

const DOT_STYLES: Record<string, string> = {
  pending: "bg-slate-300 dark:bg-slate-600",
  running: "bg-brand-500",
  waiting_confirmation: "bg-amber-500",
  done: "bg-emerald-500",
  error: "bg-rose-500",
  skipped: "bg-slate-300 dark:bg-slate-600",
};

/**
 * Timeline vertical de los pasos de una misión (WP-V2-06; uso de
 * tokens/duración por paso, agrupación visual por "ola" real y resultado
 * colapsable desde WP-V6-10, `GET /v1/missions/{id}/detalle`). Cuando un
 * paso queda `waiting_confirmation` (red de seguridad — no debería pasar con
 * los perfiles P0 de hoy, ver `edecan_agents.orchestrator`), muestra la tool
 * pendiente y los botones Aprobar/Rechazar.
 */
export function StepTimeline({
  steps,
  onApprove,
  onReject,
  busy,
}: {
  steps: MissionStepDetalle[];
  onApprove: () => void;
  onReject: () => void;
  busy: boolean;
}) {
  const [expandidos, setExpandidos] = useState<Record<number, boolean>>({});

  if (steps.length === 0) {
    return <p className="text-sm text-slate-500 dark:text-slate-400">Todavía no hay pasos planificados.</p>;
  }

  const olas = calcularOlas(steps);

  return (
    <ol className="space-y-0">
      {steps.map((step, index) => {
        const pendingCall = step.usage?.pending_tool_call ?? null;
        const isLast = index === steps.length - 1;
        const ola = olas.get(step.seq);
        const duracion =
          step.started && step.finished ? formatDuracion(step.started, step.finished) : null;
        const tokens = Object.entries(step.usage ?? {}).filter(
          (entrada): entrada is [string, number] =>
            entrada[0].endsWith("_tokens") && typeof entrada[1] === "number",
        );

        const resultado = step.resultado_truncado;
        const expandido = expandidos[step.seq] ?? false;
        const necesitaToggle = resultado !== null && resultado.length > PREVISUALIZACION_LIMITE;
        const resultadoMostrado =
          resultado === null
            ? null
            : necesitaToggle && !expandido
              ? `${resultado.slice(0, PREVISUALIZACION_LIMITE)}…`
              : resultado;

        return (
          <li key={step.seq} className="relative flex gap-3 pb-6">
            {!isLast && (
              <span className="absolute left-[7px] top-4 h-full w-px bg-slate-200 dark:bg-slate-800" aria-hidden="true" />
            )}
            <span className="relative mt-1.5 flex h-4 w-4 shrink-0 items-center justify-center">
              {step.status === "running" ? (
                <Spinner className="h-4 w-4 text-brand-500" />
              ) : (
                <span
                  className={`block h-2.5 w-2.5 rounded-full ${DOT_STYLES[step.status] ?? "bg-slate-300"}`}
                />
              )}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-semibold text-slate-400">#{step.seq}</span>
                <span className="text-sm font-medium text-slate-800 dark:text-slate-100">
                  {formatAgentName(step.agente)}
                </span>
                <StepStatusBadge status={step.status} />
                {ola && ola.tamano > 1 && (
                  <span
                    className="rounded-full bg-violet-100 px-2 py-0.5 text-xs font-medium text-violet-700 dark:bg-violet-900/50 dark:text-violet-300"
                    title={`Corrió en paralelo con ${ola.tamano - 1} paso(s) más de la misma ola`}
                  >
                    Ola {ola.ola}
                  </span>
                )}
                {duracion && <span className="text-xs text-slate-400">{duracion}</span>}
              </div>
              <p className="mt-1 text-sm text-slate-600 dark:text-slate-300">{step.instruccion}</p>
              {tokens.length > 0 && (
                <p className="mt-1 text-xs text-slate-400">
                  {tokens
                    .map(([clave, valor]) => `${valor.toLocaleString("es")} ${formatTokenLabel(clave)}`)
                    .join(" · ")}
                </p>
              )}
              {resultadoMostrado && (
                <div className="mt-1.5 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600 dark:bg-slate-900/60 dark:text-slate-300">
                  <p className="whitespace-pre-wrap">{resultadoMostrado}</p>
                  {necesitaToggle && (
                    <button
                      type="button"
                      onClick={() => setExpandidos((prev) => ({ ...prev, [step.seq]: !expandido }))}
                      className="mt-1.5 inline-flex items-center gap-1 text-xs font-medium text-brand-600 hover:underline dark:text-brand-400"
                    >
                      <ChevronDownIcon
                        className={`h-3.5 w-3.5 transition-transform ${expandido ? "rotate-180" : ""}`}
                      />
                      {expandido ? "Ver menos" : "Ver más"}
                    </button>
                  )}
                </div>
              )}
              {step.status === "waiting_confirmation" && pendingCall && (
                <div className="mt-2 rounded-xl border border-amber-300 bg-amber-50 px-3 py-2.5 text-xs dark:border-amber-800 dark:bg-amber-950/40">
                  <p className="font-medium text-amber-900 dark:text-amber-200">
                    Quiere ejecutar <span className="font-mono">{pendingCall.name}</span>
                  </p>
                  <pre className="mt-1.5 max-h-28 overflow-auto rounded-lg bg-white/70 p-2 text-[11px] text-amber-900 dark:bg-black/20 dark:text-amber-100">
                    {JSON.stringify(pendingCall.args, null, 2)}
                  </pre>
                  <div className="mt-2 flex gap-2">
                    <Button size="sm" onClick={onApprove} loading={busy}>
                      <CheckIcon className="h-3.5 w-3.5" /> Aprobar
                    </Button>
                    <Button size="sm" variant="secondary" onClick={onReject} disabled={busy}>
                      <XIcon className="h-3.5 w-3.5" /> Rechazar
                    </Button>
                  </div>
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
