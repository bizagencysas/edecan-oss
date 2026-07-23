// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Asset Registry — Structured asset intelligence (replaces base64 blobs)      ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import crypto from 'crypto';
import { embedText, EMBEDDING_DIM, isEmbeddingAvailable } from './embedding';
import {
  saveAsset,
  saveAssetBrandMapping,
  saveExtractedIdentity,
  loadAssetsByBrand,
  loadAssetsByProject,
  loadIdentityByBrand,
  loadIdentityByType,
  type AssetRow,
} from './asset-storage';
import { uploadToGCS, generateGcsPath } from '@/lib/gcs';

export type AssetCategory = 'logo' | 'font' | 'screenshot' | 'icon' | 'image' | 'design-file' | 'document' | 'other';
export type IdentityType = 'color-palette' | 'typography-system' | 'spacing-scale' | 'border-radius-system' | 'shadow-system' | 'icon-style' | 'composition-pattern' | 'logo-analysis' | 'brand-voice';
export type BrandRole = 'primary-logo' | 'secondary-logo' | 'symbol' | 'wordmark' | 'brand-color' | 'brand-font' | 'screenshot' | 'mood-reference' | 'style-reference' | 'other';

export interface AssetInput {
  name: string;
  originalName: string;
  category: AssetCategory;
  mimeType: string;
  fileSize: number;
  buffer: Buffer;
  brandId?: string;
  projectId?: string;
  metadata?: Record<string, unknown>;
  tags?: string[];
}

export interface RegisteredAsset {
  id: string;
  name: string;
  originalName: string;
  category: AssetCategory;
  mimeType: string;
  fileSize: number;
  storageUrl: string;
  thumbnailUrl: string | null;
  metadata: Record<string, unknown>;
  extractedTokens: Record<string, unknown> | null;
  tags: string[];
  brandAssociation: Record<string, unknown>;
  embedding: number[] | null;
}

export interface ExtractedVisualIdentity {
  id: string;
  brandId?: string;
  projectId?: string;
  assetId?: string;
  identityType: IdentityType;
  extractedData: Record<string, unknown>;
  confidence: number;
  source: string;
}

export interface AssetManifest {
  asset: RegisteredAsset;
  identityExtractions: ExtractedVisualIdentity[];
  brandMappings: { role: BrandRole; confidence: number }[];
}

const CATEGORY_MAP: Record<string, AssetCategory> = {
  png: 'image', jpg: 'image', jpeg: 'image', gif: 'image', webp: 'image',
  svg: 'logo', ico: 'icon', bmp: 'image', avif: 'image', tiff: 'image',
  ttf: 'font', otf: 'font', woff: 'font', woff2: 'font', eot: 'font',
  fig: 'design-file', sketch: 'design-file', xd: 'design-file',
  psd: 'design-file', ai: 'design-file', eps: 'design-file',
  pdf: 'document', doc: 'document', docx: 'document', txt: 'document',
  mp4: 'image', mov: 'image', avi: 'image', webm: 'image',
};

function detectCategory(fileName: string): AssetCategory {
  const ext = fileName.split('.').pop()?.toLowerCase() || '';
  // SVG with brand context gets 'logo'
  if (ext === 'svg') return 'logo';
  return CATEGORY_MAP[ext] || 'other';
}

function detectMimeType(ext: string): string {
  const map: Record<string, string> = {
    png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif',
    webp: 'image/webp', svg: 'image/svg+xml', ico: 'image/x-icon',
    avif: 'image/avif', tiff: 'image/tiff', bmp: 'image/bmp',
    ttf: 'font/ttf', otf: 'font/otf', woff: 'font/woff', woff2: 'font/woff2',
    pdf: 'application/pdf', mp4: 'video/mp4', mov: 'video/quicktime',
  };
  return map[ext] || 'application/octet-stream';
}

// ── Visual Identity Extraction ──────────────────────────────────────────────

function extractColorPalette(buffer: Buffer, fileName: string): ExtractedVisualIdentity | null {
  const ext = fileName.split('.').pop()?.toLowerCase() || '';
  if (!['svg'].includes(ext)) return null;

  const svgText = buffer.toString('utf-8').slice(0, 50000);
  const hexColors = svgText.match(/#[0-9A-Fa-f]{3,8}\b/g) || [];
  const fillColors = svgText.match(/fill\s*=\s*["']([^"']+)["']/g) || [];
  const uniqueColors = [...new Set(hexColors)].slice(0, 20);

  if (uniqueColors.length === 0) return null;

  return {
    id: `vi-${crypto.randomUUID().slice(0, 12)}`,
    identityType: 'color-palette',
    extractedData: {
      colors: uniqueColors.map((c) => ({ hex: c, source: 'svg-extraction' })),
      dominantCount: uniqueColors.length,
      extractionMethod: 'svg-parse',
    },
    confidence: Math.min(0.3 + uniqueColors.length * 0.05, 0.85),
    source: fileName,
  };
}

function extractLogoAnalysis(buffer: Buffer, fileName: string): ExtractedVisualIdentity | null {
  const ext = fileName.split('.').pop()?.toLowerCase() || '';
  if (ext !== 'svg') return null;

  const svgText = buffer.toString('utf-8').slice(0, 50000);
  const hasGradients = /<linearGradient|<radialGradient/.test(svgText);
  const hasComplexPaths = (svgText.match(/<path\b/g) || []).length > 3;
  const hasText = /<text\b/.test(svgText);
  const viewBox = svgText.match(/viewBox\s*=\s*["']([^"']+)["']/)?.[1] || '';
  const dimensions = viewBox.split(/\s+/).slice(2).map(Number);
  const isWide = dimensions.length >= 2 && dimensions[0]! > dimensions[1]! * 1.5;

  return {
    id: `vi-${crypto.randomUUID().slice(0, 12)}`,
    identityType: 'logo-analysis',
    extractedData: {
      hasGradients,
      hasComplexPaths,
      hasText,
      isWide,
      viewBox,
      wordmark: hasText && !hasComplexPaths,
      symbolic: hasComplexPaths && !hasText,
      combination: hasText && hasComplexPaths,
    },
    confidence: 0.7,
    source: fileName,
  };
}

function extractFontMetadata(buffer: Buffer, fileName: string): ExtractedVisualIdentity | null {
  const ext = fileName.split('.').pop()?.toLowerCase() || '';
  if (!['ttf', 'otf', 'woff', 'woff2'].includes(ext)) return null;

  // Extract basic font metadata from binary headers
  // TTF/OTF: name table starts at offset specified in offset table
  const isOTF = buffer.toString('ascii', 0, 4) === 'OTTO';
  const isTTF = buffer.toString('ascii', 0, 4) === '\x00\x01\x00\x00' || buffer.toString('ascii', 0, 4) === 'true';
  const isWOFF = buffer.toString('ascii', 0, 4) === 'wOFF' || buffer.toString('ascii', 0, 4) === 'wOF2';

  if (!isOTF && !isTTF && !isWOFF) return null;

  return {
    id: `vi-${crypto.randomUUID().slice(0, 12)}`,
    identityType: 'typography-system',
    extractedData: {
      format: isOTF ? 'otf' : isTTF ? 'ttf' : isWOFF ? 'woff' : 'unknown',
      fileName,
      fileSize: buffer.length,
      note: 'Full font metadata extraction requires opentype.js or similar library',
    },
    confidence: 0.5,
    source: fileName,
  };
}

function extractImageMetadata(buffer: Buffer, fileName: string): ExtractedVisualIdentity | null {
  const ext = fileName.split('.').pop()?.toLowerCase() || '';
  if (!['png', 'jpg', 'jpeg', 'gif', 'webp'].includes(ext)) return null;

  let width = 0, height = 0;

  // PNG: IHDR at offset 16-24
  if (ext === 'png' && buffer.length > 24) {
    width = buffer.readUInt32BE(16);
    height = buffer.readUInt32BE(20);
  }
  // JPEG: scan for SOF0 marker
  if ((ext === 'jpg' || ext === 'jpeg') && buffer.length > 100) {
    for (let i = 0; i < Math.min(buffer.length - 9, 1000); i++) {
      if (buffer[i] === 0xFF && buffer[i + 1] === 0xC0) {
        height = buffer.readUInt16BE(i + 5);
        width = buffer.readUInt16BE(i + 7);
        break;
      }
    }
  }

  if (width === 0 && height === 0) return null;

  return {
    id: `vi-${crypto.randomUUID().slice(0, 12)}`,
    identityType: 'composition-pattern',
    extractedData: {
      width, height,
      aspectRatio: width / height,
      orientation: width > height ? 'landscape' : width < height ? 'portrait' : 'square',
      fileSize: buffer.length,
    },
    confidence: 0.8,
    source: fileName,
  };
}

// ── Asset Tagging ───────────────────────────────────────────────────────────

function generateAssetTags(fileName: string, category: AssetCategory, buffer: Buffer): string[] {
  const tags: string[] = [category];
  const ext = fileName.split('.').pop()?.toLowerCase() || '';
  const nameLower = fileName.toLowerCase();

  if (category === 'logo') {
    tags.push('brand-identity');
    if (nameLower.includes('symbol') || nameLower.includes('icon')) tags.push('symbol');
    if (nameLower.includes('word') || nameLower.includes('text')) tags.push('wordmark');
    if (nameLower.includes('dark')) tags.push('dark-mode');
    if (nameLower.includes('light') || nameLower.includes('white')) tags.push('light-mode');
  }

  if (category === 'font') {
    tags.push('typography');
    if (nameLower.includes('sans')) tags.push('sans-serif');
    if (nameLower.includes('serif')) tags.push('serif');
    if (nameLower.includes('mono')) tags.push('monospace');
    if (nameLower.includes('display')) tags.push('display');
    if (nameLower.includes('variable')) tags.push('variable-font');
  }

  if (category === 'icon') tags.push('ui-element');
  if (ext === 'svg') tags.push('vector');
  if (ext === 'png' || ext === 'webp') tags.push('raster');

  // Size-based tags
  if (buffer.length < 10240) tags.push('small-asset');
  if (buffer.length > 500000) tags.push('large-asset');

  return [...new Set(tags)];
}

// ── Storage URL Generation ──────────────────────────────────────────────────

function generateStorageUrl(assetId: string): string {
  return `/api/assets/${assetId}/file`;
}

// ── Main Registry Functions ─────────────────────────────────────────────────

export async function registerAsset(input: AssetInput): Promise<AssetManifest> {
  const assetId = `ast-${crypto.randomUUID().slice(0, 12)}`;
  const ext = input.originalName.split('.').pop()?.toLowerCase() || 'bin';
  const category = input.category || detectCategory(input.originalName);
  const storageUrl = generateStorageUrl(assetId);
  const tags = input.tags || generateAssetTags(input.originalName, category, input.buffer);
  const mimeType = input.mimeType || detectMimeType(ext);

  // Upload to Google Cloud Storage (scalable, no base64 blobs)
  const gcsPath = generateGcsPath(assetId, category, ext, input.brandId);
  await uploadToGCS(gcsPath, input.buffer, mimeType);

  // Extract visual identity
  const extractions: ExtractedVisualIdentity[] = [];
  const colorPalette = extractColorPalette(input.buffer, input.originalName);
  if (colorPalette) extractions.push(colorPalette);

  const logoAnalysis = extractLogoAnalysis(input.buffer, input.originalName);
  if (logoAnalysis) extractions.push(logoAnalysis);

  const fontMeta = extractFontMetadata(input.buffer, input.originalName);
  if (fontMeta) extractions.push(fontMeta);

  const imageMeta = extractImageMetadata(input.buffer, input.originalName);
  if (imageMeta) extractions.push(imageMeta);

  // Generate embedding for the asset (using metadata + tags)
  let embedding: number[] | null = null;
  if (isEmbeddingAvailable()) {
    const embedText2 = await import('./embedding').then((m) => m.embedText);
    const embedInput = [
      `Category: ${category}`,
      `Tags: ${tags.join(', ')}`,
      `Name: ${input.originalName}`,
      ...extractions.map((e) => `${e.identityType}: ${JSON.stringify(e.extractedData).slice(0, 200)}`),
    ].join('\n');
    embedding = await embedText2(embedInput).catch(() => null);
  }

  // Build brand association
  const brandAssociation: Record<string, unknown> = {};
  if (input.brandId) {
    brandAssociation.brandId = input.brandId;
    brandAssociation.associationStrength = 1.0;
  }

  // Register in database
  const asset = await saveAsset({
    id: assetId,
    brandId: input.brandId,
    projectId: input.projectId,
    name: input.name,
    originalName: input.originalName,
    category,
    mimeType,
    fileSize: input.fileSize,
    storageUrl,
    thumbnailUrl: null,
    metadata: { ...(input.metadata || {}), gcsPath },
    extractedTokens: extractions.length > 0 ? { extractionCount: extractions.length } : null,
    tags,
    brandAssociation,
    embedding,
  });

  // Create brand mappings if brandId provided
  const brandMappings: { role: BrandRole; confidence: number }[] = [];
  if (input.brandId) {
    const role = inferBrandRole(input.originalName, category);
    await saveAssetBrandMapping({
      id: `abm-${crypto.randomUUID().slice(0, 12)}`,
      assetId,
      brandId: input.brandId,
      role,
      confidence: 0.9,
    });
    brandMappings.push({ role, confidence: 0.9 });
  }

  // Save extractions to visual identity table
  for (const extr of extractions) {
    await saveExtractedIdentity({
      ...extr,
      brandId: input.brandId,
      projectId: input.projectId,
      assetId,
    });
  }

  return {
    asset,
    identityExtractions: extractions,
    brandMappings,
  };
}

function inferBrandRole(fileName: string, category: AssetCategory): BrandRole {
  const n = fileName.toLowerCase();
  if (category === 'logo') {
    if (n.includes('symbol') || n.includes('icon') || n.includes('mark')) return 'symbol';
    if (n.includes('word') || n.includes('text') || n.includes('type')) return 'wordmark';
    return 'primary-logo';
  }
  if (category === 'font') return 'brand-font';
  if (category === 'screenshot') return 'screenshot';
  if (category === 'image') return 'mood-reference';
  return 'other';
}

// ── Retrieval-Aware Asset Loading ───────────────────────────────────────────

export async function loadAssetContext(
  brandId?: string,
  projectId?: string,
): Promise<{
  logos: RegisteredAsset[];
  fonts: RegisteredAsset[];
  screenshots: RegisteredAsset[];
  identity: ExtractedVisualIdentity[];
}> {
  const [logoAssets, fontAssets, screenshotAssets, allAssets] = await Promise.all([
    brandId ? loadAssetsByBrand(brandId, 'logo') : [],
    brandId ? loadAssetsByBrand(brandId, 'font') : [],
    brandId ? loadAssetsByBrand(brandId, 'screenshot') : [],
    brandId ? [] : [],
  ]);

  let identity: ExtractedVisualIdentity[] = [];
  if (brandId) {
    identity = await loadIdentityByBrand(brandId);
  }

  return {
    logos: logoAssets,
    fonts: fontAssets,
    screenshots: screenshotAssets,
    identity,
  };
}

export async function loadFullBrandIdentity(brandId: string): Promise<{
  assets: AssetManifest[];
  colorPalettes: ExtractedVisualIdentity[];
  typography: ExtractedVisualIdentity[];
  spacing: ExtractedVisualIdentity[];
  logoAnalysis: ExtractedVisualIdentity[];
  compositionPatterns: ExtractedVisualIdentity[];
}> {
  const assets = await loadAssetsByBrand(brandId);
  const identity = await loadIdentityByBrand(brandId);

  return {
    assets: assets.map((a) => ({
      asset: a,
      identityExtractions: identity.filter((i) => i.assetId === a.id),
      brandMappings: [],
    })),
    colorPalettes: identity.filter((i) => i.identityType === 'color-palette'),
    typography: identity.filter((i) => i.identityType === 'typography-system'),
    spacing: identity.filter((i) => i.identityType === 'spacing-scale'),
    logoAnalysis: identity.filter((i) => i.identityType === 'logo-analysis'),
    compositionPatterns: identity.filter((i) => i.identityType === 'composition-pattern'),
  };
}

export type { AssetRow };
