// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Component Library — Premium Pre-built HTML/SVG Templates                  ║
// ║  Phone frames, badges, decorative elements injected into system prompt.    ║
// ║  The AI uses these EXACT templates instead of inventing bad ones.          ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

/**
 * Returns relevant component templates for the system prompt based on design dimensions.
 */
export function getComponentLibrary(_width: number, _height: number): string {
  return `
═══ COMPONENT LIBRARY — USE THESE EXACT TEMPLATES ═══

When you need a phone mockup, App Store badge, or decorative element,
use these EXACT HTML/CSS snippets. Do NOT invent your own — these are pixel-perfect.

── IPHONE 16 PRO FRAME (Premium) ──
When showing a phone/app mockup, use this EXACT structure (adjust width/height proportionally):

<div class="phone-frame" style="
  width: 280px;
  height: 572px;
  border-radius: 48px;
  background: #1a1a1e;
  box-shadow:
    0 30px 80px rgba(0,0,0,0.45),
    0 0 0 2px rgba(255,255,255,0.08),
    inset 0 0 0 2px rgba(255,255,255,0.05),
    0 0 0 8px #0d0d0f;
  position: relative;
  overflow: hidden;
">
  <!-- Dynamic Island -->
  <div style="position:absolute;top:12px;left:50%;transform:translateX(-50%);width:120px;height:34px;background:#000;border-radius:20px;z-index:20;"></div>

  <!-- Status Bar -->
  <div style="position:absolute;top:16px;left:28px;font:600 15px/1 -apple-system,BlinkMacSystemFont,sans-serif;color:white;z-index:21;">9:41</div>
  <div style="position:absolute;top:16px;right:28px;display:flex;gap:5px;align-items:center;z-index:21;">
    <!-- Signal -->
    <svg width="17" height="12" viewBox="0 0 17 12" fill="white"><rect x="0" y="8" width="3" height="4" rx="0.5"/><rect x="4.5" y="5" width="3" height="7" rx="0.5"/><rect x="9" y="2" width="3" height="10" rx="0.5"/><rect x="13.5" y="0" width="3" height="12" rx="0.5" opacity="0.3"/></svg>
    <!-- WiFi -->
    <svg width="16" height="12" viewBox="0 0 16 12" fill="white"><path d="M8 10.5a1.5 1.5 0 110 3 1.5 1.5 0 010-3z"/><path d="M4.94 8.06a4.5 4.5 0 016.12 0" stroke="white" stroke-width="1.5" fill="none" stroke-linecap="round"/><path d="M2.1 5.22a8 8 0 0111.8 0" stroke="white" stroke-width="1.5" fill="none" stroke-linecap="round"/></svg>
    <!-- Battery -->
    <svg width="27" height="13" viewBox="0 0 27 13" fill="white"><rect x="0" y="0.5" width="23" height="12" rx="3.5" stroke="white" stroke-width="1" fill="none"/><rect x="2" y="2.5" width="17" height="8" rx="1.5" fill="white"/><path d="M25 4.5v4a2 2 0 000-4z"/></svg>
  </div>

  <!-- Screen Content Area -->
  <div class="phone-screen" style="
    position: absolute;
    top: 0; left: 0; right: 0; bottom: 0;
    border-radius: 48px;
    overflow: hidden;
    background: white;
  ">
    <!-- YOUR APP UI GOES HERE -->
    <!-- Start content at padding-top: 54px to clear the status bar -->
  </div>
</div>

── APP STORE BADGE (Download on the App Store) ──
<div style="display:inline-flex;align-items:center;gap:6px;background:#000;color:white;padding:8px 16px;border-radius:10px;font-family:-apple-system,BlinkMacSystemFont,sans-serif;cursor:pointer;">
  <svg width="20" height="24" viewBox="0 0 20 24" fill="white">
    <path d="M17.05 12.54c-.03-3.07 2.51-4.54 2.62-4.62-1.43-2.09-3.65-2.38-4.44-2.41-1.89-.19-3.69 1.11-4.65 1.11-.96 0-2.44-1.08-4.01-1.05-2.06.03-3.97 1.2-5.03 3.04-2.14 3.72-.55 9.23 1.54 12.24 1.02 1.47 2.24 3.13 3.84 3.07 1.54-.06 2.12-1 3.98-1 1.86 0 2.38 1 4 .97 1.66-.03 2.7-1.5 3.72-2.98 1.17-1.71 1.65-3.37 1.68-3.45-.04-.02-3.23-1.24-3.25-4.92z"/>
    <path d="M14.05 3.54c.85-1.03 1.42-2.46 1.27-3.88-1.23.05-2.72.82-3.6 1.85-.79.92-1.48 2.38-1.3 3.78 1.38.11 2.78-.7 3.63-1.75z"/>
  </svg>
  <div>
    <div style="font-size:8px;font-weight:400;letter-spacing:0.02em;">Download on the</div>
    <div style="font-size:16px;font-weight:600;letter-spacing:-0.01em;margin-top:-1px;">App Store</div>
  </div>
</div>

── GOOGLE PLAY BADGE (Get it on Google Play) ──
<div style="display:inline-flex;align-items:center;gap:6px;background:#000;color:white;padding:8px 16px;border-radius:10px;font-family:-apple-system,BlinkMacSystemFont,sans-serif;cursor:pointer;">
  <svg width="22" height="24" viewBox="0 0 24 24" fill="none">
    <path d="M3.61 1.81L13.44 12 3.61 22.19c-.38-.36-.61-.87-.61-1.44V3.25c0-.57.23-1.08.61-1.44z" fill="#4285F4"/>
    <path d="M17.44 8.56L13.44 12l4 4 4.48-2.52c.82-.46.82-1.5 0-1.96L17.44 8.56z" fill="#FBBC04"/>
    <path d="M3.61 1.81L13.44 12l4-3.44L7.2.36C6.4-.06 5.55-.06 4.76.36L3.61 1.81z" fill="#34A853"/>
    <path d="M13.44 12l-9.83 10.19 1.15 1.45c.79.42 1.64.42 2.44 0l10.24-5.08L13.44 12z" fill="#EA4335"/>
  </svg>
  <div>
    <div style="font-size:8px;font-weight:400;letter-spacing:0.02em;">GET IT ON</div>
    <div style="font-size:16px;font-weight:600;letter-spacing:-0.01em;margin-top:-1px;">Google Play</div>
  </div>
</div>

── 5-STAR RATING ──
<div style="display:flex;gap:2px;">
  <svg width="20" height="20" viewBox="0 0 24 24" fill="#FFC107"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87L18.18 21 12 17.27 5.82 21 7 14.14l-5-4.87 6.91-1.01L12 2z"/></svg>
  <svg width="20" height="20" viewBox="0 0 24 24" fill="#FFC107"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87L18.18 21 12 17.27 5.82 21 7 14.14l-5-4.87 6.91-1.01L12 2z"/></svg>
  <svg width="20" height="20" viewBox="0 0 24 24" fill="#FFC107"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87L18.18 21 12 17.27 5.82 21 7 14.14l-5-4.87 6.91-1.01L12 2z"/></svg>
  <svg width="20" height="20" viewBox="0 0 24 24" fill="#FFC107"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87L18.18 21 12 17.27 5.82 21 7 14.14l-5-4.87 6.91-1.01L12 2z"/></svg>
  <svg width="20" height="20" viewBox="0 0 24 24" fill="#FFC107"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87L18.18 21 12 17.27 5.82 21 7 14.14l-5-4.87 6.91-1.01L12 2z"/></svg>
</div>

── GLASSMORPHISM CARD ──
<div style="
  background: rgba(255,255,255,0.08);
  backdrop-filter: blur(24px) saturate(180%);
  -webkit-backdrop-filter: blur(24px) saturate(180%);
  border: 1px solid rgba(255,255,255,0.12);
  border-radius: 20px;
  padding: 24px;
  box-shadow: 0 8px 32px rgba(0,0,0,0.12);
">
  <!-- Card content here -->
</div>

── DECORATIVE BACKGROUND ELEMENTS ──
When you need to fill empty space, use these AT LOW OPACITY behind content.
IMPORTANT: never leave a canvas as flat white/black — add at least ONE background treatment.

<!-- Mesh gradient (premium, multi-stop, light mode) -->
<div style="position:absolute;inset:0;pointer-events:none;background:
  radial-gradient(ellipse 80% 60% at 10% 90%, oklch(92% 0.08 250 / 0.5), transparent),
  radial-gradient(ellipse 60% 50% at 85% 20%, oklch(94% 0.06 180 / 0.4), transparent),
  radial-gradient(ellipse 70% 70% at 50% 50%, oklch(96% 0.04 100 / 0.3), transparent);"></div>

<!-- Mesh gradient (premium, dark mode) -->
<div style="position:absolute;inset:0;pointer-events:none;background:
  radial-gradient(ellipse 70% 60% at 20% 80%, oklch(25% 0.12 260 / 0.6), transparent),
  radial-gradient(ellipse 50% 50% at 80% 20%, oklch(20% 0.08 200 / 0.4), transparent);"></div>

<!-- Noise texture overlay (adds tactile depth to any background) -->
<div style="position:absolute;inset:0;pointer-events:none;mix-blend-mode:overlay;
  background:url(&quot;data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.04'/%3E%3C/svg%3E&quot;);"></div>

<!-- Geometric accent grid (faded, masked to one region) -->
<div style="position:absolute;inset:0;pointer-events:none;
  background-image:linear-gradient(var(--accent,#0066FF) 1px,transparent 1px),linear-gradient(90deg,var(--accent,#0066FF) 1px,transparent 1px);
  background-size:80px 80px;opacity:0.035;
  mask-image:radial-gradient(ellipse 60% 50% at 70% 30%,black 20%,transparent 70%);"></div>

<!-- Accent circle rings (compositional anchor behind content) -->
<svg style="position:absolute;top:-10%;right:-5%;width:40%;opacity:0.06;pointer-events:none;" viewBox="0 0 200 200">
  <circle cx="100" cy="100" r="90" fill="none" stroke="var(--accent,#0066FF)" stroke-width="0.5"/>
  <circle cx="100" cy="100" r="60" fill="none" stroke="var(--accent,#0066FF)" stroke-width="0.3"/>
</svg>

── EDITORIAL STAT BLOCK (for hero numbers / metrics) ──
Use this when you want a BIG number as a compositional anchor — not just raw oversized text.

<div style="position:relative;display:inline-block;">
  <span style="font-size:clamp(64px,12vw,160px);font-weight:900;letter-spacing:-0.04em;
    background:linear-gradient(180deg,var(--fg,#0a0a0a) 50%,oklch(60% 0 0 / 0.25) 100%);
    -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;
    line-height:0.85;">720</span>
  <div style="display:flex;align-items:center;gap:8px;margin-top:8px;">
    <div style="width:40px;height:3px;background:var(--accent,#0066FF);border-radius:2px;"></div>
    <span style="font-size:11px;font-weight:700;letter-spacing:0.15em;text-transform:uppercase;
      color:var(--accent,#0066FF);">CREDIT SCORE</span>
  </div>
</div>

── FEATURE PILL ROW (alternative to cards) ──
<div style="display:flex;gap:8px;flex-wrap:wrap;">
  <span style="padding:6px 14px;border-radius:999px;font-size:13px;font-weight:500;
    background:color-mix(in oklch,var(--accent,#0066FF) 10%,white);
    color:var(--accent,#0066FF);border:1px solid color-mix(in oklch,var(--accent,#0066FF) 20%,transparent);">
    Equifax ✓
  </span>
  <span style="padding:6px 14px;border-radius:999px;font-size:13px;font-weight:500;
    background:color-mix(in oklch,var(--accent,#0066FF) 10%,white);
    color:var(--accent,#0066FF);border:1px solid color-mix(in oklch,var(--accent,#0066FF) 20%,transparent);">
    TransUnion ✓
  </span>
</div>

── CSS MICRO-ANIMATIONS ──
Include these keyframes in your <style> block and apply them:
@keyframes float { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-10px); } }
@keyframes revealUp { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
@keyframes shimmer { from { background-position: -200% 0; } to { background-position: 200% 0; } }
.animate-float { animation: float 4s ease-in-out infinite; }
.animate-reveal { animation: revealUp 0.8s cubic-bezier(0.16, 1, 0.3, 1) forwards; }

── MACBOOK FRAME (Premium) ──
When showing a web mockup, use this EXACT structure:
<div style="width:100%;max-width:800px;margin:0 auto;filter:drop-shadow(0 30px 60px rgba(0,0,0,0.3));">
  <div style="background:#2d2d2d;border-radius:16px 16px 0 0;padding:12px;display:flex;align-items:center;gap:8px;border:1px solid #444;border-bottom:none;">
    <div style="width:12px;height:12px;border-radius:50%;background:#ff5f56;"></div>
    <div style="width:12px;height:12px;border-radius:50%;background:#ffbd2e;"></div>
    <div style="width:12px;height:12px;border-radius:50%;background:#27c93f;"></div>
    <div style="flex:1;background:#1a1a1a;border-radius:6px;height:24px;margin:0 16px;"></div>
  </div>
  <div style="background:#fff;aspect-ratio:16/10;position:relative;overflow:hidden;border-left:1px solid #444;border-right:1px solid #444;">
    <!-- YOUR WEB UI GOES HERE -->
  </div>
  <div style="background:#999;height:16px;border-radius:0 0 16px 16px;box-shadow:inset 0 4px 10px rgba(0,0,0,0.2);">
    <div style="width:120px;height:8px;background:#777;margin:0 auto;border-radius:0 0 8px 8px;"></div>
  </div>
</div>

── BENTO GRID LAYOUT ──
When you need an asymmetric grid, use this structure:
<div style="display:grid;grid-template-columns:repeat(3,1fr);grid-template-rows:repeat(2,minmax(250px,auto));gap:24px;">
  <!-- Large hero card -->
  <div style="grid-column:span 2;grid-row:span 2;background:var(--surface);border-radius:24px;padding:32px;display:flex;flex-direction:column;justify-content:flex-end;border:1px solid var(--border);"></div>
  <!-- Top right card -->
  <div style="background:var(--surface);border-radius:24px;padding:24px;border:1px solid var(--border);"></div>
  <!-- Bottom right card -->
  <div style="background:var(--surface);border-radius:24px;padding:24px;border:1px solid var(--border);"></div>
</div>

── NEO-BRUTALISM CARD ──
<div style="
  background: var(--surface, #fff);
  border: 3px solid #000;
  border-radius: 0;
  padding: 24px;
  box-shadow: 8px 8px 0 #000;
  transition: transform 0.1s, box-shadow 0.1s;
">
  <!-- Content here -->
</div>

── CSS MICRO-ANIMATIONS ──
Include these keyframes in your <style> block and apply them:
@keyframes float { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-10px); } }
@keyframes revealUp { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
.animate-float { animation: float 4s ease-in-out infinite; }
.animate-reveal { animation: revealUp 0.8s cubic-bezier(0.16, 1, 0.3, 1) forwards; }

`;
}
