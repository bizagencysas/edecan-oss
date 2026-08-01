/**
 * Tema oscuro/claro (`tailwind.config.ts` usa `darkMode: "class"`). Oscuro es
 * el default del producto; el usuario puede cambiar a claro y se recuerda en
 * `localStorage`. Módulo separado de `ui.tsx`/`AppShell.tsx` para que el
 * script inline de `app/layout.tsx` (fija la clase ANTES del primer paint,
 * evita parpadeo) y el componente `ThemeToggle` compartan la misma clave.
 */

const STORAGE_KEY = "edecan_theme";

export type Theme = "dark" | "light";

export function getStoredTheme(): Theme {
  if (typeof window === "undefined") return "dark";
  return window.localStorage.getItem(STORAGE_KEY) === "light" ? "light" : "dark";
}

export function applyTheme(theme: Theme): void {
  if (typeof document === "undefined") return;
  document.documentElement.classList.toggle("dark", theme === "dark");
}

export function setStoredTheme(theme: Theme): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, theme);
  applyTheme(theme);
}

/**
 * Script inline para `app/layout.tsx`: corre antes del primer paint y fija
 * `class="dark"` en `<html>` salvo que el usuario ya haya elegido "light"
 * antes. Sin esto, `darkMode: "class"` nunca se activaría solo porque el
 * sistema operativo esté en oscuro (Tailwind con modo "class" ignora
 * `prefers-color-scheme`) y la primera pintura parpadearía en claro.
 */
export const THEME_INIT_SCRIPT = `(function(){try{if(localStorage.getItem('${STORAGE_KEY}')!=='light'){document.documentElement.classList.add('dark');}}catch(e){document.documentElement.classList.add('dark');}})();`;
