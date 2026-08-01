"use client";

/**
 * Estado 3 de §3.1: el Editor. "Explorador a la izquierda, editor al centro,
 * terminal abajo, agente a la derecha. Se entra desde un diff o desde el
 * árbol, nunca es la pantalla de inicio."
 *
 * La terminal se pinta plegada debajo del editor (no en una pestaña propia:
 * eso es justo lo que el encargo pide eliminar) y se expande con un gesto
 * explícito. El agente en curso, si lo hay, sigue visible como una columna
 * angosta a la derecha -- el trabajo no se detiene porque la persona se puso
 * a mirar un archivo.
 */

import { useState } from "react";

import AgentThread from "@/components/ide/AgentThread";
import type { IdeEstado } from "@/components/ide/estado-ide";
import { ChevronDownIcon, ChevronRightIcon, XIcon } from "@/components/icons";
import { FilesPanel } from "@/components/ide/paneles/FilesPanel";
import { TerminalPanel } from "@/components/ide/paneles/TerminalPanel";
import { ScopePill, WorkspacePill } from "@/components/ide/WorkspacePill";

export function Editor({ estado, workspaceId }: { estado: IdeEstado; workspaceId: string }) {
  const [terminalExpandido, setTerminalExpandido] = useState(true);

  return (
    <section className="flex min-h-0 flex-1 flex-col bg-forja-superficie dark:bg-forja-superficie-oscura">
      <div className="flex h-11 shrink-0 items-center gap-2 border-b border-forja-borde-suave bg-forja-superficie px-4 dark:border-forja-borde-oscuro-suave dark:bg-forja-superficie-oscura">
        <button
          type="button"
          onClick={estado.closeEditor}
          className="flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-semibold text-slate-500 hover:bg-forja-superficie-elevada dark:text-slate-400 dark:hover:bg-slate-800"
        >
          <XIcon className="h-3.5 w-3.5" /> Volver
        </button>
        <span className="min-w-0 flex-1 truncate text-xs text-slate-400 dark:text-slate-500">
          {estado.selectedFile ?? "Explorador"}
        </span>
        {/* Estado 3 de §3.1 también necesita el repositorio activo a la vista
            ("El repositorio activo NO se ve"): misma pastilla que Reposo y
            Trabajando, discreta al final de la cabecera. */}
        <div className="flex shrink-0 items-center gap-2">
          <WorkspacePill
            workspace={estado.workspace}
            workspaces={estado.workspaces}
            branch={estado.git?.branch}
            onSelectWorkspace={(next) => void estado.selectWorkspace(next)}
            onWorkspacesChange={estado.syncWorkspaces}
          />
          <ScopePill label="Local" />
        </div>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="flex min-h-0 min-w-0 flex-col">
          <div className="min-h-0 flex-1">
            <FilesPanel
              workspaceId={workspaceId}
              workspacePath={estado.workspace?.path ?? null}
              selectedFile={estado.selectedFile}
              content={estado.fileContent}
              dirty={estado.fileContent !== estado.savedContent}
              onOpen={estado.openFile}
              onContent={estado.setFileContent}
              onSave={estado.saveFile}
            />
          </div>

          <div className="shrink-0 border-t border-forja-borde-suave dark:border-forja-borde-oscuro-suave">
            <button
              type="button"
              onClick={() => setTerminalExpandido((value) => !value)}
              className="flex w-full items-center gap-1.5 bg-forja-superficie px-4 py-2 text-left text-xs font-semibold text-slate-500 hover:bg-forja-superficie-elevada dark:bg-forja-superficie-oscura dark:text-slate-400 dark:hover:bg-slate-800"
              aria-expanded={terminalExpandido}
            >
              {terminalExpandido ? (
                <ChevronDownIcon className="h-3.5 w-3.5" />
              ) : (
                <ChevronRightIcon className="h-3.5 w-3.5" />
              )}
              Terminal
              {estado.terminal && !terminalExpandido ? (
                <span className="ml-1 h-1.5 w-1.5 rounded-full bg-emerald-400" aria-hidden="true" />
              ) : null}
            </button>
            {/* Se mantiene SIEMPRE montado (solo cambia de alto con CSS) en vez
                de desmontarse con `terminalExpandido && ...`: TerminalPanel
                guarda una instancia real de xterm.js por sesión (scrollback
                incluido) y desmontar el componente la destruye por completo.
                Plegar/desplegar ya no debe borrar lo que corrió antes. */}
            <div className={terminalExpandido ? "h-64" : "h-0 overflow-hidden"} aria-hidden={!terminalExpandido}>
              <TerminalPanel
                terminals={estado.terminals}
                terminal={estado.terminal}
                output={estado.terminalText}
                input={estado.terminalInput}
                cursor={estado.terminalCursor}
                onSelect={estado.setTerminal}
                onCreate={() => void estado.startTerminal()}
                onInput={estado.setTerminalInput}
                onSend={() => void estado.submitTerminal()}
              />
            </div>
          </div>
        </div>

        {estado.agent && (
          <aside className="hidden min-h-0 overflow-y-auto border-l border-forja-borde-suave bg-forja-superficie p-4 lg:block dark:border-forja-borde-oscuro-suave dark:bg-forja-superficie-oscura">
            <p className="mb-3 text-xs font-semibold text-slate-400 dark:text-slate-500">
              {estado.agent.title || "Sesión de ingeniería"}
            </p>
            <AgentThread
              turns={estado.agentTurns}
              resolvedMcpCalls={estado.resolvedMcpCalls}
              onResolveMcp={(callId, approved) => void estado.resolveMcpCall(callId, approved)}
              className="gap-6"
            />
          </aside>
        )}
      </div>
    </section>
  );
}
