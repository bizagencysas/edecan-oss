// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  fydesign — GitHub Repository Analyzer                                     ║
// ║  Connects to GitHub, reads repo structure, downloads key files             ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { Octokit } from 'octokit';
import { AppAnalysis, ScreenInfo, ThemeInfo } from './types';
import { extractScreenInfo } from './screen-extractor';
import { extractThemeInfo } from './style-extractor';

interface RepoFile {
  path: string;
  content: string;
  size: number;
}

interface TreeItem {
  path?: string;
  type?: string;
  size?: number;
}

/**
 * Main analyzer: takes a repo URL or owner/name, returns full AppAnalysis
 */
export async function analyzeRepository(
  repoInput: string,
  token: string,
): Promise<AppAnalysis> {
  const octokit = new Octokit({ auth: token });

  // Parse repo input — supports full URL or owner/repo format
  const { owner, repo } = parseRepoInput(repoInput);

  // 1. Get repo metadata
  let repoData: Awaited<ReturnType<typeof octokit.rest.repos.get>>['data'];
  try {
    const { data } = await octokit.rest.repos.get({ owner, repo });
    repoData = data;
  } catch (err: unknown) {
    const e = err as { status?: number; message?: string; response?: { headers?: Record<string, string> } };
    const isRateLimit =
      (e.status === 403 || e.status === 429) &&
      (e.response?.headers?.['x-ratelimit-remaining'] === '0' ||
        (e.message ?? '').toLowerCase().includes('rate limit'));
    if (isRateLimit) {
      const reset = e.response?.headers?.['x-ratelimit-reset'];
      const seconds = reset ? Math.max(0, Number(reset) - Math.floor(Date.now() / 1000)) : 60;
      throw new Error(`GitHub rate limit exceeded — retry in ${seconds} seconds`);
    }
    throw err;
  }

  if (!repoData.default_branch) throw new Error('Repository has no default branch');
  if (repoData.size === 0) throw new Error('Repository is empty');

  // 2. Get the full file tree
  const { data: treeData } = await octokit.rest.git.getTree({
    owner,
    repo,
    tree_sha: repoData.default_branch,
    recursive: 'true',
  });

  if (treeData.truncated) console.warn('[github-analyzer] Tree truncated; analysis incomplete');
  if (!treeData.tree?.length) throw new Error('Repository has no files');

  const allFiles = (treeData.tree as TreeItem[])
    .filter((f) => f.type === 'blob' && f.path)
    .map((f) => ({ path: f.path!, size: f.size || 0 }));

  // 3. Detect framework
  const framework = detectFramework(allFiles.map((f) => f.path));

  // 4. Find and download key files
  const screenFiles = findScreenFiles(allFiles, framework);
  const themeFiles = findThemeFiles(allFiles);
  const appConfigFiles = findAppConfigFiles(allFiles);

  // 5. Download file contents (batch with concurrency limit)
  const filesToDownload = [
    ...screenFiles.slice(0, 30), // max 30 screens
    ...themeFiles.slice(0, 3),
    ...appConfigFiles.slice(0, 2),
  ];

  const downloadedFiles = await downloadFiles(
    octokit,
    owner,
    repo,
    filesToDownload.map((f) => f.path),
  );

  // 6. Extract theme info
  const themeFileContents = downloadedFiles.filter((f) =>
    themeFiles.some((tf) => tf.path === f.path),
  );
  const theme = extractThemeInfo(themeFileContents);

  // 7. Extract app name
  const appConfigContent = downloadedFiles.find(
    (f) => f.path.endsWith('app.json') || f.path.endsWith('package.json'),
  );
  const appName = extractAppName(appConfigContent?.content, repoData.name);

  // 8. Extract screen info
  const screenContents = downloadedFiles.filter((f) =>
    screenFiles.some((sf) => sf.path === f.path),
  );
  const screens: ScreenInfo[] = screenContents
    .map((file) => extractScreenInfo(file.path, file.content, framework))
    .filter((s): s is ScreenInfo => s !== null);

  return {
    repoName: repoData.name,
    repoFullName: repoData.full_name,
    appName,
    description: repoData.description || '',
    framework,
    screens,
    theme,
  };
}

/* ── Helpers ──────────────────────────────────────────────────────────────── */

function parseRepoInput(input: string): { owner: string; repo: string } {
  // Handle full URL: https://github.com/owner/repo
  const urlMatch = input.match(
    /github\.com[\/:]([^\/\s]+)\/([^\/\s?#]+?)(?:\.git)?(?:[\/?#]|$)/i,
  );
  if (urlMatch) return { owner: urlMatch[1], repo: urlMatch[2] };

  const parts = input.trim().split('/');
  const validPart = /^[a-zA-Z0-9._-]+$/;
  if (parts.length === 2 && validPart.test(parts[0]) && validPart.test(parts[1])) {
    return { owner: parts[0], repo: parts[1] };
  }

  throw new Error(
    `Invalid repo format: "${input}". Use owner/repo or full GitHub URL.`,
  );
}

function detectFramework(
  paths: string[],
): AppAnalysis['framework'] {
  const hasAppJson = paths.some((p) => p.match(/^(apps\/mobile\/)?app\.json$/));
  const hasExpoDir = paths.some((p) => p.includes('.expo/'));
  const hasExpoDeps = paths.some(
    (p) => p.match(/^(apps\/mobile\/)?package\.json$/),
  );

  // Check for Expo (file-based routing)
  if (hasAppJson || hasExpoDir) return 'expo';

  // Check for monorepo with Expo
  if (paths.some((p) => p.startsWith('apps/mobile/'))) return 'expo';

  // Check for Next.js
  if (paths.some((p) => p.includes('next.config'))) return 'nextjs';

  // Check for React Native CLI
  if (paths.some((p) => p.endsWith('index.js') || p.endsWith('App.tsx'))) {
    return 'react-native-cli';
  }

  return 'unknown';
}

interface FileEntry {
  path: string;
  size: number;
}

function findScreenFiles(
  files: FileEntry[],
  framework: AppAnalysis['framework'],
): FileEntry[] {
  const screenPatterns: RegExp[] = [];

  if (framework === 'expo') {
    screenPatterns.push(
      // Expo Router file-based routing
      /^(apps\/mobile\/)?app\/(?!_layout)(?!\+)([^_][^\/]*\.tsx)$/,
      /^(apps\/mobile\/)?app\/\(tabs\)\/(?!_layout)([^_][^\/]*\.tsx)$/,
      /^(apps\/mobile\/)?app\/\(auth\)\/(?!_layout)([^_][^\/]*\.tsx)$/,
      /^(apps\/mobile\/)?app\/[^\/]+\/(?!_layout)([^_][^\/]*\.tsx)$/,
    );
  } else if (framework === 'react-native-cli') {
    screenPatterns.push(
      /src\/screens\/.*\.tsx$/,
      /screens\/.*\.tsx$/,
      /src\/pages\/.*\.tsx$/,
    );
  } else if (framework === 'nextjs') {
    screenPatterns.push(
      /src\/app\/.*\/page\.tsx$/,
      /app\/.*\/page\.tsx$/,
      /pages\/(?!_app|_document).*\.tsx$/,
    );
  }

  return files.filter((f) => {
    if (f.size < 400) return false; // Skip tiny files (redirects)
    if (f.path.includes('__tests__')) return false;
    if (f.path.includes('.test.')) return false;
    if (f.path.includes('node_modules')) return false;
    return screenPatterns.some((p) => p.test(f.path));
  });
}

function findThemeFiles(files: FileEntry[]): FileEntry[] {
  const themePatterns = [
    /theme\.(ts|tsx|js)$/,
    /colors\.(ts|tsx|js)$/,
    /tokens\.(ts|tsx|js)$/,
    /design-system\.(ts|tsx|js)$/,
    /styles\/constants\.(ts|tsx|js)$/,
  ];

  return files.filter((f) =>
    themePatterns.some((p) => p.test(f.path)),
  );
}

function findAppConfigFiles(files: FileEntry[]): FileEntry[] {
  const configPatterns = [
    /^(apps\/mobile\/)?app\.json$/,
    /^(apps\/mobile\/)?package\.json$/,
    /^package\.json$/,
  ];

  return files.filter((f) =>
    configPatterns.some((p) => p.test(f.path)),
  );
}

async function downloadFiles(
  octokit: Octokit,
  owner: string,
  repo: string,
  paths: string[],
): Promise<RepoFile[]> {
  const CONCURRENCY = 5;
  const results: RepoFile[] = [];

  for (let i = 0; i < paths.length; i += CONCURRENCY) {
    const batch = paths.slice(i, i + CONCURRENCY);
    const batchResults = await Promise.all(
      batch.map(async (path) => {
        try {
          const { data } = await octokit.rest.repos.getContent({
            owner,
            repo,
            path,
          });

          if ('content' in data && data.type === 'file') {
            const content = Buffer.from(data.content, 'base64').toString(
              'utf-8',
            );
            return { path, content, size: data.size };
          }
          return null;
        } catch {
          console.warn(`Failed to download: ${path}`);
          return null;
        }
      }),
    );

    results.push(
      ...batchResults.filter((r): r is RepoFile => r !== null),
    );
  }

  return results;
}

function extractAppName(
  configContent: string | undefined,
  fallbackName: string,
): string {
  if (!configContent) return formatRepoName(fallbackName);

  try {
    const config = JSON.parse(configContent);

    // app.json (Expo)
    if (config.expo?.name) return config.expo.name;
    if (config.name) return config.name;

    // package.json
    if (config.displayName) return config.displayName;

    return formatRepoName(fallbackName);
  } catch {
    return formatRepoName(fallbackName);
  }
}

function formatRepoName(name: string): string {
  return name
    .replace(/[-_]/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}
