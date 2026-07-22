// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Builder — Claude Opus 4.7 builds HTML                                      ║
// ║  Opus thinks AND builds. Maximum quality output.                              ║
// ║                                                                              ║
// ║  Using Opus 4.7 for maximum quality output.                                  ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { callAI, hasAuth } from './deepseek-client';

const BUILDER_MODEL = 'claude-opus-4-7';

export interface DeepSeekCallOpts {
  system?: string;
  temperature?: number;
  maxTokens?: number;
  json?: boolean;
}

export function hasDeepSeekBuilder(): boolean {
  return hasAuth();
}

export async function callDeepSeekBuilder(
  prompt: string,
  opts: DeepSeekCallOpts = {},
): Promise<string> {
  return callAI(prompt, {
    model: BUILDER_MODEL,
    system: opts.system,
    temperature: opts.temperature,
    maxTokens: opts.maxTokens,
  });
}

export async function callDeepSeekBuilderJSON<T = unknown>(
  prompt: string,
  opts: DeepSeekCallOpts = {},
): Promise<T | null> {
  let raw: string;
  try {
    const jsonPrompt = `${prompt}\n\nRespond with ONLY valid JSON. No markdown fences.`;
    raw = await callDeepSeekBuilder(jsonPrompt, opts);
  } catch (e) {
    console.warn('[Builder] API call failed:', e instanceof Error ? e.message : e);
    return null;
  }

  let jsonStr = raw.trim();
  const jsonMatch = jsonStr.match(/```(?:json)?\s*([\s\S]*?)```/);
  if (jsonMatch) jsonStr = jsonMatch[1].trim();
  const braceStart = jsonStr.indexOf('{');
  if (braceStart > 0) jsonStr = jsonStr.slice(braceStart);

  let parsed: T | null = null;
  try {
    parsed = JSON.parse(jsonStr) as T;
  } catch (e) {
    console.warn('[Builder] JSON parse failed:', e instanceof Error ? e.message : e, 'raw:', raw.slice(0, 200));
    return null;
  }
  return parsed;
}
