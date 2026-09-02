/**
 * Traducción de `status` + `last_checkpoint` de un worker persistente a una
 * frase semántica corta ("● Trabajando · …") para el roster y la sección de
 * actividad. No fabrica actividad: si no hay checkpoint, la frase se queda en
 * el estado base.
 */

import type { PersistentWorker } from "@/lib/api";

import { presenceState } from "./PresenceDot";

function text(value: unknown, max = 80): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  return trimmed.length > max ? `${trimmed.slice(0, max)}…` : trimmed;
}

/** Extrae un resumen legible del `last_checkpoint` (dict JSONB del worker). */
export function checkpointSummary(checkpoint: Record<string, unknown> | null | undefined): string | null {
  if (!checkpoint || typeof checkpoint !== "object") return null;
  if (typeof checkpoint.error === "string" && checkpoint.error.trim()) {
    return text(checkpoint.error, 80);
  }
  const result = checkpoint.result;
  if (result && typeof result === "object") {
    const candidate =
      (result as Record<string, unknown>).text ??
      (result as Record<string, unknown>).resultado ??
      (result as Record<string, unknown>).resumen ??
      (result as Record<string, unknown>).output;
    const summary = text(candidate, 80);
    if (summary) return summary;
  }
  const status = text(checkpoint.status);
  if (status === "running") return "en curso";
  if (status === "done") return "terminada";
  if (status === "error") return "falló";
  return null;
}

function timeAgo(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return null;
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (seconds < 60) return "hace un momento";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `hace ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `hace ${hours} h`;
  const days = Math.floor(hours / 24);
  return `hace ${days} d`;
}

/** Frase de estado para el roster, p. ej. "Trabajando · en curso". */
export function agentStatusPhrase(worker: PersistentWorker): string {
  const state = presenceState(worker.status, worker.enabled);
  const checkpoint = worker.last_checkpoint;
  switch (state) {
    case "active": {
      const started = timeAgo(
        typeof checkpoint?.started_at === "string" ? checkpoint.started_at : null,
      );
      return started ? `Trabajando · ${started}` : "Trabajando";
    }
    case "paused":
      return "En pausa";
    case "off":
      return "Desactivado";
    case "idle": {
      const summary = checkpointSummary(checkpoint);
      if (summary && String(checkpoint?.status) === "error") {
        return `Disponible · última tarea falló`;
      }
      if (summary) return `Disponible · ${summary}`;
      return "Disponible";
    }
  }
}

/** Entrada mínima de actividad reciente, derivada del checkpoint del worker. */
export interface WorkerActivityEntry {
  id: string;
  workerName: string;
  detail: string;
  tone: "active" | "error" | "complete";
  timestamp: string | null;
}

/** Convierte el `last_checkpoint` en una entrada de actividad legible. */
export function workerActivityEntry(worker: PersistentWorker): WorkerActivityEntry | null {
  const checkpoint = worker.last_checkpoint;
  if (!checkpoint || typeof checkpoint !== "object") return null;
  const title = worker.display_name?.trim() || worker.name;
  const checkpointStatus = typeof checkpoint.status === "string" ? checkpoint.status : null;
  const tone: WorkerActivityEntry["tone"] =
    checkpointStatus === "error"
      ? "error"
      : worker.status === "running"
        ? "active"
        : "complete";
  const detail = checkpointSummary(checkpoint) ?? (checkpointStatus === "running" ? "en curso" : "tarea completada");
  const timestamp =
    typeof checkpoint.finished_at === "string"
      ? checkpoint.finished_at
      : typeof checkpoint.started_at === "string"
        ? checkpoint.started_at
        : null;
  return {
    id: `${worker.id}:${String(checkpoint.task_id ?? checkpoint.instruction_hash ?? "")}`,
    workerName: title,
    detail,
    tone,
    timestamp,
  };
}