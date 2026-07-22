// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Brand Asset Reader — Reads ALL uploaded brand assets (fonts, CSS, HTML,    ║
// ║  JSX) and converts them into actionable design intelligence for Opus.      ║
// ║  "I have your project, your fonts, your components. I'm your designer."    ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { readFromGCS } from '@/lib/gcs';
import type { RegisteredAsset } from '@/lib/corpus/asset-registry';

// ── Types ───────────────────────────────────────────────────────────────────

export interface BrandFontFace {
  family: string;
  weight: number;
  style: 'normal' | 'italic';
  format: 'truetype' | 'opentype' | 'woff' | 'woff2';
  /** URL to serve the font file (e.g., /api/assets/ast-xxx/file) */
  url: string;
  /** Original filename for debugging */
  originalName: string;
}

export interface BrandDesignTokens {
  /** Raw CSS custom properties extracted from uploaded CSS files */
  cssVariables: Record<string, string>;
  /** Full CSS file content (truncated to fit token budget) */
  rawCssSnippet: string;
  /** Source filename */
  source: string;
}

export interface BrandComponent {
  /** Component name (e.g., "buttons", "glass-card", "inputs") */
  name: string;
  /** HTML source of the component preview */
  html: string;
  /** Source filename */
  source: string;
}

export interface BrandScreenDescription {
  /** Screen name (e.g., "screen-home", "screen-score") */
  name: string;
  /** Condensed description of what the screen contains */
  description: string;
  /** Source filename */
  source: string;
}

export interface BrandAssetIntelligence {
  /** @font-face CSS declarations ready to inject into <head> */
  fontFaceCSS: string;
  /** Font family names available (for Opus to reference) */
  fontFamilies: string[];
  /** Design tokens from uploaded CSS */
  designTokens: BrandDesignTokens[];
  /** Component HTML previews */
  components: BrandComponent[];
  /** App screen descriptions */
  screens: BrandScreenDescription[];
  /** Summary for Opus prompt */
  promptSummary: string;
}

// ── Font Weight/Style Detection ─────────────────────────────────────────────

const WEIGHT_MAP: Record<string, number> = {
  'thin': 100, 'hairline': 100,
  'extralight': 200, 'ultralight': 200,
  'light': 300,
  'regular': 400, 'normal': 400,
  'medium': 500,
  'demibold': 600, 'semibold': 600,
  'bold': 700,
  'extrabold': 800, 'ultrabold': 800,
  'black': 900, 'heavy': 900,
};

function parseFontMetadata(filename: string): { family: string; weight: number; style: 'normal' | 'italic' } {
  // Remove extension and path
  const name = filename.replace(/\.(ttf|otf|woff2?|eot)$/i, '').replace(/^.*[\\/]/, '');

  // Detect italic
  const isItalic = /italic/i.test(name);

  // Remove common prefixes: "TT Firs Neue Trial" → normalize
  // Also handle "Bold Outline" as a variant
  const cleaned = name
    .replace(/\s*Trial\s*/gi, ' ')
    .replace(/\s*Italic\s*/gi, '')
    .replace(/\s*Outline\s*/gi, '')
    .replace(/[-_]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();

  // Try to find weight from the name
  let weight = 400;
  const lowerCleaned = cleaned.toLowerCase();
  for (const [key, value] of Object.entries(WEIGHT_MAP)) {
    if (lowerCleaned.includes(key)) {
      weight = value;
      break;
    }
  }

  // Extract family name by removing weight keywords
  let family = cleaned;
  for (const key of Object.keys(WEIGHT_MAP)) {
    family = family.replace(new RegExp(`\\b${key}\\b`, 'gi'), '');
  }
  // Also remove "Var Roman", "Var"
  family = family.replace(/\bVar\s*Roman\b/gi, '').replace(/\bVar\b/gi, '');
  family = family.replace(/\s+/g, ' ').trim();

  // If family is empty after cleanup, use original
  if (!family) family = name.split(/[-_\s]/)[0] || 'Brand Font';

  return { family, weight, style: isItalic ? 'italic' : 'normal' };
}

function fontExtToFormat(ext: string): 'truetype' | 'opentype' | 'woff' | 'woff2' {
  switch (ext.toLowerCase()) {
    case 'otf': return 'opentype';
    case 'woff': return 'woff';
    case 'woff2': return 'woff2';
    default: return 'truetype';
  }
}

// ── CSS Variable Extraction ─────────────────────────────────────────────────

function extractCSSVariables(cssContent: string): Record<string, string> {
  const vars: Record<string, string> = {};
  const matches = cssContent.matchAll(/--([a-zA-Z0-9_-]+)\s*:\s*([^;]+);/g);
  for (const m of matches) {
    if (m[1] && m[2]) {
      vars[`--${m[1]}`] = m[2].trim();
    }
  }
  return vars;
}

// ── HTML Component Extraction ───────────────────────────────────────────────

function extractComponentName(filename: string): string {
  // "Acme Design System/preview/components-buttons.html" → "buttons"
  // "Acme Design System/preview/colors-accent.html" → "colors-accent"
  const base = filename.replace(/^.*[\\/]/, '').replace(/\.html$/i, '');
  return base
    .replace(/^components-/, '')
    .replace(/^colors-/, 'colors-')
    .replace(/^type-/, 'type-')
    .replace(/^spacing-/, 'spacing-');
}

function extractHTMLBody(html: string): string {
  // Extract just the body content, strip scripts and styles
  const bodyMatch = html.match(/<body[^>]*>([\s\S]*?)<\/body>/i);
  let content = bodyMatch ? bodyMatch[1]! : html;
  // Remove script tags
  content = content.replace(/<script[\s\S]*?<\/script>/gi, '');
  // Remove inline style tags (keep inline styles on elements)
  content = content.replace(/<style[\s\S]*?<\/style>/gi, '');
  return content.trim();
}

// ── JSX Screen Description ──────────────────────────────────────────────────

function extractScreenDescription(jsxContent: string, filename: string): string {
  // Extract key structural info from JSX without full parsing
  const screenName = filename.replace(/^.*[\\/]/, '').replace(/\.(jsx|tsx)$/i, '');

  const parts: string[] = [`Screen: ${screenName}`];

  // Find text content patterns
  const textMatches = jsxContent.match(/[">]([A-Z][^"<]{5,60})/g);
  if (textMatches) {
    const texts = textMatches
      .map(m => m.replace(/^[">]/, '').trim())
      .filter(t => !t.startsWith('import') && !t.startsWith('const') && !t.startsWith('function'))
      .slice(0, 5);
    if (texts.length > 0) parts.push(`Content: ${texts.join(' | ')}`);
  }

  // Find component names used
  const componentMatches = jsxContent.match(/<([A-Z][A-Za-z]+)/g);
  if (componentMatches) {
    const unique = [...new Set(componentMatches.map(m => m.slice(1)))].slice(0, 8);
    parts.push(`Components: ${unique.join(', ')}`);
  }

  // Find style patterns
  const styleColors = jsxContent.match(/#[0-9A-Fa-f]{3,8}/g);
  if (styleColors) {
    const unique = [...new Set(styleColors)].slice(0, 5);
    parts.push(`Colors used: ${unique.join(', ')}`);
  }

  return parts.join(' | ');
}

// ── Main Reader Function ────────────────────────────────────────────────────

/**
 * Read ALL uploaded brand assets and convert them into actionable intelligence.
 * This is the "I have your whole project" moment.
 */
export async function readBrandAssets(
  assets: RegisteredAsset[],
  baseUrl?: string,
): Promise<BrandAssetIntelligence> {
  const result: BrandAssetIntelligence = {
    fontFaceCSS: '',
    fontFamilies: [],
    designTokens: [],
    components: [],
    screens: [],
    promptSummary: '',
  };

  if (!assets || assets.length === 0) return result;

  const fontFaces: BrandFontFace[] = [];
  const cssFiles: { asset: RegisteredAsset; gcsPath: string }[] = [];
  const htmlFiles: { asset: RegisteredAsset; gcsPath: string }[] = [];
  const jsxFiles: { asset: RegisteredAsset; gcsPath: string }[] = [];

  // ── Categorize assets ──
  for (const asset of assets) {
    const gcsPath = (asset.metadata?.gcsPath as string) || '';
    const ext = asset.originalName.split('.').pop()?.toLowerCase() || '';
    const nameLower = asset.originalName.toLowerCase();

    if (['ttf', 'otf', 'woff', 'woff2'].includes(ext) || asset.category === 'font') {
      // Font file → generate @font-face
      const meta = parseFontMetadata(asset.originalName);
      const fontUrl = baseUrl
        ? `${baseUrl}/api/assets/${asset.id}/file`
        : `/api/assets/${asset.id}/file`;
      fontFaces.push({
        family: meta.family,
        weight: meta.weight,
        style: meta.style,
        format: fontExtToFormat(ext),
        url: fontUrl,
        originalName: asset.originalName,
      });
    } else if (ext === 'css' && gcsPath) {
      cssFiles.push({ asset, gcsPath });
    } else if (ext === 'html' && gcsPath && (nameLower.includes('preview/') || nameLower.includes('preview\\'))) {
      htmlFiles.push({ asset, gcsPath });
    } else if (['jsx', 'tsx'].includes(ext) && gcsPath) {
      jsxFiles.push({ asset, gcsPath });
    }
  }

  // ── 1. Generate @font-face CSS ──
  if (fontFaces.length > 0) {
    // Group by family
    const families = new Map<string, BrandFontFace[]>();
    for (const f of fontFaces) {
      const existing = families.get(f.family) || [];
      existing.push(f);
      families.set(f.family, existing);
    }

    const cssLines: string[] = [
      '/* ── Brand Fonts (uploaded to design system) ── */',
    ];

    for (const [family, faces] of families.entries()) {
      result.fontFamilies.push(family);
      // Sort by weight for clean output
      faces.sort((a, b) => a.weight - b.weight || (a.style === 'italic' ? 1 : 0) - (b.style === 'italic' ? 1 : 0));

      for (const face of faces) {
        cssLines.push(`@font-face {
  font-family: '${family}';
  src: url('${face.url}') format('${face.format}');
  font-weight: ${face.weight};
  font-style: ${face.style};
  font-display: swap;
}`);
      }
    }

    result.fontFaceCSS = cssLines.join('\n');
    console.log(`[BrandAssetReader] Generated ${fontFaces.length} @font-face declarations for ${families.size} families: ${result.fontFamilies.join(', ')}`);
  }

  // ── 2. Read CSS files ──
  for (const { asset, gcsPath } of cssFiles.slice(0, 6)) {
    try {
      const file = await readFromGCS(gcsPath);
      if (!file) continue;

      const cssContent = file.buffer.toString('utf-8');
      const variables = extractCSSVariables(cssContent);

      // Allow full CSS files up to 8KB
      const truncated = cssContent.length > 8192
        ? cssContent.slice(0, 8192) + '\n/* ... truncated ... */'
        : cssContent;

      result.designTokens.push({
        cssVariables: variables,
        rawCssSnippet: truncated,
        source: asset.originalName,
      });

      console.log(`[BrandAssetReader] Extracted ${Object.keys(variables).length} CSS variables from ${asset.originalName}`);
    } catch (e) {
      console.warn(`[BrandAssetReader] Failed to read CSS: ${asset.originalName}`, e instanceof Error ? e.message : e);
    }
  }

  // ── 3. Read HTML component previews ──
  // Prioritize the most useful ones
  const priorityComponents = ['buttons', 'glass-card', 'metal-card', 'card', 'inputs', 'pills', 'tabbar', 'icons'];
  const sortedHtml = htmlFiles.sort((a, b) => {
    const aName = extractComponentName(a.asset.originalName);
    const bName = extractComponentName(b.asset.originalName);
    const aPriority = priorityComponents.findIndex(p => aName.includes(p));
    const bPriority = priorityComponents.findIndex(p => bName.includes(p));
    return (aPriority === -1 ? 999 : aPriority) - (bPriority === -1 ? 999 : bPriority);
  });

  for (const { asset, gcsPath } of sortedHtml.slice(0, 10)) {
    try {
      const file = await readFromGCS(gcsPath);
      if (!file) continue;

      const htmlContent = file.buffer.toString('utf-8');
      const bodyContent = extractHTMLBody(htmlContent);

      // Only include if there's meaningful content (not just whitespace)
      if (bodyContent.length > 20 && bodyContent.length < 8000) {
        result.components.push({
          name: extractComponentName(asset.originalName),
          html: bodyContent.slice(0, 6000), // Up to 6KB per component for full fidelity
          source: asset.originalName,
        });
      }
    } catch (e) {
      console.warn(`[BrandAssetReader] Failed to read HTML: ${asset.originalName}`, e instanceof Error ? e.message : e);
    }
  }
  console.log(`[BrandAssetReader] Loaded ${result.components.length} component previews`);

  // ── 4. Read JSX screen descriptions ──
  for (const { asset, gcsPath } of jsxFiles.slice(0, 10)) {
    try {
      const file = await readFromGCS(gcsPath);
      if (!file) continue;

      const jsxContent = file.buffer.toString('utf-8');
      const description = extractScreenDescription(jsxContent, asset.originalName);

      result.screens.push({
        name: asset.originalName.replace(/^.*[\\/]/, '').replace(/\.(jsx|tsx)$/i, ''),
        description,
        source: asset.originalName,
      });
    } catch (e) {
      console.warn(`[BrandAssetReader] Failed to read JSX: ${asset.originalName}`, e instanceof Error ? e.message : e);
    }
  }
  console.log(`[BrandAssetReader] Loaded ${result.screens.length} screen descriptions`);

  // ── 5. Build prompt summary ──
  result.promptSummary = buildPromptSummary(result);

  return result;
}

// ── Prompt Summary Builder ──────────────────────────────────────────────────

function buildPromptSummary(intel: BrandAssetIntelligence): string {
  const parts: string[] = [];

  // Fonts
  if (intel.fontFamilies.length > 0) {
    parts.push(`BRAND FONTS (uploaded custom fonts — USE THESE, not Google Fonts):`);
    parts.push(`  Font families: ${intel.fontFamilies.join(', ')}`);
    parts.push(`  @font-face declarations are already injected into the HTML <head>.`);
    parts.push(`  Use font-family: '${intel.fontFamilies[0]}', sans-serif; in your CSS.`);
    parts.push(`  DO NOT use Google Fonts imports. DO NOT use Inter, Roboto, or any other font.`);
    parts.push(`  The brand font is already loaded — just reference it by name.`);
  }

  // CSS design tokens
  if (intel.designTokens.length > 0) {
    parts.push(`\nBRAND CSS DESIGN TOKENS (from uploaded CSS — use these EXACT values):`);
    for (const dt of intel.designTokens) {
      const varCount = Object.keys(dt.cssVariables).length;
      parts.push(`  Source: ${dt.source} (${varCount} CSS variables)`);
      // Include the most important variables directly
      const important = Object.entries(dt.cssVariables)
        .filter(([k]) => k.match(/--(color|bg|text|accent|primary|font|radius|spacing|surface|border)/i))
        .slice(0, 20);
      for (const [key, value] of important) {
        parts.push(`    ${key}: ${value};`);
      }
    }
  }

  // Component library
  if (intel.components.length > 0) {
    parts.push(`\nBRAND COMPONENT LIBRARY (${intel.components.length} components — match this visual style):`);
    for (const comp of intel.components.slice(0, 8)) {
      parts.push(`  ── ${comp.name} ──`);
      // Include a condensed version of the HTML
      const condensed = comp.html
        .replace(/\s+/g, ' ')
        .replace(/<!--[\s\S]*?-->/g, '')
        .trim()
        .slice(0, 1200);
      parts.push(`  ${condensed}`);
    }
  }

  // Screen descriptions
  if (intel.screens.length > 0) {
    parts.push(`\nAPP SCREENS (from uploaded JSX — this is what the actual app looks like):`);
    for (const screen of intel.screens.slice(0, 8)) {
      parts.push(`  • ${screen.description}`);
    }
  }

  return parts.join('\n');
}

/**
 * Load brand assets from the asset registry DB.
 */
export async function loadAndReadBrandAssets(
  brandId: string,
  baseUrl?: string,
): Promise<BrandAssetIntelligence> {
  try {
    const { loadAssetsByBrand } = await import('@/lib/corpus/asset-storage');
    const allAssets = await loadAssetsByBrand(brandId);

    console.log(`[BrandAssetReader] Loading assets for brand ${brandId}: ${allAssets.length} total assets`);

    return readBrandAssets(allAssets, baseUrl);
  } catch (e) {
    console.warn('[BrandAssetReader] Failed to load brand assets:', e instanceof Error ? e.message : e);
    return {
      fontFaceCSS: '',
      fontFamilies: [],
      designTokens: [],
      components: [],
      screens: [],
      promptSummary: '',
    };
  }
}
