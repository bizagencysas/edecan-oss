/**
 * Drawer lateral de actividad (`GET /v1/activity`): línea de tiempo de las
 * acciones observables recientes, agrupada por día ("Hoy", "Ayer", fecha).
 *
 * Diseño en dos modos (product design):
 *  - Humano (por defecto): icono por tipo de acción + resumen + agente + hora
 *    relativa + píldora de estado. Cada entrada se expande para leer el
 *    resumen completo, sin jerga técnica.
 *  - Técnico (toggle "Detalles técnicos"): añade el tipo/estado crudo y la
 *    marca de tiempo exacta. Nunca se muestra por defecto.
 *
 * Si el endpoint todavía no existe (404, backend en construcción), muestra un
 * estado "Próximamente" honesto — nunca una lista vacía que parezca éxito.
 */

"use client";

import { useEffect, useMemo, useState } from "react";

import {
  BellIcon,
  CheckIcon,
  ChevronDownIcon,
  CodeIcon,
  ComputerIcon,
  RocketIcon,
  SendIcon,
  SparklesIcon,
  XIcon,
  ZapIcon,
} from "@/components/icons";
import { Alert, Badge, Button, Spinner, Switch } from "@/components/ui";
import { ApiError, listActivity, type ActivityItem } from "@/lib/api-activity";
import { formatDateTime } from "@/lib/format";

function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "";
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "";
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (seconds < 60) return "ahora";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `hace ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `hace ${hours} h`;
  const days = Math.floor(hours / 24);
  return `hace ${days} d`;
}

function startOfDay(value: Date): number {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();
}

/** Etiqueta del grupo de tiempo: "Hoy", "Ayer" o una fecha corta. Los items
 * sin `at` caen en "Reciente" (no inventamos un día). */
function dayBucket(iso: string | null | undefined): string {
  if (!iso) return "Reciente";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "Reciente";
  const dayDiff = Math.round((startOfDay(new Date()) - startOfDay(date)) / 86400000);
  if (dayDiff === 0) return "Hoy";
  if (dayDiff === 1) return "Ayer";
  return new Intl.DateTimeFormat("es", { day: "numeric", month: "short" }).format(date);
}

type IconFn = (props: { className?: string }) => React.ReactElement;

/** Icono por tipo de acción; cae a un punto neutro para tipos desconocidos. */
function iconForType(type: string): IconFn {
  const normalized = type.toLowerCase();
  if (normalized.includes("mission") || normalized.includes("task")) return RocketIcon;
  if (normalized.includes("message") || normalized.includes("chat")) return SendIcon;
  if (normalized.includes("automation") || normalized.includes("routine")) return ZapIcon;
  if (normalized.includes("tool") || normalized.includes("mcp")) return CodeIcon;
  if (normalized.includes("computer") || normalized.includes("desktop")) return ComputerIcon;
  if (normalized.includes("approval")) return BellIcon;
  if (normalized.includes("done") || normalized.includes("complete")) return CheckIcon;
  return SparklesIcon;
}

const STATUS_VARIANT: Record<string, "success" | "danger" | "brand" | "warning" | "neutral"> = {
  ok: "success",
  success: "success",
  done: "success",
  completed: "success",
  complete: "success",
  error: "danger",
  failed: "danger",
  failure: "danger",
  running: "brand",
  active: "brand",
  in_progress: "brand",
  pending: "warning",
  waiting: "warning",
  blocked: "warning",
};

function statusVariant(status: string): "success" | "danger" | "brand" | "warning" | "neutral" {
  return STATUS_VARIANT[status.toLowerCase()] ?? "neutral";
}

interface ActivityGroup {
  key: string;
  label: string;
  items: ActivityItem[];
}

export function ActivityDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [items, setItems] = useState<ActivityItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [upcoming, setUpcoming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [advanced, setAdvanced] = useState(false);

  useEffect(() => {
    if (!open || loaded) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    listActivity()
      .then((next) => {
        if (cancelled) return;
        setItems(next);
        setLoaded(true);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setUpcoming(true);
        } else {
          setError(err instanceof Error ? err.message : "No se pudo cargar la actividad.");
        }
        setLoaded(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, loaded]);

  // Cierra con Escape sin pisar otros atajos globales.
  useEffect(() => {
    if (!open) return;
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  const groups = useMemo<ActivityGroup[] | null>(() => {
    if (items.length === 0) return null;
    const sorted = [...items].sort((a, b) => {
      const at = a.at ? Date.parse(a.at) : Number.NEGATIVE_INFINITY;
      const bt = b.at ? Date.parse(b.at) : Number.NEGATIVE_INFINITY;
      return (Number.isNaN(bt) ? Number.NEGATIVE_INFINITY : bt) - (Number.isNaN(at) ? Number.NEGATIVE_INFINITY : at);
    });
    const result: ActivityGroup[] = [];
    for (const item of sorted) {
      const label = dayBucket(item.at);
      const last = result[result.length - 1];
      if (last && last.label === label) {
        last.items.push(item);
      } else {
        result.push({ key: label, label, items: [item] });
      }
    }
    return result;
  }, [items]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[60]"
      role="dialog"
      aria-modal="true"
      aria-label="Actividad reciente"
    >
      <button
        aria-label="Cerrar actividad"
        className="absolute inset-0 bg-slate-900/40"
        onClick={onClose}
      />
      <aside className="absolute inset-y-0 right-0 flex w-full max-w-md flex-col overflow-hidden border-l border-slate-200 bg-white shadow-2xl dark:border-slate-800 dark:bg-slate-900 motion-safe:animate-fade-in">
        <header className="flex shrink-0 items-center justify-between gap-3 border-b border-slate-100 px-5 py-4 dark:border-slate-800">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Actividad</h2>
            <p className="mt-0.5 truncate text-xs text-slate-500 dark:text-slate-400">
              Lo que tu equipo y Edecan hicieron hace poco.
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-3">
            <Switch
              id="activity-advanced"
              checked={advanced}
              onChange={setAdvanced}
              label={<span className="text-xs">Detalles técnicos</span>}
            />
            <Button type="button" variant="ghost" size="sm" onClick={onClose} aria-label="Cerrar">
              <XIcon className="h-4 w-4" />
            </Button>
          </div>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4 thin-scrollbar">
          {loading ? (
            <div className="flex justify-center py-12">
              <Spinner className="h-5 w-5 text-slate-400" />
            </div>
          ) : upcoming ? (
            <div className="flex flex-col items-center justify-center py-16 text-center">
              <p className="text-sm font-medium text-slate-700 dark:text-slate-200">Próximamente</p>
              <p className="mt-1 max-w-xs text-xs text-slate-500 dark:text-slate-400">
                El historial de actividad todavía no está disponible en esta instalación.
              </p>
            </div>
          ) : error ? (
            <Alert variant="error">{error}</Alert>
          ) : groups === null || groups.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-center">
              <p className="text-sm font-medium text-slate-700 dark:text-slate-200">Sin actividad todavía</p>
              <p className="mt-1 max-w-xs text-xs text-slate-500 dark:text-slate-400">
                En cuanto tu equipo actúe, su avance aparecerá aquí.
              </p>
            </div>
          ) : (
            <div className="space-y-6">
              {groups.map((group) => (
                <section key={group.key} aria-label={group.label}>
                  <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400 dark:text-slate-500">
                    {group.label}
                  </h3>
                  <ol className="relative space-y-1 before:absolute before:inset-y-2 before:left-[15px] before:w-px before:bg-slate-200 dark:before:bg-slate-800">
                    {group.items.map((item, index) => {
                      const Icon = iconForType(item.type);
                      const variant = statusVariant(item.status);
                      return (
                        <li key={`${group.key}:${index}:${item.at ?? item.summary}`}>
                          <details className="group relative pl-9">
                            <summary className="cursor-pointer select-none list-none py-2">
                              <span
                                className={cx(
                                  "absolute left-0 top-2 flex h-8 w-8 items-center justify-center rounded-full ring-1",
                                  "bg-white text-slate-500 ring-slate-200 dark:bg-slate-900 dark:text-slate-400 dark:ring-slate-700",
                                )}
                              >
                                <Icon className="h-4 w-4" />
                              </span>
                              <span className="flex items-center gap-1.5">
                                <span className="min-w-0 flex-1 truncate text-sm font-medium text-slate-800 dark:text-slate-100">
                                  {item.summary}
                                </span>
                                <ChevronDownIcon className="h-3.5 w-3.5 shrink-0 text-slate-300 transition-transform group-open:rotate-180 dark:text-slate-600" />
                              </span>
                              <span className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] text-slate-400 dark:text-slate-500">
                                {item.agent && <span className="truncate">{item.agent}</span>}
                                {item.agent && item.at && <span aria-hidden="true">·</span>}
                                {item.at && (
                                  <span title={formatDateTime(item.at)}>{timeAgo(item.at)}</span>
                                )}
                                <Badge variant={variant}>{item.status}</Badge>
                              </span>
                            </summary>
                            <div className="pb-2 pt-0.5 text-xs leading-5 text-slate-600 dark:text-slate-300">
                              <p>{item.summary}</p>
                              {item.at && (
                                <p className="mt-1 text-[11px] text-slate-400 dark:text-slate-500">
                                  {formatDateTime(item.at)}
                                </p>
                              )}
                              {advanced && (
                                <div className="mt-1 whitespace-pre-wrap rounded-md bg-slate-50 px-2 py-1.5 font-mono text-[11px] leading-4 text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
                                  {[
                                    `type: ${item.type}`,
                                    `status: ${item.status}`,
                                    item.at ? `at: ${item.at}` : null,
                                  ]
                                    .filter(Boolean)
                                    .join("\n")}
                                </div>
                              )}
                            </div>
                          </details>
                        </li>
                      );
                    })}
                  </ol>
                </section>
              ))}
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}
