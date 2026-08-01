"use client";

/**
 * Estado 1 de §3.1: Reposo. "El IDE abre en un campo de texto, no en un árbol
 * de archivos (...) El compositor es el producto."
 *
 * Este archivo es dueño del compositor completo -- textarea, menús "@" y "/",
 * adjuntos, selector de modelo -- porque es el mismo componente que se ve
 * encogido arriba en Trabajando (`estado/Trabajando.tsx` lo importa de acá en
 * vez de duplicarlo). También trae los modales que el compositor puede abrir
 * desde cualquier estado (autorizar carpeta, confirmar comando destructivo,
 * ver `/help`): viven aquí y `page.tsx` los monta al nivel de la página para
 * que funcionen incluso antes de que exista un workspace.
 */

import type { ChangeEvent } from "react";
import { useState } from "react";

import type { IdeCommandMatch } from "@/components/ide/estado-ide";
import { fileToImageAttachment, type IdeEstado } from "@/components/ide/estado-ide";
import {
  ChevronDownIcon,
  CodeIcon,
  PlusIcon,
  SendIcon,
  SparklesIcon,
  SquareIcon,
  XIcon,
} from "@/components/icons";
import { MessageQueue } from "@/components/ide/MessageQueue";
import { SelectorModo } from "@/components/ide/SelectorModo";
import { ScopePill, WorkspacePill } from "@/components/ide/WorkspacePill";
import { Alert } from "@/components/ui";
import type { IdeAgentAttachment, IdeCommandResult, IdeReferenceMatch } from "@/lib/api-ide";
import { isLive } from "@/components/ide/estado-ide";

function referenceKindLabel(match: IdeReferenceMatch): string {
  if (match.type === "symbol") return match.symbol_kind === "class" ? "Clase" : "Función";
  return match.type === "folder" ? "Carpeta" : "Archivo";
}

function CommandMenu({
  matches,
  activeIndex,
  query,
  onHover,
  onSelect,
}: {
  matches: IdeCommandMatch[];
  activeIndex: number;
  query: string;
  onHover: (index: number) => void;
  onSelect: (match: IdeCommandMatch) => void;
}) {
  return (
    <div className="absolute bottom-full left-0 z-40 mb-2 max-h-80 w-96 overflow-y-auto rounded-lg border border-forja-borde bg-forja-superficie p-1.5 shadow-lg dark:border-forja-borde-oscuro dark:bg-forja-superficie-oscura-elevada">
      {matches.length === 0 ? (
        <p className="px-2.5 py-2 text-xs text-slate-400 dark:text-slate-500">
          {query ? `Ningún comando empieza con "/${query}".` : "No hay comandos cargados todavía."}
        </p>
      ) : (
        matches.map((match, index) => (
          <button
            key={`${match.spec.nombre}-${match.nombre}`}
            type="button"
            // onMouseDown (no onClick) para que dispare ANTES de que el
            // `onBlur` del textarea cierre el menú.
            onMouseDown={(event) => {
              event.preventDefault();
              onSelect(match);
            }}
            onMouseEnter={() => onHover(index)}
            className={`flex w-full items-start gap-2 rounded-md px-2.5 py-1.5 text-left text-xs ${
              index === activeIndex
                ? "bg-slate-100 dark:bg-slate-800"
                : "hover:bg-forja-superficie-elevada dark:hover:bg-slate-800/60"
            }`}
          >
            <span className="shrink-0 font-mono font-semibold text-slate-700 dark:text-slate-200">
              /{match.nombre}
            </span>
            <span className="truncate text-slate-500 dark:text-slate-400">{match.spec.descripcion}</span>
            {match.spec.destructivo && (
              <span className="ml-auto shrink-0 rounded bg-red-50 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-red-600 dark:bg-red-950/40 dark:text-red-300">
                Confirma
              </span>
            )}
          </button>
        ))
      )}
    </div>
  );
}

function MentionMenu({
  matches,
  activeIndex,
  onHover,
  onSelect,
}: {
  matches: IdeReferenceMatch[];
  activeIndex: number;
  onHover: (index: number) => void;
  onSelect: (match: IdeReferenceMatch) => void;
}) {
  return (
    <div className="absolute bottom-full left-0 z-40 mb-2 max-h-72 w-80 overflow-y-auto rounded-lg border border-forja-borde bg-forja-superficie p-1.5 shadow-lg dark:border-forja-borde-oscuro dark:bg-forja-superficie-oscura-elevada">
      {matches.map((match, index) => (
        <button
          key={`${match.type}-${match.path}-${match.line ?? ""}`}
          type="button"
          onMouseDown={(event) => {
            event.preventDefault();
            onSelect(match);
          }}
          onMouseEnter={() => onHover(index)}
          className={`flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-xs ${
            index === activeIndex
              ? "bg-slate-100 dark:bg-slate-800"
              : "hover:bg-forja-superficie-elevada dark:hover:bg-slate-800/60"
          }`}
        >
          <span className="truncate font-mono text-slate-700 dark:text-slate-200">{match.path}</span>
          <span className="ml-auto shrink-0 text-[10px] uppercase tracking-wide text-slate-400 dark:text-slate-500">
            {referenceKindLabel(match)}
          </span>
        </button>
      ))}
    </div>
  );
}

function CommandResultBanner({
  result,
  onDismiss,
  onUseSuggestion,
  onOpenHelp,
}: {
  result: IdeCommandResult;
  onDismiss: () => void;
  onUseSuggestion: (sugerencia: string) => void;
  onOpenHelp: () => void;
}) {
  // "/help" ya trae el texto completo del registro -- se muestra en el
  // modal dedicado en vez de aplastarlo dentro de este banner angosto.
  if (result.ok && result.comando === "help") {
    return (
      <div className="mb-2 flex items-center justify-between gap-2 border-t border-slate-200/70 px-1 pt-2 dark:border-slate-800">
        <Alert variant="info">Lista de comandos disponible.</Alert>
        <div className="flex shrink-0 gap-2">
          <button
            type="button"
            onClick={onOpenHelp}
            className="rounded-md border border-forja-borde px-2 py-1 text-xs font-semibold hover:bg-forja-superficie-elevada dark:border-forja-borde-oscuro dark:hover:bg-slate-800"
          >
            Ver /help
          </button>
          <button type="button" onClick={onDismiss} aria-label="Cerrar">
            <XIcon className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    );
  }

  const variant = result.ok ? (result.limitado ? "info" : "success") : "error";
  const mensaje = result.mensaje || result.error || "El comando no devolvió ningún mensaje.";

  return (
    <div className="mb-2 flex items-start justify-between gap-2 border-t border-slate-200/70 px-1 pt-2 dark:border-slate-800">
      <div className="min-w-0 flex-1">
        <Alert variant={variant}>{mensaje}</Alert>
        {!result.ok && result.sugerencia && (
          <button
            type="button"
            onClick={() => onUseSuggestion(result.sugerencia as string)}
            className="mt-1 text-xs font-semibold text-indigo-600 underline hover:text-indigo-800 dark:text-indigo-300"
          >
            Usar {result.sugerencia}
          </button>
        )}
        {result.copy_text && (
          <p className="mt-1 text-[11px] text-slate-400 dark:text-slate-500">Copiado al portapapeles.</p>
        )}
      </div>
      <button type="button" onClick={onDismiss} className="shrink-0" aria-label="Cerrar">
        <XIcon className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

function AddMenu({
  onClose,
  onAction,
}: {
  onClose: () => void;
  onAction: (action: "folder" | "clone" | "image" | "terminal" | "explorer") => void;
}) {
  return (
    <div className="absolute bottom-full left-0 z-40 mb-2 w-64 rounded-lg border border-forja-borde bg-forja-superficie p-2 shadow-lg dark:border-forja-borde-oscuro dark:bg-forja-superficie-oscura-elevada">
      {[
        ["folder", "Abrir carpeta del Mac", "Autoriza un workspace del Finder"],
        ["clone", "Clonar repositorio", "HTTPS o SSH a una carpeta local"],
        ["image", "Añadir imagen o archivo", "Contexto visual para el agente"],
        ["terminal", "Nueva terminal", "Shell real dentro del proyecto"],
        ["explorer", "Explorador de archivos", "Ver y editar el código del proyecto"],
      ].map(([action, title, subtitle]) => (
        <button
          key={action}
          type="button"
          onClick={() => onAction(action as "folder" | "clone" | "image" | "terminal" | "explorer")}
          className="w-full rounded-md px-3 py-2 text-left hover:bg-forja-superficie-hundida dark:hover:bg-slate-800"
        >
          <span className="block text-sm font-semibold dark:text-slate-100">{title}</span>
          <span className="block text-[11px] text-slate-400 dark:text-slate-500">{subtitle}</span>
        </button>
      ))}
      <button
        type="button"
        onClick={onClose}
        className="mt-1 w-full rounded-md px-3 py-2 text-xs text-slate-400 hover:bg-forja-superficie-hundida dark:text-slate-500 dark:hover:bg-slate-800"
      >
        Cerrar
      </button>
    </div>
  );
}

/** El compositor: único, reusado tal cual por Reposo y por Trabajando (§3.1:
 * "en cuanto hay trabajo en marcha, el compositor se encoge a una fila
 * arriba"; visualmente sigue siendo la misma pieza, no dos implementaciones). */
export function Composer({ estado }: { estado: IdeEstado }) {
  return (
    <div className="border-t border-forja-borde-suave bg-forja-superficie p-3 dark:border-forja-borde-oscuro-suave dark:bg-forja-superficie-oscura sm:p-4">
      <div className="mx-auto flex max-w-4xl flex-col items-center gap-1">
        {/* Pastilla de proyecto ENCIMA del compositor (§3.1: "▫ edecan ⌄"):
            mismo dato que antes solo vivía en la línea diminuta del pie de
            la barra lateral, ahora visible en los tres estados. */}
        <WorkspacePill
          workspace={estado.workspace}
          workspaces={estado.workspaces}
          branch={estado.git?.branch}
          onSelectWorkspace={(next) => void estado.selectWorkspace(next)}
          onWorkspacesChange={estado.syncWorkspaces}
        />
        <div
          className="relative w-full rounded-xl border border-forja-borde bg-forja-superficie p-3 dark:border-forja-borde-oscuro dark:bg-forja-superficie-oscura-elevada"
          onDragOver={(event) => {
            if (Array.from(event.dataTransfer.types).includes("Files")) {
              event.preventDefault();
            }
          }}
          onDrop={(event) => {
            const file = Array.from(event.dataTransfer.files).find((row) => row.type.startsWith("image/"));
            if (!file) return;
            event.preventDefault();
            void estado.handleImageFile(file);
          }}
        >
          <textarea
            ref={estado.promptFieldRef}
            value={estado.prompt}
            onChange={estado.handlePromptChange}
            onKeyDown={estado.handlePromptKeyDown}
            onPaste={(event) => {
              const item = Array.from(event.clipboardData.items).find((row) => row.type.startsWith("image/"));
              const file = item?.getAsFile();
              if (!file) return;
              event.preventDefault();
              void estado.handleImageFile(file);
            }}
            onBlur={() => {
              window.setTimeout(() => estado.setMentionOpen(false), 120);
              window.setTimeout(() => estado.setCommandMenuOpen(false), 120);
            }}
            placeholder={
              estado.agent && isLive(estado.agent)
                ? "Sigue dirigiendo: lo que escribas entra en la próxima vuelta, sin cortarle el trabajo…"
                : "Describe el resultado. Escribe @ para referenciar, / para un comando, o pega/arrastra una imagen…"
            }
            className="min-h-24 w-full resize-none bg-transparent px-2 py-1 text-sm leading-6 outline-none placeholder:text-slate-400 dark:text-slate-100 dark:placeholder:text-slate-500"
          />
          {estado.commandMenuOpen && (
            <CommandMenu
              matches={estado.commandMatches}
              activeIndex={estado.commandIndex}
              query={estado.commandQuery}
              onHover={estado.setCommandIndex}
              onSelect={estado.chooseCommandFromMenu}
            />
          )}
          {estado.mentionOpen && estado.mentionMatches.length > 0 && (
            <MentionMenu
              matches={estado.mentionMatches}
              activeIndex={estado.mentionIndex}
              onHover={estado.setMentionIndex}
              onSelect={estado.selectMention}
            />
          )}
          {estado.commandResult && (
            <CommandResultBanner
              result={estado.commandResult}
              onDismiss={() => estado.setCommandResult(null)}
              onUseSuggestion={(sugerencia) => {
                estado.setCommandResult(null);
                estado.setPrompt(`${sugerencia} `);
                requestAnimationFrame(() => estado.promptFieldRef.current?.focus());
              }}
              onOpenHelp={() => estado.setHelpOpen(true)}
            />
          )}
          {estado.promptReferences.length > 0 && (
            <div className="mb-2 flex flex-wrap gap-2 border-t border-slate-200/70 px-1 pt-2 dark:border-slate-800">
              {estado.promptReferences.map((reference) => (
                <span
                  key={reference.path}
                  className="flex max-w-52 items-center gap-1.5 rounded-md bg-indigo-50 px-2.5 py-1.5 text-xs text-indigo-700 dark:bg-indigo-950/40 dark:text-indigo-300"
                >
                  <span className="truncate font-mono">@{reference.name}</span>
                  <button
                    type="button"
                    onClick={() =>
                      estado.setPromptReferences((rows) => rows.filter((row) => row.path !== reference.path))
                    }
                    aria-label={`Quitar referencia ${reference.name}`}
                  >
                    <XIcon className="h-3.5 w-3.5" />
                  </button>
                </span>
              ))}
            </div>
          )}
          {estado.agentAttachments.length > 0 && (
            <div className="mb-2 flex flex-wrap gap-2 border-t border-slate-200/70 px-1 pt-2 dark:border-slate-800">
              {estado.agentAttachments.map((attachment, index) => (
                <span
                  key={`${attachment.name}-${index}`}
                  className="flex max-w-52 items-center gap-2 rounded-md bg-slate-100 px-2.5 py-1.5 text-xs text-slate-700 dark:bg-slate-800 dark:text-slate-200"
                >
                  <span className="truncate">{attachment.name}</span>
                  <button
                    type="button"
                    onClick={() => {
                      estado.setAgentAttachments((rows) => rows.filter((_, rowIndex) => rowIndex !== index));
                      estado.setImageNotice(null);
                    }}
                    aria-label={`Quitar ${attachment.name}`}
                  >
                    <XIcon className="h-3.5 w-3.5" />
                  </button>
                </span>
              ))}
            </div>
          )}
          {estado.imageNotice && (
            <p className="mb-2 border-t border-slate-200/70 px-1 pt-2 text-[11px] text-amber-600 dark:border-slate-800 dark:text-amber-400">
              {estado.imageNotice}
            </p>
          )}
          <MessageQueue
            mensajes={estado.composerQueue}
            onRecuperarTexto={(texto) => {
              estado.setPrompt((actual) => (actual.trim() ? `${actual}\n${texto}` : texto));
              requestAnimationFrame(() => estado.promptFieldRef.current?.focus());
            }}
          />
          {/* Degradación por prioridad (encargo: "que se adapte a cualquier
              ancho"): Enviar y "+" nunca se pierden; el selector de modelo
              siempre queda alcanzable (se acorta con elipsis en vez de
              salirse); Modo/Esfuerzo y el texto de ayuda son lo primero que
              cede espacio. `flex-wrap` + `min-w-0` en vez de `@container`
              porque `@tailwindcss/container-queries` no está instalado en
              este proyecto (Tailwind 3.4 con `plugins: []` -- ver
              `tailwind.config.ts`); con `flex-wrap` la fila cae a una segunda
              línea en vez de salirse del contenedor cuando el panel lateral
              redimensionable deja al compositor angosto aunque la ventana
              siga ancha. */}
          <div className="flex flex-wrap items-center gap-x-2 gap-y-2 border-t border-slate-200/70 pt-2 dark:border-slate-800">
            <div className="relative shrink-0">
              <button
                type="button"
                onClick={() => estado.setAddMenu((value) => !value)}
                className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
                aria-label="Añadir contexto"
              >
                <PlusIcon className="h-5 w-5" />
              </button>
              {estado.addMenu && (
                <AddMenu
                  onClose={() => estado.setAddMenu(false)}
                  onAction={(action) => {
                    estado.setAddMenu(false);
                    if (action === "terminal") void estado.startTerminal();
                    else if (action === "explorer") estado.openExplorer();
                    else estado.setAddAction(action);
                  }}
                />
              )}
            </div>
            <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-x-2 gap-y-2">
              <SelectorModo
                sessionId={estado.agent?.id ?? null}
                ocupado={Boolean(estado.agent && isLive(estado.agent))}
                className="shrink-0"
              />
              {/* Selector de modelo: SIEMPRE visible (antes se escondía entero
                  bajo `sm:`) -- si el nombre no cabe, se acorta con elipsis
                  (`truncate` sobre el propio `<select>`), nunca se sale del
                  contenedor. Ancho fijo por punto de corte en vez de
                  `min-w-[13rem]` fijo, que era lo que forzaba el desborde.
                  Antes ese ancho arrancaba en `w-20` (80px): con nombres
                  reales de Workers AI ("Kimi K2.7 Code", "Nemotron 3 120B")
                  eso truncaba SIEMPRE, ni siquiera en pantallas grandes --
                  "de un vistazo" no se cumplía porque no había vistazo que
                  mostrar. Los pasos de acá abajo alcanzan para el nombre
                  completo de los cinco modelos de `modelos_ide_disponibles()`
                  desde el primer breakpoint; `truncate` + `min-w-0` +
                  `flex-shrink` (heredado del contenedor `flex-wrap`) siguen
                  siendo la red de seguridad si el panel lateral deja menos
                  espacio del que estos anchos piden. `title` repite el
                  nombre completo (mismo patrón que el resto de esta fila)
                  para el caso límite en que sí se corta. */}
              <label className="relative min-w-0 shrink-0">
                <select
                  value={estado.ideModel}
                  onChange={(event) => estado.setIdeModel(event.target.value)}
                  disabled={!estado.ideModels.length}
                  title={estado.ideModels.find((option) => option.id === estado.ideModel)?.nombre ?? "Modelo de ingeniería"}
                  className="w-32 min-w-0 truncate appearance-none rounded-lg border border-forja-borde bg-forja-superficie py-2 pl-2.5 pr-7 text-xs font-semibold text-slate-700 outline-none focus:border-slate-500 dark:border-forja-borde-oscuro dark:bg-forja-superficie-oscura-elevada dark:text-slate-200 sm:w-44 md:w-52 lg:w-56"
                  aria-label="Modelo de Forge Studio"
                >
                  {estado.ideModels.length === 0 && <option value="">Modelo de ingeniería</option>}
                  {estado.ideModels.map((option) => (
                    <option key={option.id} value={option.id}>
                      {option.nombre}
                    </option>
                  ))}
                </select>
                <ChevronDownIcon className="pointer-events-none absolute right-1.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
              </label>
              {/* Lo primero que cede: el texto de ayuda, ya escondido bajo
                  `lg:` (viewport, no contenedor -- ver nota de arriba). */}
              <span className="hidden shrink-0 text-[11px] text-slate-400 dark:text-slate-500 lg:inline">
                Cambias el modelo, no el contexto.
              </span>
              {estado.agent && isLive(estado.agent) && (
                <button
                  type="button"
                  onClick={() => void estado.stopAgent()}
                  className="flex h-10 shrink-0 items-center gap-2 rounded-lg border border-red-200 bg-forja-superficie px-3 text-xs font-bold text-red-600 hover:bg-red-50 dark:border-red-900/60 dark:bg-transparent dark:text-red-400 dark:hover:bg-red-950/30"
                  aria-label="Detener trabajo"
                >
                  <SquareIcon className="h-4 w-4" />
                  <span className="hidden sm:inline">Detener</span>
                </button>
              )}
              <button
                type="button"
                disabled={!estado.prompt.trim() || estado.commandBusy}
                onClick={() => void estado.submitPrompt()}
                className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-slate-900 text-white hover:bg-slate-700 disabled:opacity-40 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-white"
                aria-label={estado.agent && isLive(estado.agent) ? "Mandar a la cola del agente" : "Iniciar trabajo"}
              >
                <SendIcon className="h-4 w-4" />
              </button>
            </div>
          </div>
        </div>
        {/* Pastilla de alcance DEBAJO del compositor (§3.1: "▫ Local"):
            informativa, no un desplegable -- hoy no hay más de un alcance
            posible (§3.5: `terminal: total` en el Mac emparejado). */}
        <ScopePill label="Local" />
      </div>
    </div>
  );
}

export function WorkspaceModal({
  kind,
  busy,
  onClose,
  onAuthorize,
  onPick,
  onClone,
  onImage,
}: {
  kind: "folder" | "clone" | "image";
  busy: boolean;
  onClose: () => void;
  onAuthorize: (path: string, name?: string) => void;
  onPick: (name?: string) => void;
  onClone: (input: { url: string; name?: string; branch?: string }) => void;
  onImage: (attachment: IdeAgentAttachment) => void;
}) {
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [branch, setBranch] = useState("");
  const [manualPath, setManualPath] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);
  const title =
    kind === "clone" ? "Clonar repositorio" : kind === "image" ? "Añadir contexto visual" : "Abrir carpeta del Mac";
  return (
    <div className="fixed inset-0 z-[100] grid place-items-center bg-slate-950/35 p-4" role="dialog" aria-modal="true">
      <section className="w-full max-w-lg rounded-xl border border-forja-borde bg-forja-superficie p-5 shadow-xl dark:border-forja-borde-oscuro dark:bg-forja-superficie-oscura-elevada">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-bold dark:text-slate-100">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-2 hover:bg-forja-superficie-hundida dark:hover:bg-slate-800"
          >
            <XIcon className="h-4 w-4" />
          </button>
        </div>
        {kind === "image" ? (
          <label className="mt-5 block cursor-pointer rounded-lg border border-dashed border-slate-300 bg-forja-superficie p-8 text-center transition hover:bg-forja-superficie-elevada dark:border-slate-700 dark:bg-forja-superficie-oscura-elevada dark:hover:bg-slate-800">
            <span className="font-semibold dark:text-slate-100">Elegir imagen</span>
            <span className="mt-2 block text-sm text-slate-500 dark:text-slate-400">
              PNG, JPEG, WebP o GIF. Se envía directamente al modelo con visión, no como texto.
            </span>
            <input
              type="file"
              accept="image/jpeg,image/png,image/webp,image/gif"
              className="sr-only"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (!file) return;
                void (async () => {
                  const result = await fileToImageAttachment(file);
                  if ("error" in result) {
                    setFileError(result.error);
                    return;
                  }
                  onImage(result.attachment);
                })();
              }}
            />
            {fileError && <span className="mt-3 block text-sm text-red-600 dark:text-red-400">{fileError}</span>}
          </label>
        ) : kind === "clone" ? (
          <>
            <p className="mt-2 text-sm leading-6 text-slate-500 dark:text-slate-400">
              Edecán clonará el repositorio dentro del proyecto actual, sin guardar tokens incrustados en la URL.
            </p>
            <label className="mt-5 block text-sm font-semibold dark:text-slate-200">URL del repositorio</label>
            <input
              autoFocus
              value={path}
              onChange={(event: ChangeEvent<HTMLInputElement>) => setPath(event.target.value)}
              placeholder="https://github.com/organizacion/proyecto.git"
              className="mt-2 w-full rounded-lg border border-forja-borde bg-forja-superficie px-3 py-3 text-sm outline-none focus:border-slate-500 dark:border-forja-borde-oscuro dark:bg-forja-superficie-oscura dark:text-slate-100"
            />
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              <label className="block text-sm font-semibold dark:text-slate-200">
                Nombre opcional
                <input
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  placeholder="proyecto"
                  className="mt-2 w-full rounded-lg border border-forja-borde bg-forja-superficie px-3 py-3 text-sm font-normal outline-none focus:border-slate-500 dark:border-forja-borde-oscuro dark:bg-forja-superficie-oscura dark:text-slate-100"
                />
              </label>
              <label className="block text-sm font-semibold dark:text-slate-200">
                Rama opcional
                <input
                  value={branch}
                  onChange={(event) => setBranch(event.target.value)}
                  placeholder="main"
                  className="mt-2 w-full rounded-lg border border-forja-borde bg-forja-superficie px-3 py-3 text-sm font-normal outline-none focus:border-slate-500 dark:border-forja-borde-oscuro dark:bg-forja-superficie-oscura dark:text-slate-100"
                />
              </label>
            </div>
          </>
        ) : (
          <>
            <p className="mt-2 text-sm leading-6 text-slate-500 dark:text-slate-400">
              El selector nativo abre Finder. La carpeta se autoriza únicamente en tu Mac y nunca se convierte en acceso público.
            </p>
            <label className="mt-4 block text-sm font-semibold dark:text-slate-200">Nombre opcional</label>
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Mi proyecto"
              className="mt-2 w-full rounded-lg border border-forja-borde bg-forja-superficie px-3 py-3 text-sm outline-none focus:border-slate-500 dark:border-forja-borde-oscuro dark:bg-forja-superficie-oscura dark:text-slate-100"
            />
            <button
              type="button"
              disabled={busy}
              onClick={() => onPick(name.trim() || undefined)}
              className="mt-5 w-full rounded-lg bg-slate-950 px-4 py-3 text-sm font-semibold text-white disabled:opacity-40 dark:bg-slate-100 dark:text-slate-900"
            >
              Abrir Finder
            </button>
            <button
              type="button"
              onClick={() => setManualPath((value) => !value)}
              className="mt-3 text-xs font-semibold text-slate-400 dark:text-slate-500"
            >
              {manualPath ? "Ocultar ruta manual" : "Escribir una ruta manualmente"}
            </button>
            {manualPath && (
              <input
                autoFocus
                value={path}
                onChange={(event) => setPath(event.target.value)}
                placeholder="/Users/tu-usuario/Projects/mi-app"
                className="mt-2 w-full rounded-lg border border-forja-borde bg-forja-superficie px-3 py-3 text-sm outline-none focus:border-slate-500 dark:border-forja-borde-oscuro dark:bg-forja-superficie-oscura dark:text-slate-100"
              />
            )}
          </>
        )}
        <div className="mt-6 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md px-4 py-2 text-sm font-semibold text-slate-500 hover:bg-forja-superficie-hundida dark:text-slate-400 dark:hover:bg-slate-800"
          >
            Cancelar
          </button>
          {kind === "clone" && (
            <button
              type="button"
              disabled={!path.trim() || busy}
              onClick={() =>
                onClone({ url: path.trim(), name: name.trim() || undefined, branch: branch.trim() || undefined })
              }
              className="rounded-md bg-slate-950 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40 dark:bg-slate-100 dark:text-slate-900"
            >
              Clonar
            </button>
          )}
          {kind === "folder" && manualPath && (
            <button
              type="button"
              disabled={!path.trim() || busy}
              onClick={() => onAuthorize(path.trim(), name.trim() || undefined)}
              className="rounded-md bg-slate-950 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40 dark:bg-slate-100 dark:text-slate-900"
            >
              Autorizar ruta
            </button>
          )}
        </div>
      </section>
    </div>
  );
}

export function CommandConfirmModal({
  comando,
  mensaje,
  busy,
  onCancel,
  onConfirm,
}: {
  comando: string;
  mensaje: string;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="fixed inset-0 z-[110] grid place-items-center bg-slate-950/35 p-4" role="dialog" aria-modal="true">
      <section className="w-full max-w-sm rounded-xl border border-forja-borde bg-forja-superficie p-5 shadow-xl dark:border-forja-borde-oscuro dark:bg-forja-superficie-oscura-elevada">
        <h2 className="text-lg font-bold dark:text-slate-100">Confirmar /{comando}</h2>
        <p className="mt-2 text-sm text-slate-600 dark:text-slate-300">{mensaje}</p>
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="rounded-lg border border-forja-borde px-3 py-2 text-xs font-semibold hover:bg-forja-superficie-elevada disabled:opacity-50 dark:border-forja-borde-oscuro dark:hover:bg-slate-800"
          >
            Cancelar
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            className="rounded-lg bg-red-600 px-3 py-2 text-xs font-semibold text-white hover:bg-red-700 disabled:opacity-50"
          >
            {busy ? "Ejecutando…" : "Confirmar"}
          </button>
        </div>
      </section>
    </div>
  );
}

export function CommandHelpModal({ texto, onClose }: { texto: string; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-[110] grid place-items-center bg-slate-950/35 p-4" role="dialog" aria-modal="true">
      <section className="flex max-h-[80vh] w-full max-w-lg flex-col rounded-xl border border-forja-borde bg-forja-superficie shadow-xl dark:border-forja-borde-oscuro dark:bg-forja-superficie-oscura-elevada">
        <div className="flex items-center justify-between border-b border-forja-borde-suave px-5 py-4 dark:border-forja-borde-oscuro-suave">
          <h2 className="text-lg font-bold dark:text-slate-100">Comandos disponibles</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-2 hover:bg-forja-superficie-hundida dark:hover:bg-slate-800"
          >
            <XIcon className="h-4 w-4" />
          </button>
        </div>
        <pre className="min-h-0 flex-1 overflow-y-auto whitespace-pre-wrap p-4 font-mono text-xs text-slate-600 dark:text-slate-300">
          {texto || "Todavía no se cargó el registro de comandos."}
        </pre>
      </section>
    </div>
  );
}

/** Antes de tener workspace: "conecta tu Mac" / "abre tu primer proyecto". Es
 * el mismo Reposo sin proyecto todavía, así que vive en este archivo y no en
 * uno aparte. */
export function EmptyWorkspace({ connected, onAdd }: { connected: boolean; onAdd: () => void }) {
  return (
    <div className="grid flex-1 place-items-center p-6 text-center">
      <div className="max-w-lg">
        <span className="mx-auto grid h-16 w-16 place-items-center rounded-xl bg-slate-950 text-white dark:bg-slate-100 dark:text-slate-900">
          <CodeIcon className="h-7 w-7" />
        </span>
        <h2 className="mt-5 text-2xl font-bold dark:text-slate-100">
          {connected ? "Abre tu primer proyecto" : "Conecta tu Mac"}
        </h2>
        <p className="mt-2 text-sm leading-6 text-slate-500 dark:text-slate-400">
          {connected
            ? "Autoriza una carpeta local para usar archivos, terminal, Git y sesiones de ingeniería."
            : "El código y la terminal viven en tu computadora; el teléfono es un control remoto seguro."}
        </p>
        {connected && (
          <button
            type="button"
            onClick={onAdd}
            className="mt-5 rounded-lg bg-slate-950 px-5 py-3 text-sm font-semibold text-white dark:bg-slate-100 dark:text-slate-900"
          >
            Abrir carpeta
          </button>
        )}
      </div>
    </div>
  );
}

/** Estado 1 propiamente dicho: workspace ya elegido, pero sin conversación
 * arrancada todavía -- el compositor centrado es lo único que hay. */
export function Reposo({ estado }: { estado: IdeEstado }) {
  if (!estado.workspace) return null;
  return (
    <section className="relative flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-6 sm:px-10">
        <div className="mx-auto flex h-full max-w-2xl flex-col items-center justify-center text-center">
          <span className="grid h-12 w-12 place-items-center rounded-xl bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900">
            <SparklesIcon className="h-7 w-7" />
          </span>
          <h2 className="mt-5 text-2xl font-bold dark:text-slate-100">¿Qué vamos a construir?</h2>
          <p className="mt-2 max-w-lg text-sm leading-6 text-slate-500 dark:text-slate-400">
            Edecán trabajará sobre {estado.workspace.name}. Verás cada paso, comando y resultado aquí y en
            tu teléfono.
          </p>
        </div>
      </div>
      <Composer estado={estado} />
    </section>
  );
}
