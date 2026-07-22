"use client";

import { Button } from "@/components/ui";

export function HistoryNav({
  currentIndex,
  totalCount,
  onPrev,
  onNext,
  onDuplicate,
  disabled = false,
}: {
  currentIndex: number;
  totalCount: number;
  onPrev: () => void;
  onNext: () => void;
  onDuplicate: () => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex items-center gap-1 rounded-lg border border-slate-200 bg-white p-1 dark:border-slate-700 dark:bg-slate-900">
      <Button type="button" size="sm" variant="ghost" onClick={onPrev} disabled={disabled || currentIndex <= 0}>
        Anterior
      </Button>
      <span className="min-w-14 text-center text-[11px] tabular-nums text-slate-500 dark:text-slate-400">
        {totalCount ? `V${currentIndex + 1}/${totalCount}` : "Sin versión"}
      </span>
      <Button type="button" size="sm" variant="ghost" onClick={onNext} disabled={disabled || currentIndex >= totalCount - 1}>
        Siguiente
      </Button>
      <Button type="button" size="sm" variant="secondary" onClick={onDuplicate} disabled={disabled || totalCount === 0} title="Crear una rama reversible">
        Ramificar
      </Button>
    </div>
  );
}
