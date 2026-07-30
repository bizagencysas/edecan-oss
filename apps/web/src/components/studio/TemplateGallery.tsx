"use client";

import { useMemo, useState } from "react";

import { Button } from "@/components/ui";
import type { StudioTemplate } from "@/lib/studio";

export function TemplateGallery({ templates, busy, onUse, onClose }: { templates: StudioTemplate[]; busy: boolean; onUse: (template: StudioTemplate) => void; onClose: () => void }) {
  const [category, setCategory] = useState("all");
  const categories = useMemo(() => [...new Set(templates.map((item) => item.category))], [templates]);
  const filtered = category === "all" ? templates : templates.filter((item) => item.category === category);
  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-slate-950/60 p-3 backdrop-blur-sm" role="presentation" onMouseDown={onClose}>
      <section className="flex max-h-[88dvh] w-full max-w-4xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl dark:bg-slate-900" role="dialog" aria-modal="true" aria-labelledby="templates-title" onMouseDown={(event) => event.stopPropagation()}>
        <header className="flex items-center justify-between border-b border-slate-200 px-5 py-4 dark:border-slate-700">
          <div><h2 id="templates-title" className="font-semibold text-slate-900 dark:text-white">Plantillas de Studio</h2><p className="text-xs text-slate-500">Empieza desde una base versionada y privada.</p></div>
          <Button type="button" size="sm" variant="ghost" onClick={onClose}>Cerrar</Button>
        </header>
        <div className="flex flex-wrap gap-2 border-b border-slate-100 px-5 py-3 dark:border-slate-800">
          {["all", ...categories].map((item) => <button key={item} type="button" onClick={() => setCategory(item)} className={`rounded-full px-3 py-1 text-xs font-medium ${category === item ? "bg-brand-600 text-white" : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300"}`}>{item === "all" ? "Todas" : item}</button>)}
        </div>
        <div className="grid min-h-40 flex-1 gap-4 overflow-y-auto p-5 sm:grid-cols-2 lg:grid-cols-3">
          {filtered.length === 0 ? <p className="col-span-full py-12 text-center text-sm text-slate-500">Aún no hay plantillas. Puedes guardar el proyecto actual como una.</p> : filtered.map((template) => (
            <article key={template.id} className="flex flex-col rounded-xl border border-slate-200 p-4 dark:border-slate-700">
              <div className="mb-3 flex h-24 items-center justify-center rounded-lg bg-gradient-to-br from-brand-50 to-indigo-100 text-2xl font-bold text-brand-600 dark:from-brand-950 dark:to-indigo-950">E</div>
              <span className="text-[10px] font-semibold uppercase tracking-wider text-brand-600">{template.category}</span>
              <h3 className="mt-1 font-semibold text-slate-900 dark:text-white">{template.name}</h3>
              <p className="mt-1 flex-1 text-xs leading-relaxed text-slate-500">{template.description || `${template.mode} · ${template.width}×${template.height}`}</p>
              <Button type="button" className="mt-4 w-full" size="sm" onClick={() => onUse(template)} loading={busy}>Usar plantilla</Button>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
