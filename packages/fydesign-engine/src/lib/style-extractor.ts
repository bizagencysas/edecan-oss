// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  fydesign — Style / Theme Extractor                                        ║
// ║  Parses theme.ts, colors.ts, etc. to extract the app's design system       ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { ThemeInfo } from './types';

interface FileContent {
  path: string;
  content: string;
}

/**
 * Extract theme information from downloaded theme/style files
 */
export function extractThemeInfo(files: FileContent[]): ThemeInfo {
  const defaults: ThemeInfo = {
    primaryColor: '#6366F1',
    secondaryColor: '#8B5CF6',
    backgroundColor: '#FFFFFF',
    darkBackgroundColor: '#000000',
    textColor: '#1a1a1a',
    darkTextColor: '#FAFAFA',
    accentColors: ['#10B981', '#F59E0B', '#3B82F6'],
    successColor: '#10B981',
    dangerColor: '#EF4444',
    warningColor: '#F59E0B',
    brandName: '',
    hasDarkMode: false,
    borderRadius: 16,
  };

  if (files.length === 0) return defaults;

  // Merge all file contents for analysis
  const allCode = files.map((f) => f.content).join('\n');

  return {
    primaryColor: findColor(allCode, [
      'fyYellow', 'primary', 'brand', 'accent', 'main',
    ]) || defaults.primaryColor,

    secondaryColor: findColor(allCode, [
      'secondary', 'secondaryColor',
    ]) || defaults.secondaryColor,

    backgroundColor: findColor(allCode, [
      "bg:", "bg':", 'background:', 'backgroundColor:',
    ]) || defaults.backgroundColor,

    darkBackgroundColor: findDarkBg(allCode) || defaults.darkBackgroundColor,

    textColor: findColor(allCode, [
      "text:", "text':", 'textColor',
    ]) || defaults.textColor,

    darkTextColor: findColor(allCode, [
      'darkText', 'textOnDark',
    ]) || defaults.darkTextColor,

    accentColors: findAccentColors(allCode) || defaults.accentColors,

    successColor: findColor(allCode, ['success']) || defaults.successColor,
    dangerColor: findColor(allCode, ['destructive', 'danger', 'error']) || defaults.dangerColor,
    warningColor: findColor(allCode, ['warning']) || defaults.warningColor,

    brandName: findBrandName(allCode),

    hasDarkMode: allCode.includes('darkColors') ||
      allCode.includes('dark:') ||
      allCode.includes('isDark') ||
      allCode.includes('colorScheme'),

    borderRadius: findBorderRadius(allCode) || defaults.borderRadius,
  };
}

/* ── Color Finders ────────────────────────────────────────────────────────── */

function findColor(code: string, keys: string[]): string | null {
  for (const key of keys) {
    // Pattern: key: '#HEXCODE' or key: "rgba(...)"
    const patterns = [
      new RegExp(`${escapeRegex(key)}\\s*[:=]\\s*['"\`](#[0-9a-fA-F]{3,8})['"\`]`),
      new RegExp(`${escapeRegex(key)}\\s*[:=]\\s*['"\`](rgba?\\([^)]+\\))['"\`]`),
      new RegExp(`${escapeRegex(key)}\\s*[:=]\\s*['"\`](hsl[a]?\\([^)]+\\))['"\`]`),
    ];

    for (const pattern of patterns) {
      const match = code.match(pattern);
      if (match) return match[1];
    }
  }
  return null;
}

function findDarkBg(code: string): string | null {
  // Look inside darkColors block
  const darkBlock = code.match(/darkColors\s*[:=]\s*\{[\s\S]*?\n\}/);
  if (darkBlock) {
    const bgMatch = darkBlock[0].match(/bg\s*:\s*['"`](#[0-9a-fA-F]{3,8})['"`]/);
    if (bgMatch) return bgMatch[1];
  }
  return null;
}

function findAccentColors(code: string): string[] | null {
  const hexRegex = /#[0-9a-fA-F]{6}/g;
  const allHexColors = code.match(hexRegex);

  if (!allHexColors) return null;

  // Deduplicate and filter out common non-accent colors
  const unique = [...new Set(allHexColors)];
  const blackWhite = ['#000000', '#FFFFFF', '#ffffff', '#1a1a1a', '#fafafa'];
  const accents = unique.filter(
    (c) =>
      !blackWhite.includes(c) &&
      !c.toLowerCase().startsWith('#f8') && // near-white
      !c.toLowerCase().startsWith('#0f') && // near-black
      !c.toLowerCase().startsWith('#e2'), // light gray
  );

  // Return top 5 most "colorful" ones
  return accents
    .sort((a, b) => colorSaturation(b) - colorSaturation(a))
    .slice(0, 5);
}

function colorSaturation(hex: string): number {
  const r = parseInt(hex.slice(1, 3), 16) / 255;
  const g = parseInt(hex.slice(3, 5), 16) / 255;
  const b = parseInt(hex.slice(5, 7), 16) / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  if (max === 0) return 0;
  return (max - min) / max;
}

function findBrandName(code: string): string {
  // Look for explicit brand name
  const brandMatch = code.match(
    /(?:brandName|appName|APP_NAME|brand)\s*[:=]\s*['"`]([^'"`]+)['"`]/i,
  );
  if (brandMatch) return brandMatch[1];

  // Look in comments
  const commentMatch = code.match(
    /(?:\/\/|\/\*)\s*(?:Fy\s*\w+|[A-Z][a-z]+\s*[A-Z][a-z]+)/,
  );
  if (commentMatch) {
    const name = commentMatch[0].replace(/^(?:\/\/|\/\*)\s*/, '').trim();
    if (name.length < 30) return name;
  }

  return '';
}

function findBorderRadius(code: string): number | null {
  // Look for most common border radius value
  const radiusValues: number[] = [];
  const radiusRegex = /borderRadius\s*:\s*(\d+)/g;
  let match;
  while ((match = radiusRegex.exec(code)) !== null) {
    radiusValues.push(parseInt(match[1]));
  }

  // Also check radius tokens
  const tokenRegex = /(?:lg|md|xl)\s*:\s*(\d+)/g;
  while ((match = tokenRegex.exec(code)) !== null) {
    radiusValues.push(parseInt(match[1]));
  }

  if (radiusValues.length === 0) return null;

  // Return the most common value
  const counts = new Map<number, number>();
  radiusValues.forEach((v) => counts.set(v, (counts.get(v) || 0) + 1));
  let maxCount = 0;
  let mostCommon = 16;
  counts.forEach((count, value) => {
    if (count > maxCount) {
      maxCount = count;
      mostCommon = value;
    }
  });

  return mostCommon;
}

function escapeRegex(str: string): string {
  return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
