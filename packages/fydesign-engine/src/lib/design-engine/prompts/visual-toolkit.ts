// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Visual Techniques Toolkit — Executable CSS/SVG Snippets for Premium       ║
// ║  Output Quality                                                            ║
// ║                                                                            ║
// ║  Problem: builder prompts say "be bold" but don't show HOW.                ║
// ║  Solution: concrete, copy-paste-ready technique snippets the builder       ║
// ║  model can directly adapt and apply. Not aspirations — code.              ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

/**
 * Returns the visual techniques toolkit for injection into the builder system prompt.
 * Each technique has: name, when to use, and exact CSS/HTML.
 * ~2500-3000 tokens total — cached with the system prompt.
 */
export function getVisualToolkit(): string {
  return `
═══ VISUAL TECHNIQUES TOOLKIT — COPY, ADAPT, APPLY ═══

You MUST use at least 2 techniques from this toolkit per design.
A flat white/black canvas with only text and a card is an INSTANT FAIL.

── 1. BACKGROUND TREATMENTS (never leave a canvas empty) ──

MESH GRADIENT BACKGROUND (light mode):
\`\`\`css
body {
  background:
    radial-gradient(ellipse 80% 60% at 10% 90%, oklch(92% 0.08 250 / 0.5), transparent),
    radial-gradient(ellipse 60% 50% at 85% 20%, oklch(94% 0.06 180 / 0.4), transparent),
    radial-gradient(ellipse 70% 70% at 50% 50%, oklch(96% 0.04 100 / 0.3), transparent),
    var(--bg, #fafafa);
}
\`\`\`

NOISE TEXTURE OVERLAY (adds tactile depth to any background):
\`\`\`css
.texture-overlay::after {
  content: '';
  position: absolute; inset: 0;
  background: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.04'/%3E%3C/svg%3E");
  pointer-events: none;
  mix-blend-mode: overlay;
}
\`\`\`

GEOMETRIC ACCENT GRID (subtle structure):
\`\`\`css
.geo-grid::before {
  content: '';
  position: absolute; inset: 0;
  background-image:
    linear-gradient(var(--accent, #0066FF) 1px, transparent 1px),
    linear-gradient(90deg, var(--accent, #0066FF) 1px, transparent 1px);
  background-size: 80px 80px;
  opacity: 0.04;
  mask-image: radial-gradient(ellipse 60% 50% at 70% 30%, black 20%, transparent 70%);
  pointer-events: none;
}
\`\`\`

── 2. DEPTH & DIMENSION (flat is dead) ──

LAYERED SHADOW SYSTEM (premium elevation):
\`\`\`css
.elevated-card {
  box-shadow:
    0 1px 2px oklch(20% 0 0 / 0.06),
    0 4px 8px oklch(20% 0 0 / 0.04),
    0 12px 24px oklch(20% 0 0 / 0.06),
    0 24px 48px oklch(20% 0 0 / 0.08);
  border: 1px solid oklch(90% 0 0 / 0.5);
}
\`\`\`

FLOATING ELEMENT WITH PERSPECTIVE:
\`\`\`css
.float-card {
  transform: perspective(1200px) rotateY(-4deg) rotateX(2deg);
  box-shadow: 20px 30px 60px oklch(20% 0.02 260 / 0.2);
  transition: transform 0.4s cubic-bezier(0.16, 1, 0.3, 1);
}
\`\`\`

GLASS PANEL (frosted, modern):
\`\`\`css
.glass {
  background: oklch(98% 0 0 / 0.6);
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  border: 1px solid oklch(100% 0 0 / 0.3);
  box-shadow: 0 8px 32px oklch(20% 0 0 / 0.08), inset 0 1px 0 oklch(100% 0 0 / 0.4);
}
\`\`\`

── 3. TYPOGRAPHY MOVES (text IS the design) ──

GRADIENT TEXT FILL (hero headlines):
\`\`\`css
.gradient-text {
  background: linear-gradient(135deg, var(--fg, #0a0a0a) 40%, var(--accent, #0066FF) 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}
\`\`\`

OUTLINED / STROKE TEXT (dramatic compositional anchor):
\`\`\`css
.stroke-text {
  font-size: clamp(80px, 15vw, 200px);
  font-weight: 900;
  color: transparent;
  -webkit-text-stroke: 2px var(--fg, #0a0a0a);
  letter-spacing: -0.04em;
}
\`\`\`

MIXED-WEIGHT TYPOGRAPHY STACK:
\`\`\`css
.kicker { font-size: 11px; font-weight: 700; letter-spacing: 0.15em; text-transform: uppercase; color: var(--accent); }
.display { font-size: clamp(36px, 6vw, 72px); font-weight: 800; letter-spacing: -0.03em; line-height: 1.05; }
.subhead { font-size: clamp(16px, 2vw, 22px); font-weight: 400; color: var(--muted); line-height: 1.5; max-width: 32ch; }
\`\`\`

── 4. COMPOSITIONAL MOVES (break the grid) ──

DIAGONAL DIVIDER (split-screen energy):
\`\`\`css
.diagonal-section {
  clip-path: polygon(0 0, 100% 0, 100% 85%, 0 100%);
  /* or: clip-path: polygon(0 8%, 100% 0, 100% 100%, 0 100%); */
}
\`\`\`

OVERLAPPING PLANES (z-depth composition):
\`\`\`html
<div style="position:relative;">
  <!-- Background plane -->
  <div style="position:absolute;top:10%;right:0;width:55%;height:80%;background:var(--surface);border-radius:24px;"></div>
  <!-- Foreground content (overlaps) -->
  <div style="position:relative;z-index:2;padding:60px;">
    <!-- Text + phone mockup here — overlapping the background plane -->
  </div>
</div>
\`\`\`

ASYMMETRIC EDITORIAL GRID:
\`\`\`css
.editorial-grid {
  display: grid;
  grid-template-columns: 1fr 1.618fr; /* golden ratio */
  gap: 0;
  height: 100%;
}
/* Left: text-heavy with generous padding. Right: visual-heavy, edge-to-edge. */
\`\`\`

── 5. DECORATIVE SVG (purposeful accents, not decoration) ──

ACCENT CIRCLE / RING (compositional anchor):
\`\`\`html
<svg style="position:absolute;top:-10%;right:-5%;width:40%;opacity:0.06;" viewBox="0 0 200 200">
  <circle cx="100" cy="100" r="90" fill="none" stroke="var(--accent, #0066FF)" stroke-width="0.5"/>
  <circle cx="100" cy="100" r="60" fill="none" stroke="var(--accent, #0066FF)" stroke-width="0.3"/>
</svg>
\`\`\`

HORIZONTAL RULE ACCENT (editorial precision):
\`\`\`html
<div style="width:60px;height:3px;background:var(--accent);border-radius:2px;margin-bottom:16px;"></div>
\`\`\`

── 6. COLOR TECHNIQUES ──

LUMINOUS ACCENT GLOW (behind CTAs or key elements):
\`\`\`css
.glow-accent {
  box-shadow: 0 0 0 1px var(--accent), 0 4px 16px oklch(60% 0.15 260 / 0.3), 0 8px 40px oklch(60% 0.15 260 / 0.15);
}
\`\`\`

TINTED SURFACE WITH COLOR-MIX:
\`\`\`css
.tinted-surface {
  background: color-mix(in oklch, var(--accent) 6%, var(--surface, white));
  border: 1px solid color-mix(in oklch, var(--accent) 12%, var(--border));
}
\`\`\`

── 7. STAT / NUMBER DISPLAY (premium, not just big text) ──

\`\`\`html
<div style="position:relative;display:inline-block;">
  <span style="font-size:clamp(64px,10vw,140px);font-weight:900;letter-spacing:-0.04em;
    background:linear-gradient(180deg, var(--fg) 50%, oklch(60% 0 0 / 0.3) 100%);
    -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;
    line-height:0.85;">720</span>
  <span style="display:block;font-size:12px;font-weight:600;letter-spacing:0.1em;text-transform:uppercase;
    color:var(--accent);margin-top:8px;">CREDIT SCORE</span>
</div>
\`\`\`

── 8. PILL / TAG ROW (alternative to boring cards) ──

\`\`\`html
<div style="display:flex;gap:8px;flex-wrap:wrap;">
  <span style="padding:6px 14px;border-radius:999px;font-size:13px;font-weight:500;
    background:color-mix(in oklch, var(--accent) 10%, white);
    color:var(--accent);border:1px solid color-mix(in oklch, var(--accent) 20%, transparent);">
    Equifax ✓
  </span>
  <!-- repeat for other pills -->
</div>
\`\`\`

═══ END VISUAL TOOLKIT ═══

REMEMBER: Pick at least 2 techniques above and ADAPT them for this specific design.
The toolkit is your starting point — combine, modify, and make it yours.
A design with zero toolkit techniques applied is a FAIL.
`;
}
