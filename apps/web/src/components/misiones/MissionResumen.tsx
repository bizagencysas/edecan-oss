"use client";

import { Badge } from "@/components/ui";
import { MAX_REPLANS_PER_MISSION, type Mission, type MissionAgregados } from "@/lib/api-misiones";

import { STEP_LABELS } from "./MissionStatusBadge";

function formatTokenLabel(clave: string): string {
  const base = clave.endsWith("_tokens") ? clave.slice(0, -"_tokens".length) : clave;
  const CONOCIDAS: Record<string, string> = { input: "entrada", output: "salida" };
  return CONOCIDAS[base] ?? base.replace(/_/g, " ");
}

/**
 * Resumen agregado de una misión (WP-V6-10, `GET /v1/missions/{id}/detalle`):
 * tokens totales por tipo, desglose de pasos por status, y replanificaciones
 * usadas frente al presupuesto fijo del Orchestrator (`MAX_REPLANS_PER_MISSION`,
 * ver su comentario en `lib/api-misiones.ts` — el backend nunca lo expone en
 * la API a propósito). `null` si la misión todavía no tiene ningún paso
 * (recién `planning`, nada que resumir todavía).
 */
export function MissionResumen({
  mission,
  agregados,
}: {
  mission: Mission;
  agregados: MissionAgregados;
}) {
  const tokenEntries = Object.entries(agregados.tokens_totales_por_tipo);
  const pasosEntries = Object.entries(agregados.pasos_por_status).filter(([, n]) => n > 0);

  if (tokenEntries.length === 0 && pasosEntries.length === 0) return null;

  const replansUsados = Number(mission.presupuesto.replans_usados ?? 0);

  return (
    <div className="mb-4 flex flex-wrap items-center gap-x-5 gap-y-2 rounded-xl border border-slate-100 bg-slate-50/60 px-3.5 py-2.5 text-xs dark:border-slate-800 dark:bg-slate-900/40">
      {tokenEntries.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="font-medium text-slate-500 dark:text-slate-400">Tokens</span>
          {tokenEntries.map(([clave, valor]) => (
            <span key={clave} className="text-slate-600 dark:text-slate-300">
              {valor.toLocaleString("es")} {formatTokenLabel(clave)}
            </span>
          ))}
        </div>
      )}

      {pasosEntries.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="font-medium text-slate-500 dark:text-slate-400">Pasos</span>
          {pasosEntries.map(([stepStatus, n]) => (
            <span key={stepStatus} className="text-slate-600 dark:text-slate-300">
              {n} {STEP_LABELS[stepStatus as keyof typeof STEP_LABELS] ?? stepStatus}
            </span>
          ))}
        </div>
      )}

      <div className="flex items-center gap-1.5">
        <span className="font-medium text-slate-500 dark:text-slate-400">Replanificaciones</span>
        <Badge variant={replansUsados > 0 ? "warning" : "neutral"}>
          {replansUsados} / {MAX_REPLANS_PER_MISSION}
        </Badge>
      </div>
    </div>
  );
}
