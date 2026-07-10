"""Resolución STT bring-your-own **del tenant** — "tenant → stub", SIN paso
intermedio de plataforma (`ARCHITECTURE.md` §15, WP-V6-05;
`DIRECCION_ACTUAL.md` "Modelo de credenciales: TODO lo trae el cliente,
siempre").

## Por qué este módulo NO delega en `edecan_voice.tenant`

`packages/voice/edecan_voice/tenant.py` (leído, no tocado — fuera de las
rutas de este work package) solo expone la mitad **TTS** de esta resolución
(`resolver_tts_del_tenant`/`resolver_config_tts_del_tenant`): la
consolidación de WP-V6-04 (ver el docstring de
`edecan_creative.podcast`) solo tocó `resolver_config_tts_tenant`/
`_elevenlabs_sound_generation`, nunca STT. El ÚNICO lugar del repo con
resolución STT-por-tenant real es
`apps/api/edecan_api/routers/voice.py::_stt_para_tenant` — vive en
`apps/api`, un deployable que `apps/worker` no importa (`ARCHITECTURE.md`
§10.1: apps hermanas no se importan entre sí). Este módulo REPLICA ese mismo
patrón exacto (misma query SQL sobre `connector_accounts`, mismo
`connector_key="voice_stt"`, mismo criterio "provider == 'deepgram' +
api_key presente ⇒ `DeepgramSTT`, si no ⇒ `StubSTT`"), adaptado a la firma
`(session, vault, tenant_id)` que usa un handler del worker en vez del
`ToolContext`/`Repo` que usa un router HTTP — mismo criterio de adaptación
que `edecan_creative.podcast.resolver_config_tts_tenant` (que tampoco recibe
un `ToolContext`, los handlers del worker no tienen uno).

`VOICE_STT_CONNECTOR_KEY = "voice_stt"` es el mismo string EXACTO que
`apps/api/edecan_api/deps.VOICE_STT_CONNECTOR_KEY` /
`apps/api/edecan_api/routers/voice.py` (duplicado a propósito entre paquetes
hermanos, mismo criterio que `edecan_voice.tenant.VOICE_TTS_CONNECTOR_KEY`
duplica `apps/api/edecan_api/deps.VOICE_TTS_CONNECTOR_KEY`) — la config que
lee este módulo es la que ya guarda `PUT /v1/credentials/voice/stt`
(`apps/api/edecan_api/routers/credentials.py`, leído, no tocado): JSON
`{"provider": "deepgram", "api_key": "..."}`.

## Fail-closed (patrón anti-fuga v4/v5)

Sin credencial propia del tenant (o si cualquier paso de la resolución
falla — vault caído, JSON corrupto, fila a medias), cae SIEMPRE a `StubSTT`
— NUNCA lee `DEEPGRAM_API_KEY`/`VOICE_STT_PROVIDER` de `Settings`/`.env` de
PLATAFORMA como fallback. `packages/llm/edecan_llm/router.py::_build_provider_from_config`
(ver `ARCHITECTURE.md` §13, hallazgo #1 de v4) es la referencia exacta de
qué NO hacer: un campo vacío/config incompleta del tenant nunca se completa
con una credencial de plataforma.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from edecan_voice.base import STTProvider
from edecan_voice.deepgram import DeepgramSTT
from edecan_voice.stubs import StubSTT
from sqlalchemy import text as sql_text

logger = logging.getLogger(__name__)

# Ver docstring del módulo — mismo string EXACTO que
# `apps/api/edecan_api/deps.VOICE_STT_CONNECTOR_KEY`.
VOICE_STT_CONNECTOR_KEY = "voice_stt"


async def resolver_config_stt_del_tenant(
    *, session: Any, vault: Any, tenant_id: UUID
) -> dict[str, Any] | None:
    """Config de STT del tenant ya descifrada + parseada (mismo shape que
    guarda `PUT /v1/credentials/voice/stt`), o `None` si el tenant no
    conectó nada ahí (o si cualquier paso de la resolución falla — nunca
    lanza, deja constancia con `logger.warning`).
    """
    try:
        row = (
            await session.execute(
                sql_text(
                    "SELECT id FROM connector_accounts WHERE tenant_id = :tenant_id "
                    "AND connector_key = :connector_key ORDER BY created_at DESC LIMIT 1"
                ),
                {"tenant_id": tenant_id, "connector_key": VOICE_STT_CONNECTOR_KEY},
            )
        ).mappings().first()
        if row is None:
            return None

        bundle = await vault.get(tenant_id=tenant_id, connector_account_id=row["id"])
        if bundle is None:
            return None

        data = json.loads(bundle.access_token)
        return data if isinstance(data, dict) else None
    except Exception:
        logger.warning(
            "No se pudo leer la credencial de voz (STT) del tenant_id=%s; se trata "
            "como sin conectar.",
            tenant_id,
            exc_info=True,
        )
        return None


async def resolver_stt_del_tenant(*, session: Any, vault: Any, tenant_id: UUID) -> STTProvider:
    """`STTProvider` bring-your-own del tenant (ver docstring del módulo).
    NUNCA construye `DeepgramSTT` con una clave de PLATAFORMA: sin
    credencial utilizable, cae a `StubSTT` con `logger.warning` accionable.
    """
    data = await resolver_config_stt_del_tenant(session=session, vault=vault, tenant_id=tenant_id)
    if data is not None and data.get("provider") == "deepgram" and data.get("api_key"):
        return DeepgramSTT(api_key=data["api_key"])

    logger.warning(
        "tenant_id=%s no tiene una credencial de voz (STT) propia conectada (o no es "
        "utilizable); usando StubSTT. Conecta tu propia credencial en Configuración -> "
        "PUT /v1/credentials/voice/stt.",
        tenant_id,
    )
    return StubSTT()
