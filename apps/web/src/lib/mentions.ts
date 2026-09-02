/**
 * Mención portable en el composer: `@token` como texto plano (sobrevive copiar/
 * pegar a cualquier superficie) que luego se pinta como "chip" en el render de
 * mensajes. Centraliza el token, el catálogo combinado y el regex del chip.
 */

import { listConnectors } from "./api";
import { listWorkers, type PersistentWorker } from "./api";
import { listTeamsTolerant, type Team } from "./api-teams";
import { listWorkspacesTolerant, type Workspace } from "./api-workspaces";

export type MentionKind = "agente" | "team" | "workspace" | "conector";

export interface MentionableItem {
  /** Token insertado (sin `@`), de una sola palabra para que el chip sea limpio. */
  token: string;
  /** Nombre legible que se muestra en el menú. */
  label: string;
  kind: MentionKind;
  sublabel?: string;
}

/** Convierte un nombre legible a un token de una sola palabra. */
export function mentionToken(value: string): string {
  const slug = value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || "item";
}

/** Regex del chip: `@token` (letras, números, punto, guión bajo o guión),
 * sin que lo preceda otro carácter de palabra (evita falsos positivos en
 * correos tipo `a@b.com`). */
export const MENTION_RE = /(?<![\p{L}\p{N}])@([\p{L}\p{N}._-]+)/gu;

/** Catálogo combinado para el autocompletado de `@`. Cada fuente se pide de
 * forma independiente y tolerante: si una lista falla (p. ej. el backend de
 * equipos todavía no existe), el resto sigue disponible. */
export async function collectMentionTargets(): Promise<MentionableItem[]> {
  const results = await Promise.allSettled([
    listWorkers(),
    listTeamsTolerant(),
    listWorkspacesTolerant(),
    listConnectors(),
  ]);

  const items: MentionableItem[] = [];

  const workers = results[0].status === "fulfilled" ? (results[0].value as PersistentWorker[]) : [];
  for (const worker of workers) {
    items.push({
      token: mentionToken(worker.name),
      label: worker.display_name?.trim() || worker.name,
      kind: "agente",
      sublabel: worker.role_title?.trim() || "Compañero",
    });
  }

  const teams = results[1].status === "fulfilled" ? (results[1].value as Team[]) : [];
  for (const team of teams) {
    items.push({
      token: mentionToken(team.name),
      label: team.name,
      kind: "team",
      sublabel: "Equipo",
    });
  }

  const workspaces = results[2].status === "fulfilled" ? (results[2].value as Workspace[]) : [];
  for (const workspace of workspaces) {
    items.push({
      token: mentionToken(workspace.name),
      label: workspace.name,
      kind: "workspace",
      sublabel: "Workspace",
    });
  }

  const connectors = results[3].status === "fulfilled"
    ? results[3].value as Array<{ key: string; display_name: string }>
    : [];
  for (const connector of connectors) {
    items.push({
      token: mentionToken(connector.key),
      label: connector.display_name,
      kind: "conector",
      sublabel: "Conector",
    });
  }

  return items;
}