// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  fydesign — Mockup HTML Template Engine                                    ║
// ║  Generates full HTML for each mockup template at export resolution         ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { MockupData, DEVICE_SPECS, TemplateType } from './types';
import { buildScreenSimulation } from './screen-simulator';

/**
 * Generate complete standalone HTML for a mockup (used by Playwright renderer)
 */
export function generateMockupHTML(mockup: MockupData): string {
  const spec = DEVICE_SPECS[mockup.device];
  const W = spec.exportWidth;
  const H = spec.exportHeight;

  // Device dimensions relative to export canvas
  const scale = mockup.template === 'full-bleed' ? 0.92 : 0.62;
  const deviceW = Math.round(W * scale);
  const deviceH = Math.round(deviceW * (spec.screenHeight / spec.screenWidth));
  const bezel = Math.round(spec.bezelRadius * (deviceW / spec.screenWidth));
  const screenInset = Math.round(deviceW * 0.028);
  const screenW = deviceW - screenInset * 2;
  const screenH = deviceH - screenInset * 2;

  const screenHTML = buildScreenSimulation(mockup);
  const bg = buildBackground(mockup);
  const deviceFrame = buildDeviceFrame(mockup, deviceW, deviceH, bezel, screenInset, screenW, screenH, screenHTML);

  return buildLayout(mockup, W, H, bg, deviceFrame, deviceW, deviceH);
}

function buildBackground(m: MockupData): string {
  const [c1, c2] = m.gradientColors;
  switch (m.template) {
    case 'dark-luxury':
      return `background: radial-gradient(ellipse at 30% 20%, ${c1}40 0%, #0a0a0a 70%);`;
    case 'glass-premium':
      return `background: linear-gradient(135deg, ${c1}20, ${c2}20); backdrop-filter: blur(60px);`;
    case 'minimal-clean':
      return `background: ${m.darkMode ? '#111' : '#fafafa'};`;
    case 'bold-statement':
      return `background: linear-gradient(160deg, ${c1}, ${c2});`;
    default:
      return `background: linear-gradient(160deg, ${c1}, ${c2});`;
  }
}

function buildDeviceFrame(
  m: MockupData, dW: number, dH: number, bezel: number,
  inset: number, sW: number, sH: number, screenHTML: string,
): string {
  const isIOS = m.device.startsWith('iphone');
  const frameColor = m.darkMode ? '#1a1a1a' : '#2a2a2e';
  const notch = isIOS
    ? `<div style="position:absolute;top:${Math.round(inset*0.8)}px;left:50%;transform:translateX(-50%);width:${Math.round(sW*0.35)}px;height:${Math.round(sH*0.04)}px;background:#000;border-radius:${Math.round(sH*0.02)}px;z-index:10;"></div>`
    : `<div style="position:absolute;top:${Math.round(inset*1.2)}px;left:50%;transform:translateX(-50%);width:${Math.round(sW*0.03)}px;height:${Math.round(sW*0.03)}px;background:#111;border-radius:50%;z-index:10;"></div>`;

  return `<div style="position:relative;width:${dW}px;height:${dH}px;border-radius:${bezel}px;background:${frameColor};box-shadow:0 ${Math.round(dW*0.05)}px ${Math.round(dW*0.15)}px rgba(0,0,0,0.45),inset 0 1px 0 rgba(255,255,255,0.1);overflow:hidden;">
    ${notch}
    <div style="position:absolute;top:${inset}px;left:${inset}px;width:${sW}px;height:${sH}px;border-radius:${Math.round(bezel*0.85)}px;overflow:hidden;background:#000;">
      ${screenHTML}
    </div>
  </div>`;
}

function buildLayout(
  m: MockupData, W: number, H: number, bgCSS: string,
  deviceFrame: string, dW: number, dH: number,
): string {
  const textColor = m.textColor;
  const headlineSize = Math.round(W * 0.056);
  const subtitleSize = Math.round(W * 0.03);
  const pad = Math.round(W * 0.08);

  const headlineHTML = `<div style="font-size:${headlineSize}px;font-weight:800;color:${textColor};letter-spacing:-${Math.round(headlineSize*0.02)}px;line-height:1.1;text-align:center;max-width:${Math.round(W*0.85)}px;">${m.headline}</div>`;
  const subtitleHTML = `<div style="font-size:${subtitleSize}px;font-weight:400;color:${textColor};opacity:0.7;margin-top:${Math.round(subtitleSize*0.6)}px;text-align:center;max-width:${Math.round(W*0.75)}px;">${m.subtitle}</div>`;

  let bodyContent = '';

  switch (m.template) {
    case 'gradient-hero':
    case 'dark-luxury':
    case 'glass-premium':
      bodyContent = `
        <div style="display:flex;flex-direction:column;align-items:center;justify-content:flex-start;height:100%;padding-top:${pad*1.5}px;">
          ${headlineHTML}${subtitleHTML}
          <div style="margin-top:${pad}px;flex:1;display:flex;align-items:flex-end;justify-content:center;padding-bottom:0;">
            ${deviceFrame}
          </div>
        </div>`;
      break;

    case 'bold-statement':
      bodyContent = `
        <div style="display:flex;flex-direction:column;align-items:center;justify-content:flex-start;height:100%;padding-top:${pad*2}px;">
          <div style="font-size:${Math.round(headlineSize*1.4)}px;font-weight:900;color:${textColor};letter-spacing:-${Math.round(headlineSize*0.04)}px;line-height:1.05;text-align:center;max-width:${Math.round(W*0.9)}px;">${m.headline}</div>
          <div style="font-size:${Math.round(subtitleSize*1.1)}px;font-weight:400;color:${textColor};opacity:0.6;margin-top:${Math.round(subtitleSize)}px;text-align:center;">${m.subtitle}</div>
          <div style="margin-top:${pad}px;flex:1;display:flex;align-items:flex-end;justify-content:center;">
            ${deviceFrame}
          </div>
        </div>`;
      break;

    case 'minimal-clean':
      bodyContent = `
        <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;gap:${pad}px;">
          ${headlineHTML}${subtitleHTML}
          <div style="margin-top:${Math.round(pad*0.5)}px;">${deviceFrame}</div>
        </div>`;
      break;

    case 'split-screen':
    case 'feature-highlight':
      bodyContent = `
        <div style="display:flex;flex-direction:row;align-items:center;justify-content:center;height:100%;padding:${pad}px;gap:${pad}px;">
          <div style="flex:1;display:flex;flex-direction:column;align-items:flex-start;justify-content:center;padding-left:${Math.round(pad*0.5)}px;">
            <div style="font-size:${headlineSize}px;font-weight:800;color:${textColor};letter-spacing:-${Math.round(headlineSize*0.02)}px;line-height:1.1;text-align:left;">${m.headline}</div>
            <div style="font-size:${subtitleSize}px;font-weight:400;color:${textColor};opacity:0.7;margin-top:${Math.round(subtitleSize*0.6)}px;text-align:left;">${m.subtitle}</div>
          </div>
          <div style="flex:1;display:flex;align-items:center;justify-content:center;">
            ${deviceFrame}
          </div>
        </div>`;
      break;

    case 'full-bleed':
    default:
      bodyContent = `
        <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;position:relative;">
          <div style="position:absolute;top:${pad}px;left:0;right:0;text-align:center;z-index:10;">
            ${headlineHTML}${subtitleHTML}
          </div>
          ${deviceFrame}
        </div>`;
      break;
  }

  return `<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { width:${W}px; height:${H}px; ${bgCSS} font-family:'Inter',system-ui,-apple-system,sans-serif; overflow:hidden; }
</style>
</head><body>${bodyContent}</body></html>`;
}
