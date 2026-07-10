"""`TokenVault` — cifrado envolvente de credenciales por tenant (ARCHITECTURE.md §10.4).

Cada tenant tiene una **data key** AES-256 propia (tabla `tenant_keys`),
creada perezosamente en el primer `put()`. Esa data key se guarda envuelta
("wrapped") con un `KeyProvider` intercambiable:

- `LocalKeyProvider` — Fernet sobre `LOCAL_MASTER_KEY` (dev/self-host).
- `KmsKeyProvider` — AWS KMS (`KMS_KEY_ID`), para producción.

El *bundle* en sí (`edecan_schemas.TokenBundle`) se cifra con AES-256-GCM
usando la data key ya desenvuelta, vía `cryptography.hazmat`.

Nota sobre rotación de claves: `tenant_keys` guarda una sola fila por tenant
(`tenant_id UNIQUE`), así que hoy solo existe una data key "activa" por
tenant — `version` queda registrado en cada fila de `oauth_tokens` como
metadato para una futura migración de rotación (re-cifrar todo bajo una data
key nueva), pero esa rotación en sí está fuera del alcance de este paquete.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from asyncio import to_thread
from datetime import datetime
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from edecan_schemas import TokenBundle
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_db.models import OAuthToken, TenantKey
from edecan_db.settings import DbSettings

_NONCE_BYTES = 12  # tamaño estándar de nonce para AES-GCM (96 bits)


class VaultError(RuntimeError):
    """Configuración faltante/ inválida, o fallo al cifrar/descifrar."""


# ---------------------------------------------------------------------------
# KeyProvider
# ---------------------------------------------------------------------------


class KeyProvider(ABC):
    """Envuelve/desenvuelve la data key AES-256 de un tenant (§10.4)."""

    #: `kms_key_id` usado para envolver, si aplica (persistido en `tenant_keys`
    #: como metadato). `None` para `LocalKeyProvider`.
    kms_key_id: str | None = None

    @abstractmethod
    async def wrap(self, data_key: bytes) -> bytes:
        """Envuelve (cifra) `data_key` con la clave maestra del proveedor."""
        raise NotImplementedError

    @abstractmethod
    async def unwrap(self, wrapped: bytes) -> bytes:
        """Desenvuelve (descifra) una data key previamente envuelta con `wrap`."""
        raise NotImplementedError


class LocalKeyProvider(KeyProvider):
    """Envuelve la data key con Fernet sobre `LOCAL_MASTER_KEY` (dev/self-host).

    `LOCAL_MASTER_KEY` debe ser una clave Fernet válida (32 bytes aleatorios
    codificados en base64 url-safe). Generar una nueva:
    `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
    """

    def __init__(self, local_master_key: str) -> None:
        if not local_master_key:
            raise VaultError(
                "LOCAL_MASTER_KEY vacío o no configurado (ver .env.example y "
                "ARCHITECTURE.md §10.2/§10.4)."
            )
        try:
            self._fernet = Fernet(local_master_key.encode("utf-8"))
        except (ValueError, TypeError) as exc:
            raise VaultError(
                "LOCAL_MASTER_KEY inválido: debe ser una clave Fernet "
                "(32 bytes aleatorios en base64 url-safe)."
            ) from exc

    async def wrap(self, data_key: bytes) -> bytes:
        return self._fernet.encrypt(data_key)

    async def unwrap(self, wrapped: bytes) -> bytes:
        try:
            return self._fernet.decrypt(wrapped)
        except InvalidToken as exc:
            raise VaultError(
                "No se pudo desenvolver la data key: LOCAL_MASTER_KEY no coincide "
                "con la clave usada para envolverla, o el dato está corrupto."
            ) from exc


class KmsKeyProvider(KeyProvider):
    """Envuelve la data key con AWS KMS (`KMS_KEY_ID`) — producción (§10.4).

    Las llamadas a `boto3` (síncronas) se corren en un hilo aparte
    (`asyncio.to_thread`) para no bloquear el event loop.
    """

    def __init__(
        self,
        kms_key_id: str,
        *,
        region_name: str = "us-east-1",
        endpoint_url: str | None = None,
        client: Any | None = None,
    ) -> None:
        if not kms_key_id:
            raise VaultError("KMS_KEY_ID vacío o no configurado (ver ARCHITECTURE.md §10.2).")
        self.kms_key_id = kms_key_id
        if client is not None:
            self._client = client
        else:
            import boto3

            self._client = boto3.client("kms", region_name=region_name, endpoint_url=endpoint_url)

    async def wrap(self, data_key: bytes) -> bytes:
        response = await to_thread(self._client.encrypt, KeyId=self.kms_key_id, Plaintext=data_key)
        return response["CiphertextBlob"]

    async def unwrap(self, wrapped: bytes) -> bytes:
        response = await to_thread(
            self._client.decrypt, CiphertextBlob=wrapped, KeyId=self.kms_key_id
        )
        return response["Plaintext"]


def get_key_provider(settings: DbSettings) -> KeyProvider:
    """Elige el `KeyProvider` según settings: `KmsKeyProvider` si hay `KMS_KEY_ID`
    configurado, si no `LocalKeyProvider` con `LOCAL_MASTER_KEY` (§10.2, §10.4)."""
    if settings.kms_key_id:
        return KmsKeyProvider(
            settings.kms_key_id,
            region_name=settings.aws_region,
            endpoint_url=settings.aws_endpoint_url,
        )
    if not settings.local_master_key:
        raise VaultError(
            "Configura LOCAL_MASTER_KEY (dev/self-host) o KMS_KEY_ID (producción) "
            "— ver .env.example y ARCHITECTURE.md §10.2."
        )
    return LocalKeyProvider(settings.local_master_key)


# ---------------------------------------------------------------------------
# Serialización del TokenBundle
# ---------------------------------------------------------------------------


def _serialize_bundle(bundle: TokenBundle) -> bytes:
    """Serializa los campos de `bundle` a JSON (bytes), listos para cifrar.

    Lee los atributos de `bundle` "a mano" (en vez de `bundle.model_dump_json()`)
    para que cualquier objeto con la misma forma (duck typing: `access_token`,
    `refresh_token`, `expires_at`, `scopes`, `token_type`) sirva como argumento
    de `TokenVault.put` — así los tests de este paquete pueden ejercitar el
    vault con un fake local sin importar `edecan_schemas` (paquete hermano,
    ver ARCHITECTURE.md §10.1).
    """
    expires_at = bundle.expires_at
    payload = {
        "access_token": bundle.access_token,
        "refresh_token": bundle.refresh_token,
        "expires_at": expires_at.isoformat() if expires_at is not None else None,
        "scopes": list(bundle.scopes),
        "token_type": bundle.token_type,
    }
    return json.dumps(payload).encode("utf-8")


def _deserialize_bundle(raw: bytes) -> TokenBundle:
    """Reconstruye un `edecan_schemas.TokenBundle` real desde el JSON descifrado."""
    payload = json.loads(raw.decode("utf-8"))
    expires_at = payload.get("expires_at")
    return TokenBundle(
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token"),
        expires_at=datetime.fromisoformat(expires_at) if expires_at else None,
        scopes=list(payload.get("scopes") or []),
        token_type=payload.get("token_type", "bearer"),
    )


# ---------------------------------------------------------------------------
# TokenVault
# ---------------------------------------------------------------------------


class TokenVault:
    """Cifra/descifra `TokenBundle` por `(tenant_id, connector_account_id)` (§10.4)."""

    def __init__(self, session: AsyncSession, key_provider: KeyProvider) -> None:
        self._session = session
        self._key_provider = key_provider

    async def put(self, tenant_id: UUID, connector_account_id: UUID, bundle: TokenBundle) -> None:
        """Cifra `bundle` y hace upsert en `oauth_tokens` para ese conector."""
        data_key, version = await self._get_or_create_data_key(tenant_id)
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = AESGCM(data_key).encrypt(nonce, _serialize_bundle(bundle), None)

        stmt = (
            pg_insert(OAuthToken)
            .values(
                tenant_id=tenant_id,
                connector_account_id=connector_account_id,
                ciphertext=ciphertext,
                nonce=nonce,
                key_version=version,
                expires_at=bundle.expires_at,
            )
            .on_conflict_do_update(
                index_elements=[OAuthToken.connector_account_id],
                set_={
                    "ciphertext": ciphertext,
                    "nonce": nonce,
                    "key_version": version,
                    "expires_at": bundle.expires_at,
                },
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def get(self, tenant_id: UUID, connector_account_id: UUID) -> TokenBundle | None:
        """Descifra y devuelve el `TokenBundle` guardado, o `None` si no existe."""
        row = (
            await self._session.execute(
                select(OAuthToken).where(
                    OAuthToken.tenant_id == tenant_id,
                    OAuthToken.connector_account_id == connector_account_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None

        data_key, _version = await self._get_or_create_data_key(tenant_id)
        try:
            plaintext = AESGCM(data_key).decrypt(bytes(row.nonce), bytes(row.ciphertext), None)
        except InvalidTag as exc:
            raise VaultError(
                "No se pudo descifrar el token: la data key no coincide o el dato está corrupto."
            ) from exc
        return _deserialize_bundle(plaintext)

    async def _get_or_create_data_key(self, tenant_id: UUID) -> tuple[bytes, int]:
        """Devuelve `(data_key_en_claro, version)`, creando la fila en `tenant_keys`
        perezosamente si es la primera vez que este tenant usa el vault.

        La creación usa `INSERT ... ON CONFLICT (tenant_id) DO NOTHING` + un
        `SELECT` posterior para ser segura ante una carrera entre dos
        transacciones concurrentes creando la data key del mismo tenant a la vez.
        """
        row = (
            await self._session.execute(select(TenantKey).where(TenantKey.tenant_id == tenant_id))
        ).scalar_one_or_none()

        if row is None:
            data_key = AESGCM.generate_key(bit_length=256)
            wrapped = await self._key_provider.wrap(data_key)
            stmt = (
                pg_insert(TenantKey)
                .values(
                    tenant_id=tenant_id,
                    encrypted_data_key=wrapped,
                    kms_key_id=self._key_provider.kms_key_id,
                    version=1,
                )
                .on_conflict_do_nothing(index_elements=[TenantKey.tenant_id])
            )
            await self._session.execute(stmt)
            await self._session.flush()
            row = (
                await self._session.execute(
                    select(TenantKey).where(TenantKey.tenant_id == tenant_id)
                )
            ).scalar_one()

        data_key = await self._key_provider.unwrap(bytes(row.encrypted_data_key))
        return data_key, row.version
