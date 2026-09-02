/**
 * Paleta de comandos global (⌘K). Overlay que busca —con coincidencia por
 * subsecuencia, no solo por prefijo— entre acciones rápidas y las entidades
 * del espacio (agentes, conversaciones, equipos, workspaces, rutinas y
 * conectores). Los resultados se agrupan por tipo con encabezados, navegables
 * con ↑/↓ y Enter. Con la búsqueda vacía se muestran solo las acciones rápidas
 * (bajo ruido). Todas las lecturas son best-effort: si una ruta todavía no
 * existe (backend en construcción) ese grupo simplemente no aparece.
 *
 * Se monta en `AppShell` y se omite en `/app/studio`, donde el editor ya tiene
 * su propia paleta con el mismo atajo (evita que ambos respondan a ⌘K a la vez).
 */

"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useRouter } from "next/navigation";

import {
  BellIcon,
  BubblesIcon,
  ChatIcon,
  LayersIcon,
  PlugIcon,
  SparklesIcon,
  TeamIcon,
  ZapIcon,
} from "@/components/icons";
import { listConnectors, listConversations, listWorkers, type PersistentWorker } from "@/lib/api";
import { listAutomations, type Automation } from "@/lib/api-automatizaciones";
import { listTeamsTolerant, type Team } from "@/lib/api-teams";
import { listWorkspacesTolerant, type Workspace } from "@/lib/api-workspaces";
import type { ConversationOut, ConnectorListItem } from "@/lib/types";

function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

const KBD =
  "rounded border border-slate-200 bg-slate-50 px-1 py-0.5 font-sans text-[10px] leading-none text-slate-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-400";

interface PaletteItem {
  id: string;
  label: string;
  group: string;
  hint?: string;
  keywords?: string[];
  action: () => void;
}

/** Coincidencia por subsecuencia: todas las letras de la búsqueda aparecen en
 * orden. Devuelve la posición de inicio (menor = mejor) o `null`. */
function subsequenceStart(needle: string, haystack: string): number | null {
  if (needle.length === 0) return 0;
  let i = 0;
  let first = -1;
  for (let j = 0; j < haystack.length && i < needle.length; j += 1) {
    if (haystack[j] === needle[i]) {
      if (first === -1) first = j;
      i += 1;
    }
  }
  return i === needle.length ? first : null;
}

/** Puntaje menor = mejor coincidencia. La subsecuencia penaliza frente a la
 * coincidencia exacta por prefijo/substring. */
function scoreItem(query: string, item: PaletteItem): number | null {
  const needle = query.toLowerCase();
  const haystacks = [item.label.toLowerCase(), ...(item.keywords ?? []).map((k) => k.toLowerCase())];
  let best: number | null = null;
  for (const haystack of haystacks) {
    const direct = haystack.indexOf(needle);
    if (direct !== -1) {
      best = best === null ? direct : Math.min(best, direct);
      continue;
    }
    const sub = subsequenceStart(needle, haystack);
    if (sub !== null) {
      const penalized = sub + 1000;
      best = best === null ? penalized : Math.min(best, penalized);
    }
  }
  return best;
}

/** Orden de presentación de los grupos; los que no tengan resultados se omiten. */
const GROUP_ORDER = ["Acciones", "Agentes", "Equipos", "Workspaces", "Conversaciones", "Rutinas", "Conectores"];

export function GlobalCommandPalette({ enabled = true }: { enabled?: boolean }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  const [entities, setEntities] = useState<PaletteItem[]>([]);
  const [loaded, setLoaded] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // --- Acciones estáticas (siempre presentes) ---------------------------------
  const staticItems: PaletteItem[] = useMemo(
    () => [
      { id: "action:new-worker", label: "Nuevo compañero", group: "Acciones", keywords: ["agente", "worker", "crear"], action: () => router.push("/app/workers") },
      { id: "action:message", label: "Mensaje a…", group: "Acciones", hint: "Abrir el chat", keywords: ["chat", "hablar", "escribir"], action: () => router.push("/app") },
      { id: "action:tasks", label: "Abrir Misiones", group: "Acciones", keywords: ["tasks", "tareas", "delegar"], action: () => router.push("/app/misiones") },
      { id: "action:activity", label: "Actividad", group: "Acciones", keywords: ["timeline", "historial"], action: () => router.push("/app/actividad") },
      { id: "action:connect-gmail", label: "Conectar Gmail", group: "Acciones", keywords: ["google", "correo", "mail"], action: () => router.push("/app/conectores") },
      { id: "action:security", label: "Seguridad", group: "Acciones", keywords: ["pausar", "emergencia", "permisos"], action: () => router.push("/app/seguridad") },
      { id: "action:settings", label: "Ajustes", group: "Acciones", keywords: ["config", "preferencias"], action: () => router.push("/app/ajustes") },
    ],
    [router],
  );

  const loadEntities = useCallback(async () => {
    const results = await Promise.allSettled([
      listWorkers(),
      listConversations(),
      listTeamsTolerant(),
      listWorkspacesTolerant(),
      listAutomations(),
      listConnectors(),
    ]);
    const items: PaletteItem[] = [];
    const [workers, conversations, teams, workspaces, automations, connectors] = results;

    if (workers.status === "fulfilled") {
      for (const worker of workers.value as PersistentWorker[]) {
        const name = worker.display_name?.trim() || worker.name;
        items.push({
          id: `worker:${worker.id}`,
          label: name,
          group: "Agentes",
          hint: worker.role_title?.trim() || worker.purpose,
          keywords: [worker.name, worker.purpose],
          action: () => router.push("/app/workers"),
        });
      }
    }
    if (conversations.status === "fulfilled") {
      for (const conversation of conversations.value as ConversationOut[]) {
        const title = conversation.title || "Conversación";
        items.push({
          id: `conversation:${conversation.id}`,
          label: title,
          group: "Conversaciones",
          action: () => router.push("/app"),
        });
      }
    }
    if (teams.status === "fulfilled") {
      for (const team of teams.value as Team[]) {
        items.push({
          id: `team:${team.id}`,
          label: team.name,
          group: "Equipos",
          hint: `${team.members.length} ${team.members.length === 1 ? "miembro" : "miembros"}`,
          action: () => router.push(`/app/bots?team=${encodeURIComponent(team.id)}`),
        });
      }
    }
    if (workspaces.status === "fulfilled") {
      for (const workspace of workspaces.value as Workspace[]) {
        items.push({
          id: `workspace:${workspace.id}`,
          label: workspace.name,
          group: "Workspaces",
          hint: `${workspace.agents.length} ${workspace.agents.length === 1 ? "agente" : "agentes"}`,
          action: () => router.push("/app/workspaces"),
        });
      }
    }
    if (automations.status === "fulfilled") {
      for (const automation of automations.value as Automation[]) {
        items.push({
          id: `automation:${automation.id}`,
          label: automation.nombre,
          group: "Rutinas",
          hint: automation.descripcion || automation.accion.instruccion,
          action: () => router.push("/app/automatizaciones"),
        });
      }
    }
    if (connectors.status === "fulfilled") {
      for (const connector of connectors.value as ConnectorListItem[]) {
        items.push({
          id: `connector:${connector.key}`,
          label: connector.display_name,
          group: "Conectores",
          hint: connector.accounts.length > 0 ? "Conectado" : "Sin conectar",
          action: () => router.push("/app/conectores"),
        });
      }
    }
    setEntities(items);
  }, [router]);

  useEffect(() => {
    if (!open || loaded) return;
    setLoaded(true);
    void loadEntities();
  }, [open, loaded, loadEntities]);

  useEffect(() => {
    if (!enabled) return;
    const handler = (event: globalThis.KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((current) => !current);
        setQuery("");
        setSelected(0);
      } else if (event.key === "Escape") {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [enabled]);

  useEffect(() => {
    if (!open) return;
    const timer = window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => window.clearTimeout(timer);
  }, [open]);

  const allItems = useMemo(() => [...staticItems, ...entities], [staticItems, entities]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    // Vacío: solo las acciones rápidas (bajo ruido). Al escribir se busca
    // sobre acciones + entidades (agentes, conversaciones, equipos…).
    if (!needle) return staticItems;
    const scored: Array<{ item: PaletteItem; score: number }> = [];
    for (const item of allItems) {
      const score = scoreItem(needle, item);
      if (score !== null) scored.push({ item, score });
    }
    scored.sort((a, b) => a.score - b.score || a.item.label.localeCompare(b.item.label));
    return scored.map((entry) => entry.item);
  }, [allItems, query, staticItems]);

  // Agrupación ordenada para el render + lista plana en el MISMO orden para
  // que el índice del teclado coincida con lo que se ve.
  const { groups, flat } = useMemo(() => {
    const byGroup = new Map<string, PaletteItem[]>();
    for (const item of filtered) {
      const list = byGroup.get(item.group) ?? [];
      list.push(item);
      byGroup.set(item.group, list);
    }
    const labels = [
      ...GROUP_ORDER,
      ...Array.from(byGroup.keys()).filter((group) => !GROUP_ORDER.includes(group)),
    ].filter((group) => byGroup.has(group));
    const grouped = labels.map((label) => ({ label, items: byGroup.get(label)! }));
    const flattened = grouped.flatMap((group) => group.items);
    const indexByItem = new Map<PaletteItem, number>();
    flattened.forEach((item, index) => indexByItem.set(item, index));
    return {
      groups: grouped.map((group) => ({
        label: group.label,
        items: group.items.map((item) => ({ item, index: indexByItem.get(item) ?? 0 })),
      })),
      flat: flattened,
    };
  }, [filtered]);

  // Mantiene el elemento seleccionado visible al navegar con el teclado.
  useEffect(() => {
    if (!open) return;
    const element = listRef.current?.querySelector<HTMLElement>(`[data-index="${selected}"]`);
    element?.scrollIntoView({ block: "nearest" });
  }, [selected, open]);

  function choose(item: PaletteItem) {
    setOpen(false);
    setQuery("");
    item.action();
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setSelected((value) => Math.min(value + 1, Math.max(0, flat.length - 1)));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setSelected((value) => Math.max(0, value - 1));
    } else if (event.key === "Home") {
      event.preventDefault();
      setSelected(0);
    } else if (event.key === "End") {
      event.preventDefault();
      setSelected(Math.max(0, flat.length - 1));
    } else if (event.key === "Enter" && flat[selected]) {
      event.preventDefault();
      choose(flat[selected]);
    }
  }

  if (!open || !enabled) return null;

  return (
    <div
      className="fixed inset-0 z-[90] flex items-start justify-center bg-slate-950/55 px-3 pt-[12vh] backdrop-blur-sm"
      role="presentation"
      onMouseDown={() => setOpen(false)}
    >
      <section
        className="w-full max-w-xl overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl dark:border-slate-700 dark:bg-slate-900"
        role="dialog"
        aria-modal="true"
        aria-label="Comandos"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="flex items-center gap-3 border-b border-slate-200 px-4 dark:border-slate-700">
          <span aria-hidden="true" className="text-slate-400">⌕</span>
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setSelected(0);
            }}
            onKeyDown={onKeyDown}
            placeholder="Busca un agente, una acción, una rutina…"
            className="min-w-0 flex-1 bg-transparent py-4 text-sm text-slate-900 outline-none placeholder:text-slate-400 dark:text-white"
          />
        </div>
        <div ref={listRef} className="max-h-80 overflow-y-auto p-2 thin-scrollbar">
          {flat.length === 0 ? (
            <p className="px-3 py-8 text-center text-sm text-slate-500">
              No encontré nada con «{query}».
            </p>
          ) : (
            <div className="space-y-1">
              {groups.map((group) => (
                <div key={group.label}>
                  <p className="px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wider text-slate-400 dark:text-slate-500">
                    {group.label}
                  </p>
                  {group.items.map(({ item, index }) => {
                    return (
                      <button
                        key={item.id}
                        type="button"
                        data-index={index}
                        onMouseEnter={() => setSelected(index)}
                        onClick={() => choose(item)}
                        className={cx(
                          "flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm transition",
                          index === selected
                            ? "bg-brand-50 text-brand-800 dark:bg-brand-950/50 dark:text-brand-100"
                            : "text-slate-700 hover:bg-slate-50 dark:text-slate-200 dark:hover:bg-slate-800",
                        )}
                      >
                        <span className={cx(
                          "flex h-7 w-7 shrink-0 items-center justify-center rounded-lg",
                          index === selected
                            ? "bg-white text-brand-600 dark:bg-slate-900 dark:text-brand-300"
                            : "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400",
                        )}>
                          <GroupIcon group={item.group} />
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate font-medium">{item.label}</span>
                          {item.hint && (
                            <span className="block truncate text-xs text-slate-400 dark:text-slate-500">
                              {item.hint}
                            </span>
                          )}
                        </span>
                      </button>
                    );
                  })}
                </div>
              ))}
            </div>
          )}
        </div>
        <footer className="flex items-center justify-between gap-3 border-t border-slate-200 px-4 py-2 text-[11px] text-slate-400 dark:border-slate-700 dark:text-slate-500">
          <span className="flex items-center gap-1.5">
            <kbd className={KBD}>⌘K</kbd>
            <span>abrir o cerrar</span>
          </span>
          <span className="flex items-center gap-3">
            <span className="flex items-center gap-1">
              <kbd className={KBD}>↑↓</kbd>
              <span>navegar</span>
            </span>
            <span className="flex items-center gap-1">
              <kbd className={KBD}>↵</kbd>
              <span>abrir</span>
            </span>
          </span>
        </footer>
      </section>
    </div>
  );
}

function GroupIcon({ group }: { group: string }) {
  const icon =
    group === "Acciones" ? <SparklesIcon className="h-3.5 w-3.5" /> :
    group === "Agentes" ? <TeamIcon className="h-3.5 w-3.5" /> :
    group === "Conversaciones" ? <ChatIcon className="h-3.5 w-3.5" /> :
    group === "Equipos" ? <BubblesIcon className="h-3.5 w-3.5" /> :
    group === "Workspaces" ? <LayersIcon className="h-3.5 w-3.5" /> :
    group === "Rutinas" ? <ZapIcon className="h-3.5 w-3.5" /> :
    group === "Conectores" ? <PlugIcon className="h-3.5 w-3.5" /> :
    <BellIcon className="h-3.5 w-3.5" />;
  return <span className="flex h-3.5 w-3.5 items-center justify-center">{icon}</span>;
}
