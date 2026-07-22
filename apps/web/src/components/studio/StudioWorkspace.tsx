"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button, Input, Spinner, Textarea } from "@/components/ui";
import { downloadFile, runStudioAction, uploadFile } from "@/lib/api";
import {
  buildStudioEditInstruction,
  isolatedStudioHtmlBlob,
  pickStudioPreviewArtifact,
  previewKind,
  studioProjectsFromResponse,
  studioRevisionsFromResponse,
  studioTemplatesFromResponse,
  type StudioActionInput,
  type StudioActionResponse,
  type StudioArtifact,
  type StudioProject,
  type StudioRevision,
  type StudioTemplate,
  type TweakControl,
} from "@/lib/studio";

import { ClarifyPanel, type StudioCreateOptions } from "./ClarifyPanel";
import { CommandPalette, type StudioCommand } from "./CommandPalette";
import { HistoryNav } from "./HistoryNav";
import { InlineInspector, type StudioSelection } from "./InlineInspector";
import { MasterCanvas, type StudioPreview } from "./MasterCanvas";
import { TemplateGallery } from "./TemplateGallery";
import { TweaksPanel } from "./TweaksPanel";
import { WorkspacePanel } from "./WorkspacePanel";

const CREATIVE_STARTERS = [
  { label: "Imagen", prompt: "Crea una imagen visual memorable para " },
  { label: "Video", prompt: "Crea el storyboard y las escenas de un video para " },
  { label: "Campaña", prompt: "Diseña una campaña visual coherente para " },
  { label: "Producto", prompt: "Diseña la experiencia y el mockup de producto para " },
  { label: "Personas", prompt: "Crea una pieza centrada en personas, realista y respetuosa, para " },
  { label: "Analizar", prompt: "Analiza y mejora visualmente " },
] as const;

function nestedObject(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function projectIdFrom(response: StudioActionResponse): string | null {
  const project = nestedObject(response.result.project);
  return typeof project?.id === "string" ? project.id : typeof response.result.projectId === "string" ? response.result.projectId : null;
}

export function StudioWorkspace() {
  const [projects, setProjects] = useState<StudioProject[]>([]);
  const [revisions, setRevisions] = useState<StudioRevision[]>([]);
  const [templates, setTemplates] = useState<StudioTemplate[]>([]);
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null);
  const [activeRevisionId, setActiveRevisionId] = useState<string | null>(null);
  const [artifacts, setArtifacts] = useState<StudioArtifact[]>([]);
  const [preview, setPreview] = useState<StudioPreview | null>(null);
  const [prompt, setPrompt] = useState("");
  const [attachments, setAttachments] = useState<Array<{ id: string; name: string }>>([]);
  const [selection, setSelection] = useState<StudioSelection | null>(null);
  const [annotationDescription, setAnnotationDescription] = useState("");
  const [tweaks, setTweaks] = useState<TweakControl[]>([]);
  const [createOptions, setCreateOptions] = useState<StudioCreateOptions>({ mode: "general", quality: "balanced", count: 1, width: 1200, height: 800 });
  const [showClarify, setShowClarify] = useState(false);
  const [showTemplates, setShowTemplates] = useState(false);
  const [showProjects, setShowProjects] = useState(false);
  const [showInspector, setShowInspector] = useState(false);
  const [includeArchived, setIncludeArchived] = useState(false);
  const [confirmProject, setConfirmProject] = useState<{ project: StudioProject; action: "archive" | "restore" } | null>(null);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState("Dime qué quieres crear. Cada cambio quedará guardado y podrás volver atrás.");
  const [error, setError] = useState<string | null>(null);
  const objectUrlRef = useRef<string | null>(null);
  const requestGeneration = useRef(0);

  const activeProject = projects.find((project) => project.id === activeProjectId) ?? null;
  const activeRevisionIndex = revisions.findIndex((revision) => revision.id === activeRevisionId);
  const activeRevision = revisions[activeRevisionIndex] ?? null;

  const execute = useCallback(async (input: StudioActionInput): Promise<StudioActionResponse> => {
    setBusy(true);
    setError(null);
    try {
      const response = await runStudioAction(input);
      setMessage(response.message);
      return response;
    } catch (reason) {
      const detail = reason instanceof Error ? reason.message : "Studio no pudo completar esa acción.";
      setError(detail);
      throw reason;
    } finally {
      setBusy(false);
    }
  }, []);

  const refreshProjects = useCallback(async (archived = false) => {
    const generation = ++requestGeneration.current;
    const response = await runStudioAction({ action: "list", includeArchived: archived });
    if (generation !== requestGeneration.current) return [];
    const next = studioProjectsFromResponse(response);
    setProjects(next);
    return next;
  }, []);

  const loadArtifact = useCallback(async (artifact: StudioArtifact) => {
    const kind = previewKind(artifact);
    if (!kind) return;
    const blob = await downloadFile(artifact.file_id);
    const safeBlob = kind === "html" ? await isolatedStudioHtmlBlob(blob) : blob;
    const objectUrl = URL.createObjectURL(safeBlob);
    if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
    objectUrlRef.current = objectUrl;
    setPreview({ ...artifact, kind, objectUrl });
  }, []);

  const showResponseArtifacts = useCallback(async (response: StudioActionResponse) => {
    const visual = response.artifacts.filter((artifact) => previewKind(artifact));
    setArtifacts(visual);
    const first = pickStudioPreviewArtifact(visual);
    if (first) await loadArtifact(first);
  }, [loadArtifact]);

  const selectRevision = useCallback(async (projectId: string, revision: StudioRevision) => {
    setActiveRevisionId(revision.id);
    setSelection(null);
    const response = await execute({ action: "read", projectId, revisionId: revision.id });
    await showResponseArtifacts(response);
  }, [execute, showResponseArtifacts]);

  const selectProject = useCallback(async (project: StudioProject) => {
    setActiveProjectId(project.id);
    setActiveRevisionId(null);
    setRevisions([]);
    setShowProjects(false);
    const response = await execute({ action: "history", projectId: project.id });
    const next = studioRevisionsFromResponse(response);
    setRevisions(next);
    const latest = next.at(-1);
    if (latest) await selectRevision(project.id, latest);
  }, [execute, selectRevision]);

  useEffect(() => {
    let cancelled = false;
    void Promise.allSettled([
      refreshProjects(false),
      runStudioAction({ action: "template-list" }),
    ]).then((results) => {
      if (cancelled) return;
      const templateResult = results[1];
      if (templateResult.status === "fulfilled") setTemplates(studioTemplatesFromResponse(templateResult.value));
      const failed = results.some((result) => result.status === "rejected");
      if (failed) setError("No pude cargar todo el workspace. Puedes reintentar sin perder nada.");
    });
    return () => { cancelled = true; if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current); };
  }, [refreshProjects]);

  async function createProject() {
    const cleanPrompt = prompt.trim();
    if (!cleanPrompt) return;
    const response = await execute({ action: "create", prompt: cleanPrompt, projectName: cleanPrompt.slice(0, 80), files: attachments.map((item) => item.id), ...createOptions });
    setShowClarify(false);
    setPrompt("");
    setAttachments([]);
    await showResponseArtifacts(response);
    const projectId = projectIdFrom(response);
    const nextProjects = await refreshProjects();
    const project = nextProjects.find((item) => item.id === projectId) ?? nextProjects[0];
    if (project) await selectProject(project);
  }

  async function applyEdit(instruction: string, includeTweaks = false) {
    if (!activeProject || !instruction.trim() && !includeTweaks) return;
    const compiled = buildStudioEditInstruction({ instruction, selection: selection?.label, annotations: annotationDescription, tweaks: includeTweaks ? tweaks : [] });
    const response = await execute({ action: "edit", projectId: activeProject.id, revisionId: activeRevision?.id, instruction: compiled });
    setSelection(null);
    await showResponseArtifacts(response);
    const history = await execute({ action: "history", projectId: activeProject.id });
    const next = studioRevisionsFromResponse(history);
    setRevisions(next);
    setActiveRevisionId(next.at(-1)?.id ?? null);
    await refreshProjects();
  }

  async function duplicateProject() {
    if (!activeProject) return;
    const response = await execute({ action: "duplicate", projectId: activeProject.id, revisionId: activeRevision?.id, projectName: `${activeProject.name} — rama` });
    await showResponseArtifacts(response);
    const next = await refreshProjects();
    const duplicate = next.find((item) => item.id === projectIdFrom(response));
    if (duplicate) await selectProject(duplicate);
  }

  async function createFromTemplate(template: StudioTemplate) {
    const response = await execute({ action: "template-create", templateId: template.id, projectName: `${template.name} — copia` });
    setShowTemplates(false);
    await showResponseArtifacts(response);
    const next = await refreshProjects();
    const created = next.find((item) => item.id === projectIdFrom(response));
    if (created) await selectProject(created);
  }

  async function saveTemplate() {
    if (!activeProject) return;
    await execute({ action: "template-save", projectId: activeProject.id, revisionId: activeRevision?.id, templateName: activeProject.name, templateDescription: "Plantilla privada guardada desde Studio", templateCategory: "other" });
    const response = await runStudioAction({ action: "template-list" });
    setTemplates(studioTemplatesFromResponse(response));
  }

  async function confirmOrganization() {
    if (!confirmProject) return;
    await execute({ action: confirmProject.action, projectId: confirmProject.project.id, confirmed: true });
    if (confirmProject.project.id === activeProjectId && confirmProject.action === "archive") {
      setActiveProjectId(null); setActiveRevisionId(null); setRevisions([]); setArtifacts([]); setPreview(null);
    }
    setConfirmProject(null);
    await refreshProjects();
  }

  async function analyzeProject() {
    if (!activeProject) { setPrompt("Analiza y mejora visualmente "); return; }
    const response = await execute({ action: "brand-health", projectId: activeProject.id, revisionId: activeRevision?.id });
    const summary = typeof response.result.summary === "string" ? response.result.summary : response.message;
    setMessage(summary);
  }

  async function uploadReferences(files: FileList | null) {
    if (!files?.length) return;
    setUploading(true); setError(null);
    try {
      const uploaded = await Promise.all(
        [...files].slice(0, Math.max(0, 12 - attachments.length)).map((file) => uploadFile(file)),
      );
      setAttachments((current) => [...current, ...uploaded.map((file) => ({ id: file.id, name: file.filename }))].slice(0, 12));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "No se pudieron subir las referencias."); }
    finally { setUploading(false); }
  }

  async function downloadCurrent() {
    if (!preview) return;
    const blob = await downloadFile(preview.file_id);
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = preview.filename; document.body.appendChild(anchor); anchor.click(); anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
  }

  const commands: StudioCommand[] = [
    { id: "new", label: "Crear desde una idea", group: "Crear", shortcut: "N", keywords: ["imagen", "video", "campaña"], action: () => { setPrompt(""); document.getElementById("studio-prompt")?.focus(); } },
    { id: "templates", label: "Abrir plantillas", group: "Crear", shortcut: "T", action: () => setShowTemplates(true) },
    { id: "analyze", label: "Analizar coherencia visual", group: "Mejorar", action: () => void analyzeProject() },
    { id: "duplicate", label: "Ramificar versión actual", group: "Versiones", action: () => void duplicateProject() },
    { id: "template-save", label: "Guardar como plantilla", group: "Versiones", action: () => void saveTemplate() },
    { id: "download", label: "Descargar artefacto actual", group: "Entregar", action: () => void downloadCurrent() },
  ];

  return (
    <div className="relative flex h-full min-h-[640px] w-full min-w-0 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900 md:min-h-0">
      <div className={`${showProjects ? "fixed inset-y-0 left-0 z-50 block w-[min(86vw,300px)] shadow-2xl" : "hidden"} shrink-0 border-r border-slate-200 dark:border-slate-700 lg:static lg:block lg:w-64 lg:shadow-none`}>
        <WorkspacePanel projects={projects} revisions={revisions} activeProjectId={activeProjectId} activeRevisionId={activeRevisionId} loading={busy} includeArchived={includeArchived} onArchivedChange={(value) => { setIncludeArchived(value); void refreshProjects(value); }} onSelectProject={(project) => void selectProject(project)} onSelectRevision={(revision) => activeProject && void selectRevision(activeProject.id, revision)} onRequestArchive={(project) => setConfirmProject({ project, action: "archive" })} onRequestRestore={(project) => setConfirmProject({ project, action: "restore" })} />
      </div>
      {showProjects && <button type="button" aria-label="Cerrar proyectos" className="fixed inset-0 z-40 bg-slate-950/50 lg:hidden" onClick={() => setShowProjects(false)} />}

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="border-b border-slate-200 bg-white px-3 py-3 dark:border-slate-700 dark:bg-slate-900">
          <div className="flex items-center gap-2">
            <Button type="button" size="sm" variant="ghost" className="lg:hidden" onClick={() => setShowProjects(true)}>Proyectos</Button>
            <div className="hidden sm:block"><HistoryNav currentIndex={Math.max(0, activeRevisionIndex)} totalCount={revisions.length} onPrev={() => activeProject && activeRevisionIndex > 0 && void selectRevision(activeProject.id, revisions[activeRevisionIndex - 1])} onNext={() => activeProject && activeRevisionIndex < revisions.length - 1 && void selectRevision(activeProject.id, revisions[activeRevisionIndex + 1])} onDuplicate={() => void duplicateProject()} disabled={busy} /></div>
            <div className="min-w-0 flex-1"><Textarea id="studio-prompt" value={prompt} onChange={(event) => setPrompt(event.target.value)} onKeyDown={(event) => { if ((event.metaKey || event.ctrlKey) && event.key === "Enter" && prompt.trim()) setShowClarify(true); }} placeholder="Dime qué quieres crear o mejorar…" rows={2} className="min-h-12 resize-none" /></div>
            <div className="flex flex-col gap-1"><Button type="button" onClick={() => setShowClarify(true)} disabled={!prompt.trim() || busy}>Crear</Button><span className="hidden text-center text-[9px] text-slate-400 sm:block">⌘ Enter</span></div>
            <Button type="button" variant="secondary" onClick={() => setShowInspector((value) => !value)} className="xl:hidden">Editar</Button>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            {CREATIVE_STARTERS.map((starter) => <button key={starter.label} type="button" onClick={() => { setPrompt(starter.prompt); document.getElementById("studio-prompt")?.focus(); }} className="rounded-full border border-slate-200 bg-white px-2.5 py-1 text-[11px] text-slate-600 hover:border-brand-300 hover:text-brand-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300">{starter.label}</button>)}
            <label className="cursor-pointer rounded-full border border-dashed border-slate-300 px-2.5 py-1 text-[11px] text-slate-500 hover:border-brand-400 dark:border-slate-600"><input type="file" multiple accept="image/*,.pdf,.doc,.docx,.ppt,.pptx" className="sr-only" onChange={(event) => void uploadReferences(event.target.files)} />{uploading ? "Subiendo…" : "+ Referencias"}</label>
            <button type="button" onClick={() => setShowTemplates(true)} className="rounded-full px-2.5 py-1 text-[11px] font-medium text-brand-600 hover:bg-brand-50 dark:hover:bg-brand-950/40">Plantillas</button>
            <button type="button" className="ml-auto hidden rounded border border-slate-200 px-2 py-1 text-[10px] text-slate-500 dark:border-slate-700 sm:block" title="Abrir comandos" onClick={() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", metaKey: true }))}>⌘ K</button>
          </div>
          {attachments.length > 0 && <div className="mt-2 flex flex-wrap gap-1">{attachments.map((file) => <span key={file.id} className="inline-flex max-w-48 items-center gap-1 rounded bg-slate-100 px-2 py-1 text-[10px] text-slate-600 dark:bg-slate-800 dark:text-slate-300"><span className="truncate">{file.name}</span><button type="button" aria-label={`Quitar ${file.name}`} onClick={() => setAttachments((current) => current.filter((item) => item.id !== file.id))}>×</button></span>)}</div>}
        </div>

        <div className="relative min-h-0 flex-1">
          <MasterCanvas preview={preview} width={activeRevision?.width ?? createOptions.width} height={activeRevision?.height ?? createOptions.height} projectName={activeProject?.name ?? "Nuevo diseño"} revisionLabel={activeRevision?.label ?? ""} selection={selection} onSelectionChange={(value) => { setSelection(value); if (value) setShowInspector(true); }} onAnnotationsChange={setAnnotationDescription} onDownload={() => void downloadCurrent()} />
          {artifacts.length > 1 && <div className="absolute bottom-3 left-1/2 z-30 flex max-w-[calc(100%-2rem)] -translate-x-1/2 gap-1 overflow-x-auto rounded-xl border border-slate-200 bg-white/95 p-1.5 shadow-lg backdrop-blur dark:border-slate-700 dark:bg-slate-900/95">{artifacts.map((artifact, index) => <button key={artifact.file_id} type="button" onClick={() => void loadArtifact(artifact)} className={`max-w-40 truncate rounded-lg px-3 py-1.5 text-[10px] ${preview?.file_id === artifact.file_id ? "bg-brand-600 text-white" : "text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"}`}>Variante {index + 1}</button>)}</div>}
        </div>
        <div className="flex min-h-9 items-center gap-2 border-t border-slate-200 bg-white px-3 py-1.5 text-xs dark:border-slate-700 dark:bg-slate-900" aria-live="polite">{busy && <Spinner className="h-3.5 w-3.5 text-brand-600" />}<span className={`min-w-0 flex-1 truncate ${error ? "text-rose-600" : "text-slate-500"}`}>{error ?? message}</span>{activeProject && <button type="button" onClick={() => void saveTemplate()} disabled={busy} className="hidden text-brand-600 hover:underline sm:block">Guardar plantilla</button>}<Link href="/app" className="text-slate-400 hover:text-brand-600" title="Pedir cualquier otra creación en el chat">Continuar en chat</Link></div>
      </div>

      <aside className={`${showInspector ? "fixed inset-x-3 bottom-3 top-20 z-50 block overflow-y-auto rounded-2xl shadow-2xl" : "hidden"} w-72 shrink-0 border-l border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900 xl:static xl:block xl:rounded-none xl:shadow-none`} aria-label="Inspector">
        <div className="mb-3 flex items-center justify-between xl:hidden"><h2 className="text-sm font-semibold">Editar diseño</h2><Button type="button" size="sm" variant="ghost" onClick={() => setShowInspector(false)}>Cerrar</Button></div>
        <InlineInspector selection={selection} busy={busy} onApply={(instruction) => void applyEdit(instruction)} />
        <div className="mt-5"><TweaksPanel controls={tweaks} busy={busy} onChange={setTweaks} onApply={() => void applyEdit("Aplica los controles visuales indicados.", true)} /></div>
        {activeProject && <div className="mt-5 space-y-2 border-t border-slate-200 pt-4 dark:border-slate-700"><Button type="button" size="sm" variant="secondary" className="w-full" onClick={() => void analyzeProject()} loading={busy}>Analizar coherencia</Button><Button type="button" size="sm" variant="ghost" className="w-full" onClick={() => setConfirmProject({ project: activeProject, action: "archive" })}>Archivar proyecto</Button></div>}
      </aside>

      {showClarify && <ClarifyPanel prompt={prompt} options={createOptions} busy={busy} onChange={setCreateOptions} onBack={() => setShowClarify(false)} onGenerate={() => void createProject()} />}
      {showTemplates && <TemplateGallery templates={templates} busy={busy} onUse={(template) => void createFromTemplate(template)} onClose={() => setShowTemplates(false)} />}
      {confirmProject && <div className="fixed inset-0 z-[95] flex items-center justify-center bg-slate-950/60 p-3" role="presentation" onMouseDown={() => setConfirmProject(null)}><section className="w-full max-w-sm rounded-2xl bg-white p-5 shadow-2xl dark:bg-slate-900" role="alertdialog" aria-modal="true" aria-labelledby="organize-title" onMouseDown={(event) => event.stopPropagation()}><h2 id="organize-title" className="font-semibold text-slate-900 dark:text-white">{confirmProject.action === "archive" ? "¿Archivar este proyecto?" : "¿Restaurar este proyecto?"}</h2><p className="mt-2 text-sm leading-relaxed text-slate-500">{confirmProject.action === "archive" ? "Dejará de aparecer en la lista normal, pero sus revisiones no se eliminan y podrás restaurarlo." : "Volverá a aparecer entre tus proyectos activos."}</p><div className="mt-5 flex justify-end gap-2"><Button type="button" variant="ghost" onClick={() => setConfirmProject(null)}>Cancelar</Button><Button type="button" variant={confirmProject.action === "archive" ? "danger" : "primary"} onClick={() => void confirmOrganization()} loading={busy}>{confirmProject.action === "archive" ? "Archivar" : "Restaurar"}</Button></div></section></div>}
      <CommandPalette commands={commands} />
    </div>
  );
}
