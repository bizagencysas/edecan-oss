/** Optional public brand knowledge registry.
 *
 * Private, hand-curated owner data is deliberately excluded from the OSS
 * engine. Brands are registered at runtime through `fydesign_register_brand`.
 */
export interface BrandRealData {
  aliases: string[];
  name: string;
  tagline: string;
  blurb: string;
  colors: string[];
  palette: Record<string, string>;
  fonts: { display: string; body: string; mono: string };
  logoUrl: string;
  valueProps: string[];
  screens: Array<{ route: string; name: string; shows: string }>;
  followerTiers: Array<{ label: string; threshold: string }>;
  facts: { confirmed: string[]; unknownDoNotInvent: string[] };
}

const PUBLIC_BRANDS: BrandRealData[] = [];

export function findBrandRealData(
  ...hints: Array<string | undefined | null>
): BrandRealData | null {
  const candidates = hints.filter(Boolean).map((hint) => String(hint).toLowerCase());
  if (!candidates.length) return null;
  return PUBLIC_BRANDS.find((brand) =>
    brand.aliases.some((alias) =>
      candidates.some((candidate) => candidate.includes(alias) || alias.includes(candidate)),
    ),
  ) || null;
}

export function brandRealDataBlock(brand: BrandRealData): string {
  return [
    `REAL BRAND DATA — ${brand.name} (authoritative; do not invent other facts):`,
    `- What it is: ${brand.blurb}`,
    `- Tagline: ${brand.tagline}`,
    `- Palette: ${Object.entries(brand.palette).map(([key, value]) => `${key} ${value}`).join(', ')}`,
    `- Fonts: ${brand.fonts.display} (display), ${brand.fonts.body} (body)`,
    `- Confirmed facts: ${brand.facts.confirmed.join(' ')}`,
    `- Do not invent: ${brand.facts.unknownDoNotInvent.join(' ')}`,
  ].join('\n');
}
