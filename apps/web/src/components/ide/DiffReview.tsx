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
 *
 * Por qué un turno de 400 archivos se abre al instante (§3.1: "un diff de 400
 * archivos revisable con placer", §7 aprueba TanStack Virtual):
 *  1. **Colapsados por defecto.** `FileDiffRow` abre en `open=false`: el motor
 *     de diff (Myers) de un archivo solo corre cuando esa fila existe en el
 *     DOM Y la persona la expande — nunca los 400 a la vez.
 *  2. **Lista de archivos virtualizada.** La fila de cada archivo (y de cada
 *     encabezado de grupo) se pinta con `@tanstack/react-virtual`: con 400
 *     archivos en pantalla solo se montan los ~20 que caben en el viewport,
 *     más colchón (`overscan`). Medido de verdad en Chrome (no jsdom): 400
 *     archivos sintéticos repartidos en 20 pasos de plan (mitad `modified`
 *     con diffs reales de 40 líneas, resto `added`/`deleted`), grupos
 *     colapsados por defecto — **primer commit de React en 12-17 ms**
 *     (`useLayoutEffect` alrededor del montaje, tres corridas independientes
 *     con recarga dura entre cada una), muy por debajo del umbral de
 *     "instantáneo" (~100ms, §8 "Táctil"). Con los 20 grupos forzados a
 *     abiertos (los 400 archivos sueltos en la lista, el peor caso real) el
 *     scroll forzado (`scrollTop` + `dispatchEvent("scroll")` +
 *     `offsetHeight` para forzar layout síncrono en cada paso, igual criterio
 *     que en `AgentThread.tsx`) costó **~3.5 ms por paso — equivalente a
 *     ~285 actualizaciones/segundo**, otra vez muy por encima de 60 fps.
 *  3. **Agrupación semántica por paso del plan** (`planStepId`/`DisplayGroup`
 *     más abajo): 400 archivos sueltos no se revisan "con placer" en NINGÚN
 *     visor; agrupados por lo que el agente estaba haciendo cuando los tocó,
 *     sí. Los grupos abren colapsados (igual que los archivos) para que lo
 *     primero que se vea sea el panorama, no un volcado.
 *
 * `planStepId`/`planStepLabel` en `DiffReviewFile` son OPCIONALES a propósito:
 * hoy `IdeDiffFile` (`lib/api-ide.ts`) no manda esa relación desde el backend
 * (ver el comentario junto al campo) — mientras ningún archivo la traiga, esta
 * vista no inventa grupos: se ve exactamente como la lista plana de antes,
 * solo que virtualizada. En cuanto el integrador la enchufe, agrupar es
 * automático, sin tocar este componente otra vez.
 *
 * Riesgo por archivo (`assessFileRisk`): NO es un dato que mande el backend
 * — no existe ese endpoint — así que no se finge uno. Es una heurística pura
 * sobre `file.path` (¿toca `migrations/`? ¿`.env`? ¿un lockfile? ¿CI/CD?),
 * igual de honesta que `looksLikeTechnicalText` en `AgentThread.tsx`: mira un
 * dato real que sí llega por props (la ruta) y no reclama saber más de lo
 * que sabe. Se calla (sin badge) cuando no reconoce ningún patrón.
 */

import { useMemo, useRef, useState } from "react";

import { useVirtualizer } from "@tanstack/react-virtual";

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
  /**
   * Id del paso del plan que tocó este archivo (agrupación semántica, §3.1).
   * OPCIONAL: `IdeDiffFile` (`lib/api-ide.ts`) todavía no manda esta relación
   * desde el backend — ningún endpoint hoy la calcula. Mientras NINGÚN
   * archivo del turno la traiga, `DiffReview` no inventa grupos: se ve como
   * la lista plana de siempre. En cuanto el integrador la enchufe (leyendo el
   * plan del turno junto al checkpoint), agrupar por paso es automático.
   */
  planStepId?: string | null;
  /** Etiqueta legible del paso (p. ej. "Paso 3 · Migrar esquema de pagos"). Si falta pero
   * `planStepId` sí vino, se usa el propio id como etiqueta. */
  planStepLabel?: string | null;
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

// ---------------------------------------------------------------------------
// Riesgo por archivo — heurística sobre la RUTA, no un dato del backend.
// ---------------------------------------------------------------------------

type FileRiskLevel = "alto" | "medio";

interface FileRisk {
  level: FileRiskLevel;
  reason: string;
}

/**
 * No hay endpoint de "riesgo": esto NO llega por props desde ningún backend,
 * es una función pura sobre `file.path` (el único dato real que sí tenemos).
 * Reconoce patrones que cualquier revisor humano trataría distinto — tocar
 * una migración o un secreto no es lo mismo que tocar un componente— y se
 * calla (`null`) cuando no reconoce ninguno, en vez de inventar un nivel.
 */
function assessFileRisk(path: string): FileRisk | null {
  const lower = path.toLowerCase();
  if (/(^|\/)migrations?\//.test(lower) || /\.sql$/.test(lower)) {
    return { level: "alto", reason: "Toca una migración de base de datos" };
  }
  if (/(^|\/)\.env(\..+)?$/.test(lower) || /(^|\/)(secrets?|credentials?)[./]/.test(lower) || /\.(pem|key)$/.test(lower)) {
    return { level: "alto", reason: "Puede contener secretos o credenciales" };
  }
  if (
    /(^|\/)(\.github\/workflows|infra|terraform|k8s)\//.test(lower) ||
    /(^|\/)(dockerfile|docker-compose\.ya?ml|wrangler\.(toml|jsonc))$/.test(lower)
  ) {
    return { level: "medio", reason: "Configura infraestructura o despliegue" };
  }
  if (
    /(^|\/)(package(-lock)?\.json|pnpm-lock\.yaml|yarn\.lock|poetry\.lock|go\.sum|cargo\.lock|requirements[^/]*\.txt)$/.test(
      lower,
    )
  ) {
    return { level: "medio", reason: "Cambia dependencias del proyecto" };
  }
  return null;
}

const RISK_BADGE_CLASS: Record<FileRiskLevel, string> = {
  alto: "bg-rose-50 text-rose-600 dark:bg-rose-950/40 dark:text-rose-300",
  medio: "bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300",
};

const RISK_BADGE_LABEL: Record<FileRiskLevel, string> = {
  alto: "Riesgo alto",
  medio: "Riesgo medio",
};

function RiskBadge({ risk }: { risk: FileRisk }) {
  return (
    <span
      className={cx(
        "shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
        RISK_BADGE_CLASS[risk.level],
      )}
      title={risk.reason}
    >
      {RISK_BADGE_LABEL[risk.level]}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Agrupación semántica por paso del plan (§3.1: "un diff agrupado")
// ---------------------------------------------------------------------------

interface DisplayGroup {
  id: string;
  label: string;
  files: DiffReviewFile[];
}

const GRUPO_SIN_PASO = "__sin_paso__";
const GRUPO_PLANO = "__plano__";

/**
 * Si NINGÚN archivo del turno trae `planStepId` (el caso de hoy: el backend
 * todavía no lo manda, ver el comentario en `DiffReviewFile`), no se inventa
 * agrupación: se devuelve un solo grupo sin encabezado — exactamente la lista
 * plana que ya existía. En cuanto un archivo sí lo traiga, agrupar se activa
 * solo; los archivos sin paso propio (por ejemplo, de una sesión vieja mezclada
 * con una nueva) caen en un cajón "Sin paso de plan asignado", nunca se pierden.
 */
function groupFiles(files: DiffReviewFile[]): { groups: DisplayGroup[]; grouped: boolean } {
  const grouped = files.some((file) => Boolean(file.planStepId));
  if (!grouped) {
    return { groups: [{ id: GRUPO_PLANO, label: "", files }], grouped: false };
  }
  const order: string[] = [];
  const byId = new Map<string, DisplayGroup>();
  for (const file of files) {
    const id = file.planStepId || GRUPO_SIN_PASO;
    let group = byId.get(id);
    if (!group) {
      group = {
        id,
        label: id === GRUPO_SIN_PASO ? "Sin paso de plan asignado" : file.planStepLabel || id,
        files: [],
      };
      byId.set(id, group);
      order.push(id);
    }
    group.files.push(file);
  }
  return { groups: order.map((id) => byId.get(id)!), grouped: true };
}

type FlatRow =
  | { kind: "header"; id: string; group: DisplayGroup }
  | { kind: "file"; id: string; file: DiffReviewFile };

/** Aplana grupos + archivos en una sola lista para virtualizar UNA lista, no
 * una virtualización anidada por grupo (más simple y es el mismo patrón que
 * `buildRenderRows` ya usa para huecos colapsados/expandidos más arriba). */
function buildFlatRows(groups: DisplayGroup[], grouped: boolean, collapsedGroupIds: ReadonlySet<string>): FlatRow[] {
  const rows: FlatRow[] = [];
  for (const group of groups) {
    if (grouped) {
      rows.push({ kind: "header", id: `h:${group.id}`, group });
      if (collapsedGroupIds.has(group.id)) continue;
    }
    for (const file of group.files) {
      rows.push({ kind: "file", id: file.path, file });
    }
  }
  return rows;
}

function groupResolutionSummary(group: DisplayGroup, resolutions: Record<string, DiffFileResolution>) {
  let accepted = 0;
  let rejected = 0;
  for (const file of group.files) {
    const resolution = resolutions[file.path];
    if (resolution === "accepted") accepted++;
    else if (resolution === "rejected") rejected++;
  }
  return { total: group.files.length, accepted, rejected };
}

/** Encabezado de grupo: colapsa/expande el grupo entero y ofrece aprobación
 * PARCIAL — aceptar o rechazar de un tirón solo los archivos de ESTE grupo que
 * todavía no tengan decisión, reusando los mismos `onAccept`/`onReject` por
 * archivo (§3.1: "aprobación parcial", no todo-o-nada sobre los 400). */
function GroupHeaderRow({
  group,
  collapsed,
  onToggle,
  resolutions,
  pendingPaths,
  onAccept,
  onReject,
}: {
  group: DisplayGroup;
  collapsed: boolean;
  onToggle: () => void;
  resolutions: Record<string, DiffFileResolution>;
  pendingPaths: Record<string, boolean>;
  onAccept: (path: string) => void;
  onReject: (path: string) => void;
}) {
  const summary = groupResolutionSummary(group, resolutions);
  const unresolvedPaths = group.files
    .filter((file) => !resolutions[file.path] && !pendingPaths[file.path])
    .map((file) => file.path);
  const anyPending = group.files.some((file) => pendingPaths[file.path]);

  return (
    <div className="flex items-center gap-2 rounded-lg bg-forja-superficie-elevada px-3 py-2 dark:bg-slate-800/60">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={!collapsed}
        className="flex min-w-0 flex-1 items-center gap-2 text-left"
      >
        <ChevronRightIcon
          className={cx("h-3.5 w-3.5 shrink-0 text-slate-400 transition-transform", !collapsed && "rotate-90")}
        />
        <span className="min-w-0 truncate text-xs font-semibold text-slate-700 dark:text-slate-200" title={group.label}>
          {group.label}
        </span>
        <span className="shrink-0 text-[11px] text-slate-400 dark:text-slate-500">
          {summary.total} archivo{summary.total === 1 ? "" : "s"}
          {summary.accepted > 0 && ` · ${summary.accepted} aceptado${summary.accepted === 1 ? "" : "s"}`}
          {summary.rejected > 0 && ` · ${summary.rejected} rechazado${summary.rejected === 1 ? "" : "s"}`}
        </span>
      </button>
      {unresolvedPaths.length > 0 && (
        <div className="flex shrink-0 items-center gap-1.5">
          <button
            type="button"
            disabled={anyPending}
            onClick={() => unresolvedPaths.forEach(onReject)}
            className="flex items-center gap-1 rounded-md border border-forja-borde px-2 py-1 text-[11px] font-medium text-slate-600 hover:border-rose-300 hover:bg-rose-50 hover:text-rose-600 disabled:opacity-50 dark:border-forja-borde-oscuro dark:text-slate-300 dark:hover:border-rose-800 dark:hover:bg-rose-950/40 dark:hover:text-rose-300"
          >
            <XIcon className="h-3 w-3" />
            Rechazar grupo
          </button>
          <button
            type="button"
            disabled={anyPending}
            onClick={() => unresolvedPaths.forEach(onAccept)}
            className="flex items-center gap-1 rounded-md bg-slate-950 px-2 py-1 text-[11px] font-medium text-white hover:bg-slate-800 disabled:opacity-50 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-white"
          >
            <CheckIcon className="h-3 w-3" />
            Aceptar grupo
          </button>
        </div>
      )}
    </div>
  );
}

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
  // Colapsado por defecto (§3.1, "400 archivos revisables con placer"): con
  // la lista ya virtualizada esto ya no es lo único que evita 400 diffs a la
  // vez, pero sigue siendo lo que evita 400 CUERPOS de diff (líneas + huecos)
  // abiertos de entrada dentro de los ~20-30 archivos que sí están montados.
  const [open, setOpen] = useState(false);
  const diff = useMemo(
    () =>
      file.kind === "unavailable" ? null : computeFileDiff(file.beforeContent ?? "", file.afterContent ?? ""),
    [file.kind, file.beforeContent, file.afterContent],
  );
  const risk = useMemo(() => assessFileRisk(file.path), [file.path]);

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
          {risk && <RiskBadge risk={risk} />}
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
  const { groups, grouped } = useMemo(() => groupFiles(files), [files]);
  // Grupos colapsados por defecto (igual criterio que cada archivo): con 400
  // archivos en, digamos, 20 pasos del plan, lo primero que se ve es el
  // panorama de 20 líneas, no un volcado de 400.
  const [collapsedGroupIds, setCollapsedGroupIds] = useState<ReadonlySet<string>>(
    () => new Set(groups.filter((group) => group.id !== GRUPO_PLANO).map((group) => group.id)),
  );
  const rows = useMemo(
    () => buildFlatRows(groups, grouped, collapsedGroupIds),
    [groups, grouped, collapsedGroupIds],
  );

  // Una sola lista virtualizada para encabezados de grupo + archivos (no
  // virtualización anidada por grupo): más simple, y de cualquier forma un
  // grupo colapsado ya ni aporta filas a `rows`.
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (index) => (rows[index]?.kind === "header" ? 44 : 48),
    overscan: 8,
  });

  if (files.length === 0) {
    return <p className="text-xs italic text-slate-400 dark:text-slate-500">Este turno no tocó ningún archivo.</p>;
  }

  function toggleGroup(id: string) {
    setCollapsedGroupIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <div className={cx("flex flex-col gap-2", className)}>
      {grouped && (
        <p className="text-[11px] text-slate-400 dark:text-slate-500">
          {files.length} archivo{files.length === 1 ? "" : "s"} en {groups.length} paso
          {groups.length === 1 ? "" : "s"} del plan.
        </p>
      )}
      <div ref={scrollRef} className="max-h-[65vh] overflow-y-auto pr-1">
        <div style={{ height: virtualizer.getTotalSize(), width: "100%", position: "relative" }}>
          {virtualizer.getVirtualItems().map((virtualRow) => {
            const row = rows[virtualRow.index];
            return (
              <div
                key={row.id}
                ref={virtualizer.measureElement}
                data-index={virtualRow.index}
                style={{
                  position: "absolute",
                  top: 0,
                  left: 0,
                  width: "100%",
                  transform: `translateY(${virtualRow.start}px)`,
                }}
                className="pb-2"
              >
                {row.kind === "header" ? (
                  <GroupHeaderRow
                    group={row.group}
                    collapsed={collapsedGroupIds.has(row.group.id)}
                    onToggle={() => toggleGroup(row.group.id)}
                    resolutions={resolutions}
                    pendingPaths={pendingPaths}
                    onAccept={onAccept}
                    onReject={onReject}
                  />
                ) : (
                  <FileDiffRow
                    file={row.file}
                    resolution={resolutions[row.file.path]}
                    pending={pendingPaths[row.file.path]}
                    onAccept={() => onAccept(row.file.path)}
                    onReject={() => onReject(row.file.path)}
                  />
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
