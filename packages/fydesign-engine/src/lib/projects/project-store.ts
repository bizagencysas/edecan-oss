// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Project Store — Core CRUD for persistent design projects                    ║
// ║  Dual persistence: DB (indexing/relationships) + FS (portable HTML files)    ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import crypto from 'crypto';
import { getDb } from '@/lib/db';
import { generateSlug, slugMatchScore } from './slug-resolver';
import { generateProjectName } from './name-generator';
import {
  ensureProjectDirs,
  getMasterHtmlPath,
  getVariantsDir,
} from './master-html-builder';

// ── Types ──────────────────────────────────────────────────────────────────

export interface DesignProjectRow {
  id: string;
  brandId: string;
  slug: string;
  displayName: string;
  description: string | null;
  prompt: string;
  enrichedPrompt: string | null;
  mode: string;
  creativeMode: string;
  masterHtmlPath: string | null;
  variantCount: number;
  status: 'active' | 'archived' | 'deleted';
  createdAt: string;
  updatedAt: string;
}

export interface DesignVariantRow {
  id: string;
  projectId: string;
  label: string;
  folder: string | null;
  width: number;
  height: number;
  description: string | null;
  artDirection: string | null;
  visualMove: string | null;
  cssAmbition: string | null;
  htmlFilePath: string;
  qualityScore: number | null;
  critiqueIterations: number;
  sortOrder: number;
  createdAt: string;
}

// ── ID generators ──────────────────────────────────────────────────────────

function genProjectId(): string {
  return 'proj_' + crypto.randomUUID().slice(0, 12);
}

function genVariantId(): string {
  return 'var_' + crypto.randomUUID().slice(0, 12);
}

// ── Row mapping ────────────────────────────────────────────────────────────

function mapProjectRow(row: Record<string, unknown>): DesignProjectRow {
  return {
    id: row.id as string,
    brandId: row.brand_id as string,
    slug: row.slug as string,
    displayName: row.display_name as string,
    description: row.description as string | null,
    prompt: row.prompt as string,
    enrichedPrompt: row.enriched_prompt as string | null,
    mode: row.mode as string,
    creativeMode: row.creative_mode as string,
    masterHtmlPath: row.master_html_path as string | null,
    variantCount: row.variant_count as number,
    status: row.status as 'active' | 'archived' | 'deleted',
    createdAt: (row.created_at as Date).toISOString(),
    updatedAt: (row.updated_at as Date).toISOString(),
  };
}

function mapVariantRow(row: Record<string, unknown>): DesignVariantRow {
  return {
    id: row.id as string,
    projectId: row.project_id as string,
    label: row.label as string,
    folder: (row.folder as string | null) || null,
    width: row.width as number,
    height: row.height as number,
    description: row.description as string | null,
    artDirection: row.art_direction as string | null,
    visualMove: row.visual_move as string | null,
    cssAmbition: row.css_ambition as string | null,
    htmlFilePath: row.html_file_path as string,
    qualityScore: row.quality_score as number | null,
    critiqueIterations: row.critique_iterations as number,
    sortOrder: row.sort_order as number,
    createdAt: (row.created_at as Date).toISOString(),
  };
}

// ── Project CRUD ───────────────────────────────────────────────────────────

export async function createProject(input: {
  brandId: string;
  displayName?: string;
  prompt: string;
  mode?: string;
  creativeMode?: string;
  description?: string;
  slug?: string;
}): Promise<DesignProjectRow> {
  const sql = getDb();
  const id = genProjectId();
  const displayName = input.displayName || generateProjectName(input.prompt, input.mode || 'general');
  const slug = input.slug || generateSlug(displayName);
  const mode = input.mode || 'general';
  const creativeMode = input.creativeMode || 'balanced';

  // Guard against slug collision (concurrent creates with the same name)
  const [existing] = await sql`
    SELECT 1 FROM design_projects WHERE brand_id = ${input.brandId} AND slug = ${slug} LIMIT 1
  `;
  const finalSlug = existing ? `${slug}-${Date.now().toString(36).slice(-4)}` : slug;

  const [row] = await sql`
    INSERT INTO design_projects (id, brand_id, slug, display_name, prompt, mode, creative_mode, description)
    VALUES (${id}, ${input.brandId}, ${finalSlug}, ${displayName}, ${input.prompt}, ${mode}, ${creativeMode}, ${input.description || null})
    RETURNING *
  `;

  return mapProjectRow(row as Record<string, unknown>);
}

export async function findProjectById(id: string): Promise<DesignProjectRow | null> {
  const sql = getDb();
  const rows = await sql`SELECT * FROM design_projects WHERE id = ${id} LIMIT 1`;
  return rows[0] ? mapProjectRow(rows[0] as Record<string, unknown>) : null;
}

export async function findProjectBySlug(brandId: string, slug: string): Promise<DesignProjectRow | null> {
  const sql = getDb();
  const rows = await sql`
    SELECT * FROM design_projects
    WHERE brand_id = ${brandId} AND slug = ${slug} AND status != 'deleted'
    LIMIT 1
  `;
  return rows[0] ? mapProjectRow(rows[0] as Record<string, unknown>) : null;
}

export async function resolveProjectByReference(
  ref: string,
  brandId?: string,
): Promise<DesignProjectRow | null> {
  const sql = getDb();

  // 1. Try exact ID match
  if (ref.startsWith('proj_')) {
    const byId = await findProjectById(ref);
    if (byId && byId.status !== 'deleted') return byId;
  }

  // 2. Try exact slug match
  const querySlug = generateSlug(ref);
  const rows = brandId
    ? await sql`SELECT * FROM design_projects WHERE brand_id = ${brandId} AND status != 'deleted'`
    : await sql`SELECT * FROM design_projects WHERE status != 'deleted'`;

  if ((rows as Array<Record<string, unknown>>).length === 0) return null;

  // Score each project by slug match
  const scored = (rows as Array<Record<string, unknown>>)
    .map((row) => ({
      row,
      score: slugMatchScore(querySlug, (row.slug as string) || ''),
    }))
    .filter((s) => s.score > 0.3)
    .sort((a, b) => b.score - a.score);

  if (scored.length === 0) return null;

  const best = scored[0];
  if (best.score >= 0.5) {
    return mapProjectRow(best.row);
  }

  return null;
}

export async function findOldestProjectByBrandId(brandId: string): Promise<DesignProjectRow | null> {
  const sql = getDb();
  const rows = await sql`
    SELECT * FROM design_projects
    WHERE brand_id = ${brandId} AND status != 'deleted'
    ORDER BY created_at ASC LIMIT 1
  `;
  return rows[0] ? mapProjectRow(rows[0] as Record<string, unknown>) : null;
}

export async function listProjects(
  brandId?: string,
  limit = 50,
  order: 'desc' | 'asc' = 'desc',
): Promise<DesignProjectRow[]> {
  const sql = getDb();
  let rows;
  if (brandId) {
    if (order === 'asc') {
      rows = await sql`
        SELECT * FROM design_projects
        WHERE brand_id = ${brandId} AND status != 'deleted'
        ORDER BY created_at ASC LIMIT ${limit}
      `;
    } else {
      rows = await sql`
        SELECT * FROM design_projects
        WHERE brand_id = ${brandId} AND status != 'deleted'
        ORDER BY updated_at DESC LIMIT ${limit}
      `;
    }
  } else {
    if (order === 'asc') {
      rows = await sql`
        SELECT * FROM design_projects
        WHERE status != 'deleted'
        ORDER BY created_at ASC LIMIT ${limit}
      `;
    } else {
      rows = await sql`
        SELECT * FROM design_projects
        WHERE status != 'deleted'
        ORDER BY updated_at DESC LIMIT ${limit}
      `;
    }
  }
  return (rows as Array<Record<string, unknown>>).map(mapProjectRow);
}

export async function updateProject(
  id: string,
  updates: Partial<Pick<DesignProjectRow, 'displayName' | 'description' | 'status' | 'creativeMode'>>,
): Promise<DesignProjectRow | null> {
  const sql = getDb();
  if (Object.keys(updates).length === 0) return findProjectById(id);

  // Read current values first
  const [current] = await sql`SELECT * FROM design_projects WHERE id = ${id} LIMIT 1`;
  if (!current) return null;

  const cur = current as Record<string, unknown>;
  const dn = (updates.displayName ?? cur.display_name) as string;
  const desc = (updates.description !== undefined ? updates.description : cur.description) as string | null;
  const st = (updates.status ?? cur.status) as string;
  const cm = (updates.creativeMode ?? cur.creative_mode) as string;

  const [row] = await sql`
    UPDATE design_projects
    SET display_name = ${dn}, description = ${desc}, status = ${st}, creative_mode = ${cm}, updated_at = NOW()
    WHERE id = ${id}
    RETURNING *
  `;
  return row ? mapProjectRow(row as Record<string, unknown>) : null;
}

// ── Variant CRUD ───────────────────────────────────────────────────────────

/**
 * Infer a folder name from the design label or mode.
 * Examples: "1/5 — Hook" → "carousel", "Landing Page" → "landing-page"
 */
function inferFolder(label: string, mode?: string): string {
  const lower = label.toLowerCase();

  // Carousel/slide pattern: "1/5 — Hook", "Slide 1", etc.
  if (/^\d+\/\d+\s*[—–-]/.test(label) || /\bslide\b/i.test(lower)) return 'carousel';

  // Landing page
  if (/\blanding\b/i.test(lower)) return 'landing-page';

  // Mockup
  if (/\bmockup/i.test(lower)) return 'mockups';

  // Ad / social
  if (/\b(ad|anuncio|social|post|instagram|facebook|twitter|tiktok)\b/i.test(lower)) return 'ads';

  // Email
  if (/\bemail\b/i.test(lower)) return 'email';

  // Hero / section
  if (/\b(hero|header|nav|footer|pricing|features|testimonials?)\b/i.test(lower)) return 'sections';

  // App screens
  if (/\b(screen|pantalla|app|onboarding|login|dashboard|settings|profile)\b/i.test(lower)) return 'app-screens';

  // Presentation / deck
  if (/\b(deck|presentaci[oó]n|presentation)\b/i.test(lower) || mode === 'deck') return 'deck';

  // Mode-based fallback
  if (mode === 'carousel') return 'carousel';
  if (mode === 'deck') return 'deck';

  // Default
  return 'designs';
}

/**
 * Check if a folder already exists for this project, and reuse it if the content matches.
 * If 'carousel' folder exists and user asks for more carousel slides, add to it.
 */
async function resolveFolder(projectId: string, inferredFolder: string): Promise<string> {
  const sql = getDb();
  // Check existing folders for this project
  const rows = await sql`
    SELECT DISTINCT folder FROM design_variants
    WHERE project_id = ${projectId} AND folder IS NOT NULL
    ORDER BY folder
  `;
  const existingFolders = (rows as Array<Record<string, unknown>>).map(r => r.folder as string);

  // If the inferred folder already exists, reuse it
  if (existingFolders.includes(inferredFolder)) return inferredFolder;

  // Otherwise use the inferred folder (new folder will be created)
  return inferredFolder;
}

export async function addVariant(input: {
  projectId: string;
  label: string;
  width: number;
  height: number;
  description?: string;
  artDirection?: string;
  visualMove?: string;
  cssAmbition?: string;
  html: string;
  folder?: string;
  mode?: string;
}): Promise<DesignVariantRow> {
  const sql = getDb();
  const id = genVariantId();

  const project = await findProjectById(input.projectId);
  if (!project) throw new Error(`Project ${input.projectId} not found`);

  // Smart folder resolution
  const inferredFolder = input.folder || inferFolder(input.label, input.mode);
  const folder = await resolveFolder(input.projectId, inferredFolder);

  // Determine next sort order
  const [maxOrd] = await sql`
    SELECT COALESCE(MAX(sort_order), -1) as max_ord FROM design_variants
    WHERE project_id = ${input.projectId}
  `;
  const sortOrder = (((maxOrd as Record<string, unknown>)?.max_ord as number) || -1) + 1;

  // Store HTML directly in DB (Cloud Run has ephemeral filesystem)
  const htmlFileName = `${id}.html`;
  const htmlFilePath = `outputs/projects/${project.slug}/${folder}/${htmlFileName}`;

  const [row] = await sql`
    INSERT INTO design_variants
      (id, project_id, label, folder, width, height, description, art_direction, visual_move, css_ambition, html_file_path, html_content, sort_order)
    VALUES
      (${id}, ${input.projectId}, ${input.label}, ${folder}, ${input.width}, ${input.height},
       ${input.description || null}, ${input.artDirection || null},
       ${input.visualMove || null}, ${input.cssAmbition || null},
       ${htmlFilePath}, ${input.html}, ${sortOrder})
    RETURNING *
  `;

  // Update project variant count
  await sql`
    UPDATE design_projects
    SET variant_count = (SELECT COUNT(*) FROM design_variants WHERE project_id = ${input.projectId}),
        updated_at = NOW()
    WHERE id = ${input.projectId}
  `;

  return mapVariantRow(row as Record<string, unknown>);
}

export async function getVariants(projectId: string): Promise<DesignVariantRow[]> {
  const sql = getDb();
  const rows = await sql`
    SELECT * FROM design_variants
    WHERE project_id = ${projectId}
    ORDER BY sort_order
  `;
  return (rows as Array<Record<string, unknown>>).map(mapVariantRow);
}

export async function getVariantHtml(variantId: string): Promise<string | null> {
  const sql = getDb();
  const rows = await sql`SELECT html_content FROM design_variants WHERE id = ${variantId} LIMIT 1`;
  if (!rows[0]) return null;
  return (rows[0] as Record<string, unknown>).html_content as string || null;
}

export async function updateVariant(
  id: string,
  updates: { html?: string; qualityScore?: number; critiqueIterations?: number },
): Promise<void> {
  const sql = getDb();

  if (updates.html) {
    await sql`
      UPDATE design_variants SET html_content = ${updates.html}, updated_at = NOW()
      WHERE id = ${id}
    `;
  }

  if (updates.qualityScore !== undefined || updates.critiqueIterations !== undefined) {
    const [cur] = await sql`SELECT quality_score, critique_iterations FROM design_variants WHERE id = ${id} LIMIT 1`;
    if (cur) {
      const qs = updates.qualityScore ?? (cur as Record<string, unknown>).quality_score;
      const ci = updates.critiqueIterations ?? (cur as Record<string, unknown>).critique_iterations;
      await sql`
        UPDATE design_variants SET quality_score = ${qs as number | null}, critique_iterations = ${ci as number}, updated_at = NOW()
        WHERE id = ${id}
      `;
    }
  }
}

export async function deleteVariant(id: string): Promise<void> {
  const sql = getDb();
  const rows = await sql`SELECT project_id FROM design_variants WHERE id = ${id} LIMIT 1`;
  if (!rows[0]) return;

  await sql`DELETE FROM design_variants WHERE id = ${id}`;

  await sql`
    UPDATE design_projects
    SET variant_count = GREATEST(0, variant_count - 1),
        updated_at = NOW()
    WHERE id = ${rows[0].project_id}
  `;
}

// ── Helpers for Gemini Orchestrator ────────────────────────────────────

/**
 * Get all distinct folder names currently used in a project.
 * Fed to Gemini Orchestrator so it reuses existing folders byte-identically.
 */
export async function getExistingFolders(projectId: string): Promise<string[]> {
  const sql = getDb();
  const rows = await sql`
    SELECT DISTINCT folder FROM design_variants
    WHERE project_id = ${projectId} AND folder IS NOT NULL AND folder != ''
    ORDER BY folder
  ` as Array<{ folder: string }>;
  return rows.map(r => r.folder);
}

/**
 * Get labels of all variants in a project (for Next Action Suggester context).
 */
export async function getProjectVariantLabels(projectId: string): Promise<string[]> {
  const sql = getDb();
  const rows = await sql`
    SELECT label FROM design_variants
    WHERE project_id = ${projectId}
    ORDER BY created_at DESC LIMIT 50
  ` as Array<{ label: string }>;
  return rows.map(r => r.label);
}

// ── Design Memory: search variants by keyword + date ──────────────────

export interface VariantSearchResult {
  id: string;
  projectId: string;
  label: string;
  folder: string | null;
  description: string | null;
  width: number;
  height: number;
  createdAt: string;
}

/**
 * Search variants by keyword (label/folder/description) and optional date range.
 * Returns lightweight metadata — no HTML — for Gemini resolution.
 */
export async function searchVariants(params: {
  projectId?: string;
  searchTerms?: string[];
  dateFrom?: Date;
  dateTo?: Date;
  limit?: number;
}): Promise<VariantSearchResult[]> {
  const sql = getDb();
  const limit = params.limit || 15;

  // Build dynamic conditions
  const conditions: string[] = [];
  if (params.projectId) {
    conditions.push(`project_id = '${params.projectId}'`);
  }
  if (params.dateFrom) {
    conditions.push(`created_at >= '${params.dateFrom.toISOString()}'`);
  }
  if (params.dateTo) {
    conditions.push(`created_at <= '${params.dateTo.toISOString()}'`);
  }

  // For keyword search, we do ILIKE across label, folder, description
  const keywordClauses = (params.searchTerms || [])
    .filter(t => t.length >= 2)
    .map(term => {
      const escaped = term.replace(/'/g, "''").replace(/%/g, '\\%');
      return `(label ILIKE '%${escaped}%' OR folder ILIKE '%${escaped}%' OR COALESCE(description, '') ILIKE '%${escaped}%')`;
    });

  if (keywordClauses.length > 0) {
    conditions.push(`(${keywordClauses.join(' OR ')})`);
  }

  const whereClause = conditions.length > 0
    ? `WHERE ${conditions.join(' AND ')}`
    : '';

  const rows = await sql.unsafe(`
    SELECT id, project_id, label, folder, description, width, height, created_at
    FROM design_variants
    ${whereClause}
    ORDER BY created_at DESC
    LIMIT ${limit}
  `) as unknown as Array<Record<string, unknown>>;

  return rows.map(r => ({
    id: r.id as string,
    projectId: r.project_id as string,
    label: r.label as string,
    folder: (r.folder as string) || null,
    description: (r.description as string) || null,
    width: r.width as number,
    height: r.height as number,
    createdAt: (r.created_at as Date).toISOString(),
  }));
}
