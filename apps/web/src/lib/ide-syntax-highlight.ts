/**
 * Resaltado de SOLO LECTURA con Shiki, para diffs y vistas previas de código
 * (§7 de `docs/FORGE-CONSTRUCCION-COMPLETA.md`: "Resaltado de solo lectura |
 * Shiki (`codeToHast`, en servidor) | MIT"). Aquí corre en el navegador y no
 * en un servidor: el Mac empaqueta esta app como export estático de Tauri
 * (`page.tsx` del IDE, cabecera del encargo) y no hay ninguna capa HTTP
 * propia donde hacer ese trabajo antes de servir la página. El contrato de
 * §7 es el mismo igual: tokens por línea listos para pintarse, calculados
 * UNA vez por archivo, nunca en cada tecla.
 *
 * Deliberadamente NO se usa para editar: la textarea de `CodeEditor.tsx`
 * sigue monoespaciada y sin dependencias nuevas, tal como pide el encargo de
 * este paquete de trabajo. Esto solo tokeniza texto que ya no se edita -- el
 * "antes"/"después" de la propuesta de ⌘K (`CodeEditor.tsx::DiffPreview`).
 * `DiffReview.tsx` (el diff de un turno completo del agente) queda fuera a
 * propósito: otro agente de este mismo lote lo está tocando en paralelo
 * ahora mismo (se comprobó leyéndolo dos veces en la misma sesión y ya había
 * cambiado), así que integrarlo ahí es trabajo pendiente de quien lo tenga
 * asignado, no de este archivo.
 *
 * Import perezoso (`import()` dinámico): las gramáticas y temas de Shiki no
 * entran al bundle inicial del IDE, solo se descargan la primera vez que de
 * verdad hay una propuesta que resaltar.
 */

import type { BundledLanguage, ThemedToken } from "shiki";

/** Extensión de archivo (sin el punto, en minúsculas) -> id de lenguaje de Shiki. */
const LANG_BY_EXTENSION: Record<string, BundledLanguage> = {
  ts: "typescript",
  tsx: "tsx",
  js: "javascript",
  jsx: "jsx",
  mjs: "javascript",
  cjs: "javascript",
  mts: "typescript",
  cts: "typescript",
  py: "python",
  json: "json",
  jsonc: "jsonc",
  css: "css",
  scss: "scss",
  html: "html",
  md: "markdown",
  mdx: "mdx",
  yml: "yaml",
  yaml: "yaml",
  sh: "bash",
  bash: "bash",
  zsh: "bash",
  sql: "sql",
  rs: "rust",
  go: "go",
  toml: "toml",
  swift: "swift",
  kt: "kotlin",
  java: "java",
  rb: "ruby",
  php: "php",
  c: "c",
  h: "c",
  cpp: "cpp",
  hpp: "cpp",
  graphql: "graphql",
  vue: "vue",
};

/** `null` cuando la extensión no se reconoce: quien llama debe caer a texto plano. */
export function languageForPath(path: string): BundledLanguage | null {
  const name = path.split("/").pop() ?? path;
  const dot = name.lastIndexOf(".");
  if (dot <= 0) return null;
  const ext = name.slice(dot + 1).toLowerCase();
  return LANG_BY_EXTENSION[ext] ?? null;
}

const THEME_LIGHT = "github-light";
const THEME_DARK = "github-dark";

let shikiModulePromise: Promise<typeof import("shiki")> | null = null;

function loadShiki() {
  // Un solo `import()` compartido por toda la sesión: la segunda llamada (y
  // las siguientes) reusan el mismo módulo ya descargado en vez de pedirlo
  // de nuevo por cada archivo o cada vez que se abre un diff.
  if (!shikiModulePromise) shikiModulePromise = import("shiki");
  return shikiModulePromise;
}

/**
 * Tokeniza `code` línea por línea para el lenguaje que corresponda a `path`.
 *
 * Devuelve `null` -- nunca lanza -- cuando el lenguaje no se reconoce o Shiki
 * no pudo cargar (sin red la primera vez que hace falta, por ejemplo): quien
 * llama debe mostrar el texto plano de siempre en ese caso. Fingir un
 * resaltado que no ocurrió sería peor que no tenerlo.
 */
export async function highlightLines(code: string, path: string, dark: boolean): Promise<ThemedToken[][] | null> {
  const lang = languageForPath(path);
  if (!lang || !code) return null;
  try {
    const { codeToTokens } = await loadShiki();
    const { tokens } = await codeToTokens(code, {
      lang,
      theme: dark ? THEME_DARK : THEME_LIGHT,
    });
    return tokens;
  } catch {
    return null;
  }
}

export type { ThemedToken };
