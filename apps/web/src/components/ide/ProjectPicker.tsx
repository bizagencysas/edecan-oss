"use client";

/**
 * Selector de carpeta para un proyecto de Forge Studio.
 *
 * Nace de una queja concreta del dueño: en `app/ide/page.tsx` el repo activo
 * vivía en un `<select>` suelto arriba de la pantalla ("la carpeta del repo
 * está arriba... y al tocarla se abre un dropdown, eso es feo"). La carpeta
 * no es un control global de la barra superior: es una PROPIEDAD del
 * proyecto, así que este componente se usa dentro del formulario de
 * crear/editar proyecto (donde sea que viva ese formulario), no flotando
 * sobre el chat.
 *
 * Reusa exactamente la misma vía que ya autoriza carpetas en `page.tsx`
 * (`createIdeWorkspace` / `pickIdeWorkspace` vía Finder, o el selector nativo
 * de Tauri cuando corre como app de escritorio) — no se inventó un backend
 * nuevo. El único contrato con quien lo use es `value` (el workspace elegido
 * hoy, o `null` si el proyecto todavía no tiene carpeta) y `onChange`.
 *
 * Deliberadamente NO es un `<select>` ni un menú flotando con position
 * absolute: es una lista inline que empuja el layout, para no repetir el
 * mismo "dropdown feo" con otro nombre.
 *
 * Conectado en dos sitios ("El repositorio activo NO se ve" / "Conectar
 * ProjectPicker"): el formulario de crear proyecto de `ProjectSidebar.tsx`
 * (uso normal, `autoOpen` en false) y `WorkspacePill.tsx`, que lo embebe
 * dentro de su propio menú flotante -- ahí `autoOpen` se salta el botón
 * "cerrado" (el trigger ya es la pastilla, no hace falta uno adentro) y
 * `onRequestClose` avisa al menú flotante cuándo debe cerrarse él mismo.
 */

import { useEffect, useMemo, useState } from "react";

import { CheckIcon, FileIcon, PlusIcon } from "@/components/icons";
import { Spinner } from "@/components/ui";
import {
  createIdeWorkspace,
  getIdeWorkspaces,
  pickIdeWorkspace,
  type IdeWorkspace,
} from "@/lib/api-ide";
import { isTauriApp, tauriInvoke } from "@/lib/tauriListen";

type Mode = "closed" | "list" | "new";

export interface ProjectPickerProps {
  /** Carpeta asignada hoy al proyecto (`null` si aún no tiene una). */
  value: IdeWorkspace | null;
  /** Se dispara al elegir una carpeta ya autorizada o al autorizar una nueva. */
  onChange: (workspace: IdeWorkspace) => void;
  /**
   * Lista de carpetas ya autorizadas, si quien la usa ya la tiene cargada
   * (por ejemplo `page.tsx`). Si se omite, el propio componente la pide con
   * `getIdeWorkspaces()` la primera vez que se abre la lista.
   */
  workspaces?: IdeWorkspace[];
  /** Avisa al padre cuando la lista cambia (nueva carpeta autorizada), para que mantenga su propia caché sincronizada. */
  onWorkspacesChange?: (workspaces: IdeWorkspace[]) => void;
  /**
   * Arranca directo en la lista (salta el botón "cerrado" que muestra el
   * valor actual). Para cuando quien lo usa ya provee su propio disparador
   * -- p. ej. `WorkspacePill`, que abre un menú flotante y no necesita un
   * segundo botón adentro para volver a abrir lo que ya está abierto.
   */
  autoOpen?: boolean;
  /** Se dispara cuando el picker vuelve a "cerrado" (eligió algo o canceló). Con `autoOpen`, el padre usa esto para cerrar su propio menú flotante. */
  onRequestClose?: () => void;
  disabled?: boolean;
  label?: string;
  className?: string;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

export function ProjectPicker({
  value,
  onChange,
  workspaces: workspacesProp,
  onWorkspacesChange,
  autoOpen = false,
  onRequestClose,
  disabled = false,
  label = "Carpeta del proyecto",
  className = "",
}: ProjectPickerProps) {
  const [workspaces, setWorkspaces] = useState<IdeWorkspace[]>(workspacesProp ?? []);
  const [mode, setMode] = useState<Mode>(autoOpen ? "list" : "closed");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [manualPath, setManualPath] = useState("");
  const [showManualPath, setShowManualPath] = useState(false);

  // `autoOpen` arranca en "list" y la carga no pasa por `openList()` (nadie
  // hace clic en el botón "cerrado" que no se renderiza en este modo), así
  // que hay que pedirla una vez al montar.
  useEffect(() => {
    if (autoOpen) void loadWorkspaces();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- una vez al montar, no en cada repintado.
  }, []);

  function requestClose() {
    setMode("closed");
    onRequestClose?.();
  }

  // Si el padre administra su propia lista (page.tsx ya la tiene en memoria),
  // la reflejamos aquí para no pedirla dos veces al backend.
  useEffect(() => {
    if (workspacesProp) setWorkspaces(workspacesProp);
  }, [workspacesProp]);

  async function loadWorkspaces() {
    if (workspacesProp) return; // el padre ya la trae; no duplicamos la llamada
    setLoading(true);
    setError(null);
    try {
      const rows = await getIdeWorkspaces();
      setWorkspaces(rows);
      onWorkspacesChange?.(rows);
    } catch (loadError) {
      setError(errorMessage(loadError, "No se pudieron cargar las carpetas autorizadas."));
    } finally {
      setLoading(false);
    }
  }

  function openList() {
    if (disabled) return;
    setMode("list");
    setError(null);
    void loadWorkspaces();
  }

  function selectWorkspace(row: IdeWorkspace) {
    onChange(row);
    requestClose();
  }

  function applyAuthorized(created: IdeWorkspace) {
    setWorkspaces((rows) => {
      const next = [created, ...rows.filter((row) => row.id !== created.id)];
      onWorkspacesChange?.(next);
      return next;
    });
    onChange(created);
    requestClose();
    setNewName("");
    setManualPath("");
    setShowManualPath(false);
  }

  async function authorize(path: string, name?: string) {
    setBusy(true);
    setError(null);
    try {
      applyAuthorized(await createIdeWorkspace(path, name));
    } catch (authorizeError) {
      setError(errorMessage(authorizeError, "No se pudo autorizar la carpeta."));
    } finally {
      setBusy(false);
    }
  }

  async function pickFromFinder() {
    setBusy(true);
    setError(null);
    try {
      const name = newName.trim() || undefined;
      // App de escritorio (Tauri): selector nativo del Mac. Web: el backend
      // abre Finder del lado del companion emparejado (misma ruta que ya
      // usa `page.tsx` en `pickWorkspace`).
      if (isTauriApp()) {
        const path = await tauriInvoke<string | null>("pick_workspace_folder");
        if (!path) return;
        await authorize(path, name);
        return;
      }
      const result = await pickIdeWorkspace(name);
      if (result.cancelled || !result.workspace) return;
      applyAuthorized(result.workspace);
    } catch (pickError) {
      setError(errorMessage(pickError, "No se pudo abrir el selector de carpetas."));
    } finally {
      setBusy(false);
    }
  }

  const sorted = useMemo(
    () => [...workspaces].sort((a, b) => a.name.localeCompare(b.name, "es")),
    [workspaces],
  );

  return (
    <div className={`text-sm ${className}`}>
      <p className="text-[11px] font-semibold uppercase tracking-[.14em] text-slate-400 dark:text-slate-500">
        {label}
      </p>

      {mode === "closed" && !autoOpen && (
        <button
          type="button"
          disabled={disabled}
          onClick={openList}
          className="mt-2 flex w-full items-center justify-between gap-3 rounded-lg py-2 text-left transition hover:bg-forja-superficie-elevada disabled:cursor-not-allowed disabled:opacity-50 dark:hover:bg-slate-800"
        >
          <span className="flex min-w-0 items-center gap-3">
            <FileIcon className="h-4 w-4 shrink-0 text-slate-400 dark:text-slate-500" />
            <span className="min-w-0">
              {value ? (
                <>
                  <span className="block truncate font-semibold text-slate-900 dark:text-slate-100">
                    {value.name}
                  </span>
                  <span className="block truncate text-xs text-slate-400 dark:text-slate-500">{value.path}</span>
                </>
              ) : (
                <span className="text-slate-400 dark:text-slate-500">Elige una carpeta del Mac…</span>
              )}
            </span>
          </span>
          <span className="shrink-0 text-xs font-semibold text-slate-400 dark:text-slate-500">Cambiar</span>
        </button>
      )}

      {mode === "list" && (
        <div className="mt-2 space-y-0.5">
          {loading && (
            <p className="flex items-center gap-2 px-1 py-2 text-xs text-slate-400 dark:text-slate-500">
              <Spinner className="h-3.5 w-3.5" /> Cargando carpetas…
            </p>
          )}
          {!loading && !sorted.length && (
            <p className="px-1 py-2 text-xs text-slate-400 dark:text-slate-500">
              Aún no autorizas ninguna carpeta en este Mac.
            </p>
          )}
          {sorted.map((row) => {
            const disponible = row.available !== false;
            return (
              <button
                key={row.id}
                type="button"
                disabled={!disponible}
                onClick={() => selectWorkspace(row)}
                className="flex w-full items-center gap-3 rounded-lg px-1 py-2 text-left transition hover:bg-forja-superficie-elevada disabled:cursor-not-allowed disabled:opacity-50 dark:hover:bg-slate-800"
              >
                <FileIcon className="h-4 w-4 shrink-0 text-slate-400 dark:text-slate-500" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-semibold text-slate-900 dark:text-slate-100">
                    {row.name}
                    {!disponible && (
                      <span className="ml-1.5 font-normal italic text-amber-600 dark:text-amber-400">
                        no disponible
                      </span>
                    )}
                  </span>
                  <span className="block truncate text-xs text-slate-400 dark:text-slate-500">{row.path}</span>
                </span>
                {row.id === value?.id && (
                  <CheckIcon className="h-4 w-4 shrink-0 text-slate-900 dark:text-slate-100" />
                )}
              </button>
            );
          })}

          <button
            type="button"
            onClick={() => setMode("new")}
            className="flex w-full items-center gap-3 rounded-lg px-1 py-2 text-left text-slate-500 transition hover:bg-forja-superficie-elevada dark:text-slate-400 dark:hover:bg-slate-800"
          >
            <PlusIcon className="h-4 w-4 shrink-0" />
            <span className="text-sm font-semibold">Autorizar una carpeta nueva</span>
          </button>

          <button
            type="button"
            onClick={requestClose}
            className="mt-1 px-1 text-xs font-semibold text-slate-400 hover:text-slate-600 dark:text-slate-500 dark:hover:text-slate-300"
          >
            Cancelar
          </button>
        </div>
      )}

      {mode === "new" && (
        <div className="mt-2 space-y-3">
          <label className="block">
            <span className="text-xs font-semibold text-slate-500 dark:text-slate-400">Nombre opcional</span>
            <input
              autoFocus
              value={newName}
              onChange={(event) => setNewName(event.target.value)}
              placeholder="Mi proyecto"
              className="mt-1 w-full rounded-lg border border-forja-borde bg-forja-superficie px-3 py-2.5 text-sm outline-none focus:border-slate-500 dark:border-forja-borde-oscuro dark:bg-forja-superficie-oscura dark:text-slate-100"
            />
          </label>

          <button
            type="button"
            disabled={busy}
            onClick={() => void pickFromFinder()}
            className="w-full rounded-lg bg-slate-950 px-4 py-2.5 text-sm font-semibold text-white transition disabled:opacity-40 dark:bg-slate-100 dark:text-slate-900"
          >
            Abrir Finder
          </button>

          <button
            type="button"
            onClick={() => setShowManualPath((prev) => !prev)}
            className="text-xs font-semibold text-slate-400 hover:text-slate-600 dark:text-slate-500 dark:hover:text-slate-300"
          >
            {showManualPath ? "Ocultar ruta manual" : "Escribir una ruta manualmente"}
          </button>

          {showManualPath && (
            <div className="flex gap-2">
              <input
                autoFocus
                value={manualPath}
                onChange={(event) => setManualPath(event.target.value)}
                placeholder="/Users/tu-usuario/Projects/mi-app"
                className="min-w-0 flex-1 rounded-lg border border-forja-borde bg-forja-superficie px-3 py-2.5 text-sm outline-none focus:border-slate-500 dark:border-forja-borde-oscuro dark:bg-forja-superficie-oscura dark:text-slate-100"
              />
              <button
                type="button"
                disabled={!manualPath.trim() || busy}
                onClick={() => void authorize(manualPath.trim(), newName.trim() || undefined)}
                className="shrink-0 rounded-lg bg-slate-950 px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-40 dark:bg-slate-100 dark:text-slate-900"
              >
                Autorizar
              </button>
            </div>
          )}

          <button
            type="button"
            onClick={() => setMode("list")}
            className="text-xs font-semibold text-slate-400 hover:text-slate-600 dark:text-slate-500 dark:hover:text-slate-300"
          >
            Volver a la lista
          </button>
        </div>
      )}

      {error && <p className="mt-2 text-xs text-red-600 dark:text-red-400">{error}</p>}
    </div>
  );
}

export default ProjectPicker;
