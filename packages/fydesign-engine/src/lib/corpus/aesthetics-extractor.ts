// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Aesthetics Extractor — Spacing, glassmorphism, editorial/fintech aesthetics ║
// ║  Navigation systems, premium composition signals, restraint scoring          ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { readFile, readdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';
import type { RepoProfile } from './file-profiler';

// ── Helpers ───────────────────────────────────────────────────────────────

async function readFilesInDir(dir: string, pattern: RegExp): Promise<string[]> {
  const results: string[] = [];
  if (!existsSync(dir)) return results;
  async function walk(d: string) {
    try {
      const entries = await readdir(d, { withFileTypes: true });
      for (const entry of entries) {
        if (entry.name.startsWith('.') || entry.name === 'node_modules') continue;
        const full = path.join(d, entry.name);
        if (entry.isDirectory()) { await walk(full); }
        else if (pattern.test(entry.name)) { results.push(full); }
      }
    } catch { /* skip */ }
  }
  await walk(dir);
  return results.slice(0, 50);
}

async function readFileContent(filePath: string): Promise<string> {
  try { return await readFile(filePath, 'utf-8'); }
  catch { return ''; }
}

// ── Spacing Heuristics ────────────────────────────────────────────────────

export interface SpacingHeuristic {
  type: 'padding' | 'margin' | 'gap' | 'section-spacing' | 'card-padding';
  values: string[];
  pattern: string;
  file: string;
  confidence: number;
}

export async function extractSpacingHeuristics(
  profile: RepoProfile,
  cloneDir: string,
): Promise<SpacingHeuristic[]> {
  const heuristics: SpacingHeuristic[] = [];
  if (!profile.componentsDir && !profile.stylesDir) return heuristics;

  const searchDirs = [profile.componentsDir, profile.stylesDir, profile.appDir]
    .filter(Boolean)
    .map((d) => path.join(cloneDir, d!));

  for (const dir of searchDirs) {
    const files = await readFilesInDir(dir, /\.(tsx|jsx|css)$/);
    for (const file of files.slice(0, 20)) {
      const content = await readFileContent(file);
      if (!content) continue;

      const relFile = path.relative(cloneDir, file);

      // Extract padding values
      const padMatches = content.match(/p[xy]?-?(?:\[)?(\d+)(?:px)?(?:\])?/g) || [];
      const padValues = [...new Set(padMatches.map((m) => m.replace(/[^0-9]/g, '')))].filter((v) => parseInt(v) >= 4);
      if (padValues.length >= 3) {
        heuristics.push({
          type: 'padding',
          values: padValues.slice(0, 10),
          pattern: `Padding scale: ${padValues.slice(0, 5).join(', ')}px`,
          file: relFile,
          confidence: Math.min(0.4 + padValues.length * 0.05, 0.85),
        });
      }

      // Extract gap values
      const gapMatches = content.match(/gap-?(?:\[)?(\d+)(?:px)?(?:\])?/g) || [];
      const gapValues = [...new Set(gapMatches.map((m) => m.replace(/[^0-9]/g, '')))].filter((v) => parseInt(v) >= 2);
      if (gapValues.length >= 2) {
        heuristics.push({
          type: 'gap',
          values: gapValues.slice(0, 10),
          pattern: `Gap scale: ${gapValues.slice(0, 5).join(', ')}px`,
          file: relFile,
          confidence: Math.min(0.4 + gapValues.length * 0.08, 0.8),
        });
      }

      // Extract section spacing (py-16, py-24, etc.)
      const sectionMatch = content.match(/py-(?:\[)?(\d{2,3})(?:px)?(?:\])?/g) || [];
      const sectionValues = [...new Set(sectionMatch.map((m) => m.replace(/[^0-9]/g, '')))].filter((v) => parseInt(v) >= 12);
      if (sectionValues.length >= 2) {
        heuristics.push({
          type: 'section-spacing',
          values: sectionValues.slice(0, 8),
          pattern: `Section spacing: ${sectionValues.slice(0, 4).join(', ')}px vertical`,
          file: relFile,
          confidence: 0.7,
        });
      }
    }
  }

  return heuristics.slice(0, 20);
}

// ── Editorial Aesthetics Detection ────────────────────────────────────────

export interface EditorialSignal {
  type: 'serif-typography' | 'asymmetric-layout' | 'generous-whitespace' | 'oversized-headlines' | 'minimal-color' | 'cinematic-photography';
  confidence: number;
  evidence: string[];
}

export async function detectEditorialAesthetics(
  profile: RepoProfile,
  cloneDir: string,
): Promise<EditorialSignal[]> {
  const signals: EditorialSignal[] = [];

  // Check font usage via dependencies
  const depString = JSON.stringify(profile.dependencies).toLowerCase();
  const isSerif = /serif|georgia|times|garamond|playfair|bodoni|didot|caslon|merriweather|newsreader/i.test(depString);

  if (isSerif) {
    signals.push({ type: 'serif-typography', confidence: 0.85, evidence: ['Serif font dependency detected'] });
  }

  // Check for oversized typography by reading component files
  if (profile.componentsDir) {
    const compDir = path.join(cloneDir, profile.componentsDir);
    const files = await readFilesInDir(compDir, /\.(tsx|jsx)$/);
    let largeTextFound = false;
    for (const file of files.slice(0, 15)) {
      const content = await readFileContent(file);
      if (/text-[5-9]xl|text-\[(?:[5-9]|1\d)rem\]|text-\[(?:[4-9]\d|1\d{2})px\]/.test(content)) {
        largeTextFound = true;
        break;
      }
    }
    if (largeTextFound) {
      signals.push({ type: 'oversized-headlines', confidence: 0.7, evidence: ['Large headline sizes detected in components'] });
    }
  }

  // Check for minimal color palette via dependencies (not tailwind -> likely custom colors)
  if (!profile.hasTailwind) {
    signals.push({ type: 'minimal-color', confidence: 0.5, evidence: ['Custom CSS (not Tailwind) suggests intentional color choices'] });
  }

  // Asymmetric layout indicators from routes
  const routeString = profile.routes.join(' ').toLowerCase();
  if (/about|story|journal|editorial|magazine|lookbook|gallery/i.test(routeString)) {
    signals.push({ type: 'asymmetric-layout', confidence: 0.55, evidence: ['Editorial-style route structure'] });
  }

  return signals;
}

// ── Fintech Aesthetics Detection ──────────────────────────────────────────

export interface FintechSignal {
  type: 'trust-cues' | 'numeric-precision' | 'security-language' | 'structured-cards' | 'calm-palette';
  confidence: number;
  evidence: string[];
}

export async function detectFintechAesthetics(
  profile: RepoProfile,
  cloneDir: string,
): Promise<FintechSignal[]> {
  const signals: FintechSignal[] = [];

  // Scan dependencies and routes for fintech signals
  const depString = Object.keys(profile.dependencies).join(' ').toLowerCase();
  const routeString = profile.routes.join(' ').toLowerCase();
  const haystack = `${depString} ${routeString}`;

  if (/verif|security|compliance|badge|trust|kyc|auth|2fa|sso|oauth|passkey/i.test(haystack)) {
    signals.push({
      type: 'trust-cues',
      confidence: 0.75,
      evidence: ['Verification and security dependencies detected'],
    });
  }

  if (/balance|amount|transaction|currency|payment|invoice|credit|debit|wire|stripe|plaid|bank/i.test(haystack)) {
    signals.push({
      type: 'numeric-precision',
      confidence: 0.8,
      evidence: ['Financial numeric components detected'],
    });
  }

  if (/encrypt|protect|secure|private|sensitive|pci|soc2|gdpr|hipaa/i.test(haystack)) {
    signals.push({
      type: 'security-language',
      confidence: 0.7,
      evidence: ['Security terminology in codebase'],
    });
  }

  // Calm palette: check if using slate/zinc/stone
  if (/slate|zinc|stone|neutral|navy/i.test(depString)) {
    signals.push({
      type: 'calm-palette',
      confidence: 0.55,
      evidence: ['Calm color palette references'],
    });
  }

  return signals;
}

// ── Navigation System Extraction ──────────────────────────────────────────

export interface NavigationSystem {
  type: 'top-nav' | 'sidebar' | 'bottom-nav' | 'hamburger-drawer' | 'mega-menu' | 'breadcrumb' | 'tab-bar';
  pattern: string;
  files: string[];
  responsive: boolean;
  confidence: number;
}

export async function extractNavigationSystems(
  profile: RepoProfile,
  cloneDir: string,
): Promise<NavigationSystem[]> {
  const systems: NavigationSystem[] = [];
  if (!profile.componentsDir) return systems;

  const compDir = path.join(cloneDir, profile.componentsDir);
  const navFiles = await readFilesInDir(compDir, /(?:nav|header|sidebar|menu|tab|drawer)/i);

  if (navFiles.length === 0) return systems;

  const fileNames = navFiles.map((f) => path.basename(f).toLowerCase());
  const fileString = fileNames.join(' ');
  let fullContent = '';
  for (const f of navFiles.slice(0, 10)) {
    fullContent += await readFileContent(f);
  }

  const haystack = `${fileString} ${fullContent}`.toLowerCase();
  const relFiles = navFiles.map((f) => path.relative(cloneDir, f));

  if (/nav(?:bar|igation)?|header|topbar/i.test(haystack)) {
    const files = relFiles.filter((f) => /nav|header|topbar/i.test(path.basename(f)));
    systems.push({
      type: 'top-nav',
      pattern: 'Top navigation bar with logo + links + CTA',
      files: files.slice(0, 5),
      responsive: /mobile|responsive|hamburger|menu/i.test(haystack),
      confidence: 0.85,
    });
  }

  if (/sidebar|side-nav|drawer|aside/i.test(haystack)) {
    const files = relFiles.filter((f) => /sidebar|drawer/i.test(path.basename(f)));
    systems.push({
      type: 'sidebar',
      pattern: 'Side navigation with collapsible sections',
      files: files.slice(0, 5),
      responsive: /collaps|toggle|responsive|mobile/i.test(haystack),
      confidence: 0.85,
    });
  }

  if (/bottom-nav|tab-bar|mobile-nav/i.test(haystack)) {
    const files = relFiles.filter((f) => /bottom|tab|mobile/i.test(path.basename(f)));
    systems.push({
      type: 'bottom-nav',
      pattern: 'Bottom tab navigation for mobile',
      files: files.slice(0, 5),
      responsive: true,
      confidence: 0.8,
    });
  }

  if (/mega-menu|dropdown|flyout/i.test(haystack)) {
    const files = relFiles.filter((f) => /mega|dropdown|flyout/i.test(path.basename(f)));
    systems.push({
      type: 'mega-menu',
      pattern: 'Mega menu with multi-column dropdown',
      files: files.slice(0, 5),
      responsive: /mobile|responsive/i.test(haystack),
      confidence: 0.7,
    });
  }

  if (/breadcrumb|bread-crumb/i.test(haystack)) {
    const files = relFiles.filter((f) => /breadcrumb/i.test(path.basename(f)));
    systems.push({
      type: 'breadcrumb',
      pattern: 'Breadcrumb navigation trail',
      files: files.slice(0, 5),
      responsive: false,
      confidence: 0.9,
    });
  }

  return systems;
}

// ── Premium Composition Scoring ───────────────────────────────────────────

export interface PremiumScore {
  overall: number;
  breakdown: {
    layout: number;
    spacing: number;
    typography: number;
    color: number;
    originality: number;
  };
  signals: string[];
  redFlags: string[];
}

export function scorePremiumComposition(profile: RepoProfile): PremiumScore {
  let layout = 0.5;
  let spacing = 0.5;
  let typography = 0.5;
  let color = 0.5;
  let originality = 0.5;
  const signals: string[] = [];
  const redFlags: string[] = [];

  if (profile.hasRadix || profile.hasShadcn) { layout += 0.15; signals.push('Uses professional UI primitives (Radix/shadcn)'); }
  if (profile.framework === 'nextjs') { layout += 0.05; signals.push('Next.js App Router structure'); }
  if (profile.totalFrontendFiles > 50) { layout += 0.05; signals.push('Substantial component architecture'); }

  if (profile.hasTailwind) { spacing += 0.15; signals.push('Tailwind CSS spacing system'); }
  if (profile.totalFrontendFiles > 100) { spacing += 0.05; signals.push('Large frontend codebase suggests deliberate spacing'); }

  if (profile.hasTypescript) { typography += 0.05; signals.push('TypeScript codebase'); }

  const depKeys = Object.keys(profile.dependencies);
  const depString = depKeys.join(' ').toLowerCase();
  if (/framer-motion|gsap|motion/.test(depString)) { typography += 0.05; signals.push('Animation library (motion-aware design)'); }

  if (!profile.hasTailwind) { color += 0.1; signals.push('Custom CSS suggests intentional color design'); }
  if (/radix|shadcn|headless/.test(depString)) { color += 0.05; signals.push('Headless UI components (designer-controlled color)'); }

  const routeString = profile.routes.join(' ').toLowerCase();
  if (/template|boilerplate|starter/.test(routeString)) { originality -= 0.2; redFlags.push('Template/boilerplate routing detected'); }
  if (/dashboard/.test(routeString) && profile.totalFrontendFiles < 20) { originality -= 0.1; redFlags.push('Very small frontend for dashboard scope'); }

  layout = Math.min(1, Math.max(0, layout));
  spacing = Math.min(1, Math.max(0, spacing));
  typography = Math.min(1, Math.max(0, typography));
  color = Math.min(1, Math.max(0, color));
  originality = Math.min(1, Math.max(0, originality));

  return {
    overall: Math.round((layout * 0.3 + spacing * 0.2 + typography * 0.2 + color * 0.15 + originality * 0.15) * 100) / 100,
    breakdown: { layout, spacing, typography, color, originality },
    signals,
    redFlags,
  };
}
