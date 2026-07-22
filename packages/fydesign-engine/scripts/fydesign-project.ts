/** Portable, local-first project/design engine.
 *
 * This is the executable boundary for the original Studio project layer:
 * create a complete HTML artifact, keep immutable revisions, edit it from a
 * natural-language instruction, and render a safe PNG preview. It deliberately
 * excludes the original Next/Auth/multi-tenant application shell.
 */
import crypto from 'node:crypto';
import { copyFile, mkdir, readFile, rename, stat, writeFile } from 'node:fs/promises';
import path from 'node:path';
import '../src/lib/network-guard';

import { callAIJSON, type InlineImage } from '../src/lib/ai/deepseek-client';
import { launchBrowser } from '../src/lib/browser';
import { retrievePatterns } from '../src/lib/corpus/retrieval';
import { ingestReposIntoCorpus } from '../src/lib/design-engine/corpus-ingestor';
import {
  buildSystemPrompt,
  buildUserMessage,
  detectMode,
  type DesignMode,
} from '../src/lib/design-engine/prompts';
import {
  runProjectPipeline,
  type ProjectQuality,
  type RuntimePipelineInput,
  type RuntimeScreenBrief,
  type RuntimePipelineTrace,
} from '../src/lib/design-engine/runtime-pipeline';
import { buildMasterHtml } from '../src/lib/projects/master-html-builder';
import { renderHtmlToPng } from '../src/lib/render-png';
import { MAX_HTML_BYTES } from '../src/lib/project-security';
import { requiredRuntimePath } from '../src/lib/runtime-env';

// Imported Layer-B modules use console.log for observability. Keep stdout a
// strict one-object protocol for the Python adapter and route diagnostics to
// stderr instead.
console.log = (...values: unknown[]) => console.error(...values);
console.warn = (...values: unknown[]) => console.error(...values);

type ProjectAction =
  | 'health'
  | 'list'
  | 'create'
  | 'edit'
  | 'read'
  | 'render'
  | 'history'
  | 'variants'
  | 'duplicate'
  | 'brand-health'
  | 'tidy'
  | 'archive'
  | 'restore'
  | 'export'
  | 'template-list'
  | 'template-save'
  | 'template-create'
  | 'design-system-list'
  | 'design-system-generate'
  | 'corpus-ingest'
  | 'corpus-search'
  | 'share-package';

type ExportFormat = 'html' | 'png' | 'pdf';

interface TidyAction {
  kind: 'rename-revision' | 'archive-revision' | 'restore-revision';
  revisionId: string;
  label?: string;
  reason?: string;
}

interface ProjectInput {
  action: ProjectAction;
  prompt?: string;
  instruction?: string;
  projectId?: string;
  revisionId?: string;
  projectName?: string;
  brandName?: string;
  brandTokens?: string;
  mode?: DesignMode;
  width?: number;
  height?: number;
  count?: number;
  quality?: ProjectQuality;
  screenBriefs?: RuntimeScreenBrief[];
  languages?: RuntimePipelineInput['languages'];
  theme?: RuntimePipelineInput['theme'];
  assetPaths?: string[];
  sourceProjectId?: string;
  templateId?: string;
  templateName?: string;
  templateDescription?: string;
  templateCategory?: 'prototype' | 'deck' | 'landing' | 'marketing' | 'other';
  exportFormat?: ExportFormat;
  tidyActions?: TidyAction[];
  corpusLimit?: number;
  repos?: string[];
  includeArchived?: boolean;
}

interface Revision {
  id: string;
  createdAt: string;
  instruction: string;
  htmlPath: string;
  width: number;
  height: number;
  label: string;
  trace: RuntimePipelineTrace;
  archivedAt?: string;
}

interface Project {
  id: string;
  name: string;
  prompt: string;
  mode: DesignMode;
  createdAt: string;
  updatedAt: string;
  revisions: Revision[];
  brandName?: string;
  brandTokens?: string;
  archivedAt?: string;
}

interface ProjectTemplate {
  id: string;
  name: string;
  description: string;
  category: 'prototype' | 'deck' | 'landing' | 'marketing' | 'other';
  createdAt: string;
  sourceProjectId: string;
  width: number;
  height: number;
  mode: DesignMode;
  htmlPath: string;
}

interface PublishedDesignSystem {
  id: string;
  projectId: string;
  name: string;
  version: number;
  publishedAt: string;
  isActive: boolean;
  tokens: Record<string, unknown>;
}

interface ProjectIndex {
  schemaVersion: 2;
  projects: Project[];
  templates: ProjectTemplate[];
  designSystems: PublishedDesignSystem[];
}

const MAX_PROJECTS = 100;
const MAX_REVISIONS = 100;
const MAX_INDEX_BYTES = 20 * 1024 * 1024;

function stateRoot(): string {
  return path.join(requiredRuntimePath('FYDESIGN_STATE_ROOT'), 'projects');
}

function outputRoot(): string {
  return requiredRuntimePath('FYDESIGN_OUTPUT_ROOT');
}

function indexPath(): string {
  return path.join(stateRoot(), 'index.json');
}

async function loadIndex(): Promise<ProjectIndex> {
  try {
    if ((await stat(indexPath())).size > MAX_INDEX_BYTES) {
      throw new Error('El índice local de proyectos supera el límite seguro.');
    }
    const parsed = JSON.parse(await readFile(indexPath(), 'utf8')) as Partial<ProjectIndex>;
    return {
      schemaVersion: 2,
      projects: Array.isArray(parsed.projects) ? parsed.projects : [],
      templates: Array.isArray(parsed.templates) ? parsed.templates : [],
      designSystems: Array.isArray(parsed.designSystems) ? parsed.designSystems : [],
    };
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
      return { schemaVersion: 2, projects: [], templates: [], designSystems: [] };
    }
    throw error;
  }
}

async function saveIndex(index: ProjectIndex): Promise<void> {
  await mkdir(stateRoot(), { recursive: true, mode: 0o700 });
  const target = indexPath();
  const temporary = `${target}.${process.pid}.${Date.now()}.tmp`;
  const encoded = `${JSON.stringify(index, null, 2)}\n`;
  if (Buffer.byteLength(encoded) > MAX_INDEX_BYTES) {
    throw new Error('El índice local de proyectos supera el límite seguro.');
  }
  await writeFile(temporary, encoded, {
    encoding: 'utf8',
    mode: 0o600,
  });
  await rename(temporary, target);
}

function boundedInteger(value: unknown, fallback: number, min: number, max: number): number {
  const numeric = Number(value);
  return Number.isInteger(numeric) && numeric >= min && numeric <= max ? numeric : fallback;
}

function dimensions(input: ProjectInput): { width: number; height: number } {
  const prompt = input.prompt || input.instruction || '';
  const exact = prompt.match(/\b(\d{3,4})\s*[x×]\s*(\d{3,4})\b/i);
  if (exact) {
    return {
      width: boundedInteger(exact[1], 1440, 320, 4096),
      height: boundedInteger(exact[2], 900, 320, 4096),
    };
  }
  const lower = prompt.toLowerCase();
  if (/story|historia|reel|tiktok/.test(lower)) return { width: 1080, height: 1920 };
  if (/post|cuadrad|carousel|carrusel/.test(lower)) return { width: 1080, height: 1080 };
  return {
    width: boundedInteger(input.width, 1440, 320, 4096),
    height: boundedInteger(input.height, 900, 320, 4096),
  };
}

function cleanName(value: string): string {
  const cleaned = value.replace(/[\x00-\x1f<>:"/\\|?*]+/g, ' ').trim().slice(0, 80);
  return cleaned || 'Proyecto creativo';
}

function pathInside(root: string, candidate: string): string {
  const resolvedRoot = path.resolve(root);
  const resolvedCandidate = path.resolve(candidate);
  if (!resolvedCandidate.startsWith(`${resolvedRoot}${path.sep}`)) {
    throw new Error('Studio rechazó una ruta fuera de su almacén privado.');
  }
  return resolvedCandidate;
}

function extractColors(value: string): string[] {
  return [...new Set((value.match(/#[0-9a-f]{3,8}\b/gi) || []).map((color) => color.toUpperCase()))];
}

async function loadInlineImages(input: ProjectInput): Promise<InlineImage[]> {
  const candidates = Array.isArray(input.assetPaths) ? input.assetPaths.slice(0, 12) : [];
  const images: InlineImage[] = [];
  for (const candidate of candidates) {
    const safePath = pathInside(requiredRuntimePath('FYDESIGN_STATE_ROOT'), candidate);
    const extension = path.extname(safePath).toLowerCase();
    const mimeType = extension === '.png'
      ? 'image/png'
      : extension === '.webp'
        ? 'image/webp'
        : ['.jpg', '.jpeg'].includes(extension)
          ? 'image/jpeg'
          : null;
    if (!mimeType) continue;
    const metadata = await stat(safePath);
    if (!metadata.isFile() || metadata.size > 20 * 1024 * 1024) {
      throw new Error('Una imagen de referencia supera el límite seguro de 20 MB.');
    }
    images.push({ mimeType, data: (await readFile(safePath)).toString('base64') });
  }
  return images;
}

async function renderHtmlToPdf(
  html: string,
  width: number,
  height: number,
): Promise<Buffer> {
  const browser = await launchBrowser();
  try {
    const page = await browser.newPage();
    await page.route('**/*', (route) => {
      const url = route.request().url();
      if (/^https?:/i.test(url) || route.request().resourceType() === 'script') {
        route.abort().catch(() => {});
        return;
      }
      route.continue().catch(() => {});
    });
    await page.setViewportSize({ width, height });
    await page.setContent(html, { waitUntil: 'domcontentloaded', timeout: 20_000 });
    await page.evaluate(() => (document as Document & { fonts?: { ready: Promise<unknown> } }).fonts?.ready)
      .catch(() => {});
    return Buffer.from(await page.pdf({
      width: `${width}px`,
      height: `${height}px`,
      printBackground: true,
      margin: { top: '0px', right: '0px', bottom: '0px', left: '0px' },
    }));
  } finally {
    await browser.close();
  }
}

async function brandHealth(project: Project): Promise<Record<string, unknown>> {
  const active = project.revisions.filter((revision) => !revision.archivedAt).slice(-10);
  const official = new Set(extractColors(project.brandTokens || ''));
  const palettes = await Promise.all(active.map(async (revision) => ({
    revision: revision.id,
    label: revision.label,
    colors: extractColors(await readFile(revision.htmlPath, 'utf8')),
  })));
  const used = new Set(palettes.flatMap((entry) => entry.colors));
  const drift = official.size
    ? [...used].filter((color) => !official.has(color))
    : [];
  const overlap = palettes.length > 1
    ? palettes.slice(1).reduce(
        (set, entry) => new Set([...set].filter((color) => entry.colors.includes(color))),
        new Set(palettes[0]?.colors || []),
      ).size
    : used.size;
  const consistencyScore = used.size ? Math.max(2, Math.min(10, 5 + overlap - Math.floor(used.size / 8))) : 8;
  const colorScore = official.size ? Math.max(0, 10 - Math.min(10, drift.length * 2)) : 8;
  const overallScore = Math.round(((consistencyScore + colorScore) / 2) * 10) / 10;
  const issues: string[] = [];
  const recommendations: string[] = [];
  if (!official.size) {
    issues.push('El proyecto todavía no tiene una paleta oficial guardada.');
    recommendations.push('Genera un sistema de diseño para medir la coherencia contra tokens reales.');
  }
  if (drift.length) {
    issues.push(`Colores fuera de la paleta oficial: ${drift.slice(0, 8).join(', ')}.`);
    recommendations.push('Confirma si esos colores son intencionales o vuelve a los tokens activos.');
  }
  return {
    overallScore,
    colorDriftScore: colorScore,
    consistencyScore,
    analyzedRevisions: active.length,
    officialColors: [...official],
    usedColors: [...used],
    issues,
    recommendations,
  };
}

function tidySuggestions(project: Project): TidyAction[] {
  const seen = new Map<string, number>();
  const actions: TidyAction[] = [];
  for (const revision of project.revisions) {
    const normalized = revision.label.trim().toLowerCase();
    const count = (seen.get(normalized) || 0) + 1;
    seen.set(normalized, count);
    if (count > 1 && !revision.archivedAt) {
      actions.push({
        kind: 'rename-revision',
        revisionId: revision.id,
        label: `${revision.label} ${count}`,
        reason: 'Evita nombres repetidos sin borrar la revisión.',
      });
    }
  }
  return actions;
}

function fallbackDesignTokens(project: Project): Record<string, unknown> {
  const colors = extractColors(project.brandTokens || '');
  return {
    colors: {
      primary: colors[0] || '#4F46E5',
      secondary: colors[1] || '#0F172A',
      accent: colors[2] || '#7C3AED',
      background: colors[3] || '#F8FAFC',
      text: colors[4] || '#111827',
      success: '#16A34A',
      danger: '#DC2626',
      warning: '#D97706',
    },
    typography: {
      headingFont: 'Inter',
      bodyFont: 'Inter',
      monoFont: 'ui-monospace',
      sizes: { h1: '32px', h2: '24px', h3: '19px', body: '16px', small: '14px' },
      fontWeights: { heading: 700, body: 400, bold: 600 },
    },
    spacing: [4, 8, 12, 16, 24, 32, 48, 64],
    borderRadius: 16,
    hasDarkMode: true,
  };
}

async function persistRevision(
  project: Project,
  html: string,
  instruction: string,
  width: number,
  height: number,
  label: string,
  trace: RuntimePipelineTrace,
): Promise<{ revision: Revision; htmlOutput: string; pngOutput: string }> {
  const revisionId = `rev_${crypto.randomUUID().slice(0, 12)}`;
  if (project.revisions.length >= MAX_REVISIONS) {
    throw new Error('Este proyecto alcanzó el máximo de 100 revisiones.');
  }
  const projectDir = path.join(stateRoot(), project.id, 'revisions');
  await mkdir(projectDir, { recursive: true, mode: 0o700 });
  const persistedHtml = path.join(projectDir, `${revisionId}.html`);
  await writeFile(persistedHtml, html, { encoding: 'utf8', mode: 0o600 });

  const revision: Revision = {
    id: revisionId,
    createdAt: new Date().toISOString(),
    instruction,
    htmlPath: persistedHtml,
    width,
    height,
    label,
    trace,
  };
  project.revisions.push(revision);
  project.updatedAt = revision.createdAt;

  await mkdir(outputRoot(), { recursive: true, mode: 0o700 });
  const htmlOutput = path.join(outputRoot(), `${project.id}-${revisionId}.html`);
  const pngOutput = path.join(outputRoot(), `${project.id}-${revisionId}.png`);
  await copyFile(persistedHtml, htmlOutput);
  const png = await renderHtmlToPng(html, width, height, revisionId, { blockNetwork: true });
  await writeFile(pngOutput, png, { mode: 0o600 });
  return { revision, htmlOutput, pngOutput };
}

function summarizePipeline(traces: RuntimePipelineTrace[]): Record<string, unknown> {
  return {
    variants: traces,
    variantCount: traces.length,
    corpusEnriched: traces.every((trace) => trace.corpusEnriched),
    retrievedPatterns: traces.reduce((sum, trace) => sum + trace.retrievedPatterns, 0),
    designMemoryInsights: traces.reduce((sum, trace) => sum + trace.designMemoryInsights, 0),
    artisticDirectors: [...new Set(traces.map((trace) => trace.artisticDirector))],
    watchdog: traces.every((trace) => trace.watchdog),
    deterministicValidation: traces.every((trace) => trace.deterministicValidation),
    criticsAttempted: traces.filter((trace) => trace.critic.attempted).length,
    criticScores: traces.map((trace) => trace.critic.score),
    refinedVariants: traces.filter((trace) => trace.critic.refined).length,
    screenRecreation: traces.some((trace) => trace.screenRecreation),
    mockupScreens: traces.reduce((sum, trace) => sum + trace.mockupScreens, 0),
  };
}

async function writeProjectBoard(
  project: Project,
): Promise<{ htmlOutput: string; pngOutput: string }> {
  const variants = await Promise.all(project.revisions.filter((revision) => !revision.archivedAt).map(async (revision) => ({
    id: revision.id,
    label: revision.label,
    width: revision.width,
    height: revision.height,
    html: await readFile(revision.htmlPath, 'utf8'),
  })));
  const board = buildMasterHtml({
    projectId: project.id,
    projectName: project.name,
    brandName: project.brandName || 'Edecán Studio',
    variants,
    variantCount: variants.length,
    createdAt: project.createdAt,
  });
  await mkdir(outputRoot(), { recursive: true, mode: 0o700 });
  const htmlOutput = path.join(outputRoot(), `${project.id}-board.html`);
  const pngOutput = path.join(outputRoot(), `${project.id}-board.png`);
  await writeFile(htmlOutput, board, { encoding: 'utf8', mode: 0o600 });
  const preview = await renderHtmlToPng(board, 1400, 1000, `${project.id}-board`, {
    blockNetwork: true,
    fast: true,
  });
  await writeFile(pngOutput, preview, { mode: 0o600 });
  return { htmlOutput, pngOutput };
}

async function generateHtml(input: ProjectInput, currentHtml?: string): Promise<{
  html: string;
  width: number;
  height: number;
  mode: DesignMode;
  healed: boolean;
  attempts: number;
  trace: RuntimePipelineTrace;
}> {
  const prompt = input.prompt || input.instruction || '';
  if (!prompt.trim()) throw new Error('Falta la instrucción de diseño.');
  const mode = input.mode || detectMode(prompt);
  const { width, height } = dimensions(input);
  const system = buildSystemPrompt(
    width,
    height,
    String(input.brandTokens || '').slice(0, 80_000),
    mode,
    input.brandName,
  );
  const images = await loadInlineImages(input);
  const user = currentHtml
    ? `Edita el siguiente artefacto según la instrucción. Conserva lo que no se pidió cambiar.\n\n` +
      `INSTRUCCIÓN: ${prompt}\n\nHTML ACTUAL:\n${currentHtml}`
    : buildUserMessage(
        images.length
          ? `${prompt}\n\nHay ${images.length} imagen(es) privadas de referencia adjuntas. Analízalas y úsalas sin inventar detalles que no veas.`
          : prompt,
        prompt,
        input.projectName || 'Diseño',
        width,
        height,
      );
  const generated = await runProjectPipeline({
    prompt,
    userMessage: user,
    systemPrompt: system,
    mode,
    width,
    height,
    brandName: input.brandName,
    brandTokens: String(input.brandTokens || '').slice(0, 80_000),
    currentHtml,
    quality: input.quality,
    screenBriefs: input.screenBriefs,
    languages: input.languages,
    theme: input.theme,
    images,
  });
  return {
    html: generated.html,
    width,
    height,
    mode,
    healed: generated.trace.healed,
    attempts: generated.trace.healAttempts,
    trace: generated.trace,
  };
}

async function main(input: ProjectInput): Promise<Record<string, unknown>> {
  const index = await loadIndex();
  if (input.action === 'health') {
    return {
      ok: true,
      engine: 'fydesign-projects',
      actions: [
        'create', 'edit', 'list', 'read', 'render', 'history', 'variants',
        'duplicate', 'brand-health', 'tidy', 'archive', 'restore', 'export',
        'template-list', 'template-save', 'template-create',
        'design-system-list', 'design-system-generate', 'corpus-ingest', 'corpus-search',
        'share-package',
      ],
      pipeline: [
        'corpus-retrieval',
        'design-memory',
        'artistic-director',
        'watchdog',
        'validation-self-heal',
        'visual-critic',
        'screen-recreation-mockups',
        'variants-board-export',
        'private-assets-vision',
        'templates-design-systems-brand-health',
      ],
      projects: index.projects.length,
      templates: index.templates.length,
      designSystems: index.designSystems.length,
    };
  }
  if (input.action === 'list') {
    const visibleProjects = input.includeArchived
      ? index.projects
      : index.projects.filter((project) => !project.archivedAt);
    return {
      ok: true,
      projects: visibleProjects.map((project) => ({
        id: project.id,
        name: project.name,
        mode: project.mode,
        revisions: project.revisions.filter((revision) => !revision.archivedAt).length,
        updatedAt: project.updatedAt,
        brandName: project.brandName,
        archivedAt: project.archivedAt,
      })),
      archived: index.projects.filter((project) => project.archivedAt).length,
    };
  }
  if (input.action === 'template-list') {
    return {
      ok: true,
      templates: index.templates.map((template) => ({
        id: template.id,
        name: template.name,
        description: template.description,
        category: template.category,
        mode: template.mode,
        width: template.width,
        height: template.height,
        createdAt: template.createdAt,
      })),
    };
  }
  if (input.action === 'corpus-search') {
    const prompt = String(input.prompt || input.instruction || '').trim();
    if (!prompt) throw new Error('Indica qué patrón o referencia visual quieres buscar.');
    const mode = input.mode || detectMode(prompt);
    const patterns = await retrievePatterns({
      prompt,
      mode,
      brandName: input.brandName,
      styleNotes: input.brandTokens,
    }, boundedInteger(input.corpusLimit, 6, 1, 20));
    return {
      ok: true,
      query: prompt,
      mode,
      patterns: patterns.map((pattern) => ({
        title: pattern.title,
        summary: pattern.summary || '',
        rules: pattern.rules,
        cssMoves: pattern.cssMoves,
        avoid: pattern.avoid,
        appliesTo: pattern.appliesTo,
      })),
    };
  }
  if (input.action === 'corpus-ingest') {
    const repos = [...new Set((input.repos || []).map((repo) => String(repo).trim()))]
      .filter((repo) => /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo))
      .slice(0, 25);
    if (!repos.length) {
      throw new Error('Indica al menos un repositorio público con formato owner/repo.');
    }
    const results = await ingestReposIntoCorpus(repos, process.env.GITHUB_TOKEN || '');
    return {
      ok: true,
      action: 'corpus-ingest',
      ingested: results.filter((result) => result.ok).length,
      failed: results.filter((result) => !result.ok).length,
      results,
      authentication: process.env.GITHUB_TOKEN ? 'configured' : 'public-rate-limit',
    };
  }
  if (input.action === 'design-system-list') {
    return {
      ok: true,
      designSystems: index.designSystems
        .filter((system) => !input.projectId || system.projectId === input.projectId)
        .sort((a, b) => b.publishedAt.localeCompare(a.publishedAt)),
    };
  }

  if (input.action === 'create' || input.action === 'template-create') {
    if (index.projects.length >= MAX_PROJECTS) {
      throw new Error('Studio alcanzó el máximo local de 100 proyectos.');
    }
    const now = new Date().toISOString();
    let templateHtml: string | undefined;
    let template: ProjectTemplate | undefined;
    if (input.action === 'template-create') {
      template = index.templates.find((item) => item.id === input.templateId);
      if (!template) throw new Error('No encontré esa plantilla en el Studio privado.');
      templateHtml = await readFile(
        pathInside(path.join(stateRoot(), 'templates'), template.htmlPath),
        'utf8',
      );
    }
    const count = boundedInteger(input.count, 1, 1, 4);
    const generatedVariants = [];
    for (let index = 0; index < count; index++) {
      const variantInput = count === 1
        ? input
        : {
            ...input,
            prompt: `${input.prompt || ''}\n\nCreate concept ${index + 1} of ${count}. It must use a clearly distinct composition while preserving the brief and brand.`,
          };
      if (templateHtml && !input.prompt?.trim() && index === 0) {
        generatedVariants.push({
          html: templateHtml,
          width: template!.width,
          height: template!.height,
          mode: template!.mode,
          healed: false,
          attempts: 0,
          trace: {
            corpusEnriched: false,
            retrievedPatterns: 0,
            designMemoryInsights: 0,
            artisticDirector: 'plantilla local',
            watchdog: true,
            deterministicValidation: true,
            healed: false,
            healAttempts: 0,
            screenRecreation: false,
            mockupScreens: 0,
            critic: { attempted: false, score: null, refined: false, issues: 0 },
          },
        });
      } else {
        generatedVariants.push(await generateHtml(variantInput, templateHtml));
      }
    }
    const generated = generatedVariants[0];
    const project: Project = {
      id: `proj_${crypto.randomUUID().slice(0, 12)}`,
      name: cleanName(input.projectName || input.prompt || 'Proyecto creativo'),
      prompt: input.prompt || '',
      mode: generated.mode,
      createdAt: now,
      updatedAt: now,
      revisions: [],
      brandName: input.brandName,
      brandTokens: input.brandTokens,
    };
    const savedVariants = [];
    for (let index = 0; index < generatedVariants.length; index++) {
      const variant = generatedVariants[index];
      savedVariants.push(await persistRevision(
        project,
        variant.html,
        input.prompt || '',
        variant.width,
        variant.height,
        count === 1 ? 'Principal' : `Concepto ${index + 1}`,
        variant.trace,
      ));
    }
    const saved = savedVariants[0];
    index.projects.unshift(project);
    await saveIndex(index);
    const board = await writeProjectBoard(project);
    return {
      ok: true,
      action: input.action,
      project: { id: project.id, name: project.name, mode: project.mode },
      revision: saved.revision.id,
      revisions: savedVariants.map((item) => item.revision.id),
      files: [
        ...savedVariants.flatMap((item) => [item.htmlOutput, item.pngOutput]),
        board.htmlOutput,
        board.pngOutput,
      ],
      healed: generated.healed,
      healAttempts: generated.attempts,
      pipeline: summarizePipeline(generatedVariants.map((item) => item.trace)),
    };
  }

  const project = index.projects.find((item) => item.id === input.projectId);
  if (!project) throw new Error('No encontré ese proyecto creativo en el almacén privado.');
  const revision = input.revisionId
    ? project.revisions.find((item) => item.id === input.revisionId)
    : project.revisions.at(-1);
  if (!revision) throw new Error('El proyecto no tiene una revisión disponible.');
  const revisionPath = path.resolve(revision.htmlPath);
  const allowedProjectRoot = path.resolve(stateRoot(), project.id, 'revisions');
  if (!revisionPath.startsWith(`${allowedProjectRoot}${path.sep}`)) {
    throw new Error('La revisión apunta fuera del almacén privado de Studio.');
  }
  const html = await readFile(revisionPath, 'utf8');
  if (Buffer.byteLength(html) > MAX_HTML_BYTES) {
    throw new Error('La revisión HTML supera el límite seguro.');
  }

  if (input.action === 'history' || input.action === 'variants') {
    return {
      ok: true,
      project: {
        id: project.id,
        name: project.name,
        mode: project.mode,
        brandName: project.brandName,
        createdAt: project.createdAt,
        updatedAt: project.updatedAt,
      },
      revisions: project.revisions.map((item) => ({
        id: item.id,
        label: item.label,
        width: item.width,
        height: item.height,
        instruction: item.instruction,
        createdAt: item.createdAt,
        archivedAt: item.archivedAt,
        trace: item.trace,
      })),
    };
  }
  if (input.action === 'archive' || input.action === 'restore') {
    project.archivedAt = input.action === 'archive' ? new Date().toISOString() : undefined;
    project.updatedAt = new Date().toISOString();
    await saveIndex(index);
    return {
      ok: true,
      action: input.action,
      project: { id: project.id, name: project.name, archivedAt: project.archivedAt },
      reversible: true,
    };
  }
  if (input.action === 'duplicate') {
    if (index.projects.length >= MAX_PROJECTS) {
      throw new Error('Studio alcanzó el máximo local de 100 proyectos.');
    }
    const now = new Date().toISOString();
    const clone: Project = {
      id: `proj_${crypto.randomUUID().slice(0, 12)}`,
      name: cleanName(input.projectName || `${project.name} copia`),
      prompt: project.prompt,
      mode: project.mode,
      createdAt: now,
      updatedAt: now,
      revisions: [],
      brandName: project.brandName,
      brandTokens: project.brandTokens,
    };
    const files: string[] = [];
    for (const source of project.revisions.filter((item) => !item.archivedAt)) {
      const sourceHtml = await readFile(
        pathInside(path.join(stateRoot(), project.id, 'revisions'), source.htmlPath),
        'utf8',
      );
      const saved = await persistRevision(
        clone,
        sourceHtml,
        source.instruction,
        source.width,
        source.height,
        source.label,
        source.trace,
      );
      files.push(saved.htmlOutput, saved.pngOutput);
    }
    index.projects.unshift(clone);
    await saveIndex(index);
    const board = await writeProjectBoard(clone);
    return {
      ok: true,
      action: 'duplicate',
      sourceProjectId: project.id,
      project: { id: clone.id, name: clone.name, mode: clone.mode },
      files: [...files, board.htmlOutput, board.pngOutput],
    };
  }
  if (input.action === 'brand-health') {
    return { ok: true, action: 'brand-health', projectId: project.id, report: await brandHealth(project) };
  }
  if (input.action === 'tidy') {
    const proposed = tidySuggestions(project);
    const requested = Array.isArray(input.tidyActions) ? input.tidyActions.slice(0, 100) : [];
    let applied = 0;
    for (const action of requested) {
      const target = project.revisions.find((item) => item.id === action.revisionId);
      if (!target) continue;
      if (action.kind === 'rename-revision' && action.label?.trim()) {
        target.label = cleanName(action.label);
        applied++;
      } else if (action.kind === 'archive-revision' && !target.archivedAt) {
        target.archivedAt = new Date().toISOString();
        applied++;
      } else if (action.kind === 'restore-revision' && target.archivedAt) {
        target.archivedAt = undefined;
        applied++;
      }
    }
    if (applied) {
      project.updatedAt = new Date().toISOString();
      await saveIndex(index);
      await writeProjectBoard(project);
    }
    return {
      ok: true,
      action: 'tidy',
      projectId: project.id,
      applied,
      proposed,
      reversibleActions: ['archive-revision', 'restore-revision'],
    };
  }
  if (input.action === 'template-save') {
    if (index.templates.length >= 100) throw new Error('Studio alcanzó el máximo de 100 plantillas.');
    const templateId = `tpl_${crypto.randomUUID().slice(0, 12)}`;
    const templateDir = path.join(stateRoot(), 'templates');
    await mkdir(templateDir, { recursive: true, mode: 0o700 });
    const htmlPath = path.join(templateDir, `${templateId}.html`);
    await writeFile(htmlPath, html, { encoding: 'utf8', mode: 0o600 });
    const template: ProjectTemplate = {
      id: templateId,
      name: cleanName(input.templateName || `${project.name} plantilla`),
      description: String(input.templateDescription || '').slice(0, 500),
      category: input.templateCategory || 'other',
      createdAt: new Date().toISOString(),
      sourceProjectId: project.id,
      width: revision.width,
      height: revision.height,
      mode: project.mode,
      htmlPath,
    };
    index.templates.unshift(template);
    await saveIndex(index);
    return {
      ok: true,
      action: 'template-save',
      template: { ...template, htmlPath: undefined },
    };
  }
  if (input.action === 'design-system-generate') {
    const fallback = fallbackDesignTokens(project);
    const generated = await callAIJSON<{ name?: string; tokens?: Record<string, unknown> }>(
      `Genera un sistema de diseño completo, coherente y listo para producción para esta marca.\n` +
      `Marca: ${project.brandName || project.name}\n` +
      `Contexto y tokens actuales: ${(project.brandTokens || '').slice(0, 30_000)}\n` +
      `Devuelve {"name":"...","tokens":{"colors":{},"typography":{},"spacing":[],"borderRadius":16,"hasDarkMode":true}}.`,
      {
        system: 'Eres un arquitecto de sistemas visuales. No inventes hechos de negocio. Devuelve JSON válido.',
        temperature: 0.25,
        maxTokens: 4_000,
        json: true,
      },
    ).catch(() => null);
    const now = new Date().toISOString();
    for (const system of index.designSystems) {
      if (system.projectId === project.id) system.isActive = false;
    }
    const previous = index.designSystems.filter((system) => system.projectId === project.id);
    const designSystem: PublishedDesignSystem = {
      id: `ds_${crypto.randomUUID().slice(0, 12)}`,
      projectId: project.id,
      name: cleanName(generated?.name || `${project.brandName || project.name} Design System`),
      version: previous.reduce((highest, system) => Math.max(highest, system.version), 0) + 1,
      publishedAt: now,
      isActive: true,
      tokens: generated?.tokens && typeof generated.tokens === 'object' ? generated.tokens : fallback,
    };
    index.designSystems.unshift(designSystem);
    project.brandTokens = JSON.stringify(designSystem.tokens);
    project.updatedAt = now;
    await saveIndex(index);
    await mkdir(outputRoot(), { recursive: true, mode: 0o700 });
    const jsonOutput = path.join(outputRoot(), `${project.id}-${designSystem.id}.design-system.json`);
    await writeFile(jsonOutput, `${JSON.stringify(designSystem, null, 2)}\n`, { encoding: 'utf8', mode: 0o600 });
    return { ok: true, action: 'design-system-generate', designSystem, files: [jsonOutput] };
  }
  if (input.action === 'export') {
    const format = input.exportFormat || 'png';
    await mkdir(outputRoot(), { recursive: true, mode: 0o700 });
    const target = path.join(outputRoot(), `${project.id}-${revision.id}.${format}`);
    if (format === 'html') {
      await writeFile(target, html, { encoding: 'utf8', mode: 0o600 });
    } else if (format === 'pdf') {
      await writeFile(target, await renderHtmlToPdf(html, revision.width, revision.height), { mode: 0o600 });
    } else {
      await writeFile(
        target,
        await renderHtmlToPng(html, revision.width, revision.height, revision.id, { blockNetwork: true }),
        { mode: 0o600 },
      );
    }
    return { ok: true, action: 'export', format, revision: revision.id, files: [target] };
  }
  if (input.action === 'share-package') {
    const board = await writeProjectBoard(project);
    const manifestPath = path.join(outputRoot(), `${project.id}-share.json`);
    await writeFile(manifestPath, `${JSON.stringify({
      schemaVersion: 1,
      project: { id: project.id, name: project.name, mode: project.mode },
      revision: revision.id,
      createdAt: new Date().toISOString(),
      files: [path.basename(board.htmlOutput), path.basename(board.pngOutput)],
      note: 'Paquete local privado. Edecán no lo publicó en Internet.',
    }, null, 2)}\n`, { encoding: 'utf8', mode: 0o600 });
    return {
      ok: true,
      action: 'share-package',
      privacy: 'local-private',
      files: [board.htmlOutput, board.pngOutput, manifestPath],
    };
  }

  if (input.action === 'read') {
    const htmlOutput = path.join(outputRoot(), `${project.id}-${revision.id}.html`);
    await mkdir(outputRoot(), { recursive: true, mode: 0o700 });
    await copyFile(revision.htmlPath, htmlOutput);
    return {
      ok: true,
      project: { id: project.id, name: project.name, mode: project.mode },
      revision: revision.id,
      files: [htmlOutput],
    };
  }
  if (input.action === 'render') {
    const pngOutput = path.join(outputRoot(), `${project.id}-${revision.id}.png`);
    await mkdir(outputRoot(), { recursive: true, mode: 0o700 });
    const png = await renderHtmlToPng(html, revision.width, revision.height, revision.id, {
      blockNetwork: true,
    });
    await writeFile(pngOutput, png, { mode: 0o600 });
    return { ok: true, action: 'render', revision: revision.id, files: [pngOutput] };
  }
  if (input.action === 'edit') {
    const generated = await generateHtml(input, html);
    const saved = await persistRevision(
      project,
      generated.html,
      input.instruction || '',
      generated.width,
      generated.height,
      'Revisión',
      generated.trace,
    );
    await saveIndex(index);
    const board = await writeProjectBoard(project);
    return {
      ok: true,
      action: 'edit',
      project: { id: project.id, name: project.name, mode: project.mode },
      revision: saved.revision.id,
      previousRevision: revision.id,
      files: [saved.htmlOutput, saved.pngOutput, board.htmlOutput, board.pngOutput],
      healed: generated.healed,
      healAttempts: generated.attempts,
      pipeline: summarizePipeline([generated.trace]),
    };
  }
  throw new Error(`Acción de proyecto no soportada: ${input.action}`);
}

async function readInput(): Promise<ProjectInput> {
  const argv = process.argv[2];
  if (argv) return JSON.parse(argv) as ProjectInput;
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of process.stdin) {
    const buffer = Buffer.from(chunk);
    size += buffer.length;
    if (size > 16 * 1024 * 1024) throw new Error('El payload JSON supera el límite de 16 MB.');
    chunks.push(buffer);
  }
  const raw = Buffer.concat(chunks).toString('utf8').trim();
  if (!raw) throw new Error('Se requiere un objeto JSON por stdin o argv.');
  return JSON.parse(raw) as ProjectInput;
}

readInput()
  .then(main)
  .then((result) => process.stdout.write(`${JSON.stringify(result)}\n`))
  .catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
