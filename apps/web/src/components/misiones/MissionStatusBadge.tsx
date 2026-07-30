"use client";

import { Badge } from "@/components/ui";
import type { MissionStatus, MissionStepStatus } from "@/lib/api-misiones";

type BadgeVariant = "neutral" | "brand" | "success" | "warning" | "danger";

const MISSION_LABELS: Record<MissionStatus, string> = {
  planning: "Planificando",
  running: "En curso",
  waiting_confirmation: "Esperando confirmación",
  done: "Completada",
  error: "Error",
  cancelled: "Cancelada",
};

export const STEP_LABELS: Record<MissionStepStatus, string> = {
  pending: "Pendiente",
  running: "Ejecutando",
  waiting_confirmation: "Esperando confirmación",
  done: "Hecho",
  error: "Error",
  skipped: "Omitido",
};

const VARIANTS: Record<string, BadgeVariant> = {
  planning: "neutral",
  pending: "neutral",
  running: "brand",
  waiting_confirmation: "warning",
  done: "success",
  error: "danger",
  cancelled: "neutral",
  skipped: "neutral",
};

/** Badge de estado de una misión (`edecan_schemas.missions.MISSION_STATUSES`). */
export function MissionStatusBadge({ status }: { status: MissionStatus }) {
  return <Badge variant={VARIANTS[status] ?? "neutral"}>{MISSION_LABELS[status] ?? status}</Badge>;
}

/** Badge de estado de un paso (`edecan_schemas.missions.MISSION_STEP_STATUSES`). */
export function StepStatusBadge({ status }: { status: MissionStepStatus }) {
  return <Badge variant={VARIANTS[status] ?? "neutral"}>{STEP_LABELS[status] ?? status}</Badge>;
}
