/** Executable Layer-B pipeline for local Edecán projects.
 *
 * This module is deliberately the single runtime composition root. It makes
 * corpus retrieval, persistent design memory, artistic direction, watchdogs,
 * HTML validation/self-healing and the visual critic part of creation instead
 * of leaving those modules as an unreachable library inventory.
 */
import { callAI, type InlineImage } from '@/lib/ai/deepseek-client';
import { critiqueDesign, refineDesign, type VisualCritique } from '@/lib/ai/visual-critic';
import { withWatchdog } from '@/lib/ai/gemini-watchdog';
import { retrieveDesignMemory, type DesignInsight } from '@/lib/corpus/design-memory';
import { retrievePatterns } from '@/lib/corpus/retrieval';
import { generateMockupHTML } from '@/lib/mockup-html-generator';
import { artifactSafeHtml } from '@/lib/project-security';
import { recreateAllScreens } from '@/lib/screen-recreator';
import type { Language, LayoutType, ThemeInfo } from '@/lib/types';
import { extractCode, validateHTML } from './code-extractor';
import { enrichWithCorpus } from './corpus-enricher';
import {
  formatOpusGuidanceForPrompt,
  getOpusArtisticGuidance,
  type OpusArtisticGuidance,
} from './opus-director';
import { selfHeal } from './self-healer';
import type { DesignMode } from './prompts';

export type ProjectQuality = 'fast' | 'balanced' | 'max';

export interface RuntimeScreenBrief {
  name: string;
  route?: string;
  layout?: LayoutType;
  texts?: string[];
  components?: string[];
  icons?: string[];
}

export interface RuntimePipelineInput {
  prompt: string;
  userMessage: string;
  systemPrompt: string;
  mode: DesignMode;
  width: number;
  height: number;
  brandName?: string;
  brandTokens?: string;
  currentHtml?: string;
  quality?: ProjectQuality;
  screenBriefs?: RuntimeScreenBrief[];
  languages?: Language[];
  theme?: Partial<ThemeInfo>;
  images?: InlineImage[];
}

export interface RuntimePipelineTrace {
  corpusEnriched: boolean;
  retrievedPatterns: number;
  designMemoryInsights: number;
  artisticDirector: string;
  watchdog: boolean;
  deterministicValidation: boolean;
  healed: boolean;
  healAttempts: number;
  screenRecreation: boolean;
  mockupScreens: number;
  critic: {
    attempted: boolean;
    score: number | null;
    refined: boolean;
    issues: number;
  };
}

export interface RuntimePipelineResult {
  html: string;
  trace: RuntimePipelineTrace;
}

function memoryBlock(insights: DesignInsight[]): string {
  if (!insights.length) return '';
  return [
    '## LOCAL DESIGN MEMORY',
    'Apply the relevant lessons below; they are guidance, not user facts.',
    ...insights.map((item) => `- [${item.category}] ${item.insight} Why: ${item.why}`),
  ].join('\n');
}

function patternBlock(patterns: Awaited<ReturnType<typeof retrievePatterns>>): string {
  if (!patterns.length) return '';
  return [
    '## RETRIEVED DESIGN PATTERNS',
    ...patterns.map((pattern) => [
      `### ${pattern.title}`,
      ...pattern.rules.slice(0, 4).map((rule) => `- ${rule}`),
      ...pattern.avoid.slice(0, 2).map((rule) => `- Avoid: ${rule}`),
    ].join('\n')),
  ].join('\n\n');
}

function safeText(value: string, limit = 240): string {
  return value
    .slice(0, limit)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function safeColor(value: string | undefined, fallback: string): string {
  return value && /^#[0-9a-f]{3,8}$/i.test(value) ? value : fallback;
}

async function recreateMockup(input: RuntimePipelineInput): Promise<{
  html: string;
  screens: number;
} | null> {
  const briefs = input.screenBriefs?.slice(0, 8) || [];
  if (input.mode !== 'mockup' || !briefs.length) return null;
  const brandName = safeText(input.brandName || 'Aplicación');
  const theme: ThemeInfo = {
    primaryColor: safeColor(input.theme?.primaryColor, '#4F46E5'),
    secondaryColor: safeColor(input.theme?.secondaryColor, '#0F172A'),
    backgroundColor: safeColor(input.theme?.backgroundColor, '#F8FAFC'),
    darkBackgroundColor: safeColor(input.theme?.darkBackgroundColor, '#0F172A'),
    textColor: safeColor(input.theme?.textColor, '#111827'),
    darkTextColor: safeColor(input.theme?.darkTextColor, '#F8FAFC'),
    accentColors: input.theme?.accentColors?.slice(0, 4)
      .map((color) => safeColor(color, '#7C3AED')) || ['#7C3AED'],
    successColor: safeColor(input.theme?.successColor, '#16A34A'),
    dangerColor: safeColor(input.theme?.dangerColor, '#DC2626'),
    warningColor: safeColor(input.theme?.warningColor, '#D97706'),
    brandName,
    hasDarkMode: Boolean(input.theme?.hasDarkMode),
    borderRadius: Math.min(32, Math.max(0, Number(input.theme?.borderRadius) || 16)),
  };
  const screens = briefs.map((brief, index) => ({
    id: `screen-${index + 1}`,
    fileName: `screen-${index + 1}.tsx`,
    filePath: `screen-${index + 1}.tsx`,
    screenName: safeText(brief.name || `Pantalla ${index + 1}`, 100),
    route: safeText(brief.route || `/screen-${index + 1}`, 160),
    texts: (brief.texts || []).slice(0, 16).map((text) => safeText(String(text))),
    components: (brief.components || []).slice(0, 16).map((item) => safeText(String(item), 100)),
    icons: (brief.icons || []).slice(0, 12).map((item) => safeText(String(item), 40)),
    estimatedLayout: brief.layout || 'generic',
    complexity: 'medium' as const,
  }));
  const recreated = await withWatchdog(
    () => recreateAllScreens(screens, theme, brandName),
    'screen recreation',
  );
  if (!recreated?.length) return null;
  const requestedLanguages = input.languages?.filter((language) =>
    ['en', 'es', 'pt', 'fr'].includes(language)).slice(0, 4) || [];
  const languages = (requestedLanguages.length ? requestedLanguages : ['es']) as Language[];
  const copies = languages.flatMap((language) => recreated.map((screen, index) => ({
    screenId: screen.screenId,
    language,
    headline: screens[index]?.texts[0] || screen.screenName,
    subtitle: screens[index]?.texts[1] || input.prompt.slice(0, 160),
  })));
  const generated = generateMockupHTML({
    appName: brandName,
    logoDataUrl: null,
    screens: recreated,
    copies,
    languages,
    dimensions: { w: input.width, h: input.height },
    primaryColor: theme.primaryColor,
    accentColors: theme.accentColors,
  })
    .replace(/coming soon/gi, 'preparado localmente')
    .replace('html, body {', 'body { margin: 0; overflow: hidden; }\n\nhtml, body {');
  return { html: generated, screens: recreated.length };
}

async function validateAndHeal(
  raw: string,
  input: RuntimePipelineInput,
): Promise<{ html: string; healed: boolean; healAttempts: number }> {
  let html = artifactSafeHtml(extractCode(raw));
  const issues = validateHTML(html);
  let healed = false;
  let healAttempts = 0;
  if (issues.length) {
    const result = await selfHeal(html, issues, input.prompt, input.width, input.height);
    html = artifactSafeHtml(result.html);
    healed = result.healed;
    healAttempts = result.attempts;
  }
  const remaining = validateHTML(html);
  if (remaining.length) {
    throw new Error(`El artefacto no superó la validación: ${remaining.join('; ')}`);
  }
  return { html, healed, healAttempts };
}

async function artisticDirection(
  input: RuntimePipelineInput,
): Promise<OpusArtisticGuidance | null> {
  return withWatchdog(
    () => getOpusArtisticGuidance({
      userPrompt: input.prompt,
      mode: input.mode,
      designPlan: [{
        label: input.currentHtml ? 'Revisión solicitada' : 'Propuesta principal',
        description: input.prompt,
      }],
      brandContext: {
        name: input.brandName || 'Marca del usuario',
        blurb: String(input.brandTokens || '').slice(0, 2_000),
        colors: [],
      },
      domain: input.mode,
    }),
    'artistic direction',
  );
}

async function runCritic(
  html: string,
  systemPrompt: string,
  input: RuntimePipelineInput,
): Promise<{ html: string; critique: VisualCritique | null; refined: boolean }> {
  if ((input.quality || 'balanced') === 'fast') {
    return { html, critique: null, refined: false };
  }
  const critique = await withWatchdog(
    async () => {
      const result = await critiqueDesign(
        html,
        input.prompt,
        input.width,
        input.height,
        input.prompt,
        input.brandTokens,
      );
      if (!result) throw new Error('visual critic unavailable');
      return result;
    },
    'visual critic',
  );
  if (!critique || !critique.worth_fixing || critique.score >= 9 || !critique.improvements.length) {
    return { html, critique, refined: false };
  }
  const refined = await withWatchdog(
    () => refineDesign(
      html,
      critique,
      input.prompt,
      input.width,
      input.height,
      systemPrompt,
    ),
    'visual refinement',
  );
  if (!refined) return { html, critique, refined: false };
  try {
    const validated = await validateAndHeal(refined, input);
    return { html: validated.html, critique, refined: validated.html !== html };
  } catch {
    return { html, critique, refined: false };
  }
}

export async function runProjectPipeline(
  input: RuntimePipelineInput,
): Promise<RuntimePipelineResult> {
  const [patterns, insights, direction] = await Promise.all([
    retrievePatterns({
      prompt: input.prompt,
      mode: input.mode,
      brandName: input.brandName,
      styleNotes: input.brandTokens,
    }, 6),
    retrieveDesignMemory(undefined, input.prompt, 8),
    artisticDirection(input),
  ]);

  const corpusSystem = await enrichWithCorpus(input.systemPrompt, {
    prompt: input.prompt,
    mode: input.mode,
    limit: 6,
  });
  const systemPrompt = [
    corpusSystem,
    patternBlock(patterns),
    memoryBlock(insights),
    direction ? formatOpusGuidanceForPrompt(direction) : '',
  ].filter(Boolean).join('\n\n');

  const mockup = input.currentHtml ? null : await recreateMockup(input);
  const raw = mockup?.html || await withWatchdog(
    () => callAI(input.userMessage, {
      system: systemPrompt,
      temperature: input.currentHtml ? 0.35 : 0.8,
      maxTokens: 24_000,
      images: input.images,
    }),
    'project generation',
  );
  if (!raw) throw new Error('El modelo no pudo producir una versión utilizable del proyecto.');
  const validated = await validateAndHeal(raw, input);
  const reviewed = await runCritic(validated.html, systemPrompt, input);

  return {
    html: reviewed.html,
    trace: {
      corpusEnriched: corpusSystem !== input.systemPrompt,
      retrievedPatterns: patterns.length,
      designMemoryInsights: insights.length,
      artisticDirector: direction?.model || 'unavailable',
      watchdog: true,
      deterministicValidation: true,
      healed: validated.healed,
      healAttempts: validated.healAttempts,
      screenRecreation: Boolean(mockup),
      mockupScreens: mockup?.screens || 0,
      critic: {
        attempted: (input.quality || 'balanced') !== 'fast',
        score: reviewed.critique?.score ?? null,
        refined: reviewed.refined,
        issues: reviewed.critique?.issues.length || 0,
      },
    },
  };
}
