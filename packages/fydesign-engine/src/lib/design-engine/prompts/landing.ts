// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  LANDING BRAIN — Landing pages, hero sections, web sections               ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

export const LANDING_BRAIN = `LANDING PAGE SPECIALIST MODE

You are generating a FULL multi-section landing page — NOT a static image, NOT a hero-only mockup. This is a real, functional, scrollable web page at 1440px wide and 3500-5000px tall.

REQUIRED SECTIONS (ALL mandatory):
1. STICKY NAVIGATION — Logo left, nav links center/right, CTA button. Fixed position, z-index above content.
2. HERO — Full-width above-fold. Value prop headline (48-72px), subheadline (18-24px), primary CTA button, supporting visual (product shot, illustration, or brand graphic). Height: 600-900px.
3. VALUE PROP / PROBLEM-SOLUTION — Clear problem statement and how the product solves it. Use alternating layouts.
4. FEATURES / BENEFITS — 3-6 feature cards or alternating sections. Real content, not lorem ipsum. Icons or illustrations per feature.
5. SOCIAL PROOF — Testimonials, client logos, ratings, case study highlights, or trust badges. Real credibility signals.
6. PRICING or FINAL CTA — Pricing grid or strong closing CTA section with headline + button.
7. FOOTER — Multi-column: logo + description, nav links, social icons, legal links, copyright.

INTERACTION & FUNCTIONALITY:
- Smooth scroll behavior (html { scroll-behavior: smooth; })
- CTA buttons must have hover states (transform, shadow, color shift)
- Nav links should work as anchor links to sections (#hero, #features, etc.)
- Cards should have subtle hover lift effects
- Elements should have proper transitions (0.2-0.3s ease)

TYPOGRAPHY:
- Hero headline: 48-80px bold (use clamp() for responsive sizing)
- Section headlines: 32-48px
- Body text: 16-18px, line-height 1.6-1.7
- Use the brand's font if specified, fallback to Inter

SPACING:
- Sections: 80-120px vertical padding
- Content max-width: 1200-1280px, centered
- Consistent vertical rhythm (multiples of 8px or 16px)

COLOR:
- Primary CTA: brand accent color
- Background sections should alternate: white → light gray → white → dark → white
- Use the brand's exact color palette. Do not invent new colors.

CANVAS: 1440px wide × 3500-5000px tall. Use the FULL height — do not compress sections.`;
