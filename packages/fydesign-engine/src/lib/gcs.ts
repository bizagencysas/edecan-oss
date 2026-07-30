// GCS Client — singleton for Google Cloud Storage operations
import { Storage } from '@google-cloud/storage';

let _storage: Storage | null = null;
let _bucket: ReturnType<Storage['bucket']> | null = null;

function getCredentials() {
  if (process.env.GOOGLE_CREDENTIALS_JSON) {
    try {
      return JSON.parse(process.env.GOOGLE_CREDENTIALS_JSON);
    } catch { /* fall through to env-based auth */ }
  }
  return undefined; // uses ADC (Application Default Credentials)
}

function getStorage(): Storage {
  if (!_storage) {
    const creds = getCredentials();
    _storage = creds
      ? new Storage({ credentials: creds, projectId: creds.project_id })
      : new Storage();
  }
  return _storage;
}

function bucketName(): string {
  const configured = (process.env.GCS_ASSETS_BUCKET || '').trim();
  if (!configured) {
    throw new Error(
      'GCS_ASSETS_BUCKET no está configurado. Conecta almacenamiento público en el vault de Edecán para esta operación.',
    );
  }
  return configured;
}

export function getBucket() {
  if (!_bucket) {
    _bucket = getStorage().bucket(bucketName());
  }
  return _bucket;
}

export async function uploadToGCS(objectPath: string, buffer: Buffer, contentType: string): Promise<string> {
  const bucket = getBucket();
  const file = bucket.file(objectPath);
  await file.save(buffer, {
    contentType,
    resumable: false,
  });
  return objectPath;
}

export async function readFromGCS(objectPath: string): Promise<{ buffer: Buffer; contentType: string } | null> {
  const bucket = getBucket();
  const file = bucket.file(objectPath);
  try {
    const [data] = await file.download();
    const [metadata] = await file.getMetadata();
    return {
      buffer: data as Buffer,
      contentType: (metadata as Record<string,unknown>).contentType as string || 'application/octet-stream',
    };
  } catch (e) {
    if ((e as { code?: number }).code === 404) return null;
    throw e;
  }
}

export async function deleteFromGCS(objectPath: string): Promise<boolean> {
  const bucket = getBucket();
  const file = bucket.file(objectPath);
  try {
    await file.delete();
    return true;
  } catch (e) {
    if ((e as { code?: number }).code === 404) return false;
    throw e;
  }
}

// Generate GCS object path for an asset
export function generateGcsPath(assetId: string, category: string, ext: string, brandId?: string): string {
  const prefix = brandId ? `brands/${brandId}` : 'shared';
  return `assets/${prefix}/${category}/${assetId}.${ext}`;
}
