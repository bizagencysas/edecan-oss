"use client";

/**
 * Pastilla de proyecto activo + pastilla de alcance -- encargo "El
 * repositorio activo NO se ve".
 *
 * Hoy la única forma de saber sobre qué repo trabaja el agente era leer un
 * párrafo de bienvenida y una línea diminuta en el pie de la barra lateral
 * ("Mac conectado · fyinvest · main"). §3.1 de la especificación pide
 * exactamente esto en su lugar, ENCIMA y DEBAJO del compositor:
 *
 *   ▫ edecan  ⌄            <- pastilla de proyecto (con desplegable)
 *   [ compositor ]
 *       ▫ Local            <- pastilla de alcance (informativa)
 *
 * `WorkspacePill` es el mismo componente en los tres estados de §3.1: lo monta
 * `Composer` (compartido por Reposo y Trabajando) y `estado/Editor.tsx` en su
 * cabecera -- un solo lugar para el dato, no tres implementaciones.
 *
 * El desplegable de la pastilla de proyecto es "Conectar ProjectPicker": en
 * vez de reinventar una lista, embebe el selector de carpeta ya construido y
 * probado (`ProjectPicker.tsx`), con `autoOpen` (salta su propio botón
 * "cerrado", porque el disparador ya es esta pastilla) y `onRequestClose`
 * (para que el menú flotante se cierre solo al elegir o cancelar).
 *
 * `ScopePill` es deliberadamente texto, no un botón: hoy Forge solo corre en
 * el Mac emparejado (§3.5, `terminal: total`) y no existe ningún otro alcance
 * entre los que elegir -- un desplegable ahí sería falso.
 */

import { useEffect, useRef, useState } from "react";

import { ChevronDownIcon, SquareIcon } from "@/components/icons";
import { ProjectPicker } from "@/components/ide/ProjectPicker";
import type { IdeWorkspace } from "@/lib/api-ide";

export function WorkspacePill({
  workspace,
  workspaces,
  branch,
  onSelectWorkspace,
  onWorkspacesChange,
  className = "",
}: {
  workspace: IdeWorkspace | null;
  workspaces: IdeWorkspace[];
  branch?: string | null;
  onSelectWorkspace: (workspace: IdeWorkspace) => void;
  onWorkspacesChange?: (workspaces: IdeWorkspace[]) => void;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Los menús del compositor (`CommandMenu`/`MentionMenu`) se cierran con el
  // `onBlur` del textarea; acá el disparador es un botón suelto en cualquiera
  // de los tres estados, así que hace falta el propio oyente de clic-afuera.
  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  if (!workspace) return null;

  return (
    <div ref={containerRef} className={`relative inline-block ${className}`}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="true"
        aria-expanded={open}
        aria-label="Cambiar de proyecto"
        className="flex max-w-full items-center gap-1.5 rounded-md px-2 py-1 text-xs font-semibold text-slate-500 transition hover:bg-forja-superficie-elevada dark:text-slate-400 dark:hover:bg-slate-800"
      >
        <SquareIcon className="h-3 w-3 shrink-0" aria-hidden="true" />
        <span className="truncate">{workspace.name}</span>
        {branch && <span className="shrink-0 font-normal text-slate-400 dark:text-slate-500">· {branch}</span>}
        <ChevronDownIcon className="h-3 w-3 shrink-0" aria-hidden="true" />
      </button>

      {open && (
        <div className="absolute left-0 top-full z-40 mt-1.5 w-80 rounded-lg border border-forja-borde bg-forja-superficie p-2 shadow-lg dark:border-forja-borde-oscuro dark:bg-forja-superficie-oscura-elevada">
          <ProjectPicker
            value={workspace}
            workspaces={workspaces}
            onWorkspacesChange={onWorkspacesChange}
            autoOpen
            label="Cambiar de workspace"
            onChange={onSelectWorkspace}
            onRequestClose={() => setOpen(false)}
          />
        </div>
      )}
    </div>
  );
}

/** Alcance de ejecución del proyecto activo. Informativa a propósito -- ver
 * nota de cabecera: mientras exista un solo alcance posible ("Local"), un
 * desplegable acá sería un adorno falso. */
export function ScopePill({ label = "Local", className = "" }: { label?: string; className?: string }) {
  return (
    <span
      className={`inline-flex max-w-full items-center gap-1.5 truncate px-2 py-0.5 text-[11px] text-slate-400 dark:text-slate-500 ${className}`}
    >
      <SquareIcon className="h-2.5 w-2.5 shrink-0" aria-hidden="true" />
      {label}
    </span>
  );
}
