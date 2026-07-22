// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  FFmpeg / FFprobe wrapper — pure process calls, zero AI                       ║
// ║                                                                              ║
// ║  Binary resolution order (ffmpeg):                                           ║
// ║    1. FFMPEG_PATH env var                                                    ║
// ║    2. /opt/homebrew/bin/ffmpeg  (Apple Silicon Homebrew)                     ║
// ║    3. /usr/local/bin/ffmpeg     (Intel Homebrew / legacy)                    ║
// ║    4. /usr/bin/ffmpeg           (system)                                     ║
// ║    5. 'ffmpeg'                  (PATH fallback)                              ║
// ║  Same pattern for ffprobe (FFPROBE_PATH env → same dirs).                   ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { execFile as _execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { existsSync } from 'node:fs';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

// ─── Promisified execFile ─────────────────────────────────────────────────────

const execFile = promisify(_execFile);

const EXEC_OPTS = {
  maxBuffer: 256 * 1024 * 1024, // 256 MB — large frames can produce verbose output
  timeout: 10 * 60 * 1000,      // 10 minutes
};

// ─── Binary resolution ────────────────────────────────────────────────────────

const SEARCH_DIRS = [
  '/opt/homebrew/bin',
  '/usr/local/bin',
  '/usr/bin',
];

function resolveBin(name: 'ffmpeg' | 'ffprobe'): string {
  const envKey = name === 'ffmpeg' ? 'FFMPEG_PATH' : 'FFPROBE_PATH';
  const fromEnv = process.env[envKey];
  if (fromEnv && existsSync(fromEnv)) return fromEnv;

  for (const dir of SEARCH_DIRS) {
    const full = path.join(dir, name);
    if (existsSync(full)) return full;
  }
  // Fallback to PATH — will throw at exec time if not found
  return name;
}

const FFMPEG = resolveBin('ffmpeg');
const FFPROBE = resolveBin('ffprobe');

// ─── Helpers ──────────────────────────────────────────────────────────────────

/** Return the last N lines of a string (for error tails). */
function stderrTail(s: string, lines = 30): string {
  return s.trim().split('\n').slice(-lines).join('\n');
}

/** Build a clear Error that always includes the ffmpeg stderr. */
function ffmpegError(label: string, stderr: string, cause?: unknown): Error {
  const tail = stderrTail(stderr);
  const msg = `[ffmpeg] ${label}\n--- stderr tail ---\n${tail}`;
  const err = new Error(msg);
  if (cause instanceof Error) {
    (err as NodeJS.ErrnoException).stack += `\nCaused by: ${cause.stack}`;
  }
  return err;
}

/** Unique temp file in os.tmpdir(). */
function tmpFile(ext: string): string {
  return path.join(os.tmpdir(), `fydesign_${Date.now()}_${Math.random().toString(36).slice(2)}.${ext}`);
}

// ─── Public API ───────────────────────────────────────────────────────────────

/**
 * Returns true when a resolved ffmpeg binary exists on disk.
 * Falls back to false when only the name 'ffmpeg' is used and the file
 * cannot be stat'd — but doesn't shell out (synchronous, no execFile).
 */
export function ffmpegAvailable(): boolean {
  if (FFMPEG === 'ffmpeg') {
    // Could not locate in known dirs; assume unavailable unless PATH works
    // (we don't shell out here because this must be synchronous)
    return false;
  }
  return existsSync(FFMPEG);
}

/**
 * Probe a media file and return its duration in seconds.
 * Uses: ffprobe -v quiet -show_entries format=duration -of csv=p=0
 */
export async function probeDurationSec(file: string): Promise<number> {
  let stderr = '';
  try {
    const { stdout, stderr: se } = await execFile(
      FFPROBE,
      [
        '-v', 'quiet',
        '-show_entries', 'format=duration',
        '-of', 'csv=p=0',
        file,
      ],
      EXEC_OPTS,
    );
    stderr = se;
    const val = parseFloat(stdout.trim());
    if (isNaN(val)) throw new Error(`unexpected ffprobe output: "${stdout.trim()}"`);
    return val;
  } catch (e) {
    const se = e instanceof Error && 'stderr' in e ? String((e as Record<string, unknown>).stderr) : stderr;
    throw ffmpegError(`probeDurationSec(${file})`, se, e);
  }
}

/**
 * True if the file has at least one audio stream. Used to guard against lip-sync
 * models that return a SILENT video (lip-moved but muted) — the caller can then
 * mux the original voice back in. Never throws (returns false on any probe error).
 */
export async function probeHasAudio(file: string): Promise<boolean> {
  try {
    const { stdout } = await execFile(
      FFPROBE,
      ['-v', 'quiet', '-select_streams', 'a', '-show_entries', 'stream=codec_type', '-of', 'csv=p=0', file],
      EXEC_OPTS,
    );
    return /audio/.test(stdout);
  } catch {
    return false;
  }
}

/**
 * Probe a media file and return its video dimensions.
 * (Internal helper — not exported per contract.)
 */
async function probeDimensions(file: string): Promise<{ w: number; h: number }> {
  let stderr = '';
  try {
    const { stdout, stderr: se } = await execFile(
      FFPROBE,
      [
        '-v', 'quiet',
        '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height',
        '-of', 'csv=p=0',
        file,
      ],
      EXEC_OPTS,
    );
    stderr = se;
    const parts = stdout.trim().split(',');
    const w = parseInt(parts[0], 10);
    const h = parseInt(parts[1], 10);
    if (isNaN(w) || isNaN(h)) throw new Error(`unexpected ffprobe dimensions output: "${stdout.trim()}"`);
    return { w, h };
  } catch (e) {
    const se = e instanceof Error && 'stderr' in e ? String((e as Record<string, unknown>).stderr) : stderr;
    throw ffmpegError(`probeDimensions(${file})`, se, e);
  }
}

/**
 * Re-encode a video clip to a canonical format so heterogeneous model outputs
 * can be concatenated without decoder tears.
 *
 * Pipeline:
 *   scale to COVER w×h (force_original_aspect_ratio=increase + ensure even dims)
 *   → crop to exactly w×h
 *   → set fps
 *   → pix_fmt yuv420p, libx264, -an
 */
/**
 * Re-mux to a web-streamable mp4: moves the `moov` atom to the FRONT (+faststart)
 * so browsers/mobile can play it without downloading the whole file. No re-encode.
 */
export async function faststart(input: string, out: string): Promise<string> {
  try {
    await execFile(FFMPEG, ['-y', '-i', input, '-c', 'copy', '-movflags', '+faststart', out], EXEC_OPTS);
    return out;
  } catch (e) {
    const se = e instanceof Error && 'stderr' in e ? String((e as Record<string, unknown>).stderr) : '';
    throw ffmpegError(`faststart(${input} → ${out})`, se, e);
  }
}

export async function standardizeClip(
  input: string,
  out: string,
  o: { w: number; h: number; fps?: number },
): Promise<string> {
  const { w, h, fps = 30 } = o;
  // Force even dimensions (libx264 requirement)
  const ew = w % 2 === 0 ? w : w + 1;
  const eh = h % 2 === 0 ? h : h + 1;

  const scaleFilter =
    `scale=${ew}:${eh}:force_original_aspect_ratio=increase,` +
    `crop=${ew}:${eh}`;
  const vf = `${scaleFilter},fps=${fps},format=yuv420p`;

  let stderr = '';
  try {
    const { stderr: se } = await execFile(
      FFMPEG,
      [
        '-y',
        '-i', input,
        '-vf', vf,
        '-c:v', 'libx264',
        '-preset', 'fast',
        '-crf', '18',
        '-an',
        out,
      ],
      EXEC_OPTS,
    );
    stderr = se;
    return out;
  } catch (e) {
    const se = e instanceof Error && 'stderr' in e ? String((e as Record<string, unknown>).stderr) : stderr;
    throw ffmpegError(`standardizeClip(${input} → ${out})`, se, e);
  }
}

/**
 * Create a slow Ken Burns zoom/pan from a still image using the zoompan filter.
 * Still → video fallback when a video model is unavailable or sandboxed.
 *
 * d = durationSec * fps (number of output frames)
 * Subtle zoom: 1.0 → 1.05 over the full duration with a slight top-left pan.
 */
export async function kenBurnsClip(
  imagePath: string,
  out: string,
  o: { durationSec: number; w: number; h: number },
): Promise<string> {
  const { durationSec, w, h } = o;
  const fps = 30;
  const ew = w % 2 === 0 ? w : w + 1;
  const eh = h % 2 === 0 ? h : h + 1;
  const frames = Math.round(durationSec * fps);

  // zoompan: gentle zoom from 1.0 to 1.05, slight pan toward centre-left
  const zoompanFilter =
    `zoompan=z='min(zoom+0.0015,1.05)':` +
    `x='iw/2-(iw/zoom/2)':` +
    `y='ih/2-(ih/zoom/2)':` +
    `d=${frames}:s=${ew}x${eh}:fps=${fps}`;

  const vf = `${zoompanFilter},format=yuv420p`;

  let stderr = '';
  try {
    const { stderr: se } = await execFile(
      FFMPEG,
      [
        '-y',
        '-loop', '1',
        '-i', imagePath,
        '-vf', vf,
        '-c:v', 'libx264',
        '-preset', 'fast',
        '-crf', '18',
        '-t', String(durationSec),
        '-an',
        out,
      ],
      EXEC_OPTS,
    );
    stderr = se;
    return out;
  } catch (e) {
    const se = e instanceof Error && 'stderr' in e ? String((e as Record<string, unknown>).stderr) : stderr;
    throw ffmpegError(`kenBurnsClip(${imagePath} → ${out})`, se, e);
  }
}

/**
 * Overlay a transparent PNG over a video.
 * The PNG is scaled to the video's W×H.
 * If start/end are given, the overlay is gated with enable='between(t,start,end)'.
 * Audio is preserved as-is (-c:a copy, fallback to aac).
 */
export async function burnOverlay(
  video: string,
  overlayPng: string,
  out: string,
  o?: { start?: number; end?: number },
): Promise<string> {
  const { start, end } = o ?? {};

  // Build the overlay filter. scale2ref scales the overlay (input 1) to the main
  // video's real dimensions (input 0), then composites it on top.
  const enable = start !== undefined && end !== undefined ? `:enable='between(t,${start},${end})'` : '';
  const overlayFilter = `[1:v][0:v]scale2ref=w=iw:h=ih[ov][base];[base][ov]overlay=0:0${enable}`;

  let stderr = '';
  try {
    // Try with -c:a copy first; ffmpeg fails if there's no audio stream
    const { stderr: se } = await execFile(
      FFMPEG,
      [
        '-y',
        '-i', video,
        '-i', overlayPng,
        '-filter_complex', overlayFilter,
        '-c:v', 'libx264',
        '-preset', 'fast',
        '-crf', '18',
        '-c:a', 'copy',
        '-map', '0:a?',
        out,
      ],
      EXEC_OPTS,
    );
    stderr = se;
    return out;
  } catch (firstErr) {
    // Retry: re-encode audio as aac or skip audio entirely
    stderr =
      firstErr instanceof Error && 'stderr' in firstErr
        ? String((firstErr as Record<string, unknown>).stderr)
        : stderr;
    console.error(`[ffmpeg] burnOverlay: primer intento falló, reintentando sin audio copia`, stderrTail(stderr, 5));
    try {
      const { stderr: se2 } = await execFile(
        FFMPEG,
        [
          '-y',
          '-i', video,
          '-i', overlayPng,
          '-filter_complex', overlayFilter,
          '-c:v', 'libx264',
          '-preset', 'fast',
          '-crf', '18',
          '-an',
          out,
        ],
        EXEC_OPTS,
      );
      stderr = se2;
      return out;
    } catch (e) {
      const se = e instanceof Error && 'stderr' in e ? String((e as Record<string, unknown>).stderr) : stderr;
      throw ffmpegError(`burnOverlay(${video} → ${out})`, se, e);
    }
  }
}

/**
 * Concatenate video-only clips IN ORDER.
 *
 * If crossfadeSec > 0: use xfade filter chaining, probing each clip's duration
 * to compute per-transition offsets.
 *
 * If no crossfade (or crossfadeSec === 0): use the concat filter (not the
 * concat demuxer) for robustness against minor codec differences.
 *
 * Output: yuv420p, libx264, -an (audio is muxed later by muxAudio).
 */
export async function concatClips(
  clips: string[],
  out: string,
  o?: { crossfadeSec?: number; w?: number; h?: number },
): Promise<string> {
  if (clips.length === 0) throw new Error('[ffmpeg] concatClips: lista de clips vacía');
  if (clips.length === 1) {
    // Nothing to concat — just copy
    let stderr = '';
    try {
      const { stderr: se } = await execFile(
        FFMPEG,
        ['-y', '-i', clips[0], '-c:v', 'libx264', '-preset', 'fast', '-crf', '18',
         '-pix_fmt', 'yuv420p', '-an', out],
        EXEC_OPTS,
      );
      stderr = se;
      return out;
    } catch (e) {
      const se = e instanceof Error && 'stderr' in e ? String((e as Record<string, unknown>).stderr) : stderr;
      throw ffmpegError(`concatClips single clip copy`, se, e);
    }
  }

  const crossfadeSec = o?.crossfadeSec ?? 0;

  if (crossfadeSec > 0) {
    return _concatWithXfade(clips, out, crossfadeSec);
  } else {
    return _concatSimple(clips, out);
  }
}

async function _concatSimple(clips: string[], out: string): Promise<string> {
  // Build filter_complex concat
  const inputs: string[] = [];
  for (const c of clips) {
    inputs.push('-i', c);
  }
  const segments = clips.map((_, i) => `[${i}:v]`).join('');
  const filterComplex = `${segments}concat=n=${clips.length}:v=1:a=0[vout]`;

  let stderr = '';
  try {
    const { stderr: se } = await execFile(
      FFMPEG,
      [
        '-y',
        ...inputs,
        '-filter_complex', filterComplex,
        '-map', '[vout]',
        '-c:v', 'libx264',
        '-preset', 'fast',
        '-crf', '18',
        '-pix_fmt', 'yuv420p',
        '-an',
        out,
      ],
      EXEC_OPTS,
    );
    stderr = se;
    return out;
  } catch (e) {
    const se = e instanceof Error && 'stderr' in e ? String((e as Record<string, unknown>).stderr) : stderr;
    throw ffmpegError(`concatClips simple(${clips.length} clips → ${out})`, se, e);
  }
}

async function _concatWithXfade(clips: string[], out: string, crossfadeSec: number): Promise<string> {
  // Probe durations for all clips
  const durations = await Promise.all(clips.map(c => probeDurationSec(c)));

  // Build xfade chain:
  // [0:v][1:v]xfade=transition=fade:duration=X:offset=Y[x1];
  // [x1][2:v]xfade=transition=fade:duration=X:offset=Z[x2]; ...
  const inputs: string[] = [];
  for (const c of clips) {
    inputs.push('-i', c);
  }

  const filterParts: string[] = [];
  let cumulativeDuration = durations[0];
  let prevLabel = '[0:v]';

  for (let i = 1; i < clips.length; i++) {
    const offset = cumulativeDuration - crossfadeSec;
    const outLabel = i === clips.length - 1 ? '[vout]' : `[x${i}]`;
    filterParts.push(
      `${prevLabel}[${i}:v]xfade=transition=fade:duration=${crossfadeSec}:offset=${offset.toFixed(4)}${outLabel}`,
    );
    prevLabel = `[x${i}]`;
    cumulativeDuration += durations[i] - crossfadeSec;
  }

  const filterComplex = filterParts.join(';');

  let stderr = '';
  try {
    const { stderr: se } = await execFile(
      FFMPEG,
      [
        '-y',
        ...inputs,
        '-filter_complex', filterComplex,
        '-map', '[vout]',
        '-c:v', 'libx264',
        '-preset', 'fast',
        '-crf', '18',
        '-pix_fmt', 'yuv420p',
        '-an',
        out,
      ],
      EXEC_OPTS,
    );
    stderr = se;
    return out;
  } catch (e) {
    const se = e instanceof Error && 'stderr' in e ? String((e as Record<string, unknown>).stderr) : stderr;
    throw ffmpegError(`concatClips xfade(${clips.length} clips → ${out})`, se, e);
  }
}

/**
 * Turn an end-card PNG into a short clip at the video's resolution, then
 * concat it after the main video.
 * Probes the video's dimensions via ffprobe.
 */
export async function appendEndCard(
  video: string,
  endCardPng: string,
  out: string,
  o?: { seconds?: number; keepAudio?: boolean },
): Promise<string> {
  const seconds = o?.seconds ?? 2.5;
  const keepAudio = o?.keepAudio === true;
  const { w, h } = await probeDimensions(video);

  const ew = w % 2 === 0 ? w : w + 1;
  const eh = h % 2 === 0 ? h : h + 1;

  // Step 1: render end-card PNG → tmp mp4 clip. When keepAudio, give the card a
  // SILENT stereo track so the concat below can keep an audio stream on both inputs.
  const endCardClip = tmpFile('mp4');
  let stderr = '';
  try {
    const args = ['-y', '-loop', '1', '-i', endCardPng];
    if (keepAudio) args.push('-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100');
    args.push(
      '-vf', `scale=${ew}:${eh}:force_original_aspect_ratio=increase,crop=${ew}:${eh},format=yuv420p`,
      '-c:v', 'libx264', '-preset', 'fast', '-crf', '18',
      '-t', String(seconds),
    );
    if (keepAudio) args.push('-c:a', 'aac', '-ar', '44100', '-ac', '2', '-shortest');
    else args.push('-an');
    args.push(endCardClip);
    const { stderr: se1 } = await execFile(FFMPEG, args, EXEC_OPTS);
    stderr = se1;
  } catch (e) {
    const se = e instanceof Error && 'stderr' in e ? String((e as Record<string, unknown>).stderr) : stderr;
    throw ffmpegError(`appendEndCard: renderizar end-card PNG`, se, e);
  }

  // Step 2: concat main video + end-card clip.
  try {
    if (keepAudio) {
      // Preserve the main clip's audio (e.g. Gemini Omni's native lip-synced voice).
      // _concatSimple intentionally strips audio (a=0 + -an) for the assemble.ts path
      // where audio is muxed later — so we do an audio-preserving concat HERE instead.
      // Normalize both audio streams (resample + format) so the concat filter accepts them.
      await execFile(
        FFMPEG,
        [
          '-y',
          '-i', video,
          '-i', endCardClip,
          '-filter_complex',
          `[0:v]setsar=1[v0];[1:v]setsar=1[v1];` +
          `[0:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo[a0];` +
          `[1:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo[a1];` +
          `[v0][a0][v1][a1]concat=n=2:v=1:a=1[vout][aout]`,
          '-map', '[vout]', '-map', '[aout]',
          '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p',
          '-c:a', 'aac', '-ar', '44100',
          out,
        ],
        EXEC_OPTS,
      );
    } else {
      await _concatSimple([video, endCardClip], out);
    }
  } finally {
    // Clean up temp clip (best-effort)
    fs.unlink(endCardClip).catch(() => undefined);
  }

  return out;
}

/**
 * Mux audio track(s) onto a (silent) video.
 *
 * - Both voice + music: amix with music ducked to musicVolume, trimmed to
 *   video length (-shortest), aac output.
 * - Only voice or only music: add it directly (-shortest), aac.
 * - Video stream is always re-streamed unchanged (-c:v copy).
 */
export async function muxAudio(
  video: string,
  o: { voice?: string; music?: string; musicVolume?: number; out: string },
): Promise<string> {
  const { voice, music, musicVolume = 0.18, out } = o;

  if (!voice && !music) {
    throw new Error('[ffmpeg] muxAudio: se necesita al menos voice o music');
  }

  let stderr = '';

  if (voice && music) {
    // Mix: voice at full volume, music ducked
    try {
      const { stderr: se } = await execFile(
        FFMPEG,
        [
          '-y',
          '-i', video,
          '-i', voice,
          '-i', music,
          '-filter_complex',
          // Mix voice (full) + ducked music, then apad pads the mix with silence so
          // it is at least as long as the video; -shortest then trims to the VIDEO
          // length (so a short VO never truncates the clip).
          `[2:a]volume=${musicVolume}[mus];[1:a][mus]amix=inputs=2:duration=longest:dropout_transition=2[mx];[mx]apad[aout]`,
          '-map', '0:v',
          '-map', '[aout]',
          '-c:v', 'copy',
          '-c:a', 'aac',
          '-b:a', '192k',
          '-shortest',
          out,
        ],
        EXEC_OPTS,
      );
      stderr = se;
      return out;
    } catch (e) {
      const se = e instanceof Error && 'stderr' in e ? String((e as Record<string, unknown>).stderr) : stderr;
      throw ffmpegError(`muxAudio(voice+music → ${out})`, se, e);
    }
  }

  // Single audio track. apad pads the audio with trailing silence so it is at
  // least as long as the video; -shortest then trims the output to the VIDEO
  // length (a short voiceover never cuts the clip short).
  const audioFile = (voice ?? music) as string;
  try {
    const { stderr: se } = await execFile(
      FFMPEG,
      [
        '-y',
        '-i', video,
        '-i', audioFile,
        '-filter_complex', '[1:a]apad[aout]',
        '-map', '0:v',
        '-map', '[aout]',
        '-c:v', 'copy',
        '-c:a', 'aac',
        '-b:a', '192k',
        '-shortest',
        out,
      ],
      EXEC_OPTS,
    );
    stderr = se;
    return out;
  } catch (e) {
    const se = e instanceof Error && 'stderr' in e ? String((e as Record<string, unknown>).stderr) : stderr;
    throw ffmpegError(`muxAudio(single track → ${out})`, se, e);
  }
}

// ─── ASS subtitle helpers ─────────────────────────────────────────────────────

function secondsToAssTime(sec: number): string {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  const cs = Math.round((sec % 1) * 100);
  return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}.${String(cs).padStart(2, '0')}`;
}

function buildAssFile(
  cues: Array<{ start: number; end: number; text: string }>,
  w?: number,
  h?: number,
): string {
  const pw = w ?? 1080;
  const ph = h ?? 1920;
  // ~5% vertical margin
  const marginV = Math.round(ph * 0.05);

  const header = `[Script Info]
ScriptType: v4.00+
PlayResX: ${pw}
PlayResY: ${ph}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,${Math.round(ph * 0.048)},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,2,2,80,80,${marginV},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
`;

  const events = cues
    .map(c => {
      // Escape braces and newlines in subtitle text
      const safeText = c.text.replace(/\{/g, '\\{').replace(/\n/g, '\\N');
      return `Dialogue: 0,${secondsToAssTime(c.start)},${secondsToAssTime(c.end)},Default,,0,0,0,,${safeText}`;
    })
    .join('\n');

  return header + events + '\n';
}

/**
 * Burn hard-coded subtitles from cue objects.
 * Writes a temporary .ass file, then uses the subtitles/ass ffmpeg filter.
 * Audio is preserved.
 */
export async function burnSubtitles(
  video: string,
  cues: Array<{ start: number; end: number; text: string }>,
  out: string,
  o?: { w?: number; h?: number },
): Promise<string> {
  if (cues.length === 0) {
    // Nothing to burn — just copy
    let stderr = '';
    try {
      const { stderr: se } = await execFile(
        FFMPEG,
        ['-y', '-i', video, '-c', 'copy', out],
        EXEC_OPTS,
      );
      stderr = se;
      return out;
    } catch (e) {
      const se = e instanceof Error && 'stderr' in e ? String((e as Record<string, unknown>).stderr) : stderr;
      throw ffmpegError(`burnSubtitles copy-through(${video})`, se, e);
    }
  }

  const assContent = buildAssFile(cues, o?.w, o?.h);
  const assFile = tmpFile('ass');

  await fs.writeFile(assFile, assContent, 'utf8');
  console.error(`[ffmpeg] burnSubtitles: archivo .ass escrito → ${assFile} (${cues.length} cues)`);

  let stderr = '';
  try {
    // Escape the path for the subtitles filter (colons must be escaped on Windows
    // but on macOS we only need to worry about spaces and special chars)
    const escapedAss = assFile.replace(/\\/g, '/').replace(/:/g, '\\:');

    const { stderr: se } = await execFile(
      FFMPEG,
      [
        '-y',
        '-i', video,
        '-vf', `subtitles='${escapedAss}'`,
        '-c:v', 'libx264',
        '-preset', 'fast',
        '-crf', '18',
        '-c:a', 'copy',
        '-map', '0:a?',
        out,
      ],
      EXEC_OPTS,
    );
    stderr = se;
    return out;
  } catch (e) {
    const se = e instanceof Error && 'stderr' in e ? String((e as Record<string, unknown>).stderr) : stderr;
    throw ffmpegError(`burnSubtitles(${video} → ${out})`, se, e);
  } finally {
    fs.unlink(assFile).catch(() => undefined);
  }
}
