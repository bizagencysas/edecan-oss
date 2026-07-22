export type StudioAction =
  | "health"
  | "list"
  | "create"
  | "edit"
  | "read"
  | "render"
  | "history"
  | "variants"
  | "duplicate"
  | "brand-health"
  | "tidy"
  | "archive"
  | "restore"
  | "export"
  | "template-list"
  | "template-save"
  | "template-create"
  | "design-system-list"
  | "design-system-generate"
  | "corpus-ingest"
  | "corpus-search"
  | "share-package";

export type StudioMode =
  | "mockup"
  | "carousel"
  | "ad"
  | "post"
  | "landing"
  | "email"
  | "deck"
  | "general";

export type StudioQuality = "fast" | "balanced" | "max";

export interface StudioActionInput {
  action: StudioAction;
  projectId?: string;
  revisionId?: string;
  templateId?: string;
  prompt?: string;
  instruction?: string;
  projectName?: string;
  brandName?: string;
  brandTokens?: string;
  mode?: StudioMode;
  width?: number;
  height?: number;
  count?: number;
  quality?: StudioQuality;
  files?: string[];
  exportFormat?: "html" | "png" | "pdf";
  includeArchived?: boolean;
  templateName?: string;
  templateDescription?: string;
  templateCategory?: "prototype" | "deck" | "landing" | "marketing" | "other";
  confirmed?: boolean;
}

export interface StudioArtifact {
  file_id: string;
  filename: string;
  mime: string | null;
}

export interface StudioActionResponse {
  status: "ready";
  action: string;
  message: string;
  result: Record<string, unknown>;
  artifacts: StudioArtifact[];
  presentation: Array<Record<string, unknown>>;
}

export interface StudioProject {
  id: string;
  name: string;
  mode: StudioMode;
  revisions: number;
  updatedAt: string;
  brandName: string;
  archivedAt: string | null;
}

export interface StudioRevision {
  id: string;
  label: string;
  width: number;
  height: number;
  instruction: string;
  createdAt: string;
  archivedAt: string | null;
}

export interface StudioTemplate {
  id: string;
  name: string;
  description: string;
  category: string;
  mode: StudioMode;
  width: number;
  height: number;
  createdAt: string;
}

export type StudioPreviewKind = "html" | "image" | "pdf";

export interface StudioPreviewArtifact extends StudioArtifact {
  kind: StudioPreviewKind;
}

export type TweakControl =
  | { id: string; label: string; type: "toggle"; value: boolean }
  | { id: string; label: string; type: "slider"; value: number; min: number; max: number; step: number; unit: string }
  | { id: string; label: string; type: "color"; value: string }
  | { id: string; label: string; type: "select"; value: string; options: string[] };

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function number(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function studioMode(value: unknown): StudioMode {
  return ["mockup", "carousel", "ad", "post", "landing", "email", "deck", "general"].includes(
    String(value),
  )
    ? (value as StudioMode)
    : "general";
}

export function studioProjectsFromResponse(response: StudioActionResponse): StudioProject[] {
  const items = Array.isArray(response.result.projects) ? response.result.projects : [];
  return items.flatMap((item) => {
    const project = record(item);
    const id = text(project?.id).trim();
    if (!project || !id) return [];
    return [{
      id,
      name: text(project.name, "Proyecto sin nombre"),
      mode: studioMode(project.mode),
      revisions: Math.max(0, Math.round(number(project.revisions, 0))),
      updatedAt: text(project.updatedAt),
      brandName: text(project.brandName),
      archivedAt: text(project.archivedAt) || null,
    }];
  });
}

export function studioRevisionsFromResponse(response: StudioActionResponse): StudioRevision[] {
  const items = Array.isArray(response.result.revisions) ? response.result.revisions : [];
  return items.flatMap((item) => {
    const revision = record(item);
    const id = text(revision?.id).trim();
    if (!revision || !id) return [];
    return [{
      id,
      label: text(revision.label, "Revisión"),
      width: Math.max(320, Math.round(number(revision.width, 1200))),
      height: Math.max(320, Math.round(number(revision.height, 800))),
      instruction: text(revision.instruction),
      createdAt: text(revision.createdAt),
      archivedAt: text(revision.archivedAt) || null,
    }];
  });
}

export function studioTemplatesFromResponse(response: StudioActionResponse): StudioTemplate[] {
  const items = Array.isArray(response.result.templates) ? response.result.templates : [];
  return items.flatMap((item) => {
    const template = record(item);
    const id = text(template?.id).trim();
    if (!template || !id) return [];
    return [{
      id,
      name: text(template.name, "Plantilla"),
      description: text(template.description),
      category: text(template.category, "other"),
      mode: studioMode(template.mode),
      width: Math.max(320, Math.round(number(template.width, 1200))),
      height: Math.max(320, Math.round(number(template.height, 800))),
      createdAt: text(template.createdAt),
    }];
  });
}

export function previewKind(artifact: StudioArtifact): StudioPreviewKind | null {
  const mime = (artifact.mime ?? "").toLowerCase();
  const name = artifact.filename.toLowerCase();
  if (mime === "text/html" || /\.html?$/.test(name)) return "html";
  if (mime === "application/pdf" || name.endsWith(".pdf")) return "pdf";
  if (mime.startsWith("image/") || /\.(png|jpe?g|webp|gif|svg)$/.test(name)) return "image";
  return null;
}

export function pickStudioPreviewArtifact(artifacts: StudioArtifact[]): StudioPreviewArtifact | null {
  const candidates = artifacts.flatMap((artifact) => {
    const kind = previewKind(artifact);
    return kind ? [{ ...artifact, kind }] : [];
  });
  return candidates.find((item) => item.kind === "html") ?? candidates.find((item) => item.kind === "image") ?? candidates[0] ?? null;
}

export const STUDIO_PREVIEW_CSP =
  "default-src 'none'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; " +
  "media-src data: blob:; connect-src 'none'; frame-src 'none'; object-src 'none'; " +
  "script-src 'none'; form-action 'none'; base-uri 'none'";

export async function isolatedStudioHtmlBlob(blob: Blob): Promise<Blob> {
  const raw = await blob.text();
  const csp = `<meta http-equiv="Content-Security-Policy" content="${STUDIO_PREVIEW_CSP}">`;
  const html = /<head(?:\s[^>]*)?>/i.test(raw)
    ? raw.replace(/<head(?:\s[^>]*)?>/i, (head) => `${head}${csp}`)
    : `<!doctype html><html><head>${csp}</head><body>${raw}</body></html>`;
  return new Blob([html], { type: "text/html;charset=utf-8" });
}

export function createTweakControlFromPrompt(request: string): TweakControl | null {
  const label = request.trim().slice(0, 80);
  if (!label) return null;
  const normalized = label.toLowerCase();
  const id = `tweak-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
  if (/color|tono|fondo|acento|background/.test(normalized)) {
    return { id, label, type: "color", value: "#6366f1" };
  }
  if (/tamaño|radio|espacio|opacidad|intensidad|size|radius|spacing|opacity/.test(normalized)) {
    return { id, label, type: "slider", value: 50, min: 0, max: 100, step: 1, unit: "%" };
  }
  if (/estilo|fuente|alineación|layout|variant/.test(normalized)) {
    return { id, label, type: "select", value: "equilibrado", options: ["sutil", "equilibrado", "intenso"] };
  }
  return { id, label, type: "toggle", value: true };
}

export function tweaksToInstruction(controls: TweakControl[]): string {
  if (controls.length === 0) return "";
  return controls
    .map((control) => `${control.label}: ${typeof control.value === "boolean" ? (control.value ? "activado" : "desactivado") : `${control.value}${control.type === "slider" ? control.unit : ""}`}`)
    .join("; ");
}

export function buildStudioEditInstruction(input: {
  instruction: string;
  selection?: string;
  annotations?: string;
  tweaks?: TweakControl[];
}): string {
  const parts = [input.instruction.trim()];
  if (input.selection?.trim()) parts.push(`Zona indicada: ${input.selection.trim()}.`);
  if (input.annotations?.trim()) parts.push(`Anotaciones visibles: ${input.annotations.trim()}.`);
  const tweaks = tweaksToInstruction(input.tweaks ?? []);
  if (tweaks) parts.push(`Ajustes solicitados: ${tweaks}.`);
  return parts.filter(Boolean).join("\n").slice(0, 80_000);
}
