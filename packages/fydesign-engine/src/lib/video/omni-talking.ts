// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  OMNI TALKING — the UGC "real creator" video path (identity + lip-sync + voice) ║
// ║                                                                              ║
// ║  When the user uploads a REAL photo of a person, the old keyframe→i2v→separate ║
// ║  -TTS stitch failed on every joint: Nano Banana re-drew a NEW face (distorted  ║
// ║  identity), Kling moved the mouth at random, and the TTS voice was muxed on    ║
// ║  top → guaranteed lip-sync mismatch + occasional wrong-language voice.         ║
// ║                                                                              ║
// ║  Gemini Omni image-to-video does all three NATIVELY in ONE model:             ║
// ║    • image_urls = the real photo  → preserves the exact subject identity       ║
// ║    • prompt with dialogue         → the person SPEAKS it, lip-synced           ║
// ║    • audio_ids = a voice profile  → guaranteed natural Spanish female voice     ║
// ║                                                                              ║
// ║  This is the path Gemini's own app uses — and why it crushed the stitched one. ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import os from 'node:os';
import path from 'node:path';
import { copyFile, writeFile, unlink } from 'node:fs/promises';

import type { VideoBrandCtx, VideoAspect, VideoAdResult } from './types';
import { callAIJSON } from '../ai/deepseek-client';
import { hostStillForMuapi } from '../ai/brand-image';
import { detectLang } from '../ai/audio-client';
import { generateVideo, createOmniVoiceProfile } from '../ai/muapi-client';
import {
  appendEndCard,
  burnSubtitles,
  probeDurationSec,
  probeHasAudio,
  faststart,
} from './ffmpeg';
import { renderEndCard } from './render-overlays';

const LOG = '[omni-talking]';
const OMNI_I2V = 'gemini-omni-image-to-video';

// Pre-created, reusable Spanish-female voice profile (base 'aoede' + LatAm description).
// Hard fallback so the voice is NEVER male/English even if per-run profile creation fails.
const FALLBACK_VOICE_ID =
  process.env.FY_OMNI_VOICE_ID || '62838748acf844638bda4f77b1e25691';

let _seq = 0;
function tmp(ext: string): string {
  return path.join(os.tmpdir(), `fyd-omni-${Date.now()}-${++_seq}.${ext}`);
}

async function writePng(buf: Buffer): Promise<string> {
  const dest = tmp('png');
  await writeFile(dest, buf);
  return dest;
}

async function downloadToTmp(url: string): Promise<string> {
  const dest = tmp('mp4');
  const res = await fetch(url, { signal: AbortSignal.timeout(300_000) });
  if (!res.ok) throw new Error(`${LOG} descarga falló: ${res.status} ${url.slice(0, 100)}`);
  await writeFile(dest, Buffer.from(await res.arrayBuffer()));
  return dest;
}

function dimsForAspect(aspect: VideoAspect): { w: number; h: number } {
  switch (aspect) {
    case '16:9': return { w: 1920, h: 1080 };
    case '4:3': return { w: 1440, h: 1080 };
    case '3:4': return { w: 1080, h: 1440 };
    case '1:1': return { w: 1080, h: 1080 };
    case '9:16':
    default: return { w: 1080, h: 1920 }; // default vertical
  }
}

/** Snap a requested duration to Omni's accepted set. */
function snapDuration(sec?: number): 4 | 6 | 8 | 10 {
  const allowed = [4, 6, 8, 10] as const;
  const want = sec ?? 8;
  return allowed.reduce((best, v) => (Math.abs(v - want) < Math.abs(best - want) ? v : best), allowed[0]);
}

const OMNI_BRAIN = `You script ONE authentic UGC creator video: a REAL person (their real photo is provided and their identity is preserved by the model) talks straight to camera. The model lip-syncs the person to the EXACT words you write and speaks them natively. Make it feel like a real creator filmed it on their phone — NOT an ad, NOT an AI render.

RULES:
- "script" = the EXACT words the person says, in the BRIEF'S LANGUAGE (match it precisely — if the brief is Spanish, write Spanish). First person, casual, like talking to a friend. It MUST fit the clip duration at a natural speaking pace (~2.2 words/second). Respect the HARD WORD CAP. Shorter beats crammed. NEVER invent stats, prices, percentages, follower counts, awards or testimonials — use ONLY real facts from the brand info; otherwise keep it emotional/qualitative.
- "scene" (English) = the real setting, wardrobe, available light and what is physically around the person — a real but WELL-SHOT phone video (the "editorial smartphone" look real creators get on Higgsfield/Instagram: flattering natural light like soft window light or golden daylight, a clean intentional frame, a nice real location). The person should look GOOD in a real way — NOT airbrushed, NOT a sloppy random snapshot either. The FACE/identity comes from the photo, so do NOT re-describe their face/age/body. KEEP the realism bans: NO studio/ring lighting, NO bokeh/shallow DOF (sharp background), NO color grade, NO "cinematic"/teal-orange. The goal is "real, beautifully captured", not "processed".
- "motion" (English) = what the person naturally does while talking (small gestures, holds the phone, a glance) + a believable handheld selfie camera feel. Subtle and real, never robotic.
- "hookText" = at most 3-4 words for a tiny on-screen hook caption, or "" (empty). Premium ads are sparse.
- "voiceDescription" (English) = the voice timbre to synthesize, matched to the person AND the brief's language. E.g. "young warm Latina woman, natural conversational Latin American Spanish, upbeat real-creator energy, never robotic, never English-accented".
- "endCard" = a clean closing brand moment: short headline + 2-3 word CTA (+ @handle ONLY if you truly know it).

Return STRICT JSON only — no markdown, no commentary.`;

interface RawOmniPlan {
  concept?: unknown;
  script?: unknown;
  scene?: unknown;
  motion?: unknown;
  hookText?: unknown;
  voiceDescription?: unknown;
  endCard?: unknown;
  caption?: unknown;
  hashtags?: unknown;
}

export interface OmniTalkingOpts {
  outFile: string;
  logoTokens: Record<string, string>;
  aspect?: VideoAspect;
  /** Clip length — snapped to Omni's {4,6,8,10}. Default 8. */
  durationSec?: number;
  /** Real photo(s) of the on-camera person/product (data URLs, paths or http URLs). */
  refImages: string[];
  /** Output resolution: 720p | 1080p | 4k. Default 1080p. */
  resolution?: string;
  withCaptions?: boolean;
  /** Append a 2s on-brand end card after the talking clip. Default true. */
  endCardOn?: boolean;
  onProgress?: (done: number, total: number, label: string) => void;
}

/**
 * Build a finished UGC "real creator talking" ad end-to-end with Gemini Omni.
 * Identity is preserved from the uploaded photo; the voice is lip-synced and spoken
 * natively in the brief's language. No keyframe regeneration, no separate TTS mux.
 */
export async function assembleOmniTalkingAd(
  ctx: VideoBrandCtx,
  brief: string,
  opts: OmniTalkingOpts,
): Promise<VideoAdResult> {
  const aspect = (opts.aspect || '9:16') as VideoAspect;
  const { w, h } = dimsForAspect(aspect);
  const durationSec = snapDuration(opts.durationSec);
  const endCardOn = opts.endCardOn !== false;

  let step = 0;
  const totalSteps = 5; // script, voice, generate, postprocess, export
  const progress = (label: string) => {
    step++;
    try { opts.onProgress?.(step, totalSteps, label); } catch { /* never crash */ }
  };

  if (!opts.refImages || opts.refImages.length === 0) {
    throw new Error(`${LOG} se requiere al menos una foto real (refImages) para el modo Omni de persona.`);
  }

  // ── 1. Opus scripts the UGC piece ─────────────────────────────────────────
  // Word cap tuned to the clip so the speaker never rushes or gets cut off.
  const wordCap = Math.max(6, Math.round(durationSec * 2.0));
  const ask = `BRAND: ${ctx.name}
PALETTE: ${ctx.colors.join(', ')} ${ctx.brandColors}
BRAND INFO (use ONLY these real facts — invent nothing): ${ctx.info || '(infer conservatively from the brand name)'}
FORMAT: ${aspect} vertical UGC selfie. DURATION: ${durationSec}s.
SCRIPT HARD CAP: ${wordCap} WORDS (it must fit ${durationSec}s at a natural pace — count your words; shorter is better).
A real photo of the on-camera person is provided; the model preserves their identity.

BRIEF: ${brief}

Return JSON:
{
  "concept": "one-line concept",
  "script": "the EXACT spoken words in the brief's language (<= ${wordCap} words)",
  "scene": "English: the real setting / wardrobe / available light around the person",
  "motion": "English: natural action while talking + handheld selfie camera feel",
  "hookText": "<=4 words or empty string",
  "voiceDescription": "English: voice timbre matching the person + brief language",
  "endCard": { "headline": "short closing line", "cta": "2-3 word CTA", "handle": "@brand or omit" },
  "caption": "ready-to-post caption in the brief's language",
  "hashtags": ["5-8 hashtags"]
}`;

  console.error(`${LOG} Opus escribiendo el guión UGC (${durationSec}s, cap ${wordCap} palabras)…`);
  const raw = await callAIJSON<RawOmniPlan>(ask, {
    system: OMNI_BRAIN,
    maxTokens: 2000,
    model: process.env.CLAUDE_CLI_MODEL || undefined,
  });
  if (!raw || !raw.script) {
    throw new Error(`${LOG} Opus no devolvió un guión válido.`);
  }

  const script = String(raw.script).trim();
  const scene = String(raw.scene || '').trim() || 'in a real, lived-in room with natural daylight';
  const motion = String(raw.motion || '').trim()
    || 'small natural gestures, holding the phone for a selfie, slight handheld sway';
  const hookText = String(raw.hookText || '').trim();
  const voiceDescription = String(raw.voiceDescription || '').trim();
  const caption = String(raw.caption || '').trim();
  const hashtags = Array.isArray(raw.hashtags) ? raw.hashtags.map(String).slice(0, 10) : [];
  const endCard =
    raw.endCard && typeof raw.endCard === 'object'
      ? (raw.endCard as { headline?: string; cta?: string; handle?: string })
      : { headline: ctx.name, cta: 'Más info' };

  const lang = detectLang(script);
  const langName = lang === 'es' ? 'natural Latin American Spanish' : 'natural English';
  console.error(`${LOG} guión (${lang}, ${script.split(/\s+/).length} palabras): "${script.slice(0, 90)}…"`);
  progress('Guión UGC (Opus)');

  // ── 2. Voice profile — guarantee a real Spanish-female voice ───────────────
  let voiceId = FALLBACK_VOICE_ID;
  try {
    const created = await createOmniVoiceProfile({
      baseVoice: 'aoede',
      name: `fy-${(ctx.name || 'brand').toLowerCase().replace(/[^a-z0-9]+/g, '-').slice(0, 30)}`,
      description:
        voiceDescription ||
        'young warm Latina woman, natural conversational Latin American Spanish, upbeat real-creator energy, never robotic, never English-accented',
      example: script.slice(0, 110),
    });
    if (created) voiceId = created;
    console.error(`${LOG} voz: ${created ? 'perfil creado' : 'fallback'} → ${voiceId}`);
  } catch {
    console.error(`${LOG} creación de voz falló → uso voz fallback ${voiceId}`);
  }
  progress('Voz (perfil Omni)');

  // ── 3. Host the real photo(s) + generate with Omni ─────────────────────────
  const hosted: string[] = [];
  for (const src of opts.refImages.slice(0, 4)) {
    try { hosted.push(await hostStillForMuapi(src)); }
    catch (e) { console.error(`${LOG} foto no se pudo subir (se omite):`, e instanceof Error ? e.message : e); }
  }
  if (hosted.length === 0) {
    throw new Error(`${LOG} no se pudo subir ninguna foto de referencia (revisa GCS / la imagen).`);
  }

  const omniPrompt = [
    'Authentic vertical UGC selfie video filmed on a phone.',
    `The real person in the reference photo (preserve their EXACT face, identity, hair, age and body — do not beautify, do not alter their appearance, no extra acne or weight) ${scene}.`,
    `They look into the camera and speak directly to the viewer, perfectly lip-synced, in ${langName}, saying exactly: "${script}".`,
    `${motion}.`,
    // Soul-editorial realism: real + well-shot (the look of Higgsfield's real demos),
    // explicitly NOT fake cinema — flattering natural light, sharp, true skin, modern phone.
    'Shot like a real editorial creator video on a modern phone (iPhone-clean): flattering natural available light, clean intentional framing, true-to-life skin with visible real texture and pores (never airbrushed, never plastic), crisp and sharp throughout with a fully in-focus background. Authentic and high-quality but NOT processed — no studio/ring lighting, no shallow-DOF bokeh, no color grade, no teal-and-orange, no AI sheen. It must look like real footage, not an AI render.',
    'Native synchronized audio: ONLY the person\'s voice speaking the line clearly in a natural human cadence, no background music, no other voices.',
  ].join(' ');

  console.error(`${LOG} generando video con Omni (${hosted.length} foto(s), ${durationSec}s, ${aspect})…`);
  const res = await generateVideo(omniPrompt, {
    model: OMNI_I2V,
    duration: durationSec,
    imageUrls: hosted,
    extra: {
      resolution: opts.resolution || '1080p',
      aspect_ratio: aspect,
      audio_ids: [voiceId],
    },
  });
  const outputUrl = res.outputs?.[0];
  if (!outputUrl) throw new Error(`${LOG} Omni no devolvió ningún video.`);
  let video = await downloadToTmp(outputUrl);
  const costUsd = res.cost?.amount_usd ?? 0;
  console.error(`${LOG} video Omni generado ($${costUsd}) → ${video}`);
  progress('Video Omni (identidad + voz sincronizada)');

  // ── 4. Post: captions (optional) + end card (optional) ─────────────────────
  if (opts.withCaptions && script) {
    try {
      const words = script.split(/\s+/).filter(Boolean);
      const cues: Array<{ start: number; end: number; text: string }> = [];
      const perCue = 6; // ~6 words per caption line
      const nCues = Math.max(1, Math.ceil(words.length / perCue));
      const slice = durationSec / nCues;
      for (let i = 0; i < nCues; i++) {
        const text = words.slice(i * perCue, (i + 1) * perCue).join(' ');
        if (text) cues.push({ start: i * slice, end: (i + 1) * slice, text });
      }
      if (cues.length) {
        video = await burnSubtitles(video, cues, tmp('mp4'), { w, h });
        console.error(`${LOG} subtítulos quemados (${cues.length})`);
      }
    } catch (e) {
      console.error(`${LOG} subtítulos fallaron (se omiten):`, e instanceof Error ? e.message : e);
    }
  }

  if (endCardOn) {
    let ecPng = '';
    try {
      // keepAudio preserves Omni's native lip-synced voice across the concat (the
      // default concat strips audio — the critical "muted video" bug). Gate on the
      // actual stream so a silent clip can't fail the concat and drop the end card.
      const hasAudio = await probeHasAudio(video);
      const ecBuf = await renderEndCard(ctx, endCard, { w, h }, opts.logoTokens);
      ecPng = await writePng(ecBuf);
      video = await appendEndCard(video, ecPng, tmp('mp4'), { seconds: 2.0, keepAudio: hasAudio });
      console.error(`${LOG} end card añadido (audio: ${hasAudio})`);
    } catch (e) {
      console.error(`${LOG} end card falló (se continúa):`, e instanceof Error ? e.message : e);
    } finally {
      if (ecPng) await unlink(ecPng).catch(() => undefined);
    }
  }
  progress('Subtítulos + end card');

  // ── 5. Export web-streamable (moov atom at front) ──────────────────────────
  try {
    await faststart(video, opts.outFile);
  } catch (e) {
    console.error(`${LOG} faststart falló, copio sin optimizar:`, e instanceof Error ? e.message : e);
    await copyFile(video, opts.outFile);
  }
  progress('Exportación final');

  const finalDur = await probeDurationSec(opts.outFile).catch(() => durationSec);
  // hookText is currently surfaced via the caption/overlay system upstream; kept for future burn-in.
  void hookText;

  return {
    file: opts.outFile,
    durationSec: finalDur,
    caption,
    hashtags,
    shots: 1,
    totalCostUsd: costUsd,
    model: `muapi:${OMNI_I2V}`,
  };
}
