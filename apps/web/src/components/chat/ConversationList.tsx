"use client";

import { PlusIcon, TrashIcon } from "@/components/icons";
import { Button, Spinner } from "@/components/ui";
import { formatDateTime } from "@/lib/format";
import type { ConversationOut } from "@/lib/types";

function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

export function ConversationList({
  conversations,
  activeId,
  loading,
  creating,
  onSelect,
  onCreate,
  onDelete,
}: {
  conversations: ConversationOut[];
  activeId: string | null;
  loading: boolean;
  creating: boolean;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onDelete: (id: string) => void;
}) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-slate-200 px-3 py-3 dark:border-slate-800">
        <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-200">Conversaciones</h2>
        <Button size="sm" variant="secondary" onClick={onCreate} loading={creating} aria-label="Nueva conversación">
          <PlusIcon className="h-3.5 w-3.5" />
        </Button>
      </div>
      <div className="flex-1 overflow-y-auto thin-scrollbar">
        {loading ? (
          <div className="flex justify-center py-6">
            <Spinner className="h-4 w-4 text-slate-400" />
          </div>
        ) : conversations.length === 0 ? (
          <p className="px-3 py-6 text-center text-xs text-slate-400">Aún no tienes conversaciones.</p>
        ) : (
          <ul>
            {conversations.map((conv) => (
              <li key={conv.id}>
                <button
                  onClick={() => onSelect(conv.id)}
                  className={cx(
                    "group flex w-full items-center justify-between gap-2 border-b border-slate-100 px-3 py-2.5 text-left text-sm dark:border-slate-800/60",
                    conv.id === activeId
                      ? "bg-brand-50 dark:bg-brand-900/30"
                      : "hover:bg-slate-50 dark:hover:bg-slate-800/60",
                  )}
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium text-slate-800 dark:text-slate-100">
                      {conv.title || "Nueva conversación"}
                    </span>
                    <span className="block text-xs text-slate-400">{formatDateTime(conv.updated_at)}</span>
                  </span>
                  <span
                    role="button"
                    tabIndex={0}
                    onClick={(e) => {
                      e.stopPropagation();
                      onDelete(conv.id);
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.stopPropagation();
                        onDelete(conv.id);
                      }
                    }}
                    className="shrink-0 rounded-md p-1.5 text-slate-300 opacity-0 hover:bg-rose-50 hover:text-rose-600 group-hover:opacity-100 dark:hover:bg-rose-950/40"
                    aria-label="Borrar conversación"
                  >
                    <TrashIcon className="h-3.5 w-3.5" />
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
