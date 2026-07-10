"""Seguridad de `edecan_api`: hash de contraseñas, JWT y TOTP.

- Contraseñas: `argon2-cffi` (Argon2id, parámetros por defecto de la librería).
- JWT: HS256 (`PyJWT`) firmado con `Settings.JWT_SECRET`. Claims EXACTOS
  pinned en `ARCHITECTURE.md` §10.12: `{sub, ten, plan, typ, exp}` —
  `sub`=user_id, `ten`=tenant_id, `plan`=plan_key, `typ`="access"|"refresh".
  Access token: 30 min. Refresh token: 30 días. Los flags del plan NUNCA se
  guardan en el token — `deps.py` los recalcula siempre desde
  `edecan_schemas.plans.PLANES[plan]`.
- TOTP: `pyotp` (RFC 6238, ventana de validación ±1 paso de 30s).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Literal

import jwt
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerificationError, VerifyMismatchError

ACCESS_TOKEN_TTL_SECONDS = 30 * 60
REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60

JWT_ALGORITHM = "HS256"

TokenType = Literal["access", "refresh"]

_password_hasher = PasswordHasher()


# ---------------------------------------------------------------------------
# Contraseñas
# ---------------------------------------------------------------------------


def hash_password(raw_password: str) -> str:
    """Hashea `raw_password` con Argon2id. Nunca guardes la contraseña en claro."""
    return _password_hasher.hash(raw_password)


def verify_password(password_hash: str, raw_password: str) -> bool:
    """Verifica `raw_password` contra `password_hash`. No lanza en caso de mismatch."""
    try:
        return _password_hasher.verify(password_hash, raw_password)
    except (VerifyMismatchError, VerificationError, InvalidHash):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True si `password_hash` fue creado con parámetros más débiles que los actuales."""
    try:
        return _password_hasher.check_needs_rehash(password_hash)
    except InvalidHash:
        return False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


class TokenError(Exception):
    """Token ausente, mal formado, con firma inválida o expirado."""


@dataclass(frozen=True)
class DecodedToken:
    sub: uuid.UUID
    ten: uuid.UUID
    plan: str
    typ: TokenType
    exp: int


def _encode(
    *, sub: uuid.UUID, ten: uuid.UUID, plan: str, typ: TokenType, ttl_seconds: int, secret: str
) -> str:
    now = int(time.time())
    payload = {
        "sub": str(sub),
        "ten": str(ten),
        "plan": plan,
        "typ": typ,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def create_access_token(
    *, user_id: uuid.UUID, tenant_id: uuid.UUID, plan_key: str, secret: str
) -> str:
    return _encode(
        sub=user_id,
        ten=tenant_id,
        plan=plan_key,
        typ="access",
        ttl_seconds=ACCESS_TOKEN_TTL_SECONDS,
        secret=secret,
    )


def create_refresh_token(
    *, user_id: uuid.UUID, tenant_id: uuid.UUID, plan_key: str, secret: str
) -> str:
    return _encode(
        sub=user_id,
        ten=tenant_id,
        plan=plan_key,
        typ="refresh",
        ttl_seconds=REFRESH_TOKEN_TTL_SECONDS,
        secret=secret,
    )


def create_token_pair(
    *, user_id: uuid.UUID, tenant_id: uuid.UUID, plan_key: str, secret: str
) -> tuple[str, str]:
    """Devuelve `(access_token, refresh_token)`."""
    access = create_access_token(
        user_id=user_id, tenant_id=tenant_id, plan_key=plan_key, secret=secret
    )
    refresh = create_refresh_token(
        user_id=user_id, tenant_id=tenant_id, plan_key=plan_key, secret=secret
    )
    return access, refresh


def decode_token(token: str, *, secret: str, expected_typ: TokenType | None = None) -> DecodedToken:
    """Decodifica y valida `token`. Lanza `TokenError` si es inválido/expirado/tipo incorrecto."""
    try:
        payload = jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("El token expiró") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError(f"Token inválido: {exc}") from exc

    try:
        sub = uuid.UUID(str(payload["sub"]))
        ten = uuid.UUID(str(payload["ten"]))
        plan = str(payload["plan"])
        typ = str(payload["typ"])
        exp = int(payload["exp"])
    except (KeyError, ValueError, TypeError) as exc:
        raise TokenError(f"Claims del token incompletos o mal formados: {exc}") from exc

    if typ not in ("access", "refresh"):
        raise TokenError(f"Tipo de token desconocido: {typ!r}")
    if expected_typ is not None and typ != expected_typ:
        raise TokenError(f"Se esperaba un token '{expected_typ}', se recibió '{typ}'")

    return DecodedToken(sub=sub, ten=ten, plan=plan, typ=typ, exp=exp)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# TOTP (2FA)
# ---------------------------------------------------------------------------


def generate_totp_secret() -> str:
    """Genera un secreto TOTP base32 nuevo (para `users.totp_secret`)."""
    return pyotp.random_base32()


def totp_provisioning_uri(secret: str, *, account_email: str, issuer: str = "Edecán") -> str:
    """URI `otpauth://` para generar el QR que el usuario escanea en su app 2FA."""
    return pyotp.totp.TOTP(secret).provisioning_uri(name=account_email, issuer_name=issuer)


def verify_totp_code(secret: str, code: str) -> bool:
    """Verifica un código TOTP de 6 dígitos con una ventana de tolerancia de ±1 paso (30s)."""
    if not secret or not code:
        return False
    try:
        return pyotp.TOTP(secret).verify(code, valid_window=1)
    except Exception:
        return False
