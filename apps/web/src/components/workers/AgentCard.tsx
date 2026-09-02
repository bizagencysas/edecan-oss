/**
 * Tarjeta de compañero en el roster. Todo el bloque es un botón que abre el
 * detalle. Muestra avatar, nombre visible, rol y presencia — sin ruido.
 */

"use client";

import type { PersistentWorker } from "@/lib/api";

import { AgentAvatar } from "./AgentAvatar";
import { PresenceDot, presenceState } from "./PresenceDot";
import { agentStatusPhrase } from "./status";

export function AgentCard({
  worker,
  onSelect,
}: {
  worker: PersistentWorker;
  onSelect: (worker: PersistentWorker) => void;
}) {
  const state = presenceState(worker.status, worker.enabled);
  const title = worker.display_name?.trim() || worker.name;
  const subtitle = worker.role_title?.trim() || worker.purpose;

  return (
    <button
      type="button"
      onClick={() => onSelect(worker)}
      className="group flex w-full items-start gap-3 rounded-2xl border border-slate-200 bg-white p-4 text-left shadow-panel transition-colors hover:border-slate-300 hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600 dark:border-slate-800 dark:bg-slate-900 dark:hover:border-slate-700 dark:hover:bg-slate-800/60"
    >
      <AgentAvatar
        name={worker.name}
        displayName={worker.display_name}
        avatar={worker.avatar}
        size="lg"
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-semibold text-slate-900 dark:text-slate-100">
            {title}
          </span>
        </div>
        <p className="mt-0.5 truncate text-xs text-slate-500 dark:text-slate-400">{subtitle}</p>
        <div className="mt-2 flex items-center gap-1.5 text-[11px] text-slate-500 dark:text-slate-400">
          <PresenceDot state={state} />
          <span className="truncate">{agentStatusPhrase(worker)}</span>
        </div>
      </div>
    </button>
  );
}