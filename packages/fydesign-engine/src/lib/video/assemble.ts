// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  FyDesign VIDEO ASSEMBLER                                                    ║
// ║                                                                              ║
// ║  Turns a VideoPlan (from the Opus director) into a finished, on-brand .mp4: ║
// ║  generate each shot's keyframe → animate (image→video or ken-burns) →        ║
// ║  burn per-shot overlays → stitch with crossfade transitions → voiceover +    ║
// ║  music → end card → optional captions.                                       ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import os from 'node:os';
import path from 'node:path';
import { copyFile, writeFile } from 'node:fs/promises';
import { createWriteStream } from 'node:fs';

import type {
  VideoBrandCtx,
  VideoPlan,
  ShotClip,
  VideoAdResult,
} from './types';

import {
  generateBrandStill,
  dataUrlToTmpPng,
  hostStillForMuapi,
  loadRefInline,
} from '../ai/brand-image';

import {
  generateVideo,
  hasMuapi,
} from '../ai/muapi-client';

import { swapOutfit } from '../edit-pack';

import { applyCamera } from './camera';

import {
  standardizeClip,
  burnOverlay,
  concatClips,
  kenBurnsClip,
  appendEndCard,
  muxAudio,
  burnSubtitles,
  probeDurationSec,
  faststart,
} from './ffmpeg';

import {
  renderShotOverlay,
  renderEndCard,
} from './render-overlays';

import {
  generateVoiceover,
  generateMusic,
} from '../ai/audio-client';

// ── helpers ──────────────────────────────────────────────────────────────────

const LOG = '[assemble]';

let _tmpSeq = 0;
function tmpFile(ext: string): string {
  return path.join(os.tmpdir(), `fyd-assemble-${Date.now()}-${++_tmpSeq}.${ext}`);
}

/** Sum cost fields, treating absent / null as 0. */
function addCost(
  acc: number,
  cost?: { amount_usd?: number } | null,
): number {
  return acc + (cost?.amount_usd ?? 0);
}

/**
 * Download a remote URL to a local .mp4 temp file.
 * Returns the absolute path.
 */
async function downloadToTmp(url: string): Promise<string> {
  const dest = tmpFile('mp4');
  const res = await fetch(url, { signal: AbortSignal.timeout(300_000) });
  if (!res.ok) throw new Error(`${LOG} descarga falló: ${res.status} ${url.slice(0, 120)}`);
  const buf = Buffer.from(await res.arrayBuffer());
  await writeFile(dest, buf);
  return dest;
}

/**
 * Write a Buffer to a temp .png file.
 * Returns the absolute path.
 */
async function bufferToTmpPng(buf: Buffer): Promise<string> {
  const dest = tmpFile('png');
  await writeFile(dest, buf);
  return dest;
}

/**
 * Convert a data URL or an HTTP URL to a temporary PNG file.
 */
async function imageToTmpPng(urlOrData: string): Promise<string> {
  if (urlOrData.startsWith('data:')) {
    return dataUrlToTmpPng(urlOrData);
  }
  const dest = tmpFile('png');
  const res = await fetch(urlOrData, { signal: AbortSignal.timeout(60_000) });
  if (!res.ok) throw new Error(`${LOG} imageToTmpPng download failed: ${res.status} for ${urlOrData}`);
  const buf = Buffer.from(await res.arrayBuffer());
  await writeFile(dest, buf);
  return dest;
}

// ── per-shot concurrency queue (max 2 in-flight) ─────────────────────────────

type ShotTask = () => Promise<ShotClip>;

async function runWithConcurrency(
  tasks: ShotTask[],
  maxConcurrent: number,
): Promise<ShotClip[]> {
  // We run tasks in order, collecting results in order, but allow up to
  // `maxConcurrent` to be in-flight simultaneously.
  const results: ShotClip[] = new Array(tasks.length);
  let nextIdx = 0;

  async function runNext(): Promise<void> {
    while (nextIdx < tasks.length) {
      const idx = nextIdx++;
      results[idx] = await tasks[idx]();
    }
  }

  const workers = Array.from({ length: maxConcurrent }, () => runNext());
  await Promise.all(workers);
  return results;
}

// ── main export ───────────────────────────────────────────────────────────────

export async function assembleVideoAd(
  ctx: VideoBrandCtx,
  plan: VideoPlan,
  opts: {
    outFile: string;
    logoTokens: Record<string, string>;
    withVoiceover?: boolean;
    withMusic?: boolean;
    withCaptions?: boolean;
    videoModel?: string;
    sandbox?: boolean;
    /** Marketing Studio: a real product photo to composite into every keyframe. */
    productRef?: string;
    /**
     * Real model / product reference photos (data URLs, file paths, or http URLs).
     * Up to 3 are used to ground every keyframe so the same subject appears
     * consistently across shots instead of generic AI-generated people/scenes.
     */
    refImages?: string[];
    isFashionVton?: boolean;
    onProgress?: (done: number, total: number, label: string) => void;
  },
): Promise<VideoAdResult> {
  const {
    outFile,
    logoTokens,
    withVoiceover = false,
    withMusic = false,
    withCaptions = false,
    videoModel,
    sandbox = false,
    productRef,
    refImages,
    isFashionVton = false,
    onProgress,
  } = opts;

  const { w, h } = plan.format;
  const totalShots = plan.shots.length;
  // Major steps after shots: concat, endcard, audio, captions, copy = 5
  const totalSteps = totalShots + 5;
  let doneSteps = 0;

  function progress(label: string) {
    doneSteps++;
    try { onProgress?.(doneSteps, totalSteps, label); } catch { /* never crash */ }
  }

  console.error(`${LOG} iniciando montaje — ${totalShots} planos, formato ${plan.format.name} ${w}x${h}`);

  // ── Load user reference images (real model / product photos) ─────────────
  // Up to 3 refs from the caller; failures are silently skipped.
  const refsInline: Array<{ data: string; mimeType: string }> = [];
  if (refImages && refImages.length > 0) {
    const candidates = refImages.slice(0, 3);
    const settled = await Promise.allSettled(candidates.map((src) => loadRefInline(src)));
    for (const r of settled) {
      if (r.status === 'fulfilled' && r.value !== null) {
        refsInline.push(r.value);
      }
    }
    console.error(`${LOG} referencias de usuario cargadas: ${refsInline.length}/${candidates.length}`);
  }

  // ── 1–6: process each shot ──────────────────────────────────────────────────

  const tasks: ShotTask[] = plan.shots.map((shot, idx) => async () => {
    console.error(`${LOG} plano ${idx + 1}/${totalShots} (${shot.role}): generando still…`);

    // Step 1: keyframe still — composite the real product in when a productRef is
    // set (takes precedence). Otherwise use 'brand' quality (gemini-3-pro-image) so
    // every frame is photoreal and cinematic rather than a generic Imagen 4 stock
    // image. Brand logo + up to 2 asset URLs are loaded as additional references to
    // anchor the brand palette; user-supplied refImages ground the real subject.
    let still: { dataUrl: string; model: string };
    if (productRef) {
      try {
        const { compositeProductKeyframe } = await import('../product-compositing');
        still = await compositeProductKeyframe(productRef, shot.imagePrompt, ctx, { aspect: plan.format.aspect });
      } catch (e) {
        console.error(`${LOG} product compositing falló, uso still normal:`, e instanceof Error ? e.message : e);
        // Fall through to brand-quality generation below.
        still = { dataUrl: '', model: '' };
      }
    } else {
      still = { dataUrl: '', model: '' };
    }

    if (!still.dataUrl) {
      // Build brand-side reference set (logo + up to 2 asset URLs), best-effort.
      const brandRefCandidates: Array<Promise<{ data: string; mimeType: string } | null>> = [
        loadRefInline(ctx.logo),
        ...ctx.assets.slice(0, 2).map((a) => loadRefInline(a.url)),
      ];
      const brandSettled = await Promise.allSettled(brandRefCandidates);
      const brandRefs: Array<{ data: string; mimeType: string }> = [];
      for (const r of brandSettled) {
        if (r.status === 'fulfilled' && r.value !== null) {
          brandRefs.push(r.value);
        }
      }

      const allRefs = [...refsInline, ...brandRefs].slice(0, 4);
      const subjectAnchor = refsInline.length
        ? ' Keep the SAME subject as the reference images: identical identity/face/wardrobe/product, only scene/pose/angle change.'
        : '';
      // Realism anchor — make it look like a REAL photo, not an AI render.
      // No vague adjectives, no artificial bokeh, no heavy grade (those are the AI tells).
      let basePrompt = shot.imagePrompt;
      if (isFashionVton) {
        basePrompt += ' The model is wearing a simple solid neutral t-shirt.';
      }
      const enhancedPrompt =
        basePrompt +
        ' Looks like a real candid photo taken on a real camera or smartphone in natural available light; true-to-life skin texture and pores, natural imperfections, a sharp in-focus background (no artificial blur), no heavy color grade, no plastic skin.' +
        subjectAnchor;

      still = await generateBrandStill(enhancedPrompt, {
        quality: 'brand',
        aspect: plan.format.aspect,
        references: allRefs,
        allowUi: shot.allowUi,
        allowText: shot.allowText,
      });
    }

    if (isFashionVton && refImages && refImages.length > 0) {
      console.error(`${LOG} plano ${idx + 1}: ejecutando VTON outfit swap…`);
      try {
        const garmentRef = refImages[idx % refImages.length];
        const garmentUrl = await hostStillForMuapi(garmentRef);
        const swapRes = await swapOutfit(still.dataUrl, 'wear the reference garment', garmentUrl);
        if (swapRes.url) {
          console.error(`${LOG} plano ${idx + 1}: VTON outfit swap exitoso → ${swapRes.url}`);
          still = { dataUrl: swapRes.url, model: `${still.model} + ${swapRes.model}` };
        } else {
          console.error(`${LOG} plano ${idx + 1}: VTON outfit swap no devolvió url, usando still base.`);
        }
      } catch (err) {
        console.error(`${LOG} plano ${idx + 1}: VTON outfit swap falló, usando still base. Error:`, err);
      }
    }

    const stillPng = await imageToTmpPng(still.dataUrl);

    // Step 2: motion prompt — append an anti-"slop" preservation clause so the
    // video model animates the first frame faithfully (no morphing/warping/mutations).
    // Pattern from the Gemini Omni i2v best-practices: describe motion + light only,
    // and lock identity/geometry/palette to the keyframe.
    const ANTI_SLOP = ' Animate the attached first frame faithfully with subtle, REAL motion — natural micro-movement and a gentle, slightly handheld camera move (the way a real person films), no jitter, no random panning. PRESERVE the first frame EXACTLY: identity, facial structure, anatomy, proportions, framing, materials, colors, lighting and SHARPNESS — do not re-light, do not reframe, do not brighten, keep the background sharp. Avoid: morphing, warping, melting, extra limbs/fingers, deformed geometry, plastic skin, oversaturation, artificial background blur, baked text. Keep it looking like real footage, not an AI render.';
    const motion = applyCamera(shot.motionPrompt, shot.cameraPreset) + ANTI_SLOP;

    // Step 3: image→video (with fallback)
    let rawMp4: string;
    let shotCost: { amount_usd?: number } | null = null;
    let usedModel = 'kenburns';

    const canUseVideo = hasMuapi() && !sandbox;

    if (canUseVideo) {
      try {
        console.error(`${LOG} plano ${idx + 1}: image→video con muapi (${videoModel || 'default'})…`);
        const imageUrl = await hostStillForMuapi(still.dataUrl);
        const res = await generateVideo(motion, {
          model: videoModel,
          duration: shot.durationSec,
          imageUrl,
          extra: { aspect_ratio: plan.format.aspect },
        });

        const outputUrl = (res.outputs && res.outputs[0]) ? res.outputs[0] : null;
        if (!outputUrl) {
          throw new Error(`${LOG} muapi no devolvió outputs para el plano ${idx + 1}`);
        }

        rawMp4 = await downloadToTmp(outputUrl);
        shotCost = res.cost ?? null;
        usedModel = `muapi:${res.model}`;
        console.error(`${LOG} plano ${idx + 1}: video generado (${usedModel}, $${shotCost?.amount_usd ?? 0})`);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        console.error(`${LOG} plano ${idx + 1}: falló image→video, usando ken-burns como fallback. Error: ${msg}`);
        rawMp4 = await kenBurnsClip(stillPng, tmpFile('mp4'), {
          durationSec: shot.durationSec,
          w,
          h,
        });
        usedModel = 'kenburns';
      }
    } else {
      console.error(`${LOG} plano ${idx + 1}: muapi no disponible o sandbox=true → ken-burns`);
      rawMp4 = await kenBurnsClip(stillPng, tmpFile('mp4'), {
        durationSec: shot.durationSec,
        w,
        h,
      });
    }

    // Step 4: standardize clip
    let clip = await standardizeClip(rawMp4, tmpFile('mp4'), { w, h, fps: 30 });

    // Step 5: burn shot overlay (if any)
    if (shot.overlay) {
      try {
        const ovBuf = await renderShotOverlay(ctx, shot.overlay, { w, h }, logoTokens);
        if (ovBuf) {
          const ovPng = await bufferToTmpPng(ovBuf);
          clip = await burnOverlay(clip, ovPng, tmpFile('mp4'));
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        console.error(`${LOG} plano ${idx + 1}: overlay falló (se ignora). Error: ${msg}`);
      }
    }

    progress(`Plano ${idx + 1}/${totalShots} — ${shot.role}`);
    console.error(`${LOG} plano ${idx + 1} completado → ${clip}`);

    return {
      shot,
      file: clip,
      cost: shotCost,
      model: usedModel,
    } satisfies ShotClip;
  });

  // Process shots with max 2 concurrent but preserve order
  const clips = await runWithConcurrency(tasks, 2);

  // ── Step 7: concatenate ────────────────────────────────────────────────────

  console.error(`${LOG} concatenando ${clips.length} clips…`);
  const tmp = os.tmpdir();
  let video = await concatClips(
    clips.map((c) => c.file),
    tmpFile('mp4'),
    { crossfadeSec: 0.4, w, h },
  );
  progress('Concatenación de clips');

  // ── Step 8: end card ───────────────────────────────────────────────────────

  console.error(`${LOG} generando end card…`);
  try {
    const ecData = plan.endCard ?? { headline: ctx.name, cta: 'Más info' };
    const ecBuf = await renderEndCard(ctx, ecData, { w, h }, logoTokens);
    const ecPng = await bufferToTmpPng(ecBuf);
    video = await appendEndCard(video, ecPng, tmpFile('mp4'), { seconds: 2.5 });
    console.error(`${LOG} end card añadido`);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error(`${LOG} end card falló (se continúa sin él). Error: ${msg}`);
  }
  progress('End card');

  // ── Step 9: audio (voiceover + music) ────────────────────────────────────

  let voiceFile: string | undefined;
  let musicFile: string | undefined;

  if (withVoiceover && plan.voiceover) {
    try {
      console.error(`${LOG} generando voiceover…`);
      const vo = await generateVoiceover(plan.voiceover);
      voiceFile = vo?.file;
      if (voiceFile) console.error(`${LOG} voiceover listo → ${voiceFile}`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error(`${LOG} voiceover falló (se omite). Error: ${msg}`);
    }
  }

  if (withMusic && plan.musicMood) {
    try {
      console.error(`${LOG} generando música (${plan.musicMood})…`);
      const totalDur = await probeDurationSec(video);
      const mu = await generateMusic(plan.musicMood, { durationSec: Math.ceil(totalDur) });
      musicFile = mu?.file;
      if (musicFile) console.error(`${LOG} música lista → ${musicFile}`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error(`${LOG} generación de música falló (se omite). Error: ${msg}`);
    }
  }

  if (voiceFile || musicFile) {
    try {
      console.error(`${LOG} mezclando audio…`);
      video = await muxAudio(video, {
        voice: voiceFile,
        music: musicFile,
        out: tmpFile('mp4'),
      });
      console.error(`${LOG} audio mezclado`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error(`${LOG} mux de audio falló (se continúa sin audio). Error: ${msg}`);
    }
  }
  progress('Audio (voz + música)');

  // ── Step 10: captions (optional) ──────────────────────────────────────────

  if (withCaptions && plan.voiceover && voiceFile) {
    try {
      console.error(`${LOG} generando subtítulos (naive)…`);

      // Build per-shot time cues: divide VO text proportionally to shot durations.
      const voText = plan.voiceover.trim();
      const words = voText.split(/\s+/).filter(Boolean);
      const totalShotDuration = plan.shots.reduce((s, sh) => s + sh.durationSec, 0);

      // Cumulative time offsets
      let cursor = 0;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const cues: Array<{ start: number; end: number; text: string }> = [];

      plan.shots.forEach((shot) => {
        const frac = shot.durationSec / totalShotDuration;
        const shotWords = Math.max(1, Math.round(words.length * frac));
        const chunkWords = words.splice(0, shotWords);
        const chunkText = chunkWords.join(' ');
        const start = cursor;
        const end = cursor + shot.durationSec;
        if (chunkText) cues.push({ start, end, text: chunkText });
        cursor = end;
      });

      // Remaining words (rounding residue) go to the last cue or a new one
      if (words.length > 0 && cues.length > 0) {
        cues[cues.length - 1].text += ' ' + words.join(' ');
      }

      if (cues.length > 0) {
        video = await burnSubtitles(video, cues, tmpFile('mp4'), { w, h });
        console.error(`${LOG} subtítulos quemados (${cues.length} cues)`);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error(`${LOG} subtítulos fallaron (se omiten). Error: ${msg}`);
    }
  }
  progress('Subtítulos');

  // ── Step 11: export to outFile as a WEB-STREAMABLE mp4 (moov at front) ──────

  console.error(`${LOG} exportando (faststart) a ${outFile}…`);
  try {
    await faststart(video, outFile);
  } catch (e) {
    console.error(`${LOG} faststart falló, copio sin optimizar:`, e instanceof Error ? e.message : e);
    await copyFile(video, outFile);
  }
  progress('Exportación final');

  const durationSec = await probeDurationSec(outFile);
  const totalCostUsd = clips.reduce((acc, c) => addCost(acc, c.cost), 0);

  // Determine the dominant model label
  const hasReal = clips.some((c) => c.model.startsWith('muapi:'));
  const modelLabel = hasReal
    ? `muapi:${videoModel || 'default'}`
    : 'kenburns';

  console.error(
    `${LOG} montaje completo → ${outFile} (${durationSec.toFixed(1)}s, ${clips.length} planos, $${totalCostUsd.toFixed(4)})`,
  );

  return {
    file: outFile,
    durationSec,
    caption: plan.caption,
    hashtags: plan.hashtags,
    shots: clips.length,
    totalCostUsd,
    model: modelLabel,
  };
}
