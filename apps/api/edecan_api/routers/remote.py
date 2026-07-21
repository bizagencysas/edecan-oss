"""`POST /v1/remote/sessions` y rutas asociadas — control remoto de pantalla
(`ROADMAP_V2.md` §5 WP-V2-09, §7.2, §7.4, §7.5, §7.6, §7.8, §8.2) MÁS, desde
WP-V4-10 (control remoto fase 2), input de teclado/mouse nivel TeamViewer
(`docs/control-remoto.md` §7 y su nueva sección "Fase 2: input remoto").

**Qué es esto**: `kind="view"` (default, sin cambios desde WP-V2-09) sigue
siendo la mitad "real y funcional" original — una sesión de **solo vista**,
por *polling* de capturas de pantalla del companion ya emparejado
(`edecan_api.companion_manager`). `kind="control"` (WP-V4-10, esta fase) le
suma `POST /sessions/{id}/input`: reenvía comandos de teclado/mouse al
companion (`input_pointer`/`input_key`, `ROADMAP_V2.md` §7.8) DETRÁS de los
mismos candados en serie que ya exigía la vista, más dos nuevos —
ver `_require_remote_control` y el docstring de `send_input` más abajo.
El diseño completo nivel TeamViewer que sigue siendo SOLO diseño (WebRTC,
E2EE, TURN propio) vive en `docs/control-remoto.md` §5.

**Qué NO es esto**: sigue sin haber WebRTC ni transporte de video en vivo —
el prototipo sigue siendo *polling* HTTP de frames sueltos (`get_frame`,
sin cambios). El input tampoco es un *stream* continuo de eventos (nada de
seguir el mouse en tiempo real): son comandos discretos, uno por request,
cada uno con su propia aprobación local en el companion — nunca un backdoor
silencioso (`ARCHITECTURE.md` §0, `ROADMAP_V2.md` §8.2).

## Piezas que este router SÍ tiene hoy (reales)

- Gate de flag de plan `companion.remote_view` (§7.2) vía
  `edecan_schemas.plans.FLAG_COMPANION_REMOTE_VIEW` — mismo patrón que
  `voice.py`/`consents.py` con `FLAG_VOICE_WEB`/`FLAG_VOICE_TELEPHONY`. Hoy
  (WP-V2-01 ya aterrizó el catálogo de flags/planes v2) está en `True` para
  `free_selfhost`/`hosted_pro`/`hosted_business` y en `False` para
  `hosted_basic`, tal como pinnea la tabla de `ROADMAP_V2.md` §7.2.
- Consentimiento explícito (`consent: true` obligatorio, 422 si falta o es
  `false`) antes de crear cualquier sesión.
- Persistencia real en Postgres: las sesiones viven en la tabla
  `remote_sessions` (migración `0003_v2_expansion`, WP-V2-01, ya aterrizada
  — ver "Persistencia" más abajo), no en memoria.
- Uso real de `companion_manager.ConnectionManager.send_command` (inyectado
  como `request.app.state.companion_manager`, igual que
  `routers.conversations._companion_caller`) para pedirle un frame al
  companion emparejado del tenant — **nunca** se simulan datos.
- Auditoría real vía `Repo.add_audit_log` (tabla `audit_log`, ya existe
  desde v1) en cada transición relevante: solicitud, inicio, denegación y
  fin de sesión. La denegación (`get_frame`, más abajo) hace
  `db_session.commit()` explícito de esos dos writes ANTES de lanzar el 403:
  sin eso, el rollback automático de la transacción de la request
  (`edecan_db.session.get_session`, que envuelve TODA la request en una sola
  transacción) se llevaría por delante justo la evidencia de que el usuario
  denegó — la parte que más importa auditar (`HOTFIXES_PENDIENTES.md` punto
  8; el parámetro se llama `db_session`, no `session`, para no chocar con la
  variable local `session` — el dict de `remote_sessions` — ver el
  comentario en `get_frame`).
- Rate limit del *polling* de frames en Redis (`deps.get_redis`), separado
  del límite general de 60 req/min por tenant (`deps.rate_limit`, aplicado a
  nivel de router) — ver `_check_frame_rate_limit`.
- **Nuevo (WP-V4-10)**: `kind="control"` en `POST /sessions` (gate adicional
  de flag `companion.remote_input`, `_require_remote_control`) y
  `POST /sessions/{id}/input` — reenvía `input_pointer`/`input_key` al
  companion, con su propio rate limit (`_check_input_rate_limit`) y su propia
  auditoría por comando (`remote.session.input` / `remote.session.input_denied`).
  Ver el docstring de `send_input` más abajo para el detalle completo.

## Persistencia (`remote_sessions`, Postgres real)

Las tablas `devices` y `remote_sessions` de `ROADMAP_V2.md` §7.4 son
propiedad de la migración `0003_v2_expansion` (WP-V2-01), que ya aterrizó en
este árbol (modelos `edecan_db.models.Device`/`RemoteSession`, tabla real con
RLS). Este router persiste cada sesión en `remote_sessions` de verdad, vía
`edecan_api.repo.Repo.create_remote_session` / `list_remote_sessions` /
`get_remote_session` / `record_remote_session_frame` /
`mark_remote_session_denied` / `mark_remote_session_ended` — `SqlRepo` las
implementa con SQL parametrizado contra el esquema pinneado (mismo criterio
que el resto de `edecan_api.repo`, ver su docstring); `FakeRepo`
(`apps/api/tests/api_fakes.py`) las implementa en memoria solo para tests,
igual que el resto del `Repo`. Una sesión ahora sobrevive un reinicio del
proceso API y es visible desde cualquier *worker* `uvicorn` que consulte
Postgres — ya no vive en un diccionario en memoria por proceso.

Lo que esto NO arregla: la conexión WebSocket con el companion
(`edecan_api.companion_manager.ConnectionManager`, mapa en memoria de
sockets abiertos) sigue siendo inherentemente por proceso — es un socket
vivo, no una fila de tabla, así que `GET .../frame` solo funciona en el
mismo *worker* que tiene esa conexión abierta. Un despliegue con varios
*workers* detrás de un load balancer sin *sticky sessions* seguiría
necesitando un backend compartido para ESE mapa (p. ej. Redis pub/sub);
eso queda fuera del alcance de este paquete de trabajo, igual que antes.

`device_id` sigue quedando `NULL` en cada fila creada: el emparejamiento con
la tabla `devices` (apps móviles, `ROADMAP_V2.md` §6.1) sigue siendo P2, no
lo implementa este router — ver "Qué NO es esto" arriba.

`kind` ya NO está fijo en `'view'` (WP-V4-10): `create_remote_session` sigue
insertando siempre `'view'` (sin tocar su firma, ver `Repo.create_remote_session`)
y, si el cliente pidió `kind="control"`, `create_session` la promueve acto
seguido con el método nuevo `Repo.mark_remote_session_kind` — un método
aditivo en vez de un parámetro para no romper la firma que ya consumen
`SqlRepo`/`FakeRepo`/todos los tests de v2 (ver el comentario en
`edecan_api.repo.Repo` junto a su declaración). Sin migración nueva: la
columna ya es vocabulario abierto, sin `CHECK constraint`
(`edecan_db.models.RemoteSession`, migración `0003_v2_expansion`).

## Contrato de degradación con el companion (`screenshot`)

La acción `screenshot` conserva el contrato de WP-V2-08 y lo amplía en v0.5:
`{display?, format?, quality?, max_width?} -> {image_b64, width, height,
mime, origin_x, origin_y}`. Ese WP aterrizó
`apps/companion/edecan_companion/actions.py::_screenshot` MIENTRAS este WP
estaba en curso — el contrato de degradación de abajo sigue siendo necesario
igual: un companion instalado ANTES de esa actualización, cualquiera con
`ide_enabled: false` en su `~/.edecan/companion.yaml` (`_screenshot` vive en
`_IDE_ACTIONS`, así que hereda ese gate), o uno en una plataforma no
soportada, sigue sin poder servir capturas. En cualquiera de esos casos
`actions.execute` responde
con un mensaje de error reconocible, y este router lo traduce a `501` en vez
de dejarlo pasar como un `502` genérico o reventar — es el "contrato de
degradación pinned" que menciona el paquete de trabajo. Con un companion
actualizado, con `ide_enabled: true` (el default de `config.py`), el extra
`remote-control` en Windows/Linux y los permisos de captura de su sistema, la
captura es real de punta a punta.

Del mismo contrato de `actions.execute` también se reconoce el mensaje
exacto que el companion devuelve cuando el USUARIO deniega la aprobación
local de la acción (`"acción rechazada (sin aprobación del usuario)"`) — ese
caso mueve la sesión a `denied` y responde `403`, nunca `501` (son
situaciones distintas: una es "este companion no puede/no quiere hacer
esto ahora", la otra es "la persona frente a la máquina dijo que no").

## Fase 2: input remoto (WP-V4-10) — `POST /sessions/{id}/input`

Los CUATRO candados en serie que exige `docs/control-remoto.md` §6.2 para
"nivel TeamViewer" (ninguno sustituye a los demás):

1. **Flag de plan** `companion.remote_input` (`_require_remote_control`,
   recalculado en CADA request desde `PLANES[plan]` — nunca se confía en el
   flag que traía la sesión al crearse; un downgrade de plan a mitad de una
   sesión de control corta el acceso de inmediato).
2. **`remote_input_enabled: true`** en `~/.edecan/companion.yaml` del
   companion (`apagado por defecto` — opt-in explícito del dueño de la
   máquina, ver `apps/companion/edecan_companion/config.py`).
3. **Aprobación local por comando**, en el companion, con la regla "más
   dura" de `apps/companion/edecan_companion/approval.py::_approve_input_action`:
   nunca vía `auto_approve`, y su "recordado" está acotado a la sesión de
   control activa (`params["session_id"]`, que este router SIEMPRE incluye
   al reenviar el comando) además de a `remote_input_remember_minutes` — se
   pierde al cambiar de sesión aunque no hayan pasado esos minutos.
4. **Permiso de entrada del sistema operativo**. En macOS, Accesibilidad solo
   puede concederse con un clic humano y `_QuartzInputBackend` la verifica con
   `Quartz.AXIsProcessTrusted()`; Windows/Linux usan `pynput` y respetan los
   permisos y la sesión gráfica nativos. Ningún backend los evade.

`send_input` reutiliza el mismo pipeline de `get_frame` en todo lo que
aplica: 404 sesión inexistente/de otro tenant, 403 sesión `denied` (además de
un 403 nuevo, propio, si la sesión no es `kind="control"`), 409 sesión
`ended` o todavía no `active` (una sesión de control necesita, igual que una
de vista, al menos un `GET .../frame` exitoso antes de aceptar input — no
hay forma de controlar una pantalla que nunca se pidió ver), 429 rate limit
propio (`REMOTE_INPUT_MIN_INTERVAL_SECONDS`, `getattr` defensivo, default
0.05s — mucho más laxo que el de frames porque un clic/tecla es puntual, no
un *stream*), 501 el companion no soporta input o lo tiene deshabilitado o
corre en una plataforma sin soporte, 502 cualquier otra falla del companion,
503 companion no conectado o sin respuesta a tiempo. La denegación del
companion replica el patrón "commit de evidencia ANTES del raise" de
`HOTFIXES_PENDIENTES.md` punto 8 (ver el comentario en `get_frame`): marca la
sesión `denied` + audita `remote.session.input_denied` + `db_session.commit()`
explícito, TODO antes de lanzar el `HTTPException(403)` — si no, el rollback
automático de `edecan_db.session.get_session` al propagarse esa excepción se
llevaría por delante justo esa evidencia. Una denegación de UN comando de
input deniega la SESIÓN completa (no solo ese comando) — mismo criterio,
deliberadamente conservador, que ya usaba `get_frame` para la vista: si la
persona frente al equipo dice que no una vez, se cierra el grifo entero, no
solo ese pixel/tecla.

Auditoría por comando exitoso (`remote.session.input`, `meta` con `tipo` y un
resumen SIN el contenido -- `texto` nunca se escribe en claro, solo su
longitud, mismo principio que `edecan_companion.audit._REDACTED_KEYS`: ni
siquiera la propia auditoría de la plataforma se vuelve un keylogger).
"""

from __future__ import annotations

import time
import uuid
from typing import Annotated, Any, Literal

from edecan_schemas.plans import FLAG_COMPANION_REMOTE_INPUT, FLAG_COMPANION_REMOTE_VIEW
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator, model_validator
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.companion_manager import CompanionError
from edecan_api.config import Settings, get_settings
from edecan_api.deps import (
    CurrentUser,
    get_current_user,
    get_redis,
    get_repo,
    get_tenant_session,
    rate_limit,
)
from edecan_api.repo import Repo

# ``frame`` e ``input`` tienen límites dedicados mucho más precisos. El
# límite global de 60/min se aplica a las rutas administrativas mediante sus
# decoradores, para que no estrangule una vista interactiva de 3-4 FPS.
router = APIRouter(prefix="/v1/remote", tags=["remote"])

# Re-exportado bajo un nombre corto para el resto de este módulo (y para los
# tests, que lo referencian como `remote.FLAG_REMOTE_VIEW`).
FLAG_REMOTE_VIEW = FLAG_COMPANION_REMOTE_VIEW
# WP-V4-10: mismo criterio, `remote.FLAG_REMOTE_INPUT` para los tests.
FLAG_REMOTE_INPUT = FLAG_COMPANION_REMOTE_INPUT

# Acción del companion que este prototipo pide (WP-V2-08, `ROADMAP_V2.md` §7.8).
_SCREENSHOT_ACTION = "screenshot"

# Acciones del companion que pide `send_input` (WP-V4-10, `ROADMAP_V2.md` §7.8:
# `edecan_companion.actions._input_pointer`/`_input_key`).
_INPUT_POINTER_ACTION = "input_pointer"
_INPUT_KEY_ACTION = "input_key"

# Prefijos EXACTOS de `edecan_companion.actions.execute` (o de la propia
# `_screenshot`, vía `ActionError`) para los casos de error que este router
# necesita distinguir de cualquier otra falla.
# "no soportada": la acción no existe en absoluto en `ACTIONS` (companion
# desactualizado, de antes de WP-V2-08). "IDE deshabilitado": la acción SÍ
# existe pero `screenshot` vive en `_IDE_ACTIONS` y ese companion tiene
# `ide_enabled: false` en su `companion.yaml`. "plataforma no soportada": la
# acción SÍ existe y el IDE SÍ está habilitado, pero la plataforma no tiene
# un backend de captura. Los tres son motivos distintos para el mismo
# resultado práctico ("este companion no puede servir capturas ahora
# mismo"), así que los tres se traducen al mismo 501, cada uno con su
# propio mensaje (ver `_translate_companion_error`).
_ERROR_PREFIX_UNSUPPORTED = "acción no soportada"
_ERROR_PREFIX_IDE_DISABLED = "el IDE está deshabilitado"
_ERROR_PREFIX_PLATFORM_UNSUPPORTED = "captura no soportada en esta plataforma"
_ERROR_PREFIX_DENIED = "acción rechazada"

# Mismo mecanismo que los cuatro de arriba, pero para `input_pointer`/
# `input_key` (`_ERROR_PREFIX_UNSUPPORTED`/`_ERROR_PREFIX_DENIED` se
# REUTILIZAN tal cual — `actions.execute` produce exactamente el mismo texto
# sin importar la acción, ver su código). Textos EXACTOS producidos por
# `edecan_companion.actions.execute`/`_get_input_backend` (WP-V4-10):
# "deshabilitado" cuando `remote_input_enabled: false` en companion.yaml;
# "no está soportado en esta plataforma" cuando el companion corre en una
# plataforma sin backend (`_get_input_backend`, distinto de `screenshot` porque son
# mensajes de módulos/acciones distintas).
_ERROR_PREFIX_INPUT_DISABLED = "el control remoto de teclado/mouse está deshabilitado"
_ERROR_PREFIX_INPUT_PLATFORM_UNSUPPORTED = (
    "el control remoto de teclado/mouse no está soportado en esta plataforma"
)

# Mismos valores EXACTOS que `edecan_companion.actions._POINTER_ACTIONS`/
# `_MOUSE_BUTTONS`/`_SPECIAL_KEYS` (`ROADMAP_V2.md` §7.8), duplicados aquí a
# propósito como alias `Literal` -- este router no importa el paquete
# `edecan_companion` (viven en procesos distintos, uno en la máquina del
# cliente); Pydantic los usa para validar `PointerInputIn`/`KeyInputIn` con
# un 422 automático y descriptivo si el cliente manda un valor fuera de este
# vocabulario, sin que el companion tenga que enterarse siquiera.
PointerAccion = Literal[
    "move", "click", "double_click", "right_click",
    "mouse_down", "mouse_up", "drag", "scroll",
]
MouseButton = Literal["left", "right", "middle"]
SpecialKey = Literal[
    "enter",
    "tab",
    "escape",
    "backspace",
    "arrow_up",
    "arrow_down",
    "arrow_left",
    "arrow_right",
    "delete_forward",
    "home",
    "end",
    "page_up",
    "page_down",
    "space",
    "a",
    "c",
    "v",
    "x",
    "z",
    "s",
]
KeyModifier = Literal["command", "control", "option", "shift"]

# Default documentado en `ROADMAP_V2.md` §7.5 (`REMOTE_FRAME_MIN_INTERVAL_SECONDS`).
# Se lee de `settings` con `getattr(..., DEFAULT_FRAME_MIN_INTERVAL_SECONDS)`
# porque ese campo todavía no existe en `edecan_api.config.Settings` (lo
# agrega WP-V2-01 junto con el resto de `.env.example` de §7.5) — convención
# dura de `ROADMAP_V2.md` §7.5: "toda tool lee settings con
# `getattr(ctx.settings, "CAMPO", default)` — nunca revienta si falta el campo".
DEFAULT_FRAME_MIN_INTERVAL_SECONDS = 0.25

# TTL del timestamp de rate-limit en Redis: generoso respecto al intervalo
# mínimo (que hoy es ~1s) para no perder el registro entre un frame y el
# siguiente en una sesión de visualización activa, pero acotado para no
# acumular claves de sesiones abandonadas para siempre.
_FRAME_RATE_LIMIT_TTL_SECONDS = 300


def _get_companion_manager(request: Request) -> Any:
    """`request.app.state.companion_manager` (`ConnectionManager`, fijado en
    `main.create_app`) — `getattr` con default `None` en vez de acceso
    directo por si algún test monta este router sobre una `FastAPI()` que
    nunca pasó por `create_app()`."""
    return getattr(request.app.state, "companion_manager", None)


# ---------------------------------------------------------------------------
# Gate de flag de plan (§7.2) — sustituye a `get_current_user` en cada ruta.
# ---------------------------------------------------------------------------


def _require_remote_view(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not current_user.tenant.flags.get(FLAG_REMOTE_VIEW, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La vista remota no está disponible en tu plan.",
        )
    return current_user


def _require_remote_control(
    current_user: CurrentUser = Depends(_require_remote_view),
) -> CurrentUser:
    """Candado 1/4 de `docs/control-remoto.md` §6.2 (WP-V4-10): ADEMÁS de
    `companion.remote_view` (ya exigido por `_require_remote_view`, del que
    depende), exige `companion.remote_input` — recalculado en CADA request
    desde `PLANES[plan]`, nunca se confía en el flag que traía la sesión al
    crearse (`ARCHITECTURE.md` §10.12: "los flags se recalculan server-side
    ... nunca se confía en el token"). Usada tanto por `create_session`
    cuando pide `kind="control"` como por `send_input` en cada llamada."""
    if not current_user.tenant.flags.get(FLAG_REMOTE_INPUT, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El control remoto (teclado/mouse) no está disponible en tu plan.",
        )
    return current_user


# ---------------------------------------------------------------------------
# Rate limit del polling de frames (Redis, separado del general 60/min)
# ---------------------------------------------------------------------------


async def _check_frame_rate_limit(
    redis_client: Redis, tenant_id: uuid.UUID, session_id: uuid.UUID, settings: Settings
) -> None:
    """`429` si el último frame de esta sesión se pidió hace menos de
    `settings.REMOTE_FRAME_MIN_INTERVAL_SECONDS` (default
    `DEFAULT_FRAME_MIN_INTERVAL_SECONDS`, ver docstring del módulo)."""
    min_interval = getattr(
        settings, "REMOTE_FRAME_MIN_INTERVAL_SECONDS", DEFAULT_FRAME_MIN_INTERVAL_SECONDS
    )
    key = f"remote:frame:last:{tenant_id}:{session_id}"
    now = time.time()

    last_raw = await redis_client.get(key)
    if last_raw is not None:
        try:
            elapsed = now - float(last_raw)
        except (TypeError, ValueError):
            elapsed = min_interval  # valor corrupto en Redis: no bloquear por esto.
        if elapsed < min_interval:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Espera {min_interval - elapsed:.1f}s antes de pedir otro frame "
                    f"(límite: 1 cada {min_interval:g}s)."
                ),
            )

    await redis_client.set(key, str(now), ex=_FRAME_RATE_LIMIT_TTL_SECONDS)


# Default documentado en `docs/control-remoto.md` (nueva sección "Fase 2"):
# `REMOTE_INPUT_MIN_INTERVAL_SECONDS`, leído con `getattr` defensivo — mismo
# criterio que `DEFAULT_FRAME_MIN_INTERVAL_SECONDS`, este WP no toca
# `edecan_api.config.Settings` (fuera de las rutas que puede escribir).
# Mucho más laxo que el de frames (0.05s vs 1.0s): un clic/tecla es una
# acción puntual, no un *stream* continuo.
DEFAULT_INPUT_MIN_INTERVAL_SECONDS = 0.05


async def _check_input_rate_limit(
    redis_client: Redis, tenant_id: uuid.UUID, session_id: uuid.UUID, settings: Settings
) -> None:
    """`429` si el último comando de input de esta sesión se envió hace menos
    de `settings.REMOTE_INPUT_MIN_INTERVAL_SECONDS` (default
    `DEFAULT_INPUT_MIN_INTERVAL_SECONDS`). Mismo mecanismo que
    `_check_frame_rate_limit`, clave de Redis separada (`remote:input:...`
    vs `remote:frame:...`) para no compartir cupo con el *polling* de frames
    de la misma sesión."""
    min_interval = getattr(
        settings, "REMOTE_INPUT_MIN_INTERVAL_SECONDS", DEFAULT_INPUT_MIN_INTERVAL_SECONDS
    )
    key = f"remote:input:last:{tenant_id}:{session_id}"
    now = time.time()

    last_raw = await redis_client.get(key)
    if last_raw is not None:
        try:
            elapsed = now - float(last_raw)
        except (TypeError, ValueError):
            elapsed = min_interval  # valor corrupto en Redis: no bloquear por esto.
        if elapsed < min_interval:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Espera {min_interval - elapsed:.2f}s antes de enviar otro comando de "
                    f"control remoto (límite: 1 cada {min_interval:g}s)."
                ),
            )

    await redis_client.set(key, str(now), ex=_FRAME_RATE_LIMIT_TTL_SECONDS)


# ---------------------------------------------------------------------------
# Esquemas de entrada
# ---------------------------------------------------------------------------


class SessionCreateIn(BaseModel):
    consent: bool = Field(
        description=(
            "Debe ser exactamente `true`: confirma que el usuario dio consentimiento "
            "explícito, en la UI del panel, para iniciar una sesión de vista remota de "
            "su propio equipo. El companion pedirá una segunda aprobación, local, antes "
            "de entregar el primer frame — ver docs/control-remoto.md."
        ),
    )
    kind: Literal["view", "control"] = Field(
        default="view",
        description=(
            "'view' (default, sin cambios): solo vista. 'control' (WP-V4-10) además "
            "habilita POST .../input (teclado/mouse) — exige el flag de plan "
            "companion.remote_input, ADEMÁS de companion.remote_view."
        ),
    )

    @field_validator("consent")
    @classmethod
    def _consent_must_be_true(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError(
                "Debes enviar consent=true: el consentimiento explícito es obligatorio "
                "para iniciar una sesión de vista remota."
            )
        return value


class PointerInputIn(BaseModel):
    """`POST .../input {tipo: "pointer", ...}` — reenviado como `input_pointer`
    (`edecan_companion.actions._input_pointer`, mismo shape exacto)."""

    tipo: Literal["pointer"]
    x: int
    y: int
    accion: PointerAccion
    button: MouseButton | None = Field(
        default=None,
        description="Default 'left' si se omite. 'right_click' siempre usa el derecho.",
    )
    start_x: int | None = None
    start_y: int | None = None
    delta_x: int = Field(default=0, ge=-2400, le=2400)
    delta_y: int = Field(default=0, ge=-2400, le=2400)

    @model_validator(mode="after")
    def _validate_action_payload(self) -> PointerInputIn:
        if self.accion == "drag" and (self.start_x is None or self.start_y is None):
            raise ValueError("drag necesita start_x y start_y")
        if self.accion == "scroll" and self.delta_x == 0 and self.delta_y == 0:
            raise ValueError("scroll necesita delta_x o delta_y")
        return self


class KeyInputIn(BaseModel):
    """`POST .../input {tipo: "key", ...}` — reenviado como `input_key`
    (`edecan_companion.actions._input_key`, mismo shape exacto): exactamente
    uno de `texto`/`tecla`, nunca ambos ni ninguno."""

    tipo: Literal["key"]
    texto: str | None = None
    tecla: SpecialKey | None = None
    modifiers: list[KeyModifier] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def _exactly_one_of_texto_or_tecla(self) -> KeyInputIn:
        if (self.texto is None) == (self.tecla is None):
            raise ValueError(
                "Envía exactamente uno de 'texto' o 'tecla' (no ambos, no ninguno)."
            )
        return self


SessionInputIn = Annotated[PointerInputIn | KeyInputIn, Field(discriminator="tipo")]


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------


@router.post(
    "/sessions", status_code=status.HTTP_201_CREATED, dependencies=[Depends(rate_limit)]
)
async def create_session(
    body: SessionCreateIn,
    request: Request,
    current_user: CurrentUser = Depends(_require_remote_view),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Crea una sesión `pending`. Exige companion conectado (`503` si no) —
    la aprobación LOCAL en el companion todavía no ocurrió: pasa recién con
    el primer `GET .../frame` exitoso (ver `get_frame` más abajo).

    `kind="control"` (WP-V4-10) exige ADEMÁS el flag `companion.remote_input`
    — se valida aquí, a mano, en vez de con `Depends(_require_remote_control)`
    porque ese gate solo aplica cuando `body.kind == "control"`: una sesión
    `kind="view"` (el default) nunca debe exigir `companion.remote_input`,
    y una dependencia de FastAPI no puede condicionarse al valor del body ya
    parseado."""
    if body.kind == "control" and not current_user.tenant.flags.get(FLAG_REMOTE_INPUT, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El control remoto (teclado/mouse) no está disponible en tu plan.",
        )

    manager = _get_companion_manager(request)
    if manager is None or not manager.is_connected(current_user.tenant_id):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "No tienes un companion conectado. Empareja uno en /app/ajustes "
                "antes de iniciar una sesión de vista remota."
            ),
        )

    session = await repo.create_remote_session(
        tenant_id=current_user.tenant_id, user_id=current_user.user_id
    )
    if body.kind == "control":
        # `create_remote_session` siempre inserta kind='view' (firma sin
        # tocar, ver `Repo.create_remote_session`) -- se promueve acá con el
        # método nuevo y aditivo `mark_remote_session_kind` (WP-V4-10).
        session = await repo.mark_remote_session_kind(
            tenant_id=current_user.tenant_id, session_id=session["id"], kind="control"
        )

    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="remote.session.requested",
        target=str(session["id"]),
        meta={"kind": session["kind"]},
    )
    return session


@router.get("/sessions", dependencies=[Depends(rate_limit)])
async def list_sessions(
    current_user: CurrentUser = Depends(_require_remote_view),
    repo: Repo = Depends(get_repo),
) -> list[dict[str, Any]]:
    return await repo.list_remote_sessions(tenant_id=current_user.tenant_id)


@router.get("/sessions/{session_id}", dependencies=[Depends(rate_limit)])
async def get_session(
    session_id: uuid.UUID,
    current_user: CurrentUser = Depends(_require_remote_view),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    session = await repo.get_remote_session(
        tenant_id=current_user.tenant_id, session_id=session_id
    )
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Sesión de vista remota no encontrada."
        )
    return session


def _translate_companion_error(error: str) -> HTTPException:
    """Traduce el `error` de `actions.execute` (companion) al `HTTPException`
    correspondiente — ver "Contrato de degradación" en el docstring del módulo."""
    if error.startswith(_ERROR_PREFIX_UNSUPPORTED):
        return HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "El companion de esta máquina no soporta captura de pantalla; actualízalo."
            ),
        )
    if error.startswith(_ERROR_PREFIX_IDE_DISABLED):
        return HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "El companion de esta máquina tiene la captura de pantalla deshabilitada "
                "(ide_enabled: false en su companion.yaml); habilítala ahí para usar la "
                "vista remota."
            ),
        )
    if error.startswith(_ERROR_PREFIX_PLATFORM_UNSUPPORTED):
        return HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "El companion de esta máquina corre en un sistema operativo sin backend "
                "de captura compatible. Usa macOS, Windows o Linux y actualiza el companion."
            ),
        )
    # `_ERROR_PREFIX_DENIED` se maneja aparte por el llamador (necesita marcar
    # la sesión como `denied` y auditar antes de lanzar el 403), así que esta
    # función solo cubre "cualquier otra falla no reconocida" además de las de
    # arriba.
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"El companion no pudo capturar la pantalla: {error or 'error desconocido'}.",
    )


@router.get("/sessions/{session_id}/frame")
async def get_frame(
    session_id: uuid.UUID,
    request: Request,
    quality: Annotated[int, Query(ge=35, le=95)] = 68,
    max_width: Annotated[int, Query(ge=640, le=3840)] = 1600,
    current_user: CurrentUser = Depends(_require_remote_view),
    repo: Repo = Depends(get_repo),
    db_session: AsyncSession = Depends(get_tenant_session),
    redis_client: Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    # NOTA: el parámetro de la sesión SQLAlchemy se llama `db_session`, NUNCA `session` —
    # a propósito, para no colisionar con la variable local `session` de abajo (el dict de
    # `remote_sessions`, mismo nombre que usan `create_session`/`get_session`/`end_session`
    # en este mismo archivo). Antes de este fix ambos se llamaban `session`: el parámetro
    # quedaba sombreado por `session = await repo.get_remote_session(...)` en la siguiente
    # línea, así que el `await session.commit()` de HOTFIXES_PENDIENTES.md punto 8 SIEMPRE
    # operaba sobre el dict (no sobre la `AsyncSession`) y reventaba con
    # `AttributeError: '...' object has no attribute 'commit'` en cuanto se ejercitaba de
    # verdad (ver `test_frame_denied_commits_audit_evidence_before_raising_403`) — el fix
    # nunca llegó a funcionar hasta este rename.
    session = await repo.get_remote_session(
        tenant_id=current_user.tenant_id, session_id=session_id
    )
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Sesión de vista remota no encontrada."
        )
    if session["status"] == "denied":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El usuario denegó esta sesión de vista remota en su companion.",
        )
    if session["status"] == "ended":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Esta sesión ya terminó. Inicia una nueva con POST /v1/remote/sessions.",
        )

    await _check_frame_rate_limit(redis_client, current_user.tenant_id, session_id, settings)

    manager = _get_companion_manager(request)
    if manager is None or not manager.is_connected(current_user.tenant_id):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El companion se desconectó. Vuelve a emparejarlo en /app/ajustes.",
        )

    try:
        resultado = await manager.send_command(
            current_user.tenant_id,
            _SCREENSHOT_ACTION,
            {"format": "jpeg", "quality": quality, "max_width": max_width},
        )
    except CompanionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"El companion no respondió a tiempo: {exc}",
        ) from exc

    ok = isinstance(resultado, dict) and bool(resultado.get("ok"))
    if not ok:
        error = str(resultado.get("error") or "") if isinstance(resultado, dict) else ""
        if error.startswith(_ERROR_PREFIX_DENIED):
            await repo.mark_remote_session_denied(
                tenant_id=current_user.tenant_id, session_id=session_id
            )
            await repo.add_audit_log(
                tenant_id=current_user.tenant_id,
                actor_user_id=current_user.user_id,
                action="remote.session.denied",
                target=str(session_id),
                meta={"error": error},
            )
            # HOTFIXES_PENDIENTES.md punto 8: `get_tenant_session` envuelve TODA la
            # request en una única transacción (`edecan_db.session.get_session`) que hace
            # ROLLBACK automático si una excepción se propaga fuera del handler — y el
            # `HTTPException(403)` de abajo es exactamente esa excepción. Sin este commit
            # explícito, el rollback se llevaría por delante los dos writes de arriba
            # (marca de denegación + audit log), justo la evidencia que más importa
            # conservar cuando un usuario deniega control remoto. `repo` (`SqlRepo`) y
            # `db_session` son la MISMA sesión física: `get_repo` depende de
            # `get_tenant_session`, y FastAPI cachea por request cualquier dependencia que
            # se pida más de una vez con el mismo callable (ver `deps.py`; mismo patrón ya
            # usado en `routers/conversations.py::post_message`/`confirm_tool_call`). Tras
            # este commit, SQLAlchemy autobegin abriría una transacción nueva para lo que
            # quede del request SI algo más volviera a usar `db_session` — pero nada lo
            # hace: este es el ÚLTIMO uso de la sesión en todo el handler, se lanza la
            # excepción justo debajo. Eso importa porque, verificado empíricamente
            # (SQLAlchemy 2.0), intentar CUALQUIER operación nueva sobre esta sesión
            # DESPUÉS de este commit y ANTES de que `edecan_db.session.get_session` haga
            # `__aexit__` revienta con `InvalidRequestError: Can't operate on closed
            # transaction inside context manager` — por eso `commerce.py::confirm_order`
            # (HOTFIXES_PENDIENTES.md punto 9) NO puede replicar este mismo commit
            # inmediatamente después de escribir y seguir usando la sesión para la
            # ejecución paper; ver el docstring de esa función. El commit/rollback
            # implícito de `edecan_db.session.get_session` al salir del `async with
            # session.begin()` exterior, con la transacción ya cerrada por este commit
            # manual, no revienta — es inocuo (ver
            # `test_frame_denied_commits_audit_evidence_before_raising_403` y, si corre con
            # Postgres real, `test_remote_router.py::test_frame_denied_...` marcado
            # `integration`).
            await db_session.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="El usuario denegó esta sesión de vista remota en su companion.",
            )
        raise _translate_companion_error(error)

    result_data = resultado.get("result") if isinstance(resultado, dict) else None
    image_b64 = result_data.get("image_b64") if isinstance(result_data, dict) else None
    width = result_data.get("width") if isinstance(result_data, dict) else None
    height = result_data.get("height") if isinstance(result_data, dict) else None
    mime = result_data.get("mime", "image/png") if isinstance(result_data, dict) else "image/png"
    origin_x = result_data.get("origin_x", 0) if isinstance(result_data, dict) else 0
    origin_y = result_data.get("origin_y", 0) if isinstance(result_data, dict) else 0
    if not isinstance(image_b64, str) or not image_b64 or width is None or height is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="El companion devolvió una captura de pantalla con formato inválido.",
        )

    was_pending = session["status"] == "pending"
    updated = await repo.record_remote_session_frame(
        tenant_id=current_user.tenant_id, session_id=session_id
    )
    if was_pending:
        await repo.add_audit_log(
            tenant_id=current_user.tenant_id,
            actor_user_id=current_user.user_id,
            action="remote.session.started",
            target=str(session_id),
            meta={},
        )

    return {
        "image_b64": image_b64,
        "width": width,
        "height": height,
        "mime": mime if mime in {"image/png", "image/jpeg"} else "image/png",
        "origin_x": origin_x if isinstance(origin_x, int) else 0,
        "origin_y": origin_y if isinstance(origin_y, int) else 0,
        "seq": updated["frames_count"],
    }


@router.post("/sessions/{session_id}/end", dependencies=[Depends(rate_limit)])
async def end_session(
    session_id: uuid.UUID,
    current_user: CurrentUser = Depends(_require_remote_view),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    session = await repo.get_remote_session(
        tenant_id=current_user.tenant_id, session_id=session_id
    )
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Sesión de vista remota no encontrada."
        )

    already_ended = session["status"] == "ended"
    updated = await repo.mark_remote_session_ended(
        tenant_id=current_user.tenant_id, session_id=session_id
    )

    if not already_ended:
        duration_seconds: float | None = None
        if updated["started_at"] is not None and updated["ended_at"] is not None:
            duration_seconds = (updated["ended_at"] - updated["started_at"]).total_seconds()
        await repo.add_audit_log(
            tenant_id=current_user.tenant_id,
            actor_user_id=current_user.user_id,
            action="remote.session.ended",
            target=str(session_id),
            meta={
                "frames_count": updated["frames_count"],
                "duration_seconds": duration_seconds,
            },
        )
    return updated


def _translate_input_companion_error(error: str) -> HTTPException:
    """Traduce el `error` de `actions.execute` (companion) para `send_input`
    — equivalente de `_translate_companion_error` pero para
    `input_pointer`/`input_key` (WP-V4-10, ver "Fase 2" en el docstring del
    módulo). `_ERROR_PREFIX_UNSUPPORTED` se reutiliza tal cual (mismo texto
    exacto que produce `actions.execute` sin importar la acción); el resto
    son mensajes propios de estas dos acciones."""
    if error.startswith(_ERROR_PREFIX_UNSUPPORTED):
        return HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "El companion de esta máquina no soporta control remoto de teclado/mouse; "
                "actualízalo."
            ),
        )
    if error.startswith(_ERROR_PREFIX_INPUT_DISABLED):
        return HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "El companion de esta máquina tiene el control remoto de teclado/mouse "
                "deshabilitado (remote_input_enabled: false en su companion.yaml); "
                "habilítalo ahí para usar el control remoto."
            ),
        )
    if error.startswith(_ERROR_PREFIX_INPUT_PLATFORM_UNSUPPORTED):
        return HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "El companion de esta máquina corre en un sistema operativo sin backend "
                "compatible de teclado/mouse. Usa macOS, Windows o Linux."
            ),
        )
    # `_ERROR_PREFIX_DENIED` se maneja aparte por el llamador (necesita marcar
    # la sesión como `denied` y auditar antes de lanzar el 403) — igual que en
    # `_translate_companion_error`. Cualquier otra falla no reconocida
    # (incluida "falta pyobjc-framework-Quartz" o "sin permiso de
    # Accesibilidad", ambas de `_QuartzInputBackend.__init__`) cae aquí como
    # un 502 con el mensaje del companion tal cual — mismo criterio que
    # `_translate_companion_error` con el permiso de Grabación de pantalla de
    # `screenshot`: es un problema puntual del equipo del cliente, no "esta
    # acción no existe/está apagada", así que no amerita su propio 501.
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=(
            f"El companion no pudo ejecutar la acción de control remoto: "
            f"{error or 'error desconocido'}."
        ),
    )


@router.post("/sessions/{session_id}/input")
async def send_input(
    session_id: uuid.UUID,
    body: SessionInputIn,
    request: Request,
    current_user: CurrentUser = Depends(_require_remote_control),
    repo: Repo = Depends(get_repo),
    db_session: AsyncSession = Depends(get_tenant_session),
    redis_client: Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Reenvía un comando de teclado/mouse al companion de una sesión
    `kind="control"` ya `active` — ver "Fase 2: input remoto" en el docstring
    del módulo para el detalle completo de los 4 candados y los códigos de
    error. `db_session` sigue el MISMO criterio de nombrado que `get_frame`
    (nunca `session`, para no sombrear la variable local de abajo)."""
    session = await repo.get_remote_session(
        tenant_id=current_user.tenant_id, session_id=session_id
    )
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Sesión de vista remota no encontrada."
        )
    if session["kind"] != "control":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Esta sesión no es de control remoto: créala con "
                "POST /v1/remote/sessions {\"kind\": \"control\"} para poder enviar input."
            ),
        )
    if session["status"] == "denied":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El usuario denegó esta sesión de control remoto en su companion.",
        )
    if session["status"] == "ended":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Esta sesión ya terminó. Inicia una nueva con POST /v1/remote/sessions.",
        )
    if session["status"] != "active":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Esta sesión todavía no está activa: pide un frame primero con "
                "GET /v1/remote/sessions/{id}/frame."
            ),
        )

    await _check_input_rate_limit(redis_client, current_user.tenant_id, session_id, settings)

    manager = _get_companion_manager(request)
    if manager is None or not manager.is_connected(current_user.tenant_id):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El companion se desconectó. Vuelve a emparejarlo en /app/ajustes.",
        )

    # `session_id` SIEMPRE viaja en los params (aunque `actions._input_pointer`/
    # `_input_key` no lo lean): es lo que permite que la aprobación "recordada"
    # más dura del companion (`approval._approve_input_action`) quede acotada a
    # ESTA sesión de control -- ver "Fase 2" en el docstring del módulo.
    if isinstance(body, PointerInputIn):
        action = _INPUT_POINTER_ACTION
        params: dict[str, Any] = {
            "session_id": str(session_id),
            "x": body.x,
            "y": body.y,
            "accion": body.accion,
        }
        if body.button is not None:
            params["button"] = body.button
        if body.start_x is not None:
            params["start_x"] = body.start_x
        if body.start_y is not None:
            params["start_y"] = body.start_y
        if body.accion == "scroll":
            params["delta_x"] = body.delta_x
            params["delta_y"] = body.delta_y
        audit_meta: dict[str, Any] = {"tipo": "pointer", "accion": body.accion}
    else:
        action = _INPUT_KEY_ACTION
        params = {"session_id": str(session_id)}
        if body.texto is not None:
            params["texto"] = body.texto
            # NUNCA el texto en claro en el audit_log -- mismo principio que
            # `edecan_companion.audit._REDACTED_KEYS` del lado del companion.
            audit_meta = {"tipo": "key", "clave": "texto", "length": len(body.texto)}
        else:
            params["tecla"] = body.tecla
            if body.modifiers:
                params["modifiers"] = body.modifiers
            audit_meta = {
                "tipo": "key", "clave": "tecla", "tecla": body.tecla,
                "modifiers": body.modifiers,
            }

    try:
        resultado = await manager.send_command(current_user.tenant_id, action, params)
    except CompanionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"El companion no respondió a tiempo: {exc}",
        ) from exc

    ok = isinstance(resultado, dict) and bool(resultado.get("ok"))
    if not ok:
        error = str(resultado.get("error") or "") if isinstance(resultado, dict) else ""
        if error.startswith(_ERROR_PREFIX_DENIED):
            await repo.mark_remote_session_denied(
                tenant_id=current_user.tenant_id, session_id=session_id
            )
            await repo.add_audit_log(
                tenant_id=current_user.tenant_id,
                actor_user_id=current_user.user_id,
                action="remote.session.input_denied",
                target=str(session_id),
                meta={**audit_meta, "error": error},
            )
            # Mismo patrón "commit de evidencia ANTES del raise" que `get_frame`
            # (`HOTFIXES_PENDIENTES.md` punto 8, ver su comentario ahí para el
            # porqué exacto): sin este commit explícito, el rollback automático
            # de `edecan_db.session.get_session` al propagarse el
            # `HTTPException(403)` de abajo se llevaría por delante la marca de
            # denegación y su audit log.
            await db_session.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="El usuario denegó esta acción de control remoto en su companion.",
            )
        raise _translate_input_companion_error(error)

    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="remote.session.input",
        target=str(session_id),
        meta=audit_meta,
    )

    result_data = resultado.get("result") if isinstance(resultado, dict) else None
    return {"ok": True, "result": result_data if isinstance(result_data, dict) else None}
