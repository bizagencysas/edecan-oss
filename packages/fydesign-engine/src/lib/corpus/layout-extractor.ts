// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Layout Extractor — Grid systems, flex patterns, responsive breakpoints       ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { readFile, readdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';

export interface LayoutPattern {
  type: 'grid' | 'flex' | 'absolute' | 'fixed' | 'sticky';
  description: string;
  cssClasses: string[];
  file: string;
  lineNumber?: number;
}

export interface GridSystem {
  columns: number;
  gap: string;
  areas: string[];
}

export interface ContainerPattern {
  type: string;
  maxWidth: string;
  padding: string;
}

export async function extractLayoutPatterns(
  _profile: { appDir: string | null; componentsDir: string | null; framework: string },
  cloneDir: string,
): Promise<LayoutPattern[]> {
  const patterns: LayoutPattern[] = [];
  const files = await findComponentFiles(cloneDir);

  for (const file of files) {
    try {
      const content = await readFile(file, 'utf-8');
      const relPath = path.relative(cloneDir, file);

      // Grid detection
      if (/\bgrid\b/.test(content)) {
        const gridLines = content.split('\n').filter((l) => /\bgrid\b/.test(l));
        const gridClasses = gridLines.flatMap((l) => {
          const m = l.match(/className\s*=\s*["'`]([^"'`]+)["'`]/);
          return m ? m[1].split(/\s+/).filter((c) => /grid/i.test(c)) : [];
        });
        if (gridClasses.length > 0) {
          patterns.push({
            type: 'grid',
            description: `Grid layout using ${gridClasses.slice(0, 5).join(', ')}`,
            cssClasses: gridClasses.slice(0, 10),
            file: relPath,
          });
        }
      }

      // Flex detection
      if (/\bflex\b/.test(content) && !/\bgrid\b/.test(content.slice(0, 300))) {
        const flexLines = content.split('\n').filter((l) => /\bflex\b/.test(l));
        const flexClasses = flexLines.flatMap((l) => {
          const m = l.match(/className\s*=\s*["'`]([^"'`]+)["'`]/);
          return m ? m[1].split(/\s+/).filter((c) => /flex/i.test(c)) : [];
        });
        if (flexClasses.length > 0) {
          patterns.push({
            type: 'flex',
            description: `Flex layout using ${flexClasses.slice(0, 5).join(', ')}`,
            cssClasses: flexClasses.slice(0, 10),
            file: relPath,
          });
        }
      }

      // Position patterns
      const positionClasses = content.match(/className\s*=\s*["'`]([^"'`]*(?:absolute|fixed|sticky)[^"'`]*)["'`]/g);
      if (positionClasses) {
        for (const pc of positionClasses) {
          const m = pc.match(/["'`]([^"'`]+)["'`]/);
          if (m) {
            const classes = m[1].split(/\s+/).filter((c) => /absolute|fixed|sticky/.test(c));
            for (const c of classes) {
              patterns.push({
                type: c as 'absolute' | 'fixed' | 'sticky',
                description: `${c} positioning detected`,
                cssClasses: [c],
                file: relPath,
              });
            }
          }
        }
      }
    } catch { /* skip */ }
  }

  return patterns.slice(0, 30);
}

export function detectGridSystem(fileContent: string): GridSystem | null {
  const gridColsMatch = fileContent.match(/grid-cols-(\d+)/);
  const gapMatch = fileContent.match(/gap-(\d+|\[[^\]]+\])/);

  if (!gridColsMatch) return null;

  const areas: string[] = [];
  const areaRegex = /grid-area\s*:\s*['"](\w+)['"]/g;
  let m;
  while ((m = areaRegex.exec(fileContent)) !== null) {
    areas.push(m[1]);
  }

  return {
    columns: parseInt(gridColsMatch[1], 10),
    gap: gapMatch ? gapMatch[1] : '4',
    areas: [...new Set(areas)],
  };
}

export function detectResponsiveBreakpoints(fileContent: string): string[] {
  const breakpoints = new Set<string>();
  const bpRegex = /(?:sm|md|lg|xl|2xl):\S+/g;
  let m;
  while ((m = bpRegex.exec(fileContent)) !== null) {
    breakpoints.add(m[0].split(':')[0]);
  }
  return [...breakpoints];
}

export async function extractContainerPatterns(
  files: { path: string; content: string }[],
): Promise<ContainerPattern[]> {
  const patterns: ContainerPattern[] = [];

  for (const file of files) {
    const containerMatch = file.content.match(/className\s*=\s*["'`]([^"'`]*container[^"'`]*)["'`]/g);
    if (containerMatch) {
      for (const cm of containerMatch) {
        const m = cm.match(/["'`]([^"'`]+)["'`]/);
        if (m) {
          const classes = m[1];
          const maxWMatch = classes.match(/max-w-(\S+)/);
          const pMatch = classes.match(/p[xylr]?-(\S+)/);
          patterns.push({
            type: 'container',
            maxWidth: maxWMatch ? maxWMatch[1] : 'auto',
            padding: pMatch ? pMatch[1] : '4',
          });
        }
      }
    }
  }

  return patterns.slice(0, 10);
}

async function findComponentFiles(dir: string): Promise<string[]> {
  const results: string[] = [];

  async function walk(d: string) {
    try {
      const entries = await readdir(d, { withFileTypes: true });
      for (const entry of entries) {
        if (entry.name.startsWith('.') || entry.name === 'node_modules') continue;
        const full = path.join(d, entry.name);
        if (entry.isDirectory()) {
          await walk(full);
        } else if (/\.(tsx|jsx|js|ts)$/.test(entry.name) && !entry.name.includes('.test.') && !entry.name.includes('.spec.')) {
          results.push(full);
        }
      }
    } catch { /* skip */ }
  }

  await walk(dir);
  return results.slice(0, 100);
}
