// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Corpus Enricher — Injects corpus intelligence into the system prompt       ║
// ║  Bridges buildRepoBrainContext and the OD-grade system prompt so that      ║
// ║  every generation call gets real corpus patterns injected at the right      ║
// ║  location in the prompt.                                                    ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { buildRepoBrainContext } from './repo-corpus';
import type { DesignMode } from './prompts';
import type { CreativeMode } from './prompts/fydesign';

export interface EnrichWithCorpusOptions {
  prompt: string;
  mode: string;
  creativeMode?: string;
  limit?: number;
}

/**
 * Takes a system prompt string and enriches it with corpus design patterns.
 *
 * 1. Calls buildRepoBrainContext to retrieve the most relevant patterns
 * 2. Counts patterns and logs enrichment stats
 * 3. Inserts the corpus section into the system prompt before the
 *    Anti-AI-Slop Checklist (after the component library / specialist sections)
 * 4. Returns the enriched system prompt
 *
 * If no corpus patterns are found, a fallback note is inserted instead.
 */
export async function enrichWithCorpus(
  systemPrompt: string,
  options: EnrichWithCorpusOptions,
): Promise<string> {
  const repoBrainContext = await buildRepoBrainContext({
    prompt: options.prompt,
    mode: options.mode as DesignMode,
    creativeMode: options.creativeMode as CreativeMode | undefined,
    limit: options.limit,
  });

  // Count patterns for observability
  const patternCount = countPatternsInContext(repoBrainContext);
  if (patternCount > 0) {
    const names = extractPatternNames(repoBrainContext);
    console.log(
      `[CorpusEnricher] Injected ${patternCount} pattern(s): ${names.join(', ')}`,
    );
  } else {
    console.log(`[CorpusEnricher] No corpus patterns to inject — relying on builtins`);
  }

  // Insert the corpus inspiration BEFORE the Technical Contract section.
  // This places optional design references right before the hard technical rules,
  // so the model reads "here's some inspiration" → "here's the technical contract".
  if (!repoBrainContext.trim()) return systemPrompt;

  const marker = '## Technical Contract';
  const idx = systemPrompt.indexOf(marker);
  if (idx === -1) {
    // Fallback: marker not found, append at end (still functional, just less ideal ordering)
    return systemPrompt + '\n\n' + repoBrainContext;
  }

  return (
    systemPrompt.slice(0, idx) + repoBrainContext + '\n\n' + systemPrompt.slice(idx)
  );
}

/**
 * Count how many individual patterns are present in a formatted corpus context string.
 * Each pattern starts with "### " (markdown H3 heading).
 */
function countPatternsInContext(context: string): number {
  const matches = context.match(/^### /gm);
  return matches ? matches.length : 0;
}

/**
 * Extract pattern names (the text after "### id — ") from a formatted context string.
 */
function extractPatternNames(context: string): string[] {
  const names: string[] = [];
  const lines = context.split('\n');
  for (const line of lines) {
    if (line.startsWith('### ')) {
      // Format: "### id — Title" — extract the title portion
      const match = line.match(/^### [^-]+ — (.+)$/);
      if (match) {
        names.push(match[1].trim());
      } else {
        // Fallback: take everything after "### "
        names.push(line.replace(/^### /, '').trim());
      }
    }
  }
  return names;
}
