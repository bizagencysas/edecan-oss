"use client";

/**
 * Barra lateral de proyectos de Forge Studio (el IDE embebido), estilo
 * "carpetas + conversaciones anidadas" que se pidió tomando como referencia
 * Antigravity/Claude: arriba un botón para abrir una conversación suelta,
 * debajo "Proyectos" como carpetas plegables, y dentro de cada una sus
 * conversaciones con título y una marca sutil de estado.
 *
 * Por qué existe este componente: hoy Forge Studio abre una sesión nueva
 * por cada mensaje y no hay ninguna jerarquía visible (ver diagnóstico en
 * `ide_sessions._prompt_with_conversation_context`). Esta barra es la pieza
 * de UI que por fin agrupa esas sesiones bajo `conversation_id` de forma
 * visible, para que la persona entienda dónde está su historial.
 *
 * Contrato de datos: `IdeProjectSummary`/`IdeConversationSummary` reflejan
 * tal cual lo que devuelve `ProjectRegistry` en
 * `apps/companion/edecan_companion/ide_projects.py` (mismo paquete de
 * trabajo, agente en paralelo) — nombres de campo en snake_case porque así
 * cruza la API sin transformar nada. Integrar esto en
 * `apps/app/(app)/app/ide/page.tsx` (fase siguiente, otro agente) es solo
 * cablear `fetch`/`api-ide.ts` contra estas props; este archivo no asume
 * ningún estado que el backend no mande.
 *
 * Autocontenido a propósito: no importa nada de `page.tsx` y no toca
 * `components/icons.tsx` (compartido, puede estar en uso en paralelo) — el
 * único ícono que faltaba ahí (carpeta) se define aquí mismo, con el mismo
 * lenguaje visual del resto (trazo 1.75, 24×24, `currentColor`).
 */

import { useEffect, useMemo, useState, type FormEvent, type KeyboardEvent, type SVGProps } from "react";

import {
  ChatIcon,
  CheckIcon,
  ChevronDownIcon,
  PencilIcon,
  PlusIcon,
  TrashIcon,
  XIcon,
} from "@/components/icons";
import { ProjectPicker } from "@/components/ide/ProjectPicker";
import { Button, Input } from "@/components/ui";
import type { IdeSessionStatus, IdeWorkspace } from "@/lib/api-ide";

function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

function FolderIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Contrato de datos — espejo de ProjectRegistry (ide_projects.py)
// ---------------------------------------------------------------------------

/** Tal cual `ProjectRegistry._snapshot_project()`. */
export interface IdeProjectSummary {
  id: string;
  name: string;
  workspace_id: string;
  workspace_name: string | null;
  workspace_path: string | null;
  workspace_available: boolean;
  conversation_count: number;
  created_at: string;
  updated_at: string;
}

/** Tal cual las filas que persiste `ProjectRegistry` para conversaciones. */
export interface IdeConversationSummary {
  id: string;
  project_id: string | null;
  title: string;
  created_at: string;
  updated_at: string;
  /**
   * Estado de la última sesión de agente conocida para esta conversación.
   * `ProjectRegistry` no lo sabe por sí sola (vive en `ide_sessions.py`,
   * otro archivo) — quien integre este componente lo cruza con
   * `IdeSession.status` (`lib/api-ide.ts`) si quiere pintar el puntito.
   * Sin el dato, la fila simplemente no muestra ninguna marca.
   */
  last_session_status?: IdeSessionStatus | string | null;
}

/** Subconjunto de `IdeWorkspace` (`lib/api-ide.ts`) que necesita el selector al crear un proyecto. */
export interface IdeWorkspaceOption {
  id: string;
  name: string;
  available?: boolean;
}

export interface ProjectSidebarProps {
  projects: IdeProjectSummary[];
  /** Todas las conversaciones visibles: de cualquier proyecto + sin asignar (igual que `GET /conversations` sin filtro). */
  conversations: IdeConversationSummary[];
  activeConversationId: string | null;
  /** Carpetas de repo autorizadas, para elegir a cuál queda atado un proyecto nuevo. */
  workspaces: IdeWorkspaceOption[];
  /**
   * "Conectar ProjectPicker": el selector de carpeta que crea/registra el
   * proyecto puede autorizar una carpeta nueva por su cuenta (Finder o ruta
   * manual), no solo elegir entre las ya conocidas -- este callback deja que
   * quien aloja la barra (`page.tsx`) mantenga su propia lista al día.
   */
  onWorkspacesChange?: (workspaces: IdeWorkspace[]) => void;
  /** Deshabilita toda la barra mientras la pantalla que la aloja hace otra llamada (ej. cambiando de workspace activo). */
  busy?: boolean;
  className?: string;

  onSelectConversation: (conversationId: string) => void;
  onCreateConversation: (projectId: string | null) => void;
  onRenameConversation: (conversationId: string, title: string) => void;
  onDeleteConversation: (conversationId: string) => void;

  onCreateProject: (input: { name: string; workspace_id: string }) => void;
  onRenameProject: (projectId: string, name: string) => void;
  /**
   * `mode: "delete"` borra también las conversaciones del proyecto;
   * `"keep"` las desasigna ("sin proyecto") — mismo contrato que
   * `ProjectRegistry.delete_project(conversations=...)`.
   */
  onDeleteProject: (projectId: string, mode: "keep" | "delete") => void;
}

type Editing = { kind: "project" | "conversation"; id: string } | null;

const RUNNING_STATUSES = new Set<string>(["starting", "running"]);
const FAILED_STATUSES = new Set<string>(["failed", "interrupted"]);

/** Sutil a propósito: solo marca lo que importa (corriendo / falló), el resto de estados no ensucia la fila. */
function statusDotClass(status: string | null | undefined): string | null {
  if (!status) return null;
  if (RUNNING_STATUSES.has(status)) return "bg-amber-500 animate-pulse";
  if (FAILED_STATUSES.has(status)) return "bg-rose-500";
  return null;
}

export function ProjectSidebar({
  projects,
  conversations,
  activeConversationId,
  workspaces,
  onWorkspacesChange,
  busy = false,
  className,
  onSelectConversation,
  onCreateConversation,
  onRenameConversation,
  onDeleteConversation,
  onCreateProject,
  onRenameProject,
  onDeleteProject,
}: ProjectSidebarProps) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [editing, setEditing] = useState<Editing>(null);
  const [draftValue, setDraftValue] = useState("");
  const [confirmingProjectDelete, setConfirmingProjectDelete] = useState<string | null>(null);
  const [confirmingConversationDelete, setConfirmingConversationDelete] = useState<string | null>(null);
  const [creatingProject, setCreatingProject] = useState(false);
  const [newProjectName, setNewProjectName] = useState("");
  const [newProjectWorkspace, setNewProjectWorkspace] = useState<IdeWorkspace | null>(null);

  const conversationsByProject = useMemo(() => {
    const grouped = new Map<string, IdeConversationSummary[]>();
    const unassigned: IdeConversationSummary[] = [];
    for (const conversation of conversations) {
      if (conversation.project_id) {
        const list = grouped.get(conversation.project_id);
        if (list) list.push(conversation);
        else grouped.set(conversation.project_id, [conversation]);
      } else {
        unassigned.push(conversation);
      }
    }
    return { grouped, unassigned };
  }, [conversations]);

  // Si la conversación activa vive en un proyecto que el usuario había plegado, lo reabre —
  // igual que el "modo avanzado" de `layout/Sidebar.tsx` se auto-abre cuando la ruta activa cae adentro.
  useEffect(() => {
    if (!activeConversationId) return;
    const active = conversations.find((row) => row.id === activeConversationId);
    if (!active?.project_id) return;
    const projectId = active.project_id;
    setExpanded((prev) => (prev[projectId] === false ? { ...prev, [projectId]: true } : prev));
  }, [activeConversationId, conversations]);

  function toggleProject(projectId: string) {
    setExpanded((prev) => ({ ...prev, [projectId]: !(prev[projectId] ?? true) }));
  }

  function startRenameProject(project: IdeProjectSummary) {
    setConfirmingProjectDelete(null);
    setEditing({ kind: "project", id: project.id });
    setDraftValue(project.name);
  }

  function startRenameConversation(conversation: IdeConversationSummary) {
    setConfirmingConversationDelete(null);
    setEditing({ kind: "conversation", id: conversation.id });
    setDraftValue(conversation.title);
  }

  function cancelEditing() {
    setEditing(null);
    setDraftValue("");
  }

  function commitEditing() {
    const value = draftValue.trim();
    if (!editing || !value) {
      cancelEditing();
      return;
    }
    if (editing.kind === "project") onRenameProject(editing.id, value);
    else onRenameConversation(editing.id, value);
    cancelEditing();
  }

  function handleEditKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault();
      commitEditing();
    } else if (event.key === "Escape") {
      event.preventDefault();
      cancelEditing();
    }
  }

  function submitNewProject(event: FormEvent) {
    event.preventDefault();
    const name = newProjectName.trim();
    if (!name || !newProjectWorkspace) return;
    onCreateProject({ name, workspace_id: newProjectWorkspace.id });
    setCreatingProject(false);
    setNewProjectName("");
    setNewProjectWorkspace(null);
  }

  return (
    <aside
      className={cx(
        // El ancho lo da ahora el contenedor (`PanelRedimensionable`, único
        // sitio con el `~340px`/`21rem` de §3.1 -- antes vivía cableado
        // acá también, y los dos anchos fijos competían: al arrastrar el
        // panel, este `aside` se quedaba pegado a 336px y o sobraba espacio
        // en blanco o se recortaba el contenido contra el `overflow-hidden`
        // del contenedor).
        "flex h-full min-h-0 w-full flex-col overflow-hidden border-r border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900",
        className,
      )}
    >
      <div className="shrink-0 px-3 pt-3 pb-2">
        <Button
          type="button"
          variant="secondary"
          size="sm"
          disabled={busy}
          onClick={() => onCreateConversation(null)}
          className="w-full justify-start gap-2"
        >
          <PlusIcon className="h-3.5 w-3.5" />
          Nueva conversación
        </Button>
      </div>

      <nav
        aria-label="Proyectos y conversaciones"
        className="flex min-h-0 flex-1 flex-col overflow-y-auto overscroll-contain px-3 pb-3 thin-scrollbar"
      >
        <div className="flex items-center justify-between px-1 pb-1 pt-1">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">Proyectos</span>
          <button
            type="button"
            aria-label="Nuevo proyecto"
            disabled={busy}
            onClick={() => setCreatingProject((open) => !open)}
            className="rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700 disabled:opacity-50 dark:hover:bg-slate-800 dark:hover:text-slate-200"
          >
            <PlusIcon className="h-3.5 w-3.5" />
          </button>
        </div>

        {creatingProject && (
          <form
            onSubmit={submitNewProject}
            className="mb-2 space-y-1.5 rounded-lg border border-slate-200 p-2 dark:border-slate-700"
          >
            <Input
              autoFocus
              placeholder="Nombre del proyecto"
              value={newProjectName}
              onChange={(event) => setNewProjectName(event.target.value)}
              className="!py-1.5 text-xs"
              maxLength={120}
            />
            {workspaces.length === 0 && (
              <p className="text-xs text-slate-400">
                Aún no autorizas ninguna carpeta en este Mac -- se puede hacer aquí mismo, abajo.
              </p>
            )}
            {/* "Conectar ProjectPicker": la carpeta del proyecto ya no es un
                `<select>` suelto, es el mismo selector completo (elegir una
                autorizada o autorizar una nueva) que usa la pastilla de
                workspace. */}
            <ProjectPicker
              value={newProjectWorkspace}
              onChange={setNewProjectWorkspace}
              onWorkspacesChange={onWorkspacesChange}
              label="Carpeta del proyecto"
            />
            <div className="flex justify-end gap-1.5 pt-0.5">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="!px-2 !py-1 text-xs"
                onClick={() => {
                  setCreatingProject(false);
                  setNewProjectName("");
                  setNewProjectWorkspace(null);
                }}
              >
                Cancelar
              </Button>
              <Button
                type="submit"
                size="sm"
                className="!px-2 !py-1 text-xs"
                disabled={!newProjectName.trim() || !newProjectWorkspace}
              >
                Crear
              </Button>
            </div>
          </form>
        )}

        {projects.length === 0 && !creatingProject && (
          <p className="px-1 py-1 text-xs text-slate-400">Sin proyectos todavía.</p>
        )}

        <ul className="space-y-0.5">
          {projects.map((project) => {
            const isOpen = expanded[project.id] ?? true;
            const projectConversations = conversationsByProject.grouped.get(project.id) ?? [];
            const isEditingThis = editing?.kind === "project" && editing.id === project.id;
            const isConfirmingDelete = confirmingProjectDelete === project.id;

            return (
              <li key={project.id}>
                <div className="group flex items-center gap-1 rounded-md px-1.5 py-1 hover:bg-slate-100 dark:hover:bg-slate-800">
                  <button
                    type="button"
                    onClick={() => toggleProject(project.id)}
                    aria-expanded={isOpen}
                    className="flex min-w-0 flex-1 items-center gap-1 text-left"
                  >
                    <ChevronDownIcon
                      className={cx(
                        "h-3 w-3 shrink-0 text-slate-400 transition-transform",
                        isOpen ? "" : "-rotate-90",
                      )}
                    />
                    <FolderIcon className="h-3.5 w-3.5 shrink-0 text-slate-400" />
                    {isEditingThis ? (
                      <input
                        autoFocus
                        value={draftValue}
                        onChange={(event) => setDraftValue(event.target.value)}
                        onKeyDown={handleEditKeyDown}
                        onBlur={commitEditing}
                        onClick={(event) => event.stopPropagation()}
                        maxLength={120}
                        className="min-w-0 flex-1 rounded border border-brand-300 bg-white px-1 py-0.5 text-xs text-slate-900 focus:outline-none dark:border-brand-700 dark:bg-slate-800 dark:text-slate-100"
                      />
                    ) : (
                      <span
                        className="truncate text-xs font-medium text-slate-700 dark:text-slate-200"
                        title={project.workspace_name ?? undefined}
                      >
                        {project.name}
                        {!project.workspace_available && (
                          <span className="ml-1 font-normal italic text-amber-600 dark:text-amber-400">
                            carpeta no disponible
                          </span>
                        )}
                      </span>
                    )}
                    {project.conversation_count > 0 && !isEditingThis && (
                      <span className="shrink-0 text-[10px] text-slate-400">{project.conversation_count}</span>
                    )}
                  </button>

                  {!isEditingThis && !isConfirmingDelete && (
                    <div className="flex shrink-0 items-center gap-0.5 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100">
                      <button
                        type="button"
                        aria-label="Nueva conversación en este proyecto"
                        disabled={busy}
                        onClick={() => onCreateConversation(project.id)}
                        className="rounded p-1 text-slate-400 hover:bg-slate-200 hover:text-slate-700 disabled:opacity-50 dark:hover:bg-slate-700 dark:hover:text-slate-200"
                      >
                        <PlusIcon className="h-3 w-3" />
                      </button>
                      <button
                        type="button"
                        aria-label="Renombrar proyecto"
                        disabled={busy}
                        onClick={() => startRenameProject(project)}
                        className="rounded p-1 text-slate-400 hover:bg-slate-200 hover:text-slate-700 disabled:opacity-50 dark:hover:bg-slate-700 dark:hover:text-slate-200"
                      >
                        <PencilIcon className="h-3 w-3" />
                      </button>
                      <button
                        type="button"
                        aria-label="Borrar proyecto"
                        disabled={busy}
                        onClick={() => setConfirmingProjectDelete(project.id)}
                        className="rounded p-1 text-slate-400 hover:bg-rose-100 hover:text-rose-600 disabled:opacity-50 dark:hover:bg-rose-950/40 dark:hover:text-rose-400"
                      >
                        <TrashIcon className="h-3 w-3" />
                      </button>
                    </div>
                  )}
                </div>

                {isConfirmingDelete && (
                  <div className="ml-6 mb-1 rounded-md bg-rose-50 px-2 py-1.5 text-xs text-rose-700 dark:bg-rose-950/40 dark:text-rose-300">
                    <p className="mb-1.5">
                      ¿Borrar “{project.name}”?
                      {project.conversation_count > 0
                        ? ` Tiene ${project.conversation_count} conversación${project.conversation_count === 1 ? "" : "es"}.`
                        : ""}
                    </p>
                    <div className="flex flex-wrap gap-1.5">
                      <button
                        type="button"
                        onClick={() => {
                          onDeleteProject(project.id, "delete");
                          setConfirmingProjectDelete(null);
                        }}
                        className="rounded bg-rose-600 px-1.5 py-0.5 font-medium text-white hover:bg-rose-700"
                      >
                        Borrar todo
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          onDeleteProject(project.id, "keep");
                          setConfirmingProjectDelete(null);
                        }}
                        className="rounded border border-rose-300 px-1.5 py-0.5 font-medium hover:bg-rose-100 dark:border-rose-800 dark:hover:bg-rose-950"
                      >
                        Solo el proyecto
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirmingProjectDelete(null)}
                        className="rounded px-1.5 py-0.5 text-slate-500 hover:bg-rose-100 dark:text-slate-400 dark:hover:bg-rose-950"
                      >
                        Cancelar
                      </button>
                    </div>
                  </div>
                )}

                {isOpen && (
                  <div className="ml-3.5 border-l border-slate-100 pl-1.5 dark:border-slate-800">
                    {projectConversations.length === 0 ? (
                      <p className="px-1.5 py-1 text-[11px] text-slate-400">Sin conversaciones.</p>
                    ) : (
                      <ul className="space-y-0.5">
                        {projectConversations.map((conversation) => (
                          <ConversationRow
                            key={conversation.id}
                            conversation={conversation}
                            active={conversation.id === activeConversationId}
                            busy={busy}
                            editing={editing?.kind === "conversation" && editing.id === conversation.id}
                            confirmingDelete={confirmingConversationDelete === conversation.id}
                            draftValue={draftValue}
                            onDraftChange={setDraftValue}
                            onEditKeyDown={handleEditKeyDown}
                            onCommitEdit={commitEditing}
                            onSelect={() => onSelectConversation(conversation.id)}
                            onStartRename={() => startRenameConversation(conversation)}
                            onStartDelete={() => setConfirmingConversationDelete(conversation.id)}
                            onCancelDelete={() => setConfirmingConversationDelete(null)}
                            onConfirmDelete={() => {
                              onDeleteConversation(conversation.id);
                              setConfirmingConversationDelete(null);
                            }}
                          />
                        ))}
                      </ul>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>

        {conversationsByProject.unassigned.length > 0 && (
          <div className="mt-4">
            <p className="px-1 pb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
              Sin proyecto
            </p>
            <ul className="space-y-0.5">
              {conversationsByProject.unassigned.map((conversation) => (
                <ConversationRow
                  key={conversation.id}
                  conversation={conversation}
                  active={conversation.id === activeConversationId}
                  busy={busy}
                  editing={editing?.kind === "conversation" && editing.id === conversation.id}
                  confirmingDelete={confirmingConversationDelete === conversation.id}
                  draftValue={draftValue}
                  onDraftChange={setDraftValue}
                  onEditKeyDown={handleEditKeyDown}
                  onCommitEdit={commitEditing}
                  onSelect={() => onSelectConversation(conversation.id)}
                  onStartRename={() => startRenameConversation(conversation)}
                  onStartDelete={() => setConfirmingConversationDelete(conversation.id)}
                  onCancelDelete={() => setConfirmingConversationDelete(null)}
                  onConfirmDelete={() => {
                    onDeleteConversation(conversation.id);
                    setConfirmingConversationDelete(null);
                  }}
                />
              ))}
            </ul>
          </div>
        )}
      </nav>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Fila de conversación — compartida entre "dentro de un proyecto" y "sin proyecto"
// ---------------------------------------------------------------------------

function ConversationRow({
  conversation,
  active,
  busy,
  editing,
  confirmingDelete,
  draftValue,
  onDraftChange,
  onEditKeyDown,
  onCommitEdit,
  onSelect,
  onStartRename,
  onStartDelete,
  onCancelDelete,
  onConfirmDelete,
}: {
  conversation: IdeConversationSummary;
  active: boolean;
  busy: boolean;
  editing: boolean;
  confirmingDelete: boolean;
  draftValue: string;
  onDraftChange: (value: string) => void;
  onEditKeyDown: (event: KeyboardEvent<HTMLInputElement>) => void;
  onCommitEdit: () => void;
  onSelect: () => void;
  onStartRename: () => void;
  onStartDelete: () => void;
  onCancelDelete: () => void;
  onConfirmDelete: () => void;
}) {
  const dotClass = statusDotClass(conversation.last_session_status);

  return (
    <li>
      <div
        className={cx(
          "group flex items-center gap-1 rounded-md px-1.5 py-1",
          active
            ? "bg-brand-50 text-brand-700 dark:bg-brand-900/40 dark:text-brand-300"
            : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800",
        )}
      >
        {editing ? (
          <input
            autoFocus
            value={draftValue}
            onChange={(event) => onDraftChange(event.target.value)}
            onKeyDown={onEditKeyDown}
            onBlur={onCommitEdit}
            maxLength={160}
            className="min-w-0 flex-1 rounded border border-brand-300 bg-white px-1 py-0.5 text-xs text-slate-900 focus:outline-none dark:border-brand-700 dark:bg-slate-800 dark:text-slate-100"
          />
        ) : (
          <button
            type="button"
            onClick={onSelect}
            title={conversation.title}
            className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
          >
            <ChatIcon className="h-3.5 w-3.5 shrink-0 text-slate-400" />
            <span className="truncate text-xs">{conversation.title}</span>
            {dotClass && <span className={cx("h-1.5 w-1.5 shrink-0 rounded-full", dotClass)} aria-hidden="true" />}
          </button>
        )}

        {!editing && !confirmingDelete && (
          <div className="flex shrink-0 items-center gap-0.5 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100">
            <button
              type="button"
              aria-label="Renombrar conversación"
              disabled={busy}
              onClick={onStartRename}
              className="rounded p-1 text-slate-400 hover:bg-slate-200 hover:text-slate-700 disabled:opacity-50 dark:hover:bg-slate-700 dark:hover:text-slate-200"
            >
              <PencilIcon className="h-3 w-3" />
            </button>
            <button
              type="button"
              aria-label="Borrar conversación"
              disabled={busy}
              onClick={onStartDelete}
              className="rounded p-1 text-slate-400 hover:bg-rose-100 hover:text-rose-600 disabled:opacity-50 dark:hover:bg-rose-950/40 dark:hover:text-rose-400"
            >
              <TrashIcon className="h-3 w-3" />
            </button>
          </div>
        )}
      </div>

      {confirmingDelete && (
        <div className="ml-1.5 mb-1 flex items-center gap-1.5 rounded-md bg-rose-50 px-2 py-1 text-xs text-rose-700 dark:bg-rose-950/40 dark:text-rose-300">
          <span className="flex-1">¿Borrar esta conversación?</span>
          <button
            type="button"
            aria-label="Confirmar borrado"
            onClick={onConfirmDelete}
            className="rounded p-0.5 hover:bg-rose-200 dark:hover:bg-rose-900"
          >
            <CheckIcon className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            aria-label="Cancelar borrado"
            onClick={onCancelDelete}
            className="rounded p-0.5 hover:bg-rose-200 dark:hover:bg-rose-900"
          >
            <XIcon className="h-3.5 w-3.5" />
          </button>
        </div>
      )}
    </li>
  );
}
