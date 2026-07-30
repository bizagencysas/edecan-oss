// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Seed Corpus — 12 battle-tested design patterns for common scenarios       ║
// ║  Used as fallback when the DB corpus is empty, ensuring generation always  ║
// ║  has corpus intelligence to draw from.                                     ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import type { RepoBrainPattern } from './repo-corpus';

const SEED_PATTERNS: RepoBrainPattern[] = [
  // ─────────────────────────────────────────────────────────────────────────────
  // FINTECH — TRUST (2 patterns)
  // ─────────────────────────────────────────────────────────────────────────────
  {
    id: 'credit-score-gauge',
    title: 'Credit score gauge dashboard',
    appliesTo: ['fintech', 'credit', 'banking', 'score', 'dashboard'],
    signals: ['credit score', 'fico', 'credit building', 'financial health', 'monitoring'],
    rules: [
      'Place the credit score as the primary visual anchor using a large circular gauge or arc with the numerical score centered inside.',
      'Below the gauge, show 2-3 key factors affecting the score as compact horizontal bars with labels and status indicators.',
      'Use a restrained color system: one accent for the gauge fill (teal/blue for good scores), neutral grays for backgrounds, and green/amber/red only for status indicators.',
      'Include a clear CTA positioned below the factors, such as "See what changed" or "Boost my score", styled as the primary accent button.',
    ],
    cssMoves: [
      'SVG circular gauge using stroke-dasharray / stroke-dashoffset for the score arc, triggered on render',
      'CSS conic-gradient for the gauge background track with tabular-nums on the centered score digit',
      'Compact horizontal factor bars using flex layout with interior fill, label, and right-aligned status',
    ],
    avoid: [
      'Decorative illustrations or money imagery inside the gauge face',
      'More than five factors — cognitive overload destroys the dashboard feel',
      'Fake scores or invented financial values without disclaimer context',
    ],
    sourceRepos: ['seed-corpus.fintech'],
    weight: 0.95,
  },
  {
    id: 'bank-grade-security-badges',
    title: 'Bank-grade security trust badges',
    appliesTo: ['fintech', 'banking', 'security', 'trust', 'landing', 'investing'],
    signals: ['security', 'trust', 'secure', 'protection', 'encrypted', 'safe', 'fdic'],
    rules: [
      'Position security badges in a dedicated trust bar or grid near the conversion zone — never hidden in a footer.',
      'Each badge must have a recognizable visual pattern (shield icon, checkmark, lock) with a concise one-line label beneath.',
      'Use quiet, credible styling: no glowing effects, no competing accent colors, no stock clipart padlocks.',
      'Limit to 3-5 badges. FDIC/NCUA logos, SSL encryption, biometric auth, and regulatory language are credible signals.',
    ],
    cssMoves: [
      'Compact horizontal badge strip with 12-16px gap between items and a subtle bottom-border separator line',
      'SVG shield/lock icons rendered in a single muted accent color (desaturated blue or teal, never yellow or green)',
      'Grid layout for 2x2 or 2x3 badge arrangements on wider canvases using repeat(auto-fill, minmax(120px, 1fr))',
    ],
    avoid: [
      'Cheap stock security imagery like giant glowing padlocks or electromagnetic shields',
      'Overpromising language such as "military-grade encryption" or "unhackable"',
      'More than five badges — trust signals plateau and then diminish with saturation',
    ],
    sourceRepos: ['seed-corpus.fintech'],
    weight: 0.9,
  },

  // ─────────────────────────────────────────────────────────────────────────────
  // APP STORE — CONVERSION (3 patterns)
  // ─────────────────────────────────────────────────────────────────────────────
  {
    id: 'hero-phone-mockup',
    title: 'Hero with dimensional phone mockup',
    appliesTo: ['mockup', 'app store', 'play store', 'screenshot', 'mobile', 'hero', 'landing'],
    signals: ['phone frame', 'app screenshot', 'mobile app', 'iphone', 'mockup', 'device frame'],
    rules: [
      'The phone frame must be large (60-75% of canvas height), dimensional, with a realistic bezel, screen inset, and soft shadow.',
      'The screen content inside the phone must show a real app interface — never a generic gradient or placeholder.',
      'Position the headline and value prop to the left of or overlaid on the right side of the phone — never below.',
    ],
    cssMoves: [
      '3D-perspective phone frame using border-radius (40-48px for the bezel), box-shadow layering, and a subtle rotateX/rotateY transform',
      'Inner screen clipped to the frame with overflow: hidden and an interior border-radius matching the screen inset',
      'Headline set in clamp(36px, 5vw, 64px) with tight -0.02em letter-spacing and font-weight 700-800',
    ],
    avoid: [
      'Generic placeholder rectangles labeled as a "phone" or "device"',
      'Centered composition with a tiny phone and headline floating above empty space',
      'Rendering the canvas label, internal label, or "iPhone frame" instruction as visible text',
    ],
    sourceRepos: ['seed-corpus.app-store'],
    weight: 0.9,
  },
  {
    id: 'feature-grid-layout',
    title: 'Feature comparison grid',
    appliesTo: ['mockup', 'app store', 'landing', 'carousel', 'saas', 'feature'],
    signals: ['features', 'benefits', 'comparison', 'capabilities', 'highlights', 'what you get'],
    rules: [
      'Use a 2-column (narrow) or 3-column (wide) grid with each feature as a compact card containing an icon, headline, and one-line description.',
      'Keep the grid visually subordinate to the primary focal point — it should not compete with the hero.',
      'Alternate content and phone mockup columns or rows in multi-slide layouts for compositional rhythm.',
    ],
    cssMoves: [
      'CSS Grid with repeat(auto-fit, minmax(260px, 1fr)) for responsive equal-width columns',
      'Each card uses a subtle surface background (oklch difference of 1-2% from canvas) with 8-12px border-radius and no box-shadow',
      'Custom SVG icon set in a consistent 24x24 viewBox with 1.5px stroke, round caps, and round joins',
    ],
    avoid: [
      'Shadow-heavy cards that create visual noise or a cluttered "card farm" appearance',
      'More than 6 feature slots — edit down to the strongest differentiators',
      'Vague feature names like "Powerful Dashboard" or "Easy to Use" — use specific benefit-driven language',
    ],
    sourceRepos: ['seed-corpus.app-store'],
    weight: 0.85,
  },
  {
    id: 'testimonial-strip',
    title: 'Social proof testimonial strip',
    appliesTo: ['mockup', 'landing', 'carousel', 'social', 'proof', 'conversion'],
    signals: ['testimonial', 'review', 'rating', 'social proof', 'customers say', 'trust'],
    rules: [
      'Feature 2-3 testimonial cards in a horizontal strip with quotation marks, a short pull-quote, and an attribution line (name, title).',
      'Each card should have identical height but may vary slightly in width for a natural staggered appearance.',
      'Use a subtle background treatment (tonal surface layer, not a full card shadow) to separate from the main content area.',
    ],
    cssMoves: [
      'Horizontal flex row with gap: 16-20px and each card using flex: 1 with min-width: 0 for equal-height columns',
      'Large decorative opening quote mark positioned as a background element in the accent color at reduced opacity',
      'Star rating row using CSS-generated star characters or SVG stars in the brand accent color with 4px gap',
    ],
    avoid: [
      'Fake testimonials with stock photography and generic feel-good quotes',
      'More than 3 testimonials in a single row — they become unreadable at any reasonable size',
      'Identical-sounding quotes — each testimonial should offer a distinct perspective on value',
    ],
    sourceRepos: ['seed-corpus.app-store'],
    weight: 0.85,
  },

  // ─────────────────────────────────────────────────────────────────────────────
  // SOCIAL — CREATIVE (3 patterns)
  // ─────────────────────────────────────────────────────────────────────────────
  {
    id: 'bold-headline-gradient',
    title: 'Bold headline with gradient text',
    appliesTo: ['post', 'social', 'ad', 'carousel', 'landing', 'story'],
    signals: ['headline', 'bold statement', 'big text', 'hook', 'attention', 'social ad'],
    rules: [
      'The headline is the hero: set at 48-72px in display weight, covering 40-60% of the canvas height.',
      'Use a two-stop gradient on the headline text itself (not the background) to create visual impact without decoration.',
      'Keep supporting copy minimal — one sub-line of 16-20px at most. The headline does all the work.',
    ],
    cssMoves: [
      'background-clip: text with -webkit-text-fill-color: transparent for pure gradient text on a solid or simple background',
      'Headline using clamp(48px, 6vw, 72px) with -0.03em letter-spacing and font-weight 800',
      'Minimal background — solid color or subtle two-tone vertical split, never busy patterns or imagery behind text',
    ],
    avoid: [
      'Gradient backgrounds that compete with gradient text — one or the other, never both',
      'Headlines shorter than 4 words or longer than 12 words for social formats',
      'Multiple decorative elements near the headline — no icons, no badges, no flourishes',
    ],
    sourceRepos: ['seed-corpus.social'],
    weight: 0.9,
  },
  {
    id: 'phone-centric-square',
    title: 'Phone-centric square format',
    appliesTo: ['post', 'social', 'carousel', 'mockup', 'instagram'],
    signals: ['phone', 'app', 'square', 'instagram', 'slide', 'mobile demo'],
    rules: [
      'A single large phone mockup dominates the center of the square canvas, occupying roughly 70% of the height.',
      'Above or below the phone, a short 2-3 word headline or value prop in tight, tracked uppercase.',
      'The phone screen must show real app content specific to the brief — never gradients or empty placeholders.',
    ],
    cssMoves: [
      'Phone frame centered using absolute positioning with transform: translate(-50%, -50%) and a layered box-shadow for depth',
      'Subtle device shadow using multiple box-shadows: one wide ambient shadow and one tight directional shadow',
      'Uppercase headline in 14-16px with 0.08em letter-spacing positioned in the top 15% margin of the canvas',
    ],
    avoid: [
      'Portrait-oriented phone in a square frame with dead space above and below the device',
      'Multiple phones on one slide — a single large device is more impactful than a grid of small ones',
      'Text overlapping or obscuring the phone screen content',
    ],
    sourceRepos: ['seed-corpus.social'],
    weight: 0.88,
  },
  {
    id: 'proof-strip-bottom',
    title: 'Bottom proof strip for social',
    appliesTo: ['post', 'ad', 'carousel', 'landing', 'social'],
    signals: ['proof', 'stats', 'numbers', 'results', 'data', 'customers', 'social proof'],
    rules: [
      'Position a horizontal proof strip in the bottom 20-25% of the canvas with 3-4 key metrics.',
      'Each metric shows a large number (32-40px) with a small label beneath set in muted uppercase.',
      'Separate metrics with thin vertical rules — never use heavy dividers, background blocks, or card separators.',
    ],
    cssMoves: [
      'Flex row pinned to the bottom edge with a border-top separator in the muted/divider color',
      'Numbers set with font-variant-numeric: tabular-nums, font-weight 700, and 0 letter-spacing for alignment',
      'Labels in 10-11px uppercase with 0.06em letter-spacing and 60-70% opacity for clear hierarchy',
    ],
    avoid: [
      'Invented metrics — use an em dash (—) or honest ranges when real figures are unavailable',
      'More than 4 metrics in one row — attention dilutes with each additional figure',
      'Colored background blocks behind individual metrics — keep the strip clean and minimal',
    ],
    sourceRepos: ['seed-corpus.social'],
    weight: 0.85,
  },

  // ─────────────────────────────────────────────────────────────────────────────
  // LUXURY — EDITORIAL (2 patterns)
  // ─────────────────────────────────────────────────────────────────────────────
  {
    id: 'dark-premium-card',
    title: 'Dark premium card composition',
    appliesTo: ['landing', 'carousel', 'post', 'luxury', 'premium', 'vip', 'nightlife'],
    signals: ['premium', 'luxury', 'exclusive', 'elegant', 'dark theme', 'sophisticated', 'vip'],
    rules: [
      'Use a deep dark canvas (oklch(13-18% lightness)) with one premium product or visual element as the sole focal point.',
      'Typography must be restrained: one serif display face for the headline, sans-serif for body, with generous line-height (1.4+).',
      'The single accent color should appear exactly twice — once for the primary CTA or key number, once for a small decorative element.',
    ],
    cssMoves: [
      'Dark canvas with a subtle radial gradient wash emanating from behind the focal element using oklch transparency',
      'Glass-card effect using backdrop-filter: blur(20px) with a semi-transparent border highlight (0.05 opacity white) on the top edge',
      'Headline set in Playfair Display or similar serif at 40-56px with 0 letter-spacing for editorial weight and presence',
    ],
    avoid: [
      'Gold gradients, champagne overlays, and engraved ornamental borders — the cheapest luxury signifiers',
      'Overcrowding — premium is communicated through what you omit, not what you add',
      'Pure white text (#FFFFFF) on dark backgrounds — use off-white (#E8E8E8 or oklch(88% 0.01 70)) for readability',
    ],
    sourceRepos: ['seed-corpus.luxury'],
    weight: 0.92,
  },
  {
    id: 'serif-elegance-magazine',
    title: 'Serif elegance magazine layout',
    appliesTo: ['landing', 'editorial', 'post', 'carousel', 'luxury', 'magazine'],
    signals: ['editorial', 'magazine', 'elegant', 'sophisticated', 'fashion', 'premium', 'editorial layout'],
    rules: [
      'Use a large serif headline (48-72px) as the primary entry point, with generous tracking (0.02-0.05em) and leading (1.1-1.2).',
      'Composition follows magazine grid principles: strong asymmetry, ample whitespace on one side, dense content on the other.',
      'A single full-bleed or carefully cropped image anchors the layout, with typography wrapping or cleanly overlaying the image boundary.',
    ],
    cssMoves: [
      'Headline in Playfair Display, EB Garamond, or Cormorant with font-optical-sizing: auto for responsive weight',
      'Asymmetric grid using CSS Grid with unequal column fractions (e.g., 1fr 2fr or 35% 65%)',
      'Image edge treatment using mask-image with a linear gradient fade for editorial overlay transitions',
    ],
    avoid: [
      'Centered symmetrical layouts — asymmetry is the defining characteristic of editorial design',
      'Generic stock imagery — use specific product photography, brand visuals, or abstract texture fields',
      'More than two typeface families — editorial restraint means one serif display face and one sans-serif max',
    ],
    sourceRepos: ['seed-corpus.luxury'],
    weight: 0.9,
  },

  // ─────────────────────────────────────────────────────────────────────────────
  // SAAS — GENERAL (2 patterns)
  // ─────────────────────────────────────────────────────────────────────────────
  {
    id: 'dashboard-preview',
    title: 'Clean dashboard preview',
    appliesTo: ['landing', 'saas', 'dashboard', 'analytics', 'hero', 'mockup'],
    signals: ['dashboard', 'analytics', 'metrics', 'charts', 'data visual', 'platform', 'admin'],
    rules: [
      'Show a cropped or full dashboard view with real-looking data: line chart, stat cards row, a data table, and a sidebar or top navigation.',
      'Content IS the design — avoid heavy decorative flourishes. The data visualization and structural hierarchy carry the visual weight.',
      'Use a light or neutral dark palette with exactly one accent color for chart lines, selection states, and interactive elements.',
    ],
    cssMoves: [
      'SVG line chart using smooth bezier curves (C commands) with a gradient fill beneath the line using linearGradient',
      'Stat card grid using CSS Grid with auto-fill and minmax(180px, 1fr) for responsive column wrapping',
      'Sidebar with compact navigation items using a 3px border-left in the accent color for active state',
    ],
    avoid: [
      'Empty dashboard states with "coming soon" placeholder cards or disabled sections',
      'Chart shapes that do not represent actual metrics — never use sine waves or random noise as fake data',
      'Marketing language inside what should be a functional product interface view',
    ],
    sourceRepos: ['seed-corpus.saas'],
    weight: 0.88,
  },
  {
    id: 'cta-conversion-footer',
    title: 'Conversion-focused CTA section',
    appliesTo: ['landing', 'email', 'ad', 'carousel', 'saas', 'conversion'],
    signals: ['cta', 'sign up', 'get started', 'download', 'call to action', 'conversion', 'trial'],
    rules: [
      'The CTA button must be the most prominent element in the section: accent color background, comfortable vertical padding (16-20px), and clear action-oriented text.',
      'Support the CTA with a benefit line above (tighter tracking, muted color) and a trust or microcopy line below (small, reduced opacity).',
      'Use generous negative space around the CTA — no competing links, no secondary buttons, no dense surrounding text.',
    ],
    cssMoves: [
      'CTA button using the accent color background with 16-20px vertical padding, 24-32px horizontal padding, and 8-12px border-radius',
      'Benefit line in 14-16px with -0.01em tracking positioned above the button with 12-16px gap',
      'Trust/microcopy line below in 11-12px with 60% opacity and an inline SVG lock or check icon (12x12px)',
    ],
    avoid: [
      'Multiple CTAs in the same viewport section — one primary action per section is the rule',
      'Generic button text like "Learn More" or "Submit" — use specific action language ("Get My Score", "Start Free Trial")',
      'A CTA that blends into the background — it must contrast with both the canvas background and adjacent elements',
    ],
    sourceRepos: ['seed-corpus.saas'],
    weight: 0.87,
  },
];

export function getSeedPatterns(): RepoBrainPattern[] {
  return SEED_PATTERNS;
}
