// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Master HTML Builder — static, sandboxed design board                       ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import path from 'node:path';
import { getDb } from '@/lib/db';

export interface MasterHtmlConfig {
  projectId: string;
  projectName: string;
  brandName: string;
  brandLogoUrl?: string;
  variants: Array<{
    id: string;
    label: string;
    width: number;
    height: number;
    html: string;
  }>;
  variantCount: number;
  createdAt: string;
}

const PROJECTS_ROOT = path.resolve(/*turbopackIgnore: true*/ process.cwd(), 'outputs', 'projects');

export function getProjectDir(slug: string): string {
  return path.join(PROJECTS_ROOT, slug);
}

export function getMasterHtmlPath(slug: string): string {
  return path.join(getProjectDir(slug), 'master.html');
}

export function getVariantsDir(slug: string): string {
  return path.join(getProjectDir(slug), 'variants');
}

export function ensureProjectDirs(_slug: string): void {
  // No-op: DB-backed storage, no filesystem directories needed on Cloud Run.
}

function escapeHtml(text: unknown): string {
  if (text == null) return '';
  const value = text instanceof Date ? text.toISOString() : String(text);
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/**
 * Build a visible board without JavaScript. Each already-sanitized artifact is
 * embedded through srcdoc in a unique-origin iframe with no sandbox privileges.
 */
export function buildMasterHtml(config: MasterHtmlConfig): string {
  const date = new Date(config.createdAt);
  const dateLabel = Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString('es');
  const cards = config.variants.map((variant) => `
    <article class="variant-card" id="variant-${escapeHtml(variant.id)}">
      <header class="variant-heading">
        <strong>${escapeHtml(variant.label)}</strong>
        <span>${variant.width} × ${variant.height}px</span>
      </header>
      <iframe
        title="${escapeHtml(variant.label)}"
        sandbox=""
        loading="eager"
        style="aspect-ratio:${variant.width}/${variant.height}"
        srcdoc="${escapeHtml(variant.html)}"></iframe>
    </article>`).join('\n');

  return `<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'none'; img-src data: blob:; font-src data:; frame-src 'self'; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'">
<title>${escapeHtml(config.projectName)} — Edecán Studio</title>
<meta name="fydesign-project-id" content="${escapeHtml(config.projectId)}">
<style>
*{box-sizing:border-box}html{background:#0a0a0b;color:#f4f4f5;font-family:Inter,-apple-system,BlinkMacSystemFont,sans-serif}body{margin:0;min-height:100vh;overflow:auto}.board-header{position:sticky;top:0;z-index:2;display:flex;gap:14px;align-items:center;padding:18px 24px;background:rgba(10,10,11,.96);border-bottom:1px solid #27272a}.board-header strong{font-size:17px}.board-header span{color:#a1a1aa;font-size:12px}.brand{margin-left:auto;color:#5eead4}.board-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,520px),1fr));gap:24px;padding:24px}.variant-card{min-width:0;overflow:hidden;border:1px solid #27272a;border-radius:18px;background:#18181b;box-shadow:0 18px 60px rgba(0,0,0,.3)}.variant-heading{display:flex;justify-content:space-between;gap:16px;padding:12px 16px;border-bottom:1px solid #27272a}.variant-heading span{color:#a1a1aa;font-size:12px}.variant-card iframe{display:block;width:100%;min-height:360px;border:0;background:#fff}@media(max-width:600px){.board-header{align-items:flex-start;flex-direction:column}.brand{margin-left:0}.board-grid{padding:12px;gap:12px}}
</style>
</head>
<body>
<header class="board-header"><strong>${escapeHtml(config.projectName)}</strong><span>${config.variantCount} variante${config.variantCount === 1 ? '' : 's'} · ${escapeHtml(dateLabel)}</span><span class="brand">${escapeHtml(config.brandName || 'Edecán Studio')}</span></header>
<main class="board-grid">${cards}</main>
</body>
</html>`;
}

export async function regenerateMasterHtml(projectId: string): Promise<string | null> {
  try {
    const sql = getDb();
    const [project] = await sql`
      SELECT dp.*, bc.company_name, bc.logo_url
      FROM design_projects dp
      LEFT JOIN brand_configs bc ON bc.id = dp.brand_id
      WHERE dp.id = ${projectId}
    ` as Array<Record<string, unknown>>;

    if (!project) return null;

    if (!project.company_name && project.brand_id) {
      console.warn(`[master-html-builder] Orphan brand_id ${project.brand_id} on project ${project.id} — brand_configs row missing`);
    }

    const variants = await sql`
      SELECT id, label, width, height, html_content, sort_order
      FROM design_variants
      WHERE project_id = ${projectId}
      ORDER BY sort_order
    ` as Array<Record<string, unknown>>;
    const variantHtmls = variants.map((variant) => ({
      id: variant.id as string,
      label: variant.label as string,
      width: variant.width as number,
      height: variant.height as number,
      html: (variant.html_content as string) || '<!-- variant HTML not found -->',
    }));
    const masterHtml = buildMasterHtml({
      projectId,
      projectName: project.display_name as string,
      brandName: (project.company_name as string) || 'fydesign',
      brandLogoUrl: project.logo_url as string | undefined,
      variants: variantHtmls,
      variantCount: variantHtmls.length,
      createdAt: project.created_at as string,
    });
    const masterPath = getMasterHtmlPath(project.slug as string);
    if (!project.master_html_path) {
      await sql`UPDATE design_projects SET master_html_path = ${masterPath} WHERE id = ${projectId}`;
    }
    return masterHtml;
  } catch (error) {
    console.error('[MasterHTML] regeneration failed:', error);
    return null;
  }
}
