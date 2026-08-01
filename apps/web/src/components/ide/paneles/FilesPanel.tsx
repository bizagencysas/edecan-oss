"use client";

/**
 * Explorador + editor de un workspace. Vive dentro del Estado 3
 * (`components/ide/estado/Editor.tsx`, §3.1 de la especificación): "Explorador
 * a la izquierda, editor al centro". No trae su propio estado de red -- todo
 * llega por props desde `useIdeEstado` (`components/ide/estado-ide.ts`).
 *
 * Antes de este cambio esto era un `<textarea>` desnudo sin números de línea
 * ni árbol de verdad (una lista plana con "←" para subir de carpeta). Ya
 * existían dos componentes reales sin conectar -- `FileTree.tsx` (árbol
 * perezoso, expandible) y `CodeEditor.tsx` (números de línea + edición
 * inline con ⌘K) -- y este archivo es justo el que los conecta.
 *
 * `FileTree` quedó desactualizado frente al contrato de workspaces múltiples
 * (llamaba a `GET /v1/ide/tree`, sin `workspaceId`, un sandbox global que ya
 * no es el que usa el resto del IDE) -- se corrigió ahí mismo para tomar
 * `workspaceId` y usar `getIdeWorkspaceTree`.
 *
 * `onInlineEdit` de `CodeEditor` se deja SIN CABLEAR a propósito: existe
 * `ide_edicion_inline.py` en el companion (preparar/pedir/aplicar la
 * propuesta, con sus invariantes de rango probadas), pero ese módulo no lo
 * importa ni `ide_runtime.py` ni `routers/ide.py` -- no hay ruta HTTP que
 * pueda llamar este archivo. Sin esa prop, `CodeEditor` simplemente no ofrece
 * ⌘K (documentado en su propio contrato de props); inventar aquí una llamada
 * a un endpoint que no existe sería justo el dato simulado que la regla 7
 * del encargo prohíbe. Queda declarado, no fingido.
 */

import { useState } from "react";

import { CodeEditor } from "@/components/ide/CodeEditor";
import { FileTree } from "@/components/ide/FileTree";
import { SearchPanel } from "@/components/ide/SearchPanel";

export function FilesPanel({
  workspaceId,
  workspacePath,
  selectedFile,
  content,
  dirty,
  onOpen,
  onContent,
  onSave,
}: {
  workspaceId: string;
  /** Raíz absoluta del workspace (`IdeWorkspace.path`) -- la necesita el tab
   * "Símbolos" de `SearchPanel` para convertir la `uri` (`file://...`) que
   * devuelve el LSP en una ruta relativa que `onOpen` sepa abrir. `null`
   * cuando `estado.workspace` todavía no cargó. */
  workspacePath: string | null;
  selectedFile: string | null;
  content: string;
  dirty: boolean;
  /** Devuelve la promesa de `estado.openFile`: este panel la usa para saber cuándo terminó de cargar. */
  onOpen: (path: string) => Promise<void>;
  onContent: (content: string) => void;
  /** Devuelve la promesa de `estado.saveFile`: este panel la usa para el spinner de Guardar. */
  onSave: () => Promise<void>;
}) {
  // 2.2 del plan de paridad: el árbol de archivos y el panel de búsqueda
  // (texto/significado) comparten la misma columna lateral -- una persona
  // sabe el nombre de lo que busca (árbol) o no (búsqueda), rara vez las dos
  // cosas a la vez.
  const [asideView, setAsideView] = useState<"tree" | "search">("tree");
  // `useIdeEstado` no distingue "cargando archivo" / "guardando" de su `busy`
  // general (compartido con acciones que no tienen nada que ver, como abrir
  // una terminal): se rastrea aquí, local a este panel, envolviendo las
  // promesas que ya devuelven `onOpen`/`onSave`.
  const [loadingFile, setLoadingFile] = useState(false);
  const [savingFile, setSavingFile] = useState(false);

  async function handleOpen(path: string) {
    setLoadingFile(true);
    try {
      await onOpen(path);
    } finally {
      setLoadingFile(false);
    }
  }

  async function handleSave() {
    setSavingFile(true);
    try {
      await onSave();
    } finally {
      setSavingFile(false);
    }
  }

  return (
    <section className="grid min-h-0 flex-1 grid-cols-[17rem_minmax(0,1fr)] bg-forja-superficie dark:bg-forja-superficie-oscura">
      <aside className="min-h-0 overflow-y-auto border-r border-forja-borde-suave bg-forja-superficie p-3 dark:border-forja-borde-oscuro-suave dark:bg-forja-superficie-oscura">
        <div className="mb-3 flex gap-1 text-xs">
          {([
            ["tree", "Archivos"],
            ["search", "Buscar"],
          ] as const).map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => setAsideView(value)}
              className={`rounded-md px-2.5 py-1 font-semibold ${
                asideView === value
                  ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900"
                  : "text-slate-500 hover:bg-forja-superficie-elevada dark:text-slate-400 dark:hover:bg-slate-800"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        {asideView === "search" ? (
          <SearchPanel
            workspaceId={workspaceId}
            workspacePath={workspacePath}
            onOpenFile={(path) => void handleOpen(path)}
          />
        ) : (
          <FileTree workspaceId={workspaceId} activePath={selectedFile} onOpenFile={(path) => void handleOpen(path)} />
        )}
      </aside>
      <div className="flex min-h-0 min-w-0 flex-col p-3">
        <CodeEditor
          path={selectedFile}
          content={content}
          dirty={dirty}
          loading={loadingFile}
          saving={savingFile}
          // El error de abrir/guardar ya se muestra como aviso global (`page.tsx`
          // lee `estado.error`, que `openFile`/`saveFile` alimentan) -- duplicarlo
          // aquí sería el mismo mensaje dos veces en pantalla.
          error={null}
          onChange={onContent}
          onSave={() => void handleSave()}
        />
      </div>
    </section>
  );
}
