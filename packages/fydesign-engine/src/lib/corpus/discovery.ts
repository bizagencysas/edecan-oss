// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Corpus Discovery — GitHub Search API repo discovery + quality scoring      ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

const GITHUB_API = 'https://api.github.com';

interface GitHubRepoItem {
  id: number;
  full_name: string;
  html_url: string;
  description: string | null;
  default_branch: string;
  stargazers_count: number;
  forks_count: number;
  topics: string[];
  language: string | null;
  license: { spdx_id: string } | null;
  pushed_at: string;
  created_at: string;
  size: number;
  archived: boolean;
}

export interface DiscoveredRepo {
  id: string;
  owner: string;
  repo: string;
  fullName: string;
  htmlUrl: string;
  description: string;
  defaultBranch: string;
  stars: number;
  forks: number;
  topics: string[];
  language: string;
  license: string;
  pushedAt: string;
  createdAt: string;
  size: number;
  archived: boolean;
  qualityScore?: number;
}

const TOPIC_QUERIES = [
  'topic:nextjs stars:>500 pushed:>2025-01-01',
  'topic:tailwindcss stars:>500 pushed:>2025-01-01',
  'topic:shadcn-ui stars:>200 pushed:>2025-01-01',
  'topic:framer-motion stars:>200 pushed:>2025-01-01',
  'topic:dashboard stars:>300 language:TypeScript',
  'topic:fintech stars:>100 language:TypeScript',
  'topic:saas stars:>300 language:TypeScript',
  'topic:portfolio stars:>500 language:TypeScript',
  'topic:landing-page stars:>300 language:TypeScript',
  'topic:ui-components stars:>500 language:TypeScript',
];

const PACKAGE_SIGNAL_QUERIES = [
  'next react tailwindcss stars:>300 language:TypeScript',
  'framer-motion gsap stars:>200 language:TypeScript',
  'shadcn radix-ui stars:>200 language:TypeScript',
  '@react-three/fiber three stars:>100 language:TypeScript',
  'motion lucide-react stars:>200 language:TypeScript',
];

const FRONTEND_SIGNAL_PACKAGES = [
  'next', 'react', 'vite', 'tailwindcss', 'framer-motion', 'gsap',
  'three', '@react-three/fiber', 'shadcn', 'radix-ui', 'motion', 'lucide-react',
];

const DESIGN_STACK_PACKAGES = [
  'framer-motion', 'gsap', 'three', '@react-three/fiber', 'shadcn',
  'radix-ui', 'motion', 'tailwindcss', 'styled-components', 'vanilla-extract',
];

function parseRepo(fullName: string): { owner: string; repo: string } {
  const [owner, repo] = fullName.split('/');
  return { owner: owner || '', repo: repo || '' };
}

async function githubFetch(url: string, token: string): Promise<unknown> {
  const res = await fetch(url, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'User-Agent': 'fydesign-corpus',
    },
  });
  if (!res.ok) throw new Error(`GitHub API ${res.status}: ${await res.text().catch(() => 'unknown')}`);
  return res.json();
}

export async function discoverRepos(
  token: string,
  queries?: string[],
): Promise<DiscoveredRepo[]> {
  const allQueries = queries && queries.length > 0 ? queries : [...TOPIC_QUERIES, ...PACKAGE_SIGNAL_QUERIES];
  const seen = new Set<string>();
  const results: DiscoveredRepo[] = [];

  for (const q of allQueries) {
    try {
      const url = `${GITHUB_API}/search/repositories?q=${encodeURIComponent(q)}&sort=stars&order=desc&per_page=15`;
      const data = await githubFetch(url, token) as { items: GitHubRepoItem[] };

      for (const item of data.items || []) {
        if (seen.has(item.full_name)) continue;
        seen.add(item.full_name);

        const { owner, repo } = parseRepo(item.full_name);
        results.push({
          id: item.full_name,
          owner,
          repo,
          fullName: item.full_name,
          htmlUrl: item.html_url,
          description: item.description || '',
          defaultBranch: item.default_branch,
          stars: item.stargazers_count,
          forks: item.forks_count,
          topics: item.topics || [],
          language: item.language || '',
          license: item.license?.spdx_id || '',
          pushedAt: item.pushed_at || '',
          createdAt: item.created_at || '',
          size: item.size || 0,
          archived: item.archived || false,
        });
      }

      // Respect rate limits
      await new Promise((r) => setTimeout(r, 1200));
    } catch (error) {
      console.warn(`[Discovery] query failed: ${q}`, error instanceof Error ? error.message : error);
    }
  }

  return results;
}

export function scoreRepoQuality(repo: DiscoveredRepo): number {
  const starsWeight = Math.min(repo.stars / 1000, 5);

  let recencyWeight = -2;
  if (repo.pushedAt) {
    const pushedDate = new Date(repo.pushedAt);
    const daysAgo = (Date.now() - pushedDate.getTime()) / (1000 * 60 * 60 * 24);
    if (daysAgo <= 90) recencyWeight = 3;
    else if (daysAgo <= 365) recencyWeight = 1;
  }

  const haystack = `${repo.description} ${repo.topics.join(' ')} ${repo.language || ''}`.toLowerCase();
  const frontendSignalWeight = Math.min(
    5,
    FRONTEND_SIGNAL_PACKAGES.filter((pkg) => haystack.includes(pkg.toLowerCase())).length,
  );

  const topicRelevanceWeight = Math.min(
    5,
    repo.topics.filter((t) =>
      ['nextjs', 'react', 'tailwindcss', 'ui', 'dashboard', 'design-system', 'components', 'typescript'].includes(t.toLowerCase()),
    ).length,
  );

  const designStackWeight = Math.min(
    5,
    DESIGN_STACK_PACKAGES.filter((pkg) => haystack.includes(pkg.toLowerCase())).length * 1.5,
  );

  const readmeVisualSignalWeight = repo.description &&
    /\b(ui|ux|design|component|tailwind|animation|dashboard|landing|premium|beautiful|modern|glassmorphism)\b/i.test(repo.description)
    ? 2
    : 0;

  const stalePenalty = !repo.pushedAt ? 5 : 0;

  const hugeRepoPenalty = repo.size > 500000 ? 4 : repo.size > 200000 ? 2 : 0;

  const backendOnlyPenalty =
    (!haystack.includes('react') && !haystack.includes('next') && !haystack.includes('vite') && !haystack.includes('vue') && !haystack.includes('svelte'))
    && (haystack.includes('api') || haystack.includes('backend') || haystack.includes('server'))
      ? 8
      : 0;

  return (
    starsWeight +
    recencyWeight +
    frontendSignalWeight +
    topicRelevanceWeight +
    designStackWeight +
    readmeVisualSignalWeight -
    stalePenalty -
    hugeRepoPenalty -
    backendOnlyPenalty
  );
}

export function shouldIngest(repo: DiscoveredRepo): boolean {
  return (repo.qualityScore ?? scoreRepoQuality(repo)) >= 6;
}

export function rejectRepo(repo: DiscoveredRepo): string | null {
  if (repo.archived) return 'repo is archived';
  if (!repo.pushedAt) return 'no push date available';

  const pushedDate = new Date(repo.pushedAt);
  const daysAgo = (Date.now() - pushedDate.getTime()) / (1000 * 60 * 60 * 24);
  if (daysAgo > 730) return 'last push > 2 years ago';

  if (repo.size > 1000000) return 'repo too large (>1GB)';

  if (repo.stars < 5 && repo.forks < 3) return 'suspiciously low stars/forks';

  const haystack = `${repo.description} ${repo.topics.join(' ')} ${repo.language || ''}`.toLowerCase();
  const hasFrontend = FRONTEND_SIGNAL_PACKAGES.some((pkg) => haystack.includes(pkg.toLowerCase()));
  if (!hasFrontend && !['typescript', 'javascript'].includes((repo.language || '').toLowerCase())) {
    return 'no frontend signals detected';
  }

  if (repo.license && /\b(cc-by-nc|cc-by-nc-nd|no-derivatives|restrictive)\b/i.test(repo.license)) {
    return 'license forbids analysis or derivatives';
  }

  return null;
}
