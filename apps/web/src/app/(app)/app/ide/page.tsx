"use client";

/**
 * Forge Studio (el IDE de agentes de Edecán) -- cascarón delgado.
 *
 * Toda la lógica vive en `components/ide/estado-ide.ts` (`useIdeEstado`) y las
 * tres vistas de §3.1 de la especificación viven en `components/ide/estado/`.
 * Este archivo solo decide QUÉ estado pintar (`estado.vista`, derivado del
 * trabajo -- no una pestaña que la persona elige) y monta el andamiaje que no
 * cambia entre estados: la barra lateral de proyectos y los modales globales.
 *
 * Sin barra de menús propia, sin cinta de iconos, sin selector "Agente |
 * Código | Terminal": ver §3.1 y §8 del documento de construcción.
 */

import Link from "next/link";
import { useMemo } from "react";

import { AgentActivityCenter, tonoDe } from "@/components/ide/AgentActivityCenter";
import { PanelRedimensionable, PanelTab } from "@/components/ide/PanelRedimensionable";
import {
  CommandConfirmModal,
  CommandHelpModal,
  EmptyWorkspace,
  Reposo,
  WorkspaceModal,
} from "@/components/ide/estado/Reposo";
import { Trabajando } from "@/components/ide/estado/Trabajando";
import { Editor } from "@/components/ide/estado/Editor";
import { useIdeEstado } from "@/components/ide/estado-ide";
import { BrainIcon, SettingsIcon } from "@/components/icons";
import { MemoryKnowledgePanel } from "@/components/ide/MemoryKnowledgePanel";
import { ProjectSidebar } from "@/components/ide/ProjectSidebar";
import { Alert } from "@/components/ui";
import { XIcon } from "@/components/icons";

export default function IdePage() {
  const estado = useIdeEstado();
  // Bare bindings a propósito para las tres acciones de la torre de control:
  // no cambian nada del comportamiento (son las mismas funciones de
  // `useIdeEstado`), solo evitan el prefijo `estado.` en el sitio donde se
  // conectan al panel.
  const { goToRun, stopRun, directRun } = estado;

  // Cuántas ejecuciones "te esperan" (plan_pending, la única que reclama una
  // decisión -- ver `AgentActivityCenter.tonoDe`). Encargo: cuando el panel
  // se cierra esa información "se pierde entera y es justo cuando más falta
  // hace" -- por eso se calcula acá también, fuera del panel, para la
  // pestaña de reapertura y la entrada de la barra lateral.
  const waitingRunCount = useMemo(
    () => estado.activityRuns.filter((run) => tonoDe(run) === "esperando").length,
    [estado.activityRuns],
  );

  return (
    <div className="-m-6 h-[calc(100vh-3.5rem)] min-h-[44rem] overflow-hidden bg-forja-superficie text-slate-950 dark:bg-forja-superficie-oscura dark:text-slate-50">
      <div className="flex h-full">
        <div className="hidden lg:flex lg:h-full">
          {/* Ancho arrastrable (encargo: "que esas dos ventanas ... se
              puedan encoger con el mouse"); el default 336px = el `21rem`
              fijo de antes, para que nadie note el cambio hasta que
              arrastre. */}
          <PanelRedimensionable
            lado="izquierda"
            nombre="proyectos"
            etiqueta="la barra de proyectos"
            anchoPorDefecto={336}
          >
            <div className="flex h-full flex-col">
              <div className="min-h-0 flex-1">
                <ProjectSidebar
                  projects={estado.projects}
                  conversations={estado.conversationSummaries}
                  activeConversationId={estado.activeConversationId}
                  workspaces={estado.workspaceOptions}
                  onWorkspacesChange={estado.syncWorkspaces}
                  busy={estado.busy}
                  onSelectConversation={(id) => void estado.selectConversation(id)}
                  onCreateConversation={(projectId) => void estado.createConversation(projectId)}
                  onRenameConversation={(id, title) => void estado.renameConversation(id, title)}
                  onDeleteConversation={(id) => void estado.deleteConversation(id)}
                  onCreateProject={(input) => void estado.createProject(input)}
                  onRenameProject={(id, name) => void estado.renameProject(id, name)}
                  onDeleteProject={(id, mode) => void estado.deleteProject(id, mode)}
                />
              </div>
              {/* «Ejecuciones» y «Memoria» son accesos discretos de la barra
                  lateral (encargo §2): ya no viven en una cinta de botones
                  arriba del contenido. El estado de la carpeta activa (nombre,
                  rama, si el Mac está emparejado) va en la misma línea discreta
                  en vez de una barra de encabezado propia. */}
              <div className="shrink-0 space-y-1 border-t border-forja-borde-suave bg-forja-superficie p-3 dark:border-forja-borde-oscuro-suave dark:bg-forja-superficie-oscura">
                <p className="truncate px-3 text-[11px] text-slate-400 dark:text-slate-500">
                  <span
                    className={`mr-1.5 inline-block h-1.5 w-1.5 rounded-full ${
                      estado.connected ? "bg-emerald-500" : "bg-slate-300 dark:bg-slate-600"
                    }`}
                    aria-hidden="true"
                  />
                  {estado.connected ? "Mac conectado" : "Sin Mac"}
                  {estado.workspace ? ` · ${estado.workspace.name}` : ""}
                  {estado.git?.branch ? ` · ${estado.git.branch}` : ""}
                </p>
                {/* Encargo: "no ser un botón mudo" -- ahora refleja abierto/
                    cerrado con su propio fondo (no solo `aria-pressed`, que
                    no se ve) y prioriza "te espera" sobre "corriendo" porque
                    es lo que de verdad reclama una decisión. */}
                <button
                  type="button"
                  onClick={() => estado.setActivityOpen((value) => !value)}
                  aria-pressed={estado.activityOpen}
                  className={`flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors ${
                    estado.activityOpen
                      ? "bg-forja-superficie-elevada text-slate-900 dark:bg-slate-800 dark:text-slate-100"
                      : "text-slate-500 hover:bg-forja-superficie-elevada dark:text-slate-400 dark:hover:bg-slate-800"
                  }`}
                >
                  <span
                    className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                      waitingRunCount > 0
                        ? "bg-brand-500"
                        : estado.liveRunCount > 0
                          ? "animate-pulse bg-amber-400"
                          : "bg-slate-300 dark:bg-slate-600"
                    }`}
                    aria-hidden="true"
                  />
                  <span className="truncate">
                    {waitingRunCount > 0
                      ? `${waitingRunCount} te espera${waitingRunCount === 1 ? "" : "n"}`
                      : estado.liveRunCount > 0
                        ? `${estado.liveRunCount} corriendo`
                        : "Ejecuciones"}
                  </span>
                </button>
                <button
                  type="button"
                  disabled={!estado.workspace}
                  onClick={() => estado.setMemoryPanelOpen(true)}
                  className="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm text-slate-500 hover:bg-forja-superficie-elevada disabled:opacity-40 dark:text-slate-400 dark:hover:bg-slate-800"
                >
                  <BrainIcon className="h-4 w-4" /> Memoria y conocimiento
                </button>
                <Link
                  href="/app/ajustes"
                  className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm text-slate-500 hover:bg-forja-superficie-elevada dark:text-slate-400 dark:hover:bg-slate-800"
                >
                  <SettingsIcon className="h-4 w-4" /> Ajustes del estudio
                </Link>
              </div>
            </div>
          </PanelRedimensionable>
        </div>

        <main className="flex min-w-0 flex-1 flex-col overflow-hidden bg-forja-superficie dark:bg-forja-superficie-oscura">
          {estado.error && (
            <div className="px-4 pt-3">
              <Alert variant="error">
                <span className="flex items-start justify-between gap-3">
                  <span>{estado.error}</span>
                  <button type="button" onClick={() => estado.setError(null)} aria-label="Cerrar">
                    <XIcon className="h-4 w-4" />
                  </button>
                </span>
              </Alert>
            </div>
          )}

          {!estado.workspace ? (
            <EmptyWorkspace connected={Boolean(estado.connected)} onAdd={() => estado.setAddAction("folder")} />
          ) : estado.vista === "editor" ? (
            <Editor estado={estado} workspaceId={estado.workspace.id} />
          ) : estado.vista === "trabajando" ? (
            <Trabajando estado={estado} />
          ) : (
            <Reposo estado={estado} />
          )}
        </main>

        {/* En pantallas anchas la torre de control es una columna más: se ve
            al mismo tiempo que el hilo abierto ("sin perder de vista a los
            otros"). En pantallas chicas se comporta como cajón sobre el
            contenido. */}
        {estado.activityOpen ? (
          <>
            <button
              type="button"
              aria-label="Cerrar el panel de ejecuciones"
              onClick={() => estado.setActivityOpen(false)}
              className="fixed inset-0 z-[85] bg-slate-950/25 lg:hidden"
            />
            <div className="fixed inset-y-0 right-0 z-[86] w-full max-w-sm shadow-xl lg:static lg:z-auto lg:h-full lg:w-auto lg:max-w-none lg:shrink-0 lg:shadow-none">
              {/* `soloEscritorio`: en el cajón móvil (`max-w-sm` de arriba)
                  el panel siempre va a ancho completo -- el arrastre y el
                  colapso son un gesto de escritorio, no tienen sentido sobre
                  un cajón que ya se cierra tocando el fondo. */}
              <PanelRedimensionable
                lado="derecha"
                nombre="ejecuciones"
                etiqueta="el panel de ejecuciones"
                anchoPorDefecto={336}
                soloEscritorio
                className="h-full"
              >
                <AgentActivityCenter
                  runs={estado.activityRuns}
                  loading={estado.activityLoading}
                  onGo={(run) => void goToRun(run)}
                  onStop={(run) => void stopRun(run)}
                  onDirect={(run, texto) => void directRun(run, texto)}
                  onClose={() => estado.setActivityOpen(false)}
                />
              </PanelRedimensionable>
            </div>
          </>
        ) : (
          /* Encargo: al cerrar el panel "desaparece todo, hay que adivinar
             dónde darle click de nuevo". `PanelRedimensionable` ya resuelve
             esto para el colapso por arrastre con una pestaña fina en el
             borde -- acá se reutiliza el mismo `PanelTab` (no una pestaña
             inventada aparte) fijada al borde derecho de la VENTANA, porque
             al cerrar del todo ni siquiera se monta `PanelRedimensionable`.
             Sin `lg:` a propósito: es la única forma de volver a abrir el
             panel en pantallas chicas, donde la entrada de la barra lateral
             ni siquiera se monta. */
          <div className="fixed inset-y-0 right-0 z-40 h-full">
            <PanelTab
              lado="derecha"
              etiqueta={
                waitingRunCount > 0
                  ? `Abrir ejecuciones: ${waitingRunCount} te espera${waitingRunCount === 1 ? "" : "n"}`
                  : estado.liveRunCount > 0
                    ? `Abrir ejecuciones: ${estado.liveRunCount} corriendo`
                    : "Abrir el panel de ejecuciones"
              }
              onClick={() => estado.setActivityOpen(true)}
              className="border-y border-l border-forja-borde bg-forja-superficie shadow-panel dark:border-forja-borde-oscuro dark:bg-forja-superficie-oscura-elevada"
              indicador={
                waitingRunCount > 0 ? (
                  <span
                    aria-hidden="true"
                    className="absolute left-1/2 top-3 flex h-4 min-w-4 -translate-x-1/2 items-center justify-center rounded-full bg-brand-500 px-1 text-[9px] font-bold text-white"
                  >
                    {waitingRunCount}
                  </span>
                ) : estado.liveRunCount > 0 ? (
                  <span
                    aria-hidden="true"
                    className="absolute left-1/2 top-3 h-1.5 w-1.5 -translate-x-1/2 animate-pulse rounded-full bg-amber-400"
                  />
                ) : null
              }
            />
          </div>
        )}
      </div>

      {estado.addAction && (
        <WorkspaceModal
          kind={estado.addAction}
          busy={estado.busy}
          onClose={() => estado.setAddAction(null)}
          onAuthorize={(path, name) => void estado.authorizePath(path, name)}
          onPick={(name) => void estado.pickWorkspace(name)}
          onClone={(input) => void estado.cloneWorkspace(input)}
          onImage={estado.addImageAttachment}
        />
      )}

      {estado.memoryPanelOpen && estado.workspace && (
        <MemoryKnowledgePanel
          workspaceId={estado.workspace.id}
          workspaceName={estado.workspace.name}
          onClose={() => estado.setMemoryPanelOpen(false)}
        />
      )}

      {estado.pendingCommand && (
        <CommandConfirmModal
          comando={estado.pendingCommand.comando}
          mensaje={estado.pendingCommand.mensaje}
          busy={estado.commandBusy}
          onCancel={() => estado.setPendingCommand(null)}
          onConfirm={estado.confirmPendingCommand}
        />
      )}

      {estado.helpOpen && (
        <CommandHelpModal texto={estado.ideHelpText} onClose={() => estado.setHelpOpen(false)} />
      )}
    </div>
  );
}
