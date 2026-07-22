// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  fydesign — Professional Screenshot Renderer (Playwright)                  ║
// ║  Server-side HTML → PNG at exact App Store / Play Store resolutions        ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { DeviceType, DEVICE_SPECS } from './types';
import { launchBrowser } from './browser';

/**
 * Render arbitrary HTML at the given viewport size and return a PNG buffer.
 * Used by the visual-critic loop (any custom canvas size).
 */
export async function renderHtmlToPNG(
  html: string,
  width: number,
  height: number,
): Promise<Buffer> {
  const browser = await launchBrowser();
  try {
    const page = await browser.newPage();
    await page.setViewportSize({ width, height });
    await page.setContent(html, { waitUntil: 'networkidle', timeout: 15000 });
    await page.waitForTimeout(400);
    const buffer = await page.screenshot({ type: 'png', fullPage: false });
    return Buffer.from(buffer);
  } finally {
    await browser.close();
  }
}

/**
 * Render HTML mockup to PNG buffer
 */
export async function renderMockupToPNG(
  html: string,
  device: DeviceType,
): Promise<Buffer> {
  const spec = DEVICE_SPECS[device];
  const browser = await launchBrowser();

  try {
    const page = await browser.newPage();
    await page.setViewportSize({ width: spec.exportWidth, height: spec.exportHeight });
    await page.setContent(html, { waitUntil: 'networkidle' });
    await page.waitForTimeout(500);
    const buffer = await page.screenshot({ type: 'png', fullPage: false });
    return Buffer.from(buffer);
  } finally {
    await browser.close();
  }
}

/**
 * Batch render multiple mockups (reuses browser instance)
 */
export async function batchRenderMockups(
  mockups: { html: string; device: DeviceType; fileName: string }[],
): Promise<{ fileName: string; buffer: Buffer }[]> {
  const browser = await launchBrowser();
  const results: { fileName: string; buffer: Buffer }[] = [];

  try {
    for (let i = 0; i < mockups.length; i += 2) {
      const batch = mockups.slice(i, i + 2);
      const batchResults = await Promise.all(
        batch.map(async (mockup) => {
          const spec = DEVICE_SPECS[mockup.device];
          const page = await browser.newPage();
          await page.setViewportSize({ width: spec.exportWidth, height: spec.exportHeight });
          await page.setContent(mockup.html, { waitUntil: 'networkidle' });
          await page.waitForTimeout(300);
          const buffer = await page.screenshot({ type: 'png', fullPage: false });
          await page.close();
          return { fileName: mockup.fileName, buffer: Buffer.from(buffer) };
        }),
      );
      results.push(...batchResults);
    }
  } finally {
    await browser.close();
  }

  return results;
}
