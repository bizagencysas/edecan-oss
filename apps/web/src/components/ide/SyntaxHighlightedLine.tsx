"use client";

/**
 * Piezas pequeñas y compartidas para pintar texto YA TOKENIZADO por
 * `lib/ide-syntax-highlight.ts` -- ver ese archivo para el porqué (§7,
 * resaltado de solo lectura con Shiki, solo para diffs y vistas previas).
 */

import { useEffect, useState } from "react";

import type { ThemedToken } from "@/lib/ide-syntax-highlight";

/**
 * Sigue la clase `dark` de `<html>` (`lib/theme.ts::applyTheme`), que es de
 * donde Tailwind (`darkMode: "class"`) decide qué pintar. Shiki necesita el
 * tema ANTES de tokenizar (no es CSS, es color ya resuelto por token), así
 * que hace falta saber cuál es en vez de dejárselo a una clase `dark:`.
 */
export function useIsDarkMode(): boolean {
  const [dark, setDark] = useState(
    () => typeof document !== "undefined" && document.documentElement.classList.contains("dark"),
  );
  useEffect(() => {
    const root = document.documentElement;
    const observer = new MutationObserver(() => setDark(root.classList.contains("dark")));
    observer.observe(root, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return dark;
}

/**
 * Una línea de texto, coloreada token por token si `tokens` llegó (Shiki
 * tokenizó el archivo completo), o tal cual si no (lenguaje no reconocido,
 * Shiki no cargó, o la línea no existe en ese lado del diff). Nunca decide
 * por su cuenta: quien la usa ya resolvió esa rama.
 */
export function HighlightedLineText({ tokens, text }: { tokens: ThemedToken[] | null | undefined; text: string }) {
  if (!tokens) return <>{text.length ? text : " "}</>;
  if (tokens.length === 0) return <> </>;
  return (
    <>
      {tokens.map((token, index) => (
        <span key={index} style={{ color: token.color }}>
          {token.content}
        </span>
      ))}
    </>
  );
}
