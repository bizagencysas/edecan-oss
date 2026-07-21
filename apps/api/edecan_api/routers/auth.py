"""`POST /v1/auth/*` — registro, login, refresh y TOTP (ARCHITECTURE.md §10.12)."""

from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
import time
import uuid

import redis.asyncio as redis_asyncio
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from edecan_api.config import Settings, get_settings
from edecan_api.deps import CurrentUser, get_current_user, get_platform_repo, get_redis
from edecan_api.repo import Repo
from edecan_api.security import (
    TokenError,
    create_token_pair,
    decode_token,
    generate_totp_secret,
    hash_password,
    needs_rehash,
    totp_provisioning_uri,
    verify_password,
    verify_totp_code,
)

router = APIRouter(prefix="/v1/auth", tags=["auth"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DUMMY_PASSWORD_HASH = hash_password("dummy-password-used-only-to-equalize-login-timing")
_LOCAL_TENANT_NAME = "Mi Edecán"
_LOCAL_DESKTOP_HEADER = "X-Edecan-Desktop-Capability"
_LOCAL_OWNER_LOCK = asyncio.Lock()


def _validar_email(value: str) -> str:
    value = value.strip().lower()
    if not _EMAIL_RE.match(value):
        raise ValueError("Correo electrónico con formato inválido.")
    return value


class RegisterIn(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=256)
    tenant_name: str = Field(min_length=1, max_length=200)

    _validar = field_validator("email")(_validar_email)


class LoginIn(BaseModel):
    email: str
    password: str
    totp_code: str | None = None

    _validar = field_validator("email")(_validar_email)


class RefreshIn(BaseModel):
    refresh_token: str
    totp_code: str | None = None


class LogoutIn(BaseModel):
    refresh_token: str


class TotpVerifyIn(BaseModel):
    code: str


class TotpDisableIn(BaseModel):
    password: str


class TokenPairOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TotpEnableOut(BaseModel):
    secret: str
    provisioning_uri: str


def _slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-") or "tenant"
    return f"{base}-{uuid.uuid4().hex[:8]}"


# TTL del secreto TOTP "pendiente" en Redis (mismo criterio que
# `companion.PAIR_CODE_TTL_SECONDS`): tiempo de sobra para escanear el QR
# desde una app de autenticación y escribir el primer código de 6 dígitos.
PENDING_TOTP_TTL_SECONDS = 600


def _pending_totp_key(user_id: uuid.UUID) -> str:
    return f"pending_totp:{user_id}"


def _refresh_token_key(token: str) -> str:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"auth:refresh:{digest}"


async def _enforce_auth_rate_limit(
    request: Request,
    redis_client: redis_asyncio.Redis,
    settings: Settings,
    *,
    identity: str,
) -> None:
    """Límite por ruta + IP + identidad, sin persistir PII en la key."""
    max_requests = settings.AUTH_RATE_LIMIT_REQUESTS
    window_seconds = settings.AUTH_RATE_LIMIT_WINDOW_SECONDS
    if max_requests <= 0 or window_seconds <= 0:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El límite de autenticación está mal configurado.",
        )

    client_host = request.client.host if request.client is not None else "unknown"
    subject = hashlib.sha256(f"{client_host}:{identity}".encode()).hexdigest()[:24]
    window = int(time.time()) // window_seconds
    key = f"ratelimit:auth:{request.url.path}:{subject}:{window}"
    count = await redis_client.incr(key)
    if count == 1:
        await redis_client.expire(key, window_seconds)
    if count > max_requests:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiados intentos de autenticación. Intenta de nuevo más tarde.",
            headers={"Retry-After": str(window_seconds)},
        )


async def _issue_token_pair(
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    plan_key: str,
    settings: Settings,
    redis_client: redis_asyncio.Redis,
    session_id: uuid.UUID | None = None,
) -> TokenPairOut:
    access, refresh = create_token_pair(
        user_id=user_id,
        tenant_id=tenant_id,
        plan_key=plan_key,
        secret=settings.JWT_SECRET,
        session_id=session_id,
    )
    decoded = decode_token(refresh, secret=settings.JWT_SECRET, expected_typ="refresh")
    ttl_seconds = max(1, decoded.exp - int(time.time()))
    await redis_client.set(_refresh_token_key(refresh), str(decoded.sid), ex=ttl_seconds)
    return TokenPairOut(access_token=access, refresh_token=refresh)


def _is_loopback(request: Request) -> bool:
    host = request.client.host if request.client is not None else ""
    return host in {"127.0.0.1", "::1"}


async def _get_or_create_local_owner(repo: Repo) -> dict:
    # El runner local mantiene un solo proceso de API por data_dir. El lock
    # convierte el primer arranque en una operación idempotente incluso si la
    # WebView y "Abrir en el navegador" llegan al mismo tiempo.
    async with _LOCAL_OWNER_LOCK:
        selected_owner = await repo.get_local_owner()
        if selected_owner is not None:
            return selected_owner

        owner = await repo.get_first_active_owner()
        if owner is not None:
            if owner.get("owner_count") != 1:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Esta instalación contiene más de un dueño activo. "
                        "No se abrió ninguna sesión por seguridad."
                    ),
                )
            return await repo.set_local_owner(
                user_id=owner["user_id"],
                tenant_id=owner["tenant_id"],
            )

        # Correo y contraseña son valores internos, únicos y nunca mostrados.
        # Existen solo para mantener el schema compatible con hosted; la app
        # local entra por capacidad de proceso, no por credenciales humanas.
        local_id = uuid.uuid4().hex
        user = await repo.create_user(
            email=f"owner-{local_id}@edecan.local",
            password_hash=hash_password(secrets.token_urlsafe(48)),
        )
        tenant = await repo.create_tenant(
            name=_LOCAL_TENANT_NAME,
            slug=_slugify(f"mi-edecan-{local_id}"),
            plan_key="free_selfhost",
        )
        await repo.create_membership(user_id=user["id"], tenant_id=tenant["id"], role="owner")
        await repo.create_persona_default(tenant_id=tenant["id"], user_id=user["id"])
        selected_owner = await repo.set_local_owner(
            user_id=user["id"],
            tenant_id=tenant["id"],
        )
        await repo.add_audit_log(
            tenant_id=tenant["id"],
            actor_user_id=user["id"],
            action="auth.local_owner_created",
            target=str(user["id"]),
        )
        return selected_owner


@router.post("/local", response_model=TokenPairOut)
async def local_desktop_session(
    request: Request,
    repo: Repo = Depends(get_platform_repo),
    settings: Settings = Depends(get_settings),
    redis_client: redis_asyncio.Redis = Depends(get_redis),
) -> TokenPairOut:
    """Abre la instalación single-owner sin correo ni contraseña.

    Solo existe operativamente en ``EDECAN_LOCAL_MODE`` y solo responde al
    loopback con una capacidad aleatoria por proceso. El middleware del túnel
    la bloquea además antes de llegar a este router.
    """
    if not settings.EDECAN_LOCAL_MODE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ruta no disponible.")
    provided_capability = request.headers.get(_LOCAL_DESKTOP_HEADER, "")
    expected_capability = settings.LOCAL_DESKTOP_CAPABILITY or ""
    if (
        not _is_loopback(request)
        or not provided_capability
        or not expected_capability
        or not secrets.compare_digest(provided_capability, expected_capability)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Esta sesión solo puede abrirse desde la app instalada en este computador.",
        )

    await _enforce_auth_rate_limit(
        request,
        redis_client,
        settings,
        identity="local-desktop",
    )
    owner = await _get_or_create_local_owner(repo)
    return await _issue_token_pair(
        user_id=owner["user_id"],
        tenant_id=owner["tenant_id"],
        plan_key=owner["plan_key"],
        settings=settings,
        redis_client=redis_client,
    )


@router.post("/register", response_model=TokenPairOut, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterIn,
    request: Request,
    repo: Repo = Depends(get_platform_repo),
    settings: Settings = Depends(get_settings),
    redis_client: redis_asyncio.Redis = Depends(get_redis),
) -> TokenPairOut:
    """Crea tenant + usuario (owner) + persona por defecto, y devuelve tokens."""
    await _enforce_auth_rate_limit(request, redis_client, settings, identity=body.email)
    existing = await repo.get_user_by_email(body.email)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Ya existe una cuenta con ese correo."
        )

    password_hash = hash_password(body.password)
    user = await repo.create_user(email=body.email, password_hash=password_hash)

    tenant = await repo.create_tenant(
        name=body.tenant_name, slug=_slugify(body.tenant_name), plan_key="free_selfhost"
    )
    await repo.create_membership(user_id=user["id"], tenant_id=tenant["id"], role="owner")
    await repo.create_persona_default(tenant_id=tenant["id"], user_id=user["id"])
    await repo.add_audit_log(
        tenant_id=tenant["id"],
        actor_user_id=user["id"],
        action="auth.register",
        target=str(user["id"]),
    )

    return await _issue_token_pair(
        user_id=user["id"],
        tenant_id=tenant["id"],
        plan_key=tenant["plan_key"],
        settings=settings,
        redis_client=redis_client,
    )


@router.post("/login", response_model=TokenPairOut)
async def login(
    body: LoginIn,
    request: Request,
    repo: Repo = Depends(get_platform_repo),
    settings: Settings = Depends(get_settings),
    redis_client: redis_asyncio.Redis = Depends(get_redis),
) -> TokenPairOut:
    await _enforce_auth_rate_limit(request, redis_client, settings, identity=body.email)
    user = await repo.get_user_by_email(body.email)
    candidate_hash = user["password_hash"] if user is not None else _DUMMY_PASSWORD_HASH
    password_matches = verify_password(candidate_hash, body.password)
    if user is None or not password_matches:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Correo o contraseña incorrectos."
        )

    if user.get("totp_secret"):
        if not body.totp_code or not verify_totp_code(user["totp_secret"], body.totp_code):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Se requiere un código TOTP válido para esta cuenta.",
            )

    membership = await repo.get_first_membership_for_user(user["id"])
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="El usuario no pertenece a ningún tenant."
        )
    tenant = await repo.get_tenant(membership["tenant_id"])
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant no encontrado.")

    # Upgrade transparente del hash: si `password_hash` se creó con parámetros
    # Argon2 más débiles que los actuales de la librería, se recalcula con la
    # contraseña en claro que el usuario acaba de demostrar que conoce — es la
    # única oportunidad de tenerla, nunca se guarda. Solo se llega acá con el
    # login ya 100% exitoso (contraseña + TOTP si aplica + tenant válidos).
    if needs_rehash(user["password_hash"]):
        await repo.update_user_password_hash(user["id"], hash_password(body.password))

    await repo.add_audit_log(
        tenant_id=tenant["id"],
        actor_user_id=user["id"],
        action="auth.login",
        target=str(user["id"]),
    )
    return await _issue_token_pair(
        user_id=user["id"],
        tenant_id=tenant["id"],
        plan_key=tenant["plan_key"],
        settings=settings,
        redis_client=redis_client,
    )


@router.post("/refresh", response_model=TokenPairOut)
async def refresh(
    body: RefreshIn,
    request: Request,
    repo: Repo = Depends(get_platform_repo),
    settings: Settings = Depends(get_settings),
    redis_client: redis_asyncio.Redis = Depends(get_redis),
) -> TokenPairOut:
    await _enforce_auth_rate_limit(
        request, redis_client, settings, identity=_refresh_token_key(body.refresh_token)
    )
    try:
        decoded = decode_token(
            body.refresh_token, secret=settings.JWT_SECRET, expected_typ="refresh"
        )
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    user = await repo.get_user(decoded.sub)
    tenant = await repo.get_tenant(decoded.ten)
    if user is None or tenant is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario o tenant ya no existen."
        )
    membership = await repo.get_membership(user_id=decoded.sub, tenant_id=decoded.ten)
    if membership is None or tenant.get("status") != "active":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="La sesión ya no tiene acceso a este tenant.",
        )

    # Mismo enforcement de 2FA que /login (§10.12): si la cuenta tiene TOTP
    # habilitado, el refresh token NO alcanza por sí solo para renovar la
    # sesión. Sin esto, una cuenta con 2FA activado quedaría protegida solo
    # en el login inicial —cualquier refresh token ya emitido (incluso antes
    # de activar TOTP) rotaría indefinidamente sin volver a pasar nunca por
    # el segundo factor.
    if user.get("totp_secret"):
        if not body.totp_code or not verify_totp_code(user["totp_secret"], body.totp_code):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Se requiere un código TOTP válido para esta cuenta.",
            )

    # GETDEL es atómico: entre dos refresh concurrentes solo uno consume el
    # token. Así la rotación es real en servidor, no solo una convención del UI.
    consumed_session = await redis_client.getdel(_refresh_token_key(body.refresh_token))
    if consumed_session is None or consumed_session != str(decoded.sid):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="El refresh token fue revocado o ya fue utilizado.",
        )

    await repo.add_audit_log(
        tenant_id=tenant["id"],
        actor_user_id=user["id"],
        action="auth.refresh",
        target=str(decoded.sid),
    )
    return await _issue_token_pair(
        user_id=user["id"],
        tenant_id=tenant["id"],
        plan_key=tenant["plan_key"],
        settings=settings,
        redis_client=redis_client,
        session_id=decoded.sid,
    )


@router.post("/logout")
async def logout(
    body: LogoutIn,
    request: Request,
    repo: Repo = Depends(get_platform_repo),
    settings: Settings = Depends(get_settings),
    redis_client: redis_asyncio.Redis = Depends(get_redis),
) -> dict[str, bool]:
    """Revoca el refresh token actual; es idempotente para el cliente."""
    await _enforce_auth_rate_limit(
        request, redis_client, settings, identity=_refresh_token_key(body.refresh_token)
    )
    try:
        decoded = decode_token(
            body.refresh_token, secret=settings.JWT_SECRET, expected_typ="refresh"
        )
    except TokenError:
        return {"revoked": True}

    await redis_client.delete(_refresh_token_key(body.refresh_token))
    membership = await repo.get_membership(user_id=decoded.sub, tenant_id=decoded.ten)
    if membership is not None:
        await repo.add_audit_log(
            tenant_id=decoded.ten,
            actor_user_id=decoded.sub,
            action="auth.logout",
            target=str(decoded.sid),
        )
    return {"revoked": True}


@router.post("/totp/enable", response_model=TotpEnableOut)
async def totp_enable(
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_platform_repo),
    redis_client: redis_asyncio.Redis = Depends(get_redis),
) -> TotpEnableOut:
    user = await repo.get_user(current_user.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado.")

    # OJO: el secreto NO se escribe todavía en `users.totp_secret`. Se guarda
    # como "pendiente" en Redis (de un solo uso, con TTL) hasta que
    # `POST /totp/verify` demuestre que el usuario puede generar un código
    # válido con su app de autenticación. Si se persistiera acá de forma
    # incondicional, `POST /login` empezaría a exigir `totp_code` para esta
    # cuenta desde este instante — y un usuario que nunca completa el paso de
    # verificación (cierra la pestaña, falla el escaneo del QR, error de red)
    # quedaría bloqueado PARA SIEMPRE fuera de su cuenta, sin período de
    # gracia ni ruta de recuperación.
    secret = generate_totp_secret()
    await redis_client.set(
        _pending_totp_key(current_user.user_id), secret, ex=PENDING_TOTP_TTL_SECONDS
    )
    uri = totp_provisioning_uri(secret, account_email=user["email"])
    return TotpEnableOut(secret=secret, provisioning_uri=uri)


@router.post("/totp/verify")
async def totp_verify(
    body: TotpVerifyIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_platform_repo),
    redis_client: redis_asyncio.Redis = Depends(get_redis),
) -> dict[str, bool]:
    user = await repo.get_user(current_user.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado.")

    pending_key = _pending_totp_key(current_user.user_id)
    pending_secret = await redis_client.get(pending_key)
    if not pending_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No hay un secreto TOTP pendiente de confirmar (o expiró). "
                "Generá uno nuevo con /totp/enable."
            ),
        )
    if not verify_totp_code(pending_secret, body.code):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Código TOTP inválido."
        )

    # Recién acá, con la posesión de un código válido ya demostrada, se activa
    # 2FA de verdad para la cuenta.
    await repo.set_user_totp_secret(current_user.user_id, pending_secret)
    await redis_client.delete(pending_key)
    return {"verified": True}


@router.post("/totp/disable")
async def totp_disable(
    body: TotpDisableIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_platform_repo),
) -> dict[str, bool]:
    """Apaga 2FA para la cuenta. Sin este endpoint, un usuario que pierde su
    dispositivo/app de autenticación queda bloqueado PARA SIEMPRE: tanto
    `/login` como `/refresh` exigen `totp_code` de forma incondicional en
    cuanto `users.totp_secret` queda seteado (líneas ~139 y ~198), y no existe
    ninguna otra ruta de recuperación (self-service, soporte ni admin) en el
    producto.
    """
    user = await repo.get_user(current_user.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado.")

    if not user.get("totp_secret"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Esta cuenta no tiene TOTP habilitado.",
        )

    # Se re-exige la CONTRASEÑA (no un código TOTP: es justo lo que el usuario
    # puede haber perdido junto con el dispositivo) antes de apagar 2FA. Sin
    # esto, un access token robado (XSS, log filtrado, etc.) bastaría por sí
    # solo para desactivar el segundo factor de una cuenta ajena.
    if not verify_password(user["password_hash"], body.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Contraseña incorrecta."
        )

    await repo.set_user_totp_secret(current_user.user_id, None)
    return {"disabled": True}
