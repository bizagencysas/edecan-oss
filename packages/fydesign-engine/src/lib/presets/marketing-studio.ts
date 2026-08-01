// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  MARKETING STUDIO PRESETS — "pick a format, skip the setup" (Higgsfield-style) ║
// ║                                                                              ║
// ║  9 curated video formats. Each wraps the user's plain brief into that         ║
// ║  format's directorial style + recommended model / aspect / duration, then     ║
// ║  hands a single finished prompt to the DIRECT engine (one prompt → one model). ║
// ║  No multi-step pipeline, no Opus shot-list — the format IS the setup.          ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

export type DirectModel = 'seedance' | 'kling' | 'omni';

export interface MarketingPreset {
  key: string;
  name: string;
  /** One-line description (shown in the picker / MCP list). */
  desc: string;
  /** Recommended direct model for this format (used when the engine is auto/default). */
  model: DirectModel;
  /** Recommended aspect ('9:16' | '16:9' | '1:1'). */
  aspect: string;
  /** Recommended clip length in seconds. */
  duration: number;
  /** Wrap the user's plain brief into this format's finished prompt. */
  scaffold: (brief: string) => string;
}

const real = '(use only real facts from the brand; never invent prices, stats, follower counts, awards or testimonials)';

export const MARKETING_PRESETS: MarketingPreset[] = [
  {
    key: 'ugc',
    name: 'UGC',
    desc: 'Realistic social-media video — handheld, candid, native.',
    model: 'seedance', aspect: '9:16', duration: 5,
    scaffold: (b) => `Authentic UGC social-media video, shot handheld on a phone, real natural available light, candid real-creator energy — NOT a polished ad, NOT an AI render. Scroll-stopping hook in the first second. ${b}. Vertical, true-to-life skin and motion. ${real}.`,
  },
  {
    key: 'tv-spot',
    name: 'TV Spot',
    desc: 'Cinematic $1M TV commercial — authentic stories, amplified.',
    model: 'seedance', aspect: '16:9', duration: 10,
    scaffold: (b) => `Cinematic one-million-dollar TV commercial. Anamorphic, golden-hour grade, dynamic drone + dolly camera moves, premium advertising cinematography, emotional and aspirational. ${b}. Photoreal, high-end. ${real}.`,
  },
  {
    key: 'hyper-motion',
    name: 'Hyper Motion',
    desc: 'High-energy product highlight — whips, speed ramps, drama.',
    model: 'kling', aspect: '9:16', duration: 5,
    scaffold: (b) => `High-energy HYPER-MOTION product showcase. Fast whip-pans, dramatic speed ramps, snappy cuts, bold dramatic lighting, glossy premium look, the product as the hero. ${b}. Punchy and kinetic. ${real}.`,
  },
  {
    key: 'unboxing',
    name: 'Unboxing',
    desc: 'High-quality unboxing — satisfying reveals, macro detail.',
    model: 'seedance', aspect: '9:16', duration: 5,
    scaffold: (b) => `Premium UNBOXING video. Top-down and tight macro close-ups, hands opening the packaging, satisfying tactile reveals, soft clean studio light, shallow depth of field. ${b}. Crisp, ASMR-tactile, desirable. ${real}.`,
  },
  {
    key: 'product-review',
    name: 'Product Review',
    desc: 'Authentic product review — a creator showing it off.',
    model: 'seedance', aspect: '9:16', duration: 5,
    scaffold: (b) => `Authentic PRODUCT REVIEW UGC video — a real creator holding and showing the product to camera with genuine, trustworthy energy, natural light, real setting. ${b}. Honest and relatable. ${real}.`,
  },
  {
    key: 'demo',
    name: 'Demo',
    desc: 'Crisp product demo — the product in use, feature highlights.',
    model: 'seedance', aspect: '9:16', duration: 5,
    scaffold: (b) => `Clean PRODUCT DEMO. The product shown clearly in use, smooth feature highlights, crisp studio framing, modern minimal aesthetic, clear and convincing. ${b}. Sharp and informative. ${real}.`,
  },
  {
    key: 'tutorial',
    name: 'Tutorial',
    desc: 'Step-by-step tutorial — clear framing, instructional pacing.',
    model: 'kling', aspect: '9:16', duration: 10,
    scaffold: (b) => `Clear STEP-BY-STEP tutorial video. Clean instructional framing, close-ups of each step, calm helpful pacing, well-lit, easy to follow. ${b}. Clear and practical. ${real}.`,
  },
  {
    key: 'cinematic',
    name: 'Cinematic',
    desc: 'Film-grade scene — shallow DOF, grade, dramatic.',
    model: 'seedance', aspect: '16:9', duration: 10,
    scaffold: (b) => `Cinematic, film-grade scene. Shallow depth of field, deliberate color grade, dramatic lighting, elegant camera movement, mood and atmosphere. ${b}. Beautiful and intentional. ${real}.`,
  },
  {
    key: 'wild-card',
    name: 'Wild Card',
    desc: 'A bold, unexpected, creative take — surprise me.',
    model: 'seedance', aspect: '9:16', duration: 5,
    scaffold: (b) => `A BOLD, unexpected, scroll-stopping creative interpretation — take a daring conceptual angle, surprising visuals, a striking idea, high craft. ${b}. Be original and fearless. ${real}.`,
  },
];

/** Find a preset by key (case-insensitive, tolerant of spaces/underscores). */
export function findPreset(key?: string): MarketingPreset | null {
  if (!key) return null;
  const norm = (s: string) => s.toLowerCase().replace(/[\s_]+/g, '-');
  const k = norm(key);
  return MARKETING_PRESETS.find((p) => norm(p.key) === k || norm(p.name) === k) || null;
}

/** Compact list (for the MCP `fydesign_presets` tool and the UI picker). */
export function presetMenu(): Array<{ key: string; name: string; desc: string }> {
  return MARKETING_PRESETS.map((p) => ({ key: p.key, name: p.name, desc: p.desc }));
}
