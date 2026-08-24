// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Agentic campaign strategist through the configured model router               ║
// ║                                                                              ║
// ║  Unlike the plain text planner in fydesign-gen.ts (which calls callAIJSON),   ║
// ║  this spawns the Claude CLI while preserving its normal permission policy.  ║
// ║  so Opus can WEB-SEARCH: research the brand + competitors + CURRENT 2026       ║
// ║  social/marketing trends, decide a cohesive STRATEGY, and emit the campaign    ║
// ║  plan as STRICT JSON. Output shape is a SUPERSET of runCampaign's plan         ║
// ║  ({ sharedStyle, pieces, caption, hashtags } + a `strategy` summary) so it      ║
// ║  drops straight into the existing pieces→image→overlay render loop.            ║
// ║                                                                              ║
// ║  Provider-neutral child process; provider credentials are not inherited.       ║
// ║  endpoint so the explicitly selected local CLI owns its authentication.       ║
// ║                                                                              ║
// ║  RELATIVE imports only (runs under tsx, which does NOT resolve the @/ alias).  ║
// ║  Logs go to STDERR only — stdout from THIS process must stay clean in case     ║
// ║  the caller pipes it.                                                          ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import os from 'node:os';
import { claudeTextOnlyArgs, safeClaudeCliEnv } from './runtime-env';

// ── Result shape ───────────────────────────────────────────────────────────────
// Superset of the plan consumed by runCampaign() in scripts/fydesign-gen.ts:
// it reads `sharedStyle`, `pieces[].{role,imagePrompt,headline,subtext,cta}`,
// `caption`, `hashtags`. The extra `strategy` field surfaces the agent's thinking.
export interface StrategyPiece {
  role: string;
  imagePrompt: string;
  headline: string;
  subtext: string;
  cta: string;
}

export interface StrategyPlan {
  strategy: string;
  sharedStyle: string;
  pieces: StrategyPiece[];
  caption: string;
  hashtags: string[];
}

export interface PlanStrategyOpts {
  brandName: string;
  repo?: string;
  brief: string;
  pieces?: number;
  colors?: string[];
  info?: string;
}

function elog(...a: unknown[]) {
  // STDERR only — keep this process's stdout clean for piping.
  console.error('[strategist]', ...a);
}

// Resolve the explicitly configured local Claude CLI binary.
// Mirrors resolveClaudeCli() in src/lib/ai/deepseek-client.ts.
function resolveClaudeCli(): string | null {
  const explicit = process.env.CLAUDE_CLI_PATH;
  if (explicit && existsSync(explicit)) return explicit;
  for (const c of [
    `${os.homedir()}/.local/bin/claude`,
    '/opt/homebrew/bin/claude',
    '/usr/local/bin/claude',
    '/usr/bin/claude',
  ]) {
    if (existsSync(c)) return c;
  }
  return null;
}

// Pull the outermost {...} object out of arbitrary CLI stdout: strips markdown
// fences and any preamble/thinking the agent may have printed before the JSON.
// No /s regex flag (ES2017 target) — use [\s\S] character classes.
function extractJsonObject(raw: string): string | null {
  let s = (raw || '').trim();
  if (!s) return null;

  // Prefer a fenced ```json … ``` block if present.
  const fence = s.match(/```(?:json)?\s*([\s\S]*?)```/i);
  if (fence && fence[1].includes('{')) s = fence[1].trim();

  // Scan for the outermost balanced {...}, respecting strings/escapes so braces
  // inside quoted values don't confuse the matcher.
  const start = s.indexOf('{');
  if (start < 0) return null;
  let depth = 0;
  let inStr = false;
  let esc = false;
  for (let i = start; i < s.length; i++) {
    const ch = s[i];
    if (inStr) {
      if (esc) esc = false;
      else if (ch === '\\') esc = true;
      else if (ch === '"') inStr = false;
      continue;
    }
    if (ch === '"') { inStr = true; continue; }
    if (ch === '{') depth++;
    else if (ch === '}') {
      depth--;
      if (depth === 0) return s.slice(start, i + 1);
    }
  }
  return null;
}

function coercePlan(obj: unknown, want: number): StrategyPlan | null {
  if (!obj || typeof obj !== 'object') return null;
  const o = obj as Record<string, unknown>;
  const rawPieces = Array.isArray(o.pieces) ? o.pieces : [];
  const pieces: StrategyPiece[] = rawPieces
    .map((p) => {
      const pp = (p || {}) as Record<string, unknown>;
      return {
        role: String(pp.role ?? ''),
        imagePrompt: String(pp.imagePrompt ?? ''),
        headline: String(pp.headline ?? ''),
        subtext: String(pp.subtext ?? ''),
        cta: String(pp.cta ?? ''),
      };
    })
    .filter((p) => p.imagePrompt.trim().length > 0);
  if (!pieces.length) return null;

  const hashtags = Array.isArray(o.hashtags)
    ? o.hashtags.map((h) => String(h)).filter(Boolean)
    : [];

  return {
    strategy: String(o.strategy ?? ''),
    sharedStyle: String(o.sharedStyle ?? ''),
    pieces: pieces.slice(0, want),
    caption: String(o.caption ?? ''),
    hashtags,
  };
}

function buildPrompt(opts: PlanStrategyOpts, n: number): string {
  const brandLine = opts.repo ? `${opts.brandName} (repo: ${opts.repo})` : opts.brandName;
  const colors = opts.colors?.length ? opts.colors.join(', ') : '(infer an on-brand palette)';
  const info = opts.info?.trim() ? opts.info.trim() : '(infer from the brand name + your research)';

  return `You are an elite, agentic brand campaign strategist with LIVE WEB ACCESS. Use your web search tools — actually search the web; do not rely on memory alone.

BRAND: ${brandLine}
BRAND COLORS: ${colors}
BRAND INFO: ${info}
CAMPAIGN BRIEF: ${opts.brief}
PIECES REQUESTED: ${n}

DO THIS, IN ORDER:
1. RESEARCH (web): Look up the brand (use the brand name${opts.repo ? ' and repo' : ''}), what it does, its voice, and its real audience. Identify 2-4 direct competitors and how they position. Then research the CURRENT 2026 social-media and marketing TRENDS that are most relevant to this brief (formats, hooks, visual aesthetics, copy angles that are working right now).
2. STRATEGIZE: Decide a single cohesive campaign — its positioning, the sharp angle/insight, and a narrative arc across the ${n} pieces (e.g. hook → benefit → proof → CTA). Make it specific to THIS brand and informed by what you found, not generic.
3. OUTPUT: Emit the campaign as STRICT JSON and NOTHING ELSE.

The JSON MUST be exactly this shape:
{
  "strategy": "2-3 sentence summary of the positioning, angle, and narrative arc you chose (and the key trend/insight driving it)",
  "sharedStyle": "ONE consistent visual-style sentence applied to EVERY piece (lighting, palette, composition, mood) so the set looks cohesive",
  "pieces": [
    {
      "role": "hook | benefit | proof | cta (or similar)",
      "imagePrompt": "detailed image-generation prompt in ENGLISH. Include clean negative space (a calmer top OR bottom area) reserved for a text overlay. The scene must contain NO readable text/words/letters/logos.",
      "headline": "punchy headline, MAX 6 words, in the SAME LANGUAGE as the brief",
      "subtext": "one short supporting line, in the brief's language",
      "cta": "2-3 word call to action, in the brief's language"
    }
  ],
  "caption": "ready-to-post campaign caption, in the brief's language",
  "hashtags": ["5 to 8 relevant hashtags"]
}

HARD REQUIREMENTS:
- "pieces" MUST contain EXACTLY ${n} items.
- Every imagePrompt: English, vivid, photographic/premium, clean negative space for the overlay, and ZERO readable text in the image.
- headline/subtext/cta: in the brief's language.
- Output ONLY the JSON object. No markdown fences, no preamble, no closing commentary. The VERY FIRST character of your final message must be "{" and the very last must be "}".`;
}

// Spawn the FULL claude CLI agent (web-enabled) and capture its stdout.
// Resolves with raw stdout; rejects on spawn error / nonzero exit / timeout.
function runAgent(prompt: string, timeoutMs: number): Promise<string> {
  const bin = resolveClaudeCli();
  if (!bin) {
    return Promise.reject(new Error('claude CLI not found (set CLAUDE_CLI_PATH or install Claude Code)'));
  }

  // Strip API-key envs so the local CLI owns its configured authentication.
  const env = safeClaudeCliEnv();

  const args = claudeTextOnlyArgs(prompt);
  if (process.env.CLAUDE_CLI_MODEL) args.push('--model', process.env.CLAUDE_CLI_MODEL);

  elog(`spawning configured CLI → ${bin} (model=${process.env.CLAUDE_CLI_MODEL || 'CLI default'}, web-enabled)`);

  return new Promise<string>((resolve, reject) => {
    const child = spawn(bin, args, {
      env,
      cwd: os.tmpdir(),
      stdio: ['ignore', 'pipe', 'pipe'],
    });

    let stdout = '';
    let stderr = '';
    let settled = false;

    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      elog(`timeout after ${Math.round(timeoutMs / 1000)}s — killing agent`);
      try { child.kill('SIGKILL'); } catch { /* ignore */ }
      reject(new Error(`claude agent timed out after ${Math.round(timeoutMs / 1000)}s`));
    }, timeoutMs);

    child.stdout.on('data', (d: Buffer) => {
      stdout += d.toString();
      // Coarse progress heartbeat to stderr (do NOT echo stdout — it may hold the JSON).
      if (stdout.length % 4096 < 64) elog(`…receiving (${stdout.length} bytes)`);
    });
    child.stderr.on('data', (d: Buffer) => {
      stderr += d.toString();
      if (stderr.length < 4096) elog(`agent: ${d.toString().trim().slice(0, 200)}`);
    });

    child.on('error', (err) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(new Error(`claude agent spawn failed: ${err.message}`));
    });

    child.on('close', (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      const out = stdout.trim();
      if (code !== 0 && !out) {
        return reject(new Error(`claude agent exited ${code}: ${stderr.slice(0, 300)}`));
      }
      if (!out) {
        return reject(new Error(`claude agent returned empty stdout: ${stderr.slice(0, 300)}`));
      }
      resolve(out);
    });
  });
}

/**
 * God Mode: research the brand + competitors + current 2026 trends on the web,
 * decide a cohesive campaign strategy, and return a full campaign plan.
 *
 * Runs the configured agentic model with the explicitly enabled tools.
 * Returns null on ANY failure so the caller can fall back to the non-agentic
 * planner (e.g. the inline callAIJSON plan in runCampaign).
 */
export async function planStrategy(opts: PlanStrategyOpts): Promise<StrategyPlan | null> {
  const n = Math.max(1, Math.min(12, opts.pieces || 4));
  const timeoutMs = 360_000; // ~6 min — real web research takes a while.

  try {
    elog(`planning ${n}-piece campaign for "${opts.brandName}" — researching brand, competitors & 2026 trends…`);
    const prompt = buildPrompt(opts, n);
    const raw = await runAgent(prompt, timeoutMs);

    const jsonStr = extractJsonObject(raw);
    if (!jsonStr) {
      elog('no JSON object found in agent output:', raw.slice(0, 200));
      return null;
    }

    let parsed: unknown;
    try {
      parsed = JSON.parse(jsonStr);
    } catch (e) {
      elog('JSON parse failed:', e instanceof Error ? e.message : e, '| head:', jsonStr.slice(0, 200));
      return null;
    }

    const plan = coercePlan(parsed, n);
    if (!plan) {
      elog('parsed JSON missing usable pieces');
      return null;
    }

    elog(`strategy ready: ${plan.pieces.length} piece(s). ${plan.strategy.slice(0, 120)}`);
    return plan;
  } catch (e) {
    elog('planStrategy failed:', e instanceof Error ? e.message : e);
    return null;
  }
}

export default planStrategy;
