// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  FyDesign VIDEO — CINEMATIC CAMERA PRESET LIBRARY                             ║
// ║                                                                              ║
// ║  Pure data + helpers. The Higgsfield-class camera-control layer for the       ║
// ║  FyDesign image→video pipeline. Each preset carries a promptPhrase that       ║
// ║  gets appended to a shot's motionPrompt before sending to the video model,    ║
// ║  plus optional per-model parameter hints.                                     ║
// ║                                                                              ║
// ║  CONTRACT (from types.ts):                                                   ║
// ║    CAMERA_PRESETS: CameraPreset[]                                            ║
// ║    getCameraPreset(key?: string): CameraPreset | null                        ║
// ║    applyCamera(motionPrompt: string, key?: string): string                   ║
// ║    cameraVocabularyForPrompt(): string                                       ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import type { CameraPreset } from './types';

// ────────────────────────────────────────────────────────────────────────────
//  Master preset table — 16 required keys, each with vivid promptPhrase,
//  a human label, an emotion/use note, and optional conservative param hints.
// ────────────────────────────────────────────────────────────────────────────

export const CAMERA_PRESETS: CameraPreset[] = [
  {
    key: 'crash-zoom',
    label: 'Crash Zoom',
    promptPhrase:
      'a rapid crash zoom punching in hard on the subject, kinetic and aggressive, like a gut-punch cut',
    category: 'push',
    params: { motion_strength: 0.85, camera_speed: 'fast' },
  },
  {
    key: 'dolly-in',
    label: 'Dolly In',
    promptPhrase:
      'a smooth, deliberate dolly-in gliding toward the subject, building intimacy and focus as the background compresses',
    category: 'push',
    params: { motion_strength: 0.55 },
  },
  {
    key: 'dolly-out',
    label: 'Dolly Out',
    promptPhrase:
      'a graceful dolly-out pulling back from the subject, expanding the world around it and releasing tension',
    category: 'pull',
    params: { motion_strength: 0.55 },
  },
  {
    key: 'pull-back-reveal',
    label: 'Pull-Back Reveal',
    promptPhrase:
      'a dramatic pull-back reveal — the camera retreats to unveil a surprising wider context, making the viewer gasp at the scale or setting',
    category: 'reveal',
    params: { motion_strength: 0.7 },
  },
  {
    key: 'orbit',
    label: 'Orbit',
    promptPhrase:
      'a smooth 180-degree orbit around the subject, parallax revealing depth and dimension from every angle',
    category: 'orbit',
    params: { motion_strength: 0.6 },
  },
  {
    key: 'fpv-drone',
    label: 'FPV Drone',
    promptPhrase:
      'an FPV drone shot diving and weaving through the scene at speed, visceral and immersive, the horizon tilting on every turn',
    category: 'aerial',
    params: { motion_strength: 0.9, camera_speed: 'fast' },
  },
  {
    key: 'crane-up',
    label: 'Crane Up',
    promptPhrase:
      'a majestic crane-up rising vertically from ground level to reveal the full scene above, bestowing grandeur and scale',
    category: 'aerial',
    params: { motion_strength: 0.65 },
  },
  {
    key: 'top-down-reveal',
    label: 'Top-Down Reveal',
    promptPhrase:
      'a top-down overhead reveal descending straight down onto the subject from directly above, clinical and striking',
    category: 'reveal',
    params: { motion_strength: 0.6 },
  },
  {
    key: 'slow-push',
    label: 'Slow Push',
    promptPhrase:
      'an imperceptibly slow push-in, barely moving, building subtle tension and drawing the eye deeper into the frame',
    category: 'push',
    params: { motion_strength: 0.25 },
  },
  {
    key: 'whip-pan',
    label: 'Whip Pan',
    promptPhrase:
      'a lightning-fast whip-pan snap cut to the side, energetic and jarring, transitioning with pure kinetic momentum',
    category: 'speed',
    params: { motion_strength: 0.9, camera_speed: 'fast' },
  },
  {
    key: 'parallax',
    label: 'Parallax',
    promptPhrase:
      'a parallax slide — foreground and background moving at different speeds, layering depth and cinematic dimension',
    category: 'handheld',
    params: { motion_strength: 0.5 },
  },
  {
    key: 'speed-ramp',
    label: 'Speed Ramp',
    promptPhrase:
      'a speed-ramped move that starts fast and snaps to a dramatic slow-motion hold on the hero moment, then accelerates out',
    category: 'speed',
    params: { motion_strength: 0.8 },
  },
  {
    key: 'handheld-follow',
    label: 'Handheld Follow',
    promptPhrase:
      'a handheld follow shot tracking the subject with natural shoulder movement and micro-corrections, intimate and documentary-real',
    category: 'handheld',
    params: { motion_strength: 0.4, camera_shake: 0.3 },
  },
  {
    key: 'rack-focus',
    label: 'Rack Focus',
    promptPhrase:
      'a rack focus pull shifting depth-of-field from a foreground element to the hero subject mid-shot, cinematic and emotionally directed',
    category: 'static',
    params: { motion_strength: 0.2 },
  },
  {
    key: 'static-locked',
    label: 'Static Locked',
    promptPhrase:
      'a perfectly static locked-off shot, the camera completely still, letting the subject and motion within the frame command full attention',
    category: 'static',
    params: { motion_strength: 0.0 },
  },
  {
    key: 'hyperlapse',
    label: 'Hyperlapse',
    promptPhrase:
      'a hyperlapse tracking shot moving through space at accelerated time, environment flowing past as clouds and light race overhead',
    category: 'speed',
    params: { motion_strength: 0.75, camera_speed: 'fast' },
  },
];

// ────────────────────────────────────────────────────────────────────────────
//  Internal lookup map — built once at module load
// ────────────────────────────────────────────────────────────────────────────

/** Normalise a raw key to the canonical form used in the map. */
function normaliseKey(raw: string): string {
  return raw.trim().toLowerCase().replace(/[\s_]+/g, '-');
}

const _byKey = new Map<string, CameraPreset>(
  CAMERA_PRESETS.map((p) => [p.key, p]),
);

// ────────────────────────────────────────────────────────────────────────────
//  Public helpers
// ────────────────────────────────────────────────────────────────────────────

/**
 * Look up a camera preset by key.
 * - Case-insensitive, trims whitespace, tolerates spaces and underscores in
 *   place of hyphens (e.g. "crash zoom", "crash_zoom" → 'crash-zoom').
 * - Returns null for unknown keys or when key is absent / empty.
 */
export function getCameraPreset(key?: string): CameraPreset | null {
  if (!key) return null;
  try {
    const normalised = normaliseKey(key);
    return _byKey.get(normalised) ?? null;
  } catch (err) {
    console.error('[camera] getCameraPreset error:', err);
    return null;
  }
}

/**
 * Append the camera's promptPhrase to a motionPrompt.
 * Returns motionPrompt unchanged if the key is absent or unknown.
 *
 * Output format: '<motionPrompt>. Camera: <promptPhrase>.'
 */
export function applyCamera(motionPrompt: string, key?: string): string {
  try {
    const preset = getCameraPreset(key);
    if (!preset) return motionPrompt;
    // Trim trailing punctuation from the motion prompt before joining
    const base = motionPrompt.trimEnd().replace(/[.,;!?]+$/, '');
    return `${base}. Camera: ${preset.promptPhrase}.`;
  } catch (err) {
    console.error('[camera] applyCamera error:', err);
    return motionPrompt;
  }
}

/**
 * Compact camera vocabulary menu — one line per preset, formatted for
 * inclusion in the Opus director system prompt so it can pick a cameraPreset
 * key per shot.
 *
 * Format per line:  key — Label: emotion / ideal use-case
 */
export function cameraVocabularyForPrompt(): string {
  const lines: string[] = [
    'CAMERA PRESETS (use the key in cameraPreset field):',
    ...CAMERA_PRESETS.map((p) => {
      const note = _emotionNote(p.key);
      return `  ${p.key} — ${p.label}: ${note}`;
    }),
  ];
  return lines.join('\n');
}

// ────────────────────────────────────────────────────────────────────────────
//  Internal: terse emotion / use-case notes for the director prompt menu
// ────────────────────────────────────────────────────────────────────────────

function _emotionNote(key: string): string {
  switch (key) {
    case 'crash-zoom':       return 'shock / aggression / scroll-stopper hook';
    case 'dolly-in':         return 'intimacy / focus / product hero reveal';
    case 'dolly-out':        return 'release / scale / end-of-scene breath';
    case 'pull-back-reveal': return 'surprise / context reveal / wonder';
    case 'orbit':            return 'dimension / product 360 / premium feel';
    case 'fpv-drone':        return 'adrenaline / lifestyle / speed / adventure';
    case 'crane-up':         return 'grandeur / aspiration / location scale';
    case 'top-down-reveal':  return 'elegance / composition / fashion / flat-lay';
    case 'slow-push':        return 'tension build / contemplation / subtle drama';
    case 'whip-pan':         return 'energy / quick transitions / hype cuts';
    case 'parallax':         return 'depth / layered narrative / cinematic texture';
    case 'speed-ramp':       return 'drama / hero moment emphasis / action peak';
    case 'handheld-follow':  return 'authenticity / documentary / human connection';
    case 'rack-focus':       return 'emotional pivot / subject spotlight / poetry';
    case 'static-locked':    return 'calm / confidence / minimalism / product purity';
    case 'hyperlapse':       return 'time / journey / transformation / energy';
    default:                 return '';
  }
}

// ── CINEMA STUDIO: optics (lens / focal length) + lighting/film look ──────────
// Image→video models honor lens & look language in the prompt, so the Opus
// director (the "AI DP") specifies them per shot for a genuinely cinematic feel.

/** Lens / focal-length presets → a prompt clause the video model honors. */
export const LENS_PRESETS: Record<string, string> = {
  'wide-24mm': '24mm wide-angle lens, expansive perspective, deep focus',
  'standard-35mm': '35mm lens, natural documentary perspective',
  'normal-50mm': '50mm lens, true-to-eye perspective, gentle subject separation',
  'portrait-85mm': '85mm portrait lens, compressed perspective, creamy shallow depth of field',
  'telephoto-135mm': '135mm telephoto, strong compression, isolated subject, soft bokeh',
  'macro': 'macro lens, extreme close-up detail, razor-thin plane of focus',
  'anamorphic': 'anamorphic lens, 2.39 cinematic widescreen feel, oval bokeh, subtle horizontal lens flares',
};

/** Lighting / film-look presets → a prompt clause for grade and mood. */
export const LOOK_PRESETS: Record<string, string> = {
  'cinematic-teal-orange': 'cinematic grade, teal shadows and warm highlights, filmic contrast',
  'natural-daylight': 'clean natural daylight, soft and true color',
  'golden-hour': 'warm golden-hour light, long shadows, glowing rim light',
  'moody-lowkey': 'moody low-key lighting, deep shadows, a single sculpting key light',
  'high-key-clean': 'bright high-key studio lighting, minimal shadows, premium and clean',
  'film-35mm': 'shot on 35mm film, fine grain, organic highlight roll-off',
  'neon-night': 'neon night ambience, vivid practical lights, wet reflective surfaces',
};

/** Build a cinematography clause from a lens + look (folded into still & motion prompts). */
export function opticsClause(lens?: string, look?: string): string {
  const L = lens ? (LENS_PRESETS[lens] || lens) : '';
  const K = look ? (LOOK_PRESETS[look] || look) : '';
  const parts = [L, K].filter(Boolean);
  return parts.length ? `Cinematography: ${parts.join('; ')}.` : '';
}

/** Compact lens + look menu for the Opus director (Cinema Studio). */
export function cinemaVocabularyForPrompt(): string {
  const lens = Object.keys(LENS_PRESETS).join(', ');
  const look = Object.keys(LOOK_PRESETS).join(', ');
  return `LENS / focal length (pick one per shot): ${lens}\nLOOK / lighting (pick one per shot): ${look}`;
}
