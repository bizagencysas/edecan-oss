"use client";

import { useState } from "react";

import { Button, Textarea } from "@/components/ui";

export interface StudioSelection {
  x: number;
  y: number;
  label: string;
}

export function InlineInspector({ selection, busy, onApply }: { selection: StudioSelection | null; busy: boolean; onApply: (instruction: string) => void }) {
  const [instruction, setInstruction] = useState("");
  return (
    <section aria-labelledby="inline-title">
      <p className="text-[10px] font-bold uppercase tracking-widest text-slate-400">Edición contextual</p>
      <h3 id="inline-title" className="text-sm font-semibold text-slate-800 dark:text-slate-100">Señala y pide el cambio</h3>
      {!selection ? <p className="mt-2 rounded-lg bg-slate-50 p-3 text-xs leading-relaxed text-slate-500 dark:bg-slate-800">Activa “Señalar”, toca una zona del diseño y describe el resultado que quieres. La revisión anterior siempre queda disponible.</p> : (
        <div className="mt-3">
          <div className="mb-2 flex items-center gap-2 text-xs text-slate-500"><span className="flex h-5 w-5 items-center justify-center rounded-full bg-brand-600 font-semibold text-white">1</span><span>{selection.label}</span></div>
          <Textarea value={instruction} onChange={(event) => setInstruction(event.target.value)} rows={3} placeholder="Ej. Haz este titular más corto y aumenta el contraste…" className="min-h-20" />
          <Button type="button" className="mt-2 w-full" size="sm" onClick={() => { onApply(instruction.trim()); setInstruction(""); }} disabled={!instruction.trim()} loading={busy}>Aplicar en una revisión nueva</Button>
        </div>
      )}
    </section>
  );
}
