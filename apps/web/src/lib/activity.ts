import type { Automation } from "./api-automatizaciones";
import type { Mission } from "./api-misiones";
import type { Reminder } from "./types";
import type { PhoneCall } from "./types";

export type ActivityKind = "mission" | "reminder" | "automation" | "phone";
export type ActivityTone = "attention" | "active" | "scheduled" | "complete" | "error";

export interface ActivityEntry {
  id: string;
  kind: ActivityKind;
  title: string;
  detail: string;
  href: string;
  tone: ActivityTone;
  statusLabel: string;
  timestamp: string | null;
  phoneConfirmation?: { callId: string; toE164: string; goal: string };
}

export interface ActivityOverview {
  attention: ActivityEntry[];
  current: ActivityEntry[];
  recent: ActivityEntry[];
}

const MISSION_LABELS: Record<Mission["status"], string> = {
  planning: "Preparando",
  running: "En curso",
  waiting_confirmation: "Necesita aprobación",
  done: "Finalizada",
  error: "Requiere revisión",
  cancelled: "Cancelada",
};

function missionTone(status: Mission["status"]): ActivityTone {
  if (status === "waiting_confirmation") return "attention";
  if (status === "error") return "error";
  if (status === "planning" || status === "running") return "active";
  return "complete";
}

function timestampValue(value: string | null): number {
  if (!value) return Number.POSITIVE_INFINITY;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? Number.POSITIVE_INFINITY : parsed;
}

function newestFirst(a: ActivityEntry, b: ActivityEntry): number {
  return timestampValue(b.timestamp) - timestampValue(a.timestamp);
}

function soonestFirst(a: ActivityEntry, b: ActivityEntry): number {
  return timestampValue(a.timestamp) - timestampValue(b.timestamp);
}

/** Une la actividad que antes estaba repartida entre varias páginas. */
export function buildActivityOverview({
  reminders,
  missions,
  automations,
  calls = [],
  now = new Date(),
}: {
  reminders: Reminder[];
  missions: Mission[];
  automations: Automation[];
  calls?: PhoneCall[];
  now?: Date;
}): ActivityOverview {
  const entries: ActivityEntry[] = [];
  const nowTime = now.getTime();

  for (const mission of missions) {
    entries.push({
      id: `mission:${mission.id}`,
      kind: "mission",
      title: mission.objetivo,
      detail: mission.error || "Trabajo delegado a Edecan",
      href: "/app/misiones",
      tone: missionTone(mission.status),
      statusLabel: MISSION_LABELS[mission.status],
      timestamp: mission.updated_at,
    });
  }

  for (const reminder of reminders) {
    if (reminder.status === "cancelled") continue;
    const dueTime = timestampValue(reminder.due_at);
    const overdue = reminder.status === "pending" && dueTime < nowTime;
    entries.push({
      id: `reminder:${reminder.id}`,
      kind: "reminder",
      title: reminder.message,
      detail: overdue ? "Este recordatorio ya venció" : "Recordatorio",
      href: "/app/recordatorios",
      tone: overdue ? "attention" : reminder.status === "pending" ? "scheduled" : "complete",
      statusLabel: overdue ? "Vencido" : reminder.status === "pending" ? "Próximo" : "Enviado",
      timestamp: reminder.due_at,
    });
  }

  for (const automation of automations) {
    if (!automation.enabled) continue;
    entries.push({
      id: `automation:${automation.id}`,
      kind: "automation",
      title: automation.nombre,
      detail: automation.descripcion || automation.accion.instruccion,
      href: "/app/automatizaciones",
      tone: "scheduled",
      statusLabel: automation.next_run_at ? "Programada" : "Activa",
      timestamp: automation.next_run_at || automation.updated_at,
    });
  }

  const callLabels: Record<PhoneCall["status"], string> = {
    draft: "Confirma la llamada",
    confirmed: "Confirmada",
    queued: "Marcando",
    ringing: "Sonando",
    in_progress: "En llamada",
    completed: "Finalizada",
    failed: "Falló",
    busy: "Ocupado",
    no_answer: "Sin respuesta",
    cancelled: "Cancelada",
  };
  for (const call of calls) {
    const isAttention = call.status === "draft";
    const isError = call.status === "failed" || call.status === "busy" || call.status === "no_answer";
    const isActive = ["confirmed", "queued", "ringing", "in_progress"].includes(call.status);
    const peer = call.direction === "outgoing" ? call.to_e164 : call.from_e164;
    entries.push({
      id: `phone:${call.id}`,
      kind: "phone",
      title: call.direction === "outgoing" ? `Llamada a ${peer}` : `Llamada de ${peer}`,
      detail: call.error || call.goal,
      href: "/app",
      tone: isAttention ? "attention" : isError ? "error" : isActive ? "active" : "complete",
      statusLabel: callLabels[call.status],
      timestamp: call.updated_at || call.created_at,
      phoneConfirmation: call.status === "draft" ? {
        callId: call.id,
        toE164: call.to_e164,
        goal: call.goal,
      } : undefined,
    });
  }

  return {
    attention: entries
      .filter((entry) => entry.tone === "attention" || entry.tone === "error")
      .sort(soonestFirst),
    current: entries
      .filter((entry) => entry.tone === "active" || entry.tone === "scheduled")
      .sort(soonestFirst),
    recent: entries.filter((entry) => entry.tone === "complete").sort(newestFirst).slice(0, 8),
  };
}
