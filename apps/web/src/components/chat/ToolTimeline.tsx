"use client";

import { useEffect, useState } from "react";

import { CheckIcon } from "@/components/icons";
import { Spinner } from "@/components/ui";
import type { ToolTimelineEntry } from "@/lib/chat-blocks";
import { getMission, type MissionDetail } from "@/lib/api-misiones";

import { ArtifactLinks } from "./ArtifactLinks";

export type ToolEvent = ToolTimelineEntry;

/** Traza visual de las herramientas que el agente fue llamando durante el turno (§10.7). */
export function ToolTimeline({ events }: { events: ToolEvent[] }) {
  if (events.length === 0) return null;
  return (
    <div className="flex flex-col gap-1.5 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs dark:border-slate-800 dark:bg-slate-900/60">
      {events.map((event) => (
        <div key={event.callKey} className="flex items-start gap-2 text-slate-600 dark:text-slate-300">
          {event.status === "running" ? (
            <Spinner className="mt-0.5 h-3 w-3 shrink-0 text-brand-500" />
          ) : (
            <CheckIcon className="mt-0.5 h-3 w-3 shrink-0 text-emerald-500" />
          )}
          <div>
            <span className="font-medium text-slate-700 dark:text-slate-200">{displayToolName(event.name)}</span>
            {event.status === "done" && event.resultPreview && (
              <span className="text-slate-500 dark:text-slate-400"> — {event.resultPreview}</span>
            )}
            {event.status === "running" && (
              <span className="text-slate-400">
                {" — "}{event.progressMessage ?? "ejecutando…"}
                {typeof event.elapsedSeconds === "number" ? ` · ${formatDuration(event.elapsedSeconds)}` : ""}
              </span>
            )}
            {event.status === "done" && <ArtifactLinks artifacts={event.artifacts ?? []} />}
            {event.missionId && <MissionInlineProgress missionId={event.missionId} />}
          </div>
        </div>
      ))}
    </div>
  );
}

function displayToolName(name: string): string {
  const words = name.replace(/[_-]+/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : "Trabajo";
}

function MissionInlineProgress({ missionId }: { missionId: string }) {
  const [detail, setDetail] = useState<MissionDetail | null>(null);
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    async function refresh() {
      try {
        const next = await getMission(missionId);
        if (cancelled) return;
        setDetail(next);
        setUnavailable(false);
        if (next.mission.status === "planning" || next.mission.status === "running") {
          timer = setTimeout(refresh, 3_000);
        }
      } catch {
        if (!cancelled) setUnavailable(true);
      }
    }
    void refresh();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [missionId]);

  if (unavailable && !detail) {
    return <p className="mt-1 text-slate-400">El progreso seguirá disponible en Actividad.</p>;
  }
  if (!detail) return <p className="mt-1 text-slate-400">Preparando el plan…</p>;

  const completed = detail.steps.filter((step) => step.status === "done").length;
  const running = detail.steps.find((step) => step.status === "running");
  const status = detail.mission.status;
  return (
    <div className="mt-1.5 rounded-lg bg-white/70 px-2 py-1.5 dark:bg-slate-950/50">
      <p className="font-medium text-slate-700 dark:text-slate-200">
        {status === "done"
          ? "Trabajo completado"
          : status === "error"
            ? "El trabajo encontró un error"
            : status === "waiting_confirmation"
              ? "Necesita tu aprobación"
              : "Trabajo en curso"}
      </p>
      <p className="text-slate-500 dark:text-slate-400">
        {completed} de {detail.steps.length || detail.mission.plan?.length || 1} pasos
        {running ? ` · ${running.instruccion}` : ""}
      </p>
      {detail.mission.error && <p className="mt-1 text-red-600">{detail.mission.error}</p>}
    </div>
  );
}

function formatDuration(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return minutes > 0 ? `${minutes}m ${remainder}s` : `${remainder}s`;
}
