"use client";

import type { StudioProject, StudioRevision } from "@/lib/studio";

function relativeDate(value: string): string {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return "";
  const minutes = Math.max(0, Math.floor((Date.now() - timestamp) / 60_000));
  if (minutes < 1) return "ahora";
  if (minutes < 60) return `hace ${minutes} min`;
  if (minutes < 1_440) return `hace ${Math.floor(minutes / 60)} h`;
  return `hace ${Math.floor(minutes / 1_440)} d`;
}

export function WorkspacePanel({ projects, revisions, activeProjectId, activeRevisionId, loading, includeArchived, onArchivedChange, onSelectProject, onSelectRevision, onRequestArchive, onRequestRestore }: {
  projects: StudioProject[];
  revisions: StudioRevision[];
  activeProjectId: string | null;
  activeRevisionId: string | null;
  loading: boolean;
  includeArchived: boolean;
  onArchivedChange: (value: boolean) => void;
  onSelectProject: (project: StudioProject) => void;
  onSelectRevision: (revision: StudioRevision) => void;
  onRequestArchive: (project: StudioProject) => void;
  onRequestRestore: (project: StudioProject) => void;
}) {
  return (
    <aside className="flex h-full min-h-0 flex-col bg-white dark:bg-slate-900" aria-label="Proyectos y revisiones">
      <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3 dark:border-slate-700"><div><p className="text-[10px] font-bold uppercase tracking-widest text-slate-400">Workspace</p><h2 className="text-sm font-semibold text-slate-900 dark:text-white">Tus proyectos</h2></div><label className="flex items-center gap-1.5 text-[10px] text-slate-500"><input type="checkbox" checked={includeArchived} onChange={(event) => onArchivedChange(event.target.checked)} />Archivados</label></div>
      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {loading && projects.length === 0 ? <p className="p-3 text-xs text-slate-500">Cargando proyectos…</p> : projects.length === 0 ? <p className="p-3 text-xs leading-relaxed text-slate-500">Tu primer diseño se guardará aquí automáticamente.</p> : projects.map((project) => {
          const active = project.id === activeProjectId;
          return <div key={project.id} className="mb-1">
            <button type="button" onClick={() => onSelectProject(project)} className={`group w-full rounded-lg px-3 py-2 text-left transition ${active ? "bg-brand-50 dark:bg-brand-950/40" : "hover:bg-slate-50 dark:hover:bg-slate-800"}`}>
              <div className="flex items-start gap-2"><span className={`mt-0.5 ${active ? "text-brand-600" : "text-slate-400"}`} aria-hidden="true">▰</span><span className="min-w-0 flex-1"><span className="block truncate text-xs font-semibold text-slate-800 dark:text-slate-100">{project.name}</span><span className="mt-0.5 block text-[10px] text-slate-500">{project.revisions} versiones · {project.mode} {relativeDate(project.updatedAt)}</span></span><span role="button" tabIndex={0} title={project.archivedAt ? "Restaurar" : "Archivar"} onClick={(event) => { event.stopPropagation(); project.archivedAt ? onRequestRestore(project) : onRequestArchive(project); }} onKeyDown={(event) => { if (event.key === "Enter") project.archivedAt ? onRequestRestore(project) : onRequestArchive(project); }} className="rounded px-1 text-slate-400 opacity-0 hover:text-brand-600 group-hover:opacity-100 focus:opacity-100">{project.archivedAt ? "↺" : "⌁"}</span></div>
            </button>
            {active && revisions.length > 0 && <div className="ml-5 border-l border-slate-200 py-1 pl-2 dark:border-slate-700">{revisions.map((revision, index) => <button key={revision.id} type="button" onClick={() => onSelectRevision(revision)} className={`flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-[11px] ${revision.id === activeRevisionId ? "bg-slate-100 font-semibold text-slate-900 dark:bg-slate-800 dark:text-white" : "text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"}`}><span className="h-1.5 w-1.5 rounded-full bg-brand-500" /><span className="min-w-0 flex-1 truncate">V{index + 1} · {revision.label}</span></button>)}</div>}
          </div>;
        })}
      </div>
    </aside>
  );
}
