// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  fydesign one-shot generator — local process, provider-neutral router         ║
// ║                                                                              ║
// ║  Loads the private local brand registry and asks the configured provider for N ║
// ║  on-brand slides using the CAROUSEL_BRAIN → renders each to PNG → saves →      ║
// ║  EXITS. Nothing stays listening on :3000.                                     ║
// ║                                                                              ║
// ║  Usage (JSON arg, as the MCP calls it):                                       ║
// ║    npm run studio -- '{"brand":"Acme",                                      ║
// ║      "prompt":"3 tips de finanzas","slides":3,"platform":"instagram-feed"}'   ║
// ║    npm run studio -- '{"list":true}'                                           ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import path from 'path';
import os from 'os';
import { mkdir, writeFile, readFile, unlink } from 'fs/promises';
import { existsSync } from 'node:fs';
import '../src/lib/network-guard';
// OSS runtime boundary: Edecán injects configuration from its encrypted
// vault. Never load repository .env files or ambient developer secrets.

import { callAIJSON, callAI } from '../src/lib/ai/deepseek-client';
import { renderHtmlToPng } from '../src/lib/render-png';
import { CAROUSEL_BRAIN } from '../src/lib/design-engine/prompts/carousel';
import { buildBrandStyleGuide } from '../src/lib/brand-style';
import { planStrategy } from '../src/lib/strategist';
import { generateBrandSVG } from '../src/lib/ai/svg-gen';
import { hasOpenAI, generateGptImage } from '../src/lib/ai/openai-image-client';
import { hasFal, generateFalImage } from '../src/lib/ai/fal-client';
import { loadFeedMemory, appendFeedMemory, memoryDigest } from '../src/lib/feed-memory';
import { findBrandRealData, brandRealDataBlock } from '../src/lib/brand-registry';
import { requiredRuntimePath } from '../src/lib/runtime-env';
// NOTE: github-analyzer (pulls in octokit, which trips tsx's ESM resolver) is
// imported lazily below — only when we must analyze a repo with no saved brand.

interface Input {
  list?: boolean;
  brand?: string;
  brandId?: string;
  repo?: string;
  prompt?: string;
  slides?: number;
  platform?: string;
  outDir?: string;
  media?: 'image' | 'video' | 'post' | 'edit' | 'campaign' | 'svg' | 'landing'
    | 'video-ad' | 'product-ad' | 'persona' | 'talking-head' | 'photo-dump' | 'batch'
    | 'register' | 'analyze' | 'clip' | 'ad-engine'
    | 'edit-pro' | 'photodump' | 'instadump' | 'ambassador' | 'train-face'
    | 'storyboard' | 'upscale' | 'animate'
    | 'moodboard' | 'autoroute' | 'virality' | 'angles' | 'product-shots'
    | 'product-photoshoot' | 'marketplace-card' | 'instant'
    | 'refine'; // generation mode
  // ── Cinema Studio override (video-ad) ──
  cinemaBody?: string;         // CAMERA_BODIES key (arri-alexa-35, red-v-raptor…)
  genre?: string;             // GENRES key (commercial, epic, noir…)
  colorGrade?: string;        // COLOR_GRADES key (teal-and-orange, golden-hour…)
  speedRamp?: string;         // SPEED_RAMPS key (slow-motion, impact…)
  // ── FyHighDesign: edit pack / studio / animate / upscale ──
  editOp?: 'inpaint' | 'place' | 'expand' | 'relight' | 'bg-remove' | 'outfit' | 'face-swap' | 'headshot' | 'skin' | 'erase' | 'style' | 'product';
  editRef?: string;            // product / garment / face / style reference image
  styleKey?: string;           // preset style key from the taste catalog
  frames?: number;             // storyboard frame count
  upscaleTarget?: 'image' | 'video';
  upscaleScale?: number;
  animateOp?: 'animate' | 'recast' | 'reference' | 'start-end';
  drivingVideo?: string;       // animate: driving performance video URL
  characterRef?: string;       // recast: character to swap in
  startImage?: string;         // start-end frame: first frame
  endImage?: string;           // start-end frame: last frame
  trendKeys?: string[];        // instadump trend packs
  colorLock?: boolean;         // Soul HEX: lock generation to brand palette
  // ── Brief refiner (Opus creative-director) ──
  kind?: string;               // asset kind for the refiner (video-ad, image, post…)
  refineAnswers?: Array<{ q: string; a: string }>; // answers to prior clarifying questions
  // ── Brand registration (media:'register') ──
  regColors?: string[];        // brand palette hex, primary first
  regLogo?: string;            // main logo (path / URL / dataURL)
  regAssets?: Array<{ name: string; url: string }>; // logo kit variants + app screenshots
  regFonts?: string;           // "Display + Body + Mono" or comma list
  regFacts?: string;           // REAL product facts (anti-invention) → description
  regBlurb?: string;           // one-line company blurb
  regRepo?: string;            // optional repo URL
  regId?: string;              // explicit brand id (to update an existing brand)
  inputImage?: string;         // for edit: path / URL / data URL of the image to remix
  // ── Higgsfield-class video / persona / batch ──
  shots?: number;              // video-ad: number of shots (3-6)
  withVoiceover?: boolean;     // video-ad: Opus writes + narrates a VO script (TTS)
  withMusic?: boolean;         // video-ad: add a music bed (needs MUAPI_MUSIC_MODEL)
  withCaptions?: boolean;      // video-ad: burn VO subtitles
  engine?: 'auto' | 'seedance' | 'omni' | 'kling' | 'direct' | 'avatar' | 'keyframe'; // video-ad: DIRECT (default) = one prompt + optional ref (logo) → ONE model (seedance/omni/kling), no pipeline; 'avatar' = real-person lip-sync; 'keyframe' = classic Opus multi-shot director
  preset?: string;             // video-ad: Marketing Studio format (ugc|tv-spot|hyper-motion|unboxing|product-review|demo|tutorial|cinematic|wild-card) — wraps the brief + sets model/aspect/duration
  videoUrl?: string;           // analyze/clip: a video URL (YouTube/TikTok/Vimeo via yt-dlp, or a direct http video)
  videoFile?: string;          // analyze/clip: a local video file path
  analyzeFrames?: number;      // analyze: how many keyframes Opus sees (4-10)
  clipCount?: number;          // clip: how many vertical clips to cut (1-10, default 3)
  clipLength?: number;         // clip: target length of each clip in seconds (8-90, default 25)
  adNiches?: number;           // ad-engine: how many audience niches to derive (1-6, default 3)
  adGenerate?: boolean;        // ad-engine: also generate a direct video variant per niche (costs)
  productImage?: string;       // product-ad: path/URL/dataURL of the real product shot
  productUrl?: string;         // product-ad: a product page to research for real facts
  // ── Instant mode: URL → full marketing suite, zero brand config ──
  siteUrl?: string;            // instant: the website URL to derive an EPHEMERAL brand from
  brandName?: string;          // instant: override the auto-derived brand name
  suite?: string[];            // instant: which pieces to make (posts|carousel|story|ad|video); default posts+story+video
  saveAsBrand?: boolean;       // instant: also persist the derived identity to Neon (default ephemeral)
  dryRun?: boolean;            // instant: stop after the plan; no media rendering
  shootMode?: string;          // product-photoshoot: named mode (product_shot|lifestyle_scene|closeup_with_person|moodboard_pin|hero_banner|social_carousel|ad_creative_pack|virtual_model_tryout|conceptual_product|restyle)
  cardTemplate?: string;       // marketplace-card: named card template (amazon|etsy|shopify|ebay|mercadolibre|app_store|thumbnail|review_badge)
  cardPrice?: string;          // marketplace-card: real price to print (no invented numbers)
  cardTitle?: string;          // marketplace-card: product title/headline to print
  personaName?: string;        // persona / talking-head: which AI influencer
  personaAction?: 'create' | 'use' | 'list'; // persona management
  refImages?: string[];        // persona/photo-dump: reference images (the "photo dump")
  voiceText?: string;          // talking-head: explicit spoken script (else uses prompt)
  lipsyncModel?: string;       // talking-head: Muapi lipsync endpoint override
  batchCount?: number;         // batch (supercomputer): how many variations
  tier?: 'fast' | 'pro' | 'max' | 'ultra'; // video quality ladder (current Muapi gen)
  provider?: 'vertex' | 'muapi' | 'openai' | 'fal'; // force a provider (default image=vertex, video=muapi)
  godMode?: boolean;           // campaign: Opus researches the web + sets strategy first
  quality?: 'ultra' | 'standard' | 'fast' | 'brand'; // Imagen 4 tier, or 'brand' = Nano Banana + refs
  style?: string;              // style hint (photographic, 3D, illustration, minimal, cinematic…)
  model?: string;              // override model endpoint (Muapi)
  duration?: number;           // video length (s)
  aspectRatio?: string;        // override aspect ratio
  sizes?: string[];            // explicit formats (['instagram-feed','instagram-story'] or ['all'])
  count?: number;              // number of image variations (1-4)
  noRefs?: boolean;            // skip brand reference images
}

interface Slide { title?: string; html?: string }
interface GenResult { slides?: Slide[]; caption?: string; hashtags?: string[] }

const SIZES: Record<string, { w: number; h: number }> = {
  'instagram-feed': { w: 1080, h: 1080 },
  square: { w: 1080, h: 1080 },
  'instagram-story': { w: 1080, h: 1920 },
  story: { w: 1080, h: 1920 },
  tiktok: { w: 1080, h: 1920 },
  reel: { w: 1080, h: 1920 },
  facebook: { w: 1200, h: 628 },
  'facebook-cover': { w: 1200, h: 630 },
  landscape: { w: 1600, h: 900 },
  'youtube-thumbnail': { w: 1280, h: 720 },
  linkedin: { w: 1200, h: 627 },
  portrait: { w: 1080, h: 1350 },
};

const AD_BRAIN = `SINGLE AD CREATIVE MODE
You generate ONE high-converting social ad creative: a bold headline (max 6 words),
one clear benefit, a strong CTA, the brand palette, modern layout. Inline all CSS.
NEVER invent statistics, numbers, follower/member counts, percentages, prices or testimonials. Use only real facts from the brief; if you have no real figure, write qualitative copy instead of a made-up number.`;

function log(...a: unknown[]) { console.error('[fydesign-gen]', ...a); }
// Machine-readable progress line (parsed live by the Jarvis plugin → push per piece).
function emitProgress(done: number, total: number, label: string) {
  process.stderr.write('__FYDP__' + JSON.stringify({ done, total, label }) + '\n');
}

function slug(s: string): string {
  return (s || 'design').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 30) || 'design';
}

// Brand style guide (Opus-learned, cached per brand) — threaded into every generation prompt.
let brandStyleGuide = '';
const styleBlock = () => (brandStyleGuide ? `\nBRAND STYLE GUIDE (replicate this EXACT established aesthetic — fonts, color rules, motifs, layout, voice):\n${brandStyleGuide}\n` : '');

// Recent-feed memory — keeps new posts non-repetitive AND stylistically consistent with the feed.
let feedMemory = '';
const feedBlock = () => (feedMemory ? `\nRECENT FEED FOR THIS BRAND (already posted — do NOT repeat these topics/concepts; vary the SUBJECT, but KEEP the visual style, composition language and color rhythm CONSISTENT with these so the feed stays cohesive and balanced, never random):\n${feedMemory}\n` : '');

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

// Retry on Vertex 429 / quota errors with linear backoff (Imagen has low per-minute quotas).
async function retry429<T>(fn: () => Promise<T>, tries = 4): Promise<T> {
  let lastErr: unknown;
  for (let i = 0; i < tries; i++) {
    try { return await fn(); }
    catch (e) {
      lastErr = e;
      const msg = e instanceof Error ? e.message : String(e);
      if (/429|resource_exhausted|quota/i.test(msg) && i < tries - 1) {
        const wait = 15000 * (i + 1);
        log(`  ⏳ cuota Vertex (429); espero ${Math.round(wait / 1000)}s y reintento…`);
        await sleep(wait);
        continue;
      }
      throw e;
    }
  }
  throw lastErr;
}

async function readStdinPayload(): Promise<string> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of process.stdin) {
    const buffer = Buffer.from(chunk);
    size += buffer.length;
    if (size > 16 * 1024 * 1024) throw new Error('El payload JSON supera el límite de 16 MB.');
    chunks.push(buffer);
  }
  return Buffer.concat(chunks).toString('utf8').trim();
}

async function parseArgs(): Promise<Input> {
  const raw = process.argv[2];
  if (raw && raw.trim().startsWith('{')) return JSON.parse(raw) as Input;
  const args = process.argv.slice(2);
  if (args.length === 0 && !process.stdin.isTTY) {
    const stdin = await readStdinPayload();
    if (stdin) return JSON.parse(stdin) as Input;
  }
  const get = (k: string) => {
    const a = args.find((x) => x.startsWith(`--${k}=`));
    return a ? a.slice(k.length + 3) : undefined;
  };
  return {
    list: args.includes('--list') || args.includes('--list-brands'),
    brand: get('brand'),
    brandId: get('brandId'),
    repo: get('repo'),
    prompt: get('prompt'),
    slides: get('slides') ? parseInt(get('slides')!, 10) : undefined,
    platform: get('platform'),
    outDir: get('outDir'),
    media: get('media') as Input['media'],
    inputImage: get('inputImage'),
    provider: get('provider') as 'vertex' | 'muapi' | 'openai' | 'fal' | undefined,
    godMode: args.includes('--god'),
    quality: get('quality') as 'ultra' | 'standard' | 'fast' | 'brand' | undefined,
    style: get('style'),
    model: get('model'),
    duration: get('duration') ? parseInt(get('duration')!, 10) : undefined,
    aspectRatio: get('aspectRatio'),
    sizes: get('sizes') ? get('sizes')!.split(',').map((s) => s.trim()).filter(Boolean) : undefined,
    count: get('count') ? parseInt(get('count')!, 10) : undefined,
    noRefs: args.includes('--no-refs'),
  };
}

function inferSlides(prompt: string): number {
  const p = (prompt || '').toLowerCase();
  const m = p.match(/(\d+)\s*(slides?|diapositivas?|frames?|im[aá]genes?)/);
  if (m) return parseInt(m[1], 10);
  const m2 = p.match(/(?:carrusel|carousel)\s*(?:de|of)?\s*(\d+)/);
  if (m2) return parseInt(m2[1], 10);
  if (/carrusel|carousel/.test(p)) return 6;
  return 1;
}

async function loadBrand(input: Input) {
  const db = await import('../src/lib/db');
  if (input.brandId) {
    const c = await db.loadBrandConfig(input.brandId);
    if (c) return c;
  }
  const all = await db.loadAllBrandConfigs();
  if (input.brand) {
    const q = input.brand.toLowerCase();
    const hit = all.find((b) => (b.company_name || '').toLowerCase() === q)
      || all.find((b) => (b.company_name || '').toLowerCase().includes(q));
    if (hit) return (await db.loadBrandConfig(hit.id)) || hit;
  }
  if (input.repo) {
    const q = input.repo.toLowerCase().replace(/^https?:\/\/github\.com\//, '');
    const hit = all.find((b) => (b.repo_url || '').toLowerCase().includes(q));
    if (hit) return (await db.loadBrandConfig(hit.id)) || hit;
  }
  // A brand/repo was specified but not matched → DON'T silently fall back to another
  // brand. Return null so main analyzes the repo fresh, or raises a clear error.
  if (input.brand || input.repo) return null;
  // Nothing specified → use the single configured brand.
  return db.loadLatestBrandConfig();
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function brandContext(cfg: any, freshAnalysis?: any) {
  let analysis: any = freshAnalysis || {};
  if (!freshAnalysis) {
    try {
      analysis = typeof cfg?.analysis_json === 'string' ? JSON.parse(cfg.analysis_json || '{}') : (cfg?.analysis_json || {});
    } catch { analysis = {}; }
  }
  const theme = analysis.theme || {};
  const colors = [theme.primaryColor, ...(theme.accentColors || [])].filter(Boolean) as string[];
  const screens = (analysis.screens || []).slice(0, 6)
    .map((s: any) => `${s.screenName}: ${(s.texts || []).slice(0, 4).join(' | ')}`).join('\n');
  const info = [cfg?.company_blurb, cfg?.brand_notes, analysis.description].filter(Boolean).join('\n');

  // Image assets (logo, icons, app screenshots) usable as generation references.
  let assets: Array<{ name: string; url: string }> = [];
  try {
    let ua: unknown = cfg?.uploaded_assets;
    ua = typeof ua === 'string' ? JSON.parse(ua) : ua;
    if (Array.isArray(ua)) {
      assets = ua
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        .filter((x: any) => x && (x.isImage || /\.(png|jpe?g|webp)$/i.test(x.name || '')))
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        .map((x: any) => ({ name: String(x.name || ''), url: String(x.url || '') }))
        .filter((x) => x.url);
    }
  } catch { /* ignore */ }

  const name = cfg?.company_name || analysis.appName || 'Brand';

  // ── Real-data enrichment (fix: no invented prices/claims, no empty mockups) ──
  // If this brand has curated REAL data from its product repo, fold it in:
  //  • real value props + confirmed facts + "do NOT invent" guardrails → info
  //  • real app screens → screens (so mockups recreate the actual UI, not a blank phone)
  //  • real palette / logo / fonts as a FALLBACK when the DB record lacks them
  const real = findBrandRealData(name, cfg?.company_name, cfg?.repo_url, cfg?.id);
  let outColors = colors;
  let outScreens = screens;
  let outInfo = info;
  let outLogo = cfg?.logo_url || '';
  let outFonts = analysis.brand_fonts || analysis.brandFonts || '';
  if (real) {
    outInfo = [info, brandRealDataBlock(real)].filter(Boolean).join('\n\n');
    if (!outScreens) outScreens = real.screens.map((s) => `${s.name}: ${s.shows}`).join('\n');
    if (!outColors.length) outColors = real.colors.slice();
    if (!outLogo) outLogo = real.logoUrl;
    if (!outFonts) outFonts = `${real.fonts.display} + ${real.fonts.body} + ${real.fonts.mono}`;
    log(`Datos reales de marca cargados para "${real.name}" (${real.screens.length} pantallas, sin inventar cifras)`);
  }

  return {
    name,
    colors: outColors,
    brandColors: analysis.brand_colors || analysis.brandColors || '',
    fonts: outFonts,
    screens: outScreens,
    info: outInfo,
    logo: outLogo,
    assets,
  };
}

// Resolve the brand logo to an embeddable data URL (no server needed):
//   • data: URL             → used as-is
//   • /api/assets/<id>/file  → look up gcsPath in asset_registry, read bytes from GCS
//   • http(s) URL           → fetched and inlined
async function resolveLogoDataUrl(logo: string): Promise<string> {
  if (!logo) return '';
  if (logo.startsWith('data:')) return logo;
  const m = logo.match(/\/api\/assets\/([^/]+)\/file/);
  if (m) {
    try {
      const { getDb } = await import('../src/lib/db');
      const sql = getDb();
      const rows = await sql`SELECT metadata, mime_type FROM asset_registry WHERE id = ${m[1]} LIMIT 1`;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const row = rows[0] as any;
      const gcsPath = row?.metadata?.gcsPath as string | undefined;
      if (gcsPath) {
        const { readFromGCS } = await import('../src/lib/gcs');
        const file = await readFromGCS(gcsPath);
        if (file?.buffer) {
          const mime = row.mime_type || file.contentType || 'image/png';
          return `data:${mime};base64,${Buffer.from(file.buffer).toString('base64')}`;
        }
      }
    } catch (e) { log('logo resolve (GCS) failed:', e instanceof Error ? e.message : e); }
    return '';
  }
  if (logo.startsWith('http')) {
    try {
      const r = await fetch(logo);
      if (r.ok) {
        const buf = Buffer.from(await r.arrayBuffer());
        return `data:${r.headers.get('content-type') || 'image/png'};base64,${buf.toString('base64')}`;
      }
    } catch (e) { log('logo fetch failed:', e instanceof Error ? e.message : e); }
  }
  return '';
}

// Resolve any asset URL (logo, screenshot) to base64 bytes for use as a reference image.
async function urlToInline(url: string): Promise<{ data: string; mimeType: string } | null> {
  const dataUrl = await resolveLogoDataUrl(url);
  const m = /^data:([^;]+);base64,([\s\S]+)$/.exec(dataUrl);
  return m ? { mimeType: m[1], data: m[2] } : null;
}

// Pick the best brand reference images: logo first, then a real app screenshot.
function pickRefs(ctx: ReturnType<typeof brandContext>): string[] {
  const kit = buildLogoKit(ctx);
  const refs: string[] = [];
  if (kit.lockup) refs.push(kit.lockup);
  if (kit.mark && kit.mark !== kit.lockup) refs.push(kit.mark);   // give the model the compact SYMBOL too
  const isLogo = (n: string) => /logo|icon|mark|lockup|favicon|tile/i.test(n);
  const shot =
    ctx.assets.find((a) => /score|appstore|screen|mockup|preview/i.test(a.name) && !isLogo(a.name)) ||
    ctx.assets.find((a) => !isLogo(a.name) && a.url !== kit.lockup && a.url !== kit.mark);
  if (shot) refs.push(shot.url);
  return refs.slice(0, 3);
}

// ── LOGO INTELLIGENCE ────────────────────────────────────────────────────────
// A real brand has a KIT: lockup (symbol+wordmark), mark (symbol only), icon (app tile),
// each in color / white / ink. We catalog ALL variants from the brand's assets and let Opus
// CHOOSE the right one per placement (hero→lockup, corner→mark, app→icon, dark bg→white)
// instead of forcing the literal logo_url everywhere — and the vision critique catches misuse.
function _logoRole(n: string): string {
  const s = n.toLowerCase();
  if (/lockup|wordmark|imagotipo|horizontal/.test(s)) return 'lockup';
  if (/app-?icon|favicon|tile|adaptive|play-?store|launcher|store-?icon/.test(s)) return 'icon';
  if (/logo-?mark|isotipo|symbol|monogram|glyph|\bmark\b/.test(s)) return 'mark';
  if (/logo/.test(s)) return 'lockup';   // a bare "logo" usually IS the lockup
  return '';
}
function _logoTone(n: string): string {
  const s = n.toLowerCase();
  if (/white|blanco|\blight\b|paper|claro/.test(s)) return 'light';
  if (/\bink\b|black|negro|oscuro|\bdark\b/.test(s)) return 'dark';
  return 'color';   // wine/vino/color/default → the brand-color version
}
function buildLogoKit(ctx: ReturnType<typeof brandContext>) {
  const all: Array<{ name: string; url: string }> = [];
  if (ctx.logo) all.push({ name: 'logo_url', url: ctx.logo });
  for (const a of ctx.assets) all.push({ name: a.name, url: a.url });
  const cands = all.filter((a) => (/\.(svg|png|jpe?g|webp)$/i.test(a.name) || a.name === 'logo_url') && _logoRole(a.name));
  const pick = (role: string, tone?: string): string => {
    const m = cands.filter((a) => _logoRole(a.name) === role && (!tone || _logoTone(a.name) === tone));
    if (!m.length) return '';
    m.sort((a, b) => (/\.svg$/i.test(a.name) ? 0 : 1) - (/\.svg$/i.test(b.name) ? 0 : 1) || a.name.length - b.name.length);
    return m[0].url;
  };
  const lockup = ctx.logo || pick('lockup', 'color') || pick('lockup');   // respect the user's chosen logo_url as primary
  const mark = pick('mark', 'color') || pick('mark') || '';
  return {
    lockup,
    lockupLight: pick('lockup', 'light') || lockup,
    mark: mark || lockup,
    markLight: pick('mark', 'light') || mark || lockup,
    markDark: pick('mark', 'dark') || mark || lockup,
    icon: pick('icon') || mark || lockup,
    catalog: cands.map((a) => `${a.name} [${_logoRole(a.name)}/${_logoTone(a.name)}]`),
  };
}

// Module-level logo kit (loaded once per generation, like styleBlock/feedBlock).
let _logoTokens: Record<string, string> = { __LOGO__: '', __LOGO_LIGHT__: '', __LOGOMARK__: '', __LOGOMARK_LIGHT__: '', __LOGOMARK_DARK__: '', __ICON__: '' };
let _logoBlock = '';
const logoBlock = () => _logoBlock;
function applyLogos(html: string): string {
  let out = html;
  // longest tokens first so __LOGOMARK_LIGHT__ isn't clobbered by __LOGO__/__LOGOMARK__
  for (const tok of Object.keys(_logoTokens).sort((a, b) => b.length - a.length)) out = out.split(tok).join(_logoTokens[tok] || '');
  return out;
}
async function loadLogoKit(ctx: ReturnType<typeof brandContext>): Promise<void> {
  const kit = buildLogoKit(ctx);
  const uniq = [...new Set([kit.lockup, kit.lockupLight, kit.mark, kit.markLight, kit.markDark, kit.icon].filter(Boolean))];
  const cache = new Map<string, string>();
  for (const u of uniq) { try { cache.set(u, await resolveLogoDataUrl(u)); } catch { cache.set(u, ''); } }
  const d = (u: string) => (u && cache.get(u)) || '';
  const lockup = d(kit.lockup); const mark = d(kit.mark) || lockup;
  _logoTokens = {
    __LOGO__: lockup,
    __LOGO_LIGHT__: d(kit.lockupLight) || lockup,
    __LOGOMARK__: mark,
    __LOGOMARK_LIGHT__: d(kit.markLight) || mark,
    __LOGOMARK_DARK__: d(kit.markDark) || mark,
    __ICON__: d(kit.icon) || mark,
  };
  const have = (t: string) => (_logoTokens[t] ? 'available' : 'falls back');
  _logoBlock = (mark || lockup) ? `\nBRAND LOGO KIT — CHOOSE the right mark per placement; NEVER stretch, crop, recolor, or cram the wide lockup into a small corner:
- __LOGO__ (${have('__LOGO__')}): the FULL lockup (symbol + wordmark) — ONLY for a header/hero at comfortable width.
- __LOGO_LIGHT__ (${have('__LOGO_LIGHT__')}): the lockup in white — for dark or photo backgrounds.
- __LOGOMARK__ (${have('__LOGOMARK__')}): just the SYMBOL (no text) — for corners, badges, avatars, watermarks, small spaces.
- __LOGOMARK_LIGHT__ (${have('__LOGOMARK_LIGHT__')}) / __LOGOMARK_DARK__ (${have('__LOGOMARK_DARK__')}): the symbol in white / ink — match the background.
- __ICON__ (${have('__ICON__')}): the app icon / rounded tile — for app mockups, store badges, favicons.
Insert each as <img src="__TOKEN__" alt="logo">. A small corner = __LOGOMARK__, NEVER __LOGO__. Match the background tone.\n` : '';
  log(`Logo kit: ${kit.catalog.length} variantes` + (_logoTokens.__LOGOMARK__ && _logoTokens.__LOGOMARK__ !== lockup ? ' (lockup + mark' + (d(kit.icon) ? ' + icon)' : ')') : ''));
}

function escapeHtml(s: string): string {
  return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// Build a finished, ready-to-post creative: the generated image + brand logo + headline/CTA overlay.
function buildPostHtml(
  baseDataUrl: string,
  ctx: ReturnType<typeof brandContext>,
  plan: { headline?: string; subtext?: string; cta?: string },
  logoDataUrl: string,
  size: { w: number; h: number },
): string {
  const primary = ctx.colors[0] || '#0066FF';
  const headline = escapeHtml(plan.headline || '');
  const subtext = escapeHtml(plan.subtext || '');
  const cta = escapeHtml(plan.cta || '');
  const hSize = Math.round(size.w * 0.068);
  const sSize = Math.round(size.w * 0.030);
  return `<!doctype html><html><head><meta charset="utf-8"><style>
    *{margin:0;box-sizing:border-box}
    body{margin:0;font-family:Inter,-apple-system,system-ui,sans-serif}
    .wrap{position:relative;width:${size.w}px;height:${size.h}px;overflow:hidden}
    .bg{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
    .scrim{position:absolute;inset:0;background:linear-gradient(to top,rgba(8,12,22,.84) 0%,rgba(8,12,22,.35) 44%,rgba(8,12,22,0) 70%)}
    .logo{position:absolute;top:${Math.round(size.h * 0.05)}px;left:${Math.round(size.w * 0.06)}px;height:${Math.round(size.h * 0.058)}px}
    .txt{position:absolute;left:${Math.round(size.w * 0.07)}px;right:${Math.round(size.w * 0.07)}px;bottom:${Math.round(size.h * 0.07)}px;color:#fff}
    .h{font-size:${hSize}px;font-weight:800;line-height:1.04;letter-spacing:-.02em;text-shadow:0 2px 30px rgba(0,0,0,.45)}
    .s{font-size:${sSize}px;opacity:.93;margin-top:14px;max-width:86%;line-height:1.35}
    .cta{display:inline-block;margin-top:26px;background:${primary};color:#fff;font-weight:700;font-size:${sSize}px;padding:14px 30px;border-radius:999px}
  </style></head><body>
    <div class="wrap">
      <img class="bg" src="${baseDataUrl}"/>
      <div class="scrim"></div>
      ${(_logoTokens.__LOGOMARK__ || logoDataUrl) ? `<img class="logo" src="${_logoTokens.__LOGOMARK__ || logoDataUrl}"/>` : ''}
      <div class="txt">
        ${headline ? `<div class="h">${headline}</div>` : ''}
        ${subtext ? `<div class="s">${subtext}</div>` : ''}
        ${cta ? `<span class="cta">${cta}</span>` : ''}
      </div>
    </div>
  </body></html>`;
}

// Crop/scale an image (data URL) to EXACT pixel dimensions via the shared renderer.
async function cropToSize(dataUrl: string, w: number, h: number): Promise<Buffer> {
  const html = `<!doctype html><html><body style="margin:0"><img src="${dataUrl}" style="display:block;width:${w}px;height:${h}px;object-fit:cover"/></body></html>`;
  return renderHtmlToPng(html, w, h, `fit ${w}×${h}`);
}

// The configured creative director designs the post composition itself in HTML/CSS,
// directing the layout/typography. The builder's photo + logo are injected via tokens.
// Returns null on failure → caller falls back to the fixed template.
async function opusOverlayHtml(
  ctx: ReturnType<typeof brandContext>,
  copy: { headline?: string; subtext?: string; cta?: string },
  size: { w: number; h: number },
): Promise<string | null> {
  const sys = 'You are an elite art director and front-end designer. Output ONLY a single, complete, self-contained HTML document — no explanation, no markdown fences.';
  const ask = `Design a premium, on-brand social composition as ONE self-contained HTML document, EXACTLY ${size.w}x${size.h} px.

BRAND: ${ctx.name}
PALETTE (use these): ${ctx.colors.join(', ')} ${ctx.brandColors}
FONTS: ${ctx.fonts || 'Inter, system-ui'}${styleBlock()}
COPY: headline="${copy.headline || ''}" · subtext="${copy.subtext || ''}" · cta="${copy.cta || ''}"${logoBlock()}

HARD RULES:
- Root element exactly ${size.w}px x ${size.h}px, position:relative, overflow:hidden.
- Full-bleed background: <img src="__IMAGE__" style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover"> (a generated photo — keep the EXACT token __IMAGE__ as src).
- Brand logo: pick from the LOGO KIT above — a small corner on a photo = __LOGOMARK_LIGHT__ (symbol, white), NEVER the full lockup. Keep the EXACT token.
- Add a subtle gradient scrim where text sits, for legibility.
- Lay out headline / subtext / CTA with bold, beautiful, on-brand typography; the CTA looks like a button in a brand color.
- Compose intelligently (don't always bottom-align); premium ad quality.
- Inline ALL CSS. No external scripts/JS. Google Fonts <link> is allowed.

Output ONLY the HTML document.`;
  try {
    const raw = await callAI(ask, { system: sys, maxTokens: 4000 });
    let h = (raw || '').trim();
    const fence = h.match(/```(?:html)?\s*([\s\S]*?)```/i);
    if (fence) h = fence[1].trim();
    const start = h.search(/<!doctype|<html|<body|<div/i);
    if (start > 0) h = h.slice(start);
    if (!h.includes('__IMAGE__') || !/<\/(div|body|html)>/i.test(h)) return null;
    return h;
  } catch {
    return null;
  }
}

// Vision always sends the actual bytes through the provider-neutral AI bridge
// or Anthropic/Vertex. A text-only CLI is never asked to read a local path.
async function callVision(prompt: string, imagePath: string): Promise<string> {
  const ext = path.extname(imagePath).toLowerCase();
  const mimeType = ext === '.jpg' || ext === '.jpeg'
    ? 'image/jpeg'
    : ext === '.webp'
      ? 'image/webp'
      : 'image/png';
  const data = (await readFile(imagePath)).toString('base64');
  return callAI(prompt, { image: { mimeType, data }, maxTokens: 8000 });
}

let _vtmp = 0;
const tmpPng = () => path.join(os.tmpdir(), `fyd-${Date.now()}-${++_vtmp}.png`);

// Opus SEES the generated photo and designs the overlay around its real negative space.
async function opusVisionOverlay(
  ctx: ReturnType<typeof brandContext>,
  copy: { headline?: string; subtext?: string; cta?: string },
  size: { w: number; h: number },
  baseImagePath: string,
  feedback: string,
): Promise<string | null> {
  const prompt = `View the attached image. It is a generated ${size.w}x${size.h}px marketing photo for the brand "${ctx.name}".
Design a COMPLETE self-contained HTML document, EXACTLY ${size.w}x${size.h}px, that overlays the brand copy onto this photo, placing text in the photo's ACTUAL empty/negative space — NEVER cover the main subject's face or focal point. Match the photo's lighting, mood and composition.
PALETTE: ${ctx.colors.join(', ')} ${ctx.brandColors} · FONTS: ${ctx.fonts || 'Inter, system-ui'}${styleBlock()}${logoBlock()}
COPY: headline="${copy.headline || ''}" · subtext="${copy.subtext || ''}" · cta="${copy.cta || ''}"
RULES: full-bleed background must be <img src="__IMAGE__" style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover"> (keep the EXACT token __IMAGE__). Brand logo: pick from the LOGO KIT — a small corner on a photo = __LOGOMARK_LIGHT__ (symbol, white), NEVER the full lockup; keep the EXACT token. Add a subtle scrim only where text sits. Bold, beautiful, on-brand typography; CTA looks like a button. Inline ALL CSS, no scripts.${feedback ? `\nThe previous version had these problems — FIX them: ${feedback}` : ''}
Output ONLY the HTML document, nothing else.`;
  try {
    const raw = await callVision(prompt, baseImagePath);
    let h = raw.trim();
    const fence = h.match(/```(?:html)?\s*([\s\S]*?)```/i);
    if (fence) h = fence[1].trim();
    const start = h.search(/<!doctype|<html|<body|<div/i);
    if (start > 0) h = h.slice(start);
    if (!h.includes('__IMAGE__') || !/<\/(div|body|html)>/i.test(h)) return null;
    return h;
  } catch {
    return null;
  }
}

// Opus LOOKS at the finished post and critiques it (legibility, face coverage, on-brand, polish).
async function opusCritique(
  finalPath: string,
  ctx: ReturnType<typeof brandContext>,
): Promise<{ good: boolean; issues: string }> {
  const prompt = `View the attached image. It is a finished social-media post for the brand "${ctx.name}" (palette ${ctx.colors.join(', ')}).
Critique it as a STRICT art director. Check: (1) are the headline and CTA fully legible with strong contrast? (2) does any text cover the subject's face/focal point? (3) is it on-brand? (4) is the LOGO right — the correct variant for its placement (a full lockup must NOT be squeezed into a small corner; that needs the symbol/mark), correct color for the background, not stretched or cropped? (5) does it look premium and polished?
Respond with ONLY JSON: {"good": true or false, "issues": "if not good, a short concrete list of fixes (placement/contrast/size/spacing); else empty string"}`;
  try {
    const raw = await callVision(prompt, finalPath);
    let s = raw.trim();
    const fence = s.match(/```(?:json)?\s*([\s\S]*?)```/i);
    if (fence) s = fence[1].trim();
    const b = s.indexOf('{');
    if (b > 0) s = s.slice(b);
    const j = JSON.parse(s) as { good?: boolean; issues?: string };
    return { good: j.good !== false, issues: String(j.issues || '') };
  } catch {
    return { good: true, issues: '' }; // on failure, accept rather than loop forever
  }
}

// Opus LOOKS at a freshly generated base image (pre-overlay) and says whether the
// image model baked GARBAGE TEXT into it. The selected vision model receives bytes.
// Returns true when the image is CLEAN (no baked text). Fails OPEN on error.
async function imageIsTextFree(imgPath: string): Promise<boolean> {
  const prompt = `View the attached image. Does it contain ANY readable text, letters, words, numbers, captions, labels, signage or watermarks BAKED into the pixels? Misspelled/scrambled/gibberish text counts as YES. Respond with ONLY JSON: {"hasText": true or false}.`;
  try {
    const raw = await callVision(prompt, imgPath);
    let s = raw.trim();
    const fence = s.match(/```(?:json)?\s*([\s\S]*?)```/i);
    if (fence) s = fence[1].trim();
    const b = s.indexOf('{');
    if (b > 0) s = s.slice(b);
    const j = JSON.parse(s) as { hasText?: boolean };
    return j.hasText !== true;
  } catch {
    return true; // never block generation on a vision outage
  }
}

// Generate a brand image and, if Opus sees baked-in garbage text, regenerate with an
// ever-stronger no-text instruction (≤ tries). The no-text policy is already enforced
// inside generateImagenImage; this is the VISION verification on top of it.
async function genCleanVertexImage(
  basePrompt: string,
  quality: Input['quality'],
  aspect: string,
  references: Array<{ data: string; mimeType: string }>,
  tries = 2,
  engine?: 'vertex' | 'gpt-image-2',
  allowUi?: boolean,
  allowText?: boolean,
): Promise<{ dataUrl: string; model: string }> {
  // Explicit engine wins; otherwise a global FY_IMAGE_ENGINE=gpt-image-2 flips every tool.
  const eng: 'vertex' | 'gpt-image-2' = engine ?? (process.env.FY_IMAGE_ENGINE === 'gpt-image-2' ? 'gpt-image-2' : 'vertex');
  const suffixes = [
    '',
    ' Absolutely no text, letters, numbers or symbols anywhere — keep all surfaces blank or abstract.',
    ' ZERO text of any kind; any sign/screen/label must be empty, blurred, or turned away from camera.',
  ];
  let r = await genImageBest(basePrompt, quality, aspect, references, eng, allowUi, allowText);
  if (allowText) return r; // skip vision text critique if text is explicitly allowed
  for (let i = 0; i < tries; i++) {
    const m = /^data:[^;]+;base64,([\s\S]+)$/.exec(r.dataUrl);
    if (!m) break;
    const p = tmpPng();
    try { await writeFile(p, Buffer.from(m[1], 'base64')); } catch { break; }
    if (await imageIsTextFree(p)) return r;
    if (i === tries - 1) break;
    log(`  ⚠ la imagen traía texto basura — regenero sin texto (intento ${i + 2}/${tries + 1})`);
    r = await genImageBest(basePrompt + suffixes[Math.min(i + 1, suffixes.length - 1)], quality, aspect, references, eng, allowUi, allowText);
  }
  return r;
}

// Compose a finished post: Opus sees the photo → designs overlay → renders → self-critiques → refines (≤2).
async function composePostWithVision(
  baseDataUrl: string,
  ctx: ReturnType<typeof brandContext>,
  copy: { headline?: string; subtext?: string; cta?: string },
  logoDataUrl: string,
  size: { w: number; h: number },
): Promise<Buffer> {
  const m = /^data:[^;]+;base64,([\s\S]+)$/.exec(baseDataUrl);
  let basePath = '';
  if (m) { basePath = tmpPng(); try { await writeFile(basePath, Buffer.from(m[1], 'base64')); } catch { basePath = ''; } }

  let feedback = '';
  let last: Buffer | null = null;
  for (let iter = 0; iter < 2; iter++) {
    const designed = basePath ? await opusVisionOverlay(ctx, copy, size, basePath, feedback) : null;
    const tpl = designed || (await opusOverlayHtml(ctx, copy, size));
    const html = tpl
      ? applyLogos(tpl.split('__IMAGE__').join(baseDataUrl))
      : buildPostHtml(baseDataUrl, ctx, copy, logoDataUrl, size);
    log(iter === 0
      ? (designed ? 'Opus VIO la foto y compuso el overlay' : 'Opus compuso el overlay (sin visión)')
      : 'Opus rediseñó tras su auto-crítica');
    last = await renderHtmlToPng(html, size.w, size.h, `post v${iter + 1}`);
    if (!basePath) break;
    const finalPath = tmpPng();
    try { await writeFile(finalPath, last); } catch { break; }
    const crit = await opusCritique(finalPath, ctx);
    log(`Auto-crítica Opus v${iter + 1}: ${crit.good ? 'aprobado ✓' : 'a mejorar — ' + crit.issues.slice(0, 90)}`);
    if (crit.good) break;
    feedback = crit.issues;
  }
  return last as Buffer;
}

// Persist generated outputs (data URLs, http URLs, or raw buffers) to disk + print result JSON.
async function saveOutputs(
  outputs: Array<{ dataUrl?: string; url?: string; buffer?: Buffer; tag?: string }>,
  ctx: ReturnType<typeof brandContext>,
  input: Input,
  prompt: string,
  meta: { media: string; model: string; cost?: { amount_usd?: number } | null; caption?: string; hashtags?: string[]; subdir?: string; headline?: string; concept?: string },
) {
  const baseDir = input.outDir || requiredRuntimePath('FYDESIGN_OUTPUT_ROOT');
  const outDir = meta.subdir ? path.join(baseDir, meta.subdir) : baseDir;
  await mkdir(outDir, { recursive: true });
  const d = new Date();
  const stamp = `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}-${String(d.getHours()).padStart(2, '0')}${String(d.getMinutes()).padStart(2, '0')}`;
  const base = slug(prompt);
  const isVid = meta.media === 'video';

  const files: string[] = [];
  for (let i = 0; i < outputs.length; i++) {
    const o = outputs[i];
    let buf: Buffer;
    let ext: string;
    if (o.buffer) {
      buf = o.buffer;
      ext = 'png';
    } else if (o.dataUrl) {
      const m = /^data:([^;]+);base64,([\s\S]+)$/.exec(o.dataUrl);
      if (!m) { log('  ✗ data URL inválido'); continue; }
      buf = Buffer.from(m[2], 'base64');
      ext = (m[1].split('/')[1] || 'png').replace('jpeg', 'jpg');
    } else {
      const r = await fetch(o.url!, { signal: AbortSignal.timeout(120_000) });
      if (!r.ok) { log(`  ✗ no pude descargar ${o.url} (${r.status})`); continue; }
      buf = Buffer.from(await r.arrayBuffer());
      ext = (o.url!.match(/\.(png|jpe?g|webp|mp4|mov|webm)(?:\?|$)/i)?.[1] || (isVid ? 'mp4' : 'png')).toLowerCase().replace('jpeg', 'jpg');
    }
    const tag = o.tag ? `-${o.tag}` : '';
    const file = path.join(outDir, `${base}-${stamp}${tag}-${String(i + 1).padStart(2, '0')}.${ext}`);
    await writeFile(file, buf);
    files.push(file);
    log(`  ✓ ${file} (${(buf.length / 1024).toFixed(0)} KB)`);
  }
  if (meta.caption || (meta.hashtags && meta.hashtags.length)) {
    await writeFile(path.join(outDir, `${base}-${stamp}-caption.txt`), `${meta.caption || ''}\n\n${(meta.hashtags || []).join(' ')}\n`);
  }

  // Record in the brand's feed memory (so next time stays non-repetitive + consistent).
  try {
    await appendFeedMemory(baseDir, {
      date: new Date().toISOString(),
      media: meta.media,
      brief: prompt,
      headline: meta.headline,
      concept: (meta.concept || '').slice(0, 140),
      files: files.length,
    });
  } catch { /* noop */ }

  process.stdout.write(JSON.stringify({
    ok: true, brand: ctx.name, media: meta.media, model: meta.model,
    count: files.length, dir: outDir, files, cost: meta.cost || null,
    caption: meta.caption || '', hashtags: meta.hashtags || [],
  }, null, 2) + '\n');
}

// Load an image (file path / URL / data URL) as base64 inline data for editing.
async function loadImageInline(src: string): Promise<{ data: string; mimeType: string } | null> {
  if (!src) return null;
  if (src.startsWith('data:')) {
    const m = /^data:([^;]+);base64,([\s\S]+)$/.exec(src);
    return m ? { mimeType: m[1], data: m[2] } : null;
  }
  if (src.startsWith('http') || src.startsWith('/api/assets')) return urlToInline(src);
  try {
    const buf = await readFile(src);
    const ext = (src.split('.').pop() || 'png').toLowerCase();
    const mimeType = ext === 'jpg' || ext === 'jpeg' ? 'image/jpeg' : ext === 'webp' ? 'image/webp' : 'image/png';
    return { data: buf.toString('base64'), mimeType };
  } catch { return null; }
}

// Generate one image via Vertex, picking the model from the quality tier.
async function genVertexImage(
  prompt: string,
  quality: Input['quality'],
  aspect: string,
  references: Array<{ data: string; mimeType: string }>,
  allowUi?: boolean,
  allowText?: boolean,
): Promise<{ dataUrl: string; model: string }> {
  const { generateImagenImage } = await import('../src/lib/ai/imagen-client');
  const IMAGEN4: Record<string, string> = {
    ultra: 'imagen-4.0-ultra-generate-001',
    standard: 'imagen-4.0-generate-001',
    fast: 'imagen-4.0-fast-generate-001',
  };
  const q = quality || 'standard';
  const vModel = q === 'brand'
    ? (process.env.GOOGLE_PREMIUM_IMAGE_MODEL || 'gemini-3-pro-image-preview')
    : (IMAGEN4[q] || IMAGEN4.standard);
  const ar = (['1:1', '16:9', '9:16', '4:3', '3:4'].includes(aspect) ? aspect : '1:1') as '1:1' | '16:9' | '9:16' | '4:3' | '3:4';
  const refs = vModel.includes('gemini') ? references : [];
  const img = await retry429(() => generateImagenImage(prompt, { aspectRatio: ar, references: refs, model: vModel, allowUi, allowText }));
  return { dataUrl: img.dataUrl, model: `vertex:${vModel}` };
}

// Route image generation to the chosen engine. 'gpt-image-2' (OpenAI via Muapi) is the
// premium photographic engine; its image-to-image variant composites the REAL product/logo,
// so inline references are hosted to URLs first. Any failure falls back to Vertex.
async function genImageBest(
  prompt: string,
  quality: Input['quality'],
  aspect: string,
  references: Array<{ data: string; mimeType: string }>,
  engine: 'vertex' | 'gpt-image-2' = 'vertex',
  allowUi?: boolean,
  allowText?: boolean,
): Promise<{ dataUrl: string; model: string }> {
  if (engine === 'gpt-image-2') {
    try {
      const { generateGptImage2 } = await import('../src/lib/ai/muapi-client');
      const { hostStillForMuapi } = await import('../src/lib/ai/brand-image');
      const refUrls: string[] = [];
      for (const r of references.slice(0, 6)) {
        try { const u = await hostStillForMuapi(`data:${r.mimeType};base64,${r.data}`); if (/^https?:\/\//i.test(u)) refUrls.push(u); } catch { /* skip a ref that can't be hosted */ }
      }
      const res = await generateGptImage2(prompt, { refUrls, aspect, resolution: '2K', quality: 'high', allowText });
      const url = res.outputs?.[0];
      if (!url) throw new Error('gpt-image-2 no devolvió imagen');
      const dl = await fetch(url, { signal: AbortSignal.timeout(120_000) });
      if (!dl.ok) throw new Error(`descarga gpt-image-2 HTTP ${dl.status}`);
      const buf = Buffer.from(await dl.arrayBuffer());
      if (!buf.length) throw new Error('gpt-image-2 devolvió 0 bytes');
      const mime = (dl.headers.get('content-type') || 'image/png').split(';')[0];
      return { dataUrl: `data:${mime};base64,${buf.toString('base64')}`, model: `muapi:${res.model}` };
    } catch (e) {
      log(`  ⚠ gpt-image-2 falló (${e instanceof Error ? e.message : e}) — uso Vertex`);
    }
  }
  return genVertexImage(prompt, quality, aspect, references, allowUi, allowText);
}

// Edit / remix an existing image (image-to-image via Nano Banana / gemini-image).
async function runEdit(input: Input, ctx: ReturnType<typeof brandContext>, prompt: string) {
  if (!input.inputImage) throw new Error('inputImage requerido (ruta, URL o data URL) para editar');
  const inl = await loadImageInline(input.inputImage);
  if (!inl) throw new Error(`No pude cargar la imagen de entrada: ${input.inputImage}`);
  const { generateImagenImage } = await import('../src/lib/ai/imagen-client');
  const size = SIZES[input.platform || 'instagram-feed'] || SIZES['instagram-feed'];
  const aspect = input.aspectRatio || (size.w === size.h ? '1:1' : size.h > size.w ? '9:16' : '16:9');
  const ar = (['1:1', '16:9', '9:16', '4:3', '3:4'].includes(aspect) ? aspect : '1:1') as '1:1' | '16:9' | '9:16' | '4:3' | '3:4';
  const editPrompt = `${prompt}\n\nApply this edit to the attached image. Keep it photorealistic and consistent with the ${ctx.name} brand palette (${ctx.colors.join(', ')}).${input.style ? ` Visual style: ${input.style}.` : ''}`;
  const count = Math.max(1, Math.min(4, input.count || 1));
  log(`Editando imagen con Nano Banana${count > 1 ? ` × ${count}` : ''}…`);
  const outputs: Array<{ dataUrl?: string; url?: string; buffer?: Buffer }> = [];
  for (let n = 0; n < count; n++) {
    if (n > 0) await sleep(2000);
    const img = await retry429(() => generateImagenImage(editPrompt, { aspectRatio: ar, references: [inl], model: 'gemini-3-pro-image-preview' }));
    outputs.push({ dataUrl: img.dataUrl });
  }
  await saveOutputs(outputs, ctx, input, prompt, { media: 'edit', model: 'vertex:gemini-3-pro-image-preview' });
}

// Pure-Opus vector asset (logo / icon / badge / infographic) — no raster model needed.
async function runSvg(input: Input, ctx: ReturnType<typeof brandContext>, prompt: string) {
  log('Generando SVG vectorial con Opus…');
  const svg = await generateBrandSVG(prompt, { name: ctx.name, colors: ctx.colors, fonts: ctx.fonts });
  if (!svg) throw new Error('Opus no pudo generar un SVG válido');
  const outDir = input.outDir || requiredRuntimePath('FYDESIGN_OUTPUT_ROOT');
  await mkdir(outDir, { recursive: true });
  const d = new Date();
  const stamp = `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}-${String(d.getHours()).padStart(2, '0')}${String(d.getMinutes()).padStart(2, '0')}`;
  const file = path.join(outDir, `${slug(prompt)}-${stamp}.svg`);
  await writeFile(file, svg, 'utf8');
  log(`  ✓ SVG → ${file}`);
  process.stdout.write(JSON.stringify({ ok: true, brand: ctx.name, media: 'svg', model: 'opus:svg', count: 1, dir: outDir, files: [file] }, null, 2) + '\n');
}

// Pure-Opus, on-brand HTML LANDING PAGE (no image model) — a real .html file the señor can open.
async function runLanding(input: Input, ctx: ReturnType<typeof brandContext>, prompt: string) {
  log(`Diseñando landing page HTML on-brand de ${ctx.name}…`);
  const logoDataUrl = await resolveLogoDataUrl(ctx.logo);
  const sys = 'You are an elite brand web designer and senior front-end engineer. Output ONE complete, production-quality, self-contained HTML document. No markdown, no code fences, no commentary — only the HTML, starting with <!doctype html>.';
  const logoLine = logoDataUrl
    ? 'In the nav/header use __LOGO__ (full lockup) at modest height; in the footer or any small/badge/avatar spot use __LOGOMARK__ (the symbol only). On dark sections use the _LIGHT_ variants. Keep the EXACT tokens (swapped for the real logos).'
    : `No logo file — render "${ctx.name}" as an elegant wordmark.`;
  const ask = `BRAND: ${ctx.name}
BRAND COLORS (use EXACTLY as the palette): ${ctx.colors.join(', ')} ${ctx.brandColors}
FONTS: ${ctx.fonts || 'Playfair Display + Inter + JetBrains Mono'}${styleBlock()}${logoBlock()}
PRODUCT INFO: ${ctx.info || '(infer from the brand)'}
REAL COPY / SCREENS:
${ctx.screens || '(none)'}

TASK: ${prompt}

Design a COMPLETE, long, content-RICH, fully responsive LANDING PAGE as a SINGLE self-contained HTML file:
- Load fonts from Google Fonts (<link>): Playfair Display, Inter, JetBrains Mono.
- Rich sections (mucho contenido): sticky nav (logo + CTA); a striking hero (mono eyebrow, big Playfair headline with an italic accent word, subhead, primary CTA); 4-6 feature/value cards; a "cómo funciona" steps section; a benefits band; QUALITATIVE social proof (NEVER invent numbers, follower counts, %, prices or fake quotes); a "para quién es" section; an FAQ (use <details> accordions); a strong final CTA; a footer with logo + links.
- Apply the EXACT brand palette and the STYLE GUIDE above: light/editorial, rosa/vino + dorado on cream, soft rounded cards, wine-tinted shadows, rose gradient on primary buttons/pills; Playfair display + Inter body + JetBrains Mono labels.
- ${logoLine}
- Persuasive Spanish (LatAm) copy, premium and warm. Subtle hover states, smooth scroll, mobile-responsive. Inline ALL CSS in a <style> block; no build step, no external JS required.
Output ONLY the HTML document.`;

  let html = (await callAI(ask, { system: sys, maxTokens: 20000 }) || '').trim();
  html = html.replace(/^```[a-z]*\s*\n?/i, '').replace(/\n?```\s*$/i, '').trim();   // strip stray fences
  html = applyLogos(html);
  if (!/<html|<!doctype/i.test(html)) throw new Error('Opus no devolvió un HTML de landing válido');

  const outDir = input.outDir || requiredRuntimePath('FYDESIGN_OUTPUT_ROOT');
  await mkdir(outDir, { recursive: true });
  const file = path.join(outDir, `${slug(prompt)}.html`);
  await writeFile(file, html, 'utf8');
  log(`  ✓ Landing → ${file} (${(html.length / 1024).toFixed(0)} KB)`);
  emitProgress(1, 1, 'landing');
  // Edecán entrega el artefacto en chat; el motor nunca abre aplicaciones ni
  // ejecuta una cadena de shell por su cuenta.
  process.stdout.write(JSON.stringify({ ok: true, brand: ctx.name, media: 'landing', model: 'opus:html', count: 1, dir: outDir, files: [file] }, null, 2) + '\n');
}

// Generate a cohesive multi-piece campaign of finished posts.
async function runCampaign(input: Input, ctx: ReturnType<typeof brandContext>, prompt: string) {
  const size = SIZES[input.platform || 'instagram-feed'] || SIZES['instagram-feed'];
  const aspect = input.aspectRatio || (size.w === size.h ? '1:1' : size.h > size.w ? '9:16' : '16:9');
  const n = Math.max(2, Math.min(6, input.count || 4));

  log(`Planeando campaña de ${n} piezas con el modelo configurado…`);
  const sys = `You are a brand campaign director AND an expert performance-creative copywriter. Plan a cohesive ${n}-piece social campaign with ONE consistent visual style across all pieces (narrative arc: hook → benefit → proof → CTA — this maps to the "define distinct angles" step of ad-creative strategy: hook = curiosity/pain angle, benefit = outcome angle, proof = social-proof angle, cta = urgency/action angle). Each piece is a finished ad. Vary word choice, specificity, and tone piece-to-piece so the set doesn't read as four versions of the same line. NEVER invent statistics, numbers, follower/member counts, percentages, prices or testimonials — use only real facts from the brand info/brief; if you lack a real figure, use qualitative copy (e.g. "creadores de verdad", not "2.500 creadores"). The "proof" piece must convey credibility WITHOUT fabricated metrics — pull real language from the brief/brand info if it contains a genuine testimonial or reviewer quote; otherwise lean on a concrete qualitative claim, not an invented number. Return STRICT JSON only.`;
  const ask = `BRAND: ${ctx.name}
COLORS: ${ctx.colors.join(', ')} ${ctx.brandColors}
INFO: ${ctx.info || '(infer from brand + brief)'}${styleBlock()}${feedBlock()}
BRIEF: ${prompt}

Return JSON: {
  "sharedStyle": "one consistent visual-style sentence applied to EVERY piece",
  "pieces": [ { "role": "hook|benefit|proof|cta", "imagePrompt": "detailed image prompt in English, with clean negative space for a text overlay and NO readable text in the image", "headline": "punchy, max 6 words, in the brief language — specific and benefit-led, not a vague label", "subtext": "one short line — a proof point, an objection handled, or CTA reinforcement, never a headline repeat", "cta": "2-3 word CTA, a real action verb" } ],
  "caption": "campaign caption in the brief language",
  "hashtags": ["5-8 hashtags"]
}`;
  type CampaignPlan = { strategy?: string; sharedStyle?: string; pieces?: Array<{ role?: string; imagePrompt?: string; headline?: string; subtext?: string; cta?: string }>; caption?: string; hashtags?: string[] };
  let plan: CampaignPlan | null = null;
  if (input.godMode) {
    log('🌐 God Mode: Opus investigando marca, competencia y tendencias en la web…');
    plan = await planStrategy({ brandName: ctx.name, repo: input.repo, brief: prompt, pieces: n, colors: ctx.colors, info: ctx.info }).catch(() => null);
    if (plan?.strategy) log(`Estrategia de Opus: ${plan.strategy.slice(0, 160)}`);
  }
  if (!plan) plan = await callAIJSON<CampaignPlan>(ask, { system: sys, maxTokens: 4000 });
  if (!plan?.pieces?.length) throw new Error('El proveedor de texto no devolvió piezas de campaña');

  const logoDataUrl = await resolveLogoDataUrl(ctx.logo);
  const references: Array<{ data: string; mimeType: string }> = [];
  if (input.quality === 'brand' && !input.noRefs) {
    for (const u of pickRefs(ctx)) { const ri = await urlToInline(u); if (ri) references.push(ri); }
  }

  const pieces = plan.pieces.slice(0, n);
  const outputs: Array<{ dataUrl?: string; url?: string; buffer?: Buffer }> = [];
  let model = '';
  for (let i = 0; i < pieces.length; i++) {
    const p = pieces[i];
    if (i > 0) await sleep(4000); // breathe between calls — Imagen has a low per-minute quota
    const ip = `${p.imagePrompt || prompt}${plan.sharedStyle ? `\n\nStyle: ${plan.sharedStyle}` : ''}${input.style ? `\n\nVisual style: ${input.style}.` : ''}`;
    log(`Pieza ${i + 1}/${pieces.length} (${p.role || 'pieza'})…`);
    // 1) Base photo with the no-text policy + Opus VERIFYING no garbage text crept in.
    const r = await genCleanVertexImage(ip, input.quality, aspect, references);
    model = r.model;
    // 2) Opus SEES the photo, composes the overlay in its real negative space, renders,
    //    self-critiques and refines (≤2) — same eyes treatment as single posts.
    const copy = { headline: p.headline, subtext: p.subtext, cta: p.cta };
    outputs.push({ buffer: await composePostWithVision(r.dataUrl, ctx, copy, logoDataUrl, size) });
    emitProgress(i + 1, pieces.length, p.role || `post ${i + 1}`);
  }

  await saveOutputs(outputs, ctx, input, prompt, {
    media: 'campaign', model, caption: plan.caption, hashtags: plan.hashtags,
    subdir: `campaign-${slug(prompt)}`, concept: plan.sharedStyle,
  });
}

// ── Real generative media — image via Vertex (cheap) or Muapi, video via Muapi ─
// The configured text provider writes an on-brand prompt; a media model renders pixels.
// Images default to Vertex (Imagen 4 / Gemini-image, already configured & cheap);
// Muapi handles video and "elevated" image models (Flux, Midjourney, Seedream…).
type MediaPlan = { mediaPrompt?: string; headline?: string; subtext?: string; cta?: string; caption?: string; hashtags?: string[]; format?: string; width?: number; height?: number };
type Out = { dataUrl?: string; url?: string; buffer?: Buffer; tag?: string };

// Generate (and, for posts, overlay) the design at ONE size. Returns its outputs.
async function generateForSize(
  input: Input,
  ctx: ReturnType<typeof brandContext>,
  plan: MediaPlan,
  target: { name: string; w: number; h: number },
  isVideo: boolean,
  isPost: boolean,
): Promise<{ outputs: Out[]; model: string; cost: { amount_usd?: number } | null }> {
  const size = { w: target.w, h: target.h };
  const aspect = input.aspectRatio || (size.w === size.h ? '1:1' : size.h > size.w ? '9:16' : '16:9');
  const mediaPrompt = plan.mediaPrompt || '';

  let usedModel = '';
  let cost: { amount_usd?: number } | null = null;
  const outputs: Out[] = [];

  if (isVideo) {
    const { hasMuapi, generateVideo } = await import('../src/lib/ai/muapi-client');
    if (!hasMuapi()) throw new Error('Falta conectar Muapi para generar video. Puedes usar MUAPI_SANDBOX=1 solo para validar el cableado sin un artefacto real.');
    const res = await generateVideo(mediaPrompt, { model: input.model, duration: input.duration, extra: { aspect_ratio: aspect } });
    usedModel = `muapi:${res.model}`;
    cost = res.cost || null;
    res.outputs.forEach((u) => outputs.push({ url: u }));
  } else if (input.provider === 'openai') {
    if (!hasOpenAI()) throw new Error('Falta OPENAI_API_KEY para provider="openai".');
    const oaSize = size.w === size.h ? '1024x1024' : (size.h > size.w ? '1024x1536' : '1536x1024');
    const count = Math.max(1, Math.min(4, input.count || 1));
    log(`Imagen vía OpenAI gpt-image-1 @ ${size.w}×${size.h}…`);
    for (let n = 0; n < count; n++) {
      if (n > 0) await sleep(1500);
      const img = await generateGptImage(mediaPrompt, { size: oaSize });
      outputs.push({ dataUrl: img.dataUrl });
    }
    usedModel = 'openai:gpt-image-1';
  } else if (input.provider === 'fal') {
    if (!hasFal()) throw new Error('Falta FAL_KEY para provider="fal".');
    const count = Math.max(1, Math.min(4, input.count || 1));
    log(`Imagen vía fal.ai (${input.model || process.env.FAL_IMAGE_MODEL || 'fal-ai/flux/dev'}) @ ${size.w}×${size.h}…`);
    for (let n = 0; n < count; n++) {
      if (n > 0) await sleep(1500);
      const res = await generateFalImage(mediaPrompt, { model: input.model, aspectRatio: aspect });
      res.outputs.forEach((u) => outputs.push({ url: u }));
    }
    usedModel = `fal:${input.model || process.env.FAL_IMAGE_MODEL || 'fal-ai/flux/dev'}`;
  } else {
    const muapiImageModel = /^(flux|seedream|bytedance|midjourney|gpt4o|gpt-image|hidream|reve|qwen|wan|ideogram|nano-banana|google-imagen)/i;
    const wantMuapi = input.provider === 'muapi' || (!!input.model && muapiImageModel.test(input.model));
    const { hasVertexCredentials, generateImagenImage } = await import('../src/lib/ai/imagen-client');

    if (!wantMuapi && input.provider !== 'muapi' && hasVertexCredentials()) {
      const IMAGEN4: Record<string, string> = {
        ultra: 'imagen-4.0-ultra-generate-001',
        standard: 'imagen-4.0-generate-001',
        fast: 'imagen-4.0-fast-generate-001',
      };
      const q = input.quality || 'standard';
      const vModel = q === 'brand'
        ? (process.env.GOOGLE_PREMIUM_IMAGE_MODEL || 'gemini-3-pro-image-preview')
        : (IMAGEN4[q] || IMAGEN4.standard);
      const ar = (['1:1', '16:9', '9:16', '4:3', '3:4'].includes(aspect) ? aspect : '1:1') as '1:1' | '16:9' | '9:16' | '4:3' | '3:4';

      const references: Array<{ data: string; mimeType: string }> = [];
      if (!input.noRefs && vModel.includes('gemini')) {
        for (const u of pickRefs(ctx)) { const inl = await urlToInline(u); if (inl) references.push(inl); }
      }
      const genPrompt = references.length
        ? `${mediaPrompt}\n\nIMPORTANT — reference images are attached: the brand's logo variants come first (reproduce the brand mark accurately wherever it appears — never distort or recolor it; use the compact SYMBOL for small placements like app icons, the full lockup only at larger sizes); any REAL app screenshots show the exact UI to depict on device screens. Keep everything strictly on-brand.`
        : mediaPrompt;
      if (references.length) log(`Referencias de marca: ${references.length} imagen(es)`);

      const count = Math.max(1, Math.min(4, input.count || 1));
      log(`Imagen on-brand vía Vertex (${vModel}) @ ${size.w}×${size.h}${count > 1 ? ` × ${count}` : ''}…`);
      // Raw images (no overlay) MUST be text-free at the source — regenerate if Opus
      // sees baked garbage text. (Posts get the same guard later via composePostWithVision.)
      const verifyRawText = !isPost;
      const noTextSuffix = [
        ' Absolutely no text, letters, numbers or symbols anywhere — keep all surfaces blank or abstract.',
        ' ZERO text of any kind; any sign/screen/label must be empty, blurred, or turned away from camera.',
      ];
      for (let n = 0; n < count; n++) {
        if (n > 0) await sleep(3000);
        let img = await retry429(() => generateImagenImage(genPrompt, { aspectRatio: ar, references, model: vModel }));
        if (verifyRawText) {
          for (let t = 0; t < 2; t++) {
            const m = /^data:[^;]+;base64,([\s\S]+)$/.exec(img.dataUrl);
            if (!m) break;
            const p = tmpPng();
            try { await writeFile(p, Buffer.from(m[1], 'base64')); } catch { break; }
            if (await imageIsTextFree(p)) break;
            if (t === 1) { log('  ⚠ la imagen seguía con texto; uso la última versión'); break; }
            log(`  ⚠ texto basura en la imagen — regenero sin texto (intento ${t + 2}/3)`);
            await sleep(1500);
            img = await retry429(() => generateImagenImage(genPrompt + noTextSuffix[Math.min(t, noTextSuffix.length - 1)], { aspectRatio: ar, references, model: vModel }));
          }
        }
        outputs.push({ dataUrl: img.dataUrl });
      }
      usedModel = `vertex:${vModel}`;
    } else {
      const { hasMuapi, generateImage } = await import('../src/lib/ai/muapi-client');
      if (!hasMuapi()) throw new Error('No hay proveedor de imagen disponible (configura Vertex o MUAPI_API_KEY).');
      log(`Imagen on-brand vía Muapi (${input.model || process.env.MUAPI_IMAGE_MODEL || 'flux-2-dev'}) @ ${size.w}×${size.h}…`);
      const res = await generateImage(mediaPrompt, { model: input.model, aspectRatio: aspect });
      usedModel = `muapi:${res.model}`;
      cost = res.cost || null;
      res.outputs.forEach((u) => outputs.push({ url: u }));
    }
  }

  if (!outputs.length) throw new Error('El proveedor no devolvió ninguna salida');

  // Finished post: overlay brand logo + headline/CTA onto the generated image(s).
  if (isPost) {
    const logoDataUrl = await resolveLogoDataUrl(ctx.logo);
    const composed: Out[] = [];
    for (const o of outputs) {
      let baseDataUrl = o.dataUrl || '';
      if (!baseDataUrl && o.url) {
        const r = await fetch(o.url, { signal: AbortSignal.timeout(120_000) });
        if (r.ok) baseDataUrl = `data:image/png;base64,${Buffer.from(await r.arrayBuffer()).toString('base64')}`;
      }
      if (!baseDataUrl) continue;
      composed.push({ buffer: await composePostWithVision(baseDataUrl, ctx, plan, logoDataUrl, size) });
    }
    if (!composed.length) throw new Error('No pude componer el post (imagen base faltante)');
    return { outputs: composed, model: usedModel, cost };
  }

  // Raw images → crop/scale to the EXACT target dimensions (so "una historia" is truly 1080×1920).
  if (!isVideo) {
    const exact: Out[] = [];
    for (const o of outputs) {
      let dataUrl = o.dataUrl || '';
      if (!dataUrl && o.url) {
        const r = await fetch(o.url, { signal: AbortSignal.timeout(120_000) });
        if (r.ok) dataUrl = `data:image/png;base64,${Buffer.from(await r.arrayBuffer()).toString('base64')}`;
      }
      if (!dataUrl) { exact.push(o); continue; }
      exact.push({ buffer: await cropToSize(dataUrl, size.w, size.h) });
    }
    return { outputs: exact, model: usedModel, cost };
  }

  return { outputs, model: usedModel, cost };
}

async function runMedia(input: Input, ctx: ReturnType<typeof brandContext>, prompt: string) {
  const isVideo = input.media === 'video';
  const isPost = input.media === 'post';
  const kind = isVideo ? 'video' : 'image';
  // No explicit format? Let Opus infer the right format + exact pixel dimensions from the brief.
  const explicitSize = !!(input.platform || (input.sizes && input.sizes.length) || input.aspectRatio);

  // 1. The configured text provider writes one on-brand prompt reused across sizes.
  log(`Redactando prompt de ${isPost ? 'post' : kind} on-brand…`);
  const sys = `You are a brand art director AND an expert performance-creative copywriter (the discipline: headlines, descriptions, and primary text that drive clicks and conversions — not just pretty visuals). Write ONE vivid, detailed ${kind} generation prompt for a marketing ${isVideo ? 'clip' : 'image'}.${isPost ? ' This image will receive a TEXT OVERLAY afterwards — compose with clean negative space (calmer top or bottom area) for it.' : ''} Bake in the brand palette, mood and subject; photographic/cinematic quality. CRITICAL: the image generator hallucinates GARBAGE text (e.g. "HEGADLE MOPFLARD"), so the scene must contain ZERO readable text, words, numbers, labels, signage or UI — describe ONLY the visual (people, products, environment, light). All real text is added later as a CSS overlay; leave clean space for it. NEVER describe a phone screen, app, UI, interface, dashboard, mockup or screenshot in the prompt — the image generator turns those into garbage fake UIs. This image is a PHOTOGRAPHIC BACKGROUND ONLY; any app/phone screen is built afterwards in HTML/CSS as an overlay. If a device appears in the scene, its screen must be blank/off.${explicitSize ? '' : 'Also choose the best social/marketing FORMAT for the brief and its EXACT standard pixel dimensions (you know them — e.g. Instagram Story 1080x1920, Instagram post 1080x1080, YouTube thumbnail 1280x720, Facebook cover 1200x630, web banner 1600x900). '}
COPY RULES for headline/subtext/cta (performance-creative discipline):
- Pick ONE clear angle before writing — pain point, outcome, social proof, curiosity, comparison, urgency, identity, or contrarian. Don't blend angles into a vague headline.
- Specific beats vague ("Cut reporting time 75%" not "Save time"); benefits beat features; active voice beats passive; use a real number when you have one.
- Never invented numbers/stats/testimonials (existing hard rule) — if you lack a real figure, stay qualitative and specific about the FEELING or OUTCOME instead.
- Avoid unsubstantiated superlatives ("best," "leading," "top"), jargon, and clickbait the rest of the post can't back up.
- cta is an action verb phrase (e.g. "Start free," "Ver más," "Reserva ya") — never a generic "Learn more"/"Más información" unless nothing sharper fits.
Return STRICT JSON only.`;
  const postFields = isPost
    ? ',\n  "angle": "which single angle this headline uses: pain|outcome|social-proof|curiosity|comparison|urgency|identity|contrarian",\n  "headline": "punchy headline, max 6 words, in the brief language — specific and benefit-led, not a vague label",\n  "subtext": "one short supporting line — adds a proof point, handles an objection, or reinforces the CTA; never just repeats the headline",\n  "cta": "2-3 word call to action, a real action verb (not a generic \'learn more\')"'
    : '';
  const sizeFields = explicitSize
    ? ''
    : ',\n  "format": "the format name that best fits the brief",\n  "width": <exact pixel width as an integer>,\n  "height": <exact pixel height as an integer>';
  const ask = `BRAND: ${ctx.name}
COLORS: ${ctx.colors.join(', ')} ${ctx.brandColors}
INFO: ${ctx.info || '(infer from brand + brief)'}${styleBlock()}${feedBlock()}
BRIEF: ${prompt}

Return JSON: {
  "mediaPrompt": "detailed ${kind} prompt in English",
  "caption": "post caption in the brief's language",
  "hashtags": ["5-8 hashtags"]${postFields}${sizeFields}
}`;
  const plan = await callAIJSON<MediaPlan>(ask, { system: sys, maxTokens: 1500 });
  if (!plan?.mediaPrompt) throw new Error('El proveedor de texto no devolvió un prompt de medios válido');
  if (input.style) plan.mediaPrompt = `${plan.mediaPrompt}\n\nVisual style: ${input.style}.`;
  if (input.styleKey) { const { applyStyle } = await import('../src/lib/presets/catalog'); plan.mediaPrompt = applyStyle(plan.mediaPrompt, input.styleKey); }
  if (input.colorLock) { const { applyBrandColorLock } = await import('../src/lib/brand-aesthetic'); plan.mediaPrompt = applyBrandColorLock(plan.mediaPrompt, ctx.colors); }
  log(`Prompt: ${plan.mediaPrompt.slice(0, 140)}…`);

  // 2. Resolve target formats: explicit sizes/platform, else Opus-inferred dimensions.
  let targets: Array<{ name: string; w: number; h: number }>;
  if (input.sizes && input.sizes.length) {
    const names = input.sizes.includes('all') ? ['instagram-feed', 'instagram-story', 'tiktok', 'facebook', 'landscape'] : input.sizes;
    targets = names.map((n) => { const sz = SIZES[n] || SIZES['instagram-feed']; return { name: n, w: sz.w, h: sz.h }; });
  } else if (input.platform) {
    const sz = SIZES[input.platform] || SIZES['instagram-feed'];
    targets = [{ name: input.platform, w: sz.w, h: sz.h }];
  } else if (plan.width && plan.height && plan.width >= 240 && plan.height >= 240 && plan.width <= 4096 && plan.height <= 4096) {
    targets = [{ name: slug(plan.format || 'custom'), w: Math.round(plan.width), h: Math.round(plan.height) }];
    log(`Formato inferido por Opus: ${plan.format || 'custom'} → ${targets[0].w}×${targets[0].h}`);
  } else {
    const sz = SIZES['instagram-feed'];
    targets = [{ name: 'instagram-feed', w: sz.w, h: sz.h }];
  }
  const multi = targets.length > 1;

  const allOutputs: Out[] = [];
  let usedModel = '';
  let totalCost = 0;
  let hasCost = false;

  for (let s = 0; s < targets.length; s++) {
    const t = targets[s];
    if (multi) { log(`── Formato ${s + 1}/${targets.length}: ${t.name} (${t.w}×${t.h}) ──`); if (s > 0) await sleep(3000); }
    const r = await generateForSize(input, ctx, plan, t, isVideo, isPost);
    usedModel = r.model;
    if (r.cost?.amount_usd != null) { totalCost += r.cost.amount_usd; hasCost = true; }
    r.outputs.forEach((o) => { if (multi) o.tag = t.name; allOutputs.push(o); });
    emitProgress(s + 1, targets.length, t.name);
  }
  const cost = hasCost ? { amount_usd: totalCost } : null;
  if (hasCost) log(`Costo total: ~$${totalCost.toFixed(3)}`);

  // 3. Save to disk.
  await saveOutputs(allOutputs, ctx, input, prompt, {
    media: input.media || 'image',
    model: usedModel,
    cost,
    caption: plan.caption,
    hashtags: plan.hashtags,
    headline: plan.headline,
    concept: plan.mediaPrompt,
  });
}

// ── Higgsfield-class engines: directed video ads, AI influencers, photo-dump, batch ──
type VAspect = '1:1' | '16:9' | '9:16' | '4:3' | '3:4';
function aspectFor(input: Input): VAspect {
  const ar = input.aspectRatio || '';
  if (['1:1', '16:9', '9:16', '4:3', '3:4'].includes(ar)) return ar as VAspect;
  const p = input.platform || '';
  if (/story|tiktok|reel/.test(p)) return '9:16';
  if (/feed|square/.test(p)) return '1:1';
  if (/facebook|landscape|youtube/.test(p)) return '16:9';
  return '9:16'; // ads default to vertical
}

function brandBaseDir(input: Input, ctx: ReturnType<typeof brandContext>): string {
  return input.outDir || requiredRuntimePath('FYDESIGN_OUTPUT_ROOT');
}
function stampNow(): string {
  const d = new Date();
  return `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}-${String(d.getHours()).padStart(2, '0')}${String(d.getMinutes()).padStart(2, '0')}`;
}

// Finish a video deliverable already written to disk: caption sidecar + feed memory + result JSON.
async function finishVideoFile(
  outFile: string,
  ctx: ReturnType<typeof brandContext>,
  input: Input,
  prompt: string,
  meta: { media: string; model: string; cost?: { amount_usd?: number } | null; caption?: string; hashtags?: string[]; concept?: string },
) {
  const baseDir = brandBaseDir(input, ctx);
  if (meta.caption || (meta.hashtags && meta.hashtags.length)) {
    try { await writeFile(outFile.replace(/\.[^.]+$/, '') + '-caption.txt', `${meta.caption || ''}\n\n${(meta.hashtags || []).join(' ')}\n`); } catch { /* noop */ }
  }
  try {
    await appendFeedMemory(baseDir, { date: new Date().toISOString(), media: meta.media, brief: prompt, concept: (meta.concept || '').slice(0, 140), files: 1 });
  } catch { /* noop */ }
  // La capa de chat entrega el archivo; no se abre una app del sistema.
  process.stdout.write(JSON.stringify({
    ok: true, brand: ctx.name, media: meta.media, model: meta.model,
    count: 1, dir: path.dirname(outFile), files: [outFile], cost: meta.cost || null,
    caption: meta.caption || '', hashtags: meta.hashtags || [],
  }, null, 2) + '\n');
}

// Full directed, on-brand VIDEO AD: Opus shot list → image→video per shot → camera moves →
// overlays → transitions → voiceover + music → end card. The Higgsfield-class flagship.
// Ad Engine: derive niches + angles + outreach + report (Opus), generate variants (direct, opt-in).
async function runAdEngine(input: Input, ctx: ReturnType<typeof brandContext>, prompt: string) {
  const { planAdEngine } = await import('../src/lib/video/ad-engine');
  log(`🧠 Ad Engine — derivando nichos de alto intento para ${ctx.name}…`);
  const plan = await planAdEngine(ctx, prompt, { niches: input.adNiches });
  log(`✓ ${plan.niches.length} nichos: ${plan.niches.map((x) => x.name).join(' · ')}`);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const out: any = { ok: true, media: 'ad-engine', brand: ctx.name, niches: plan.niches, report: plan.report };

  if (input.adGenerate) {
    const { generateVideoDirect } = await import('../src/lib/ai/muapi-client');
    const { faststart } = await import('../src/lib/video/ffmpeg');
    const { findPreset } = await import('../src/lib/presets/marketing-studio');
    const { copyFile } = await import('node:fs/promises');
    const baseDir = brandBaseDir(input, ctx);
    await mkdir(baseDir, { recursive: true });
    const aspect = aspectFor(input);
    for (let i = 0; i < plan.niches.length; i++) {
      const niche = plan.niches[i];
      try {
        const p = findPreset(niche.format);
        const directPrompt = p ? p.scaffold(niche.videoPrompt) : niche.videoPrompt;
        log(`🎬 Variante ${i + 1}/${plan.niches.length} — ${niche.name} (${niche.format})…`);
        // Respect an explicit --model (any of the 3 valid direct models); otherwise the
        // preset's recommended model, then seedance. (Earlier this only honored omni/kling,
        // so an explicit seedance/auto silently fell back to the preset — fixed.)
        const directModel = (input.model === 'seedance' || input.model === 'omni' || input.model === 'kling')
          ? input.model : (p?.model || 'seedance');
        const res = await generateVideoDirect(directPrompt, {
          model: directModel as 'seedance' | 'omni' | 'kling',
          aspect: p?.aspect || aspect, duration: p?.duration,
        });
        const url = res.outputs?.[0];
        // Muapi can return completed-with-no-outputs on a genuine failure; treat that as an
        // error so it surfaces in the per-variant catch instead of silently vanishing.
        if (!url) throw new Error(`la generación no devolvió ningún video (${res.model || directModel})`);
        const dest = path.join(baseDir, `ad-${slug(niche.name)}-${stampNow()}-${i + 1}.mp4`);
        const tmpMp4 = path.join(os.tmpdir(), `fyd-ad-${stampNow()}-${i}-${Math.random().toString(36).slice(2, 8)}.mp4`);
        const dl = await fetch(url, { signal: AbortSignal.timeout(300_000) });
        if (!dl.ok) throw new Error(`descarga del video falló: HTTP ${dl.status}`);
        const buf = Buffer.from(await dl.arrayBuffer());
        if (buf.length === 0) throw new Error('descarga del video vacía (0 bytes)');
        await writeFile(tmpMp4, buf);
        try { await faststart(tmpMp4, dest); } catch { await copyFile(tmpMp4, dest).catch(async () => { await writeFile(dest, buf); }); }
        await unlink(tmpMp4).catch(() => undefined);
        niche.video = dest;
      } catch (e) { log(`  variante ${i + 1} falló (se continúa): ${e instanceof Error ? e.message : e}`); }
    }
    out.dir = baseDir;
  }
  process.stdout.write(JSON.stringify(out, null, 2) + '\n');
}

// Personal Clipper: long video → N vertical short clips (+ subs from YouTube auto-captions).
async function runClip(input: Input) {
  const { clipVideo } = await import('../src/lib/video/personal-clipper');
  const { copyFile } = await import('node:fs/promises');
  const src = input.videoUrl || input.videoFile;
  if (!src) throw new Error('clip: pasa "videoUrl" (YouTube/TikTok/…) o "videoFile" (ruta/URL del video).');
  log(`✂️ Clipper — ${String(src).slice(0, 80)} → ${input.clipCount || 3} clips 9:16…`);
  const res = await clipVideo(
    { url: input.videoUrl, file: input.videoFile },
    { count: input.clipCount, clipLengthSec: input.clipLength },
  );
  const baseDir = input.outDir || requiredRuntimePath('FYDESIGN_OUTPUT_ROOT');
  await mkdir(baseDir, { recursive: true });
  // Pair each copied destination with its source clip so a failed copy can't misalign the
  // JSON (skipping a clip used to shift every later file path by one and leave a trailing
  // undefined). Only successfully-copied clips appear in the output.
  const copied: Array<{ file: string; start: number; end: number; reason: string; hasSubs: boolean }> = [];
  for (let i = 0; i < res.clips.length; i++) {
    const c = res.clips[i];
    const dest = path.join(baseDir, `clip-${stampNow()}-${i + 1}.mp4`);
    try {
      await copyFile(c.file, dest);
      copied.push({ file: dest, start: c.start, end: c.end, reason: c.reason, hasSubs: c.hasSubs });
    } catch (e) { log(`  clip ${i + 1} no se pudo copiar (se omite): ${e instanceof Error ? e.message : e}`); }
    await unlink(c.file).catch(() => undefined);
  }
  const files = copied.map((c) => c.file);
  log(`✓ ${files.length} clips en ${baseDir}` + (res.usedTranscript ? ' (con subtítulos)' : ' (sin transcript → sin subs)'));
  process.stdout.write(JSON.stringify({
    ok: true, media: 'clip', count: files.length, dir: baseDir, files,
    usedTranscript: res.usedTranscript, sourceDurationSec: res.sourceDurationSec,
    clips: copied,
  }, null, 2) + '\n');
}

// Video Analyzer: "analyze this video, I want something like that" → deconstruct + recreate prompt.
async function runAnalyze(input: Input) {
  const { analyzeVideo } = await import('../src/lib/video/video-analyzer');
  const src = input.videoUrl || input.videoFile || input.inputImage;
  if (!src) throw new Error('analyze: pasa "videoUrl" (YouTube/TikTok/…) o "videoFile" (ruta/URL del video).');
  log(`🔎 Analizando video: ${String(src).slice(0, 90)}…`);
  const a = await analyzeVideo(
    { url: input.videoUrl, file: input.videoFile || input.inputImage },
    { frames: input.analyzeFrames },
  );
  log(`✓ Análisis listo — ${a.framesSeen} frames, ${a.durationSec.toFixed(1)}s · concepto: ${a.concept.slice(0, 80)}`);
  process.stdout.write(JSON.stringify({
    ok: true, media: 'analyze', analysis: a, recreatePrompt: a.recreatePrompt,
  }, null, 2) + '\n');
}

// Instant mode: a website URL → an EPHEMERAL brand (Opus reads the site) → a full
// marketing pack (posts, carousel, story, ad stills + one direct short video). No brand
// registration. Writes its own files and emits ONE result JSON.
// ── CREATIVE DIRECTOR ────────────────────────────────────────────────────────
// Opus with MAXIMUM creative latitude: it SEES the real product/brand (vision via the
// configured vision-capable model) and decides the concept, composition and copy.
// many pieces, each format, the narrative, and the exact on-image copy. The only two
// hard rules left are the user's own non-negotiables: TRUTH (anti-invention) and the
// TECHNICAL no-baked-text mechanic (words are composited later as crisp typography, not
// rendered by the image model which garbles letters). No creative cage.
interface FreePiece { role?: string; imagePrompt?: string; headline?: string; subtext?: string; cta?: string; aspect?: string; allowUi?: boolean; allowText?: boolean }
interface FreePlan { concept?: string; pieces?: FreePiece[]; videoKeyframePrompt?: string; videoPrompt?: string; videoAllowUi?: boolean; videoAllowText?: boolean; caption?: string; hashtags?: string[] }

async function freeDirectorPlan(
  ctx: ReturnType<typeof brandContext>,
  brief: string,
  visionPaths: string[],
  opts: { wantVideo?: boolean } = {},
): Promise<FreePlan> {
  const seeing = visionPaths.length
    ? `\n\nYou have ${visionPaths.length} REAL image(s) of the product/subject to promote — VIEW each one and study it (exact product, materials, colours, who it's for):\n${visionPaths.map((p, i) => `  • image ${i + 1}: ${p}`).join('\n')}`
    : '';
  const system = `You are a world-class creative director and copywriter with TOTAL creative freedom — the level of Nike, Apple, Aesop. You are making a REAL marketing pack with a message and a through-line, never generic disconnected stock images.

YOU decide everything: the big idea, how many pieces, what each piece is, the art direction, the narrative across the pieces, and the exact on-image copy (headline / subtext / CTA) — or no copy for a purely visual piece. Think like a human director, not a prompt generator.

Only TWO hard rules (everything else is entirely your call):
1) TRUTH — never invent prices, statistics, percentages, follower/customer counts, awards or testimonials. Use only what you can SEE or genuinely know; if you lack a real figure, stay qualitative.
2) NO TEXT IN THE GENERATED IMAGE BY DEFAULT — image models garble letters, so by default the "imagePrompt" describes ONLY the photo (people / product / scene / light / mood) with calm negative space where copy will go; put ALL words in the headline/subtext/cta fields (they are composited later as crisp typography). However, if a piece specifically requires showing a logo, sign, text, or device interface in the scene (for example, a phone screen displaying a fintech chart or a brand logo mounted on a wall), describe it clearly in "imagePrompt" and set "allowText": true and/or "allowUi": true for that piece.

Return STRICT JSON only — no markdown, no commentary.`;
  const ask = `BRAND: ${ctx.name}
${ctx.colors.length ? `KNOWN COLOURS: ${ctx.brandColors}` : '(no palette given — derive it from what you SEE)'}
${ctx.info ? `WHAT THEY DO (real facts only): ${ctx.info}` : ''}
${ctx.fonts ? `FONTS: ${ctx.fonts}` : ''}
USER BRIEF: ${brief || '(none — you decide the most effective marketing pack)'}${seeing}

Design the pack and return JSON:
{
  "concept": "the big creative idea / through-line, one sentence",
  "pieces": [
    { "role": "what this piece is (your words)", "imagePrompt": "vivid English photo description — if a logo, text, or device screen is explicitly required, describe its realistic placement in the scene; otherwise, keep it text-free and UI-free", "headline": "on-image headline in the brief's language (or omit)", "subtext": "optional one short line", "cta": "optional 2-4 word CTA", "aspect": "1:1 | 9:16 | 16:9 | 4:3 | 3:4", "allowText": true|false, "allowUi": true|false }
  ],${opts.wantVideo ? `
  "videoKeyframePrompt": "photo description for the first frame of a short video — if a logo, text, or device screen is required, describe its realistic placement in the scene; otherwise, keep it text-free and UI-free",
  "videoAllowText": true|false,
  "videoAllowUi": true|false,
  "videoPrompt": "what happens in the short video: motion + camera move + scene, realistic and faithful to the keyframe",` : ''}
  "caption": "social caption in the brief's language",
  "hashtags": ["5-8 hashtags"]
}
You have full freedom over how many pieces and their formats. Make it sell.`;
  const raw = await callAI(`${system}\n\n${ask}`, { maxTokens: 8000 });
  let s = raw.trim();
  const fence = s.match(/```(?:json)?\s*([\s\S]*?)```/i);
  if (fence) s = fence[1].trim();
  const b = s.indexOf('{');
  if (b > 0) s = s.slice(b);
  const e = s.lastIndexOf('}');
  if (e >= 0) s = s.slice(0, e + 1);
  let plan: FreePlan;
  try { plan = JSON.parse(s) as FreePlan; }
  catch { throw new Error('el director no devolvió un plan JSON válido; revisa el modelo principal configurado.'); }
  return plan;
}

async function runInstant(input: Input) {
  const hasUrl = !!input.siteUrl;
  const hasRefs = !!(input.refImages && input.refImages.length);
  if (!hasUrl && !hasRefs) {
    throw new Error('instant: pasa "siteUrl" (URL de tu marca) y/o "refImages" (fotos reales del producto a promocionar).');
  }

  // ── Brand context: from the site (Opus saw it) OR a minimal ctx that Opus fills by SEEING the product.
  let ctx: ReturnType<typeof brandContext>;
  let ab: import('../src/lib/auto-brand').AutoBrand | null = null;
  if (hasUrl) {
    const { deriveBrandFromUrl, autoBrandToCtx } = await import('../src/lib/auto-brand');
    log(`🌐 Instant — derivando marca de ${input.siteUrl} …`);
    ab = await deriveBrandFromUrl(input.siteUrl!, { brandName: input.brandName });
    ctx = autoBrandToCtx(ab);
    log(`✓ Marca: "${ctx.name}" · ${ctx.colors.join(' ')}${ab.screenshot ? ' · (Opus vio el sitio)' : ''}`);
  } else {
    ctx = { name: input.brandName || 'Tu Marca', colors: [], brandColors: '', fonts: '', screens: '', info: '', logo: '', assets: [] };
    log('📦 Instant — sin marca registrada; Opus dirigirá VIENDO el producto');
  }

  const brief = (input.prompt || '').trim();
  const sections = (Array.isArray(input.suite) && input.suite.length ? input.suite : ['posts', 'story', 'video']).map((s) => String(s).toLowerCase());
  const wantVideo = sections.includes('video');
  const baseDir = input.outDir || requiredRuntimePath('FYDESIGN_OUTPUT_ROOT');
  await mkdir(baseDir, { recursive: true });

  // ── VISION: load the real product bytes for the configured vision model.
  const visionPaths: string[] = [];
  const tmpRefs: string[] = [];
  const productRefs: Array<{ data: string; mimeType: string }> = [];
  for (const u of (input.refImages || []).slice(0, 6)) {
    const inl = await loadImageInline(u).catch(() => null);
    if (!inl) continue;
    const p = tmpPng();
    try { await writeFile(p, Buffer.from(inl.data, 'base64')); visionPaths.push(p); tmpRefs.push(p); productRefs.push(inl); } catch { /* skip */ }
  }
  const cleanupTmp = async () => { await Promise.all(tmpRefs.map((p) => unlink(p).catch(() => undefined))); };

  // ── The configured director sees the product and decides the pack + copy.
  log(`🎬 Opus (director libre) ${visionPaths.length ? `viendo ${visionPaths.length} imagen(es) del producto, ` : ''}planeando el pack…`);
  let plan: FreePlan;
  try { plan = await freeDirectorPlan(ctx, brief, visionPaths, { wantVideo }); }
  catch (e) { await cleanupTmp(); throw e; }
  log(`💡 Concepto: ${plan.concept || '(sin concepto)'} · ${plan.pieces?.length || 0} pieza(s)`);

  // ── DRY-RUN: return the plan and stop before media generation.
  if (input.dryRun) {
    await cleanupTmp();
    process.stdout.write(JSON.stringify({
      ok: true, media: 'instant', dryRun: true,
      brand: { name: ctx.name, colors: ctx.colors, ...(ab ? { sourceUrl: ab.sourceUrl, description: ab.description } : {}) },
      plan,
    }, null, 2) + '\n');
    return;
  }

  // ── References for generation: the REAL product images + the logo (so the product itself
  //    appears on-brand; Nano Banana / quality:'brand' honours references).
  const references: Array<{ data: string; mimeType: string }> = [...productRefs];
  if (!input.noRefs && ctx.logo) { const li = await loadImageInline(ctx.logo).catch(() => null); if (li) references.push(li); }
  const logoDataUrl = ctx.logo ? await resolveLogoDataUrl(ctx.logo).catch(() => '') : '';
  const quality = (input.quality as Input['quality']) || 'brand';
  // Image engine: GPT Image 2 (OpenAI via Muapi) by default for top photographic quality;
  // set FY_IMAGE_ENGINE=vertex to fall back to Gemini 3 Pro Image (Nano Banana Pro).
  const imgEngine: 'vertex' | 'gpt-image-2' = process.env.FY_IMAGE_ENGINE === 'vertex' ? 'vertex' : 'gpt-image-2';
  log(`🖌️  motor de imagen: ${imgEngine}`);
  const sizeFor = (a?: string) => a === '9:16' ? { w: 1080, h: 1920 } : a === '16:9' ? { w: 1920, h: 1080 } : a === '4:3' ? { w: 1440, h: 1080 } : a === '3:4' ? { w: 1080, h: 1440 } : { w: 1080, h: 1080 };

  const files: string[] = [];
  const pieceOut: Array<Record<string, unknown>> = [];
  let cost = 0;

  // ── Render each piece: clean photo (Opus verifies it's text-free) → Opus SEES it, composes
  //    the headline/CTA/logo overlay in its real negative space, then SELF-CRITIQUES and refines.
  const pieces = (plan.pieces || []).filter((p) => p && p.imagePrompt);
  for (let i = 0; i < pieces.length; i++) {
    const p = pieces[i];
    if (i > 0) await sleep(3000); // breathe between Vertex calls (low per-minute quota)
    const aspect = ['1:1', '9:16', '16:9', '4:3', '3:4'].includes(p.aspect || '') ? p.aspect! : '1:1';
    const size = sizeFor(aspect);
    log(`🖼️  pieza ${i + 1}/${pieces.length} — ${p.role || 'post'}…`);
    try {
      const img = await genCleanVertexImage(p.imagePrompt!, quality, aspect, references, 2, imgEngine, p.allowUi, p.allowText);
      const buf = await composePostWithVision(img.dataUrl, ctx, { headline: p.headline, subtext: p.subtext, cta: p.cta }, logoDataUrl, size);
      const dest = path.join(baseDir, `pieza-${i + 1}-${slug(p.role || 'post')}-${stampNow()}.png`);
      await writeFile(dest, buf);
      files.push(dest);
      pieceOut.push({ file: dest, role: p.role, headline: p.headline, subtext: p.subtext, cta: p.cta, aspect });
    } catch (e) { log(`  pieza ${i + 1} falló (se continúa): ${e instanceof Error ? e.message : e}`); }
  }

  // ── Video: keyframe with the REAL product refs (faithful try-on/compositing) → image-to-video.
  if (wantVideo && (plan.videoPrompt || plan.videoKeyframePrompt)) {
    try {
      const { generateVideoDirect } = await import('../src/lib/ai/muapi-client');
      const { hostStillForMuapi } = await import('../src/lib/ai/brand-image');
      const { faststart } = await import('../src/lib/video/ffmpeg');
      let refUrls: string[] = [];
      if (plan.videoKeyframePrompt && references.length) {
        log('🎬 keyframe del video (con el producto real)…');
        let kfPrompt = plan.videoKeyframePrompt;
        const isFashion = input.refImages && input.refImages.length > 0 && /clothing|garment|shirt|dress|wear|model|fashion|pants|skirt|jacket|hoodie|outfit|ropa|prenda|modelo/i.test(brief);
        if (isFashion) {
          kfPrompt += ' The model is wearing a simple solid neutral t-shirt.';
        }
        let kf = await genCleanVertexImage(kfPrompt, 'brand', '9:16', references, 2, imgEngine, plan.videoAllowUi, plan.videoAllowText);
        if (isFashion && input.refImages && input.refImages.length > 0) {
          log('👗 ejecutando VTON outfit swap para el keyframe de video instantáneo…');
          try {
            const { swapOutfit } = await import('../src/lib/edit-pack');
            const garmentRef = input.refImages[0];
            const garmentUrl = await hostStillForMuapi(garmentRef);
            const swapRes = await swapOutfit(kf.dataUrl, 'wear the reference garment', garmentUrl);
            if (swapRes.url) {
              log(`👗 VTON exitoso para keyframe de video → ${swapRes.url}`);
              kf = { dataUrl: swapRes.url, model: `${kf.model} + ${swapRes.model}` };
            }
          } catch (e) {
            log(`  VTON outfit swap falló en keyframe, uso still base: ${e instanceof Error ? e.message : e}`);
          }
        }
        const hosted = await hostStillForMuapi(kf.dataUrl).catch(() => '');
        if (hosted && /^https?:\/\//i.test(hosted)) refUrls = [hosted];
      }
      log(`🎬 video (${refUrls.length ? 'i2v fiel desde el keyframe' : 't2v'}, seedance 9:16)…`);
      const vres = await generateVideoDirect(plan.videoPrompt || plan.videoKeyframePrompt || brief, {
        model: 'seedance', aspect: '9:16', duration: input.duration || 5, refUrls,
      });
      const url = vres.outputs?.[0];
      if (!url) throw new Error(`la generación no devolvió ningún video (${vres.model || 'seedance'})`);
      const dest = path.join(baseDir, `video-${stampNow()}.mp4`);
      const tmpMp4 = path.join(os.tmpdir(), `fyd-instant-${stampNow()}-${Math.random().toString(36).slice(2, 8)}.mp4`);
      const dl = await fetch(url, { signal: AbortSignal.timeout(300_000) });
      if (!dl.ok) throw new Error(`descarga del video falló: HTTP ${dl.status}`);
      const vbuf = Buffer.from(await dl.arrayBuffer());
      if (vbuf.length === 0) throw new Error('descarga del video vacía (0 bytes)');
      await writeFile(tmpMp4, vbuf);
      try { await faststart(tmpMp4, dest); } catch { await writeFile(dest, vbuf); }
      await unlink(tmpMp4).catch(() => undefined);
      files.push(dest);
      pieceOut.push({ file: dest, role: 'video' });
      if (vres.cost?.amount_usd) cost += vres.cost.amount_usd;
    } catch (e) { log(`  video falló (se continúa con el pack de imágenes): ${e instanceof Error ? e.message : e}`); }
  }

  await cleanupTmp();

  // ── Optional: persist the URL-derived identity as a reusable brand.
  let savedBrandId: string | null = null;
  if (input.saveAsBrand && ab) {
    try {
      const db = await import('../src/lib/db');
      const id = `${slug(ctx.name)}-${stampNow()}`;
      await db.saveBrandConfig({
        id, companyName: ctx.name, companyBlurb: ab.description.slice(0, 200),
        repoUrl: ab.sourceUrl, logoUrl: ctx.logo || null, brandNotes: ab.voice || null,
        analysisJson: JSON.stringify({ appName: ctx.name, description: ab.description, theme: { primaryColor: ctx.colors[0], accentColors: ctx.colors.slice(1) }, brand_fonts: ctx.fonts, brand_colors: ctx.colors, valueProps: ab.valueProps, screens: [] }),
        status: 'ready',
      });
      savedBrandId = id;
      log(`💾 Marca guardada como "${id}" (reutilizable).`);
    } catch (e) { log(`  no se pudo guardar la marca (el pack ya se generó): ${e instanceof Error ? e.message : e}`); }
  }

  if (files.length === 0) throw new Error('instant: no se generó ninguna pieza; revisa la conexión de imagen/visión y el modelo principal configurado.');

  process.stdout.write(JSON.stringify({
    ok: true, media: 'instant', dir: baseDir, files, count: files.length, cost: cost || null, savedBrandId,
    concept: plan.concept || '', caption: plan.caption || '', hashtags: plan.hashtags || [],
    brand: { name: ctx.name, colors: ctx.colors, logo: ctx.logo, ...(ab ? { sourceUrl: ab.sourceUrl, description: ab.description, voice: ab.voice, industry: ab.industry } : {}) },
    pieces: pieceOut,
  }, null, 2) + '\n');
}

async function runVideoAd(input: Input, ctx: ReturnType<typeof brandContext>, prompt: string, productInfo?: string) {
  const { directVideoAd } = await import('../src/lib/video/director');
  const { assembleVideoAd } = await import('../src/lib/video/assemble');
  const { ffmpegAvailable } = await import('../src/lib/video/ffmpeg');
  const { videoI2V } = await import('../src/lib/video/models');
  if (!ffmpegAvailable()) throw new Error('ffmpeg no está disponible (instálalo: brew install ffmpeg) — es necesario para ensamblar el anuncio de video.');

  const aspect = aspectFor(input);

  // ── ROUTING ──────────────────────────────────────────────────────────────
  // DEFAULT = DIRECT: the (optionally Pensar-mejor-refined) prompt + any reference images
  // (a logo, a product) go STRAIGHT to ONE video model that builds the whole video end-to-
  // end and integrates the references itself — NO Opus director, NO keyframe→animate stitch,
  // NO manual compositing, references OPTIONAL. (engine: seedance|omni|kling|direct|auto)
  // 'avatar' = real-person lip-sync; 'keyframe' = the classic Opus multi-shot director.
  let directModel: 'seedance' | 'omni' | 'kling' | null =
    (input.engine === 'avatar' || input.engine === 'keyframe') ? null
      : (input.engine === 'omni' || input.engine === 'kling' || input.engine === 'seedance') ? input.engine
        : (input.engine === 'direct') ? (input.model === 'omni' || input.model === 'kling' ? input.model : 'seedance')
          : 'seedance';

  const isFashion = input.refImages && input.refImages.length > 0 && /clothing|garment|shirt|dress|wear|model|fashion|pants|skirt|jacket|hoodie|outfit|ropa|prenda|modelo/i.test(prompt);
  if (isFashion) {
    log(`  👗 Campaña de moda detectada (prenda en referencias) — forzando motor 'keyframe' para evitar distorsión.`);
    directModel = null;
  }

  if (directModel) {
    const { generateVideoDirect } = await import('../src/lib/ai/muapi-client');
    const { faststart } = await import('../src/lib/video/ffmpeg');
    const { hostStillForMuapi } = await import('../src/lib/ai/brand-image');
    const { findPreset } = await import('../src/lib/presets/marketing-studio');

    // ── Marketing Studio preset: "pick a format, skip the setup" ──────────────
    // The format wraps the plain brief into its directorial style and sets the
    // recommended model / aspect / duration — then it all goes DIRECT to one model.
    const preset = findPreset(input.preset);
    let directPrompt = prompt;
    let directAspect: string = aspect;
    let directDuration = input.duration;
    if (preset) {
      directPrompt = preset.scaffold(prompt);
      if (input.duration == null) directDuration = preset.duration;
      if (!input.aspectRatio && !input.platform) directAspect = preset.aspect;
      // honor the format's recommended model unless the user explicitly picked one
      if (!input.model && (!input.engine || input.engine === 'auto')) directModel = preset.model;
      log(`🎬 Formato "${preset.name}" (${preset.desc})`);
    }

    const refs: string[] = [];
    for (const r of (input.refImages || []).slice(0, 7)) { try { refs.push(await hostStillForMuapi(r)); } catch { /* skip */ } }
    log(`🎬 Modo DIRECTO (${directModel}) — un solo prompt${refs.length ? ` + ${refs.length} referencia(s) (logo/producto)` : ' · text-to-video (sin foto)'} → el modelo genera TODO de una, sin pipeline…`);
    const res = await generateVideoDirect(directPrompt, { model: directModel, refUrls: refs, aspect: directAspect, duration: directDuration });
    const url = res.outputs?.[0];
    if (!url) throw new Error(`El modelo ${directModel} no devolvió video.`);
    const baseDir = brandBaseDir(input, ctx);
    await mkdir(baseDir, { recursive: true });
    const outFile = path.join(baseDir, `${slug(prompt)}-${stampNow()}-ad.mp4`);
    const tmpMp4 = path.join(os.tmpdir(), `fyd-direct-${stampNow()}-${Math.floor(Math.random() * 1e6)}.mp4`);
    const dl = await fetch(url, { signal: AbortSignal.timeout(300_000) });
    const buf = Buffer.from(await dl.arrayBuffer());
    await writeFile(tmpMp4, buf);
    try { await faststart(tmpMp4, outFile); } catch { await writeFile(outFile, buf); }
    await unlink(tmpMp4).catch(() => undefined);
    const cost = res.cost?.amount_usd ?? 0;
    log(`✓ Video directo listo: ${outFile} (${res.model})` + (cost ? ` · ~$${cost.toFixed(2)}` : ''));
    await finishVideoFile(outFile, ctx, input, prompt, {
      media: 'video-ad', model: `muapi:${res.model}`,
      cost: cost ? { amount_usd: cost } : null, caption: '', hashtags: [], concept: prompt,
    });
    return;
  }

  if (input.engine === 'avatar') {
    const { assembleTalkingAvatarAd } = await import('../src/lib/video/talking-avatar');
    log(`🎬 Modo persona real — animo tu foto + voz española sincronizada (lip-sync, sin reinventar tu cara, sin filtro de Google)…`);
    const baseDir = brandBaseDir(input, ctx);
    await mkdir(baseDir, { recursive: true });
    const outFile = path.join(baseDir, `${slug(prompt)}-${stampNow()}-ad.mp4`);
    const result = await assembleTalkingAvatarAd(ctx, prompt, {
      outFile, logoTokens: _logoTokens, aspect,
      durationSec: input.duration, refImages: input.refImages || [],
      withCaptions: !!input.withCaptions,
      onProgress: (done, total, label) => emitProgress(done, total, label),
    });
    log(`✓ Anuncio persona real listo: ${result.file} (${result.durationSec.toFixed(1)}s · ${result.model})` + (result.totalCostUsd ? ` · ~$${result.totalCostUsd.toFixed(2)}` : ''));
    await finishVideoFile(result.file, ctx, input, prompt, {
      media: 'video-ad', model: result.model,
      cost: result.totalCostUsd ? { amount_usd: result.totalCostUsd } : null,
      caption: result.caption, hashtags: result.hashtags, concept: prompt,
    });
    return;
  }

  const videoModel = videoI2V(input.tier, input.model);  // current-gen Muapi image→video model
  log(`🎬 Director Opus diseñando el anuncio de video on-brand (${aspect}, modelo: ${videoModel})…`);
  const cinema = (input.cinemaBody || input.genre || input.colorGrade || input.speedRamp)
    ? { cinemaBody: input.cinemaBody, genre: input.genre, colorGrade: input.colorGrade, speedRamp: input.speedRamp }
    : undefined;
  const plan = await directVideoAd(ctx, prompt, {
    shots: input.shots,
    aspect,
    withVoiceover: input.withVoiceover,
    productInfo,
    styleHint: input.style,
    cinema,
  });
  log(`Concepto: ${plan.concept} · ${plan.shots.length} tomas` + (plan.voiceover ? ' · con voz' : ''));

  const baseDir = brandBaseDir(input, ctx);
  await mkdir(baseDir, { recursive: true });
  const outFile = path.join(baseDir, `${slug(prompt)}-${stampNow()}-ad.mp4`);

  const sandbox = /^(1|true|yes)$/i.test(process.env.MUAPI_SANDBOX || '');
  const result = await assembleVideoAd(ctx, plan, {
    outFile,
    logoTokens: _logoTokens,
    withVoiceover: input.withVoiceover !== false,
    withMusic: !!input.withMusic,
    withCaptions: !!input.withCaptions,
    videoModel,
    sandbox,
    productRef: input.productImage,
    refImages: input.refImages,
    isFashionVton: isFashion,
    onProgress: (done, total, label) => emitProgress(done, total, label),
  });
  log(`✓ Anuncio listo: ${result.file} (${result.durationSec.toFixed(1)}s, ${result.shots} tomas)` + (result.totalCostUsd ? ` · ~$${result.totalCostUsd.toFixed(2)}` : ''));
  await finishVideoFile(result.file, ctx, input, prompt, {
    media: 'video-ad', model: result.model,
    cost: result.totalCostUsd ? { amount_usd: result.totalCostUsd } : null,
    caption: result.caption, hashtags: result.hashtags, concept: plan.concept,
  });
}

// Marketing-studio product ad: research the real product, then direct a product-led video ad.
async function runProductAd(input: Input, ctx: ReturnType<typeof brandContext>, prompt: string) {
  let productInfo = prompt;
  if (input.productUrl) {
    log(`🔎 Investigando el producto en ${input.productUrl}…`);
    try {
      const plan = await planStrategy({ brandName: ctx.name, brief: `Investiga el producto en ${input.productUrl} y resume sus características REALES (sin inventar precios ni cifras): ${prompt}`, pieces: 1, colors: ctx.colors, info: ctx.info }).catch(() => null);
      if (plan?.strategy) productInfo = `${prompt}\n\nProducto (datos reales): ${plan.strategy}`;
    } catch { /* fall back to the brief */ }
  }
  if (input.productImage) productInfo += '\n\n(Una foto real del producto fue provista como referencia de identidad visual.)';
  // product-ad keeps the classic compositing director unless an engine is explicitly chosen.
  await runVideoAd({ ...input, engine: input.engine || 'keyframe', withVoiceover: input.withVoiceover ?? true }, ctx, prompt, productInfo);
}

// AI influencer / persona: create from a photo dump, list, or generate consistent on-brand images.
async function runPersona(input: Input, ctx: ReturnType<typeof brandContext>, prompt: string) {
  const persona = await import('../src/lib/persona');
  const action = input.personaAction || (input.personaName && (input.refImages?.length) ? 'create' : input.personaName ? 'use' : 'list');

  if (action === 'list') {
    const all = await persona.listPersonas(ctx.name);
    process.stdout.write(JSON.stringify({ ok: true, brand: ctx.name, media: 'persona', personas: all.map((p) => ({ id: p.id, name: p.name, refs: p.refs.length, description: p.description })) }, null, 2) + '\n');
    return;
  }
  if (action === 'create') {
    if (!input.personaName) throw new Error('personaName requerido para crear una persona');
    if (!input.refImages?.length) throw new Error('refImages (fotos de referencia) requeridas para crear una persona');
    log(`👤 Creando persona "${input.personaName}" con ${input.refImages.length} fotos de referencia…`);
    const p = await persona.createPersona(ctx.name, input.personaName, { description: prompt, refs: input.refImages });
    process.stdout.write(JSON.stringify({ ok: true, brand: ctx.name, media: 'persona', created: { id: p.id, name: p.name, refs: p.refs.length, dir: persona.personaDir(ctx.name) } }, null, 2) + '\n');
    return;
  }
  // use → generate consistent images of the persona
  if (!input.personaName) throw new Error('personaName requerido');
  const p = await persona.loadPersona(ctx.name, input.personaName);
  if (!p) throw new Error(`Persona "${input.personaName}" no existe. Créala primero (personaAction:"create" + refImages).`);
  const count = Math.max(1, Math.min(4, input.count || 1));
  log(`👤 Generando ${count} imagen(es) consistentes de "${p.name}"…`);
  const dataUrls = await persona.generatePersonaImage(p, ctx, prompt, { aspect: aspectFor(input), count });
  await saveOutputs(dataUrls.map((d) => ({ dataUrl: d })), ctx, input, prompt, { media: 'persona', model: 'vertex:gemini (persona refs)' });
}

// Talking-head / UGC spokesperson: persona portrait + voiceover → lipsync video.
async function runTalkingHead(input: Input, ctx: ReturnType<typeof brandContext>, prompt: string) {
  const persona = await import('../src/lib/persona');
  if (!input.personaName) throw new Error('personaName requerido para un talking-head');
  const p = await persona.loadPersona(ctx.name, input.personaName);
  if (!p) throw new Error(`Persona "${input.personaName}" no existe. Créala primero.`);
  const script = input.voiceText || prompt;
  log(`🗣️  Generando talking-head de "${p.name}" (lipsync)…`);
  const res = await persona.generateTalkingHead(p, ctx, { script, lipsyncModel: input.lipsyncModel });
  if (!res.url) throw new Error('El modelo de lipsync no devolvió video');
  const baseDir = brandBaseDir(input, ctx);
  await mkdir(baseDir, { recursive: true });
  const outFile = path.join(baseDir, `${slug(p.name)}-${stampNow()}-talkinghead.mp4`);
  const r = await fetch(res.url, { signal: AbortSignal.timeout(120_000) });
  if (!r.ok) throw new Error(`No pude descargar el talking-head (${r.status})`);
  await writeFile(outFile, Buffer.from(await r.arrayBuffer()));
  await finishVideoFile(outFile, ctx, input, prompt, { media: 'talking-head', model: res.model, cost: res.cost || null, concept: script.slice(0, 140) });
}

// Photo dump → multi-reference consistent images (Soul-v2 photo-dump equivalent), no saved persona.
async function runPhotoDump(input: Input, ctx: ReturnType<typeof brandContext>, prompt: string) {
  if (!input.refImages?.length) throw new Error('refImages (varias fotos de referencia) requeridas para photo-dump');
  const { generateBrandStill, loadRefInline } = await import('../src/lib/ai/brand-image');
  const references: Array<{ data: string; mimeType: string }> = [];
  for (const u of input.refImages.slice(0, 4)) { const inl = await loadRefInline(u); if (inl) references.push(inl); }
  if (!references.length) throw new Error('No pude cargar ninguna de las fotos de referencia');
  const count = Math.max(1, Math.min(4, input.count || 2));
  const aspect = aspectFor(input);
  log(`🖼️  Photo-dump: ${references.length} refs → ${count} imagen(es) consistentes…`);
  const consistentPrompt = `${prompt}\n\nKeep the SAME subject/person/product identity as the reference images — consistent face, features, wardrobe, materials and vibe across the result. On-brand palette: ${ctx.colors.join(', ')}.`;
  const outputs: Array<{ dataUrl?: string }> = [];
  for (let n = 0; n < count; n++) {
    if (n > 0) await sleep(2500);
    const img = await generateBrandStill(consistentPrompt, { quality: 'brand', aspect, references });
    outputs.push({ dataUrl: img.dataUrl });
    emitProgress(n + 1, count, `photo-dump ${n + 1}`);
  }
  await saveOutputs(outputs, ctx, input, prompt, { media: 'photo-dump', model: 'vertex:gemini (photo-dump)' });
}

// Supercomputer batch: one brief → many diverse on-brand variations, generated in parallel.
async function runBatch(input: Input, ctx: ReturnType<typeof brandContext>, prompt: string) {
  const { batchGenerate } = await import('../src/lib/supercomputer');
  const count = Math.max(2, Math.min(24, input.batchCount || input.count || 8));
  const aspect = aspectFor(input);
  log(`⚡ Supercomputer: ${count} variaciones on-brand en paralelo…`);
  const results = await batchGenerate(ctx, prompt, {
    count, quality: input.quality === 'brand' ? 'brand' : (input.quality || 'fast'), aspect, concurrency: 4,
    onProgress: (done, total, label) => emitProgress(done, total, label),
  });
  const outputs = results.filter((r) => r.dataUrl || r.url).map((r) => ({ dataUrl: r.dataUrl, url: r.url }));
  if (!outputs.length) throw new Error('El batch no produjo ninguna imagen');
  await saveOutputs(outputs, ctx, input, prompt, { media: 'batch', model: results[0]?.model || 'vertex', subdir: `batch-${slug(prompt)}` });
}

// Register (or update) a brand in the DB so EVERY tool can use it on-brand.
async function runRegister(input: Input) {
  const db = await import('../src/lib/db');
  const name = (input.brand || '').trim();
  if (!name) throw new Error('brand (nombre de la marca) es obligatorio para registrar');
  const colors = (input.regColors || []).filter(Boolean);
  const facts = (input.regFacts || input.prompt || '').trim();

  // Resolve the main logo + asset kit to data URLs (so they work without GCS).
  let logoDataUrl: string | null = null;
  if (input.regLogo) {
    const inl = await loadImageInline(input.regLogo);
    if (inl) logoDataUrl = `data:${inl.mimeType};base64,${inl.data}`;
    else log(`⚠ no pude cargar el logo: ${input.regLogo}`);
  }
  const uploadedAssets: Array<{ name: string; url: string; isImage: boolean }> = [];
  for (const a of input.regAssets || []) {
    const inl = await loadImageInline(a.url);
    if (inl) uploadedAssets.push({ name: a.name, url: `data:${inl.mimeType};base64,${inl.data}`, isImage: true });
    else log(`⚠ no pude cargar el asset "${a.name}": ${a.url}`);
  }

  // The configured provider tidies real facts into an anti-invention description.
  let description = facts;
  let brandNotes = facts;
  try {
    if (facts) {
      const sys = 'You distill brand facts into a concise, factual brand description for an on-brand design engine. Use ONLY the given facts; never invent numbers, prices, claims or features. Return STRICT JSON.';
      const ask = `BRAND: ${name}\nCOLORS: ${colors.join(', ')}\nREAL FACTS (do not add any others):\n${facts}\n\nReturn JSON: { "description": "2-4 sentence factual description of what the brand/product is and who it is for", "valueProps": ["3-5 short, real value props"], "doNotInvent": ["things the copy must NOT fabricate"] }`;
      const j = await callAIJSON<{ description?: string; valueProps?: string[]; doNotInvent?: string[] }>(ask, { system: sys, maxTokens: 900 });
      if (j?.description) {
        description = j.description;
        brandNotes = [facts, j.valueProps?.length ? 'Value props: ' + j.valueProps.join(' · ') : '', j.doNotInvent?.length ? 'DO NOT INVENT: ' + j.doNotInvent.join(' · ') : ''].filter(Boolean).join('\n');
      }
    }
  } catch { /* keep raw facts */ }

  const analysis = {
    appName: name,
    description,
    theme: { primaryColor: colors[0] || '', accentColors: colors.slice(1) },
    brand_colors: colors.join(', '),
    brand_fonts: input.regFonts || '',
  };

  // Reuse an existing brand id (update in place) or mint a new one.
  let id = input.regId || '';
  if (!id) {
    const all = await db.loadAllBrandConfigs();
    const hit = all.find((b) => (b.company_name || '').toLowerCase() === name.toLowerCase());
    id = hit?.id || `bc_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
  }

  await db.saveBrandConfig({
    id,
    companyName: name,
    companyBlurb: input.regBlurb || '',
    repoUrl: input.regRepo || '',
    logoUrl: logoDataUrl,
    brandNotes,
    uploadedAssets: uploadedAssets.length ? uploadedAssets : undefined,
    analysisJson: JSON.stringify(analysis),
    status: 'ready',
  });
  log(`✓ Marca "${name}" registrada (id ${id}, ${colors.length} colores, logo ${logoDataUrl ? 'sí' : 'no'}, ${uploadedAssets.length} assets)`);
  process.stdout.write(JSON.stringify({ ok: true, registered: { id, name, colors, logo: !!logoDataUrl, assets: uploadedAssets.length, status: 'ready' } }, null, 2) + '\n');
}

// ── FyHighDesign: edit pack (Higgsfield "apps") ──────────────────────────────
async function runEditPro(input: Input, ctx: ReturnType<typeof brandContext>, prompt: string) {
  if (!input.inputImage) throw new Error('inputImage requerido para edit-pro');
  const ep = await import('../src/lib/edit-pack');
  const { hostStillForMuapi } = await import('../src/lib/ai/brand-image');
  const op = input.editOp || 'inpaint';
  const refUrl = input.editRef ? await hostStillForMuapi(input.editRef) : undefined;
  const src = input.inputImage;
  log(`✂️  Edit pro: ${op}…`);
  let r: { url?: string; dataUrl?: string; model: string; cost?: { amount_usd?: number } | null };
  switch (op) {
    case 'inpaint': r = await ep.inpaint(src, prompt); break;
    case 'place': r = await ep.placeObject(src, prompt, refUrl); break;
    case 'expand': r = await ep.expandImage(src, { aspect: input.aspectRatio }); break;
    case 'relight': r = await ep.relight(src, {}); break;
    case 'bg-remove': r = await ep.removeBackground(src); break;
    case 'outfit': r = await ep.swapOutfit(src, prompt, refUrl); break;
    case 'face-swap': if (!refUrl) throw new Error('editRef (cara) requerido para face-swap'); r = await ep.faceSwap(src, refUrl); break;
    case 'headshot': r = await ep.headshot(src, { instruction: prompt }); break;
    case 'skin': r = await ep.skinEnhance(src); break;
    case 'erase': r = await ep.objectErase(src, prompt); break;
    case 'style': r = await ep.styleTransfer(src, { styleRefUrl: refUrl, styleKey: input.styleKey }); break;
    case 'product': r = await ep.productPhoto(src, prompt); break;
    default: throw new Error(`editOp desconocido: ${op}`);
  }
  await saveOutputs([{ url: r.url, dataUrl: r.dataUrl }], ctx, input, prompt || op, { media: 'edit-pro', model: r.model, cost: r.cost || null });
}

// Persona → batch photo set (Higgsfield Photodump Studio).
async function runPhotodump(input: Input, ctx: ReturnType<typeof brandContext>, prompt: string) {
  const { photodump } = await import('../src/lib/persona-studio');
  const { loadPersona } = await import('../src/lib/persona');
  if (!input.personaName) throw new Error('personaName requerido para photodump');
  const p = await loadPersona(ctx.name, input.personaName);
  if (!p) throw new Error(`Persona "${input.personaName}" no existe`);
  log(`📸 Photodump de "${p.name}"…`);
  const dataUrls = await photodump(p, ctx, { count: input.count || input.batchCount });
  if (!dataUrls.length) throw new Error('Photodump no produjo imágenes');
  await saveOutputs(dataUrls.map((d) => ({ dataUrl: d })), ctx, input, prompt || `${p.name}-photodump`, { media: 'photodump', model: 'vertex:gemini (persona)', subdir: `photodump-${slug(p.name)}` });
}

// Portrait + trend packs → consistent trend set (Higgsfield Instadump).
async function runInstadump(input: Input, ctx: ReturnType<typeof brandContext>, prompt: string) {
  if (!input.inputImage) throw new Error('inputImage (retrato) requerido para instadump');
  const { instadump } = await import('../src/lib/persona-studio');
  log('🌀 Instadump (transferencia de trends)…');
  const dataUrls = await instadump(input.inputImage, ctx, { trendKeys: input.trendKeys, count: input.count });
  if (!dataUrls.length) throw new Error('Instadump no produjo imágenes');
  await saveOutputs(dataUrls.map((d) => ({ dataUrl: d })), ctx, input, prompt || 'instadump', { media: 'instadump', model: 'vertex:gemini (trend)', subdir: 'instadump' });
}

// Persona → planned content series (AI Influencer / Brand Ambassador).
async function runAmbassador(input: Input, ctx: ReturnType<typeof brandContext>, prompt: string) {
  const { brandAmbassador } = await import('../src/lib/persona-studio');
  const { loadPersona } = await import('../src/lib/persona');
  if (!input.personaName) throw new Error('personaName requerido para ambassador');
  const p = await loadPersona(ctx.name, input.personaName);
  if (!p) throw new Error(`Persona "${input.personaName}" no existe`);
  log(`🤝 Brand Ambassador "${p.name}"…`);
  const res = await brandAmbassador(p, ctx, prompt, { pieces: input.count });
  if (res.plan) log(`Plan: ${res.plan.slice(0, 160)}`);
  await saveOutputs(res.images.map((d) => ({ dataUrl: d })), ctx, input, prompt || `${p.name}-ambassador`, { media: 'ambassador', model: 'vertex:gemini (persona)', subdir: `ambassador-${slug(p.name)}`, concept: res.plan });
}

// Train a real identity LoRA (opt-in; provider pricing varies).
async function runTrainFace(input: Input, ctx: ReturnType<typeof brandContext>, prompt: string) {
  const { trainFyID } = await import('../src/lib/persona-studio');
  const { hostStillForMuapi } = await import('../src/lib/ai/brand-image');
  if (!input.refImages?.length) throw new Error('refImages requeridas para entrenar una FyID');
  const name = input.personaName || slug(prompt) || 'fyid';
  log(`🧬 Entrenando FyID "${name}" (LoRA real; costo según proveedor)…`);
  const hosted: string[] = [];
  for (const u of input.refImages) { try { hosted.push(await hostStillForMuapi(u)); } catch { /* skip */ } }
  const res = await trainFyID(name, hosted, {});
  process.stdout.write(JSON.stringify({ ok: true, brand: ctx.name, media: 'train-face', fyid: name, model: res.model, lora: res.loraUrl || null, cost: res.cost ?? null, note: res.note }, null, 2) + '\n');
}

// Opus storyboard (Higgsfield Popcorn).
async function runStoryboard(input: Input, ctx: ReturnType<typeof brandContext>, prompt: string) {
  const { generateStoryboard } = await import('../src/lib/storyboard');
  log('🍿 Storyboard…');
  const sb = await generateStoryboard(ctx, prompt, { frames: input.frames, aspect: aspectFor(input) });
  if (!sb.frames.length) throw new Error('El storyboard no produjo frames');
  // caption sidecar holds each frame's caption in order
  const caption = sb.frames.map((f, i) => `${i + 1}. ${f.caption}`).join('\n');
  await saveOutputs(sb.frames.map((f) => ({ dataUrl: f.dataUrl })), ctx, input, prompt, { media: 'storyboard', model: 'vertex (storyboard)', subdir: `storyboard-${slug(prompt)}`, caption, concept: sb.concept });
}

// Upscale an image or video (Higgsfield Topaz).
async function runUpscale(input: Input, ctx: ReturnType<typeof brandContext>, prompt: string) {
  if (!input.inputImage) throw new Error('inputImage (ruta/URL del archivo a escalar) requerido para upscale');
  const up = await import('../src/lib/ai/upscale');
  if (input.upscaleTarget === 'video') {
    const r = await up.upscaleVideo(input.inputImage, {});
    if (!r.url) throw new Error('El upscale de video no devolvió salida (¿el video necesita una URL pública?)');
    const baseDir = brandBaseDir(input, ctx); await mkdir(baseDir, { recursive: true });
    const outFile = path.join(baseDir, `${slug(prompt || 'upscaled')}-${stampNow()}-4k.mp4`);
    const rr = await fetch(r.url, { signal: AbortSignal.timeout(180_000) });
    await writeFile(outFile, Buffer.from(await rr.arrayBuffer()));
    await finishVideoFile(outFile, ctx, input, prompt || 'upscale', { media: 'upscale', model: r.model, cost: r.cost || null });
  } else {
    const r = await up.upscaleImage(input.inputImage, { scale: input.upscaleScale });
    await saveOutputs([{ url: r.url, dataUrl: r.dataUrl }], ctx, input, prompt || 'upscaled', { media: 'upscale', model: r.model, cost: r.cost || null });
  }
}

// Animate a still / recast / reference-to-video / start-end-frame (Higgsfield WAN-Animate etc.).
async function runAnimate(input: Input, ctx: ReturnType<typeof brandContext>, prompt: string) {
  const an = await import('../src/lib/video/animate');
  const op = input.animateOp || 'animate';
  log(`🎞️  Animate: ${op}…`);
  let r: { url?: string; model: string; cost?: { amount_usd?: number } | null };
  if (op === 'recast') {
    if (!input.inputImage || !input.characterRef) throw new Error('inputImage (video) y characterRef requeridos para recast');
    r = await an.recastCharacter(input.inputImage, input.characterRef, {});
  } else if (op === 'reference') {
    if (!input.inputImage) throw new Error('inputImage (referencia) requerido para reference');
    r = await an.referenceToVideo(input.inputImage, prompt, { duration: input.duration });
  } else if (op === 'start-end') {
    if (!input.startImage || !input.endImage) throw new Error('startImage y endImage requeridos para start-end');
    r = await an.startEndFrame(input.startImage, input.endImage, prompt, { duration: input.duration });
  } else {
    if (!input.inputImage) throw new Error('inputImage (still) requerido para animate');
    r = await an.animateStill(input.inputImage, { motionKey: input.styleKey, drivingVideoUrl: input.drivingVideo, model: input.model });
  }
  if (!r.url) throw new Error('Animate no devolvió video');
  const baseDir = brandBaseDir(input, ctx); await mkdir(baseDir, { recursive: true });
  const outFile = path.join(baseDir, `${slug(prompt || op)}-${stampNow()}-${op}.mp4`);
  const rr = await fetch(r.url, { signal: AbortSignal.timeout(180_000) });
  await writeFile(outFile, Buffer.from(await rr.arrayBuffer()));
  await finishVideoFile(outFile, ctx, input, prompt || op, { media: 'animate', model: r.model, cost: r.cost || null });
}

// Distill a brand moodboard from reference images (Higgsfield Moodboards).
async function runMoodboard(input: Input, ctx: ReturnType<typeof brandContext>, prompt: string) {
  if (!input.refImages?.length) throw new Error('refImages (set de referencia) requeridas para moodboard');
  const { distillMoodboard } = await import('../src/lib/brand-aesthetic');
  log(`🎨 Destilando moodboard de ${input.refImages.length} referencias…`);
  const descriptor = await distillMoodboard(input.refImages, ctx);
  if (!descriptor) throw new Error('No pude destilar el moodboard (¿imágenes inaccesibles?)');
  const baseDir = brandBaseDir(input, ctx); await mkdir(baseDir, { recursive: true });
  const file = path.join(baseDir, `moodboard-${slug(prompt || 'brand')}-${stampNow()}.txt`);
  await writeFile(file, descriptor, 'utf8');
  process.stdout.write(JSON.stringify({ ok: true, brand: ctx.name, media: 'moodboard', descriptor, file }, null, 2) + '\n');
}

// Opus picks the best model for a brief (Higgsfield multi-model hub / auto-router).
async function runAutoroute(input: Input, ctx: ReturnType<typeof brandContext>, prompt: string) {
  const { autoRoute } = await import('../src/lib/model-router');
  const r = await autoRoute(prompt, ctx);
  process.stdout.write(JSON.stringify({ ok: true, brand: ctx.name, media: 'autoroute', task: r.task, model: r.model, why: r.why }, null, 2) + '\n');
}

// Score a concept for virality (Higgsfield virality predictor).
async function runVirality(input: Input, ctx: ReturnType<typeof brandContext>, prompt: string) {
  const { scoreVirality } = await import('../src/lib/virality');
  const s = await scoreVirality(ctx, { concept: prompt, platform: input.platform });
  process.stdout.write(JSON.stringify({ ok: true, brand: ctx.name, media: 'virality', ...s }, null, 2) + '\n');
}

// Multi-angle views of one image (Higgsfield Angles 2.0).
async function runAngles(input: Input, ctx: ReturnType<typeof brandContext>, prompt: string) {
  if (!input.inputImage) throw new Error('inputImage requerido para angles');
  const { multiAngle } = await import('../src/lib/edit-pack');
  log('🔄 Generando ángulos alternos…');
  const results = await multiAngle(input.inputImage, { count: input.count });
  const outputs = results.filter((r) => r.url || r.dataUrl).map((r) => ({ url: r.url, dataUrl: r.dataUrl }));
  if (!outputs.length) throw new Error('Angles no produjo imágenes');
  await saveOutputs(outputs, ctx, input, prompt || 'angles', { media: 'angles', model: results[0]?.model || 'muapi', subdir: 'angles' });
}

// On-brand product hero shots compositing the real product (Marketing Studio).
async function runProductShots(input: Input, ctx: ReturnType<typeof brandContext>, prompt: string) {
  const src = input.productImage || input.inputImage;
  if (!src) throw new Error('productImage (o inputImage) requerido para product-shots');
  const { productHeroSet } = await import('../src/lib/product-compositing');
  log('🛍️ Generando product hero shots…');
  const dataUrls = await productHeroSet(src, ctx, { count: input.count, aspect: aspectFor(input) });
  if (!dataUrls.length) throw new Error('No se generaron product shots');
  await saveOutputs(dataUrls.map((d) => ({ dataUrl: d })), ctx, input, prompt || 'product', { media: 'product-shots', model: 'vertex:nano-banana(product)', subdir: 'product-shots' });
}

// Product Photoshoot: 10 named modes (product_shot, lifestyle_scene, closeup_with_person,
// moodboard_pin, hero_banner, social_carousel, ad_creative_pack, virtual_model_tryout,
// conceptual_product, restyle) — composites the REAL product into a curated scene.
async function runProductPhotoshoot(input: Input, ctx: ReturnType<typeof brandContext>, prompt: string) {
  const src = input.productImage || input.inputImage;
  if (!src) throw new Error('productImage (o inputImage) requerido para product-photoshoot');
  const { productPhotoshoot, findShootMode, SHOOT_MODES } = await import('../src/lib/video/product-photoshoot');
  const m = findShootMode(input.shootMode) || SHOOT_MODES[0];
  log(`📸 Product Photoshoot — modo "${m.name}" (${m.key}), ${Math.max(1, Math.min(6, input.count || 1))} imagen(es)…`);
  const res = await productPhotoshoot(src, ctx, {
    mode: m.key, prompt, count: input.count, aspect: input.aspectRatio,
  });
  await saveOutputs(res.images.map((d) => ({ dataUrl: d })), ctx, input, prompt || m.key,
    { media: 'product-photoshoot', model: 'vertex:nano-banana-pro(product)', subdir: `photoshoot-${m.key}`, concept: m.name });
}

// Marketplace Card: drop the REAL product into a platform-accurate listing/thumbnail card
// (Amazon, Etsy, Shopify, eBay, MercadoLibre, App Store, YouTube thumb, review badge).
async function runMarketplaceCard(input: Input, ctx: ReturnType<typeof brandContext>, prompt: string) {
  const src = input.productImage || input.inputImage;
  if (!src) throw new Error('productImage (o inputImage) requerido para marketplace-card');
  const { marketplaceCard, findCardTemplate, CARD_TEMPLATES } = await import('../src/lib/video/marketplace-cards');
  const t = findCardTemplate(input.cardTemplate) || CARD_TEMPLATES[0];
  log(`🏷️ Marketplace Card — plantilla "${t.name}" (${t.key}), ${Math.max(1, Math.min(4, input.count || 1))} variante(s)…`);
  const res = await marketplaceCard(src, ctx, {
    template: t.key, prompt, title: input.cardTitle, price: input.cardPrice, count: input.count,
  });
  await saveOutputs(res.images.map((d) => ({ dataUrl: d })), ctx, input, prompt || t.key,
    { media: 'marketplace-card', model: 'vertex:nano-banana-pro(card)', subdir: `card-${t.key}`, concept: t.name });
}

// Opus creative-director: dynamic clarifying questions (1-15, Opus decides) + a better brief.
async function runRefine(input: Input, ctx: ReturnType<typeof brandContext>, prompt: string) {
  const { refineBrief } = await import('../src/lib/brief-refiner');
  log('🧠 Opus refinando el brief…');
  const r = await refineBrief(ctx, prompt, { kind: input.kind, answers: input.refineAnswers });
  process.stdout.write(JSON.stringify({ ok: true, brand: ctx.name, media: 'refine', questions: r.questions, refinedBrief: r.refinedBrief, assumptions: r.assumptions, rationale: r.rationale }, null, 2) + '\n');
}

async function main() {
  const input = await parseArgs();

  // The broker owns every output path. Ignore JSON outDir so a model or MCP
  // client cannot write elsewhere on the machine.
  if (!input.list) {
    input.outDir = path.resolve(requiredRuntimePath('FYDESIGN_OUTPUT_ROOT'));
  }

  // ── List brands ──────────────────────────────────────────────────────────
  if (input.list) {
    const db = await import('../src/lib/db');
    const all = await db.loadAllBrandConfigs();
    process.stdout.write(JSON.stringify({
      brands: all.map((b) => ({ id: b.id, name: b.company_name, repo: b.repo_url, hasLogo: !!b.logo_url, status: b.status })),
    }, null, 2) + '\n');
    return;
  }

  // Brand registration needs no prompt and no existing brand — handle it first.
  if (input.media === 'register') { await runRegister(input); return; }
  // Video Analyzer needs no brand/prompt — just a video URL/file.
  if (input.media === 'analyze') { await runAnalyze(input); return; }
  // Personal Clipper needs no brand/prompt — long video → vertical clips.
  if (input.media === 'clip') { await runClip(input); return; }
  // Instant mode derives its OWN ephemeral brand from a URL — no pre-registered brand needed.
  if (input.media === 'instant') { await runInstant(input); return; }

  const prompt = (input.prompt || '').trim();
  // Some modes operate on an image/persona and don't require a text prompt.
  const noPromptModes = ['persona', 'photodump', 'train-face', 'upscale', 'edit-pro', 'instadump', 'animate', 'moodboard', 'angles', 'product-shots', 'product-photoshoot', 'marketplace-card', 'autoroute', 'virality', 'ad-engine'];
  const promptOptional = noPromptModes.includes(input.media || '') || (input.media === 'talking-head' && !!input.voiceText);
  if (!prompt && !promptOptional) throw new Error('prompt is required');

  // ── Resolve brand ────────────────────────────────────────────────────────
  let cfg = await loadBrand(input);
  let freshAnalysis: any = null;
  if (!cfg && input.repo) {
    log(`No saved brand; analyzing repo ${input.repo} fresh…`);
    const token = process.env.GITHUB_TOKEN;
    if (!token) throw new Error('GITHUB_TOKEN required to analyze a repo with no saved brand');
    const { analyzeRepository } = await import('../src/lib/github-analyzer');
    freshAnalysis = await analyzeRepository(input.repo, token);
    cfg = { company_name: freshAnalysis.appName, repo_url: input.repo, logo_url: null } as any;
  }
  if (!cfg) {
    // FyDesign is NOT brand-gated (Higgsfield-style): a registered brand is NEVER required.
    // With no match, build an EPHEMERAL context from whatever name was passed (or a generic
    // one) and let Opus direct from the brief + any reference images. Registering a brand only
    // ENRICHES output with its real colors/logo/facts — it is never mandatory.
    const ephemeralName = (input.brand || input.brandName || '').trim() || 'Marca';
    log(`Sin marca registrada — contexto efímero "${ephemeralName}" (Opus dirige desde el brief/refs; registra una marca solo para fijar colores/logo reales).`);
    cfg = { company_name: ephemeralName, analysis_json: '{}', logo_url: null } as Awaited<ReturnType<typeof loadBrand>>;
  }

  const ctx = brandContext(cfg, freshAnalysis);

  // Learn the brand's real aesthetic once (cached per brand) → threaded into prompts.
  try {
    // Versioned by the brand's updated_at → editar la marca en la BD invalida el caché solo.
    const ver = String((cfg as any)?.updated_at || '').replace(/\D/g, '').slice(0, 14) || 'v0';
    const cacheFile = path.join(
      requiredRuntimePath('FYDESIGN_STATE_ROOT'),
      'cache',
      `style-${slug(ctx.name)}-${ver}.txt`,
    );
    try {
      brandStyleGuide = (await readFile(cacheFile, 'utf8')).trim();
    } catch {
      brandStyleGuide = (await buildBrandStyleGuide(cfg).catch(() => '')) || '';
      if (brandStyleGuide) {
        try { await mkdir(path.dirname(cacheFile), { recursive: true }); await writeFile(cacheFile, brandStyleGuide); } catch { /* noop */ }
      }
    }
    if (brandStyleGuide) log(`Estilo de marca cargado (${brandStyleGuide.length} chars)`);
  } catch { /* noop */ }

  // Load this brand's recent feed → keep new work non-repetitive AND stylistically consistent.
  try {
    const brandDir = input.outDir || requiredRuntimePath('FYDESIGN_OUTPUT_ROOT');
    const mem = await loadFeedMemory(brandDir);
    feedMemory = memoryDigest(mem);
    if (mem.length) log(`Memoria de feed: ${mem.length} publicaciones previas`);
  } catch { /* noop */ }

  // Catalog the brand's logo kit (lockup / mark / icon variants) → Opus picks the right one per placement.
  await loadLogoKit(ctx).catch(() => {});

  // Real generative media takes different paths than HTML slides.
  if (input.media === 'svg') { await runSvg(input, ctx, prompt); return; }
  if (input.media === 'edit') { await runEdit(input, ctx, prompt); return; }
  if (input.media === 'campaign') { await runCampaign(input, ctx, prompt); return; }
  if (input.media === 'landing') { await runLanding(input, ctx, prompt); return; }
  if (input.media === 'video-ad') { await runVideoAd(input, ctx, prompt); return; }
  if (input.media === 'ad-engine') { await runAdEngine(input, ctx, prompt); return; }
  if (input.media === 'product-ad') { await runProductAd(input, ctx, prompt); return; }
  if (input.media === 'persona') { await runPersona(input, ctx, prompt); return; }
  if (input.media === 'talking-head') { await runTalkingHead(input, ctx, prompt); return; }
  if (input.media === 'photo-dump') { await runPhotoDump(input, ctx, prompt); return; }
  if (input.media === 'batch') { await runBatch(input, ctx, prompt); return; }
  if (input.media === 'edit-pro') { await runEditPro(input, ctx, prompt); return; }
  if (input.media === 'photodump') { await runPhotodump(input, ctx, prompt); return; }
  if (input.media === 'instadump') { await runInstadump(input, ctx, prompt); return; }
  if (input.media === 'ambassador') { await runAmbassador(input, ctx, prompt); return; }
  if (input.media === 'train-face') { await runTrainFace(input, ctx, prompt); return; }
  if (input.media === 'storyboard') { await runStoryboard(input, ctx, prompt); return; }
  if (input.media === 'upscale') { await runUpscale(input, ctx, prompt); return; }
  if (input.media === 'animate') { await runAnimate(input, ctx, prompt); return; }
  if (input.media === 'moodboard') { await runMoodboard(input, ctx, prompt); return; }
  if (input.media === 'autoroute') { await runAutoroute(input, ctx, prompt); return; }
  if (input.media === 'virality') { await runVirality(input, ctx, prompt); return; }
  if (input.media === 'angles') { await runAngles(input, ctx, prompt); return; }
  if (input.media === 'product-shots') { await runProductShots(input, ctx, prompt); return; }
  if (input.media === 'product-photoshoot') { await runProductPhotoshoot(input, ctx, prompt); return; }
  if (input.media === 'marketplace-card') { await runMarketplaceCard(input, ctx, prompt); return; }
  if (input.media === 'refine') { await runRefine(input, ctx, prompt); return; }
  if (input.media === 'image' || input.media === 'video' || input.media === 'post') {
    await runMedia(input, ctx, prompt);
    return;
  }

  const logoDataUrl = await resolveLogoDataUrl(ctx.logo);
  const N = Math.max(1, Math.min(12, input.slides || inferSlides(prompt)));
  const platform = input.platform || 'instagram-feed';
  const size = SIZES[platform] || SIZES['instagram-feed'];
  const isCarousel = N > 1;

  log(`Brand: ${ctx.name} | colors: ${ctx.colors.join(', ') || '(none)'} | logo: ${logoDataUrl ? 'yes (embedded)' : ctx.logo ? 'unresolved → wordmark' : 'none'}`);
  log(`Generating ${N} ${isCarousel ? 'carousel slide(s)' : 'ad creative'} @ ${size.w}x${size.h}…`);

  // ── Build the on-brand prompt ────────────────────────────────────────────
  const system = isCarousel ? CAROUSEL_BRAIN : AD_BRAIN;
  const logoLine = logoDataUrl
    ? 'Use the LOGO KIT: header/large = __LOGO__ (full lockup); corner/badge/small = __LOGOMARK__ (symbol only); on dark backgrounds use the _LIGHT_ variants. Keep the EXACT tokens (swapped for the real logos before rendering).'
    : `No logo asset — render "${ctx.name}" as a clean wordmark.`;

  const userPrompt = `BRAND: ${ctx.name}
BRAND COLORS (use these EXACTLY as the palette): ${ctx.colors.join(', ')} ${ctx.brandColors}
FONTS: ${ctx.fonts || 'clean modern sans-serif (Inter / system-ui)'}
PRODUCT INFO: ${ctx.info || '(infer from the brand name and the task)'}
APP SCREENS / REAL COPY:
${ctx.screens || '(none)'}

TASK: ${prompt}

Produce ${N} ${isCarousel ? 'carousel slides that work as a sequence' : 'single ad creative'}, EACH at EXACTLY ${size.w}x${size.h}px.
${logoLine}${logoBlock()}
Each slide must be a COMPLETE, self-contained HTML document: <!doctype html> + inline <style>, no external JS. Use the brand palette and fonts, high contrast, bold large type, on-brand and premium.

Return STRICT valid JSON (properly escape every quote and newline inside html):
{
  "slides": [ { "title": "short label", "html": "<!doctype html>… full ${size.w}x${size.h} slide …" } ],
  "caption": "ready-to-post caption, in the language of the TASK",
  "hashtags": ["5-8 relevant hashtags"]
}`;

  const result = await callAIJSON<GenResult>(userPrompt, { system, maxTokens: 16000, cacheSystem: true });
  if (!result || !Array.isArray(result.slides) || result.slides.length === 0) {
    throw new Error('El proveedor de texto no devolvió slides válidos');
  }

  // ── Render each slide → PNG → save ───────────────────────────────────────
  const outDir = input.outDir || requiredRuntimePath('FYDESIGN_OUTPUT_ROOT');
  await mkdir(outDir, { recursive: true });
  const d = new Date();
  const stamp = `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}-${String(d.getHours()).padStart(2, '0')}${String(d.getMinutes()).padStart(2, '0')}`;
  const base = slug(prompt);

  const files: string[] = [];
  for (let i = 0; i < result.slides.length; i++) {
    const html = applyLogos(result.slides[i]?.html || '');
    const buf = await renderHtmlToPng(html, size.w, size.h, `${ctx.name} ${i + 1}/${result.slides.length}`);
    const file = path.join(outDir, `${base}-${stamp}-${String(i + 1).padStart(2, '0')}.png`);
    await writeFile(file, buf);
    files.push(file);
    log(`  ✓ slide ${i + 1}/${result.slides.length} → ${file} (${(buf.length / 1024).toFixed(0)} KB)`);
  }

  if (result.caption || (result.hashtags && result.hashtags.length)) {
    const captionFile = path.join(outDir, `${base}-${stamp}-caption.txt`);
    await writeFile(captionFile, `${result.caption || ''}\n\n${(result.hashtags || []).join(' ')}\n`);
  }

  process.stdout.write(JSON.stringify({
    ok: true,
    brand: ctx.name,
    platform,
    size,
    count: files.length,
    dir: outDir,
    files,
    caption: result.caption || '',
    hashtags: result.hashtags || [],
  }, null, 2) + '\n');
}

main().catch((e) => {
  console.error('[fydesign-gen] ERROR:', e instanceof Error ? e.message : e);
  process.exit(1);
});
