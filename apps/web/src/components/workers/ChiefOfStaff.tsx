/**
 * "Chief of Staff" / resumen diario del roster (product design§83): un saludo
 * discreto con el estado del equipo, dos bloques de agregado — lo que necesita
 * atención (aprobaciones, handoffs, rutinas fallidas, compañeros que requieren
 * revisión) y lo que está trabajando ahora — y un bloque de "Próximamente" con
 * las rutinas agendadas.
 *
 * Los trabajadores y handoffs llegan ya cargados desde la página (evita
 * lecturas duplicadas); las aprobaciones y rutinas se piden acá, de forma
 * tolerante (si una ruta no existe todavía, ese conteo simplemente no aparece).
 */

"use client";

import { useEffect, useState } from "react";

import { ClockIcon, ZapIcon } from "@/components/icons";
import { Card, CardBody } from "@/components/ui";
import { AgentAvatar } from "@/components/workers/AgentAvatar";
import { PresenceDot } from "@/components/workers/PresenceDot";
import { type PersistentWorker, type WorkerHandoff } from "@/lib/api";
import { listApprovals, type Approval } from "@/lib/api-approvals";
import { listAutomations, type Automation } from "@/lib/api-automatizaciones";

function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

function greetingFor(now: Date): string {
  const hour = now.getHours();
  if (hour < 12) return "Buenos días";
  if (hour < 20) return "Buenas tardes";
  return "Buenas noches";
}

/** Etiqueta breve y relativa para una rutina agendada ("Hoy · 16:00"). */
function upcomingLabel(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const now = new Date();
  const startOfDay = (value: Date) =>
    new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();
  const dayDiff = Math.round((startOfDay(date) - startOfDay(now)) / 86400000);
  const time = new Intl.DateTimeFormat("es", { hour: "numeric", minute: "2-digit" }).format(date);
  if (dayDiff === 0) return `Hoy · ${time}`;
  if (dayDiff === 1) return `Mañana · ${time}`;
  if (dayDiff === 2) return `Pasado mañana · ${time}`;
  const day = new Intl.DateTimeFormat("es", { day: "numeric", month: "short" }).format(date);
  return `${day} · ${time}`;
}

interface AttentionItem {
  id: string;
  label: string;
  count: number;
  tone: "danger" | "warning" | "brand";
}

export function ChiefOfStaff({
  workers,
  handoffs,
  canAutomations,
}: {
  workers: PersistentWorker[];
  handoffs: WorkerHandoff[];
  canAutomations: boolean;
}) {
  const [approvals, setApprovals] = useState<Approval[] | null>(null);
  const [automations, setAutomations] = useState<Automation[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    listApprovals()
      .then((next) => {
        if (!cancelled) setApprovals(next);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!canAutomations) return;
    let cancelled = false;
    listAutomations()
      .then((next) => {
        if (!cancelled) setAutomations(next);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [canAutomations]);

  const failedAutomations = (automations ?? []).filter(
    (automation) => (automation.consecutive_failures ?? 0) > 0,
  );
  const workersNeedingReview = workers.filter(
    (worker) =>
      worker.last_checkpoint &&
      typeof worker.last_checkpoint === "object" &&
      worker.last_checkpoint.status === "error",
  );
  const runningWorkers = workers.filter((worker) => worker.enabled && worker.status === "running");

  const attention: AttentionItem[] = [];
  if (approvals !== null && approvals.length > 0) {
    attention.push({
      id: "approvals",
      label: "Aprobaciones pendientes",
      count: approvals.length,
      tone: "warning",
    });
  }
  if (handoffs.length > 0) {
    attention.push({
      id: "handoffs",
      label: "Traspasos que necesitan tu OK",
      count: handoffs.length,
      tone: "warning",
    });
  }
  if (failedAutomations.length > 0) {
    attention.push({
      id: "automations",
      label: "Rutinas con fallos",
      count: failedAutomations.length,
      tone: "danger",
    });
  }
  if (workersNeedingReview.length > 0) {
    attention.push({
      id: "workers-error",
      label: "Compañeros que requieren revisión",
      count: workersNeedingReview.length,
      tone: "danger",
    });
  }

  const upcoming = (automations ?? [])
    .filter((automation) => automation.enabled && automation.next_run_at)
    .sort((a, b) => Date.parse(a.next_run_at ?? "") - Date.parse(b.next_run_at ?? ""))
    .slice(0, 5);

  const headerSubcopy =
    runningWorkers.length > 0
      ? "Tu equipo está trabajando."
      : workers.length > 0
        ? "Tu equipo está listo."
        : "Aquí vivirá tu equipo.";

  return (
    <div className="space-y-4">
      <header className="flex items-end justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold tracking-tight text-slate-900 dark:text-slate-50">
            {greetingFor(new Date())}
          </h2>
          <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">{headerSubcopy}</p>
        </div>
      </header>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <Card>
          <CardBody>
            <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
              Necesita tu atención
            </h3>
            {attention.length === 0 ? (
              <p className="mt-2 text-xs text-slate-400 dark:text-slate-500">
                Nada esperando por ti. Todo al día.
              </p>
            ) : (
              <ul className="mt-3 space-y-1.5">
                {attention.map((item) => (
                  <li
                    key={item.id}
                    className="flex items-center justify-between gap-3 rounded-lg border border-slate-100 px-3 py-2 dark:border-slate-800"
                  >
                    <span className="flex min-w-0 items-center gap-2 text-sm text-slate-700 dark:text-slate-200">
                      <span
                        className={cx(
                          "h-1.5 w-1.5 shrink-0 rounded-full",
                          item.tone === "danger"
                            ? "bg-rose-500"
                            : item.tone === "warning"
                              ? "bg-amber-500"
                              : "bg-brand-500",
                        )}
                      />
                      <span className="truncate">{item.label}</span>
                    </span>
                    <span className="shrink-0 rounded-full bg-slate-100 px-2 py-0.5 text-xs font-semibold tabular-nums text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                      {item.count}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardBody>
            <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
              Trabajando ahora
            </h3>
            {runningWorkers.length === 0 ? (
              <p className="mt-2 text-xs text-slate-400 dark:text-slate-500">
                Ningún compañero está trabajando en este momento.
              </p>
            ) : (
              <ul className="mt-3 space-y-1.5">
                {runningWorkers.map((worker) => (
                  <li
                    key={worker.id}
                    className="flex items-center gap-3 rounded-lg border border-slate-100 px-3 py-2 dark:border-slate-800"
                  >
                    <AgentAvatar
                      name={worker.name}
                      displayName={worker.display_name}
                      avatar={worker.avatar}
                      size="sm"
                    />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">
                        {worker.display_name?.trim() || worker.name}
                      </p>
                      <p className="truncate text-xs text-slate-500 dark:text-slate-400">
                        {worker.role_title?.trim() || worker.purpose}
                      </p>
                    </div>
                    <span className="flex items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400">
                      <PresenceDot state="active" />
                      En curso
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {canAutomations && failedAutomations.length > 0 && (
              <p className="mt-3 flex items-center gap-1.5 border-t border-slate-100 pt-2 text-xs text-slate-500 dark:border-slate-800 dark:text-slate-400">
                <ZapIcon className="h-3.5 w-3.5" />
                {failedAutomations.length}{" "}
                {failedAutomations.length === 1 ? "rutina falló" : "rutinas fallaron"} la última vez.
              </p>
            )}
          </CardBody>
        </Card>
      </div>

      {canAutomations && (
        <Card>
          <CardBody>
            <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Próximamente</h3>
            {upcoming.length === 0 ? (
              <p className="mt-2 text-xs text-slate-400 dark:text-slate-500">
                No hay rutinas programadas para los próximos días.
              </p>
            ) : (
              <ul className="mt-3 grid grid-cols-1 gap-1.5 sm:grid-cols-2">
                {upcoming.map((automation) => (
                  <li
                    key={automation.id}
                    className="flex items-center gap-3 rounded-lg border border-slate-100 px-3 py-2 dark:border-slate-800"
                  >
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                      <ClockIcon className="h-4 w-4" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">
                        {automation.nombre}
                      </p>
                      <p className="truncate text-xs text-slate-500 dark:text-slate-400">
                        {upcomingLabel(automation.next_run_at ?? "")}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
      )}
    </div>
  );
}
