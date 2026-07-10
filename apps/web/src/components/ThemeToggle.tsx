"use client";

import { useEffect, useState } from "react";

import { MoonIcon, SunIcon } from "@/components/icons";
import { applyTheme, getStoredTheme, setStoredTheme, type Theme } from "@/lib/theme";

/** Botón para alternar tema oscuro/claro (oscuro es el default, ver `lib/theme.ts`). */
export function ThemeToggle({ className }: { className?: string }) {
  // El script inline de `app/layout.tsx` ya fijó la clase real en <html> antes
  // del primer paint; este estado solo decide qué ícono mostrar, así que
  // arranca en "dark" (el default) y se corrige en el primer efecto sin
  // parpadeo visible.
  const [theme, setTheme] = useState<Theme>("dark");

  useEffect(() => {
    const stored = getStoredTheme();
    setTheme(stored);
    applyTheme(stored);
  }, []);

  function toggle() {
    const next: Theme = theme === "dark" ? "light" : "dark";
    setStoredTheme(next);
    setTheme(next);
  }

  return (
    <button
      type="button"
      onClick={toggle}
      title={theme === "dark" ? "Cambiar a tema claro" : "Cambiar a tema oscuro"}
      aria-label="Cambiar tema"
      className={
        className ??
        "rounded-md p-2 text-slate-500 hover:bg-slate-100 hover:text-slate-900 dark:hover:bg-slate-800 dark:hover:text-white"
      }
    >
      {theme === "dark" ? <SunIcon className="h-4 w-4" /> : <MoonIcon className="h-4 w-4" />}
    </button>
  );
}
