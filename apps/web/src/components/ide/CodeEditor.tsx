"use client";

/**
 * Editor central del IDE embebido: textarea monoespaciada con números de
 * línea en una columna aparte, sincronizada por scroll (`ROADMAP_V2.md`
 * §7.8/§7.10 — "editor IDE = textarea monoespaciada"). Deliberadamente SIN
 * resaltado de sintaxis: cero dependencias npm nuevas y fuera del alcance
 * de este paquete de trabajo (queda como mejora futura, ver `docs/ide.md`).
 */

import { useMemo, useRef } from "react";

import { Button, Spinner } from "@/components/ui";

const LINE_HEIGHT = "1.375rem";

export function CodeEditor({
  path,
  content,
  dirty,
  loading,
  saving,
  error,
  onChange,
  onSave,
}: {
  path: string | null;
  content: string;
  dirty: boolean;
  loading: boolean;
  saving: boolean;
  error: string | null;
  onChange: (text: string) => void;
  onSave: () => void;
}) {
  const gutterRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  const lineCount = useMemo(() => (content.length === 0 ? 1 : content.split("\n").length), [content]);

  function syncGutterScroll() {
    if (gutterRef.current && textareaRef.current) {
      gutterRef.current.scrollTop = textareaRef.current.scrollTop;
    }
  }

  if (!path) {
    return (
      <div className="flex flex-1 items-center justify-center rounded-2xl border border-dashed border-slate-300 text-sm text-slate-400 dark:border-slate-700">
        Elige un archivo del árbol para editarlo.
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center justify-between gap-3 border-b border-slate-100 px-4 py-2.5 dark:border-slate-800">
        <div className="flex min-w-0 items-center gap-2">
          {loading && <Spinner className="h-3.5 w-3.5 shrink-0 text-slate-400" />}
          <code className="truncate text-sm font-medium text-slate-700 dark:text-slate-200">{path}</code>
          {dirty && !loading && (
            <span title="Cambios sin guardar" className="h-2 w-2 shrink-0 rounded-full bg-amber-500" />
          )}
        </div>
        <Button size="sm" onClick={onSave} loading={saving} disabled={!dirty || loading}>
          Guardar
        </Button>
      </div>

      {error && (
        <div className="border-b border-rose-100 bg-rose-50 px-4 py-2 text-xs text-rose-700 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-300">
          {error}
        </div>
      )}

      <div className="flex flex-1 overflow-hidden font-mono text-sm">
        <div
          ref={gutterRef}
          aria-hidden="true"
          className="select-none overflow-hidden bg-slate-50 px-2 py-2 text-right text-slate-400 dark:bg-slate-950 dark:text-slate-600"
          style={{ lineHeight: LINE_HEIGHT }}
        >
          {Array.from({ length: lineCount }, (_, i) => (
            <div key={i}>{i + 1}</div>
          ))}
        </div>
        <textarea
          ref={textareaRef}
          value={content}
          disabled={loading}
          onChange={(e) => onChange(e.target.value)}
          onScroll={syncGutterScroll}
          spellCheck={false}
          aria-label={`Editor de ${path}`}
          className="flex-1 resize-none overflow-auto bg-transparent px-3 py-2 text-slate-800 outline-none disabled:opacity-60 dark:text-slate-100"
          style={{ lineHeight: LINE_HEIGHT }}
        />
      </div>
    </div>
  );
}
