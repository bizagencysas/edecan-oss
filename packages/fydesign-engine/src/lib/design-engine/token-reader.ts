// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  MODULE 2: Token Reader                                                    ║
// ║  Reads brand tokens from DB → converts to EXACT CSS variables + rules      ║
// ║  NOW ALSO loads visual assets (logo, screenshots) for vision-aware         ║
// ║  generation. The AI must SEE the brand, not just read about it.            ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

interface BrandConfig {
  company_name?: string | null;
  company_blurb?: string | null;
  brand_notes?: string | null;
  analysis_json?: unknown;
  repo_url?: string | null;
  logo_url?: string | null;
  uploaded_assets?: unknown;
}

export interface BrandVisualAssets {
  /** Logo image as base64 data (no data: prefix) with mimeType */
  logo?: { data: string; mimeType: string };
  /** App screenshots as base64 data with mimeType */
  screenshots: { data: string; mimeType: string; name: string }[];
}

function safeParse(val: unknown, fallback: unknown = {}): unknown {
  if (!val) return fallback;
  if (typeof val === 'object') return val;
  try { return JSON.parse(val as string); } catch { return fallback; }
}

/**
 * Download an image URL and return it as base64.
 * Handles both data: URLs and regular HTTP URLs.
 */
async function fetchImageAsBase64(url: string): Promise<{ data: string; mimeType: string } | null> {
  try {
    // If it's already a data URL, extract the base64 part
    const dataMatch = url.match(/^data:(image\/[^;]+);base64,(.+)$/);
    if (dataMatch) {
      return { mimeType: dataMatch[1], data: dataMatch[2] };
    }

    // Regular URL — fetch and convert
    const response = await fetch(url, { signal: AbortSignal.timeout(10000) });
    if (!response.ok) return null;

    const contentType = response.headers.get('content-type') || 'image/png';
    const mimeType = contentType.split(';')[0].trim();
    const arrayBuffer = await response.arrayBuffer();
    const data = Buffer.from(arrayBuffer).toString('base64');

    return { data, mimeType };
  } catch (e) {
    console.warn(`[TokenReader] Failed to fetch image: ${url.slice(0, 60)}`, e instanceof Error ? e.message : e);
    return null;
  }
}

/**
 * Extract design tokens from a brand configuration.
 * Returns CSS-level technical variables that the AI must follow exactly.
 */
export function extractBrandTokens(config: BrandConfig | null): string {
  if (!config) return '';

  const analysis = safeParse(config.analysis_json, {}) as Record<string, unknown>;
  const theme = (analysis?.theme || {}) as Record<string, unknown>;
  const brandIntel = (analysis?.brandIntelligence || {}) as Record<string, unknown>;

  const lines: string[] = [];

  // ── Company Identity ──
  if (config.company_name) lines.push(`BRAND: ${config.company_name}`);
  if (config.company_blurb) lines.push(`WHAT IT IS: ${config.company_blurb}`);
  if (brandIntel?.designStyle) lines.push(`DESIGN STYLE: ${brandIntel.designStyle}`);
  if (brandIntel?.appCategory) lines.push(`CATEGORY: ${brandIntel.appCategory}`);

  // ── CSS VARIABLES (exact technical tokens) ──
  lines.push('\nCSS VARIABLES (use these EXACTLY in your code):');

  // Background
  if (theme.backgroundColor) lines.push(`  --bg: ${theme.backgroundColor};`);
  if (theme.darkBackgroundColor) lines.push(`  --bg-dark: ${theme.darkBackgroundColor};`);

  // Text
  if (theme.textColor) lines.push(`  --text: ${theme.textColor};`);
  if (theme.darkTextColor) lines.push(`  --text-dark: ${theme.darkTextColor};`);

  // Primary/Secondary
  if (theme.primaryColor) lines.push(`  --primary: ${theme.primaryColor};`);
  if (theme.secondaryColor) lines.push(`  --secondary: ${theme.secondaryColor};`);

  // Accent colors
  if (Array.isArray(theme.accentColors) && theme.accentColors.length > 0) {
    const accents = theme.accentColors as string[];
    accents.forEach((hex, i) => lines.push(`  --accent-${i + 1}: ${hex};`));
    lines.push(`  --cta-color: ${accents[0]};  /* USE THIS for buttons and CTAs */`);
  } else if (theme.accentColor) {
    lines.push(`  --accent: ${theme.accentColor};`);
    lines.push(`  --cta-color: ${theme.accentColor};  /* USE THIS for buttons and CTAs */`);
  }

  // Status colors
  if (theme.successColor) lines.push(`  --success: ${theme.successColor};`);
  if (theme.warningColor) lines.push(`  --warning: ${theme.warningColor};`);
  if (theme.dangerColor) lines.push(`  --danger: ${theme.dangerColor};`);

  // Border radius
  if (theme.borderRadius) lines.push(`  --radius: ${theme.borderRadius}px;`);

  // ── BRAND DESIGN RULES (from brand_notes — these are CSS-level instructions) ──
  if (config.brand_notes) {
    lines.push(`\nBRAND DESIGN RULES (FOLLOW EXACTLY):\n${config.brand_notes}`);
  }

  // ── Brand Voice ──
  if (brandIntel?.brandVoice) {
    lines.push(`\nCOPY VOICE: ${String(brandIntel.brandVoice)}`);
  }

  // ── Key Features (for realistic content) ──
  if (Array.isArray(brandIntel?.keyFeatures)) {
    const features = brandIntel.keyFeatures as string[];
    lines.push(`\nKEY FEATURES (use in designs):\n${features.map(f => `  • ${f}`).join('\n')}`);
  }

  // ── App info ──
  if (analysis?.appName) lines.push(`\nAPP NAME: ${analysis.appName}`);
  if (analysis?.tagline) lines.push(`TAGLINE: ${analysis.tagline}`);

  // ── LOGO URL (embed directly in HTML with <img>) ──
  if (config.logo_url) {
    lines.push(`\nLOGO URL (use in <img src="..."> tags to embed the brand logo):`);
    if (config.logo_url.startsWith('data:image')) {
       lines.push(`  Use exactly this placeholder: {{BRAND_LOGO}}`);
    } else {
       lines.push(`  ${config.logo_url}`);
    }
  }

  // ── Asset URLs (screenshots, icons etc.) ──
  if (config.uploaded_assets) {
    try {
      const assets = typeof config.uploaded_assets === 'string' ? JSON.parse(config.uploaded_assets) : config.uploaded_assets;
      if (Array.isArray(assets)) {
        const imageAssets = (assets as Array<{ url: string; isImage?: boolean; name?: string; category?: string }>)
          .filter(a => a.isImage || a.category === 'images')
          .slice(0, 12);
        if (imageAssets.length > 0) {
          lines.push(`\nBRAND IMAGE URLs (use these in <img> tags when you need app screenshots or brand images):`);
          imageAssets.forEach((a, i) => {
            // CRITICAL FIX: Do not dump massive base64 strings into the text prompt
            if (a.url && a.url.startsWith('data:image')) {
              lines.push(`  • ${a.name || 'asset'}: Use exactly this placeholder: {{BRAND_IMAGE_${i}}}`);
            } else {
              lines.push(`  • ${a.name || 'asset'}: ${a.url}`);
            }
          });
        }
      }
    } catch { /* skip */ }
  }

  // ── Screen descriptions (so the AI knows what the app actually looks like) ──
  const screens = analysis?.screens as Array<Record<string, unknown>> | undefined;
  if (screens && screens.length > 0) {
    lines.push(`\nAPP SCREENS (${screens.length} detected — use these for realistic mockup content):`);
    screens.slice(0, 15).forEach((s) => {
      const components = Array.isArray(s.components) ? (s.components as string[]).slice(0, 10).join(', ') : '';
      const texts = Array.isArray(s.texts) ? (s.texts as string[]).slice(0, 6).join(' | ') : '';
      lines.push(`  • ${s.screenName} (${s.estimatedLayout}): [${components}] — "${texts}"`);
    });
    lines.push(`When showing the app in a phone mockup, recreate the ACTUAL app UI from these screens — not a generic placeholder.`);
  }

  return lines.join('\n');
}

/**
 * Load brand config from DB and extract tokens.
 */
export async function loadBrandTokens(brandId: string): Promise<string> {
  if (!brandId) return '';
  try {
    const { loadBrandConfig } = await import('@/lib/db');
    const config = await loadBrandConfig(brandId);
    return extractBrandTokens(config);
  } catch (e) {
    console.warn('[TokenReader] Brand token load failed:', e);
    return '';
  }
}

/**
 * Load visual assets (logo + screenshots) from the brand config.
 * Returns base64-encoded images ready to pass as inlineData to Gemini.
 *
 * This is the KEY missing piece — the AI needs to SEE the brand's visual identity,
 * not just read color hex codes. Passing the actual logo and app screenshots
 * as vision input dramatically improves output fidelity.
 */
export async function loadBrandVisualAssets(brandId: string): Promise<BrandVisualAssets> {
  const result: BrandVisualAssets = { screenshots: [] };
  if (!brandId) return result;

  try {
    const { loadBrandConfig } = await import('@/lib/db');
    const config = await loadBrandConfig(brandId);
    if (!config) return result;

    // ── Load logo ──
    if (config.logo_url) {
      console.log(`[TokenReader] Loading logo from: ${config.logo_url.slice(0, 60)}...`);
      const logo = await fetchImageAsBase64(config.logo_url);
      if (logo) {
        result.logo = logo;
        console.log(`[TokenReader] Logo loaded: ${logo.mimeType}, ${Math.round(logo.data.length / 1024)}KB base64`);
      }
    }

    // ── Load uploaded assets (screenshots, additional images) ──
    if (config.uploaded_assets) {
      try {
        const assets = typeof config.uploaded_assets === 'object'
          ? config.uploaded_assets
          : JSON.parse(config.uploaded_assets as string);

        if (Array.isArray(assets)) {
          const imageAssets = (assets as Array<{ url: string; isImage?: boolean; name?: string; category?: string }>)
            .filter(a => a.isImage || a.category === 'images')
            .slice(0, 8); // Allow up to 8 screenshots for full visual context

          console.log(`[TokenReader] Loading ${imageAssets.length} visual assets...`);

          const settled = await Promise.allSettled(
            imageAssets.map(async (asset) => {
              const img = await fetchImageAsBase64(asset.url);
              if (img) return { ...img, name: asset.name || 'asset' };
              return null;
            })
          );
          settled.filter(r => r.status === 'rejected').forEach(r => console.warn('[token-reader] asset failed:', (r as PromiseRejectedResult).reason));
          const fetched = settled
            .filter((r): r is PromiseFulfilledResult<Awaited<ReturnType<typeof fetchImageAsBase64>> & { name: string } | null> => r.status === 'fulfilled')
            .map(r => r.value);

          result.screenshots = fetched.filter((s): s is NonNullable<typeof s> => s !== null);
          console.log(`[TokenReader] Loaded ${result.screenshots.length}/${imageAssets.length} visual assets`);
        }
      } catch (e) {
        console.warn('[TokenReader] Failed to parse uploaded_assets:', e);
      }
    }
  } catch (e) {
    console.warn('[TokenReader] Visual asset load failed:', e);
  }

  return result;
}

/**
 * Load the full analysis JSON from the brand config.
 * Contains screens[], theme, framework, etc. — used by screen-resolver
 * to find specific screens by route and return their source code.
 */
export async function loadBrandAnalysis(brandId: string): Promise<Record<string, unknown> | null> {
  if (!brandId) return null;
  try {
    const { loadBrandConfig } = await import('@/lib/db');
    const config = await loadBrandConfig(brandId);
    if (!config) return null;

    const analysis = typeof config.analysis_json === 'object'
      ? config.analysis_json as Record<string, unknown>
      : JSON.parse(config.analysis_json || '{}') as Record<string, unknown>;

    return analysis;
  } catch (e) {
    console.warn('[TokenReader] Analysis load failed:', e);
    return null;
  }
}

// ── Full Brand Intelligence (tokens + assets) ─────────────────────────────

export interface FullBrandIntelligence {
  /** Text tokens for the Opus prompt */
  brandTokens: string;
  /** @font-face CSS to inject into <head> */
  fontFaceCSS: string;
  /** Font family names available */
  fontFamilies: string[];
  /** Full asset intelligence summary for Opus */
  assetPromptSummary: string;
  /** Component HTML snippets for reference */
  componentSnippets: string;
  /** Raw CSS design tokens from uploaded files */
  cssTokensSnippet: string;
}

/**
 * Load EVERYTHING about a brand — tokens, fonts, CSS, components, screens.
 * This is the "I have your whole project" function.
 */
export async function loadFullBrandIntelligence(
  brandId: string,
  baseUrl?: string,
): Promise<FullBrandIntelligence> {
  const empty: FullBrandIntelligence = {
    brandTokens: '',
    fontFaceCSS: '',
    fontFamilies: [],
    assetPromptSummary: '',
    componentSnippets: '',
    cssTokensSnippet: '',
  };

  if (!brandId) return empty;

  try {
    // Load tokens and full assets in parallel
    const [brandTokens, assetIntel] = await Promise.all([
      loadBrandTokens(brandId),
      import('./brand-asset-reader').then(m => m.loadAndReadBrandAssets(brandId, baseUrl)),
    ]);

    // Build component reference snippet (condensed HTML for Opus)
    let componentSnippets = '';
    if (assetIntel.components.length > 0) {
      componentSnippets = assetIntel.components
        .slice(0, 8)
        .map(c => `<!-- ${c.name} -->\n${c.html.slice(0, 4000)}`)
        .join('\n\n');
    }

    // Build raw CSS tokens snippet
    let cssTokensSnippet = '';
    if (assetIntel.designTokens.length > 0) {
      cssTokensSnippet = assetIntel.designTokens
        .map(dt => dt.rawCssSnippet)
        .join('\n\n')
        .slice(0, 12000); // Up to 12KB total CSS for full fidelity
    }

    return {
      brandTokens,
      fontFaceCSS: assetIntel.fontFaceCSS,
      fontFamilies: assetIntel.fontFamilies,
      assetPromptSummary: assetIntel.promptSummary,
      componentSnippets,
      cssTokensSnippet,
    };
  } catch (e) {
    console.warn('[TokenReader] Full brand intelligence load failed:', e instanceof Error ? e.message : e);
    return { ...empty, brandTokens: await loadBrandTokens(brandId).catch(() => '') };
  }
}
