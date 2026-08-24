"use client";

import { useState } from "react";

import { Button, Input, Select } from "@/components/ui";
import { createTweakControlFromPrompt, type TweakControl } from "@/lib/studio";

export function TweaksPanel({ controls, busy, onChange, onApply }: { controls: TweakControl[]; busy: boolean; onChange: (controls: TweakControl[]) => void; onApply: () => void }) {
  const [request, setRequest] = useState("");
  function addControl() {
    const control = createTweakControlFromPrompt(request);
    if (!control) return;
    onChange([...controls, control]);
    setRequest("");
  }
  function update(id: string, value: string | number | boolean) {
    onChange(controls.map((control) => control.id === id ? { ...control, value } as TweakControl : control));
  }
  return (
    <section className="border-t border-slate-200 pt-4 dark:border-slate-700" aria-labelledby="tweaks-title">
      <div className="flex items-center justify-between"><div><p className="text-[10px] font-bold uppercase tracking-widest text-slate-400">Ajustes</p><h3 id="tweaks-title" className="text-sm font-semibold text-slate-800 dark:text-slate-100">Controles dinámicos</h3></div><span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-500 dark:bg-slate-800">{controls.length}</span></div>
      <div className="mt-3 flex gap-2">
        <Input value={request} onChange={(event) => setRequest(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") addControl(); }} placeholder="Ej. color de acento, radio…" className="min-w-0" />
        <Button type="button" size="sm" onClick={addControl} disabled={!request.trim()}>Añadir</Button>
      </div>
      {controls.length === 0 ? <p className="mt-3 text-xs leading-relaxed text-slate-500">Describe un control y Edecán lo convierte en un ajuste que puede aplicar como nueva revisión.</p> : (
        <div className="mt-3 space-y-3">
          {controls.map((control) => (
            <div key={control.id} className="rounded-lg border border-slate-200 p-3 dark:border-slate-700">
              <div className="mb-2 flex items-center justify-between gap-2"><label className="truncate text-xs font-medium text-slate-700 dark:text-slate-200" htmlFor={control.id}>{control.label}</label><button type="button" className="text-xs text-slate-400 hover:text-rose-500" onClick={() => onChange(controls.filter((item) => item.id !== control.id))}>Quitar</button></div>
              {control.type === "toggle" && <label className="flex items-center gap-2 text-xs text-slate-500"><input id={control.id} type="checkbox" checked={control.value} onChange={(event) => update(control.id, event.target.checked)} />{control.value ? "Activado" : "Desactivado"}</label>}
              {control.type === "slider" && <div className="flex items-center gap-2"><input id={control.id} type="range" className="min-w-0 flex-1 accent-brand-600" min={control.min} max={control.max} step={control.step} value={control.value} onChange={(event) => update(control.id, Number(event.target.value))} /><span className="text-xs tabular-nums text-slate-500">{control.value}{control.unit}</span></div>}
              {control.type === "color" && <div className="flex gap-2"><input id={control.id} type="color" value={control.value} onChange={(event) => update(control.id, event.target.value)} className="h-9 w-10 rounded" /><Input value={control.value} onChange={(event) => update(control.id, event.target.value)} /></div>}
              {control.type === "select" && <Select id={control.id} value={control.value} onChange={(event) => update(control.id, event.target.value)}>{control.options.map((option) => <option key={option}>{option}</option>)}</Select>}
            </div>
          ))}
          <Button type="button" variant="secondary" className="w-full" size="sm" onClick={onApply} loading={busy}>Aplicar como nueva revisión</Button>
        </div>
      )}
    </section>
  );
}
