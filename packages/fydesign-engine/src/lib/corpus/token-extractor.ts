// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Token Extractor — CSS variables, Tailwind config, design tokens             ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { readFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';

export interface DesignTokens {
  colors: Record<string, string>;
  spacing: Record<string, string>;
  borderRadius: Record<string, string>;
  fontFamily: Record<string, string[]>;
  screens: Record<string, string>;
  boxShadow: Record<string, string>;
}

export interface CSSVariable {
  name: string;
  value: string;
  file: string;
}

export interface ColorSystem {
  primary: string[];
  accent: string[];
  neutral: string[];
  semantic: { success: string; danger: string; warning: string; info: string };
}

export interface SpacingScale {
  base: number;
  scale: number[];
  radiusScale: number[];
}

export interface TypographySystem {
  headings: string;
  body: string;
  mono: string;
  scale: { size: string; lineHeight: string; weight: string }[];
}

export async function extractTailwindTokens(cloneDir: string): Promise<DesignTokens> {
  const defaults: DesignTokens = {
    colors: {},
    spacing: {},
    borderRadius: {},
    fontFamily: {},
    screens: {},
    boxShadow: {},
  };

  for (const name of ['tailwind.config.ts', 'tailwind.config.js', 'tailwind.config.mjs']) {
    const configPath = path.join(cloneDir, name);
    if (existsSync(configPath)) {
      try {
        const content = await readFile(configPath, 'utf-8');
        return parseTailwindConfig(content, defaults);
      } catch {
        continue;
      }
    }
  }

  return defaults;
}

function parseTailwindConfig(content: string, defaults: DesignTokens): DesignTokens {
  try {
    // Extract theme extension block (dotAll mode via [\s\S])
    const extendMatch = content.match(/extend\s*:\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}/);
    const themeMatch = content.match(/theme\s*:\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}/);

    const merged: Record<string, string> = {};

    // Parse colors: { name: '#hex' or { ... } }
    const colorsMatch = (extendMatch?.[1] || themeMatch?.[1] || '').match(/colors\s*:\s*\{([^}]+)\}/);
    if (colorsMatch) {
      Object.assign(merged, parseObjectLiteral(colorsMatch[1], 'colors'));
    }

    // Parse borderRadius
    const radiusMatch = (extendMatch?.[1] || '').match(/borderRadius\s*:\s*\{([^}]+)\}/);
    if (radiusMatch) {
      const radius = parseObjectLiteral(radiusMatch[1], 'radius');
      defaults.borderRadius = radius;
    }

    // Parse fontFamily
    const fontMatch = (extendMatch?.[1] || '').match(/fontFamily\s*:\s*\{([^}]+)\}/);
    if (fontMatch) {
      const fonts = parseObjectLiteral(fontMatch[1], 'font');
      for (const [k, v] of Object.entries(fonts)) {
        defaults.fontFamily[k] = v.split(',').map((s: string) => s.trim().replace(/['"]/g, ''));
      }
    }

    return defaults;
  } catch {
    return defaults;
  }
}

function parseObjectLiteral(text: string, _prefix: string): Record<string, string> {
  const result: Record<string, string> = {};
  text = text.replace(/\/\/[^\n]*/g, '').replace(/\/\*[\s\S]*?\*\//g, ''); // strip comments

  const pairRegex = /(\w+)\s*:\s*(['"][^'"]*['"]|[\w.]+|\[[^\]]*\])/g;
  let match;
  while ((match = pairRegex.exec(text)) !== null) {
    result[match[1]] = match[2].replace(/^['"]|['"]$/g, '').trim();
  }

  return result;
}

export async function extractCSSVariables(cloneDir: string): Promise<CSSVariable[]> {
  const variables: CSSVariable[] = [];
  const cssFiles = await findCSSFiles(cloneDir);

  for (const file of cssFiles) {
    try {
      const content = await readFile(file, 'utf-8');
      const varRegex = /(--[\w-]+)\s*:\s*([^;]+)/g;
      let match;
      while ((match = varRegex.exec(content)) !== null) {
        variables.push({
          name: match[1],
          value: match[2].trim(),
          file: path.relative(cloneDir, file),
        });
      }
    } catch { /* skip */ }
  }

  return variables;
}

async function findCSSFiles(dir: string): Promise<string[]> {
  const results: string[] = [];
  const { readdir } = await import('node:fs/promises');

  async function walk(d: string) {
    try {
      const entries = await readdir(d, { withFileTypes: true });
      for (const entry of entries) {
        if (entry.name.startsWith('.') || entry.name === 'node_modules') continue;
        const full = path.join(d, entry.name);
        if (entry.isDirectory()) {
          await walk(full);
        } else if (/\.(css|scss|less)$/.test(entry.name)) {
          results.push(full);
        }
      }
    } catch { /* skip */ }
  }

  await walk(dir);
  return results.slice(0, 50); // Limit
}

export function extractColorSystem(tokens: DesignTokens, cssVars: CSSVariable[]): ColorSystem {
  const allColors = { ...tokens.colors };

  for (const v of cssVars) {
    if (v.name.includes('color') || v.name.includes('--c-') || /^--[a-z]+-\d+$/.test(v.name)) {
      allColors[v.name] = v.value;
    }
  }

  const entries = Object.entries(allColors);

  return {
    primary: entries.filter(([, v]) => /#([0-9A-Fa-f]{3,8})/.test(v)).slice(0, 5).map(([k]) => k),
    accent: entries.filter(([k]) => /accent|secondary|highlight/i.test(k)).slice(0, 3).map(([k]) => k),
    neutral: entries.filter(([k]) => /neutral|gray|slate|zinc|stone/i.test(k)).slice(0, 5).map(([k]) => k),
    semantic: {
      success: cssVars.find((v) => /success|green/i.test(v.name))?.value || '#22c55e',
      danger: cssVars.find((v) => /danger|error|red/i.test(v.name))?.value || '#ef4444',
      warning: cssVars.find((v) => /warning|yellow|amber/i.test(v.name))?.value || '#f59e0b',
      info: cssVars.find((v) => /info|blue/i.test(v.name))?.value || '#3b82f6',
    },
  };
}

export function extractSpacingScale(_cssVars: CSSVariable[], _tailwindConfig?: unknown): SpacingScale {
  return {
    base: 4,
    scale: [0, 2, 4, 6, 8, 10, 12, 14, 16, 20, 24, 28, 32, 36, 40, 48, 56, 64, 72, 80, 96],
    radiusScale: [0, 2, 4, 6, 8, 12, 16, 24],
  };
}

export function extractTypographySystem(tokens: DesignTokens, cssVars: CSSVariable[]): TypographySystem {
  const headingVar = cssVars.find((v) => /heading|title|display/i.test(v.name));
  const bodyVar = cssVars.find((v) => /body|text/i.test(v.name));
  const monoVar = cssVars.find((v) => /mono|code/i.test(v.name));

  return {
    headings: tokens.fontFamily?.heading?.join(', ') || headingVar?.value || 'Inter, sans-serif',
    body: tokens.fontFamily?.body?.join(', ') || bodyVar?.value || 'Inter, sans-serif',
    mono: tokens.fontFamily?.mono?.join(', ') || monoVar?.value || 'JetBrains Mono, monospace',
    scale: [
      { size: '0.75rem', lineHeight: '1rem', weight: '400' },
      { size: '0.875rem', lineHeight: '1.25rem', weight: '400' },
      { size: '1rem', lineHeight: '1.5rem', weight: '400' },
      { size: '1.125rem', lineHeight: '1.75rem', weight: '500' },
      { size: '1.25rem', lineHeight: '1.75rem', weight: '600' },
      { size: '1.5rem', lineHeight: '2rem', weight: '600' },
      { size: '2rem', lineHeight: '2.5rem', weight: '700' },
      { size: '3rem', lineHeight: '3.75rem', weight: '700' },
    ],
  };
}
