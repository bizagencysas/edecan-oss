"use client";

/** Lista de automatizaciones: switch `enabled`, badge de próximo run, click
 * para ver el detalle (`ROADMAP_V2.md` §7.10, WP-V2-07). */

import { TrashIcon, ZapIcon } from "@/components/icons";
import { Badge, Card, CardBody, CardHeader, EmptyState, Switch } from "@/components/ui";
import { formatDateTime } from "@/lib/format";
import type { Automation } from "@/lib/api-automatizaciones";

function triggerLabel(automation: Automation): string {
  if (automation.trigger.kind === "schedule") return automation.trigger.rrule;
  return "Webhook";
}

export function AutomationList({
  items,
  loading,
  busyId,
  onToggleEnabled,
  onSelect,
  onDelete,
}: {
  items: Automation[];
  loading: boolean;
  busyId: string | null;
  onToggleEnabled: (automation: Automation, enabled: boolean) => void;
  onSelect: (automation: Automation) => void;
  onDelete: (automation: Automation) => void;
}) {
  return (
    <Card>
      <CardHeader
        title="Tus automatizaciones"
        description="Reglas que corren una instrucción del agente en modo headless."
      />
      <CardBody>
        {loading ? null : items.length === 0 ? (
          <EmptyState
            title="Sin automatizaciones todavía"
            description="Crea la primera arriba, o pídeselo a tu asistente en el chat con «gestionar_automatizacion»."
          />
        ) : (
          <ul className="space-y-2">
            {items.map((automation) => (
              <li
                key={automation.id}
                className="flex items-center justify-between gap-3 rounded-lg border border-slate-100 px-3 py-2.5 dark:border-slate-800"
              >
                <button
                  type="button"
                  onClick={() => onSelect(automation)}
                  className="flex min-w-0 flex-1 items-center gap-3 text-left"
                >
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-50 text-brand-600 dark:bg-brand-900/50 dark:text-brand-300">
                    <ZapIcon className="h-4 w-4" />
                  </span>
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-medium text-slate-700 dark:text-slate-200">
                      {automation.nombre}
                    </span>
                    <span className="block truncate text-xs text-slate-400">
                      {triggerLabel(automation)}
                      {automation.next_run_at
                        ? ` · próxima: ${formatDateTime(automation.next_run_at)}`
                        : ""}
                    </span>
                  </span>
                </button>
                <div className="flex shrink-0 items-center gap-3">
                  {automation.next_run_at && (
                    <Badge variant="brand">{formatDateTime(automation.next_run_at)}</Badge>
                  )}
                  <Switch
                    checked={automation.enabled}
                    onChange={(checked) => onToggleEnabled(automation, checked)}
                    label=""
                    id={`enabled-${automation.id}`}
                  />
                  <button
                    onClick={() => onDelete(automation)}
                    disabled={busyId === automation.id}
                    className="rounded-md p-1.5 text-slate-400 hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-950/40"
                    aria-label="Borrar"
                  >
                    <TrashIcon className="h-3.5 w-3.5" />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}
