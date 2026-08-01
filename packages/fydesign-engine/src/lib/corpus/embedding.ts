// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Embedding Pipeline — Google Generative AI text-embedding-004                ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

export interface CorpusPatternInput {
  summary: string;
  rules: string[];
  tags: string[];
  metadata: Record<string, unknown>;
}

export const EMBEDDING_DIM = 768;
const ZERO_VECTOR: number[] = new Array(EMBEDDING_DIM).fill(0);

export function isEmbeddingAvailable(): boolean {
  return !!process.env.GEMINI_API_KEY || !!process.env.GOOGLE_GENAI_API_KEY;
}

export async function embedText(text: string): Promise<number[]> {
  if (!text || text.trim().length === 0) return ZERO_VECTOR;

  const apiKey = process.env.GEMINI_API_KEY || process.env.GOOGLE_GENAI_API_KEY;
  if (!apiKey) {
    console.warn('[Embedding] no API key configured, returning zero vector');
    return ZERO_VECTOR;
  }

  try {
    // Use raw fetch to support outputDimensionality (SDK types don't expose it)
    const url = `https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-2:embedContent?key=${apiKey}`;
    const body = JSON.stringify({
      content: { parts: [{ text: text.slice(0, 3000) }] },
      outputDimensionality: EMBEDDING_DIM,
    });
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
    });
    const data = await res.json() as { embedding?: { values?: number[] }; error?: { message: string } };

    if (data.error) {
      console.warn('[Embedding] API error:', data.error.message);
      return ZERO_VECTOR;
    }

    const embedding = data.embedding?.values;
    if (embedding && embedding.length > 0) {
      if (embedding.length !== EMBEDDING_DIM) {
        console.warn(`[Embedding] dimension mismatch: got ${embedding.length}, expected ${EMBEDDING_DIM}`);
      }
      return embedding;
    }

    console.warn('[Embedding] empty embedding returned');
    return ZERO_VECTOR;
  } catch (error) {
    console.warn('[Embedding] API call failed:', error instanceof Error ? error.message : error);
    return ZERO_VECTOR;
  }
}

export async function embedPattern(pattern: CorpusPatternInput): Promise<number[]> {
  const text = buildPatternEmbeddingInput(pattern);
  return embedText(text);
}

export async function embedScreenshot(screenshot: {
  visualTags: string[];
  routeOrFile: string;
  repoFullName: string;
}): Promise<number[]> {
  const text = [
    `Screenshot route: ${screenshot.routeOrFile}`,
    `Repository: ${screenshot.repoFullName}`,
    `Visual tags: ${screenshot.visualTags.join(', ')}`,
  ].join('\n');

  return embedText(text);
}

export function buildPatternEmbeddingInput(pattern: CorpusPatternInput): string {
  const parts = [
    `Pattern: ${pattern.summary}`,
    pattern.rules.length > 0 ? `Rules: ${pattern.rules.join(' | ')}` : '',
    pattern.tags.length > 0 ? `Tags: ${pattern.tags.join(', ')}` : '',
    pattern.metadata.framework ? `Framework: ${pattern.metadata.framework}` : '',
    pattern.metadata.repo ? `Source: ${pattern.metadata.repo}` : '',
  ];
  return parts.filter(Boolean).join('\n');
}
