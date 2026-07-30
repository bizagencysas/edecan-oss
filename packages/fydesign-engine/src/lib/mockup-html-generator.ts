// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  fydesign — Mockup HTML Page Generator                                     ║
// ║  Produces a complete standalone HTML page                                  ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import type { GeneratedCopy, Language } from './types';
import type { RecreatedScreen } from './screen-recreator';

export interface MockupPageConfig {
  appName: string;
  logoDataUrl: string | null;
  screens: RecreatedScreen[];
  copies: GeneratedCopy[];
  languages: Language[];
  dimensions: { w: number; h: number };
  primaryColor: string;
  accentColors: string[];
}

const LANG_LABELS: Record<string, string> = { en: 'English', es: 'Español', pt: 'Português', fr: 'Français' };

export function generateMockupHTML(config: MockupPageConfig): string {
  const { appName, logoDataUrl, screens, copies, languages, dimensions, primaryColor, accentColors } = config;
  const totalCount = screens.length * languages.length;
  const langList = languages.map(l => LANG_LABELS[l] || l).join(' + ');

  // Build mockup cards
  const cards = buildMockupCards(config);

  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>${appName} · App Store Mockups</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg: #0D0D0F;
  --surface: #18181B;
  --surface-hover: #1F1F23;
  --border: #27272A;
  --text: #FAFAFA;
  --text-secondary: #A1A1AA;
  --text-muted: #71717A;
  --primary: ${primaryColor};
  --accent: ${accentColors[0] || '#6366F1'};
  --radius: 16px;
}

html, body {
  height: 100%;
  font-family: 'Inter', -apple-system, system-ui, sans-serif;
  background: var(--bg);
  color: var(--text);
  -webkit-font-smoothing: antialiased;
}

/* ── Header ── */
.page-header {
  position: sticky;
  top: 0;
  z-index: 50;
  background: rgba(13,13,15,0.85);
  backdrop-filter: blur(20px);
  border-bottom: 1px solid var(--border);
  padding: 16px 32px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.header-logo {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  object-fit: contain;
}

.header-logo-placeholder {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: var(--primary);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  font-weight: 800;
  color: white;
}

.header-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--text);
}

.header-meta {
  font-size: 13px;
  color: var(--text-muted);
  margin-left: 12px;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

/* ── Language Filter Tabs ── */
.lang-tabs {
  display: flex;
  gap: 0;
  background: var(--surface);
  border-radius: 10px;
  padding: 3px;
  border: 1px solid var(--border);
}

.lang-tab {
  padding: 6px 16px;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-muted);
  background: none;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.15s;
  font-family: inherit;
}
.lang-tab:hover { color: var(--text); }
.lang-tab.active { background: var(--primary); color: white; font-weight: 600; }

/* ── Export Button ── */
.export-btn {
  padding: 8px 20px;
  background: #DC2626;
  color: white;
  border: none;
  border-radius: 10px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  font-family: inherit;
  display: flex;
  align-items: center;
  gap: 6px;
  transition: background 0.15s;
}
.export-btn:hover { background: #B91C1C; }

/* ── Section Header ── */
.section-header {
  padding: 24px 32px 16px;
  display: flex;
  align-items: center;
  gap: 12px;
}

.lang-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 4px 10px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
}
.lang-badge.en { background: #16A34A; color: white; }
.lang-badge.es { background: #DC2626; color: white; }
.lang-badge.pt { background: #2563EB; color: white; }
.lang-badge.fr { background: #7C3AED; color: white; }

.section-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

/* ── Mockup Grid ── */
.mockup-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 24px;
  padding: 0 32px 48px;
}

/* ── Mockup Card ── */
.mockup-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
  transition: transform 0.2s, box-shadow 0.2s;
  cursor: pointer;
  position: relative;
}
.mockup-card:hover {
  transform: translateY(-4px);
  box-shadow: 0 12px 40px rgba(0,0,0,0.4);
}

.mockup-card-badge {
  position: absolute;
  top: 12px;
  left: 12px;
  z-index: 10;
  padding: 4px 10px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.3px;
}
.mockup-card-badge.en { background: #16A34A; color: white; }
.mockup-card-badge.es { background: #DC2626; color: white; }
.mockup-card-badge.pt { background: #2563EB; color: white; }
.mockup-card-badge.fr { background: #7C3AED; color: white; }

/* ── Marketing Section (top of card) ── */
.card-marketing {
  padding: 48px 28px 20px;
  min-height: 160px;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
}

.card-brand {
  font-size: 14px;
  font-weight: 700;
  color: var(--text-secondary);
  margin-bottom: 4px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.card-brand img {
  width: 18px;
  height: 18px;
  border-radius: 4px;
}

.card-category {
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 1px;
  color: var(--text-muted);
  margin-bottom: 12px;
}

.card-headline {
  font-size: 26px;
  font-weight: 800;
  line-height: 1.15;
  color: var(--text);
  margin-bottom: 8px;
  letter-spacing: -0.5px;
}

.card-subtitle {
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.5;
}

/* ── Device Frame ── */
.device-container {
  display: flex;
  justify-content: center;
  padding: 0 24px 24px;
}

.device-frame {
  width: 220px;
  background: #1A1A1E;
  border-radius: 28px;
  padding: 8px;
  box-shadow: 0 8px 32px rgba(0,0,0,0.4), inset 0 0 0 1px rgba(255,255,255,0.06);
  position: relative;
}

.device-notch {
  position: absolute;
  top: 12px;
  left: 50%;
  transform: translateX(-50%);
  width: 80px;
  height: 22px;
  background: #000;
  border-radius: 12px;
  z-index: 5;
}

.device-screen {
  border-radius: 20px;
  overflow: hidden;
  background: #000;
  width: 204px;
  height: 441px; /* 204 * (844/390) */
  position: relative;
}

.device-screen-inner {
  width: 390px;
  height: 844px;
  transform: scale(0.5231); /* 204/390 */
  transform-origin: top left;
  pointer-events: none;
}

.device-home {
  width: 100px;
  height: 4px;
  background: rgba(255,255,255,0.15);
  border-radius: 2px;
  margin: 6px auto 0;
}

/* ── Hidden for filter ── */
.mockup-card[data-hidden="true"] { display: none; }

/* ── Footer ── */
.page-footer {
  padding: 32px;
  text-align: center;
  color: var(--text-muted);
  font-size: 12px;
  border-top: 1px solid var(--border);
}
</style>
</head>
<body>

<header class="page-header">
  <div class="header-left">
    ${logoDataUrl ? `<img src="${logoDataUrl}" class="header-logo" alt="${appName}">` : `<div class="header-logo-placeholder">${appName.charAt(0)}</div>`}
    <span class="header-title">${appName} · App Store mockups</span>
    <span class="header-meta">${totalCount} screenshots · ${dimensions.w} × ${dimensions.h} px · ${langList}</span>
  </div>
  <div class="header-right">
    <div class="lang-tabs">
      <button class="lang-tab active" onclick="filterLang('all')">All</button>
      ${languages.map(l => `<button class="lang-tab" onclick="filterLang('${l}')">${LANG_LABELS[l] || l}</button>`).join('')}
    </div>
    <button class="export-btn" onclick="alert('Export ZIP coming soon — use browser screenshot for now')">Export ZIP (${totalCount} PNG)</button>
  </div>
</header>

${cards}

<footer class="page-footer">
  Generated by fydesign · ${appName} · ${new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })}
</footer>

<script>
function filterLang(lang) {
  document.querySelectorAll('.lang-tab').forEach(t => t.classList.remove('active'));
  event.target.classList.add('active');
  document.querySelectorAll('.mockup-card').forEach(card => {
    if (lang === 'all') { card.dataset.hidden = 'false'; }
    else { card.dataset.hidden = card.dataset.lang !== lang ? 'true' : 'false'; }
  });
  document.querySelectorAll('.lang-section').forEach(sec => {
    if (lang === 'all') { sec.style.display = ''; }
    else { sec.style.display = sec.dataset.lang !== lang ? 'none' : ''; }
  });
}
</script>
</body>
</html>`;
}

function buildMockupCards(config: MockupPageConfig): string {
  const { appName, logoDataUrl, screens, copies, languages } = config;
  let html = '';

  for (const lang of languages) {
    const langScreens = screens.map((screen, idx) => {
      const copy = copies.find(c => c.screenId === screen.screenId && c.language === lang);
      const num = idx + 1;
      const total = screens.length;
      const headline = copy?.headline || screen.screenName;
      const subtitle = copy?.subtitle || '';

      // Embed screen HTML directly (no iframe needed)
      const screenHtml = screen.html || '<div style="width:390px;height:844px;background:#111;display:flex;align-items:center;justify-content:center;color:#555;font-family:system-ui;">No preview</div>';

      return `<div class="mockup-card" data-lang="${lang}" data-hidden="false">
  <div class="mockup-card-badge ${lang}">${lang.toUpperCase()} · ${String(num).padStart(2, '0')}/${total}</div>
  <div class="card-marketing">
    <div class="card-brand">${logoDataUrl ? `<img src="${logoDataUrl}" alt="">` : ''} ${appName}</div>
    <div class="card-category">${screen.layout.toUpperCase()}</div>
    <div class="card-headline">${headline}</div>
    ${subtitle ? `<div class="card-subtitle">${subtitle}</div>` : ''}
  </div>
  <div class="device-container">
    <div class="device-frame">
      <div class="device-notch"></div>
      <div class="device-screen">
        <div class="device-screen-inner">
          ${screenHtml}
        </div>
      </div>
      <div class="device-home"></div>
    </div>
  </div>
</div>`;
    });

    html += `
<div class="lang-section" data-lang="${lang}">
  <div class="section-header">
    <span class="lang-badge ${lang}">${lang.toUpperCase()}</span>
    <span class="section-title">${LANG_LABELS[lang] || lang} (${languages.indexOf(lang) === 0 ? 'US' : lang.toUpperCase()}) · ${screens.length} SCREENSHOTS · ${config.dimensions.w} × ${config.dimensions.h}</span>
  </div>
  <div class="mockup-grid">
    ${langScreens.join('\n')}
  </div>
</div>`;
  }

  return html;
}
