"use client";

/**
 * Chrome de un paso del wizard de bienvenida (`app/(app)/app/bienvenida`):
 * barra de progreso + título + descripción. Cada paso compone su propio
 * contenido y botones de navegación como `children` — este componente no
 * asume qué acciones tiene cada paso (conectar, saltar, CTA final…), solo
 * da el marco visual consistente (DIRECCION_ACTUAL.md "Wizard de primer
 * arranque: pantalla corta... 2-3 pasos como máximo").
 */

import type { ReactNode } from "react";

export function PasoWizard({
  paso,
  totalPasos,
  titulo,
  descripcion,
  children,
}: {
  paso: number;
  totalPasos: number;
  titulo: string;
  descripcion?: string;
  children: ReactNode;
}) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-panel dark:border-slate-800 dark:bg-slate-900 sm:p-8">
      <div className="mb-6">
        <div
          className="mb-2 flex items-center gap-1.5"
          role="progressbar"
          aria-valuenow={paso}
          aria-valuemin={1}
          aria-valuemax={totalPasos}
          aria-label={`Paso ${paso} de ${totalPasos}`}
        >
          {Array.from({ length: totalPasos }, (_, i) => i + 1).map((n) => (
            <span
              key={n}
              className={
                "h-1.5 flex-1 rounded-full transition-colors " +
                (n <= paso ? "bg-brand-600" : "bg-slate-200 dark:bg-slate-700")
              }
            />
          ))}
        </div>
        <p className="text-xs font-medium text-slate-400">
          Paso {paso} de {totalPasos}
        </p>
        <h1 className="mt-1 text-xl font-semibold text-slate-900 dark:text-slate-50">{titulo}</h1>
        {descripcion && <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{descripcion}</p>}
      </div>
      {children}
    </div>
  );
}
