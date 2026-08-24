"use client";

/**
 * Ventana "Memoria y conocimiento" de Forge Studio (CABO 3 del cierre de
 * IDE): la pantalla para VER y borrar lo que Edecán ya recordó, hoy solo
 * visible como texto plano si alguien corre `/memory` en el compositor.
 *
 * Dos almacenes distintos, con una diferencia que importa mostrar como tal
 * (ver los docstrings de `ide_memoria.py` e `ide_conocimiento.py` en el
 * companion, que son la fuente de verdad de esta distinción):
 *
 *   MEMORIA DEL PROYECTO -- una convención, ubicación, error a evitar o
 *   decisión propia de ESTE repo. No caduca sola: sigue siendo cierta
 *   mientras nadie la borre. Viene de `GET .../memory` (real, ya wireado).
 *
 *   CONOCIMIENTO VERIFICADO -- un hecho del MUNDO exterior al repo
 *   (versión vigente de algo, práctica recomendada, deprecación, límite o
 *   cuota), con su URL de fuente y su fecha de verificación, compartido
 *   entre TODOS los proyectos (no solo este). Caduca según su tipo -- un
 *   hecho vencido se muestra igual, marcado como tal, nunca escondido (ver
 *   `ConocimientoStore._con_frescura`).
 *
 * Estado real de esta pieza al momento de escribir este archivo (verificado
 * leyendo el código, no asumido): la memoria SÍ tiene ruta HTTP end-to-end
 * (`routers/ide.py::get_workspace_memory`/`delete_workspace_memory_note` ->
 * `ide_runtime.py` -> `ide_memoria.MemoriaStore`), así que esa pestaña
 * funciona de verdad. El conocimiento verificado NO: `ide_conocimiento.py`
 * (el store, con `listar()`/`olvidar()` ya escritos y probados) existe pero
 * `ide_runtime.py` no importa nada de ese módulo y `routers/ide.py` no tiene
 * ningún endpoint de conocimiento -- de ahí que ese propio docstring diga
 * "en desarrollo en paralelo, no importa nada de routers/ide.py". Este
 * archivo NO puede cerrar esa brecha (`routers/ide.py`/`ide_runtime.py` no
 * son archivos de este paquete de trabajo, y otro agente puede estar
 * tocándolos en paralelo) -- lo que sí hace es dejar la pestaña lista
 * (`lib/api-ide.ts::getIdeKnowledgeFacts`/`deleteIdeKnowledgeFact`, mismo
 * contrato REST que memoria) y, mientras el backend no responda, mostrarlo
 * HONESTAMENTE como pendiente (ver `NotWiredNotice` más abajo) en vez de
 * simular datos o esconder el error.
 *
 * Autocontenido a propósito (mismo criterio que `SearchPanel.tsx`): llama a
 * `lib/api-ide.ts` directo, sin pasar por callbacks de `page.tsx` -- quien
 * lo monta solo decide CUÁNDO mostrarlo (`workspaceId` + `onClose`).
 */

import { useEffect, useState, type SVGProps } from "react";

import { TrashIcon, XIcon } from "@/components/icons";
import { Alert, Badge, Spinner } from "@/components/ui";
import {
  ApiError,
  deleteIdeKnowledgeFact,
  deleteIdeMemoryNote,
  getIdeKnowledgeFacts,
  getIdeMemoryNotes,
  type IdeKnowledgeFact,
  type IdeKnowledgeFactKind,
  type IdeMemoryNote,
} from "@/lib/api-ide";

function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

function BrainIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>
      <path d="M9 4a3 3 0 0 0-3 3v.3A3 3 0 0 0 4.5 10 3 3 0 0 0 6 15.5V16a3 3 0 0 0 3 3" />
      <path d="M15 4a3 3 0 0 1 3 3v.3A3 3 0 0 1 19.5 10 3 3 0 0 1 18 15.5V16a3 3 0 0 1-3 3" />
      <path d="M9 4v15M15 4v15" />
    </svg>
  );
}

/** No hay `GlobeIcon` en `components/icons.tsx` -- se define acá mismo, mismo lenguaje visual que el resto (trazo 1.75, `currentColor`). */
function GlobeIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" />
    </svg>
  );
}

function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString("es", { day: "2-digit", month: "short", year: "numeric" });
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

// ---------------------------------------------------------------------------
// Memoria del proyecto
// ---------------------------------------------------------------------------

const MEMORY_KIND_LABEL: Record<IdeMemoryNote["kind"], string> = {
  convencion: "Convención",
  ubicacion: "Ubicación",
  error_evitar: "Error a evitar",
  decision: "Decisión",
};

const MEMORY_KIND_BADGE: Record<IdeMemoryNote["kind"], "brand" | "neutral" | "danger" | "success"> = {
  convencion: "brand",
  ubicacion: "neutral",
  error_evitar: "danger",
  decision: "success",
};

function MemoryNoteRow({
  note,
  pending,
  confirming,
  onStartDelete,
  onCancelDelete,
  onConfirmDelete,
}: {
  note: IdeMemoryNote;
  pending: boolean;
  confirming: boolean;
  onStartDelete: () => void;
  onCancelDelete: () => void;
  onConfirmDelete: () => void;
}) {
  return (
    <li className="rounded-lg border border-slate-200 p-3 dark:border-slate-800">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <Badge variant={MEMORY_KIND_BADGE[note.kind]}>{MEMORY_KIND_LABEL[note.kind] ?? note.kind}</Badge>
          <p className="mt-1.5 text-sm text-slate-700 dark:text-slate-200">{note.content}</p>
          <p className="mt-1.5 text-[11px] text-slate-400">
            Usado {note.use_count} {note.use_count === 1 ? "vez" : "veces"} · creado {formatDate(note.created_at)}
            {note.last_used_at ? ` · último uso ${formatDate(note.last_used_at)}` : ""}
          </p>
        </div>
        {!confirming && (
          <button
            type="button"
            aria-label="Borrar este recuerdo"
            disabled={pending}
            onClick={onStartDelete}
            className="shrink-0 rounded-md p-1.5 text-slate-400 hover:bg-rose-100 hover:text-rose-600 disabled:opacity-50 dark:hover:bg-rose-950/40 dark:hover:text-rose-400"
          >
            {pending ? <Spinner className="h-3.5 w-3.5" /> : <TrashIcon className="h-3.5 w-3.5" />}
          </button>
        )}
      </div>
      {confirming && (
        <div className="mt-2 flex items-center gap-2 rounded-md bg-rose-50 px-2.5 py-1.5 text-xs text-rose-700 dark:bg-rose-950/40 dark:text-rose-300">
          <span className="flex-1">¿Borrar este recuerdo? El agente puede volver a aprenderlo si aplica otra vez.</span>
          <button type="button" onClick={onConfirmDelete} className="rounded bg-rose-600 px-2 py-1 font-medium text-white hover:bg-rose-700">
            Borrar
          </button>
          <button type="button" onClick={onCancelDelete} className="rounded px-2 py-1 font-medium hover:bg-rose-100 dark:hover:bg-rose-950">
            Cancelar
          </button>
        </div>
      )}
    </li>
  );
}

function MemoryTab({ workspaceId }: { workspaceId: string }) {
  const [notes, setNotes] = useState<IdeMemoryNote[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pendingIds, setPendingIds] = useState<Record<string, boolean>>({});
  const [confirmingId, setConfirmingId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getIdeMemoryNotes(workspaceId)
      .then((rows) => {
        if (!cancelled) setNotes(rows);
      })
      .catch((err) => {
        if (!cancelled) setError(errorMessage(err, "No se pudo cargar la memoria de este proyecto."));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  async function handleDelete(noteId: string) {
    setConfirmingId(null);
    setPendingIds((rows) => ({ ...rows, [noteId]: true }));
    setError(null);
    try {
      await deleteIdeMemoryNote(workspaceId, noteId);
      setNotes((rows) => rows.filter((row) => row.id !== noteId));
    } catch (err) {
      setError(errorMessage(err, "No se pudo borrar ese recuerdo."));
    } finally {
      setPendingIds((rows) => {
        const next = { ...rows };
        delete next[noteId];
        return next;
      });
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-10">
        <Spinner className="h-5 w-5" />
      </div>
    );
  }

  if (error) {
    return <Alert variant="error">{error}</Alert>;
  }

  if (notes.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-slate-400">
        Sin memoria todavía. Edecán todavía no guardó ninguna convención, ubicación, error a evitar o decisión de
        este repo.
      </p>
    );
  }

  return (
    <ul className="space-y-2">
      {notes.map((note) => (
        <MemoryNoteRow
          key={note.id}
          note={note}
          pending={Boolean(pendingIds[note.id])}
          confirming={confirmingId === note.id}
          onStartDelete={() => setConfirmingId(note.id)}
          onCancelDelete={() => setConfirmingId(null)}
          onConfirmDelete={() => void handleDelete(note.id)}
        />
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Conocimiento verificado
// ---------------------------------------------------------------------------

const KNOWLEDGE_KIND_LABEL: Record<IdeKnowledgeFactKind, string> = {
  version_vigente: "Versión vigente",
  practica_recomendada: "Práctica recomendada",
  cambio_deprecado: "Cambio o deprecación",
  limite_o_cuota: "Límite o cuota",
};

function NotWiredNotice() {
  return (
    <Alert variant="info">
      <p className="font-semibold">Esta vista todavía no está conectada al backend.</p>
      <p className="mt-1 text-xs leading-5 opacity-90">
        El almacén de conocimiento verificado (<code className="font-mono">ide_conocimiento.py</code>, en el
        companion) ya existe y ya sabe listar y borrar hechos, pero <code className="font-mono">routers/ide.py</code>
        {" "}todavía no expone ningún endpoint de conocimiento y <code className="font-mono">ide_runtime.py</code> no
        lo despacha. Falta ese cableado (fuera de este paquete de trabajo) para que estos datos aparezcan aquí de
        verdad.
      </p>
    </Alert>
  );
}

function KnowledgeFactRow({
  fact,
  pending,
  confirming,
  onStartDelete,
  onCancelDelete,
  onConfirmDelete,
}: {
  fact: IdeKnowledgeFact;
  pending: boolean;
  confirming: boolean;
  onStartDelete: () => void;
  onCancelDelete: () => void;
  onConfirmDelete: () => void;
}) {
  return (
    <li className="rounded-lg border border-slate-200 p-3 dark:border-slate-800">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge variant="brand">{KNOWLEDGE_KIND_LABEL[fact.kind] ?? fact.kind}</Badge>
            <Badge variant={fact.vigente ? "success" : "warning"}>
              {fact.vigente ? "Vigente" : `Vencido hace ${Math.round(fact.edad_dias - fact.ttl_dias)} días`}
            </Badge>
          </div>
          <p className="mt-1.5 text-sm font-medium text-slate-700 dark:text-slate-200">{fact.tema}</p>
          <p className="mt-0.5 text-sm text-slate-600 dark:text-slate-300">{fact.contenido}</p>
          <p className="mt-1.5 text-[11px] text-slate-400">
            Verificado {formatDate(fact.verified_at)} (hace {Math.round(fact.edad_dias)} días)
            {fact.veces_reverificado > 0 ? ` · reverificado ${fact.veces_reverificado}×` : ""}
          </p>
          <a
            href={fact.fuente_url}
            target="_blank"
            rel="noreferrer"
            className="mt-1 block truncate text-[11px] text-brand-600 hover:underline dark:text-brand-400"
            title={fact.fuente_url}
          >
            {fact.fuente_url}
          </a>
        </div>
        {!confirming && (
          <button
            type="button"
            aria-label="Borrar este hecho"
            disabled={pending}
            onClick={onStartDelete}
            className="shrink-0 rounded-md p-1.5 text-slate-400 hover:bg-rose-100 hover:text-rose-600 disabled:opacity-50 dark:hover:bg-rose-950/40 dark:hover:text-rose-400"
          >
            {pending ? <Spinner className="h-3.5 w-3.5" /> : <TrashIcon className="h-3.5 w-3.5" />}
          </button>
        )}
      </div>
      {confirming && (
        <div className="mt-2 flex items-center gap-2 rounded-md bg-rose-50 px-2.5 py-1.5 text-xs text-rose-700 dark:bg-rose-950/40 dark:text-rose-300">
          <span className="flex-1">¿Borrar este hecho? Se comparte con todos los proyectos, no solo este.</span>
          <button type="button" onClick={onConfirmDelete} className="rounded bg-rose-600 px-2 py-1 font-medium text-white hover:bg-rose-700">
            Borrar
          </button>
          <button type="button" onClick={onCancelDelete} className="rounded px-2 py-1 font-medium hover:bg-rose-100 dark:hover:bg-rose-950">
            Cancelar
          </button>
        </div>
      )}
    </li>
  );
}

function KnowledgeTab({ workspaceId }: { workspaceId: string }) {
  const [facts, setFacts] = useState<IdeKnowledgeFact[]>([]);
  const [loading, setLoading] = useState(true);
  const [notWired, setNotWired] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingIds, setPendingIds] = useState<Record<string, boolean>>({});
  const [confirmingId, setConfirmingId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setNotWired(false);
    getIdeKnowledgeFacts(workspaceId)
      .then((rows) => {
        if (!cancelled) setFacts(rows);
      })
      .catch((err) => {
        if (cancelled) return;
        // 404 = la ruta todavía no existe en el backend (ver docstring del
        // módulo) -- eso es "pendiente", no un error real que espantar.
        if (err instanceof ApiError && err.status === 404) {
          setNotWired(true);
        } else {
          setError(errorMessage(err, "No se pudo cargar el conocimiento verificado."));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  async function handleDelete(factId: string) {
    setConfirmingId(null);
    setPendingIds((rows) => ({ ...rows, [factId]: true }));
    setError(null);
    try {
      await deleteIdeKnowledgeFact(workspaceId, factId);
      setFacts((rows) => rows.filter((row) => row.id !== factId));
    } catch (err) {
      setError(errorMessage(err, "No se pudo borrar ese hecho."));
    } finally {
      setPendingIds((rows) => {
        const next = { ...rows };
        delete next[factId];
        return next;
      });
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-10">
        <Spinner className="h-5 w-5" />
      </div>
    );
  }

  if (notWired) {
    return <NotWiredNotice />;
  }

  if (error) {
    return <Alert variant="error">{error}</Alert>;
  }

  if (facts.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-slate-400">
        Sin conocimiento verificado todavía. Edecán todavía no comprobó ningún hecho del mundo exterior con fuente.
      </p>
    );
  }

  return (
    <ul className="space-y-2">
      {facts.map((fact) => (
        <KnowledgeFactRow
          key={fact.id}
          fact={fact}
          pending={Boolean(pendingIds[fact.id])}
          confirming={confirmingId === fact.id}
          onStartDelete={() => setConfirmingId(fact.id)}
          onCancelDelete={() => setConfirmingId(null)}
          onConfirmDelete={() => void handleDelete(fact.id)}
        />
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Panel público
// ---------------------------------------------------------------------------

export type MemoryKnowledgeTab = "memoria" | "conocimiento";

export interface MemoryKnowledgePanelProps {
  workspaceId: string;
  workspaceName?: string | null;
  initialTab?: MemoryKnowledgeTab;
  onClose: () => void;
}

export function MemoryKnowledgePanel({
  workspaceId,
  workspaceName,
  initialTab = "memoria",
  onClose,
}: MemoryKnowledgePanelProps) {
  const [tab, setTab] = useState<MemoryKnowledgeTab>(initialTab);

  return (
    <div className="fixed inset-0 z-[100] grid place-items-center bg-slate-950/35 p-4" role="dialog" aria-modal="true">
      <section className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-forja-borde bg-white shadow-xl dark:border-slate-700 dark:bg-slate-900">
        <div className="flex shrink-0 items-center justify-between border-b border-slate-100 px-5 py-4 dark:border-slate-800">
          <div>
            <h2 className="text-lg font-bold">Memoria y conocimiento</h2>
            {workspaceName && <p className="text-xs text-slate-400">{workspaceName}</p>}
          </div>
          <button type="button" onClick={onClose} aria-label="Cerrar" className="rounded-md p-2 hover:bg-forja-superficie-hundida dark:hover:bg-slate-800">
            <XIcon className="h-4 w-4" />
          </button>
        </div>

        <div className="flex shrink-0 gap-1 border-b border-slate-100 px-3 pt-2 dark:border-slate-800">
          {(
            [
              ["memoria", "Memoria del proyecto", BrainIcon],
              ["conocimiento", "Conocimiento verificado", GlobeIcon],
            ] as const
          ).map(([value, label, Icon]) => (
            <button
              key={value}
              type="button"
              onClick={() => setTab(value)}
              className={cx(
                "flex items-center gap-1.5 rounded-t-md px-3 py-2 text-xs font-semibold",
                tab === value
                  ? "border-b-2 border-slate-950 text-slate-950 dark:border-slate-100 dark:text-slate-100"
                  : "text-slate-400 hover:text-slate-600 dark:hover:text-slate-300",
              )}
            >
              <Icon className="h-3.5 w-3.5" />
              {label}
            </button>
          ))}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4 thin-scrollbar">
          {tab === "memoria" ? (
            <>
              <p className="mb-3 text-xs text-slate-400">
                Convenciones, ubicaciones, errores a evitar y decisiones propias de este repo. No caducan solas.
              </p>
              <MemoryTab key={`memoria-${workspaceId}`} workspaceId={workspaceId} />
            </>
          ) : (
            <>
              <p className="mb-3 text-xs text-slate-400">
                Hechos del mundo exterior, con fuente y fecha, compartidos entre todos los proyectos. Un hecho
                vencido se muestra igual, marcado como tal.
              </p>
              <KnowledgeTab key={`conocimiento-${workspaceId}`} workspaceId={workspaceId} />
            </>
          )}
        </div>
      </section>
    </div>
  );
}

export default MemoryKnowledgePanel;
