// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  PERSONAL CLIPPER — long video → N vertical clips (+ subtitles)                ║
// ║                                                                              ║
// ║  "Take this 18-min YouTube interview and cut 3 vertical TikTok clips."         ║
// ║  yt-dlp downloads the video and available YouTube auto-captions (VTT)           ║
// ║  model needed). Opus reads the timestamped transcript and picks the N most     ║
// ║  viral standalone moments (this is where Opus genuinely adds value — reasoning  ║
// ║  over text). ffmpeg cuts each, reframes to 9:16, and burns its subtitles.      ║
// ║  No transcript (non-YouTube / captions off) → evenly-spaced clips, no subs.    ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import os from 'node:os';
import path from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { readFile, writeFile, readdir, unlink } from 'node:fs/promises';
import { callAIJSON } from '../ai/deepseek-client';
import { burnSubtitles, probeDurationSec } from './ffmpeg';

const exec = promisify(execFile);
const FFMPEG = process.env.FFMPEG_PATH || 'ffmpeg';
const YTDLP = process.env.YTDLP_PATH || 'yt-dlp';
const LOG = '[clipper]';

let _seq = 0;
const tmp = (ext: string) => path.join(os.tmpdir(), `fyd-clip-${Date.now()}-${++_seq}.${ext}`);

interface Cue { start: number; end: number; text: string }
export interface Clip { file: string; start: number; end: number; reason: string; hasSubs: boolean }
export interface ClipResult { clips: Clip[]; sourceDurationSec: number; usedTranscript: boolean }

const hhmmss = (s: number) => {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = (s % 60);
  return `${h ? h + ':' : ''}${String(m).padStart(2, '0')}:${sec.toFixed(2).padStart(5, '0')}`;
};

/** Parse a WebVTT timestamp (00:01:02.500 or 01:02.500) to seconds. */
function vttTime(t: string): number {
  const parts = t.trim().replace(',', '.').split(':').map(Number);
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  if (parts.length === 2) return parts[0] * 60 + parts[1];
  return Number(parts[0]) || 0;
}

/** Parse VTT text into deduped cues (YouTube auto-subs repeat lines heavily). */
function parseVtt(vtt: string): Cue[] {
  const cues: Cue[] = [];
  const blocks = vtt.replace(/\r/g, '').split(/\n\n+/);
  for (const b of blocks) {
    const m = b.match(/(\d{1,2}:\d{2}:\d{2}[.,]\d{3}|\d{1,2}:\d{2}[.,]\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[.,]\d{3}|\d{1,2}:\d{2}[.,]\d{3})/);
    if (!m) continue;
    const start = vttTime(m[1]); const end = vttTime(m[2]);
    const text = b.split('\n').slice(1).join(' ')
      .replace(/<[^>]+>/g, '').replace(/\{[^}]+\}/g, '').replace(/\s+/g, ' ').trim();
    if (!text) continue;
    const prev = cues[cues.length - 1];
    if (prev && prev.text === text) { prev.end = end; continue; } // collapse repeats
    cues.push({ start, end, text });
  }
  return cues;
}

/** Download a source (YouTube/social via yt-dlp incl. auto-subs, else http). */
async function fetchSource(input: { url?: string; file?: string }): Promise<{ video: string; cues: Cue[]; cleanup: string[] }> {
  if (input.file && !/^https?:\/\//i.test(input.file)) return { video: input.file, cues: [], cleanup: [] };
  const src = input.url || input.file;
  if (!src) throw new Error(`${LOG} se requiere "url" o "file".`);
  const cleanup: string[] = [];
  if (/youtube\.com|youtu\.be|tiktok\.com|vimeo\.com|instagram\.com/i.test(src)) {
    const base = tmp('').replace(/\.$/, '');
    const fmt = ['-f', 'mp4[height<=720]/best[height<=720]/best', '--no-playlist'];
    const subArgs = ['--write-auto-subs', '--write-subs', '--sub-langs', 'en.*,es.*', '--sub-format', 'vtt'];
    const outArgs = ['-o', `${base}.%(ext)s`];
    // Datacenter IPs get YouTube's "confirm you're not a bot" wall — authenticate with the
    // user's browser cookies. Set YTDLP_COOKIES_FROM_BROWSER=chrome|safari|firefox (default
    // tries chrome). Attempts degrade: env-cookies+subs → chrome-cookies+subs → chrome no-subs → no-cookies.
    const cookieEnv = process.env.YTDLP_COOKIES_FROM_BROWSER;
    const attempts: string[][] = [
      [...fmt, ...subArgs, ...(cookieEnv ? ['--cookies-from-browser', cookieEnv] : []), ...outArgs, src],
      [...fmt, ...subArgs, '--cookies-from-browser', 'chrome', ...outArgs, src],
      [...fmt, '--cookies-from-browser', 'chrome', ...outArgs, src],
      [...fmt, ...outArgs, src],
    ];
    let ok = false; let lastErr = '';
    for (const args of attempts) {
      try { await exec(YTDLP, args, { timeout: 600_000, maxBuffer: 1 << 27 }); ok = true; break; }
      catch (e) { lastErr = String((e as { stderr?: string; message?: string })?.stderr || (e as Error)?.message || e).slice(0, 300); }
    }
    if (!ok) throw new Error(`${LOG} yt-dlp no pudo bajar el video — la IP puede estar bloqueada por YouTube. En tu Mac configura YTDLP_COOKIES_FROM_BROWSER=chrome (o safari/firefox). Detalle: ${lastErr}`);
    const dir = path.dirname(base); const stem = path.basename(base);
    const files = (await readdir(dir)).filter((f) => f.startsWith(stem));
    const videoFile = files.find((f) => /\.(mp4|mkv|webm)$/i.test(f));
    const vttFile = files.find((f) => /\.vtt$/i.test(f));
    if (!videoFile) throw new Error(`${LOG} yt-dlp no bajó el video.`);
    const video = path.join(dir, videoFile); cleanup.push(video);
    let cues: Cue[] = [];
    if (vttFile) { cleanup.push(path.join(dir, vttFile)); try { cues = parseVtt(await readFile(path.join(dir, vttFile), 'utf8')); } catch { /* no subs */ } }
    files.forEach((f) => { const p = path.join(dir, f); if (p !== video && !cleanup.includes(p)) cleanup.push(p); });
    return { video, cues, cleanup };
  }
  // plain http video
  const out = tmp('mp4'); cleanup.push(out);
  const r = await fetch(src, { signal: AbortSignal.timeout(600_000) });
  if (!r.ok) throw new Error(`${LOG} descarga falló: ${r.status}`);
  await writeFile(out, Buffer.from(await r.arrayBuffer()));
  return { video: out, cues: [], cleanup };
}

/** Opus picks the N most viral standalone windows from the transcript. */
async function pickMoments(cues: Cue[], n: number, dur: number, clipLen: number): Promise<Array<{ start: number; end: number; reason: string }>> {
  const transcript = cues.map((c) => `[${c.start.toFixed(0)}s] ${c.text}`).join('\n').slice(0, 24000);
  const ask = `This is a timestamped transcript of a ${dur.toFixed(0)}s video. Pick the ${n} MOST viral, scroll-stopping, STANDALONE moments to cut as vertical short-form clips (each ~${clipLen}s, self-contained, with a strong hook). Return JSON:
{ "clips": [ { "start": <seconds>, "end": <seconds>, "reason": "why this hooks" } ] }
Return exactly ${n} clips, non-overlapping, in time order.

TRANSCRIPT:
${transcript}`;
  try {
    const res = await callAIJSON<{ clips?: Array<{ start: number; end: number; reason: string }> }>(ask, { maxTokens: 1200, json: true });
    const picks = (res?.clips || [])
      .map((c) => ({ start: Math.max(0, Number(c.start) || 0), end: Math.min(dur, Number(c.end) || 0), reason: String(c.reason || '') }))
      .filter((c) => c.end - c.start >= 3)
      .slice(0, n);
    if (picks.length) return picks;
  } catch { /* fall through to even split */ }
  return evenWindows(n, dur, clipLen);
}

function evenWindows(n: number, dur: number, clipLen: number): Array<{ start: number; end: number; reason: string }> {
  const out: Array<{ start: number; end: number; reason: string }> = [];
  const span = Math.max(clipLen, dur / n);
  for (let i = 0; i < n; i++) {
    const start = Math.min(dur - clipLen, i * span);
    out.push({ start: Math.max(0, start), end: Math.max(0, start) + Math.min(clipLen, dur), reason: 'segmento uniforme' });
  }
  return out;
}

/**
 * Cut a long video into N vertical (9:16) short clips, with subtitles when a
 * transcript is available (YouTube auto-subs).
 */
export async function clipVideo(
  input: { url?: string; file?: string },
  opts: { count?: number; clipLengthSec?: number; aspect?: string } = {},
): Promise<ClipResult> {
  const n = Math.max(1, Math.min(10, opts.count || 3));
  const clipLen = Math.max(8, Math.min(90, opts.clipLengthSec || 25));
  const vertical = (opts.aspect || '9:16') !== '16:9';
  const W = vertical ? 1080 : 1920, H = vertical ? 1920 : 1080;

  const { video, cues, cleanup } = await fetchSource(input);
  try {
    const dur = await probeDurationSec(video).catch(() => 0);
    if (!dur) throw new Error(`${LOG} no se pudo leer la duración del video.`);
    const usedTranscript = cues.length > 0;
    console.error(`${LOG} fuente ${dur.toFixed(0)}s · transcript: ${usedTranscript ? cues.length + ' cues' : 'no'} · cortando ${n} clips`);
    const windows = usedTranscript ? await pickMoments(cues, n, dur, clipLen) : evenWindows(n, dur, clipLen);

    const clips: Clip[] = [];
    for (let i = 0; i < windows.length; i++) {
      const w = windows[i];
      const start = Math.max(0, w.start);
      const len = Math.max(3, Math.min(clipLen * 1.6, (w.end || start + clipLen) - start)) || clipLen;
      try {
        // cut + reframe to 9:16 (scale to cover, center-crop)
        const cut = tmp('mp4');
        await exec(FFMPEG, ['-y', '-ss', String(start), '-t', String(len), '-i', video,
          '-vf', `scale=${W}:${H}:force_original_aspect_ratio=increase,crop=${W}:${H},fps=30,format=yuv420p`,
          '-c:v', 'libx264', '-crf', '20', '-preset', 'medium', '-c:a', 'aac', '-movflags', '+faststart', cut],
          { timeout: 300_000, maxBuffer: 1 << 27 });
        let finalClip = cut;
        let hasSubs = false;
        // burn the cues that fall in this window (shifted to clip-relative time)
        const winCues = cues
          .filter((c) => c.end > start && c.start < start + len)
          .map((c) => ({ start: Math.max(0, c.start - start), end: Math.min(len, c.end - start), text: c.text }))
          .filter((c) => c.text && c.end > c.start);
        if (winCues.length) {
          try { finalClip = await burnSubtitles(cut, winCues, tmp('mp4'), { w: W, h: H }); hasSubs = true; }
          catch (e) { console.error(`${LOG} subtítulos clip ${i + 1} fallaron (se omiten):`, e instanceof Error ? e.message : e); }
        }
        // delete the intermediate pre-subtitle cut if a new (subtitled) file replaced it.
        if (finalClip !== cut) await unlink(cut).catch(() => undefined);
        clips.push({ file: finalClip, start, end: start + len, reason: w.reason, hasSubs });
        console.error(`${LOG} ✓ clip ${i + 1}/${windows.length} @ ${hhmmss(start)} (${len.toFixed(0)}s, subs:${hasSubs})`);
      } catch (e) {
        console.error(`${LOG} clip ${i + 1} falló:`, e instanceof Error ? e.message : e);
      }
    }
    if (!clips.length) throw new Error(`${LOG} no se pudo cortar ningún clip.`);
    return { clips, sourceDurationSec: dur, usedTranscript };
  } finally {
    for (const f of cleanup) unlink(f).catch(() => undefined);
  }
}
