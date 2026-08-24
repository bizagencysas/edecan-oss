"use client";

import { useState } from "react";

import type { ChatSource } from "@/lib/chat-sources";
import { isTauriApp, tauriInvoke } from "@/lib/tauriListen";

function openSource(url: string) {
  if (isTauriApp()) {
    void tauriInvoke("open_external_url", { url }).catch(() => {
      window.open(url, "_blank", "noopener,noreferrer");
    });
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
}

export function SourceChips({ sources }: { sources: ChatSource[] }) {
  const [expanded, setExpanded] = useState(false);
  if (sources.length === 0) return null;
  const visible = expanded ? sources : sources.slice(0, 4);

  return (
    <div className="mt-2 space-y-1.5">
      <div className="flex items-center gap-2 text-[11px] font-medium text-slate-500 dark:text-slate-400">
        <span>Fuentes · {sources.length}</span>
        {sources.length > 4 && (
          <button
            type="button"
            onClick={() => setExpanded((current) => !current)}
            className="text-brand-600 hover:underline dark:text-brand-400"
          >
            {expanded ? "Ver menos" : `Ver ${sources.length - 4} más`}
          </button>
        )}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {visible.map((source) => (
          <button
            key={source.url}
            type="button"
            title={source.snippet ?? source.url}
            onClick={() => openSource(source.url)}
            className="max-w-full truncate rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] text-slate-600 transition-colors hover:border-brand-300 hover:text-brand-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:border-brand-700 dark:hover:text-brand-300"
          >
            {source.site ? `${source.site} · ${source.title}` : source.title}
          </button>
        ))}
      </div>
    </div>
  );
}
