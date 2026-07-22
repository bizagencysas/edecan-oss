// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  File Profiler — Framework detection, route mapping, dependency analysis      ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { readFile, readdir, stat } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';

export interface RepoProfile {
  framework: 'nextjs' | 'vite' | 'expo' | 'react-native-cli' | 'astro' | 'nuxt' | 'remix' | 'unknown';
  packageManager: 'pnpm' | 'npm' | 'yarn' | null;
  hasTypescript: boolean;
  hasTailwind: boolean;
  hasShadcn: boolean;
  hasRadix: boolean;
  hasFramerMotion: boolean;
  hasGsap: boolean;
  hasThreeJS: boolean;
  hasStorybook: boolean;
  appDir: string | null;
  componentsDir: string | null;
  stylesDir: string | null;
  routes: string[];
  entryFiles: string[];
  totalFiles: number;
  totalFrontendFiles: number;
  dependencies: Record<string, string>;
}

const FRONTEND_EXTS = new Set(['.tsx', '.ts', '.jsx', '.js', '.css', '.scss', '.less', '.astro', '.vue', '.svelte']);

export async function profileRepo(cloneDir: string): Promise<RepoProfile> {
  const framework = detectFramework(cloneDir);
  const packageManager = detectPackageManagerSync(cloneDir);
  const deps = await loadDependencies(cloneDir);
  const routes = await detectRoutes(cloneDir, framework);
  const { totalFiles, totalFrontendFiles } = await countFiles(cloneDir);

  return {
    framework,
    packageManager,
    hasTypescript: existsSync(path.join(cloneDir, 'tsconfig.json')),
    hasTailwind: checkTailwind(cloneDir, deps),
    hasShadcn: checkDep(deps, 'shadcn') || checkDep(deps, '@shadcn/ui') || existsSync(path.join(cloneDir, 'components.json')),
    hasRadix: checkDep(deps, '@radix-ui'),
    hasFramerMotion: checkDep(deps, 'framer-motion') || checkDep(deps, 'motion'),
    hasGsap: checkDep(deps, 'gsap'),
    hasThreeJS: checkDep(deps, 'three') || checkDep(deps, '@react-three/fiber'),
    hasStorybook: checkDep(deps, '@storybook') || existsSync(path.join(cloneDir, '.storybook')),
    appDir: findAppDir(cloneDir, framework),
    componentsDir: findComponentsDir(cloneDir, framework),
    stylesDir: findStylesDir(cloneDir),
    routes,
    entryFiles: findEntryFiles(cloneDir, framework),
    totalFiles,
    totalFrontendFiles,
    dependencies: deps,
  };
}

function detectFramework(cloneDir: string): RepoProfile['framework'] {
  if (existsSync(path.join(cloneDir, 'next.config.js')) ||
      existsSync(path.join(cloneDir, 'next.config.ts')) ||
      existsSync(path.join(cloneDir, 'next.config.mjs'))) return 'nextjs';
  if (existsSync(path.join(cloneDir, 'vite.config.js')) ||
      existsSync(path.join(cloneDir, 'vite.config.ts'))) return 'vite';
  if (existsSync(path.join(cloneDir, 'app.json')) ||
      existsSync(path.join(cloneDir, '.expo'))) return 'expo';
  if (existsSync(path.join(cloneDir, 'astro.config.js')) ||
      existsSync(path.join(cloneDir, 'astro.config.mjs'))) return 'astro';
  if (existsSync(path.join(cloneDir, 'nuxt.config.js')) ||
      existsSync(path.join(cloneDir, 'nuxt.config.ts'))) return 'nuxt';
  if (existsSync(path.join(cloneDir, 'remix.config.js')) ||
      existsSync(path.join(cloneDir, 'remix.config.ts'))) return 'remix';
  if (existsSync(path.join(cloneDir, 'index.js')) ||
      existsSync(path.join(cloneDir, 'App.tsx'))) return 'react-native-cli';
  return 'unknown';
}

function detectPackageManagerSync(cloneDir: string): RepoProfile['packageManager'] {
  if (existsSync(path.join(cloneDir, 'pnpm-lock.yaml'))) return 'pnpm';
  if (existsSync(path.join(cloneDir, 'yarn.lock'))) return 'yarn';
  if (existsSync(path.join(cloneDir, 'package-lock.json'))) return 'npm';
  if (existsSync(path.join(cloneDir, 'package.json'))) return 'npm';
  return null;
}

async function loadDependencies(cloneDir: string): Promise<Record<string, string>> {
  try {
    const pkgPath = path.join(cloneDir, 'package.json');
    if (!existsSync(pkgPath)) return {};
    const pkg = JSON.parse(await readFile(pkgPath, 'utf-8'));
    return { ...(pkg.dependencies || {}), ...(pkg.devDependencies || {}) };
  } catch {
    return {};
  }
}

function checkTailwind(cloneDir: string, deps: Record<string, string>): boolean {
  if (checkDep(deps, 'tailwindcss')) return true;
  return existsSync(path.join(cloneDir, 'tailwind.config.js')) ||
         existsSync(path.join(cloneDir, 'tailwind.config.ts')) ||
         existsSync(path.join(cloneDir, 'tailwind.config.mjs'));
}

function checkDep(deps: Record<string, string>, name: string): boolean {
  for (const key of Object.keys(deps)) {
    if (key === name || key.startsWith(`${name}/`) || key.startsWith(`${name}-`)) return true;
  }
  return false;
}

function findAppDir(cloneDir: string, framework: RepoProfile['framework']): string | null {
  const candidates = [
    path.join('src', 'app'),
    'app',
    path.join('src', 'pages'),
    'pages',
  ];
  for (const c of candidates) {
    const full = path.join(cloneDir, c);
    if (existsSync(full)) return c;
  }
  return null;
}

function findComponentsDir(cloneDir: string, _framework: RepoProfile['framework']): string | null {
  const candidates = [
    path.join('src', 'components'),
    'components',
    path.join('src', 'ui'),
    'ui',
    path.join('src', 'lib', 'components'),
  ];
  for (const c of candidates) {
    if (existsSync(path.join(cloneDir, c))) return c;
  }
  return null;
}

function findStylesDir(cloneDir: string): string | null {
  const candidates = [
    path.join('src', 'styles'),
    'styles',
    path.join('src', 'app', 'styles'),
  ];
  for (const c of candidates) {
    if (existsSync(path.join(cloneDir, c))) return c;
  }
  return null;
}

async function detectRoutes(cloneDir: string, framework: RepoProfile['framework']): Promise<string[]> {
  const routes: string[] = [];

  if (framework === 'nextjs') {
    for (const base of ['src/app', 'app']) {
      const dir = path.join(cloneDir, base);
      if (existsSync(dir)) {
        const found = await findFiles(dir, /page\.(tsx|jsx|js|ts)$/, 1);
        routes.push(...found.map(f => {
          const rel = path.relative(dir, f.parentPath || f.dir);
          return rel === '' ? '/' : `/${rel}`;
        }));
      }
    }
  }

  if (framework === 'vite') {
    const dir = path.join(cloneDir, 'src', 'routes');
    if (existsSync(dir)) {
      const found = await findFiles(dir, /\.(tsx|jsx)$/, 1);
      routes.push(...found.map(f => `/${path.basename(f.parentPath || f.dir)}`));
    }
  }

  return [...new Set(routes)].slice(0, 20);
}

async function findFiles(
  dir: string,
  pattern: RegExp,
  depth: number,
): Promise<{ parentPath: string; dir: string }[]> {
  const results: { parentPath: string; dir: string }[] = [];
  if (depth < 0) return results;

  try {
    const entries = await readdir(dir, { withFileTypes: true });
    for (const entry of entries) {
      if (entry.name.startsWith('.') || entry.name === 'node_modules') continue;
      const full = path.join(dir, entry.name);
      if (entry.isDirectory() && depth >= 0) {
        const subResults = await findFiles(full, pattern, depth - 1);
        results.push(...subResults);
      } else if (entry.isFile() && pattern.test(entry.name)) {
        results.push({ parentPath: dir, dir: full });
      }
    }
  } catch {
    // Permission or missing dir
  }

  return results;
}

async function countFiles(cloneDir: string): Promise<{ totalFiles: number; totalFrontendFiles: number }> {
  let totalFiles = 0;
  let totalFrontendFiles = 0;

  async function walk(dir: string) {
    try {
      const entries = await readdir(dir, { withFileTypes: true });
      for (const entry of entries) {
        if (entry.name.startsWith('.') || entry.name === 'node_modules') continue;
        const full = path.join(dir, entry.name);
        try {
          const s = await stat(full);
          if (s.isDirectory()) {
            await walk(full);
          } else if (s.isFile()) {
            totalFiles++;
            if (FRONTEND_EXTS.has(path.extname(entry.name))) totalFrontendFiles++;
          }
        } catch { /* skip inaccessible */ }
      }
    } catch { /* skip inaccessible */ }
  }

  await walk(cloneDir);
  return { totalFiles, totalFrontendFiles };
}

function findEntryFiles(cloneDir: string, framework: RepoProfile['framework']): string[] {
  const entries: string[] = [];
  const candidates = [
    path.join('src', 'app', 'layout.tsx'),
    'app/layout.tsx',
    'src/main.tsx',
    'src/main.ts',
    'src/index.tsx',
    'App.tsx',
    'src/App.tsx',
  ];
  for (const c of candidates) {
    if (existsSync(path.join(cloneDir, c))) entries.push(c);
  }
  return entries;
}
