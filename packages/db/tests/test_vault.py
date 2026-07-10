"""Roundtrip del `TokenVault` con `LocalKeyProvider`, sin red (§10.4).

Ejercita toda la lógica criptográfica de `edecan_db.vault` -- envoltura de la
data key (`LocalKeyProvider.wrap`/`.unwrap`), serialización del bundle, y el
ciclo completo AES-256-GCM que usan `TokenVault.put`/`.get` -- sin abrir una
sola conexión de base de datos ni de red. La parte de `TokenVault` que sí
pega contra Postgres (el upsert/lookup en `oauth_tokens`/`tenant_keys`) se
cubre en `test_rls.py`, marcada `@pytest.mark.integration`.

Nota (`ARCHITECTURE.md` §10.1): este archivo NO importa `edecan_schemas`
(paquete hermano). `FakeTokenBundle` replica localmente la forma de
`edecan_schemas.TokenBundle` -- funciona porque `edecan_db.vault` lee esos
atributos "a mano" (duck typing), no vía métodos de Pydantic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from edecan_db.vault import (
    LocalKeyProvider,
    VaultError,
    _deserialize_bundle,
    _serialize_bundle,
)


@dataclass
class FakeTokenBundle:
    """Réplica local (solo para tests) de la forma de `edecan_schemas.TokenBundle`."""

    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None
    scopes: list[str] = field(default_factory=list)
    token_type: str = "bearer"


def _fresh_master_key() -> str:
    return Fernet.generate_key().decode("ascii")


# ---------------------------------------------------------------------------
# LocalKeyProvider
# ---------------------------------------------------------------------------


async def test_local_key_provider_wrap_unwrap_roundtrip():
    provider = LocalKeyProvider(_fresh_master_key())
    data_key = AESGCM.generate_key(bit_length=256)

    wrapped = await provider.wrap(data_key)
    assert wrapped != data_key  # de verdad quedó cifrada, no es un passthrough

    unwrapped = await provider.unwrap(wrapped)
    assert unwrapped == data_key


async def test_local_key_provider_unwrap_con_master_key_distinta_falla():
    wrapped = await LocalKeyProvider(_fresh_master_key()).wrap(b"0" * 32)

    otro_provider = LocalKeyProvider(_fresh_master_key())
    with pytest.raises(VaultError):
        await otro_provider.unwrap(wrapped)


def test_local_key_provider_rechaza_master_key_vacia():
    with pytest.raises(VaultError):
        LocalKeyProvider("")


def test_local_key_provider_rechaza_master_key_invalida():
    with pytest.raises(VaultError):
        LocalKeyProvider("esto-no-es-una-clave-fernet-valida")


def test_local_key_provider_kms_key_id_es_none():
    # `KeyProvider.kms_key_id` es el metadato persistido en `tenant_keys` -
    # para Local no aplica.
    assert LocalKeyProvider(_fresh_master_key()).kms_key_id is None


# ---------------------------------------------------------------------------
# Serialización del TokenBundle
# ---------------------------------------------------------------------------


def test_serialize_deserialize_bundle_roundtrip_completo():
    expires_at = datetime(2030, 1, 1, 12, 30, 0, tzinfo=UTC)
    bundle = FakeTokenBundle(
        access_token="access-123",
        refresh_token="refresh-456",
        expires_at=expires_at,
        scopes=["scope-a", "scope-b"],
        token_type="bearer",
    )

    raw = _serialize_bundle(bundle)
    assert isinstance(raw, bytes)

    result = _deserialize_bundle(raw)
    assert result.access_token == "access-123"
    assert result.refresh_token == "refresh-456"
    assert result.expires_at == expires_at
    assert result.scopes == ["scope-a", "scope-b"]
    assert result.token_type == "bearer"


def test_serialize_deserialize_bundle_con_opcionales_none():
    bundle = FakeTokenBundle(access_token="solo-access-token")

    result = _deserialize_bundle(_serialize_bundle(bundle))
    assert result.access_token == "solo-access-token"
    assert result.refresh_token is None
    assert result.expires_at is None
    assert result.scopes == []
    assert result.token_type == "bearer"


# ---------------------------------------------------------------------------
# Ciclo completo (envelope encryption) sin base de datos: lo mismo que hacen
# `TokenVault.put`/`.get` por dentro, una vez que ya tienen la data key en
# claro -- la única parte que dejan fuera es el INSERT/SELECT a Postgres.
# ---------------------------------------------------------------------------


async def test_ciclo_completo_de_cifrado_del_bundle_offline():
    provider = LocalKeyProvider(_fresh_master_key())

    # 1. Nace la data key del tenant (normalmente vive envuelta en `tenant_keys`).
    data_key = AESGCM.generate_key(bit_length=256)
    wrapped_data_key = await provider.wrap(data_key)

    # 2. `put()`: desenvuelve la data key y cifra el bundle con AES-256-GCM.
    bundle = FakeTokenBundle(
        access_token="secreto-de-verdad",
        refresh_token="refresh-de-verdad",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        scopes=["mail.read", "calendar.write"],
    )
    unwrapped_data_key = await provider.unwrap(wrapped_data_key)
    aesgcm = AESGCM(unwrapped_data_key)
    nonce = b"0" * 12
    ciphertext = aesgcm.encrypt(nonce, _serialize_bundle(bundle), None)

    assert bundle.access_token.encode() not in ciphertext  # de verdad está cifrado

    # 3. `get()`: vuelve a desenvolver la MISMA data key y descifra.
    data_key_para_leer = await provider.unwrap(wrapped_data_key)
    plaintext = AESGCM(data_key_para_leer).decrypt(nonce, ciphertext, None)
    result = _deserialize_bundle(plaintext)

    assert result.access_token == "secreto-de-verdad"
    assert result.refresh_token == "refresh-de-verdad"
    assert result.scopes == ["mail.read", "calendar.write"]
