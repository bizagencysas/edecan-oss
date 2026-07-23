"""Persistencia inmutable y tenant-scoped sobre el object store de Edecán."""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

import aioboto3
from botocore.exceptions import ClientError

from .models import DesignVersion

DEFAULT_S3_BUCKET = "edecan-files"
DEFAULT_AWS_REGION = "us-east-1"
MAX_HISTORY_VERSIONS = 200


class DesignNotFoundError(LookupError):
    pass


@runtime_checkable
class DesignStore(Protocol):
    async def save(self, tenant_id: UUID, version: DesignVersion) -> None: ...

    async def get(
        self, tenant_id: UUID, artifact_id: UUID, version_id: UUID | None = None
    ) -> DesignVersion: ...

    async def history(self, tenant_id: UUID, artifact_id: UUID) -> list[DesignVersion]: ...


class S3DesignStore:
    """Versiones JSON inmutables bajo el prefijo privado del tenant.

    Funciona contra AWS S3 y contra el object store loopback de `edecan-local`,
    que implementa Put/Get/ListObjectsV2. No necesita tablas ni credenciales
    nuevas: reutiliza exactamente `S3_BUCKET`, `AWS_REGION` y
    `AWS_ENDPOINT_URL` del runtime.
    """

    def __init__(self, settings: Any) -> None:
        self.bucket = getattr(settings, "S3_BUCKET", None) or DEFAULT_S3_BUCKET
        self.region = getattr(settings, "AWS_REGION", None) or DEFAULT_AWS_REGION
        self.endpoint_url = getattr(settings, "AWS_ENDPOINT_URL", None)
        self._session = aioboto3.Session()

    @staticmethod
    def _prefix(tenant_id: UUID, artifact_id: UUID) -> str:
        return f"tenants/{tenant_id}/design-studio/{artifact_id}/versions/"

    @classmethod
    def _key(cls, tenant_id: UUID, version: DesignVersion) -> str:
        return f"{cls._prefix(tenant_id, version.artifact_id)}{version.version_id}.json"

    def _client(self):
        return self._session.client(
            "s3",
            region_name=self.region,
            endpoint_url=self.endpoint_url,
        )

    async def save(self, tenant_id: UUID, version: DesignVersion) -> None:
        payload = json.dumps(version.to_dict(), ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        async with self._client() as client:
            await client.put_object(
                Bucket=self.bucket,
                Key=self._key(tenant_id, version),
                Body=payload,
                ContentType="application/vnd.edecan.design-version+json",
            )

    async def _keys(self, tenant_id: UUID, artifact_id: UUID) -> list[str]:
        prefix = self._prefix(tenant_id, artifact_id)
        keys: list[str] = []
        continuation: str | None = None
        async with self._client() as client:
            while len(keys) < MAX_HISTORY_VERSIONS:
                request: dict[str, Any] = {
                    "Bucket": self.bucket,
                    "Prefix": prefix,
                    "MaxKeys": min(1000, MAX_HISTORY_VERSIONS - len(keys)),
                }
                if continuation:
                    request["ContinuationToken"] = continuation
                response = await client.list_objects_v2(**request)
                keys.extend(
                    str(item["Key"])
                    for item in response.get("Contents", [])
                    if str(item.get("Key", "")).endswith(".json")
                )
                if not response.get("IsTruncated"):
                    break
                continuation = response.get("NextContinuationToken")
                if not continuation:
                    break
        return keys[:MAX_HISTORY_VERSIONS]

    async def _read_key(self, key: str) -> DesignVersion:
        async with self._client() as client:
            response = await client.get_object(Bucket=self.bucket, Key=key)
            body = await response["Body"].read()
        data = json.loads(body.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Versión de diseño corrupta en el object store.")
        return DesignVersion.from_dict(data)

    async def history(self, tenant_id: UUID, artifact_id: UUID) -> list[DesignVersion]:
        versions = [await self._read_key(key) for key in await self._keys(tenant_id, artifact_id)]
        versions = [item for item in versions if item.artifact_id == artifact_id]
        versions.sort(key=lambda item: (item.created_at, str(item.version_id)))
        return versions

    async def get(
        self, tenant_id: UUID, artifact_id: UUID, version_id: UUID | None = None
    ) -> DesignVersion:
        if version_id is not None:
            key = f"{self._prefix(tenant_id, artifact_id)}{version_id}.json"
            try:
                version = await self._read_key(key)
            except ClientError as exc:
                code = str(exc.response.get("Error", {}).get("Code") or "")
                if code in {"404", "NoSuchKey", "NotFound"}:
                    raise DesignNotFoundError("No encontré esa versión del diseño.") from exc
                raise
            if version.artifact_id != artifact_id:
                raise DesignNotFoundError("No encontré esa versión del diseño.")
            return version

        versions = await self.history(tenant_id, artifact_id)
        if not versions:
            raise DesignNotFoundError("No encontré ese diseño.")
        return versions[-1]
