"""`POST /v1/auth/*` con `FakeRepo` en memoria + rechazo de tokens expirados
(ARCHITECTURE.md §10.12)."""

from __future__ import annotations

import time
import uuid

import jwt
import pyotp
from conftest import TEST_JWT_SECRET


async def test_register_creates_tenant_and_returns_token_pair(client) -> None:
    response = await client.post(
        "/v1/auth/register",
        json={
            "email": "ana@example.com",
            "password": "supersecreta123",
            "tenant_name": "Ana Consultora",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["refresh_token"]


async def test_register_rejects_duplicate_email(client) -> None:
    payload = {"email": "dup@example.com", "password": "supersecreta123", "tenant_name": "Dup Co"}
    first = await client.post("/v1/auth/register", json=payload)
    assert first.status_code == 201

    second = await client.post("/v1/auth/register", json=payload)
    assert second.status_code == 409


async def test_register_rejects_short_password(client) -> None:
    response = await client.post(
        "/v1/auth/register",
        json={"email": "corta@example.com", "password": "123", "tenant_name": "Corta Co"},
    )
    assert response.status_code == 422


async def test_login_returns_tokens_for_valid_credentials(client) -> None:
    await client.post(
        "/v1/auth/register",
        json={
            "email": "beto@example.com",
            "password": "otra-clave-larga",
            "tenant_name": "Beto SRL",
        },
    )

    response = await client.post(
        "/v1/auth/login", json={"email": "beto@example.com", "password": "otra-clave-larga"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]


async def test_login_rejects_wrong_password(client) -> None:
    await client.post(
        "/v1/auth/register",
        json={
            "email": "carla@example.com",
            "password": "clave-correcta-1",
            "tenant_name": "Carla LLC",
        },
    )
    response = await client.post(
        "/v1/auth/login", json={"email": "carla@example.com", "password": "clave-incorrecta"}
    )
    assert response.status_code == 401


async def test_login_rejects_unknown_email(client) -> None:
    response = await client.post(
        "/v1/auth/login", json={"email": "no-existe@example.com", "password": "lo-que-sea-123"}
    )
    assert response.status_code == 401


async def test_refresh_returns_new_token_pair(client) -> None:
    register = await client.post(
        "/v1/auth/register",
        json={"email": "dana@example.com", "password": "clave-refresh-1", "tenant_name": "Dana Co"},
    )
    refresh_token = register.json()["refresh_token"]

    response = await client.post("/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]

    # El access token nuevo debe servir de verdad contra una ruta protegida.
    me = await client.get("/v1/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200


async def test_refresh_rejects_an_access_token(client) -> None:
    register = await client.post(
        "/v1/auth/register",
        json={"email": "eva@example.com", "password": "clave-refresh-2", "tenant_name": "Eva Co"},
    )
    access_token = register.json()["access_token"]

    response = await client.post("/v1/auth/refresh", json={"refresh_token": access_token})
    assert response.status_code == 401


async def test_missing_authorization_header_returns_401(client) -> None:
    response = await client.get("/v1/me")
    assert response.status_code == 401


async def test_expired_access_token_returns_401(client) -> None:
    expired_payload = {
        "sub": str(uuid.uuid4()),
        "ten": str(uuid.uuid4()),
        "plan": "hosted_basic",
        "typ": "access",
        "exp": int(time.time()) - 60,  # expiró hace un minuto
    }
    expired_token = jwt.encode(expired_payload, TEST_JWT_SECRET, algorithm="HS256")

    response = await client.get("/v1/me", headers={"Authorization": f"Bearer {expired_token}"})

    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


async def test_malformed_authorization_header_returns_401(client) -> None:
    response = await client.get("/v1/me", headers={"Authorization": "Token abc123"})
    assert response.status_code == 401


async def _registrar_y_loguear(client, *, email: str, password: str) -> str:
    """Registra un usuario nuevo y devuelve su `access_token`."""
    register = await client.post(
        "/v1/auth/register",
        json={"email": email, "password": password, "tenant_name": f"Tenant {email}"},
    )
    assert register.status_code == 201
    return register.json()["access_token"]


async def test_totp_enable_alone_never_locks_the_user_out(client) -> None:
    """Regresión: generar el secreto TOTP (paso 1) sin completar `/totp/verify`
    (paso 2) NO debe exigir `totp_code` en `/login` — de lo contrario el
    usuario queda bloqueado de forma permanente en su cuenta."""
    email, password = "totp-sin-verificar@example.com", "clave-totp-1234"
    access_token = await _registrar_y_loguear(client, email=email, password=password)

    enable = await client.post(
        "/v1/auth/totp/enable", headers={"Authorization": f"Bearer {access_token}"}
    )
    assert enable.status_code == 200
    assert enable.json()["secret"]

    # Nunca se llama a /totp/verify. El login normal (sin totp_code) debe
    # seguir funcionando: el secreto pendiente no debe haberse activado.
    login = await client.post("/v1/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    assert login.json()["access_token"]


async def test_totp_verify_with_wrong_code_does_not_lock_the_user_out(client) -> None:
    """Un código incorrecto en `/totp/verify` tampoco debe activar 2FA."""
    email, password = "totp-codigo-malo@example.com", "clave-totp-1234"
    access_token = await _registrar_y_loguear(client, email=email, password=password)
    headers = {"Authorization": f"Bearer {access_token}"}

    enable = await client.post("/v1/auth/totp/enable", headers=headers)
    assert enable.status_code == 200

    verify = await client.post("/v1/auth/totp/verify", json={"code": "000000"}, headers=headers)
    assert verify.status_code == 401

    login = await client.post("/v1/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200


async def test_totp_verify_without_pending_secret_returns_400(client) -> None:
    """Verificar sin haber llamado antes a `/totp/enable` (o tras expirar el
    TTL) responde 400 en vez de 500/401 confuso."""
    access_token = await _registrar_y_loguear(
        client, email="totp-sin-pendiente@example.com", password="clave-totp-1234"
    )
    response = await client.post(
        "/v1/auth/totp/verify",
        json={"code": "123456"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert response.status_code == 400


async def test_totp_verify_with_valid_code_activates_2fa_for_login(client) -> None:
    """Flujo feliz: recién tras un `/totp/verify` exitoso, `/login` debe
    empezar a exigir `totp_code` — y el código correcto sí debe dejar entrar."""
    email, password = "totp-activado@example.com", "clave-totp-1234"
    access_token = await _registrar_y_loguear(client, email=email, password=password)
    headers = {"Authorization": f"Bearer {access_token}"}

    enable = await client.post("/v1/auth/totp/enable", headers=headers)
    assert enable.status_code == 200
    secret = enable.json()["secret"]
    valid_code = pyotp.TOTP(secret).now()

    verify = await client.post("/v1/auth/totp/verify", json={"code": valid_code}, headers=headers)
    assert verify.status_code == 200
    assert verify.json() == {"verified": True}

    sin_codigo = await client.post("/v1/auth/login", json={"email": email, "password": password})
    assert sin_codigo.status_code == 401

    con_codigo = await client.post(
        "/v1/auth/login",
        json={"email": email, "password": password, "totp_code": pyotp.TOTP(secret).now()},
    )
    assert con_codigo.status_code == 200
    assert con_codigo.json()["access_token"]


async def _registrar_y_activar_totp(client, *, email: str, password: str) -> tuple[str, str]:
    """Registra un usuario, activa 2FA por completo (`/totp/enable` +
    `/totp/verify`) y devuelve `(refresh_token, secret)` de un login posterior
    ya con `totp_code` — punto de partida realista para probar el mismo
    enforcement de 2FA en `/refresh` (§10.12)."""
    register = await client.post(
        "/v1/auth/register",
        json={"email": email, "password": password, "tenant_name": f"Tenant {email}"},
    )
    assert register.status_code == 201
    headers = {"Authorization": f"Bearer {register.json()['access_token']}"}

    enable = await client.post("/v1/auth/totp/enable", headers=headers)
    assert enable.status_code == 200
    secret = enable.json()["secret"]

    verify = await client.post(
        "/v1/auth/totp/verify", json={"code": pyotp.TOTP(secret).now()}, headers=headers
    )
    assert verify.status_code == 200

    login = await client.post(
        "/v1/auth/login",
        json={"email": email, "password": password, "totp_code": pyotp.TOTP(secret).now()},
    )
    assert login.status_code == 200
    return login.json()["refresh_token"], secret


async def test_refresh_without_totp_code_is_rejected_when_2fa_enabled(client) -> None:
    """Mismo gate de 2FA que /login (auth.py ~L192-203): con TOTP habilitado,
    un `/refresh` sin `totp_code` debe rechazarse. Sin esta regla, el segundo
    factor solo protegería el login inicial y cualquier refresh token ya
    emitido rotaría indefinidamente sin volver a pasar nunca por el segundo
    factor."""
    refresh_token, _secret = await _registrar_y_activar_totp(
        client, email="totp-refresh-sin-codigo@example.com", password="clave-totp-1234"
    )

    response = await client.post("/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert response.status_code == 401


async def test_refresh_with_wrong_totp_code_is_rejected_when_2fa_enabled(client) -> None:
    """Un `totp_code` incorrecto en `/refresh` tampoco debe alcanzar para
    renovar la sesión."""
    refresh_token, _secret = await _registrar_y_activar_totp(
        client, email="totp-refresh-codigo-malo@example.com", password="clave-totp-1234"
    )

    response = await client.post(
        "/v1/auth/refresh", json={"refresh_token": refresh_token, "totp_code": "000000"}
    )
    assert response.status_code == 401


async def test_refresh_with_valid_totp_code_rotates_tokens_when_2fa_enabled(client) -> None:
    """Flujo feliz: con el código TOTP correcto, `/refresh` sí rota el par de
    tokens y el `access_token` nuevo funciona contra una ruta protegida."""
    refresh_token, secret = await _registrar_y_activar_totp(
        client, email="totp-refresh-codigo-valido@example.com", password="clave-totp-1234"
    )

    response = await client.post(
        "/v1/auth/refresh",
        json={"refresh_token": refresh_token, "totp_code": pyotp.TOTP(secret).now()},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]

    me = await client.get("/v1/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200


async def test_totp_disable_removes_2fa_requirement_from_login(client) -> None:
    """Regresión del bloqueo permanente por pérdida de dispositivo: una vez
    activado 2FA, `/totp/disable` (con la contraseña correcta) debe apagarlo
    y devolver `/login` a aceptar de nuevo sin `totp_code`."""
    email, password = "totp-disable@example.com", "clave-totp-1234"
    access_token = await _registrar_y_loguear(client, email=email, password=password)
    headers = {"Authorization": f"Bearer {access_token}"}

    enable = await client.post("/v1/auth/totp/enable", headers=headers)
    secret = enable.json()["secret"]
    verify = await client.post(
        "/v1/auth/totp/verify", json={"code": pyotp.TOTP(secret).now()}, headers=headers
    )
    assert verify.status_code == 200

    # Con 2FA activo, el login sin código queda bloqueado (línea de base).
    bloqueado = await client.post("/v1/auth/login", json={"email": email, "password": password})
    assert bloqueado.status_code == 401

    disable = await client.post(
        "/v1/auth/totp/disable", json={"password": password}, headers=headers
    )
    assert disable.status_code == 200
    assert disable.json() == {"disabled": True}

    # El usuario recupera el acceso sin necesitar el dispositivo TOTP perdido.
    recuperado = await client.post("/v1/auth/login", json={"email": email, "password": password})
    assert recuperado.status_code == 200
    assert recuperado.json()["access_token"]


async def test_totp_disable_rejects_wrong_password(client) -> None:
    """Un access token robado no debe alcanzar por sí solo para apagar 2FA:
    hace falta también la contraseña — y una incorrecta no debe desactivarlo."""
    email, password = "totp-disable-mal@example.com", "clave-totp-1234"
    access_token = await _registrar_y_loguear(client, email=email, password=password)
    headers = {"Authorization": f"Bearer {access_token}"}

    enable = await client.post("/v1/auth/totp/enable", headers=headers)
    secret = enable.json()["secret"]
    await client.post(
        "/v1/auth/totp/verify", json={"code": pyotp.TOTP(secret).now()}, headers=headers
    )

    disable = await client.post(
        "/v1/auth/totp/disable", json={"password": "contraseña-incorrecta"}, headers=headers
    )
    assert disable.status_code == 401

    # 2FA sigue activo: el login sin código sigue bloqueado.
    sigue_bloqueado = await client.post(
        "/v1/auth/login", json={"email": email, "password": password}
    )
    assert sigue_bloqueado.status_code == 401


async def test_totp_disable_without_totp_enabled_returns_400(client) -> None:
    access_token = await _registrar_y_loguear(
        client, email="totp-disable-sin-2fa@example.com", password="clave-totp-1234"
    )
    response = await client.post(
        "/v1/auth/totp/disable",
        json={"password": "clave-totp-1234"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert response.status_code == 400
