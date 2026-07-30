"use client";

/**
 * Campo atómico "pegar y validar" (DIRECCION_ACTUAL.md "Para API keys...:
 * flujo de pegar-y-validar — un campo, un botón 'Conectar'... con un link
 * directo a la página donde se saca esa key"). `type=password` con toggle
 * "Mostrar/Ocultar" (nunca se persiste en localStorage — viaja directo al
 * backend cuando el formulario que lo usa llama al PUT correspondiente).
 * Reutilizado por `SelectorLLM` y `SelectorVoz`.
 */

import { useState } from "react";

import { Input, Label } from "@/components/ui";

export function CampoLlave({
  id,
  label,
  value,
  onChange,
  placeholder,
  linkHref,
  linkLabel = "Sacar mi key →",
  hint,
  disabled,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  linkHref: string;
  linkLabel?: string;
  hint?: string;
  disabled?: boolean;
}) {
  const [mostrar, setMostrar] = useState(false);

  return (
    <div>
      <Label htmlFor={id}>{label}</Label>
      <div className="relative">
        <Input
          id={id}
          type={mostrar ? "text" : "password"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          autoComplete="off"
          disabled={disabled}
          className="pr-16"
        />
        <button
          type="button"
          onClick={() => setMostrar((s) => !s)}
          aria-pressed={mostrar}
          className="absolute right-2 top-1/2 -translate-y-1/2 rounded px-1.5 py-0.5 text-xs font-medium text-slate-400 hover:text-slate-600 dark:hover:text-slate-200"
        >
          {mostrar ? "Ocultar" : "Mostrar"}
        </button>
      </div>
      <div className="mt-1 flex flex-wrap items-center justify-between gap-x-3 gap-y-0.5">
        {hint ? (
          <p className="text-xs text-slate-500 dark:text-slate-400">{hint}</p>
        ) : (
          <span />
        )}
        <a
          href={linkHref}
          target="_blank"
          rel="noreferrer"
          className="text-xs font-medium text-brand-600 hover:text-brand-700 dark:text-brand-400"
        >
          {linkLabel}
        </a>
      </div>
    </div>
  );
}
