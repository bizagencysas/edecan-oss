/** Portable, tenant-scoped brand store for the OSS Studio engine.
 *
 * Edecán supplies `FYDESIGN_STORE_PATH` per tenant. No database URL, SQL
 * schema, cloud bucket or owner data is embedded in this package.
 */
import { mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import path from 'node:path';

export interface BrandConfigRow {
  id: string;
  company_name: string;
  company_blurb: string;
  repo_url: string;
  logo_url: string | null;
  brand_notes: string | null;
  uploaded_assets: unknown;
  analysis_json: string;
  status: 'configuring' | 'ready' | 'error';
  user_id: string | null;
  created_at: string;
  updated_at: string;
}

interface StoreDocument {
  schemaVersion: 1;
  brands: BrandConfigRow[];
  designMemory: DesignMemoryRow[];
  designCorpus: DesignCorpusPatternRow[];
  corpusRepos: CorpusRepoRow[];
  corpusJobs: CorpusJobRow[];
  corpusPatterns: CorpusPatternRow[];
  corpusScreenshots: CorpusScreenshotRow[];
  creativeDirectives: CreativeDirectiveRow[];
}

function storePath(): string {
  const configured = (process.env.FYDESIGN_STORE_PATH || '').trim();
  if (!configured) {
    throw new Error(
      'FYDESIGN_STORE_PATH no está configurado. Edecán debe asignar un almacén privado al Studio.',
    );
  }
  return path.resolve(configured);
}

async function loadStore(): Promise<StoreDocument> {
  try {
    const parsed = JSON.parse(await readFile(storePath(), 'utf8')) as Partial<StoreDocument>;
    return {
      schemaVersion: 1,
      brands: Array.isArray(parsed.brands) ? parsed.brands : [],
      designMemory: Array.isArray(parsed.designMemory) ? parsed.designMemory : [],
      designCorpus: Array.isArray(parsed.designCorpus) ? parsed.designCorpus : [],
      corpusRepos: Array.isArray(parsed.corpusRepos) ? parsed.corpusRepos : [],
      corpusJobs: Array.isArray(parsed.corpusJobs) ? parsed.corpusJobs : [],
      corpusPatterns: Array.isArray(parsed.corpusPatterns) ? parsed.corpusPatterns : [],
      corpusScreenshots: Array.isArray(parsed.corpusScreenshots) ? parsed.corpusScreenshots : [],
      creativeDirectives: Array.isArray(parsed.creativeDirectives) ? parsed.creativeDirectives : [],
    };
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
      return {
        schemaVersion: 1,
        brands: [],
        designMemory: [],
        designCorpus: [],
        corpusRepos: [],
        corpusJobs: [],
        corpusPatterns: [],
        corpusScreenshots: [],
        creativeDirectives: [],
      };
    }
    throw error;
  }
}

async function saveStore(document: StoreDocument): Promise<void> {
  const target = storePath();
  await mkdir(path.dirname(target), { recursive: true });
  const temporary = `${target}.${process.pid}.${Date.now()}.tmp`;
  await writeFile(temporary, `${JSON.stringify(document, null, 2)}\n`, {
    encoding: 'utf8',
    mode: 0o600,
  });
  await rename(temporary, target);
}

export async function saveBrandConfig(config: {
  id: string;
  companyName: string;
  companyBlurb: string;
  repoUrl: string;
  logoUrl: string | null;
  brandNotes: string | null;
  uploadedAssets?: unknown;
  analysisJson: string;
  status: 'configuring' | 'ready' | 'error';
  userId?: string | null;
}): Promise<void> {
  const document = await loadStore();
  const previous = document.brands.find((brand) => brand.id === config.id);
  const now = new Date().toISOString();
  const row: BrandConfigRow = {
    id: config.id,
    company_name: config.companyName,
    company_blurb: config.companyBlurb,
    repo_url: config.repoUrl,
    logo_url: config.logoUrl,
    brand_notes: config.brandNotes,
    uploaded_assets: config.uploadedAssets ?? null,
    analysis_json: config.analysisJson,
    status: config.status,
    user_id: config.userId || null,
    created_at: previous?.created_at || now,
    updated_at: now,
  };
  document.brands = [row, ...document.brands.filter((brand) => brand.id !== row.id)].slice(0, 50);
  await saveStore(document);
}

export async function loadBrandConfig(id: string): Promise<BrandConfigRow | null> {
  return (await loadStore()).brands.find((brand) => brand.id === id) || null;
}

export async function loadLatestBrandConfig(userId?: string): Promise<BrandConfigRow | null> {
  const brands = (await loadStore()).brands.filter(
    (brand) => brand.status === 'ready' && (!userId || brand.user_id === userId),
  );
  return brands.sort((left, right) => right.updated_at.localeCompare(left.updated_at))[0] || null;
}

export async function loadAllBrandConfigs(userId?: string): Promise<BrandConfigRow[]> {
  return (await loadStore()).brands
    .filter((brand) => !userId || brand.user_id === userId)
    .sort((left, right) => right.updated_at.localeCompare(left.updated_at))
    .slice(0, 50);
}

/** Compatibility adapter for legacy `/api/assets/*` references.
 *
 * The OSS engine never receives the private SQL/GCS registry, so these
 * lookups return no rows. New brand assets use data URLs, file paths or URLs.
 */
export function getDb(): any {
  const legacyAssetQuery = async function legacyAssetQuery(
    _strings: TemplateStringsArray,
    ..._values: unknown[]
  ): Promise<unknown[]> {
    return [];
  };
  legacyAssetQuery.unsafe = async () => [];
  return legacyAssetQuery;
}

export type DesignCorpusPatternRow = Record<string, any>;
export interface DesignMemoryRow {
  id: string;
  category: string;
  insight: string;
  why: string;
  signals: string[];
  confidence: number;
  evidence_count: number;
  source_repos: string[];
  quality_score: number;
  embedding: number[] | null;
  created_at: string;
  updated_at: string;
}
export type CorpusRepoRow = Record<string, any>;
export interface CorpusJobRow extends Record<string, any> {
  id: string;
  repo_full_name: string;
  type: string;
  status: string;
  priority: number;
  attempts: number;
  max_attempts: number;
  error: string | null;
  payload: Record<string, unknown>;
  locked_at: string | null;
  locked_by: string | null;
  created_at: string;
  updated_at: string;
}
export type CorpusPatternRow = Record<string, any>;
export type CorpusScreenshotRow = Record<string, any>;
export type CreativeDirectiveRow = Record<string, any>;

let mutationQueue: Promise<unknown> = Promise.resolve();

function mutateStore<T>(mutator: (document: StoreDocument) => T | Promise<T>): Promise<T> {
  const operation = mutationQueue.then(async () => {
    const document = await loadStore();
    const result = await mutator(document);
    await saveStore(document);
    return result;
  });
  mutationQueue = operation.catch(() => undefined);
  return operation;
}

function upsert<T extends Record<string, any>>(rows: T[], row: T, key = 'id'): void {
  const index = rows.findIndex((item) => item[key] === row[key]);
  if (index >= 0) rows[index] = { ...rows[index], ...row };
  else rows.push(row);
}

function cosine(left: number[] | null, right: number[]): number {
  if (!left || left.length !== right.length || !left.length) return 0;
  let dot = 0;
  let l2 = 0;
  let r2 = 0;
  for (let index = 0; index < left.length; index++) {
    dot += left[index] * right[index];
    l2 += left[index] ** 2;
    r2 += right[index] ** 2;
  }
  return l2 && r2 ? dot / Math.sqrt(l2 * r2) : 0;
}

function normalizedTerms(value: string): Set<string> {
  return new Set(
    value
      .toLocaleLowerCase()
      .normalize('NFKD')
      .replace(/[\u0300-\u036f]/g, '')
      .split(/[^a-z0-9]+/)
      .filter((term) => term.length >= 3),
  );
}

function lexicalScore(row: DesignMemoryRow, query: string): number {
  const needles = normalizedTerms(query);
  if (!needles.size) return row.quality_score;
  const haystack = normalizedTerms(
    [row.category, row.insight, row.why, ...row.signals].join(' '),
  );
  let overlap = 0;
  for (const term of needles) if (haystack.has(term)) overlap++;
  return overlap / needles.size + row.quality_score * 0.1;
}

/** Atomic local design memory used by the OSS runtime. */
export async function saveDesignMemoryInsights(
  insights: Array<Omit<DesignMemoryRow, 'created_at' | 'updated_at'>>,
): Promise<number> {
  return mutateStore((document) => {
    const now = new Date().toISOString();
    let changed = 0;
    for (const insight of insights) {
      const duplicate = document.designMemory.find(
        (item) => item.category === insight.category && item.insight === insight.insight,
      );
      if (duplicate) {
        duplicate.evidence_count = Math.max(duplicate.evidence_count, insight.evidence_count);
        duplicate.quality_score = Math.max(duplicate.quality_score, insight.quality_score);
        duplicate.confidence = Math.max(duplicate.confidence, insight.confidence);
        duplicate.signals = [...new Set([...duplicate.signals, ...insight.signals])].slice(0, 50);
        duplicate.source_repos = [
          ...new Set([...duplicate.source_repos, ...insight.source_repos]),
        ].slice(0, 50);
        duplicate.embedding = insight.embedding || duplicate.embedding;
        duplicate.updated_at = now;
      } else {
        document.designMemory.push({ ...insight, created_at: now, updated_at: now });
      }
      changed++;
    }
    document.designMemory = document.designMemory
      .sort((left, right) => right.quality_score - left.quality_score)
      .slice(0, 2_000);
    return changed;
  });
}

export async function loadDesignMemoryInsights(options: {
  categories?: string[];
  query?: string;
  embedding?: number[] | null;
  limit?: number;
} = {}): Promise<DesignMemoryRow[]> {
  const categories = new Set(options.categories || []);
  const query = options.query || '';
  const limit = Math.max(1, Math.min(options.limit || 10, 100));
  return (await loadStore()).designMemory
    .filter((row) => !categories.size || categories.has(row.category))
    .map((row) => ({
      row,
      score: options.embedding?.length
        ? cosine(row.embedding, options.embedding)
        : lexicalScore(row, query),
    }))
    .sort((left, right) => right.score - left.score)
    .slice(0, limit)
    .map(({ row }) => row);
}

export async function getLocalDesignMemoryStats(): Promise<{
  total: number;
  byCategory: Record<string, number>;
  avgQuality: number;
}> {
  const rows = (await loadStore()).designMemory;
  const byCategory: Record<string, number> = {};
  for (const row of rows) byCategory[row.category] = (byCategory[row.category] || 0) + 1;
  return {
    total: rows.length,
    byCategory,
    avgQuality: rows.length
      ? rows.reduce((sum, row) => sum + row.quality_score, 0) / rows.length
      : 0,
  };
}

export async function saveDesignCorpusPattern(pattern: Record<string, any>): Promise<void> {
  await mutateStore((document) => {
    const now = new Date().toISOString();
    upsert(document.designCorpus, {
      id: pattern.id,
      title: pattern.title,
      applies_to: pattern.appliesTo || [],
      signals: pattern.signals || [],
      rules: pattern.rules || [],
      css_moves: pattern.cssMoves || [],
      avoid: pattern.avoid || [],
      source_repos: pattern.sourceRepos || [],
      weight: pattern.weight ?? 1,
      created_at: now,
      updated_at: now,
    });
  });
}

export async function loadDesignCorpusPatterns(limit = 80): Promise<DesignCorpusPatternRow[]> {
  return (await loadStore()).designCorpus
    .slice()
    .sort((a, b) => Number(b.weight) - Number(a.weight))
    .slice(0, limit);
}

export async function saveCorpusRepo(repo: Record<string, any>): Promise<void> {
  await mutateStore((document) => {
    const now = new Date().toISOString();
    const previous = document.corpusRepos.find((item) => item.full_name === repo.fullName);
    upsert(document.corpusRepos, {
      ...previous,
      id: repo.id,
      owner: repo.owner,
      repo: repo.repo,
      full_name: repo.fullName,
      html_url: repo.htmlUrl,
      description: repo.description || null,
      default_branch: repo.defaultBranch || null,
      stars: repo.stars,
      forks: repo.forks,
      topics: repo.topics || [],
      language: repo.language || null,
      license: repo.license || null,
      discovered_by: repo.discoveredBy,
      quality_score: repo.qualityScore,
      last_seen_at: now,
      last_ingested_at: previous?.last_ingested_at || null,
      status: previous?.status === 'discovered' ? (repo.status || 'discovered') : (previous?.status || repo.status || 'discovered'),
      created_at: previous?.created_at || now,
    });
  });
}

export async function loadCorpusRepo(fullName: string): Promise<CorpusRepoRow | null> {
  return (await loadStore()).corpusRepos.find((item) => item.full_name === fullName) || null;
}

export async function loadTopUnprocessedRepos(limit = 20): Promise<CorpusRepoRow[]> {
  return (await loadStore()).corpusRepos
    .filter((item) => item.status === 'discovered')
    .sort((a, b) => Number(b.quality_score) - Number(a.quality_score))
    .slice(0, limit);
}

export async function updateCorpusRepoStatus(fullName: string, status: string): Promise<void> {
  await mutateStore((document) => {
    const repo = document.corpusRepos.find((item) => item.full_name === fullName);
    if (repo) {
      repo.status = status;
      if (status === 'ingested') repo.last_ingested_at = new Date().toISOString();
    }
  });
}

export async function enqueueJob(job: Record<string, any>): Promise<void> {
  await mutateStore((document) => {
    if (document.corpusJobs.some((item) => item.id === job.id)) return;
    const now = new Date().toISOString();
    document.corpusJobs.push({
      id: job.id,
      repo_full_name: job.repoFullName,
      type: job.type,
      status: 'queued',
      priority: job.priority ?? 50,
      attempts: 0,
      max_attempts: 3,
      error: null,
      payload: job.payload || {},
      locked_at: null,
      locked_by: null,
      created_at: now,
      updated_at: now,
    });
  });
}

export async function claimNextJob(workerId: string): Promise<CorpusJobRow | null> {
  return mutateStore((document) => {
    const job = document.corpusJobs
      .filter((item) => item.status === 'queued')
      .sort((a, b) => Number(b.priority) - Number(a.priority))[0];
    if (!job) return null;
    job.status = 'running';
    job.locked_at = new Date().toISOString();
    job.locked_by = workerId;
    job.attempts = Number(job.attempts || 0) + 1;
    job.updated_at = job.locked_at;
    return { ...job };
  });
}

export async function completeJob(jobId: string): Promise<void> {
  await setJobStatus(jobId, 'completed');
}

export async function failJob(jobId: string, error: string): Promise<void> {
  await setJobStatus(jobId, 'failed', error.slice(0, 2000));
}

async function setJobStatus(id: string, status: string, error: string | null = null): Promise<void> {
  await mutateStore((document) => {
    const job = document.corpusJobs.find((item) => item.id === id);
    if (job) Object.assign(job, { status, error, updated_at: new Date().toISOString() });
  });
}

export async function requeueStuckJobs(timeoutMinutes: number): Promise<number> {
  return mutateStore((document) => {
    const cutoff = Date.now() - timeoutMinutes * 60_000;
    let count = 0;
    for (const job of document.corpusJobs) {
      if (job.status === 'running' && Date.parse(job.locked_at || '') < cutoff) {
        Object.assign(job, { status: 'queued', locked_at: null, locked_by: null });
        count++;
      }
    }
    return count;
  });
}

export async function saveCorpusPatternFull(pattern: Record<string, any>): Promise<void> {
  await mutateStore((document) => {
    upsert(document.corpusPatterns, {
      id: pattern.id,
      repo_full_name: pattern.repoFullName,
      pattern_type: pattern.patternType,
      title: pattern.title,
      summary: pattern.summary,
      tags: pattern.tags || [],
      applies_to: pattern.appliesTo || [],
      signals: pattern.signals || [],
      rules: pattern.rules || [],
      css_moves: pattern.cssMoves || [],
      snippets: pattern.snippets || [],
      avoid: pattern.avoid || [],
      metadata: pattern.metadata || {},
      source_files: pattern.sourceFiles || [],
      confidence: pattern.confidence ?? 0,
      quality_score: pattern.qualityScore ?? 0,
      embedding: pattern.embedding || null,
      created_at: new Date().toISOString(),
    });
  });
}

export async function searchCorpusPatternsByVector(
  queryEmbedding: number[], limit = 20,
): Promise<Array<CorpusPatternRow & { similarity: number }>> {
  return (await loadStore()).corpusPatterns
    .map((item) => ({ ...item, similarity: cosine(item.embedding, queryEmbedding) }))
    .sort((a, b) => b.similarity - a.similarity)
    .slice(0, limit);
}

export async function loadCorpusPatternsByRepo(repoFullName: string): Promise<CorpusPatternRow[]> {
  return (await loadStore()).corpusPatterns.filter((item) => item.repo_full_name === repoFullName);
}

export async function searchCorpusPatternsByKeywords(
  keywords: string[], limit = 20,
): Promise<CorpusPatternRow[]> {
  const needles = keywords.map((item) => item.toLowerCase()).filter(Boolean);
  return (await loadStore()).corpusPatterns
    .filter((item) => {
      const text = JSON.stringify([item.title, item.summary, item.tags, item.applies_to]).toLowerCase();
      return needles.some((needle) => text.includes(needle));
    })
    .sort((a, b) => Number(b.quality_score) - Number(a.quality_score))
    .slice(0, limit);
}

export async function saveCorpusScreenshot(screenshot: Record<string, any>): Promise<void> {
  await mutateStore((document) => {
    upsert(document.corpusScreenshots, {
      id: screenshot.id,
      repo_full_name: screenshot.repoFullName,
      route_or_file: screenshot.routeOrFile,
      storage_url: screenshot.storageUrl,
      thumbnail_url: screenshot.thumbnailUrl || null,
      width: screenshot.width || null,
      height: screenshot.height || null,
      perceptual_hash: screenshot.perceptualHash || null,
      visual_tags: screenshot.visualTags || [],
      quality_score: screenshot.qualityScore ?? 0,
      embedding: screenshot.embedding || null,
      created_at: new Date().toISOString(),
    });
  });
}

export async function searchScreenshotsByVector(
  queryEmbedding: number[], limit = 10,
): Promise<Array<CorpusScreenshotRow & { similarity: number }>> {
  return (await loadStore()).corpusScreenshots
    .map((item) => ({ ...item, similarity: cosine(item.embedding, queryEmbedding) }))
    .sort((a, b) => b.similarity - a.similarity)
    .slice(0, limit);
}

export async function saveCreativeDirective(directive: Record<string, any>): Promise<void> {
  await mutateStore((document) => {
    upsert(document.creativeDirectives, {
      id: directive.id,
      name: directive.name,
      domain: directive.domain,
      directive_text: directive.directiveText,
      anti_patterns: directive.antiPatterns || [],
      scoring_rubric: directive.scoringRubric || {},
      examples_good: directive.examplesGood || [],
      examples_bad: directive.examplesBad || [],
      version: directive.version ?? 1,
      created_at: new Date().toISOString(),
    });
  });
}

export async function loadCreativeDirectivesByDomain(
  domain: string,
): Promise<CreativeDirectiveRow[]> {
  return (await loadStore()).creativeDirectives
    .filter((item) => item.domain === domain)
    .sort((a, b) => Number(b.version) - Number(a.version));
}

export async function loadLatestDirectives(): Promise<CreativeDirectiveRow[]> {
  const latest = new Map<string, CreativeDirectiveRow>();
  for (const item of (await loadStore()).creativeDirectives) {
    const current = latest.get(item.domain);
    if (!current || Number(item.version) > Number(current.version)) latest.set(item.domain, item);
  }
  return [...latest.values()];
}

export async function getCorpusStats(): Promise<{
  totalRepos: number;
  ingestedRepos: number;
  totalPatterns: number;
  totalScreenshots: number;
  avgQualityScore: number;
}> {
  const document = await loadStore();
  const scores = document.corpusPatterns.map((item) => Number(item.quality_score) || 0);
  return {
    totalRepos: document.corpusRepos.length,
    ingestedRepos: document.corpusRepos.filter((item) => item.status === 'ingested').length,
    totalPatterns: document.corpusPatterns.length,
    totalScreenshots: document.corpusScreenshots.length,
    avgQualityScore: scores.length ? scores.reduce((sum, item) => sum + item, 0) / scores.length : 0,
  };
}
