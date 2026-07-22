// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  brand-image — reusable on-brand STILL generator + Muapi image hosting        ║
// ║                                                                              ║
// ║  Extracted so the video assembler, persona/influencer engine and the          ║
// ║  supercomputer batch can all generate the SAME on-brand, text-free stills     ║
// ║  the post pipeline does, and turn a still into a URL an image→video model      ║
// ║  can fetch. The no-text / no-UI policy is already enforced inside              ║
// ║  generateImagenImage (the Vertex chokepoint); this is a thin, shared front.    ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import os from 'os';
import path from 'path';
import { writeFile, readFile } from 'fs/promises';
import { generateImagenImage } from './imagen-client';
import type { VideoAspect } from '../video/types';

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

const IMAGEN4: Record<string, string> = {
  ultra: 'imagen-4.0-ultra-generate-001',
  standard: 'imagen-4.0-generate-001',
  fast: 'imagen-4.0-fast-generate-001',
};

function modelForQuality(quality?: string): string {
  const q = quality || 'standard';
  if (q === 'brand') return process.env.GOOGLE_PREMIUM_IMAGE_MODEL || 'gemini-3-pro-image-preview';
  return IMAGEN4[q] || IMAGEN4.standard;
}

async function retry429<T>(fn: () => Promise<T>, tries = 4): Promise<T> {
  let lastErr: unknown;
  for (let i = 0; i < tries; i++) {
    try { return await fn(); }
    catch (e) {
      lastErr = e;
      const msg = e instanceof Error ? e.message : String(e);
      if (/429|resource_exhausted|quota/i.test(msg) && i < tries - 1) {
        await sleep(15000 * (i + 1));
        continue;
      }
      throw e;
    }
  }
  throw lastErr;
}

/**
 * Generate one on-brand, text-free still via Vertex (Imagen 4 / Nano Banana).
 * The no-text/no-UI policy is enforced inside generateImagenImage.
 */
export async function generateBrandStill(
  prompt: string,
  opts: {
    quality?: 'ultra' | 'standard' | 'fast' | 'brand';
    aspect?: VideoAspect;
    references?: Array<{ data: string; mimeType: string }>;
    verifyTextFree?: boolean;
    allowUi?: boolean;
    allowText?: boolean;
  } = {},
): Promise<{ dataUrl: string; model: string }> {
  const vModel = modelForQuality(opts.quality);
  const ar = (['1:1', '16:9', '9:16', '4:3', '3:4'].includes(opts.aspect || '')
    ? opts.aspect
    : '16:9') as VideoAspect;
  // References (logo / app screens / persona refs) are only honored by gemini-image.
  const refs = vModel.includes('gemini') ? (opts.references || []) : [];
  const img = await retry429(() =>
    generateImagenImage(prompt, {
      aspectRatio: ar,
      references: refs,
      model: vModel,
      allowUi: opts.allowUi,
      allowText: opts.allowText,
    }),
  );
  return { dataUrl: img.dataUrl, model: `vertex:${vModel}` };
}

let _tmpSeq = 0;
/** Write a data URL to a temp .png and return its absolute path. */
export async function dataUrlToTmpPng(dataUrl: string): Promise<string> {
  const m = /^data:[^;]+;base64,([\s\S]+)$/.exec(dataUrl);
  if (!m) throw new Error('dataUrlToTmpPng: not a base64 data URL');
  const p = path.join(os.tmpdir(), `fyd-still-${Date.now()}-${++_tmpSeq}.png`);
  await writeFile(p, Buffer.from(m[1], 'base64'));
  return p;
}

/**
 * Turn a still (data URL, file path, or Buffer) into a URL a Muapi image→video
 * model can fetch. Prefers a temporary GCS v4 signed URL; falls back to the raw
 * data URL (most Muapi models accept base64 data URIs).
 */
export async function hostStillForMuapi(src: string | Buffer): Promise<string> {
  let buf: Buffer;
  let dataUrl = '';
  if (Buffer.isBuffer(src)) {
    buf = src;
    dataUrl = `data:image/png;base64,${buf.toString('base64')}`;
  } else if (src.startsWith('data:')) {
    dataUrl = src;
    const m = /^data:[^;]+;base64,([\s\S]+)$/.exec(src);
    buf = m ? Buffer.from(m[1], 'base64') : Buffer.alloc(0);
  } else if (src.startsWith('http')) {
    return src; // already a URL
  } else {
    buf = await readFile(src);
    dataUrl = `data:image/png;base64,${buf.toString('base64')}`;
  }
  // Try GCS signed URL (works without a public bucket).
  try {
    const { getBucket, uploadToGCS, generateGcsPath } = await import('../gcs');
    const objectPath = generateGcsPath(`vid-${Date.now()}-${Math.floor(Math.random() * 1e6)}`, 'video-still', 'png');
    await uploadToGCS(objectPath, buf, 'image/png');
    const [url] = await getBucket().file(objectPath).getSignedUrl({
      version: 'v4',
      action: 'read',
      expires: Date.now() + 24 * 3600 * 1000,
    });
    if (url) return url;
  } catch { /* GCS not configured → fall back to data URL */ }
  return dataUrl;
}

/** Load a reference image (path / http URL / data URL) as inline base64 (no data: prefix). */
export async function loadRefInline(src: string): Promise<{ data: string; mimeType: string } | null> {
  if (!src) return null;
  if (src.startsWith('data:')) {
    const m = /^data:([^;]+);base64,([\s\S]+)$/.exec(src);
    return m ? { mimeType: m[1], data: m[2] } : null;
  }
  if (src.startsWith('http')) {
    try {
      const r = await fetch(src, { signal: AbortSignal.timeout(60_000) });
      if (!r.ok) return null;
      const buf = Buffer.from(await r.arrayBuffer());
      return { data: buf.toString('base64'), mimeType: r.headers.get('content-type') || 'image/png' };
    } catch { return null; }
  }
  try {
    const buf = await readFile(src);
    const ext = (src.split('.').pop() || 'png').toLowerCase();
    const mimeType = ext === 'jpg' || ext === 'jpeg' ? 'image/jpeg' : ext === 'webp' ? 'image/webp' : 'image/png';
    return { data: buf.toString('base64'), mimeType };
  } catch { return null; }
}
