// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Gemini Asset Matcher (C3)                                                 ║
// ║  For each design, intelligently maps placeholders to the best-fit brand    ║
// ║  assets based on the design's description and available assets.            ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { callGeminiJSON, GEMINI_PRO } from './gemini-client';

export interface AssetInfo {
  id: string;
  url: string;
  name: string;
  tags: string[];
  category: string;
}

/**
 * Ask Gemini to intelligently match design placeholders to brand assets.
 *
 * Instead of linear assignment ({{BRAND_IMAGE_0}} → first image),
 * Gemini reviews the design description and picks the most relevant asset
 * for each placeholder.
 *
 * Returns a mapping of placeholder → asset URL.
 */
export async function matchAssets(input: {
  designDescription: string;
  availableAssets: AssetInfo[];
  placeholders: string[];  // ['{{HERO_IMAGE}}', '{{ICON_1}}', etc.]
}): Promise<Record<string, string>> {
  if (input.availableAssets.length === 0 || input.placeholders.length === 0) {
    return {};
  }

  const assetCatalog = input.availableAssets
    .map((a, i) => `  ${i}. "${a.name}" [${a.category}] tags=[${a.tags.join(',')}] url=${a.url}`)
    .join('\n');

  const prompt = `You are an asset matcher for fydesign. Given a design description and available brand assets, pick the BEST asset for each placeholder.

DESIGN DESCRIPTION: "${input.designDescription}"

AVAILABLE ASSETS:
${assetCatalog}

PLACEHOLDERS TO FILL: ${input.placeholders.join(', ')}

For each placeholder, pick the asset whose content best matches what the design needs at that point. For example:
- If the design mentions "dashboard score" → pick a dashboard screenshot
- If the design mentions "pricing" → pick a pricing page screenshot
- If the design has a hero section → pick the most visually impactful image
- {{BRAND_LOGO}} always maps to the logo asset (if available)

Return JSON:
{ "mapping": { "{{HERO_IMAGE}}": "https://...asset-url...", "{{ICON_1}}": "https://...other-url..." } }

Only include placeholders that have a good match. Skip placeholders with no suitable asset.`;

  try {
    console.log(`[AssetHunter] Matching ${input.placeholders.length} placeholder(s) against ${input.availableAssets.length} asset(s)...`);
    const result = await callGeminiJSON<{ mapping: Record<string, string> }>(prompt, {
      model: GEMINI_PRO,
      temperature: 0.2,
      maxTokens: 1500,
    });

    const mapping = result?.mapping || {};
    console.log(`[AssetHunter] Matched ${Object.keys(mapping).length}/${input.placeholders.length} placeholder(s)`);
    return mapping;
  } catch (e) {
    console.warn('[AssetHunter] Failed:', e instanceof Error ? e.message : e);
    return {};
  }
}
