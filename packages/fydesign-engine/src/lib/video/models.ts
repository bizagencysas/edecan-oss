// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Muapi MODEL REGISTRY — curated to the CURRENT generation only (June 2026).    ║
// ║                                                                              ║
// ║  Single source of truth for which Muapi endpoints FyDesign uses. Old gens     ║
// ║  (Kling ≤v2.1, Veo3 non-.1, Sora v1, Seedance v1, Hailuo 02, Flux-dev, LTX     ║
// ║  2.3 defaults, Hunyuan) are intentionally OUT — only the latest models.        ║
// ║  Everything here runs on the ONE Muapi API key. Verify the live catalog with   ║
// ║  GET https://api.muapi.ai/api/v1/models                                        ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

/** Quality / cost ladder. */
export type Tier = 'fast' | 'pro' | 'max' | 'ultra';
export const DEFAULT_TIER: Tier = 'pro';

/** Image→Video (animate an on-brand still). Cost = approx USD per clip.
 *  IMPORTANT: Kling is the default because it ANIMATES THE KEYFRAME FAITHFULLY
 *  (preserves composition, lighting and identity). Gemini Omni i2v REINTERPRETS /
 *  brightens the still (verified — it reframed + flattened a dark premium keyframe),
 *  so it's NOT used for i2v anymore — kept only as a manual override / for its
 *  native text-to-video + character/audio features. */
export const VIDEO_I2V: Record<Tier, string> = {
  fast: 'seedance-v1.5-pro-i2v-fast',      // fastest configured route
  pro: 'kling-v2.5-turbo-pro-i2v',         // faithful motion (default)
  max: 'kling-v3.0-pro-image-to-video',    // highest configured fidelity
  ultra: 'kling-v3.0-4k-image-to-video',   // Kling v3 at 4K, faithful and sharp
};

/** Gemini Omni — natively-multimodal any-to-any (identity + native audio). */
export const GEMINI_OMNI_I2V = 'gemini-omni-image-to-video';   // image_urls[], audio_ids, character_ids
export const GEMINI_OMNI_T2V = 'gemini-omni-text-to-video';
export const GEMINI_OMNI_VIDEO_EDIT = 'gemini-omni-video-edit';
export const GEMINI_OMNI_CHARACTER = 'gemini-omni-character';   // reusable character from one reference (Soul-ID)
export const GEMINI_OMNI_AUDIO = 'gemini-omni-audio';           // named voice profile

/** Endpoints that take an image_urls ARRAY (not image_url) + resolution/aspect_ratio. */
export function usesImageUrlsArray(endpoint: string): boolean {
  return /gemini-omni/.test(endpoint);
}

/** Text→Video (no input image). */
export const VIDEO_T2V: Record<Tier, string> = {
  fast: 'seedance-v1.5-pro-t2v-fast',
  pro: 'kling-v2.5-turbo-pro-t2v',
  max: 'kling-v3.0-pro-text-to-video',
  ultra: 'veo3.1-text-to-video',
};

/** The newest premium endpoints, available via explicit `model` override. */
export const VIDEO_PREMIUM = [
  'gemini-omni-image-to-video',           // identity + native audio; provider plan dependent
  'veo-4-image-to-video',                 // current Veo route
  'veo3.1-image-to-video',                // cinematic route
  'openai-sora-2-pro-image-to-video',     // Sora Pro route
  'openai-sora-2-image-to-video',
  'kling-v3.0-omni-4k-image-to-video',    // Kling v3 omni 4K
  'minimax-hailuo-2.3-pro-i2v',
  'wan2.6-image-to-video',
  'seedance-v1.5-pro-i2v',
] as const;

/** Muapi image models (when provider='muapi'; brand refs honored by nano-banana). */
export const IMAGE_MODEL: Record<Tier, string> = {
  fast: 'flux-2-pro',
  pro: 'bytedance-seedream-v5.0',         // Seedream 5
  max: 'nano-banana-pro',                 // brand-reference route
  ultra: 'nano-banana-pro',
};

/** Voiceover (TTS) + voice clone + music. */
export const TTS_HD = 'minimax-speech-2.6-hd';        // realistic
export const TTS_TURBO = 'minimax-speech-2.6-turbo';  // faster
export const VOICE_CLONE = 'minimax-voice-clone';     // clone a voice
export const MUSIC_MODEL = 'suno-create-music';       // music route

/** Talking-head / avatar (still or portrait + audio → lip-synced video). */
export const TALKING_AVATAR = 'kling-v2-avatar-standard';   // standard avatar
export const TALKING_AVATAR_PRO = 'kling-v2-avatar-pro';    // higher-fidelity avatar
export const LIPSYNC_STILL = 'infinitetalk-image-to-video'; // image + audio talking route
export const LIPSYNC_FAST = 'sync-lipsync';                 // fast lipsync route

/**
 * REAL-PERSON TALKING ("make my uploaded person talk") — fallback chain, NEWEST &
 * most-realistic first. These animate the REAL photo + a voice clip, lip-synced.
 * CRITICAL: unlike Gemini Omni (which rejects real faces with PROMINENT_PEOPLE_FILTER),
 * none of these have a real-person filter — they're built for UGC avatars.
 */
/**
 * IDENTITY → NEW SCENE (image): lock the user's EXACT face and place it in a brand-new
 * UGC scene (a generated still, NOT the original selfie). These INJECT the face identity
 * (PuLID / character-reference) instead of re-drawing it from scratch — so the face stays
 * the same person across new scenes (fixes the "redraw drifted my face" failure). Newest/
 * best-identity first. Low real-face-filter risk. Stage 1 of the talking pipeline.
 */
export const IDENTITY_SCENE_CHAIN = [
  'flux-pulid',                          // PuLID face injection, consistent face across scenes
  'ideogram-character',                  // character reference, Realistic style
  'minimax-image-01-subject-reference',  // subject-reference route
] as const;

export const TALKING_AVATAR_CHAIN = [
  'ltx-2.3-lipsync',             // LTX-2.3, 1080p, preserves lighting and identity
  'kling-v2-avatar-pro',         // realistic avatar with head and eye movement
  'wan2.2-speech-to-video',      // Wan 2.2, 720p talking route
  'infinitetalk-image-to-video', // portrait talking route, 480/720p
] as const;

/** Motion control (video→video) — Higgsfield's signature, latest Kling v3. */
export const MOTION_CONTROL = 'kling-v3.0-std-motion-control';
export const MOTION_CONTROL_PRO = 'kling-v3.0-pro-motion-control';

function tierOf(t?: string): Tier {
  return (['fast', 'pro', 'max', 'ultra'].includes(t || '') ? t : DEFAULT_TIER) as Tier;
}

/** Resolve the image→video endpoint: explicit override wins, else the tier. */
export function videoI2V(tier?: string, override?: string): string {
  return override || VIDEO_I2V[tierOf(tier)];
}
/** Resolve the text→video endpoint: explicit override wins, else the tier. */
export function videoT2V(tier?: string, override?: string): string {
  return override || VIDEO_T2V[tierOf(tier)];
}
/** Resolve a Muapi image endpoint by tier. */
export function imageModel(tier?: string, override?: string): string {
  return override || IMAGE_MODEL[tierOf(tier)];
}
