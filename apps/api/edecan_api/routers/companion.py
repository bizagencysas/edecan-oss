"""`POST /v1/companion/pair-code` y `WS /v1/companion/ws` (ARCHITECTURE.md §10.12).

El pair-code es un código alfanumérico de 8 caracteres guardado en Redis
(`pair:{code}` -> `tenant_id`, TTL 24h). El companion de escritorio lo
introduce al conectar el WebSocket; si es válido, la conexión se registra en
`app.state.companion_manager` (`edecan_api.companion_manager.ConnectionManager`).

El pair-code es REUTILIZABLE durante su TTL: el handshake WS valida el código
contra Redis pero NO lo borra. Solo expira por TTL (o si el usuario genera un
código nuevo, que reemplaza la key). El motivo: el companion de la Mac
reconecta con el MISMO código tras cada corte de red (caída de internet,
reinicio del API); si el primer uso borrara el par, cada reconexión quedaría
sin emparejar hasta que el loop vivo de la Mac pidiera un código nuevo, y el
pairing viejo era de un solo uso justo para eso. El QR del teléfono NO usa
esta key: es `devices.py` con su propio secreto de un solo uso (GETDEL).

`companion_ws` NO puede usar `Depends(rate_limit)` como el resto de rutas con
credenciales (p. ej. `pair-code` más abajo): `rate_limit` exige
`Depends(get_current_user)`, es decir un JWT — y en este punto del flujo el
companion todavía no tiene ninguno (es justo lo que el pairing resuelve). Por
eso `_pair_ws_rate_limited` reimplementa el mismo patrón INCR+EXPIRE de
ventana fija de `deps.rate_limit`, pero indexado por IP de origen en vez de
`tenant_id`, para que adivinar el código a fuerza bruta contra el handshake
WS quede acotado en velocidad igual que cualquier otro endpoint con
credenciales de este código base.
"""

from __future__ import annotations

import secrets
import string
import time
import uuid

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from redis.asyncio import Redis

from edecan_api.config import get_settings
from edecan_api.deps import CurrentUser, get_current_user, get_redis, rate_limit

router = APIRouter(prefix="/v1/companion", tags=["companion"])

PAIR_CODE_TTL_SECONDS = 24 * 60 * 60  # 24h: el companion reconecta con el MISMO código (ver docstring).
PAIR_CODE_LENGTH = 8
# Alfabeto sin caracteres ambiguos (0/O, 1/I/L) para que sea fácil de teclear a mano.
_PAIR_CODE_ALPHABET = "".join(
    c for c in (string.ascii_uppercase + string.digits) if c not in "01OIL"
)

# Código de cierre WS (rango privado 4000-4999 de RFC 6455) para pairing inválido/expirado.
WS_CLOSE_INVALID_PAIR_CODE = 4401
# Ídem, para exceso de intentos de pairing desde una misma IP (distinto del
# anterior para que un companion legítimo sepa que debe esperar antes de
# reintentar, en vez de pedir un pair-code nuevo).
WS_CLOSE_RATE_LIMITED = 4429

# Intentos de handshake (código válido o inválido, cuenta igual) permitidos
# por IP de origen antes de cortar la conexión — mismo espíritu que un
# lockout de login: lo que hay que acotar es la velocidad de adivinanza, no
# solo los intentos fallidos.
PAIR_WS_RATE_LIMIT_MAX_ATTEMPTS = 10
PAIR_WS_RATE_LIMIT_WINDOW_SECONDS = 60


async def _pair_ws_rate_limited(redis_client: Redis, client_host: str) -> bool:
    """`True` si `client_host` ya superó `PAIR_WS_RATE_LIMIT_MAX_ATTEMPTS`
    handshakes en la ventana fija actual de `PAIR_WS_RATE_LIMIT_WINDOW_SECONDS`
    segundos (mismo algoritmo INCR+EXPIRE que `deps.rate_limit`)."""
    window = int(time.time()) // PAIR_WS_RATE_LIMIT_WINDOW_SECONDS
    key = f"pairws:{client_host}:{window}"
    count = await redis_client.incr(key)
    if count == 1:
        await redis_client.expire(key, PAIR_WS_RATE_LIMIT_WINDOW_SECONDS)
    return count > PAIR_WS_RATE_LIMIT_MAX_ATTEMPTS


@router.post("/pair-code", dependencies=[Depends(rate_limit)])
async def create_pair_code(
    current_user: CurrentUser = Depends(get_current_user),
    redis_client=Depends(get_redis),
) -> dict[str, str]:
    code = "".join(secrets.choice(_PAIR_CODE_ALPHABET) for _ in range(PAIR_CODE_LENGTH))
    await redis_client.set(f"pair:{code}", str(current_user.tenant_id), ex=PAIR_CODE_TTL_SECONDS)
    return {"code": code}


@router.websocket("/ws")
async def companion_ws(websocket: WebSocket, code: str) -> None:
    # `get_redis`/`get_settings` son funciones normales (no dependen de
    # `Request`), así que se llaman directo en vez de pasar por `Depends(...)`.
    redis_client = get_redis(get_settings())

    client_host = websocket.client.host if websocket.client is not None else "desconocido"
    if await _pair_ws_rate_limited(redis_client, client_host):
        await websocket.close(code=WS_CLOSE_RATE_LIMITED)
        return

    tenant_id_raw = await redis_client.get(f"pair:{code}")
    if not tenant_id_raw:
        await websocket.close(code=WS_CLOSE_INVALID_PAIR_CODE)
        return
    # El par NO se borra acá (reutilizable durante su TTL): el companion
    # reconecta con el mismo código tras cada corte. Si dos conexiones usan
    # el mismo par, `ConnectionManager.connect` reemplaza la vieja por la
    # nueva. La expiración es SOLO por TTL; generar un código nuevo reemplaza
    # la key y deja inservible el anterior.
    tenant_id = uuid.UUID(tenant_id_raw)

    manager = websocket.app.state.companion_manager
    await manager.connect(
        tenant_id, websocket, name=websocket.query_params.get("name", "Mac")
    )
    try:
        while True:
            message = await websocket.receive_json()
            await manager.handle_incoming(tenant_id, message)
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(tenant_id, websocket)
