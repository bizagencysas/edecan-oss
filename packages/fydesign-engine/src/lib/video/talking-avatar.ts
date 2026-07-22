// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  TALKING AVATAR — "make my REAL uploaded person talk" (lip-sync, no AI face)    ║
// ║                                                                              ║
// ║  Gemini Omni refuses real faces (PROMINENT_PEOPLE_FILTER — Google's anti-      ║
// ║  deepfake guardrail). The right tool is a lip-sync / talking-avatar model that ║
// ║  ANIMATES THE REAL PHOTO directly (so it's literally her real face moving —    ║
// ║  the LEAST "AI-looking" option) and syncs the mouth to a real voice clip:       ║
// ║                                                                              ║
// ║    real photo (image_url) + Spanish TTS voice (audio_url) → talking video.     ║
// ║                                                                              ║
// ║  Model chain (newest/most-realistic first): ltx-2.3-lipsync (1080p, preserves  ║
// ║  lighting/identity) → kling-v2-avatar-pro → wan2.2-speech-to-video →            ║
// ║  infinitetalk. None have a real-person filter — they're built for UGC avatars. ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import os from 'node:os';
import path from 'node:path';
import { copyFile, writeFile, unlink } from 'node:fs/promises';

import type { VideoBrandCtx, VideoAspect, VideoAdResult } from './types';
import { callAIJSON } from '../ai/deepseek-client';
import { hostStillForMuapi } from '../ai/brand-image';
import { generateVoiceoverUrl } from '../ai/audio-client';
import { generateTalkingVideo, generateIdentityImage } from '../ai/muapi-client';
import { TALKING_AVATAR_CHAIN, IDENTITY_SCENE_CHAIN } from './models';
import {
  appendEndCard,
  burnSubtitles,
  probeDurationSec,
  probeHasAudio,
  muxAudio,
  faststart,
} from './ffmpeg';
import { renderEndCard } from './render-overlays';

const LOG = '[talking-avatar]';

let _seq = 0;
function tmp(ext: string): string {
  return path.join(os.tmpdir(), `fyd-avatar-${Date.now()}-${++_seq}.${ext}`);
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
async function downloadAudio(url: string): Promise<string> {
  const dest = tmp('mp3');
  const res = await fetch(url, { signal: AbortSignal.timeout(120_000) });
  if (!res.ok) throw new Error(`${LOG} descarga audio falló: ${res.status}`);
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
    default: return { w: 1080, h: 1920 };
  }
}

const AVATAR_BRAIN = `You script ONE authentic UGC creator video. The user's REAL face is provided; an identity-lock model first PLACES that exact face into a brand-new scene you describe, then a lip-sync model makes her speak. So you are directing a NEW shot of her — not animating her selfie. Make it feel like a real creator filmed it on their phone — NOT an ad, NOT an AI render.

RULES:
- "script" = the EXACT words the person says, in the BRIEF'S LANGUAGE (match it — Spanish brief → Spanish script). First person, casual, like talking to a friend. It MUST fit the clip duration at a natural speaking pace (~2.4 words/second). Respect the HARD WORD CAP. Shorter beats crammed. NEVER invent stats, prices, percentages, follower counts, awards or testimonials — use ONLY real facts from the brand info; otherwise keep it emotional/qualitative.
- "scene" (English) = the NEW setting to generate her in — a real, candid UGC moment: where she is, the real environment, wardrobe, natural available light, what she's doing/holding (e.g. her phone, the product). CRITICAL for identity lock: she must be FRONTAL and fairly CLOSE (a selfie / talking-to-camera framing, head-and-shoulders), looking straight at the camera, mouth relaxed/neutral. NO full-body, NO profile/3-4 turns, NO extreme angles (those make the face drift). Real natural light, real room, sharp — NOT studio, NOT bokeh, NOT cinematic grade.
- "expression" (English) = the micro-behaviour the lip-sync model should add: warm natural smile, genuine eye contact, small head nods, relaxed candid energy. Subtle and human, never theatrical. Do NOT re-describe her face/age/body (it comes from the photo).
- "hookText" = at most 3-4 words for a tiny on-screen hook caption, or "" (empty).
- "endCard" = a clean closing brand moment: short headline + 2-3 word CTA (+ @handle ONLY if you truly know it).

Return STRICT JSON only — no markdown, no commentary.`;

interface RawAvatarPlan {
  concept?: unknown;
  script?: unknown;
  scene?: unknown;
  expression?: unknown;
  hookText?: unknown;
  endCard?: unknown;
  caption?: unknown;
  hashtags?: unknown;
}

export interface TalkingAvatarOpts {
  outFile: string;
  logoTokens: Record<string, string>;
  aspect?: VideoAspect;
  /** Target clip length (s) — used only for the script word-cap; the lip-sync clip
   *  length is driven by the generated audio. */
  durationSec?: number;
  /** Real photo(s) of the on-camera person (data URLs, paths or http URLs). */
  refImages: string[];
  /** Output resolution hint: 480p | 720p | 1080p (clamped per model). Default 1080p. */
  resolution?: string;
  /** Explicit lip-sync model override (else the newest-first chain). */
  model?: string;
  withCaptions?: boolean;
  endCardOn?: boolean;
  onProgress?: (done: number, total: number, label: string) => void;
}

/**
 * Build a finished UGC "real creator talking" ad by animating the REAL uploaded photo
 * with a lip-sync model + a real Spanish voice. No face regeneration, no Google filter.
 */
export async function assembleTalkingAvatarAd(
  ctx: VideoBrandCtx,
  brief: string,
  opts: TalkingAvatarOpts,
): Promise<VideoAdResult> {
  const aspect = (opts.aspect || '9:16') as VideoAspect;
  const { w, h } = dimsForAspect(aspect);
  const durationSec = Math.max(4, Math.min(20, opts.durationSec || 10));
  const endCardOn = opts.endCardOn !== false;

  let step = 0;
  const totalSteps = 6; // script, voice, scene(identity), lipsync, postprocess, export
  const progress = (label: string) => {
    step++;
    try { opts.onProgress?.(step, totalSteps, label); } catch { /* never crash */ }
  };

  if (!opts.refImages || opts.refImages.length === 0) {
    throw new Error(`${LOG} se requiere una foto real (refImages) de la persona para el modo avatar.`);
  }

  // ── 1. Opus scripts the UGC piece ─────────────────────────────────────────
  const wordCap = Math.max(6, Math.round(durationSec * 2.2));
  const ask = `BRAND: ${ctx.name}
PALETTE: ${ctx.colors.join(', ')} ${ctx.brandColors}
BRAND INFO (use ONLY these real facts — invent nothing): ${ctx.info || '(infer conservatively from the brand name)'}
FORMAT: ${aspect} vertical UGC selfie. DURATION target: ${durationSec}s.
SCRIPT HARD CAP: ${wordCap} WORDS (must fit ~${durationSec}s at a natural pace — count your words; shorter is better).
A real photo of the on-camera person is provided. An identity-lock model will place her EXACT face into the NEW scene you describe, then a lip-sync model makes her speak.

BRIEF: ${brief}

Return JSON:
{
  "concept": "one-line concept",
  "script": "the EXACT spoken words in the brief's language (<= ${wordCap} words)",
  "scene": "English: the NEW real UGC setting to generate her in — FRONTAL, close selfie/talking-to-camera framing, looking at camera, mouth relaxed, natural light (see rules)",
  "expression": "English: warm natural delivery / micro-behaviour for the lip-sync model",
  "hookText": "<=4 words or empty string",
  "endCard": { "headline": "short closing line", "cta": "2-3 word CTA", "handle": "@brand or omit" },
  "caption": "ready-to-post caption in the brief's language",
  "hashtags": ["5-8 hashtags"]
}`;

  console.error(`${LOG} Opus escribiendo el guión UGC (${durationSec}s, cap ${wordCap} palabras)…`);
  const raw = await callAIJSON<RawAvatarPlan>(ask, {
    system: AVATAR_BRAIN,
    maxTokens: 1600,
    model: process.env.CLAUDE_CLI_MODEL || undefined,
  });
  if (!raw || !raw.script) throw new Error(`${LOG} Opus no devolvió un guión válido.`);

  const script = String(raw.script).trim();
  const scene = String(raw.scene || '').trim()
    || 'in a real, lived-in room with natural daylight, head-and-shoulders selfie framing, looking straight at the camera';
  const expression = String(raw.expression || '').trim()
    || 'warm natural smile, genuine eye contact, small relaxed head movement, candid real-creator energy';
  const hookText = String(raw.hookText || '').trim();
  const caption = String(raw.caption || '').trim();
  const hashtags = Array.isArray(raw.hashtags) ? raw.hashtags.map(String).slice(0, 10) : [];
  const endCard =
    raw.endCard && typeof raw.endCard === 'object'
      ? (raw.endCard as { headline?: string; cta?: string; handle?: string })
      : { headline: ctx.name, cta: 'Más info' };
  console.error(`${LOG} guión (${script.split(/\s+/).length} palabras): "${script.slice(0, 90)}…"`);
  progress('Guión UGC (Opus)');

  // ── 2. Real Spanish voice → hosted audio URL (feeds the lip-sync model) ─────
  const vo = await generateVoiceoverUrl(script);
  if (!vo || !vo.url) {
    throw new Error(`${LOG} no se pudo generar la voz (TTS Muapi). Revisa MUAPI_API_KEY / crédito.`);
  }
  const audioUrl = vo.url;
  let costUsd = vo.cost?.amount_usd ?? 0;
  console.error(`${LOG} voz lista (TTS) → ${audioUrl.slice(0, 80)}`);
  progress('Voz española (TTS)');

  // ── 3. Host the real face photo ────────────────────────────────────────────
  const photoUrl = await hostStillForMuapi(opts.refImages[0]);
  if (!/^https?:\/\//i.test(photoUrl)) {
    throw new Error(`${LOG} la foto no se pudo subir a una URL pública (GCS). Configura GCS para el modo persona real.`);
  }

  // ── 3b. STAGE 1 — IDENTITY → NEW SCENE: lock her exact face into a brand-new UGC
  // scene (a generated still). This is the key fix: the user wants their face used to
  // CREATE a new video, not their exact selfie puppeted. flux-pulid INJECTS the face
  // identity (doesn't redraw it), so the person stays the same across the new scene.
  const scenePrompt =
    `A real candid UGC phone photo of this exact woman (keep her EXACT face and identity — same person), ${scene}. ` +
    `She looks straight into the camera, relaxed neutral mouth, natural available light, true-to-life skin texture, sharp and in focus. ` +
    `Vertical phone selfie, authentic and real — NOT studio lighting, NO background blur, NO color grade, NOT an AI render.`;
  let stageImageUrl = photoUrl; // fallback: the original selfie if scene-gen fails
  for (const m of IDENTITY_SCENE_CHAIN) {
    try {
      console.error(`${LOG} STAGE 1 escena nueva con ${m}…`);
      const r = await generateIdentityImage({ faceUrl: photoUrl, prompt: scenePrompt, aspect, model: m });
      const url = r.outputs?.[0];
      if (url && /^https?:\/\//i.test(url)) {
        stageImageUrl = url;
        costUsd += r.cost?.amount_usd ?? 0;
        console.error(`${LOG} ✓ STAGE 1 (${m}) generó la escena nueva (acum $${costUsd.toFixed(2)})`);
        break;
      }
      throw new Error('sin output http');
    } catch (e) {
      console.error(`${LOG} STAGE 1 ${m} falló, pruebo el siguiente:`, e instanceof Error ? e.message.slice(0, 140) : e);
    }
  }
  if (stageImageUrl === photoUrl) {
    console.error(`${LOG} ⚠ STAGE 1 no generó escena nueva — uso la foto original como keyframe`);
  }
  progress('Escena nueva con tu cara (identidad)');

  // ── 4. STAGE 2 — lip-sync the new-scene keyframe to the voice (model chain) ──
  const chain = opts.model ? [opts.model, ...TALKING_AVATAR_CHAIN] : [...TALKING_AVATAR_CHAIN];
  let video: string | null = null;
  let usedModel = '';
  const errors: string[] = [];
  for (const model of chain) {
    try {
      console.error(`${LOG} lip-sync con ${model}…`);
      const res = await generateTalkingVideo({
        model,
        imageUrl: stageImageUrl,
        audioUrl,
        prompt: expression,
        resolution: opts.resolution || '1080p',
      });
      const url = res.outputs?.[0];
      if (!url) throw new Error('sin output');
      video = await downloadToTmp(url);
      usedModel = model;
      costUsd += res.cost?.amount_usd ?? 0;
      console.error(`${LOG} ✓ ${model} generó el video (acum $${costUsd.toFixed(2)})`);
      break;
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      errors.push(`${model}: ${msg.slice(0, 120)}`);
      console.error(`${LOG} ${model} falló, pruebo el siguiente. ${msg.slice(0, 160)}`);
    }
  }
  if (!video) {
    throw new Error(`${LOG} todos los modelos de lip-sync fallaron:\n${errors.join('\n')}`);
  }

  // Defensive: a talking-avatar model SHOULD bake the voice into its output, but if any
  // returns a SILENT video (lip-moved but muted), re-mux the exact TTS audio it lip-synced
  // to — perfect sync (the mouth was generated FROM this audio). Guarantees an audible ad.
  try {
    if (!(await probeHasAudio(video))) {
      console.error(`${LOG} ⚠ ${usedModel} devolvió video SIN audio — re-inyecto la voz`);
      const audioFile = await downloadAudio(audioUrl);
      video = await muxAudio(video, { voice: audioFile, out: tmp('mp4') });
    }
  } catch (e) {
    console.error(`${LOG} chequeo/mux de audio falló (se continúa):`, e instanceof Error ? e.message : e);
  }
  progress(`Lip-sync (${usedModel})`);

  // ── 4. Captions (optional) + brand end card (audio-preserving) ─────────────
  if (opts.withCaptions && script) {
    try {
      const words = script.split(/\s+/).filter(Boolean);
      const clipDur = await probeDurationSec(video).catch(() => durationSec);
      const perCue = 6;
      const nCues = Math.max(1, Math.ceil(words.length / perCue));
      const slice = clipDur / nCues;
      const cues: Array<{ start: number; end: number; text: string }> = [];
      for (let i = 0; i < nCues; i++) {
        const text = words.slice(i * perCue, (i + 1) * perCue).join(' ');
        if (text) cues.push({ start: i * slice, end: (i + 1) * slice, text });
      }
      if (cues.length) {
        video = await burnSubtitles(video, cues, tmp('mp4'), { w, h });
      }
    } catch (e) {
      console.error(`${LOG} subtítulos fallaron (se omiten):`, e instanceof Error ? e.message : e);
    }
  }

  if (endCardOn) {
    let ecPng = '';
    try {
      // keepAudio MUST match reality: if the clip somehow ended up silent, an audio-
      // preserving concat would fail (no [0:a] stream) and silently drop the end card.
      const hasAudio = await probeHasAudio(video);
      const ecBuf = await renderEndCard(ctx, endCard, { w, h }, opts.logoTokens);
      ecPng = await writePng(ecBuf);
      video = await appendEndCard(video, ecPng, tmp('mp4'), { seconds: 2.0, keepAudio: hasAudio });
    } catch (e) {
      console.error(`${LOG} end card falló (se continúa):`, e instanceof Error ? e.message : e);
    } finally {
      if (ecPng) await unlink(ecPng).catch(() => undefined);
    }
  }
  progress('Subtítulos + end card');

  // ── 5. Export web-streamable ───────────────────────────────────────────────
  try {
    await faststart(video, opts.outFile);
  } catch (e) {
    console.error(`${LOG} faststart falló, copio sin optimizar:`, e instanceof Error ? e.message : e);
    await copyFile(video, opts.outFile);
  }
  progress('Exportación final');

  const finalDur = await probeDurationSec(opts.outFile).catch(() => durationSec);
  void hookText; // reserved for an on-screen hook overlay (future)

  return {
    file: opts.outFile,
    durationSec: finalDur,
    caption,
    hashtags,
    shots: 1,
    totalCostUsd: costUsd,
    model: `muapi:${usedModel}`,
  };
}
