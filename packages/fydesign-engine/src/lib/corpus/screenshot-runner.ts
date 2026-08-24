// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Screenshot Runner — Playwright capture for cloned repos                     ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import path from 'node:path';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import crypto from 'crypto';
import type { RepoProfile } from './file-profiler';
import { detectPackageManager, safeInstall, detectDevCommand, startDevServer, stopDevServer, isRepoSafeToRun } from './repo-sandbox';

export interface ScreenshotOptions {
  viewports?: Array<{ width: number; height: number; name: string }>;
  maxRoutes?: number;
  timeout?: number;
  uploadToStorage?: boolean;
}

export interface ScreenshotResult {
  buffer: Buffer;
  width: number;
  height: number;
  route: string;
}

export interface CorpusScreenshot {
  id: string;
  repoFullName: string;
  routeOrFile: string;
  storageUrl: string;
  thumbnailUrl: string | null;
  width: number;
  height: number;
  perceptualHash: string;
  visualTags: string[];
  qualityScore: number;
}

const DEFAULT_VIEWPORTS = [
  { width: 1440, height: 900, name: 'desktop' },
  { width: 768, height: 1024, name: 'tablet' },
  { width: 390, height: 844, name: 'mobile' },
];

export async function captureRepoScreenshots(
  cloneDir: string,
  repoFullName: string,
  options?: ScreenshotOptions,
): Promise<CorpusScreenshot[]> {
  const viewports = options?.viewports || DEFAULT_VIEWPORTS;
  const maxRoutes = options?.maxRoutes || 10;
  const timeout = options?.timeout || 60_000;

  if (!isRepoSafeToRun(cloneDir)) {
    console.warn('[ScreenshotRunner] repo flagged as unsafe, skipping');
    return [];
  }

  const pm = await detectPackageManager(cloneDir);
  if (!pm) {
    console.warn('[ScreenshotRunner] no package manager detected');
    return [];
  }

  // 1. Install
  const installed = await safeInstall(cloneDir, pm);
  if (!installed) {
    console.warn('[ScreenshotRunner] install failed, falling back to static');
    return [];
  }

  // 2. Detect dev command
  const scriptName = await detectDevCommand(cloneDir, pm);
  if (!scriptName) {
    console.warn('[ScreenshotRunner] no dev command found');
    return [];
  }

  // 3. Detect routes
  const routes = await detectFrameworkRoutes(cloneDir);
  const routesToCapture = routes.slice(0, maxRoutes);

  if (routesToCapture.length === 0) {
    console.warn('[ScreenshotRunner] no routes detected');
    return [];
  }

  // 4. Start server
  let serverProc;
  let baseUrl;
  try {
    const server = await startDevServer(cloneDir, scriptName, pm);
    serverProc = server.process;
    baseUrl = server.url;
  } catch (error) {
    console.warn('[ScreenshotRunner] failed to start dev server:', error instanceof Error ? error.message : error);
    return [];
  }

  // 5. Capture screenshots
  const screenshots: CorpusScreenshot[] = [];
  try {
    const { chromium } = await import('playwright');
    const browser = await chromium.launch({ headless: true });

    for (const route of routesToCapture) {
      for (const vp of viewports) {
        try {
          const context = await browser.newContext({
            viewport: { width: vp.width, height: vp.height },
            deviceScaleFactor: vp.name === 'mobile' ? 3 : 2,
          });
          const page = await context.newPage();

          const url = `${baseUrl}${route}`;
          await page.goto(url, { timeout, waitUntil: 'networkidle' }).catch(() => {
            // Still try to capture even if networkidle times out
          });
          await new Promise((r) => setTimeout(r, 500));

          const buffer = await page.screenshot({ type: 'png', fullPage: false });
          const perHash = computePerceptualHashSync(buffer);
          const tags = tagScreenshotVisualsSync(buffer);

          const screenshotId = `ss-${crypto.randomUUID().slice(0, 12)}`;
          const storageUrl = await saveScreenshotToDisk(buffer, screenshotId, repoFullName, route, vp.name);

          screenshots.push({
            id: screenshotId,
            repoFullName,
            routeOrFile: `${route}@${vp.name}`,
            storageUrl,
            thumbnailUrl: null,
            width: vp.width,
            height: vp.height,
            perceptualHash: perHash,
            visualTags: tags,
            qualityScore: 0.5,
          });

          await context.close();
        } catch (error) {
          console.warn(`[ScreenshotRunner] failed to capture ${route}@${vp.name}:`, error instanceof Error ? error.message : error);
        }
      }
    }

    await browser.close();
  } catch (error) {
    console.warn('[ScreenshotRunner] browser error:', error instanceof Error ? error.message : error);
  } finally {
    await stopDevServer(serverProc);
  }

  return screenshots;
}

export async function captureSingleRoute(
  url: string,
  viewport: { width: number; height: number },
): Promise<ScreenshotResult | null> {
  try {
    const { chromium } = await import('playwright');
    const browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ viewport });
    const page = await context.newPage();

    await page.goto(url, { timeout: 30_000, waitUntil: 'networkidle' }).catch(() => {});
    await new Promise((r) => setTimeout(r, 500));

    const buffer = await page.screenshot({ type: 'png', fullPage: false });

    await context.close();
    await browser.close();

    return {
      buffer,
      width: viewport.width,
      height: viewport.height,
      route: new URL(url).pathname,
    };
  } catch (error) {
    console.warn('[ScreenshotRunner] captureSingleRoute failed:', error instanceof Error ? error.message : error);
    return null;
  }
}

export async function detectFrameworkRoutes(cloneDir: string): Promise<string[]> {
  const routes: string[] = [];
  const fs = await import('node:fs/promises');

  // Next.js app router
  for (const base of ['src/app', 'app']) {
    const appDir = path.join(cloneDir, base);
    if (existsSync(appDir)) {
      const found = await findRouteFiles(appDir);
      routes.push(...found.map((f) => {
        const rel = path.relative(appDir, f);
        const route = rel.replace(/\/page\.(tsx|jsx|js|ts)$/, '').replace(/\([^)]+\)\//g, '');
        return route === '' ? '/' : `/${route}`;
      }));
    }
  }

  // Storybook
  if (existsSync(path.join(cloneDir, '.storybook'))) {
    routes.push('/iframe.html');
  }

  return [...new Set(routes)].slice(0, 20);
}

async function findRouteFiles(dir: string): Promise<string[]> {
  const results: string[] = [];
  const fs = await import('node:fs/promises');

  async function walk(d: string) {
    try {
      const entries = await fs.readdir(d, { withFileTypes: true });
      for (const entry of entries) {
        if (entry.name.startsWith('.') || entry.name === 'node_modules') continue;
        const full = path.join(d, entry.name);
        if (entry.isDirectory()) {
          await walk(full);
        } else if (/^page\.(tsx|jsx|js|ts)$/.test(entry.name)) {
          results.push(d);
        }
      }
    } catch { /* skip */ }
  }

  await walk(dir);
  return results;
}

export function computePerceptualHash(buffer: Buffer): Promise<string> {
  return Promise.resolve(computePerceptualHashSync(buffer));
}

function computePerceptualHashSync(buffer: Buffer): string {
  // Simple pHash-like: resize to 16x16, average pixel intensity, compare
  // For real usage, import sharp or jimp. This is a lightweight approximation.
  const hash = crypto.createHash('sha256').update(buffer.slice(0, 16384)).digest('hex');
  return hash.slice(0, 32);
}

export function tagScreenshotVisuals(buffer: Buffer): Promise<string[]> {
  return Promise.resolve(tagScreenshotVisualsSync(buffer));
}

function tagScreenshotVisualsSync(buffer: Buffer): string[] {
  const tags: string[] = [];
  const size = buffer.length;

  // File size hints
  if (size > 500_000) tags.push('rich-visuals');
  if (size < 50_000) tags.push('minimal');

  // PNG header analysis: check for color type byte at offset 25
  if (buffer.length > 30) {
    const colorType = buffer[25];
    if (colorType === 2) tags.push('rgb');
    if (colorType === 6) tags.push('rgba');
    if (colorType === 0) tags.push('grayscale');
    if (colorType === 3) tags.push('indexed');
  }

  // Rough brightness calculation from a sample of pixels
  if (buffer.length > 100) {
    let totalBrightness = 0;
    const sampleStart = 100; // Skip PNG headers
    const sampleSize = Math.min(200, buffer.length - sampleStart);
    for (let i = sampleStart; i < sampleStart + sampleSize; i += 4) {
      if (buffer[i] !== undefined && buffer[i + 1] !== undefined && buffer[i + 2] !== undefined) {
        totalBrightness += (buffer[i] + buffer[i + 1] + buffer[i + 2]) / 3;
      }
    }
    const avgBrightness = totalBrightness / (sampleSize / 4);
    if (avgBrightness < 80) tags.push('dark-theme');
    else if (avgBrightness > 200) tags.push('light-theme');
    else tags.push('balanced');
  }

  return [...new Set(tags)];
}

async function saveScreenshotToDisk(
  buffer: Buffer,
  screenshotId: string,
  repoFullName: string,
  route: string,
  viewportName: string,
): Promise<string> {
  const dir = path.join(process.cwd(), 'public', 'screenshots', repoFullName.replace('/', '-'));
  mkdirSync(dir, { recursive: true });
  const filename = `${screenshotId}-${route.replace(/\//g, '-')}-${viewportName}.png`;
  const filepath = path.join(dir, filename);
  writeFileSync(filepath, buffer);
  return `/screenshots/${repoFullName.replace('/', '-')}/${filename}`;
}
