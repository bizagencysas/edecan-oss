"""`/v1/voz/*` — voces del tenant y clonación autorizada (WP-V5-10;
`ROADMAP_V2.md` §6.3: "clonación de voz SOLO con consentimiento grabado y
verificado"; `docs/voz-telefonia.md` sección "Voces y clonación autorizada").

Este router NO se monta a sí mismo: `edecan_api.main` (WP-V5-01) lo monta de
forma defensiva, igual que el resto de routers v2/v3/v4
(`importlib.import_module` + `try/except ImportError`) — este módulo solo
declara `router`.

## `GET /voces` — reutiliza la credencial de voz TTS ya existente

NO existe una credencial nueva para esto: reutiliza `PUT
/v1/credentials/voice/tts` (`connector_key="voice_tts"`,
`apps/api/edecan_api/routers/credentials.py`) — si esa credencial es
`provider="elevenlabs"`, se listan las voces reales de esa cuenta (de stock +
clones ya creados); si no (Polly, o nada conectado), se devuelven 2 voces de
ejemplo offline (mismo criterio "tenant → stub, SIN paso de plataforma" que
`edecan_voice.tenant`/`apps/api/edecan_api/routers/voice.py`).

## `POST /clones` — clonación con consentimiento verificable

**El agente JAMÁS clona una voz** — ninguna `Tool` de `edecan_voice.tools`
(ni de ningún otro paquete) expone una acción de clonación; ver el docstring
de `edecan_voice.cloning`. Este endpoint es la ÚNICA vía de clonación de todo
el producto, y exige un humano presente en la UI:

1. Gate de plan: flag `voice.cloning` (`_FLAG_VOICE_CLONING` — ver nota de
   flags abajo). `403` si el plan no lo trae.
2. `attestation` debe venir literalmente como el string `"true"` — es la
   declaración explícita de que el usuario tiene el consentimiento grabado y
   verificado de la persona cuya voz va a clonar (texto exacto en la UI, ver
   `apps/web/src/app/(app)/app/voz/page.tsx`). Cualquier otro valor (u
   omitirlo) → `400` explicando el requisito legal.
3. `consentimiento` (un archivo de audio con la GRABACIÓN de esa persona
   dando su consentimiento) es OBLIGATORIO — sin él, `400`. Es la evidencia:
   se sube a S3 + fila `files` ANTES de tocar ElevenLabs.
4. `muestras`: 1 a 5 archivos de audio de la voz a clonar — fuera de ese
   rango, `400`.
5. Se sube `consentimiento` (S3 + `files`, `repo.create_file`, MISMO patrón
   que `routers/files.py`) y se inserta la fila en `voice_consents`
   (`attestation=true, status='attested'`) **ANTES** de intentar nada contra
   ElevenLabs — la evidencia del consentimiento queda registrada aunque el
   paso técnico de clonar falle después (p. ej. porque el tenant todavía no
   conectó ElevenLabs): nunca se pierde el registro de que la persona
   consintió, incluso si el clon técnico no se llegó a crear.
6. Recién ahí se valida que la credencial `voice_tts` del tenant sea
   `elevenlabs` — si no, `400` "conecta ElevenLabs en Configuración primero"
   (la fila de `voice_consents` ya creada en el paso 5 queda con
   `provider_voice_id=NULL`; el tenant puede repetir la clonación una vez
   conecte ElevenLabs, con una nueva grabación o reutilizando la misma si
   vuelve a subirla — no hay hoy un endpoint de "reintentar" sobre una fila
   existente, limitación conocida documentada en `docs/voz-telefonia.md`).
7. `edecan_voice.cloning.crear_clon` con la `api_key` DEL TENANT. Si
   ElevenLabs rechaza, `400` con el detalle exacto (sin filtrar la key, ver
   `edecan_voice.cloning`).
8. Se guarda `provider_voice_id` en la fila y se responde `201`.

`DELETE /clones/{id}` borra el clon en ElevenLabs en modo BEST-EFFORT
(`edecan_voice.cloning.borrar_clon`, con `try/except` — un error ahí NUNCA
bloquea la revocación local) y marca `status='revoked'`. **La fila de
`voice_consents` NUNCA se borra** — es evidencia legal de que hubo una
declaración de consentimiento, no una simple referencia técnica al clon.

## Flag `voice.cloning`

`_FLAG_VOICE_CLONING = "voice.cloning"` se usa aquí como string local, NO
importado de `edecan_schemas.plans.FLAG_VOICE_CLONING`. La coordinación con
WP-V5-01 sobre esto ya cerró: esa constante ya existe
(`packages/schemas/edecan_schemas/plans.py`) y ya tiene su fila en la matriz
de `PLANES` (`ARCHITECTURE.md` §10.13) — `True` en `free_selfhost`,
`hosted_pro` y `hosted_business`, `False` en `hosted_basic`. Este router
sigue sin importarla, mismo criterio que ya usa este mismo repo con
dependencias disponibles pero no usadas por consistencia
(`edecan_ads.tools._FLAG_ADS`, `edecan_creative.tools.GenerarImagenTool` con
el literal `"tools.images"`): si el string local alguna vez se
desincronizara del real, `tenant.flags.get("voice.cloning", False)` sigue
siendo `False` para los planes sin ese flag — fail-closed por diseño (nadie
puede clonar hasta que un plan lo habilite explícitamente), nunca fail-open.

## Tabla `voice_consents`

La coordinación con WP-V5-01 sobre esta tabla también ya cerró: existe desde
la migración `packages/db/alembic/versions/0007_v5_expansion.py` y tiene
modelo `edecan_db.models.VoiceConsent`. Esquema real (`ARCHITECTURE.md`
§14), con dos precisiones sobre lo que este docstring documentaba antes como
"esquema asumido" — `consent_file_id` es NULLABLE y SIN FK (referencia
informativa a `files.id`, no forzada en base de datos, `files` =
`edecan_db.models.File`) en vez de `NOT NULL REFERENCES files(id)`; y existe
además una columna `meta jsonb NOT NULL DEFAULT '{}'` que no se mencionaba:

```
voice_consents(
    id UUID PK, tenant_id UUID NOT NULL, user_id UUID NOT NULL,
    voice_name TEXT NOT NULL, attestation BOOLEAN NOT NULL DEFAULT false,
    status TEXT NOT NULL DEFAULT 'attested',  -- 'attested' | 'revoked'
    consent_file_id UUID NULL,  -- SIN FK, referencia informativa a files.id
    provider_voice_id TEXT NULL,
    meta JSONB NOT NULL DEFAULT '{}',  -- no usada por las queries de abajo
    created_at timestamptz, updated_at timestamptz
)
```

Este router accede a `voice_consents` con SQL parametrizado
(`sqlalchemy.text`) sobre `get_tenant_session` (RLS ya activo) — mismo estilo
que `routers/erp.py`/`edecan_business.inventory` para tablas que se acceden
por SQL crudo en vez del modelo ORM. A diferencia de cuando se escribió este
router, `voice_consents` ya tiene modelo SQLAlchemy
(`edecan_db.models.VoiceConsent`); este archivo no lo usa y sigue accediendo
por SQL parametrizado directo. Las columnas que tocan las funciones
`_insertar_voice_consent`/`_actualizar_provider_voice_id`/
`_listar_voice_consents`/`_obtener_voice_consent`/`_revocar_voice_consent` de
este archivo ya están verificadas contra el esquema real de arriba —
`_insertar_voice_consent` siempre provee `consent_file_id` explícito (la
divergencia de nullability no le afecta) y ninguna de las funciones nombra
`meta` en sus `INSERT`/`UPDATE` (Postgres aplica el `DEFAULT`). Los tests de
este módulo (`test_voz_avanzada.py`) usan una sesión falsa (`FakeSession`,
mismo patrón que `test_erp_router.py`) y NO dependen de que la tabla exista
de verdad — corren offline sin Postgres.

## Podcasts (WP-V6-04)

Vertical completo de podcasts: antes de este WP, `crear_podcast`
(`edecan_creative.tools.CrearPodcastTool`) era la ÚNICA forma de generar uno
— una tool de chat que encola `generate_podcast` directo, sin ninguna fila
que el usuario pudiera consultar desde la UI (`docs/api.md` lo documentaba
como pendiente). Este WP agrega el camino HTTP/UI completo, apoyado en una
tabla `podcasts` nueva (ver "Tabla `podcasts`" abajo) que ambos caminos
— esta ruta y la tool de chat, vía `apps/worker/edecan_worker/handlers/
generate_podcast.py` — terminan compartiendo:

- `POST /podcasts` `{titulo: str, guion: [{texto: str, voz?: str}]}` — gate
  `_require_tools_podcast` (flag `tools.podcast`, patrón EXACTO de
  `_require_voice_cloning` de arriba: la dependencia de la ACCIÓN, no la del
  grupo). Valida el guion con `edecan_creative.podcast.validar_guion` (mismo
  módulo que ya usa la tool de chat — traduce `voz` → `voice_id` antes de
  llamarla, para no duplicar las reglas de longitud/cantidad de segmentos),
  inserta la fila (`status='pending'`) y encola `generate_podcast` con
  `{"podcast_id": str(id)}` — `201` con la fila creada (pinned en
  `ARCHITECTURE.md` §15.e, aterrizó durante este WP: NO `202`, aunque la
  síntesis en sí sea asíncrona — mismo criterio que `POST /v1/files`/`POST
  /v1/voz/clones`, que también responden `201` aunque encolen o disparen
  trabajo async). A diferencia de `POST /clones` (arriba), esta ruta NO
  necesita el patrón commit-antes-de-raise de `HOTFIXES_PENDIENTES.md`:
  `podcasts` es un registro operativo, no evidencia legal/de cumplimiento (a
  diferencia de `voice_consents`), así que si `enqueue()` falla después del
  INSERT, dejar que el rollback automático de `get_tenant_session` se lleve
  la fila junto con el job que nunca se encoló es el comportamiento correcto
  (todo o nada) — nunca queda una fila `'pending'` huérfana que ningún job
  vaya a procesar jamás.
- `GET /podcasts` — lista del tenant, orden `created_at DESC`.
- `GET /podcasts/{id}` — una fila (incluye `file_id` cuando `status='done'`);
  `404` si no existe o no es del tenant.

El worker (`generate_podcast.py`) es quien de verdad sintetiza/ensambla y
marca `status`/`file_id`/`error` — ver el docstring de ese módulo.

## Tabla `podcasts` — `ARCHITECTURE.md` §15.b, migración `0008_v6_expansion`

Este router se escribió inicialmente contra un esquema asumido (mismo
criterio que `voice_consents` en v5: `packages/db` no está en las rutas que
este WP puede tocar) mientras el linchpin de v6 (WP-V6-01) corría en
paralelo — aterrizó durante la ejecución de este mismo WP, con una única
diferencia real respecto a lo asumido: `guion` es NULLABLE, sin default
(no `NOT NULL DEFAULT '[]'`) — inofensivo para este router, que SIEMPRE
provee un valor explícito en el `INSERT` (nunca depende del default de la
columna); `_guion_desde_jsonb` de abajo ya trata `None` como `[]` de todas
formas. Esquema real (`packages/db/alembic/versions/0008_v6_expansion.py`):

```
podcasts(
    id UUID PK, tenant_id UUID NOT NULL, user_id UUID NOT NULL,
    titulo TEXT NOT NULL,
    guion JSONB NULL,  -- [{"texto": str, "voz": str|null}, ...] normalmente,
                        -- pero NULLABLE a nivel de esquema
    status TEXT NOT NULL DEFAULT 'pending',  -- CHECK 'pending'|'running'|'done'|'error'
    file_id UUID NULL,  -- SIN FK, referencia informativa a files.id (igual
                         -- criterio que voice_consents.consent_file_id)
    error TEXT NULL,
    created_at timestamptz, updated_at timestamptz
)
```

Los cuatro valores literales de `status` no se importan de ningún módulo
compartido (mismo criterio que `_FLAG_VOICE_CLONING` arriba): están
duplicados entre este router y `generate_podcast.py` a propósito. Los tests
de este módulo (`test_podcasts_router.py`) usan `FakeSession` — no dependen
de que la tabla exista de verdad (y siguieron en verde sin cambios tras
converger contra el esquema real).
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import aioboto3
from edecan_core.queue import enqueue
from edecan_creative.podcast import GuionInvalidoError, validar_guion
from edecan_db.vault import TokenVault
from edecan_schemas.plans import FLAG_VOICE_WEB
from edecan_voice import cloning
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.config import Settings, get_settings
from edecan_api.deps import (
    VOICE_TTS_CONNECTOR_KEY,
    CurrentUser,
    get_current_user,
    get_repo,
    get_tenant_session,
    get_vault,
    rate_limit,
)
from edecan_api.repo import Repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/voz", tags=["voz"], dependencies=[Depends(rate_limit)])

# Ver docstring del módulo ("Flag voice.cloning").
_FLAG_VOICE_CLONING = "voice.cloning"

# Ver docstring del módulo ("Tabla podcasts"): mismo criterio de string local
# (no importado de `edecan_schemas.plans.FLAG_TOOLS_PODCAST`, que sí existe)
# que `_FLAG_VOICE_CLONING` — fail-closed si algún día se desincroniza.
_FLAG_TOOLS_PODCAST = "tools.podcast"

_MAX_MUESTRAS = 5
_MIN_MUESTRAS = 1

# Dos voces de ejemplo offline, mismas que `edecan_voice.tools.VOCES_STUB`
# (duplicadas a propósito: este router no importa `edecan_voice.tools`, un
# módulo de herramientas del agente, no la superficie pública del paquete —
# `ARCHITECTURE.md` §10.1, mismo criterio de duplicación deliberada que ya
# usa el resto del repo entre paquetes/routers hermanos).
_VOCES_STUB: tuple[cloning.VozDisponible, ...] = (
    cloning.VozDisponible(
        voice_id="stub-voz-neutral", nombre="Voz neutral (offline)", categoria="premade"
    ),
    cloning.VozDisponible(
        voice_id="stub-voz-calida", nombre="Voz cálida (offline)", categoria="premade"
    ),
)


# ---------------------------------------------------------------------------
# Gates de flag de plan
# ---------------------------------------------------------------------------


async def _require_voice_web(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    if not current_user.tenant.flags.get(FLAG_VOICE_WEB, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La voz web no está disponible en tu plan.",
        )
    return current_user


async def _require_voice_cloning(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    if not current_user.tenant.flags.get(_FLAG_VOICE_CLONING, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La clonación de voz no está disponible en tu plan.",
        )
    return current_user


async def _require_tools_podcast(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    if not current_user.tenant.flags.get(_FLAG_TOOLS_PODCAST, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Los podcasts no están disponibles en tu plan.",
        )
    return current_user


# ---------------------------------------------------------------------------
# Credencial de voz (TTS) del tenant — reutiliza `PUT /v1/credentials/voice/tts`
# (ver docstring del módulo), duplicado del helper equivalente de
# `routers/voice.py::_read_tenant_voice_config` (paquetes/routers hermanos no
# se importan entre sí en este repo, ver `routers/erp.py`/`routers/vehiculos.py`).
# ---------------------------------------------------------------------------


async def _tts_config_del_tenant(
    repo: Repo, vault: TokenVault, tenant_id: uuid.UUID
) -> dict[str, Any] | None:
    accounts = await repo.list_connector_accounts(tenant_id=tenant_id)
    account = next((a for a in accounts if a["connector_key"] == VOICE_TTS_CONNECTOR_KEY), None)
    if account is None:
        return None
    bundle = await vault.get(tenant_id, account["id"])
    if bundle is None:
        return None
    try:
        data = json.loads(bundle.access_token)
    except (TypeError, ValueError):
        logger.warning("Config de voz (TTS) ilegible en el vault (tenant_id=%s).", tenant_id)
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# Subida de la grabación de consentimiento — mismo layout que `routers/files.py`
# (S3 + `repo.create_file`), duplicado localmente (ver docstring del módulo).
# ---------------------------------------------------------------------------


async def _subir_archivo(
    repo: Repo,
    settings: Settings,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    data: bytes,
    filename: str,
    mime: str,
) -> uuid.UUID:
    file_id = uuid.uuid4()
    s3_key = f"tenants/{tenant_id}/files/{file_id}/{filename}"

    aioboto3_session = aioboto3.Session()
    async with aioboto3_session.client(
        "s3", region_name=settings.AWS_REGION, endpoint_url=settings.AWS_ENDPOINT_URL
    ) as s3:
        await s3.put_object(Bucket=settings.S3_BUCKET, Key=s3_key, Body=data, ContentType=mime)

    row = await repo.create_file(
        tenant_id=tenant_id,
        user_id=user_id,
        s3_key=s3_key,
        filename=filename,
        mime=mime,
        size_bytes=len(data),
        status="ready",
        file_id=file_id,
    )
    return row["id"]


# ---------------------------------------------------------------------------
# `voice_consents` — SQL parametrizado (ver docstring del módulo, "Tabla
# voice_consents").
# ---------------------------------------------------------------------------


async def _insertar_voice_consent(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    voice_name: str,
    consent_file_id: uuid.UUID,
) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                "INSERT INTO voice_consents "
                "(tenant_id, user_id, voice_name, attestation, status, consent_file_id) "
                "VALUES (:tenant_id ::uuid, :user_id ::uuid, :voice_name, true, 'attested', "
                ":consent_file_id ::uuid) RETURNING *"
            ),
            {
                "tenant_id": str(tenant_id),
                "user_id": str(user_id),
                "voice_name": voice_name,
                "consent_file_id": str(consent_file_id),
            },
        )
    ).mappings().first()
    await session.flush()
    if row is None:  # defensivo: Postgres no devolvió la fila recién insertada.
        raise RuntimeError("No se pudo registrar el consentimiento de voz.")
    return dict(row)


async def _actualizar_provider_voice_id(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    consent_id: uuid.UUID,
    provider_voice_id: str,
) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                "UPDATE voice_consents SET provider_voice_id = :provider_voice_id, "
                "updated_at = now() WHERE id = :id ::uuid AND tenant_id = :tenant_id ::uuid "
                "RETURNING *"
            ),
            {
                "provider_voice_id": provider_voice_id,
                "id": str(consent_id),
                "tenant_id": str(tenant_id),
            },
        )
    ).mappings().first()
    await session.flush()
    if row is None:  # defensivo: la fila desapareció entre el INSERT y este UPDATE.
        raise RuntimeError("No se pudo actualizar el clon de voz recién creado.")
    return dict(row)


async def _listar_voice_consents(
    session: AsyncSession, *, tenant_id: uuid.UUID
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                "SELECT * FROM voice_consents WHERE tenant_id = :tenant_id ::uuid "
                "ORDER BY created_at DESC"
            ),
            {"tenant_id": str(tenant_id)},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


async def _obtener_voice_consent(
    session: AsyncSession, *, tenant_id: uuid.UUID, consent_id: uuid.UUID
) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                "SELECT * FROM voice_consents WHERE id = :id ::uuid "
                "AND tenant_id = :tenant_id ::uuid"
            ),
            {"id": str(consent_id), "tenant_id": str(tenant_id)},
        )
    ).mappings().first()
    return dict(row) if row is not None else None


async def _revocar_voice_consent(
    session: AsyncSession, *, tenant_id: uuid.UUID, consent_id: uuid.UUID
) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                "UPDATE voice_consents SET status = 'revoked', updated_at = now() "
                "WHERE id = :id ::uuid AND tenant_id = :tenant_id ::uuid RETURNING *"
            ),
            {"id": str(consent_id), "tenant_id": str(tenant_id)},
        )
    ).mappings().first()
    await session.flush()
    return dict(row) if row is not None else None


def _voz_a_dict(voz: cloning.VozDisponible) -> dict[str, Any]:
    return {
        "voice_id": voz.voice_id,
        "nombre": voz.nombre,
        "categoria": voz.categoria,
        "preview_url": voz.preview_url,
    }


# ---------------------------------------------------------------------------
# GET /v1/voz/voces
# ---------------------------------------------------------------------------


@router.get("/voces")
async def listar_voces_endpoint(
    current_user: CurrentUser = Depends(_require_voice_web),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
) -> list[dict[str, Any]]:
    config = await _tts_config_del_tenant(repo, vault, current_user.tenant_id)

    if config is not None and config.get("provider") == "elevenlabs" and config.get("api_key"):
        try:
            voces = await cloning.listar_voces(config["api_key"])
        except cloning.VoiceCloningError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    else:
        voces = list(_VOCES_STUB)

    return [_voz_a_dict(v) for v in voces]


# ---------------------------------------------------------------------------
# POST /v1/voz/clones — ver docstring del módulo.
# ---------------------------------------------------------------------------


@router.post("/clones", status_code=status.HTTP_201_CREATED)
async def crear_clon_voz(
    nombre: str = Form(...),
    attestation: str | None = Form(default=None),
    descripcion: str | None = Form(default=None),
    consentimiento: UploadFile | None = File(default=None),
    muestras: list[UploadFile] | None = File(default=None),
    current_user: CurrentUser = Depends(_require_voice_cloning),
    repo: Repo = Depends(get_repo),
    session: AsyncSession = Depends(get_tenant_session),
    vault: TokenVault = Depends(get_vault),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    nombre = nombre.strip()
    if not nombre:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="nombre es obligatorio.")

    if attestation != "true":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                "Debes declarar explícitamente (attestation=true) que tienes el "
                "consentimiento explícito y grabado de la persona cuya voz vas a clonar."
            ),
        )
    if consentimiento is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                "Falta el archivo de audio con la grabación del consentimiento de la "
                "persona — es obligatorio para clonar una voz."
            ),
        )
    muestras = muestras or []
    if not (_MIN_MUESTRAS <= len(muestras) <= _MAX_MUESTRAS):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Necesitas entre {_MIN_MUESTRAS} y {_MAX_MUESTRAS} muestras de audio de "
                "la voz a clonar."
            ),
        )

    # Paso 5 del docstring del módulo: la evidencia del consentimiento se
    # sube y se registra ANTES de tocar ElevenLabs.
    consentimiento_bytes = await consentimiento.read()
    consent_file_id = await _subir_archivo(
        repo,
        settings,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        data=consentimiento_bytes,
        filename=consentimiento.filename or "consentimiento.mp3",
        mime=consentimiento.content_type or "audio/mpeg",
    )
    registro = await _insertar_voice_consent(
        session,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        voice_name=nombre,
        consent_file_id=consent_file_id,
    )

    tts_config = await _tts_config_del_tenant(repo, vault, current_user.tenant_id)
    if (
        tts_config is None
        or tts_config.get("provider") != "elevenlabs"
        or not tts_config.get("api_key")
    ):
        # HOTFIXES_PENDIENTES.md punto 8/9 (mismo patrón que
        # `routers/remote.py::get_frame`/`routers/commerce.py::confirm_order`):
        # `get_tenant_session` envuelve TODA la request en una única transacción
        # con ROLLBACK automático ante cualquier excepción. Sin este commit
        # explícito, el `HTTPException` de abajo se llevaría por delante el
        # INSERT de `voice_consents` de arriba (y el de `files` de
        # `_subir_archivo`, misma sesión vía `repo`) — justo la evidencia de
        # consentimiento que el paso 5 del docstring del módulo promete
        # conservar aunque la clonación técnica falle. Es la ÚLTIMA operación
        # de sesión de esta rama (nada la vuelve a tocar antes del `raise`):
        # comitear y seguir usando la misma sesión revienta con
        # `InvalidRequestError: Can't operate on closed transaction inside
        # context manager` (verificado empíricamente en los puntos 8/9).
        await session.commit()
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                "Conecta ElevenLabs en Configuración primero "
                "(PUT /v1/credentials/voice/tts) para poder clonar una voz."
            ),
        )

    muestras_voz = [
        cloning.MuestraVoz(
            data=await muestra.read(),
            filename=muestra.filename or "muestra.mp3",
            content_type=muestra.content_type or "audio/mpeg",
        )
        for muestra in muestras
    ]

    try:
        provider_voice_id = await cloning.crear_clon(
            tts_config["api_key"], nombre, muestras_voz, descripcion
        )
    except cloning.VoiceCloningError as exc:
        # Mismo criterio que el bloque de arriba (HOTFIXES_PENDIENTES.md punto
        # 8/9): tampoco esta rama vuelve a tocar `session` antes del `raise`,
        # así que es seguro comitear aquí la misma evidencia ya insertada.
        await session.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    registro = await _actualizar_provider_voice_id(
        session,
        tenant_id=current_user.tenant_id,
        consent_id=registro["id"],
        provider_voice_id=provider_voice_id,
    )

    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="voz.clon.creado",
        target=str(registro["id"]),
        meta={"voice_name": nombre},
    )
    return registro


# ---------------------------------------------------------------------------
# GET /v1/voz/clones
# ---------------------------------------------------------------------------


@router.get("/clones")
async def listar_clones_voz(
    current_user: CurrentUser = Depends(_require_voice_cloning),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[dict[str, Any]]:
    return await _listar_voice_consents(session, tenant_id=current_user.tenant_id)


# ---------------------------------------------------------------------------
# DELETE /v1/voz/clones/{clon_id} — ver docstring del módulo.
# ---------------------------------------------------------------------------


@router.delete("/clones/{clon_id}")
async def revocar_clon_voz(
    clon_id: uuid.UUID,
    current_user: CurrentUser = Depends(_require_voice_cloning),
    repo: Repo = Depends(get_repo),
    session: AsyncSession = Depends(get_tenant_session),
    vault: TokenVault = Depends(get_vault),
) -> dict[str, Any]:
    registro = await _obtener_voice_consent(
        session, tenant_id=current_user.tenant_id, consent_id=clon_id
    )
    if registro is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="No encontramos ese clon de voz."
        )

    provider_voice_id = registro.get("provider_voice_id")
    if provider_voice_id:
        tts_config = await _tts_config_del_tenant(repo, vault, current_user.tenant_id)
        if (
            tts_config is not None
            and tts_config.get("provider") == "elevenlabs"
            and tts_config.get("api_key")
        ):
            try:
                await cloning.borrar_clon(tts_config["api_key"], provider_voice_id)
            except cloning.VoiceCloningError as exc:
                # Best-effort (ver docstring del módulo): un fallo acá NUNCA
                # bloquea la revocación local — el registro de consentimiento
                # sigue siendo evidencia válida aunque el clon técnico en
                # ElevenLabs no se haya podido borrar todavía.
                logger.warning(
                    "No se pudo borrar el clon %s en ElevenLabs (se revoca igual "
                    "localmente): %s",
                    provider_voice_id,
                    exc,
                )

    actualizado = await _revocar_voice_consent(
        session, tenant_id=current_user.tenant_id, consent_id=clon_id
    )
    if actualizado is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="No encontramos ese clon de voz."
        )

    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="voz.clon.revocado",
        target=str(clon_id),
        meta={"voice_name": actualizado.get("voice_name")},
    )
    return actualizado


# ---------------------------------------------------------------------------
# Podcasts — ver docstring del módulo ("Podcasts", "Tabla podcasts").
# ---------------------------------------------------------------------------


class SegmentoGuionIn(BaseModel):
    """Un segmento del guion de un podcast. Mismo concepto que
    `edecan_creative.podcast.SegmentoPodcast`, con los nombres de campo que
    expone esta API REST (`voz` en vez de `voice_id`; sin `orador` — los
    podcasts creados desde aquí no distinguen oradores, a diferencia de la
    tool de chat `crear_podcast`, que sí lo acepta)."""

    texto: str
    voz: str | None = None


class PodcastIn(BaseModel):
    titulo: str
    guion: list[SegmentoGuionIn]


def _guion_desde_jsonb(value: Any) -> list[dict[str, Any]]:
    """`guion` puede llegar como `list` ya decodificada o como texto JSON
    crudo según el driver — mismo criterio defensivo que
    `edecan_api.routers.ads._from_jsonb`/`edecan_api.routers.commerce._from_jsonb`,
    duplicado aquí (variante lista, no dict)."""
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        try:
            cargado = json.loads(value)
        except (TypeError, ValueError):
            return []
        return cargado if isinstance(cargado, list) else []
    return []


def _podcast_a_dict(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["guion"] = _guion_desde_jsonb(out.get("guion"))
    return out


async def _insertar_podcast(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    titulo: str,
    guion: list[dict[str, Any]],
) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                "INSERT INTO podcasts (tenant_id, user_id, titulo, guion, status) "
                "VALUES (:tenant_id ::uuid, :user_id ::uuid, :titulo, "
                "CAST(:guion AS jsonb), 'pending') RETURNING *"
            ),
            {
                "tenant_id": str(tenant_id),
                "user_id": str(user_id),
                "titulo": titulo,
                "guion": json.dumps(guion),
            },
        )
    ).mappings().first()
    await session.flush()
    if row is None:  # defensivo: Postgres no devolvió la fila recién insertada.
        raise RuntimeError("No se pudo crear el podcast.")
    return dict(row)


async def _listar_podcasts(session: AsyncSession, *, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                "SELECT * FROM podcasts WHERE tenant_id = :tenant_id ::uuid "
                "ORDER BY created_at DESC"
            ),
            {"tenant_id": str(tenant_id)},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


async def _obtener_podcast(
    session: AsyncSession, *, tenant_id: uuid.UUID, podcast_id: uuid.UUID
) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text("SELECT * FROM podcasts WHERE id = :id ::uuid AND tenant_id = :tenant_id ::uuid"),
            {"id": str(podcast_id), "tenant_id": str(tenant_id)},
        )
    ).mappings().first()
    return dict(row) if row is not None else None


@router.post("/podcasts", status_code=status.HTTP_201_CREATED)
async def crear_podcast_endpoint(
    body: PodcastIn,
    current_user: CurrentUser = Depends(_require_tools_podcast),
    session: AsyncSession = Depends(get_tenant_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    titulo = body.titulo.strip()
    if not titulo:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="titulo es obligatorio.")

    try:
        segmentos = validar_guion([{"texto": seg.texto, "voice_id": seg.voz} for seg in body.guion])
    except GuionInvalidoError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    guion_normalizado = [{"texto": s.texto, "voz": s.voice_id} for s in segmentos]
    registro = await _insertar_podcast(
        session,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        titulo=titulo,
        guion=guion_normalizado,
    )
    await enqueue(
        settings, "generate_podcast", {"podcast_id": str(registro["id"])}, current_user.tenant_id
    )

    logger.info(
        "crear_podcast_endpoint: encolado podcast_id=%s tenant=%s segmentos=%d",
        registro["id"],
        current_user.tenant_id,
        len(segmentos),
    )
    return _podcast_a_dict(registro)


@router.get("/podcasts")
async def listar_podcasts_endpoint(
    current_user: CurrentUser = Depends(_require_tools_podcast),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[dict[str, Any]]:
    rows = await _listar_podcasts(session, tenant_id=current_user.tenant_id)
    return [_podcast_a_dict(r) for r in rows]


@router.get("/podcasts/{podcast_id}")
async def obtener_podcast_endpoint(
    podcast_id: uuid.UUID,
    current_user: CurrentUser = Depends(_require_tools_podcast),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    registro = await _obtener_podcast(
        session, tenant_id=current_user.tenant_id, podcast_id=podcast_id
    )
    if registro is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No encontramos ese podcast.")
    return _podcast_a_dict(registro)
