// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Asset Storage — DB operations for asset registry tables                     ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { getDb } from '@/lib/db';
import type { ExtractedVisualIdentity, RegisteredAsset, BrandRole } from './asset-registry';

export interface AssetRow {
  id: string;
  brand_id: string | null;
  project_id: string | null;
  name: string;
  original_name: string;
  category: string;
  mime_type: string;
  file_size: number;
  storage_url: string;
  thumbnail_url: string | null;
  metadata: Record<string, unknown>;
  extracted_tokens: Record<string, unknown> | null;
  embedding: number[] | null;
  tags: string[];
  brand_association: Record<string, unknown>;
  status: string;
  created_at: string;
  updated_at: string;
}

function mapAssetRow(row: AssetRow): RegisteredAsset {
  return {
    id: row.id,
    name: row.name,
    originalName: row.original_name,
    category: row.category as RegisteredAsset['category'],
    mimeType: row.mime_type,
    fileSize: row.file_size,
    storageUrl: row.storage_url,
    thumbnailUrl: row.thumbnail_url,
    metadata: row.metadata,
    extractedTokens: row.extracted_tokens,
    tags: row.tags,
    brandAssociation: row.brand_association,
    embedding: row.embedding,
  };
}

export async function saveAsset(asset: {
  id: string;
  brandId?: string;
  projectId?: string;
  name: string;
  originalName: string;
  category: string;
  mimeType: string;
  fileSize: number;
  storageUrl: string;
  thumbnailUrl: string | null;
  metadata: Record<string, unknown>;
  extractedTokens: Record<string, unknown> | null;
  tags: string[];
  brandAssociation: Record<string, unknown>;
  embedding: number[] | null;
}): Promise<RegisteredAsset> {
  const sql = getDb();
  const rows = await sql`
    INSERT INTO asset_registry (id, brand_id, project_id, name, original_name, category, mime_type, file_size, storage_url, thumbnail_url, metadata, extracted_tokens, tags, brand_association, embedding)
    VALUES (${asset.id}, ${asset.brandId || null}, ${asset.projectId || null}, ${asset.name}, ${asset.originalName}, ${asset.category}, ${asset.mimeType}, ${asset.fileSize}, ${asset.storageUrl}, ${asset.thumbnailUrl}, ${JSON.stringify(asset.metadata)}::jsonb, ${asset.extractedTokens ? JSON.stringify(asset.extractedTokens) : null}::jsonb, ${JSON.stringify(asset.tags)}::jsonb, ${JSON.stringify(asset.brandAssociation)}::jsonb, ${asset.embedding ? JSON.stringify(asset.embedding) : null}::vector)
    ON CONFLICT (id) DO UPDATE SET
      name = EXCLUDED.name,
      metadata = EXCLUDED.metadata,
      extracted_tokens = EXCLUDED.extracted_tokens,
      tags = EXCLUDED.tags,
      embedding = EXCLUDED.embedding,
      updated_at = NOW()
    RETURNING *
  `;
  return mapAssetRow(rows[0] as AssetRow);
}

export async function saveAssetBrandMapping(mapping: {
  id: string;
  assetId: string;
  brandId: string;
  role: BrandRole;
  confidence: number;
  notes?: string;
}): Promise<void> {
  const sql = getDb();
  await sql`
    INSERT INTO asset_brand_mappings (id, asset_id, brand_id, role, confidence, notes)
    VALUES (${mapping.id}, ${mapping.assetId}, ${mapping.brandId}, ${mapping.role}, ${mapping.confidence}, ${mapping.notes || null})
    ON CONFLICT (id) DO NOTHING
  `;
}

export async function saveExtractedIdentity(identity: ExtractedVisualIdentity): Promise<void> {
  const sql = getDb();
  await sql`
    INSERT INTO extracted_visual_identity (id, brand_id, project_id, asset_id, identity_type, extracted_data, confidence, source)
    VALUES (${identity.id}, ${identity.brandId || null}, ${identity.projectId || null}, ${identity.assetId || null}, ${identity.identityType}, ${JSON.stringify(identity.extractedData)}::jsonb, ${identity.confidence}, ${identity.source})
    ON CONFLICT (id) DO NOTHING
  `;
}

export async function loadAssetsByBrand(brandId: string, category?: string): Promise<RegisteredAsset[]> {
  const sql = getDb();
  const rows = category
    ? await sql`SELECT * FROM asset_registry WHERE brand_id = ${brandId} AND category = ${category} AND status = 'active' ORDER BY created_at DESC LIMIT 100`
    : await sql`SELECT * FROM asset_registry WHERE brand_id = ${brandId} AND status = 'active' ORDER BY created_at DESC LIMIT 100`;
  return (rows as AssetRow[]).map(mapAssetRow);
}

export async function loadAssetsByProject(projectId: string): Promise<RegisteredAsset[]> {
  const sql = getDb();
  const rows = await sql`SELECT * FROM asset_registry WHERE project_id = ${projectId} AND status = 'active' ORDER BY created_at DESC LIMIT 100`;
  return (rows as AssetRow[]).map(mapAssetRow);
}

export async function loadIdentityByBrand(brandId: string): Promise<ExtractedVisualIdentity[]> {
  const sql = getDb();
  const rows = await sql`SELECT * FROM extracted_visual_identity WHERE brand_id = ${brandId} ORDER BY confidence DESC LIMIT 50` as Record<string, unknown>[];
  return rows.map((r) => ({
    id: r.id as string,
    brandId: r.brand_id as string,
    projectId: r.project_id as string,
    assetId: r.asset_id as string,
    identityType: r.identity_type as ExtractedVisualIdentity['identityType'],
    extractedData: r.extracted_data as Record<string, unknown>,
    confidence: r.confidence as number,
    source: r.source as string,
  }));
}

export async function loadIdentityByType(identityType: string, brandId?: string): Promise<ExtractedVisualIdentity[]> {
  const sql = getDb();
  const rows = brandId
    ? await sql`SELECT * FROM extracted_visual_identity WHERE identity_type = ${identityType} AND brand_id = ${brandId} ORDER BY confidence DESC LIMIT 20` as Record<string, unknown>[]
    : await sql`SELECT * FROM extracted_visual_identity WHERE identity_type = ${identityType} ORDER BY confidence DESC LIMIT 20` as Record<string, unknown>[];
  return rows.map((r) => ({
    id: r.id as string,
    brandId: r.brand_id as string,
    projectId: r.project_id as string,
    assetId: r.asset_id as string,
    identityType: r.identity_type as ExtractedVisualIdentity['identityType'],
    extractedData: r.extracted_data as Record<string, unknown>,
    confidence: r.confidence as number,
    source: r.source as string,
  }));
}

export async function deleteAsset(assetId: string): Promise<boolean> {
  const sql = getDb();
  const rows = await sql`SELECT id, metadata FROM asset_registry WHERE id = ${assetId} LIMIT 1`;
  if (rows.length === 0) return false;
  await sql`DELETE FROM asset_registry WHERE id = ${assetId}`;
  return true;
}

export async function loadAssetById(assetId: string): Promise<RegisteredAsset | null> {
  const sql = getDb();
  const rows = await sql`SELECT * FROM asset_registry WHERE id = ${assetId} LIMIT 1`;
  if (rows.length === 0) return null;
  return mapAssetRow(rows[0] as AssetRow);
}
