"use client";

import { Button, Field, Select } from "@/components/ui";
import type { StudioMode, StudioQuality } from "@/lib/studio";

export interface StudioCreateOptions {
  mode: StudioMode;
  quality: StudioQuality;
  count: number;
  width: number;
  height: number;
}

export function ClarifyPanel({
  prompt,
  options,
  busy,
  onChange,
  onBack,
  onGenerate,
}: {
  prompt: string;
  options: StudioCreateOptions;
  busy: boolean;
  onChange: (options: StudioCreateOptions) => void;
  onBack: () => void;
  onGenerate: () => void;
}) {
  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-slate-950/60 p-3 backdrop-blur-sm" role="presentation" onMouseDown={onBack}>
      <section className="w-full max-w-lg rounded-2xl bg-white p-5 shadow-2xl dark:bg-slate-900" role="dialog" aria-modal="true" aria-labelledby="studio-clarify-title" onMouseDown={(event) => event.stopPropagation()}>
        <div className="mb-5">
          <p className="text-xs font-semibold uppercase tracking-wider text-brand-600">Antes de crear</p>
          <h2 id="studio-clarify-title" className="mt-1 text-xl font-semibold text-slate-900 dark:text-white">Solo necesito tres decisiones</h2>
          <p className="mt-2 line-clamp-3 rounded-lg bg-slate-50 p-3 text-sm text-slate-600 dark:bg-slate-800 dark:text-slate-300">{prompt}</p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Formato" htmlFor="studio-mode">
            <Select id="studio-mode" value={options.mode} onChange={(event) => onChange({ ...options, mode: event.target.value as StudioMode })}>
              <option value="general">Diseño general</option><option value="mockup">App / producto</option><option value="landing">Landing</option><option value="carousel">Carrusel</option><option value="post">Post</option><option value="ad">Anuncio</option><option value="deck">Presentación</option><option value="email">Email</option>
            </Select>
          </Field>
          <Field label="Calidad" htmlFor="studio-quality" hint="Equilibrada incluye revisión visual.">
            <Select id="studio-quality" value={options.quality} onChange={(event) => onChange({ ...options, quality: event.target.value as StudioQuality })}>
              <option value="fast">Rápida</option><option value="balanced">Equilibrada (recomendada)</option><option value="max">Máxima</option>
            </Select>
          </Field>
          <Field label="Variantes" htmlFor="studio-count">
            <Select id="studio-count" value={options.count} onChange={(event) => onChange({ ...options, count: Number(event.target.value) })}>
              <option value={1}>1 concepto</option><option value={2}>2 conceptos</option><option value={3}>3 conceptos</option><option value={4}>4 conceptos</option>
            </Select>
          </Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Ancho"><input aria-label="Ancho" type="number" min={320} max={4096} value={options.width} onChange={(event) => onChange({ ...options, width: Number(event.target.value) })} className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-950" /></Field>
            <Field label="Alto"><input aria-label="Alto" type="number" min={320} max={4096} value={options.height} onChange={(event) => onChange({ ...options, height: Number(event.target.value) })} className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-950" /></Field>
          </div>
        </div>
        <div className="mt-6 flex justify-end gap-2 border-t border-slate-200 pt-4 dark:border-slate-700">
          <Button type="button" variant="ghost" onClick={onBack} disabled={busy}>Volver</Button>
          <Button type="button" onClick={onGenerate} loading={busy}>Crear y guardar revisión</Button>
        </div>
      </section>
    </div>
  );
}
