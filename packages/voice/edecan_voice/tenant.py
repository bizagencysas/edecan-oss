"""Resolución de TTS bring-your-own **del tenant** — "tenant → stub", SIN paso
intermedio de plataforma (WP-V5-10; `DIRECCION_ACTUAL.md` "Modelo de
credenciales: TODO lo trae el cliente, siempre").

Mismo criterio EXACTO que ya siguen:

- `apps/api/edecan_api/routers/voice.py::_tts_para_tenant` (para las rutas
  HTTP `/v1/voice/speak`/`/v1/voice/transcribe`).
- `edecan_creative.providers.get_tenant_image_provider` (para `generar_imagen`).
- `edecan_toolkit.research.get_tenant_search_provider` (para `buscar_web`).

Ninguno de los tres —ni este módulo— lee JAMÁS `ELEVENLABS_API_KEY`/
`VOICE_TTS_PROVIDER`/`POLLY_VOICE` de `Settings`/`.env` de PLATAFORMA como
fallback: sin credencial propia del tenant (o si cualquier paso de la
resolución falla), se cae DIRECTO a `StubTTS`, nunca a una clave compartida
del operador. `packages/llm/edecan_llm/router.py::_build_provider_from_config`
—el bug de fuga de credencial más serio hasta ahora en el proyecto, ver
`ARCHITECTURE.md` §13 y `DIRECCION_ACTUAL.md` ("v4 completado", hallazgo
#1)— es la referencia exacta de qué NO hacer: un campo vacío/config
incompleta del tenant NUNCA debe completarse con `self._settings`/env de
plataforma. Aquí, "config incompleta" simplemente cae a `StubTTS`.

`resolver_tts_del_tenant`/`resolver_config_tts_del_tenant` hacen duck-typing
de `ctx` (`ctx.tenant_id`/`ctx.session`/`ctx.vault`) en vez de tipar
`edecan_core.tools.ToolContext` directo — mismo motivo que
`get_tenant_image_provider`: `edecan_voice` no depende de `edecan_core` solo
por una anotación de tipo (`ctx: Any`), y la consulta SQL directa
(`connector_accounts`) evita que este paquete dependa de `edecan_api.repo`
(paquete que, además, ya depende DE `edecan_voice` — sería un ciclo).

`connector_key = "voice_tts"` — la MISMA cadena literal que
`apps/api/edecan_api/deps.VOICE_TTS_CONNECTOR_KEY` (duplicada a propósito,
paquete hermano no importado acá, mismo criterio que `IMAGES_CONNECTOR_KEY`
en `edecan_creative.providers`) y el shape del bundle es el que ya guarda
`PUT /v1/credentials/voice/tts` (`apps/api/edecan_api/routers/credentials.py`,
ver su docstring): `{"provider": "elevenlabs", "api_key", "voice_id"}` o
`{"provider": "polly", "voice"}`.

`PollyTTS` en sí exige `allow_ambient_credentials=True` para poder heredar la
identidad AWS del proceso (`edecan_voice.polly`, segunda capa de defensa) —
la rama `provider == "polly"` de abajo SOLO pasa esa bandera DENTRO del
chequeo `EDECAN_LOCAL_MODE` de este mismo módulo, nunca fuera de él.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text as sql_text

from edecan_voice.base import TTSProvider
from edecan_voice.elevenlabs import ElevenLabsTTS
from edecan_voice.polly import PollyTTS
from edecan_voice.stubs import StubTTS

logger = logging.getLogger(__name__)

# Clave EXACTA pinned — ver docstring del módulo ("connector_key = voice_tts").
VOICE_TTS_CONNECTOR_KEY = "voice_tts"

_POLLY_DEFAULT_VOICE = "Lupe"


def _stub_con_aviso(tenant_id: Any) -> StubTTS:
    """`StubTTS` + `logger.warning` accionable — se llama desde CADA rama de
    `resolver_tts_del_tenant` que no encontró una credencial de voz (TTS) del
    tenant utilizable (nunca conectó nada, cuenta a medio escribir, JSON
    corrupto, campos incompletos). JAMÁS consulta `ELEVENLABS_API_KEY`/
    `VOICE_TTS_PROVIDER`/`POLLY_VOICE` de plataforma."""
    logger.warning(
        "tenant_id=%s no tiene una credencial de voz (TTS) propia conectada (o no es "
        "utilizable); usando StubTTS. Conecta tu propia credencial en Configuración -> "
        "PUT /v1/credentials/voice/tts.",
        tenant_id,
    )
    return StubTTS()


async def resolver_config_tts_del_tenant(ctx: Any) -> dict[str, Any] | None:
    """Config de TTS del tenant ya descifrada + parseada (mismo shape que
    guarda `PUT /v1/credentials/voice/tts`, ver docstring del módulo), o
    `None` si el tenant no conectó nada ahí (o si cualquier paso de la
    resolución falla — vault caído, JSON corrupto, etc. — nunca lanza, deja
    constancia con `logger.warning`).

    Lee `ctx.tenant_id`/`ctx.session`/`ctx.vault` de forma defensiva
    (`getattr`): si falta cualquiera de los tres, devuelve `None` directo
    (mismo criterio "sin contexto suficiente = como si no hubiera credencial"
    que `get_tenant_image_provider`).
    """
    tenant_id = getattr(ctx, "tenant_id", None)
    session = getattr(ctx, "session", None)
    vault = getattr(ctx, "vault", None)
    if tenant_id is None or session is None or vault is None:
        return None

    try:
        row = (
            await session.execute(
                sql_text(
                    "SELECT id FROM connector_accounts WHERE tenant_id = :tenant_id "
                    "AND connector_key = :connector_key ORDER BY created_at DESC LIMIT 1"
                ),
                {"tenant_id": tenant_id, "connector_key": VOICE_TTS_CONNECTOR_KEY},
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
            "No se pudo leer la config de voz (TTS) del tenant_id=%s.", tenant_id, exc_info=True
        )
        return None


async def resolver_tts_del_tenant(ctx: Any) -> TTSProvider:
    """`TTSProvider` bring-your-own del tenant — "tenant → stub" (ver
    docstring del módulo). NUNCA construye `ElevenLabsTTS`/`PollyTTS` con una
    clave de PLATAFORMA: si el tenant no conectó nada, o el proveedor/campos
    guardados no alcanzan para construir un proveedor real, cae a `StubTTS`
    con `logger.warning` accionable.

    `provider="polly"` además exige `getattr(ctx.settings, "EDECAN_LOCAL_MODE",
    False)` (ver docstring del módulo): sin eso, aunque el tenant SÍ tenga
    esa fila guardada, cae a `StubTTS` igual que si no hubiera nada — nunca
    comparte la identidad AWS del proceso entre tenants. `ctx.settings` se
    lee por `getattr` doble (nunca revienta si `ctx`/`ctx.settings` no lo
    traen, mismo duck-typing defensivo que `tenant_id`/`session`/`vault`).
    """
    tenant_id = getattr(ctx, "tenant_id", None)
    data = await resolver_config_tts_del_tenant(ctx)
    if data is None:
        return _stub_con_aviso(tenant_id)

    provider = data.get("provider")
    if provider == "elevenlabs" and data.get("api_key"):
        return ElevenLabsTTS(api_key=data["api_key"], default_voice_id=data.get("voice_id"))
    if provider == "polly" and getattr(getattr(ctx, "settings", None), "EDECAN_LOCAL_MODE", False):
        # `allow_ambient_credentials=True`: seguro AQUÍ porque el chequeo de
        # arriba ya confirmó que el backend corre en modo local de un único
        # tenant (ver docstring del módulo y de `edecan_voice.polly`).
        return PollyTTS(
            voice_id=data.get("voice") or _POLLY_DEFAULT_VOICE, allow_ambient_credentials=True
        )

    return _stub_con_aviso(tenant_id)
