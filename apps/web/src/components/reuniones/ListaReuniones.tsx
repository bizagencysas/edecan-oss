"use client";

/**
 * Lista de reuniones con badge de status — mientras alguna esté
 * `pending`/`running`, la página (`app/reuniones/page.tsx`) hace polling y
 * vuelve a pasar la lista actualizada acá (`ARCHITECTURE.md` §15, WP-V6-05).
 */

import { MicIcon, TrashIcon } from "@/components/icons";
import { Badge, Spinner } from "@/components/ui";
import type { ReunionOut, ReunionStatus } from "@/lib/api-reuniones";
import { formatDateTime } from "@/lib/format";

const STATUS_LABEL: Record<ReunionStatus, string> = {
  pending: "En cola",
  running: "Procesando",
  done: "Lista",
  error: "Error",
};

const STATUS_VARIANT: Record<ReunionStatus, "brand" | "success" | "warning" | "danger" | "neutral"> = {
  pending: "neutral",
  running: "warning",
  done: "success",
  error: "danger",
};

function StatusBadge({ status }: { status: ReunionStatus }) {
  if (status === "running") {
    return (
      <span className="inline-flex items-center gap-1.5">
        <Spinner className="h-3 w-3 text-amber-500" />
        <Badge variant={STATUS_VARIANT[status]}>{STATUS_LABEL[status]}</Badge>
      </span>
    );
  }
  return <Badge variant={STATUS_VARIANT[status]}>{STATUS_LABEL[status]}</Badge>;
}

export function ListaReuniones({
  reuniones,
  selectedId,
  onSelect,
  onDelete,
}: {
  reuniones: ReunionOut[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  return (
    <ul className="space-y-2">
      {reuniones.map((r) => {
        const seleccionada = r.id === selectedId;
        return (
          <li
            key={r.id}
            className={`flex items-center justify-between gap-3 rounded-lg border px-3 py-2.5 transition-colors ${
              seleccionada
                ? "border-brand-400 bg-brand-50 dark:border-brand-700 dark:bg-brand-950/30"
                : "border-slate-100 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/60"
            }`}
          >
            <button
              type="button"
              onClick={() => onSelect(r.id)}
              className="flex min-w-0 flex-1 items-center gap-3 text-left"
            >
              <MicIcon className="h-4 w-4 shrink-0 text-slate-400" />
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-slate-700 dark:text-slate-200">
                  {r.titulo}
                </p>
                <p className="text-xs text-slate-400">{formatDateTime(r.created_at)}</p>
              </div>
            </button>
            <div className="flex shrink-0 items-center gap-2">
              <StatusBadge status={r.status} />
              <button
                type="button"
                onClick={() => onDelete(r.id)}
                className="rounded p-1 text-slate-400 hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-950/40 dark:hover:text-rose-400"
                aria-label={`Borrar reunión ${r.titulo}`}
              >
                <TrashIcon className="h-3.5 w-3.5" />
              </button>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
