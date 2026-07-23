// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Opus VIDEO DIRECTOR — the mind behind a "brutal" on-brand motion ad           ║
// ║                                                                              ║
// ║  The configured text provider acts as creative director and cinematographer.   ║
// ║  brief into a structured shot list (text-free keyframes + camera moves), a     ║
// ║  voiceover script, a music mood, on-screen copy and an end card. The           ║
// ║  assembler (src/lib/video/assemble.ts) then BUILDS it shot-by-shot.            ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { callAIJSON } from '../ai/deepseek-client';
import { cinemaVocabularyForPrompt, opticsClause } from './camera';
import {
  motionsMenu,
  cinemaMenu,
  CAMERA_BODIES,
  GENRES,
  COLOR_GRADES,
  SPEED_RAMPS,
  buildCinemaPrompt,
} from '../presets/catalog';
import type { VideoBrandCtx, VideoPlan, VideoAspect, VideoFormat, Shot } from './types';

function dimsForAspect(aspect: VideoAspect): { w: number; h: number } {
  switch (aspect) {
    case '9:16': return { w: 1080, h: 1920 };
    case '16:9': return { w: 1920, h: 1080 };
    case '4:3': return { w: 1440, h: 1080 };
    case '3:4': return { w: 1080, h: 1440 };
    default: return { w: 1080, h: 1080 };
  }
}

/** Hard-cap a voiceover script to maxWords, trimming back to a clean sentence end. */
function capWords(text: string, maxWords: number): string {
  const words = text.split(/\s+/).filter(Boolean);
  if (words.length <= maxWords) return text;
  const truncated = words.slice(0, maxWords).join(' ');
  const lastStop = Math.max(truncated.lastIndexOf('.'), truncated.lastIndexOf('!'), truncated.lastIndexOf('?'));
  return lastStop > truncated.length * 0.5 ? truncated.slice(0, lastStop + 1) : truncated + '.';
}

const DIRECTOR_BRAIN = `You are a creative director for SHORT social/brand video ads. Your #1 MANDATE: the result must look REAL — like authentic footage a real person filmed or a real production actually captured — NEVER like a polished "AI render" or a processed stock video. Default to AUTHENTIC REALISM (candid, true-to-life, UGC-native). Only use a polished cinematic look if the brief EXPLICITLY asks for it.

NON-NEGOTIABLE CRAFT RULES:
- HOOK in the first shot: the opening 1.5s must stop the scroll — with a REAL, relatable, human moment, not a stylized AI flourish.
- Keyframe ("imagePrompt") = the first frame. Write it for MAXIMUM REALISM: a real, candid photograph shot on a real camera or a modern smartphone, in NATURAL available light; describe the exact real subject, the real environment and the real lighting; true-to-life skin with visible texture/pores and small natural imperfections; natural human proportions and a genuine, un-posed expression; a SHARP, clearly-readable background (deep or normal focus). It should look like a real photo a real person actually took.
- HARD BANS — these are the "AI tells" people hate, NEVER do them unless the brief explicitly demands a cinematic look: NO artificial background blur / shallow-DOF bokeh (sharp backgrounds); NO heavy color grade (no teal-and-orange, no moody chiaroscuro); NO dramatic studio/rim/volumetric lighting; NO plastic, airbrushed or waxy skin; NO oversaturation; NO anamorphic look or lens-flare gimmicks. Aim for ordinary, believable, real light.
- ANTI-SLOP ADJECTIVES: NEVER use empty hype words ("photorealistic", "hyperdetailed", "amazing", "stunning", "beautiful", "cinematic", "epic", "4k", "high quality"). Describe real subjects, real light and real materials — that is the language these models obey.
- LOGOS, TEXT, & UI SCREENS: By default, keep keyframes free of baked-in text and complex UIs (leaving clean negative space for overlays). However, if the user's brief or the narrative specifically requires showing a logo, sign, text, or device interface in the scene (for example, a computer/phone screen displaying a chart or the brand app, or a logo mounted on a wall), describe it clearly in "imagePrompt" and set "allowText": true and/or "allowUi": true in the shot's JSON. If the screen should be off/blank, leave them false.
- TEXT-FREE & UI-FREE keyframe: when allowText/allowUi are false, ensure no readable text, signage, labels, logos or app/phone screens in the pixels; a device screen is blank/off. Leave calm space for copy.
- motionPrompt = three parts separated by " ; ": (1) MOVEMENT — what the subject really does, the speed, with believable weight and gravity; (2) CAMERA — a NATURAL move (a real handheld or gimbal feel, a gentle push-in or follow) the way a real operator shoots, slight and organic, NOT robotic, NOT over-smooth, NOT random; (3) AMBIENT — small real-world motion (hair, fabric, breath, background people, subtle light shifts). Keep it subtle and believable; PRESERVE the keyframe's identity, framing, lighting and sharpness; no morphing, no warping.
- CINEMA STUDIO fields (lens, look, cameraBody, genre, colorGrade, speedRamp) are OPTIONAL and must be LEFT EMPTY ("") by DEFAULT — an explicit cinematic look is opt-in only. Do NOT default to anamorphic, teal-and-orange, moody grades or specific camera bodies; realism beats polish here.
- NARRATIVE: hook → a real moment → the value → CTA. For an authentic feel, prefer FEWER, connected shots (even 1 single coherent shot) over many disjoint stock-like scenes.
- On-screen copy (overlay) is VERY SPARSE — premium ads let the visual breathe. AT MOST ONE text element per shot, and MOST shots carry NONE. NEVER stack headline + subtext + cta + lowerThird on the same shot (that looks cluttered and amateur). Prefer a single SHORT headline (≤ 4 words) on the hook only; the end card carries the CTA + brand. Keep subtext empty unless truly essential.
- Voiceover (if used) is a tight, confident script in the BRIEF'S LANGUAGE that MUST fit the total runtime at a NATURAL pace (~2.2 words/second). Respect the HARD WORD CAP given below — going over makes the voice rush and get cut off. Write it to be SPOKEN; shorter is better than crammed.
- ABSOLUTELY NO INVENTED FACTS: never fabricate statistics, numbers, prices, percentages, follower/user counts, awards or testimonials. Use ONLY real facts from the brand info; otherwise use qualitative, emotional copy.
- End card: a clean closing brand moment — short line + CTA (+ the brand @handle only if you actually know it).

Return STRICT JSON only — no markdown, no commentary.`;

/** Global cinema override — when provided, forces these values on EVERY shot. */
export interface CinemaOverride {
  /** One of the CAMERA_BODIES keys (e.g. 'arri-alexa-35', 'red-v-raptor'). */
  cinemaBody?: string;
  /** One of the GENRES keys (e.g. 'commercial', 'music-video', 'epic'). */
  genre?: string;
  /** One of the COLOR_GRADES keys (e.g. 'teal-and-orange', 'golden-hour'). */
  colorGrade?: string;
  /** One of the SPEED_RAMPS keys (e.g. 'slow-motion', 'flash-in', 'impact'). */
  speedRamp?: string;
}

export interface DirectOpts {
  shots?: number;
  aspect?: VideoAspect;
  withVoiceover?: boolean;
  /** Extra real product context (e.g. from a product URL / marketing studio). */
  productInfo?: string;
  /** If this ad stars a persona, its description (keeps the talent consistent). */
  personaDescription?: string;
  styleHint?: string;
  /** Force a global cinematic look on every shot (overrides per-shot Opus choices). */
  cinema?: CinemaOverride;
}

/** Raw shot shape returned by Opus — includes Cinema Studio fields not on the Shot contract. */
interface RawShot {
  role?: unknown;
  imagePrompt?: unknown;
  motionPrompt?: unknown;
  /** Legacy single-preset field — no longer used; motion is baked from cameraMotions. */
  cameraPreset?: unknown;
  lens?: unknown;
  look?: unknown;
  durationSec?: unknown;
  overlay?: unknown;
  /** NEW: 1–3 stacked camera motion keys from CAMERA_MOTIONS catalog. */
  cameraMotions?: unknown;
  /** NEW: One CAMERA_BODIES key — sets the physical camera's image science. */
  cameraBody?: unknown;
  /** NEW: One GENRES key — pacing / energy register. */
  genre?: unknown;
  /** NEW: One COLOR_GRADES key — color science / grade. */
  colorGrade?: unknown;
  /** NEW: One SPEED_RAMPS key — time manipulation on playback. */
  speedRamp?: unknown;
  allowText?: unknown;
  allowUi?: unknown;
}

/** Raw VideoPlan shape as returned by Opus before normalization. */
interface RawVideoPlan {
  concept?: unknown;
  shots?: RawShot[];
  voiceover?: unknown;
  musicMood?: unknown;
  caption?: unknown;
  hashtags?: unknown;
  endCard?: unknown;
}

/** Opus designs the full directed ad. Returns a normalized, ready-to-build VideoPlan. */
export async function directVideoAd(
  ctx: VideoBrandCtx,
  brief: string,
  opts: DirectOpts = {},
): Promise<VideoPlan> {
  const aspect = (opts.aspect || '9:16') as VideoAspect;
  // Allow a single coherent shot (closest to a great one-prompt generation) up to 6.
  const nShots = Math.max(1, Math.min(6, opts.shots || 4));
  const dims = dimsForAspect(aspect);
  // Voiceover must fit the clip: ~5s per shot, ~2.0 spoken words/sec → a hard word cap.
  const estRuntimeSec = nShots * 5;
  const voWordCap = Math.max(8, Math.round(estRuntimeSec * 2.0));

  const ask = `BRAND: ${ctx.name}
PALETTE: ${ctx.colors.join(', ')} ${ctx.brandColors}
FONTS: ${ctx.fonts || 'modern sans-serif'}
BRAND INFO (use ONLY these real facts — do not invent any others): ${ctx.info || '(infer conservatively from the brand name)'}
${opts.productInfo ? `PRODUCT: ${opts.productInfo}\n` : ''}${opts.personaDescription ? `ON-CAMERA TALENT (keep consistent across shots): ${opts.personaDescription}\n` : ''}${opts.styleHint ? `VISUAL STYLE: ${opts.styleHint}\n` : ''}
CAMERA MOTIONS MENU (stack 1–3 keys per shot; primary motion first):
${motionsMenu()}

CINEMA STUDIO — OPTICS MENU (pick one lens + one look per shot):
${cinemaVocabularyForPrompt()}

CINEMA STUDIO — CAMERA BODY / GENRE / COLOR GRADE / SPEED RAMP (pick one per field per shot):
${cinemaMenu()}

FORMAT: ${aspect} (${dims.w}x${dims.h}). SHOTS: exactly ${nShots} (~${estRuntimeSec}s total). VOICEOVER: ${opts.withVoiceover ? `yes — write the script, HARD CAP ${voWordCap} WORDS (it must fit ~${estRuntimeSec}s; count your words, shorter is better)` : 'optional'}.

BRIEF: ${brief}

Return JSON:
{
  "concept": "one-line creative concept",
  "shots": [
    {
      "role": "hook|desire|reveal|benefit|proof|cta",
      "imagePrompt": "vivid English still description (if a logo, text, or phone/laptop screen is explicitly required, describe its realistic placement in the scene; otherwise, keep it text-free and UI-free)",
      "motionPrompt": "subject action and movement description (English)",
      "cameraMotions": ["primary-motion-key", "optional-second-key", "optional-third-key"],
      "lens": "one key from the LENS menu",
      "look": "one key from the LOOK menu",
      "cameraBody": "one key from CAMERA BODIES menu",
      "genre": "one key from GENRES menu",
      "colorGrade": "one key from COLOR GRADES menu",
      "speedRamp": "one key from SPEED RAMPS menu",
      "allowText": true|false,
      "allowUi": true|false,
      "durationSec": 3-6,
      "overlay": { "headline": "≤6 words or omit", "subtext": "one short line or omit", "cta": "2-3 words or omit", "lowerThird": "short label or omit", "position": "top|center|bottom" }
    }
  ],
  "voiceover": "${opts.withVoiceover ? 'spoken script in the brief language' : 'script or omit'}",
  "musicMood": "music brief (e.g. 'uplifting cinematic build, modern, confident')",
  "endCard": { "headline": "short closing line", "cta": "2-3 word CTA", "handle": "@brand or omit" },
  "caption": "ready-to-post caption in the brief's language",
  "hashtags": ["5-8 hashtags"]
}`;

  const raw = await callAIJSON<RawVideoPlan>(ask, {
    system: DIRECTOR_BRAIN,
    maxTokens: 4000,
    model: process.env.CLAUDE_CLI_MODEL || undefined,
  });
  if (!raw || !Array.isArray(raw.shots) || raw.shots.length === 0) {
    throw new Error('El director Opus no devolvió un shot list válido');
  }

  // ── Resolve global cinema overrides ────────────────────────────────────
  const globalCinema = opts.cinema ?? {};

  // ── Normalize / harden ──────────────────────────────────────────────────
  const format: VideoFormat = { name: aspect.replace(':', 'x'), w: dims.w, h: dims.h, aspect };
  const shots: Shot[] = raw.shots.slice(0, nShots).map((s, i) => {
    const lens = s.lens ? String(s.lens) : undefined;
    const look = s.look ? String(s.look) : undefined;

    // Resolve per-shot Cinema Studio fields — global opts override Opus choices.
    const cameraBody: string | undefined =
      globalCinema.cinemaBody ??
      (s.cameraBody && typeof s.cameraBody === 'string' && CAMERA_BODIES[s.cameraBody]
        ? s.cameraBody
        : undefined);
    const genre: string | undefined =
      globalCinema.genre ??
      (s.genre && typeof s.genre === 'string' && GENRES[s.genre]
        ? s.genre
        : undefined);
    const colorGrade: string | undefined =
      globalCinema.colorGrade ??
      (s.colorGrade && typeof s.colorGrade === 'string' && COLOR_GRADES[s.colorGrade]
        ? s.colorGrade
        : undefined);
    const speedRamp: string | undefined =
      globalCinema.speedRamp ??
      (s.speedRamp && typeof s.speedRamp === 'string' && SPEED_RAMPS[s.speedRamp]
        ? s.speedRamp
        : undefined);

    // Parse stacked motions (1–3) from cameraMotions array.
    let motionKeys: [string] | [string, string] | [string, string, string] | undefined;
    if (Array.isArray(s.cameraMotions)) {
      const validKeys = (s.cameraMotions as unknown[])
        .map((k) => (typeof k === 'string' ? k.trim() : ''))
        .filter(Boolean)
        .slice(0, 3);
      if (validKeys.length === 1) motionKeys = [validKeys[0]];
      else if (validKeys.length === 2) motionKeys = [validKeys[0], validKeys[1]];
      else if (validKeys.length >= 3) motionKeys = [validKeys[0], validKeys[1], validKeys[2]];
    }

    // Build the optics clause (lens + look) for the still prompt.
    const optics = opticsClause(lens, look);
    const baseImg = String(s.imagePrompt || '').trim() || `cinematic on-brand scene for ${ctx.name}`;
    const baseMotion = String(s.motionPrompt || '').trim() || 'slow, confident camera move';

    // Build the full Cinema prompt for the STILL (imagePrompt) — no motion stacking here.
    // Fold: optics (as suffix) + cameraBody + genre + colorGrade into the still description.
    let finalImagePrompt: string;
    try {
      finalImagePrompt = buildCinemaPrompt({
        base: optics ? `${baseImg} ${optics}` : baseImg,
        cameraBody,
        genre,
        colorGrade,
        // No speed ramp or motions in the still prompt — they're temporal.
      });
    } catch (err) {
      console.error('[director] Error construyendo imagePrompt cinemático:', err);
      finalImagePrompt = optics ? `${baseImg} ${optics}` : baseImg;
    }

    // Build the full Cinema prompt for the VIDEO (motionPrompt) — includes motion stack + speed ramp.
    let finalMotionPrompt: string;
    try {
      finalMotionPrompt = buildCinemaPrompt({
        base: optics ? `${baseMotion} ${optics}` : baseMotion,
        cameraBody,
        genre,
        colorGrade,
        speedRamp,
        motions: motionKeys,
      });
    } catch (err) {
      console.error('[director] Error construyendo motionPrompt cinemático:', err);
      finalMotionPrompt = optics ? `${baseMotion} ${optics}` : baseMotion;
    }

    // NOTE: cameraPreset is intentionally set to undefined.
    // assemble.ts calls applyCamera(motionPrompt, shot.cameraPreset) which would
    // double-apply the camera motion — we've already baked it into motionPrompt
    // via buildCinemaPrompt + stackMotions above.
    return {
      role: String(s.role || `shot-${i + 1}`),
      imagePrompt: finalImagePrompt,
      motionPrompt: finalMotionPrompt,
      cameraPreset: undefined,
      lens,
      look,
      durationSec: Math.max(2, Math.min(8, Math.round(Number(s.durationSec) || 4))),
      overlay: s.overlay && typeof s.overlay === 'object' ? s.overlay : undefined,
      allowText: s.allowText === true,
      allowUi: s.allowUi === true,
    };
  });

  return {
    concept: String(raw.concept || brief).slice(0, 240),
    format,
    shots,
    voiceover: opts.withVoiceover !== false && raw.voiceover ? (capWords(String(raw.voiceover).trim(), voWordCap) || undefined) : undefined,
    musicMood: raw.musicMood ? String(raw.musicMood) : undefined,
    caption: String(raw.caption || '').trim(),
    hashtags: Array.isArray(raw.hashtags) ? raw.hashtags.map(String).slice(0, 10) : [],
    endCard: raw.endCard && typeof raw.endCard === 'object' ? raw.endCard : undefined,
  };
}
