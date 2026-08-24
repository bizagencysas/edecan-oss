"use client";

import { useRef, useState } from "react";

import {
  ChatIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  PencilIcon,
  PlusIcon,
  TrashIcon,
} from "@/components/icons";
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
  collapsed = false,
  onSelect,
  onCreate,
  onDelete,
  onRename,
  onToggleCollapsed,
}: {
  conversations: ConversationOut[];
  activeId: string | null;
  loading: boolean;
  creating: boolean;
  collapsed?: boolean;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onDelete: (id: string) => void;
  onRename: (id: string, title: string) => Promise<void>;
  onToggleCollapsed?: () => void;
}) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const savesInFlight = useRef(new Set<string>());

  async function saveTitle(id: string) {
    if (savesInFlight.current.has(id)) return;
    const title = draftTitle.trim();
    if (!title) return;
    savesInFlight.current.add(id);
    setEditingId(null);
    try {
      await onRename(id, title);
    } finally {
      savesInFlight.current.delete(id);
    }
  }

  if (collapsed) {
    return (
      <div className="flex h-full min-h-0 flex-col items-center" data-testid="conversation-list-collapsed">
        <div className="flex w-full shrink-0 flex-col items-center gap-2 border-b border-slate-200 py-3 dark:border-slate-800">
          <button
            type="button"
            onClick={onToggleCollapsed}
            className="rounded-xl p-2 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-900 dark:hover:bg-slate-800 dark:hover:text-white"
            aria-label="Expandir historial"
            title="Expandir historial"
          >
            <ChevronRightIcon className="h-4 w-4" />
          </button>
          <Button
            size="sm"
            onClick={onCreate}
            loading={creating}
            aria-label="Nueva conversación"
            title="Nueva conversación"
            className="h-9 w-9 rounded-xl px-0"
          >
            <PlusIcon className="h-4 w-4" />
          </Button>
        </div>

        <div className="min-h-0 w-full flex-1 overflow-y-auto overscroll-contain py-2 thin-scrollbar">
          {loading ? (
            <div className="flex justify-center py-5">
              <Spinner className="h-4 w-4 text-slate-400" />
            </div>
          ) : (
            <ul className="space-y-1 px-2">
              {conversations.map((conversation) => (
                <li key={conversation.id}>
                  <button
                    type="button"
                    onClick={() => onSelect(conversation.id)}
                    className={cx(
                      "flex h-10 w-full items-center justify-center rounded-xl transition-colors",
                      conversation.id === activeId
                        ? "bg-brand-100 text-brand-700 dark:bg-brand-900/50 dark:text-brand-300"
                        : "text-slate-400 hover:bg-slate-100 hover:text-slate-800 dark:hover:bg-slate-800 dark:hover:text-slate-100",
                    )}
                    aria-label={conversation.title || "Nueva conversación"}
                    title={conversation.title || "Nueva conversación"}
                  >
                    <ChatIcon className="h-4 w-4" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="conversation-list-expanded">
      <div className="flex shrink-0 items-center justify-between border-b border-slate-200 px-3 py-3 dark:border-slate-800">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-100">Historial</h2>
          <p className="text-[11px] text-slate-400">
            {conversations.length === 1 ? "1 conversación" : `${conversations.length} conversaciones`}
          </p>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={onToggleCollapsed}
            className="rounded-lg p-2 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-800 dark:hover:bg-slate-800 dark:hover:text-slate-100"
            aria-label="Contraer historial"
            title="Contraer historial"
          >
            <ChevronLeftIcon className="h-4 w-4" />
          </button>
          <Button
            size="sm"
            onClick={onCreate}
            loading={creating}
            aria-label="Nueva conversación"
            title="Nueva conversación"
            className="h-9 w-9 rounded-xl px-0"
          >
            <PlusIcon className="h-4 w-4" />
          </Button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-2 thin-scrollbar">
        {loading ? (
          <div className="flex justify-center py-6">
            <Spinner className="h-4 w-4 text-slate-400" />
          </div>
        ) : conversations.length === 0 ? (
          <p className="px-3 py-8 text-center text-xs leading-relaxed text-slate-400">
            Tus conversaciones aparecerán aquí.
          </p>
        ) : (
          <ul className="space-y-1">
            {conversations.map((conversation) => (
              <li
                key={conversation.id}
                className={cx(
                  "group flex items-center rounded-xl transition-colors",
                  conversation.id === activeId
                    ? "bg-brand-50 dark:bg-brand-900/30"
                    : "hover:bg-slate-50 dark:hover:bg-slate-800/60",
                )}
              >
                {editingId === conversation.id ? (
                  <form
                    className="flex min-w-0 flex-1 items-center gap-2 px-2 py-2"
                    onSubmit={(event) => {
                      event.preventDefault();
                      void saveTitle(conversation.id);
                    }}
                  >
                    <input
                      autoFocus
                      value={draftTitle}
                      maxLength={120}
                      onChange={(event) => setDraftTitle(event.target.value)}
                      onBlur={() => void saveTitle(conversation.id)}
                      onKeyDown={(event) => {
                        if (event.key === "Escape") setEditingId(null);
                      }}
                      className="min-w-0 flex-1 rounded-lg border border-brand-300 bg-white px-2 py-1.5 text-sm outline-none ring-brand-200 focus:ring-2 dark:border-brand-700 dark:bg-slate-900"
                      aria-label="Nombre de la conversación"
                    />
                  </form>
                ) : (
                  <button
                    type="button"
                    onClick={() => onSelect(conversation.id)}
                    onDoubleClick={() => {
                      setEditingId(conversation.id);
                      setDraftTitle(conversation.title || "");
                    }}
                    className="flex min-w-0 flex-1 items-center gap-2.5 px-3 py-2.5 text-left"
                  >
                  <span
                    className={cx(
                      "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg",
                      conversation.id === activeId
                        ? "bg-brand-100 text-brand-700 dark:bg-brand-900/60 dark:text-brand-300"
                        : "bg-slate-100 text-slate-400 dark:bg-slate-800",
                    )}
                  >
                    <ChatIcon className="h-3.5 w-3.5" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium text-slate-800 dark:text-slate-100">
                      {conversation.title || "Nueva conversación"}
                    </span>
                    <span className="block truncate text-[11px] text-slate-400">
                      {formatDateTime(conversation.updated_at)}
                    </span>
                  </span>
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => {
                    setEditingId(conversation.id);
                    setDraftTitle(conversation.title || "");
                  }}
                  className="shrink-0 rounded-lg p-2 text-slate-300 opacity-0 transition-all hover:bg-brand-50 hover:text-brand-600 focus-visible:opacity-100 group-hover:opacity-100 dark:hover:bg-brand-950/40"
                  aria-label={`Renombrar ${conversation.title || "conversación"}`}
                  title="Renombrar conversación"
                >
                  <PencilIcon className="h-3.5 w-3.5" />
                </button>
                <button
                  type="button"
                  onClick={() => onDelete(conversation.id)}
                  className="mr-2 shrink-0 rounded-lg p-2 text-slate-300 opacity-0 transition-all hover:bg-rose-50 hover:text-rose-600 focus-visible:opacity-100 group-hover:opacity-100 dark:hover:bg-rose-950/40"
                  aria-label={`Borrar ${conversation.title || "conversación"}`}
                  title="Borrar conversación"
                >
                  <TrashIcon className="h-3.5 w-3.5" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
