// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  FyDesign video / motion-ad engine — SHARED CONTRACTS                          ║
// ║                                                                              ║
// ║  Single source of truth for the Higgsfield-class capabilities built on top    ║
// ║  of the existing FyDesign engine (Opus = mind, Muapi/Vertex = builders).      ║
// ║  Every module under src/lib/video/* and the persona / supercomputer engines   ║
// ║  import their data shapes AND adhere to the function CONTRACTS documented      ║
// ║  here so the parts compose. Do NOT change a signature without updating the     ║
// ║  module that implements it and every caller.                                  ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

/** Mirror of brandContext()'s return shape in scripts/fydesign-gen.ts (structural). */
export interface VideoBrandCtx {
  name: string;
  colors: string[];
  brandColors: string;
  fonts: string;
  screens: string;
  info: string;
  logo: string;
  assets: Array<{ name: string; url: string }>;
}

export type VideoAspect = '1:1' | '16:9' | '9:16' | '4:3' | '3:4';

export interface VideoFormat {
  name: string;
  w: number;
  h: number;
  aspect: VideoAspect;
}

/** On-screen brand copy burned over a single shot (Opus places it in negative space). */
export interface ShotOverlay {
  headline?: string;
  subtext?: string;
  cta?: string;
  /** A small caption strip / lower-third (e.g. a feature name). */
  lowerThird?: string;
  position?: 'top' | 'center' | 'bottom';
}

export interface Shot {
  /** Narrative role: hook | product | benefit | proof | cta | b-roll … */
  role: string;
  /** Text-free, UI-free still description in English (the keyframe to animate). */
  imagePrompt: string;
  /** How the shot moves — subject motion + the camera move, in English. */
  motionPrompt: string;
  /** Named camera move from src/lib/video/camera.ts (e.g. 'crash-zoom'). Optional. */
  cameraPreset?: string;
  /** Cinema Studio: lens / focal length key (e.g. 'portrait-85mm', 'anamorphic'). Optional. */
  lens?: string;
  /** Cinema Studio: lighting / film look key (e.g. 'golden-hour', 'film-35mm'). Optional. */
  look?: string;
  /** Clip length in seconds (typically 3–8). */
  durationSec: number;
  /** On-screen brand copy for this shot. Optional — many shots stay clean. */
  overlay?: ShotOverlay;
  /** Allow text/typography to be generated in the keyframe still. */
  allowText?: boolean;
  /** Allow device/app UI interfaces to be generated in the keyframe still. */
  allowUi?: boolean;
}

/** The full directed ad — produced by the Opus video director, consumed by the assembler. */
export interface VideoPlan {
  concept: string;
  format: VideoFormat;
  shots: Shot[];
  /** Full voiceover script in the brief's language, or undefined for no VO. */
  voiceover?: string;
  /** Music mood/brief, e.g. 'uplifting cinematic build, modern, confident'. */
  musicMood?: string;
  caption: string;
  hashtags: string[];
  /** Closing brand card. */
  endCard?: { headline?: string; cta?: string; handle?: string };
}

/** A reusable brand persona / AI influencer (Soul-ID-equivalent via reference images). */
export interface Persona {
  id: string;
  name: string;
  brand: string;
  /** Look, vibe, demographic, wardrobe, setting — used in every generation prompt. */
  description: string;
  /** Absolute file paths of reference images (the "photo dump") on disk. */
  refs: string[];
  /** Optional Muapi voice id used for talking-head VO. */
  voice?: string;
  createdAt: string;
}

export interface CameraPreset {
  key: string;
  label: string;
  /** Phrase appended to a shot's motionPrompt to instruct the video model. */
  promptPhrase: string;
  /** Per-model parameter hints (passed via Muapi `extra`). Optional. */
  params?: Record<string, unknown>;
  category: 'push' | 'pull' | 'orbit' | 'aerial' | 'handheld' | 'static' | 'reveal' | 'speed';
}

export interface AudioResult {
  /** Absolute path to the generated audio file (.mp3/.wav). */
  file: string;
  durationSec?: number;
  kind: 'voice' | 'music';
  cost?: { amount_usd?: number } | null;
}

/** One produced clip on disk plus the metadata the assembler threads through. */
export interface ShotClip {
  shot: Shot;
  /** Absolute path to the rendered .mp4 for this shot (already overlay-burned). */
  file: string;
  cost?: { amount_usd?: number } | null;
  model: string;
}

export interface VideoAdResult {
  /** Absolute path to the final assembled .mp4. */
  file: string;
  durationSec: number;
  caption: string;
  hashtags: string[];
  shots: number;
  totalCostUsd: number;
  model: string;
}

// ════════════════════════════════════════════════════════════════════════════
//  MODULE CONTRACTS — the exact exports each module MUST expose. Callers rely on
//  these signatures; implementers must match them byte-for-byte.
//
//  src/lib/ai/brand-image.ts
//    generateBrandStill(prompt: string, opts?: {
//      quality?: 'ultra'|'standard'|'fast'|'brand'; aspect?: VideoAspect;
//      references?: Array<{ data: string; mimeType: string }>; verifyTextFree?: boolean;
//    }): Promise<{ dataUrl: string; model: string }>
//    dataUrlToTmpPng(dataUrl: string): Promise<string>           // writes png, returns path
//    hostStillForMuapi(src: string | Buffer): Promise<string>    // GCS public URL, else data URL
//    loadRefInline(src: string): Promise<{ data: string; mimeType: string } | null>
//
//  src/lib/video/ffmpeg.ts
//    ffmpegAvailable(): boolean
//    probeDurationSec(file: string): Promise<number>
//    standardizeClip(input: string, out: string, o: { w: number; h: number; fps?: number }): Promise<string>
//    burnOverlay(video: string, overlayPng: string, out: string, o?: { start?: number; end?: number }): Promise<string>
//    concatClips(clips: string[], out: string, o?: { crossfadeSec?: number; w?: number; h?: number }): Promise<string>
//    kenBurnsClip(imagePath: string, out: string, o: { durationSec: number; w: number; h: number }): Promise<string>   // still→video fallback (no AI)
//    appendEndCard(video: string, endCardPng: string, out: string, o?: { seconds?: number }): Promise<string>
//    muxAudio(video: string, o: { voice?: string; music?: string; musicVolume?: number; out: string }): Promise<string>
//    burnSubtitles(video: string, cues: Array<{ start: number; end: number; text: string }>, out: string, o?: { w?: number; h?: number }): Promise<string>
//
//  src/lib/ai/audio-client.ts
//    hasAudio(): boolean
//    generateVoiceover(text: string, opts?: { voice?: string; model?: string }): Promise<AudioResult | null>
//    generateMusic(mood: string, opts?: { durationSec?: number; model?: string }): Promise<AudioResult | null>
//
//  src/lib/video/camera.ts
//    CAMERA_PRESETS: CameraPreset[]
//    getCameraPreset(key?: string): CameraPreset | null
//    applyCamera(motionPrompt: string, key?: string): string
//    cameraVocabularyForPrompt(): string   // a compact menu Opus can pick from
//
//  src/lib/video/render-overlays.ts
//    renderShotOverlay(ctx: VideoBrandCtx, overlay: ShotOverlay, size: { w: number; h: number }, logoTokens: Record<string,string>): Promise<Buffer | null>
//    renderEndCard(ctx: VideoBrandCtx, endCard: { headline?: string; cta?: string; handle?: string }, size: { w: number; h: number }, logoTokens: Record<string,string>): Promise<Buffer>
//    renderLogoBug(logoTokens: Record<string,string>, size: { w: number; h: number }): Promise<Buffer | null>
//
//  src/lib/persona.ts
//    personaDir(brand: string): string
//    listPersonas(brand: string): Promise<Persona[]>
//    loadPersona(brand: string, name: string): Promise<Persona | null>
//    createPersona(brand: string, name: string, opts: { description?: string; refs: string[]; voice?: string }): Promise<Persona>
//    generatePersonaImage(persona: Persona, ctx: VideoBrandCtx, prompt: string, opts?: { aspect?: VideoAspect; count?: number }): Promise<string[]>  // data URLs
//    generateTalkingHead(persona: Persona, ctx: VideoBrandCtx, opts: { script: string; baseImage?: string; lipsyncModel?: string }): Promise<{ url: string; cost?: { amount_usd?: number } | null; model: string }>
//
//  src/lib/supercomputer.ts
//    batchGenerate(ctx: VideoBrandCtx, brief: string, opts: {
//      count: number; kind?: 'image'|'post'; quality?: 'ultra'|'standard'|'fast'|'brand';
//      aspect?: VideoAspect; concurrency?: number; references?: Array<{ data: string; mimeType: string }>;
//      onProgress?: (done: number, total: number, label: string) => void;
//    }): Promise<Array<{ dataUrl?: string; url?: string; concept: string; cost?: { amount_usd?: number } | null; model: string }>>
//
//  src/lib/video/assemble.ts
//    assembleVideoAd(ctx: VideoBrandCtx, plan: VideoPlan, opts: {
//      outFile: string; logoTokens: Record<string,string>;
//      withVoiceover?: boolean; withMusic?: boolean; withCaptions?: boolean;
//      videoModel?: string; sandbox?: boolean;
//      onProgress?: (done: number, total: number, label: string) => void;
//    }): Promise<VideoAdResult>
// ════════════════════════════════════════════════════════════════════════════
