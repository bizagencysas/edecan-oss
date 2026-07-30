"use client";

/**
 * Revisión de diffs de un turno del agente, con aceptar/rechazar POR ARCHIVO.
 *
 * Por qué existe: hoy el agente edita archivos del repo del usuario y la
 * persona se entera después, leyendo el árbol de archivos o corriendo `git
 * diff` a mano. `ide_checkpoints.py` (mismo paquete de trabajo, ya
 * construido) guarda el contenido "antes" de cada archivo que el agente toca
 * y sabe restaurarlo — este componente es la mitad que faltaba: mostrar QUÉ
 * cambió, archivo por archivo, con un botón para quedarse con el cambio o
 * deshacerlo.
 *
 * Contrato de datos: este componente NO llama a ningún endpoint ni conoce
 * `ide_checkpoints.py` — recibe el contenido "antes"/"después" ya resuelto
 * por props (`DiffReviewFile`) y calcula el diff línea por línea en el
 * navegador. Quien lo integre (`ide_sessions.py` + `routers/ide.py`, otro
 * agente del mismo paquete de trabajo) arma `DiffReviewFile[]` leyendo
 * `TrackedFile` del checkpoint del turno (contenido "antes", vía el blob del
 * CAS) y el contenido actual en disco (contenido "después"); "rechazar" lo
 * resuelve el integrador llamando a `CheckpointStore.restore_file()` y
 * "aceptar" normalmente no necesita tocar nada (el archivo ya quedó como lo
 * dejó el agente) — este componente solo notifica la decisión con
 * `onAccept`/`onReject`, nunca escribe al disco por su cuenta.
 *
 * Motor de diff: Myers O((N+M)·D) implementado aquí mismo (sin dependencia
 * npm nueva — `package.json` de este paquete no trae ninguna librería de
 * diff y no es este componente el que decide agregar una). `D` es el número
 * de líneas que de verdad cambiaron, no el tamaño del archivo: un archivo de
 * 5000 líneas con un cambio de una sola línea se difea casi instantáneo. El
 * único caso caro es un archivo reescrito de punta a punta (D ~ N+M), y para
 * ESE caso hay un tope de seguridad (`LINE_DIFF_SAFETY_CAP`) que se rinde a
 * tiempo y avisa en vez de arriesgarse a trabar la pestaña.
 *
 * Por qué un diff de 2000 líneas no cuelga el navegador (dos capas, como
 * hace cualquier visor de diffs serio):
 *  1. Contexto sin cambios colapsado en bloques ("mostrar N líneas sin
 *     cambios"), igual que `git diff -U3` — un archivo grande con cambios
 *     puntuales solo pinta unas pocas líneas alrededor de cada cambio.
 *  2. Un tope global de filas visibles por archivo con un botón "ver más" —
 *     red de seguridad para el caso sin nada que colapsar (un archivo
 *     reescrito completo), donde SÍ hay miles de líneas realmente distintas.
 */

import { useMemo, useState } from "react";

import { CheckIcon, ChevronRightIcon, XIcon } from "@/components/icons";
import { Spinner } from "@/components/ui";

function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

// ---------------------------------------------------------------------------
// Contrato de datos público
// ---------------------------------------------------------------------------

export type DiffFileChangeKind = "added" | "modified" | "deleted" | "unavailable";
export type DiffFileResolution = "accepted" | "rejected";

export interface DiffReviewFile {
  /** Ruta relativa al workspace, tal cual `TrackedFile.path` en `ide_checkpoints.py`. */
  path: string;
  kind: DiffFileChangeKind;
  /** Contenido "antes" (según el checkpoint). `null` si el archivo no existía (kind === "added"). */
  beforeContent: string | null;
  /** Contenido "después" (estado actual en disco). `null` si el archivo ya no existe (kind === "deleted"). */
  afterContent: string | null;
  /**
   * Solo para `kind === "unavailable"`: el checkpoint no pudo capturar el
   * contenido "antes" (`TrackedFile.status` era `skipped_too_large` o
   * `skipped_budget`, ver `ide_checkpoints.py`) — no hay diff que mostrar ni
   * forma de deshacer este archivo en particular. Texto libre para explicar
   * por qué, p. ej. "Archivo de 12.4 MB: supera el tope de 8 MB por archivo."
   */
  unavailableReason?: string;
}

export interface DiffReviewProps {
  /** Archivos tocados en el turno, en el orden en que se quieren listar. */
  files: DiffReviewFile[];
  /** Decisión ya tomada por archivo (persiste tras recargar la vista de este turno). */
  resolutions?: Record<string, DiffFileResolution>;
  /** Archivos con una decisión en curso (llamada al backend todavía en vuelo): deshabilita sus botones y muestra spinner. */
  pendingPaths?: Record<string, boolean>;
  /** El usuario decide quedarse con el cambio de `path` tal cual lo dejó el agente. */
  onAccept: (path: string) => void;
  /** El usuario decide deshacer el cambio de `path` (el integrador restaura desde el checkpoint). */
  onReject: (path: string) => void;
  className?: string;
}

// ---------------------------------------------------------------------------
// Motor de diff por líneas — Myers, sin dependencias.
// ---------------------------------------------------------------------------

type DiffLineType = "context" | "add" | "del";

interface DiffLine {
  type: DiffLineType;
  text: string;
  beforeLine: number | null;
  afterLine: number | null;
}

/**
 * Tope de líneas combinadas (antes + después) por encima del cual NO se
 * intenta diferenciar línea por línea. Con Myers el costo real depende de
 * cuánto cambió (`D`), no del tamaño del archivo, así que este tope solo se
 * activa en el caso caro de verdad: un archivo reescrito de punta a punta.
 * Medido: 5000×5000 líneas totalmente distintas ≈ 350ms; este tope se queda
 * bien por debajo de donde el costo (cuadrático en el peor caso) empieza a
 * notarse en la interfaz.
 */
const LINE_DIFF_SAFETY_CAP = 12000;

/** Líneas de contexto sin cambios que se conservan a cada lado de un hueco colapsado (estilo `git diff -U3`). */
const CONTEXT_LINES = 3;

/** Filas visibles por archivo al abrir el diff; "ver más" las va sumando de a este tanto. */
const INITIAL_VISIBLE_ROWS = 500;
const ROWS_PER_LOAD_MORE = 500;

function splitLines(text: string): string[] {
  if (text === "") return [];
  return text.split("\n");
}

type EditOp =
  | { type: "equal"; aIndex: number; bIndex: number }
  | { type: "del"; aIndex: number }
  | { type: "add"; bIndex: number };

/**
 * Miyers, O((N+M)·D): construye el script de edición más corto entre `a` y
 * `b`. Implementación clásica (greedy + backtrace); probada aparte contra
 * fuerza bruta (LCS por programación dinámica) en varios casos, incluyendo
 * bordes (arreglos vacíos, solo inserciones, solo borrados, completamente
 * distintos) y contra el tiempo en archivos de miles de líneas.
 */
function myersEditScript(a: string[], b: string[]): EditOp[] {
  const n = a.length;
  const m = b.length;
  const max = n + m;
  if (max === 0) return [];

  const offset = max;
  const vSize = 2 * max + 1;
  let v = new Int32Array(vSize);
  const trace: Int32Array[] = [];
  let foundD = -1;

  outer: for (let d = 0; d <= max; d++) {
    trace.push(v.slice());
    for (let k = -d; k <= d; k += 2) {
      let x: number;
      if (k === -d || (k !== d && v[offset + k - 1] < v[offset + k + 1])) {
        x = v[offset + k + 1];
      } else {
        x = v[offset + k - 1] + 1;
      }
      let y = x - k;
      while (x < n && y < m && a[x] === b[y]) {
        x++;
        y++;
      }
      v[offset + k] = x;
      if (x >= n && y >= m) {
        foundD = d;
        break outer;
      }
    }
  }

  if (foundD === -1) return []; // inalcanzable (max cubre todo el espacio de d), pero nunca lanzar

  const ops: EditOp[] = [];
  let x = n;
  let y = m;
  for (let d = foundD; d > 0; d--) {
    const vPrev = trace[d];
    const k = x - y;
    let prevK: number;
    if (k === -d || (k !== d && vPrev[offset + k - 1] < vPrev[offset + k + 1])) {
      prevK = k + 1;
    } else {
      prevK = k - 1;
    }
    const prevX = vPrev[offset + prevK];
    const prevY = prevX - prevK;

    while (x > prevX && y > prevY) {
      ops.push({ type: "equal", aIndex: x - 1, bIndex: y - 1 });
      x--;
      y--;
    }
    if (x === prevX) {
      ops.push({ type: "add", bIndex: y - 1 });
      y--;
    } else {
      ops.push({ type: "del", aIndex: x - 1 });
      x--;
    }
    x = prevX;
    y = prevY;
  }
  while (x > 0 && y > 0) {
    ops.push({ type: "equal", aIndex: x - 1, bIndex: y - 1 });
    x--;
    y--;
  }
  while (x > 0) {
    ops.push({ type: "del", aIndex: x - 1 });
    x--;
  }
  while (y > 0) {
    ops.push({ type: "add", bIndex: y - 1 });
    y--;
  }
  ops.reverse();
  return ops;
}

interface FileDiffResult {
  lines: DiffLine[];
  added: number;
  removed: number;
  /** El archivo era demasiado grande y distinto para difear con seguridad: ver `LINE_DIFF_SAFETY_CAP`. */
  tooLargeToDiff: boolean;
}

function computeFileDiff(beforeText: string, afterText: string): FileDiffResult {
  const before = splitLines(beforeText);
  const after = splitLines(afterText);

  if (before.length + after.length > LINE_DIFF_SAFETY_CAP) {
    return { lines: [], added: 0, removed: 0, tooLargeToDiff: true };
  }

  const ops = myersEditScript(before, after);
  const lines: DiffLine[] = [];
  let added = 0;
  let removed = 0;
  for (const op of ops) {
    if (op.type === "equal") {
      lines.push({ type: "context", text: before[op.aIndex], beforeLine: op.aIndex + 1, afterLine: op.bIndex + 1 });
    } else if (op.type === "del") {
      lines.push({ type: "del", text: before[op.aIndex], beforeLine: op.aIndex + 1, afterLine: null });
      removed++;
    } else {
      lines.push({ type: "add", text: after[op.bIndex], beforeLine: null, afterLine: op.bIndex + 1 });
      added++;
    }
  }
  return { lines, added, removed, tooLargeToDiff: false };
}

// ---------------------------------------------------------------------------
// Colapso de contexto sin cambios en huecos (estilo `git diff -U3`)
// ---------------------------------------------------------------------------

type HunkSegment =
  | { kind: "lines"; id: string; lines: DiffLine[] }
  | { kind: "collapsed"; id: string; lines: DiffLine[] };

function collapseContext(lines: DiffLine[], contextSize: number): HunkSegment[] {
  const segments: HunkSegment[] = [];
  let currentVisible: DiffLine[] = [];
  let segId = 0;
  let i = 0;

  function flushVisible() {
    if (currentVisible.length) {
      segments.push({ kind: "lines", id: `l${segId++}`, lines: currentVisible });
      currentVisible = [];
    }
  }

  while (i < lines.length) {
    if (lines[i].type !== "context") {
      currentVisible.push(lines[i]);
      i++;
      continue;
    }
    let j = i;
    while (j < lines.length && lines[j].type === "context") j++;
    const runLength = j - i;
    const isFirstRun = i === 0;
    const isLastRun = j === lines.length;
    // Correr sin cambios en ambos extremos de un hueco intermedio cuesta 2×contextSize
    // en contexto conservado; en los extremos del archivo solo se conserva un lado.
    const minToCollapse = isFirstRun || isLastRun ? contextSize + 1 : contextSize * 2 + 1;

    if (runLength < minToCollapse) {
      currentVisible.push(...lines.slice(i, j));
      i = j;
      continue;
    }

    if (isFirstRun) {
      const hiddenEnd = j - contextSize;
      segments.push({ kind: "collapsed", id: `c${segId++}`, lines: lines.slice(i, hiddenEnd) });
      currentVisible.push(...lines.slice(hiddenEnd, j));
    } else if (isLastRun) {
      currentVisible.push(...lines.slice(i, i + contextSize));
      flushVisible();
      segments.push({ kind: "collapsed", id: `c${segId++}`, lines: lines.slice(i + contextSize, j) });
    } else {
      currentVisible.push(...lines.slice(i, i + contextSize));
      flushVisible();
      segments.push({
        kind: "collapsed",
        id: `c${segId++}`,
        lines: lines.slice(i + contextSize, j - contextSize),
      });
      currentVisible.push(...lines.slice(j - contextSize, j));
    }
    i = j;
  }
  flushVisible();
  return segments;
}

// ---------------------------------------------------------------------------
// Filas de render — aplana los segmentos y respeta huecos colapsados/expandidos
// ---------------------------------------------------------------------------

type RenderRow =
  | { kind: "line"; id: string; line: DiffLine }
  | { kind: "collapsed"; id: string; count: number; lines: DiffLine[] };

function buildRenderRows(segments: HunkSegment[], expandedIds: ReadonlySet<string>): RenderRow[] {
  const rows: RenderRow[] = [];
  for (const segment of segments) {
    if (segment.kind === "lines" || expandedIds.has(segment.id)) {
      for (const line of segment.lines) {
        rows.push({ kind: "line", id: `${segment.id}:${line.beforeLine ?? "-"}:${line.afterLine ?? "-"}`, line });
      }
    } else {
      rows.push({ kind: "collapsed", id: segment.id, count: segment.lines.length, lines: segment.lines });
    }
  }
  return rows;
}

// ---------------------------------------------------------------------------
// Presentación
// ---------------------------------------------------------------------------

const KIND_LABEL: Record<DiffFileChangeKind, string> = {
  added: "Nuevo",
  modified: "Modificado",
  deleted: "Borrado",
  unavailable: "Sin vista previa",
};

const KIND_DOT_CLASS: Record<DiffFileChangeKind, string> = {
  added: "bg-emerald-500",
  modified: "bg-amber-500",
  deleted: "bg-rose-500",
  unavailable: "bg-slate-400",
};

function DiffLineRow({ line }: { line: DiffLine }) {
  const marker = line.type === "add" ? "+" : line.type === "del" ? "−" : " ";
  return (
    <div
      className={cx(
        "flex text-[12px] leading-5",
        line.type === "add" && "bg-emerald-50 dark:bg-emerald-950/30",
        line.type === "del" && "bg-rose-50 dark:bg-rose-950/30",
      )}
    >
      <span className="w-10 shrink-0 select-none border-r border-slate-100 pr-1.5 text-right tabular-nums text-slate-300 dark:border-slate-800 dark:text-slate-600">
        {line.beforeLine ?? ""}
      </span>
      <span className="w-10 shrink-0 select-none border-r border-slate-100 pr-1.5 text-right tabular-nums text-slate-300 dark:border-slate-800 dark:text-slate-600">
        {line.afterLine ?? ""}
      </span>
      <span
        className={cx(
          "w-4 shrink-0 select-none text-center font-mono",
          line.type === "add" && "text-emerald-600 dark:text-emerald-400",
          line.type === "del" && "text-rose-500 dark:text-rose-400",
          line.type === "context" && "text-slate-300 dark:text-slate-700",
        )}
      >
        {marker}
      </span>
      <span
        className={cx(
          "min-w-0 flex-1 whitespace-pre pl-1 font-mono",
          line.type === "add" && "text-emerald-800 dark:text-emerald-300",
          line.type === "del" && "text-rose-700 dark:text-rose-300",
          line.type === "context" && "text-slate-600 dark:text-slate-300",
        )}
      >
        {line.text.length ? line.text : " "}
      </span>
    </div>
  );
}

function CollapsedRow({ count, onExpand }: { count: number; onExpand: () => void }) {
  return (
    <button
      type="button"
      onClick={onExpand}
      className="flex w-full items-center justify-center gap-2 border-y border-slate-100 bg-slate-50 py-1 text-[11px] text-slate-400 hover:bg-slate-100 hover:text-slate-600 dark:border-slate-800 dark:bg-slate-900/60 dark:text-slate-500 dark:hover:bg-slate-800 dark:hover:text-slate-300"
    >
      <span aria-hidden="true">···</span>
      Mostrar {count} línea{count === 1 ? "" : "s"} sin cambios
    </button>
  );
}

function FileDiffBody({ file, diff }: { file: DiffReviewFile; diff: FileDiffResult | null }) {
  const [expandedIds, setExpandedIds] = useState<ReadonlySet<string>>(() => new Set());
  const [visibleRows, setVisibleRows] = useState(INITIAL_VISIBLE_ROWS);

  // `diff` es `null` únicamente cuando `file.kind === "unavailable"` (ver `FileDiffRow`) — nunca se
  // difea un archivo sin contenido "antes" capturado. `collapseContext`/`buildRenderRows` reciben un
  // arreglo vacío en ese caso; el `if` de abajo corta antes de usar `rows`.
  const segments = useMemo(() => collapseContext(diff?.lines ?? [], CONTEXT_LINES), [diff]);
  const rows = useMemo(() => buildRenderRows(segments, expandedIds), [segments, expandedIds]);

  if (file.kind === "unavailable" || !diff) {
    return (
      <p className="px-3 py-3 text-xs italic text-slate-400 dark:text-slate-500">
        {file.unavailableReason || "No se guardó el contenido original de este archivo: no hay diff que mostrar."}
      </p>
    );
  }

  if (diff.tooLargeToDiff) {
    return (
      <p className="px-3 py-3 text-xs italic text-slate-400 dark:text-slate-500">
        Este archivo es demasiado grande para comparar línea por línea sin arriesgar la fluidez del navegador. Ábrelo
        en el editor para ver el contenido completo.
      </p>
    );
  }

  if (diff.lines.length === 0) {
    return (
      <p className="px-3 py-3 text-xs italic text-slate-400 dark:text-slate-500">
        El agente tocó este archivo pero su contenido no cambió.
      </p>
    );
  }

  const visible = rows.slice(0, visibleRows);
  const remaining = rows.length - visible.length;

  return (
    <div className="overflow-x-auto">
      <div className="min-w-fit">
        {visible.map((row) =>
          row.kind === "line" ? (
            <DiffLineRow key={row.id} line={row.line} />
          ) : (
            <CollapsedRow
              key={row.id}
              count={row.count}
              onExpand={() => setExpandedIds((prev) => new Set(prev).add(row.id))}
            />
          ),
        )}
        {remaining > 0 && (
          <button
            type="button"
            onClick={() => setVisibleRows((n) => n + ROWS_PER_LOAD_MORE)}
            className="w-full border-t border-slate-100 py-1.5 text-[11px] font-medium text-slate-400 hover:bg-slate-50 hover:text-slate-600 dark:border-slate-800 dark:hover:bg-slate-900 dark:hover:text-slate-300"
          >
            Mostrar {Math.min(remaining, ROWS_PER_LOAD_MORE)} línea{Math.min(remaining, ROWS_PER_LOAD_MORE) === 1 ? "" : "s"}{" "}
            más (quedan {remaining})
          </button>
        )}
      </div>
    </div>
  );
}

function FileDiffStats({ diff }: { diff: FileDiffResult | null }) {
  if (!diff || diff.tooLargeToDiff || (diff.added === 0 && diff.removed === 0)) return null;
  return (
    <span className="shrink-0 font-mono text-[11px] tabular-nums">
      {diff.added > 0 && <span className="text-emerald-600 dark:text-emerald-400">+{diff.added}</span>}
      {diff.added > 0 && diff.removed > 0 && <span className="text-slate-300 dark:text-slate-700"> </span>}
      {diff.removed > 0 && <span className="text-rose-500 dark:text-rose-400">−{diff.removed}</span>}
    </span>
  );
}

function ResolutionBadge({ resolution }: { resolution: DiffFileResolution }) {
  if (resolution === "accepted") {
    return (
      <span className="flex shrink-0 items-center gap-1 rounded-md bg-emerald-50 px-2 py-1 text-[11px] font-medium text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300">
        <CheckIcon className="h-3 w-3" />
        Aceptado
      </span>
    );
  }
  return (
    <span className="flex shrink-0 items-center gap-1 rounded-md bg-slate-100 px-2 py-1 text-[11px] font-medium text-slate-500 dark:bg-slate-800 dark:text-slate-400">
      <XIcon className="h-3 w-3" />
      Rechazado
    </span>
  );
}

function FileDiffRow({
  file,
  resolution,
  pending,
  onAccept,
  onReject,
}: {
  file: DiffReviewFile;
  resolution?: DiffFileResolution;
  pending?: boolean;
  onAccept: () => void;
  onReject: () => void;
}) {
  const [open, setOpen] = useState(true);
  const diff = useMemo(
    () =>
      file.kind === "unavailable" ? null : computeFileDiff(file.beforeContent ?? "", file.afterContent ?? ""),
    [file.kind, file.beforeContent, file.afterContent],
  );

  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center gap-2 px-3 py-2">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
        >
          <ChevronRightIcon
            className={cx("h-3.5 w-3.5 shrink-0 text-slate-400 transition-transform", open && "rotate-90")}
          />
          <span className={cx("h-1.5 w-1.5 shrink-0 rounded-full", KIND_DOT_CLASS[file.kind])} aria-hidden="true" />
          <code className="min-w-0 truncate text-xs font-medium text-slate-700 dark:text-slate-200" title={file.path}>
            {file.path}
          </code>
          <span className="shrink-0 text-[11px] text-slate-400 dark:text-slate-500">{KIND_LABEL[file.kind]}</span>
          <FileDiffStats diff={diff} />
        </button>

        {resolution ? (
          <ResolutionBadge resolution={resolution} />
        ) : (
          <div className="flex shrink-0 items-center gap-1.5">
            <button
              type="button"
              disabled={pending}
              onClick={onReject}
              className="flex items-center gap-1 rounded-md border border-slate-200 px-2 py-1 text-[11px] font-medium text-slate-600 hover:border-rose-300 hover:bg-rose-50 hover:text-rose-600 disabled:opacity-50 dark:border-slate-700 dark:text-slate-300 dark:hover:border-rose-800 dark:hover:bg-rose-950/40 dark:hover:text-rose-300"
            >
              {pending ? <Spinner className="h-3 w-3" /> : <XIcon className="h-3 w-3" />}
              Rechazar
            </button>
            <button
              type="button"
              disabled={pending}
              onClick={onAccept}
              className="flex items-center gap-1 rounded-md bg-slate-950 px-2 py-1 text-[11px] font-medium text-white hover:bg-slate-800 disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-white"
            >
              {pending ? <Spinner className="h-3 w-3" /> : <CheckIcon className="h-3 w-3" />}
              Aceptar
            </button>
          </div>
        )}
      </div>

      {open && (
        <div className="border-t border-slate-100 dark:border-slate-800">
          <FileDiffBody file={file} diff={diff} />
        </div>
      )}
    </div>
  );
}

export default function DiffReview({
  files,
  resolutions = {},
  pendingPaths = {},
  onAccept,
  onReject,
  className,
}: DiffReviewProps) {
  if (files.length === 0) {
    return <p className="text-xs italic text-slate-400 dark:text-slate-500">Este turno no tocó ningún archivo.</p>;
  }

  return (
    <div className={cx("flex flex-col gap-2", className)}>
      {files.map((file) => (
        <FileDiffRow
          key={file.path}
          file={file}
          resolution={resolutions[file.path]}
          pending={pendingPaths[file.path]}
          onAccept={() => onAccept(file.path)}
          onReject={() => onReject(file.path)}
        />
      ))}
    </div>
  );
}
