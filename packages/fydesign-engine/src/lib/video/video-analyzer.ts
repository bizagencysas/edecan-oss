// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  VIDEO ANALYZER — "analyze this video, I want something like that"             ║
// ║                                                                              ║
// ║  Take a video (a URL via yt-dlp, or a local/remote file) → extract evenly-    ║
// ║  spaced keyframes → show them to Opus (vision) → deconstruct the structure     ║
// ║  (concept, camera, lighting, subject, pacing) and synthesize ONE paste-ready   ║
// ║  generation prompt to recreate a video like it. No guessing — Opus SEES it.    ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import os from 'node:os';
import path from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { readFile, writeFile, unlink } from 'node:fs/promises';
import { callAIJSON, type InlineImage } from '../ai/deepseek-client';

const exec = promisify(execFile);
const FFMPEG = process.env.FFMPEG_PATH || 'ffmpeg';
const FFPROBE = process.env.FFPROBE_PATH || 'ffprobe';
const YTDLP = process.env.YTDLP_PATH || 'yt-dlp';
const LOG = '[video-analyzer]';

let _seq = 0;
const tmp = (ext: string) => path.join(os.tmpdir(), `fyd-analyze-${Date.now()}-${++_seq}.${ext}`);

export interface VideoAnalysis {
  concept: string;
  structure: string[];
  camera: string;
  lighting: string;
  subject: string;
  pacing: string;
  audioFeel: string;
  /** One paste-ready text-to-video prompt to recreate a video like this. */
  recreatePrompt: string;
  /** Seconds analyzed + how many frames Opus saw. */
  durationSec: number;
  framesSeen: number;
}

/** Resolve a URL (yt-dlp) or http file (download) or local path to a local file. */
async function resolveToFile(input: { url?: string; file?: string }): Promise<{ file: string; cleanup: boolean }> {
  if (input.file && !/^https?:\/\//i.test(input.file)) return { file: input.file, cleanup: false };
  const src = input.url || input.file;
  if (!src) throw new Error(`${LOG} se requiere "url" o "file".`);
  const out = tmp('mp4');
  if (/youtube\.com|youtu\.be|tiktok\.com|instagram\.com|vimeo\.com/i.test(src)) {
    // social/streaming URL → yt-dlp (cap to 720p to keep it fast).
    await exec(YTDLP, ['-f', 'mp4[height<=720]/best[height<=720]/best', '--no-playlist', '-o', out, src], {
      timeout: 300_000, maxBuffer: 1 << 26,
    });
    return { file: out, cleanup: true };
  }
  // plain http(s) video → fetch
  const r = await fetch(src, { signal: AbortSignal.timeout(300_000) });
  if (!r.ok) throw new Error(`${LOG} descarga falló: ${r.status}`);
  await writeFile(out, Buffer.from(await r.arrayBuffer()));
  return { file: out, cleanup: true };
}

async function probeDuration(file: string): Promise<number> {
  try {
    const { stdout } = await exec(FFPROBE, ['-v', 'quiet', '-show_entries', 'format=duration', '-of', 'csv=p=0', file]);
    return parseFloat(stdout.trim()) || 0;
  } catch { return 0; }
}

async function frameAt(file: string, t: number): Promise<string> {
  const out = tmp('jpg');
  await exec(FFMPEG, ['-y', '-ss', String(t), '-i', file, '-frames:v', '1', '-vf', 'scale=640:-1', '-q:v', '4', out]);
  return out;
}

const BRAIN = `You are a senior creative director and cinematographer. You are shown evenly-spaced frames sampled from ONE short video, in order. Deconstruct it PRECISELY — what you actually SEE, no guessing about things off-screen — so the video could be recreated, and write a single paste-ready generation prompt. Return STRICT JSON only.`;

/**
 * Analyze a video and return a structured deconstruction + a paste-ready recreate prompt.
 * `input.url` (YouTube/TikTok/etc. via yt-dlp) OR `input.file` (local path or http URL).
 */
export async function analyzeVideo(
  input: { url?: string; file?: string },
  opts: { frames?: number } = {},
): Promise<VideoAnalysis> {
  const { file, cleanup } = await resolveToFile(input);
  const created: string[] = [];
  try {
    const dur = await probeDuration(file);
    const n = Math.max(4, Math.min(10, opts.frames || 7));
    const times = dur > 0
      ? Array.from({ length: n }, (_, i) => +(((i + 0.5) * dur) / n).toFixed(2))
      : Array.from({ length: n }, (_, i) => i * 1.0);

    const images: InlineImage[] = [];
    for (const t of times) {
      try {
        const f = await frameAt(file, t);
        created.push(f);
        const b = await readFile(f);
        if (b.length) images.push({ mimeType: 'image/jpeg', data: b.toString('base64') });
      } catch { /* skip a bad frame */ }
    }
    if (images.length === 0) throw new Error(`${LOG} no se pudieron extraer frames del video.`);
    console.error(`${LOG} analizando ${images.length} frames (${dur ? dur.toFixed(1) + 's' : 'duración desconocida'})…`);

    const ask = `These are ${images.length} evenly-spaced frames from a ${dur ? dur.toFixed(1) + 's' : 'short'} video, in order. Return JSON:
{
  "concept": "one line: what this video is / what it sells",
  "structure": ["beat 1: what happens", "beat 2: ...", "..."],
  "camera": "camera work, movement, angles, lens feel",
  "lighting": "lighting setup + color grade",
  "subject": "subject(s), styling, wardrobe, setting, props",
  "pacing": "edit rhythm and energy",
  "audioFeel": "the audio/music vibe this likely has",
  "recreatePrompt": "ONE vivid, specific, model-agnostic text-to-video prompt to recreate a video like this"
}`;

    const res = await callAIJSON<Partial<VideoAnalysis>>(ask, {
      system: BRAIN,
      images,
      maxTokens: 1600,
      json: true,
      model: process.env.CLAUDE_VISION_MODEL || undefined,
    });
    if (!res || !res.recreatePrompt) throw new Error(`${LOG} Opus no devolvió un análisis válido.`);

    return {
      concept: String(res.concept || ''),
      structure: Array.isArray(res.structure) ? res.structure.map(String) : [],
      camera: String(res.camera || ''),
      lighting: String(res.lighting || ''),
      subject: String(res.subject || ''),
      pacing: String(res.pacing || ''),
      audioFeel: String(res.audioFeel || ''),
      recreatePrompt: String(res.recreatePrompt),
      durationSec: dur,
      framesSeen: images.length,
    };
  } finally {
    await Promise.all([
      ...created.map((f) => unlink(f).catch(() => undefined)),
      ...(cleanup ? [unlink(file).catch(() => undefined)] : []),
    ]);
  }
}
