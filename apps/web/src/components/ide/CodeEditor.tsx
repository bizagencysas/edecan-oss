"use client";

/**
 * Editor central del IDE embebido: textarea monoespaciada con números de
 * línea en una columna aparte, sincronizada por scroll (`ROADMAP_V2.md`
 * §7.8/§7.10 — "editor IDE = textarea monoespaciada"). Deliberadamente SIN
 * resaltado de sintaxis: cero dependencias npm nuevas y fuera del alcance
 * de este paquete de trabajo (queda como mejora futura, ver `docs/ide.md`).
 *
 * Encima de eso vive la EDICIÓN INLINE (`ide_edicion_inline.py`, pieza 2 del
 * plan del editor): seleccionas código, pulsas ⌘K/Ctrl+K, dices qué cambiar y
 * revisas el diff sin pasar por el chat.
 *
 * Tres decisiones de esta mitad, que es la que ve la persona:
 *
 * 1. **No llama a ningún endpoint.** Igual que `DiffReview.tsx`, recibe el
 *    trabajo por prop (`onInlineEdit`) y se queda con la experiencia. Quien
 *    integre decide por dónde viaja; este componente no conoce la API.
 *
 * 2. **Aplicar es local, no un guardado.** El cambio entra a la textarea
 *    (`onChange`) y el archivo queda sucio, como si lo hubieras escrito tú:
 *    puedes seguir editándolo, deshacerlo o descartarlo antes de Guardar.
 *    Escribir a disco a espaldas de la persona sería lo contrario de revisar.
 *
 * 3. **Con cambios sin guardar, no se ofrece.** El backend lee el archivo de
 *    DISCO para armar el rango; con el buffer sucio, las líneas que ve él y
 *    las que ves tú no son las mismas. En vez de mandar el contenido del
 *    buffer y tener dos fuentes de verdad, se pide guardar primero — y se
 *    dice por qué, no se deshabilita en silencio.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { CheckIcon, SparklesIcon, XIcon } from "@/components/icons";
import { Button, Spinner } from "@/components/ui";

/**
 * Alto de línea en píxeles (1.375rem con la raíz por defecto). Hace falta el
 * número, no solo la cadena CSS, para poder anclar el panel de edición inline
 * a la línea correcta.
 */
const LINE_HEIGHT_PX = 22;
const LINE_HEIGHT = `${LINE_HEIGHT_PX}px`;
/** `py-2` de la textarea y de la columna de números: ambas empiezan igual. */
const EDITOR_PADDING_TOP_PX = 8;
/** Alto aproximado del panel, solo para no anclarlo pegado al borde inferior. */
const PANEL_SAFE_HEIGHT_PX = 180;

// ---------------------------------------------------------------------------
// Contrato de datos público (lo llena el integrador con `ide_edicion_inline`)
// ---------------------------------------------------------------------------

export interface InlineEditRequest {
  path: string;
  /** Primera línea seleccionada, 1-based e incluida. */
  startLine: number;
  /** Última línea seleccionada, 1-based e incluida. */
  endLine: number;
  instruction: string;
}

export interface InlineEditProposal {
  /** Diff unificado tal cual lo devuelve `PropuestaEdicion.diff`. */
  diff: string;
  /** Contenido completo del archivo ya con el cambio (`contenido_nuevo`). */
  newContent: string;
  /** Solo el reemplazo del rango (`texto_rango_nuevo`), para reseleccionarlo. */
  newRangeText: string;
  startLine: number;
  endLine: number;
  addedLines: number;
  removedLines: number;
}

export interface CodeEditorProps {
  path: string | null;
  content: string;
  dirty: boolean;
  loading: boolean;
  saving: boolean;
  error: string | null;
  onChange: (text: string) => void;
  onSave: () => void;
  /**
   * Pide el cambio acotado al rango. Sin esta prop la edición inline no
   * existe (ni atajo ni pista), así que el editor sigue funcionando igual
   * donde todavía no se haya cableado.
   */
  onInlineEdit?: (request: InlineEditRequest) => Promise<InlineEditProposal>;
}

// ---------------------------------------------------------------------------
// Selección → rango de líneas
// ---------------------------------------------------------------------------

/**
 * Con `indexOf` y no con `slice(0, offset).split("\n")`: esto corre en cada
 * pulsación mientras escribes, y la versión con `split` copia el archivo
 * entero y construye un arreglo con todas sus líneas cada vez.
 */
function lineAt(text: string, offset: number): number {
  let line = 1;
  let index = text.indexOf("\n");
  while (index !== -1 && index < offset) {
    line += 1;
    index = text.indexOf("\n", index + 1);
  }
  return line;
}

/**
 * Rango 1-based con ambos extremos incluidos, contando las líneas igual que
 * la columna de números (`split("\n")`) y que `Rango` en el backend.
 *
 * Si la selección termina justo al principio de una línea, esa línea NO se
 * incluye: seleccionar de arriba abajo con el ratón siempre se pasa una línea,
 * y cobrarla haría que el modelo reescribiera código que nadie marcó.
 */
function selectionRange(text: string, start: number, end: number): { start: number; end: number } {
  const startLine = lineAt(text, start);
  let endLine = lineAt(text, end);
  if (end > start && text[end - 1] === "\n") endLine -= 1;
  return { start: startLine, end: Math.max(startLine, endLine) };
}

/** Offset del primer carácter de `line` (1-based) dentro de `text`. */
function offsetOfLine(text: string, line: number): number {
  let offset = 0;
  for (let i = 1; i < line; i += 1) {
    const next = text.indexOf("\n", offset);
    if (next === -1) return text.length;
    offset = next + 1;
  }
  return offset;
}

// ---------------------------------------------------------------------------
// Vista del diff propuesto
// ---------------------------------------------------------------------------

function DiffPreview({ diff }: { diff: string }) {
  const rows = useMemo(
    () =>
      diff
        .split("\n")
        .filter((line) => !line.startsWith("--- ") && !line.startsWith("+++ "))
        .filter((line, index, all) => line !== "" || index < all.length - 1),
    [diff],
  );

  return (
    <div className="max-h-56 overflow-auto border-y border-slate-100 bg-slate-50/60 dark:border-slate-800 dark:bg-slate-950/40">
      {rows.map((line, index) => {
        const kind = line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : line.startsWith("@@") ? "hunk" : "ctx";
        return (
          <div
            key={`${index}-${line.slice(0, 24)}`}
            className={[
              "whitespace-pre px-3 font-mono text-[11.5px] leading-5",
              kind === "add" && "bg-emerald-50 text-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300",
              kind === "del" && "bg-rose-50 text-rose-700 dark:bg-rose-950/40 dark:text-rose-300",
              kind === "hunk" && "text-slate-400 dark:text-slate-500",
              kind === "ctx" && "text-slate-500 dark:text-slate-400",
            ]
              .filter(Boolean)
              .join(" ")}
          >
            {line.length ? line : " "}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Editor
// ---------------------------------------------------------------------------

type PanelPhase = "prompt" | "loading" | "proposal";

export function CodeEditor({
  path,
  content,
  dirty,
  loading,
  saving,
  error,
  onChange,
  onSave,
  onInlineEdit,
}: CodeEditorProps) {
  const gutterRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const codeAreaRef = useRef<HTMLDivElement | null>(null);
  const instructionRef = useRef<HTMLInputElement | null>(null);
  /** Contenido del archivo cuando se abrió el panel: si cambia, todo caduca. */
  const panelContentRef = useRef<string>("");
  /**
   * Número de petición en curso. Una respuesta que llega después de cerrar el
   * panel (o después de pedir otra cosa) NO debe reabrirlo con un diff viejo:
   * se compara contra este contador y se descarta.
   */
  const requestSeqRef = useRef(0);

  const [selection, setSelection] = useState<{ start: number; end: number } | null>(null);
  const [phase, setPhase] = useState<PanelPhase | null>(null);
  const [instruction, setInstruction] = useState("");
  /**
   * La instrucción que produjo la propuesta que estás viendo. Que el texto de
   * la caja se separe de esta es la señal de "no era eso, prueba así": ver
   * `canRegenerate`.
   */
  const [submittedInstruction, setSubmittedInstruction] = useState("");
  const [proposal, setProposal] = useState<InlineEditProposal | null>(null);
  const [inlineError, setInlineError] = useState<string | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [shortcutLabel, setShortcutLabel] = useState("Ctrl+K");

  const lineCount = useMemo(() => (content.length === 0 ? 1 : content.split("\n").length), [content]);
  const inlineEnabled = Boolean(onInlineEdit) && Boolean(path) && !loading;

  useEffect(() => {
    // En useEffect y no al renderizar: el servidor no sabe qué teclado tienes,
    // y decidirlo en el render rompería la hidratación.
    if (typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.userAgent)) {
      setShortcutLabel("⌘K");
    }
  }, []);

  const closePanel = useCallback(() => {
    requestSeqRef.current += 1;
    setPhase(null);
    setProposal(null);
    setInlineError(null);
    setInstruction("");
    setSubmittedInstruction("");
  }, []);

  // Cambiar de archivo con un panel abierto dejaría una propuesta apuntando a
  // líneas de otro archivo: se cierra siempre.
  useEffect(() => {
    closePanel();
    setSelection(null);
  }, [path, closePanel]);

  // El archivo cambió por debajo del panel (escribiste en la textarea, o se
  // acaba de aplicar un cambio): el rango ya no señala lo mismo y la propuesta
  // se calculó sobre otro texto. Se cierra en vez de arriesgar un empalme en
  // el lugar equivocado. Comparar contra un ref y no contra una variable
  // capturada es lo que hace que esto se entere de verdad: el `content` que ve
  // un `await` es el de cuando arrancó, no el de ahora.
  useEffect(() => {
    if (phase === null || content === panelContentRef.current) return;
    closePanel();
  }, [content, phase, closePanel]);

  function syncScroll() {
    const textarea = textareaRef.current;
    if (!textarea) return;
    if (gutterRef.current) gutterRef.current.scrollTop = textarea.scrollTop;
    // Solo cuando hay panel abierto: si no, cada scroll re-renderizaría el
    // editor entero sin que nadie lo note.
    if (phase !== null) setScrollTop(textarea.scrollTop);
  }

  function refreshSelection() {
    const textarea = textareaRef.current;
    if (!inlineEnabled || !textarea) return;
    const next = selectionRange(content, textarea.selectionStart, textarea.selectionEnd);
    // Conservar el objeto anterior cuando el rango no cambió evita re-renderizar
    // el editor en cada tecla escrita dentro de la misma línea.
    setSelection((prev) => (prev && prev.start === next.start && prev.end === next.end ? prev : next));
  }

  const openPanel = useCallback(() => {
    if (!inlineEnabled || dirty) return;
    const textarea = textareaRef.current;
    if (!textarea) return;
    setSelection(selectionRange(content, textarea.selectionStart, textarea.selectionEnd));
    setScrollTop(textarea.scrollTop);
    panelContentRef.current = content;
    setProposal(null);
    setInlineError(null);
    setPhase("prompt");
    window.requestAnimationFrame(() => instructionRef.current?.focus());
  }, [content, dirty, inlineEnabled]);

  async function submitInstruction() {
    if (!onInlineEdit || !path || !selection) return;
    const texto = instruction.trim();
    if (!texto) return;
    requestSeqRef.current += 1;
    const seq = requestSeqRef.current;
    setPhase("loading");
    setInlineError(null);
    setSubmittedInstruction(texto);
    try {
      const result = await onInlineEdit({
        path,
        startLine: selection.start,
        endLine: selection.end,
        instruction: texto,
      });
      if (requestSeqRef.current !== seq) return;
      setProposal(result);
      setPhase("proposal");
    } catch (err) {
      if (requestSeqRef.current !== seq) return;
      setInlineError(err instanceof Error ? err.message : "No se pudo preparar el cambio.");
      setPhase("prompt");
    }
  }

  function applyProposal() {
    if (!proposal) return;
    onChange(proposal.newContent);
    const start = offsetOfLine(proposal.newContent, proposal.startLine);
    closePanel();
    // Tras aplicar, la selección queda sobre el texto nuevo: ves qué entró sin
    // tener que buscarlo.
    window.requestAnimationFrame(() => {
      const textarea = textareaRef.current;
      if (!textarea) return;
      textarea.focus();
      textarea.setSelectionRange(start, start + proposal.newRangeText.length);
    });
  }

  function handleEditorKeyDown(event: React.KeyboardEvent) {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      // Con el panel abierto el atajo no hace nada: reabrirlo borraría lo que
      // ya escribiste o la propuesta que estás revisando.
      if (phase === null) openPanel();
      return;
    }
    if (event.key === "Escape" && phase !== null) {
      event.preventDefault();
      closePanel();
      textareaRef.current?.focus();
    }
  }

  if (!path) {
    return (
      <div className="flex flex-1 items-center justify-center rounded-2xl border border-dashed border-slate-300 text-sm text-slate-400 dark:border-slate-700">
        Elige un archivo del árbol para editarlo.
      </div>
    );
  }

  const selectedLines = selection ? selection.end - selection.start + 1 : 0;
  const rangeLabel = selection
    ? selection.start === selection.end
      ? `Línea ${selection.start}`
      : `Líneas ${selection.start}-${selection.end}`
    : "";

  /**
   * Con una propuesta en pantalla, reescribir la instrucción es la forma
   * natural de decir "no era eso, prueba así". Sin esto ⏎ aplicaba el diff
   * viejo aunque hubieras cambiado el texto, y no había manera de reintentar
   * sin descartar y volver a escribirlo todo: el bucle más frecuente de la
   * edición inline era justo el que no se podía hacer.
   */
  const canRegenerate =
    phase === "proposal" &&
    instruction.trim().length > 0 &&
    instruction.trim() !== submittedInstruction;

  const anchorLine = proposal ? proposal.endLine : (selection?.end ?? 1);
  const viewportHeight = codeAreaRef.current?.clientHeight ?? 0;
  const rawTop = EDITOR_PADDING_TOP_PX + anchorLine * LINE_HEIGHT_PX - scrollTop;
  const panelTop = Math.max(
    4,
    viewportHeight > 0 ? Math.min(rawTop, Math.max(4, viewportHeight - PANEL_SAFE_HEIGHT_PX)) : rawTop,
  );

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
        <div className="flex shrink-0 items-center gap-2">
          {inlineEnabled && selection && phase === null && (
            <button
              type="button"
              onClick={openPanel}
              disabled={dirty}
              title={
                dirty
                  ? "Guarda el archivo primero: la edición inline trabaja sobre el archivo guardado."
                  : `Editar la selección con IA (${shortcutLabel})`
              }
              className="flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] font-medium text-slate-500 transition hover:bg-slate-100 hover:text-slate-800 disabled:cursor-not-allowed disabled:opacity-40 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-100"
            >
              <SparklesIcon className="h-3.5 w-3.5" />
              {selectedLines > 1 ? `Editar ${selectedLines} líneas` : "Editar línea"}
              <kbd className="rounded border border-slate-200 px-1 font-mono text-[10px] text-slate-400 dark:border-slate-700 dark:text-slate-500">
                {shortcutLabel}
              </kbd>
            </button>
          )}
          <Button size="sm" onClick={onSave} loading={saving} disabled={!dirty || loading}>
            Guardar
          </Button>
        </div>
      </div>

      {error && (
        <div className="border-b border-rose-100 bg-rose-50 px-4 py-2 text-xs text-rose-700 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-300">
          {error}
        </div>
      )}

      <div
        ref={codeAreaRef}
        className="relative flex flex-1 overflow-hidden font-mono text-sm"
        onKeyDown={handleEditorKeyDown}
      >
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
          onScroll={syncScroll}
          onSelect={refreshSelection}
          onKeyUp={refreshSelection}
          onMouseUp={refreshSelection}
          spellCheck={false}
          aria-label={`Editor de ${path}`}
          className="flex-1 resize-none overflow-auto bg-transparent px-3 py-2 text-slate-800 outline-none disabled:opacity-60 dark:text-slate-100"
          style={{ lineHeight: LINE_HEIGHT }}
        />

        {phase !== null && selection && (
          <div
            role="dialog"
            aria-label="Editar la selección con IA"
            style={{ top: panelTop }}
            className="absolute left-3 right-3 z-10 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-xl shadow-slate-900/10 dark:border-slate-700 dark:bg-slate-900 dark:shadow-black/40"
          >
            <div className="flex items-center gap-2 px-3 pt-2.5 text-[11px] text-slate-400 dark:text-slate-500">
              <SparklesIcon className="h-3 w-3" />
              <span className="font-sans">{rangeLabel}</span>
              <span className="font-sans">·</span>
              <span className="font-sans">Solo se cambia lo que seleccionaste</span>
              <button
                type="button"
                onClick={closePanel}
                aria-label="Cerrar"
                className="ml-auto rounded p-0.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-slate-800 dark:hover:text-slate-200"
              >
                <XIcon className="h-3.5 w-3.5" />
              </button>
            </div>

            <div className="flex items-center gap-2 px-3 py-2">
              <input
                ref={instructionRef}
                value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    if (phase === "proposal" && !canRegenerate) applyProposal();
                    else void submitInstruction();
                  }
                }}
                disabled={phase === "loading"}
                placeholder="Di qué cambiar en la selección…"
                aria-label="Instrucción para la edición inline"
                className="min-w-0 flex-1 bg-transparent font-sans text-sm text-slate-800 outline-none placeholder:text-slate-400 disabled:opacity-60 dark:text-slate-100 dark:placeholder:text-slate-600"
              />
              {phase === "loading" ? (
                <span className="flex shrink-0 items-center gap-1.5 font-sans text-[11px] text-slate-400">
                  <Spinner className="h-3.5 w-3.5" />
                  Pensando…
                </span>
              ) : (
                (phase === "prompt" || canRegenerate) && (
                  <button
                    type="button"
                    onClick={() => void submitInstruction()}
                    disabled={!instruction.trim()}
                    className="shrink-0 rounded-md bg-slate-950 px-2.5 py-1 font-sans text-[11px] font-medium text-white transition hover:bg-slate-800 disabled:opacity-40 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-white"
                  >
                    Generar ⏎
                  </button>
                )
              )}
            </div>

            {inlineError && (
              <p className="border-t border-rose-100 bg-rose-50 px-3 py-2 font-sans text-[11px] text-rose-700 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-300">
                {inlineError}
              </p>
            )}

            {phase === "proposal" && proposal && (
              <>
                {proposal.addedLines === 0 && proposal.removedLines === 0 ? (
                  <p className="border-t border-slate-100 px-3 py-2.5 font-sans text-[11px] italic text-slate-400 dark:border-slate-800 dark:text-slate-500">
                    El modelo no propuso ningún cambio en la selección.
                  </p>
                ) : (
                  <DiffPreview diff={proposal.diff} />
                )}
                <div className="flex items-center gap-2 px-3 py-2">
                  <span className="font-mono text-[11px] tabular-nums">
                    <span className="text-emerald-600 dark:text-emerald-400">+{proposal.addedLines}</span>{" "}
                    <span className="text-rose-500 dark:text-rose-400">−{proposal.removedLines}</span>
                  </span>
                  <span className="font-sans text-[11px] text-slate-400 dark:text-slate-500">
                    Se aplica en el editor; guarda tú cuando quieras.
                  </span>
                  <div className="ml-auto flex items-center gap-1.5">
                    <button
                      type="button"
                      onClick={closePanel}
                      className="flex items-center gap-1 rounded-md border border-slate-200 px-2 py-1 font-sans text-[11px] font-medium text-slate-600 transition hover:border-rose-300 hover:bg-rose-50 hover:text-rose-600 dark:border-slate-700 dark:text-slate-300 dark:hover:border-rose-800 dark:hover:bg-rose-950/40 dark:hover:text-rose-300"
                    >
                      <XIcon className="h-3 w-3" />
                      Descartar
                    </button>
                    <button
                      type="button"
                      onClick={applyProposal}
                      disabled={proposal.addedLines === 0 && proposal.removedLines === 0}
                      className="flex items-center gap-1 rounded-md bg-slate-950 px-2 py-1 font-sans text-[11px] font-medium text-white transition hover:bg-slate-800 disabled:opacity-40 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-white"
                    >
                      <CheckIcon className="h-3 w-3" />
                      {/* Sin el "⏎" cuando ⏎ ya no aplica sino que vuelve a generar. */}
                      {canRegenerate ? "Aplicar" : "Aplicar ⏎"}
                    </button>
                  </div>
                </div>
              </>
            )}
          </div>
        )}
      </div>

      {inlineEnabled && dirty && (
        <p className="border-t border-slate-100 px-4 py-1.5 text-[11px] text-slate-400 dark:border-slate-800 dark:text-slate-500">
          Guarda el archivo para editar con IA: el cambio se calcula sobre el archivo guardado.
        </p>
      )}
    </div>
  );
}
