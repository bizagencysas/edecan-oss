// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  fydesign Design Engine — Barrel Export                                    ║
// ║  7 Module Architecture:                                                    ║
// ║    1. Orchestrator    — Dynamic system prompting with design DNA           ║
// ║    2. Token Reader    — Brand tokens from DB → design dictionary + visuals ║
// ║    3. Code Extractor  — Strip markdown, extract pure code                  ║
// ║    4. Sandbox         — Isolated rendering with error detection (client)   ║
// ║    5. Self-Healer     — Auto-fix broken code by re-prompting AI           ║
// ║    6. Component Lib   — Premium phone frames, badges, decorative elements ║
// ║    7. Screen Resolver — Find & load specific screens from the real app    ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

export { buildSystemPrompt, buildUserMessage, detectMode } from './orchestrator';
export type { DesignMode } from './orchestrator';
export { extractBrandTokens, loadBrandTokens, loadBrandVisualAssets, loadBrandAnalysis } from './token-reader';
export type { BrandVisualAssets } from './token-reader';
export { extractCode, validateHTML } from './code-extractor';
export { selfHeal, wrapWithErrorCatcher } from './self-healer';
export { getComponentLibrary } from './component-library';
export { resolveScreensFromPrompt, buildScreenContext, buildScreenDirectory } from './screen-resolver';
export type { ResolvedScreen } from './screen-resolver';
export { buildRepoBrainContext, listBuiltinRepoBrainPatterns } from './repo-corpus';
export { ingestReposIntoCorpus } from './corpus-ingestor';
