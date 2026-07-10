"""Amazon Polly como proveedor TTS (`ARCHITECTURE.md` §10.9).

Se autentica con la cadena de credenciales AWS *ambiente* del proceso que
corre el backend (variables de entorno / perfil compartido / rol IAM de la
tarea ECS) — Polly no tiene un campo de credencial propia del tenant como
`api_key` (a diferencia de `ElevenLabsTTS`/`DeepgramSTT`). Esa identidad de
proceso solo es de verdad "la del tenant" cuando el backend corre en modo
local de UN SOLO tenant (`EDECAN_LOCAL_MODE=True`, la máquina ES la del
cliente): en cualquier despliegue que sirva a más de un tenant desde el
mismo proceso (hosted compartido, o un self-host que dé acceso a varios
clientes/equipos), esa cadena ambiente es una identidad AWS COMPARTIDA entre
todos los tenants que elijan `"polly"` — el mismo patrón "llave compartida de
plataforma" que `DIRECCION_ACTUAL.md` prohíbe (hallazgo "riesgo-legal-tos",
ver `HOTFIXES_PENDIENTES.md`; el mismo criterio que ya aplica
`packages/llm/edecan_llm/router.py::_LOCAL_ONLY_KINDS` para `claude_cli`/
`codex_cli`/`ollama`).

Por eso `allow_ambient_credentials` (default `False`) exige un opt-in
EXPLÍCITO para construir la sesión ambiente: cada caller que SÍ puede usarla
legítimamente lo pasa a propósito, con un comentario que dice por qué —
`edecan_voice.registry.get_tts` (resolución de un único tenant en self-host)
y las dos resoluciones multi-tenant SOLO dentro de su propio chequeo
`getattr(settings, "EDECAN_LOCAL_MODE", False)`
(`edecan_voice.tenant.resolver_tts_del_tenant`,
`apps/api/edecan_api/routers/voice.py::_tts_para_tenant`; ver también
`apps/api/edecan_api/routers/credentials.py::put_voice_tts_credentials`, que
rechaza guardar `"polly"` fuera de `EDECAN_LOCAL_MODE`). Sin ese opt-in
explícito (ni una sesión inyectada para tests), el constructor lanza
`ValueError` en vez de heredar la identidad AWS del proceso en silencio —
segunda capa de defensa si algún caller nuevo olvidara el chequeo de
`EDECAN_LOCAL_MODE` antes de instanciar `PollyTTS`.

`region_name`/`endpoint_url` SÍ pueden venir de config no-secreta (región
AWS, endpoint de LocalStack en dev) — no identifican ni autorizan una
cuenta, a diferencia de las credenciales.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

import aioboto3

from edecan_voice.base import TTSProvider

logger = logging.getLogger(__name__)

DEFAULT_VOICE_ID = "Lupe"
POLLY_ENGINE = "neural"
POLLY_OUTPUT_FORMAT = "mp3"  # Pinned: Polly siempre sintetiza en mp3 en este paquete.


class PollyTTS(TTSProvider):
    """Sintetiza voz con Amazon Polly (`synthesize_speech`, motor neural).

    `fmt` se acepta por compatibilidad con `TTSProvider` pero se ignora (con
    aviso por logging si se pide algo distinto de `"mp3"`) — igual que
    `ElevenLabsTTS`.
    """

    def __init__(
        self,
        *,
        voice_id: str = DEFAULT_VOICE_ID,
        region_name: str | None = None,
        endpoint_url: str | None = None,
        session: Any | None = None,
        allow_ambient_credentials: bool = False,
    ) -> None:
        self._voice_id = voice_id or DEFAULT_VOICE_ID
        self._region_name = region_name or None
        self._endpoint_url = endpoint_url or None

        # `session` es inyectable para tests (cliente/sesión fake) — se usa
        # tal cual, sin pasar por el chequeo de `allow_ambient_credentials`.
        if session is not None:
            self._session = session
            return

        if not allow_ambient_credentials:
            raise ValueError(
                "PollyTTS usa la cadena de credenciales AWS ambiente del proceso "
                "del backend — eso SOLO es 'la credencial del tenant' cuando el "
                "servidor corre en modo local de un único tenant "
                "(EDECAN_LOCAL_MODE=True). Pasa `allow_ambient_credentials=True` "
                "SOLO desde ahí (ver docstring del módulo), o `session=` en tests."
            )
        # Ver docstring del módulo: caller confirmó explícitamente que esta
        # identidad de proceso pertenece a un único tenant.
        self._session = aioboto3.Session()

    async def synthesize(
        self,
        text: str,
        voice_id: str | None = None,
        fmt: Literal["mp3", "wav"] = "mp3",
    ) -> bytes:
        if fmt != "mp3":
            logger.warning("PollyTTS solo produce mp3; se ignora fmt=%r", fmt)

        client_kwargs: dict[str, Any] = {}
        if self._region_name:
            client_kwargs["region_name"] = self._region_name
        if self._endpoint_url:
            client_kwargs["endpoint_url"] = self._endpoint_url

        async with self._session.client("polly", **client_kwargs) as client:
            response = await client.synthesize_speech(
                Text=text,
                VoiceId=voice_id or self._voice_id,
                Engine=POLLY_ENGINE,
                OutputFormat=POLLY_OUTPUT_FORMAT,
            )
            async with response["AudioStream"] as stream:
                return await stream.read()
