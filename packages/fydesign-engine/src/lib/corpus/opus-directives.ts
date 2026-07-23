// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Opus Directive Engine — Artistic director layer, taste intelligence         ║
// ║  "Opus thinks. DeepSeek builds." — comprehensive 13-domain taste profiles   ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import crypto from 'crypto';
import { saveCreativeDirective, loadCreativeDirectivesByDomain, loadLatestDirectives } from '@/lib/db';
import { hasAuth, callAIJSON } from '@/lib/ai/deepseek-client';

export interface CreativeDirective {
  id: string;
  name: string;
  domain: string;
  directiveText: string;
  heuristics: string[];
  antiPatterns: string[];
  extractionTargets: string[];
  scoringRubric: Record<string, number>;
  examplesGood: string[];
  examplesBad: string[];
  version: number;
}

export const ALL_DOMAINS = [
  'fintech', 'luxury', 'editorial', 'saas', 'dashboard',
  'app-store-screenshots', 'social-carousel', 'paid-ads', 'landing-pages',
  'glassmorphism', 'typography', 'animation', 'composition',
];

// ── Universal Design Principles (cross-domain) ────────────────────────────

const UNIVERSAL_RESTRAINT = [
  'Whitespace is a luxury material — spend it generously',
  'Every element must earn its place on the screen',
  'Prefer subtraction over addition when in doubt',
  'One dominant visual move per composition',
  'Reduce visual noise before adding decoration',
  'Typography hierarchy should be visible at 50% scale',
  'Color is seasoning, not the main ingredient',
];

const UNIVERSAL_ANTI_PATTERNS = [
  'Startup gradient fog (purple-to-blue backgrounds)',
  'Three equal feature cards above the fold',
  'Floating UI mockups on gradient blobs',
  'Centered generic hero with no focal point',
  'Over-decorated cards within cards',
  'Lorem ipsum or placeholder-heavy designs',
  'Stock illustrations pretending to be product',
  'Weak typography hierarchy (all same-size text)',
  'Fake data or impossible metrics in dashboards',
];

// ── Complete 13-Domain Directive Database ──────────────────────────────────

const BUILTIN_DIRECTIVES: Record<string, CreativeDirective> = {

  // ── 1. FINTECH ──────────────────────────────────────────────────────────

  fintech: {
    id: 'dir-fintech-v2',
    name: 'Premium Fintech Directive',
    domain: 'fintech',
    directiveText: 'Fintech design uses calm authority, structured cards, precise numerics, and trust cues. The visual language must feel secure, controlled, and credible — never playful or chaotic. Real financial data shapes the UI, not decorative elements.',
    heuristics: [
      'Use calm, controlled contrast — not neon excitement',
      'Prefer structured cards with precise numerics and trust cues',
      'Whitespace rhythm should feel controlled, not empty',
      'Real financial UI structure: balances, score gauges, payment history, transaction lists',
      'Trust proof should be visual and quiet: verification marks, security language, regulatory badges',
      'Tabular numerics with proper alignment and monospace where appropriate',
      'Color palette: deep navy, charcoal, trustworthy green accents, subtle red warnings',
      'Spacing: generous card padding, tight numeric alignment, breathing room between sections',
    ],
    antiPatterns: [
      'startup gradient fog',
      'cartoon money graphics or piggy banks',
      'fake crypto dashboard energy',
      'playful neobank noise with emojis',
      'exaggerated financial claims or get-rich-quick aesthetics',
      'charts with no axes, labels, or scale',
      'decorative cards inside financial dashboards',
      'gradients pretending to be data visualization',
    ],
    extractionTargets: [
      'balance cards', 'score rings', 'verification states', 'security copy placement',
      'financial chart styling', 'payment flows', 'transaction lists', 'account summaries',
      'fraud alert design', 'compliance badges', 'audit trail UIs', 'transfer confirmation screens',
    ],
    scoringRubric: { brandTrust: 0.3, layoutPrecision: 0.25, typographyMaturity: 0.2, visualNovelty: 0.15, reusePotential: 0.1 },
    examplesGood: ['Stripe Dashboard', 'Plaid design system', 'Monzo card design', 'Revolut app', 'Mercury bank'],
    examplesBad: ['Crypto casino UIs', 'Fake trading platforms', 'Cartoon piggy bank illustrations', 'Get-rich-quick landing pages'],
    version: 2,
  },

  // ── 2. LUXURY ───────────────────────────────────────────────────────────

  luxury: {
    id: 'dir-luxury-v2',
    name: 'Luxury Editorial Directive',
    domain: 'luxury',
    directiveText: 'Luxury design uses restraint, editorial composition, deeper blacks, and selective accent use. Make exclusivity visible through what is omitted, not what is added. The CTA should feel like access, not a purchase.',
    heuristics: [
      'Use restraint: fewer words, stronger cropping, deeper blacks',
      'Editorial composition: oversized type, asymmetry, vertical rhythm',
      'One tactile luxury cue per composition (leather texture, metallic accent, deep shadow)',
      'The CTA should feel like access, not a discount',
      'Controlled whitespace that feels intentional, never empty',
      'Photography should be cinematic, high-contrast, editorial-grade',
      'Typography: use serif or refined sans with generous letter-spacing',
      'Gold is cheap — use deep tones, cream, charcoal, and a single restrained metallic',
    ],
    antiPatterns: [
      'gold luxury clichés and metallic gradients',
      'cheap club flyer energy',
      'overcrowded event-poster layouts',
      'generic serif fonts pretending to be luxury',
      'discount badges or urgency timers',
      'stock photos of champagne and sports cars',
    ],
    extractionTargets: [
      'editorial hero layouts', 'premium card designs', 'membership pages',
      'event invitations', 'brand film layouts', 'lookbook compositions',
      'product showcase grids', 'typography-first layouts',
    ],
    scoringRubric: { brandTrust: 0.2, layoutPrecision: 0.3, typographyMaturity: 0.25, visualNovelty: 0.15, reusePotential: 0.1 },
    examplesGood: ['Fashion editorial sites', 'Watch brand landing pages', 'Membership club designs', 'High-end hotel websites', 'Sotheby\'s'],
    examplesBad: ['Gold-trimmed everything', 'Generic luxury templates', 'Fake marble textures', 'Discount countdown timers on premium products'],
    version: 2,
  },

  // ── 3. EDITORIAL ────────────────────────────────────────────────────────

  editorial: {
    id: 'dir-editorial-v1',
    name: 'Editorial Design Directive',
    domain: 'editorial',
    directiveText: 'Editorial design leads with typography, generous whitespace, and asymmetric composition. Content hierarchy is the primary visual system. Images support the story — they do not dominate it.',
    heuristics: [
      'Typography-first composition: headlines command attention, body text invites reading',
      'Asymmetric layouts with strong vertical rhythm',
      'Generous margins and breathing room around text blocks',
      'Images should be full-bleed or precisely framed — never floating',
      'Pull quotes and callouts as rhythmic breaks in the reading flow',
      'Serif for long-form reading, refined sans for UI chrome',
      'Line length: 55-75 characters for body text',
      'Drop caps, section numbers, and running headers add editorial authority',
    ],
    antiPatterns: [
      'centered text for long-form content',
      'images as decorative filler between paragraphs',
      'uniform text sizing without hierarchy',
      'overcrowded layouts with insufficient margins',
      'clickbait-style typography (all caps, oversized, urgent)',
    ],
    extractionTargets: [
      'article layouts', 'magazine-style grids', 'pull quote designs',
      'section header systems', 'footnote/sidenote patterns', 'table of contents designs',
      'author bio cards', 'related content grids',
    ],
    scoringRubric: { typographyMaturity: 0.35, layoutPrecision: 0.25, visualNovelty: 0.15, brandTrust: 0.15, reusePotential: 0.1 },
    examplesGood: ['The New Yorker', 'A24 films site', 'SSENSE editorial', 'Apple News', 'Linear blog'],
    examplesBad: ['Medium clones with no personality', 'Over-stylized blog templates', 'Clickbait content farms'],
    version: 1,
  },

  // ── 4. SAAS ─────────────────────────────────────────────────────────────

  saas: {
    id: 'dir-saas-v2',
    name: 'Premium SaaS Directive',
    domain: 'saas',
    directiveText: 'Premium SaaS design leads with product proof, structured sections, and conversion-aware composition. The visual should feel like a strategic operator, not a generic brochure. Show the real product surface.',
    heuristics: [
      'Lead with product proof and specificity — not vague value props',
      'Structured sections: diagnosis → intervention → proof → operating model → CTA',
      'Keep the composition dense enough for expertise, clean enough to scan',
      'Product-first: show real UI screenshots, not illustrations of UI',
      'Social proof integrated into the design, not as a separate testimonial carousel',
      'Interactive product demos or high-fidelity screenshots beat illustrations',
      'Pricing: transparent, comparison-aware, with clear value differentiation',
    ],
    antiPatterns: [
      'vague startup hero copy ("The future of X")',
      'cartoon SaaS illustrations replacing product screenshots',
      'gradient blob decoration as the main visual idea',
      'three equal feature cards above the fold',
      'testimonial carousel as an afterthought section',
      'fake metrics or impossible customer counts',
    ],
    extractionTargets: [
      'hero sections', 'proof blocks', 'pricing layouts', 'case study pages',
      'feature grids', 'integration logos', 'ROI calculators', 'comparison tables',
      'interactive demo sections', 'enterprise security pages',
    ],
    scoringRubric: { brandTrust: 0.2, layoutPrecision: 0.25, typographyMaturity: 0.15, visualNovelty: 0.2, reusePotential: 0.2 },
    examplesGood: ['Linear.app', 'Vercel marketing', 'Stripe product pages', 'Raycast', 'Figma marketing'],
    examplesBad: ['Generic SaaS templates', 'Stock illustration heroes', 'Feature dump grids', 'Over-promising landing pages'],
    version: 2,
  },

  // ── 5. DASHBOARD ────────────────────────────────────────────────────────

  dashboard: {
    id: 'dir-dashboard-v2',
    name: 'Dashboard Density Directive',
    domain: 'dashboard',
    directiveText: 'Dashboard design prioritizes information density, compact hierarchy, aligned controls, and real data-shaped UI. The dashboard should feel quiet, fast, and repeatable — not a marketing page.',
    heuristics: [
      'Information density is the feature — maximize data per pixel without clutter',
      'Compact hierarchy with aligned controls and consistent spacing',
      'Charts need labels, axes or implied scale, and accessible contrast',
      'Tabular numerics with proper decimal alignment throughout',
      'Operational tools should feel quiet, fast, repeatable — like a surgical instrument',
      'Left-aligned labels, right-aligned numerics in tables',
      'Consistent card padding: tight for data cards, generous for summary cards',
      'Color-coded status indicators should be subtle and accessible',
    ],
    antiPatterns: [
      'marketing hero scale inside dashboards',
      'decorative cards inside cards',
      'charts with no information architecture or missing axes',
      'fake decorative dashboards with impossible metrics',
      'oversized metric cards that waste screen real estate',
      'rainbow color schemes for data visualization',
    ],
    extractionTargets: [
      'metric cards', 'chart layouts', 'table designs', 'navigation patterns',
      'filter bars', 'search interfaces', 'empty states', 'loading skeletons',
      'notification panels', 'settings layouts', 'user management tables',
    ],
    scoringRubric: { layoutPrecision: 0.3, brandTrust: 0.1, typographyMaturity: 0.15, visualNovelty: 0.2, reusePotential: 0.25 },
    examplesGood: ['Vercel Analytics', 'Stripe Dashboard', 'Linear.app interface', 'GitHub dashboard', 'Notion databases'],
    examplesBad: ['Decorative admin templates', 'Oversized metric cards', 'Chart junk', 'Fake data dashboards'],
    version: 2,
  },

  // ── 6. APP-STORE-SCREENSHOTS ────────────────────────────────────────────

  'app-store-screenshots': {
    id: 'dir-appstore-v1',
    name: 'App Store Screenshot Directive',
    domain: 'app-store-screenshots',
    directiveText: 'App Store screenshots must communicate value in 2 seconds. Use device framing, bold typography, and one clear message per screenshot. Each screenshot is a self-contained ad.',
    heuristics: [
      'One clear message per screenshot with bold, readable typography',
      'Device framing with realistic shadows and perspective',
      'High contrast backgrounds that make the UI pop',
      'Feature callouts with precise, benefit-focused copy',
      'Consistent visual rhythm across all 6-8 screenshots',
      'Background colors should complement, not compete with, the app UI',
      'Caption text should be 3-7 words max — scannable at thumbnail size',
    ],
    antiPatterns: [
      'screenshots full of UI with no context or caption',
      'tiny illegible text in thumbnail preview',
      'inconsistent framing or device sizes across screenshots',
      'busy backgrounds that distract from the app UI',
      'fake or misleading UI states',
    ],
    extractionTargets: [
      'device mockup compositions', 'caption placement patterns', 'background treatments',
      'feature callout designs', 'screenshot grid layouts', 'before/after comparisons',
    ],
    scoringRubric: { visualNovelty: 0.3, layoutPrecision: 0.25, brandTrust: 0.2, typographyMaturity: 0.15, reusePotential: 0.1 },
    examplesGood: ['Apple award-winning apps', 'Superhuman screenshots', 'Things 3 App Store page', 'Halide camera app'],
    examplesBad: ['Screenshots with no captions', 'Cluttered feature lists on one screenshot', 'Fake UI with impossible states'],
    version: 1,
  },

  // ── 7. SOCIAL-CAROUSEL ──────────────────────────────────────────────────

  'social-carousel': {
    id: 'dir-carousel-v1',
    name: 'Social Carousel Directive',
    domain: 'social-carousel',
    directiveText: 'Social carousels need a strong first slide to earn the swipe, consistent visual language across slides, and a clear narrative arc. Each slide must work alone AND as part of the sequence.',
    heuristics: [
      'Slide 1 must hook: bold statement, surprising visual, or provocative question',
      'Consistent visual language: color palette, typography, framing across all slides',
      'Clear narrative arc: problem → insight → solution → proof → CTA',
      'Each slide should communicate one idea — swiping is cheap, confusion is expensive',
      'Large, readable typography optimized for mobile preview (minimum 24px equivalent)',
      'Strong visual rhythm with alternating layouts to maintain interest',
      'Final slide must have a clear, low-friction CTA',
      'Brand watermark or handle on every slide for attribution',
    ],
    antiPatterns: [
      'slide 1 that looks like a blog post title',
      'all slides with identical layout (boring to swipe)',
      'too much text per slide (it\'s a carousel, not an article)',
      'weak or missing CTA on final slide',
      'inconsistent branding across slides',
    ],
    extractionTargets: [
      'carousel first-slide hooks', 'slide transition patterns', 'CTA slide designs',
      'data-visualization slides', 'quote/testimonial slides', 'numbered progression designs',
    ],
    scoringRubric: { visualNovelty: 0.3, layoutPrecision: 0.2, brandTrust: 0.2, typographyMaturity: 0.2, reusePotential: 0.1 },
    examplesGood: ['Morning Brew carousels', 'Visualize Value designs', 'Jack Butcher visual essays', 'Airbnb Instagram'],
    examplesBad: ['Text-wall slides', 'Stock photo carousels', 'Inconsistent branding per slide', 'Carousels that forget the CTA'],
    version: 1,
  },

  // ── 8. PAID-ADS ─────────────────────────────────────────────────────────

  'paid-ads': {
    id: 'dir-ads-v1',
    name: 'Paid Ads Directive',
    domain: 'paid-ads',
    directiveText: 'Paid ads must stop the scroll in 0.5 seconds. Visual hierarchy is: image/visual → headline → subhead → CTA. The ad should be understandable at thumbnail size and compelling at full size.',
    heuristics: [
      'Visual-first: the image does 80% of the work, copy seals the deal',
      'Headline under 10 words — scannable at speed',
      'One clear CTA with action-oriented language',
      'High contrast between text and background for legibility',
      'Platform-native sizing and safe zones respected',
      'Social proof in ads: "Join 10,000+ teams" beats "Best tool ever"',
      'A/B test visual styles: illustration vs. product shot vs. typography-only',
      'Urgency without desperation: limited time > BUY NOW',
    ],
    antiPatterns: [
      'stock photography that screams "ad"',
      'walls of text that require reading to understand',
      'multiple competing CTAs in one ad',
      'tiny illegible terms and conditions',
      'clickbait that damages brand trust',
      'fake urgency ("Only 2 left!") when it\'s clearly false',
    ],
    extractionTargets: [
      'ad visual hierarchies', 'CTA button treatments', 'social proof badge placements',
      'product shot compositions', 'typography-only ad layouts', 'comparison ad designs',
    ],
    scoringRubric: { visualNovelty: 0.3, brandTrust: 0.2, layoutPrecision: 0.2, typographyMaturity: 0.15, reusePotential: 0.15 },
    examplesGood: ['Stripe ads', 'Notion paid social', 'Linear.app ads', 'Figma sponsored content'],
    examplesBad: ['Stock photo + generic headline', 'Multi-CTA confusion', 'Clickbait ads', 'Deceptive before/after'],
    version: 1,
  },

  // ── 9. LANDING-PAGES ────────────────────────────────────────────────────

  'landing-pages': {
    id: 'dir-landing-v2',
    name: 'Landing Pages Directive',
    domain: 'landing-pages',
    directiveText: 'Landing pages must have a clear visual hierarchy: focal point → proof/value → product surface → CTA. One dominant visual move per fold. Copy must be specific to the product.',
    heuristics: [
      'One dominant focal point per fold — the eye must know where to land',
      'Real product surfaces beat illustrations of product every time',
      'Clear hierarchy: headline → proof → product → operating model → CTA',
      'Trust cues integrated visually into the design, not as separate badge sections',
      'Social proof embedded in design flow, not relegated to a testimonial ghetto',
      'Pricing presented with confidence and comparison structure',
      'Footer as a conversion safety net, not an afterthought',
    ],
    antiPatterns: [
      'centered generic hero with purple-to-blue gradient',
      'three equal columns above fold with identical icon + title + text cards',
      'feature grid without hierarchy or priority',
      'testimonial section that looks like it was pasted from a different website',
      'CTA that says "Get Started" with no context about what happens next',
      'stock photography of diverse people pointing at whiteboards',
    ],
    extractionTargets: [
      'hero layouts', 'proof sections', 'CTA placements', 'pricing cards',
      'feature visualization', 'integration logo grids', 'demo/sandbox sections',
      'comparison tables', 'enterprise/security sections',
    ],
    scoringRubric: { layoutPrecision: 0.3, brandTrust: 0.15, typographyMaturity: 0.2, visualNovelty: 0.2, reusePotential: 0.15 },
    examplesGood: ['Linear homepage', 'Vercel landing', 'Raycast product page', 'Stripe Atlas', 'Figma landing'],
    examplesBad: ['Generic startup templates', 'Centered hero with blur bg', 'Overstuffed landing pages', 'Template marketplaces'],
    version: 2,
  },

  // ── 10. GLASSMORPHISM ───────────────────────────────────────────────────

  glassmorphism: {
    id: 'dir-glass-v1',
    name: 'Glassmorphism Restraint Directive',
    domain: 'glassmorphism',
    directiveText: 'Glassmorphism is a seasoning, not a meal. Use frosted glass effects only where they enhance depth perception or create visual hierarchy. Overuse creates visual noise and reduces legibility.',
    heuristics: [
      'Glass effects should reveal depth, not obscure content',
      'Use backdrop-blur with restraint: 8-20px, never 50px+',
      'Glass cards must have sufficient contrast against their background',
      'One or two glass elements per view max — not every card',
      'Pair glass with strong typography that stays readable over blurred backgrounds',
      'Glass works best over rich, dark, or photographic backgrounds — not flat white',
      'Border: subtle white/transparent stroke helps define the glass edge',
    ],
    antiPatterns: [
      'everything-is-glass interfaces',
      'glass over white backgrounds (invisible, pointless)',
      'excessive blur making text illegible',
      'glass as the only design idea on a page',
      'combining glass with heavy drop shadows (pick one depth cue)',
    ],
    extractionTargets: [
      'glass card implementations', 'backdrop-blur usage patterns', 'glass navigation bars',
      'modal/dialog glass treatments', 'glass + gradient combinations', 'frosted sidebar patterns',
    ],
    scoringRubric: { visualNovelty: 0.3, layoutPrecision: 0.25, brandTrust: 0.15, typographyMaturity: 0.2, reusePotential: 0.1 },
    examplesGood: ['Apple Vision Pro pages', 'macOS notification center', 'Stripe glass cards', 'Linear.app modals'],
    examplesBad: ['Everything-is-glass websites', 'Illegible glass text', 'Glass over flat white backgrounds'],
    version: 1,
  },

  // ── 11. TYPOGRAPHY ──────────────────────────────────────────────────────

  typography: {
    id: 'dir-typography-v1',
    name: 'Typography System Directive',
    domain: 'typography',
    directiveText: 'Typography is the foundation of design intelligence. A strong type system uses limited sizes, clear hierarchy, intentional pairing, and rhythm. Without typography hierarchy, all other design decisions fall apart.',
    heuristics: [
      'Limit to 5-7 type sizes in the scale — more is noise, fewer is rigid',
      'Type scale should follow a consistent ratio (1.25 major third or 1.333 perfect fourth)',
      'Body text: 16-18px with 1.5-1.6 line height for readability',
      'Headings: clear size jump between levels (h1=2.5x body, h2=1.8x, h3=1.3x)',
      'One display/heading font + one body font max — three fonts is a visual tax',
      'Letter-spacing: negative for large headlines, normal for body, positive for caps/labels',
      'Color: use weight and size for hierarchy before reaching for color',
      'Font loading: system fonts as fallback, proper font-display strategy',
    ],
    antiPatterns: [
      'too many font sizes (8+ different sizes on one page)',
      'weak hierarchy where all text looks the same size',
      'poor contrast text (gray on slightly different gray)',
      'fancy display fonts for body text',
      'centered text for anything longer than 3 lines',
      'all-caps for more than 5 words',
    ],
    extractionTargets: [
      'type scale definitions', 'font pairing patterns', 'heading hierarchy systems',
      'body text rhythm', 'label/caption treatments', 'typography-only hero sections',
    ],
    scoringRubric: { typographyMaturity: 0.4, layoutPrecision: 0.2, brandTrust: 0.15, visualNovelty: 0.15, reusePotential: 0.1 },
    examplesGood: ['Apple typography', 'The New York Times', 'Stripe docs', 'Linear.app type system', 'Vercel design'],
    examplesBad: ['8-font-size chaos', 'All-caps paragraphs', 'Fancy body text that hurts readability', 'Centered long-form text'],
    version: 1,
  },

  // ── 12. ANIMATION ───────────────────────────────────────────────────────

  animation: {
    id: 'dir-animation-v1',
    name: 'Animation Restraint Directive',
    domain: 'animation',
    directiveText: 'Animation serves the user\'s understanding, not the designer\'s ego. Motion should guide attention, provide feedback, and create continuity. Every animation must have a purpose beyond decoration.',
    heuristics: [
      'Animations should be 200-400ms for micro-interactions, 300-600ms for transitions',
      'Easing: use ease-out for entering elements, ease-in for exiting elements',
      'Stagger children by 50-100ms for lists and grids (not all at once)',
      'Scroll-triggered reveals should animate once, not on every scroll',
      'Reduce motion: respect prefers-reduced-motion media query',
      'Framer Motion spring animations should use stiffness 100-300, damping 15-30',
      'Page transitions should be subtle — fade + slight Y shift, not slide-from-mars',
    ],
    antiPatterns: [
      'animations over 1 second that block interaction',
      'everything animating on page load (motion vomit)',
      'scroll-jacking or smooth-scroll abuse',
      'bouncing elements that never settle',
      'rotation or scale transforms on text elements',
      'autoplay video or animation with no pause control',
    ],
    extractionTargets: [
      'fade-in patterns', 'staggered list reveals', 'page transitions',
      'hover state animations', 'loading skeleton animations', 'scroll-triggered reveals',
      'parallax implementations', 'animated number counters',
    ],
    scoringRubric: { visualNovelty: 0.3, layoutPrecision: 0.2, brandTrust: 0.15, typographyMaturity: 0.15, reusePotential: 0.2 },
    examplesGood: ['Linear.app transitions', 'Stripe animation', 'Apple product pages', 'Vercel deployment animations'],
    examplesBad: ['Motion vomit pages', 'Everything-bounces sites', 'Scroll-jacking websites', '3D transforms on body text'],
    version: 1,
  },

  // ── 13. COMPOSITION ─────────────────────────────────────────────────────

  composition: {
    id: 'dir-composition-v1',
    name: 'Composition Philosophy Directive',
    domain: 'composition',
    directiveText: 'Composition is the invisible architecture of premium design. Strong composition uses asymmetry, focal points, negative space, and intentional rhythm. Weak composition looks like a template no matter what colors or fonts you use.',
    heuristics: [
      'Rule of thirds: main focal point at intersection, not center',
      'Asymmetric balance: heavier element balanced by negative space + lighter element',
      'One clear entry point per view — the eye must know where to start',
      'Visual weight hierarchy: image > headline > color block > body text > UI chrome',
      'Z-pattern for scanning pages, F-pattern for reading pages',
      'Generous negative space between sections (120-200px) defines rhythm',
      'Grid: 12-column for flexibility, 4-column for simplicity, asymmetric for editorial',
      'Alignment: everything should align to something — no orphan spacing',
    ],
    antiPatterns: [
      'perfectly centered layouts (feels like a default, not a choice)',
      'equal spacing everywhere (no rhythm, no hierarchy)',
      'elements floating without alignment to anything',
      'sections that look copy-pasted from different websites',
      'overcrowded compositions with no breathing room',
      'Z-pattern on content meant for reading',
    ],
    extractionTargets: [
      'asymmetric hero layouts', 'grid-based compositions', 'focal point treatments',
      'negative space usage patterns', 'section rhythm patterns', 'Z-pattern landing pages',
      'editorial composition layouts', 'split-screen designs',
    ],
    scoringRubric: { layoutPrecision: 0.35, visualNovelty: 0.25, brandTrust: 0.15, typographyMaturity: 0.15, reusePotential: 0.1 },
    examplesGood: ['Apple product pages', 'A24 film sites', 'SSENSE editorial', 'Stripe sessions', 'Linear changelog'],
    examplesBad: ['Generic centered templates', 'Overcrowded landing pages', 'Equal-spacing-everywhere layouts'],
    version: 1,
  },
};

// ── Default fallback for uncovered domains ─────────────────────────────────

function defaultDirective(domain: string): CreativeDirective {
  return {
    id: `dir-${domain}-v1`,
    name: `${domain} Directive`,
    domain,
    directiveText: `Define what premium looks like for ${domain}. Extract heuristics, anti-patterns, and extraction targets.`,
    heuristics: [
      ...UNIVERSAL_RESTRAINT.slice(0, 3),
      'Every element must earn its place on the screen',
      'Use real-looking content, never lorem ipsum or fake data',
    ],
    antiPatterns: [...UNIVERSAL_ANTI_PATTERNS.slice(0, 5)],
    extractionTargets: ['layout systems', 'color usage', 'typography patterns', 'component structure'],
    scoringRubric: { layoutPrecision: 0.3, typographyMaturity: 0.2, visualNovelty: 0.2, brandTrust: 0.15, reusePotential: 0.15 },
    examplesGood: [],
    examplesBad: [],
    version: 1,
  };
}

// ── OPUS AI Generation Prompt ──────────────────────────────────────────────

const OPUS_PROMPT = `You are the fydesign artistic director — "Opus" — the taste layer of a design intelligence OS.

Your job is NOT to generate code. Your job is to define artistic direction, composition critique, spacing philosophy, visual restraint, emotional interface rhythm, and anti-template heuristics.

Domain: {domain}

Define:
1. What makes this domain feel premium vs. template-like
2. Specific heuristics a cheaper execution model (DeepSeek) can follow precisely
3. Anti-patterns to strictly avoid
4. Extraction targets for repo analysis
5. Scoring rubric weights

Make instructions strict enough for an execution model to follow without interpretation.

Return JSON:
{
  "domain": "{domain}",
  "heuristics": ["specific", "actionable", "rules"],
  "antiPatterns": ["avoid", "these", "explicitly"],
  "extractionTargets": ["what", "to", "extract", "from", "repos"],
  "scoringRubric": { "layoutPrecision": 0.3, "typographyMaturity": 0.2, "visualNovelty": 0.2, "brandTrust": 0.15, "reusePotential": 0.15 }
}`;

// ── Public API ─────────────────────────────────────────────────────────────

export async function generateDirectivePack(domain: string): Promise<CreativeDirective | null> {
  if (hasAuth()) {
    try {
      const prompt = OPUS_PROMPT.replace(/\{domain\}/g, domain);
      const result = await callAIJSON<{
        domain: string;
        heuristics: string[];
        antiPatterns: string[];
        extractionTargets: string[];
        scoringRubric: Record<string, number>;
      }>(prompt, { temperature: 0.7, maxTokens: 4000 });

      if (result && result.heuristics) {
        const directive: CreativeDirective = {
          id: `dir-${domain}-${Date.now()}`,
          name: `Opus ${domain} Directive`,
          domain: result.domain || domain,
          directiveText: `Opus-generated directive for ${domain}. Heuristics: ${result.heuristics.slice(0, 3).join('; ')}`,
          heuristics: result.heuristics,
          antiPatterns: result.antiPatterns || [],
          extractionTargets: result.extractionTargets || [],
          scoringRubric: result.scoringRubric || {},
          examplesGood: [],
          examplesBad: [],
          version: 1,
        };

        await saveCreativeDirective({
          id: directive.id,
          name: directive.name,
          domain: directive.domain,
          directiveText: directive.directiveText,
          antiPatterns: directive.antiPatterns,
          scoringRubric: directive.scoringRubric,
          examplesGood: directive.examplesGood,
          examplesBad: directive.examplesBad,
          version: directive.version,
        });

        return directive;
      }
    } catch (error) {
      console.warn(`[OpusDirectives] AI generation failed for ${domain}:`, error instanceof Error ? error.message : error);
    }
  }

  return BUILTIN_DIRECTIVES[domain] || defaultDirective(domain);
}

export async function refreshAllDirectives(): Promise<CreativeDirective[]> {
  const results: CreativeDirective[] = [];
  for (const domain of ALL_DOMAINS) {
    const directive = await generateDirectivePack(domain);
    if (directive) results.push(directive);
  }
  return results;
}

export async function loadDirectivesForDomain(domain: string): Promise<CreativeDirective> {
  try {
    const rows = await loadCreativeDirectivesByDomain(domain);
    if (rows.length > 0) {
      const row = rows[0];
      return {
        id: row.id,
        name: row.name,
        domain: row.domain,
        directiveText: row.directive_text,
        heuristics: [],
        antiPatterns: row.anti_patterns,
        extractionTargets: [],
        scoringRubric: row.scoring_rubric,
        examplesGood: row.examples_good,
        examplesBad: row.examples_bad,
        version: row.version,
      };
    }
  } catch (error) {
    console.warn(`[OpusDirectives] load failed for ${domain}:`, error instanceof Error ? error.message : error);
  }

  return BUILTIN_DIRECTIVES[domain] || defaultDirective(domain);
}

export async function getAllLatestDirectives(): Promise<CreativeDirective[]> {
  try {
    const rows = await loadLatestDirectives();
    return rows.map((row) => ({
      id: row.id,
      name: row.name,
      domain: row.domain,
      directiveText: row.directive_text,
      heuristics: [],
      antiPatterns: row.anti_patterns,
      extractionTargets: [],
      scoringRubric: row.scoring_rubric,
      examplesGood: row.examples_good,
      examplesBad: row.examples_bad,
      version: row.version,
    }));
  } catch {
    return [];
  }
}

export function getAllDirectiveDomains(): string[] {
  return ALL_DOMAINS;
}

export function applyDirectivesToScoring(
  _pattern: { patternType: string; tags: string[]; rules: string[]; avoids?: string[] },
  _directives: CreativeDirective[],
): number {
  let adjustment = 0;
  for (const directive of _directives) {
    const haystack = [..._pattern.tags, _pattern.patternType, ..._pattern.rules].join(' ').toLowerCase();
    for (const anti of directive.antiPatterns) {
      if (haystack.includes(anti.toLowerCase())) {
        adjustment -= 0.15;
      }
    }
    for (const heuristic of directive.heuristics) {
      const keywords = heuristic.split(/\s+/).filter((w) => w.length > 4);
      const matches = keywords.filter((kw) => haystack.includes(kw.toLowerCase())).length;
      if (matches >= 2) adjustment += 0.05;
    }
  }
  return Math.max(-0.3, Math.min(0.3, adjustment));
}

// ── Runtime directive injection for context building ────────────────────────

export function formatDirectivesForPrompt(directives: CreativeDirective[]): { dos: string[]; donts: string[] } {
  const dos = [...new Set(directives.flatMap((d) => d.heuristics))];
  const donts = [...new Set(directives.flatMap((d) => d.antiPatterns))];
  return { dos, donts };
}

export function getUniversalRestraint(): string[] {
  return UNIVERSAL_RESTRAINT;
}

export function getUniversalAntiPatterns(): string[] {
  return UNIVERSAL_ANTI_PATTERNS;
}
