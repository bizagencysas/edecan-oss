// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  audio-client — TTS voiceover + music via Muapi                             ║
// ║                                                                              ║
// ║  CONTRACT (src/lib/video/types.ts):                                          ║
// ║    hasAudio(): boolean                                                        ║
// ║    generateVoiceover(text, opts?): Promise<AudioResult | null>               ║
// ║    generateMusic(mood, opts?): Promise<AudioResult | null>                   ║
// ║                                                                              ║
// ║  Voiceover priority:                                                         ║
// ║    1. Muapi TTS (if MUAPI_API_KEY set)                                       ║
// ║    2. macOS `say` → .aiff fallback (if process.platform === 'darwin')        ║
// ║                                                                              ║
// ║  Music: Muapi only — returns null if no key or no model configured.          ║
// ║                                                                              ║
// ║  Env vars:                                                                   ║
// ║    MUAPI_API_KEY        your Muapi key                                       ║
// ║    MUAPI_TTS_MODEL      TTS endpoint (default: minimax-speech-2.6-hd)       ║
// ║    MUAPI_TTS_VOICE      voice_id override — always wins regardless of lang   ║
// ║    MUAPI_TTS_VOICE_ES   Spanish voice_id override (default: Spanish_Narrator)║
// ║    MUAPI_MUSIC_MODEL    music endpoint (default: suno-create-music)         ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { execFile } from 'node:child_process';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { hasMuapi, muapiGenerate } from './muapi-client';
import type { AudioResult } from '../video/types';

// ─── Language detection ───────────────────────────────────────────────────────

/**
 * Simple heuristic language detector.
 *
 * Treats text as Spanish if it contains any of:
 *   - Spanish-specific characters: ñ, ¿, ¡, á, é, í, ó, ú (lower or upper)
 *   - Common Spanish function words as whole words (case-insensitive):
 *     que · los · las · con · para · una · más · tu · del · por · hay
 *
 * Returns 'en' otherwise. Intentionally conservative — false-negatives (English
 * with accented loanwords) are rare and less harmful than mis-routing English as
 * Spanish. Callers may override via opts.voice / env MUAPI_TTS_VOICE.
 */
export function detectLang(text: string): 'es' | 'en' {
  // Fast character-level check first — these glyphs are unambiguously Spanish.
  if (/[ñÑ¿¡áéíóúÁÉÍÓÚ]/.test(text)) return 'es';

  // Otherwise SCORE Spanish vs English on frequent function words and pick the
  // stronger signal. The old version returned 'en' on the first miss, which
  // mislabeled accent-free Spanish like "Comi gratis solo por subir un reel"
  // → an English (male) voice reading Spanish. Counting evidence fixes that.
  const es = (text.match(/\b(que|los|las|con|para|una|uno|más|mas|del|por|hay|hola|gracias|como|tu|tú|tus|mi|mis|este|esta|esto|eso|esa|muy|pero|porque|cuando|donde|quien|todo|nada|aqui|aquí|ahora|sin|sobre|entre|hasta|desde|también|solo|sólo|ya|yo|nosotros|ellos|ella|son|está|estás|estoy|vamos|tienes|tengo|puedes|puedo|quiero|gratis|cuenta|reel|sube|subir|aplica)\b/gi) || []).length;
  const en = (text.match(/\b(the|and|you|your|with|for|this|that|have|are|was|not|but|they|will|can|just|now|here|from|about|into|over|free|account|video)\b/gi) || []).length;
  if (en > es) return 'en';
  // Tie / Spanish-leaning / no signal: the engine's audience is Spanish-first.
  return 'es';
}

// ─── Voice selection constants ────────────────────────────────────────────────

/**
 * Default English voice — neutral, works across all topics.
 * Real value from minimax-speech-2.6-hd schema enum.
 */
const DEFAULT_VOICE_EN = 'Friendly_Person';

/**
 * Default Spanish voice — a natural, confident LatAm-friendly voice for ads.
 * ('Spanish_Narrator' sounded flat/accented.) Overridable via env MUAPI_TTS_VOICE_ES.
 */
const DEFAULT_VOICE_ES = 'Spanish_ConfidentWoman';

// ─── Helpers ─────────────────────────────────────────────────────────────────

let _seq = 0;
function tmpPath(ext: string): string {
  return path.join(os.tmpdir(), `fyd-audio-${Date.now()}-${++_seq}${ext}`);
}

/** Download a URL to a local tmp file; returns the absolute path. */
async function downloadToTmp(url: string, ext: string): Promise<string> {
  const dest = tmpPath(ext);
  const res = await fetch(url, { signal: AbortSignal.timeout(120_000) });
  if (!res.ok) throw new Error(`[audio-client] Descarga fallida: ${url} → ${res.status}`);
  const buf = Buffer.from(await res.arrayBuffer());
  await fs.writeFile(dest, buf);
  return dest;
}

/**
 * Try to probe duration in seconds via ffprobe.
 * Returns undefined if ffprobe is not available or fails — non-fatal.
 */
async function probeDuration(file: string): Promise<number | undefined> {
  return new Promise<number | undefined>((resolve) => {
    execFile(
      'ffprobe',
      ['-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', file],
      { timeout: 10_000 },
      (err, stdout) => {
        if (err) { resolve(undefined); return; }
        const n = parseFloat((stdout || '').trim());
        resolve(isNaN(n) ? undefined : n);
      },
    );
  });
}

// ─── macOS 'say' fallback ────────────────────────────────────────────────────

/**
 * Synthesise text with macOS `say` → .aiff.
 * Picks a Spanish voice (Paulina, es_MX) when lang === 'es'; otherwise the
 * system default English voice.
 * Optionally converts to .mp3 via ffmpeg (if available).
 * Returns the output file path (.mp3 or .aiff).
 */
function sayVoiceover(text: string, lang: 'es' | 'en'): Promise<string> {
  const aiff = tmpPath('.aiff');
  // macOS ships with Paulina (es_MX) as the standard Spanish voice.
  // Fall back to no -v flag (system default) if somehow unavailable — the
  // execFile error will propagate and the caller will log + return null.
  const sayArgs = lang === 'es'
    ? ['-v', 'Paulina', '-o', aiff, text]
    : ['-o', aiff, text];

  return new Promise<string>((resolve, reject) => {
    execFile('say', sayArgs, { timeout: 60_000 }, (err) => {
      if (err) { reject(new Error(`[audio-client] 'say' falló: ${err.message}`)); return; }
      // Try ffmpeg conversion to .mp3 — if it fails, keep the .aiff (acceptable).
      const mp3 = aiff.replace(/\.aiff$/, '.mp3');
      execFile(
        'ffmpeg',
        ['-y', '-i', aiff, '-codec:a', 'libmp3lame', '-qscale:a', '4', mp3],
        { timeout: 60_000 },
        (ffErr) => {
          if (ffErr) {
            // ffmpeg not available or failed — return .aiff (ffmpeg can read it later).
            console.error('[audio-client] ffmpeg no disponible, devolviendo .aiff');
            resolve(aiff);
          } else {
            // Clean up .aiff now that we have .mp3
            fs.unlink(aiff).catch(() => undefined);
            resolve(mp3);
          }
        },
      );
    });
  });
}

// ─── Public API ───────────────────────────────────────────────────────────────

/**
 * Returns true if at least one audio backend is available.
 * Muapi covers both TTS and music; macOS 'say' covers TTS only.
 */
export function hasAudio(): boolean {
  return hasMuapi() || process.platform === 'darwin';
}

/**
 * Generate a voiceover .mp3 from `text`.
 *
 * Language is auto-detected via detectLang(); the global override MUAPI_TTS_VOICE
 * always wins. For Spanish-specific overrides use MUAPI_TTS_VOICE_ES.
 *
 * Priority:
 *  1. Muapi TTS (if MUAPI_API_KEY is set)
 *  2. macOS `say` fallback (if Muapi is unavailable or fails AND we're on darwin)
 *
 * Returns null only when ALL backends fail.
 */
export async function generateVoiceover(
  text: string,
  opts: { voice?: string; model?: string } = {},
): Promise<AudioResult | null> {
  const lang = detectLang(text);

  // ── Voice resolution order ─────────────────────────────────────────────────
  //   1. opts.voice  (caller-supplied, always wins)
  //   2. MUAPI_TTS_VOICE  (global env override, always wins)
  //   3. MUAPI_TTS_VOICE_ES  (Spanish-specific env override)
  //   4. DEFAULT_VOICE_ES / DEFAULT_VOICE_EN  (built-in defaults)
  const globalVoiceOverride = process.env.MUAPI_TTS_VOICE;
  const voiceId = opts.voice
    ?? globalVoiceOverride
    ?? (lang === 'es'
        ? (process.env.MUAPI_TTS_VOICE_ES ?? DEFAULT_VOICE_ES)
        : DEFAULT_VOICE_EN);

  // language_boost: 'Spanish' forces correct pronunciation; 'English' for EN.
  // Only set when we have a clear signal — do not set for ambiguous/override cases
  // where the caller supplied an explicit voice (they may know better).
  const languageBoost: string | undefined =
    (opts.voice == null && globalVoiceOverride == null)
      ? (lang === 'es' ? 'Spanish' : 'English')
      : undefined;

  // ── 1. Muapi TTS ──────────────────────────────────────────────────────────
  if (hasMuapi()) {
    const endpoint = opts.model || process.env.MUAPI_TTS_MODEL || 'minimax-speech-2.6-hd';
    try {
      console.error(
        `[audio-client] Generando voiceover con Muapi (${endpoint}, voz: ${voiceId}, ` +
        `lang: ${lang}${languageBoost ? `, language_boost: ${languageBoost}` : ''})`,
      );

      // Required: prompt (text), voice_id.
      // language_boost: enum value from schema — forces correct phoneme set.
      // english_normalization: only useful for English number-reading; disable for ES.
      const body: Record<string, unknown> = {
        prompt: text,
        voice_id: voiceId,
        ...(languageBoost !== undefined ? { language_boost: languageBoost } : {}),
        ...(lang === 'en' ? { english_normalization: true } : { english_normalization: false }),
      };

      const result = await muapiGenerate(endpoint, body, { timeoutMs: 180_000, intervalMs: 3_000 });
      const audioUrl = result.outputs[0];
      if (!audioUrl) throw new Error('[audio-client] Muapi TTS: sin output URL');

      const file = await downloadToTmp(audioUrl, '.mp3');
      const durationSec = await probeDuration(file);

      console.error(`[audio-client] Voiceover Muapi listo: ${file} (${durationSec?.toFixed(1) ?? '?'}s)`);
      return {
        file,
        kind: 'voice',
        durationSec,
        cost: result.cost ?? null,
      };
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      console.error(`[audio-client] Muapi TTS falló: ${msg}`);
      // Fall through to macOS fallback below.
    }
  }

  // ── 2. macOS 'say' fallback ───────────────────────────────────────────────
  if (process.platform === 'darwin') {
    try {
      const sayVoiceLabel = lang === 'es' ? 'Paulina (es_MX)' : 'default';
      console.error(`[audio-client] Usando macOS \`say\` como fallback local (voz: ${sayVoiceLabel})`);
      const file = await sayVoiceover(text, lang);
      const durationSec = await probeDuration(file);
      console.error(`[audio-client] Voiceover macOS listo: ${file} (${durationSec?.toFixed(1) ?? '?'}s)`);
      return {
        file,
        kind: 'voice',
        durationSec,
        cost: null,
      };
    } catch (e) {
      console.error(`[audio-client] macOS 'say' también falló: ${e instanceof Error ? e.message : e}`);
    }
  }

  // All backends exhausted.
  console.error('[audio-client] generateVoiceover: todos los backends fallaron, devolviendo null');
  return null;
}

/**
 * Generate a voiceover and return the REMOTE Muapi audio URL (not a local file).
 * Used to feed lip-sync / talking-avatar models (which take an `audio_url`): the
 * Muapi TTS output URL can be passed straight to another Muapi model, no re-hosting.
 *
 * Muapi-only (no macOS `say` fallback — a lip-synced ad needs a real voice, and the
 * `say` voice is a robotic local demo). Returns null if Muapi TTS is unavailable/fails so
 * the caller can fail fast instead of lip-syncing to nothing.
 */
export async function generateVoiceoverUrl(
  text: string,
  opts: { voice?: string; model?: string } = {},
): Promise<{ url: string; cost?: { amount_usd?: number } | null; durationSec?: number } | null> {
  if (!hasMuapi()) return null;
  const lang = detectLang(text);
  const globalVoiceOverride = process.env.MUAPI_TTS_VOICE;
  const voiceId = opts.voice
    ?? globalVoiceOverride
    ?? (lang === 'es' ? (process.env.MUAPI_TTS_VOICE_ES ?? DEFAULT_VOICE_ES) : DEFAULT_VOICE_EN);
  const languageBoost: string | undefined =
    (opts.voice == null && globalVoiceOverride == null)
      ? (lang === 'es' ? 'Spanish' : 'English')
      : undefined;

  const endpoint = opts.model || process.env.MUAPI_TTS_MODEL || 'minimax-speech-2.6-hd';
  try {
    console.error(`[audio-client] TTS→URL (${endpoint}, voz: ${voiceId}, lang: ${lang})`);
    const body: Record<string, unknown> = {
      prompt: text,
      voice_id: voiceId,
      ...(languageBoost !== undefined ? { language_boost: languageBoost } : {}),
      ...(lang === 'en' ? { english_normalization: true } : { english_normalization: false }),
    };
    const result = await muapiGenerate(endpoint, body, { timeoutMs: 180_000, intervalMs: 3_000 });
    const url = result.outputs[0];
    if (!url) return null;
    return { url, cost: result.cost ?? null };
  } catch (e) {
    console.error(`[audio-client] TTS→URL falló: ${e instanceof Error ? e.message : e}`);
    return null;
  }
}

/**
 * Generate background music for `mood`.
 *
 * Music is entirely optional — returns null if:
 *   - No Muapi key is configured, OR
 *   - No music model endpoint is set/provided, OR
 *   - The Muapi call or download fails.
 *
 * Never throws.
 */
export async function generateMusic(
  mood: string,
  opts: { durationSec?: number; model?: string } = {},
): Promise<AudioResult | null> {
  const endpoint = opts.model || process.env.MUAPI_MUSIC_MODEL || 'suno-create-music';
  if (!endpoint || !hasMuapi()) {
    if (!hasMuapi()) {
      console.error('[audio-client] generateMusic: MUAPI_API_KEY no configurada, omitiendo música');
    } else {
      console.error('[audio-client] generateMusic: MUAPI_MUSIC_MODEL no configurado, omitiendo música');
    }
    return null;
  }

  try {
    console.error(`[audio-client] Generando música con Muapi (${endpoint}, estilo: ${mood})`);
    // suno-create-music requires "style" (required by schema); "duration" is NOT a valid property.
    // "prompt" is optional (used as lyrics); send both so the mood drives both style and content.
    const result = await muapiGenerate(
      endpoint,
      { style: mood, prompt: mood },
      { timeoutMs: 300_000, intervalMs: 5_000 },
    );
    const audioUrl = result.outputs[0];
    if (!audioUrl) throw new Error('[audio-client] Muapi music: sin output URL');

    const file = await downloadToTmp(audioUrl, '.mp3');
    const durationSec = await probeDuration(file);

    console.error(`[audio-client] Música Muapi lista: ${file} (${durationSec?.toFixed(1) ?? '?'}s)`);
    return {
      file,
      kind: 'music',
      durationSec,
      cost: result.cost ?? null,
    };
  } catch (e) {
    console.error(`[audio-client] generateMusic falló: ${e instanceof Error ? e.message : e}`);
    return null;
  }
}
