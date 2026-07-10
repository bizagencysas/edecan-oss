"use client";

/** Detalle de una automatización: info, acciones (probar/activar/borrar) y
 * feed de corridas — el estado `waiting_confirmation` queda claramente
 * visible (`ROADMAP_V2.md` §7.10, WP-V2-07: "estado waiting_confirmation
 * visible"). */

import { PlayIcon, TrashIcon } from "@/components/icons";
import { Alert, Badge, Button, Card, CardBody, CardHeader, EmptyState, Spinner, Switch } from "@/components/ui";
import { formatDateTime } from "@/lib/format";
import type { Automation, AutomationRun } from "@/lib/api-automatizaciones";

const RUN_STATUS_VARIANT: Record<string, "brand" | "success" | "danger" | "warning" | "neutral"> = {
  running: "brand",
  done: "success",
  error: "danger",
  waiting_confirmation: "warning",
};

const RUN_STATUS_LABEL: Record<string, string> = {
  running: "corriendo",
  done: "completada",
  error: "error",
  waiting_confirmation: "esperando confirmación",
};

function RunItem({ run }: { run: AutomationRun }) {
  const variant = RUN_STATUS_VARIANT[run.status] ?? "neutral";
  const label = RUN_STATUS_LABEL[run.status] ?? run.status;
  const pendiente = run.detalle?.pendiente as
    | { name?: string; args?: Record<string, unknown> }
    | undefined;
  const resultado = typeof run.detalle?.resultado === "string" ? run.detalle.resultado : null;
  const error = typeof run.detalle?.error === "string" ? run.detalle.error : null;

  return (
    <li className="rounded-lg border border-slate-100 px-3 py-2.5 text-sm dark:border-slate-800">
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs text-slate-400">
          {run.started_at ? formatDateTime(run.started_at) : "—"}
        </span>
        <Badge variant={variant}>{label}</Badge>
      </div>
      {run.status === "waiting_confirmation" && pendiente && (
        <p className="mt-1.5 rounded-md bg-amber-50 px-2 py-1.5 text-xs text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
          Se detuvo esperando aprobación humana para usar «{pendiente.name ?? "una herramienta"}».
          Este tipo de corrida no se reanuda sola — revísala y vuelve a correrla si corresponde.
        </p>
      )}
      {resultado && (
        <p className="mt-1.5 truncate text-xs text-slate-500 dark:text-slate-400">{resultado}</p>
      )}
      {error && (
        <p className="mt-1.5 truncate text-xs text-rose-600 dark:text-rose-400">{error}</p>
      )}
    </li>
  );
}

export function AutomationDetail({
  automation,
  runs,
  runsLoading,
  onBack,
  onToggleEnabled,
  onProbar,
  onDelete,
  probando,
  toggling,
  deleting,
}: {
  automation: Automation;
  runs: AutomationRun[];
  runsLoading: boolean;
  onBack: () => void;
  onToggleEnabled: (enabled: boolean) => void;
  onProbar: () => void;
  onDelete: () => void;
  probando: boolean;
  toggling: boolean;
  deleting: boolean;
}) {
  return (
    <div className="space-y-6">
      <button
        onClick={onBack}
        className="text-sm text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200"
      >
        ← Volver a la lista
      </button>

      <Card>
        <CardHeader
          title={automation.nombre}
          description={
            automation.trigger.kind === "schedule"
              ? `Agenda: ${automation.trigger.rrule}`
              : "Disparador: webhook entrante"
          }
          actions={
            <Button
              size="sm"
              variant="secondary"
              onClick={onProbar}
              loading={probando}
              className="gap-1.5"
            >
              <PlayIcon className="h-3.5 w-3.5" />
              Probar ahora
            </Button>
          }
        />
        <CardBody className="space-y-4">
          {automation.trigger.kind === "webhook" && automation.hook_secret && (
            <Alert variant="success">
              Guarda este secreto ahora — no vuelve a mostrarse: <code>{automation.hook_secret}</code>
              <br />
              URL: <code>{automation.trigger.hook_url}</code> (header{" "}
              <code>X-Hook-Secret</code>)
            </Alert>
          )}
          {automation.trigger.kind === "webhook" && !automation.hook_secret && (
            <p className="text-xs text-slate-500 dark:text-slate-400">
              URL: <code>{automation.trigger.hook_url}</code>
            </p>
          )}

          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-4 dark:border-slate-800">
            <Switch
              checked={automation.enabled}
              onChange={onToggleEnabled}
              label={automation.enabled ? "Activa" : "Desactivada"}
              id="detail-enabled"
              className={toggling ? "opacity-60" : undefined}
            />
            <div className="flex items-center gap-4 text-xs text-slate-500 dark:text-slate-400">
              {automation.next_run_at && <span>Próxima: {formatDateTime(automation.next_run_at)}</span>}
              {automation.last_run_at && <span>Última: {formatDateTime(automation.last_run_at)}</span>}
            </div>
            <button
              onClick={onDelete}
              disabled={deleting}
              className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-slate-400 hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-950/40"
            >
              <TrashIcon className="h-3.5 w-3.5" />
              Borrar
            </button>
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Corridas"
          description="Cada vez que se disparó esta automatización, con su resultado."
        />
        <CardBody>
          {runsLoading ? (
            <div className="flex justify-center py-8">
              <Spinner className="h-5 w-5 text-slate-400" />
            </div>
          ) : runs.length === 0 ? (
            <EmptyState
              title="Todavía no corrió"
              description="Usa «Probar ahora» arriba, o espera a su próximo disparo."
            />
          ) : (
            <ul className="space-y-2">
              {runs.map((run) => (
                <RunItem key={run.id} run={run} />
              ))}
            </ul>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
