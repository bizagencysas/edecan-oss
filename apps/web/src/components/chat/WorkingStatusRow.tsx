"use client";

import { SparklesIcon } from "@/components/icons";

/** Fila silenciosa del chat: avatar + «{Nombre} está trabajando». Sin tarjeta ni spinner aparte. */
export function WorkingStatusRow({ agentName = "Edecán" }: { agentName?: string }) {
  const visibleName = agentName.trim() || "Edecán";
  const accessibilityLabel = `${visibleName} está trabajando`;

  return (
    <div
      className="flex items-center gap-2.5 py-1"
      role="status"
      aria-live="polite"
      aria-label={accessibilityLabel}
    >
      <span
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-brand-600 to-indigo-600 text-white shadow-sm"
        aria-hidden
      >
        <SparklesIcon className="h-3.5 w-3.5" />
      </span>
      <p className="text-sm leading-none">
        <span className="text-slate-500 dark:text-slate-400">{visibleName} está </span>
        <span className="font-medium text-slate-800 dark:text-slate-100">trabajando</span>
      </p>
    </div>
  );
}
