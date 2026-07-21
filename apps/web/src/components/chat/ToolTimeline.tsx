"use client";

import { CheckIcon } from "@/components/icons";
import { Spinner } from "@/components/ui";
import type { ArtifactRef } from "@/lib/types";

import { ArtifactLinks } from "./ArtifactLinks";

export interface ToolEvent {
  callKey: string;
  name: string;
  args: Record<string, unknown>;
  status: "running" | "done";
  resultPreview?: string;
  artifacts?: ArtifactRef[];
}

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
            <span className="font-mono font-medium text-slate-700 dark:text-slate-200">{event.name}</span>
            {event.status === "done" && event.resultPreview && (
              <span className="text-slate-500 dark:text-slate-400"> — {event.resultPreview}</span>
            )}
            {event.status === "running" && <span className="text-slate-400"> — ejecutando…</span>}
            {event.status === "done" && <ArtifactLinks artifacts={event.artifacts ?? []} />}
          </div>
        </div>
      ))}
    </div>
  );
}
